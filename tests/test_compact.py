"""压缩阶梯测试（SPEC v2 §3.3 / 验收线 B2）。

这里测的不是"能不能变小"，而是三件更容易出事的事：
1. **绝不发出坏配对的历史** —— 压完还 400 比不压更糟；
2. **绝不丢掉 agent 正在依据的那份观察** —— 最近轮组必须原样保留；
3. **摘要里那些不能丢的字段由代码算，不靠模型复述** —— 押在模型记性上的判据
   测的是模型，不是我们的压缩。
"""

from __future__ import annotations

from typing import Callable, Sequence

import pytest

from miniclaude.agent.context import (
    L1_ELIDE_PRESSURE,
    L1_TARGET_PRESSURE,
    L2_SUMMARIZE_PRESSURE,
    ContextManager,
)
from miniclaude.messages import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    assert_pairing,
    pairing_problems,
)

BODY = "行内容 abcd1234 " * 200          # ≈2,600 chars，真实 read_file 的量级
SUMMARIZER: Callable[[list[Message], str], tuple[str, int]] = lambda dropped, goal: (  # noqa: E731
    f"目标：{goal}\n已确认事实：模块结构已看清",
    320,
)


def cm(budget: int = 1000, cpt: float = 3.5, **kwargs: object) -> ContextManager:
    return ContextManager(budget=budget, chars_per_token=cpt, **kwargs)  # type: ignore[arg-type]


def history(rounds: int = 8, *, body: str = BODY, writer_at: tuple[int, ...] = (2, 5)) -> list[Message]:
    """一条典型的工具往返历史：user 任务 → N 轮 (assistant 调用 + 结果)。"""
    msgs = [Message.user_text("修复 pkg 里的时区换算 bug，并补测试")]
    for i in range(rounds):
        name = "write_file" if i in writer_at else "read_file"
        args: dict[str, object] = {"path": f"pkg/mod{i}.py"}
        if name == "write_file":
            args["content"] = "def f(): pass\n" * 30
        msgs.append(
            Message.assistant([TextBlock(f"第 {i} 步"), ToolUseBlock(id=f"c{i}", name=name, input=args)])
        )
        msgs.append(Message.tool_results([ToolResultBlock(tool_use_id=f"c{i}", content=body)]))
    return msgs


def est(manager: ContextManager, msgs: Sequence[Message]) -> int:
    return manager.estimate(system="", tools=[], messages=msgs)


def levels(reports: list[object]) -> list[str]:
    return [str(r.level) for r in reports]  # type: ignore[attr-defined]


# --------------------------------------------------------------- 该不该压


def test_nothing_happens_below_the_l1_line() -> None:
    manager = cm(budget=100_000)
    msgs = history(3)
    assert manager.pressure(system="", tools=[], messages=msgs) < L1_ELIDE_PRESSURE
    assert manager.run_ladder(system="", tools=[], messages=msgs, summarizer=SUMMARIZER) == []


def test_a_zero_budget_disables_the_ladder_instead_of_dividing_by_zero() -> None:
    """预算没配 ≠ 立刻爆炸（v1 的 pressure_from_chars 就是这条口径）。"""
    manager = cm(budget=0)
    assert manager.run_ladder(system="", tools=[], messages=history(4), summarizer=SUMMARIZER) == []
    assert manager.level_for_pressure(9.9) is None


def test_turning_compaction_off_returns_the_history_untouched() -> None:
    """B2 要"压缩关闭时失败、开启时成功"，所以这个开关必须是真开关。"""
    manager = cm(budget=200, enabled=False)
    msgs = history(6)
    before = est(manager, msgs)
    reports = manager.run_ladder(system="", tools=[], messages=msgs, summarizer=SUMMARIZER)
    assert reports == [] or all(not r.changed for r in reports)
    assert manager.compact(system="", tools=[], messages=msgs, level="elide").messages == msgs
    assert est(manager, msgs) == before
    assert manager.snapshot()["compactions"] == 0


# --------------------------------------------------------------- L1 elide


