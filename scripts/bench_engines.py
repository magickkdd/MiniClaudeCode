#!/usr/bin/env python3
"""同一模型、三种推理引擎（Ollama / llama.cpp / vLLM）的吞吐实测与兼容性核对。

契约在 `docs/inference-bench-spec.md`，本文照 §4 的工作负载协议执行，判据是**客户端计量**：
Qwen tokenizer 数完整拼接文本，服务端 `usage` 字段只作交叉核对（偏差 >5% 逐样本如实记录，
不强行对齐 —— Ollama 的 usage 粒度差异本身就是兼容性数据）。

两个反直觉的写法有意为之：
1. 完成度按"整段文本一次数 token"而不是逐 delta 数 —— 逐 delta 数会把 BPE 跨界合并的
   token 数出两遍，短回复上能虚高 10% 以上，而三个引擎的分块边界各不相同。
2. 失败请求也进 JSONL —— 汇总里的 `attempts` 必须恒等于协议规模（12×repeats），
   否则一次网络抖动就把覆盖范围削薄，而 `write_evidence` 的守卫正是拿这个数字比Thin的。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _evidence import write_evidence  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("INFER_BENCH_HOME", "D:/inference-bench")).resolve()
RESULTS = REPO / "eval" / "results"
RAW_DIR = RESULTS / "inference-bench"
SUMMARY = RESULTS / "inference-bench.json"
COMPAT_SUMMARY = RESULTS / "inference-bench-compat.json"
COMPAT_RAW = RAW_DIR / "compat"
TOKENIZER_DEFAULT = HOME / "models" / "tokenizer.json"

MAX_TOKENS = 256
TEMPERATURE = 0.0
REQUEST_TIMEOUT = 300.0
DEVIATION_LIMIT = 0.05


# --------------------------------------------------------------------------- 引擎

@dataclass(frozen=True)
class Engine:
    name: str
    base_url: str
    model: str
    command: str
    tier: str = "main"
    concs: tuple[int, ...] = (1, 4)
    repeats: int = 10


GGUF_15B = HOME / "models" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
GGUF_7B = HOME / "models" / "qwen2.5-7b-instruct-q4_k_m-merged.gguf"
LLAMA_SERVER = HOME / "llama.cpp" / "llama-server.exe"

ENGINES: dict[str, Engine] = {
    "ollama": Engine(
        "ollama",
        "http://localhost:11434/v1",
        "qwen2.5:1.5b-instruct-q4_K_M",
        "ollama-windows-amd64.zip 解出的 ollama.exe serve（OLLAMA_MODELS=D:/inference-bench/ollama-models）"
        " + ollama create qwen2.5:1.5b-instruct-q4_K_M -f Modelfile(FROM 本地官方 GGUF，SHA256 已核)",
    ),
    "llamacpp": Engine(
        "llamacpp",
        "http://localhost:8080/v1",
        "qwen2.5-1.5b-instruct-q4_k_m",
        f"{LLAMA_SERVER} -m {GGUF_15B} -c 4096 -ngl 999 --port 8080",
    ),
    "vllm": Engine(
        "vllm",
        "http://localhost:8000/v1",
        "Qwen/Qwen2.5-1.5B-Instruct-AWQ",
        "wsl -d Ubuntu -e bash -lc 'VLLM_ATTENTION_BACKEND=FLASH_ATTN VLLM_USE_FLASHINFER_SAMPLER=0 "
        f"vllm serve {HOME}/models/AWQ --served-model-name Qwen/Qwen2.5-1.5B-Instruct-AWQ "
        "--max-model-len 4096 --gpu-memory-utilization 0.8 --enforce-eager'（spec 的 0.85 起不来，见 §8）",
    ),
    # D-2：4G 卡跑 7B 的真实可行路径 —— 部分卸载，慢是预期，只进附表。
    "llamacpp-7b": Engine(
        "llamacpp-7b",
        "http://localhost:8080/v1",
        "qwen2.5-7b-instruct-q4_k_m",
        f"{LLAMA_SERVER} -m {GGUF_7B} -c 4096 -ngl 20 --port 8080",
        tier="appendix",
        concs=(1,),
        repeats=2,
    ),
}


# --------------------------------------------------------------------------- prompt 集

# 长 prompt 的素材逐字节抄自盘上 fixture；`--verify-fixtures` 复核这一点（改一个字就报）。
_SRC_BUGHUNT_PARSE = r'''"""把 `1h30m` 这类字符串解析成秒。"""

from __future__ import annotations

import re

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_TOKEN_RE = re.compile(r"(\d+)\s*([smhd])", re.IGNORECASE)


def parse_duration(text: str) -> int:
    """按 `d/h/m/s` 逐段累加。顺序无关，出现无法识别的字符就报错。"""
    raw = (text or "").strip().lower().replace(" ", "")
    if not raw:
        raise ValueError("时长不能为空")

    tokens = _TOKEN_RE.findall(raw)
    if not tokens:
        raise ValueError(f"无法解析时长 {text!r}，期望形如 1h30m")

    consumed = "".join(f"{num}{unit}" for num, unit in tokens)
    if consumed != raw:
        raise ValueError(f"无法解析时长 {text!r}，多余部分 {raw[len(consumed):]!r}")

    return sum(int(num) * _UNITS[unit] for num, unit in tokens)
'''

_SRC_BUGHUNT_FORMAT = r'''"""把秒数格式化成 `1d2h3m4s`。输出规则写在 README 的表格里。"""

from __future__ import annotations

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400


def format_duration(seconds: int) -> str:
    total = int(seconds)
    if total < 0:
        raise ValueError(f"seconds 不能为负：{seconds}")

    days, rest = divmod(total, SECONDS_PER_HOUR)
    hours, rest = divmod(rest, SECONDS_PER_HOUR)
    minutes, secs = divmod(rest, SECONDS_PER_MINUTE)

    if days:
        return f"{days}d{hours}h{minutes}m{secs}s"
    if hours:
        return f"{hours}h{minutes}m{secs}s"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"
'''

_SRC_BUGHUNT_CLI = r'''"""`python -m duration` 的入口。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .format import format_duration
from .parse import parse_duration


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="duration", description="解析或格式化时长。")
    parser.add_argument("seconds", nargs="?", type=int, default=None, help="要格式化的秒数")
    parser.add_argument("--text", default=None, help="要解析的字符串，例如 1h30m")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.text is not None:
        print(format_duration(parse_duration(args.text)))
        return 0
    if args.seconds is None:
        build_parser().print_help()
        return 2
    print(format_duration(args.seconds))
    return 0
'''

_SRC_BUGHUNT_MAIN = r'''from duration.cli import main

