"""一个薄秒表。批处理脚本用它统计各阶段耗时。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .format import format_duration


@dataclass
class Stopwatch:
    started_at: float | None = None
    samples: list[float] = field(default_factory=list)

    def start(self) -> "Stopwatch":
        self.started_at = time.perf_counter()
        return self

    def stop(self) -> float:
        if self.started_at is None:
            raise RuntimeError("必须先 start() 才能 stop()")
        elapsed = time.perf_counter() - self.started_at
        self.started_at = None
        self.samples.append(elapsed)
        return elapsed

    @property
    def total(self) -> float:
        return sum(self.samples)

    def label(self) -> str:
        """给人看的累计耗时。秒以下按 0 处理，报表里不出现小数。"""
        return format_duration(int(self.total))
