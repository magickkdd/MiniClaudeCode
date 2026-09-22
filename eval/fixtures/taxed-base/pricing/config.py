"""税率与业务上限的配置。价格逻辑在 `cart.py`，两边都要读才对得上。"""

from __future__ import annotations

# 欧盟 VAT 是 23%（README 的算例就是按这个来的）。
TAX_RATES = {
    "eu": 0.20,
    "uk": 0.20,
    "none": 0.0,
}

MAX_DISCOUNT = 0.5


def tax_of(region: str) -> float:
    try:
        return TAX_RATES[region]
    except KeyError:
        raise ValueError(f"未知地区：{region}（可选 {', '.join(sorted(TAX_RATES))}）") from None
