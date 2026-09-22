"""Skills —— 延迟加载的提示词（SPEC v2 §3.7、D20）。

架构含义只有一句话：**上下文是预算资源，谁进来都要报价**。
常驻的只有目录（`catalog()`，≤30 行，只有名字和一句话），正文要靠
`load_skill` 工具按需展开。这和 §3.3 的压缩阶梯是同一个思路的两面。

一个技能 = 一个目录里的一个 `SKILL.md`：

    ---
    name: write-eval-task
    description: 给 eval/ 加一道新题时怎么写判据
    when_to_use: 用户要求"加一道评测任务"时
    ---
    正文指令……

**不实现技能自带脚本**（D20）：SKILL.md 旁边如果有别的文件，这里只报出它们的名字，
既不读也不执行。多文件引用在 §7.4 顺位 5 里 —— 保留单文件是一条明确的取舍，
不是没做完。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from miniclaude.tools.base import BaseTool, RiskLevel, ToolResult
from miniclaude.tools.workspace import Workspace

SKILL_FILE = "SKILL.md"
CATALOG_TITLE = "# 可用技能（这里只有目录，正文要用 load_skill 取）"
CATALOG_HINT = "用到哪一条再取，别一次全取进来 —— 取进来的正文会占住后面的每一轮。"

MAX_CATALOG_LINES = 30
MAX_DESCRIPTION_CHARS = 110
MAX_SKILL_CHARS = 12_000  # 单个技能正文的硬上限：超了就是提示词写成了文档

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._\-]{0,63}$")
_FRONT_KEY = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*)$")
_MAX_HEADER_LINES = 60  # 超过这个行数还没闭合，就当这份 SKILL.md 没写头


class SkillError(ValueError):
    """技能目录非法或技能取不出来。"""


@dataclass(frozen=True)
class SkillManifest:
    """目录里的一行。`body_chars` 是它"要报价"的那部分。"""

    name: str
    description: str
    path: Path
    when_to_use: str = ""
    body_chars: int = 0
    siblings: tuple[str, ...] = ()

    def as_row(self) -> str:
        line = f"- {self.name}：{self.description[:MAX_DESCRIPTION_CHARS]}"
        if self.when_to_use:
            line += f"（何时用：{self.when_to_use[:60]}）"
        return line


@dataclass
class SkillLoader:
    """扫一个目录，产出目录行与正文。"""

    root: Path
    max_catalog_lines: int = MAX_CATALOG_LINES
    _cache: dict[str, SkillManifest] | None = field(default=None, init=False, repr=False)
    rejected: list[dict[str, str]] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------- 发现

    def manifests(self) -> list[SkillManifest]:
        if self._cache is None:
            self._cache = {}
            self.rejected = []
            if not self.root.is_dir():
                return []
            for entry in sorted(self.root.iterdir(), key=lambda item: item.name.lower()):
                if not entry.is_dir() or entry.name.startswith((".", "_")):
                    continue
                manifest, problem = self._read(entry)
                if problem is not None:
                    self.rejected.append({"dir": entry.name, "reason": problem})
                    continue
                assert manifest is not None
                if manifest.name in self._cache:
                    self.rejected.append({"dir": entry.name, "reason": f"技能名 {manifest.name} 重复"})
                    continue
                self._cache[manifest.name] = manifest
        return [self._cache[key] for key in sorted(self._cache)]

    def _read(self, entry: Path) -> tuple[SkillManifest | None, str | None]:
        skill = entry / SKILL_FILE
        if not skill.is_file():
            return None, f"没有 {SKILL_FILE}"
        try:
            fields, body = _parse_frontmatter(skill.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            return None, f"读不了：{exc}"
        name = (fields.get("name") or entry.name).strip().lower()
        if not _SAFE_NAME.match(name):
            # load_skill 按名字取，名字进不了安全字符集就没有"取的时候不越界"的保证。
            return None, f"技能名不合法：{name!r}"
        description = (fields.get("description") or "").strip()
        if not description:
            return None, f"{SKILL_FILE} 的 frontmatter 缺 description（目录里没法一句话说明它）"
        if not body.strip():
            return None, "正文是空的"
        siblings = tuple(
            sorted(item.name for item in entry.iterdir() if item.is_file() and item.name != SKILL_FILE)
        )
        return (
            SkillManifest(
                name=name,
                description=description,
                path=skill,
                when_to_use=(fields.get("when_to_use") or "").strip(),
                body_chars=len(body),
                siblings=siblings,
            ),
            None,
        )

    # ------------------------------------------------------------- 两条出口

    def catalog(self) -> str:
        """注入 system 的那一段。**长度只和技能个数有关，与正文长度无关** —— 这条
        由 `test_skill_lazy_load_costs` 钉住：正文再长也不许把目录撑大。

        超预算时少列一个技能，也不丢掉结尾那句"别一次全取进来" —— 溢出说明和
        成本提示才是这段文字里唯一影响模型行为的部分，条目少一条它照样能干活。
        """
        rows = [item.as_row() for item in self.manifests()]
        if not rows:
            return ""
        budget = max(self.max_catalog_lines, 4)  # 标题 + 提示 + 溢出说明 + 至少一行
        room = budget - 2
        if len(rows) > room:
            room = budget - 3
        hidden = rows[room:]
        lines = [CATALOG_TITLE, *rows[:room]]
        if hidden:
            names = ", ".join(row[2:].split("：")[0] for row in hidden[:5])
            lines.append(f"- …另有 {len(hidden)} 个技能未列出：{names}")
        lines.append(CATALOG_HINT)
        return "\n".join(lines)

    def load(self, name: str) -> str:
        """按需展开。只认目录里已有的名字 —— 参数里没有路径，就没有越界这条路。"""
        key = str(name or "").strip().lower()
        manifest = self.manifests_by_name().get(key)
        if manifest is None:
            known = ", ".join(sorted(self.manifests_by_name())) or "（这个目录里一个技能都没有）"
            raise SkillError(f"没有名为 {name!r} 的技能。可用的有：{known}")
        _, body = _parse_frontmatter(manifest.path.read_text(encoding="utf-8", errors="replace"))
        body = body.strip()
        if len(body) > MAX_SKILL_CHARS:
            body = body[:MAX_SKILL_CHARS] + f"\n…（正文超出 {MAX_SKILL_CHARS} 字符上限，已截断）"
        text = f"# 技能 {manifest.name}\n\n{body}"
        if manifest.siblings:
            listed = ", ".join(manifest.siblings)
            text += (
                f"\n\n（同目录下还有 {len(manifest.siblings)} 个文件未加载：{listed}。"
                "本客户端不读技能自带的其他文件，也不执行技能脚本 —— SPEC v2 D20。）"
            )
        return text

    def manifests_by_name(self) -> dict[str, SkillManifest]:
        return {item.name: item for item in self.manifests()}

    def stats(self) -> dict[str, Any]:
        rows = self.manifests()
        catalog = self.catalog()
        return {
            "root": str(self.root),
            "skills": len(rows),
            "names": [row.name for row in rows],
            "catalog_lines": len(catalog.splitlines()) if catalog else 0,
            "catalog_chars": len(catalog),
            "body_chars_total": sum(row.body_chars for row in rows),
            "rejected": len(self.rejected),
            "cap_lines": self.max_catalog_lines,
        }


class LoadSkillTool(BaseTool):
    """把某个技能的正文读进上下文。只读，所以 AUTO 模式下不会卡住无人值守跑批。"""

    name = "load_skill"
    description = (
        "按名字取一个技能的完整指令正文（system 里只有目录，没有正文）。"
        "名字必须逐字来自那份目录；取回来的内容会留在上下文里，所以一次只取真正要用的那个。"
    )
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "目录里给出的技能名"}},
        "required": ["name"],
    }
    risk_level = RiskLevel.READ

    def __init__(self, workspace: Workspace, loader: SkillLoader) -> None:
        super().__init__(workspace)
        self.loader = loader

    def run(self, *, name: str = "") -> ToolResult:
        try:
            return ToolResult.ok(self.loader.load(name))
        except SkillError as exc:
            return ToolResult.err(str(exc))


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 `---` 包起来的 `key: value` 头，返回 `(字段, 正文)`。

    刻意不引入 YAML：依赖预算（§2.4）里没有它，而且技能头需要支持的只有平铺的
    字符串键 —— 用 20 行手写解析换掉一个第三方依赖，是这笔账算得过来的那种。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    fields: dict[str, str] = {}
    for index, line in enumerate(lines[1 : _MAX_HEADER_LINES + 1], start=1):
        if line.strip() in {"---", "..."}:
            body = "\n".join(lines[index + 1 :])
            for raw in lines[1:index]:
                match = _FRONT_KEY.match(raw.strip())
                if match:
                    fields[match.group(1).strip().lower()] = _unquote(match.group(2).strip())
            return fields, body
    return {}, text  # 没有闭合的 `---`：整篇当正文，别猜用户想写什么


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
