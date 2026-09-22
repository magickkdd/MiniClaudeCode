# Demo 2 · 在陌生仓库按一句话描述修 bug（验收项 A1）

> 本文件由 `python demos/run_demo.py --demo bug-hunt --engine live` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。
> 引擎：真实端点 `agnes-2.5-flash`，会受模型随机性影响。

| 字段 | 值 |
|---|---|
| task | README 承诺 `format_duration(90000)` 返回 `1d1h0m0s`，实际返回 `25d0h0m0s`。定位根因并修好它，补一个能防回归的测试，然后跑一遍测试确认没弄坏别的。 |
| repo/baseline | `demos/fixtures/bug-hunt` · baseline `66e7e20db481`（13 个文件） |
| expected | pytest 退出码 0；README 表格里的示例逐条兑现；用例总数比基线多（确实补了回归测试） |
| actual | `completed` · 判定 PASS（4/4） |
| turns / tokens | 9 轮 / 52,017 tokens |
| tool_calls | 20 次，其中 is_error 0 次（0%） |
| 工具序列 | read_file → read_file → read_file → read_file → edit_file → edit_file → run_tests → run_tests → read_file → read_file → read_file → read_file → run_tests → bash → bash → read_file → bash → bash → bash → bash |
| denied / repeated / stalled | 0 次被拒 · 0 次逐调用重复 · 0 轮整组重演 |
| 失败模式 | — |
| 上下文峰值 | 8,853 tokens |
| 权限模式 | `auto`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |
| wall time | 59.8s |
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
✓ read_file   duration/format.py  2ms
✓ read_file   duration/__init__.py  2ms
✓ read_file   tests/test_format.py  2ms
✓ read_file   README.md  2ms
根因找到：第 15 行把 `divmod(total, SECONDS_PER_HOUR)` 当成"除以天"来算 days，应该是 `SECONDS_PER_DAY`（86400），导致 90000 // 3600 = 25。

修 bug，补回归测试，跑测试。
✓ edit_file   duration/format.py  2ms
✓ edit_file   tests/test_format.py  1ms
✓ run_tests   tests/  1.1s
测试计数为 0，可能路径未包含 `duration/`，再跑一次全量。
✓ run_tests   extra_args=['-q']  1.1s
✓ read_file   conftest.py  1ms
✓ read_file   tests/test_cli.py  1ms
✓ read_file   tests/test_parse.py  1ms
✓ read_file   tests/test_stopwatch.py  1ms
✓ run_tests   extra_args=['-v', '--co']  919ms
✓ bash        python -m pytest duration/format.py -v  666ms
✓ bash        python -m pytest duration/format.py -v && echo "=== exit: $?"  706ms
✓ read_file   tests/test_format.py  1ms
✓ bash        ls duration/ tests/ && echo "---" && python -m pytest tests/test_fo…  740ms
✓ bash        python -m pytest tests/test_format.py -v 2>&1; echo "exit=$?"  678ms
✓ bash        python -m pytest tests/ -v 2>&1; echo "exit=$?"  886ms
✓ bash        python -m pytest tests/ -v; echo "exit=$?"  840ms
全部 22 项测试通过。

**根因**：`duration/format.py` 第 15 行把 `divmod(total, SECONDS_PER_HOUR)` 错当成"算天数"，导致 `days = total // 3600`（90000 // 3600 = 25），`rest` 也被异常截断，后续小时/分钟全部错乱。

**修复**：把除数从 `SECONDS_PER_HOUR` 改成 `SECONDS_PER_DAY`。

**防回归测试**：新增 `tests/test_format.py::test_crosses_day_boundary`，用 90000 → `"1d1h0m0s"`、90061 → `"1d1h1m1s"` 两条断言覆盖跨天边界。

**下一步**：可直接提交，或顺带补一个 `format_duration(86400) == "1d0h0m0s"` 的边界用例。
```

## 模型的最后一段话

全部 22 项测试通过。

**根因**：`duration/format.py` 第 15 行把 `divmod(total, SECONDS_PER_HOUR)` 错当成"算天数"，导致 `days = total // 3600`（90000 // 3600 = 25），`rest` 也被异常截断，后续小时/分钟全部错乱。

**修复**：把除数从 `SECONDS_PER_HOUR` 改成 `SECONDS_PER_DAY`。

**防回归测试**：新增 `tests/test_format.py::test_crosses_day_boundary`，用 90000 → `"1d1h0m0s"`、90061 → `"1d1h1m1s"` 两条断言覆盖跨天边界。

**下一步**：可直接提交，或顺带补一个 `format_duration(86400) == "1d0h0m0s"` 的边界用例。

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
@@ -23,2 +23,8 @@
     with pytest.raises(ValueError):
         format_duration(bad)
+
+
+def test_crosses_day_boundary():
+    """防回归：90000 秒 = 1 天 + 1 小时，不应返回 25d"""
+    assert format_duration(90000) == "1d1h0m0s"
+    assert format_duration(90061) == "1d1h1m1s"
```
