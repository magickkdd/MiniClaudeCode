"""指标的测试 —— SPEC v2 §3.2 那张表的三条纪律，逐条钉住。

1. **每个数字都能追到 `RunRecord` 的某个字段。** 这里对每个键都用另一条路径重算一遍：
   对不上就是"报表里有个数字没人生产过"（v1 的 `redundant_calls` 事故）。
2. **n 小的时候不许给结论。** `success_rate_ci` 给 Wilson 95% 区间；24 道题跑一次，
   区间宽到足够包住两种相反的解释，所以报表里 CI 比点估计大就先说 CI。
3. **比率类指标一律分子分母都摆出来。** `pass_at_1` 是 `6/24` 而不只是 25%。
"""

from __future__ import annotations

from typing import Any

import pytest
from miniclaude.eval.metrics import per_tag, percentile, summarize_batch, wilson_ci
from miniclaude.eval.runner import RunRecord


def record(
    task_id: str,
    verdict: str,
    *,
    repeat: int = 0,
    turns: int = 3,
    calls: int = 4,
    errors: int = 0,
    executed: int = 4,
    repeated: int = 0,
    stalled: int = 0,
    denied: int = 0,
    tokens: int = 1000,
    peak: int = 8000,
    wall: int = 900,
    out_chars: int = 500,
    omitted: int = 0,
    modes: tuple[str, ...] = (),
    tags: tuple[str, ...] = ("bugfix",),
    engine: str = "fake",
    cost: float | None = None,
) -> RunRecord:
    return RunRecord(
        task_id=task_id,
        repeat=repeat,
        engine=engine,
        verdict=verdict,
        trace_path=None,
        metrics={
            "turns": turns,
            "tool_calls": calls,
            "tool_executed": executed,
            "tool_errors": errors,
            "repeated_calls": repeated,
            "stalled_groups": stalled,
            "denied_actions": denied,
            "tokens": tokens,
            "context_peak_tokens": peak,
            "wall_ms": wall,
            "output_chars": out_chars,
            "omitted_output_chars": omitted,
        },
        failure_modes=list(modes),
        wall_ms=wall,
        cost_est=cost,
        tags=tags,
    )


# ---------------------------------------------------------------- 可溯源性


def test_every_number_in_the_table_is_recomputed_from_records() -> None:
    """这张表里没有"额外信息"：每个键都要能从 `runs` 重算出来。

    写这条测试的理由是 v1 的教训：一个指标定义了、进了 snapshot、被报表读、印在
    证据文件里，全代码库没有一处累加它。那种数字不会报错，只会一直看起来对。
    """
    runs = [
        record("a", "pass", turns=2, calls=5, errors=1, tokens=1200, peak=9000, wall=800, cost=0.5),
        record("b", "fail", calls=3, errors=0, repeated=2, stalled=1, denied=1, tokens=800, peak=4000,
               wall=1200, out_chars=600, omitted=300, modes=("thrashing",)),
        record("b", "fail", repeat=1, tokens=800),
    ]
    s = summarize_batch(runs)
    assert s["runs"] == 3 and s["tasks"] == 2 and s["repeats"] == 2
    assert s["engine"] == ["fake"]
    assert s["verdicts"] == {"pass": 1, "fail": 2, "error": 0}
    assert s["tokens_total"] == sum(run.tokens for run in runs)
    assert s["tool_error_rate"] == round(1 / (5 + 3 + 4), 4)
    assert s["tool_executed"] == 4 + 4 + 4
    assert s["repeated_call_rate"] == round(2 / (5 + 3 + 4), 4)
    assert s["denial_rate"] == round(1 / (5 + 3 + 4), 4)
    assert s["stalled_group_rate"] == round(1 / (2 + 3 + 3), 4)
    assert s["wasted_output_ratio"] == round(300 / (500 + 600 + 500), 4)
    assert s["cost_est_total"] == pytest.approx(0.5)
    assert s["failure_mode_dist"] == {"thrashing": 1}
    assert s["steps_to_success_median"] == 2, "中位数只统计通过的那些运行"
    assert s["wall_time_p50_ms"] == percentile([800, 1200, 900], 0.50)
    assert s["context_peak_p95"] == round(percentile([9000, 4000, 8000], 0.95))
    assert s["aborted_batch"] is False


