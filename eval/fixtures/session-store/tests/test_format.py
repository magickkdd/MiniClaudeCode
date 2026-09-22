from __future__ import annotations

import pytest

from store import humanize


@pytest.mark.parametrize("seconds,expected", [(45, "45s"), (0, "0s"), (59, "59s")])
def test_humanize_below_a_minute(seconds: int, expected: str):
    assert humanize(seconds) == expected


def test_humanize_minutes_and_seconds():
    assert humanize(125) == "2m5s"


def test_humanize_exact_minute_keeps_the_seconds():
    assert humanize(60) == "1m0s"


def test_humanize_full_form():
    assert humanize(3725) == "1h2m5s"
