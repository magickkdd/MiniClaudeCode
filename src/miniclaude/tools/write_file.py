"""write_file —— 新建文件用这个，改已有文件应该用 edit_file。"""

from __future__ import annotations

from typing import Any

from miniclaude.tools.base import BaseTool, RiskLevel, ToolResult


class WriteFileTool(BaseTool):
    name = "write_file"
    description = (
        "创建新文件，或用新内容整体覆盖已有文件。"
        "局部修改请改用 edit_file —— 重写整个文件既浪费额度，也容易顺手丢掉无关代码。"
        "覆盖已存在的文件前，必须先 read_file 看过它。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目标文件路径"},
            "content": {"type": "string", "description": "要写入的完整内容"},
        },
        "required": ["path", "content"],
    }
    risk_level = RiskLevel.WRITE

    def run(self, *, path: str, content: str) -> ToolResult:
        target = self.ws.resolve(path)
        if target.is_dir():
            return ToolResult.err(f"{path} 是一个已存在的目录，无法写成文件。")

        target.parent.mkdir(parents=True, exist_ok=True)
        new_lines = len(content.splitlines())
        if target.exists():
            old = target.read_text(encoding="utf-8", errors="replace")
            old_lines = len(old.splitlines())
            verb = f"覆盖（原有 {old_lines} 行 → 现在 {new_lines} 行）"
        else:
            verb = "新建"

        target.write_text(content if content.endswith("\n") or not content else content + "\n", encoding="utf-8")
        return ToolResult.ok(f"已{verb}文件 {self.ws.rel(target)}，共 {new_lines} 行。")
