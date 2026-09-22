"""`/undo` 与检查点的循环层契约（SPEC v2 §3.6，B6 的"可回退"那一半）。

这里测的是**游标**，不是 git：git 层的事实（三类文件、字节、用户仓库安全）在
`test_checkpoints.py` 里。循环层独有的四件事：

1. **会话一开始就有一条基线 rev。** 没有它，第一次写入永远撤不回去 —— 栈里每条
   "写入之后"的状态撤到哪儿都还是"已经改过了"。
2. **一次成功的写 = 一条 rev。** 失败的写不拍：撤销一次没发生的写入是空操作，
   却会占掉游标一格。
3. **每按一次 `/undo` 往更早走一格。** 影子仓库是 append-only 的，`history()` 不会
   因为回滚而变化，所以撤销进度必须自己记账 —— 不记的话第二次撤的是同一格。
4. **只读模式在用的那一刻判定。** `/mode readonly` 是会话中途切进来的，装配时关掉
   的那套开关管不到它。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fakes import FakeLLM, scripted_final_text, scripted_tool_calls
from miniclaude.cli.main import Session

from miniclaude.agent.loop import Agent, EventKind
from miniclaude.agent.permissions import Answer, PermissionGate, PermissionMode
from miniclaude.agent.planner import TodoList
from miniclaude.agent.todo_tool import WriteTodosTool
from miniclaude.backend.factory import select_backend
from miniclaude.backend.protocol import NO_CHECKPOINT_REASON
from miniclaude.backend.sessions import SESSIONS_DIRNAME, SessionLog, SessionRecorder
from miniclaude.infra.trace import Tracer, prompt_hash, replay
from miniclaude.tools.registry import ToolRegistry
from miniclaude.tools.workspace import Workspace

SYSTEM = "你是测试用 Agent。"
TRACE = "undo-test.jsonl"


def make_agent(
    root: Path,
    responses: list[Any],
    *,
    mode: PermissionMode = PermissionMode.AUTO,
    checkpoints: bool = True,
    with_recorder: bool = True,
    confirmer: Any = None,
    session_id: str = "undo-test",
) -> Agent:
    """真 git + 真工具 + 假模型。"撤销得回去"这件事不能靠替身证明。"""
    workspace = Workspace(root)
    todos = TodoList()
    choice = select_backend("local", workspace_root=root, memory_dir=root / ".mcc", checkpoints=checkpoints)
    registry = ToolRegistry.default(
        workspace, bash_timeout=30, extra_tools=[WriteTodosTool(workspace, todos)], backend=choice.backend
    )
    tracer = Tracer(root / ".traces" / f"{session_id}.jsonl", session_id=session_id)
    tracer.start_session(
        model="fake", tools=list(registry.names()), config={}, system_prompt_hash=prompt_hash(SYSTEM)
    )
    recorder = (
        SessionRecorder(
            log=SessionLog(root=root / ".mcc" / SESSIONS_DIRNAME),
            session_id=session_id,
            backend=choice.name,
        )
        if with_recorder
        else None
    )
    return Agent(
        llm=FakeLLM(responses),
        registry=registry,
        gate=PermissionGate(workspace=workspace, mode=mode, confirmer=confirmer),
        system_prompt=SYSTEM,
        todos=todos,
        tracer=tracer,
        backend=choice.backend,
        recorder=recorder,
        checkpoints=checkpoints,
    )


def write(agent: Agent, path: str, content: str) -> None:
    """让 Agent 真跑一次写入：一次 run 一轮，rev 因此可数。"""
    agent.llm = FakeLLM(
        [
            scripted_tool_calls([("write_file", {"path": path, "content": content})]),
            scripted_final_text("写好了。"),
        ]
    )
    result = agent.run(f"写 {path}")
    assert result.succeeded, "这轮没跑成，后面的 rev 断言就没有意义"


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    (tmp_path / "seed.py").write_text("SEED = 0\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def agent_factory(seeded: Path) -> Any:
    def build(responses: list[Any], **kwargs: Any) -> Agent:
        return make_agent(seeded, responses, **kwargs)

    return build


def checkpoint_events(root: Path) -> list[dict[str, Any]]:
    return [r for r in replay(root / ".traces" / TRACE) if r.get("kind") == "checkpoint"]


# --------------------------------------------------------------- 基线与记账


def test_first_run_takes_a_baseline_rev(agent_factory: Any, seeded: Path) -> None:
    agent = agent_factory([scripted_final_text("什么都没做。")])
    assert agent.backend.history() == []
    agent.run("看一眼就行")
    assert len(agent.backend.history()) == 1, "只读的一轮也要有基线，否则第一次写入撤不回去"
    first = checkpoint_events(seeded)[0]
    assert first["note"] == "baseline" and first["tool"] is None and first["ok"] is True


def test_baseline_is_taken_once_per_session(agent_factory: Any) -> None:
    agent = agent_factory([scripted_final_text("一"), scripted_final_text("二")])
    agent.run("第一轮")
    agent.run("第二轮")
    history = agent.backend.history()
    assert len(history) == 1, "每轮重拍基线会把撤销栈变成一堵墙"
    assert history[0] == history[-1]


def test_each_successful_write_gets_its_own_rev(agent_factory: Any) -> None:
    agent = agent_factory([])
    write(agent, "a.py", "A = 1\n")
    write(agent, "b.py", "B = 2\n")
    assert len(agent.backend.history()) == 3, "基线 + 两次写入"


def test_failed_write_takes_no_rev(agent_factory: Any, seeded: Path) -> None:
    """写失败不占游标一格：用户按 /undo 撤一次"没发生的写入"会以为按键坏了。"""
    agent = agent_factory([])
    (seeded / "blocker").write_text("x", encoding="utf-8")
    agent.llm = FakeLLM(
        [
            scripted_tool_calls([("write_file", {"path": "blocker/nope.py", "content": "1\n"})]),
            scripted_final_text("失败了。"),
        ]
    )
    agent.run("往一个不能当目录的路径里写")
    assert len(agent.backend.history()) == 1, "只剩基线"


# --------------------------------------------------------------- 撤销游标


def test_undo_walks_back_one_write_per_call(agent_factory: Any, seeded: Path) -> None:
    agent = agent_factory([])
    write(agent, "a.py", "A = 1\n")
    write(agent, "b.py", "B = 2\n")

    ok, why = agent.undo_last_write()
    assert ok, why
    assert not (seeded / "b.py").exists() and (seeded / "a.py").exists()

    ok, why = agent.undo_last_write()
    assert ok, why
    assert not (seeded / "a.py").exists()
    assert (seeded / "seed.py").read_text(encoding="utf-8") == "SEED = 0\n", "基线之前的世界不该被动过"

    ok, why = agent.undo_last_write()
    assert not ok and "已经没有可撤销的写入" in why
    assert why == agent.undo_plan()[2], "CLI 的确认文案与循环的判定必须同源，否则两边会各说一套"


def test_undo_plan_names_both_revs(agent_factory: Any) -> None:
    agent = agent_factory([])
    write(agent, "a.py", "A = 1\n")
    write(agent, "b.py", "B = 2\n")
    history = agent.backend.history()
    target, undone, why = agent.undo_plan()
    assert why == "" and undone == history[0] and target == history[1]
    agent.undo_last_write()
    target2, undone2, _ = agent.undo_plan()
    assert (target2, undone2) == (history[2], history[1]), "第二次要往更早那一格走，不是同一格撤两遍"


def test_a_new_write_rewinds_the_cursor(agent_factory: Any, seeded: Path) -> None:
    """撤销过之后再写入，游标归零：`/undo` 说的是"撤掉最近那次写入"。"""
    agent = agent_factory([])
    write(agent, "a.py", "A = 1\n")
    assert agent.undo_last_write()[0]
    assert not (seeded / "a.py").exists()
    write(agent, "b.py", "B = 2\n")
    assert (seeded / "b.py").exists()
    assert agent.undo_last_write()[0]
    assert not (seeded / "b.py").exists()
    assert (seeded / "a.py").read_text(encoding="utf-8") == "A = 1\n", "撤掉 b 之后 a 该回到原位"


def test_undo_emits_a_checkpoint_event(agent_factory: Any, seeded: Path) -> None:
    agent = agent_factory([])
    write(agent, "a.py", "A = 1\n")
    agent.undo_last_write()
    undos = [r for r in checkpoint_events(seeded) if str(r.get("note", "")).startswith("undo")]
    assert len(undos) == 1 and undos[0]["ok"] is True and undos[0]["tool"] is None


# --------------------------------------------------------------- 降级与模式


def test_degraded_snapshot_is_recorded_but_does_not_break_the_write(seeded: Path) -> None:
    """记不下检查点时：写入照样成功，trace 里留一条 ok=False 的 checkpoint。

    这是 §2.3-1"度量层没有否决任务的权力"在执行层的形状 —— 反过来（因为拍不下 rev
    而让 write_file 报错）就等于让度量层有权判一次任务失败。
    """
    agent = make_agent(seeded, [])

    class NoSnapshot:
        """除了 snapshot 之外全部转发给真后端：只把"拍不下"这一件事造出来。"""

        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def __getattr__(self, item: str) -> Any:
            return getattr(self.inner, item)

        def available(self) -> tuple[bool, str]:
            return True, "替身"

        def snapshot(self) -> tuple[str, str]:
            return "", "git 坏了（测试造的）"

        def history(self) -> list[str]:
            return self.inner.history()

    agent.backend = NoSnapshot(agent.backend)
    events: list[Any] = []
    agent.on_event = lambda event: events.append(event)
    agent.llm = FakeLLM(
        [
            scripted_tool_calls([("write_file", {"path": "a.py", "content": "A = 1\n"})]),
            scripted_final_text("写好了。"),
        ]
    )
    result = agent.run("写 a.py")
    assert result.succeeded and (seeded / "a.py").exists()
    degraded = [e for e in events if e.kind is EventKind.CHECKPOINT and not e.payload["ok"]]
    assert degraded and "git 坏了" in degraded[0].payload["reason"]
    ok, why = agent.undo_last_write()
    assert not ok and why


def test_readonly_mode_refuses_undo(agent_factory: Any, seeded: Path) -> None:
    agent = agent_factory([])
    write(agent, "a.py", "A = 1\n")
    agent.gate.mode = PermissionMode.READONLY  # `/mode readonly` 是会话中途切的
    ok, why = agent.undo_last_write()
    assert not ok and "只读" in why
    assert (seeded / "a.py").exists(), "拒绝就必须真的一个字节都没动"


def test_readonly_mode_takes_no_baseline(agent_factory: Any, seeded: Path) -> None:
    agent = agent_factory([scripted_final_text("看一眼就完了。")], mode=PermissionMode.READONLY)
    agent.run("看一眼")
    assert agent.backend.history() == []
    assert checkpoint_events(seeded) == []


def test_checkpoints_disabled_says_the_same_thing(agent_factory: Any) -> None:
    agent = agent_factory([scripted_final_text("看一眼就完了。")], checkpoints=False)
    agent.run("看一眼")
    ok, why = agent.undo_last_write()
    assert not ok and why == NO_CHECKPOINT_REASON
    assert agent.undo_plan()[2] == NO_CHECKPOINT_REASON


def test_no_recorder_still_checkpoints(agent_factory: Any, seeded: Path) -> None:
    """现场落盘和检查点是两件事：eval 里没有 recorder，撤销能力不该跟着消失。"""
    agent = agent_factory([], with_recorder=False)
    write(agent, "a.py", "A = 1\n")
    assert len(agent.backend.history()) == 2
    assert agent.undo_last_write()[0] and not (seeded / "a.py").exists()


# --------------------------------------------------------------- CLI 两条命令


def cli_session(
    root: Path,
    responses: list[Any],
    *,
    mode: PermissionMode = PermissionMode.AUTO,
    confirmer: Any = None,
    **config: Any,
) -> Session:
    from miniclaude.cli.main import build_session
    from miniclaude.cli.render import Renderer
    from miniclaude.config import Config

    return build_session(
        config=Config(
            base_url="https://mock.local/v1",
            api_key="sk-test-abcdefghijklmn",
            model="mock-model",
            project_root=root,
            trace_path=root / ".traces" / "cli.jsonl",
            **config,
        ),
        renderer=Renderer(write=lambda _text: None, use_rich=False),
        llm=FakeLLM(responses),
        mode=mode,
        confirmer=confirmer,
    )


def test_cli_undo_reports_both_revs(seeded: Path) -> None:
    from miniclaude.cli.main import do_undo

    session = cli_session(seeded, [])
    write(session.agent, "a.py", "A = 1\n")
    text = do_undo(session)
    assert text.startswith("已回退工作区到 ") and "对话历史不会倒带" in text
    assert not (seeded / "a.py").exists()
    assert "已经没有可撤销的写入" in do_undo(session)


def test_cli_undo_refuses_a_session_without_checkpoints(seeded: Path) -> None:
    from miniclaude.cli.main import do_undo

    session = cli_session(seeded, [scripted_final_text("好。")], checkpoints=False)
    session.agent.run("看一眼")
    assert "没有启用检查点" in do_undo(session)


def test_cli_undo_asks_before_touching_files(seeded: Path) -> None:
    """ASK 模式下的 `/undo` 要过确认，而且拒绝时磁盘一个字都没变。"""
    from miniclaude.cli.main import do_undo

    session = cli_session(seeded, [], mode=PermissionMode.ASK, confirmer=lambda *_a: Answer.ONCE)
    write(session.agent, "a.py", "A = 1\n")
    session.agent.gate.mode = PermissionMode.ASK
    session.agent.gate.confirmer = lambda *_a: Answer.NO
    assert "已取消" in do_undo(session)
    assert (seeded / "a.py").exists()
    session.agent.gate.confirmer = lambda *_a: Answer.ONCE
    assert "已回退" in do_undo(session)
    assert not (seeded / "a.py").exists()


def test_cli_backend_panel_shows_the_whole_picture(seeded: Path) -> None:
    from miniclaude.cli.main import render_backend

    session = cli_session(seeded, [scripted_final_text("好。")])
    session.agent.run("看一眼")
    panel = render_backend(session)
    assert "本次用 local 后端" in panel and "点名 local" in panel
    assert "检查点 开" in panel
    assert "mcc resume" in panel, "面板要能告诉用户崩了之后怎么接着跑"
    assert "栈（新→旧）" in panel


def test_cli_backend_panel_is_honest_about_readonly(seeded: Path) -> None:
    from miniclaude.cli.main import render_backend

    session = cli_session(seeded, [scripted_final_text("好。")], mode=PermissionMode.READONLY)
    panel = render_backend(session)
    assert "检查点 关" in panel and "会话现场：关" in panel


def test_cli_backend_panel_names_the_degradation(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from miniclaude.cli.main import render_backend

    monkeypatch.setattr("shutil.which", lambda _name: None)
    session = cli_session(seeded, [scripted_final_text("好。")], execution_backend="docker")
    panel = render_backend(session)
    assert "docker 不可用" in panel and "本次用 local 后端" in panel