def test_no_key_is_invented_by_the_report() -> None:
    """新加一个报表键，必须同时在这儿写清它从哪个字段来。"""
    s = summarize_batch([record("a", "pass")])
    assert set(s) == {
        "runs", "tasks", "repeats", "engine", "verdicts", "pass_at_1", "pass_at_k",
        "pass_at_1_raw", "pass_at_k_raw", "success_rate_ci", "steps_to_success_median",
        "tool_error_rate", "tool_executed", "repeated_call_rate", "stalled_group_rate",
        "denial_rate", "wasted_output_ratio", "context_peak_p95", "tokens_total",
        "tokens_per_success", "cost_est_total", "failure_mode_dist",
        "wall_time_p50_ms", "wall_time_p95_ms", "aborted_batch",
    }, "改了指标集合就同步 SPEC v2 §3.2 的表"


def test_an_empty_batch_says_so_instead_of_averaging_nothing() -> None:
    assert summarize_batch([]) == {"runs": 0}


# ---------------------------------------------------------------- pass@1 与 pass@k


def test_retries_are_reported_separately_from_first_try() -> None:
    """第一次没做对、重试做对了 —— 这两个数必须分开，差值就是"重试换来的"。"""
    runs = [record("a", "fail"), record("a", "pass", repeat=1), record("a", "pass", repeat=2)]
    s = summarize_batch(runs)
    assert s["pass_at_1_raw"] == "0/1" and s["pass_at_1"] == 0.0
    assert s["pass_at_k_raw"] == "1/1" and s["pass_at_k"] == 1.0
    assert s["repeats"] == 3


def test_pass_at_k_denominates_tasks_not_runs() -> None:
    runs = [record("a", "pass"), record("b", "fail"), record("b", "pass", repeat=1)]
    assert summarize_batch(runs)["pass_at_k_raw"] == "2/2"
    assert summarize_batch(runs)["pass_at_1_raw"] == "1/2"


def test_aborted_runs_are_counted_as_error_not_fail() -> None:
    """中止的批次既不是"做错了"也不是"没做"，混进 fail 会低估判据的准确率。"""
    runs = [record("a", "aborted")]
    s = summarize_batch(runs)
    assert s["verdicts"] == {"pass": 0, "fail": 0, "error": 1}
    assert s["aborted_batch"] is True


# ---------------------------------------------------------------- 区间


@pytest.mark.parametrize(
    ("successes", "total", "low", "high"),
    [(0, 0, 0.0, 1.0), (0, 10, 0.0, 0.2775), (10, 10, 0.7225, 1.0), (5, 10, 0.2366, 0.7634)],
)
def test_wilson_ci_known_values(successes: int, total: int, low: float, high: float) -> None:
    """空样本给 (0, 1)：什么都不知道就别装知道。其余值对着 Wilson 表手算。"""
    got = wilson_ci(successes, total)
    assert got[0] == pytest.approx(low, abs=5e-4)
    assert got[1] == pytest.approx(high, abs=5e-4)
    if total:
        # 10/10 的上界是 1.0 - 1e-16：`min(1.0, ...)` 只会截掉越界的部分，不会把
        # 差一点点的浮点结果抬到 1.0。这里容 1e-9，而不是给生产代码加装饰性舍入。
        assert got[0] - 1e-9 <= successes / total <= got[1] + 1e-9


