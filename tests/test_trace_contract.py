"""trace 契约测试 —— SPEC v2 §3.1 的机器化不变式。

这个文件盯的不是"功能对不对"，而是"报表里的数字是不是真的被测出来的"。
v1 的 `redundant_calls` 定义了、进了 snapshot、被 summarize 读、印在 9 份证据
文件里，全代码库没有一处累加它 —— 这类错误靠人眼 review 抓不干净，靠语法测试
也抓不到，只能把"指标必须有产地"变成一条会失败的测试。

五条不变式：

1. `test_metrics_have_producers`：`AgentState.snapshot()` 的每个键都有非平凡写入点。
2. `test_summarize_reads_only_recorded_fields`：报表从 `run_end` 读的每个键，
   真实轨迹里那个字段确实存在（SPEC v1 写 `session_end`、代码写 `run_end`
   这类漂移就在这里失败）。
3. `test_repeated_and_stalled_...` / `test_attempts_and_executions_...` /
   `test_live_and_offline_classification_agree`：口径本身对得上，且实时与离线同一份判定。
4. `test_trace_schema_snapshot`：每个 kind 的键名集合与 `tests/schema_v2.json` 一致；
   `test_every_kind_the_code_emits_is_pinned` 再用静态扫描补齐样例轨迹走不到的 kind。
5. 最后两条：检测器自身必须会报警，纯初始化不算产地。
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeLLM, scripted_final_text, scripted_tool_calls
from miniclaude.agent.context import ContextManager
from miniclaude.agent.loop import Agent
from miniclaude.agent.permissions import Answer, PermissionGate, PermissionMode
from miniclaude.agent.planner import TodoList
from miniclaude.agent.state import AgentState, TerminationReason
from miniclaude.agent.todo_tool import WriteTodosTool
from miniclaude.infra.failure import classify, facts_from_records
from miniclaude.infra.trace import OUTPUT_CAP, Tracer, prompt_hash, replay, summarize_records
from miniclaude.llm.openai_compat import LLMError
from miniclaude.memory import RepoMap
from miniclaude.tools.registry import ToolRegistry
from miniclaude.tools.workspace import Workspace

import miniclaude

SRC_ROOT = Path(miniclaude.__file__).parent
SCHEMA_FILE = Path(__file__).parent / "schema_v2.json"
FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "mcp_fixture_server.py"

# 每条记录都必须自带这些字段 —— 少一个就不是"同一份 trace"，离线工具会读崩。
COMMON_ENVELOPE = frozenset({"seq", "ts", "session", "kind", "trace_id", "schema_version"})

# 这些键不是"某个字段被累加"，而是从多条记录算出来的 —— 每条都要写清怎么算的。
DERIVED_METRICS = {
    "session": "records[0]['session']",
    "termination": "最后一条 run_end 的 termination",
    "turns": "len(kind == turn_start)",
    "tool_executed": "len(kind == tool_call)",
    "tokens": "sum(每条 llm_response 的 usage.prompt + usage.completion) + sum(context_compact.summary_tokens)",
    "tool_sequence": "[tool_call.name]",
    "output_chars": "sum(tool_call.output_chars)",
    "omitted_output_chars": "sum(max(0, tool_call.output_chars - 2 * (OUTPUT_CAP // 2)))",
}
"""报表里允许存在的派生指标：值由多条记录算出，不来自 `run_end` 的单个字段。

