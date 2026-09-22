"""把秒数写成人话。"""

from __future__ import annotations


def humanize(seconds: float) -> str:
    """`3725 → "1h2m5s"`。每一段非零都要出现，分钟后面的秒也要写。"""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
        return "".join(parts)
    if secs or not parts:
        parts.append(f"{secs}s")
    return "".join(parts)
