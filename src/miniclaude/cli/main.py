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
from miniclaude.backend import (
    BACKEND_NAMES,
    SESSIONS_DIRNAME,
    BackendChoice,
    SessionLog,
    SessionRecorder,
    select_backend,
)
from miniclaude.config import Config, ConfigError, get_config
from miniclaude.cli import ext_cmd, trace_cmd
from miniclaude.cli.render import Renderer
from miniclaude.ext import LoadSkillTool, MCPBridge, MCPServerSpec, MCPSpecError, SkillLoader
from miniclaude.infra.trace import Tracer, of_session, prompt_hash, replay, summarize_records
from miniclaude.llm.openai_compat import OpenAICompatClient
from miniclaude.memory import MEMORY_DIRNAME, MemoryStore, RepoMap
from miniclaude.messages import PairingError
from miniclaude.tools.base import BaseTool
from miniclaude.tools.registry import ToolRegistry
from miniclaude.tools.workspace import IGNORED_DIRS, Workspace

PROMPT = "你 > "
MODE_FLAGS = {"ask": PermissionMode.ASK, "auto": PermissionMode.AUTO, "readonly": PermissionMode.READONLY}
HELP = """命令：
  /help              看这个帮助
  /reset             清空对话历史与任务清单（重新开始）
  /tools             列出工具与风险级别
  /context           显示上下文用量与估算系数
  /todos             显示当前任务清单
  /backend           显示执行后端、检查点栈与会话现场
  /undo              回退到上一个检查点（撤销最近一次写入）
  /mcp               列出外部工具：名字、远端自报的风险、我们实际采信的档位
  /skills            列出技能目录（正文不常驻，load_skill 按需取）
  /mode <模式>       切换权限模式：ask / auto / readonly
  /trace             显示本次会话日志位置与统计
  /exit              退出（也可以按 Ctrl-D）

不在 REPL 里的诊断命令（读日志，不占对话轮数）：
  mcc trace --latest                最近一次会话的时间线
  mcc trace --latest --hot          最贵 3 轮 / 报错最多的工具 / 重复调用簇
  mcc trace --latest --why-failed   失败模式标签 + 证据 + 处方
  mcc trace <会话 id> --json        机器可读汇总（eval 与脚本用）

  mcc resume --list                 列出还能接着跑的会话现场（最近的在前）
  mcc resume <会话 id>              从现场继续跑完（已做过的写入不会重放）
  mcc resume --latest               续跑最近那份现场

  mcc mcp                             发现并列出外部工具（会真的握手一次）
  mcc mcp --json                       机器可读的那份
  mcc skills                          列出技能目录与常驻/按需的字符数

  mcc eval --list                   列出考题与题集哈希
  mcc eval --lint                   考题自检：这道题**可能**被做对吗
  mcc eval                          fake 引擎跑全批（不读 .env、不打网络、秒级）
  mcc eval --engine live --smoke    6 道 supports_live 题的真实模型冒烟
  mcc eval --no-compact             上下文阶梯的对照臂（B2）
  mcc eval --no-repo-map            符号地图的对照臂（B3，两臂只能差这一个开关）
  mcc eval --backend docker         执行后端的对照臂（B6，降级会写在报表里）
  mcc eval --baseline <文件>        与基线对比，退步写进报表"""


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
    backend: BackendChoice | None = None
    mcp: MCPBridge | None = None
    mcp_skip_reason: str = ""
    skills: SkillLoader | None = None
    history: list[AgentResult] = field(default_factory=list)

    @property
    def registry(self) -> ToolRegistry:
        return self.agent.registry

    @property
    def gate(self) -> PermissionGate:
        return self.agent.gate

    def close(self) -> None:
        """关掉外部工具的子进程。MCP 服务是**我们**起的，收尾就得由我们负责。"""
        if self.mcp is not None:
            self.mcp.close()


