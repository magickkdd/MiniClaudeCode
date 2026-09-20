"""CLI 测试 —— 装配对不对，只有跑一遍才知道。

这里最值钱的是 `test_build_session_wires_everything`：它证明
"CLI 构造出来的那个 Agent"和"测试里那个 Agent"是同一套依赖，
而不是 demo 能跑、用户一跑就炸。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeLLM, scripted_final_text, scripted_tool_calls
from miniclaude.agent.permissions import Answer, PermissionMode
from miniclaude.agent.state import TerminationReason
from miniclaude.cli import main as cli
from miniclaude.cli.main import RequestedExit, build_session, handle_command, one_line, repl, run_turn
from miniclaude.cli.render import Renderer, _label
from miniclaude.config import Config
from miniclaude.llm.openai_compat import LLMError


class Recorder:
    """把 Renderer 的输出接成可断言的列表。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, text: str) -> None:
        self.lines.append(text)

    def text(self) -> str:
        return "\n".join(self.lines)


def make_config(root: Path, **overrides: Any) -> Config:
    base = {
        "base_url": "https://mock.local/v1",
        "api_key": "sk-test-abcdefghijklmn",
        "model": "mock-model",
        "project_root": root,
        "trace_path": root / ".traces" / "session.jsonl",
        "bash_timeout": 20,
    }
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def session_factory(tmp_path: Path):
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")

    def build(responses: list[Any], **kwargs: Any):
        recorder = Recorder()
        renderer = Renderer(write=recorder, use_rich=False)
        session = build_session(
            config=make_config(tmp_path),
            renderer=renderer,
            llm=FakeLLM(responses),
            mode=kwargs.pop("mode", PermissionMode.AUTO),
            **kwargs,
        )
        return session, recorder

    return build


# --------------------------------------------------------------- 装配


def test_build_session_wires_everything(session_factory: Any, tmp_path: Path) -> None:
    session, recorder = session_factory([scripted_final_text("好的")])
    agent = session.agent

    assert "write_todos" in agent.registry  # 8 个 MVP 工具全在
    assert len(agent.registry) == 8
    assert agent.gate.mode is PermissionMode.AUTO
    assert session.tracer.path == tmp_path / ".traces" / "session.jsonl"
    assert session.tracer.path.exists()

    # 环境事实与仓库地图进了 system —— 这是 Windows 上能跑对命令的前提
    system = agent.current_system()
    assert "Windows" in system or "python3" in system
    assert "hello.py" in system
    assert "mock-model" in system
    assert "write_todos" in system


def test_config_never_leaks_into_trace_or_prompt(session_factory: Any) -> None:
    session, _ = session_factory([scripted_final_text("好的")])
    assert "sk-test-abcdefghijklmn" not in session.tracer.path.read_text(encoding="utf-8")
    assert "sk-test-abcdefghijklmn" not in session.agent.current_system()


def test_llm_round_trip_through_cli_assembly(session_factory: Any, tmp_path: Path) -> None:
    session, recorder = session_factory(
        [
            scripted_tool_calls([("read_file", {"path": "hello.py"})]),
            scripted_final_text("文件里只有一行打印。"),
        ]
    )
    result = run_turn(session, "读 hello.py")

    assert result.succeeded
    assert "read_file" in recorder.text()
    assert "文件里只有一行打印。" in recorder.text()
    assert session.agent.llm.call_count == 2


# --------------------------------------------------------------- 中断与崩溃


class ExplodingLLM:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.called = 0

    def create(self, *, system: str, messages: list[Any], tools: list[Any]):
        self.called += 1
        raise self.error


def test_ctrl_c_mid_request_gives_a_cancelled_result(session_factory: Any) -> None:
    session, _ = session_factory([])
    session.agent.llm = ExplodingLLM(KeyboardInterrupt())  # type: ignore[assignment]
    result = run_turn(session, "做点什么")

    assert result.termination is TerminationReason.CANCELLED
    assert "已取消" in result.text
    # 输入没被丢掉：紧接着说"继续"，模型还能看见原始任务
    assert [message.text() for message in session.agent.messages] == ["做点什么"]


def test_unexpected_bug_does_not_kill_the_repl(session_factory: Any) -> None:
    session, recorder = session_factory([])
    session.agent.llm = ExplodingLLM(RuntimeError("我自己写崩了"))  # type: ignore[assignment]
    result = run_turn(session, "做点什么")

    assert result.termination is TerminationReason.INTERNAL_ERROR
    assert "RuntimeError" in result.text
    assert session.history and session.history[-1] is result


# --------------------------------------------------------------- 斜杠命令


def test_exit_command_raises(session_factory: Any) -> None:
    session, _ = session_factory([])
    with pytest.raises(RequestedExit):
        handle_command(session, "/exit")


def test_help_lists_every_command(session_factory: Any) -> None:
    session, _ = session_factory([])
    text = handle_command(session, "/help")
    for name in ("/reset", "/tools", "/context", "/todos", "/mode", "/trace", "/exit"):
        assert name in text, name


