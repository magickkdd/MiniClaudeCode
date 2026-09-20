"""给人看的订单摘要，小票打印用。"""

from __future__ import annotations

from .pricing import Order


def money(value: float) -> str:
    return f"{value:.2f}"


def describe_order(order: Order) -> str:
    rows = [
        f"小计    {money(order.subtotal)}",
        f"折扣   -{money(order.discount)}",
        f"税         {money(order.tax)}",
        f"运费     {money(order.shipping)}",
        f"合计    {money(order.total)}",
    ]
    return "\n".join(rows)
