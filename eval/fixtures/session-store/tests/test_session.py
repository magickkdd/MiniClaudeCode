from __future__ import annotations

from store import Session


def test_touch_restarts_the_clock():
    session = Session("a", ttl_minutes=30, created=0.0)
    later = session.touch(100.0)
    assert later.sid == "a"
    assert later.created == 100.0
    assert later.expires_at() > session.expires_at()


def test_expired_session_reports_zero_remaining():
    assert Session("a", ttl_minutes=1, created=0.0).remaining(5000) == 0.0


def test_expires_at_uses_minutes():
    assert Session("abc", ttl_minutes=30, created=0.0).expires_at() == 1800.0


def test_remaining_counts_minutes():
    assert Session("abc", ttl_minutes=30, created=0.0).remaining(600) == 1200.0
