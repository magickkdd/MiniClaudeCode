"""把秒数格式化成 `1d2h3m4s`。输出规则写在 README 的表格里。"""

from __future__ import annotations

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400


def format_duration(seconds: int) -> str:
    total = int(seconds)
    if total < 0:
        raise ValueError(f"seconds 不能为负：{seconds}")

    days, rest = divmod(total, SECONDS_PER_HOUR)
    hours, rest = divmod(rest, SECONDS_PER_HOUR)
    minutes, secs = divmod(rest, SECONDS_PER_MINUTE)

    if days:
        return f"{days}d{hours}h{minutes}m{secs}s"
    if hours:
        return f"{hours}h{minutes}m{secs}s"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"
