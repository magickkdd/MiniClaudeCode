# 附加 · 只读模式回答「这段代码对不对」（验收项 A3（权限边界））

> 本文件由 `python demos/run_demo.py --demo readonly-qa --engine fake` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。
> 引擎：FakeLLM 脚本 + 真工具真落盘：证明工程闭环，不证明模型能力。

| 字段 | 值 |
|---|---|
| task | `format_duration` 处理超过一天的秒数时，输出和 README 的承诺一致吗？不一致的话根因在哪一行，为什么现有测试没抓到？不要修改任何文件。 |
| repo/baseline | `demos/fixtures/bug-hunt` · baseline `66e7e20db481`（13 个文件） |
| expected | 工作区哈希与基线一致（真没写盘）；答案指出 `divmod` 用错了换算常量并解释测试为何漏掉 |
| actual | `completed` · 判定 PASS（3/3） |
| turns / tokens | 3 轮 / 不适用（FakeLLM 不返回 usage） |
| tool_calls | 发起 4 次 · 执行 4 次，其中 is_error 0 次（0%） |
| 工具序列 | find_files → search_text → read_file → read_file |
| denied / repeated / stalled | 0 次被拒 · 0 次逐调用重复 · 0 轮整组重演 |
| 失败模式 | — |
| 上下文峰值 | 0 tokens |
| 权限模式 | `readonly`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |
| wall time | 0.0s |
| trace | `demos/traces/readonly-qa.fake.jsonl` |
| 工作副本 | `demos/.work/readonly-qa`（判定就在这个目录跑） |

## 判定明细

- [x] 只读模式真的没改任何文件 —— 基线 66e7e20db481 → 运行后 66e7e20db481
- [x] 任务正常收尾 —— completed
- [x] 答案指出了用错的换算常量 —— 命中 3600/86400 对照

退出码判据：判定脚本在 `readonly-qa` 里独立运行 pytest 与行为探测，不看模型最后那段话。

## 终端输出（原样）

```text
先看仓库形状，再定位实现。
✓ find_files  **/*.py  1ms
✓ search_text format_duration  4ms
✓ read_file   duration/format.py  1ms
✓ read_file   README.md  2ms
**超过一天的输出是错的。**

`duration/format.py:15` 里切「天」用的除数是 `SECONDS_PER_HOUR`：

```python
days, rest = divmod(total, SECONDS_PER_HOUR)   # 应为 SECONDS_PER_DAY
```

于是 `format_duration(90000)` 返回 `25d0h0m0s`，而 README 的承诺是 `1d1h0m0s`（`SECONDS_PER_DAY = 86400` 就在同一文件第 7 行，定义了却没用上）。

为什么测试没抓到：`tests/test_format.py` 最大只测到 3599 秒，`days` 恒为 0，这条分支从未被执行。`python -m duration 90000` 这个 CLI 路径同样受影响。
```

## 模型的最后一段话

**超过一天的输出是错的。**

`duration/format.py:15` 里切「天」用的除数是 `SECONDS_PER_HOUR`：

```python
days, rest = divmod(total, SECONDS_PER_HOUR)   # 应为 SECONDS_PER_DAY
```

于是 `format_duration(90000)` 返回 `25d0h0m0s`，而 README 的承诺是 `1d1h0m0s`（`SECONDS_PER_DAY = 86400` 就在同一文件第 7 行，定义了却没用上）。

为什么测试没抓到：`tests/test_format.py` 最大只测到 3599 秒，`days` 恒为 0，这条分支从未被执行。`python -m duration 90000` 这个 CLI 路径同样受影响。

## diff：基线 → 运行后

```diff
（无差异）
```
