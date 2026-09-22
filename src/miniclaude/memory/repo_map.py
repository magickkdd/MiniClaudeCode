"""RepoMap —— 用标准库 `ast` 给模型一张"哪个文件里有什么"的地图（SPEC v2 §3.4）。

它要替代的 MVP 是 `prompts.render_repo_map()` 那张广度优先目录树：目录树只说
"这里有哪些名字"，不说"这个文件是干什么的、里面有什么可调的东西"，而且 30 行预算下
深目录的尾部直接看不见。符号地图把这两件事都补上了，代价是一次解析。

五个决定：

- **`ast` 而不是 tree-sitter**：本项目"实现只为 Python 负责"，`ast.parse` 足以拿到
  模块级函数/类/方法/常量与 import 关系，零依赖、零语法文件维护。
- **排序只用三个可解释的廉价信号**：① 与最近 `read_file`/`edit_file` 的目标同目录，
  ② 文件名出现在当前任务清单里，③ 被仓库内其他模块 import 的次数。不做 PageRank ——
  那需要跨文件引用图加迭代收敛，而 `ast` 只能拿到 import 名，边本来就不准，
  PageRank 只会把"猜"包装成"算过"。
- **每个条目带上入选理由，落选者也点名**。`mcc trace` 必须能回答"地图给了它，
  为什么没给那个文件"；答不了，这条机制就不可 debug。
- **缓存带 import 边**。写文件后的重扫只有 O(文件数) 次 stat，符号行与 import 边都从
  `.mcc/` 里原样取回 —— 否则"热缓存"那一晚信号 ③ 会全变 0，而它恰恰是唯一一个
  不需要模型配合就能算出来的信号。
- **地图在会话内不抖**。`map_for_prompt()` 只在 `invalidate()` 之后重算；焦点变化
  **不**触发重算，因为它是每轮都在动的东西。地图进的是 system 段，每轮换一份等于
  每轮打掉前缀缓存，还会让"这轮和上轮差在哪"无法回答。

预算单位是 token，换算系数 `chars_per_token` 与 `ContextManager` 默认值取同一个数。
真实记账走的是 `wire_chars(system=...)`，所以这里只需要"别让地图把预算吃光"。
"""

from __future__ import annotations

import ast
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, Literal, Sequence

from miniclaude.memory.store import MemKind, MemoryStore, file_fingerprint
from miniclaude.tools.workspace import Workspace

SymbolKind = Literal["module", "class", "def", "method", "const"]

DEFAULT_TOKEN_CAP = 1500
DEFAULT_CHARS_PER_TOKEN = 3.5
MAX_SOURCE_BYTES = 256_000      # 生成的巨型 py 不值得进地图
MAX_SIGNATURE_CHARS = 70
MAX_DOC_CHARS = 50
FOOTER_RESERVE = 160            # 给"未列出"名单固定留的字符数
FOCUS_MEMORY = 8                # 记住最近几个读/改目标（信号 ①）
MAX_SYMBOLS_PER_FILE = 16       # 单个文件最多几行符号
MAX_CONSTS_PER_FILE = 8         # 其中最多几个是常量

# 分数只要求"量级可读"：模型分不清 47 和 40，人能从 trace 里看出谁压过了谁。
SCORE_DIRECT = 60               # 焦点里就是这个文件
SCORE_SAME_DIR = 40             # 信号 ①
SCORE_TODO = 25                 # 信号 ②
SCORE_IMPORT = 8                # 信号 ③，× 被 import 的文件数
SCORE_IMPORT_CAP = 24
SCORE_PACKAGE = 10              # __init__.py 通常是包的门面
SCORE_DEPTH = 2                 # 同分时浅的先出；乘目录深度，最多扣 12

REASON_DIRECT = "最近读/改的就是它"
REASON_SAME_DIR = "与最近读/改同目录"
REASON_TODO = "任务清单点了这个文件名"
REASON_IMPORT = "被 {n} 个模块 import"
REASON_PACKAGE = "包入口"

