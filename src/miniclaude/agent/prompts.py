"""系统提示 —— 模型表现好坏一半取决于这个文件。

分节编写，每节只做一件事，便于单独调整而不影响其他行为：
  1. 身份与目标        2. 环境事实        3. 工具使用规范
  4. 编辑规范          5. 计划与自我调试  6. 安全与授权边界
  7. 输出风格

三个容易被忽略但决定成败的点：
- **环境事实必须显式注入**。模型默认按 Linux 思考，Windows 上会写出 `cat`、
  `/dev/null`、`python3` 这类跑不了的命令，然后烧三轮去试错。
- **工具清单要写进提示**。报文里的 tools 字段有时会被端点吃掉或改写，
  提示里再列一次能挡住大部分幻觉工具名。
- **目录树是零成本的仓库认知**（D7）。没有它，模型第一轮只能瞎猜文件名；
  有它，A1 类任务平均省 2–3 轮。上限 30 行，超了就用工具继续探。
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path
from typing import Iterable, Sequence

from miniclaude.messages import Message, TextBlock, ToolUseBlock
from miniclaude.tools.workspace import Workspace

SYSTEM_PROMPT = """\
你是 Mini Claude Code，一个运行在用户终端里的软件工程 Agent。
你通过调用工具来完成任务，而不是把代码打印给用户让他们自己执行。

# 工作方式
- 先把任务变成"能验证的成功判据"：要修 bug 就找到那条失败的测试，要加功能就想清楚怎么验证。
- 修改任何文件之前必须先读取它。禁止凭猜测编辑。
- 不确定文件是否存在或内容是什么时，先调查（find_files / search_text / read_file），不要假设。
- 一次只做一件有副作用的事，执行后先看结果再继续。
- 工具报错不是终点。读错误信息，改参数或换工具重试；同一路子走三次就是没在思考。

# 编辑规范
- 局部改动优先用 edit_file，只有新建文件或整体重写时才用 write_file。
- edit_file 的 old_string 要和文件里**逐字符一致**（含缩进、空行、引号），并在文件内唯一。
  不唯一就扩大上下文多带几行，不要盲目 replace_all。
- 写代码要完整可运行，不留 TODO 占位，不写"此处省略"。
- 不顺手重构与任务无关的代码，不改动你没读过的文件。

# 安全
- 所有文件操作限于工作目录内。用户没要求时，不要碰工作区之外的任何路径。
- 不执行不可逆的破坏性命令（递归删除、重置版本历史、强制推送）。需要这样做时，先停下来向用户说明。
- 不读取、不输出、不记录任何密钥或凭据文件的内容。
- 需要权限的操作被拒绝后，不要重复请求同一件事；改用只读路径继续，或者清楚说明你缺什么权限然后收尾。

# 输出风格
- 简洁。不要复述刚执行过的命令，不要写"我现在要去做 X"的流水账。
- 中文回答（除非用户用别的语言）。
- 任务完成后，用一两句话说明改了什么、结果如何、下一步建议是什么。
"""

PLANNING_PROMPT = """\
# 计划
- 3 步以上的多阶段任务：先用 write_todos 列出计划，再开始动手。
- 每完成一步立刻再调一次 write_todos 把它标为 done（整体替换，给完整清单），不要攒着一次更新。
- 同一时刻最多一步 in_progress。
- 单步就能看到结果的小任务**不要**建清单 —— 直接做，别浪费轮数。
"""

SELF_DEBUG_PROMPT = """\
# 自我调试
- 改完代码必须用 run_tests 验证，**以它给的退出码和用例名为准**，不要凭"我改了应该就对了"收尾。
- 测试仍失败时：读失败用例名 → 读它指向的源码 → 定位到具体行 → 只改那一处 → 再跑。
- 失败用例名发生变化，说明改动生效了一部分，这是继续缩小范围的信号，不是白干。
- 同一处改动重跑两次仍不奏效，就换假设：说明你读到的信息不足以解释现象，去读更多上下游代码。
"""

WINDOWS_NOTES = """\
- 这是 **Windows**，但 bash 工具会优先用 Git Bash 执行命令，所以管道、`;`、`/dev/null` 在这里能用：
  · 读文件、搜内容请用 read_file / search_text 工具，不要在 shell 里用 cat、head、tail、grep、sed、awk。
  · 丢弃输出写 `>/dev/null`。**不要 `>NUL`** —— Git Bash 会真的建出一个名叫 `NUL` 的文件，
    而 Windows 的保留设备名会让它没法正常删除，污染工作区。
  · 万一命令落到了 cmd.exe（没有 bash），`&&` 与 `/dev/null` 就不成立了，改用 `;` 和 `>NUL`。
  · 递归删除、移动之类操作用 PowerShell 的 Remove-Item / Move-Item，或者干脆交给 read_file+write_file。