raise SystemExit(main())
'''

_SRC_BUGHUNT_STOPWATCH = r'''"""一个薄秒表。批处理脚本用它统计各阶段耗时。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .format import format_duration


@dataclass
class Stopwatch:
    started_at: float | None = None
    samples: list[float] = field(default_factory=list)

    def start(self) -> "Stopwatch":
        self.started_at = time.perf_counter()
        return self

    def stop(self) -> float:
        if self.started_at is None:
            raise RuntimeError("必须先 start() 才能 stop()")
        elapsed = time.perf_counter() - self.started_at
        self.started_at = None
        self.samples.append(elapsed)
        return elapsed

    @property
    def total(self) -> float:
        return sum(self.samples)

    def label(self) -> str:
        """给人看的累计耗时。秒以下按 0 处理，报表里不出现小数。"""
        return format_duration(int(self.total))
'''

_SRC_BUGHUNT_INIT = r'''"""duration —— 时长解析与格式化。"""

from __future__ import annotations

from .format import format_duration
from .parse import parse_duration
from .stopwatch import Stopwatch

__all__ = ["format_duration", "parse_duration", "Stopwatch"]
__version__ = "0.4.2"
'''

_SRC_BUGHUNT_README = r'''# duration —— 面向人的时长解析与格式化

只依赖标准库。给 CLI、报表和日志脱敏脚本用。

## 命令行

```
$ python -m duration --text 1h30m
1h30m0s
$ python -m duration 90061
1d1h1m1s
```

## API

### `parse_duration(text) -> int`

把 `1d2h3m4s` 解析成秒数（93784）。单位只认 `d/h/m/s`，顺序无所谓，多余字符报 `ValueError`。

### `format_duration(seconds) -> str`

向下取整，从最高的非零单位开始打印，每段补零到个位：

| 输入 | 输出 |
|---:|---|
| 0 | `0s` |
| 45 | `45s` |
| 119 | `1m59s` |
| 3661 | `1h1m1s` |
| 90000 | `1d1h0m0s` |
| 90061 | `1d1h1m1s` |

超过一天时保留 `d` 段，后面的 `h/m/s` 照旧打印（哪怕是 0）。

## 约定

- 负数秒一律 `ValueError`，不返回 `-1h` 这种串。
- `Stopwatch` 只做秒表，不做线程安全保证。
'''

_SRC_BUGHUNT_CHANGELOG = r'''# 变更日志

## 0.4.2
- CLI 增加 `--text`，直接解析 `1h30m` 这类写法。

## 0.4.0
- 新增 `Stopwatch`，`label()` 复用 `format_duration`。

## 0.3.0
- `parse_duration` 允许乱序单位（`30m1h`）。
- 负数输入统一抛 `ValueError`。

## 0.2.0
- `parse_duration` 支持 `d` 单位。
- README 补上格式化对照表。
'''

_SRC_BUGHUNT_TEST_PARSE = r'''import pytest

from duration import parse_duration


def test_single_units():
    assert parse_duration("45s") == 45
    assert parse_duration("10m") == 600
    assert parse_duration("3h") == 10800
    assert parse_duration("2d") == 172800


def test_compound_order_does_not_matter():
    assert parse_duration("1h30m") == 5400
    assert parse_duration("30m 1h") == 5400
    assert parse_duration("1d2h3m4s") == 93784


def test_uppercase_accepted():
    assert parse_duration("1H30M") == 5400


@pytest.mark.parametrize("bad", ["", "   ", "abc", "90", "1h30x"])
def test_garbage_raises(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)
'''

_SRC_BUGHUNT_TEST_FORMAT = r'''import pytest

from duration import format_duration


def test_under_a_minute():
    assert format_duration(0) == "0s"
    assert format_duration(45) == "45s"


def test_whole_and_partial_minutes():
    assert format_duration(60) == "1m0s"
    assert format_duration(119) == "1m59s"
    assert format_duration(600) == "10m0s"


def test_just_under_an_hour():
    assert format_duration(3599) == "59m59s"


@pytest.mark.parametrize("bad", [-1, -3600])
def test_negative_raises(bad):
    with pytest.raises(ValueError):
        format_duration(bad)
'''

_SRC_BUGHUNT_TEST_CLI = r'''import subprocess
import sys

from duration.cli import main


def test_formats_bare_seconds(capsys):
    assert main(["75"]) == 0
    assert capsys.readouterr().out.strip() == "1m15s"


def test_parses_text(capsys):
    assert main(["--text", "5m"]) == 0
    assert capsys.readouterr().out.strip() == "5m0s"


def test_no_arguments_prints_help(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().out


def test_module_entrypoint_works():
    done = subprocess.run(
        [sys.executable, "-m", "duration", "119"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    assert done.stdout.strip() == "1m59s"
'''

_SRC_BUGHUNT_TEST_STOPWATCH = r'''import time

from duration import Stopwatch
from duration.stopwatch import format_duration


def test_start_stop_accumulates():
    watch = Stopwatch().start()
    elapsed = watch.stop()
    assert elapsed >= 0
    assert len(watch.samples) == 1
    assert watch.total == elapsed


def test_stop_without_start_raises():
    try:
        Stopwatch().stop()
    except RuntimeError as exc:
        assert "start()" in str(exc)
    else:
        raise AssertionError("未 start 就 stop 应该报错")


def test_label_uses_the_shared_formatter():
    watch = Stopwatch(samples=[1.9, 2.2])
    assert watch.label() == format_duration(4)
    assert watch.label() == "4s"


def test_context_manager_shape_is_not_promised():
    # README 没写 with 语法，所以这里只锁住现有行为，别顺手扩展 API
    assert time.perf_counter() > 0
'''

_SRC_SLUG_CORE = r'''"""唯一的 slug 清洗规则。别处不要再抄一份。"""

from __future__ import annotations

import re

_NOISE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """小写、连续非字母数字并成一个 `-`、去掉首尾的 `-`。"""
    return _NOISE.sub("-", (text or "").lower()).strip("-")
'''

_SRC_SLUG_CLI = r'''"""命令行入口。

