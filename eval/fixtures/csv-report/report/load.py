"""读 CSV 与解析那些写得很脏的金额。"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

_AMOUNT_NOISE = re.compile(r"[^\d.\-]")


@dataclass(frozen=True)
class Row:
    region: str
    amount: float
    label: str = ""


def parse_amount(raw: str) -> float:
    """`1,200.50` / `€820` / ` 430.00 ` 都要能读出来。"""
    text = _AMOUNT_NOISE.sub("", (raw or "").strip())
    if not text or text == "-":
        raise ValueError(f"读不出金额：{raw!r}")
    return round(float(text), 2)


def read_rows(path: str | Path) -> list[Row]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [
            Row(
                region=(record.get("region") or "").strip().lower(),
                amount=parse_amount(record.get("amount", "")),
                label=(record.get("label") or "").strip(),
            )
            for record in csv.DictReader(handle)
        ]
