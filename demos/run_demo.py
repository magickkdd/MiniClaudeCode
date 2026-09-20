#!/usr/bin/env python
"""demo 跑批器 —— 把"跑一遍 Agent + 客观判定 + 生成证据"固定成一条命令。

为什么不让 README 直接贴手工结果：那样数字就跟 trace 脱钩了，等于自证无效。
这个脚本做四件事，缺一条都不算 demo：

1. **隔离**：每次运行都把 fixture 复制到 `demos/.work/<id>`，跑完再判，
   绝不碰 `demos/fixtures/` 本体 —— 否则第二次运行就不干净了。
2. **同一张依赖图**：装配走 `cli.main.build_session()`，demo 跑通的代码路径
   和用户 `python main.py` 跑通的完全一致。
3. **独立判定**：成功与否由"跑 pytest + 校验函数行为 + 比对基线哈希"决定，
   不看模型自己怎么说。`tests/` 被改动就是作弊，直接判失败。
4. **可追溯**：终端输出、trace 统计、before/after diff 一起写进
   `demos/results/<id>.<engine>.md`。

用法：

```
python demos/run_demo.py --list
python demos/run_demo.py --all                    # 确定性，不打网络
python demos/run_demo.py --demo red-tests --engine live
```
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence

DEMO_DIR = Path(__file__).resolve().parent
ROOT = DEMO_DIR.parent
for entry in (ROOT / "src", DEMO_DIR, ROOT / "tests"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from miniclaude.agent.permissions import Answer, PermissionMode  # noqa: E402
from miniclaude.agent.state import AgentResult, TerminationReason  # noqa: E402
from miniclaude.cli.main import build_session  # noqa: E402
from miniclaude.cli.render import Renderer  # noqa: E402
from miniclaude.config import Config  # noqa: E402
from miniclaude.infra.trace import summarize  # noqa: E402
from miniclaude.llm.openai_compat import OpenAICompatClient  # noqa: E402
from miniclaude.messages import LLMResponse  # noqa: E402

import fake_scripts  # noqa: E402
from fakes import FakeLLM  # noqa: E402

FIXTURES = DEMO_DIR / "fixtures"
WORK = DEMO_DIR / ".work"
TRACES = DEMO_DIR / "traces"
RESULTS = DEMO_DIR / "results"
NOISE = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache")
CHILD_ENV = {
    **os.environ,
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
}
_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|errors?|error|skipped)\b")
MAX_DIFF_LINES = 160


# --------------------------------------------------------------- 判定原语


@dataclass
class Check:
    label: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        mark = "x" if self.ok else " "
        body = f"{self.label} —— {self.detail}" if self.detail else self.label
        return f"- [{mark}] {body}"


@dataclass
class Outcome:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    @property
    def passed(self) -> int:
        return sum(1 for check in self.checks if check.ok)

    def verdict(self) -> str:
        return "PASS" if self.ok else "FAIL"

    def lines(self) -> list[str]:
        return [check.line() for check in self.checks]


@dataclass
class Context:
    """判定能看到的一切。判定逻辑不许依赖模型的最后一段话。"""

    engine: str
    workdir: Path
    baseline: Path
    result: AgentResult
    console: list[str]


def _run(workdir: Path, argv: list[str], timeout: int) -> tuple[int, str]:
    try:
        done = subprocess.run(  # noqa: S603 - 判定脚本由我们自己拼，不含用户输入
            argv,
            cwd=str(workdir),
            env=CHILD_ENV,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 124, f"{type(exc).__name__}: {exc}"
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()


def pytest_report(workdir: Path) -> tuple[int, dict[str, int], str]:
    """在指定目录里独立跑一遍 pytest。

    `--confcutdir .` 与显式的 `.` 都是必需的：本项目自己的 `pyproject.toml`
    设了 `testpaths=["tests"]`，根目录还有一份 `conftest.py` 在忽略 demo
    fixture。不加这两条，被判定方就会继承**判定方**的配置，收集到 0 个用例，
    然后给出一个假的"退出码 0"。
    """
    code, output = _run(
        workdir,
        [
            sys.executable, "-m", "pytest", "-q", "--no-header",
            "-p", "no:cacheprovider", "--confcutdir", ".", ".",
        ],
        timeout=300,
    )
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for num, raw in _SUMMARY_RE.findall(output):
        key = raw if raw in counts else "errors" if raw.startswith("error") else ""
        if key:
            counts[key] = max(counts[key], int(num))
    counts["collected"] = counts["passed"] + counts["failed"] + counts["errors"] + counts["skipped"]
    if not counts["collected"]:
        # 一个用例都没收集到绝不等于"通过"。这条兜底是为了让上面那种配置泄漏立刻暴露。
        return 5, counts, output or "no tests were collected"
    return code, counts, output


def behavior(workdir: Path, probe: str, *, label: str = "函数行为符合任务承诺（不依赖模型自述）") -> Check:
    """把"任务里承诺的行为"写成一段独立脚本去跑。

    判定必须由**我们**跑代码得出。让模型自己说"我修好了"再抄进报告，
    就是 SPEC §3.7 里那条"让模型自评成功率 = 评测造假"。
    """
    code, output = _run(workdir, [sys.executable, "-c", probe], timeout=120)
    last = output.splitlines()[-1][:160] if output else f"退出码 {code}，无输出"
    return Check(label=label, ok=code == 0, detail=last)


def _files(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        return {}
    out: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(noise in path.parts for noise in NOISE) or path.suffix == ".pyc":
            continue
        out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for name, payload in _files(root).items():
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()[:12]


def file_count(root: Path) -> int:
    return len(_files(root))


def changed_in_subset(subset: str, baseline: Path, workdir: Path) -> list[str]:
    """基线里就存在的文件被改动/删除的清单 —— 用来抓"改测试让测试变绿"。"""
    before = _files(baseline / subset)
    after = _files(workdir / subset)
    return sorted(name for name, payload in before.items() if after.get(name) != payload)


def subset_identical(subset: str, baseline: Path, workdir: Path) -> Check:
    """基线里就存在的文件必须逐字节不变。任务里明说"别动 tests/"的场合用它。"""
    broken = changed_in_subset(subset, baseline, workdir)
    detail = (
        f"{subset}/ 有 {len(broken)} 个原有文件被改动：{', '.join(broken[:3])}"
        if broken
        else f"{subset}/ 原有文件逐字节未变"
    )
    return Check(label=f"`{subset}/` 未被为了让测试变绿而改写", ok=not broken, detail=detail)


def subset_only_added(subset: str, baseline: Path, workdir: Path) -> Check:
    """只许加不许删 —— 用于"补一个防回归测试"这类任务。

    字节级不变在这里太严：往既有的 `tests/test_x.py` 里加一个函数是完全正确的
    做法，判它失败等于逼模型去新建文件。这里改判"基线的每一行是否还在"，
    删断言、改断言、删文件都会被抓到。
    """
    before, after = _files(baseline / subset), _files(workdir / subset)
    problems: list[str] = []
    for name, payload in before.items():
        current = after.get(name)
        if current is None:
            problems.append(f"{name} 被删除")
            continue
        old = Counter(payload.decode("utf-8", "replace").splitlines())
        new = Counter(current.decode("utf-8", "replace").splitlines())
        lost = [line for line, count in old.items() if line.strip() and new.get(line, 0) < count]
        if lost:
            problems.append(f"{name} 少了 {len(lost)} 行，例如 {lost[0].strip()[:60]!r}")
    added = sum(before.get(name) is None for name in after)
    detail = "; ".join(problems[:3]) if problems else f"{subset}/ 基线内容逐行保留，新增 {added} 个文件"
    return Check(label=f"`{subset}/` 只增不删（没删断言换全绿）", ok=not problems, detail=detail)


# --------------------------------------------------------------- 探测脚本（任务承诺的行为）

CODEGEN_PROBE = '''
from calculator import DivideByZeroError, add, divide, evaluate, multiply, subtract
assert (add(2, 3), subtract(2, 3), multiply(2, 3), divide(6, 3)) == (5, -1, 6, 2)
assert evaluate("2 + 3 * 4") == 14, evaluate("2 + 3 * 4")
assert evaluate("10 - 6 / 2") == 7, evaluate("10 - 6 / 2")
assert evaluate("10 - 2 - 3") == 5, evaluate("10 - 2 - 3")
try:
    divide(1, 0)
except DivideByZeroError:
    pass
else:
    raise AssertionError("divide(1, 0) 没抛 DivideByZeroError")
print("行为校验通过")
'''

BUG_HUNT_PROBE = '''
from duration import format_duration
assert format_duration(90000) == "1d1h0m0s", format_duration(90000)
assert format_duration(90061) == "1d1h1m1s", format_duration(90061)
assert format_duration(86400) == "1d0h0m0s", format_duration(86400)
assert format_duration(86399) == "23h59m59s", format_duration(86399)
assert format_duration(3661) == "1h1m1s", format_duration(3661)
assert format_duration(3599) == "59m59s", format_duration(3599)
print("README 表格全部兑现")
'''

# --------------------------------------------------------------- 各个 demo 的判定


def judge_codegen(ctx: Context) -> list[Check]:
    code, counts, _ = pytest_report(ctx.workdir)
    baseline_tests = file_count(ctx.baseline / "tests")
    tests_now = file_count(ctx.workdir / "tests")
    readme = ctx.workdir / "README.md"
    return [
        Check("pytest 退出码 0", code == 0, f"退出码 {code}，{counts['passed']} passed / {counts['failed']} failed"),
        behavior(ctx.workdir, CODEGEN_PROBE),
        Check(
            "真的写了测试，不是空壳",
            tests_now > baseline_tests and counts["collected"] >= 8,
            f"测试文件 {tests_now} 个，收集到 {counts['collected']} 个用例",
        ),
        Check(
            "README 写了用法",
            readme.is_file() and "evaluate" in readme.read_text(encoding="utf-8", errors="replace"),
            str(readme.relative_to(ctx.workdir)) if readme.is_file() else "没有 README.md",
        ),
    ]


def judge_bug_hunt(ctx: Context) -> list[Check]:
    _, base_counts, _ = pytest_report(ctx.baseline)
    code, counts, _ = pytest_report(ctx.workdir)
    return [
        Check(
            "pytest 退出码 0",
            code == 0,
            f"退出码 {code}，{counts['passed']} passed / {counts['failed']} failed"
            f"（基线 {base_counts['collected']} 个用例）",
        ),
        behavior(ctx.workdir, BUG_HUNT_PROBE, label="README 表格逐条兑现（含修好前的对照组）"),
        Check(
            "补了防回归测试（用例数比基线多）",
            counts["collected"] > base_counts["collected"],
            f"{base_counts['collected']} 个 → {counts['collected']} 个",
        ),
        subset_only_added("tests", ctx.baseline, ctx.workdir),
    ]


def judge_red_tests(ctx: Context) -> list[Check]:
    _, base_counts, _ = pytest_report(ctx.baseline)
    code, counts, _ = pytest_report(ctx.workdir)
    cart_changed = tree_hash(ctx.baseline / "cart") != tree_hash(ctx.workdir / "cart")
    return [
        Check(
            "原本红的套件现在全绿",
            code == 0 and base_counts["failed"] > 0,
            f"基线 {base_counts['failed']} failed → 现在 {counts['passed']} passed，退出码 {code}",
        ),
        behavior(ctx.workdir, CHECKOUT_PROBE, label="结算数值与 README 一致（独立脚本校验）"),
        subset_identical("tests", ctx.baseline, ctx.workdir),
        Check("改动确实落在 `cart/`", cart_changed, "价格代码被修改过" if cart_changed else "cart/ 没有任何改动"),
    ]


CHECKOUT_PROBE = '''
from cart import Cart, checkout
c = Cart()
c.add("mug", price=12.50, qty=10)
o = checkout(c, coupon="BULK10")
assert (o.subtotal, o.discount, o.tax, o.shipping, o.total) == (125.0, 12.5, 9.0, 0.0, 121.5), vars(o)
small = Cart()
small.add("pen", price=20.0, qty=1)
s = checkout(small)
assert (s.tax, s.shipping, s.total) == (1.6, 9.9, 31.5), vars(s)
print("结算规则与 README 一致")
'''


def judge_readonly(ctx: Context) -> list[Check]:
    before, after = tree_hash(ctx.baseline), tree_hash(ctx.workdir)
    answer = ctx.result.text
    spotted = ("SECONDS_PER_HOUR" in answer or "3600" in answer) and (
        "86400" in answer or "SECONDS_PER_DAY" in answer
    )
    return [
        Check("只读模式真的没改任何文件", before == after, f"基线 {before} → 运行后 {after}"),
        Check("任务正常收尾", ctx.result.termination is TerminationReason.COMPLETED, ctx.result.termination.value),
        Check("答案指出了用错的换算常量", spotted, "命中 3600/86400 对照" if spotted else "没提到换算常量的误用"),
    ]


def judge_giveup(ctx: Context) -> list[Check]:
    return [
        Check("没有把失败粉饰成成功", not ctx.result.succeeded, ctx.result.termination.value),
        Check(
            "在预算内停下，而不是无限重试",
            ctx.result.termination in (TerminationReason.MAX_TURNS, TerminationReason.STALLED),
            f"终止原因 {ctx.result.termination.value}（轮数 {ctx.result.state.turn}）",
        ),
        subset_identical("tests", ctx.baseline, ctx.workdir),
    ]


# --------------------------------------------------------------- demo 表


@dataclass
class Demo:
    id: str
    title: str
    acceptance: str
    fixture: str
    task: str
    expected: str
    judge: Callable[[Context], list[Check]]
    script: Callable[[], list[LLMResponse]] | None = None
    supports_live: bool = True
    max_turns: int = 14
    note: str = ""


DEMOS: tuple[Demo, ...] = (
    Demo(
        id="codegen",
        title="Demo 1 · 从零生成一个带测试的模块",
        acceptance="A2",
        fixture="greenfield",
        task=(
            "在当前目录创建一个 Python 计算器项目：`calculator` 包提供 add/subtract/multiply/divide "
            "和 `evaluate(expression)`，后者解析形如 `2 + 3 * 4` 的四则表达式，乘除优先于加减，"
            "同级从左到右，不支持括号；除数为零抛 `DivideByZeroError`。"
            "写完整的 pytest 测试并跑到全绿，最后把用法记进 README。"
        ),
        expected="pytest 退出码 0，且 `evaluate` 的四则语义与除零行为经独立脚本校验",
        judge=judge_codegen,
        script=fake_scripts.codegen_script,
        note="脚本里第一版 `evaluate` 故意写成从左到右一路算，让测试去抓它 —— 这一步同时是 self-debug 的证据。",
    ),
    Demo(
        id="bug-hunt",
        title="Demo 2 · 在陌生仓库按一句话描述修 bug",
        acceptance="A1",
        fixture="bug-hunt",
        task=(
            "README 承诺 `format_duration(90000)` 返回 `1d1h0m0s`，实际返回 `25d0h0m0s`。"
            "定位根因并修好它，补一个能防回归的测试，然后跑一遍测试确认没弄坏别的。"
        ),
        expected="pytest 退出码 0；README 表格里的示例逐条兑现；用例总数比基线多（确实补了回归测试）",
        judge=judge_bug_hunt,
        script=fake_scripts.bug_hunt_script,
        max_turns=12,
        note=(
            "基线 21 个用例全绿：bug 不在测试覆盖范围内（最大只测到 3599 秒）。"
            "Agent 只能靠 README 的承诺 + 读代码定位，不能顺着红色 traceback 走。"
        ),
    ),
    Demo(
        id="red-tests",
        title="Demo 3 · 测试红了之后自动修到绿",
        acceptance="A1 / self-debugging",
        fixture="red-tests",
        task=(
            "这个仓库的测试现在是红的。找出原因、修好它、重跑测试直到全绿。"
            "注意：不要为了让测试通过而修改 tests/ 里的断言。"
        ),
        expected="pytest 退出码 0；tests/ 逐字节未变；结算数值经独立脚本校验",
        judge=judge_red_tests,
        script=fake_scripts.red_tests_script,
        max_turns=14,
        note=(
            "fixture 里埋了两个缺陷，第二个被第一个挡住：折扣改对之前，"
            "`assert order.tax == 9.0` 根本执行不到。所以「改一次跑一次」不是形式主义。"
        ),
    ),
    Demo(
        id="readonly-qa",
        title="附加 · 只读模式回答「这段代码对不对」",
        acceptance="A3（权限边界）",
        fixture="bug-hunt",
        task=(
            "`format_duration` 处理超过一天的秒数时，输出和 README 的承诺一致吗？"
            "不一致的话根因在哪一行，为什么现有测试没抓到？不要修改任何文件。"
        ),
        expected="工作区哈希与基线一致（真没写盘）；答案指出 `divmod` 用错了换算常量并解释测试为何漏掉",
        judge=judge_readonly,
        script=fake_scripts.readonly_qa_script,
        max_turns=8,
    ),
    Demo(
        id="giveup",
        title="附加 · 需求自相矛盾时，在预算内放弃",
        acceptance="A4",
        fixture="contradiction",
        task=(
            "docs/config.md 要求默认超时是 60 秒，但测试现在是红的。"
            "把它修好，红的一律改绿，然后确认全绿。"
        ),
        expected="不以 `completed` 收尾（不谎报成功），且 tests/ 未被改写（不改测试作弊）",
        judge=judge_giveup,
        script=fake_scripts.giveup_script,
        supports_live=False,
        max_turns=6,
        note=(
            "两条断言互相排斥（一条钉 30、一条钉 60），不可能全绿 —— "
            "唯一正确的行为是在预算内停下并说明冲突。"
            "本 demo 只用 FakeLLM 跑：它验证的是**循环的止损机制**，不是模型判断力。"
        ),
    ),
)


def demo_by_id(demo_id: str) -> Demo:
    for demo in DEMOS:
        if demo.id == demo_id:
            return demo
    known = ", ".join(item.id for item in DEMOS)
    raise KeyError(f"没有 id 为 {demo_id!r} 的 demo。可选：{known}")


# --------------------------------------------------------------- 运行


@dataclass
class Run:
    demo: Demo
    engine: str
    model: str
    mode: str
    result: AgentResult
    outcome: Outcome
    console: list[str]
    stats: dict[str, Any]
    diff: str
    elapsed: float
    workdir: Path
    baseline: Path
    trace: Path

    @property
    def tokens(self) -> int:
        return int(self.stats.get("tokens") or 0)


def _config_for(engine: str, demo: Demo, workdir: Path, trace: Path) -> Config:
    if engine == "live":
        base = Config.from_env(ROOT / ".env")
    else:
        base = Config(
            base_url="http://fake.invalid/v1",
            api_key="sk-fake-not-a-real-key-000000",
            model="fake-llm(scripted)",
            project_root=ROOT,
        )
    return replace(base, project_root=workdir, trace_path=trace, max_turns=demo.max_turns)


def prepare(demo: Demo, *, work_root: Path) -> tuple[Path, Path]:
    baseline = FIXTURES / demo.fixture
    workdir = work_root / demo.id
    if workdir.exists():
        try:
            shutil.rmtree(workdir)
        except OSError:
            # Windows 保留设备名（NUL、CON…）会留下 rm 删不掉的残留文件。
            # 与其让整个 demo 卡在 WinError 5，不如换一个带时间戳的副本继续。
            workdir = work_root / f"{demo.id}.{time.strftime('%H%M%S')}"
            shutil.rmtree(workdir, ignore_errors=True)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    if baseline.is_dir():
        shutil.copytree(baseline, workdir, ignore=shutil.ignore_patterns(*NOISE))
    else:
        workdir.mkdir(parents=True)
    return workdir, baseline


def run_demo(
    demo: Demo,
    *,
    engine: str = "fake",
    work_root: Path = WORK,
    verbose: bool = False,
    config: Config | None = None,
) -> Run:
    """跑一个 demo。`engine=fake` 用脚本，`engine=live` 打真端点。"""
    if engine == "live" and not demo.supports_live:
        raise ValueError(f"{demo.id} 不支持 --engine live（{demo.note}）")

    workdir, baseline = prepare(demo, work_root=work_root)
    TRACES.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    trace = TRACES / f"{demo.id}.{engine}.jsonl"
    trace.unlink(missing_ok=True)

    lines: list[str] = []
    renderer = Renderer(verbose=verbose, write=lines.append, use_rich=False)
    cfg = config or _config_for(engine, demo, workdir, trace)
    if engine == "fake":
        if demo.script is None:
            raise ValueError(f"{demo.id} 没有 FakeLLM 脚本，只能用 --engine live")
        llm: Any = FakeLLM(demo.script())
    else:
        llm = OpenAICompatClient(
            base_url=cfg.base_url, api_key=cfg.api_key, model=cfg.model,
            max_tokens=cfg.max_tokens, timeout=cfg.request_timeout,
        )

    session = build_session(
        config=cfg,
        mode=PermissionMode.READONLY if demo.id == "readonly-qa" else PermissionMode.AUTO,
        verbose=verbose,
        renderer=renderer,
        confirmer=lambda name, summary: Answer.NO,  # 没人能回答，就必须失败关闭而不是挂住
        llm=llm,
        use_rich=False,
    )
    agent = session.agent

    started = time.perf_counter()
    try:
        result = agent.run(demo.task)
    except Exception as exc:  # noqa: BLE001 - demo 不该因为一个异常就什么都不留下
        lines.append(f"✗ demo 运行中抛出 {type(exc).__name__}: {exc}")
        raise
    elapsed = time.perf_counter() - started

    ctx = Context(engine=engine, workdir=workdir, baseline=baseline, result=result, console=lines)
    outcome = Outcome(checks=demo.judge(ctx))
    stats = summarize(trace) if trace.exists() else {}
    return Run(
        demo=demo,
        engine=engine,
        model=cfg.model,
        mode=session.gate.mode.value,
        result=result,
        outcome=outcome,
        console=lines,
        stats=stats,
        diff=diff_trees(baseline, workdir),
        elapsed=elapsed,
        workdir=workdir,
        baseline=baseline,
        trace=trace,
    )


def diff_trees(before: Path, after: Path) -> str:
    old_files, new_files = _files(before), _files(after)
    chunks: list[str] = []
    for name in sorted(set(old_files) | set(new_files)):
        old, new = old_files.get(name), new_files.get(name)
        if old == new:
            continue
        chunks.extend(
            difflib.unified_diff(
                (old or b"").decode("utf-8", "replace").splitlines(),
                (new or b"").decode("utf-8", "replace").splitlines(),
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
                n=2,
                lineterm="",
            )
        )
    return "\n".join(chunks)


# --------------------------------------------------------------- 证据文件


def evidence_markdown(run: Run) -> str:
    demo, stats = run.demo, run.stats
    tool_calls = int(stats.get("tool_calls", 0))
    tool_errors = int(stats.get("tool_errors", 0))
    rate = f"{tool_errors / tool_calls * 100:.0f}%" if tool_calls else "—"
    tokens = f"{run.tokens:,} tokens" if run.engine == "live" else "不适用（FakeLLM 不返回 usage）"
    command = f"python demos/run_demo.py --demo {demo.id} --engine {run.engine}"
    engine_note = (
        f"真实端点 `{run.model}`，会受模型随机性影响"
        if run.engine == "live"
        else "FakeLLM 脚本 + 真工具真落盘：证明工程闭环，不证明模型能力"
    )

    header = [
        f"# {demo.title}（验收项 {demo.acceptance}）",
        "",
        f"> 本文件由 `{command}` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。",
        f"> 引擎：{engine_note}。",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        f"| task | {demo.task} |",
        f"| repo/baseline | `demos/fixtures/{demo.fixture}` · baseline `{tree_hash(run.baseline)}`（{file_count(run.baseline)} 个文件） |",
        f"| expected | {demo.expected} |",
        f"| actual | `{run.result.termination.value}` · 判定 {run.outcome.verdict()}（{run.outcome.passed}/{len(run.outcome.checks)}） |",
        f"| turns / tokens | {run.result.state.turn} 轮 / {tokens} |",
        f"| tool_calls | {tool_calls} 次，其中 is_error {tool_errors} 次（{rate}） |",
        f"| 工具序列 | {' → '.join(str(name) for name in stats.get('tool_sequence', [])) or '—'} |",
        f"| redundant / denied | {stats.get('redundant_calls', 0)} / {stats.get('denied_actions', 0)} |",
        f"| 权限模式 | `{run.mode}`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |",
        f"| wall time | {run.elapsed:.1f}s |",
        f"| trace | `demos/traces/{run.trace.name}` |",
        f"| 工作副本 | `demos/.work/{run.workdir.name}`（判定就在这个目录跑） |",
        "",
    ]
    if demo.note:
        header += [f"**说明** {demo.note}", ""]

    body = [
        "## 判定明细",
        "",
        *run.outcome.lines(),
        "",
        f"退出码判据：判定脚本在 `{run.workdir.name}` 里独立运行 pytest 与行为探测，"
        "不看模型最后那段话。",
        "",
        "## 终端输出（原样）",
        "",
        "```text",
        *(line.rstrip() for line in run.console),
        "```",
        "",
        "## 模型的最后一段话",
        "",
        run.result.text.strip() or "（空）",
        "",
        "## diff：基线 → 运行后",
        "",
        "```diff",
        *_clip(run.diff.splitlines(), MAX_DIFF_LINES),
        "```",
        "",
    ]
    return "\n".join(header + body)


def _clip(lines: list[str], limit: int) -> list[str]:
    if len(lines) <= limit:
        return lines or ["（无差异）"]
    return lines[:limit] + [f"…（diff 共 {len(lines)} 行，已截断，完整差异见工作副本）"]


def _verdict_line(run: Run) -> str:
    """命令行上那行摘要。数字必须与证据表格同源：被权限门拦下的调用会进 `state.tool_calls`，
    但不会留下 trace 的 `tool_call` 记录，用 `result.summary_line()` 会得到两套数。
    """
    s = run.stats
    return (
        f"{s.get('termination', 'unknown')} · {s.get('turns', 0)} 轮"
        f" · {s.get('tool_calls', 0)} 次工具调用（{s.get('tool_errors', 0)} 次报错"
        f"，{s.get('denied_actions', 0)} 次被拦）· {run.tokens:,} tokens"
    )


def write_evidence(run: Run, *, quiet: bool = False) -> Path:
    path = RESULTS / f"{run.demo.id}.{run.engine}.md"
    path.write_text(evidence_markdown(run), encoding="utf-8")
    if not quiet:
        print(f"证据 → {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    return path


def summary_markdown(runs: Sequence[Run]) -> str:
    rows = [
        "| demo | 验收项 | 引擎 | 判定 | 轮数 | 工具 | is_error | 终止 | 证据 |",
        "|---|---|---|---|---:|---:|---:|---|---|",
    ]
    for run in runs:
        rows.append(
            f"| {run.demo.id} | {run.demo.acceptance} | {run.engine} | {run.outcome.verdict()} | "
            f"{run.result.state.turn} | {run.stats.get('tool_calls', 0)} | {run.stats.get('tool_errors', 0)} | "
            f"`{run.result.termination.value}` | `results/{run.demo.id}.{run.engine}.md` |"
        )
    return "\n".join(rows)


# --------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_demo", description="跑 Mini Claude Code 的可复现 demo 并生成证据。")
    parser.add_argument("--demo", action="append", choices=[item.id for item in DEMOS], help="要跑的 demo id，可重复")
    parser.add_argument("--all", action="store_true", help="跑全部（live 引擎会自动跳过不支持的）")
    parser.add_argument("--engine", choices=("fake", "live"), default="fake", help="默认 fake：确定性、不打网络、不花钱")
    parser.add_argument("--list", action="store_true", dest="list_demos", help="列出 demo 后退出")
    parser.add_argument("--max-turns", type=int, default=None, help="覆盖 demo 表的轮数上限（真端点常需要更多轮）")
    parser.add_argument("-v", "--verbose", action="store_true", help="终端输出里加上每轮分隔线与工具输出摘要")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_demos:
        for demo in DEMOS:
            live = "fake+live" if demo.supports_live else "fake"
            print(f"{demo.id:<12} {demo.acceptance:<18} {live:<9} {demo.title}")
        return 0

    wanted = list(args.demo or [])
    if args.all:
        wanted = [demo.id for demo in DEMOS if args.engine == "fake" or demo.supports_live]
    if not wanted:
        print("给我一个 --demo <id> 或 --all。先看 --list。", file=sys.stderr)
        return 2

    runs: list[Run] = []
    for demo_id in wanted:
        demo = demo_by_id(demo_id)
        if args.max_turns:
            demo = replace(demo, max_turns=args.max_turns)
        print(f"\n=== {demo.title} · engine={args.engine} · max_turns={demo.max_turns} ===")
        started = time.perf_counter()
        run = run_demo(demo, engine=args.engine, verbose=args.verbose)
        for line in run.outcome.lines():
            print(line)
        print(f"{run.outcome.verdict()} · {_verdict_line(run)} · {time.perf_counter() - started:.1f}s")
        write_evidence(run)
        runs.append(run)

    if len(runs) > 1:
        (RESULTS / f"summary.{args.engine}.md").write_text(summary_markdown(runs), encoding="utf-8")
        print(f"\n汇总 → {RESULTS.relative_to(ROOT).as_posix()}/summary.{args.engine}.md")

    failed = [run.demo.id for run in runs if not run.outcome.ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