历史上从这里复制过一份清洗实现 —— 复制的那份漏了去首尾 `-`，
所以带标点或重音的前缀会洗出和 `core.slugify` 不一样的结果。
"""

from __future__ import annotations

import argparse
import re

from .core import slugify

_PREFIX_NOISE = re.compile(r"[^a-z0-9]+")


def clean(text: str) -> str:
    """`core.slugify` 的复制版，少了收尾的 `strip("-")`。"""
    return _PREFIX_NOISE.sub("-", (text or "").lower())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slug", description="把标题变成 slug。")
    parser.add_argument("--prefix", default="", help="可选前缀，与标题之间用 -- 连接")
    parser.add_argument("--title", required=True, help="要清洗的标题")
    return parser


def main(argv: list[str] | None = None) -> str:
    args = build_parser().parse_args(argv)
    parts = [segment for segment in (clean(args.prefix), slugify(args.title)) if segment]
    return "--".join(parts)


if __name__ == "__main__":  # pragma: no cover - 手动跑的时候用
    print(main())
'''

_SRC_SLUG_README = r'''# slug-cli

把标题变成 slug。**清洗规则只应该有一份**：`slug/core.py` 的 `slugify()`。

```python
from slug.core import slugify

slugify("Café Bar!")     # → "caf-bar"
slugify("  --Weird-- ")  # → "weird"
```

命令行把 `--prefix` 与 `--title` 各清洗成一段，用 `--` 连接，空段跳过：

```
$ python -m slug.cli --prefix "Café Bar!" --title "Sale, 2026"
caf-bar--sale-2026
```

`slug/cli.py` 里目前还有一份从 core 复制出去的 `clean()`，两边已经漂了：
带非 ASCII 或结尾标点的前缀，`cli` 洗出来的结果和 `core` 不一样。
它的输出也必须走 `--` 连接的同一套规则。
'''

_SRC_SLUG_TEST_CLI = r'''from __future__ import annotations

import pytest

from slug.cli import build_parser, main


def test_cli_joins_prefix_and_title():
    assert main(["--prefix", "ops", "--title", "Sale, 2026"]) == "ops--sale-2026"


def test_cli_skips_empty_prefix():
    assert main(["--title", "Hello World"]) == "hello-world"


def test_title_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--prefix", "ops"])


def test_prefix_and_title_share_one_cleaning_rule():
    """前缀和标题必须被洗成同一个样子 —— 现在不是。"""
    assert main(["--prefix", "Café Bar!", "--title", "Café Bar!"]) == "caf-bar--caf-bar"
'''

_SRC_SLUG_TEST_CORE = r'''from __future__ import annotations

from slug.core import slugify


def test_lowercases_and_joins_words():
    assert slugify("Hello World") == "hello-world"


def test_collapses_runs_of_punctuation():
    assert slugify("a!!!b   c") == "a-b-c"


def test_strips_leading_and_trailing_dashes():
    assert slugify("  --Weird-- ") == "weird"


def test_non_ascii_counts_as_separator():
    assert slugify("Café Bar!") == "caf-bar"


def test_empty_and_punctuation_only_input():
    assert slugify("") == ""
    assert slugify("!!!") == ""
'''

_SRC_TAXED_CART = r'''"""结算：小计 → 折扣 → 税。"""

from __future__ import annotations

from .config import MAX_DISCOUNT, tax_of

Line = tuple[int, float]


def subtotal(lines: list[Line]) -> float:
    return round(sum(quantity * price for quantity, price in lines), 2)


def discounted(amount: float, rate: float) -> float:
    if not 0.0 <= rate <= MAX_DISCOUNT:
        raise ValueError(f"折扣率必须在 0–{MAX_DISCOUNT} 之间，收到 {rate}")
    return round(amount * (1.0 - rate), 2)


def total(lines: list[Line], *, region: str = "none", discount: float = 0.0) -> float:
    """含税总价。欧盟规则是**折后价**计税，这里目前按小计算税再减折扣。"""
    base = subtotal(lines)
    tax = base * tax_of(region)
    return round(base + tax - base * discount, 2)
'''

_SRC_TAXED_CONFIG = r'''"""税率与业务上限的配置。价格逻辑在 `cart.py`，两边都要读才对得上。"""

from __future__ import annotations

# 欧盟 VAT 是 23%（README 的算例就是按这个来的）。
TAX_RATES = {
    "eu": 0.20,
    "uk": 0.20,
    "none": 0.0,
}

MAX_DISCOUNT = 0.5


def tax_of(region: str) -> float:
    try:
        return TAX_RATES[region]
    except KeyError:
        raise ValueError(f"未知地区：{region}（可选 {', '.join(sorted(TAX_RATES))}）") from None
'''

_SRC_TAXED_INIT = r'''"""pricing —— 小计、折扣与含税总价。"""

from .cart import Line, discounted, subtotal, total
from .config import MAX_DISCOUNT, TAX_RATES, tax_of

__all__ = [
    "Line",
    "MAX_DISCOUNT",
    "TAX_RATES",
    "discounted",
    "subtotal",
    "tax_of",
    "total",
]
'''

_SRC_TAXED_README = r'''# taxed-base

订单结算：**小计 → 折扣 → 税**。税率按地区查 `pricing/config.py` 的 `TAX_RATES`。

```python
from pricing import total

total([(2, 19.99), (1, 4.5)], region="none")              # → 44.48
total([(1, 80.0)], region="uk")                           # → 96.00
total([(1, 100.0)], region="eu", discount=0.10)           # → 110.70
```

规则（欧盟 VAT）：

- 折扣先扣，税按**折后**金额算，不是按打折前的小计算；
- `region="none"` 表示免税；
- 折扣率上限 50%，超过直接 `ValueError`；
- 金额一律保留两位小数。
'''

_SRC_TAXED_TEST_PRICING = r'''"""结算逻辑的既有覆盖。注意：`region` 与 `discount` 从来没被同时测过。"""

from __future__ import annotations

import pytest

from pricing import MAX_DISCOUNT, discounted, subtotal, tax_of, total


def test_subtotal_sums_line_items():
    assert subtotal([(2, 19.99), (1, 4.5)]) == 44.48


def test_subtotal_of_empty_cart_is_zero():
    assert subtotal([]) == 0.0


def test_discounted_applies_rate():
    assert discounted(100.0, 0.10) == 90.0


def test_discounted_rejects_rate_above_cap():
    with pytest.raises(ValueError):
        discounted(100.0, MAX_DISCOUNT + 0.1)


def test_tax_of_known_regions():
    assert tax_of("none") == 0.0
    assert tax_of("uk") == 0.20


def test_tax_of_unknown_region_raises():
    with pytest.raises(ValueError):
        tax_of("mars")


def test_total_without_discount_adds_tax():
    assert total([(1, 80.0)], region="uk") == 96.0


def test_total_with_discount_but_no_tax():
    assert total([(1, 100.0)], region="none", discount=0.10) == 90.0


def test_total_keeps_two_decimals():
    assert total([(3, 0.1)], region="none") == 0.3
'''


def _bundle(question: str, *parts: tuple[str, str]) -> str:
    body = "\n\n".join(f"===== {label} =====\n{text}" for label, text in parts)
    return f"{question}\n\n只依据下面给出的材料回答，材料之外的一律不要假设。\n\n{body}\n"


SHORT_PROMPTS: list[tuple[str, str]] = [
    ("short-01", "把下面这句用户需求改写成一条可以直接交给编码 Agent 的任务指令，不超过 30 个字，"
                 "只输出改写结果：我想要一个命令行计算器，能做加减乘除，最好带自动跳过 0 除的检查，还要有单元测试。"),
    ("short-02", "You are answering about MCC, a small Python coding agent. Name the two "
                 "context-compression levels it uses, and say in one line each what the level drops "
                 "and what it keeps. Answer in English, at most six lines total, do not use any tool "
                 "and do not list files."),
    ("short-03", "用 pytest.mark.parametrize 给 parse_duration 写 4 条参数化用例，覆盖 \"45s\"→45、"
                 "\"10m\"→600、\"3h\"→10800、\"2d\"→172800。只输出代码，不要解释。"),
    ("short-04", "用三行中文说明：为什么吞吐基准要把 temperature 固定为 0；如果改成 1.0，"
                 "TTFT、生成吞吐、成功率这三个指标里哪两个的可比性会先坏掉，各自为什么。"
                 "不要举例，不要列工具，不要写代码。"),
    ("short-05", "MCC 的系统提示里 60% 是重复的工具说明，而上下文窗口只有 4K。给出三条压缩策略，"
                 "每条不超过 20 字，并各自标一句风险；再指出哪一条会破坏工具调用的可复现性。"
                 "输出用编号列表。"),
    ("short-06", "Translate into one English sentence: 「工作区哈希不变是只读模式的判据，模型自述不算数」. "
                 "Then answer in English in at most 25 words: why is a content hash a stronger check than "
                 "the model's own claim that it changed nothing? Label the two parts 1 and 2."),
]

LONG_PROMPTS: list[tuple[str, str]] = [
    ("long-01", _bundle(
        "README 说 parse_duration 对单位顺序无所谓，而 test_parse.py 把 \"90\" 列进了应该抛 ValueError 的"
        "垃圾输入。请回答：(1) \"90\" 走的是哪一条分支、报的是哪一句错误消息；(2) 如果用户输入 \"1h30\"，"
        "consumed 与 raw 各自是什么、多余部分是什么；(3) 只改一处让 \"1h30\" 报出更准的提示，给出改动后的代码行。",
        ("demos/fixtures/bug-hunt/duration/parse.py", _SRC_BUGHUNT_PARSE),
        ("demos/fixtures/bug-hunt/tests/test_parse.py", _SRC_BUGHUNT_TEST_PARSE),
        ("demos/fixtures/bug-hunt/README.md", _SRC_BUGHUNT_README),
        ("demos/fixtures/bug-hunt/CHANGELOG.md", _SRC_BUGHUNT_CHANGELOG),
    )),
    ("long-02", _bundle(
        "format.py 里第一次 divmod 用的是 SECONDS_PER_HOUR 而不是 SECONDS_PER_DAY。请按代码实际行为回答："
        "(1) format_duration(90000) 与 format_duration(90061) 各返回什么字符串；(2) README 表格里哪几行"
        "与这段实现不一致，把不一致的输入区间写出来；(3) 给出修复后的三行 divmod，并说明 test_format.py "
        "为什么抓不到这个 bug（指出它缺哪一类断言）。",
        ("demos/fixtures/bug-hunt/duration/format.py", _SRC_BUGHUNT_FORMAT),
        ("demos/fixtures/bug-hunt/tests/test_format.py", _SRC_BUGHUNT_TEST_FORMAT),
        ("demos/fixtures/bug-hunt/README.md", _SRC_BUGHUNT_README),
        ("demos/fixtures/bug-hunt/duration/__init__.py", _SRC_BUGHUNT_INIT),
        ("demos/fixtures/bug-hunt/CHANGELOG.md", _SRC_BUGHUNT_CHANGELOG),
    )),
    ("long-03", _bundle(
        "命令行有三条入口路径。请逐条回答：(1) `python -m duration 90061 --text 1h` 会打印什么、退出码是多少，"
        "并说明 argparse 在这里是怎么消歧的；(2) `python -m duration` 无参数时的输出与退出码；"
        "(3) test_cli.py 的 test_module_entrypoint_works 为什么必须用 subprocess 而不是直接调 main()；"
        "(4) 如果要把 --text 改成位置参数，README 的哪一行示例会立刻失效，CHANGELOG 里哪一条会跟着过时。",
        ("demos/fixtures/bug-hunt/duration/cli.py", _SRC_BUGHUNT_CLI),
        ("demos/fixtures/bug-hunt/duration/__main__.py", _SRC_BUGHUNT_MAIN),
        ("demos/fixtures/bug-hunt/tests/test_cli.py", _SRC_BUGHUNT_TEST_CLI),
        ("demos/fixtures/bug-hunt/README.md", _SRC_BUGHUNT_README),
        ("demos/fixtures/bug-hunt/CHANGELOG.md", _SRC_BUGHUNT_CHANGELOG),
    )),
    ("long-04", _bundle(
        "Stopwatch.label() 对 samples=[1.9, 2.2] 返回 \"4s\"，而真实累计是 4.1s。请回答："
        "(1) 这个取整方向对报表是系统性偏大还是偏小，为什么；(2) 如果要保留一位小数，需要动哪几个函数、"
        "README 的哪一条约定会先被破坏；(3) test_stopwatch.py 里哪两条断言会因此失败，给出改后的断言；"
        "(4) Stopwatch 不做线程安全保证，在多进程采样器里最典型的失真是什么。",
        ("demos/fixtures/bug-hunt/duration/stopwatch.py", _SRC_BUGHUNT_STOPWATCH),
        ("demos/fixtures/bug-hunt/tests/test_stopwatch.py", _SRC_BUGHUNT_TEST_STOPWATCH),
        ("demos/fixtures/bug-hunt/duration/format.py", _SRC_BUGHUNT_FORMAT),
        ("demos/fixtures/bug-hunt/README.md", _SRC_BUGHUNT_README),
    )),
    ("long-05", _bundle(
        "slug/cli.py 的 clean() 是 core.slugify() 的复制版，少了收尾的 strip(\"-\")。请回答："
        "(1) 给出两个具体的 --prefix 输入，使 --prefix 与 --title 相同时输出不等于 README 承诺的 "
        "\"caf-bar--caf-bar\"；(2) 说明这两输入在 core 与 cli 两边的差集出现在哪一步；"
        "(3) 最小修复是删掉 clean() 改调 slugify()，还是在 clean() 补一行？给出理由并指出会连带变化的行为；"
        "(4) test_cli.py 的第四条用例现在必然失败，它期望的返回值是什么。",
        ("eval/fixtures/slug-cli/slug/core.py", _SRC_SLUG_CORE),
        ("eval/fixtures/slug-cli/slug/cli.py", _SRC_SLUG_CLI),
        ("eval/fixtures/slug-cli/tests/test_cli.py", _SRC_SLUG_TEST_CLI),
        ("eval/fixtures/slug-cli/tests/test_core.py", _SRC_SLUG_TEST_CORE),
        ("eval/fixtures/slug-cli/README.md", _SRC_SLUG_README),
    )),
    ("long-06", _bundle(
        "taxed-base 有三处互相矛盾：config 的 eu 税率是 0.20 而注释说 VAT 是 23%；total() 按小计算税再减折扣，"
        "README 要求折扣先扣、按折后金额计税；test_pricing.py 从没把 region 与 discount 同时测过。请回答："
        "(1) 按现有实现算出 total([(2, 100.0)], region=\"eu\", discount=0.10) 的返回值，写出中间量；"
        "(2) 按 README 规则应有的值是多少，差在哪一步；(3) README 第三行算例期望 110.70，"
        "用现有实现能否得到，为什么；(4) 列出要改的三处，并给出一条能同时锁住 region 与 discount 的新断言。",
        ("eval/fixtures/taxed-base/pricing/cart.py", _SRC_TAXED_CART),
        ("eval/fixtures/taxed-base/pricing/config.py", _SRC_TAXED_CONFIG),
        ("eval/fixtures/taxed-base/pricing/__init__.py", _SRC_TAXED_INIT),
        ("eval/fixtures/taxed-base/tests/test_pricing.py", _SRC_TAXED_TEST_PRICING),
        ("eval/fixtures/taxed-base/README.md", _SRC_TAXED_README),
    )),
]

PROMPTS: list[tuple[str, str]] = SHORT_PROMPTS + LONG_PROMPTS
PROMPT_BANDS = {"short": (50, 100), "long": (1000, 2000)}


# --------------------------------------------------------------------------- token 计量

class Counter:
    """客户端计量：Qwen tokenizer 数完整文本。

    数 prompt 时只数发出去的内容本身，不加聊天模板 —— 模板是每个引擎自己拼的，
    把它们混进"客户端计量"就等于替引擎编一个数。模板开销因此单独成为一个可观测量：
    `template_overhead` = 服务端 prompt − 客户端 prompt。
    """

    def __init__(self, path: Path) -> None:
        from tokenizers import Tokenizer  # 只有真跑基准才需要，--report 不装也能用

        self.tokenizer = Tokenizer.from_file(str(path))
        self.sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def count(self, text: str) -> int:
        return len(self.tokenizer.encode(text).ids)


# --------------------------------------------------------------------------- 单请求

def one_request(engine: Engine, model: str, prompt_id: str, text: str, repeat: int, conc: int,
                counter: Counter, client: httpx.Client) -> dict[str, Any]:
    url = f"{engine.base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": text}],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    started = time.time()
    record, support = _stream_once(client, url, payload, engine, model, prompt_id, text, repeat,
                                   conc, counter, started)
    if record.get("status") == 400 and "stream_options" in payload:
        # 老版 Ollama 不认 stream_options：这是 §4 说的 usage 粒度坑，属于兼容性数据不是 bug。
        payload = {k: v for k, v in payload.items() if k != "stream_options"}
        record, _ = _stream_once(client, url, payload, engine, model, prompt_id, text, repeat,
                                 conc, counter, started)
        support = "rejected-400-stream-options"
    record["usage_support"] = support
    return record


def _stream_once(client: httpx.Client, url: str, payload: dict[str, Any], engine: Engine,
                 model: str, prompt_id: str, text: str, repeat: int, conc: int,
                 counter: Counter, started_wall: float) -> tuple[dict[str, Any], str]:
    def failed(error: str, status: int | None = None) -> tuple[dict[str, Any], str]:
        return _failure(engine, model, prompt_id, repeat, conc, started_wall, error, status), "n/a"

    t0 = time.perf_counter()
    parts: list[str] = []
    first_at: float | None = None
    last_at: float | None = None
    usage: dict[str, Any] | None = None
    finish: str | None = None
    server_error: str | None = None
    try:
        with client.stream("POST", url, json=payload, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status_code != 200:
                return failed(f"HTTP {resp.status_code}: {resp.read()[:400].decode('utf-8', 'replace')}")
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                # llama.cpp 会把 "Context size has been exceeded." 塞在 HTTP 200 的流里：
                # 只看状态码的客户端会把这种失败记成成功，所以错误体必须进证据。
                if obj.get("error"):
                    server_error = json.dumps(obj["error"], ensure_ascii=False)[:300]
                    continue
                if obj.get("usage"):
                    usage = obj["usage"]
                for choice in obj.get("choices") or []:
                    content = ((choice.get("delta") or {}).get("content")) or ""
                    if content:
                        now = time.perf_counter()
                        if first_at is None:
                            first_at = now
                        last_at = now
                        parts.append(content)
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
    except httpx.HTTPError as exc:
        return failed(f"{type(exc).__name__}: {exc}", status=200)
    ended = time.perf_counter()

    full = "".join(parts)
    client_completion = counter.count(full) if full else 0
    client_prompt = counter.count(text)
    gen_ms = (last_at - first_at) * 1000 if first_at is not None and last_at is not None else None
    completion_dev = prompt_dev = None
    if usage:
        server_completion = usage.get("completion_tokens")
        server_prompt = usage.get("prompt_tokens")
        if isinstance(server_completion, int) and client_completion:
            completion_dev = abs(server_completion - client_completion) / client_completion
        if isinstance(server_prompt, int) and client_prompt:
            prompt_dev = abs(server_prompt - client_prompt) / client_prompt
    ok = bool(full) and finish in ("stop", "length")
    if ok:
        error = None
    elif server_error:
        error = f"in-stream error: {server_error}"
    elif not full:
        error = "no-content" + (f" | {server_error}" if server_error else "")
    else:
        error = f"finish={finish}"
    record = {
        "engine": engine.name,
        "model": model,
        "prompt_id": prompt_id,
        "repeat": repeat,
        "conc": conc,
        "started_at": round(started_wall, 3),
        "status": 200,
        "ok": ok,
        "error": error,
        "server_error": server_error,
        "finish_reason": finish,
        "ttft_ms": round((first_at - t0) * 1000, 1) if first_at is not None else None,
        "gen_ms": round(gen_ms, 1) if gen_ms is not None else None,
        "wall_ms": round((ended - t0) * 1000, 1),
        "chars": len(full),
        "client_prompt_tokens": client_prompt,
        "client_completion_tokens": client_completion,
        "server_usage": usage,
        "completion_dev": None if completion_dev is None else round(completion_dev, 4),
        "prompt_dev": None if prompt_dev is None else round(prompt_dev, 4),
        "template_overhead": (usage.get("prompt_tokens") - client_prompt) if usage else None,
        "deviation_flag": bool(completion_dev and completion_dev > DEVIATION_LIMIT)
        or bool(prompt_dev and prompt_dev > DEVIATION_LIMIT),
        "response_sha256": hashlib.sha256(full.encode("utf-8")).hexdigest()[:16],
    }
    return record, ("include_usage" if usage else "no-usage-in-stream")


def _failure(engine: Engine, model: str, prompt_id: str, repeat: int, conc: int, started: float,
             error: str, status: int | None = None) -> dict[str, Any]:
    """失败也要占一个 attempt 位 —— 见模块 docstring 第 2 条。"""
    return {
        "engine": engine.name, "model": model, "prompt_id": prompt_id, "repeat": repeat,
        "conc": conc, "started_at": round(started, 3), "status": status, "ok": False,
        "error": error[:300], "server_error": None, "finish_reason": None, "ttft_ms": None, "gen_ms": None,
        "wall_ms": None, "chars": 0, "client_prompt_tokens": None,
        "client_completion_tokens": None, "server_usage": None, "completion_dev": None,
        "prompt_dev": None, "template_overhead": None, "deviation_flag": None,
        "response_sha256": None,
    }


# --------------------------------------------------------------------------- 分组与统计

def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(p / 100 * len(ordered)) - 1))
    return round(ordered[idx], 1)


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def _client(**kw: Any) -> httpx.Client:
    """trust_env=False 是必须的，不是洁癖：这台机器的代理写在注册表里（没有环境变量），
    httpx 默认会经 `urllib.getproxies()` 取到它 —— 同一发本机请求经代理 143ms、直连 17ms，
    且闭合端口回 502 而不是拒连。让三个引擎都走直连，跨引擎的 TTFT 才是同一个量。"""
    kw.setdefault("trust_env", False)
    return httpx.Client(**kw)


def run_group(engine: Engine, conc: int, repeats: int, counter: Counter, model: str) -> tuple[list[dict[str, Any]], float]:
    records: list[dict[str, Any]] = []
    jobs = [(pid, text, r) for pid, text in PROMPTS for r in range(repeats)]
    t0 = time.perf_counter()
    limits = httpx.Limits(max_connections=conc + 2, max_keepalive_connections=conc + 2)
    with _client(limits=limits, headers={"Content-Type": "application/json"}) as client:
        with ThreadPoolExecutor(max_workers=conc) as pool:
            records = list(pool.map(
                lambda job: one_request(engine, model, job[0], job[1], job[2], conc, counter, client),
                jobs,
            ))
    return records, time.perf_counter() - t0


def summarize(records: list[dict[str, Any]], wall_s: float, engine: Engine, conc: int,
              model: str) -> dict[str, Any]:
    ok = [r for r in records if r["ok"]]
    tps = [r["client_completion_tokens"] * 1000 / r["gen_ms"] for r in ok if r.get("gen_ms")]
    total_tokens = sum(r["client_completion_tokens"] or 0 for r in ok)
    devs = [r["completion_dev"] for r in ok if r.get("completion_dev") is not None]
    prompt_devs = [r["prompt_dev"] for r in ok if r.get("prompt_dev") is not None]
    overheads = [r["template_overhead"] for r in ok if isinstance(r.get("template_overhead"), int)]
    errors: dict[str, int] = {}
    for r in records:
        if not r["ok"]:
            key = (r.get("error") or "unknown")[:80]
            errors[key] = errors.get(key, 0) + 1
    return {
        "engine": engine.name,
        "tier": engine.tier,
        "model_served": model,
        "conc": conc,
        "prompts": len(PROMPTS),
        "repeats": engine.repeats,
        "attempts": len(records),
        "successes": len(ok),
        "ttft_p50_ms": _pct([r["ttft_ms"] for r in ok if r.get("ttft_ms") is not None], 50),
        "ttft_p95_ms": _pct([r["ttft_ms"] for r in ok if r.get("ttft_ms") is not None], 95),
        "tps_p50": _pct(tps, 50),
        "tps_p95": _pct(tps, 95),
        "tps_mean": round(statistics.mean(tps), 2) if tps else None,
        "wall_tps": round(total_tokens / wall_s, 2) if wall_s else None,
        "completion_tokens_total": total_tokens,
        "wall_s": round(wall_s, 1),
        "usage_dev_median": _median(devs),
        "usage_dev_flagged": sum(1 for r in ok if r.get("deviation_flag")),
        "prompt_dev_median": _median(prompt_devs),
        "template_overhead_median": _median([float(v) for v in overheads]) if overheads else None,
        "usage_support": sorted({r.get("usage_support") or "n/a" for r in records}),
        "finish_reasons": {k: sum(1 for r in ok if r.get("finish_reason") == k)
                           for k in sorted({r.get("finish_reason") for r in ok})},
        "errors": errors,
        "server_errors": {msg: sum(1 for r in records if r.get("server_error") == msg)
                          for msg in sorted({r["server_error"] for r in records if r.get("server_error")})},
    }


# --------------------------------------------------------------------------- 汇总落盘

def protocol_fingerprint(counter: Counter, engines: dict[str, Engine]) -> dict[str, Any]:
    prompts_text = "\n\x1e\n".join(f"{pid}\x1f{text}" for pid, text in PROMPTS)
    return {
        "prompts_sha256": hashlib.sha256(prompts_text.encode("utf-8")).hexdigest(),
        "prompt_ids": [pid for pid, _ in PROMPTS],
        "prompt_count": len(PROMPTS),
        "tokenizer_sha256": counter.sha256,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "repeats": {e.name: e.repeats for e in engines.values()},
        "concs": {e.name: list(e.concs) for e in engines.values()},
        "commands": {e.name: e.command for e in engines.values()},
        "endpoints": {e.name: e.base_url for e in engines.values()},
        "models_requested": {e.name: e.model for e in engines.values()},
        "client_metric": "completion tokens = Qwen tokenizer over the fully concatenated text",
        "http_client": "httpx trust_env=False —— 本机代理写在注册表里，默认会劫持 127.0.0.1",
    }


def group_metrics(payload: dict[str, Any]) -> dict[str, int]:
    """metrics 只放协议规模（attempt 数），不放时延/成功率 —— 重跑变快会被误判变薄而拒写。"""
    return {f"{g['engine']}:c{g['conc']}": int(g["attempts"]) for g in payload.get("groups", {}).values()}


def merge_and_write(existing_path: Path, fingerprint: dict[str, Any], new_groups: list[dict[str, Any]],
                    raw_paths: dict[str, str], allow_thinning: bool) -> bool:
    payload: dict[str, Any] = {"schema": 1, "protocol": fingerprint, "groups": {}, "raw": {}}
    if existing_path.exists():
        payload = json.loads(existing_path.read_text(encoding="utf-8"))
        if payload.get("protocol", {}).get("prompts_sha256") != fingerprint["prompts_sha256"]:
            print("! prompt 集相对已入库证据变了（协议换版）—— 旧组的数字与新组不再同尺。",
                  file=sys.stderr)
    # 协议块每次都按当前预设重写：它描述的是"怎么跑的"，与测出来的数字无关，
    # 冻结在第一次写入会让复现凭据过期（实测就出现过 commands 仍是改前文本）。
    payload["protocol"] = fingerprint
    groups = payload.setdefault("groups", {})
    for group in new_groups:
        groups[f"{group['engine']}:c{group['conc']}"] = group
    payload["raw"].update(raw_paths)
    payload["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return write_evidence(existing_path, payload, group_metrics, allow_thinning=allow_thinning,
                          note="attempt 数 = 12 prompts × repeats；掉数说明有一组没跑满。")


# --------------------------------------------------------------------------- 表格

def markdown_table(payload: dict[str, Any]) -> str:
    header = ("| 引擎 | 档 | 并发 | attempts | 成功率 | TTFT p50/p95 (ms) | 吞吐 p50/p95 (tok/s) "
              "| 墙钟吞吐 (tok/s) | usage 中位偏差 | 偏差>5% 样本 |")
    rule = "|" + "---|" * 10
    lines = [header, rule]
    order = {"main": 0, "appendix": 1}
    for group in sorted(payload.get("groups", {}).values(),
                        key=lambda g: (order.get(g.get("tier", "main"), 9), g["engine"], g["conc"])):
        attempts = group["attempts"] or 1
        lines.append(
            f"| {group['engine']} | {group.get('tier', 'main')} | c{group['conc']} | {attempts} | "
            f"{group['successes'] / attempts:.0%} | {group['ttft_p50_ms']} / {group['ttft_p95_ms']} | "
            f"{group['tps_p50']} / {group['tps_p95']} | {group['wall_tps']} | "
            f"{group['usage_dev_median']} | {group['usage_dev_flagged']} |"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- 健康检查与模型名

def resolve_model(client: httpx.Client, engine: Engine, override: str | None) -> str:
    if override:
        return override
    try:
        served = [m["id"] for m in client.get(f"{engine.base_url}/models", timeout=20).json()["data"]]
    except Exception as exc:  # noqa: BLE001 - 探针失败不该顶着一句 traceback 退出
        print(f"· {engine.name}: 读 /models 失败（{type(exc).__name__}），按预设名 {engine.model} 试",
              file=sys.stderr)
        return engine.model
    if engine.model in served:
        return engine.model
    if len(served) == 1:
        print(f"! {engine.name}: 盘上服务名是 {served[0]}，不是预设的 {engine.model} —— 按服务名跑",
              file=sys.stderr)
        return served[0]
    print(f"✗ {engine.name}: 服务端模型列表 {served} 里没有预设 {engine.model}，"
          f"用 --model 指定其一", file=sys.stderr)
    raise SystemExit(2)


def cmd_check(engines: list[str], counter: Counter, model_override: str | None) -> int:
    print(f"prompt 集 {len(PROMPTS)} 条（客户端计量，Qwen tokenizer）")
    bad_band = 0
    for pid, text in PROMPTS:
        n = counter.count(text)
        lo, hi = PROMPT_BANDS["short" if pid.startswith("short") else "long"]
        in_band = lo <= n <= hi
        bad_band += 0 if in_band else 1
        print(f"  {pid:<9} {n:>5} tok  {'ok' if in_band else f'偏离 {lo}-{hi} 档'}")
    print(f"· 偏离档位的 prompt：{bad_band}")
    with _client() as client:
        for name in engines:
            engine = ENGINES[name]
            try:
                data = client.get(f"{engine.base_url}/models", timeout=20).json()
                served = [m["id"] for m in data.get("data", [])]
                print(f"· {name:<12} {engine.base_url} 在线，服务名 {served}")
            except Exception as exc:  # noqa: BLE001
                print(f"· {name:<12} {engine.base_url} 不在线（{type(exc).__name__}: {exc}）")
    return 0


def verify_fixtures() -> int:
    """逐字节复核长 prompt 里嵌的 fixture 文本还等于盘上那份。"""
    pairs = {
        "demos/fixtures/bug-hunt/duration/parse.py": _SRC_BUGHUNT_PARSE,
        "demos/fixtures/bug-hunt/duration/format.py": _SRC_BUGHUNT_FORMAT,
        "demos/fixtures/bug-hunt/duration/cli.py": _SRC_BUGHUNT_CLI,
        "demos/fixtures/bug-hunt/duration/__main__.py": _SRC_BUGHUNT_MAIN,
        "demos/fixtures/bug-hunt/duration/stopwatch.py": _SRC_BUGHUNT_STOPWATCH,
        "demos/fixtures/bug-hunt/duration/__init__.py": _SRC_BUGHUNT_INIT,
        "demos/fixtures/bug-hunt/README.md": _SRC_BUGHUNT_README,
        "demos/fixtures/bug-hunt/CHANGELOG.md": _SRC_BUGHUNT_CHANGELOG,
        "demos/fixtures/bug-hunt/tests/test_parse.py": _SRC_BUGHUNT_TEST_PARSE,
        "demos/fixtures/bug-hunt/tests/test_format.py": _SRC_BUGHUNT_TEST_FORMAT,
        "demos/fixtures/bug-hunt/tests/test_cli.py": _SRC_BUGHUNT_TEST_CLI,
        "demos/fixtures/bug-hunt/tests/test_stopwatch.py": _SRC_BUGHUNT_TEST_STOPWATCH,
        "eval/fixtures/slug-cli/slug/core.py": _SRC_SLUG_CORE,
        "eval/fixtures/slug-cli/slug/cli.py": _SRC_SLUG_CLI,
        "eval/fixtures/slug-cli/tests/test_cli.py": _SRC_SLUG_TEST_CLI,
        "eval/fixtures/slug-cli/tests/test_core.py": _SRC_SLUG_TEST_CORE,
        "eval/fixtures/slug-cli/README.md": _SRC_SLUG_README,
        "eval/fixtures/taxed-base/pricing/cart.py": _SRC_TAXED_CART,
        "eval/fixtures/taxed-base/pricing/config.py": _SRC_TAXED_CONFIG,
        "eval/fixtures/taxed-base/pricing/__init__.py": _SRC_TAXED_INIT,
        "eval/fixtures/taxed-base/tests/test_pricing.py": _SRC_TAXED_TEST_PRICING,
        "eval/fixtures/taxed-base/README.md": _SRC_TAXED_README,
    }
    drift = []
    for rel, embedded in pairs.items():
        on_disk = (REPO / rel).read_text(encoding="utf-8")
        if on_disk != embedded:
            drift.append(rel)
    print(f"fixture 文本核对：{len(pairs) - len(drift)}/{len(pairs)} 逐字节一致")
    for rel in drift:
        print(f"  ✗ 已漂移：{rel}")
    return 1 if drift else 0


# --------------------------------------------------------------------------- 兼容性

MCC_CALC_TASK = (
    "在当前目录写一个 calculator.py：实现 add、sub、mul、div 四个函数，div 遇到除数为 0 时抛 ValueError。"
    "再写 tests/test_calculator.py，用 pytest 的 parametrize 覆盖四个函数各至少两条用例。"
    "最后运行 python -m pytest -q 并把输出贴给我。不要问我确认，直接做完。"
)
MCC_READONLY_TASK = (
    "只读回答，不要修改任何文件，也不要创建文件：打开 demos/fixtures/bug-hunt/duration/parse.py，"
    "回答 parse_duration(\"90\") 会返回什么，或者抛出哪种异常类型。"
    "只回答异常类型名，例如 ValueError 或 TypeError 或 KeyError。"
)
INSIGHT_TOPIC = "本地推理引擎 Ollama 与 vLLM 的适用场景差异"
INSIGHT_JSON_KEYS = ("topic", "report", "brief", "verification", "latency_s")
INSIGHT_HOME = Path(os.environ.get("INSIGHT_AGENT_HOME", "D:/insight-agent-full")).resolve()
INSIGHT_PY = INSIGHT_HOME / ".venv" / "Scripts" / "python.exe"

COMPAT_EXCLUDES = (".git", ".venv", ".mcc", ".traces", "__pycache__", ".pytest_cache",
                   "node_modules", "eval/results")


def _excluded(rel: str) -> bool:
    parts = rel.split("/")
    return any(part in COMPAT_EXCLUDES or "/".join(parts[: i + 1]) in COMPAT_EXCLUDES
               for i, part in enumerate(parts))


def workspace_digest(root: Path) -> str:
    """工作区内容摘要：只读问答的判据是它前后不变（模型自述不算数）。"""
    digest = hashlib.sha256()
    paths = [path.relative_to(root).as_posix() for path in root.rglob("*")
             if path.is_file() and not _excluded(path.relative_to(root).as_posix())]
    for rel in sorted(paths):
        digest.update(rel.encode("utf-8"))
        digest.update((root / rel).read_bytes())
    return digest.hexdigest()[:32]


def trace_evidence(trace_path: Path) -> dict[str, Any]:
    """从 MCC 轨迹里读出工具链的形态：tools 有没有发出去、tool_calls 有没有被执行、is_error 长什么样。"""
    if not trace_path.exists():
        return {"trace_present": False}
    records = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    kinds = {k: sum(1 for r in records if r.get("kind") == k) for k in
             ("session_start", "turn_start", "llm_response", "tool_call", "context_compact", "run_end")}
    session = next((r for r in records if r.get("kind") == "session_start"), {})
    tool_calls = [r for r in records if r.get("kind") == "tool_call"]
    end = next((r for r in reversed(records) if r.get("kind") == "run_end"), {})
    return {
        "trace_present": True,
        "kinds": kinds,
        "tools_offered": len(session.get("tools") or []),
        "tool_names_offered": sorted(session.get("tools") or [])[:20],
        "tool_calls_executed": len(tool_calls),
        "tool_errors": sum(1 for r in tool_calls if r.get("ok") is False),
        "run_end": {k: end.get(k) for k in ("termination", "tool_calls", "tool_errors", "turns",
                                            "denied_actions", "failure_modes") if k in end},
        "usage_seen": any((r.get("usage") or {}) for r in records if r.get("kind") == "llm_response"),
    }


def _run(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int) -> dict[str, Any]:
    try:
        done = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
        return {"cmd": cmd, "cwd": str(cwd), "exit": done.returncode,
                "stdout": done.stdout or "", "stderr": done.stderr or ""}
    except subprocess.TimeoutExpired as exc:
        return {"cmd": cmd, "cwd": str(cwd), "exit": None,
                "stdout": (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                "stderr": f"TimeoutExpired after {timeout}s"}


def _engine_env(engine: Engine, model: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LLM_BASE_URL"] = engine.base_url
    env["LLM_API_KEY"] = "local-bench"
    env["LLM_MODEL"] = model
    return env


def compat_mcc_calc(engine: Engine, model: str) -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="bench-calc-"))
    trace = workdir / ".traces" / "session.jsonl"
    env = _engine_env(engine, model)
    env["TRACE_PATH"] = str(trace)
    env["PROJECT_ROOT"] = str(workdir)
    try:
        run = _run([sys.executable, "main.py", "-t", MCC_CALC_TASK, "--mode", "auto",
                    "--max-turns", "8"], REPO, env, timeout=1800)
        produced, internal = [], 0
        for p in workdir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(workdir).as_posix()
            if rel.startswith((".traces/", ".mcc/")) or "__pycache__" in rel:
                internal += 1  # MCC 自己的记忆与 shadow-git 检查点，不是任务产物
                continue
            produced.append(rel)
        run["checks"] = {"exit_zero": run["exit"] == 0, "produced_files": sorted(produced),
                         "mcc_internal_files": internal}
        run["trace"] = trace_evidence(trace)
        run["judgement"] = "退出码==0（spec §5 MCC①）"
        run["passed"] = run["exit"] == 0
        term = run["trace"].get("run_end", {}).get("termination") if run["trace"].get("trace_present") else None
        run["failure_form"] = (f"termination={term} · tool_errors="
                               f"{run['trace'].get('tool_errors')} · 产出 {len(produced)} 个文件")
        # 判据（退出码）与任务完成度是两件事：1.5B 会只叙述不动手，退出码照样是 0。
        run["note"] = ("" if (produced or run["exit"] != 0) else
                       f"退出码 0 但零文件产出，tool_calls_executed="
                       f"{run['trace'].get('tool_calls_executed')}（模型只叙述不动手，退出码判据挡不住）")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return run


def server_props(engine: Engine) -> dict[str, Any]:
    """引擎自报的装配参数。`/props` 是 llama.cpp 才有的端点，Ollama/vLLM 会 404 ——
    探不到就记探不到（`props_error`），不编。能探到时记 n_ctx / alias / template_sha256：
    那条 400 的容量凭据必须是**在线服务端自己说的**，不是我转述命令。"""
    out: dict[str, Any] = {}
    try:
        root = engine.base_url.rsplit("/v1", 1)[0]
        props = _client().get(f"{root}/props", timeout=8).json()
        out["total_slots"] = props.get("total_slots")
        out["alias"] = props.get("model_alias")
        out["model_ftype"] = props.get("model_ftype")
        tpl = props.get("chat_template") or ""
        out["template_sha256"] = hashlib.sha256(tpl.encode("utf-8")).hexdigest()[:16] if tpl else None
    except Exception as exc:  # noqa: BLE001
        out["props_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return out


def compat_mcc_readonly(engine: Engine, model: str) -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="bench-qa-"))
    trace = workdir / "session.jsonl"
    env = _engine_env(engine, model)
    env["TRACE_PATH"] = str(trace)
    before = workspace_digest(REPO)
    run = _run([sys.executable, "main.py", "-t", MCC_READONLY_TASK, "--readonly",
                "--max-turns", "6"], REPO, env, timeout=1200)
    after = workspace_digest(REPO)
    answer = run["stdout"]
    run["checks"] = {
        "exit_zero": run["exit"] == 0,
        "workspace_unchanged": before == after,
        "digest_before": before,
        "digest_after": after,
        "keyword_hit": "ValueError" in answer,
        "expected_keyword": "ValueError",
        "excluded_from_digest": list(COMPAT_EXCLUDES),
    }
    run["trace"] = trace_evidence(trace)
    reject = re.search(r"\{\"error\":\{.*?\}\}", answer or run["stderr"])
    run["checks"]["endpoint_reject"] = reject.group(0)[:300] if reject else None
    run["judgement"] = "退出 0 + 工作区摘要不变 + 答案含 ValueError（spec §5 MCC②）"
    run["passed"] = all([run["checks"]["exit_zero"], run["checks"]["workspace_unchanged"],
                        run["checks"]["keyword_hit"]])
    run["failure_form"] = (run["checks"]["endpoint_reject"]
                          or ((run["stderr"].strip().splitlines() or [""])[-1][:200]))
    shutil.rmtree(workdir, ignore_errors=True)
    return run


def compat_insight(engine: Engine, model: str) -> dict[str, Any]:
    env = _engine_env(engine, model)
    env.update({
        "LLM_PROFILE": "local",
        "LOCAL_BASE_URL": engine.base_url,
        "LOCAL_MODEL": model,
        "LLM_MODEL_ID": model,
        "TAVILY_API_KEY": "local-bench-dummy",
        "LANGFUSE_PUBLIC_KEY": "",
        "LANGFUSE_SECRET_KEY": "",
        "RESEARCH_DEPTH": "fast",
    })
    # 上游 pyproject 把入口写成 `insight_agent:main`，而包里根本没有 main —— 装好的
    # insight-agent.exe 一跑就 ImportError。绕开控制台脚本、直接按模块调，是本机唯一能
    # 跑到 graph 的那条路，所以两个形态都记进证据（失败的形态本身就是兼容性数据）。
    broken = _run(["uv", "run", "insight-agent", "research", INSIGHT_TOPIC, "--depth", "fast",
                   "--json"], INSIGHT_HOME, env, timeout=180)
    run = _run([str(INSIGHT_PY), "-m", "insight_agent.cli", "research", INSIGHT_TOPIC,
                "--depth", "fast", "--json"], INSIGHT_HOME, env, timeout=2400)
    run["console_script_entry"] = {
        "cmd": broken["cmd"], "exit": broken["exit"],
        "error_tail": broken["stderr"].strip().splitlines()[-1] if broken["stderr"] else "",
    }
    parsed: dict[str, Any] | None = None
    # `--json` 打的是多行美化 JSON，逐行扫描只会看见一个孤零零的 "{"。
    start = run["stdout"].find("{")
    if start >= 0:
        try:
            candidate = json.JSONDecoder().raw_decode(run["stdout"][start:])[0]
            parsed = candidate if isinstance(candidate, dict) else None
        except json.JSONDecodeError:
            parsed = None
    keys = sorted(parsed) if isinstance(parsed, dict) else []
    # 判据取自 `cli.py:51-57` 真正拼出的 payload，不是它 help 里那句
    # 「report+verification+metrics」—— 上游自述与自己的实现不一致：fast 档不发 metrics。
    verification = (parsed or {}).get("verification") or {}
    run["checks"] = {
        "exit_zero": run["exit"] == 0,
        "json_object": parsed is not None,
        "top_level_keys": keys,
        "required_keys_present": all(k in keys for k in INSIGHT_JSON_KEYS),
        "missing_vs_help": sorted({"report", "verification", "metrics"} - set(keys)),
        "verification_error": str(verification.get("error") or "")[:300],
        "claims": len(verification.get("claims") or []),
        "report_chars": len(str((parsed or {}).get("report") or "")),
        "langfuse_mounted": bool(env["LANGFUSE_PUBLIC_KEY"] and env["LANGFUSE_SECRET_KEY"]),
    }
    run["failure_form"] = run["checks"]["verification_error"] or run["console_script_entry"]["error_tail"]
    run["judgement"] = f"退出 0 + JSON 含 {list(INSIGHT_JSON_KEYS)}（依 cli.py:51-57 实现，非其 help 自述）"
    run["passed"] = run["checks"]["exit_zero"] and run["checks"]["required_keys_present"]
    return run


COMPAT_CASES = {
    "mcc-calc": compat_mcc_calc,
    "mcc-readonly": compat_mcc_readonly,
    "insight-research": compat_insight,
}


def compat_metrics(payload: dict[str, Any]) -> dict[str, int]:
    return {f"{engine}/{case}": int(record.get("attempts", 1))
            for engine, cases in payload.get("engines", {}).items() for case, record in cases.items()}


def run_digest(record: dict[str, Any]) -> dict[str, Any]:
    """覆盖旧记录前留下的摘要 —— 同一科两次跑出两种结局时，被覆盖的那次也得可查。"""
    return {k: record.get(k) for k in ("at", "exit", "passed", "judgement", "failure_form",
                                       "note", "model_used", "error_tail", "checks")}


def cmd_compat(engine_names: list[str], cases: list[str], counter: Counter,
               model_override: str | None, allow_thinning: bool) -> int:
    payload: dict[str, Any] = {"schema": 2, "engines": {},
                               "protocol": protocol_fingerprint(counter, ENGINES)}
    if COMPAT_SUMMARY.exists():
        payload = json.loads(COMPAT_SUMMARY.read_text(encoding="utf-8"))
        payload["schema"] = 2
    payload["protocol"] = protocol_fingerprint(counter, ENGINES)
    with _client() as probe:
        for name in engine_names:
            engine = ENGINES[name]
            model = resolve_model(probe, engine, model_override)
            bucket = payload.setdefault("engines", {}).setdefault(name, {})
            for case in cases:
                print(f"· compat {name}/{case} …", flush=True)
                raw = COMPAT_CASES[case](engine, model)
                out_dir = COMPAT_RAW / name
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"{case}.stdout.txt").write_text(
                    raw["stdout"][-20000:], encoding="utf-8", errors="replace")
                (out_dir / f"{case}.stderr.txt").write_text(
                    raw["stderr"][-8000:], encoding="utf-8", errors="replace")
                # 同名原始件会被下一次运行覆盖，而 flaky 的证据恰恰是"上几次长什么样"：
                # 所以每一次运行另存一份带时间戳的原始件，路径记在该次的 digest 里。
                run_tag = time.strftime("%H%M%S")
                keep_out = out_dir / f"{case}.{run_tag}.stdout.txt"
                keep_err = out_dir / f"{case}.{run_tag}.stderr.txt"
                keep_out.write_text(raw["stdout"][-20000:], encoding="utf-8", errors="replace")
                keep_err.write_text(raw["stderr"][-8000:], encoding="utf-8", errors="replace")
                record = {k: v for k, v in raw.items() if k not in ("stdout", "stderr")}
                record["attempts"] = 1
                record["model_used"] = model
                record["server_props"] = server_props(engine)
                tail = (raw["stderr"].strip().splitlines() or [""])[-1]
                record["error_tail"] = tail[:200]
                # stderr 里的错误签名优先于"坏入口 ImportError"：后者每次都在，挂在任何一行
                # 都会盖掉这一轮真正的失败原因（BadRequestError / LengthFinishReason 等）。
                sig = re.search(r"(BadRequestError|LengthFinishReasonError|APIStatusError|"
                                r"InternalServerError|ConnectionError|TimeoutExpired|"
                                r'"error":\s*\{.{0,200})', raw["stderr"], re.S)
                record["stderr_signature"] = (sig.group(0)[:220] if sig else "")
                if not record.get("failure_form"):
                    record["failure_form"] = record["stderr_signature"]
                # 通过的科目不挂失败形态：坏入口的 ImportError 永远在，挂在 pass 行上会变成假的失败形态。
                if record.get("passed"):
                    record["failure_form"] = ""
                record.setdefault("failure_form", "")
                record.setdefault("note", "")
                record["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                record["raw_stdout_run"] = keep_out.relative_to(REPO).as_posix()
                previous = bucket.get(case)
                if previous:
                    history = list(previous.get("previous_runs", []))
                    digest = run_digest(previous)
                    digest["raw_stdout_run"] = previous.get("raw_stdout_run")
                    history.append(digest)
                    record["previous_runs"] = history[-8:]
                record["raw_stdout"] = (out_dir / f"{case}.stdout.txt").relative_to(REPO).as_posix()
                bucket[case] = record
                print(f"  → passed={record['passed']} exit={record['exit']} "
                      f"{record.get('checks', {})}", flush=True)
    payload["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    ok = write_evidence(COMPAT_SUMMARY, payload, compat_metrics, allow_thinning=allow_thinning,
                        note="每个 引擎/科目 记 1 次尝试；少一科就是少一科证据。")
    return 0 if ok else 1


# --------------------------------------------------------------------------- 主流程

def cmd_bench(engine_names: list[str], concs: str | None, counter: Counter,
              model_override: str | None, allow_thinning: bool) -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    new_groups: list[dict[str, Any]] = []
    raw_paths: dict[str, str] = {}
    with _client() as probe:
        for name in engine_names:
            engine = ENGINES[name]
            model = resolve_model(probe, engine, model_override)
            wanted = [c for c in engine.concs
                      if c in (int(x) for x in (concs.split(",") if concs else engine.concs))]
            for conc in wanted:
                print(f"· {engine.name} c{conc}：{len(PROMPTS)} prompts × {engine.repeats} 次 "
                      f"= {len(PROMPTS) * engine.repeats} 请求 …", flush=True)
                records, wall = run_group(engine, conc, engine.repeats, counter, model)
                path = RAW_DIR / f"{engine.name}.c{conc}.jsonl"
                path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                                encoding="utf-8")
                group = summarize(records, wall, engine, conc, model)
                group["raw"] = path.relative_to(REPO).as_posix()
                new_groups.append(group)
                raw_paths[f"{engine.name}:c{conc}"] = group["raw"]
                print(f"  完成 {group['successes']}/{group['attempts']}，"
                      f"TTFT p50 {group['ttft_p50_ms']}ms，吞吐 p50 {group['tps_p50']} tok/s",
                      flush=True)
    if not merge_and_write(SUMMARY, protocol_fingerprint(counter, ENGINES), new_groups,
                           raw_paths, allow_thinning):
        return 1
    merged = json.loads(SUMMARY.read_text(encoding="utf-8"))
    print()
    print(markdown_table(merged))
    return 0


def markdown_compat_table(payload: dict[str, Any]) -> str:
    """兼容矩阵从证据 JSON 现算，不手抄：判据写过的键只认盘上那份。"""
    engines = payload.get("engines", {})
    cases = [c for c in COMPAT_CASES if any(c in cases for cases in engines.values())]
    lines = ["| 引擎 | 科目 | 退出码 | 判据 | 失败形态（服务端/上游原话） |",
             "|---|---|---|---|---|"]
    for name in engines:
        for case in cases:
            rec = engines[name].get(case)
            if not rec:
                continue
            checks = rec.get("checks", {})
            form = rec.get("failure_form") or rec.get("note") or "—"
            history = rec.get("previous_runs") or []
            flips = sum(1 for h in history if bool(h.get("passed")) != bool(rec.get("passed")))
            verdict = ("✓" if rec.get("passed") else "✗") + (f"（另有 {flips} 次结局相反）" if flips else "")
            lines.append(f"| {name} | {case} | {rec.get('exit')} | "
                         f"{verdict} {rec.get('judgement', '')[:60]} | "
                         f"`{str(form)[:120]}` |")
    return "\n".join(lines)


def cmd_refresh_protocol(counter: Counter) -> int:
    """按当前预设重写汇总证据的协议块（不重测、不动任何组）。"""
    if not SUMMARY.exists():
        print("还没有汇总证据可刷新，先跑 --engine", file=sys.stderr)
        return 1
    payload = json.loads(SUMMARY.read_text(encoding="utf-8"))
    payload["protocol"] = protocol_fingerprint(counter, ENGINES)
    return 0 if merge_existing(payload, counter) else 1


def merge_existing(payload: dict[str, Any], counter: Counter) -> bool:
    return write_evidence(SUMMARY, payload, group_metrics,
                          note="只改协议块，所有组的 attempt 数不变。")


def cmd_report(what: str) -> int:
    which = ("bench", "compat") if what == "all" else (what,)
    for kind in which:
        path = SUMMARY if kind == "bench" else COMPAT_SUMMARY
        if not path.exists():
            print(f"还没有 {kind} 证据，先跑对应模式", file=sys.stderr)
            return 1
        payload = json.loads(path.read_text(encoding="utf-8"))
        print(markdown_table(payload) if kind == "bench" else markdown_compat_table(payload))
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    # GBK 控制台会在第一句中文字上抛 UnicodeEncodeError，实测脚本不能依赖 locale。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="同一模型跨三引擎推理实测（docs/inference-bench-spec.md）")
    parser.add_argument("--engine", default="all", help="逗号分隔：ollama,llamacpp,vllm,llamacpp-7b")
    parser.add_argument("--conc", default=None, help="逗号分隔并发度，默认按引擎预设（1,4）")
    parser.add_argument("--compat", action="store_true", help="跑兼容性科目而不是吞吐")
    parser.add_argument("--cases", default="mcc-calc,mcc-readonly,insight-research")
    parser.add_argument("--check", action="store_true", help="核对 prompt 档位与各引擎端点")
    parser.add_argument("--refresh-protocol", action="store_true",
                        help="按当前预设重写汇总证据的协议块（不重测任何数字）")
    parser.add_argument("--report", nargs="?", choices=["bench", "compat", "all"], const="all",
                        help="只从盘上证据重打表格（bench=吞吐表，compat=兼容矩阵，省略=两份）")
    parser.add_argument("--verify-fixtures", action="store_true", help="逐字节复核长 prompt 里的 fixture 文本")
    parser.add_argument("--model", default=None, help="覆盖请求里的 model 字段（vLLM 走本地路径时用）")
    parser.add_argument("--tokenizer", default=str(TOKENIZER_DEFAULT))
    parser.add_argument("--allow-thinning", action="store_true", help="见 _evidence.FLAG")
    args = parser.parse_args(argv)

    if args.verify_fixtures:
        return verify_fixtures()
    if args.refresh_protocol:
        # 这条分支在 counter 之前跑，所以自己造一个 —— 刷新协议块只需要 tokenizer 的哈希。
        return cmd_refresh_protocol(Counter(Path(args.tokenizer)))
    if args.report:
        return cmd_report(args.report)

    names = list(ENGINES) if args.engine == "all" else [n.strip() for n in args.engine.split(",")]
    unknown = [n for n in names if n not in ENGINES]
    if unknown:
        print(f"未知引擎 {unknown}，可选 {list(ENGINES)}", file=sys.stderr)
        return 2
    counter = Counter(Path(args.tokenizer))
    if args.check:
        return cmd_check(names, counter, args.model)
    if args.compat:
        cases = [c.strip() for c in args.cases.split(",")]
        missing = [c for c in cases if c not in COMPAT_CASES]
        if missing:
            print(f"未知科目 {missing}，可选 {list(COMPAT_CASES)}", file=sys.stderr)
            return 2
        return cmd_compat(names, cases, counter, args.model, args.allow_thinning)
    return cmd_bench(names, args.conc, counter, args.model, args.allow_thinning)


if __name__ == "__main__":
    raise SystemExit(main())
