from __future__ import annotations

from report import Row, money, top_region, totals_by_region

ROWS = [Row("eu", 820.0), Row("us", 430.0), Row("eu", 0.0)]


def test_totals_group_by_region():
    assert totals_by_region(ROWS) == {"eu": 820.0, "us": 430.0}


def test_top_region_picks_largest():
    assert top_region({"eu": 820.0, "us": 900.0}) == ("us", 900.0)


def test_top_region_breaks_ties_alphabetically():
    assert top_region({"us": 10.0, "eu": 10.0}) == ("eu", 10.0)


def test_money_uses_thousands_separator():
    assert money(1250.0) == "1,250.00"


def test_totals_of_no_rows_is_empty():
    assert totals_by_region([]) == {}
