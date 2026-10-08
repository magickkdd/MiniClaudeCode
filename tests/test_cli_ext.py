"""§3.7 两条扩展在 CLI 与装配层的接线测试。

`test_mcp_bridge.py` / `test_skills.py` 测的是机制本身；这里测的是**接上了没有**：
`build_session` 有没有把外部工具装进同一个注册表、system 里有没有那段目录、
`mcc mcp` 的退出码是不是真反映握手结果、以及最重要的一条 —— 配置里的密钥
不会顺着 trace 落盘。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeLLM, scripted_final_text, scripted_tool_calls
from miniclaude.agent.permissions import Answer, PermissionMode
from miniclaude.cli import ext_cmd
from miniclaude.cli.main import build_session, handle_command
from miniclaude.cli.render import Renderer
from miniclaude.config import Config
from miniclaude.ext import MCPBridge, SkillLoader

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "mcp_fixture_server.py"


def server_arg(mode: str = "good") -> list[str]:
    return [str(FIXTURE_SERVER), mode]


def make_config(root: Path, **overrides: Any) -> Config:
    base: dict[str, Any] = {
        "base_url": "https://mock.local/v1",
        "api_key": "sk-test-abcdefghijklmn",
        "model": "mock-model",
        "project_root": root,
        "trace_path": root / ".traces" / "session.jsonl",
        "bash_timeout": 20,
    }
    base.update(overrides)
    return Config(**base)


def add_skill(root: Path, name: str = "demo-skill", body: str = "第一条\n第二条\n") -> Path:
    entry = root / "skills" / name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 接线测试用的技能\n---\n{body}", encoding="utf-8"
    )
    return entry


class Lines:
    def __init__(self) -> None:
        self.items: list[str] = []

    def __call__(self, text: str) -> None:
        self.items.append(text)

    def text(self) -> str:
        return "\n".join(self.items)


@pytest.fixture
def assembled(tmp_path: Path):
    """一次真装配：起真子进程、读真技能目录。用完必须关掉我们起的进程。"""
    (tmp_path / "notes.txt").write_text("第一行\n", encoding="utf-8")
    add_skill(tmp_path)
    opened: list[Any] = []

    def build(**overrides: Any) -> Any:
        responses = overrides.pop("responses", [scripted_final_text("好")])
        mode = overrides.pop("mode", PermissionMode.AUTO)
        confirmer = overrides.pop("confirmer", None)
        lines = Lines()
        session = build_session(
            config=make_config(
                tmp_path,
                mcp_servers=overrides.pop(
                    "mcp_servers", ({"name": "fx", "endpoint": sys.executable, "args": server_arg()},)
                ),
                **overrides,
            ),
            renderer=Renderer(write=lines, use_rich=False),
            llm=FakeLLM(responses),
            mode=mode,
            confirmer=confirmer,
        )
        session.lines = lines  # type: ignore[attr-defined]
        opened.append(session)
        return session

    yield build
    for session in opened:
        session.close()


# --------------------------------------------------------------- 装配


def test_external_capabilities_land_in_the_same_registry(assembled: Any) -> None:
    """外部工具走的是 `extra_tools` 这**一条**装配路径，因此权限门与 trace 天然覆盖它。"""
    session = assembled()
    names = list(session.agent.registry.names())
    assert "mcp__fx__echo" in names and "load_skill" in names
    assert session.mcp is not None and session.mcp.stats()["tools"] == 4
    # 远端把工具数报多了也没用：注册表里只有一个名字，且它是 EXECUTE 级
    assert session.agent.registry.get("mcp__fx__echo").risk_level.value == "execute"


def test_system_prompt_carries_the_catalog_and_the_external_note(assembled: Any) -> None:
    system = assembled().agent.current_system()
    assert "可用技能" in system and "demo-skill" in system
    assert "外部工具（MCP）" in system and "AUTO 模式也不例外" in system
    # 正文不许跟着目录进 system —— 那正是延迟加载要省掉的东西
    assert "第一条" not in system


def test_catalog_and_note_come_last_so_the_prefix_stays_cacheable(assembled: Any) -> None:
    """两段"装配期才知道"的文字排在最后。改技能或加服务不会打掉前面整段的前缀缓存。"""
    system = assembled().agent.current_system()
    stable = system.index("# 自我调试")
    assert stable < system.index("外部工具（MCP）") < system.index("可用技能")


def test_no_external_note_without_an_external_tool(assembled: Any) -> None:
    session = assembled(mcp_servers=())
    assert session.mcp is None
    assert "外部工具（MCP）" not in session.agent.current_system()
    assert "mcc mcp" in handle_command(session, "/mcp")


def test_readonly_spawns_nothing(assembled: Any) -> None:
    """只读模式的承诺是"一个字节都不变"，发现阶段起第三方进程也在"变"的范围内。"""
    session = assembled(mode=PermissionMode.READONLY)
    assert session.mcp is None
    assert "mcp__fx__echo" not in session.agent.registry.names()
    assert session.mcp_skip_reason and "只读" in session.mcp_skip_reason
    assert "只读模式" in handle_command(session, "/mcp")


def test_close_reaps_the_children(assembled: Any) -> None:
    session = assembled()
    bridge: MCPBridge = session.mcp
    assert bridge.available() == ["fx"]
    session.close()
    assert bridge.available() == []


def test_a_server_that_fails_to_start_is_warned_about_not_silently_dropped(assembled: Any) -> None:
    """配了两个、起来一个 —— 静音的话用户会拿一个缺工具的房间去测 agent。"""
    from miniclaude.cli.main import announce

    session = assembled(
        mcp_servers=(
            {"name": "fx", "endpoint": sys.executable, "args": server_arg("good")},
            {"name": "dead", "endpoint": sys.executable, "args": server_arg("exit")},
        )
    )
    announce(session)
    assert "没起来" in session.lines.text()
    assert "dead" in session.lines.text()
    # 坏服务不能带走好服务
    assert "mcp__fx__echo" in session.agent.registry.names()


def test_readonly_warns_that_nothing_was_spawned(assembled: Any) -> None:
    from miniclaude.cli.main import announce

    session = assembled(mode=PermissionMode.READONLY)
    announce(session)
    assert "没有启动任何 MCP 服务" in session.lines.text()


# --------------------------------------------------------------- 副作用边界


def test_a_refused_remote_call_has_no_side_effect_on_disk(assembled: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """拒绝的那一次是**真的没发生** —— 拿"同意"那一臂做对照才证得出来。

    `write_note` 故意落在**工作区之外**（MARKER 指向系统临时目录）：agent 的写闸门管不到
    那种路径，远端自己挑地方。能拦住它的只有 §6.3-1 那一条 —— 外部工具在 AUTO 下也要问。
    所以这一条测的不是"gate 说不"，而是"gate 说不之后，那个文件确实不存在"。
    """
    outside = Path(tempfile.mkdtemp(prefix="mcc-mcp-side-effect-"))
    note = outside / "note.txt"
    monkeypatch.setenv("MCP_FIXTURE_MARKER", str(note))
    servers = (
        {
            "name": "fx",
            "endpoint": sys.executable,
            "args": server_arg(),
            "env": ["MCP_FIXTURE_MARKER"],
        },
    )
    script = [
        scripted_tool_calls([("mcp__fx__write_note", {"text": "这一行不该出现在盘上"})]),
        scripted_final_text("它被拒了。"),
    ]
    try:
        refused = assembled(mcp_servers=servers, responses=script, confirmer=lambda _n, _s: Answer.NO)
        refused.agent.run("让外部工具记一条")
        assert not note.exists(), "被拒的调用还是落了盘 —— 闸门是装饰"
        # 被拒的那一次**不该有** tool_call：账上出现一条"没执行成功的调用"，
        # 与"根本没进执行分支"是两件事，离线判据（denied 占比）靠 permission 记录算。
        assert [r for r in _trace(refused) if r.get("kind") == "tool_call"] == []
        denied = [
            r for r in _trace(refused) if r.get("kind") == "permission" and r.get("tool") == "mcp__fx__write_note"
        ]
        assert len(denied) == 1 and denied[0]["decision"] == "deny", "拒了却账上无痕，等于没拒"

        # 同一支工具、同一个门：点了同意就真的写进去了 —— 排除"这条路本来就通不了"
        granted = assembled(
            mcp_servers=servers,
            responses=[
                scripted_tool_calls([("mcp__fx__write_note", {"text": "这一行应当出现"})]),
                scripted_final_text("写好了。"),
            ],
            confirmer=lambda _n, _s: Answer.ONCE,
        )
        granted.agent.run("让外部工具记一条")
        approved = [
            record
            for record in _trace(granted)
            if record.get("kind") == "tool_call" and record.get("name") == "mcp__fx__write_note"
        ]
        assert len(approved) == 1 and approved[0]["ok"] is True
        assert note.read_text(encoding="utf-8").splitlines() == ["这一行应当出现"]
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def _trace(session: Any) -> list[dict[str, Any]]:
    lines = session.tracer.path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# --------------------------------------------------------------- 泄密路径


def test_secrets_in_server_args_never_reach_the_trace(assembled: Any) -> None:
    """`--token sk-…` 写在 args 里时，`_scrub` 按**值**形状认密钥，这里要按**位置**也认。"""
    secret = "sk-live-abcdefghijklmnop"
    session = assembled(
        mcp_servers=(
            {
                "name": "fx",
                "endpoint": sys.executable,
                "args": server_arg() + ["--token", secret, "--api-key=" + secret],
            },
        )
    )
    dumped = json.dumps(session.config.redacted(), ensure_ascii=False)
    assert secret not in dumped
    assert session.config.mcp_servers[0]["args"][3] == secret  # 配置本身照原样留着，只是不外传
    trace = session.tracer.path.read_text(encoding="utf-8")
    assert secret not in trace
    assert "已脱敏" in trace
    assert "fx" in trace  # 名字照留，否则查不到连的是谁


def test_redacted_reports_server_names_only(tmp_path: Path) -> None:
    cfg = make_config(
        tmp_path,
        mcp_servers=({"name": "kb", "endpoint": "python", "args": ["s.py", "pass", "hunter2"]},),
    )
    view = cfg.redacted()
    assert "hunter2" not in json.dumps(view, ensure_ascii=False)
    assert view["mcp_servers"] == ["kb"]


# --------------------------------------------------------------- 斜杠命令


def test_skills_command_shows_resident_and_deferred_cost(assembled: Any) -> None:
    body = "长正文。\n" * 200
    session = assembled()
    add_skill(session.config.project_root, "big-skill", body=body)
    session.skills = SkillLoader(session.config.skills_root)
    text = handle_command(session, "/skills")
    assert "常驻" in text and "demo-skill" in text
    assert len(text) < len(body)  # 面板报数，不把正文搬进来


def test_tools_command_shows_the_remote_risk_level(assembled: Any) -> None:
    listing = handle_command(session := assembled(), "/tools")
    row = next(line for line in listing.splitlines() if "mcp__fx__echo" in line)
    assert "execute" in row


# --------------------------------------------------------------- mcc mcp / mcc skills


def capture(capfd: Any, call: Any) -> tuple[int, str, str]:
    code = call()
    out, err = capfd.readouterr()
    return code, out, err


def test_mcp_entry_exits_zero_only_when_every_server_handshakes(tmp_path: Path, capfd: Any) -> None:
    cfg = make_config(tmp_path, mcp_servers=({"name": "fx", "endpoint": sys.executable, "args": server_arg()},))
    code, out, _ = capture(capfd, lambda: ext_cmd.mcp_entry([], cfg))
    assert code == 0
    assert "采信 execute" in out and "远端自报 read" in out
    assert "✗" in out  # 坏工具逐条列原因，不是"少了一个"就完事


def test_mcp_entry_exit_code_1_when_a_server_is_down(tmp_path: Path, capfd: Any) -> None:
    cfg = make_config(
        tmp_path,
        mcp_servers=({"name": "dead", "endpoint": sys.executable, "args": server_arg("exit")},),
    )
    code, out, _ = capture(capfd, lambda: ext_cmd.mcp_entry(["--json"], cfg))
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False and payload["servers"][0]["error"]


def test_mcp_entry_distinguishes_unconfigured_from_broken(tmp_path: Path, capfd: Any) -> None:
    code, out, _ = capture(capfd, lambda: ext_cmd.mcp_entry([], make_config(tmp_path, mcp_servers=())))
    assert code == 0 and "没有配置任何 MCP 服务" in out

    cfg = make_config(tmp_path, mcp_servers=({"name": "fx", "transport": "sse", "endpoint": "x"},))
    code, _, err = capture(capfd, lambda: ext_cmd.mcp_entry([], cfg))
    assert code == 2 and "transport" in err  # 配错 ≠ 没配，两者报法必须不同


def test_skills_entry_prices_the_lazy_loading(tmp_path: Path, capfd: Any) -> None:
    add_skill(tmp_path, "one", body="字" * 900)
    add_skill(tmp_path, "two", body="字" * 100)
    (tmp_path / "skills" / "broken").mkdir()
    code, out, _ = capture(capfd, lambda: ext_cmd.skills_entry([], make_config(tmp_path)))
    assert code == 0
    assert "不常驻省下来的那部分" in out
    assert "没有 SKILL.md" in out  # 被拒的也要列，不然"为什么少一个"没人答
    assert "正文 900 字符" in out


def test_skills_entry_json_lists_rejections(tmp_path: Path, capfd: Any) -> None:
    add_skill(tmp_path, "one", body="正文")
    (tmp_path / "skills" / "broken").mkdir()
    code, out, _ = capture(capfd, lambda: ext_cmd.skills_entry(["--json"], make_config(tmp_path)))
    payload = json.loads(out)
    assert code == 0 and payload["skills"] == 1
    assert payload["rejected"] == [{"dir": "broken", "reason": "没有 SKILL.md"}]


def test_root_flag_resolves_skills_relative_to_it(tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    add_skill(other)
    cfg = make_config(tmp_path / "untouched")
    assert ext_cmd._skills_root(cfg, other) == (other / "skills").resolve()
    assert ext_cmd._skills_root(cfg, None) == cfg.skills_root


def test_main_dispatches_the_two_subcommands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: Any) -> None:
    from miniclaude.cli.main import main

    # cwd 必须挪走：`Config.from_env()` 会读 `cwd/.env`，开发机上那份 `.env` 里配着
    # 真实的 MCP_SERVERS，于是这个"没配 MCP 就该报未配置"的断言在开发机上必红、
    # 在 CI 上恒绿 —— 绿灯是环境送的，不是这条断言挣的。
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_BASE_URL", "https://mock.local/v1")
    monkeypatch.setenv("LLM_MODEL", "mock-model")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-abcdefghijklmn")
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("MCP_SERVERS", raising=False)
    add_skill(tmp_path, "one", body="字")
    assert main(["skills"]) == 0
    assert "one" in capfd.readouterr().out
    assert main(["mcp"]) == 0
    assert "没有配置任何 MCP 服务" in capfd.readouterr().out