def build_session(
    *,
    config: Config | None = None,
    mode: PermissionMode = PermissionMode.ASK,
    verbose: bool = False,
    renderer: Renderer | None = None,
    confirmer: Callable[[str, str], Answer] | None = None,
    llm: Any = None,
    summarizer_llm: Any = None,
    use_rich: bool | None = None,
) -> Session:
    """装配依赖图：Config -> Workspace/Registry/Gate/Tracer/LLM -> Agent。

    后端与检查点**只从 Config 读**（`EXECUTION_BACKEND` / `CHECKPOINTS`，命令行与 eval
    都用 `replace()` 覆盖它）。这里再开一个同名参数就会出现"两条路径给出不同答案"，
    而 B6 的整个结论建立在"两臂只差后端这一件事"上。
    """
    cfg = config or get_config()
    # 只读模式承诺的是"这个工作区一个字节都不变"。检查点与会话快照都是往
    # `<记忆目录>/` 写盘，所以在这条承诺下它们必须一起关掉 —— 靠"写进了白名单目录
    # 所以不算改"来圆场，是在自己挖的坑上再盖一层布。
    writable = mode is not PermissionMode.READONLY
    use_checkpoints = cfg.checkpoints and writable
    workspace = Workspace(
        cfg.project_root,
        output_limit=cfg.tool_output_limit,
        # 记忆目录（.mcc 或 MEMORY_DIR 改成的任何名字）必须对搜索与地图隐身：
        # 影子仓库的 object 文件、会话快照，一个都不该进模型的视野。
        # 缺省名走 `MEMORY_DIRNAME` 这一个产地，不在这里再抄一次字符串。
        ignored_dirs=IGNORED_DIRS | {Path(cfg.memory_dir).name or MEMORY_DIRNAME},
    )
    todos = TodoList()
    choice = select_backend(
        cfg.execution_backend,
        workspace_root=workspace.root,
        memory_dir=cfg.memory_root,
        ignored_dirs=sorted(workspace.ignored_dirs),
        image=cfg.docker_image,
        network=cfg.docker_network,
        checkpoints=use_checkpoints,
    )
    # §3.7 的两条扩展都从这里进，**只有这一条装配路径**（`extra_tools`）：
    # 给外部能力单开一条注册通道，权限门与 trace 就得各修一遍才追得上。
    extra: list[BaseTool] = [WriteTodosTool(workspace, todos)]
    bridge: MCPBridge | None = None
    mcp_skipped = ""
    if cfg.mcp_servers:
        if not writable:
            # 发现本身就是副作用：起一个第三方进程，它能在我们看不见的地方写盘。
            # "只读模式一个字节都不变"这条承诺不该为"只是列一下工具"破例。
            mcp_skipped = "只读模式：没有启动任何 MCP 服务，外部工具未装配"
        else:
            try:
                specs = tuple(MCPServerSpec.from_mapping(item) for item in cfg.mcp_servers)
            except MCPSpecError as exc:
                # 配置写错 → 启动即失败。一个起不来的服务被跳过，用户会以为功能在。
                raise ConfigError(str(exc)) from exc
            bridge = MCPBridge(servers=specs, workspace=workspace)
            extra.extend(bridge.discover())
    skills = SkillLoader(cfg.skills_root)
    skill_rows = skills.manifests()
    if skill_rows:
        extra.append(LoadSkillTool(workspace, skills))
    registry = ToolRegistry.default(
        workspace,
        bash_timeout=cfg.bash_timeout,
        extra_tools=extra,
        backend=choice.backend,
    )
    gate = PermissionGate(workspace=workspace, mode=mode, confirmer=confirmer or make_confirmer())
    # 地图只在装配时建一次，之后由 Agent 在每轮 `_system()` 里按需刷新（指纹没变就
    # 返回同一个字符串，所以前缀缓存不会被打掉）。
    # READONLY 下**不落盘**：只读模式承诺的是"这个工作区一个字节都不变"，而"缓存写了
    # 但判据把它白名单掉"是在自己挖的坑上再盖一层布 —— A3 要成立得靠不写。
    store = (
        MemoryStore.for_project(workspace.root, cfg.memory_dir) if cfg.repo_map and writable else None
    )
    repo_map = (
        RepoMap(workspace, store=store, token_cap=cfg.repo_map_tokens) if cfg.repo_map else None
    )

    external_note = (
        "# 外部工具（MCP）\n"
        "带 `mcp__` 前缀的工具跑在我们这个进程之外，它们做了什么我们只能转述。\n"
        "所以：每一次调用都要单独确认，AUTO 模式也不例外；被拒绝之后就换回本地工具，别重试。"
        if bridge is not None and any(name.startswith("mcp__") for name in registry.names())
        else ""
    )

    def render_system() -> str:
        """整段 system。抽成函数是为了让 Agent 能在写文件之后重画地图，
        而不必把"仓库形状"这种会过期的东西硬编进一次性的字符串。"""
        return build_system_prompt(
            project_root=workspace.root,
            platform=sys.platform,
            model=cfg.model,
            python_executable=sys.executable,
            tool_names=registry.names(),
            workspace=workspace,
            map_provider=(repo_map.map_for_prompt if repo_map is not None else None),
            skills_catalog=skills.catalog(),
            external_note=external_note,
        )

    system_prompt = render_system()
    tracer = Tracer(cfg.trace_path)
    # 提示词指纹进 trace：两次跑批之间提示词改没改，看这个字段而不是看 diff
    tracer.start_session(
        model=cfg.model,
        tools=registry.names(),
        config=cfg.redacted(),
        system_prompt_hash=prompt_hash(system_prompt),
    )
    # 后端与降级必须落在 trace 的第一屏：一次会话跑出来的所有数字，都是在"命令在哪儿
    # 执行"这个前提下才成立的。B6 的报表只读这一条就能确认两臂各自用的什么后端。
    tracer.log("backend", turn=0, **choice.as_trace())
    if repo_map is not None:
        # turn=0 = 会话装配时的那次渲染。不记这条的话，"地图第一次出现在哪"
        # 在 trace 里就查不到，而 B3 要按它归因轮数差值。
        tracer.log("repo_map", turn=0, **repo_map.stats.as_trace())
    # 外部能力各自留一条装配期事件。**只在真配了的时候记**：一条永远为空的
    # `mcp` 记录会把"这个机制参与了这次跑"与"它没参与"抹平，而后者才是要查的。
    if bridge is not None:
        # `skipped` 是"被跳过的工具条数"，`skip_reason` 是"这一整条扩展没参与的原因"：
        # 两个都叫 skipped 会让 READONLY 那条把整数换成字符串，离线读的人无从分辨。
        tracer.log("mcp", turn=0, skip_reason="", **bridge.stats())
    elif mcp_skipped:
        tracer.log("mcp", turn=0, configured=[str(s.get("name")) for s in cfg.mcp_servers], skip_reason=mcp_skipped)
    if skill_rows:
        tracer.log("skills", turn=0, **skills.stats())
    # 现场快照用**会话 id** 命名：一次会话一个文件，resume 与 trace 说的是同一件事。
    # 只读模式没有 recorder —— 它连 .mcc 都不该创建。
    recorder = (
        SessionRecorder(
            log=SessionLog(root=cfg.memory_root / SESSIONS_DIRNAME),
            session_id=tracer.session_id,
            config=cfg.redacted(),
            backend=choice.name,
        )
        if writable
        else None
    )

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
        summarizer_llm=summarizer_llm,
        registry=registry,
        gate=gate,
        system_prompt=system_prompt,
        max_turns=cfg.max_turns,
        max_total_tokens=cfg.max_total_tokens,
        context=ContextManager(
            budget=cfg.token_budget,
            hard_limit=cfg.context_hard_limit,
            enabled=cfg.context_compact,
        ),
        todos=todos,
        tracer=tracer,
        on_event=render.handle,
        price_per_mtokens=cfg.price_per_mtokens,
        repo_map=repo_map,
        system_provider=render_system,
        notes=store.notes_for_prompt() if store is not None else "",
        backend=choice.backend,
        recorder=recorder,
        checkpoints=use_checkpoints,
    )
    return Session(
        agent=agent,
        config=cfg,
        renderer=render,
        tracer=tracer,
        workspace=workspace,
        backend=choice,
        mcp=bridge,
        mcp_skip_reason=mcp_skipped,
        skills=skills if skill_rows else None,
    )


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
        ladder = (
            f"已压缩 {snap['compactions']} 次：省略 {snap['elided_blocks']} 块工具输出、"
            f"摘要 {snap['summaries']} 次（自身花 {snap['summary_tokens']:,} tokens）"
            if snap["compactions"]
            else "尚未触发压缩"
        )
        stats = agent.repo_map.stats if agent.repo_map is not None else None
        mapping = (
            "仓库地图：关（system 里是 v1 的目录树）"
            if stats is None
            else f"仓库地图：{stats.modules_found} 个模块列出 {stats.listed}（未列 {stats.omitted}）"
            f" · {stats.est_tokens:,}/{stats.token_cap:,} tokens · 已重画 {agent.repo_map.refreshes} 次"
        )
        return (
            f"  消息 {len(agent.messages)} 条 · 估算 {used:,} / 预算 {agent.context.budget:,} tokens"
            f"（{used / budget * 100:.0f}%）· 硬熔断 {agent.context.hard_limit:,}\n"
            f"  系数 {snap['chars_per_token']} 字符/token · 端点上次实测 {snap['last_actual_prompt_tokens']:,} prompt tokens\n"
            f"  压缩阶梯 {'开' if snap['enabled'] else '关'} · {ladder}\n"
            f"  {mapping} · 工作记忆笔记 {len(agent.notes):,} 字符\n"
            f"  累计 {agent.state.usage.total:,} / 上限 {agent.max_total_tokens:,} tokens · 轮数 {agent.state.turn}/{agent.max_turns}"
        )
    if name == "todos":
        return agent.todos.render()
    if name == "backend":
        return render_backend(session)
    if name == "undo":
        return do_undo(session)
    if name == "mcp":
        return ext_cmd.render_session_mcp(
            session.mcp,
            configured=[str(item.get("name")) for item in session.config.mcp_servers],
            skipped=session.mcp_skip_reason,
        )
    if name == "skills":
        return ext_cmd.render_session_skills(session.skills, root=session.config.skills_root)
    if name == "mode":
        if argument not in MODE_FLAGS:
            return f"当前模式 {agent.gate.mode.value}。可切换：{' / '.join(MODE_FLAGS)}"
        agent.gate.mode = MODE_FLAGS[argument]
        return f"权限模式已切到 {argument}。"
    if name == "trace":
        path = session.tracer.path
        if not path:
            return "本次会话没有写日志（--no-trace 或 TRACE_PATH 未配置）。"
        # 按会话过滤：TRACE_PATH 是追加式的，一个文件里可能已经有好几次会话
        stats = summarize_records(of_session(replay(Path(path)), session.tracer.session_id))
        modes = "、".join(stats["failure_modes"]) if stats["failure_modes"] else "无"
        return (
            f"  日志 {path} · 会话 {stats['session']}\n"
            f"  轮数 {stats['turns']} · 工具 {stats['tool_calls']} 次（{stats['tool_errors']} 次报错）"
            f" · 结束于 {stats['termination']}\n"
            f"  重复调用 {stats['repeated_calls']} 次 · 整组重演 {stats['stalled_groups']} 轮"
            f" · 被拒 {stats['denied_actions']} 次 · 上下文峰值 {stats['context_peak_tokens']:,} tokens\n"
            f"  失败模式：{modes}\n"
            "  看细节：mcc trace --latest --hot / --why-failed"
        )
    return f"不认识的命令：{raw}\n\n{HELP}"


