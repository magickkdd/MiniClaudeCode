# csv-report

把销售 CSV 读进来做地区汇总。`report.load` 负责解析，`report.summary` 负责聚合。

```python
from report import read_rows, totals_by_region, describe

rows = read_rows("sales.csv")
totals_by_region(rows)      # → {"eu": 820.0, "us": 430.0}
describe(rows)              # → "3 行 · 总额 1,250.00 · 最高地区 eu（820.00）"
```

`describe(rows)` 是 README 承诺但还没写的函数，格式必须**逐字**照上面这行：

- `N 行` —— 行数；
- `总额 X` —— 金额合计，千分位、保留两位小数；
- `最高地区 R（Y）` —— 合计最大的地区与它的金额（同样千分位两位小数）；
- 金额为 0 行时返回 `0 行 · 总额 0.00 · 最高地区 无（0.00）`；
- 平局时取地区名字典序最小的那个。

金额列的写法很脏：可能是 `1,200.50`、`€820`、` 430.00 `，都要能解析。

已知问题记在 `docs/known-issues.md` 里 —— 那条不在本次范围内。
