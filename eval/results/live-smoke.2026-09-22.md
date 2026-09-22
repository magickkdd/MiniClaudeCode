# 评测批次报表

- 任务集哈希：`0935fa95ca49` · 引擎 live · 重复 1 × 6 任务 = 6 次运行
- 批次预算 300,000 tokens，实际花费 313,812 tokens
- pass@1 = 4/6，pass@1 = 4/6
- 单次成功率 Wilson 95% CI：[0.300, 0.903]（区间宽到容得下两种相反的结论，别拿点估计说话）

| 任务 | 重复 | 判定 | 轮数 | 工具(报错) | 终止 | tokens | 失败模式 | 红掉的用例 |
|---|---:|---|---:|---|---|---:|---|---|
| bh-format-duration | 0 | fail | 7 | 12(1) | completed | 32,192 | — | — |
| bh-readonly-explain | 0 | pass | 4 | 6(2) | completed | 14,924 | — | — |
| csv-add-export | 0 | pass | 4 | 6(1) | completed | 13,855 | — | — |
| csv-implement-describe | 0 | pass | 6 | 11(0) | completed | 30,355 | — | — |
| csv-qa-scope | 0 | pass | 3 | 6(1) | completed | 11,949 | — | — |
| gf-calculator | 0 | fail | 13 | 22(3) | completed | 210,537 | context_growth | — |

## 按标签

| tag | runs | pass@1 | 失败模式 |
|---|---:|---|---|
| bugfix | 1 | 0/1 | — |
| codegen | 1 | 0/1 | context_growth |
| constant-mixup | 1 | 0/1 | — |
| doc-lookup | 1 | 1/1 | — |
| explanation | 1 | 1/1 | — |
| feature | 2 | 2/2 | — |
| greenfield | 1 | 0/1 | context_growth |
| greenfield-file | 1 | 1/1 | — |
| partial-red-repo | 1 | 1/1 | — |
| permission-boundary | 1 | 1/1 | — |
| readme-drift | 2 | 1/2 | — |
| readonly | 2 | 2/2 | — |
| regression-test | 1 | 0/1 | — |
| scope | 1 | 1/1 | — |
| self-debugging | 1 | 0/1 | context_growth |
| whitelist | 2 | 2/2 | — |

## 指标表（SPEC v2 §3.2）

```json
{
  "runs": 6,
  "tasks": 6,
  "repeats": 1,
  "engine": [
    "live"
  ],
  "verdicts": {
    "pass": 4,
    "fail": 2,
    "error": 0
  },
  "pass_at_1": 0.6667,
  "pass_at_k": 0.6667,
  "pass_at_1_raw": "4/6",
  "pass_at_k_raw": "4/6",
  "success_rate_ci": [
    0.3,
    0.9032
  ],
  "steps_to_success_median": 4.0,
  "tool_error_rate": 0.127,
  "tool_executed": 62,
  "repeated_call_rate": 0.0,
  "stalled_group_rate": 0.0,
  "denial_rate": 0.0159,
  "wasted_output_ratio": 0.0,
  "context_peak_p95": 21028,
  "tokens_total": 313812,
  "tokens_per_success": 78453,
  "cost_est_total": null,
  "failure_mode_dist": {
    "context_growth": 1
  },
  "wall_time_p50_ms": 19034,
  "wall_time_p95_ms": 68284,
  "aborted_batch": false,
  "budget_tokens": 300000,
  "tokens_spent": 313812,
  "taskset_sha": "0935fa95ca49",
  "resumed": 0,
  "regressions": []
}
```

## 失败任务的判据明细

### bh-format-duration · repeat 0
- [x] `tests/` 只增不删（没删断言换全绿） —— tests/ 基线内容逐行保留，新增 0 个文件
- [x] PASS_TO_PASS 8 个用例未被改坏 —— 8/8 仍绿（退出码 0）
- [ ] 行为探测通过（不依赖模型自述） —— AssertionError: 用例数 22，基线 21 —— 至少补两条跨天的防回归用例
- [x] 任务正常收尾（completed） —— 终止原因 completed
- [x] 改动落在代码里（≥2 个文件） —— 2 个文件有差异：duration/format.py, tests/test_format.py

### gf-calculator · repeat 0
- [ ] 行为探测通过（不依赖模型自述） —— ImportError: cannot import name 'DivideByZeroError' from 'calculator' (D:\Embodied Agent\Agent-JD\mini-claude-code\eval\.work\live\work\gf-calculator.r0\calcula
- [x] `python -m pytest tests -q --no-header -p no:cacheprovider --confcutdir . --rootdir .` 退出码 0 —— 退出码 0 —— 38 passed in 0.04s
- [x] 要求的文件都在、禁止的文件没出现 —— 4 个应有文件全部存在
- [x] 任务正常收尾（completed） —— 终止原因 completed
- [x] 改动落在代码里（≥4 个文件） —— 4 个文件有差异：README.md, calculator/__init__.py, calculator/core.py, tests/test_calculator.py
