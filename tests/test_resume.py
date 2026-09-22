"""恢复语义测试（SPEC v2 §3.6 的 durable session + `mcc resume`）。

这里最值钱的一条是"撤掉重放"：一次崩在批次中间的会话，恢复时**已经做过的那个调用
绝不能再做一遍**。判据不是看有没有一条日志，而是看磁盘 —— 做完之后人为改过那个文件，
如果恢复过程把它写回了原样，那就是重放了副作用。

四条承诺，每条一个用例组：

- at-most-once 副作用（磁盘内容 + `session_replay` 记录 + 不写 `tool_call`）
- 现场破损时**拒绝**恢复，并把坏文件留在磁盘上
- 计数与 rev 跟着现场回来，`status` 回到 running（否则一次 run 都开不了）
- 恢复链可追：`resumed_from` 从现场进到 trace，再进到下一份现场
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeLLM, scripted_final_text, scripted_tool_calls
from test_undo import SYSTEM, make_agent

from miniclaude.agent.loop import Agent
from miniclaude.agent.permissions import PermissionMode
from miniclaude.backend.sessions import REPLAY_NOTE, SessionLog, SessionRecorder
from miniclaude.cli import main as cli
from miniclaude.config import Config
from miniclaude.infra.trace import replay
from miniclaude.messages import PairingError

SECRET = "sk-resume-secret-value-0123456789"


def crash_scene(agent: Agent, *, done: list[str], keep: int = 2) -> Any:
    """把一份真现场剪成"崩在批次中间"。

    `keep=2` 是这条形状的要点，不是随手挑的下标：留下的最后一条必须是**声明了整批调用、
    一个结果都没回填**的助手消息（user + assistant(2 calls)）。`restore_session` 只承认
    这一种破损，剪到第 3 条就变成"结果齐了"，那是完整现场，走不到重放这条分支。
    """
    snapshot = agent.recorder.snapshot_of(agent, termination="")
    return replace(snapshot, messages=snapshot.messages[:keep], done_call_ids=done, termination="")


def one_write_call(path: str) -> Any:
    """单调用那一轮：id 一定是 `call_0`（`scripted_tool_calls` 按下标命名）。

    故意的 —— "每轮都发同一个 id"正是 FakeLLM 与真实端点的差别所在，也是上面那条
    误判唯一能被触发的形状。
    """
    return scripted_tool_calls([("write_file", {"path": path, "content": "A = 1\n"})])


def two_write_calls() -> Any:
    return scripted_tool_calls(
        [("write_file", {"path": "a.py", "content": "A = 1\n"}), ("write_file", {"path": "b.py", "content": "B = 2\n"})]
    )


@pytest.fixture
def rooted(tmp_path: Path) -> Path:
    (tmp_path / "seed.py").write_text("SEED = 0\n", encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------- at-most-once


def test_the_ledger_of_this_process_is_not_a_replay_guard(rooted: Path) -> None:
    """`done_call_ids` 里有这个 id，**不代表**上一个进程做过它。

    真实的踩坑现场（B2 的 on 臂，`eval/.work/b2-ab/on` —— 那份 trace 已被修复后的重跑覆盖，
    同名 trace 只留最后一次，所以**这条测试就是它的可复现形式**）：`scripted_tool_calls` 每轮都发
    `call_0`，`dedupe_tool_use_ids` 只按**当前历史**改名，而 L2 摘要会把带旧名的那条助手消息
    整组删掉 —— 历史里看不见冲突了，于是第 13 轮又发出来一个干净的 `call_0`。判据如果读
    `done_call_ids`（本进程每执行一次就往里加一个），这条全新的写入就被认成"上一个进程做过"、
    直接跳过：当时那一批 19 轮里跳了 6 次，八份汇总只写出四份，判据判 fail，而 trace 看着一切正常。

    所以这里直接摆出那个状态：账本里有 `call_0`，历史里没有。它必须**执行**。
    """
    agent = make_agent(rooted, [one_write_call("a.py"), scripted_final_text("写完了。")], session_id="recycled")
    agent.done_call_ids.add("call_0")  # ← 本进程自己记的账，不是从现场恢复来的
    agent.run("写一个文件")

    assert (rooted / "a.py").is_file(), "全新的一次写入被误判成重放，副作用没发生"
    records = list(replay(next(Path(rooted).rglob(".traces/*.jsonl"))))
    kinds = [record["kind"] for record in records]
    assert "session_replay" not in kinds, "没有恢复过现场，就不该有任何一条重放记录"
    assert kinds.count("tool_call") == 1


def test_a_done_call_is_never_re_executed(rooted: Path) -> None:
    """核心那条：崩之前写完的文件被人为改过，恢复之后它必须还是被改过的样子。

    如果恢复把 `write_file("a.py")` 重放了一遍，文件会被写回 "A = 1" —— 那就是
    "把上次那个 rm 再跑一次"的同一种事故。
    """
    first = make_agent(rooted, [two_write_calls(), scripted_final_text("写完了。")], session_id="p1")
    first.run("一次写两个文件")
    assert (rooted / "a.py").exists() and (rooted / "b.py").exists()
    scene = crash_scene(first, done=["call_0"])

    (rooted / "a.py").write_text("TAMPERED BY HAND\n", encoding="utf-8")
    (rooted / "b.py").unlink()

    second = make_agent(rooted, [scripted_final_text("补完了。")], session_id="p2")
    note = second.restore_session(scene)
    assert "还要补跑 1 个调用" in note
    second.resume()

    assert (rooted / "a.py").read_text(encoding="utf-8") == "TAMPERED BY HAND\n", "已完成的写被重放了"
    assert (rooted / "b.py").read_text(encoding="utf-8") == "B = 2\n", "没做过的那个调用该补上"


def test_replay_backfills_a_note_instead_of_an_output(rooted: Path) -> None:
    """回填给模型的必须是一条说明，而不是一个洞：配对了，但内容是"没重放"。"""
    first = make_agent(rooted, [two_write_calls(), scripted_final_text("写完了。")], session_id="p1")
    first.run("一次写两个文件")
    scene = crash_scene(first, done=["call_0"])

    second = make_agent(rooted, [scripted_final_text("补完了。")], session_id="p2")
    second.restore_session(scene)
    second.resume()

    sent = second.llm.calls[0]["messages"]  # type: ignore[attr-defined]
    results = [block for message in sent for block in message.results]
    notes = [block.content for block in results if REPLAY_NOTE in block.content]
    assert len(notes) == 1, "跳过的调用要有一条结果顶着，否则历史就破了相"
    assert any("未重放" in text for text in notes)


def test_replay_is_recorded_but_not_as_a_tool_call(rooted: Path) -> None:
    """`tool_call` 的口径是"本进程执行过"。把重放记进去，eval 的执行数就掺了假。"""
    first = make_agent(rooted, [two_write_calls(), scripted_final_text("写完了。")], session_id="p1")
    first.run("一次写两个文件")
    scene = crash_scene(first, done=["call_0"])

    second = make_agent(rooted, [scripted_final_text("补完了。")], session_id="p2")
    second.restore_session(scene)
    second.resume()

    records = replay(rooted / ".traces" / "p2.jsonl")
    calls = [r for r in records if r.get("kind") == "tool_call"]
    replays = [r for r in records if r.get("kind") == "session_replay"]
    assert len(calls) == 1 and calls[0]["name"] == "write_file"
    assert len(replays) == 1
    assert replays[0]["tool_use_id"] == "call_0" and replays[0]["why"]
    end = next(r for r in reversed(records) if r.get("kind") == "run_end")
    # 3 = 上个进程发起的 2 次 + 本进程新发起的 1 次。`state` 是**会话级**的账，
    # trace 文件是**进程级**的观察 —— 两者的口径差就是这么来的，B6 的报表按后者数执行。
    assert end["tool_calls"] == 3, "发起数跟着现场续算，重放的那次不重复计数"


# --------------------------------------------------------------- 拒绝恢复


def test_a_broken_middle_is_refused_and_left_on_disk(rooted: Path) -> None:
    """中间断掉的配对不是"崩在批次里"，猜着跑会把损坏扩大。文件必须留着。"""
    first = make_agent(rooted, [two_write_calls(), scripted_final_text("写完了。")], session_id="p1")
    first.run("一次写两个文件")
    scene = first.recorder.snapshot_of(first, termination="")
    broken = replace(scene, messages=scene.messages[:2] + scene.messages[3:])  # 抽掉结果那条

    path = first.recorder.log.path_for(scene.session_id)
    assert first.recorder.log.save(broken)
    second = make_agent(rooted, [scripted_final_text("不该跑到这儿")], session_id="p2")
    with pytest.raises(PairingError) as excinfo:
        second.restore_session(broken)
    assert "拒绝恢复" in str(excinfo.value)
    assert path.exists(), "删掉坏现场就毁掉了排查它唯一的一份证据"
    assert second.messages == [], "拒绝发生在装回去之前"


def test_restored_counters_and_status_continue(rooted: Path) -> None:
    """计数跟着现场回来，`status` 一律回到 running —— 带着 done 的状态是开不了下一轮的。"""
    first = make_agent(rooted, [two_write_calls(), scripted_final_text("写完了。")], session_id="p1")
    first.run("一次写两个文件")
    scene = first.recorder.snapshot_of(first, termination="completed")
    second = make_agent(rooted, [scripted_final_text("接着说。")], session_id="p2")
    second.restore_session(scene)
    assert second.state.tool_calls == first.state.tool_calls
    assert second.state.status == "running"
    assert sorted(second.done_call_ids) == ["call_0", "call_1"]
    assert second.recorder.last_checkpoint_rev == scene.last_checkpoint_rev


def test_resume_does_not_take_a_second_baseline(rooted: Path) -> None:
    """现场里已经有 rev 了，再拍一条基线等于把撤销栈的底部换成"恢复时的样子"。"""
    first = make_agent(
        rooted,
        [scripted_tool_calls([("write_file", {"path": "a.py", "content": "A\n"})]), scripted_final_text("写完了。")],
        session_id="p1",
    )
    first.run("写 a.py")
    before = list(first.backend.history())  # type: ignore[union-attr]
    scene = first.recorder.snapshot_of(first, termination="")

    second = make_agent(rooted, [scripted_final_text("接着。")], session_id="p2")
    second.restore_session(scene)
    second.resume()
    assert second.backend.history() == before  # type: ignore[union-attr]
    second_records = replay(rooted / ".traces" / "p2.jsonl")
    assert not [r for r in second_records if r.get("kind") == "checkpoint" and r.get("note") == "baseline"]


def test_resumed_from_chain_is_writable(rooted: Path) -> None:
    """第二份现场要说得出自己是从哪儿来的 —— 不然一次长任务断三次之后没人知道顺序。"""
    first = make_agent(rooted, [scripted_final_text("第一段。")], session_id="p1")
    first.run("先跑一段")
    scene = first.recorder.snapshot_of(first, termination="completed")

    second = make_agent(rooted, [scripted_final_text("第二段。")], session_id="p2")
    second.restore_session(scene)
    second.resume()
    later = second.recorder.log.load("p2")
    assert later[0] is not None and later[0].resumed_from == "p1"
    start = [r for r in replay(rooted / ".traces" / "p2.jsonl") if r.get("kind") == "run_start"]
    assert start[0]["resumed_from"] == "p1"
    assert second.recorder.stats()["resumed_from"] == "p1"


# --------------------------------------------------------------- CLI


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Config, Path]:
    """真装配（真 git、真检查点、真现场目录），只把 LLM 换成假的。

    下面每个用例都要显式给 `mode=PermissionMode.AUTO`：`build_session` 的默认是 ASK，
    而测试里没有终端可答 —— 让权限门去问一个不存在的人，它只会读 stdin。
    """
    root = tmp_path / "proj"
    root.mkdir()
    (root / "seed.py").write_text("SEED = 0\n", encoding="utf-8")
    config = Config(
        base_url="https://mock.local/v1",
        api_key=SECRET,
        model="mock-model",
        project_root=root,
        trace_path=root / ".traces" / "cli.jsonl",
    )
    monkeypatch.setattr(cli, "get_config", lambda *a, **k: config)
    return config, root


def run_cli(argv: list[str], responses: list[Any], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(cli, "OpenAICompatClient", lambda **_kw: FakeLLM(list(responses)))
    return cli.main(argv)


def test_resume_continues_a_saved_session(cli_env: tuple[Config, Path], monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    config, root = cli_env
    session = cli.build_session(config=config, mode=PermissionMode.AUTO, llm=FakeLLM([two_write_calls(), scripted_final_text("写完了。")]))
    session.agent.run("一次写两个文件")
    session_id = session.tracer.session_id
    # 剪成崩在批次中间，再写回同一份文件。
    scene = crash_scene(session.agent, done=["call_0"])
    assert session.agent.recorder.log.save(scene)
    (root / "a.py").write_text("TAMPERED\n", encoding="utf-8")
    (root / "b.py").unlink()

    code = run_cli(["resume", session_id], [scripted_final_text("补完了。")], monkeypatch)
    assert code == 0
    assert (root / "a.py").read_text(encoding="utf-8") == "TAMPERED\n"
    assert (root / "b.py").read_text(encoding="utf-8") == "B = 2\n"
    out = capsys.readouterr().out
    assert "已恢复会话" in out and "不会重放" in out


def test_resume_lists_scenes_newest_first(cli_env: tuple[Config, Path], monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    """`--list` 是 `mcc resume`（不带 id）之外用户唯一的找路方式，顺序就是它的可用性。

    条目行以两个空格缩进开头，最后一行还得带上"这份现场属于哪个工作区"—— 下一行那个
    用例正靠它来判断该不该拒绝。
    """
    config, root = cli_env
    ids = []
    for _ in range(2):
        session = cli.build_session(config=config, mode=PermissionMode.AUTO, llm=FakeLLM([scripted_final_text("好。")]))
        session.agent.run("看一眼")
        ids.append(session.tracer.session_id)
    code = run_cli(["resume", "--list"], [], monkeypatch)
    assert code == 0
    printed = capsys.readouterr().out
    assert "可恢复的会话现场" in printed
    entries = [line.strip() for line in printed.splitlines() if line.startswith("  ")]
    assert [line.split(" ")[0] for line in entries] == list(reversed(ids)), "最近的现场必须排在最前"
    assert all(str(root) in line for line in entries), "每条都要说清自己是哪个工作区的现场"


def test_resume_without_any_scene_says_where_it_looked(
    cli_env: tuple[Config, Path], monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    code = run_cli(["resume", "--latest"], [], monkeypatch)
    assert code == 2
    err = capsys.readouterr().err
    assert "没有可恢复的现场" in err and "sessions" in err


def test_resume_refuses_a_scene_from_another_project(
    cli_env: tuple[Config, Path], monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """现场里的路径都是相对工作区的，换个根续跑等于把上半句话写到另一棵树上。

    这里把 `MEMORY_DIR` 换成一个**绝对路径**，让两个工作区共用同一批现场 —— 否则换根
    之后目录也跟着换，走到的是"没找到"那条分支，拒绝守卫根本没被碰到。（顺带证明了
    §3.6 的 MEMORY_DIR 支持绝对路径。）
    """
    config, root = cli_env
    session = cli.build_session(config=config, mode=PermissionMode.AUTO, llm=FakeLLM([scripted_final_text("好。")]))
    session.agent.run("看一眼")
    session_id = session.tracer.session_id

    elsewhere = config.project_root.parent / "other"
    elsewhere.mkdir()
    shared = str(root / ".mcc")
    monkeypatch.setattr(
        cli, "get_config", lambda *a, **k: replace(config, project_root=elsewhere, memory_dir=shared)
    )
    code = run_cli(["resume", session_id], [], monkeypatch)
    assert code == 2
    err = capsys.readouterr().err
    assert "拒绝恢复" in err and str(root) in err
    assert not (elsewhere / ".mcc").exists(), "拒绝要发生在装配之前"


def test_resume_of_a_broken_scene_keeps_the_file(
    cli_env: tuple[Config, Path], monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    config, root = cli_env
    session = cli.build_session(config=config, mode=PermissionMode.AUTO, llm=FakeLLM([two_write_calls(), scripted_final_text("好。")]))
    session.agent.run("一次写两个文件")
    log = session.agent.recorder.log
    scene = session.agent.recorder.snapshot_of(session.agent, termination="")
    broken = replace(scene, messages=scene.messages[:2] + scene.messages[3:])
    assert log.save(broken)
    code = run_cli(["resume", scene.session_id], [], monkeypatch)
    assert code == 2
    assert "拒绝恢复" in capsys.readouterr().err
    assert log.path_for(scene.session_id).exists()


def test_resume_reports_a_missing_scene_with_a_hint(
    cli_env: tuple[Config, Path], monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    config, root = cli_env
    session = cli.build_session(config=config, mode=PermissionMode.AUTO, llm=FakeLLM([scripted_final_text("好。")]))
    session.agent.run("看一眼")
    code = run_cli(["resume", "nosuchid"], [], monkeypatch)
    assert code == 2
    assert "没有找到" in capsys.readouterr().err


def test_restored_scene_carries_no_secret(
    cli_env: tuple[Config, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """现场是明文 JSON，会被人贴进 issue —— 密钥进盘之前就得掩掉（§0.4 密钥纪律）。"""
    config, root = cli_env
    session = cli.build_session(config=config, mode=PermissionMode.AUTO, llm=FakeLLM([scripted_final_text("好。")]))
    session.agent.run("看一眼")
    files = list((root / ".mcc" / "sessions").glob("*.json"))
    assert files
    text = files[0].read_text(encoding="utf-8")
    assert SECRET not in text and json.loads(text)["config"]["api_key"] != SECRET
