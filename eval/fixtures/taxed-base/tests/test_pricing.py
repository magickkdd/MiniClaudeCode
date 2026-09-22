"""结算逻辑的既有覆盖。注意：`region` 与 `discount` 从来没被同时测过。"""

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
