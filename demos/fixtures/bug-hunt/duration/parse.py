"""把 `1h30m` 这类字符串解析成秒。"""

from __future__ import annotations

import re

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_TOKEN_RE = re.compile(r"(\d+)\s*([smhd])", re.IGNORECASE)


def parse_duration(text: str) -> int:
    """按 `d/h/m/s` 逐段累加。顺序无关，出现无法识别的字符就报错。"""
    raw = (text or "").strip().lower().replace(" ", "")
    if not raw:
        raise ValueError("时长不能为空")

    tokens = _TOKEN_RE.findall(raw)
    if not tokens:
        raise ValueError(f"无法解析时长 {text!r}，期望形如 1h30m")

    consumed = "".join(f"{num}{unit}" for num, unit in tokens)
    if consumed != raw:
        raise ValueError(f"无法解析时长 {text!r}，多余部分 {raw[len(consumed):]!r}")

    return sum(int(num) * _UNITS[unit] for num, unit in tokens)