def render_backend(session: Session) -> str:
    """`/backend` —— §3.6 这一层的自述面板：命令在哪儿跑、能退回哪儿、现场在哪儿。

    降级（点名 docker 却用上 local）必须在这里看得见，而不是只写在 trace 里：
    用户问"我的沙箱呢"的时候，终端要给出一行带原因的答案，否则这个功能就像没做。
    """
    agent = session.agent
    choice = session.backend
    if choice is None or agent.backend is None:
        return "本次会话没有装配执行后端（由测试或脚本直接构造的 Agent）。"
    stats = agent.backend.stats()
    checkpoints = stats.get("checkpoints") or {}
    revs = agent.backend.history()
    isolated = "是" if stats.get("isolated") else "否"
    extras = f"镜像 {stats.get('image')}" if stats.get("image") else f"shell {stats.get('shell')}"
    lines = [
        f"  {choice.note()}",
        f"  点名 {choice.requested} · 沙箱隔离 {isolated} · {extras}",
    ]
    if stats.get("image"):
        lines.append(f"  网络 {stats.get('network') or 'none'} · 已启动 {stats.get('launches', 0)} 次容器")
    lines.append(
        "  检查点 {}{} · 影子仓库 {} · 已拍 {} 次 · 回滚 {} 次 · 失败 {} 次".format(
            "开" if checkpoints.get("enabled") else "关",
            "" if not checkpoints.get("enabled") else ("（已就位）" if checkpoints.get("ready") else "（仓库待首次写入时建）"),
            checkpoints.get("last_rev") or "—",
            checkpoints.get("snapshots", 0),
            checkpoints.get("restores", 0),
            checkpoints.get("failures", 0),
        )
    )
    if checkpoints.get("degraded"):
        lines.append(f"  检查点降级原因：{checkpoints['degraded']}")
    if revs:
        lines.append("  栈（新→旧）：" + " ".join(rev[:8] for rev in revs[:8]) + (
            f" …共 {len(revs)} 条" if len(revs) > 8 else ""
        ))
    recorder = agent.recorder
    if recorder is not None:
        snapshot_root = recorder.log.root
        lines.append(
            f"  会话现场 {recorder.session_id} · 已落盘 {recorder.saves} 次 · 目录 {snapshot_root}"
            + (f" · 写入降级：{recorder.log.degraded}" if recorder.log.degraded else "")
        )
        lines.append(f"  崩了之后接着跑：mcc resume {recorder.session_id}")
    else:
        # 不写死目录名：`MEMORY_DIR` 可以改成任何名字，这里说清"什么都没写"就够了。
        lines.append("  会话现场：关（只读模式不创建记忆目录）")
    return "\n".join(lines)


