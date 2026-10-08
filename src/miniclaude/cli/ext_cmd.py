"""`mcc mcp` / `mcc skills` —— 扩展层的自述面板（SPEC v2 §3.7）。

两个命令共用一个文件，是因为它们回答的是同一个问题：**这次会话外面接着什么**。
分成两份 argparse 壳就会有两套"怎么说清降级"的措辞，而 §3.6 已经证明过一次
"同一件事两个写法"的代价。

`mcp` 会**真的起一次服务**去发现工具 —— 这是它的全部意义：配置写对了没有，
看 `mcp_server.py` 的源码看不出来，只有握手成功才算数。代价是它不是只读命令，
所以输出里逐条写明连了谁、拿了几个工具、哪些被弃用以及为什么。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from miniclaude.config import Config
from miniclaude.ext import MCPBridge, MCPServerSpec, MCPSpecError, SkillLoader
from miniclaude.tools.base import RiskLevel

NO_SERVER = (
    "没有配置任何 MCP 服务。\n"
    "  配法：export MCP_SERVERS='[{\"name\":\"kb\",\"endpoint\":\"python\",\"args\":[\"server.py\"]}]'\n"
    "  然后 mcc mcp 看握手结果。"
)


def build_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--root", type=Path, default=None, help="工作目录（技能目录相对它解析）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    return parser


# --------------------------------------------------------------- mcc mcp


def _bridge_from(config: Config) -> tuple[MCPBridge | None, str, list[str]]:
    """按配置建桥并发现工具。返回 (bridge, 错误, 配置里的服务名)。"""
    names = [str(item.get("name", "")) for item in config.mcp_servers]
    if not config.mcp_servers:
        return None, "", names
    try:
        specs = tuple(MCPServerSpec.from_mapping(item) for item in config.mcp_servers)
    except MCPSpecError as exc:
        return None, str(exc), names
    from miniclaude.tools.workspace import Workspace

    # 这个命令只需要一个 Workspace 实例给工具当截断器 —— 它不碰任何文件。
    workspace = Workspace(config.project_root, output_limit=config.tool_output_limit)
    bridge = MCPBridge(servers=specs, workspace=workspace, tool_timeout=config.mcp_tool_timeout)
    bridge.discover()
    return bridge, "", names


def mcp_entry(argv: Sequence[str], config: Config) -> int:
    args = build_parser("mcc mcp", "连接配置里的 MCP 服务，列出它提供的工具。").parse_args(list(argv))
    bridge, problem, configured = _bridge_from(config)
    if problem:
        print(f"MCP_SERVERS 配错了：{problem}", file=sys.stderr)
        return 2
    if bridge is None:
        print('{"servers": []}' if args.json else NO_SERVER)
        return 0
    try:
        payload = bridge_view(bridge, configured=configured)
    finally:
        # 服务是我们起的，退出时一个都不留。放在 finally 是因为报错路径同样会留下进程。
        bridge.close()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_bridge_view(payload))
    return 0 if payload["ok"] else 1


def bridge_view(bridge: MCPBridge, *, configured: Sequence[str] = ()) -> dict[str, Any]:
    stats = bridge.stats()
    servers = [entry for entry in stats["servers"]]
    return {
        "configured": list(configured) or stats["configured"],
        "running": stats["running"],
        "tools": stats["tools"],
        "skipped": stats["skipped"],
        "servers": servers,
        # 只要有一个服务没握上手，整体结论就不是"成功"。列表面板报绿是骗人。
        "ok": all(entry.get("ok") for entry in servers) if servers else False,
    }


def render_bridge_view(payload: dict[str, Any]) -> str:
    lines = [
        f"配置了 {len(payload['configured'])} 个服务：{', '.join(payload['configured']) or '（无）'}",
        f"握上手 {len(payload['running'])} 个 · 外部工具 {payload['tools']} 个 · 弃用 {payload['skipped']} 个",
    ]
    for entry in payload["servers"]:
        head = f"  {entry['name']}（{entry['transport']}）"
        lines.append(head + (f" → {entry['error']}" if not entry.get("ok") else " → 可用"))
        for tool in entry.get("tools", []):
            declared = tool.get("declared_risk") or "未声明"
            lines.append(
                f"    {tool['name']:<34} 远端自报 {declared:<8} 采信 {RiskLevel.EXECUTE.value}"
                "（每次都要确认，AUTO 也不例外）"
            )
        for skip in entry.get("skipped", []):
            lines.append(f"    ✗ {skip['name']}：{skip['reason']}")
        if entry.get("dropped_env"):
            lines.append(f"    未透传的环境变量：{', '.join(entry['dropped_env'])}")
    lines.append("  命令行里的密钥位置已按旗标脱敏；endpoint 原样显示，它不含值。")
    return "\n".join(lines)


def render_session_mcp(bridge: MCPBridge | None, *, configured: Sequence[str], skipped: str) -> str:
    """`/mcp` —— 会话内视角。不重新握手：会话已经拿着它discover过的工具了。"""
    if bridge is None:
        if skipped:
            return f"{skipped}（配置的{len(configured)}个：{', '.join(configured)}）"
        return "本会话没有外部工具：MCP_SERVERS 是空的。`mcc mcp` 看怎么配。"
    return render_bridge_view(bridge_view(bridge, configured=configured))


# --------------------------------------------------------------- mcc skills


def _skills_root(config: Config, override: Path | None) -> Path:
    """`--root` 改的是**工作目录**，SKILLS_DIR 相对它解析 —— 和 `mcc --root` 同一个语义。"""
    if override is None:
        return config.skills_root
    configured = Path(config.skills_dir).expanduser()
    if configured.is_absolute():
        return configured
    return Path(override).expanduser().resolve() / configured


def skills_entry(argv: Sequence[str], config: Config) -> int:
    args = build_parser("mcc skills", "列出技能目录：常驻的目录行与按需展开的正文各占多少。").parse_args(list(argv))
    root = _skills_root(config, args.root)
    loader = SkillLoader(root)
    rows = loader.manifests()
    if args.json:
        print(json.dumps(loader.stats() | {"rejected": loader.rejected}, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print(f"{root} 下没有可用技能。一个技能 = 一个子目录里的 SKILL.md（frontmatter 要有 description）。")
        for item in loader.rejected:
            print(f"  ✗ {item['dir']}：{item['reason']}")
        return 0
    stats = loader.stats()
    print(f"技能目录 {stats['root']}（常驻 {stats['catalog_lines']} 行 / {stats['catalog_chars']:,} 字符）")
    for row in rows:
        extra = f" · 同目录另有 {len(row.siblings)} 个文件不加载（D20）" if row.siblings else ""
        print(f"  {row.name:<24} {row.description[:70]}")
        print(f"  {'':<24} 正文 {row.body_chars:,} 字符 · 按需（load_skill 才进上下文）{extra}")
    for item in loader.rejected:
        print(f"  ✗ {item['dir']}：{item['reason']}")
    deferred = max(stats["body_chars_total"] - stats["catalog_chars"], 0)
    print(
        f"  合计：全部展开 {stats['body_chars_total']:,} 字符，常驻只有 {stats['catalog_chars']:,}"
        f" —— 差额 {deferred:,} 就是不常驻省下来的那部分"
    )
    return 0


def render_session_skills(skills: SkillLoader | None, *, root: Path) -> str:
    rows = skills.manifests() if skills is not None else []
    if not rows:
        return f"{root} 下没有可用技能（技能 = 子目录里的 SKILL.md）。"
    stats = skills.stats() if skills is not None else {}
    lines = [f"  常驻 {stats.get('catalog_lines', 0)} 行 / {stats.get('catalog_chars', 0):,} 字符 · 展开一个才付一个的正文："]
    lines.extend(f"    {row.name}：{row.description[:70]}" for row in rows)
    if skills is not None and skills.rejected:
        lines.extend(f"    ✗ {item['dir']}：{item['reason']}" for item in skills.rejected)
    return "\n".join(lines)
