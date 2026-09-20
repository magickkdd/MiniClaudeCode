"""find_files —— 按 glob 找文件，用来快速建立仓库形状感。"""

from __future__ import annotations

from typing import Any

from miniclaude.tools.base import BaseTool, RiskLevel, ToolResult

DEFAULT_MAX = 200


class FindFilesTool(BaseTool):
    name = "find_files"
    description = (
        "按 glob 模式查找工作区内的文件，返回相对路径。"
        "接手陌生仓库时先用它看清结构（如 **/*.py、**/test_*.py）。"
        "自动跳过 .git、__pycache__、.venv、node_modules 等目录。"
        "要找的是**内容**而不是文件名时，请改用 search_text。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式，例如 **/*.py"},
            "max_results": {"type": "integer", "description": f"最多返回条数，默认 {DEFAULT_MAX}"},
        },
        "required": ["pattern"],
    }
    risk_level = RiskLevel.READ

    def run(self, *, pattern: str, max_results: int = DEFAULT_MAX) -> ToolResult:
        cleaned = pattern.strip().lstrip("/")
        if not cleaned or ".." in cleaned.replace("\\", "/").split("/"):
            return ToolResult.err(
                f"非法 glob 模式 {pattern!r}：必须落在工作区内，不允许使用 .. 或绝对路径。"
            )
        try:
            limit = max(1, min(int(max_results), DEFAULT_MAX))
        except (TypeError, ValueError):
            limit = DEFAULT_MAX

        matches: list[str] = []
        scanned = 0
        for candidate in self.ws.root.glob(cleaned):
            scanned += 1
            if scanned > self.ws.max_scan_files:
                break
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(self.ws.root)
            if self.ws.is_ignored(relative):
                continue
            matches.append(relative.as_posix())
            if len(matches) >= limit:
                break

        if not matches:
            return ToolResult.ok(f"没有文件匹配 {pattern!r}（扫描了 {scanned} 个候选）。换个更宽的模式试试。")
        matches.sort()
        return ToolResult.ok("\n".join(matches) + f"\n\n共 {len(matches)} 个文件匹配 {pattern!r}")