def do_undo(session: Session) -> str:
    """`/undo` —— 回退到上一个检查点。回滚前照例要过确认，它改的是用户的文件。

    两条要说清的边界：影子仓库是 append-only 的，所以回滚之后还能再滚回来；
    而**对话历史不会倒带**，模型仍然记得自己写过什么 —— /undo 撤的是磁盘，不是记忆。
    """
    agent = session.agent
    backend = agent.backend
    if backend is None or not agent.checkpoints:
        return "本会话没有启用检查点（--no-checkpoints 或只读模式），没有可回退的点。/backend 看现状。"
    # rev 由 agent.undo_plan() 给，不在这里重算下标：撤销游标（`_undo_depth`）归循环层
    # 所有，CLI 自己数 history() 的话第二次 /undo 就会撤错一格。
    target, undone, why = agent.undo_plan()
    if not target:
        return why
    summary = f"回退工作区到 {target[:8]}（撤销 {undone[:8]} 那次写入带来的改动）"
    if agent.gate.mode is PermissionMode.ASK and agent.gate.confirmer is not None:
        if agent.gate.confirmer("undo", summary) is Answer.NO:
            return "已取消，工作区未改动。"
    ok, reason = agent.undo_last_write()
    if not ok:
        return f"回滚失败：{reason}"
    return f"已{summary}。检查点栈仍留着 {len(backend.history())} 条，对话历史不会倒带。"


