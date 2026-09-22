"""基线对比的测试 —— 这个模块的产物是"能不能把这批汇报出去"，所以每条结论都要钉死。

四件事必须成立，每件各一个测试：

1. 考卷变了（`taskset_sha` 不一致）时**只拒绝对比**，不给任何 Δ 数字；
2. pass→fail 逐题翻红要被抓出来，且 `blockers()` 非空（CI 靠它拦）；
3. 小样本上的显著性用精确二项，不用卡方近似 —— 顺带把 McNemar 的几个手算值钉住；
4. 判据自检（`must-fail` 题被判绿）与"没做对比 ≠ 没有差异"这两条口径不能糊过去。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from miniclaude.eval.regression import (
    INDISTINGUISHABLE,
    MUST_FAIL_TAG,
    Regression,
    baseline_from_report,
    blockers,
    compare,
    instrument_checks,
    load_baseline,
    mcnemar_exact,
    save_baseline,
    taskset_gate,
)
from miniclaude.eval.runner import BatchReport, RunRecord

SHA = "a" * 12


def run(
    task_id: str,
    verdict: str,
    *,
    repeat: int = 0,
    tags: tuple[str, ...] = (),
    broken: list[str] | None = None,
    **metrics: Any,
) -> RunRecord:
    return RunRecord(
        task_id=task_id,
        repeat=repeat,
        engine="fake",
        verdict=verdict,
        trace_path=None,
        metrics={"turns": 4, "tokens": 100, "termination": "completed", **metrics},
        tags=tags,
        checks=[{"label": "FAIL_TO_PASS 用例已转绿", "ok": verdict == "pass", "detail": "明细"}],
        broken=broken or [],
        taskset_sha=SHA,
    )


def report(runs: list[RunRecord], *, sha: str = SHA, **summary: Any) -> BatchReport:
    return BatchReport(
        runs=runs,
        summary={"runs": len(runs), "pass_at_1_raw": "1/2", "repeats": 1, **summary},
        per_tag={},
        taskset_sha=sha,
    )


def baseline_of(verdicts: dict[str, str], *, sha: str = SHA) -> dict[str, Any]:
    return {
        "schema": 1,
        "taskset_sha": sha,
        "engine": ["fake"],
        "summary": {"tokens_total": 1000, "wall_time_p50_ms": 100, "context_peak_p95": 900},
        "per_tag": {},
        "tasks": {tid: {"verdict": verdict} for tid, verdict in verdicts.items()},
    }


@dataclass
class _TaskSet:
    """只要 `sha` 与可迭代的 `id` —— 开跑前那道闸不该依赖真的 TaskSet。"""

    sha: str
    ids: list[str]

    def __iter__(self):
        return iter([type("T", (), {"id": tid})() for tid in self.ids])


# ---------------------------------------------------------------- 考卷一致性


def test_changed_taskset_refuses_to_compare_instead_of_giving_a_delta() -> None:
    """哈希不一致时**不许**出现 Δ 数字。改了 fixture 里那行 bug 能让分数平白涨四点，
    那种"进步"会被写进汇报 —— 所以这里只有一条 refused，没有第二行。
    """
    items = compare(baseline_of({"a": "pass"}, sha="b" * 12), report([run("a", "fail")]))
    assert [item.severity for item in items] == ["refused"]
    assert "不可比" in items[0].detail
    assert blockers(items), "拒绝对比本身就是不能拿去汇报的理由"


def test_taskset_gate_flags_a_different_paper_but_not_a_subset() -> None:
    """`--only` 出来的批次带着全量哈希，单看哈希看不出是子集；但"少跑几题"不是错误，
    花真模型额度去和一个不存在的对比才是。所以开跑前只对**哈希不一致**说不，
    子集只在终端提示一句（逐题对比那侧由 `compare` 的"没跑到"行负责）。
    """
    full = baseline_of({"a": "pass", "b": "pass", "c": "pass"})
    assert taskset_gate(full, _TaskSet(SHA, ["a"])) == []

    rows = taskset_gate(full, _TaskSet("d" * 12, ["a", "b", "c"]))
    assert [row.severity for row in rows] == ["refused"]
    assert "题集哈希" in rows[0].label


def test_load_baseline_rejects_files_it_did_not_write(tmp_path: Path) -> None:
    """缺 `tasks` 或 schema 对不上就报错。手写一份"漂亮成绩"当基线，之后每一批
    都会跟它比 —— 这个入口必须关上。
    """
    (tmp_path / "fake.json").write_text(json.dumps({"summary": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="tasks"):
        load_baseline(tmp_path / "fake.json")
    (tmp_path / "old.json").write_text(json.dumps({"schema": 0, "tasks": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        load_baseline(tmp_path / "old.json")
    with pytest.raises(FileNotFoundError):
        load_baseline(tmp_path / "missing.json")


def test_baseline_round_trip_drops_trace_paths(tmp_path: Path) -> None:
    """基线只存配对所需的最小事实。trace 路径是工作副本里的临时物，
    换台机器就是垃圾，留着只会让人以为基线可复现。
    """
    payload = baseline_from_report(report([run("a", "pass"), run("a", "pass", repeat=1)]), note="冒烟")
    assert set(payload["tasks"]) == {"a"}, "基线按任务存，重复次数的细节留在 report.json"
    assert "trace_path" not in json.dumps(payload)
    path = save_baseline(payload, tmp_path / "b" / "fake-x.json")
    assert load_baseline(path)["taskset_sha"] == SHA
    assert load_baseline(path)["note"] == "冒烟"


# ---------------------------------------------------------------- 逐题配对


def test_pass_to_fail_flip_is_a_blocker_with_evidence() -> None:
    items = compare(
        baseline_of({"a": "pass", "b": "pass"}),
        report([run("a", "fail", tags=("bugfix",), broken=["tests/test_a.py::test_x"]), run("b", "pass")]),
    )
    flip = [item for item in items if item.severity == "regression" and item.task_id == "a"]
    assert len(flip) == 1
    assert flip[0].before == "pass" and flip[0].after == "fail"
    # 明细必须能回答"哪一步挡住的"，否则这行结论在报表里等于没写。
    assert "FAIL_TO_PASS" in flip[0].detail and "tests/test_a.py::test_x" in flip[0].detail
    assert blockers(items)


def test_healing_a_task_is_reported_as_improvement_not_regression() -> None:
    items = compare(baseline_of({"a": "fail", "b": "pass"}), report([run("a", "pass"), run("b", "pass")]))
    assert [item.severity for item in items if item.task_id] == ["improvement"]
    assert not blockers(items), "没有退步就不该拦住这批"


def test_missing_runs_refuse_the_comparison_rather_than_counting_as_fixed() -> None:
    """基线里有、这批没跑到的题不能悄悄当成"没退步"。少跑 20 道题的批次当然
    一道都没退 —— 那是"没比"，不是"没退"。
    """
    items = compare(baseline_of({"a": "pass", "b": "pass"}), report([run("a", "pass")]))
    refused = [item for item in items if item.severity == "refused"]
    assert len(refused) == 1 and "没跑到" in refused[0].label


def test_rate_row_counts_only_common_tasks() -> None:
    """聚合 Δ 只在共同任务上算。拿"这次少了三题"换来的高分会被当成进步。"""
    baseline = baseline_of({"a": "pass", "b": "pass", "c": "pass"})
    items = compare(baseline, report([run("a", "pass"), run("b", "pass")]))
    rate = next(item for item in items if "pass@1" in item.label)
    assert "2/2 → 2/2" in rate.detail
    assert "Δ +0.0%" in rate.detail, "子集里全过不等于 3 题的 2/3 涨成了 100%"


def test_one_task_move_on_24_tasks_is_declared_indistinguishable() -> None:
    """24 题的颗粒度就是 1/24。把 ±1 题说成趋势是这类报表最常见的谎。

    逐题那一行仍然报"退步"（该查），聚合这行报"分辨不出"（别汇报）—— 两者不矛盾：
    配对信息在逐题行里，聚合显著性只是给"这批改了什么"下一个结论。
    """
    verdicts = {f"t{i}": "pass" for i in range(24)}
    now = dict(verdicts, t0="fail")
    items = compare(baseline_of(verdicts), report([run(tid, verdict) for tid, verdict in now.items()]))
    rate = next(item for item in items if "pass@1" in item.label)
    assert rate.severity == "noise"
    assert f"Δ {-1 / 24:+.1%}" in rate.detail
    assert INDISTINGUISHABLE == pytest.approx(1 / 8), "阈值写的就是'八分之一批'，改它要连文档一起改"
    assert "分辨不出" in rate.detail


def test_cost_growth_is_a_blocker_even_when_every_task_still_passes() -> None:
    """判据给不出"烧了五倍 token 算不算好消息"的结论，只能靠这一行摆出来。"""
    items = compare(
        baseline_of({"a": "pass", "b": "pass"}),
        report([run("a", "pass"), run("b", "pass")], tokens_total=6000, wall_time_p50_ms=110, context_peak_p95=950),
    )
    cost = next(item for item in items if "成本" in item.label)
    assert cost.severity == "regression"
    assert "+500.0%" in cost.detail
    assert blockers(items)


def test_a_zero_delta_cost_row_says_so_instead_of_crying_change() -> None:
    """续跑复用同一批记录时三个数字一个都不动。标题写"成本变化"却找不出变化，
    读者就会去明细里怀疑自己漏看了。
    """
    items = compare(
        baseline_of({"a": "pass", "b": "pass"}),
        report([run("a", "pass"), run("b", "pass")], tokens_total=1000, wall_time_p50_ms=100, context_peak_p95=900),
    )
    cost = next(item for item in items if "成本" in item.label)
    assert cost.label == "成本无变化"
    assert "+0.0%" in cost.detail
    assert not blockers(items)


# ---------------------------------------------------------------- 判据自检


def test_must_fail_task_turning_green_is_a_guard_alarm() -> None:
    """`must-fail` 是"判据必须抓住它"的题。它判绿不是进步，是量尺坏了。"""
    items = instrument_checks([run("tamper", "pass", tags=("negative", MUST_FAIL_TAG))])
    assert len(items) == 1
    assert items[0].severity == "guard"
    assert blockers(items)
    assert instrument_checks([run("tamper", "fail", tags=("negative", MUST_FAIL_TAG))]) == []


def test_honest_stop_is_not_flagged_even_though_the_task_is_a_negative_sample() -> None:
    """`negative` 里有一半要的是"agent 老实停下来"（stall / 超预算），判绿是对的。
    如果这里也报警，就会有人把告警关掉 —— 告警一多，没人看。
    """
    assert instrument_checks([run("stall", "pass", tags=("negative", "loop-guard"))]) == []


def test_only_repeat_zero_is_judged() -> None:
    """pass@1 与判据自检都只看 repeat 0；把 r1/r2 也算进去会把一次抖动说成两次事故。"""
    assert instrument_checks([run("t", "pass", repeat=2, tags=(MUST_FAIL_TAG,))]) == []


# ---------------------------------------------------------------- McNemar


@pytest.mark.parametrize(
    ("b01", "b10", "expected"),
    [(0, 0, 1.0), (1, 0, 1.0), (5, 0, 0.0625), (6, 0, 0.03125), (3, 3, 1.0)],
)
def test_mcnemar_exact_values(b01: int, b10: int, expected: float) -> None:
    """手算值钉住实现。n=1 时双侧 p 必须是 1.0 —— 单次翻转不构成任何结论。"""
    assert mcnemar_exact(b01, b10) == pytest.approx(expected, abs=1e-9)


def test_mcnemar_is_symmetric_and_monotone() -> None:
    """翻转方向不该影响 p 值；对子越多才越显著。"""
    assert mcnemar_exact(7, 1) == pytest.approx(mcnemar_exact(1, 7))
    assert mcnemar_exact(8, 0) < mcnemar_exact(5, 0) < mcnemar_exact(2, 0)


def test_regression_line_and_dict_carry_the_same_facts() -> None:
    """`line()` 给人看、`to_dict()` 给 CI 用（`jq -e '.summary.regressions == []'`）。
    两边口径必须来自同一份字段，否则报表与闸门会各说一套。
    """
    item = Regression(label="a 由 pass 变为 fail", detail="2 轮", severity="regression", task_id="a", before="pass", after="fail")
    assert item.line() == "**退步** a 由 pass 变为 fail —— 2 轮"
    assert item.to_dict()["severity"] == "regression"
    assert item.blocker is True