def test_small_samples_get_a_wide_interval() -> None:
    """6 次跑对的区间必须明显宽于 240 次 —— 报表里"先说 CI"就是靠这条立得住的。"""
    small = wilson_ci(5, 6)
    large = wilson_ci(200, 240)
    assert (small[1] - small[0]) > 3 * (large[1] - large[0])
    s = summarize_batch([record("a", "pass"), record("b", "pass"), record("c", "fail")])
    low, high = s["success_rate_ci"]
    assert high - low > 0.5, f"3 个样本的区间本该宽到盖住整个 [0,1]：{s['success_rate_ci']}"


def test_rate_helper_survives_a_zero_denominator() -> None:
    """没有任何工具调用的运行（比如只读问答）不能让报表崩在除零上。"""
    s = summarize_batch([record("a", "pass", calls=0, errors=0, out_chars=0, omitted=0, turns=0)])
    assert s["tool_error_rate"] == 0.0
    assert s["stalled_group_rate"] == 0.0
    assert s["wasted_output_ratio"] == 0.0
    assert s["tokens_per_success"] == 1000


# ---------------------------------------------------------------- 分组


def test_per_tag_keeps_a_structural_failure_visible() -> None:
    """只看总通过率会把"bugfix 全过、只读问答全挂"抹平 —— 分组表就是为此存在。"""
    runs = [
        record("a", "pass", tags=("bugfix",)),
        record("b", "fail", tags=("readonly",), modes=("self_confirm",)),
        record("c", "fail", tags=("readonly",)),
    ]
    groups = per_tag(runs)
    assert groups["bugfix"]["pass_at_1_raw"] == "1/1"
    assert groups["readonly"]["pass_at_1_raw"] == "0/2"
    assert groups["readonly"]["failure_modes"] == ["self_confirm"]
    overall = summarize_batch(runs)
    assert overall["pass_at_1_raw"] == "1/3", "总数与分组数都摆出来，读者才能自己看出结构"


def test_a_run_can_belong_to_several_tags() -> None:
    runs = [record("a", "pass", tags=("bugfix", "whitelist")), record("b", "fail", tags=("whitelist",))]
    groups = per_tag(runs)
    assert groups["whitelist"]["runs"] == 2
    assert groups["bugfix"]["runs"] == 1


# ---------------------------------------------------------------- 分位数


@pytest.mark.parametrize(
    ("values", "pct", "expected"),
    [([], 0.5, 0.0), ([7], 0.95, 7.0), ([1, 2, 3, 4], 0.5, 2.5), ([1, 2, 3, 4], 0.0, 1.0), ([1, 2, 3, 4], 1.0, 4.0)],
)
def test_percentile_values(values: list[float], pct: float, expected: float) -> None:
    """空样本给 0.0 —— 但报表里"没有样本"与"样本是 0"要分开写，所以调用方得看 `runs`。"""
    assert percentile(values, pct) == pytest.approx(expected)


def test_p95_of_three_samples_is_interpolated_not_the_max() -> None:
    """3 个样本的 p95 落在第 3 个附近但不是它。小批量里这类差值最容易被当成"稳定"。"""
    assert percentile([8000, 9000, 4000], 0.95) == pytest.approx(8900.0)


def test_metrics_accept_records_loaded_from_disk() -> None:
    """`mcc eval` 的续跑路径喂进来的是反序列化的记录。指标层不能依赖内存对象的痕迹。"""
    from miniclaude.eval.runner import RunRecord as RC

    restored = RC.from_dict(record("a", "pass", cost=1.25).to_dict())
    s = summarize_batch([restored])
    assert s["cost_est_total"] == pytest.approx(1.25)
    assert s["tokens_total"] == 1000


def test_summary_is_json_serialisable() -> None:
    """报表要落 `report.json`，失败模式分布用的是元组键就会炸。"""
    import json

    runs: Any = [record("a", "pass", tags=("x",)), record("b", "fail", tags=("x",))]
    json.dumps({"summary": summarize_batch(runs), "per_tag": per_tag(runs)}, ensure_ascii=False)