def test_tools_command_covers_all_registered_tools(session_factory: Any) -> None:
    session, _ = session_factory([])
    listing = handle_command(session, "/tools")
    for name in session.agent.registry.names():
        assert name in listing
    assert "read" in listing and "execute" in listing


def test_context_command_reports_real_numbers(session_factory: Any) -> None:
    session, _ = session_factory([scripted_final_text("好")])
    run_turn(session, "读点什么")
    text = handle_command(session, "/context")

    assert "tokens" in text
    assert "消息 2 条" in text
    assert "轮数 1/25" in text


def test_mode_command_switches_gate_and_validates_input(session_factory: Any) -> None:
    session, _ = session_factory([])
    assert handle_command(session, "/mode readonly").startswith("权限模式已切到 readonly")
    assert session.agent.gate.mode is PermissionMode.READONLY
    assert "可切换" in handle_command(session, "/mode nope")
    assert "readonly" in handle_command(session, "/mode")


def test_todos_and_trace_and_reset(session_factory: Any) -> None:
    session, _ = session_factory(
        [
            scripted_tool_calls([("write_todos", {"todos": [{"content": "甲", "status": "pending"}]})]),
            scripted_final_text("建好了"),
        ]
    )
    run_turn(session, "列个计划")
    assert "甲" in handle_command(session, "/todos")
    assert ".traces" in handle_command(session, "/trace")

    handle_command(session, "/reset")
    assert session.agent.messages == []
    assert session.agent.todos.is_empty
    assert "没有任务清单" in handle_command(session, "/todos")


def test_unknown_command_is_tolerated(session_factory: Any) -> None:
    session, _ = session_factory([])
    assert "不认识的命令" in handle_command(session, "/frobnicate")


def test_no_trace_config_says_so(tmp_path: Path) -> None:
    renderer = Renderer(write=Recorder(), use_rich=False)
    session = build_session(
        config=make_config(tmp_path, trace_path=None), renderer=renderer, llm=FakeLLM([])
    )
    assert "没有写日志" in handle_command(session, "/trace")


# --------------------------------------------------------------- REPL 分发


def make_scripted_read(lines: list[str]) -> Any:
    queue = list(lines)

    def read(_prompt: str) -> str:
        if not queue:
            raise EOFError
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return read


def test_repl_handles_commands_and_tasks_then_exits(session_factory: Any) -> None:
    session, recorder = session_factory(
        [scripted_final_text("答复一"), scripted_final_text("答复二")]
    )
    code = repl(session, read=make_scripted_read(["第一个任务", "", "/tools", "第二个任务", "/exit"]))

    assert code == 0
    assert "答复一" in recorder.text() and "答复二" in recorder.text()
    assert "read_file" in recorder.text()  # /tools 的输出


def test_repl_exits_on_eof(session_factory: Any) -> None:
    session, _ = session_factory([])
    assert repl(session, read=make_scripted_read([])) == 0


def test_repl_survives_stray_keyboard_interrupt(session_factory: Any) -> None:
    session, recorder = session_factory([scripted_final_text("还活着")])
    assert repl(session, read=make_scripted_read([KeyboardInterrupt(), "做点什么"])) == 0
    assert "还活着" in recorder.text()


# --------------------------------------------------------------- 渲染


def make_renderer() -> tuple[Renderer, Recorder]:
    recorder = Recorder()
    return Renderer(write=recorder, use_rich=False), recorder


def test_one_tool_call_is_one_line() -> None:
    renderer, recorder = make_renderer()
    renderer.tool_line("read_file", {"path": "src/app.py"}, ok=True, elapsed=0.012)
    renderer.tool_line("bash", {"command": "pytest -q"}, ok=False, elapsed=1.84)

    lines = recorder.lines
    assert len(lines) == 2
    assert lines[0].startswith("✓ read_file") and "src/app.py" in lines[0] and "12ms" in lines[0]
    assert lines[1].startswith("✗ bash") and "pytest -q" in lines[1] and "1.8s" in lines[1]


def test_label_picks_the_identifying_argument() -> None:
    assert _label({"path": "a/b.py"}) == "a/b.py"
    assert _label({"command": "python -m pytest -q"}) == "python -m pytest -q"
    assert _label({"pattern": "def parse_"}) == "def parse_"
    assert _label({"target": ""}) == ""
    assert _label({"todos": [{"content": "x"}, {"content": "y"}]}) == "2 步"
    assert _label({}) == ""
    assert _label({"path": "C:\\src\\app.py"}) == "C:/src/app.py"  # 反斜杠在终端里读起来像转义


def test_label_is_clipped_to_one_screen() -> None:
    label = _label({"command": "python -c " + '"x" ' * 80})
    assert len(label) <= 68 and label.endswith("…")


