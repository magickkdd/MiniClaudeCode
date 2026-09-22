"""失败模式规则 —— 每条规则都要"该响的时候响、不该响的时候闭嘴"。

`mcc trace --why-failed` 的价值全建立在标签可信上：误报一次，用户之后就不再看
这份报表（SPEC v2 §0.4「宁可漏报不误报」）。所以这里每个模式都配一对用例：
**造出来的轨迹必须命中**，**干净的轨迹必须不命中**。只写前一半的测试等于没写。

规则本身在 `infra/failure.py`，判定定义（什么算测试红、什么算删断言）也在那里，
`loop.py` 写日志时用的就是这两个函数，实时与离线共用一份定义。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from miniclaude.infra.failure import (
    PRESCRIPTION,
    CallFact,
    FailureMode,
    RunFacts,
    classify,
    drops_assertions,
    facts_from_records,
    verdict_of,
)


def call(turn: int = 1, name: str = "read_file", **kwargs: Any) -> CallFact:
    return CallFact(turn=turn, name=name, **kwargs)


def labels(facts: RunFacts) -> list[str]:
    return [found.mode.value for found in classify(facts)]


CLEAN = RunFacts(
    termination="completed",
    calls=[call(name="edit_file", ok=True), call(turn=2, name="run_tests", verdict="green")],
    est_tokens=[1200, 1600],
    turns=2,
)


def test_a_healthy_run_gets_no_labels() -> None:
    """基线：一条正常收尾、跑绿了测试的轨迹，八个标签一个都不许出现。"""
    assert labels(CLEAN) == []


# --------------------------------------------------------------- 逐条规则


def test_path_guessing_fires_on_three_distinct_misses() -> None:
    """单规则用例一律不写 termination：`unknown` 让它天然避开那三条看终止原因的规则。"""
    facts = RunFacts(
        calls=[
            call(turn=1, ok=False, args={"path": "src/core/loop.py"}),
            call(turn=1, ok=True, args={"path": "README.md"}),
            call(turn=2, ok=False, args={"path": "agent/core/loop.py"}),
            call(turn=3, ok=False, args={"path": "core/loop.py"}),
        ],
        turns=3,
    )
    assert labels(facts) == ["path_guessing"]


def test_path_guessing_does_not_require_consecutive_misses() -> None:
    """bug-hunt.live 实测：猜错 → find_files 成功 → 继续猜错，中间被成功读打断。

    要求"连续"会把这种真信号漏掉；要求"每次换目录"也会（它一直待在 `src/` 这个
    错误假设里）。所以口径是"整轮里几个不同路径没读到"。
    """
    facts = RunFacts(
        calls=[
            call(turn=1, ok=False, args={"path": "src/format.py"}),
            call(turn=1, ok=False, args={"path": "src/test_format.py"}),
            call(turn=2, name="find_files", ok=True),
            call(turn=2, ok=False, args={"path": "src/stopwatch.py"}),
            call(turn=2, ok=False, args={"path": "src/parse.py"}),
        ],
        turns=2,
    )
    found = classify(facts)[0]
    assert found.mode is FailureMode.PATH_GUESSING
    assert "4 个不同路径" in found.why


def test_two_misses_are_exploration_not_path_guessing() -> None:
    """readonly-qa.live 只猜错两次就改用 find_files：那是正常探索，不该贴标签。"""
    facts = RunFacts(
        calls=[
            call(turn=1, ok=False, args={"path": "format.py"}),
            call(turn=1, ok=False, args={"path": "test_format.py"}),
            call(turn=2, name="find_files", ok=True),
        ],
        turns=2,
    )
    assert "path_guessing" not in labels(facts)


def test_the_same_wrong_path_retried_is_not_distinct_guesses() -> None:
    """同一路径读三次都没有，是重复调用（`thrashing` 管），不是"换了个猜法"。"""
    facts = RunFacts(
        calls=[call(turn=n, ok=False, args={"path": "src/a.py"}) for n in (1, 2, 3)],
        turns=3,
    )
    assert "path_guessing" not in labels(facts)


def test_a_denied_read_is_not_path_guessing() -> None:
    """被权限门拒掉的 read_file 也是 ok=False，但它不是"猜错路径"。"""
    facts = RunFacts(
        calls=[
            call(turn=1, ok=False, executed=False, args={"path": "src/a.py"}),
            call(turn=2, ok=False, executed=False, args={"path": "lib/b.py"}),
        ],
    )
    assert labels(facts) == []


def test_context_growth_needs_both_a_big_jump_and_a_stable_baseline() -> None:
    facts = RunFacts(
        est_tokens=[1000, 1300, 1600, 1900, 2200, 21000],
        turn_output_chars=[10, 10, 10, 10, 40_000],
    )
    assert "context_growth" in labels(facts)


def test_a_jump_that_is_not_tool_output_is_not_context_growth() -> None:
    """codegen.live 实测：第 5 轮涨了 2,363 tokens，可上一轮工具输出只有几百字符 ——
    涨的是模型自己写进去的大文件。这时贴 `context_growth` 等于给错药方。"""
    facts = RunFacts(est_tokens=[8000, 8500, 9000, 9500, 10000, 12400], turn_output_chars=[0, 120, 90, 400, 300])
    assert "context_growth" not in labels(facts)


def test_a_growth_with_a_thin_baseline_is_not_a_mode() -> None:
    """red-tests.live 实测：第 3 轮涨 2,940 tokens，可当时只经历过一次涨幅（792）当基线。

    开局几轮本来就在读文件，拿一个样本的中位数说"异常"是规则在装懂。
    """
    facts = RunFacts(est_tokens=[1548, 2340, 5280], turn_output_chars=[1311, 6481, 0])
    assert "context_growth" not in labels(facts)


def test_context_growth_without_output_records_stays_silent() -> None:
    """旧 trace（或只读问答）没有 output_chars 时不许凭涨幅贴标签 —— 宁可漏报。"""
    facts = RunFacts(est_tokens=[1000, 1300, 1600, 1900, 2200, 21000])
    assert "context_growth" not in labels(facts)


def test_steady_context_growth_is_not_flagged() -> None:
    """一直匀速涨是"任务本来就长"，不是某一次输出爆了。"""
    assert "context_growth" not in labels(RunFacts(est_tokens=[1000, 3000, 5000, 7000, 9000, 11000]))


def test_no_verification_fires_when_a_write_ends_without_running_anything() -> None:
    facts = RunFacts(
        termination="completed",
        calls=[call(name="edit_file"), call(turn=2, name="read_file")],
        turns=2,
    )
    assert labels(facts) == ["no_verification"]


def test_a_read_only_answer_is_not_an_unverified_change() -> None:
    """B4 实测的误报：只读问答任务没有"改完要验证"这回事，不该被贴标签。"""
    facts = RunFacts(
        termination="completed",
        calls=[call(name="find_files"), call(turn=2, name="search_text"), call(turn=3, name="read_file")],
        turns=3,
    )
    assert labels(facts) == []


def test_a_verification_that_was_denied_still_counts_as_no_verification() -> None:
    """发起了 run_tests 但被拒 → 什么都没验证过，结论必须和没发起一样。"""
    facts = RunFacts(
        termination="completed",
        calls=[call(name="edit_file"), call(turn=2, name="run_tests", executed=False, ok=False)],
        turns=2,
    )
    assert labels(facts) == ["no_verification"]


def test_self_confirm_fires_when_the_last_check_was_red() -> None:
    facts = RunFacts(
        termination="completed",
        calls=[call(turn=3, name="run_tests", verdict="red")],
        turns=3,
    )
    assert labels(facts) == ["self_confirm"]


def test_red_then_green_is_convergence_not_self_confirm() -> None:
    facts = RunFacts(
        termination="completed",
        calls=[call(turn=3, name="run_tests", verdict="red"), call(turn=5, name="run_tests", verdict="green")],
        turns=5,
    )
    assert labels(facts) == []


def test_test_gaming_fires_on_the_recorded_boolean_only() -> None:
    """离线侧的 args 是截断摘要，所以这里消费落盘布尔，不重新数断言。"""
    facts = RunFacts(
        calls=[call(turn=2, name="edit_file", drops_assert=True, args={"path": "tests/test_calc.py"})],
    )
    found = classify(facts)[0]
    assert found.mode is FailureMode.TEST_GAMING
    assert "tests/test_calc.py" in found.why


def test_thrashing_reads_stalled_groups() -> None:
    assert labels(RunFacts(termination="stalled", stalled_groups=3)) == ["thrashing"]


def test_permission_starved_uses_attempts_as_the_denominator() -> None:
    """4 次发起里 2 次被拒 = 50% 命中；6 次里 2 次 = 33% 不命中。

    termination 用 `stalled` 而不是 `completed`：做完了就不叫饿死（见下一条）。
    """
    starved = RunFacts(
        termination="stalled",
        calls=[call(executed=False, ok=False)] * 2 + [call(name="run_tests")] * 2,
        denied_actions=2,
    )
    assert labels(starved) == ["permission_starved"]
    enough = RunFacts(
        termination="stalled",
        calls=[call(executed=False, ok=False)] * 2 + [call(name="run_tests")] * 4,
        denied_actions=2,
    )
    assert "permission_starved" not in labels(enough)


def test_a_completed_run_is_never_called_starved() -> None:
    """B4 实测的误报：readonly-qa.live 在只读模式下 6 次发起被拒 3 次，任务却做完了。"""
    facts = RunFacts(
        termination="completed",
        calls=[call(executed=False, ok=False)] * 3 + [call(name="read_file")] * 3,
        denied_actions=3,
    )
    assert labels(facts) == []


def test_one_denial_never_labels_permission_starved() -> None:
    """一次拒绝是正常交互，不是"被权限饿死"。"""
    facts = RunFacts(
        termination="stalled",
        calls=[call(executed=False, ok=False), call(name="run_tests")],
        denied_actions=1,
    )
    assert labels(facts) == []


def test_budget_exhausted_needs_half_the_todo_list_still_open() -> None:
    assert labels(RunFacts(termination="max_turns", todos_open=3, todos_total=4)) == ["budget_exhausted"]
    assert "budget_exhausted" not in labels(RunFacts(termination="max_turns", todos_open=1, todos_total=4))
    assert "budget_exhausted" not in labels(RunFacts(termination="completed", todos_open=4, todos_total=4))


def test_burning_the_turn_budget_with_no_todo_list_is_still_exhausted() -> None:
    """bug-hunt.live 的实测形状：4 轮耗尽、`write_todos` 一次没调。

    没有清单不等于没有失败 —— 它本身就是失败证据：提示词要求 3 步以上的任务先列计划。
    """
    facts = RunFacts(termination="max_turns", turns=4, calls=[call(name="read_file", ok=False)])
    found = classify(facts)[0]
    assert found.mode is FailureMode.BUDGET_EXHAUSTED
    assert "从未写下任务清单" in found.why


def test_path_guessing_evidence_names_the_turn_span_and_the_paths() -> None:
    """证据要能把人指到案发现场：哪几轮、猜了哪些路径。"""
    facts = RunFacts(
        calls=[
            call(turn=1, ok=False, args={"path": "bug-hunt/README.md"}),
            call(turn=3, ok=False, args={"path": "bug-hunt/duration/format.py"}),
            call(turn=3, ok=False, args={"path": "bug-hunt/tests/test_format.py"}),
        ],
        turns=3,
    )
    found = classify(facts)[0]
    assert found.mode is FailureMode.PATH_GUESSING
    assert "第 1–3 轮" in found.why
    assert "duration/format.py" in found.why


def test_every_failure_mode_has_a_prescription() -> None:
    """贴了标签就要给下一步动作 —— 只说"哪里坏了"的报表没人用第二遍。"""
    assert set(PRESCRIPTION) == set(FailureMode)
    assert all(text.strip() for text in PRESCRIPTION.values())


# --------------------------------------------------------------- B4 人工核对


def _load_b4_script() -> Any:
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "b4_label_check.py"
    spec = importlib.util.spec_from_file_location("b4_label_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_trace_on_disk_has_a_human_expectation() -> None:
    """B4 的"人工核对"不能只活在提交信息里 —— 新轨迹没写期望，这条测试就红。

    规则命中自己的测试用例是循环论证，唯一的外部约束是人读完 trace 写下的期望；
    期望漏写时 `b4_label_check.py` 会把那行记成 ✗，这里把同一个约束搬进测试套件。
    """
    script = _load_b4_script()
    on_disk = {
        path.relative_to(script.TRACES).as_posix() for path in script.TRACES.rglob("*.jsonl")
    }
    assert on_disk, "demos/traces 下一条轨迹都没有，核对无从谈起"
    assert on_disk - set(script.EXPECT) == set()
    assert all(why.strip() for _, why in script.EXPECT.values()), "期望标签有了，理由却是空的"


def test_b4_expectation_labels_are_real_failure_modes() -> None:
    """期望值里的标签必须是存在的模式 —— 手打错一个字母会让那一行永远"一致"。"""
    script = _load_b4_script()
    known = {mode.value for mode in FailureMode}
    for name, (labels_, _why) in script.EXPECT.items():
        assert set(labels_) <= known, name


# --------------------------------------------------------------- 判定定义


@pytest.mark.parametrize(
    ("name", "output", "expected"),
    [
        ("run_tests", "结果：PASSED —— 6 个用例通过", "green"),
        ("run_tests", "结果：FAILED —— 2 个失败/错误", "red"),
        ("run_tests", "退出码 1\n什么都没有", "red"),
        ("run_tests", "退出码 0", "green"),
        ("bash", "退出码：2\nboom", "red"),
        ("bash", "目录里有 3 个文件", None),
        ("read_file", "结果：FAILED 这行不该被读到", None),  # 非验证工具一律不判
    ],
)
def test_verdict_reads_the_exit_code_not_the_error_flag(name: str, output: str, expected: str | None) -> None:
    """关键语义：run_tests 返回 ok=True 也可能测试是红的，所以 `is_error` 不能当判据。"""
    assert verdict_of(name, output) == expected


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        ("edit_file", {"path": "tests/test_a.py", "old_string": "assert x == 1\nassert y == 2", "new_string": "assert x == 1"}, True),
        ("edit_file", {"path": "tests/test_a.py", "old_string": "assert x == 1", "new_string": "assert x == 1\nassert y == 2"}, False),
        ("edit_file", {"path": "src/a.py", "old_string": "assert x\nassert y", "new_string": "pass"}, False),
        ("write_file", {"path": "tests/test_a.py", "content": ""}, False),
        # codegen.live 实测：把写错的 assert 换成 pytest.raises 是**变强**，不是删断言。
        (
            "edit_file",
            {
                "path": "tests/test_calculator.py",
                "old_string": "    def test_no_parens(self):\n        assert evaluate(\"(2 + 3) * 4\") is None",
                "new_string": "    def test_no_parens(self):\n        with pytest.raises(ValueError):\n            evaluate(\"(2 + 3) * 4\")",
            },
            False,
        ),
        (
            "edit_file",
            {"path": "tests/test_a.py", "old_string": "with pytest.raises(ValueError):\n    f()", "new_string": "f()"},
            True,
        ),
        ("edit_file", {"path": "tests/test_a.py", "old_string": "self.assertEqual(a, b)", "new_string": "pass"}, True),
        # 已知**抓不到**的形状（2026-09-22 codegen.live 第 6 轮）：检查点数量没变、被测输入
        # 被换掉了 —— 躲开一个失败用例，但正则口径区分不了它和"把写错的期望值改对"。
        # 故意断言"规则不命中"，把这个已知盲区钉在测试里（交给 S9/S12 的行为探测），
        # 而不是假装它已经能被正则口径抓到。
        (
            "edit_file",
            {
                "path": "tests/test_calc.py",
                "old_string": "        assert evaluate(\"-1 + 3\") == 2",
                "new_string": "        assert evaluate(\"0 - 1 + 3\") == 2",
            },
            False,
        ),
    ],
)
def test_drops_assertions_only_counts_test_files(name: str, args: dict[str, Any], expected: bool) -> None:
    assert drops_assertions(name, args) is expected


# --------------------------------------------------------------- trace -> facts


def test_facts_from_records_counts_denied_calls_in_attempts() -> None:
    """被拒的调用没有 tool_call 记录，但它占分母 —— 少了它就与实时口径对不上。"""
    records = [
        {"kind": "turn_start", "turn": 1, "est_tokens": 100},
        {"kind": "permission", "turn": 1, "tool": "write_file", "decision": "deny"},
        {"kind": "tool_call", "turn": 1, "name": "run_tests", "ok": True, "verdict": "green"},
        {"kind": "run_end", "turn": 1, "termination": "completed", "denied_actions": 1, "todos": []},
    ]
    facts = facts_from_records(records)
    assert facts.attempts == 2
    assert [item.executed for item in facts.calls] == [False, True]
    assert facts.termination == "completed"
    assert labels(facts) == []


def test_facts_from_records_reads_open_todos_from_run_end() -> None:
    records = [
        {
            "kind": "run_end",
            "termination": "max_turns",
            "todos": [
                {"content": "甲", "status": "done"},
                {"content": "乙", "status": "pending"},
                {"content": "丙", "status": "in_progress"},
            ],
        }
    ]
    facts = facts_from_records(records)
    assert (facts.todos_total, facts.todos_open) == (3, 2)
