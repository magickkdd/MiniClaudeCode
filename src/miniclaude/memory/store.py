"""`.mcc/` 工作记忆 —— 可重建的派生物，不是第二个事实来源（SPEC v2 §2.3-1）。

这里存两类东西：

1. **缓存**（`REPO_MAP`）：ast 解析出来的每文件符号行。冷启动扫全树要几百毫秒，
   命中缓存只要几毫秒；判据是 `mtime+size` 指纹，所以"文件动过"永远比"看起来没动"保守。
2. **笔记**（`TEST_COMMAND` / `CONVENTION` / `GOTCHA`）：harness 观察到的事实，
   下一轮提示词里展示给模型看。

三条纪律，都是被 §6.3 点过名的：

- **任何"以磁盘状态为准"的设计一律驳回**。这里的每一条都可以从代码仓库本身重算，
  所以文件损坏、被删、跨机器搬过来指纹对不上，统统降级成"没有缓存"，绝不报错停机。
- **`TEST_COMMAND` 只展示，不自动执行**。渲染时原样带上这句话，并且不做任何
  `subprocess` 调用 —— 一次错误的观察若能让 agent 自动去跑命令，错误就自成了。
- **记忆内容永不进可执行路径**。没有 `eval`、没有 `exec`、没有 import；只有字符串。

密钥纪律照常：本文件写出去的内容全部由代码生成，不含凭据；`root` 恒在工作区内。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from miniclaude.config import DEFAULTS

MEMORY_DIRNAME = str(DEFAULTS["MEMORY_DIR"])  # 缺省目录名只有一个产地：config.DEFAULTS
STORE_FILENAME = "memory.json"
STORE_VERSION = 1

# 单条笔记的长度上限：模型爱写长文，但笔记进的是每轮都发的提示词。
MAX_VALUE_CHARS = 4_000
MAX_NOTES_PER_KIND = 24


class MemKind(StrEnum):
    REPO_MAP = "repo_map"          # 缓存，不当笔记渲染
    TEST_COMMAND = "test_command"  # 只展示
    CONVENTION = "convention"
    GOTCHA = "gotcha"


# 进提示词的笔记种类与顺序。REPO_MAP 故意不在这里：它由 RepoMap 自己渲染成地图，
# 混进笔记区就会出现"同一份信息在 system 里发两遍"。
NOTE_KINDS: tuple[MemKind, ...] = (MemKind.TEST_COMMAND, MemKind.CONVENTION, MemKind.GOTCHA)

_KIND_LABEL = {
    MemKind.TEST_COMMAND: "测试命令（只展示，不会自动执行）",
    MemKind.CONVENTION: "仓库约定",
    MemKind.GOTCHA: "踩过的坑",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_fingerprint(path: Path) -> tuple[float, int]:
    """`mtime + size`。只看这两个是因为它们足以抓住"内容变过"的绝大多数情况，
    而 size 不变、mtime 变（重新格式化）时重建一次的代价可以忽略。"""
    stat = path.stat()
    return round(stat.st_mtime, 3), stat.st_size


@dataclass
class MemoryStore:
    """`<project>/.mcc/` 的读写。落盘 JSON 而不是 sqlite：一个 <200KB 的缓存
    不值得引入 schema 迁移问题，而且 JSON 能直接在终端里 cat 出来排查。"""

    root: Path
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    degraded: str = ""          # 非空 = 这次没读到缓存，原因写在这里，要能报出来
    writes: int = 0

    @classmethod
    def for_project(cls, project_root: Path | str, memory_dir: str | Path = MEMORY_DIRNAME) -> "MemoryStore":
        """`memory_dir` 由调用方（Config.MEMORY_DIR）给，这里只负责拼路径。"""
        mem = Path(memory_dir)
        root = mem if mem.is_absolute() else Path(project_root) / mem
        return cls(root=root)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self._loaded = False
        self._dirty = False

    # ------------------------------------------------------------ 读写

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self.root / STORE_FILENAME
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            # 读坏了就当没有：缓存的正确归宿是重建，不是把会话带着一起死。
            self.degraded = f"{type(exc).__name__}: {exc}"[:200]
            self.entries = {}
            return
        if not isinstance(raw, dict) or raw.get("version") != STORE_VERSION or not isinstance(
            raw.get("entries"), dict
        ):
            self.degraded = f"版本或形状不认识（version={raw.get('version') if isinstance(raw, dict) else '?'}），已弃用"
            self.entries = {}
            return
        self.entries = {k: v for k, v in raw["entries"].items() if isinstance(v, dict)}

    def put(
        self,
        kind: MemKind,
        key: str,
        value: str,
        *,
        fingerprint: dict[str, list[float]] | None = None,
    ) -> None:
        self._load()
        text = str(value)
        if len(text) > MAX_VALUE_CHARS:
            text = text[:MAX_VALUE_CHARS].rstrip() + "…"
        self.entries[_composite(kind, key)] = {
            "kind": kind.value,
            "key": str(key),
            "value": text,
            "written_at": _now(),
            "fingerprint": fingerprint or {},
        }
        self._dirty = True

    def get(self, kind: MemKind, key: str) -> str | None:
        self._load()
        entry = self.entries.get(_composite(kind, key))
        return str(entry["value"]) if entry else None

    def fingerprint_of(self, kind: MemKind, key: str) -> dict[str, list[float]]:
        self._load()
        entry = self.entries.get(_composite(kind, key))
        return dict((entry or {}).get("fingerprint") or {})

    def keys(self, kind: MemKind) -> list[str]:
        self._load()
        return sorted(
            str(entry.get("key", "")) for entry in self.entries.values() if entry.get("kind") == kind.value
        )

    def forget(self, kind: MemKind, key: str | None = None) -> int:
        """删掉条目并立即落盘。`invalidate()` 靠它把改动文件的缓存摘掉。"""
        self._load()
        if key is None:
            doomed = [k for k, v in self.entries.items() if v.get("kind") == kind.value]
        else:
            doomed = [_composite(kind, key)]
        removed = 0
        for composite in doomed:
            if self.entries.pop(composite, None) is not None:
                removed += 1
        if removed:
            self._dirty = True
            self.flush()
        return removed

    def flush(self) -> None:
        """原子替换：半截文件比没有文件更糟 —— 那会让下次启动读到坏 JSON 并丢掉全部缓存。"""
        self._load()
        if not self._dirty:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / STORE_FILENAME
        tmp = self.root / f"{STORE_FILENAME}.tmp"
        payload = {
            "version": STORE_VERSION,
            "built_at": _now(),
            "entries": {key: dict(value) for key, value in sorted(self.entries.items())},
        }
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, target)
        except OSError as exc:
            # 写不进去（只读盘、并发删除）不该让这轮任务失败 —— 缓存的意义就是"下次更快"。
            self.degraded = f"写入失败 {type(exc).__name__}: {exc}"[:200]
            tmp.unlink(missing_ok=True)
            return
        self.writes += 1
        self._dirty = False

    # ------------------------------------------------------------ 渲染

    def notes_for_prompt(self, *, cap: int = 2_000) -> str:
        """把笔记区渲染成一段提示词。`cap` 是字符预算，不是条数预算 ——
        笔记的价值在于"少而准"，涨到没人读得完时就该按种类丢最老的。"""
        self._load()
        if not self.entries:
            return ""
        sections: list[str] = []
        budget = cap
        for kind in NOTE_KINDS:
            items = sorted(
                (entry for entry in self.entries.values() if entry.get("kind") == kind.value),
                key=lambda entry: str(entry.get("written_at") or ""),
                reverse=True,
            )[:MAX_NOTES_PER_KIND]
            if not items:
                continue
            lines = [f"## {_KIND_LABEL[kind]}"]
            for entry in items:
                line = f"- [{entry.get('key', '')}] {entry.get('value', '')}".strip()
                if len(line) > budget:
                    lines.append("- …（笔记预算已满，其余未展示）")
                    sections.append("\n".join(lines))
                    return _notes_header(sections)
                budget -= len(line)
                lines.append(line)
            sections.append("\n".join(lines))
        if not sections:
            return ""
        return _notes_header(sections)

    def stats(self) -> dict[str, Any]:
        self._load()
        by_kind: dict[str, int] = {}
        for entry in self.entries.values():
            kind = str(entry.get("kind") or "?")
            by_kind[kind] = by_kind.get(kind, 0) + 1
        return {
            "root": self.root.name,
            "entries": len(self.entries),
            "by_kind": by_kind,
            "writes": self.writes,
            "degraded": self.degraded or None,
        }


def _composite(kind: MemKind, key: str) -> str:
    return f"{kind.value}\x1f{key}"


def _notes_header(sections: list[str]) -> str:
    body = "\n\n".join(sections)
    return (
        "# 工作记忆（来自上一轮观察，可能已过时；与现状冲突时以工具读到的内容为准）\n" + body
    )
