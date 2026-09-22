"""README 里的三个算例。最后一个目前对不上 —— 折扣与税同时存在时的口径没人定过。"""

from __future__ import annotations

from pricing import total


def test_readme_subtotal_example():
    assert total([(2, 19.99), (1, 4.5)], region="none") == 44.48


def test_readme_uk_example():
    assert total([(1, 80.0)], region="uk") == 96.00


def test_readme_eu_discount_example():
    assert total([(1, 100.0)], region="eu", discount=0.10) == 110.70
