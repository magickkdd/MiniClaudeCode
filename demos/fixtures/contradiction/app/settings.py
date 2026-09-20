"""外部调用的默认配置。"""

from __future__ import annotations

DEFAULT_TIMEOUT = 30
RETRIES = 2


def describe() -> str:
    return f"timeout={DEFAULT_TIMEOUT}s retries={RETRIES}"
