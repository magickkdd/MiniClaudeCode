"""按地区聚合，以及 README 承诺的那行摘要文本。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .load import Row


def totals_by_region(rows: Iterable[Row]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in rows:
        totals[row.region] = round(totals.get(row.region, 0.0) + row.amount, 2)
    return totals


def top_region(totals: dict[str, float]) -> tuple[str, float]:
    """合计最大的地区；平局取字典序最小的名字。空输入返回 `("无", 0.0)`。"""
    if not totals:
        return "无", 0.0
    name, amount = min(totals.items(), key=lambda item: (-item[1], item[0]))
    return name, amount


def money(value: float) -> str:
    return f"{value:,.2f}"


def describe(rows: Sequence[Row]) -> str:
    """`"3 行 · 总额 1,250.00 · 最高地区 eu（820.00）"` —— 格式见 README。

    还没实现。
    """
    raise NotImplementedError("describe() 待实现，格式定义在 README")
