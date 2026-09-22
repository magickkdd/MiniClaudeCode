"""批次指标 —— SPEC v2 §3.2 指标表的实现。

三条写死的规矩：

1. 每个数字都能追到 `RunRecord` 的某个字段，而那个字段又追到 trace 的一条记录。
   中位数不是"大概是两轮"，是 `sorted(...)[n // 2]`。
2. **n 小的时候不许给结论。** `success_rate_ci` 给 Wilson 95% 区间；24 道题跑一次，
   区间宽到足够包住"完全不行"和"相当能行"两种解释，所以报表里 CI 比点估计大就先说 CI。
3. 比率类指标一律"分子分母都摆出来"，`pass_at_1` 是 6/24 而不是 25%。
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from statistics import median
from typing import Any, Sequence

Z_95 = 1.959_963_984_540_054  # 双侧 95%


def wilson_ci(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """比例的 Wilson 得分区间。空样本给 (0, 1)：什么都不知道就别装知道。"""
    if total <= 0:
        return 0.0, 1.0
    phat = successes / total
    z2 = z * z
    denom = 1.0 + z2 / total
    centre = (phat + z2 / (2 * total)) / denom
    margin = (z / denom) * math.sqrt(phat * (1 - phat) / total + z2 / (4 * total * total))
    return max(0.0, centre - margin), min(1.0, centre + margin)


def percentile(values: Sequence[float], pct: float) -> float:
    """线性插值分位数。`values` 空时给 0.0 —— 报表里"没有样本"与"样本是 0"要分开写。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * pct
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    return float(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


def summarize_batch(runs: Sequence[Any]) -> dict[str, Any]:
    """一批 `RunRecord` → SPEC v2 §3.2 的指标表。

    `runs` 里可以是同一任务的多次重复：`pass_at_1` 只看 `repeat == 0`，
    `pass_at_k` 看"这个任务至少成一次"，两者差值就是"重试换来的"，必须分开报。
    """
    if not runs:
        return {"runs": 0}

    first = [run for run in runs if run.repeat == 0]
    passed_first = sum(1 for run in first if run.verdict == "pass")
    by_task: dict[str, list[Any]] = defaultdict(list)
    for run in runs:
        by_task[run.task_id].append(run)
    solved = [task_id for task_id, group in by_task.items() if any(run.verdict == "pass" for run in group)]

    successes_steps = [run.metric("turns") for run in runs if run.verdict == "pass"]
    calls = sum(run.metric("tool_calls") for run in runs)
    executed = sum(run.metric("tool_executed") for run in runs)
    errors = sum(run.metric("tool_errors") for run in runs)
    repeated = sum(run.metric("repeated_calls") for run in runs)
    stalled_groups = sum(run.metric("stalled_groups") for run in runs)
    denied = sum(run.metric("denied_actions") for run in runs)
    output_chars = sum(run.metric("output_chars") for run in runs)
    omitted_chars = sum(run.metric("omitted_output_chars") for run in runs)
    tokens_total = sum(run.tokens for run in runs)
    cost_total = sum(run.cost_est or 0.0 for run in runs)
    walls = [run.wall_ms for run in runs]
    peaks = [run.metric("context_peak_tokens") for run in runs]
    mode_dist: Counter[str] = Counter(mode for run in runs for mode in run.failure_modes)

    passed = sum(1 for run in runs if run.verdict == "pass")
    return {
        "runs": len(runs),
        "tasks": len(by_task),
        "repeats": max(len(group) for group in by_task.values()),
        "engine": sorted({run.engine for run in runs}),
        "verdicts": {"pass": passed, "fail": len(runs) - passed - _errored(runs), "error": _errored(runs)},
        "pass_at_1": _rate(passed_first, len(first)),
        "pass_at_k": _rate(len(solved), len(by_task)),
        "pass_at_1_raw": f"{passed_first}/{len(first)}",
        "pass_at_k_raw": f"{len(solved)}/{len(by_task)}",
        "success_rate_ci": [round(value, 4) for value in wilson_ci(passed, len(runs))],
        "steps_to_success_median": median(successes_steps) if successes_steps else None,
        "tool_error_rate": _rate(errors, calls),
        "tool_executed": executed,
        "repeated_call_rate": _rate(repeated, calls),
        "stalled_group_rate": _rate(stalled_groups, sum(run.metric("turns") for run in runs)),
        "denial_rate": _rate(denied, calls),
        "wasted_output_ratio": _rate(omitted_chars, output_chars),
        "context_peak_p95": round(percentile(peaks, 0.95)),
        "tokens_total": tokens_total,
        "tokens_per_success": round(tokens_total / passed) if passed else None,
        "cost_est_total": round(cost_total, 4) if cost_total else None,
        "failure_mode_dist": {mode: count for mode, count in sorted(mode_dist.items(), key=lambda kv: (-kv[1], kv[0]))},
        "wall_time_p50_ms": round(percentile(walls, 0.50)),
        "wall_time_p95_ms": round(percentile(walls, 0.95)),
        "aborted_batch": any(run.verdict == "aborted" for run in runs),
    }


def _errored(runs: Sequence[Any]) -> int:
    return sum(1 for run in runs if run.verdict not in ("pass", "fail"))


def _rate(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def per_tag(runs: Sequence[Any]) -> dict[str, dict[str, Any]]:
    """按标签分组。只看总通过率会把"bugfix 全过、只读问答全挂"这种结构性问题抹平。"""
    groups: dict[str, list[Any]] = defaultdict(list)
    for run in runs:
        for tag in run.tags:
            groups[tag].append(run)
    out: dict[str, dict[str, Any]] = {}
    for tag, group in sorted(groups.items()):
        first = [run for run in group if run.repeat == 0]
        passed = sum(1 for run in first if run.verdict == "pass")
        out[tag] = {
            "runs": len(group),
            "pass_at_1_raw": f"{passed}/{len(first)}",
            "pass_at_1": _rate(passed, len(first)),
            "failure_modes": sorted({mode for run in group for mode in run.failure_modes}),
        }
    return out
