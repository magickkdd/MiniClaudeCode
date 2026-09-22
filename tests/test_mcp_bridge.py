"""`ext/mcp.py` 的契约测试（SPEC v2 §3.7、§6.1 的 MCP 层、§6.3-1）。

对端是一个**真的子进程**（`tests/fixtures/mcp_fixture_server.py`）：拿 MagicMock
当远端的话，"我们能不能把远端的话接住"这件事就永远没被证过 —— 而那恰恰是这一层
唯一有风险的部分。仍然零网络：只有管道。
"""

from __future__ import annotations

import json
import sys
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from miniclaude.agent.permissions import Answer, Decision, PermissionGate, PermissionMode
from miniclaude.ext import MCPBridge, MCPServerSpec, MCPSpecError, RemoteTool
from miniclaude.ext.mcp import SUPPORTED_TRANSPORTS, _decode_content, _declared_risk, parse_env, tool_name
from miniclaude.messages import ToolUseBlock
from miniclaude.tools.base import RiskLevel
from miniclaude.tools.registry import DuplicateToolError, ToolRegistry
from miniclaude.tools.workspace import Workspace

SERVER = Path(__file__).parent / "fixtures" / "mcp_fixture_server.py"


def spec(mode: str = "good", *, name: str = "fx", env: tuple[str, ...] = ()) -> MCPServerSpec:
    return MCPServerSpec(name=name, endpoint=sys.executable, args=(str(SERVER), mode), env=env)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path, output_limit=30_000)


@pytest.fixture
def bridge(ws: Workspace) -> Any:
    instance = MCPBridge(servers=[spec()], workspace=ws, timeout=25.0)
    yield instance
    instance.close()


def call(tool: RemoteTool, **args: Any) -> Any:
    return tool.invoke(ToolUseBlock(id="t1", name=tool.name, input=args))


# --------------------------------------------------------------- 契约一致性


def test_advertised_transports_match_the_bridge() -> None:
    """配置层那份名单与实现层那份必须一字不差（BACKEND_NAMES 的同一条路）。"""
    from miniclaude.config import MCP_TRANSPORTS

    assert MCP_TRANSPORTS == SUPPORTED_TRANSPORTS


def test_discovery_namespaces_and_skips_the_broken_ones(bridge: MCPBridge) -> None:
    tools = bridge.discover()
    assert sorted(tool.name for tool in tools) == [
        "mcp__fx__boom",
        "mcp__fx__echo",
        "mcp__fx__probe",
        "mcp__fx__write_note",
    ]
    entry = next(item for item in bridge.stats()["servers"] if item["name"] == "fx")
    reasons = {item["name"]: item["reason"] for item in entry["skipped"]}
    # 四条坏的一律"跳过 + 说明"，不是把整批带走，也不是洗个名字留下。
    # 名字非法那条记的是**原始**名：它没能组成 `mcp__` 名，编一个假的不如照抄。
    assert reasons["bad name"].startswith("远端工具名不合法")
    assert "inputSchema" in reasons["mcp__fx__no_schema"]
    assert "properties" in reasons["mcp__fx__bare_object"]
    assert "同名" in reasons["mcp__fx__echo"]


def test_missing_properties_is_skipped_because_coerce_would_silently_drop_it(ws: Workspace) -> None:
    """`_coerce()` 只放行 schema 点名的参数。

    这条单独立一个测试，是因为它是那种**两边都不报错**的错：远端少写一个
    `properties`，工具照样被发现、照样能调用成功，只是参数全没了。
    """
    bridge = MCPBridge(servers=[spec()], workspace=ws)
    problem, tool = bridge._wrap(spec(), {"name": "x", "inputSchema": {"type": "object"}}, set())
    assert tool is None and "properties" in (problem or {})["reason"]


def test_namespace_can_never_shadow_a_local_tool(bridge: MCPBridge, ws: Workspace) -> None:
    local = set(ToolRegistry.default(ws).names())
    remote = {tool.name for tool in bridge.discover()}
    assert remote and not (remote & local)
    assert all(name.startswith("mcp__fx__") for name in remote)


def test_name_collision_rejected_by_the_registry(bridge: MCPBridge, ws: Workspace) -> None:
    """重名注册必须启动就失败，而不是让后加载的那个悄悄盖掉前一个。"""
    tools = bridge.discover()
    registry = ToolRegistry.default(ws, extra_tools=tools)
    with pytest.raises(DuplicateToolError):
        registry.register(next(tool for tool in tools if tool.name == "mcp__fx__echo"))


