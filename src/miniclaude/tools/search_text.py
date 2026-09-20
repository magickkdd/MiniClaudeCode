"""search_text —— 跨文件正则检索。

「在陌生仓库里定位 bug」这条验收线全部压在这个工具上。没有它，模型只能
对着一堆文件逐个 read_file 猜，步数和 token 都会失控。
"""

from __future__ import annotations

import re
from typing import Any

from miniclaude.tools.base import BaseTool, RiskLevel, ToolResult
from miniclaude.tools.workspace import BINARY_SUFFIXES

DEFAULT_MAX_HITS = 150
MAX_LINE_REPORT = 300
MAX_SCAN_BYTES = 1_000_000


class SearchTextTool(BaseTool):
    name = "search_text"
    description = (
        "在工作区内做正则搜索，返回 `文件:行号:内容` 形式的前后匹配行。"
        "定位某个函数、类、字符串或调用点时的首选工具，比逐个读文件快得多。"
        "用 path_glob 缩小范围（如 **/*.py）。结果被截断时，说明命中太多，请收窄模式。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python 正则表达式"},
            "path_glob": {"type": "string", "description": "限定文件范围，默认 **/*"},
            "ignore_case": {"type": "boolean", "description": "忽略大小写"},
            "max_hits": {"type": "integer", "description": f"最多返回命中数，默认 {DEFAULT_MAX_HITS}"},
        },
        "required": ["pattern"],
    }
    risk_level = RiskLevel.READ

    def run(
        self,
        *,
        pattern: str,
        path_glob: str = "**/*",
        ignore_case: bool = False,
        max_hits: int = DEFAULT_MAX_HITS,
    ) -> ToolResult:
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            return ToolResult.err(f"正则表达式非法：{exc}. 请检查括号与转义（在 JSON 里反斜杠要写成 \\\\）。")

        cleaned = path_glob.strip().lstrip("/")
        if not cleaned or ".." in cleaned.replace("\\", "/").split("/"):
            return ToolResult.err(f"非法的 path_glob {path_glob!r}：必须落在工作区内。")

        try:
            limit = max(1, min(int(max_hits), DEFAULT_MAX_HITS))
        except (TypeError, ValueError):
            limit = DEFAULT_MAX_HITS

        hits: list[str] = []
        files_with_hits: set[str] = set()
        scanned = 0
        truncated = False

        for candidate in self.ws.root.glob(cleaned):
            scanned += 1
            if scanned > self.ws.max_scan_files:
                truncated = True
                break
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(self.ws.root)
            if self.ws.is_ignored(relative) or candidate.suffix.lower() in BINARY_SUFFIXES:
                continue
            try:
                if candidate.stat().st_size > MAX_SCAN_BYTES:
                    continue
                text = candidate.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeDecodeError):
                continue

            display = relative.as_posix()
            for number, line in enumerate(text.splitlines(), start=1):
                if not regex.search(line):
                    continue
                stripped = line.strip()
                hits.append(f"{display}:{number}:{stripped[:MAX_LINE_REPORT]}")
                files_with_hits.add(display)
                if len(hits) >= limit:
                    truncated = True
                    break
            if truncated:
                break

        if not hits:
            return ToolResult.ok(
                f"没有匹配 {pattern!r}（在 {scanned} 个候选文件里搜索）。"
                "试试更宽松的模式、加 ignore_case，或换 path_glob。"
            )
        summary = f"\n\n共 {len(hits)} 处命中，分布在 {len(files_with_hits)} 个文件" + (
            "（结果已达上限被截断，请收窄 pattern 或 path_glob）" if truncated else ""
        )
        return ToolResult.ok("\n".join(hits) + summary)
