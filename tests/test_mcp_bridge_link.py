"""mcp-link-spec T4 —— MCC 侧接线的替身集成测试。

对端是 `tests/fixtures/mcp_link_stub_server.py`：按真 insight-agent 的工具面
（research / research_async / get_notes / list_archives）回放报文。真系统的握手与
长任务联动属于 T1-T3 的手动验收；CI 只离线钉三条路径：

  ① namespace 注册 —— 远端 research 进 registry 后叫 `mcp__insight-agent__research`，
     本地工具一个不让路（真 server 的形状由替身逐字复刻，名字对不上就先挂这里）；
  ② external 确认 —— AUTO 模式照样逐次询问，人点头才能跑；
  ③ 无 timeout 死锁 —— 预算内的长调用完整返回；撞 `MCP_TOOL_TIMEOUT` 的长调用在
     预算处变成 is_error 并立刻可恢复，而不是把主循环挂死（P1：会话线固定 30s 时
     fast 档实测 ≈90s 的 research 必被误杀）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest
from miniclaude.agent.permissions import Answer, Decision, PermissionGate, PermissionMode
from miniclaude.ext import MCPBridge, MCPServerSpec
from miniclaude.messages import ToolUseBlock
from miniclaude.tools.base import RiskLevel
from miniclaude.tools.registry import ToolRegistry
from miniclaude.tools.workspace import Workspace

SERVER = Path(__file__).parent / "fixtures" / "mcp_link_stub_server.py"


def spec(*, delay: float = 0, name: str = "insight-agent") -> MCPServerSpec:
    return MCPServerSpec(name=name, endpoint=sys.executable, args=(str(SERVER), str(delay)))


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path, output_limit=30_000)


def call(tool: Any, **args: Any) -> Any:
    return tool.invoke(ToolUseBlock(id="t1", name=tool.name, input=args))


# --------------------------------------------------------------- ① namespace 注册


def test_link_stub_registers_under_namespace_without_shadowing_locals(ws: Workspace) -> None:
    bridge = MCPBridge(servers=[spec()], workspace=ws)
    try:
        tools = bridge.discover()
        assert sorted(tool.name for tool in tools) == [
            "mcp__insight-agent__get_notes",
            "mcp__insight-agent__list_archives",
            "mcp__insight-agent__research",
            "mcp__insight-agent__research_async",
        ]
        registry = ToolRegistry.default(ws, extra_tools=tools)
        # 本地 8 工具原样在座，远端一个前缀名都不与其相撞（撞了注册表会当场拒绝）
        assert {"read_file", "write_file", "bash", "run_tests"} <= set(registry.names())
        research = registry.get("mcp__insight-agent__research")
        assert research.external and research.risk_level is RiskLevel.EXECUTE
        result = call(registry.get("mcp__insight-agent__get_notes"), topic="httpx timeout")
        assert not result.is_error and "研究档案" in result.content
    finally:
        bridge.close()


# --------------------------------------------------------------- ② external 确认


def test_auto_mode_asks_for_every_research_call_and_a_yes_lets_it_run(ws: Workspace) -> None:
    bridge = MCPBridge(servers=[spec()], workspace=ws)
    try:
        research = next(tool for tool in bridge.discover() if tool.remote_name == "research")
        gate = PermissionGate(workspace=ws, mode=PermissionMode.AUTO)
        decision, reason = gate.check(research, {"topic": "httpx timeout"})
        assert decision is Decision.ASK
        assert "外部工具" in reason and "工作区" in reason
        # 没有确认渠道就保守拒绝，绝不擅自执行
        ok, why = gate.authorize(research, {"topic": "httpx timeout"})
        assert ok is False and "确认" in why
        # 人点头：确认 → 调用 → 报告回填
        answered = PermissionGate(
            workspace=ws, mode=PermissionMode.AUTO, confirmer=lambda _name, _summary: Answer.ONCE
        )
        ok, _ = answered.authorize(research, {"topic": "httpx timeout"})
        assert ok is True
        result = call(research, topic="httpx timeout", depth="fast")
        assert not result.is_error and "替身报告" in result.content
    finally:
        bridge.close()


# --------------------------------------------------------------- ③ 无 timeout 死锁


def test_long_call_within_the_budget_returns_the_full_report(ws: Workspace) -> None:
    bridge = MCPBridge(servers=[spec(delay=1)], workspace=ws, timeout=25.0, tool_timeout=30.0)
    try:
        research = next(tool for tool in bridge.discover() if tool.remote_name == "research")
        started = time.monotonic()
        result = call(research, topic="httpx timeout", depth="fast")
        assert not result.is_error, result.content
        assert "替身报告" in result.content and "python-httpx.org" in result.content
        assert time.monotonic() - started >= 1.0, "远端的应答延迟必须被等满，而不是提前判负"
    finally:
        bridge.close()


def test_over_budget_call_fails_at_the_budget_and_recovers_immediately(ws: Workspace) -> None:
    """`MCP_TOOL_TIMEOUT` 是 tools/call 那一根线：报文里带着**生效的那条预算**。"""
    bridge = MCPBridge(servers=[spec(delay=10)], workspace=ws, timeout=25.0, tool_timeout=2.0)
    try:
        tools = {tool.remote_name: tool for tool in bridge.discover()}
        started = time.monotonic()
        result = call(tools["research"], topic="httpx timeout")
        elapsed = time.monotonic() - started
        assert result.is_error
        # 会话线是 25s，报文里写 2s —— 证明 tools/call 走的是 tool_timeout 那根线
        assert "回答超时（2s）" in result.content
        assert elapsed < 20, "长调用必须在预算处失败，而不是把主循环挂死"
        notes = call(tools["get_notes"], topic="httpx timeout")
        assert not notes.is_error and "研究档案" in notes.content
    finally:
        bridge.close()


# --------------------------------------------------------------- 配置旋钮


def test_mcp_tool_timeout_knob_parses_and_rejects_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miniclaude.config import Config, ConfigError

    for name, value in [
        ("LLM_BASE_URL", "https://mock.invalid/v1"),
        ("LLM_MODEL", "mock"),
        ("LLM_API_KEY", "sk-x-000000000000"),
    ]:
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MCP_TOOL_TIMEOUT", "42")
    assert Config.from_env().mcp_tool_timeout == 42
    monkeypatch.setenv("MCP_TOOL_TIMEOUT", "0")
    with pytest.raises(ConfigError, match="至少 1 秒"):
        Config.from_env()


def test_bridge_default_tool_timeout_is_the_config_default() -> None:
    """同一张缺省表：桥的缺省值漂了，改 .env 的人不会知道。"""
    from miniclaude.config import DEFAULTS

    assert MCPBridge.__dataclass_fields__["tool_timeout"].default == DEFAULTS["MCP_TOOL_TIMEOUT"]
