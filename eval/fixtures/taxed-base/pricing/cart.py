"""结算：小计 → 折扣 → 税。"""

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