_TAGS = {
    REASON_DIRECT: "recent",
    REASON_SAME_DIR: "same-dir",
    REASON_TODO: "todo",
    REASON_IMPORT: "imported",
    REASON_PACKAGE: "package",
}


@dataclass(frozen=True)
class Symbol:
    kind: SymbolKind
    name: str
    qualname: str
    lineno: int
    signature: str
    doc_first_line: str


@dataclass
class MapStats:
    """一次建图/渲染的账。`repo_map` 这个 trace 事件的字段口径以 `as_trace()` 为准。"""

    modules_found: int = 0
    modules_parsed: int = 0
    from_cache: int = 0
    unparsable: int = 0
    listed: int = 0
    omitted: int = 0
    chars: int = 0
    est_tokens: int = 0
    token_cap: int = DEFAULT_TOKEN_CAP
    focus: tuple[str, ...] = ()
    reasons: tuple[dict[str, object], ...] = ()
    rebuilt: bool = False
    note: str = ""

    def as_trace(self) -> dict[str, object]:
        payload = {key: value for key, value in vars(self).items() if key not in ("focus", "reasons")}
        payload["focus"] = list(self.focus)
        # 理由要能被 `mcc trace` 回答，但 40 条以内就够看清趋势；整张表在报表侧另算。
        payload["reasons"] = [dict(item) for item in self.reasons[:40]]
        return payload


