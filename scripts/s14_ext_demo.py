"""S14 验收证据生成器（SPEC v2 §3.7 · §7.1 行 14）。

行 14 的原话是「接一个真实 MCP server 跑通，6 项安全测试绿」。测试绿只说明**写测试的
那个人**相信这些判据；这份文件把同一批判据在真子进程上再量一遍，把数字连同「没量到的
部分」一起落到 `eval/results/` 下，供 README 与 ledger 引用。

三件事决定了这里能证到什么：

- **对端有两个。** `tests/fixtures/mcp_fixture_server.py` 是我们自己写的、故意使坏的那一个
  （冒充本地工具名、自报只读、被拒之后其实会往工作区外落盘）；`tests/fixtures/mcp_sdk_server.py`
  是**官方 SDK 写的**那一个 —— 报文形状从此不由我们说了算。SPEC 那句「真实 MCP server」
  只有后者够格；前者证的是「坏远端也接得住」。两条都跑，各自标各自证了什么。
- **引擎是 fake。** 这一段测的是接线与安全边界，不是模型能力；换成真端点只会把结论换成噪声。
- **「被拒 = 没有副作用」靠盘上有没有那个文件来判**，不靠日志。`write_note` 就是为此存在的。

用法：
  PYTHONPATH="src;demos" python -X utf8 scripts/s14_ext_demo.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "demos"), str(ROOT / "scripts")]

from _evidence import write_evidence  # noqa: E402
from fakes import FakeLLM, scripted_final_text, scripted_tool_calls  # noqa: E402
from miniclaude.agent.permissions import Answer, PermissionGate, PermissionMode  # noqa: E402
from miniclaude.cli.main import build_session  # noqa: E402
from miniclaude.cli.render import Renderer  # noqa: E402
from miniclaude.config import Config  # noqa: E402
from miniclaude.ext import MCPBridge, MCPServerSpec, SkillLoader  # noqa: E402
from miniclaude.ext.skills import LoadSkillTool  # noqa: E402
from miniclaude.messages import ToolUseBlock  # noqa: E402
from miniclaude.tools.registry import ToolRegistry  # noqa: E402
from miniclaude.tools.workspace import Workspace  # noqa: E402
from miniclaude.tools.write_file import WriteFileTool  # noqa: E402

WORK = ROOT / "eval" / ".work" / "s14-ext"
RESULT = ROOT / "eval" / "results" / "s14-mcp-skills.json"
FIXTURE = ROOT / "tests" / "fixtures" / "mcp_fixture_server.py"
SDK_SERVER = ROOT / "tests" / "fixtures" / "mcp_sdk_server.py"
HAS_SDK = importlib.util.find_spec("mcp") is not None

SECRET = "sk-live-abcdefghijklmnop"
MARKER_ENV = "MCP_FIXTURE_MARKER"
LEAK_KEY = "LLM_API_KEY"


def fixture_spec(mode: str = "good", *, name: str = "fx", env: tuple[str, ...] = ()) -> MCPServerSpec:
    return MCPServerSpec(name=name, endpoint=sys.executable, args=(str(FIXTURE), mode), env=env)


def workspace_at(root: Path, name: str) -> Workspace:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    return Workspace(directory, output_limit=30_000)


def call(tool: Any, **args: Any) -> Any:
    return tool.invoke(ToolUseBlock(id="t1", name=tool.name, input=args))


def records_of(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ------------------------------------------------------------------ 发现与握手


def measure_discovery(root: Path) -> dict[str, Any]:
    """四个模式各起一次真进程：能起的要起得来，起不来的要留下原因，且不留孤儿进程。"""
    out: dict[str, Any] = {}

    def one(name: str, mode: str, *, timeout: float = 30.0, env: tuple[str, ...] = ()) -> None:
        bridge = MCPBridge(
            servers=[fixture_spec(mode, env=env)],
            workspace=workspace_at(root, f"discovery-{name}"),
            timeout=timeout,
        )
        try:
            tools = bridge.discover()
            entry = bridge.stats()["servers"][0]
            out[name] = {
                "mode": mode,
                "tools": sorted(tool.name for tool in tools),
                "adopted": {
                    tool.name: {
                        "risk": tool.risk_level.value,
                        "declared": tool.declared_risk,
                        "external": tool.external,
                    }
                    for tool in tools
                },
                "ok": entry["ok"],
                "error": entry.get("error", ""),
                "server_info": entry.get("server_info", {}),
                "skipped": [item["reason"] for item in entry["skipped"]],
            }
        finally:
            bridge.close()
            # 只在 close 之后量：close 之前当然是开着的，那一格数的就不是"有没有孤儿进程"
            out[name]["children_after_close"] = len(bridge._sessions)

    one("good", "good")
    one("shadow", "shadow")
    one("dead", "exit")
    one("silent", "silent", timeout=3.0)
    one("garbage", "garbage")

    if not HAS_SDK:
        out["sdk"] = {"ok": False, "error": "没装官方 mcp SDK（dev extra）：pip install -e .[dev]"}
        return out

    # 官方 SDK 那一个：报文由第三方实现产生，我们这侧一行都不参与
    bridge = MCPBridge(
        servers=[MCPServerSpec(name="sdk", endpoint=sys.executable, args=(str(SDK_SERVER),))],
        workspace=workspace_at(root, "discovery-sdk"),
        timeout=60.0,
    )
    try:
        tools = {tool.name: tool for tool in bridge.discover()}
        entry = bridge.stats()["servers"][0]
        echo = tools["mcp__sdk__sdk_echo"]
        out["sdk"] = {
            "ok": entry["ok"],
            "error": entry.get("error", ""),
            "server_info": entry.get("server_info", {}),
            "tools": sorted(tools),
            "adopted": {
                name: {
                    "risk": tool.risk_level.value,
                    "declared": tool.declared_risk,
                    "external": tool.external,
                }
                for name, tool in tools.items()
            },
            "schema_from_sdk": sorted(echo.input_schema["properties"]),
            "unicode_round_trip": call(echo, text="你好 MCP", times=2).content,
            "remote_failure_is_error": call(tools["mcp__sdk__sdk_boom"]).is_error,
        }
    finally:
        bridge.close()
        out["sdk"]["children_after_close"] = len(bridge._sessions)
    return out


def measure_env_boundary(root: Path) -> dict[str, Any]:
    """点名要 `LLM_API_KEY`：看它是不是真的没进那个进程，以及剔除有没有留痕。"""
    os.environ[LEAK_KEY] = "sk-leak-check-0000000000"
    os.environ["MCP_FIXTURE_NAMED"] = "点名了才给"
    bridge = MCPBridge(
        servers=[fixture_spec(env=(LEAK_KEY, "MCP_FIXTURE_NAMED", "MCP_DEFINITELY_NOT_SET_ANYWHERE"))],
        workspace=workspace_at(root, "env"),
        timeout=30.0,
    )
    try:
        tools = {tool.name: tool for tool in bridge.discover()}
        seen = json.loads(call(tools["mcp__fx__probe"]).content.splitlines()[0])
        entry = bridge.stats()["servers"][0]
        return {
            "asked_for": [LEAK_KEY, "MCP_FIXTURE_NAMED", "MCP_DEFINITELY_NOT_SET_ANYWHERE"],
            "child_sees": seen,
            "dropped_env": entry.get("dropped_env", []),
            "named_env_delivered": seen.get("named") == "点名了才给",
        }
    finally:
        bridge.close()
        os.environ.pop(LEAK_KEY, None)
        os.environ.pop("MCP_FIXTURE_NAMED", None)


def measure_shadow(root: Path) -> dict[str, Any]:
    """硬要求①最强的一臂：远端逐字报出 `read_file`。注册之后本地那一格得还是本地的。"""
    ws = workspace_at(root, "shadow")
    (ws.root / "target.txt").write_text("本地才有的内容", encoding="utf-8")
    bridge = MCPBridge(servers=[fixture_spec("shadow")], workspace=ws, timeout=30.0)
    try:
        tools = bridge.discover()
        registry = ToolRegistry.default(ws, extra_tools=tools)
        local = registry.get("read_file")
        impostor = registry.get("mcp__fx__read_file")
        mine = call(local, path="target.txt")
        theirs = call(impostor, path="target.txt")
        return {
            "advertised_by_remote": ["read_file", "write_file"],
            "registered_as": sorted(tool.name for tool in tools),
            "local_still_reads_disk": (not mine.is_error) and "本地才有的内容" in mine.content,
            "impostor_says_it_is_remote": "远端的 read_file" in theirs.content,
            "impostor_cannot_see_disk": "本地才有的内容" not in theirs.content,
            "local_external": local.external,
            "local_risk": local.risk_level.value,
            "remote_risk": impostor.risk_level.value,
            "registry_size": len(registry.names()),
        }
    finally:
        bridge.close()


def measure_gate(root: Path) -> dict[str, Any]:
    """三种模式下的判定，外加一个对照组：AUTO 放行本地写，否则这条安全规则就成了「谁都被问」。"""
    ws = workspace_at(root, "gate")
    bridge = MCPBridge(servers=[fixture_spec()], workspace=ws, timeout=30.0)
    loader = SkillLoader(ROOT / "skills")
    rows: dict[str, Any] = {}
    try:
        tools = {tool.name: tool for tool in bridge.discover()}
        remote = tools["mcp__fx__echo"]
        local = WriteFileTool(ws)
        skill = LoadSkillTool(ws, loader)
        skill_name = loader.manifests()[0].name
        for mode in PermissionMode:
            asked: list[str] = []

            def confirm(name: str, _summary: str) -> Answer:
                asked.append(name)
                return Answer.NO

            gate = PermissionGate(workspace=ws, mode=mode, confirmer=confirm)
            decision, reason = gate.check(remote, {"text": "hi"})
            allowed, why = gate.authorize(remote, {"text": "hi"})
            rows[mode.value] = {
                "external_decision": decision.value,
                "external_reason": reason,
                "authorize_after_refusal": allowed,
                "authorize_reason": why,
                "confirmer_called_with": asked,
                "local_write": gate.check(local, {"path": "a.py", "content": "x"})[0].value,
                "load_skill": gate.check(skill, {"name": skill_name})[0].value,
            }
        rows["adopted_risks"] = sorted({tool.risk_level.value for tool in tools.values()})
        rows["declared_risks"] = sorted({tool.declared_risk for tool in tools.values() if tool.declared_risk})
        # 「没有人可问」那一臂：confirmer=None。问到空气时必须是拒绝，不能是放行
        nobody = PermissionGate(workspace=ws, mode=PermissionMode.AUTO)
        allowed, why = nobody.authorize(remote, {"text": "hi"})
        rows["no_confirmer"] = {"authorized": allowed, "reason": why}
        return rows
    finally:
        bridge.close()


# ------------------------------------------------------------------ 跑一轮真循环


def make_config(root: Path, *, servers: tuple[dict[str, Any], ...], trace: Path) -> Config:
    return Config(
        base_url="https://mock.local/v1",
        api_key=SECRET,
        model="mock-model",
        project_root=root,
        trace_path=trace,
        mcp_servers=servers,
        bash_timeout=20,
    )


def add_skill(root: Path, name: str = "demo-skill") -> None:
    entry = root / "skills" / name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 证据脚本用的技能\n---\n第一步：读一眼再动手。\n",
        encoding="utf-8",
    )


def run_arm(name: str, servers: tuple[dict[str, Any], ...], note: Path, *, mode: PermissionMode, answer: Answer, text: str) -> dict[str, Any]:
    """一次真装配 + 一轮真循环。`answer` 是唯一变量时，两臂之差就是「人点没点同意」。"""
    root = WORK / name
    root.mkdir(parents=True, exist_ok=True)
    add_skill(root)
    note.unlink(missing_ok=True)
    asked: list[str] = []
    session = build_session(
        config=make_config(root, servers=servers, trace=root / ".trace.jsonl"),
        renderer=Renderer(write=lambda _t: None, use_rich=False),
        llm=FakeLLM(
            [
                scripted_tool_calls([("mcp__fx__write_note", {"text": text})]),
                scripted_final_text("收尾。"),
            ]
            if mode is not PermissionMode.READONLY
            else [scripted_final_text("只读，不跑工具")]
        ),
        mode=mode,
        confirmer=lambda tool_name, _summary: (asked.append(tool_name), answer)[1],
    )
    try:
        session.agent.run("让外部工具记一条")
    finally:
        children_before_close = session.mcp.available() if session.mcp is not None else []
        session.close()
    trace = session.tracer.path
    records = records_of(trace)
    text_of_trace = trace.read_text(encoding="utf-8")
    system = session.agent.current_system()
    return {
        "mode": mode.value,
        "confirmer_called_with": asked,
        "note_written": note.exists(),
        "note_lines": note.read_text(encoding="utf-8").splitlines() if note.exists() else [],
        "permission_decisions": [
            {"tool": r.get("tool"), "decision": r.get("decision")}
            for r in records
            if r.get("kind") == "permission" and str(r.get("tool", "")).startswith("mcp__")
        ],
        "tool_calls": [{"name": r.get("name"), "ok": r.get("ok")} for r in records if r.get("kind") == "tool_call"],
        "trace_kinds": sorted({str(r.get("kind")) for r in records}),
        "mcp_event": next((r for r in records if r.get("kind") == "mcp"), None),
        "skills_event": next((r for r in records if r.get("kind") == "skills"), None),
        "secret_in_trace": SECRET in text_of_trace,
        "masked_marker_in_trace": "已脱敏" in text_of_trace,
        "secret_in_redacted": SECRET in json.dumps(session.config.redacted(), ensure_ascii=False),
        "redacted_servers": session.config.redacted()["mcp_servers"],
        "bridge_built": session.mcp is not None,
        "external_tools_in_registry": [n for n in session.agent.registry.names() if n.startswith("mcp__")],
        "children_running_during_session": children_before_close,
        "children_after_close": session.mcp.available() if session.mcp is not None else [],
        "system_has_external_note": "外部工具（MCP）" in system,
        "system_has_catalog": "可用技能" in system,
        "catalog_costs_body_out": "第一步：读一眼再动手" not in system,
    }


def measure_loop(root: Path) -> dict[str, Any]:
    """三臂 + 一次坏参数。全部在真子进程上跑。"""
    outside = root / "outside-workspace"
    outside.mkdir(parents=True, exist_ok=True)
    note = outside / "note.txt"
    os.environ[MARKER_ENV] = str(note)
    servers = (
        {
            "name": "fx",
            "endpoint": sys.executable,
            "args": [str(FIXTURE), "good", "--token", SECRET],
            "env": [MARKER_ENV],
        },
    )
    try:
        refused = run_arm("refused", servers, note, mode=PermissionMode.AUTO, answer=Answer.NO, text="这一行不该出现在盘上")
        granted = run_arm("granted", servers, note, mode=PermissionMode.AUTO, answer=Answer.ONCE, text="这一行应当出现")
        readonly = run_arm("readonly", servers, note, mode=PermissionMode.READONLY, answer=Answer.ONCE, text="")

        # 参数校验在上线之前：坏参数连管道都不该碰
        bad_ws = workspace_at(root, "badargs")
        bridge = MCPBridge(servers=[fixture_spec(env=(MARKER_ENV,))], workspace=bad_ws, timeout=30.0)
        try:
            tools = {t.name: t for t in bridge.discover()}
            note.unlink(missing_ok=True)
            bad = call(tools["mcp__fx__write_note"], text=12)
            still_alive = call(tools["mcp__fx__echo"], text="校验失败之后连接还在")
            bad_args = {
                "is_error": bad.is_error,
                "reason": bad.content.strip(),
                "note_written": note.exists(),
                "wire_still_usable": still_alive.content == "校验失败之后连接还在",
            }
        finally:
            bridge.close()
        return {"refused": refused, "granted": granted, "readonly": readonly, "bad_arguments": bad_args}
    finally:
        os.environ.pop(MARKER_ENV, None)
        shutil.rmtree(outside, ignore_errors=True)


# ------------------------------------------------------------------ 技能的经济性


def measure_skills(root: Path) -> dict[str, Any]:
    """延迟加载那笔账：常驻段的长度只跟技能**个数**有关，跟正文长度无关。"""
    shipped = SkillLoader(ROOT / "skills")
    rows = shipped.manifests()
    catalog = shipped.catalog()
    shipped_stats = {
        **shipped.stats(),
        "root": str(ROOT / "skills"),
        "bodies": {row.name: row.body_chars for row in rows},
        "every_name_listed": all(row.name in catalog for row in rows),
        "no_body_in_catalog": all(
            row.path.read_text(encoding="utf-8").splitlines()[-1].strip() not in catalog for row in rows
        ),
    }

    lab = root / "skills" / "huge-skill"
    lab.mkdir(parents=True, exist_ok=True)
    big_body = "这是一条很长很长的指令。\n" * 400
    (lab / "SKILL.md").write_text(
        "---\nname: huge-skill\ndescription: 用来量常驻与展开之间那道差的胖技能\n---\n" + big_body,
        encoding="utf-8",
    )
    before = SkillLoader(root / "skills").stats()
    loaded = SkillLoader(root / "skills").load("huge-skill")
    second = root / "skills" / "second-skill"
    second.mkdir()
    (second / "SKILL.md").write_text(
        "---\nname: second-skill\ndescription: 第二个\n---\n短正文。\n", encoding="utf-8"
    )
    after = SkillLoader(root / "skills").stats()
    return {
        "shipped": shipped_stats,
        "experiment": {
            "big_body_chars": len(big_body),
            "loaded_chars": len(loaded),
            "catalog_chars_one_skill": before["catalog_chars"],
            "catalog_lines_one_skill": before["catalog_lines"],
            "catalog_chars_two_skills": after["catalog_chars"],
            "catalog_lines_two_skills": after["catalog_lines"],
            "row_cost_when_body_grows_by_8800": after["catalog_chars"] - before["catalog_chars"],
            "resident_over_expanded": round(before["catalog_chars"] / len(loaded), 4),
        },
    }


def measure_assembly_path() -> dict[str, Any]:
    """「不新增第二条装配路径」是可以静态量的：src 里真有一个 `ToolRegistry.default(...)` 调用点，且只有一处。

    用 ast 而不是 grep：文档字符串里也在提这个名字（`ext/mcp.py` 的那段注释就是），
    按行匹配会得到三处，于是这条判据要么假失败，要么被人改掉过滤器变成假通过。
    """
    import ast

    hits: list[str] = []
    mentioned: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "default" or not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id == "ToolRegistry":
                hits.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
        for number, line in enumerate(source.splitlines(), start=1):
            if "ToolRegistry.default(" in line:
                mentioned.append(f"{path.relative_to(ROOT).as_posix()}:{number}")
    return {"call_sites": hits, "count": len(hits), "text_mentions": mentioned}


# ------------------------------------------------------------------ 汇总


def build_premises(
    discovery: dict[str, Any],
    env: dict[str, Any],
    shadow: dict[str, Any],
    gate: dict[str, Any],
    loop: dict[str, Any],
    skills: dict[str, Any],
    assembly: dict[str, Any],
) -> list[dict[str, Any]]:
    good = discovery["good"]
    sdk = discovery.get("sdk", {})
    declared = set(gate["declared_risks"])
    shipped = skills["shipped"]
    exp = skills["experiment"]
    third_party = sdk.get("ok") is True if HAS_SDK else None
    if HAS_SDK:
        info = sdk.get("server_info") or {}
        third_party_detail = (
            f"{info.get('name')} @ {info.get('protocolVersion')} → {sdk.get('tools')}"
        )
    else:
        third_party_detail = f"未量：{sdk.get('error')}"
    premises: list[dict[str, Any]] = [
        {
            "claim": "接一个真实 MCP server 跑通（官方 SDK 写的那个：握手 + tools/list + tools/call）",
            "ok": third_party and len(sdk.get("tools", [])) == 3,
            "detail": third_party_detail,
        },
        {
            "claim": "inputSchema 由第三方实现产生，参数名照原样进到我们这套校验里",
            "ok": set(sdk.get("schema_from_sdk", [])) == {"text", "times"} if HAS_SDK else None,
            "detail": f"sdk_echo 的 properties = {sdk.get('schema_from_sdk')}",
        },
        {
            "claim": "中文在 stdio 上来回无损（Windows 默认 cp936 那一刀已经在两端挡掉）",
            "ok": str(sdk.get("unicode_round_trip", "")).count("你好 MCP") == 2 if HAS_SDK else None,
            "detail": f"回话 = {sdk.get('unicode_round_trip')!r}",
        },
        {
            "claim": "远端自报的风险一律不采信：读得到、只展示、采信值恒为 execute",
            "ok": all(row["risk"] == "execute" for row in good["adopted"].values())
            and bool(declared)
            and "execute" not in declared
            and all(row["risk"] == "execute" for row in (sdk.get("adopted") or {}).values()),
            "detail": f"自报 {sorted(declared)} → 采信 {gate['adopted_risks']}；SDK 那臂 {sorted(set(row['declared'] for row in (sdk.get('adopted') or {}).values()))}",
        },
        {
            "claim": "命名空间前缀挡得住顶名：远端逐字报 read_file / write_file 也抢不走本地那一格",
            "ok": shadow["registered_as"] == ["mcp__fx__read_file", "mcp__fx__write_file"]
            and shadow["local_still_reads_disk"]
            and shadow["impostor_says_it_is_remote"]
            and shadow["impostor_cannot_see_disk"],
            "detail": (
                f"注册名 {shadow['registered_as']} · 本地 read_file 仍读到盘上内容 = {shadow['local_still_reads_disk']}"
                f" · 冒名者拿不到本地内容 = {shadow['impostor_cannot_see_disk']}（registry 共 {shadow['registry_size']} 格）"
            ),
        },
        {
            "claim": "AUTO 模式下外部工具仍然要问，而本地写照样放行（§6.3-1）",
            "ok": gate["auto"]["external_decision"] == "ask"
            and gate["auto"]["local_write"] == "allow"
            and "外部工具" in gate["auto"]["external_reason"],
            "detail": f"auto：外部 {gate['auto']['external_decision']} / 本地写 {gate['auto']['local_write']} · 理由「{gate['auto']['external_reason']}」",
        },
        {
            "claim": "没有确认渠道时保守拒绝，绝不擅自执行",
            "ok": gate["no_confirmer"]["authorized"] is False and "确认" in gate["no_confirmer"]["reason"],
            "detail": f"confirmer=None 的 AUTO 门：authorize = {gate['no_confirmer']['authorized']}，理由「{gate['no_confirmer']['reason']}」",
        },
        {
            "claim": "ask 问、readonly 拒，而 load_skill 是只读级（无人值守跑批不会卡在它上面）",
            "ok": gate["ask"]["external_decision"] == "ask"
            and gate["readonly"]["external_decision"] == "deny"
            and gate["auto"]["load_skill"] == "allow",
            "detail": (
                f"ask={gate['ask']['external_decision']} · readonly={gate['readonly']['external_decision']}"
                f" · load_skill(auto)={gate['auto']['load_skill']}"
            ),
        },
        {
            "claim": "被拒的外部调用在工作区之外没留下任何文件（判盘，不判日志）",
            "ok": loop["refused"]["note_written"] is False
            and loop["refused"]["confirmer_called_with"] == ["mcp__fx__write_note"]
            and loop["refused"]["tool_calls"] == []
            and loop["refused"]["permission_decisions"] == [{"tool": "mcp__fx__write_note", "decision": "deny"}],
            "detail": (
                f"确认弹窗 {loop['refused']['confirmer_called_with']} → 落盘 = {loop['refused']['note_written']} · "
                f"tool_call 记录 {loop['refused']['tool_calls']}（拒绝不进执行分支，账上只有 permission 那条 deny）"
            ),
        },
        {
            "claim": "同一支工具点了同意就真的执行 —— 排除「这条路本来就通不了」那种假安全",
            "ok": loop["granted"]["note_written"] is True
            and loop["granted"]["note_lines"] == ["这一行应当出现"]
            and loop["granted"]["tool_calls"] == [{"name": "mcp__fx__write_note", "ok": True}],
            "detail": f"调用记录 {loop['granted']['tool_calls']} · 盘上内容 {loop['granted']['note_lines']}",
        },
        {
            "claim": "只读模式连远端进程都不起：没有副作用是结构性的，不是判出来的",
            "ok": loop["readonly"]["bridge_built"] is False
            and loop["readonly"]["external_tools_in_registry"] == []
            and loop["readonly"]["note_written"] is False
            and bool(loop["readonly"]["mcp_event"].get("skip_reason"))
            and "running" not in loop["readonly"]["mcp_event"],
            "detail": (
                f"registry 里的 mcp__ 工具 = {loop['readonly']['external_tools_in_registry']} · "
                f"起了的进程 = {loop['readonly']['children_running_during_session']} · "
                f"skip_reason「{loop['readonly']['mcp_event'].get('skip_reason')}」"
            ),
        },
        {
            "claim": "BaseTool.invoke() 的校验链路在上管道之前：坏参数不落盘、也不弄坏连接",
            "ok": loop["bad_arguments"]["is_error"]
            and loop["bad_arguments"]["note_written"] is False
            and loop["bad_arguments"]["wire_still_usable"],
            "detail": (
                f"text=12 → 「{loop['bad_arguments']['reason']}」 · 落盘 = {loop['bad_arguments']['note_written']}"
                f" · 之后 echo 仍通 = {loop['bad_arguments']['wire_still_usable']}"
            ),
        },
        {
            "claim": "宿主 env 不外泄：密钥即使被点名也不透传，且剔除留痕；白名单外的名字不假装给了",
            "ok": env["child_sees"]["has_api_key"] is False
            and env["child_sees"]["has_pythonpath"] is False
            and env["child_sees"]["has_path"] is True
            and env["named_env_delivered"]
            and set(env["dropped_env"]) == {LEAK_KEY, "MCP_DEFINITELY_NOT_SET_ANYWHERE"},
            "detail": f"点名 {env['asked_for']} → 子进程可见 {env['child_sees']} · dropped_env {env['dropped_env']}",
        },
        {
            "claim": "配置 args 里的密钥既不进 trace 也不进 redacted()，但服务名照留",
            "ok": loop["granted"]["secret_in_trace"] is False
            and loop["granted"]["masked_marker_in_trace"] is True
            and loop["granted"]["secret_in_redacted"] is False
            and loop["granted"]["redacted_servers"] == ["fx"],
            "detail": (
                f"trace 里含明文 = {loop['granted']['secret_in_trace']} · 含「已脱敏」 = "
                f"{loop['granted']['masked_marker_in_trace']} · redacted 只留名字 {loop['granted']['redacted_servers']}"
            ),
        },
        {
            "claim": "坏远端带不走会话：起不来的报错、不答的超时、stdout 上的噪声，进程都收干净",
            "ok": discovery["dead"]["ok"] is False
            and "我不干" in discovery["dead"]["error"]
            and discovery["silent"]["ok"] is False
            and "超时" in discovery["silent"]["error"]
            and discovery["garbage"]["ok"] is True
            and all(
                item["children_after_close"] == 0
                for item in discovery.values()
                if isinstance(item, dict) and "children_after_close" in item
            ),
            "detail": (
                f"exit → 「{discovery['dead']['error']}」 · silent → 「{discovery['silent']['error']}」 · "
                f"garbage → {len(discovery['garbage']['tools'])} 个工具照样列出 · close 之后无残留进程"
            ),
        },
        {
            "claim": "坏工具条目逐条给理由并跳过，不是把整批带走，也不是洗个名字留下",
            "ok": len(good["skipped"]) == 4 and len(good["tools"]) == 4,
            "detail": f"收 {good['tools']} · 跳 {len(good['skipped'])} 条：{good['skipped']}",
        },
        {
            "claim": "技能目录的常驻成本与正文长度解耦（延迟加载那笔账）",
            "ok": exp["catalog_lines_one_skill"] == 3
            and exp["row_cost_when_body_grows_by_8800"] < 120
            and exp["resident_over_expanded"] < 0.1,
            "detail": (
                f"正文 {exp['big_body_chars']} 字符 → 目录 {exp['catalog_chars_one_skill']} 字符"
                f"（{exp['catalog_lines_one_skill']} 行）；再加一个技能只多 "
                f"{exp['row_cost_when_body_grows_by_8800']} 字符；常驻/展开 = {exp['resident_over_expanded']}"
            ),
        },
        {
            "claim": "随仓库发布的 skills/ 打得进 system，且正文不跟着进",
            "ok": shipped["skills"] >= 2
            and shipped["catalog_lines"] <= 30
            and shipped["every_name_listed"]
            and shipped["no_body_in_catalog"],
            "detail": (
                f"{shipped['skills']} 个技能 {shipped['names']} · 目录 {shipped['catalog_lines']} 行 / "
                f"{shipped['catalog_chars']} 字符 · 正文合计 {shipped['body_chars_total']} 字符 {shipped['bodies']}"
            ),
        },
        {
            "claim": "装配路径只有一条：src 里 ToolRegistry.default(...) 的调用点只有一个，外部工具走 extra_tools",
            "ok": assembly["count"] == 1
            and loop["granted"]["external_tools_in_registry"]
            and loop["granted"]["mcp_event"]["tools"] == 4,
            "detail": (
                f"调用点 {assembly['call_sites']}；另有 {len(assembly['text_mentions']) - 1} 处只是文档字符串提到 "
                f"· 那一臂注册了 {len(loop['granted']['external_tools_in_registry'])} 个 mcp__ 工具"
            ),
        },
        {
            "claim": "外部能力与技能各留一条装配期 trace 事件，事后查得到「这一轮问没问过人」",
            "ok": "mcp" in loop["granted"]["trace_kinds"]
            and "skills" in loop["granted"]["trace_kinds"]
            and loop["granted"]["skills_event"]["skills"] >= 1
            and loop["granted"]["system_has_external_note"]
            and loop["granted"]["system_has_catalog"]
            and loop["granted"]["catalog_costs_body_out"],
            "detail": (
                f"mcp = {_flat(loop['granted']['mcp_event'])} · skills = {_flat(loop['granted']['skills_event'])}"
            ),
        },
    ]
    return premises


def _flat(record: dict[str, Any]) -> dict[str, Any]:
    """只留这条事件自己的字段：信封（seq/session/trace_id/schema）每条都一样，摆在这里只会淹死数字。"""
    noise = {"kind", "ts", "span_id", "parent_span_id", "turn", "servers", "seq", "session", "trace_id", "schema_version"}
    return {key: value for key, value in record.items() if key not in noise}


def _metrics(payload: dict[str, Any]) -> dict[str, Any]:
    """盘上那份和新这份共用的尺：只量覆盖，不量结论。

    守卫管的是"这一条还量不量得出来"，不管"这一条成不成立" —— 判据真翻了红必须写得进去，
    那正是证据的存在理由。SDK 那一臂取决于**当前解释器**装没装官方 `mcp`，它掉线时
    premise 条数不变、三条一起变成未量（`ok: null`），所以比的是 `measured`。
    """
    premises = payload.get("premises", [])
    return {
        "premises": len(premises),
        "measured": sum(1 for item in premises if item.get("ok") is not None),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 S14 验收证据（MCP bridge + Skills）")
    parser.add_argument(
        "--allow-thinning",
        action="store_true",
        help="明知证据变薄也写盘（缩水条目会打到 stderr，SPEC/README 里欠一段说明）",
    )
    args = parser.parse_args()

    started = time.perf_counter()
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)

    discovery = measure_discovery(WORK)
    env = measure_env_boundary(WORK)
    shadow = measure_shadow(WORK)
    gate = measure_gate(WORK)
    loop = measure_loop(WORK)
    skills = measure_skills(WORK)
    assembly = measure_assembly_path()
    premises = build_premises(discovery, env, shadow, gate, loop, skills, assembly)

    failed = [item for item in premises if item["ok"] is False]
    unmeasured = [item for item in premises if item["ok"] is None]
    payload: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "acceptance": "S14 / §7.1 行 14",
        "spec": "§3.7 MCP bridge 与 Skills",
        "engine": "fake",
        "verdict": "fail" if failed else "pass",
        "wall_seconds": round(time.perf_counter() - started, 1),
        "peers": {
            "自写的敌意服务": str(FIXTURE.relative_to(ROOT)),
            "官方 SDK 服务": str(SDK_SERVER.relative_to(ROOT)) if HAS_SDK else "缺 dev extra，这一臂没跑",
        },
        "premises": premises,
        "counts": {
            "premises": len(premises),
            "passed": sum(1 for item in premises if item["ok"] is True),
            "failed": len(failed),
            "not_measured": len(unmeasured),
        },
        "measurements": {
            "discovery": discovery,
            "env_boundary": env,
            "shadow": shadow,
            "gate": gate,
            "loop": loop,
            "skills": skills,
            "assembly_path": assembly,
        },
        "amendments": AMENDMENTS,
        "what_this_proves": (
            "两条扩展接在同一个注册表、同一个权限门、同一份 trace 上；远端自报什么都不改变采信档位；"
            "拒绝一次外部调用之后，工作区之外那个文件确实不存在；密钥既没进子进程环境也没进 trace；"
            "技能目录的常驻成本与正文长度解耦。全部数字由本脚本产生。"
        ),
        "what_this_does_not_prove": (
            "① 模型在真端点下会不会滥用外部工具（引擎是 fake，剧本是我们写的）；"
            "② MCP 的 resources / prompts 两类能力，以及进度、取消、订阅（一行实现都没有）；"
            "③ 非 stdio 传输；④ 真实第三方生态的兼容面 —— SDK 那臂只有一个服务、三个工具；"
            "⑤ 多服务并发握手的耗时分布（现在是串行）。"
        ),
    }

    if not write_evidence(
        RESULT,
        payload,
        _metrics,
        exact=("premises", "measured"),
        allow_thinning=args.allow_thinning,
        note=(
            "SDK 那一臂要看当前解释器装没装官方 mcp：换错 python（没装 dev extra 的那个）"
            "就会把三条量过的变成未量。跑批用 .venv/Scripts/python.exe。"
        ),
    ):
        return 2

    marks = {True: "x", False: "!", None: "?"}
    for item in premises:
        print(f"  [{marks[item['ok']]}] {item['claim']}")
        print(f"      {item['detail']}")
    counts = payload["counts"]
    print(
        f"\n{counts['passed']}/{counts['premises']} 条量过并成立"
        f"（失败 {counts['failed']} · 未量 {counts['not_measured']}）· 用时 {payload['wall_seconds']}s"
    )
    print(f"结论：{payload['verdict'].upper()} · 结果写入 {RESULT.relative_to(ROOT)}")
    return 0 if payload["verdict"] == "pass" else 1


AMENDMENTS: list[dict[str, str]] = [
    {
        "item": "不依赖官方 SDK 运行时",
        "spec_said": "§3.7 只规定了 MCPBridge / RemoteTool 的接口",
        "as_built": "自己写的 stdio 客户端（约 190 行）；官方 SDK 只作为 dev extra 存在，用途是给对端",
        "why": "把 SDK 放进 runtime 依赖，等于把「能不能跑通」外包出去，而 §2.4 的依赖预算要的是核心功能零第三方。代价写清楚了：SDK 改了报文形状我们不会立刻知道，只能靠这条互操作臂手动重跑。",
    },
    {
        "item": "只有 stdio 传输",
        "spec_said": "MCPServerSpec 预留 transport 字段",
        "as_built": "transport 只接受 stdio，写 sse / http 在启动时报错",
        "why": "非 stdio 在 §7.4 顺位 2 砍掉。报错而不是忽略：「配了 sse 结果按 stdio 跑」比「配不上」难查得多。",
    },
    {
        "item": "MCPBridge 不接 gate 参数",
        "spec_said": "§3.7 代码片段写的是 MCPBridge(servers, gate)",
        "as_built": "MCPBridge(servers, workspace, timeout)，判定住在循环的权限门那一侧",
        "why": "bridge 只管传输与包装。判定放两处，就会出现「bridge 与 gate 各持一份档位真相」，与 §3.3 砍重复裁剪同一个理由。",
    },
    {
        "item": "trace 的 mcp 事件用 skip_reason 而不是 skipped",
        "spec_said": "§3.1 只要求「有一条装配期事件」",
        "as_built": "skipped = 被跳过的工具条数（int）；这一整条扩展没参与的原因另起名为 skip_reason（str）",
        "why": "同名字段两种类型会让 schema 契约失去意义，而那份契约是 S8 全部价值的来源。",
    },
    {
        "item": "技能不带脚本、不读同目录文件",
        "spec_said": "D20：只做提示词包",
        "as_built": "SKILL.md 旁边的文件只报名字（load 的返回值里那句「未加载」），既不读也不执行",
        "why": "一旦允许脚本，「技能」就是远程代码执行入口，D19 那套不采信逻辑要在第二条路上重做一遍。多文件引用在 §7.4 顺位 5 砍掉。",
    },
    {
        "item": "读的是规范字段名",
        "spec_said": "§3.7 写的是「远端声明的 risk_level」",
        "as_built": "官方 SDK 不给 risk_level，给的是 annotations.readOnlyHint / destructiveHint / openWorldHint；两种形状都读，全部只进 declared_risk 展示字段",
        "why": "互操作那一臂逼出来的：自写夹具时按 SPEC 读 risk_level 是「过得了自己的测试」，实际一个都读不到。",
    },
    {
        "item": "握手是同步且串行的",
        "spec_said": "未规定",
        "as_built": "discover() 逐个服务起进程、握手、tools/list；单次请求超时 30s",
        "why": "配 N 个服务就有最坏 30N 秒的启动等待，而这段时间会算进「首 token 延迟」。并发握手段在 §7.4 顺位 6。",
    },
    {
        "item": "远端的图片 / 资源块不渲染",
        "spec_said": "未规定",
        "as_built": "_decode_content 把它们变成一句「〈image 内容块，本客户端不渲染〉」",
        "why": "不能当没看见：模型必须知道有东西没给它，否则它会按「远端只说了这些」来推理。",
    },
]


if __name__ == "__main__":
    raise SystemExit(main())
