"""用**官方 MCP SDK** 写的服务：证明我们的客户端跟生态对得上，不只是跟自家替身说话。

SPEC v2 §3.7 的验收线是"接一个真实 MCP server 跑通"。`mcp_fixture_server.py` 证的是
协议另一侧的极端行为（不回答、崩溃、吐脏行）—— 那些是**我们自己的代码**，只能证明
我们接得住自己写得出的东西。这个文件不同：握手、`tools/list`、`tools/call` 全部由
第三方实现产生，报文形状不由我们说了算。

它需要 `pip install mcp`（开发期依赖，不在运行期的依赖清单里）。没装时
`s14_ext_demo.py` 会把这一臂标成 `skipped` 而不是假装通过。

工具故意带上 `readOnlyHint=True`：规范里这就是"服务端自报只读"。我们那侧读它、
显示它，但**采信值永远是 EXECUTE**（D19）—— 这条正是这次互操作要拿证据的地方。
"""

from __future__ import annotations

import os
import sys

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

server: MCPServer = MCPServer("sdk-fx")


@server.tool(
    name="sdk_echo",
    title="回声",
    description="把收到的文本原样返回。",
    annotations=ToolAnnotations(title="回声", read_only_hint=True),
)
def sdk_echo(text: str, times: int = 1) -> str:
    return " ".join([text] * max(times, 1))


@server.tool(
    name="sdk_env",
    title="环境自述",
    description="报告这个进程看见了哪些环境变量 —— 用来检查宿主有没有把密钥漏给它。",
    annotations=ToolAnnotations(title="环境自述", read_only_hint=True),
)
def sdk_env() -> dict[str, object]:
    return {
        "has_api_key": bool(os.environ.get("LLM_API_KEY")),
        "has_path": bool(os.environ.get("PATH")),
        "has_pythonpath": bool(os.environ.get("PYTHONPATH")),
        "python": os.path.basename(sys.executable),
    }


@server.tool(name="sdk_boom", title="总是失败", description="返回一个 isError 结果。")
def sdk_boom() -> str:
    raise RuntimeError("这个工具按设计失败")


if __name__ == "__main__":
    server.run(transport="stdio")