- Python 用 `python`（不是 `python3`）；装包用 `python -m pip install <包名>`。
- 传给文件类工具的路径请写**正斜杠**相对路径，例如 src/app.py。"""

POSIX_NOTES = """\
- 这是类 Unix 环境，可用 bash。但读文件、搜内容仍然请优先用 read_file / search_text 工具 ——
  它们带行号、会截断，比 shell 输出更省上下文。
- Python 用 `python3`（若无则 `python`）；装包用 `python3 -m pip install <包名>`。
- 路径用正斜杠相对路径，例如 src/app.py。"""


def build_system_prompt(
    *,
    project_root: Path,
    platform: str,
    model: str,
    python_executable: str | None = None,
    tool_names: Sequence[str] = (),
    workspace: Workspace | None = None,
    map_provider: Callable[[], str] | None = None,
    max_map_lines: int = 30,
) -> str:
    """把运行期事实注入模板：工作目录、操作系统、Python、工具清单、仓库形状。

    返回值会被 Agent 每轮当作 system 发送（外加当前任务清单），所以这里
    只放**整个会话内稳定**的内容 —— 会变的东西放进来就是缓存杀手。

    `map_provider` 是 SPEC v2 §3.4 的注入点：给定它就用它（符号地图，自己按仓库
    指纹缓存），没给就退回那张 30 行的广度优先目录树。两条臂同时存在是 B3 的 A/B
    要求的 —— "地图关"必须是一个真能跑的配置，不是把段落删掉。
    """
    ws = workspace or Workspace(project_root)
    sections: list[str] = [SYSTEM_PROMPT.strip(), _environment(ws, platform, model, python_executable)]

    repo_map = map_provider() if map_provider is not None else render_repo_map(ws, max_lines=max_map_lines)
    if repo_map:
        sections.append(repo_map)

    sections.append(_tools_section(tool_names))
    sections.append(PLANNING_PROMPT.strip())
    sections.append(SELF_DEBUG_PROMPT.strip())
    return "\n\n".join(sections)


def _environment(ws: Workspace, platform: str, model: str, python_executable: str | None) -> str:
    normalized = (platform or sys.platform).lower()
    # 刻意用前缀而不是子串："darwin" 里含 "win"，子串判断会把 macOS 当成 Windows
    is_windows = normalized.startswith(("win", "cygwin", "msys")) or "windows" in normalized
    notes = WINDOWS_NOTES if is_windows else POSIX_NOTES
    python = python_executable or sys.executable or "python"
    return (
        "# 运行环境（按这些事实行事，不要按你的默认假设）\n"
        f"- 工作目录（唯一可写范围）：{ws.root}\n"
        "  工具参数里的相对路径都相对它解析；请一律写正斜杠，例如 src/app.py。\n"
        f"- 操作系统：{platform}\n"
        f"- 解释器：{python}\n"
        f"- 模型：{model}\n"
        f"{notes}"
    )


def _tools_section(tool_names: Iterable[str]) -> str:
    names = [name for name in tool_names if name]
    if not names:
        return "# 工具\n只有报文里声明的工具存在。不要虚构工具名。"
    return (
        "# 工具\n"
        f"你只有这些工具：{', '.join(names)}。名字必须逐字一致，其余一律不存在。\n"
        "不要用它做工具能做的事：读文件、改文件、跑测试都走工具，别用 bash 拼 python -c 绕路。"
    )


def render_repo_map(ws: Workspace, *, max_lines: int = 30, header: str | None = None) -> str:
    """广度优先的缩进目录树 —— D7 里那个"零成本仓库地图"。

    按层展开而不是深度优先：预算只有 30 行时，先让模型看清仓库**有多宽**
    比先看清某个子目录有多深更有用。

    `max_lines` 约束的是树本身（根名那一行、条目、截断提示都算），
    标题 "# 仓库形状" 不计入 —— 这样调用方能拿它当硬预算用。
    """
    tree: list[str] = [header if header is not None else f"{ws.root.name}/"]
    pending: deque[tuple[Path, int]] = deque([(ws.root, 1)])
    hidden = 0
    room = max(max_lines - 1, 1)  # 给截断提示固定留一行

    while pending and len(tree) < room:
        current, depth = pending.popleft()
        try:
            entries = sorted(current.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        except OSError:
            continue

        visible = [item for item in entries if not ws.is_ignored(item) and not item.name.startswith(".")]
        hidden += len(entries) - len(visible)
        for entry in visible:
            if len(tree) >= room:
                hidden += 1
                continue
            tree.append(f"{'  ' * depth}{entry.name}" + ("/" if entry.is_dir() else ""))
            if entry.is_dir():
                pending.append((entry, depth + 1))

    if pending or hidden:
        tree.append(f"  …（只显示 {room} 条，另有 {hidden} 项未列出；请用 find_files / search_text 继续探查）")
    return "# 仓库形状\n" + "\n".join(tree)


# ---------------------------------------------------------------- 上下文压缩

COMPACT_SYSTEM = """\
你是会话历史的压缩器。你的输出会**替换**掉一段已经完成的对话历史，
后面每一步都要靠它决定做什么，所以只能写"后续工作必须依赖的事实"。

