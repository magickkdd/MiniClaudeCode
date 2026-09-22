"""Mini Claude Code v2 §3.7 —— 扩展层（MCP bridge 与 Skills）。

这一层的存在理由：**外部能力必须经过同一道权限门**，而不是另起一条装配路径。
"""

from miniclaude.ext.mcp import (
    MCPServerSpec,
    MCPBridge,
    MCPError,
    MCPSpecError,
    RemoteTool,
)
from miniclaude.ext.skills import (
    LoadSkillTool,
    SkillError,
    SkillLoader,
    SkillManifest,
)

__all__ = [
    "LoadSkillTool",
    "MCPBridge",
    "MCPError",
    "MCPServerSpec",
    "MCPSpecError",
    "RemoteTool",
    "SkillError",
    "SkillLoader",
    "SkillManifest",
]
