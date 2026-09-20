"""duration —— 时长解析与格式化。"""

from __future__ import annotations

from .format import format_duration
from .parse import parse_duration
from .stopwatch import Stopwatch

__all__ = ["format_duration", "parse_duration", "Stopwatch"]
__version__ = "0.4.2"
