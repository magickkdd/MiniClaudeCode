"""配对不变式测试 —— 这是压缩、resume、子 agent 回填三条路径共用的地基。

为什么值得单独一个文件：这三条路径都会**删改历史消息**，而端点对
`tool_use` / `tool_result` 配对的要求是硬性的 —— 半删必炸（400）。
v1 的"漏回填一个就 400"和 v2 的"压缩删错一组也 400"是同一条物理规律的两个方向，
所以判定函数只准有一份，这里的用例就是把那份函数钉死在规律上。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_loop_with_fake_llm import make_agent
from fakes import scripted_final_text, scripted_tool_calls

from miniclaude.messages import (
    Message,
    PairingError,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    assert_pairing,
    pairing_problems,
)
from miniclaude.tools.workspace import Workspace


def call(tool_use_id: str, name: str = "read_file") -> ToolUseBlock:
    return ToolUseBlock(id=tool_use_id, name=name, input={"path": "a.py"})


def result(tool_use_id: str, content: str = "1: print(1)") -> ToolResultBlock:
    return ToolResultBlock(tool_use_id=tool_use_id, content=content)


def round_trip(*ids: str) -> list[Message]:
    """一个完整"轮组"：assistant 发 N 个调用，紧接一条 user 消息回填 N 个结果。"""
    return [
        Message.user_text("看一下"),
        Message.assistant([TextBlock("我读一下"), *[call(i) for i in ids]]),
        Message.tool_results([result(i) for i in ids]),
    ]


def check(messages: list[Message]) -> list[str]:
    """跑一遍判定；不合法时把违规列表交回来，合法时返回空列表。"""
    return pairing_problems(messages)


# --------------------------------------------------------------- 合法形态


def test_a_single_tool_round_is_legal() -> None:
    assert check(round_trip("c1")) == []


def test_a_parallel_round_pairs_every_id_in_one_result_message() -> None:
    """端点真实行为：一轮可以并行返回多个 tool_calls（SPEC v1 §2.2 实测）。

    这条是并行调用的"存在性证明" —— 判定若要求一调用一消息，会把合法报文判成违法。
    """
    assert check(round_trip("c1", "c2", "c3")) == []


def test_two_consecutive_rounds_are_legal() -> None:
    messages = round_trip("c1") + [
        Message.assistant([call("c2", "bash")]),
        Message.tool_results([result("c2", "ok")]),
    ]
    assert check(messages) == []


def test_text_only_history_is_legal_including_empty() -> None:
    assert check([]) == []
    assert check([Message.user_text("你好"), Message.assistant([TextBlock("在的")])]) == []


def test_error_results_are_still_valid_answers() -> None:
    """失败的工具结果同样是回填；判成"未回填"会让 agent 一报错就被压缩删掉。"""
    messages = [
        Message.assistant([call("c1")]),
        Message(Role.USER, [ToolResultBlock(tool_use_id="c1", content="boom", is_error=True)]),
    ]
    assert check(messages) == []


def test_assert_pairing_returns_nothing_when_legal() -> None:
    assert assert_pairing(round_trip("c1")) is None


# --------------------------------------------------------------- 规则 1：必须有且仅有一次回填


def test_unanswered_call_is_reported_with_its_tool_name() -> None:
    problems = check([Message.assistant([call("c1", "run_tests")])])
    assert len(problems) == 1
    assert "run_tests" in problems[0] and "c1" in problems[0] and "从未回填" in problems[0]


def test_a_partially_answered_parallel_round_is_still_a_violation() -> None:
    """三个调用只回填两个 —— 少掉的那个就是 400 的成因，必须点名。"""
    messages = [
        Message.assistant([call("c1"), call("c2"), call("c3")]),
        Message.tool_results([result("c1"), result("c3")]),
    ]
    problems = check(messages)
    assert len(problems) == 1 and "c2" in problems[0]


def test_answering_the_same_call_twice_is_reported() -> None:
    messages = [
        Message.assistant([call("c1")]),
        Message.tool_results([result("c1")]),
        Message.tool_results([result("c1", "again")]),
    ]
    problems = check(messages)
    assert len(problems) == 1 and "回填了两次" in problems[0]


def test_a_result_that_precedes_its_call_is_reported_as_an_order_error() -> None:
    """顺序颠倒 = 模型看到"结果先于请求"，resume 拼快照时最容易写错的一处。

    单趟扫描会把它误判成"这条调用压根不存在" —— 两种病根的修法完全不同，
    所以判定扫两趟：先登记全部调用，再看每条结果的顺序。
    """
    messages = [Message.tool_results([result("c1")]), Message.assistant([call("c1")])]
    problems = check(messages)
    assert problems == ["#0 结果 c1 出现在它的调用之前"]
    # 调用后来是有人声明的，所以不该连带报"从未回填"，也不该报"找不到对应"。
    assert not any("从未回填" in p or "找不到对应" in p for p in problems)


def test_declaring_the_same_id_twice_is_reported() -> None:
    messages = [Message.assistant([call("c1")]), Message.assistant([call("c1")]), Message.tool_results([result("c1")])]
    problems = check(messages)
    assert len(problems) == 1 and "重复声明" in problems[0]


# --------------------------------------------------------------- 规则 2：结果必须可回溯


def test_an_orphan_result_is_reported() -> None:
    problems = check([Message.user_text("读一下"), Message.tool_results([result("nope")])])
    assert len(problems) == 1 and "找不到对应的工具调用" in problems[0] and "nope" in problems[0]


# --------------------------------------------------------------- 规则 3：块归属


def test_mixing_text_into_a_result_message_is_reported() -> None:
    """v1 的纪律是"一批结果作为一条 user 消息回填"（messages.py:69）。

    往结果消息里塞文本会让模型把叙述当成工具产出，这是 self_confirm 的温床。
    """
    problems = check([Message.assistant([call("c1")]), Message(Role.USER, [TextBlock("顺便说一句"), result("c1")])])
    assert len(problems) == 1 and "混在同一条消息" in problems[0]


def test_an_empty_text_block_alongside_results_is_reported_too() -> None:
    """按块类型判，不按 `text().strip()` 判：适配器偶尔会回填一个空 TextBlock。"""
    problems = check([Message.assistant([call("c1")]), Message(Role.USER, [TextBlock("  "), result("c1")])])
    assert problems and "混在同一条消息" in problems[0]


def test_a_call_inside_a_user_message_is_reported() -> None:
    problems = check([Message(Role.USER, [call("c1")])])
    assert "user 消息里出现了工具调用请求" in problems[0]
    # 它同时也是一条没人回填的调用；归属那条排在前，因为它才是病根。
    assert len(problems) == 2 and "从未回填" in problems[1]


def test_an_illegally_placed_call_that_gets_an_answer_reports_only_the_placement() -> None:
    """登记表连角色不合法的调用也收：否则一次归属错误连带刷出一屏"从未回填"，
    真问题就被埋在噪声里了。"""
    messages = [Message(Role.USER, [call("c1")]), Message.tool_results([result("c1")])]
    assert check(messages) == ["#0 user 消息里出现了工具调用请求"]


def test_a_result_inside_an_assistant_message_is_reported() -> None:
    problems = check([Message.assistant([call("c1")]), Message(Role.ASSISTANT, [result("c1")])])
    kinds = "".join(problems)
    assert "assistant 消息里回填了工具结果" in kinds
    # 归属违规不该同时报"未回填"—— 那个 c1 已经有人应答了，重复报会让人找错地方。
    assert "从未回填" not in kinds


# --------------------------------------------------------------- 报错形态


def test_assert_pairing_raises_and_counts_every_problem() -> None:
    messages = [
        Message.tool_results([result("ghost")]),
        Message.assistant([call("c1"), call("c1")]),
        Message(Role.ASSISTANT, [result("c1")]),
    ]
    problems = check(messages)
    assert len(problems) > 1
    with pytest.raises(PairingError) as raised:
        assert_pairing(messages)
    text = str(raised.value)
    assert "消息配对不合法" in text
    assert problems[0] in text                          # 印的是第一条，不是随便一条
    if len(problems) > 6:
        assert f"共 {len(problems)} 处" in text


def test_problems_are_ordered_by_message_index_not_discovery_order() -> None:
    """两趟扫描的发现顺序与消息顺序相反，排序才不会被读成"病根在后面那条"。"""
    messages = [
        Message.tool_results([result("ghost")]),
        Message.assistant([call("c1")]),
        Message.assistant([call("c1")]),
    ]
    problems = check(messages)
    # 发现顺序本来是 #2 → #0 → #1（重复声明在第一趟，另两条在第二趟）。
    assert [p.split(" ")[0] for p in problems] == ["#0", "#1", "#2"]


def test_a_truncated_report_still_names_the_problem_count() -> None:
    """八处违规只印前六条，但必须说"共 8 处"，否则读者以为已经看全了。"""
    messages = [Message.assistant([call(f"c{i}")]) for i in range(8)]
    assert len(check(messages)) == 8
    with pytest.raises(PairingError, match="共 8 处"):
        assert_pairing(messages)


def test_the_checker_never_mutates_the_messages() -> None:
    """resume 路径要先检查再决定用不用这批消息 —— 检查器改了就等于毁了现场。"""
    messages = round_trip("c1", "c2")
    before = [(m.role, [type(b).__name__ for b in m.content]) for m in messages]
    check(messages)
    assert [(m.role, [type(b).__name__ for b in m.content]) for m in messages] == before


# --------------------------------------------------------------- 与真实循环对齐


def test_the_real_loop_produces_pairable_history(tmp_path: Path) -> None:
    """判定与生产者必须对得上：真循环跑出来的历史过不了这道闸，就是判定写错了。

    这条不测压缩 —— 它测的是"我们现在合法"这个基线，
    后面 compact() 的每次改动都要仍然让它成立。
    """
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    agent = make_agent(
        [
            scripted_tool_calls([("read_file", {"path": "hello.py"}), ("find_files", {"pattern": "**/*.py"})]),
            scripted_tool_calls([("read_file", {"path": "hello.py"})], id_prefix="second"),
            scripted_final_text("就一行打印。"),
        ],  # type: ignore[arg-type]
        Workspace(tmp_path),
    )
    agent.run("看看 hello.py")
    assert len(agent.messages) >= 6
    assert_pairing(agent.messages)


def test_a_script_that_reuses_ids_still_leaves_pairable_history(tmp_path: Path) -> None:
    """端点按响应内序号发 id（`call_0`、`call_1`）时，跨轮必然撞车。

    这条是 B2 的地基：撞车的历史会让 `_guard_pairing` 认为**原始**历史就非法，
    于是每一次压缩都被放弃 —— 阶梯整体变成空转，而表面上只是"这道题没过"。
    三轮读三个不同文件：读同一个文件三次会先被停滞检测拦下，那时最后一条调用
    本来就不该有结果（循环已经终止），测不到这里想测的东西。
    """
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text(f"print('{name}')\n", encoding="utf-8")
    agent = make_agent(
        [
            scripted_tool_calls([("read_file", {"path": "a.py"})]),
            scripted_tool_calls([("read_file", {"path": "b.py"})]),
            scripted_tool_calls([("read_file", {"path": "c.py"})]),
            scripted_final_text("三个文件都读过了。"),
        ],  # type: ignore[arg-type]
        Workspace(tmp_path),
    )
    agent.run("把三个文件各读一遍")
    ids = [block.id for m in agent.messages for block in m.tool_uses]
    assert len(ids) == 3 and len(set(ids)) == 3, ids
    assert_pairing(agent.messages)


# ------------------------------------------------------- 撞车 id 的就地修正


def test_dedupe_renames_the_later_use_deterministically() -> None:
    """改名要能复现：eval 基线与 trace 快照比的是字节，uuid 每次都不一样。"""
    from miniclaude.messages import dedupe_tool_use_ids

    history = round_trip("call_0")
    incoming = Message.assistant([call("call_0"), call("call_1")])
    renamed = dedupe_tool_use_ids(incoming, history)
    assert renamed == [("call_0", "call_0~0")]
    assert [b.id for b in incoming.tool_uses] == ["call_0~0", "call_1"]


def test_dedupe_fixes_duplicates_inside_one_response() -> None:
    from miniclaude.messages import dedupe_tool_use_ids

    incoming = Message.assistant([call("x"), call("x")])
    assert dedupe_tool_use_ids(incoming, []) == [("x", "x~1")]
    assert [b.id for b in incoming.tool_uses] == ["x", "x~1"]


def test_dedupe_leaves_a_clean_message_and_its_blocks_untouched() -> None:
    """不改是常态：脚本条目会被多个 run 复用，改到块对象上就是污染下一场。"""
    from miniclaude.messages import dedupe_tool_use_ids

    block = call("c1")
    incoming = Message.assistant([block])
    assert dedupe_tool_use_ids(incoming, round_trip("other")) == []
    assert incoming.content == [block] and block.id == "c1"

    collide = Message.assistant([block])
    dedupe_tool_use_ids(collide, round_trip("c1"))
    assert block.id == "c1", "原块必须原样 —— 修的是这条消息的列表，不是共享对象"
    assert collide.content[0] is not block