def test_a_remote_named_exactly_like_a_local_tool_stays_behind_the_prefix(
    ws: Workspace, tmp_path: Path
) -> None:
    """硬要求①最强的那一臂：远端**逐字**报出 `read_file` / `write_file`。

    `test_namespace_can_never_shadow_a_local_tool` 只比对了名字集合；这里要比行为 ——
    注册之后叫 `read_file` 的那一格仍然读得到盘上的真文件，而 `mcp__fx__read_file`
    只能拿到远端那句假话。名字没盖住但调用被劫走，是一样的事故。
    """
    (tmp_path / "target.txt").write_text("本地才有的内容", encoding="utf-8")
    shadow = MCPBridge(servers=[spec("shadow")], workspace=ws, timeout=25.0)
    try:
        tools = shadow.discover()
        assert sorted(tool.name for tool in tools) == ["mcp__fx__read_file", "mcp__fx__write_file"]
        registry = ToolRegistry.default(ws, extra_tools=tools)
        local = registry.get("read_file")
        assert not local.external and local.risk_level is not RiskLevel.EXECUTE
        impostor = registry.get("mcp__fx__read_file")
        assert impostor.external and impostor.risk_level is RiskLevel.EXECUTE

        mine = local.invoke(ToolUseBlock(id="t1", name="read_file", input={"path": "target.txt"}))
        assert "本地才有的内容" in mine.content and not mine.is_error
        theirs = call(impostor, path="target.txt")
        assert "远端的 read_file" in theirs.content and "本地才有的内容" not in theirs.content
    finally:
        shadow.close()


# --------------------------------------------------------------- D19：风险


def test_remote_tool_default_execute_risk(bridge: MCPBridge) -> None:
    tools = {tool.name: tool for tool in bridge.discover()}
    echo = tools["mcp__fx__echo"]
    assert echo.risk_level is RiskLevel.EXECUTE
    # 远端按规范字段自报 readOnlyHint=true —— 这个值只进描述文字，不参与任何判定
    assert echo.declared_risk == "read"
    assert "未采信" in echo.description
    assert tools["mcp__fx__probe"].declared_risk == ""  # 没自报的，也不会被我们编一个出来
    assert all(tool.risk_level is RiskLevel.EXECUTE for tool in tools.values())
    assert all(tool.external for tool in tools.values())


@pytest.mark.parametrize(
    "item, wanted",
    [
        ({"annotations": {"readOnlyHint": True}}, "read"),  # 规范字段
        ({"annotations": {"destructiveHint": True}}, "destructive"),
        ({"riskLevel": "READ"}, "read"),  # 私有写法：大小写归一
        ({"riskLevel": "read", "annotations": {"readOnlyHint": True}}, "read"),  # 两处同话，不重复列
        ({"riskLevel": "read", "annotations": {"destructiveHint": True}}, "read+destructive"),
        ({"annotations": "不是对象"}, ""),  # 远端给什么都不能让我们崩
        ({}, ""),
    ],
)
def test_declared_risk_is_read_from_both_field_shapes(item: dict[str, Any], wanted: str) -> None:
    """自报档位有两种现实（规范字段与私有字段），都要读得出来 —— 但读出来只为展示。

    `read+destructive` 是合法输出：一个服务同时自称只读和破坏性，说明它的自报毫无价值，
    而这正是 D19 存在的原因。我们把它原样印在面板上，采信值仍是 EXECUTE。
    """
    assert _declared_risk(item) == wanted


@pytest.mark.parametrize("mode", [PermissionMode.ASK, PermissionMode.AUTO])
def test_external_tools_ask_in_every_interactive_mode(bridge: MCPBridge, ws: Workspace, mode: PermissionMode) -> None:
    """§6.3-1：AUTO 的承诺是"工作区内自动放行"，外部工具不在那个集合里。"""
    echo = next(tool for tool in bridge.discover() if tool.name == "mcp__fx__echo")
    gate = PermissionGate(workspace=ws, mode=mode)
    decision, reason = gate.check(echo, {"text": "hi"})
    assert decision is Decision.ASK
    if mode is PermissionMode.AUTO:
        # 这条理由是整个 §6.3-1 的落点：AUTO 不能把外部工具一起放过去
        assert "外部工具" in reason and "工作区" in reason
    # 没有确认渠道时保守拒绝，绝不擅自执行
    ok, why = gate.authorize(echo, {"text": "hi"})
    assert ok is False and "确认" in why


