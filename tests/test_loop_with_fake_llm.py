"""核心循环测试 —— 用 FakeLLM 驱动，零 API 成本、完全确定性。

这几个用例是整个项目的安全网：改动 loop.py 后跑一遍就知道有没有把
"工具结果回填"这件事改坏。覆盖 SPEC §6.2 的表格行：
单工具往返、并行多调用、未知工具名、STALLED、MAX_TURNS、todo 不变式，
以及 self-debugging（跑测试→改→重跑）这条主线的确定性版本。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fakes import (
    FakeLLM,
    scripted_empty,
    scripted_final_text,
    scripted_tool_calls,
    scripted_truncated,
)
from miniclaude.agent.context import ContextManager
from miniclaude.agent.loop import MAX_OUTPUT_CHARS, STALL_LIMIT, Agent, EventKind, _cap
from miniclaude.agent.permissions import Answer, PermissionGate, PermissionMode
from miniclaude.agent.planner import TodoList, TodoStatus
from miniclaude.agent.state import TerminationReason
from miniclaude.agent.todo_tool import WriteTodosTool
from miniclaude.infra.trace import Tracer, summarize
from miniclaude.llm.openai_compat import LLMError
from miniclaude.messages import Role, Usage
from miniclaude.tools.registry import ToolRegistry
from miniclaude.tools.workspace import Workspace

SYSTEM = "你是测试用 Agent。"


def make_agent(
    responses: list[Any],
    workspace: Workspace,
    *,
    mode: PermissionMode = PermissionMode.AUTO,
    max_turns: int = 25,
    max_total_tokens: int = 10_000_000,
    context: ContextManager | None = None,
    confirmer: Any = None,
    on_event: Any = None,
    tracer: Any = None,
) -> Agent:
    """真工具 + 假模型。工具不假，回填出来的 observation 才是真的。"""
    todos = TodoList()
    registry = ToolRegistry.default(workspace, bash_timeout=30, extra_tools=[WriteTodosTool(workspace, todos)])
    gate = PermissionGate(workspace=workspace, mode=mode, confirmer=confirmer)
    return Agent(
        llm=FakeLLM(responses),
        registry=registry,
        gate=gate,
        system_prompt=SYSTEM,
        max_turns=max_turns,
        max_total_tokens=max_total_tokens,
        context=context or ContextManager(budget=200_000),
        todos=todos,
        on_event=on_event,
        tracer=tracer,
    )


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    (tmp_path / "hello.py").write_text("print('hi from hello')\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("第一行\n第二行\n", encoding="utf-8")
    return Workspace(tmp_path)


def sent_tool_results(fake: FakeLLM, call_index: int = 1) -> list[Any]:
    """取第 call_index 次请求里**最后一条**回填消息 —— 即紧邻那次请求之前的工具结果。

    一轮回填只发一条 user 消息，所以"最后一条"就是上一轮那批结果；
    这样多轮链路里也能按轮次取，不会把所有历史结果混在一起。
    """
    messages = [message for message in fake.calls[call_index]["messages"] if message.results]
    return messages[-1].results if messages else []


# --------------------------------------------------------------- 正常收尾


def test_stops_when_model_returns_text(ws: Workspace) -> None:
    agent = make_agent([scripted_final_text("这个文件只有一行打印。")], ws)
    result = agent.run("hello.py 里有什么？")

    assert agent.llm.call_count == 1
    assert result.termination is TerminationReason.COMPLETED
    assert result.succeeded
    assert result.text == "这个文件只有一行打印。"
    assert result.state.tool_calls == 0
    assert [m.role for m in agent.messages] == [Role.USER, Role.ASSISTANT]


def test_executes_tool_and_feeds_result_back(ws: Workspace) -> None:
    agent = make_agent(
        [
            scripted_tool_calls([("read_file", {"path": "hello.py"})]),
            scripted_final_text("它打印 hi from hello。"),
        ],
        ws,
    )
    result = agent.run("读 hello.py 并总结")

    assert agent.llm.call_count == 2
    assert result.termination is TerminationReason.COMPLETED
    assert result.state.tool_calls == 1
    assert result.state.tool_errors == 0

    blocks = sent_tool_results(agent.llm, 1)
    assert len(blocks) == 1
    assert blocks[0].tool_use_id == "call_0"      # 配对错一个字符，下一轮就 400
    assert blocks[0].is_error is False
    assert "hi from hello" in blocks[0].content


def test_parallel_tool_calls_are_all_executed_and_backfilled_in_order(ws: Workspace) -> None:
    """端点会一轮返回多个 tool_calls：漏执行或漏回填任何一个都会让下一请求 400。"""
    agent = make_agent(
        [
            scripted_tool_calls(
                [("read_file", {"path": "hello.py"}), ("read_file", {"path": "notes.txt"})],
                text="我同时读两个文件。",
            ),
            scripted_final_text("两个文件都读完了。"),
        ],
        ws,
    )
    result = agent.run("同时读 hello.py 和 notes.txt")

    blocks = sent_tool_results(agent.llm, 1)
    assert [b.tool_use_id for b in blocks] == ["call_0", "call_1"]  # 顺序 = 模型声明顺序
    assert "hi from hello" in blocks[0].content
    assert "第二行" in blocks[1].content
    assert result.state.tool_calls == 2
    assert agent.llm.last["messages"][-2].tool_uses[1].name == "read_file"


# --------------------------------------------------------------- 多轮链路


def test_multi_turn_tool_chain(ws: Workspace, tmp_path: Path) -> None:
    """读 → 写 → 搜 → 答复，历史必须逐轮累积而不是替换。"""
    agent = make_agent(
        [
            scripted_tool_calls([("read_file", {"path": "notes.txt"})], id_prefix="r"),
            scripted_tool_calls(
                [("write_file", {"path": "summary.md", "content": "# 摘要\n\n两行文本。\n"})],
                id_prefix="w",
            ),
            scripted_tool_calls([("search_text", {"pattern": "摘要"})], id_prefix="s"),
            scripted_final_text("已生成 summary.md，并确认内容可搜索到。"),
        ],
        ws,
    )
    result = agent.run("读 notes.txt，写一份 summary.md，再验证写入成功")

    assert agent.llm.call_count == 4
    assert result.termination is TerminationReason.COMPLETED
    assert (tmp_path / "summary.md").read_text(encoding="utf-8").startswith("# 摘要")

    # 每轮请求都比上一轮长：历史是累积的
    sizes = [len(call["messages"]) for call in agent.llm.calls]
    assert sizes == [1, 3, 5, 7]
    assert result.state.turn == 4
    assert result.state.tool_calls == 3


# --------------------------------------------------------------- 止损闸门


def test_max_turns_guard(ws: Workspace) -> None:
    """模型永远要求调工具时，应在 max_turns 停下而不是死循环。"""
    endless = [
        scripted_tool_calls([("read_file", {"path": "notes.txt", "offset": index})])
        for index in range(1, 8)
    ]
    agent = make_agent(endless, ws, max_turns=3)
    result = agent.run("一直读")

    assert result.termination is TerminationReason.MAX_TURNS
    assert not result.succeeded
    assert agent.llm.call_count == 3
    assert result.state.turn == 3
    assert "最大轮数" in result.text
    assert result.state.status == "aborted"


def test_token_ceiling_guard(ws: Workspace) -> None:
    heavy = Usage(prompt_tokens=50_000, completion_tokens=0)
    agent = make_agent(
        [
            scripted_tool_calls([("read_file", {"path": "notes.txt"})], usage=heavy),
            scripted_tool_calls([("read_file", {"path": "notes.txt", "offset": 2})], usage=heavy),
            scripted_final_text("不该走到这里"),
        ],
        ws,
        max_total_tokens=60_000,
    )
    events: list[EventKind] = []
    agent.on_event = lambda event: events.append(event.kind)
    result = agent.run("读")

    assert result.termination is TerminationReason.MAX_TURNS
    assert agent.llm.call_count == 2
    assert result.state.usage.total == 100_000
    assert EventKind.WARNING in events


def test_context_overflow_stops_before_calling_llm(ws: Workspace) -> None:
    agent = make_agent([scripted_final_text("不会被用到")], ws, context=ContextManager(budget=40))
    result = agent.run("读 notes.txt")

    assert result.termination is TerminationReason.CONTEXT_OVERFLOW
    assert agent.llm.call_count == 0


def test_stall_detection_stops_identical_repetition(ws: Workspace) -> None:
    same = [scripted_tool_calls([("read_file", {"path": "notes.txt"})])] * STALL_LIMIT
    agent = make_agent(same + [scripted_final_text("不该到这里")], ws)
    result = agent.run("读")

    assert result.termination is TerminationReason.STALLED
    assert agent.llm.call_count == STALL_LIMIT
    assert result.state.tool_calls == STALL_LIMIT - 1  # 第三轮识别出停滞，没再执行


def test_empty_replies_retried_then_stalled(ws: Workspace) -> None:
    agent = make_agent([scripted_empty()] * 3, ws)
    result = agent.run("随便做点什么")

    assert result.termination is TerminationReason.STALLED
    assert agent.llm.call_count == 3
    nudges = [m for m in agent.messages if m.role is Role.USER and "上一条回复是空的" in m.text()]
    assert len(nudges) == 2


def test_truncated_reply_gets_a_smaller_scope_nudge(ws: Workspace) -> None:
    agent = make_agent([scripted_truncated("def add(a, b" ), scripted_final_text("改用分段写入。")], ws)
    result = agent.run("写个大文件")

    assert result.termination is TerminationReason.COMPLETED
    assert agent.llm.call_count == 2
    nudges = [m for m in agent.messages if "截断" in m.text()]
    assert len(nudges) == 1
    assert "offset" in nudges[0].text()  # 提示要可操作，不是只说"太长了"


def test_llm_failure_returns_reason_not_exception(ws: Workspace) -> None:
    agent = make_agent([LLMError("端点返回 400：messages 顺序不对")], ws)
    result = agent.run("读文件")

    assert result.termination is TerminationReason.LLM_FAILURE
    assert not result.succeeded
    assert "模型调用失败" in result.text
    assert "400" in result.text  # 原始错误要能透出来，否则没法排查


# --------------------------------------------------------------- 失败转观察


def test_unknown_tool_becomes_error_result(ws: Workspace) -> None:
    agent = make_agent(
        [
            scripted_tool_calls([("delete_all_files", {"path": "."})]),
            scripted_final_text("抱歉，没有这个工具，我改用读文件确认内容。"),
        ],
        ws,
    )
    result = agent.run("删掉所有文件")

    block = sent_tool_results(agent.llm, 1)[0]
    assert block.is_error is True                       # 工具本身失败 → 计入 tool_error_rate
    assert "delete_all_files" in block.content
    assert "read_file" in block.content                 # 必须告诉模型有哪些真工具
    assert result.state.tool_errors == 1
    assert result.termination is TerminationReason.COMPLETED  # 报错不崩，继续跑


def test_malformed_tool_args_do_not_crash(ws: Workspace) -> None:
    agent = make_agent(
        [
            scripted_tool_calls([("read_file", {})]),          # 缺 required 的 path
            scripted_tool_calls([("read_file", {"path": "hello.py", "extra": 1})]),  # 多未知参数
            scripted_final_text("已按正确参数重试。"),
        ],
        ws,
    )
    result = agent.run("读")

    first, second = sent_tool_results(agent.llm, 1), sent_tool_results(agent.llm, 2)
    assert first[0].is_error is True
    assert "path" in first[0].content
    assert second[0].is_error is False  # 未知参数被丢弃而不是浪费一轮
    assert result.termination is TerminationReason.COMPLETED


def test_path_escape_is_denied_before_execution(ws: Workspace, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("秘密", encoding="utf-8")
    agent = make_agent(
        [
            scripted_tool_calls([("read_file", {"path": f"../{outside.name}"})]),
            scripted_final_text("工作区外的文件读不到。"),
        ],
        ws,
    )
    result = agent.run("读工作区外的文件")

    block = sent_tool_results(agent.llm, 1)[0]
    assert block.is_error is True
    assert "超出工作区" in block.content
    assert result.state.denied_actions == 1
    assert result.termination is TerminationReason.COMPLETED


def test_readonly_mode_blocks_writes_and_says_why(ws: Workspace, tmp_path: Path) -> None:
    agent = make_agent(
        [
            scripted_tool_calls([("write_file", {"path": "x.py", "content": "x=1\n"})]),
            scripted_final_text("只读模式下我不能写文件。"),
        ],
        ws,
        mode=PermissionMode.READONLY,
    )
    result = agent.run("写个 x.py")

    assert not (tmp_path / "x.py").exists()
    block = sent_tool_results(agent.llm, 1)[0]
    assert block.is_error is True
    assert "只读模式" in block.content
    assert result.state.denied_actions == 1


def test_repeated_denial_stops_the_run(ws: Workspace) -> None:
    agent = make_agent(
        [
            scripted_tool_calls([("write_file", {"path": "a.py", "content": "a=1\n"})]),
            scripted_tool_calls([("write_file", {"path": "b.py", "content": "b=1\n"})]),
            scripted_final_text("不该继续"),
        ],
        ws,
        mode=PermissionMode.READONLY,
    )
    result = agent.run("写两个文件")

    assert result.termination is TerminationReason.USER_REJECTED
    assert agent.llm.call_count == 2
    assert "拒绝" in result.text


def test_ask_mode_without_confirmer_fails_closed(ws: Workspace, tmp_path: Path) -> None:
    """ASK 模式却拿不到人的回答时，宁可拒绝也不能擅自写盘。"""
    agent = make_agent(
        [
            scripted_tool_calls([("write_file", {"path": "y.py", "content": "y=1\n"})]),
            scripted_final_text("没有确认渠道，我停下来问你。"),
        ],
        ws,
        mode=PermissionMode.ASK,
    )
    result = agent.run("写 y.py")

    assert not (tmp_path / "y.py").exists()
    block = sent_tool_results(agent.llm, 1)[0]
    assert block.is_error is True
    assert "确认渠道" in block.content
    assert result.state.denied_actions == 1


def test_always_grant_scopes_to_command_prefix(ws: Workspace) -> None:
    """用户点"始终允许"只应放行同类命令前缀，不能变成放行整个 bash。"""
    asked: list[str] = []

    def confirmer(tool_name: str, summary: str) -> Answer:
        asked.append(summary)
        return Answer.ALWAYS

    agent = make_agent(
        [
            scripted_tool_calls([("bash", {"command": "echo one"})], id_prefix="a"),
            scripted_tool_calls([("bash", {"command": "echo two"})], id_prefix="b"),
            scripted_tool_calls([("bash", {"command": "ls"})], id_prefix="c"),
            scripted_final_text("完成"),
        ],
        ws,
        mode=PermissionMode.ASK,
        confirmer=confirmer,
    )
    result = agent.run("跑几条命令")

    assert result.termination is TerminationReason.COMPLETED
    assert asked == ["$ echo one", "$ ls"]           # 第二次 echo 命中前缀规则，没再问
    assert agent.gate.grants == ["bash:echo", "bash:ls"]  # 授权按前缀累加，不是整工具一次通开
    assert [rule.pattern for rule in agent.gate.rules()] == ["echo", "ls"]


# --------------------------------------------------------------- 计划可见


def test_write_todos_updates_plan_and_reinjects_it(ws: Workspace) -> None:
    agent = make_agent(
        [
            scripted_tool_calls(
                [
                    (
                        "write_todos",
                        {
                            "todos": [
                                {"content": "读 README", "status": "in_progress"},
                                {"content": "补测试", "status": "pending"},
                            ]
                        },
                    )
                ],
                id_prefix="t",
            ),
            scripted_final_text("计划已建立。"),
        ],
        ws,
    )
    result = agent.run("分两步做")

    assert [item.status for item in agent.todos.items] == [TodoStatus.IN_PROGRESS, TodoStatus.PENDING]
    assert result.todos[0]["content"] == "读 README"

    second_system = agent.llm.calls[1]["system"]
    assert "# 当前任务清单" in second_system          # 计划不回注进上下文，模型下一轮就忘了
    assert "- [~] 读 README" in second_system

    backfilled = sent_tool_results(agent.llm, 1)[0]
    assert backfilled.is_error is False
    assert "读 README" in backfilled.content


def test_todo_invariant_only_one_in_progress(ws: Workspace) -> None:
    agent = make_agent(
        [
            scripted_tool_calls(
                [
                    (
                        "write_todos",
                        {
                            "todos": [
                                {"content": "甲", "status": "in_progress"},
                                {"content": "乙", "status": "in_progress"},
                                {"content": "丙", "status": "in progress"},
                            ]
                        },
                    )
                ],
                id_prefix="i",
            ),
            scripted_final_text("收到"),
        ],
        ws,
    )
    agent.run("三步同时做")

    statuses = [item.status for item in agent.todos.items]
    assert statuses.count(TodoStatus.IN_PROGRESS) == 1
    assert statuses.count(TodoStatus.PENDING) == 2
    notes = sent_tool_results(agent.llm, 1)[0].content
    assert "同一时刻只能有一步在进行中" in notes       # 被静默修正之处必须告诉模型


# --------------------------------------------------------------- self-debugging


@pytest.fixture
def buggy_repo(tmp_path: Path) -> Workspace:
    """一个真实会失败的仓库：add() 写成了减法，测试断言加法。"""
    (tmp_path / "calc.py").write_text(
        '"""计算器。"""\n\n\ndef add(a, b):\n    return a - b\n', encoding="utf-8"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    return Workspace(tmp_path)


def test_self_debugging_fixes_until_tests_pass(buggy_repo: Workspace, tmp_path: Path) -> None:
    """观察失败 → 分析 → 修改 → 重跑，这条闭环必须在测试里真的跑通。"""
    agent = make_agent(
        [
            scripted_tool_calls([("run_tests", {"target": "tests"})], id_prefix="u"),
            scripted_tool_calls([("read_file", {"path": "calc.py"})], id_prefix="r"),
            scripted_tool_calls(
                [("edit_file", {"path": "calc.py", "old_string": "    return a - b", "new_string": "    return a + b"})],
                id_prefix="e",
            ),
            scripted_tool_calls([("run_tests", {"target": "tests"})], id_prefix="v"),
            scripted_final_text("add 误写成减法，已修正，测试全绿。"),
        ],
        buggy_repo,
    )
    events: list[EventKind] = []
    agent.on_event = lambda event: events.append(event.kind)
    result = agent.run("修好这个仓库的测试")

    assert result.termination is TerminationReason.COMPLETED
    assert "return a + b" in (tmp_path / "calc.py").read_text(encoding="utf-8")

    first_run = sent_tool_results(agent.llm, 1)[0]
    second_run = sent_tool_results(agent.llm, 4)[0]
    assert "FAILED" in first_run.content
    assert "PASSED" in second_run.content
    # 关键语义：测试红了不是工具失败，所以第二次不增加 tool_errors
    assert first_run.is_error is False
    assert second_run.is_error is False
    assert result.state.tool_errors == 0
    assert result.state.tool_calls == 4
    assert events[-1] is EventKind.FINISHED


# --------------------------------------------------------------- 可观测性


def test_events_cover_the_full_round_trip(ws: Workspace) -> None:
    kinds: list[tuple[EventKind, dict[str, Any]]] = []
    agent = make_agent(
        [
            scripted_tool_calls([("read_file", {"path": "notes.txt"})], text="我先读文件。"),
            scripted_final_text("读完了。"),
        ],
        ws,
        on_event=lambda event: kinds.append((event.kind, event.payload)),
    )
    agent.run("读")

    seen = [kind for kind, _ in kinds]
    assert seen == [
        EventKind.TURN_START,
        EventKind.ASSISTANT_TEXT,
        EventKind.PERMISSION,
        EventKind.TOOL_START,
        EventKind.TOOL_END,
        EventKind.TURN_START,
        EventKind.ASSISTANT_TEXT,
        EventKind.FINISHED,
    ]
    tool_end = dict(kinds[4][1])
    assert tool_end["ok"] is True and tool_end["tool"] == "read_file"
    assert tool_end["elapsed"] >= 0
    assert kinds[3][1]["args"] == {"path": "notes.txt"}


def test_trace_records_the_round_trip(ws: Workspace, tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    agent = make_agent(
        [
            scripted_tool_calls([("read_file", {"path": "notes.txt"})]),
            scripted_final_text("好了"),
        ],
        ws,
        tracer=Tracer(trace_path, session_id="test-session"),
    )
    result = agent.run("读")

    assert result.trace_path == trace_path
    summary = summarize(trace_path)
    assert summary["turns"] == 2
    assert summary["tool_calls"] == 1
    assert summary["tool_errors"] == 0
    assert summary["termination"] == "completed"
    assert summary["tool_sequence"] == ["read_file"]
    assert summary["session"] == "test-session"


def test_trace_never_records_tool_output_verbatim(ws: Workspace) -> None:
    """trace 只记摘要 —— 否则日志会把整仓代码抄一遍，还会带走密钥。"""
    trace_path = ws.root / "trace2.jsonl"
    agent = make_agent(
        [
            scripted_tool_calls([("read_file", {"path": "hello.py"})]),
            scripted_final_text("好了"),
        ],
        ws,
        tracer=Tracer(trace_path, session_id="s2"),
    )
    agent.run("读")

    raw = trace_path.read_text(encoding="utf-8")
    assert "hi from hello" not in raw
    assert '"name": "read_file"' in raw
    assert json.loads(raw.splitlines()[-1])["kind"] == "run_end"


def test_cap_keeps_head_and_tail() -> None:
    """截断必须保尾：pytest 的失败摘要在末尾，只留开头等于什么都没留。"""
    text = "A" * 1000 + "M" * 2000 + "B" * 1000
    capped = _cap(text, MAX_OUTPUT_CHARS)
    assert capped == text  # 未超限原样返回

    small = _cap(text, 400)
    assert small.startswith("A" * 100)
    assert small.endswith("B" * 100)
    assert "省略" in small
    assert "M" not in small
