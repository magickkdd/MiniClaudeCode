from __future__ import annotations

from pathlib import Path

import pytest

from report import Row, parse_amount, read_rows

CSV = """region,amount,label
eu,"1,200.50",键盘
eu,€820,显示器
 us , 430.00 ,鼠标
"""


def test_parse_amount_plain_number():
    assert parse_amount("12.5") == 12.5


def test_parse_amount_thousands_separator():
    assert parse_amount("1,200.50") == 1200.5


def test_parse_amount_currency_and_padding():
    assert parse_amount("€820") == 820.0
    assert parse_amount(" 430.00 ") == 430.0


def test_parse_amount_rejects_garbage():
    with pytest.raises(ValueError):
        parse_amount("无")


def test_read_rows_normalises_regions(tmp_path: Path):
    path = tmp_path / "sales.csv"
    path.write_text(CSV, encoding="utf-8")
    assert read_rows(path) == [
        Row("eu", 1200.5, "键盘"),
        Row("eu", 820.0, "显示器"),
        Row("us", 430.0, "鼠标"),
    ]
