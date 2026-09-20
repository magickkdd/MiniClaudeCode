"""edit_file —— 精确字符串替换。

比整文件重写好在哪：token 省一个数量级、改动可审查、不会因为模型顺手
重写而静默丢掉无关代码。匹配失败时的**诊断文案质量**直接决定模型能否自愈。
"""

from __future__ import annotations

from typing import Any

from miniclaude.tools.base import BaseTool, RiskLevel, ToolResult

SAMPLE_SNIPPETS = 3


class EditFileTool(BaseTool):
    name = "edit_file"
    description = (
        "通过精确匹配 old_string 修改文件，是最推荐的编辑方式。"
        "old_string 必须与文件内容**逐字符一致（含缩进和空行）**，且在文件中唯一匹配。"
        "使用前必须先 read_file。匹配到多处时，扩大 old_string 的上下文直到唯一，"
        "或设 replace_all=true 一次替换全部。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string", "description": "待替换的原文，需唯一匹配"},
            "new_string": {"type": "string", "description": "替换后的文本"},
            "replace_all": {"type": "boolean", "description": "替换全部匹配项，默认 false"},
        },
        "required": ["path", "old_string", "new_string"],
    }
    risk_level = RiskLevel.WRITE

    def run(
        self,
        *,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> ToolResult:
        target = self.ws.resolve(path)
        if not target.is_file():
            return ToolResult.err(f"文件不存在：{path}。要新建文件请用 write_file。")
        if not old_string:
            return ToolResult.err("old_string 不能为空字符串。")
        if old_string == new_string:
            return ToolResult.err("old_string 与 new_string 完全相同，这次编辑不会改变任何内容。")

        text = target.read_text(encoding="utf-8", errors="replace")
        diagnosis = self._check_match(text, old_string, bool(replace_all))
        if diagnosis:
            return ToolResult.err(diagnosis)

        count = text.count(old_string)
        updated = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        target.write_text(updated, encoding="utf-8")

        delta = len(updated.splitlines()) - len(text.splitlines())
        applied = count if replace_all else 1
        change = "行数不变" if delta == 0 else f"{'+' if delta > 0 else ''}{delta} 行"
        return ToolResult.ok(f"已在 {self.ws.rel(target)} 中替换 {applied} 处（{change}）。")

    def _check_match(self, text: str, old_string: str, replace_all: bool) -> str | None:
        """匹配数不对时，返回**能指导模型改正**的文案。"""
        occurrences = text.count(old_string)

        if occurrences == 0:
            return (
                f"没找到匹配。old_string 必须与文件内容逐字符一致（注意缩进、空行和引号）。"
                "请先用 read_file 确认实际内容，再取一小段确定存在的代码重试。"
                f"文件当前共 {len(text.splitlines())} 行。"
            )
        if occurrences > 1 and not replace_all:
            samples = self._occurrence_lines(text, old_string)
            return (
                f"old_string 在文件中匹配到 {occurrences} 处，无法确定要改哪一处。"
                "请扩大 old_string 的上下文（多带几行前后代码）使其唯一，"
                f"或设 replace_all=true 全部替换。它出现在第 {samples} 行。"
            )
        return None

    @staticmethod
    def _occurrence_lines(text: str, old_string: str) -> str:
        positions: list[int] = []
        start = text.find(old_string)
        while start != -1 and len(positions) < SAMPLE_SNIPPETS:
            positions.append(text.count("\n", 0, start) + 1)
            start = text.find(old_string, start + 1)
        if text.count(old_string) > len(positions):
            positions.append(-1)
        return ", ".join("…" if p == -1 else str(p) for p in positions)
