import pytest

from duration import format_duration


def test_under_a_minute():
    assert format_duration(0) == "0s"
    assert format_duration(45) == "45s"


def test_whole_and_partial_minutes():
    assert format_duration(60) == "1m0s"
    assert format_duration(119) == "1m59s"
    assert format_duration(600) == "10m0s"


def test_just_under_an_hour():
    assert format_duration(3599) == "59m59s"


@pytest.mark.parametrize("bad", [-1, -3600])
def test_negative_raises(bad):
    with pytest.raises(ValueError):
        format_duration(bad)
