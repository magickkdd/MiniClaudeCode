"""工作区边界 —— 工具层所有路径与输出尺寸的唯一裁决者。

SPEC §3.1 把路径锁划给 PermissionGate，但工具自己也要解析路径，两处各写
一份 contains 判断必然漂移。因此这里下沉一个 Workspace：
工具与权限门共用它，gate 负责"要不要问用户"，Workspace 负责"这个路径存不存在于沙箱内"。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# 检索与列举时默认跳过：这些目录既慢又会把模型引向无关内容
IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "build",
        "dist",
        ".idea",
        ".vscode",
        ".traces",
        # agent 自己的记忆缓存（SPEC v2 §3.4）。不忽略它就会出现一条真实的自我强化
        # 回路：地图读到 .mcc/ 里缓存的地图，下一次地图再基于它生成，几轮之后
        # 模型看到的是自己的输出被当成代码。
        ".mcc",
    }
)

BINARY_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".exe", ".dll", ".so", ".pyc", ".db", ".whl"}
)


class PathEscape(ValueError):
    """请求的路径跳出工作区 —— 属于业务失败，转成 is_error 给模型看。"""


@dataclass(frozen=True)
class Workspace:
    """Agent 被允许触碰的唯一目录，以及输出体积上限。"""

    root: Path
    output_limit: int = 30_000
    max_read_lines: int = 500
    max_scan_files: int = 20_000
    ignored_dirs: frozenset[str] = field(default=IGNORED_DIRS)

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).resolve())

    # ------------------------------------------------------------- 路径

    def resolve(self, raw: str | Path) -> Path:
        """解析成绝对路径并强制落在工作区内。

        用 realpath 而不是只比较字符串：`..` 拼接和符号链接都能绕过 naive 检查。
        """
        candidate = Path(str(raw)).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        real = Path(os.path.realpath(str(candidate)))
        try:
            real.relative_to(self.root)
        except ValueError:
            raise PathEscape(
                f"路径 {raw!r} 在工作区之外（允许范围：{self.root}）。请使用相对路径。"
            ) from None
        return real

    def contains(self, raw: str | Path) -> bool:
        try:
            self.resolve(raw)
        except PathEscape:
            return False
        return True

    def rel(self, path: str | Path) -> str:
        """转成展示用的相对路径，统一用正斜杠 —— 模型对 `src/a.py` 的直觉远好于 `src\\a.py`。"""
        resolved = self.resolve(path)
        return resolved.relative_to(self.root).as_posix() or "."

    def is_ignored(self, path: Path) -> bool:
        return any(part in self.ignored_dirs for part in path.parts)

    # ------------------------------------------------------------- 输出

    def truncate(self, text: str, limit: int | None = None) -> str:
        """超限时**保留首尾各一半**：错误常在开头，堆栈尾巴在结尾，只留头部会丢关键信息。"""
        cap = limit or self.output_limit
        if len(text) <= cap:
            return text
        keep = max(cap // 2 - 200, 500)
        omitted = len(text) - 2 * keep
        return f"{text[:keep]}\n\n[... {omitted} chars omitted, output truncated ...]\n\n{text[-keep:]}"
