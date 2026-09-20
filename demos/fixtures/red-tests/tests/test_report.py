from cart import Order, describe_order


def test_describe_order_lists_every_component():
    order = Order(subtotal=125.0, discount=12.5, tax=9.0, shipping=0.0)
    text = describe_order(order)
    assert "小计    125.00" in text
    assert "折扣   -12.50" in text
    assert "税         9.00" in text
    assert "合计    121.50" in text


def test_money_rounds_to_two_places():
    from cart.report import money

    assert money(1.005) == "1.00"
    assert money(2.5) == "2.50"