def test_auto_mode_still_runs_local_tools_untouched(bridge: MCPBridge, ws: Workspace) -> None:
    """对照组：同一种 AUTO 模式下本地写文件是放行的 —— 否则这条安全规则就成了"谁都被问"。"""
    from miniclaude.tools.write_file import WriteFileTool

    remote = next(tool for tool in bridge.discover() if tool.name == "mcp__fx__echo")
    local = WriteFileTool(ws)
    gate = PermissionGate(
        workspace=ws, mode=PermissionMode.AUTO, confirmer=lambda _name, _summary: Answer.ONCE
    )
    assert gate.check(local, {"path": "a.py", "content": "x"})[0] is Decision.ALLOW
    assert gate.check(remote, {"text": "x"})[0] is Decision.ASK
    ok, _ = gate.authorize(remote, {"text": "x"})
    assert ok is True  # 人点了同意就能跑


def test_readonly_denies_external_tools_before_any_spawn(bridge: MCPBridge, ws: Workspace) -> None:
    echo = next(tool for tool in bridge.discover() if tool.name == "mcp__fx__echo")
    gate = PermissionGate(workspace=ws, mode=PermissionMode.READONLY)
    assert gate.check(echo, {"text": "x"})[0] is Decision.DENY


# --------------------------------------------------------------- 环境与密钥


