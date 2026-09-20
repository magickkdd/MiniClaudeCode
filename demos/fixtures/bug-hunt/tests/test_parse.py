import pytest

from duration import parse_duration


def test_single_units():
    assert parse_duration("45s") == 45
    assert parse_duration("10m") == 600
    assert parse_duration("3h") == 10800
    assert parse_duration("2d") == 172800


def test_compound_order_does_not_matter():
    assert parse_duration("1h30m") == 5400
    assert parse_duration("30m 1h") == 5400
    assert parse_duration("1d2h3m4s") == 93784


def test_uppercase_accepted():
    assert parse_duration("1H30M") == 5400


@pytest.mark.parametrize("bad", ["", "   ", "abc", "90", "1h30x"])
def test_garbage_raises(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)