`tool_calls` / `tool_errors` 刻意不在这里 —— 它们必须读 `run_end` 里 state 记的那份，
否则"发起数"和"执行数"会各自顶一个同名指标（v1 就是这样分叉的）。
"""


# --------------------------------------------------------------- 检测器


def _dotted(node: ast.expr) -> tuple[str, ...] | None:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    parts.reverse()
    if parts[0] in {"self", "cls"}:
        parts = parts[1:]
    return tuple(parts) or None


def _is_trivial(value: ast.expr | None) -> bool:
    """`x = 0` / `x = []` / `x = Usage()` 这类赋值只是初始化，不构成"这个数被测出来了"。"""
    if value is None:
        return False
    if isinstance(value, ast.Constant):
        return value.value in (0, 0.0, "", None, False)
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return not value.elts
    if isinstance(value, ast.Dict):
        return not value.keys and not value.values
    if isinstance(value, ast.Call):
        func = value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        return name[:1].isupper() or name in {"field", "list", "dict", "set", "tuple"}
    return False


def written_targets(node: ast.AST) -> list[tuple[ast.expr, ast.expr | None]]:
    """返回这个节点写入的 (目标, 右值) 对。"""
    if isinstance(node, ast.Assign):
        return [(target, node.value) for target in node.targets]
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        return [(node.target, node.value)]
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [(node.target, None)]
    return []


def write_paths(sources: dict[str, str]) -> set[tuple[str, ...]]:
    """所有**非平凡**属性写入的 dotted 路径集合。"""
    paths: set[tuple[str, ...]] = set()
    for text in sources.values():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            for target, value in written_targets(node):
                if _is_trivial(value):
                    continue
                dotted = _dotted(target)
                if dotted:
                    paths.add(dotted)
    return paths


def package_sources() -> dict[str, str]:
    return {str(file): file.read_text(encoding="utf-8") for file in sorted(SRC_ROOT.rglob("*.py"))}


def emitted_kinds() -> set[str]:
    """代码里所有 `tracer.log("...")` / `self._trace("...")` 的第一个字面量参数。"""
    out: set[str] = set()
    for text in package_sources().values():
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            first = node.args[0]
            if name in {"_trace", "log"} and isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.add(first.value)
    return out


def produced_keys(paths: set[tuple[str, ...]]) -> set[str]:
    """一个名字只要有"写进它的某条路径"就算有产地（含 `usage.prompt_tokens` 这类嵌套）。"""
    out: set[str] = set()
    for path in paths:
        out.add(path[0])
        out.add(path[-1])
    return out


# --------------------------------------------------------------- 两次真会话


PROMPT = "契约测试用提示词。"

TODOS = {
    "todos": [
        {"content": "读两次 notes.txt", "status": "done"},
        {"content": "写一个文件", "status": "pending"},  # 故意留未完成
    ]
}


def make_traced_agent(
    root: Path,
    responses: list[Any],
    *,
    session_id: str,
    mode: PermissionMode = PermissionMode.ASK,
    budget: int = 200_000,
    repo_map: RepoMap | None = None,
) -> tuple[Agent, Path]:
    """真工具 + 假模型 + 真落盘日志。schema 与指标都从这两次会话里取。

    `mode=ASK` 且没有确认渠道时，写操作会被保守拒绝 —— 契约要覆盖"发起了但没执行"
    这条形状，否则 `tool_calls` 与 `tool_executed` 永远相等，两个名字看着都一样。

    `budget` 默认取实测窗口量级（200k），所以正常情况下阶梯不会介入，
    上面那些数字断言才是稳定的；只有 `compacting` 那个 fixture 会把它调小。

    给了 `repo_map` 就走 `system_provider`（地图是 system 的一部分，不是单独一段
    消息）—— 不接这条线的会话永远产不出 `repo_map` 事件。
    """
    (root / "notes.txt").write_text("第一行\n第二行\n", encoding="utf-8")
    workspace = Workspace(root)
    todos = TodoList()
    registry = ToolRegistry.default(
        workspace, bash_timeout=30, extra_tools=[WriteTodosTool(workspace, todos)]
    )

    def system_provider() -> str:
        return f"{PROMPT}\n\n{repo_map.map_for_prompt()}" if repo_map is not None else PROMPT

    trace_path = root / f"{session_id}.jsonl"
    tracer = Tracer(trace_path, session_id=session_id)
    tracer.start_session(
        model="fake",
        tools=list(registry.names()),
        config={"mode": mode.value},
        system_prompt_hash=prompt_hash(PROMPT),
    )
    agent = Agent(
        llm=FakeLLM(responses),
        registry=registry,
        gate=PermissionGate(workspace=workspace, mode=mode, confirmer=None),
        system_prompt=PROMPT,
        context=ContextManager(budget=budget),
        todos=todos,
        tracer=tracer,
        price_per_mtokens=2.5,
        repo_map=repo_map,
        system_provider=system_provider if repo_map is not None else None,
    )
    return agent, trace_path


# 一次"正常但磕磕绊绊"的会话：重复读、被拒的写、虚构工具名、留了未完成的待办。
SESSION_SCRIPT = [
    scripted_tool_calls([("read_file", {"path": "notes.txt"})]),
    scripted_tool_calls([("read_file", {"path": "notes.txt"})]),
    scripted_tool_calls([("write_file", {"path": "out.txt", "content": "x"}), ("list_files", {})]),
    scripted_tool_calls([("write_todos", TODOS)]),
    scripted_final_text("读完了，写被拒了。"),
]


@pytest.fixture(scope="module")
def session(tmp_path_factory: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = tmp_path_factory.mktemp("contract")
    agent, trace_path = make_traced_agent(root, SESSION_SCRIPT, session_id="contract")
    agent.run("读两次 notes.txt，写个文件，然后收尾")
    records = replay(trace_path)
    return records, summarize_records(records)


@pytest.fixture(scope="module")
def broken(tmp_path_factory: Any) -> list[dict[str, Any]]:
    """模型侧故障的会话：只有它才产出 `error` 记录，schema 快照必须覆盖到。"""
    root = tmp_path_factory.mktemp("contract-broken")
    agent, trace_path = make_traced_agent(root, [LLMError("端点返回 500")], session_id="broken")
    agent.run("这个任务跑不动")
    return replay(trace_path)


# 走到压缩阶梯的那次会话。budget 不是手调出来的魔法数：先用一个不可能触发的
# 大预算把同一条脚本跑完，量出它最贵的一轮有多少 token，再按 `峰值 / 0.75` 定
# budget —— 压力因此稳定落在 [L1 的 0.70, L2 的 0.85) 之间，阶梯会做事，
# 但绝不掏 LLM 调用（L2 要花钱，而 FakeLLM 的脚本是按轮次排的，多要一次就全盘错位）。
COMPACT_SCRIPT = [
    scripted_tool_calls([("read_file", {"path": "big.txt"})]),
    scripted_tool_calls([("read_file", {"path": "notes.txt"})]),
    scripted_final_text("读完了。"),
]

COMPACT_PRESSURE = 0.75


def _seed_big_file(root: Path) -> None:
    (root / "big.txt").write_text("压缩契约测试用的长文本 0123456789\n" * 900, encoding="utf-8")


@pytest.fixture(scope="module")
def compacting(tmp_path_factory: Any) -> list[dict[str, Any]]:
    """`context_compact` 的字段形状必须由真事件钉住，不能照着代码手敲一份。"""
    probe_root = tmp_path_factory.mktemp("compact-probe")
    _seed_big_file(probe_root)
    probe, probe_path = make_traced_agent(
        probe_root, COMPACT_SCRIPT, session_id="compact-probe", budget=1_000_000
    )
    probe.run("读两遍文件")
    # 阶梯看的是**估算**，不是端点回来的 usage —— 所以峰值也要从 turn_start 的 est_tokens 量。
    # （假模型不回填 usage，`context_peak_tokens` 会是 0，拿它定 budget 就永远压不动。）
    peak = max(
        (int(r.get("est_tokens") or 0) for r in replay(probe_path) if r.get("kind") == "turn_start"),
        default=0,
    )
    assert peak > 0, "探针没量到估算峰值，budget 就成了瞎猜"

    root = tmp_path_factory.mktemp("compact")
    _seed_big_file(root)
    agent, trace_path = make_traced_agent(
        root, COMPACT_SCRIPT, session_id="compact", budget=int(peak / COMPACT_PRESSURE)
    )
    agent.run("读两遍文件")
    records = replay(trace_path)
    events = [r for r in records if r.get("kind") == "context_compact"]
    assert events, (
        f"这条会话本该踩到 L1（峰值 {peak:,} tokens / 预算 {int(peak / COMPACT_PRESSURE):,}）却没压缩，"
        "schema 快照就悄悄不再钉这个 kind 了。"
    )
    return records


@pytest.fixture(scope="module")
def refusing(tmp_path_factory: Any) -> list[dict[str, Any]]:
    """被上下文预算止损杀掉的那次会话：只有它才产出 `context_refuse`。

    这个 kind 是 B2 对照组的归因凭据（"关阶梯必败"必须能和"开关被拧小了"分开），
    所以它的字段形状同样得由真事件钉住。budget 直接给一个不可能够用的量级就行：
    工具 schema 本身就已经超压，第一轮请求前就该停。
    """
    root = tmp_path_factory.mktemp("refuse")
    agent, trace_path = make_traced_agent(
        root, [scripted_final_text("不会被用到")], session_id="refuse", budget=400
    )
    result = agent.run("读 notes.txt")
    records = replay(trace_path)
    events = [r for r in records if r.get("kind") == "context_refuse"]
    assert result.termination is TerminationReason.CONTEXT_OVERFLOW, (
        "这条会话本该死在预算上；死因变了就说明 `context_refuse` 正在悄悄失去覆盖"
    )
    assert len(events) == 1, f"止损记录应当恰好一条，实到 {len(events)}"
    return records


MAP_SCRIPT = [
    scripted_tool_calls([("read_file", {"path": "pkg.py"})]),
    scripted_tool_calls([("write_file", {"path": "pkg.py", "content": '"""包。"""\n\nSIZE = 2\n'})]),
    scripted_final_text("读过了，也改了。"),
]


@pytest.fixture(scope="module")
def mapped(tmp_path_factory: Any) -> list[dict[str, Any]]:
    """`repo_map` 的字段形状由真事件钉住（SPEC v2 §3.4）。

    先读后写：读那一轮证明**焦点变化不重画地图**，写那一轮证明 `invalidate()` 之后
    真的重画 —— 于是恰好两条事件（首建 + 写后重画）。条数从 2 变多就是地图开始每轮
    抖动了，那会直接打掉提示词前缀缓存，所以这里当契约钉住，不留给行为测试。
    """
    root = tmp_path_factory.mktemp("repo-map")
    (root / "pkg.py").write_text('"""包。"""\n\nVERSION = 1\n', encoding="utf-8")
    agent, trace_path = make_traced_agent(
        root,
        MAP_SCRIPT,
        session_id="repo-map",
        mode=PermissionMode.AUTO,
        repo_map=RepoMap(Workspace(root)),
    )
    agent.run("读 pkg.py，然后把常量改掉")
    records = replay(trace_path)
    events = [record for record in records if record.get("kind") == "repo_map"]
    assert len(events) == 2, (
        f"`repo_map` 应当恰好两条（首建 + 写文件后重画），实到 {len(events)} —— "
        "多了说明地图每轮重画（前缀缓存被打掉），少了说明这个 kind 正在悄悄失去覆盖"
    )
    return records


# §3.6 的执行后端与可恢复现场。一批两个调用：写（call_0）+ 读（call_1）——
# 于是"崩在批次中间"这份现场是真的能拼出来的（只保留那条助手消息，done 里只留 call_0）。
DURABLE_SCRIPT = [
    scripted_tool_calls(
        [("write_file", {"path": "made.py", "content": "VALUE = 1\n"}), ("read_file", {"path": "notes.txt"})]
    ),
    scripted_final_text("写完并撤掉了。"),
]


@pytest.fixture(scope="module")
def durable(tmp_path_factory: Any) -> list[dict[str, Any]]:
    """`backend` / `checkpoint` / `session_snapshot` / `session_replay` 的产地。

    这里走 `cli.main.build_session` 而不是手搓 Agent：`backend` 那条事件是**装配层**写的，
    测试自己 `tracer.log("backend", ...)` 等于把形状抄一遍 —— 装配改了这里不会红。

    `session_replay` 只能来自"崩在一批调用的中间"：新响应里的重复 id 会先被
    `dedupe_tool_use_ids` 改名（配对不变式要求全历史唯一 id），所以只有**已在历史里**的
    尾部批次才可能命中 `done_call_ids`。于是这份现场是真的从磁盘绕了一圈再装回去的。
    """
    from dataclasses import replace

    from miniclaude.backend.sessions import SESSIONS_DIRNAME, SessionLog
    from miniclaude.cli.main import build_session
    from miniclaude.cli.render import Renderer
    from miniclaude.config import Config

    root = tmp_path_factory.mktemp("durable")
    (root / "notes.txt").write_text("第一行\n第二行\n", encoding="utf-8")
    log = SessionLog(root=root / ".mcc" / SESSIONS_DIRNAME)

    def assemble(trace: Path, responses: list[Any]) -> Any:
        return build_session(
            config=Config(
                base_url="https://mock.local/v1",
                api_key="sk-test-abcdefghijklmn",
                model="mock-model",
                project_root=root,
                trace_path=trace,
            ),
            renderer=Renderer(write=lambda _text: None, use_rich=False),
            llm=FakeLLM(responses),
            mode=PermissionMode.AUTO,
        )

    first = assemble(root / ".traces" / "first.jsonl", DURABLE_SCRIPT)
    first.agent.run("写一个 made.py 再读一眼 notes.txt，然后收尾")
    ok, why = first.agent.undo_last_write()
    assert ok, f"写完就该撤得回去，这次撤不掉：{why}"
    clean, why = log.load(first.tracer.session_id)
    assert clean is not None, f"副作用落过盘却没有现场可恢复：{why}"
    records = list(replay(root / ".traces" / "first.jsonl"))

    # 崩在批次中间：历史停在"助手声明了 2 个调用"，账上只有第 1 个做完了。
    crash = replace(clean, messages=clean.messages[:2], done_call_ids=["call_0"], termination="")
    assert log.save(crash), f"现场写不回去：{log.degraded}"
    restored, why = log.load(crash.session_id)
    assert restored is not None, why

    second = assemble(root / ".traces" / "second.jsonl", [scripted_final_text("补完就收尾。")])
    second.agent.restore_session(restored)
    second.agent.resume()
    records += replay(root / ".traces" / "second.jsonl")

    kinds = {str(record.get("kind")) for record in records}
    missing = {"backend", "checkpoint", "session_snapshot", "session_replay"} - kinds
    assert not missing, f"这次装配没产出 {sorted(missing)} —— 快照会悄悄不再看守这些 kind"
    return records


EXT_SCRIPT = [
    scripted_tool_calls([("load_skill", {"name": "contract-skill"})]),
    scripted_tool_calls([("mcp__fx__echo", {"text": "外部工具跑一轮"})]),
    scripted_final_text("技能取到了，外部工具也调过了。"),
]


@pytest.fixture(scope="module")
def extended(tmp_path_factory: Any) -> list[dict[str, Any]]:
    """`mcp` / `skills` 两条装配期事件的产地（SPEC v2 §3.7）。

    仍然走 `build_session`：这两条记录是**装配层**写的，测试自己 log 一遍等于抄形状。
    这里同时把 AUTO 那一臂跑到真调用 —— 远端工具在 AUTO 下也必须弹确认（D19），
    只有真的调用成功一次，"命名空间前缀 → 注册 → 权限门 → 子进程"这条链路才算被证过。

    第二臂是 READONLY：`mcp` 在那里走的是"配了但一个进程都不起"的另一条形状，
    两种形状都得进快照，不然快照只钉住其中一种，另一条改了没人知道。
    """
    from miniclaude.cli.main import build_session
    from miniclaude.cli.render import Renderer
    from miniclaude.config import Config

    root = tmp_path_factory.mktemp("ext")
    (root / "notes.txt").write_text("第一行\n第二行\n", encoding="utf-8")
    skill = root / "skills" / "contract-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: contract-skill\ndescription: 契约测试用的技能\n---\n照正文做。\n" * 4,
        encoding="utf-8",
    )
    servers = (
        {
            "name": "fx",
            "endpoint": sys.executable,
            "args": [str(FIXTURE_SERVER), "good"],
        },
    )

    def assemble(mode: PermissionMode, responses: list[Any], trace: Path, confirmer: Any = None) -> Any:
        return build_session(
            config=Config(
                base_url="https://mock.local/v1",
                api_key="sk-test-abcdefghijklmn",
                model="mock-model",
                project_root=root,
                trace_path=trace,
                mcp_servers=servers,
            ),
            renderer=Renderer(write=lambda _text: None, use_rich=False),
            llm=FakeLLM(responses),
            mode=mode,
            confirmer=confirmer,
        )

    records: list[dict[str, Any]] = []

    granted = assemble(
        PermissionMode.AUTO, EXT_SCRIPT, root / ".traces" / "ext-auto.jsonl",
        confirmer=lambda _name, _summary: Answer.ONCE,
    )
    try:
        granted.agent.run("取一个技能，再用外部工具回声一句话")
    finally:
        granted.close()
    records += replay(root / ".traces" / "ext-auto.jsonl")

    refused = assemble(
        PermissionMode.READONLY,
        [scripted_final_text("只读模式不跑工具")],
        root / ".traces" / "ext-readonly.jsonl",
    )
    try:
        refused.agent.run("只读着看一眼")
    finally:
        refused.close()
    records += replay(root / ".traces" / "ext-readonly.jsonl")

    events = [record for record in records if record.get("kind") == "mcp"]
    assert len(events) == 2, f"`mcp` 应当有两条形状（装配 + 只读跳过），实到 {len(events)}"
    assert events[0]["skip_reason"] == "" and events[0]["tools"] >= 3
    assert events[1]["skip_reason"] and "running" not in events[1], "只读那一臂应当只有原因，不带运行计数"
    skill_events = [record for record in records if record.get("kind") == "skills"]
    assert skill_events and skill_events[0]["skills"] == 1
    # D19 的端到端证据：AUTO 模式下远端工具确实经过了确认，也真的执行了
    calls = [record for record in records if record.get("kind") == "tool_call" and record.get("name") == "mcp__fx__echo"]
    assert len(calls) == 1 and calls[0]["ok"] is True
    permissions = [
        record for record in records
        if record.get("kind") == "permission" and record.get("tool") == "mcp__fx__echo"
    ]
    assert permissions, "远端工具在 AUTO 模式下没留下确认记录 —— §6.3-1 就没人看守了"
    return records


# --------------------------------------------------------------- 不变式


def test_metrics_have_producers() -> None:
    paths = write_paths(package_sources())
    produced = produced_keys(paths)
    orphans = [key for key in AgentState.snapshot(AgentState()) if key not in produced]
    assert not orphans, (
        f"这些指标没有任何写入点，报表里的它们只是默认值：{orphans}。"
        "要么补上累加，要么把字段删掉 —— 不许留着它继续印进证据文件（SPEC v2 §0.3 E1）。"
    )


def test_summarize_reads_only_recorded_fields(session: Any) -> None:
    records, report = session
    end = next(record for record in reversed(records) if record.get("kind") == "run_end")
    unexplained = {key for key in report if key not in DERIVED_METRICS and key not in end}
    assert not unexplained, f"报表键 {sorted(unexplained)} 在 run_end 记录里不存在 —— 它被谁写的？"


def test_repeated_and_stalled_are_measured_not_assumed(session: Any) -> None:
    """E1/E2 的正向证据：一次整组重演要同时产出两个口径，且它们不相等、都不终止会话。

    `repeated_calls` 逐调用统计（第 2 次 read_file 与前一次同名同参 → 1），
    `stalled_groups` 整组统计（第 2 轮的签名与第 1 轮相同 → 1 组，未到 STALL_LIMIT=3
    所以不判停滞）。v1 把这两个混成一个 `redundant_calls`，谁也不累加。
    """
    _, report = session
    assert report["repeated_calls"] == 1, "连续两次同名同参调用必须被计入 repeated_calls"
    assert report["stalled_groups"] == 1, "一次整组重演计 1，但不等于终止"
    assert report["termination"] == "completed", "重复一轮就收尾，不构成 STALLED"


def test_attempts_and_executions_are_two_different_numbers(session: Any) -> None:
    """被拒 / 虚构工具名的调用"发起了但没执行"，两个数必须各自有名字。

    脚本第 3 轮一次发起 write_file（ASK 模式无确认渠道 → 拒）与 list_files（不存在的
    工具名），所以这一轮 0 次执行。v1 里 `tool_calls` 一个名字两头用。
    """
    _, report = session
    assert report["tool_calls"] == 5, "5 次发起：2 读 + 被拒的写 + 虚构工具名 + 待办"
    assert report["tool_executed"] == 3, "只有 3 次真的跑过"
    assert report["denied_actions"] == 1, "denied 只数权限门拒的，虚构工具名不算用户拒绝"
    assert report["tool_errors"] == 2, "被拒与虚构都给了模型一个 is_error 结果"
    # 只有 thrashing：整组重演了一次。no_verification 不成立 —— 这次会话一个字没写，
    # 被拒的 write_file 不算"改过东西"（判据见 infra/failure.py 的同名规则）。
    assert report["failure_modes"] == ["thrashing"]


def test_output_chars_is_summed_from_records(session: Any) -> None:
    """`wasted_output_ratio` 的分子与分母只能从 `tool_call` 记录里加出来。

    这次会话的输出都远小于 `OUTPUT_CAP`，所以"被省略的字符"必须是 **0** ——
    不是"没算"，是算了且为 0。少了这条区分，非截断会话上永远显示 0%，
    和"这个字段根本没落盘"长得一模一样。
    """
    records, report = session
    calls = [record for record in records if record.get("kind") == "tool_call"]
    assert report["output_chars"] == sum(int(record.get("output_chars") or 0) for record in calls)
    assert report["output_chars"] > 0, "全 0 说明 tool_call 里没记这个字段，那 wasted_output_ratio 就是假的"
    assert report["omitted_output_chars"] == 0


def test_omitted_chars_counts_only_what_cap_dropped() -> None:
    """省略量 = `output_chars - 2 * (OUTPUT_CAP // 2)`，只算循环层 `_cap` 丢掉的。

    分母取截断前还是截断后，同一个名字会差出一倍，所以定义本身要有一条测试钉住。
    """
    keep = OUTPUT_CAP // 2
    records = [
        {"seq": 1, "session": "s", "kind": "session_start"},
        {"seq": 2, "session": "s", "kind": "tool_call", "turn": 1, "name": "read_file",
         "ok": True, "output_chars": 2 * keep + 500},
        {"seq": 3, "session": "s", "kind": "tool_call", "turn": 1, "name": "run_tests",
         "ok": True, "output_chars": 80},
    ]
    report = summarize_records(records)
    assert report["output_chars"] == 2 * keep + 580
    assert report["omitted_output_chars"] == 500, "没到上限的那个输出不该算进浪费"


def test_live_and_offline_classification_agree(session: Any) -> None:
    """同一份轨迹，实时判定与 `mcc trace --why-failed` 的离线判定必须给同一批标签。

    这是"派生结论随记录落盘"这条纪律的存在理由：离线侧看不见完整参数和内存里的
    事实，只能读 trace。两边算出不同结果就说明有字段的定义散在了两处。
    """
    _, report = session
    assert [found.mode.value for found in classify(facts_from_records(session[0]))] == report["failure_modes"]


def test_trace_schema_snapshot(
    session: Any, broken: Any, compacting: Any, refusing: Any, mapped: Any, durable: Any, extended: Any
) -> None:
    """每个 kind 的字段集合与快照逐字段比对。

    加字段/改名/删字段时：先跑 `MCC_REGEN_SCHEMA=1 pytest tests/test_trace_contract.py`
    重生成 `tests/schema_v2.json`，再同步 SPEC v2 §3.1 的事件表 —— 两处不一致就是
    "文档说的和代码做的不是一回事"，v1 的 `session_end` 事故（§0.3 E3）正是这么发生的。

    七条会话各带一段形状：`session` 是正常流程，`broken` 只在那里出现的 `error`，
    `compacting` 提供 `context_compact`，`refusing` 提供 `context_refuse`，
    `mapped` 提供 `repo_map`，`durable` 提供 §3.6 的四条，`extended` 提供 §3.7 的两条。
    少一条，快照上就少一个无人看守的 kind。
    """
    actual = _keys_by_kind(
        list(session[0]) + list(broken) + list(compacting) + list(refusing)
        + list(mapped) + list(durable) + list(extended)
    )
    for kind, keys in sorted(actual.items()):
        assert keys >= COMMON_ENVELOPE, f"{kind} 缺了公共字段：{sorted(COMMON_ENVELOPE - keys)}"
    if os.environ.get("MCC_REGEN_SCHEMA") == "1":
        payload = {kind: sorted(keys) for kind, keys in actual.items()}
        SCHEMA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        pytest.skip("已重新生成 tests/schema_v2.json")
    assert SCHEMA_FILE.exists(), f"缺少快照文件 {SCHEMA_FILE.name}，用 MCC_REGEN_SCHEMA=1 生成它"
    expected = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert {kind: sorted(keys) for kind, keys in actual.items()} == expected, _schema_diff(expected, actual)


def test_every_kind_the_code_emits_is_pinned(session: Any, broken: Any) -> None:
    """代码里出现的每个 kind 都必须在快照里 —— 光靠样例轨迹覆盖不到全部分支。

    会话只走到它走到的那些 kind；`error` 要靠模型故障、`permission` 的 deny 要靠拒绝。
    这条用静态扫描补齐：新增一个 kind 而没同步快照就是测试红，而不是"没人跑到就算没事"。
    """
    expected = set(json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))) if SCHEMA_FILE.exists() else set()
    emitted = emitted_kinds()
    assert emitted <= expected, (
        f"这些 kind 代码里会产出、快照里没有：{sorted(emitted - expected)}。"
        "跑 MCC_REGEN_SCHEMA=1 重生成，并同步 SPEC v2 §3.1。"
    )


def _keys_by_kind(records: list[dict[str, Any]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for record in records:
        out.setdefault(str(record.get("kind")), set()).update(record.keys())
    return {kind: keys for kind, keys in out.items()}


def _schema_diff(expected: dict[str, list[str]], actual: dict[str, list[str]]) -> str:
    lines = ["trace 字段形状与快照不一致（改代码要同时改 tests/schema_v2.json 与 SPEC §3.1）："]
    for kind in sorted(set(expected) | set(actual)):
        if kind not in actual:
            lines.append(f"  · {kind}: 快照里有、实际不再产出（kind 被删或改名？）")
            continue
        if kind not in expected:
            lines.append(f"  · {kind}: 新出现的 kind，快照里没有")
            continue
        added = sorted(set(actual[kind]) - set(expected[kind]))
        removed = sorted(set(expected[kind]) - set(actual[kind]))
        if added or removed:
            lines.append(f"  · {kind}: +{added or '[]'} -{removed or '[]'}")
    return "\n".join(lines)


# --------------------------------------------------------------- 检测器自检


ORPHAN_SOURCE = """
class State:
    measured: int = 0
    guessed: int = 0

    def snapshot(self):
        return {"measured": self.measured, "guessed": self.guessed}

    def bump(self):
        self.measured += 1
"""


def test_detector_catches_a_planted_orphan() -> None:
    """前两条不变式用的检测器必须真的会报警 —— 否则它是绿色的摆设。"""
    produced = produced_keys(write_paths({"orphan.py": ORPHAN_SOURCE}))
    assert "measured" in produced
    assert "guessed" not in produced


def test_detector_ignores_pure_initialisation() -> None:
    """`self.x = 0` 不算产地。这条把"定义了但从未累加"的形状钉死。"""
    produced = produced_keys(write_paths({"a.py": "class S:\n    def __init__(self):\n        self.x = 0\n"}))
    assert "x" not in produced
