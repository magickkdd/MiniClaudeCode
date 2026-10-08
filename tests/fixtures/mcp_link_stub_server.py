"""insight-agent 的替身（mcp-link-spec T4）：工具面按真 server 逐字回放。

真系统的握手与研究属于 T1-T3 的手动验收；CI 要离线可跑，所以这里用一个最小的
JSON-RPC 回放脚本钉住我们这侧的三条路径 —— namespace 注册、external 确认、
撞 `MCP_TOOL_TIMEOUT` 的长调用在预算处失败并立刻恢复。零依赖、零网络，只有管道。

argv[1] = research / research_async 的应答延迟秒数（默认 0，即秒回）。
"""

from __future__ import annotations

import json
import sys
import time

DELAY = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0

# 与 `insight_agent/mcp_server.py` 的 @server.tool() 一一对应：名字、参数、required。
TOOLS = [
    {
        "name": "research",
        "description": "对指定主题执行完整研究，返回结构化 Markdown 报告（分钟级长任务）",
        "inputSchema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}, "depth": {"type": "string"}},
            "required": ["topic"],
        },
    },
    {
        "name": "research_async",
        "description": "异步版 research：线程池里跑同步图",
        "inputSchema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}, "depth": {"type": "string"}},
            "required": ["topic"],
        },
    },
    {
        "name": "get_notes",
        "description": "获取某主题的研究档案（秒级）",
        "inputSchema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    },
    {
        "name": "list_archives",
        "description": "列出全部已研究主题",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

REPORT = (
    "# httpx 超时配置（替身报告）\n\n"
    "- 引用：https://www.python-httpx.org/advanced/timeouts/\n\n"
    "---\n[研究耗时 96s | 提纲 5 条 | 幻觉率 0.02 | 可信度 0.91]"
)


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(call_id, result=None, error=None) -> None:
    body: dict = {"jsonrpc": "2.0", "id": call_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    send(body)


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, call_id = message.get("method"), message.get("id")
        if method == "initialize":
            reply(
                call_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "insight-agent", "title": "行业洞察研究 Agent（替身）"},
                },
            )
        elif method == "tools/list":
            reply(call_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = message.get("params") or {}
            name, args = params.get("name"), params.get("arguments") or {}
            if name in {"research", "research_async"}:
                time.sleep(DELAY)
                reply(call_id, {"content": [{"type": "text", "text": REPORT}]})
            elif name == "get_notes":
                reply(call_id, {"content": [{"type": "text", "text": f"「{args.get('topic', '')}」的研究档案（替身回放）"}]})
            elif name == "list_archives":
                reply(call_id, {"content": [{"type": "text", "text": "[]"}]})
            else:
                reply(call_id, error={"code": -32601, "message": f"unknown tool {name}"})
        elif call_id is not None:
            reply(call_id, error={"code": -32601, "message": f"unsupported method {method}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
