# 已知问题

- `tests/test_export.py` 是红的：CSV 导出（`report.export.write_csv`）从来没实现过，
  排在下个季度，**不属于**当前任何一次改动的范围。
- `report.summary.describe()` 抛 `NotImplementedError`：README 承诺了格式，实现待补。
