"""read_file —— 带行号读取。行号是 edit_file 能精确命中的前提。"""

from __future__ import annotations

from typing import Any

from miniclaude.tools.base import BaseTool, RiskLevel, ToolResult
from miniclaude.tools.workspace import BINARY_SUFFIXES

MAX_LINE_CHARS = 400
MAX_FILE_BYTES = 2_000_000


class ReadFileTool(BaseTool):
    name = "read_file"
    description = (
        "读取工作区内的文本文件，返回带行号的内容。"
        "在修改任何文件之前必须先读取它 —— 禁止凭猜测编辑。"
        "文件过大时先用 search_text 定位，再用 offset/limit 只读需要的那一段。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径，相对工作区根目录"},
            "offset": {"type": "integer", "description": "起始行号，从 1 开始，默认 1"},
            "limit": {"type": "integer", "description": "最多读取的行数，默认 500"},
        },
        "required": ["path"],
    }
    risk_level = RiskLevel.READ

    def run(self, *, path: str, offset: int = 1, limit: int | None = None) -> ToolResult:
        target = self.ws.resolve(path)
        if not target.exists():
            return ToolResult.err(
                f"文件不存在：{path}。用 find_files 确认实际路径，注意大小写。"
            )
        if target.is_dir():
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())[:200]
            return ToolResult.err(
                f"{path} 是目录，不是文件。该目录下有：{', '.join(entries) or '（空）'}"
            )
        if target.suffix.lower() in BINARY_SUFFIXES:
            return ToolResult.err(f"{path} 是二进制文件（{target.suffix}），无法作为文本读取。")

        size = target.stat().st_size
        if size > MAX_FILE_BYTES:
            return ToolResult.err(
                f"{path} 有 {size:,} 字节，超过单次读取上限 {MAX_FILE_BYTES:,}。"
                "请改用 search_text 定位关键行，再用 offset/limit 读取小范围。"
            )

        raw = target.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        total = len(lines)
        start = max(int(offset), 1)
        count = int(limit) if limit else self.ws.max_read_lines
        count = max(1, min(count, self.ws.max_read_lines))
        chunk = lines[start - 1 : start - 1 + count]

        if not chunk and total:
            return ToolResult.err(f"{path} 只有 {total} 行，offset={start} 已超出文件末尾。")

        numbered = [
            f"{i}\t{line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + ' …(行过长，已截断)'}"
            for i, line in enumerate(chunk, start=start)
        ]
        header = f"文件 {self.ws.rel(target)} 共 {total} 行，以下是第 {start}-{start + len(chunk) - 1} 行："
        footer = (
            ""
            if start + len(chunk) - 1 >= total
            else f"\n[还有 {total - (start + len(chunk) - 1)} 行未显示，用 offset={start + len(chunk)} 继续读]"
        )
        return ToolResult.ok(f"{header}\n" + "\n".join(numbered) + footer)
