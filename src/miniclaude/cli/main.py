"""REPL 入口 —— 把所有层装配起来跑。

`python main.py` / `python -m miniclaude` / 安装后的 `mcc`。
CLI 层不写任何业务逻辑，只负责：装配依赖、读输入、分发斜杠命令、渲染。

装配集中在 `build_session()` 一处。REPL 与 `--task` 一次性模式共用同一张
依赖图 —— 否则 demo 跑通的东西和用户手上跑的不是同一个东西。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from miniclaude.agent.context import ContextManager
from miniclaude.agent.loop import Agent
from miniclaude.agent.permissions import Answer, PermissionGate, PermissionMode
from miniclaude.agent.planner import TodoList
from miniclaude.agent.prompts import build_system_prompt
from miniclaude.agent.state import AgentResult, TerminationReason
from miniclaude.agent.todo_tool import WriteTodosTool
from miniclaude.config import Config, ConfigError, get_config
from miniclaude.cli.render import Renderer
from miniclaude.infra.trace import Tracer, summarize
from miniclaude.llm.openai_compat import OpenAICompatClient
from miniclaude.tools.registry import ToolRegistry
from miniclaude.tools.workspace import Workspace

PROMPT = "你 > "
MODE_FLAGS = {"ask": PermissionMode.ASK, "auto": PermissionMode.AUTO, "readonly": PermissionMode.READONLY}
HELP = """命令：
  /help              看这个帮助
  /reset             清空对话历史与任务清单（重新开始）
  /tools             列出工具与风险级别
  /context           显示上下文用量与估算系数
  /todos             显示当前任务清单
  /mode <模式>       切换权限模式：ask / auto / readonly
  /trace             显示本次会话日志位置与统计
  /exit              退出（也可以按 Ctrl-D）"""


class RequestedExit(Exception):
    """斜杠命令要求退出 REPL。它不是错误，所以不混进返回值。"""


@dataclass
class Session:
    """一次 CLI 会话持有的全部装配产物。"""

    agent: Agent
    config: Config
    renderer: Renderer
    tracer: Tracer
    workspace: Workspace
    history: list[AgentResult] = field(default_factory=list)

    @property
    def registry(self) -> ToolRegistry:
        return self.agent.registry

    @property
    def gate(self) -> PermissionGate:
        return self.agent.gate


def build_session(
    *,
    config: Config | None = None,
    mode: PermissionMode = PermissionMode.ASK,
    verbose: bool = False,
    renderer: Renderer | None = None,
    confirmer: Callable[[str, str], Answer] | None = None,
    llm: Any = None,
    use_rich: bool | None = None,
) -> Session:
    """装配依赖图：Config -> Workspace/Registry/Gate/Tracer/LLM -> Agent。"""
    cfg = config or get_config()
    workspace = Workspace(cfg.project_root, output_limit=cfg.tool_output_limit)
    todos = TodoList()
    registry = ToolRegistry.default(
        workspace, bash_timeout=cfg.bash_timeout, extra_tools=[WriteTodosTool(workspace, todos)]
    )
    gate = PermissionGate(workspace=workspace, mode=mode, confirmer=confirmer or make_confirmer())
    tracer = Tracer(cfg.trace_path)
    tracer.start_session(model=cfg.model, tools=registry.names(), config=cfg.redacted())

    render = renderer if renderer is not None else Renderer(verbose=verbose, use_rich=use_rich)
    client = llm if llm is not None else OpenAICompatClient(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        timeout=cfg.request_timeout,
    )
    agent = Agent(
        llm=client,
        registry=registry,
        gate=gate,
        system_prompt=build_system_prompt(
            project_root=workspace.root,
            platform=sys.platform,
            model=cfg.model,
            python_executable=sys.executable,
            tool_names=registry.names(),
            workspace=workspace,
        ),
        max_turns=cfg.max_turns,
        max_total_tokens=cfg.max_total_tokens,
        context=ContextManager(budget=cfg.token_budget),
        todos=todos,
        tracer=tracer,
        on_event=render.handle,
    )
    return Session(agent=agent, config=cfg, renderer=render, tracer=tracer, workspace=workspace)


def make_confirmer(*, ask: Callable[[str], str] = input, echo: Callable[[str], None] = print) -> Any:
    """交互式确认。任何中断或看不懂的输入都按"拒绝"处理。"""

    def confirm(tool_name: str, summary: str) -> Answer:
        echo("")
        echo(f"  需要授权 · {tool_name}：{summary}")
        echo("  [y] 只做这一次   [a] 本会话内同类都允许   [n] 拒绝")
        try:
            reply = ask("  你的选择 > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            echo("  （中断，按拒绝处理）")
            return Answer.NO
        if reply in {"a", "always", "始终"}:
            return Answer.ALWAYS
        if reply in {"y", "yes"}:
            return Answer.ONCE
        return Answer.NO

    return confirm


# --------------------------------------------------------------- 斜杠命令


def handle_command(session: Session, raw: str) -> str:
    """执行一条斜杠命令，返回要打印的文本；要求退出时抛 RequestedExit。"""
    agent = session.agent
    parts = raw.strip().lstrip("/").split(None, 1)
    name = (parts[0] if parts else "").lower()
    argument = parts[1].strip().lower() if len(parts) > 1 else ""

    if name in {"exit", "quit", "q"}:
        raise RequestedExit
    if name == "help":
        return HELP
    if name == "reset":
        agent.reset()
        return "已清空对话历史与任务清单。"
    if name == "tools":
        return "\n".join(
            f"  {tool.name:<12} {tool.risk_level:<8} {one_line(tool.description)}" for tool in agent.registry.all()
        )
    if name == "context":
        used = agent.context.estimate(
            system=agent.current_system(), tools=agent.registry.specs(), messages=agent.messages
        )
        snap = agent.context.snapshot()
        budget = max(agent.context.budget, 1)
        return (
            f"  消息 {len(agent.messages)} 条 · 估算 {used:,} / 预算 {agent.context.budget:,} tokens"
            f"（{used / budget * 100:.0f}%）\n"
            f"  系数 {snap['chars_per_token']} 字符/token · 端点上次实测 {snap['last_actual_prompt_tokens']:,} prompt tokens\n"
            f"  累计 {agent.state.usage.total:,} / 上限 {agent.max_total_tokens:,} tokens · 轮数 {agent.state.turn}/{agent.max_turns}"
        )
    if name == "todos":
        return agent.todos.render()
    if name == "mode":
        if argument not in MODE_FLAGS:
            return f"当前模式 {agent.gate.mode.value}。可切换：{' / '.join(MODE_FLAGS)}"
        agent.gate.mode = MODE_FLAGS[argument]
        return f"权限模式已切到 {argument}。"
    if name == "trace":
        path = session.tracer.path
        if not path:
            return "本次会话没有写日志（--no-trace 或 TRACE_PATH 未配置）。"
        stats = summarize(Path(path))
        return (
            f"  日志 {path}\n"
            f"  轮数 {stats['turns']} · 工具 {stats['tool_calls']} 次（{stats['tool_errors']} 次报错）"
            f" · 结束于 {stats['termination']}"
        )
    return f"不认识的命令：{raw}\n\n{HELP}"


def one_line(text: str, limit: int = 46) -> str:
    first = " ".join((text or "").split())
    return first if len(first) <= limit else first[: limit - 1] + "…"


# --------------------------------------------------------------- REPL


def run_turn(session: Session, text: str) -> AgentResult:
    """跑一次任务。中途被打断也要给出结论，而不是让异常逃出去。"""
    agent, renderer = session.agent, session.renderer
    try:
        result = agent.run(text)
    except KeyboardInterrupt:
        print("")
        result = agent.cancel()
    except Exception as exc:  # noqa: BLE001 - 让 REPL 崩掉等于丢掉用户整段上下文
        result = AgentResult(
            text=f"内部错误：{type(exc).__name__}: {exc}\n（本次请求已中止，历史仍在内存里，可以直接说「继续」。）",
            termination=TerminationReason.INTERNAL_ERROR,
            state=agent.state,
        )
        renderer.error(result.text.splitlines()[0])

    renderer.final_text(result.text)
    renderer.usage(
        prompt_tokens=result.state.usage.prompt_tokens,
        completion_tokens=result.state.usage.completion_tokens,
        turns=result.state.turn,
    )
    if result.todos and not result.succeeded:
        renderer.todos(result.todos)
    session.history.append(result)
    return result


def repl(session: Session, *, read: Callable[[str], str] = input) -> int:
    """读-求值-打印主循环。Ctrl-C 放弃当前输入，Ctrl-D 或 /exit 结束会话。

    `read` 可注入是测试用的：REPL 的逻辑（分发、退出码、命令优先级）
    值得全部覆盖一遍，而它不该依赖真实终端。
    """
    session.renderer.banner(
        model=session.config.model,
        project_root=session.config.project_root,
        tools=session.agent.registry.names(),
        mode=session.agent.gate.mode.value,
    )
    while True:
        try:
            text = read(PROMPT)
        except EOFError:
            print("\n再见。")
            return 0
        except KeyboardInterrupt:
            print("")
            continue

        if not text.strip():
            continue
        if text.lstrip().startswith("/"):
            try:
                message = handle_command(session, text)
            except RequestedExit:
                print("再见。")
                return 0
            if message:
                print(message)
            continue

        run_turn(session, text)


# --------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="miniclaude",
        description="Mini Claude Code —— 自己读写文件、跑命令、修测试的编程 Agent。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP,
    )
    parser.add_argument("request", nargs="*", metavar="任务…", help="一次性任务（给了就不进 REPL）")
    parser.add_argument("-t", "--task", default=None, help="非交互执行一条任务后退出")
    parser.add_argument("--root", type=Path, default=None, help="工作目录（默认取 PROJECT_ROOT 或当前目录）")
    parser.add_argument("--mode", choices=tuple(MODE_FLAGS), default=None, help="权限模式，默认 ask 逐次确认")
    parser.add_argument("-y", "--yes", action="store_true", help="等价 --mode auto：工作区内自动放行，破坏性命令仍拒绝")
    parser.add_argument("--readonly", action="store_true", help="等价 --mode readonly：只允许只读工具")
    parser.add_argument("--max-turns", type=int, default=None, help="单次任务最大轮数")
    parser.add_argument("--model", default=None, help="覆盖配置里的模型名")
    parser.add_argument("--no-trace", action="store_true", help="不写会话日志")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印轮次分隔线与工具输出摘要")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口：无参进 REPL，给了任务则跑一轮并用退出码交代结果。"""
    args = build_parser().parse_args(argv)

    try:
        config = get_config()
    except ConfigError as exc:
        print(f"配置不完整：{exc}", file=sys.stderr)
        return 2

    overrides: dict[str, Any] = {}
    if args.root:
        overrides["project_root"] = Path(args.root).expanduser().resolve()
    if args.model:
        overrides["model"] = args.model
    if args.max_turns:
        overrides["max_turns"] = args.max_turns
    if args.no_trace:
        overrides["trace_path"] = None
    if overrides:
        config = replace(config, **overrides)

    one_shot = (args.task or " ".join(args.request)).strip()

    mode = PermissionMode.ASK
    if args.mode:
        mode = MODE_FLAGS[args.mode]
    elif args.readonly:
        mode = PermissionMode.READONLY
    elif args.yes:
        mode = PermissionMode.AUTO
    elif not sys.stdin.isatty():
        # 管道里没人能回答确认，逐次询问只会得到"没有确认渠道"，不如直接说明
        if not one_shot:
            print("（输入不是终端，已切到 auto 模式；需要逐次确认请在真终端里运行）", file=sys.stderr)
        mode = PermissionMode.AUTO

    try:
        session = build_session(config=config, mode=mode, verbose=args.verbose)
    except ConfigError as exc:
        print(f"配置不完整：{exc}", file=sys.stderr)
        return 2

    if one_shot:
        result = run_turn(session, one_shot)
        return 0 if result.succeeded else 1
    return repl(session)
