# Demo 2 · 在陌生仓库按一句话描述修 bug（验收项 A1）

> 本文件由 `python demos/run_demo.py --demo bug-hunt --engine fake` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。
> 引擎：FakeLLM 脚本 + 真工具真落盘：证明工程闭环，不证明模型能力。

| 字段 | 值 |
|---|---|
| task | README 承诺 `format_duration(90000)` 返回 `1d1h0m0s`，实际返回 `25d0h0m0s`。定位根因并修好它，补一个能防回归的测试，然后跑一遍测试确认没弄坏别的。 |
| repo/baseline | `demos/fixtures/bug-hunt` · baseline `66e7e20db481`（13 个文件） |
| expected | pytest 退出码 0；README 表格里的示例逐条兑现；用例总数比基线多（确实补了回归测试） |
| actual | `completed` · 判定 PASS（4/4） |
| turns / tokens | 7 轮 / 不适用（FakeLLM 不返回 usage） |
| tool_calls | 发起 7 次 · 执行 7 次，其中 is_error 0 次（0%） |
| 工具序列 | write_todos → search_text → read_file → read_file → edit_file → write_file → run_tests |
| denied / repeated / stalled | 0 次被拒 · 0 次逐调用重复 · 0 轮整组重演 |
| 失败模式 | — |
| 上下文峰值 | 0 tokens |
| 权限模式 | `auto`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |
| wall time | 1.1s |
| trace | `demos/traces/bug-hunt.fake.jsonl` |
| 工作副本 | `demos/.work/bug-hunt`（判定就在这个目录跑） |

**说明** 基线 21 个用例全绿：bug 不在测试覆盖范围内（最大只测到 3599 秒）。Agent 只能靠 README 的承诺 + 读代码定位，不能顺着红色 traceback 走。

## 判定明细

- [x] pytest 退出码 0 —— 退出码 0，25 passed / 0 failed（基线 21 个用例）
- [x] README 表格逐条兑现（含修好前的对照组） —— README 表格全部兑现
- [x] 补了防回归测试（用例数比基线多） —— 21 个 → 25 个
- [x] `tests/` 只增不删（没删断言换全绿） —— tests/ 基线内容逐行保留，新增 1 个文件

退出码判据：判定脚本在 `bug-hunt` 里独立运行 pytest 与行为探测，不看模型最后那段话。

## 终端输出（原样）

```text
✓ write_todos 3 步  0ms
任务清单
  [~] 定位 format_duration 的分段逻辑
  [ ] 对照 README 表格确认期望输出
  [ ] 修复并补防回归测试
先确认承诺的格式，再找实现落在哪个文件。
✓ search_text def format_duration  12ms
✓ read_file   README.md  2ms
✓ read_file   duration/format.py  1ms
根因找到：切「天」那次 divmod 用的是每小时的秒数，90000 秒因此被切成 25 段。改成 SECONDS_PER_DAY。
✓ edit_file   duration/format.py  1ms
现有测试最远只到 3599 秒，所以这个 bug 一直是哑的。补上跨天的用例。
✓ write_file  tests/test_format_days.py  1ms
✓ run_tests     1.0s
修好了，根因是单位换算用错常量：`duration/format.py` 里切「天」的那次 `divmod` 传的是 `SECONDS_PER_HOUR`（3600）而不是 `SECONDS_PER_DAY`（86400），于是 90000 秒变成 `25d0h0m0s`。

- 改动 1 行：`duration/format.py`
- 新增 `tests/test_format_days.py`：卡住 86399/86400 边界与 README 的两条示例
- `run_tests` 退出码 0

顺带一句：`parse_duration` 早就支持 `d`，只有格式化侧漏了 —— README 的表格比测试更接近真实契约，这类不一致最容易被测试全绿的假象掩盖。
```

## 模型的最后一段话

修好了，根因是单位换算用错常量：`duration/format.py` 里切「天」的那次 `divmod` 传的是 `SECONDS_PER_HOUR`（3600）而不是 `SECONDS_PER_DAY`（86400），于是 90000 秒变成 `25d0h0m0s`。

- 改动 1 行：`duration/format.py`
- 新增 `tests/test_format_days.py`：卡住 86399/86400 边界与 README 的两条示例
- `run_tests` 退出码 0

顺带一句：`parse_duration` 早就支持 `d`，只有格式化侧漏了 —— README 的表格比测试更接近真实契约，这类不一致最容易被测试全绿的假象掩盖。

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
--- a/tests/test_format_days.py
+++ b/tests/test_format_days.py
@@ -0,0 +1,21 @@
+"""跨天的格式化。README 表格里那两行一直没有测试覆盖。"""
+
+from duration import format_duration
+
+
+def test_exactly_one_day():
+    assert format_duration(86400) == "1d0h0m0s"
+
+
+def test_just_under_a_day():
+    assert format_duration(86399) == "23h59m59s"
+
+
+def test_readme_examples():
+    assert format_duration(90000) == "1d1h0m0s"
+    assert format_duration(90061) == "1d1h1m1s"
+
+
+def test_days_and_hours_stay_separate():
+    assert format_duration(86400 + 3600) == "1d1h0m0s"
+    assert format_duration(3 * 86400 + 2 * 3600 + 61) == "3d2h1m1s"
```
