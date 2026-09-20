"""终端渲染层 —— Agent 过程可见，是用户信任它的前提。

两条硬约束：
1. **一次工具调用只占一行**。八轮工具如果每轮回显整份 stdout，终端就变成
   日志瀑布，用户看不见重点，也看不出 Agent 有没有跑偏。
2. **rich 是可选的**。没装也能跑 —— 只降级成纯文本，绝不因为"美化输出"
   的依赖缺失而让整个 Agent 起不来。
"""

from __future__ import annotations

from typing import Any, Callable

from miniclaude.agent.loop import AgentEvent, EventKind

try:  # rich 只用来自适应换行和着色；缺失时全部退化成 plain text
    from rich.console import Console
    from rich.markdown import Markdown

    _HAS_RICH = True
except ImportError:  # pragma: no cover - 取决于环境
    _HAS_RICH = False

_TOOL_LABEL_ARGS = ("path", "target", "pattern", "command", "todos")
_MAX_LABEL_CHARS = 68


def _plain(text: str) -> None:
    print(text)


class Renderer:
    """把 AgentEvent 流画成人类可读的终端输出。"""

    def __init__(
        self,
        *,
        verbose: bool = False,
        write: Callable[[str], None] | None = None,
        use_rich: bool | None = None,
    ) -> None:
        self.verbose = verbose
        self._write = write or _plain
        self._rich = _HAS_RICH if use_rich is None else use_rich
        self._console: Any = Console(highlight=False) if self._rich else None
        self._pending_args: dict[str, dict[str, Any]] = {}
        self._last_text = ""       # 已经打过的模型正文，避免结尾重复一遍

    # --------------------------------------------------------------- 事件分发

    def handle(self, event: AgentEvent) -> None:
        """事件分发。新增事件种类时必须显式决定"要不要占用户一行"。"""
        kind, data = event.kind, event.payload
        if kind is EventKind.TURN_START:
            if self.verbose:
                self._dim(f"── 第 {data.get('turn', 0)} 轮 " + "─" * 24)
        elif kind is EventKind.ASSISTANT_TEXT:
            self.assistant_text(str(data.get("text", "")))
        elif kind is EventKind.PERMISSION:
            self._permission(data)
        elif kind is EventKind.TOOL_START:
            self._pending_args[str(data.get("tool"))] = dict(data.get("args") or {})
        elif kind is EventKind.TOOL_END:
            self.tool_line(
                str(data.get("tool")),
                self._pending_args.pop(str(data.get("tool")), {}),
                bool(data.get("ok")),
                float(data.get("elapsed") or 0.0),
            )
            if self.verbose and data.get("output"):
                self._preview(str(data["output"]))
        elif kind is EventKind.TODO_UPDATE:
            self.todos(list(data.get("items") or []))
        elif kind is EventKind.WARNING:
            self._write("⚠ " + str(data.get("message") or data.get("reason") or ""))
        elif kind is EventKind.ERROR:
            self.error(str(data.get("message", "")))

    # --------------------------------------------------------------- 输出件

    def banner(self, *, model: str, project_root: Any, tools: list[str], mode: str = "") -> None:
        """启动横幅：模型、工作目录、已注册工具。让人一眼看清 Agent 的能力边界。"""
        lines = [
            f"Mini Claude Code · 模型 {model}",
            f"工作目录 {project_root}",
            f"工具 {len(tools)} 个：{' '.join(tools)}",
        ]
        if mode:
            lines.append(f"权限模式 {mode}")
        lines.append("输入任务开始；/help 看命令，/exit 退出")
        self._box(lines)

    def tool_line(self, name: str, args: dict[str, Any], ok: bool, elapsed: float) -> None:
        """一次工具调用折叠成一行，例如 `· read_file src/a.py  12ms`。"""
        mark = "✓" if ok else "✗"
        label = _label(args)
        timing = f"{elapsed * 1000:.0f}ms" if elapsed < 1 else f"{elapsed:.1f}s"
        self._write(f"{mark} {name:<11} {label}  {timing}".rstrip())

    def assistant_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._last_text = text
        if self._console is not None:
            self._console.print(Markdown(text))
        else:
            self._write(text)

    def final_text(self, text: str) -> None:
        """收尾答复。模型自己已经说过的那句不重复打；
        非正常结束时 _closing_text 合成的解释必须打出来。"""
        text = text.strip()
        if not text or text == self._last_text:
            return
        self.assistant_text(text)

    def error(self, text: str) -> None:
        self._write(f"✗ {text}" if text else "")

    def dim(self, text: str) -> None:
        self._dim(text)

    def usage(self, *, prompt_tokens: int, completion_tokens: int, turns: int) -> None:
        self._dim(
            f"用量：{turns} 轮 · 输入 {prompt_tokens:,} + 输出 {completion_tokens:,} "
            f"= {prompt_tokens + completion_tokens:,} tokens"
        )

    def todos(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        marks = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]", "cancelled": "[-]"}
        self._dim("任务清单")
        for item in items:
            self._dim(f"  {marks.get(str(item.get('status')), '[?]')} {item.get('content', '')}")

    # --------------------------------------------------------------- 内部

    def _permission(self, data: dict[str, Any]) -> None:
        """放行不必刷一行；被拦下必须说清是哪一件事。"""
        if data.get("allowed"):
            if self.verbose:
                self._dim(f"  授权：{data.get('reason')}")
            return
        self._dim(f"  已拦下 {data.get('tool')}：{data.get('reason')}")

    def _preview(self, output: str) -> None:
        lines = [line for line in output.splitlines() if line.strip()][:6]
        for line in lines:
            self._dim(f"  │ {line[:110]}")
        if len(output.splitlines()) > len(lines):
            self._dim("  │ …")

    def _dim(self, text: str) -> None:
        if self._console is not None:
            self._console.print(text, style="dim")
        else:
            self._write(text)

    def _box(self, lines: list[str]) -> None:
        width = max(len(line) for line in lines)
        self._write("┌" + "─" * width + "┐")
        for line in lines:
            self._write("│ " + line)
        self._write("└" + "─" * width + "┘")


def _label(args: dict[str, Any]) -> str:
    """从入参里挑一个最能说明"这次动了什么"的字段。"""
    if "todos" in args:
        staged = args.get("todos") or []
        return f"{len(staged)} 步" if isinstance(staged, list) else "更新清单"

    for key in _TOOL_LABEL_ARGS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            if key in ("path", "target"):
                text = text.replace("\\", "/")  # 反斜杠路径在终端里读起来像转义
            return _shorten(text, _MAX_LABEL_CHARS)
    if args:
        rendered = " ".join(f"{key}={value}" for key, value in args.items() if str(value).strip())
        return _shorten(rendered, _MAX_LABEL_CHARS)
    return ""


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