def test_l1_elides_old_tool_results_and_leaves_the_recent_round_verbatim() -> None:
    manager = cm(budget=1000)
    msgs = history(8)
    report = manager.compact(system="", tools=[], messages=msgs, level="elide")

    assert report.elided_blocks > 0 and report.pairing_ok
    assert report.after_est < report.before_est
    # 最近一轮的结果必须还是原文：模型下一步就靠它，抹掉就开始瞎猜。
    last_result = [b for m in report.messages for b in m.content if isinstance(b, ToolResultBlock)][-1]
    assert last_result.content == BODY
    assert sum(1 for m in report.messages for b in m.content if isinstance(b, ToolResultBlock)
               and b.content.startswith("[elided:")) == report.elided_blocks


def test_l1_elides_only_as_far_as_the_target_line() -> None:
    """压到 0.65 就收手，不是把老历史全清光 —— 留缓冲，也别一次丢太多现场。"""
    manager = cm(budget=1000)
    report = manager.compact(system="", tools=[], messages=history(10), level="elide")
    target = L1_TARGET_PRESSURE * manager.budget
    assert report.elided_blocks < 10
    assert report.after_est >= target            # 不为过线多砍一刀
    assert report.after_est < report.before_est


def test_the_elision_marker_names_the_call_so_the_model_can_refetch_it() -> None:
    """只写"已省略"会逼模型重新乱找文件；标记要带上工具名和参数。"""
    manager = cm(budget=1000)
    report = manager.compact(system="", tools=[], messages=history(8), level="elide")
    markers = [
        b.content for m in report.messages for b in m.content
        if isinstance(b, ToolResultBlock) and b.content.startswith("[elided:")
    ]
    assert markers
    assert any("read_file" in text and "pkg/mod" in text for text in markers)
    assert any("chars" in text for text in markers)


def test_l1_never_grows_the_payload_even_when_outputs_are_tiny() -> None:
    """省略标记比 "ok" 长得多。不挡住这条，L1 会在短输出上反向膨胀。"""
    manager = cm(budget=50)
    report = manager.compact(
        system="", tools=[], messages=history(6, body="ok"), level="elide"
    )
    assert report.elided_blocks == 0
    assert report.after_est == report.before_est
    assert "没有可省略的老输出" in report.note


def test_l1_does_not_change_the_message_count_or_pairing() -> None:
    """L1 是唯一"绝对安全"的一层：只改字符串，不碰结构。这条要钉住。"""
    manager = cm(budget=1000)
    msgs = history(8)
    report = manager.compact(system="", tools=[], messages=msgs, level="elide")
    assert len(report.messages) == len(msgs)
    assert pairing_problems(report.messages) == []
    assert report.pairing_ok


def test_eliding_twice_is_a_no_op() -> None:
    manager = cm(budget=1000)
    first = manager.compact(system="", tools=[], messages=history(8), level="elide")
    again = manager.compact(system="", tools=[], messages=first.messages, level="elide")
    assert again.elided_blocks == 0
    assert again.after_est == first.after_est


# --------------------------------------------------------------- L2 summarize


def test_l2_replaces_whole_round_groups_and_still_pairs() -> None:
    manager = cm(budget=1000)
    msgs = history(8)
    report = manager.compact(system="", tools=[], messages=msgs, level="summarize", summarizer=SUMMARIZER)

    assert report.dropped_blocks > 0 and report.pairing_ok
    assert len(report.messages) < len(msgs)
    assert_pairing(report.messages)                       # B2：0 次因配对破损的 400
    ids = {b.id for m in report.messages for b in m.content if isinstance(b, ToolUseBlock)}
    results = {b.tool_use_id for m in report.messages for b in m.content if isinstance(b, ToolResultBlock)}
    assert ids == results                                 # 调用与结果同进同出


def test_l2_keeps_the_original_task_message() -> None:
    """压掉目标等于让 agent 失忆；摘要是补不回"用户到底要什么"的。"""
    manager = cm(budget=1000)
    report = manager.compact(
        system="", tools=[], messages=history(8), level="summarize", summarizer=SUMMARIZER
    )
    assert report.messages[0].text() == "修复 pkg 里的时区换算 bug，并补测试"


