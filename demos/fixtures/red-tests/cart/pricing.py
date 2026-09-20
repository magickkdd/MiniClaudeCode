"""价格规则与结算。文档见 README。"""

from __future__ import annotations

from dataclasses import dataclass, field

TAX_RATE = 0.08
SHIPPING_FLAT = 9.90
FREE_SHIPPING_FROM = 100.00

COUPONS = {"BULK10": 0.01, "WELCOME": 0.05}


@dataclass
class Line:
    sku: str
    price: float
    qty: int

    @property
    def amount(self) -> float:
        return round(self.price * self.qty, 2)


@dataclass
class Cart:
    lines: list[Line] = field(default_factory=list)

    def add(self, sku: str, price: float, qty: int = 1) -> None:
        price = float(price)
        qty = int(qty)
        if price <= 0:
            raise ValueError(f"price 必须为正数，收到 {price}")
        if qty <= 0:
            raise ValueError(f"qty 必须为正整数，收到 {qty}")
        for line in self.lines:
            if line.sku == sku:
                line.qty += qty
                return
        self.lines.append(Line(sku=sku, price=price, qty=qty))

    @property
    def subtotal(self) -> float:
        return round(sum(line.amount for line in self.lines), 2)

    @property
    def skus(self) -> list[str]:
        return [line.sku for line in self.lines]

    def __len__(self) -> int:
        return sum(line.qty for line in self.lines)


@dataclass
class Order:
    subtotal: float
    discount: float
    tax: float
    shipping: float

    @property
    def total(self) -> float:
        return round(self.subtotal - self.discount + self.tax + self.shipping, 2)


def shipping_for(subtotal: float) -> float:
    return 0.0 if subtotal >= FREE_SHIPPING_FROM else SHIPPING_FLAT


def discount_for(subtotal: float, coupon: str = "") -> float:
    code = (coupon or "").strip().upper()
    if not code:
        return 0.0
    if code not in COUPONS:
        raise ValueError(f"未知优惠券：{coupon}")
    return round(subtotal * COUPONS[code], 2)


def taxable_amount(subtotal: float, discount: float) -> float:
    return round(subtotal - discount, 2)


def checkout(cart: Cart, coupon: str = "") -> Order:
    subtotal = cart.subtotal
    discount = discount_for(subtotal, coupon)
    tax = round(subtotal * TAX_RATE, 2)
    return Order(
        subtotal=subtotal,
        discount=discount,
        tax=tax,
        shipping=shipping_for(subtotal),
    )
