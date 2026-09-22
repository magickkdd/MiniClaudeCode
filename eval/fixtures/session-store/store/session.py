"""会话与剩余时间。`ttl_minutes` 的单位是分钟，别处都按这个口径写。"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Session:
    sid: str
    ttl_minutes: float
    created: float = 0.0

    def expires_at(self) -> float:
        """过期的时间戳（秒）。"""
        return self.created + self.ttl_minutes

    def remaining(self, now: float) -> float:
        """还剩多少秒；已经过期就是 0.0，不返回负数。"""
        return max(0.0, self.expires_at() - now)

    def is_expired(self, now: float) -> bool:
        return self.remaining(now) == 0.0

    def touch(self, now: float) -> "Session":
        """续期：以 `now` 为新的创建时间。"""
        return replace(self, created=now)
