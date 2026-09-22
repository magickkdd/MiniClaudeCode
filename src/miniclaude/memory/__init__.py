"""记忆层：仓库地图与工作区内的可重建缓存（SPEC v2 §3.4）。

依赖约束（§2.3-2）：这一层只允许被 `agent/` 与 `cli/` 使用，`tools/` 不得 import 它 ——
否则工具会反向依赖 agent 层。
"""

from miniclaude.memory.repo_map import (
    MapStats,
    RepoMap,
    Symbol,
    analyze_source,
    build_import_index,
    extract_symbols,
    parse_imports,
    render_file_lines,
)
from miniclaude.memory.store import MEMORY_DIRNAME, MemKind, MemoryStore

__all__ = [
    "MEMORY_DIRNAME",
    "MapStats",
    "MemKind",
    "MemoryStore",
    "RepoMap",
    "Symbol",
    "analyze_source",
    "build_import_index",
    "extract_symbols",
    "parse_imports",
    "render_file_lines",
]