def test_denied_action_is_reported_allowed_is_silent() -> None:
    from miniclaude.agent.loop import AgentEvent, EventKind

    renderer, recorder = make_renderer()
    renderer.handle(AgentEvent(EventKind.PERMISSION, {"tool": "bash", "allowed": True, "reason": "自动放行"}))
    assert recorder.lines == []
    renderer.handle(
        AgentEvent(EventKind.PERMISSION, {"tool": "write_file", "allowed": False, "reason": "超出工作区"})
    )
    assert "已拦下 write_file" in recorder.text() and "超出工作区" in recorder.text()


def test_final_text_does_not_repeat_what_was_already_printed() -> None:
    renderer, recorder = make_renderer()
    renderer.assistant_text("任务完成，改了 2 个文件。")
    renderer.final_text("任务完成，改了 2 个文件。")
    assert recorder.lines == ["任务完成，改了 2 个文件。"]

    renderer.final_text("已达到最大轮数（25），任务未完成。")  # 合成解释必须打出来
    assert "最大轮数" in recorder.text()


def test_banner_shows_capability_boundary() -> None:
    renderer, recorder = make_renderer()
    renderer.banner(model="m", project_root=Path("D:/repo"), tools=["read_file", "bash"], mode="auto")
    text = recorder.text()
    assert "m" in text and "read_file" in text and "auto" in text and "2 个" in text


def test_verbose_mode_shows_round_separators_and_previews() -> None:
    from miniclaude.agent.loop import AgentEvent, EventKind

    renderer, recorder = make_renderer()
    quiet, loud = Renderer(write=Recorder(), use_rich=False), Renderer(verbose=True, write=recorder, use_rich=False)
    quiet.handle(AgentEvent(EventKind.TURN_START, {"turn": 3}))
    assert recorder.lines == []
    loud.handle(AgentEvent(EventKind.TURN_START, {"turn": 3}))
    assert "第 3 轮" in recorder.text()
    loud.handle(AgentEvent(EventKind.TOOL_END, {"tool": "read_file", "ok": True, "elapsed": 0.1, "output": "1 行\n2 行"}))
    assert "1 行" in recorder.text()


def test_one_line_helper_truncates_description() -> None:
    assert one_line("  多   行\n说明  ") == "多 行 说明"
    assert one_line("x" * 100).endswith("…")


# --------------------------------------------------------------- 入口


def test_main_rejects_bad_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> Config:
        raise cli.ConfigError("缺少环境变量 LLM_API_KEY")

    monkeypatch.setattr(cli, "get_config", boom)
    assert cli.main([]) == 2


def test_main_one_shot_maps_termination_to_exit_code(
    monkeypatch: pytest.MonkeyPatch, session_factory: Any, tmp_path: Path
) -> None:
    """退出码是 demo 脚本唯一的判据，必须与"任务是否真做完"绑定。"""
    monkeypatch.setattr(cli, "get_config", lambda *a, **k: make_config(tmp_path))

    done, _ = session_factory([scripted_final_text("做完了")])
    monkeypatch.setattr(cli, "build_session", lambda **kwargs: done)
    assert cli.main(["--task", "随便", "--yes"]) == 0

    stalled, _ = session_factory([LLMError("端点 500，重试耗尽")])
    monkeypatch.setattr(cli, "build_session", lambda **kwargs: stalled)
    assert cli.main(["--task", "随便", "--yes"]) == 1


def test_main_falls_back_to_auto_when_stdin_is_not_a_tty(
    monkeypatch: pytest.MonkeyPatch, session_factory: Any, tmp_path: Path
) -> None:
    session, _ = session_factory([scripted_final_text("好")])
    monkeypatch.setattr(cli, "get_config", lambda *a, **k: make_config(tmp_path))
    monkeypatch.setattr(sys, "stdin", type("NotATty", (), {"isatty": lambda self: False})())
    monkeypatch.setattr(cli, "build_session", lambda **kwargs: session)

    seen: dict[str, Any] = {}

    def fake_repl(injected: Any, **_kw: Any) -> int:
        seen["mode"] = injected.agent.gate.mode
        return 0

    monkeypatch.setattr(cli, "repl", fake_repl)
    assert cli.main([]) == 0
    assert seen["mode"] is PermissionMode.AUTO


def test_confirmer_translates_answers() -> None:
    replies = iter(["a", "y", "n", "", "howard"])
    confirmer = cli.make_confirmer(ask=lambda _p: next(replies), echo=lambda _t: None)
    assert confirmer("bash", "$ pytest") is Answer.ALWAYS
    assert confirmer("bash", "$ pytest") is Answer.ONCE
    assert confirmer("bash", "$ pytest") is Answer.NO
    assert confirmer("bash", "$ pytest") is Answer.NO
    assert confirmer("bash", "$ pytest") is Answer.NO


def test_confirmer_treats_interrupt_as_refusal() -> None:
    def interrupt(_prompt: str) -> str:
        raise KeyboardInterrupt

    assert cli.make_confirmer(ask=interrupt, echo=lambda _t: None)("bash", "$ ls") is Answer.NO
