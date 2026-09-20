import pytest

from cart import COUPONS, Cart, checkout
from cart.pricing import discount_for, shipping_for, taxable_amount


def make_cart(*items):
    cart = Cart()
    for sku, price, qty in items:
        cart.add(sku, price=price, qty=qty)
    return cart


def test_empty_cart_still_pays_shipping():
    order = checkout(Cart())
    assert (order.subtotal, order.discount, order.tax, order.shipping) == (0.0, 0.0, 0.0, 9.9)
    assert order.total == 9.9


def test_small_cart_pays_flat_shipping():
    order = checkout(make_cart(("mug", 20.0, 1)))
    assert order.tax == 1.6
    assert order.shipping == 9.9
    assert order.total == 31.5


def test_free_shipping_at_the_threshold():
    order = checkout(make_cart(("mug", 50.0, 2)))
    assert shipping_for(100.0) == 0.0
    assert order.shipping == 0.0
    assert order.total == 108.0


def test_unknown_coupon_is_rejected():
    with pytest.raises(ValueError, match="未知优惠券"):
        checkout(make_cart(("mug", 20.0, 1)), coupon="PI Rate-day")


def test_welcome_coupon_is_five_percent():
    assert COUPONS["WELCOME"] == 0.05
    assert discount_for(80.0, "welcome") == 4.0


def test_tax_base_is_after_discount():
    assert taxable_amount(125.0, 12.5) == 112.5


def test_bulk_purchase_checkout():
    """README 的示例：10 件 12.50，用 BULK10 打 9 折。"""
    order = checkout(make_cart(("mug", 12.50, 10)), coupon="BULK10")
    assert order.subtotal == 125.0
    assert order.discount == 12.5
    assert order.tax == 9.0
    assert order.shipping == 0.0
    assert order.total == 121.5
