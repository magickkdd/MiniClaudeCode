"""唯一的 slug 清洗规则。别处不要再抄一份。"""

from __future__ import annotations

import re

_NOISE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """小写、连续非字母数字并成一个 `-`、去掉首尾的 `-`。"""
    return _NOISE.sub("-", (text or "").lower()).strip("-")