def test_the_summary_carries_changed_files_computed_locally_not_by_the_model() -> None:
    """B2 的字面要求：摘要里必须能 grep 到本轮已改文件名。

    判据不能押在模型有没有照模板写字段上 —— 上面那个 SUMMARIZER 就故意没写
    "已改动文件"，摘要里仍然必须有，因为那部分是代码算的。
    """
    manager = cm(budget=1000)
    report = manager.compact(
        system="", tools=[], messages=history(8, writer_at=(2, 5)), level="summarize",
        summarizer=SUMMARIZER,
    )
    assert report.summary_message is not None
    text = report.summary_message.text()
    assert "[上下文已压缩" in text
    assert "已改动文件" in text and "pkg/mod2.py" in text and "pkg/mod5.py" in text
    assert text.index("pkg/mod2.py") > text.index("目标：")   # 模型文本在前，本地统计附后


def test_the_summary_lists_which_files_actually_survived() -> None:
    """`summary_files` 记"留下了哪些"，不是"打算留哪些"。

    B2 的证据脚本要在 trace 里 grep 已改文件名，而 digest 有 12 条的截断上限 ——
    记意图会让那条判据在长会话上悄悄变成假绿灯。
    """
    manager = cm(budget=1000)
    report = manager.compact(
        system="", tools=[], messages=history(8, writer_at=(2, 5)), level="summarize",
        summarizer=SUMMARIZER,
    )
    assert report.summary_files == ("pkg/mod2.py", "pkg/mod5.py")
    assert report.as_trace()["summary_files"] == ["pkg/mod2.py", "pkg/mod5.py"]
    for path in report.summary_files:  # 报出来的每一个都必须真在替换消息里
        assert path in report.summary_message.text()  # type: ignore[union-attr]
    # 一次都没写的历史：没有清单可报，也没有"已改动文件"那段
    quiet = manager.compact(
        system="", tools=[], messages=history(8, writer_at=()), level="summarize",
        summarizer=SUMMARIZER,
    )
    assert quiet.summary_files == ()


def test_the_summary_marks_which_turns_it_stands_for() -> None:
    manager = cm(budget=1000)
    report = manager.compact(
        system="", tools=[], messages=history(8), level="summarize", summarizer=SUMMARIZER
    )
    head = report.summary_message.text().splitlines()[0]  # type: ignore[union-attr]
    assert "第 1–" in head and "轮]" in head


def test_l2_charges_its_own_tokens() -> None:
    """L2 不免费：报表要能说出"压缩这一档花了几次调用、多少 token"。"""
    manager = cm(budget=1000)
    report = manager.compact(
        system="", tools=[], messages=history(8), level="summarize", summarizer=SUMMARIZER
    )
    assert report.summary_tokens == 320
    snapshot = manager.snapshot()
    assert snapshot["summaries"] == 1 and snapshot["summary_tokens"] == 320


def test_a_summarizer_that_returns_nothing_still_yields_a_usable_digest() -> None:
    """模型偶尔会回一段空话。这时摘要消息不该是空的 —— 本地统计必须顶上。"""
    manager = cm(budget=1000)
    report = manager.compact(
        system="", tools=[], messages=history(8), level="summarize",
        summarizer=lambda dropped, goal: ("   ", 12),
    )
    text = report.summary_message.text()  # type: ignore[union-attr]
    assert "已改动文件" in text and "pkg/mod0.py" not in text.split("已改动文件")[0]


def test_summarize_without_a_summarizer_is_a_programming_error_not_a_silent_skip() -> None:
    manager = cm(budget=1000)
    with pytest.raises(ValueError, match="summarizer"):
        manager.compact(system="", tools=[], messages=history(8), level="summarize")


def test_l2_refuses_when_only_one_round_is_old_enough_to_drop() -> None:
    """把唯一的现场变成摘要，模型就只剩自述可看了 —— 这种"压缩"是负收益。"""
    manager = cm(budget=60)
    report = manager.compact(
        system="", tools=[], messages=history(2), level="summarize", summarizer=SUMMARIZER
    )
    assert report.dropped_blocks == 0 and report.pairing_ok
    assert "没有可摘要的内容" in report.note


# --------------------------------------------------------------- 配对守门


