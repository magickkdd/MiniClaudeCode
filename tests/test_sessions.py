"""可恢复现场的落盘规则（SPEC v2 §3.6 的 SessionSnapshot / SessionLog）。

`session_id` 会变成文件名，`config` 会变成文件内容 —— 这两件事合起来就是这一层的全部
风险：一个可能写出目录之外，一个可能把密钥写进磁盘。所以这里的用例分两组：

- **形状**：存进去的现场取得出来，字段一个不丢（丢了 resume 就是在猜）。
- **失败**：文件不存在 / 读坏 / 版本不匹配，统统交回一句能印的原因，而不是抛出去
  让 `mcc resume` 崩在 traceback 上。坏文件一律**留着**，它是排查唯一的一份证据。
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fakes import scripted_final_text
from test_undo import make_agent

from miniclaude.backend.sessions import (
    SNAPSHOT_SCHEMA,
    SessionLog,
    SessionRecorder,
    SessionSnapshot,
    message_from_dict,
    message_to_dict,
)
from miniclaude.config import Config
from miniclaude.messages import Message, Role, TextBlock, ToolResultBlock, ToolUseBlock

SECRET = "sk-super-secret-value-0123456789"


@pytest.fixture
def log(tmp_path: Path) -> SessionLog:
    return SessionLog(root=tmp_path / "sessions")


def snapshot(session_id: str = "abc123", **kwargs: Any) -> SessionSnapshot:
    base = {
        "session_id": session_id,
        "turn": 3,
        "messages": [
            message_to_dict(Message.user_text("做点什么")),
            message_to_dict(
                Message(Role.ASSISTANT, [ToolUseBlock(id="call_0", name="write_file", input={"path": "a.py"})])
            ),
            message_to_dict(
                Message(Role.USER, [ToolResultBlock(tool_use_id="call_0", content="wrote a.py", is_error=False)])
            ),
        ],
        "todos": [{"content": "收尾", "status": "pending"}],
        "state": {"turn": 3, "tool_calls": 1},
        "backend": "local",
        "last_checkpoint_rev": "deadbeef",
        "done_call_ids": ["call_0"],
    }
    return SessionSnapshot(**{**base, **kwargs})


# --------------------------------------------------------------- 形状


def test_round_trip_keeps_every_field(log: SessionLog) -> None:
    original = snapshot()
    assert log.save(original)
    loaded, why = log.load(original.session_id)
    assert loaded is not None, why
    assert loaded.as_dict() == original.as_dict()


def test_messages_survive_as_blocks(log: SessionLog) -> None:
    """角色、块类型、tool_use 的 id 与参数都得原样回来 —— resume 靠 id 判断谁做过。"""
    original = snapshot()
    log.save(original)
    messages = original.load_messages()
    assert [m.role for m in messages] == [Role.USER, Role.ASSISTANT, Role.USER]
    assert messages[1].tool_uses[0].id == "call_0"
    assert messages[1].tool_uses[0].input == {"path": "a.py"}
    assert messages[2].results[0].tool_use_id == "call_0"


def test_save_is_atomic_and_leaves_no_temporary(log: SessionLog) -> None:
    log.save(snapshot())
    files = sorted(p.name for p in log.root.iterdir())
    assert files == ["abc123.json"], "留下 .tmp 就是每次崩掉都多一个孤儿文件"


def test_unknown_session_says_so(log: SessionLog) -> None:
    loaded, why = log.load("nope")
    assert loaded is None and "没有找到" in why


def test_corrupt_file_is_refused_not_repaired(log: SessionLog) -> None:
    log.save(snapshot())
    log.path_for("abc123").write_text("{这不是 json", encoding="utf-8")
    loaded, why = log.load("abc123")
    assert loaded is None and "读坏" in why


def test_non_object_payload_is_refused(log: SessionLog) -> None:
    log.root.mkdir(parents=True, exist_ok=True)
    (log.root / "list.json").write_text("[1, 2]", encoding="utf-8")
    loaded, why = log.load("list")
    assert loaded is None and "不是一个对象" in why


def test_schema_mismatch_refuses_to_guess(log: SessionLog) -> None:
    """版本对不上就停手。猜一份旧现场的结构，等于把缺的字段当成空值往下跑。"""
    log.save(snapshot(schema=SNAPSHOT_SCHEMA + 1))
    loaded, why = log.load("abc123")
    assert loaded is None and "不匹配" in why
    assert log.path_for("abc123").exists(), "拒绝可以，删掉文件不行"


def test_future_and_missing_dirs_degrade_quietly(tmp_path: Path) -> None:
    """一个都不存在的目录：save 不抛，load 交回原因。盘不可用不该让会话开不了。"""
    log = SessionLog(root=tmp_path / "deeply" / "missing")
    assert log.save(snapshot())
    assert log.load("abc123")[0] is not None


def test_blocked_directory_records_degraded(tmp_path: Path) -> None:
    blocker = tmp_path / "sessions"
    blocker.write_text("我占着这个名字", encoding="utf-8")
    log = SessionLog(root=blocker)
    assert log.save(snapshot()) is False
    assert log.degraded and log.writes == 0


# --------------------------------------------------------------- 文件名安全


@pytest.mark.parametrize("raw", ["../../escape", "a/b.json", "..\\..\\win", "", "ok-1_2", "x" * 500])
def test_session_id_cannot_escape_its_directory(log: SessionLog, raw: str) -> None:
    """会话 id 来自模型可能影响到的地方（trace 文件名同理），拼路径前必须先洗。"""
    path = log.path_for(raw)
    assert path.parent == log.root
    assert path.name.endswith(".json")
    assert not any(sep in path.name for sep in "/\\")
    assert ".." not in path.name
    assert len(path.name) <= 64 + len(".json")


# --------------------------------------------------------------- 目录层


def test_ids_are_newest_first_and_skip_junk(log: SessionLog) -> None:
    for index in range(3):
        log.save(snapshot(session_id=f"s{index}"))
        time.sleep(0.01)
    (log.root / "junk.json").write_text("not json at all", encoding="utf-8")
    (log.root / "notes.txt").write_text("ignore me", encoding="utf-8")
    assert log.ids() == ["s2", "s1", "s0"]


def test_prune_keeps_the_newest_n(log: SessionLog) -> None:
    for index in range(7):
        log.save(snapshot(session_id=f"s{index}"))
        time.sleep(0.01)
    removed = log.prune(keep=3)
    assert removed == 4
    assert sorted(p.name for p in log.root.iterdir()) == ["s4.json", "s5.json", "s6.json"]


def test_prune_leaves_temporary_files_alone(log: SessionLog) -> None:
    """`.tmp` 不是现场，删它不该算进 prune 的账（它由 save 自己覆盖）。"""
    for index in range(4):
        log.save(snapshot(session_id=f"s{index}"))
        time.sleep(0.01)
    (log.root / "leftover.json.tmp").write_text("{}", encoding="utf-8")
    log.prune(keep=2)
    assert (log.root / "leftover.json.tmp").exists()


# --------------------------------------------------------------- 密钥纪律


def test_snapshot_never_carries_a_plaintext_key(tmp_path: Path) -> None:
    """现场里存的是 `Config.redacted()`。这条不是形式主义：快照是明文 JSON，会被人贴进 issue。"""
    config = Config(
        base_url="https://mock.local/v1",
        api_key=SECRET,
        model="mock-model",
        project_root=tmp_path,
        trace_path=tmp_path / ".traces" / "s.jsonl",
    )
    agent = make_agent(tmp_path, [scripted_final_text("看一眼。")])
    recorder = SessionRecorder(log=SessionLog(root=tmp_path / "sessions"), session_id="s", config=config.redacted())
    recorder.save(agent)
    text = next(tmp_path.joinpath("sessions").glob("*.json")).read_text(encoding="utf-8")
    assert SECRET not in text
    assert json.loads(text)["config"]["api_key"] != SECRET


def test_recorder_stats_describe_the_chain(tmp_path: Path) -> None:
    agent = make_agent(tmp_path, [scripted_final_text("看一眼。")])
    recorder = SessionRecorder(
        log=SessionLog(root=tmp_path / "sessions"), session_id="s1", backend="local", resumed_from="s0"
    )
    recorder.record_checkpoint("rev-1")
    assert recorder.save(agent)
    stats = recorder.stats()
    assert stats["writes"] == 1 and stats["resumed_from"] == "s0" and stats["last_checkpoint_rev"] == "rev-1"
    assert stats["root"] == "sessions"


def test_snapshot_of_reads_the_live_agent(tmp_path: Path) -> None:
    from fakes import scripted_tool_calls

    agent = make_agent(
        tmp_path,
        [
            scripted_tool_calls([("write_file", {"path": "a.py", "content": "A\n"})]),
            scripted_final_text("写完了。"),
        ],
    )
    agent.run("写 a.py")
    recorder = SessionRecorder(log=SessionLog(root=tmp_path / "sessions"), session_id="s")
    snap = recorder.snapshot_of(agent, termination="completed")
    assert snap.done_call_ids == ["call_0"]
    assert snap.termination == "completed"
    assert snap.schema == SNAPSHOT_SCHEMA
    assert snap.turn == agent.state.turn


def test_replace_keeps_the_session_id_when_editing_a_scene(tmp_path: Path) -> None:
    """测试与 CLI 都会 `replace()` 现场来模拟崩溃 —— 那条路径不能悄悄换 id。"""
    edited = replace(snapshot(), termination="", done_call_ids=[])
    assert edited.session_id == "abc123" and edited.done_call_ids == []
