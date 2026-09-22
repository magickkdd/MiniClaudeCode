# taxed-base

订单结算：**小计 → 折扣 → 税**。税率按地区查 `pricing/config.py` 的 `TAX_RATES`。

```python
from pricing import total

total([(2, 19.99), (1, 4.5)], region="none")              # → 44.48
total([(1, 80.0)], region="uk")                           # → 96.00
total([(1, 100.0)], region="eu", discount=0.10)           # → 110.70
```

规则（欧盟 VAT）：

- 折扣先扣，税按**折后**金额算，不是按打折前的小计算；
- `region="none"` 表示免税；
- 折扣率上限 50%，超过直接 `ValueError`；
- 金额一律保留两位小数。