def test_a_compaction_that_would_break_pairing_is_abandoned_and_returns_the_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B2 的反面证据：守门函数真的会拦，不是永远返回 True。

    这里故意把 `_split_for_summary` 换成"切在轮组中间"的错版实现 —— 那正是
    "半删必炸"的成因。压缩必须放弃、原历史必须原样交回。
    """
    manager = cm(budget=1000)
    msgs = history(6)
    monkeypatch.setattr(
        ContextManager,
        "_split_for_summary",
        lambda self, messages: (list(messages[:-2]), [messages[-2]], [messages[-1]]),
    )
    report = manager.compact(system="", tools=[], messages=msgs, level="summarize", summarizer=SUMMARIZER)

    assert report.pairing_ok is False
    assert "配对破损" in report.note
    assert report.messages == msgs                       # 原样交回，不是半坏的那份
    assert report.after_est == report.before_est
    assert manager.snapshot()["compactions"] == 0        # 放弃的那次不算成功


def test_compact_never_mutates_the_callers_messages() -> None:
    """放弃路径靠的就是这个：原历史没被就地改掉，才能原样交回。"""
    manager = cm(budget=1000)
    msgs = history(6)
    snapshot_before = [[b.content if isinstance(b, ToolResultBlock) else None for b in m.content] for m in msgs]
    manager.compact(system="", tools=[], messages=msgs, level="elide")
    manager.compact(system="", tools=[], messages=msgs, level="summarize", summarizer=SUMMARIZER)
    snapshot_after = [[b.content if isinstance(b, ToolResultBlock) else None for b in m.content] for m in msgs]
    assert snapshot_before == snapshot_after


# --------------------------------------------------------------- 阶梯顺序


def test_the_ladder_pays_for_the_free_tier_first_even_when_past_the_l2_line() -> None:
    """压力已经越过摘要线也先跑 L1：它零调用、零丢失风险，通常就够回到线下。"""
    manager = cm(budget=1000)
    msgs = history(10)
    assert manager.pressure(system="", tools=[], messages=msgs) > L2_SUMMARIZE_PRESSURE
    reports = manager.run_ladder(system="", tools=[], messages=msgs, summarizer=SUMMARIZER)
    assert levels(reports)[0] == "elide"
    assert reports[0].changed
    if len(reports) > 1:
        assert reports[1].before_est <= reports[0].after_est     # 第二档是在 L1 之后接着算的


def test_the_ladder_returns_one_row_per_attempted_tier() -> None:
    """报表要能回答"L2 到底试没试过"，所以每层都要留下一条记录。"""
    manager = cm(budget=2000)
    reports = manager.run_ladder(system="", tools=[], messages=history(8), summarizer=SUMMARIZER)
    assert reports
    assert levels(reports) == ["elide"] or levels(reports) == ["elide", "summarize"]
    assert all(r.level == levels(reports)[reports.index(r)] for r in reports)


def test_the_ladder_brings_pressure_back_under_the_l2_line() -> None:
    manager = cm(budget=1000)
    msgs = history(10)
    reports = manager.run_ladder(system="", tools=[], messages=msgs, summarizer=SUMMARIZER)
    final = reports[-1].messages if reports else msgs
    pressure = manager.pressure(system="", tools=[], messages=final)
    assert pressure < L2_SUMMARIZE_PRESSURE
    assert_pairing(final)


# --------------------------------------------------------------- 硬熔断


def test_the_hard_fuse_is_independent_of_the_ladder() -> None:
    """熔断看的是 `hard_limit`，不是预算：预算压到多小都不该改它的判定。"""
    tight = cm(budget=1000, hard_limit=200_000)
    assert not tight.over_hard_limit(179_999)
    assert tight.over_hard_limit(180_000)                # 0.9 × 200,000
    assert tight.pressure(system="", tools=[], messages=[Message.user_text("x" * 630_000)]) > 1.0
    # 压力已经爆表，但离窗口还远 —— 这时该压缩，不该熔断。
    assert not tight.over_hard_limit(30_000)


def test_snapshot_reports_both_knobs_so_a_bug_report_can_quote_them() -> None:
    manager = cm(budget=32_000, hard_limit=200_000)
    snapshot = manager.snapshot()
    assert snapshot["budget"] == 32_000 and snapshot["hard_limit"] == 200_000
    assert snapshot["enabled"] is True
