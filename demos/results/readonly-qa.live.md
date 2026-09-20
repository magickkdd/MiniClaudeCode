# 附加 · 只读模式回答「这段代码对不对」（验收项 A3（权限边界））

> 本文件由 `python demos/run_demo.py --demo readonly-qa --engine live` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。
> 引擎：真实端点 `agnes-2.5-flash`，会受模型随机性影响。

| 字段 | 值 |
|---|---|
| task | `format_duration` 处理超过一天的秒数时，输出和 README 的承诺一致吗？不一致的话根因在哪一行，为什么现有测试没抓到？不要修改任何文件。 |
| repo/baseline | `demos/fixtures/bug-hunt` · baseline `66e7e20db481`（13 个文件） |
| expected | 工作区哈希与基线一致（真没写盘）；答案指出 `divmod` 用错了换算常量并解释测试为何漏掉 |
| actual | `completed` · 判定 PASS（3/3） |
| turns / tokens | 5 轮 / 20,680 tokens |
| tool_calls | 6 次，其中 is_error 1 次（17%） |
| 工具序列 | read_file → read_file → read_file → find_files → read_file → read_file |
| redundant / denied | 0 / 3 |
| 权限模式 | `readonly`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |
| wall time | 8.4s |
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
✗ read_file   src/format.py  0ms
✓ read_file   tests/test_format.py  2ms
✓ find_files  **/*.py  1ms
✓ read_file   duration/format.py  2ms
✓ read_file   tests/test_format.py  1ms
让我复现一下实际输出：
  已拦下 bash：当前是只读模式，execute 级操作被禁用。
  已拦下 bash：当前是只读模式，execute 级操作被禁用。
  已拦下 bash：当前是只读模式，execute 级操作被禁用。
代码里已经能直接看出问题，无需执行。分析如下：

---

## 不一致 & 根因

### README 的承诺

第 33 行明确写着：
> **超过一天时**保留 `d` 段，后面的 `h/m/s` 照旧打印（**哪怕是 0**）。

README 表格里 `90000` → `1d1h0m0s` 和 `90061` → `1d1h1m1s` 也体现了"满一天后始终输出 d/h/m/s 四段"的规则。

### 实际代码

`duration/format.py` **第 15 行**是 bug 所在：

```python
days, rest = divmod(total, SECONDS_PER_HOUR)   # ← 应该是 SECONDS_PER_DAY (86400)
```

把 `total` 对 `SECONDS_PER_HOUR`（=3600）做整除，于是：

- `days = total // 3600`，这其实是"小时数"，被错标成 `days`
- `rest = total % 3600`，再对 3600 取余得 `hours = 0`
- 最终输出的形式像 `25h0m0s` 这类"假天串"，而不是 `1d1h0m0s`

### 为什么现有测试没抓到

`tests/test_format.py` 里没有任何 `≥ 86400` 的测试用例——它只覆盖了：
- `< 60s`
- `< 1h`
- 负数

**关键缺失**：没有覆盖"跨天"分支，因此第 15 行的除数错误、以及第 19 行 `if days:` 之后的拼接逻辑都没有被任何断言覆盖到。现有 4 个测试函数共 7 条断言，全部停留在小时以内。

---

**结论**：格式输出与 README 承诺不一致。根因是 `duration/format.py:15` 用 `SECONDS_PER_HOUR` 而不是 `SECONDS_PER_DAY` 来做天的除法；测试缺口是没有 ≥ 86400 秒的用例。
```

## 模型的最后一段话

代码里已经能直接看出问题，无需执行。分析如下：

---

## 不一致 & 根因

### README 的承诺

第 33 行明确写着：
> **超过一天时**保留 `d` 段，后面的 `h/m/s` 照旧打印（**哪怕是 0**）。

README 表格里 `90000` → `1d1h0m0s` 和 `90061` → `1d1h1m1s` 也体现了"满一天后始终输出 d/h/m/s 四段"的规则。

### 实际代码

`duration/format.py` **第 15 行**是 bug 所在：

```python
days, rest = divmod(total, SECONDS_PER_HOUR)   # ← 应该是 SECONDS_PER_DAY (86400)
```

把 `total` 对 `SECONDS_PER_HOUR`（=3600）做整除，于是：

- `days = total // 3600`，这其实是"小时数"，被错标成 `days`
- `rest = total % 3600`，再对 3600 取余得 `hours = 0`
- 最终输出的形式像 `25h0m0s` 这类"假天串"，而不是 `1d1h0m0s`

### 为什么现有测试没抓到

`tests/test_format.py` 里没有任何 `≥ 86400` 的测试用例——它只覆盖了：
- `< 60s`
- `< 1h`
- 负数

**关键缺失**：没有覆盖"跨天"分支，因此第 15 行的除数错误、以及第 19 行 `if days:` 之后的拼接逻辑都没有被任何断言覆盖到。现有 4 个测试函数共 7 条断言，全部停留在小时以内。

---

**结论**：格式输出与 README 承诺不一致。根因是 `duration/format.py:15` 用 `SECONDS_PER_HOUR` 而不是 `SECONDS_PER_DAY` 来做天的除法；测试缺口是没有 ≥ 86400 秒的用例。

## diff：基线 → 运行后

```diff
（无差异）
```