def one_line(text: str, limit: int = 46) -> str:
    first = " ".join((text or "").split())
    return first if len(first) <= limit else first[: limit - 1] + "…"


# --------------------------------------------------------------- REPL


def run_turn(session: Session, text: str) -> AgentResult:
    """跑一次任务。中途被打断也要给出结论，而不是让异常逃出去。"""
    return _drive_turn(session, lambda: session.agent.run(text))


def _drive_turn(session: Session, invoke: Callable[[], AgentResult]) -> AgentResult:
    """把一次 `agent.run()` / `agent.resume()` 包在同样的兜底与渲染里。

    续跑与开跑共用这条路径是有要求的：resume 崩在半路同样要留下结论、同样要把现场
    留在磁盘上，而不是甩一段 traceback 给用户。
    """
    agent, renderer = session.agent, session.renderer
    try:
        result = invoke()
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


def announce(session: Session) -> None:
    """把后端结论说给人听：点名要的是什么、实际用的是哪个、为什么换了。

    REPL 的横幅里不写这一行是有代价的（横幅在 `repl()` 里由 renderer 画，后端结论在
    装配期就有了），所以 `main()` 与 `mcc resume` 两条路都显式调一次 —— 一次性模式下
    这是它唯一的露出机会，而降级一旦没人看见，B6 的一致性判定就失去意义。
    """
    choice = session.backend
    if choice is not None:
        if choice.degraded:
            session.renderer.warn(choice.note())
        else:
            session.renderer.dim(f"{choice.note()} · /backend 看详情")
    # 同一条纪律的第二处应用：后端换了要说，外部工具少装了也要说。
    # "配了三个服务、起来两个"如果静音，用户会拿一个缺工具的房间去测 agent。
    if session.mcp_skip_reason:
        session.renderer.warn(session.mcp_skip_reason)
    elif session.mcp is not None:
        failed = [entry for entry in session.mcp.stats()["servers"] if not entry.get("ok")]
        if failed:
            names = "、".join(f"{item['name']}（{str(item['error'])[:60]}）" for item in failed)
            session.renderer.warn(f"MCP 服务没起来 {len(failed)} 个：{names} · /mcp 看详情")


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
    announce(session)
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


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """`mcc` 与 `mcc resume` 共用的开关。分两处写就会有一处忘 —— 尤其是 §3.6 这两个。"""
    parser.add_argument("--root", type=Path, default=None, help="工作目录（默认取 PROJECT_ROOT 或当前目录）")
    parser.add_argument("--mode", choices=tuple(MODE_FLAGS), default=None, help="权限模式，默认 ask 逐次确认")
    parser.add_argument("-y", "--yes", action="store_true", help="等价 --mode auto：工作区内自动放行，破坏性命令仍拒绝")
    parser.add_argument("--readonly", action="store_true", help="等价 --mode readonly：只允许只读工具")
    parser.add_argument("--max-turns", type=int, default=None, help="单次任务最大轮数")
    parser.add_argument("--model", default=None, help="覆盖配置里的模型名")
    parser.add_argument(
        "--no-repo-map",
        dest="repo_map",
        action="store_false",
        default=None,
        help="不画符号地图，退回 v1 的目录树（B3 的对照组）",
    )
    parser.add_argument(
        "--repo-map-tokens", type=int, default=None, help="地图的 token 预算（默认 REPO_MAP_TOKENS=1500）"
    )
    parser.add_argument(
        "--backend",
        choices=BACKEND_NAMES,
        default=None,
        help="命令在哪儿跑（默认 EXECUTION_BACKEND=local）。docker 不可用时降级并说明，绝不静默",
    )
    parser.add_argument(
        "--no-checkpoints",
        dest="checkpoints",
        action="store_false",
        default=None,
        help="不拍检查点（/undo 因此失效，但也不往记忆目录写影子仓库）",
    )
    parser.add_argument("--no-trace", action="store_true", help="不写会话日志")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印轮次分隔线与工具输出摘要")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="miniclaude",
        description="Mini Claude Code —— 自己读写文件、跑命令、修测试的编程 Agent。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP,
    )
    parser.add_argument("request", nargs="*", metavar="任务…", help="一次性任务（给了就不进 REPL）")
    parser.add_argument("-t", "--task", default=None, help="非交互执行一条任务后退出")
    _add_common_args(parser)
    return parser