def test_host_env_does_not_leak_into_the_server(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    """子进程只看得见白名单。这条判据从 B6 的 docker 臂抄过来：宿主 env 一条都不该透传。"""
    monkeypatch.setenv("LLM_API_KEY", "sk-leak-check-0000000000")
    monkeypatch.setenv("MCP_FIXTURE_NAMED", "点名了才给")
    bridge = MCPBridge(servers=[spec(env=("LLM_API_KEY", "MCP_FIXTURE_NAMED"))], workspace=ws, timeout=25.0)
    try:
        tools = {tool.name: tool for tool in bridge.discover()}
        result = call(tools["mcp__fx__probe"])
        assert not result.is_error, result.content
        seen = json.loads(result.content.splitlines()[0])
        assert seen["has_api_key"] is False, "密钥类变量即使被点名也不该透传"
        assert seen["has_pythonpath"] is False
        assert seen["has_path"] is True
        assert seen["named"] == "点名了才给"
        entry = next(item for item in bridge.stats()["servers"] if item["name"] == "fx")
        assert entry["dropped_env"] == ["LLM_API_KEY"], "剔除必须留痕，不能静默"
    finally:
        bridge.close()


def test_args_that_look_like_secrets_are_masked_before_tracing() -> None:
    built = MCPServerSpec.from_mapping(
        {"name": "kb", "endpoint": "kb-server", "args": ["--token", "ghp_AAAAAAAAAAAAAAAAAAAA", "--index", "docs"]}
    )
    traced = built.as_trace()["args"]
    assert traced[0] == "--token"
    assert "ghp_" not in json.dumps(traced)
    assert traced[3:] == ["docs"]  # 只有被旗标点名的那一位被遮


def test_config_time_shape_rejection_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniclaude.config import Config, ConfigError

    for name, value in [("LLM_BASE_URL", "https://mock.invalid/v1"), ("LLM_MODEL", "mock"), ("LLM_API_KEY", "sk-x-000000000000")]:
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MCP_SERVERS", "{not json")
    with pytest.raises(ConfigError, match="JSON"):
        Config.from_env()
    monkeypatch.setenv("MCP_SERVERS", '[{"endpoint":"x"}]')
    with pytest.raises(ConfigError, match="name"):
        Config.from_env()
    monkeypatch.setenv(
        "MCP_SERVERS", '[{"name":"a","endpoint":"x"},{"name":"a","endpoint":"y"}]'
    )
    with pytest.raises(ConfigError, match="都叫"):
        Config.from_env()
    # 单个对象是最自然的写法，纠正成一项的数组而不是报错
    monkeypatch.setenv("MCP_SERVERS", '{"name":"a","endpoint":"x"}')
    assert Config.from_env().mcp_servers[0]["name"] == "a"


@pytest.mark.parametrize(
    "raw, match",
    [
        ({"name": "bad name", "endpoint": "x"}, "名字"),
        ({"name": "../etc", "endpoint": "x"}, "名字"),
        ({"name": "ok", "endpoint": ""}, "endpoint"),
        ({"name": "ok", "endpoint": "x", "transport": "sse"}, "transport"),
        ({"name": "ok", "endpoint": "x", "command": "y"}, "不认识"),
        ({"name": "ok", "endpoint": "x", "args": "y"}, "数组"),
    ],
)
def test_spec_rejects_hostile_values(raw: dict[str, Any], match: str) -> None:
    with pytest.raises(MCPSpecError, match=match):
        MCPServerSpec.from_mapping(raw)


# --------------------------------------------------------------- 调用与兜底


def test_echo_round_trip_and_truncation(tmp_path: Path) -> None:
    """远端输出和本地工具输出走同一个截断器 —— 外部工具不该有"想打多大打多大"的通道。"""
    ws = Workspace(tmp_path, output_limit=1200)
    bridge = MCPBridge(servers=[spec()], workspace=ws, timeout=25.0)
    try:
        echo = next(tool for tool in bridge.discover() if tool.name == "mcp__fx__echo")
        result = call(echo, text="哈" * 2000)
        assert not result.is_error, result.content
        assert len(result.content) < 2000 and "omitted" in result.content
    finally:
        bridge.close()


def test_remote_failure_is_a_tool_result_not_an_exception(bridge: MCPBridge) -> None:
    boom = next(tool for tool in bridge.discover() if tool.name == "mcp__fx__boom")
    result = call(boom)
    assert result.is_error and "远端自己失败了" in result.content


def test_bad_arguments_are_rejected_before_the_wire(bridge: MCPBridge) -> None:
    echo = next(tool for tool in bridge.discover() if tool.name == "mcp__fx__echo")
    result = echo.invoke(ToolUseBlock(id="t2", name=echo.name, input={"text": 12}))
    assert result.is_error and "参数校验失败" in result.content


def test_unknown_remote_method_becomes_an_error(bridge: MCPBridge) -> None:
    tools = bridge.discover()
    ghost = RemoteTool(
        bridge.workspace,
        bridge=bridge,
        server="fx",
        remote_name="ghost",
        input_schema={"type": "object", "properties": {}},
    )
    assert any(tool.name == "mcp__fx__echo" for tool in tools)
    result = call(ghost)
    assert result.is_error and "unknown tool" in result.content


def test_dead_server_is_reported_not_raised(ws: Workspace) -> None:
    bridge = MCPBridge(servers=[spec("exit")], workspace=ws, timeout=25.0)
    try:
        assert bridge.discover() == []
        entry = bridge.stats()["servers"][0]
        assert entry["ok"] is False and entry["error"]
        assert "我不干" in entry["error"]  # stderr 是唯一的诊断渠道，得带回来
    finally:
        bridge.close()


def test_silent_server_times_out_and_is_reaped(ws: Workspace) -> None:
    bridge = MCPBridge(servers=[spec("silent")], workspace=ws, timeout=3.0)
    try:
        assert bridge.discover() == []
        assert "超时" in bridge.stats()["servers"][0]["error"]
        assert bridge.available() == [], "超时的服务不能被留在跑着"
    finally:
        bridge.close()


def test_stdout_noise_does_not_derail_the_wire(ws: Workspace) -> None:
    bridge = MCPBridge(servers=[spec("garbage")], workspace=ws, timeout=25.0)
    try:
        echo = next(tool for tool in bridge.discover() if tool.name == "mcp__fx__echo")
        assert call(echo, text="还在").content == "还在"
    finally:
        bridge.close()


def test_close_lets_a_later_call_restart(ws: Workspace) -> None:
    """`_drop()` 之后必须能重开：一次超时不该让整个服务这一会话内报废。"""
    bridge = MCPBridge(servers=[spec()], workspace=ws, timeout=25.0)
    try:
        echo = next(tool for tool in bridge.discover() if tool.name == "mcp__fx__echo")
        assert call(echo, text="一").content == "一"
        bridge._drop("fx")
        assert call(echo, text="二").content == "二"
    finally:
        bridge.close()


@pytest.mark.parametrize(
    "payload, expected_error",
    [
        ("纯字符串", False),
        (None, False),
        ({"content": [{"type": "image", "data": "..."}]}, False),
        ({"content": [], "isError": True}, True),
        ({"structuredContent": {"a": 1}}, False),
    ],
)
def test_decode_content_survives_anything_the_remote_sends(payload: Any, expected_error: bool) -> None:
    result = _decode_content(payload)
    assert result.is_error is expected_error
    assert isinstance(result.content, str)


def test_tool_name_helper_is_the_single_origin() -> None:
    assert tool_name("kb", "search") == "mcp__kb__search"
    assert tool_name("kb", "search").count("__") == 2


def test_structured_content_is_not_duplicated_into_the_context() -> None:
    """官方 SDK 的 `-> str` 工具会把同一句话写在 text 与 structuredContent 两处。

    两份都送进上下文，就是让模型为同一句付两次 token —— 这一层的存在理由恰恰是
    "上下文是预算资源"。真有额外字段时不许被这条规则吞掉。
    """
    echoed = {"content": [{"type": "text", "text": "甲乙"}], "structuredContent": {"result": "甲乙"}}
    assert _decode_content(echoed).content == "甲乙"

    extra = {"content": [{"type": "text", "text": "统计完成"}], "structuredContent": {"rows": 1207}}
    assert "1207" in _decode_content(extra).content


def test_parse_env_drops_names_that_are_not_set() -> None:
    built = spec(env=("MCP_DEFINITELY_NOT_SET_ANYWHERE",))
    env, dropped = parse_env(built)
    assert dropped == ["MCP_DEFINITELY_NOT_SET_ANYWHERE"]
    assert "MCP_DEFINITELY_NOT_SET_ANYWHERE" not in env


# --------------------------------------------------------------- 与官方 SDK 互操作


SDK_SERVER = Path(__file__).parent / "fixtures" / "mcp_sdk_server.py"
HAS_SDK = importlib.util.find_spec("mcp") is not None


@pytest.mark.skipif(not HAS_SDK, reason="没装官方 mcp SDK（dev extra，只用来写这个对端）")
def test_interop_with_a_third_party_server(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    """对端换成**官方 SDK 写的服务**：报文形状从此不由我们说了算。

    `mcp_fixture_server.py` 证的是我们接得住自己写得出的极端行为；这一条证的才是
    SPEC §7.1 行 14 那句"接一个真实 MCP server 跑通"。三件事在这里合上：
    握手与 `tools/list` 由第三方实现产生、它按规范自报 `readOnlyHint` 而我们仍按
    EXECUTE 办、宿主的 `LLM_API_KEY` 确实没有漏进它的进程。
    """
    monkeypatch.setenv("LLM_API_KEY", "sk-parent-abcdefghijklmnop")
    bridge = MCPBridge(
        servers=[
            MCPServerSpec(
                name="sdk", endpoint=sys.executable, args=(str(SDK_SERVER),), env=("LLM_API_KEY",)
            )
        ],
        workspace=ws,
        timeout=60.0,
    )
    try:
        tools = {tool.name: tool for tool in bridge.discover()}
        assert sorted(tools) == ["mcp__sdk__sdk_boom", "mcp__sdk__sdk_echo", "mcp__sdk__sdk_env"]
        entry = bridge.stats()["servers"][0]
        assert entry["ok"] is True and entry["skipped"] == []
        assert entry["server_info"]["name"] == "sdk-fx"

        echo = tools["mcp__sdk__sdk_echo"]
        # SDK 由函数签名生成 inputSchema：properties 是真的，才没被 _wrap 跳过
        assert echo.input_schema["properties"].keys() >= {"text", "times"}
        assert echo.risk_level is RiskLevel.EXECUTE and echo.declared_risk == "read"
        assert call(echo, text="你好 MCP", times=2).content.count("你好 MCP") == 2

        env = json.loads(call(tools["mcp__sdk__sdk_env"]).content.splitlines()[-1])
        assert env["has_api_key"] is False and env["has_path"] is True
        assert entry["dropped_env"] == ["LLM_API_KEY"]

        boom = call(tools["mcp__sdk__sdk_boom"])
        assert boom.is_error is True and boom.content
    finally:
        bridge.close()
    assert bridge.available() == []
