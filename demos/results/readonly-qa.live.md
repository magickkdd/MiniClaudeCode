# 附加 · 只读模式回答「这段代码对不对」（验收项 A3（权限边界））

> 本文件由 `python demos/run_demo.py --demo readonly-qa --engine live` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。
> 引擎：真实端点 `agnes-2.5-flash`，会受模型随机性影响。

| 字段 | 值 |
|---|---|
| task | `format_duration` 处理超过一天的秒数时，输出和 README 的承诺一致吗？不一致的话根因在哪一行，为什么现有测试没抓到？不要修改任何文件。 |
| repo/baseline | `demos/fixtures/bug-hunt` · baseline `66e7e20db481`（13 个文件） |
| expected | 工作区哈希与基线一致（真没写盘）；答案指出 `divmod` 用错了换算常量并解释测试为何漏掉 |
| actual | `completed` · 判定 PASS（3/3） |
| turns / tokens | 4 轮 / 14,976 tokens |
| tool_calls | 6 次，其中 is_error 2 次（33%） |
| 工具序列 | read_file → read_file → read_file → find_files → read_file → read_file |
| denied / repeated / stalled | 0 次被拒 · 0 次逐调用重复 · 0 轮整组重演 |
| 失败模式 | — |
| 上下文峰值 | 4,275 tokens |
| 权限模式 | `readonly`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |
| wall time | 12.8s |
| trace | `demos/traces/readonly-qa.live.jsonl` |
| 工作副本 | `demos/.work/readonly-qa`（判定就在这个目录跑） |

## 判定明细

- [x] 只读模式真的没改任何文件 —— 基线 66e7e20db481 → 运行后 66e7e20db481
- [x] 任务正常收尾 —— completed
- [x] 答案指出了用错的换算常量 —— 命中 3600/86400 对照

退出码判据：判定脚本在 `readonly-qa` 里独立运行 pytest 与行为探测，不看模型最后那段话。

## 终端输出（原样）

```text
✓ read_file   README.md  2ms
✗ read_file   format.py  0ms
✗ read_file   test_format.py  0ms
✓ find_files  **/*.py  1ms
✓ read_file   duration/format.py  2ms
✓ read_file   tests/test_format.py  2ms
不一致，根因就在 `duration/format.py` 第 15 行。

## 为什么跟 README 承诺对不上

README 的"超过一天"规则说：**保留 `d` 段，后面的 `h/m/s` 照旧打印（哪怕是 0）**，表格里也给了 `90000 → 1d1h0m0s` 这个用例。

但实现里：

```python
# duration/format.py:15
days, rest = divmod(total, SECONDS_PER_HOUR)   # ← bug：应该除 SECONDS_PER_DAY (86400)
hours, rest = divmod(rest, SECONDS_PER_HOUR)
```

除以的是 `SECONDS_PER_HOUR`（3600）而不是 `SECONDS_PER_DAY`（86400）。于是 90000 秒被算成 `days=25, rest=0`，输出 `25d0h0m0s`，既不是 README 期望的 `1d1h0m0s`，也没有"从最高非零单位开始"的意思——它把"天"硬套在了一个其实已经是"小时级"的量上。

## 为什么现有测试没抓到

`tests/test_format.py` 里没有任何超过一天的用例（只测到 3599 秒），连 `90000`、`90061` 这种 README 明确列出的边界都没进测试，所以 bug 一直隐身。

## 修法提示（不实际改文件）

第 15 行换成：

```python
days, rest = divmod(total, SECONDS_PER_DAY)
```

改完后再把 README 表格里那两条（90000、90061）以及"超过一天"的通用断言补进 `test_format.py`，就能锁住这个回归。
```

## 模型的最后一段话

不一致，根因就在 `duration/format.py` 第 15 行。

## 为什么跟 README 承诺对不上

README 的"超过一天"规则说：**保留 `d` 段，后面的 `h/m/s` 照旧打印（哪怕是 0）**，表格里也给了 `90000 → 1d1h0m0s` 这个用例。

但实现里：

```python
# duration/format.py:15
days, rest = divmod(total, SECONDS_PER_HOUR)   # ← bug：应该除 SECONDS_PER_DAY (86400)
hours, rest = divmod(rest, SECONDS_PER_HOUR)
```

除以的是 `SECONDS_PER_HOUR`（3600）而不是 `SECONDS_PER_DAY`（86400）。于是 90000 秒被算成 `days=25, rest=0`，输出 `25d0h0m0s`，既不是 README 期望的 `1d1h0m0s`，也没有"从最高非零单位开始"的意思——它把"天"硬套在了一个其实已经是"小时级"的量上。

## 为什么现有测试没抓到

`tests/test_format.py` 里没有任何超过一天的用例（只测到 3599 秒），连 `90000`、`90061` 这种 README 明确列出的边界都没进测试，所以 bug 一直隐身。

## 修法提示（不实际改文件）

第 15 行换成：

```python
days, rest = divmod(total, SECONDS_PER_DAY)
```

改完后再把 README 表格里那两条（90000、90061）以及"超过一天"的通用断言补进 `test_format.py`，就能锁住这个回归。

## diff：基线 → 运行后

```diff
（无差异）
```