@dataclass
class RepoMap:
    """`build()` 给全量地图，`relevant()` 给"当前该看的那几份"，`invalidate()` 在写文件后作废。"""

    ws: Workspace
    store: MemoryStore | None = None
    token_cap: int = DEFAULT_TOKEN_CAP
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN
    max_files: int = 2_000
    max_symbols_per_file: int = MAX_SYMBOLS_PER_FILE

    _lines: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _cache: dict[str, tuple[tuple[float, int], list[str], list[str]]] = field(
        default_factory=dict, repr=False
    )
    _index: dict[str, str] = field(default_factory=dict, repr=False)
    _imports: Counter[str] = field(default_factory=Counter, repr=False)
    _scanned: bool = False
    _rescan: bool = False
    _dirty: bool = True
    _rendered: str = ""
    _focus: list[str] = field(default_factory=list, repr=False)
    refreshes: int = 0
    stats: MapStats = field(default_factory=MapStats)

    # ------------------------------------------------------------ 对外

    def build(self, ws: Workspace | None = None, *, token_cap: int | None = None) -> str:
        """无焦点的全量地图。`ws` 只是 SPEC 签名的兼容位：换工作区请重建本对象。"""
        if ws is not None and Path(ws.root) != Path(self.ws.root):
            raise ValueError("RepoMap 与工作区一一绑定；要换目录请重建 RepoMap")
        return self.relevant([], token_cap=token_cap)

    def relevant(
        self,
        focus: Sequence[str] = (),
        *,
        token_cap: int | None = None,
        todo_text: str = "",
    ) -> str:
        """打分 + 预算裁剪后的地图文本；仓库里没有可列出的 Python 文件时返回空串。

        `focus` 是最近读/改过的路径（信号 ①），`todo_text` 是任务清单原文（信号 ②）。
        后者单独开参数而不是混进 `focus`：那是文本不是路径，混在一起两个信号就会互相
        接受对方的输入，也就没人能从 trace 里复现某个分数是怎么来的了。
        """
        self._ensure_scanned()
        cap = self.token_cap if token_cap is None else int(token_cap)
        scored = self._score(focus, todo_text)
        if not scored:
            self.stats = MapStats(
                modules_found=self.stats.modules_found,
                unparsable=self.stats.unparsable,
                token_cap=cap,
                note=self.stats.note,
            )
            return ""
        blocks, kept, dropped, used = self._trim(scored, cap)
        self.stats = MapStats(
            modules_found=self.stats.modules_found,
            modules_parsed=self.stats.modules_parsed,
            from_cache=self.stats.from_cache,
            unparsable=self.stats.unparsable,
            listed=len(kept),
            omitted=len(dropped),
            chars=used,
            est_tokens=int(used / max(self.chars_per_token, 0.1)),
            token_cap=cap,
            focus=tuple(str(item) for item in focus),
            reasons=tuple(
                {"file": rel, "score": score, "reasons": list(reasons), "listed": listed}
                for score, reasons, rel, listed in [*kept, *dropped]
            ),
            rebuilt=self.stats.rebuilt,
            note=self.stats.note,
        )
        return self._compose(blocks, kept, dropped, cap=cap, used=used)

    def map_for_prompt(self, *, focus: Sequence[str] | None = None, todo_text: str = "") -> str:
        """提示词入口：仓库没变就返回同一个字符串，`invalidate()` 之后才重算。

        `focus=None` 用 `observe_paths()` 攒下来的最近读/改目标；显式传参时以参数为准
        （测试要能钉住"给这个焦点就出这个地图"，不去猜历史）。
        """
        if self._dirty:
            self._rendered = self.relevant(
                list(self._focus if focus is None else focus), todo_text=todo_text
            )
            self._dirty = False
            self.refreshes += 1
            self.stats = MapStats(**{**vars(self.stats), "rebuilt": True})
        return self._rendered

    def observe_paths(self, paths: Iterable[str], *, limit: int = FOCUS_MEMORY) -> None:
        """记下最近读/改的目标 —— 信号 ① 的来源（SPEC §3.4 写的就是 `read_file`/`edit_file`）。

        改过的也要记：agent 正在编辑的那片目录恰恰是最该出现在窗口里的。
        新的排前面、去重、只留 `limit` 条：焦点列表只用来打分，留太长会让一个早就
        看完的目录一直占着高分，把真正在改的那片挤出去。
        """
        for raw in paths:
            rel = self._as_rel(raw)
            if not rel:
                continue
            if rel in self._focus:
                self._focus.remove(rel)
            self._focus.insert(0, rel)
        del self._focus[limit:]

    def forget_focus(self) -> None:
        """清空焦点并安排重画：`/reset` 之后是新任务，上一个任务的现场不该继续影响排序。"""
        self._focus.clear()
        self._dirty = True

    def invalidate(self, changed: Iterable[str]) -> None:
        """文件被写过：作废受影响条目，并安排一次重扫。

        重扫是整树 walk 而不是只处理这几个路径，因为 `write_file` 可能建出**新**文件 ——
        不重扫的话新文件永远进不了地图，而"agent 刚建的文件下一轮看不见"是会让它
        重写一遍已有文件的。符号行与 import 边都命中缓存，所以重扫只是 stat 一遍。
        """
        touched = [self._as_rel(item) for item in changed if str(item).strip()]
        if not touched:
            return
        for rel in touched:
            self._cache.pop(rel, None)
            self._lines.pop(rel, None)
            if self.store is not None:
                self.store.forget(MemKind.REPO_MAP, rel)
        self._dirty = True
        self._rescan = True

    def symbols_of(self, rel: str) -> list[Symbol]:
        """给测试与诊断用：某个文件当前的符号（缓存里只有渲染后的行，反解不出来）。"""
        path = self.ws.root / self._as_rel(rel)
        try:
            return extract_symbols(path.read_text(encoding="utf-8"), rel=self._as_rel(rel))
        except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
            return []

    # ------------------------------------------------------------ 建图

    def _ensure_scanned(self) -> None:
        if self._scanned and not self._rescan:
            return
        self._scanned, self._rescan = True, False
        found = self._python_files()
        # 索引一次建好、每个文件查一次：在解析循环里现场扫 known 会把建图变成 O(n²)，
        # 而 §6.2 给全量建图的上限是 800ms。
        self._index = build_import_index(found)
        imports: Counter[str] = Counter()
        parsed = cached = broken = 0
        self._lines = {}
        for rel in found:
            path = self.ws.root / rel
            try:
                fingerprint = file_fingerprint(path)
            except OSError:
                broken += 1
                continue
            memory = self._cache.get(rel)
            if memory is not None and memory[0] == fingerprint:
                _, lines, targets = memory
                cached += 1
            else:
                lines, targets = self._from_store(rel, fingerprint)
                if lines is None:
                    lines, targets, note = self._parse(path, rel)
                    parsed += 1
                    if note:
                        broken += 1
                    if self.store is not None:
                        self.store.put(
                            MemKind.REPO_MAP,
                            rel,
                            json.dumps({"lines": lines, "imports": targets}, ensure_ascii=False),
                            fingerprint={rel: [float(fingerprint[0]), float(fingerprint[1])]},
                        )
                else:
                    cached += 1
                self._cache[rel] = (fingerprint, lines, targets)
            self._lines[rel] = lines
            for target in targets:
                imports[target] += 1
        if self.store is not None:
            self.store.flush()
        self._imports = imports
        self.stats = MapStats(
            modules_found=len(found),
            modules_parsed=parsed,
            from_cache=cached,
            unparsable=broken,
            token_cap=self.token_cap,
            note=f"解析 {parsed} · 复用缓存 {cached} · 不可解析 {broken}",
        )

    def _python_files(self) -> list[str]:
        """工作区里全部 .py。跳过忽略目录（含 `.mcc/`），排序后返回，两次跑同一张图。"""
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.ws.root):
            here = Path(dirpath)
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if not name.startswith(".") and not self.ws.is_ignored(here / name)
            ]
            for name in sorted(filenames):
                if not name.endswith(".py") or self.ws.is_ignored(here / name):
                    continue
                out.append((here / name).relative_to(self.ws.root).as_posix())
                if len(out) >= self.max_files:
                    return out
        return out

    def _from_store(self, rel: str, fingerprint: tuple[float, int]) -> tuple[None, None] | tuple[list[str], list[str]]:
        """跨会话命中。指纹不认就宁可重解析，也不给模型看上一轮的旧地图。"""
        if self.store is None:
            return None, None
        text = self.store.get(MemKind.REPO_MAP, rel)
        if text is None:
            return None, None
        stored = self.store.fingerprint_of(MemKind.REPO_MAP, rel).get(rel) or []
        if len(stored) != 2 or (round(float(stored[0]), 3), int(stored[1])) != fingerprint:
            return None, None
        try:
            payload = json.loads(text)
            lines = [str(line) for line in payload["lines"]]
            targets = [str(item) for item in payload["imports"]]
        except (ValueError, KeyError, TypeError):
            return None, None
        return lines, targets

    def _parse(self, path: Path, rel: str) -> tuple[list[str], list[str], str]:
        """→ (符号行, 仓库内 import 目标, 不可解析原因)。"""
        try:
            if path.stat().st_size > MAX_SOURCE_BYTES:
                return [], [], f"{rel} 超过 {MAX_SOURCE_BYTES:,} 字节，跳过"
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return [], [], f"{rel} 读不出来"
        try:
            symbols, targets = analyze_source(source, rel=rel, index=self._index)
            lines = render_file_lines(symbols, max_symbols=self.max_symbols_per_file)
        except (SyntaxError, ValueError, RecursionError):
            # 语法错误是常态（agent 正改到一半），不是故障：跳过它，下次 invalidate 再进图。
            return [], [], f"{rel} 解析失败"
        return lines, targets, ""

    # ------------------------------------------------------------ 打分与裁剪

    def _score(self, focus: Sequence[str], todo_text: str) -> list[tuple[int, list[str], str]]:
        rels = [self._as_rel(item) for item in focus if str(item).strip()]
        focus_files = {item for item in rels if item.endswith(".py")}
        focus_dirs = {PurePosixPath(item).parent.as_posix() for item in rels}
        lowered = todo_text.lower()
        scored: list[tuple[int, list[str], str]] = []
        for rel, lines in self._lines.items():
            if not lines:
                # 只有"解析被跳过"（语法错误、超大）才会空：能解析的文件至少有一行文档哨兵，
                # 所以一个没有任何模块级符号的脚本依然会进图 —— 地图要先保证"这文件存在"。
                continue
            reasons: list[str] = []
            score = 0
            parent = PurePosixPath(rel).parent.as_posix()
            if rel in focus_files:
                score += SCORE_DIRECT
                reasons.append(REASON_DIRECT)
            elif parent in focus_dirs:
                score += SCORE_SAME_DIR
                reasons.append(REASON_SAME_DIR)
            stem = PurePosixPath(rel).stem
            if stem and stem != "__init__" and stem in lowered:
                score += SCORE_TODO
                reasons.append(REASON_TODO)
            used_by = self._imports.get(rel, 0)
            if used_by:
                score += min(used_by * SCORE_IMPORT, SCORE_IMPORT_CAP)
                reasons.append(REASON_IMPORT.format(n=used_by))
            if PurePosixPath(rel).name == "__init__.py":
                score += SCORE_PACKAGE
                reasons.append(REASON_PACKAGE)
            depth = 0 if parent == "." else parent.count("/") + 1
            score -= min(depth * SCORE_DEPTH, 12)
            scored.append((score, reasons, rel))
        scored.sort(key=lambda item: (-item[0], item[2]))
        return scored

    def _trim(
        self, scored: Sequence[tuple[int, list[str], str]], cap: int
    ) -> tuple[list[str], list[tuple[int, list[str], str, bool]], list[tuple[int, list[str], str, bool]], int]:
        """按分数从高往下装，装不下就落到"未列出"名单里。

        固定预留 `FOOTER_RESERVE` 给落选名单：不给它留地方就会被最后一个文件挤掉，
        而它恰恰是"为什么没给那个文件"的唯一答案。
        """
        budget = int(cap * self.chars_per_token)
        used = 0
        blocks: list[str] = []
        kept: list[tuple[int, list[str], str, bool]] = []
        dropped: list[tuple[int, list[str], str, bool]] = []
        for score, reasons, rel in scored:
            block = self._block(reasons, rel)
            cost = sum(len(line) + 1 for line in block)
            if used + cost > budget - FOOTER_RESERVE:
                dropped.append((score, reasons, rel, False))
                continue
            blocks.extend(block)
            kept.append((score, reasons, rel, True))
            used += cost
        return blocks, kept, dropped, used

    def _block(self, reasons: Sequence[str], rel: str) -> list[str]:
        """一个文件一块：`路径 · 文档首行 · [理由标签]` 抬头，下面缩进符号行。

        第 0 行固定是 `render_file_lines()` 写的文档哨兵（`· xxx` 或 `·`），
        所以这里无条件切掉它，不按"像不像文档"猜。
        """
        lines = self._lines.get(rel) or []
        doc = ""
        body = list(lines)
        if body and body[0].startswith("·"):
            doc = body[0][1:].strip()
            body = body[1:]
        tags = ",".join(_TAGS[reason] for reason in reasons[:3] if reason in _TAGS)
        head = rel + (f" · {doc}" if doc else "") + (f" · [{tags}]" if tags else "")
        if not body:
            return [head + " ·（无模块级符号）"]
        return [head, *[f"  {line}" for line in body]]

    def _compose(
        self,
        blocks: Sequence[str],
        kept: Sequence[tuple[int, list[str], str, bool]],
        dropped: Sequence[tuple[int, list[str], str, bool]],
        *,
        cap: int,
        used: int,
    ) -> str:
        head = (
            f"# 仓库地图（ast 符号 · {self.stats.modules_found} 个模块列出 {len(kept)} 个"
            f" · {used:,}/{int(cap * self.chars_per_token):,} 字符，预算 {cap:,} token）"
        )
        lines = [head, *blocks]
        if dropped:
            named = "、".join(
                f"{rel}({score})" for score, _, rel, _ in sorted(dropped, key=lambda item: -item[0])[:6]
            )
            more = f"，另有 {len(dropped) - 6} 个分数更低" if len(dropped) > 6 else ""
            lines.append(f"未列出（{len(dropped)} 个，按分数排序最靠后）：{named}{more}")
        lines.append("地图只给形状与用途；具体行为必须 read_file 确认，不要依据地图猜实现。")
        return "\n".join(lines)

    def _as_rel(self, raw: str | Path) -> str:
        text = str(raw).replace("\\", "/").strip()
        root = Path(self.ws.root).as_posix().rstrip("/")
        for prefix in (f"{root}/", "./", "/"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        return text


# ---------------------------------------------------------------- 纯函数


def extract_symbols(source: str, *, rel: str = "") -> list[Symbol]:
    """模块文档串 + 模块级函数/类/方法/常量。单独要用它时不需要先 parse。"""
    return symbols_from_tree(ast.parse(source), rel=rel)


def symbols_from_tree(tree: ast.Module, *, rel: str = "") -> list[Symbol]:
    """签名的规范形态 `def name(args) -> ret` 与评测层 `_public_api()` 用同一个口径
    （`ast.unparse`）：两边各写一套归一化规则的话，"逐字一致"就变成假判据。
    """
    out: list[Symbol] = [
        Symbol("module", rel or "<string>", rel, 1, rel, _doc_first(ast.get_docstring(tree)))
    ]
    for node in tree.body:
        out.extend(_symbols_for(node))
    return out


def _symbols_for(node: ast.stmt) -> list[Symbol]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [
            Symbol(
                kind="def",
                name=node.name,
                qualname=node.name,
                lineno=node.lineno,
                signature=function_signature(node),
                doc_first_line=_doc_first(ast.get_docstring(node)),
            )
        ]
    if isinstance(node, ast.ClassDef):
        methods = [
            Symbol(
                kind="method",
                name=child.name,
                qualname=f"{node.name}.{child.name}",
                lineno=child.lineno,
                signature=function_signature(child),
                doc_first_line=_doc_first(ast.get_docstring(child)),
            )
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        return [
            Symbol(
                kind="class",
                name=node.name,
                qualname=node.name,
                lineno=node.lineno,
                signature=class_signature(node),
                doc_first_line=_doc_first(ast.get_docstring(node)),
            ),
            *methods,
        ]
    if isinstance(node, ast.Assign):
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        # 一个赋值只出一行：`A = B = 1` 在地图里没有额外信息量。
        consts = [name for name in names if name.replace("_", "").isupper()]
        return [Symbol("const", name, name, node.lineno, const_signature(node, name), "") for name in consts[:1]]
    return []


def build_import_index(paths: Iterable[str]) -> dict[str, str]:
    """`尾部路径 → 仓库内文件`。一次建好，`parse_imports` 只做字典查找。

    歧义（两个目录里都有 `workspace.py`）按字母序取第一个：这是排序信号的计数，
    不是导入语义的实现，猜错的代价是某个文件的分数低一点。
    """
    index: dict[str, str] = {}
    for candidate in sorted(str(item).replace("\\", "/") for item in paths):
        parts = candidate.split("/")
        for start in range(len(parts)):
            index.setdefault("/".join(parts[start:]), candidate)
    return index


def parse_imports(source: str, *, rel: str, index: dict[str, str]) -> list[str]:
    return imports_from_tree(ast.parse(source), rel=rel, index=index)


def imports_from_tree(tree: ast.Module, *, rel: str, index: dict[str, str]) -> list[str]:
    """把 import 的名字落到**仓库内**的文件上（去重、排除自己）。

    相对 import 按目录精确解析；绝对 import 按尾部组件查 `index`。不做 sys.path 求解 ——
    这个输出只是三个排序信号之一的计数，不是导入语义的实现。
    """
    here = PurePosixPath(rel).parent
    found: list[str] = []

    def locate(dotted: str) -> None:
        path = dotted.strip("/").replace(".", "/")
        if not path:
            return
        for probe in (f"{path}.py", f"{path}/__init__.py"):
            hit = index.get(probe)
            if hit:
                found.append(hit)
                return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                locate(alias.name)
        elif isinstance(node, ast.ImportFrom):
            parts = list(here.parts)
            for _ in range(max(node.level - 1, 0)):
                if parts:
                    parts.pop()
            if node.level:
                base = "/".join([*parts, (node.module or "").replace(".", "/")]).strip("/")
                locate(base)
                for alias in node.names:  # `from . import store` 里的 store 是兄弟文件
                    locate(f"{base}/{alias.name}")
            else:
                prefix = (node.module or "").replace(".", "/")
                locate(prefix)
                for alias in node.names:  # `from pkg import module` 里的 module 可能是文件
                    locate(f"{prefix}/{alias.name}")
    out: list[str] = []
    for item in found:
        if item and item != rel and item not in out:
            out.append(item)
    return out


def analyze_source(source: str, *, rel: str, index: dict[str, str]) -> tuple[list[Symbol], list[str]]:
    """**一次** parse 同时拿到符号与 import 边。

    拆成两次 parse 会把建图成本直接翻倍，而 §6.2 给全量建图的上限只有 800ms ——
    这个函数存在的理由就是那条预算。
    """
    tree = ast.parse(source)
    return symbols_from_tree(tree, rel=rel), imports_from_tree(tree, rel=rel, index=index)


def render_file_lines(
    symbols: Sequence[Symbol],
    *,
    max_symbols: int = MAX_SYMBOLS_PER_FILE,
    max_consts: int = MAX_CONSTS_PER_FILE,
) -> list[str]:
    """一个文件的符号行。**第一行固定是文档哨兵** `· 模块文档首行`（没有文档时是 `·`），
    其余是类/函数/常量行。缓存里存的就是这个列表，`_block()` 按"第 0 行是文档"来切，
    所以这个约定不能改。

    两条裁剪都是为了把预算花在"能被导航的东西"上：下划线开头的名字不进地图
    （地图是索引，私有细节 read_file 才看得见），常量单独限额（一个文件里
    `A = 1` 堆到二十行，比少列两个文件更不值得）。
    """
    doc = next((item.doc_first_line for item in symbols if item.kind == "module"), "")
    entries = [item for item in symbols if item.kind != "module" and not item.name.startswith("_")]
    body: list[str] = []
    seen_consts = 0
    for index, item in enumerate(entries):
        if len(body) >= max_symbols:
            body.append(f"…另有 {len(entries) - index} 个符号未列")
            break
        if item.kind == "const":
            seen_consts += 1
            if seen_consts > max_consts:
                continue
        text = item.signature
        if item.kind == "method" and text.startswith("def "):
            text = text[4:]  # 缩进已经说明它是方法，`def ` 是纯开销
        body.append(f"{text} · {item.doc_first_line}" if item.doc_first_line else text)
    if not doc and not body:
        return ["·"]
    return [f"· {doc}".rstrip(), *body]


def function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = _unparse(node.args, "...")
    returns = f" -> {_unparse(node.returns)}" if node.returns is not None else ""
    return _clip(f"def {node.name}({args}){returns}", MAX_SIGNATURE_CHARS)


def class_signature(node: ast.ClassDef) -> str:
    bases = [base for base in (_unparse(item) for item in node.bases) if base]
    text = f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
    return _clip(text, MAX_SIGNATURE_CHARS)


def const_signature(node: ast.Assign, name: str) -> str:
    return _clip(f"{name} = {_unparse(node.value, '...')}", MAX_SIGNATURE_CHARS)


def _unparse(node: object, fallback: str = "") -> str:
    if node is None:
        return fallback
    try:
        return " ".join(str(ast.unparse(node)).split())  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - 签名只是提示词，还原失手就退回空
        return fallback


def _doc_first(doc: str | None) -> str:
    lines = (doc or "").strip().splitlines()
    return _clip(lines[0] if lines else "", MAX_DOC_CHARS)


def _clip(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"
