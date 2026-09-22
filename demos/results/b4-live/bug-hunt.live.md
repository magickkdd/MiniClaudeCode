# Demo 2 · 在陌生仓库按一句话描述修 bug（验收项 A1）

> 本文件由 `python demos/run_demo.py --demo bug-hunt --engine live` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。
> 引擎：真实端点 `agnes-2.5-flash`，会受模型随机性影响。

| 字段 | 值 |
|---|---|
| task | README 承诺 `format_duration(90000)` 返回 `1d1h0m0s`，实际返回 `25d0h0m0s`。定位根因并修好它，补一个能防回归的测试，然后跑一遍测试确认没弄坏别的。 |
| repo/baseline | `demos/fixtures/bug-hunt` · baseline `66e7e20db481`（13 个文件） |
| expected | pytest 退出码 0；README 表格里的示例逐条兑现；用例总数比基线多（确实补了回归测试） |
| actual | `max_turns` · 判定 FAIL（2/4） |
| turns / tokens | 4 轮 / 13,573 tokens |
| tool_calls | 10 次，其中 is_error 5 次（50%） |
| 工具序列 | read_file → read_file → bash → find_files → read_file → read_file → read_file → bash → bash → bash |
| denied / repeated / stalled | 0 次被拒 · 0 次逐调用重复 · 0 轮整组重演 |
| 失败模式 | path_guessing |
| 上下文峰值 | 3,709 tokens |
| 权限模式 | `auto`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |
| wall time | 9.2s |
| trace | `demos/traces/bug-hunt.live.jsonl` |
| 工作副本 | `demos/.work/bug-hunt`（判定就在这个目录跑） |

**说明** 基线 21 个用例全绿：bug 不在测试覆盖范围内（最大只测到 3599 秒）。Agent 只能靠 README 的承诺 + 读代码定位，不能顺着红色 traceback 走。

## 判定明细

- [x] pytest 退出码 0 —— 退出码 0，21 passed / 0 failed（基线 21 个用例）
- [ ] README 表格逐条兑现（含修好前的对照组） —— AssertionError: 25d0h0m0s
- [ ] 补了防回归测试（用例数比基线多） —— 21 个 → 21 个
- [x] `tests/` 只增不删（没删断言换全绿） —— tests/ 基线内容逐行保留，新增 0 个文件

退出码判据：判定脚本在 `bug-hunt` 里独立运行 pytest 与行为探测，不看模型最后那段话。

## 终端输出（原样）

```text
✗ read_file   bug-hunt/README.md  0ms
✗ read_file   bug-hunt/format.py  0ms
✓ bash        pwd && ls -la  58ms
✓ find_files  **/*  1ms
✗ read_file   bug-hunt/README.md  0ms
✗ read_file   bug-hunt/duration/format.py  0ms
✗ read_file   bug-hunt/tests/test_format.py  0ms
✓ bash        cat README.md  46ms
✓ bash        cat duration/format.py  46ms
✓ bash        cat tests/test_format.py  44ms
```

## 模型的最后一段话

已达到最大轮数（4），任务未完成。请把任务拆小一点，或指定更明确的目标文件后重试。

## diff：基线 → 运行后

```diff
（无差异）
```
