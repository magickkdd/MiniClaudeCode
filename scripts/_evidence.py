"""把证据写进 `eval/results/` 之前，先跟盘上那份比一次。

README §10 第 27 行记着这类缺陷的第一次：脚本重跑一遍，把已入库的证据悄悄削薄，
而文件的形状看起来仍然完整 —— `pass: true`、`schema: 1` 一个不少，掉下去的是覆盖范围。
第 30 行记着它的修法只落在一个脚本上。这个模块是那条账的收口：一次比较、一份契约，
任何写证据的脚本都过这里。

为什么只比"数字变小了没有"：变薄的方式无穷多种（少一个文件、少一条判据、少一次核对），
逐种写法去堵是打地鼠。可比较的计数是这些写法共同的下游 —— 覆盖范围缩水，计数必掉。
比例数字反而会更漂亮，所以这道守卫不能靠"看这份证据像不像完整的"，只能靠比。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

# 计数掉到盘上那份的九成以下算变薄。这个容差留给"同一批东西重新测一遍"的抖动
# （时长、重试次数、探针靶子换了），不给覆盖范围。
TOLERANCE = 0.9

# 这些键一个都不许掉：少一份轨迹、少一条判据、少一次核对，都是实验对象换了。
EXACT_DEFAULT: frozenset[str] = frozenset()

FLAG = "--allow-thinning"


def _shrink_lines(before: Mapping[str, Any], after: Mapping[str, Any], exact: Iterable[str]) -> list[str]:
    """两把尺子量同一批键：只在盘上那份也有这个键时比。

    新加的键不比 —— 一个上次测不出来的量，不该因为"上次是 0"就被判成变薄。
    """
    hard = set(exact)
    lines = []
    for key, new in after.items():
        old = before.get(key)
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            continue
        floor = old * (1.0 if key in hard else TOLERANCE)
        if new < floor:
            lines.append(f"{key}：{_fmt(old)} → {_fmt(new)}")
    return lines


def _fmt(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) else f"{value:g}"


def refuse(path: Path, lines: list[str], note: str) -> str:
    override = f"确有其事（换了判据、换了科目）就加 {FLAG}，并在 SPEC/README 写明为什么。"
    return (
        f"✗ {path.name} 已入库的证据这次变薄了：" + "；".join(lines)
        + "\n  覆盖范围缩水会让所有比例数字变好看 —— 拒绝覆盖。"
        + (f"\n  {note}" if note else "")
        + f"\n  {override}"
    )


def write_evidence(
    result: Path,
    payload: dict[str, Any],
    metrics: Callable[[dict[str, Any]], Mapping[str, Any]],
    *,
    exact: Iterable[str] = EXACT_DEFAULT,
    allow_thinning: bool = False,
    note: str = "",
    sort_keys: bool = False,
) -> bool:
    """守卫 + 写盘。返回 False 表示已拒绝写盘（调用方应非 0 退出）。

    盘上没这份 → 第一次签字，不比。`allow_thinning` 放行时照写，但把缩水条目打到
    stderr —— 绕过守卫的人应当看得见自己绕过了什么。
    """
    lines: list[str] = []
    if result.exists():
        prior = json.loads(result.read_text(encoding="utf-8"))
        lines = _shrink_lines(metrics(prior), metrics(payload), exact)
    if lines and not allow_thinning:
        print(refuse(result, lines, note), file=sys.stderr)
        return False
    if lines:
        print(f"! {FLAG}：证据变薄仍然写盘 —— " + "；".join(lines), file=sys.stderr)
    result.parent.mkdir(parents=True, exist_ok=True)
    # 写字节、结尾一个 \n：文本模式在 Windows 上会把 \n 变成 \r\n，同一份 JSON 于是两个指纹。
    # 证据文件要能被逐字节复算，这一条对所有脚本都成立，不只是带 sha 的那份。
    result.write_bytes(
        (json.dumps(payload, ensure_ascii=False, indent=2, default=str, sort_keys=sort_keys) + "\n").encode(
            "utf-8"
        )
    )
    return True