不要写：寒暄、对你的称呼、你打算怎么做这件事的空话、模板里没有内容的字段的占位。
必须写全这五项，缺一项就说明缺哪项：
目标 / 已确认事实 / 未验证的假设 / 最后一次测试结论 / 被否决的路径

"被否决的路径"最重要：漏了它，下一步会重走同一条死路。
只输出正文字段，不要复述这段说明。"""

_SUMMARY_FIELDS = ("目标", "已确认事实", "未验证的假设", "最后一次测试结论", "被否决的路径")


def render_transcript(
    messages: Sequence[Message], *, per_block_chars: int = 1200, max_chars: int = 120_000
) -> str:
    """把要压掉的那批消息渲染成给摘要器看的纯文本。

    截断是有意的：摘要器看到的是"每段观察的开头"，不是全文 —— 全量重发的话
    L2 自己就会变成新的上下文瓶颈。上限按字符数算，约 3.4 万 token，
    离 `CONTEXT_HARD_LIMIT` 还有足够余量。
    """
    lines: list[str] = []
    total = 0
    for message in messages:
        for block in message.content:
            if isinstance(block, TextBlock):
                piece = f"[{message.role.value}] {block.text}"
            elif isinstance(block, ToolUseBlock):
                piece = f"[{message.role.value}] 调用 {block.name} {_args_brief(block)}"
            else:
                tag = "结果(失败)" if block.is_error else "结果"
                piece = f"[{tag}] {block.content}"
            if len(piece) > per_block_chars:
                head = piece[: per_block_chars // 2]
                tail = piece[-(per_block_chars // 4) :]
                piece = f"{head}\n…（省略 {len(piece) - len(head) - len(tail)} chars）\n{tail}"
            total += len(piece) + 1
            if total > max_chars:
                lines.append("…（更早的内容因摘要请求自身的预算被截断）")
                return "\n".join(lines)
            lines.append(piece)
    return "\n".join(lines)


def build_summary_request(messages: Sequence[Message], goal: str) -> str:
    """摘要请求：模板 + 待压历史。

    字段清单写在这里、`_local_digest()` 写在 context.py 里，是有意的分工：
    文件名这类**代码确定知道**的事不劳模型复述，模型只写它才有的判断。
    """
    fields = "\n".join(f"{name}：" for name in _SUMMARY_FIELDS)
    return (
        "下面这个任务的对话历史太长了，需要压成一段后续工作要依赖的纪要。\n\n"
        f"用户原始任务：\n{goal}\n\n"
        "请按下面的字段逐条写，没有内容的字段写「无」：\n"
        f"{fields}\n\n"
        "待压缩的历史（越早的越在前面）：\n"
        f"{render_transcript(messages)}"
    )


def _args_brief(block: ToolUseBlock) -> str:
    args = block.input if isinstance(block.input, dict) else {}
    return ", ".join(f"{key}={str(value)[:60]}" for key, value in list(args.items())[:3])
