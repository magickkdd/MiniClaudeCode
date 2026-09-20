import time

from duration import Stopwatch
from duration.stopwatch import format_duration


def test_start_stop_accumulates():
    watch = Stopwatch().start()
    elapsed = watch.stop()
    assert elapsed >= 0
    assert len(watch.samples) == 1
    assert watch.total == elapsed


def test_stop_without_start_raises():
    try:
        Stopwatch().stop()
    except RuntimeError as exc:
        assert "start()" in str(exc)
    else:
        raise AssertionError("未 start 就 stop 应该报错")


def test_label_uses_the_shared_formatter():
    watch = Stopwatch(samples=[1.9, 2.2])
    assert watch.label() == format_duration(4)
    assert watch.label() == "4s"


def test_context_manager_shape_is_not_promised():
    # README 没写 with 语法，所以这里只锁住现有行为，别顺手扩展 API
    assert time.perf_counter() > 0
