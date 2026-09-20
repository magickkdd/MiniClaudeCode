import pytest

from cart import Cart


def test_add_creates_a_line():
    cart = Cart()
    cart.add("mug", price=12.5, qty=2)
    assert cart.subtotal == 25.0
    assert cart.skus == ["mug"]
    assert len(cart) == 2


def test_repeated_sku_merges_quantity():
    cart = Cart()
    cart.add("mug", price=12.5, qty=2)
    cart.add("mug", price=12.5, qty=3)
    assert cart.skus == ["mug"]
    assert len(cart) == 5
    assert cart.subtotal == 62.5


def test_multiple_lines_sum():
    cart = Cart()
    cart.add("mug", price=12.5, qty=2)
    cart.add("book", price=9.9, qty=1)
    assert cart.subtotal == 34.9
    assert cart.skus == ["mug", "book"]


def test_default_quantity_is_one():
    cart = Cart()
    cart.add("pen", price=1.0)
    assert len(cart) == 1


@pytest.mark.parametrize("price,qty", [(0, 1), (-1, 1), (1.0, 0), (1.0, -3)])
def test_rejects_non_positive_price_or_qty(price, qty):
    with pytest.raises(ValueError):
        Cart().add("x", price=price, qty=qty)
