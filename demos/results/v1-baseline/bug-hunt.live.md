# Demo 2 · 在陌生仓库按一句话描述修 bug（验收项 A1）

> 本文件由 `python demos/run_demo.py --demo bug-hunt --engine live` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。
> 引擎：真实端点 `agnes-2.5-flash`，会受模型随机性影响。

| 字段 | 值 |
|---|---|
| task | README 承诺 `format_duration(90000)` 返回 `1d1h0m0s`，实际返回 `25d0h0m0s`。定位根因并修好它，补一个能防回归的测试，然后跑一遍测试确认没弄坏别的。 |
| repo/baseline | `demos/fixtures/bug-hunt` · baseline `66e7e20db481`（13 个文件） |
| expected | pytest 退出码 0；README 表格里的示例逐条兑现；用例总数比基线多（确实补了回归测试） |
| actual | `completed` · 判定 PASS（4/4） |
| turns / tokens | 6 轮 / 24,994 tokens |
| tool_calls | 11 次，其中 is_error 3 次（27%） |
| 工具序列 | read_file → read_file → read_file → find_files → read_file → read_file → read_file → edit_file → edit_file → run_tests → run_tests |
| redundant / denied | 0 / 0 |
| 权限模式 | `auto`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |
| wall time | 12.6s |
| trace | `demos/traces/bug-hunt.live.jsonl` |
| 工作副本 | `demos/.work/bug-hunt`（判定就在这个目录跑） |

**说明** 基线 21 个用例全绿：bug 不在测试覆盖范围内（最大只测到 3599 秒）。Agent 只能靠 README 的承诺 + 读代码定位，不能顺着红色 traceback 走。

## 判定明细

- [x] pytest 退出码 0 —— 退出码 0，22 passed / 0 failed（基线 21 个用例）
- [x] README 表格逐条兑现（含修好前的对照组） —— README 表格全部兑现
- [x] 补了防回归测试（用例数比基线多） —— 21 个 → 22 个
- [x] `tests/` 只增不删（没删断言换全绿） —— tests/ 基线内容逐行保留，新增 0 个文件

退出码判据：判定脚本在 `bug-hunt` 里独立运行 pytest 与行为探测，不看模型最后那段话。

## 终端输出（原样）

```text
✓ read_file   README.md  2ms
✗ read_file   src/format.py  0ms
✗ read_file   src/test_format.py  0ms
✓ find_files  **/*.py  1ms
✗ read_file   src/__init__.py  0ms
✓ read_file   duration/format.py  2ms
✓ read_file   tests/test_format.py  2ms
Bug 找到：第 15 行把天数算成 `divmod(total, SECONDS_PER_HOUR)` 应该用 `SECONDS_PER_DAY`，导致 90000 秒被算成 25 小时而非 1 天 1 小时。
✓ edit_file   duration/format.py  1ms
✓ edit_file   tests/test_format.py  1ms
✓ run_tests   tests/test_format.py  930ms
✓ run_tests     1.1s
修好了。根因是 `duration/format.py` 第 15 行把 `divmod(total, SECONDS_PER_HOUR)` 误写成了按小时取余，导致天数被当作剩余小时计算；改为 `SECONDS_PER_DAY` 后 90000 秒正确输出 `1d1h0m0s`。同时在 `tests/test_format.py` 补了 `test_over_one_day`（覆盖 90000 和 90061 两个用例），全量 22 个测试通过。
```

## 模型的最后一段话

修好了。根因是 `duration/format.py` 第 15 行把 `divmod(total, SECONDS_PER_HOUR)` 误写成了按小时取余，导致天数被当作剩余小时计算；改为 `SECONDS_PER_DAY` 后 90000 秒正确输出 `1d1h0m0s`。同时在 `tests/test_format.py` 补了 `test_over_one_day`（覆盖 90000 和 90061 两个用例），全量 22 个测试通过。

## diff：基线 → 运行后

```diff
--- a/duration/format.py
+++ b/duration/format.py
@@ -13,5 +13,5 @@
         raise ValueError(f"seconds 不能为负：{seconds}")
 
-    days, rest = divmod(total, SECONDS_PER_HOUR)
+    days, rest = divmod(total, SECONDS_PER_DAY)
     hours, rest = divmod(rest, SECONDS_PER_HOUR)
     minutes, secs = divmod(rest, SECONDS_PER_MINUTE)
--- a/tests/test_format.py
+++ b/tests/test_format.py
@@ -15,4 +15,9 @@
 
 
+def test_over_one_day():
+    assert format_duration(90000) == "1d1h0m0s"
+    assert format_duration(90061) == "1d1h1m1s"
+
+
 def test_just_under_an_hour():
     assert format_duration(3599) == "59m59s"
```