def build_resume_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcc resume",
        description="从上次崩掉/中断的会话现场接着跑（SPEC v2 §3.6）。已做过的写入不会重放。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("session", nargs="?", default=None, metavar="会话 id", help="要续跑的会话 id")
    parser.add_argument("--latest", action="store_true", help="续跑最近那份现场（不给 id 时的默认）")
    parser.add_argument("--list", dest="list_sessions", action="store_true", help="列出还有现场的会话")
    _add_common_args(parser)
    return parser


def _overrides_from(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    """命令行 -> Config 字段覆盖。返回 (覆盖, 错误说明)，说明非空即应退出。"""
    overrides: dict[str, Any] = {}
    if args.root:
        overrides["project_root"] = Path(args.root).expanduser().resolve()
    if args.model:
        overrides["model"] = args.model
    if args.max_turns:
        overrides["max_turns"] = args.max_turns
    if args.no_trace:
        overrides["trace_path"] = None
    if args.repo_map is False:
        overrides["repo_map"] = False
    if getattr(args, "repo_map_tokens", None) is not None:
        if args.repo_map_tokens < 1:
            # `REPO_MAP_TOKENS=0` 在 §5.3 里曾被当作"关地图"的另一种写法。两种写法
            # 关的是同一件事、报错却长得不一样，所以只留一个开关，另一个明确拒绝。
            return {}, "--repo-map-tokens 至少 1；要关地图请用 --no-repo-map"
        overrides["repo_map_tokens"] = args.repo_map_tokens
    if getattr(args, "backend", None):
        overrides["execution_backend"] = args.backend
    if getattr(args, "checkpoints", None) is False:
        overrides["checkpoints"] = False
    return overrides, ""


def _mode_from(args: argparse.Namespace, *, announce_non_tty: bool) -> PermissionMode:
    if args.mode:
        return MODE_FLAGS[args.mode]
    if args.readonly:
        return PermissionMode.READONLY
    if args.yes:
        return PermissionMode.AUTO
    if not sys.stdin.isatty():
        # 管道里没人能回答确认，逐次询问只会得到"没有确认渠道"，不如直接说明
        if announce_non_tty:
            print("（输入不是终端，已切到 auto 模式；需要逐次确认请在真终端里运行）", file=sys.stderr)
        return PermissionMode.AUTO
    return PermissionMode.ASK


def _trace_entry(argv: Sequence[str]) -> int:
    """`mcc trace` —— 只读日志。TRACE_PATH 仍经 Config 解析，env 读取不散落到别处。"""
    try:
        config = get_config()
    except ConfigError as exc:
        print(f"配置不完整：{exc}（日志路径也来自这份配置）", file=sys.stderr)
        return 2
    return trace_cmd.run(argv, config)


def _eval_entry(argv: Sequence[str]) -> int:
    """`mcc eval` —— 跑批评测。延迟 import 有两个理由：`eval.runner` 反过来要用
    本模块的 `build_session`（顶层 import 会成环），以及 fake 引擎不该因为
    `.env` 缺字段而连 `--help` 都打不开。
    """
    from miniclaude.cli import eval_cmd

    return eval_cmd.run(argv)


def _resume_entry(argv: Sequence[str]) -> int:
    """`mcc resume [会话 id]` —— 读现场、装回去、把没跑完的那批补完再继续。

    这里**不**重放已完成轮次的工具：`done_call_ids` 里的那些只补一条说明（见
    `Agent._run_tools`），因为"把上次那个 `rm` 再跑一遍"不是恢复，是第二次事故。
    """
    args = build_resume_parser().parse_args(list(argv))
    try:
        config = get_config()
    except ConfigError as exc:
        print(f"配置不完整：{exc}", file=sys.stderr)
        return 2
    overrides, problem = _overrides_from(args)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    if overrides:
        config = replace(config, **overrides)

    log = SessionLog(root=config.memory_root / SESSIONS_DIRNAME)
    known = log.ids()
    if args.list_sessions:
        if not known:
            print(f"{log.root} 下没有可恢复的现场")
            return 0
        print(f"可恢复的会话现场（{log.root}，最近的在前）：")
        for session_id in known:
            snapshot, _ = log.load(session_id)
            tail = "" if snapshot is None else f" · {len(snapshot.messages)} 条消息 · 后端 {snapshot.backend}"
            tail += f" · 结束于 {snapshot.termination}" if snapshot is not None and snapshot.termination else ""
            # 带上工作区：现场是按会话 id 平铺在一个目录里的，共用了 MEMORY_DIR（或把
            # 同一个项目 clone 到两处）之后，光看 id 猜不出这份现场该在哪个根上续跑 ——
            # 而猜错的后果就是下面那条拒绝。
            if snapshot is not None and snapshot.config.get("project_root"):
                tail += f" · 工作区 {snapshot.config['project_root']}"
            print(f"  {session_id}{tail}")
        return 0

    # 不带 id 就是"最近那份"，与 `--latest` 同义（帮助里两条都这么写，两条都得能用）。
    # 曾经写成 `known[0] if (args.latest or known)`，于是 `mcc resume --latest` 在没有
    # 现场时先去索引一个空列表 —— 用户敲的第一条恢复命令换来一个 traceback。
    wanted = args.session or (known[0] if known else "")
    if not wanted:
        print(f"没有可恢复的现场（{log.root} 是空的）。跑一次 mcc 再回来，或用 --list 看看。", file=sys.stderr)
        return 2
    snapshot, reason = log.load(wanted)
    if snapshot is None:
        print(f"无法恢复 {wanted}：{reason}", file=sys.stderr)
        if known:
            print("  mcc resume --list 看有哪些现场", file=sys.stderr)
        return 2

    recorded_root = str(snapshot.config.get("project_root") or "")
    if recorded_root and Path(recorded_root).resolve() != config.project_root.resolve():
        # 现场里的路径都是相对工作区的，换个根续跑 = 把上次那半句话写到另一棵树上。
        # 判在装配之前：装配会打开 trace、建记忆目录，那些都不该为一个错误的根发生。
        print(
            f"拒绝恢复：这份现场属于 {recorded_root}，当前工作区是 {config.project_root}。\n"
            f"  接着跑它请换回原来的根：mcc resume {wanted} --root {recorded_root}",
            file=sys.stderr,
        )
        return 2

    session = build_session(
        config=config, mode=_mode_from(args, announce_non_tty=False), verbose=args.verbose
    )
    try:
        announce(session)
        try:
            note = session.agent.restore_session(snapshot)
        except PairingError as exc:
            # 坏现场**原样留在磁盘上**：删掉它就毁掉了排查它唯一的一份证据。
            print(f"拒绝恢复：{exc}\n现场文件保留在 {log.path_for(wanted)}，可以先看一眼再决定。", file=sys.stderr)
            return 2
        session.renderer.dim(note)
        result = _drive_turn(session, lambda: session.agent.resume())
        return 0 if result.succeeded else 1
    finally:
        session.close()


def _mcp_entry(argv: Sequence[str]) -> int:
    """`mcc mcp` —— 握手并列出外部工具。它**会起进程**，所以和 `trace` 不是一类只读命令。"""
    try:
        config = get_config()
    except ConfigError as exc:
        print(f"配置不完整：{exc}", file=sys.stderr)
        return 2
    return ext_cmd.mcp_entry(argv, config)


def _skills_entry(argv: Sequence[str]) -> int:
    """`mcc skills` —— 只读一个目录，不起进程、不打网络。"""
    try:
        config = get_config()
    except ConfigError as exc:
        print(f"配置不完整：{exc}", file=sys.stderr)
        return 2
    return ext_cmd.skills_entry(argv, config)


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口：无参进 REPL，给了任务则跑一轮并用退出码交代结果。

    `trace` / `eval` / `resume` / `mcp` / `skills` 是子命令，放在最前面分流：`trace`
    只读日志，排查一次烧了 8 万 token 的会话时不该再依赖 LLM 配置可用；`eval` 自己管
    引擎与配置，fake 模式连 `.env` 都不需要；`resume` 要读现场，所以它得先于
    positional 解析被摘走（否则 "resume" 会被当成一条任务发给模型）。`mcp` 与 `skills`
    同理 —— 不给分流的话，"mcp" 会被当成一次性任务发给模型。
    """
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "trace":
        return _trace_entry(raw[1:])
    if raw and raw[0] == "eval":
        return _eval_entry(raw[1:])
    if raw and raw[0] == "resume":
        return _resume_entry(raw[1:])
    if raw and raw[0] == "mcp":
        return _mcp_entry(raw[1:])
    if raw and raw[0] == "skills":
        return _skills_entry(raw[1:])

    args = build_parser().parse_args(raw)

    try:
        config = get_config()
    except ConfigError as exc:
        print(f"配置不完整：{exc}", file=sys.stderr)
        return 2

    overrides, problem = _overrides_from(args)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    if overrides:
        config = replace(config, **overrides)

    one_shot = (args.task or " ".join(args.request)).strip()
    mode = _mode_from(args, announce_non_tty=not one_shot)

    try:
        session = build_session(config=config, mode=mode, verbose=args.verbose)
    except ConfigError as exc:
        print(f"配置不完整：{exc}", file=sys.stderr)
        return 2

    try:
        if one_shot:
            announce(session)
            result = run_turn(session, one_shot)
            return 0 if result.succeeded else 1
        return repl(session)
    finally:
        # MCP 服务是装配期起的：不管会话怎么结束（正常退出、/exit、异常），
        # 子进程都不该留在宿主上。孤儿进程比孤儿代码难查。
        session.close()
