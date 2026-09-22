"""Agent 核心循环。

    组装上下文 → 问模型 → 若要求调工具就执行并回填 → 再问 → 直到模型不再调工具

这就是 Agent（模型决策下一步），而不是 Workflow（代码写死步骤）。
本文件不 import input()/print()/argparse —— 交互属于 CLI，控制属于这里。

四条不可让与的控制策略（SPEC §3.1）：
  1. 全量回填：单轮多个 tool_calls 必须全部执行并一并回填，漏一个下一轮就 400。
  2. 停滞检测：连续 3 轮重复同一组调用即终止，这是防烧钱的唯一闸门。
  3. 轮数与 token 双上限：任一命中就止损。
  4. 失败不是终点：工具报错、权限被拒、未知工具名一律转成模型可读的失败描述继续跑。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from miniclaude.agent.context import HARD_FUSE_RATIO, ContextManager
from miniclaude.agent.permissions import PermissionGate
from miniclaude.agent.planner import TodoList
from miniclaude.agent.prompts import COMPACT_SYSTEM, build_summary_request
from miniclaude.agent.state import AgentResult, AgentState, TerminationReason
from miniclaude.infra.failure import (
    CallFact,
    RunFacts,
    classify,
    drops_assertions,
    verdict_of,
)
from miniclaude.infra.trace import new_span_id, prompt_hash
from miniclaude.llm.base import LLMClient
from miniclaude.llm.openai_compat import LLMError
from miniclaude.memory.repo_map import RepoMap
from miniclaude.messages import Message, Role, StopReason, ToolResultBlock, dedupe_tool_use_ids
from miniclaude.tools.base import ToolResult
from miniclaude.tools.registry import ToolRegistry

STALL_LIMIT = 3
EMPTY_REPLY_RETRIES = 2
REJECTION_STOP = 2
MAX_OUTPUT_CHARS = 30_000
PREFIX_SAMPLE_CHARS = 2000
# 地图的信号 ① 只认真正指向单个文件的调用。search_text / find_files 给的是目录或
# glob 模式，把它们混进"最近读过的文件"会让一个宽搜索把整个仓库标成焦点 —— 那等于没有焦点。
FOCUS_READ_TOOLS = frozenset({"read_file"})
FOCUS_WRITE_TOOLS = frozenset({"write_file", "edit_file"})


class EventKind(StrEnum):
    TURN_START = "turn_start"
    ASSISTANT_TEXT = "assistant_text"
    PERMISSION = "permission"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TODO_UPDATE = "todo_update"
    CONTEXT_COMPACT = "context_compact"    # 压缩阶梯动手了
    WARNING = "warning"
    ERROR = "error"
    FINISHED = "finished"


@dataclass(frozen=True)
class AgentEvent:
    """循环向外汇报的进度。CLI 只订阅它，从不侵入循环内部。"""

    kind: EventKind
    payload: dict[str, Any] = field(default_factory=dict)


EventHandler = Callable[[AgentEvent], None]


class Agent:
    """持有对话历史，驱动"问模型 → 执行工具 → 回填结果"的循环。"""

    def __init__(
        self,
        *,
        llm: LLMClient,
        registry: ToolRegistry,
        gate: PermissionGate,
        system_prompt: str,
        max_turns: int = 25,
        max_total_tokens: int = 800_000,
        context: ContextManager | None = None,
        todos: TodoList | None = None,
        on_event: EventHandler | None = None,
        tracer: Any = None,
        price_per_mtokens: float = 0.0,
        summarizer_llm: LLMClient | None = None,
        repo_map: RepoMap | None = None,
        system_provider: Callable[[], str] | None = None,
        notes: str = "",
    ) -> None:
        self.llm = llm
        # L2 摘要默认与主循环同一个客户端；eval 的 fake 引擎必须换成独立队列的假摘要器，
        # 否则每次摘要都会从主剧本里吃掉一条响应 —— 见 demos/fakes.py::FakeSummarizer。
        self.summarizer_llm = summarizer_llm or llm
        self.registry = registry
        self.gate = gate
        self.base_prompt = system_prompt
        # 地图是活的（仓库变了要重画），所以装配方可以给一个"重渲染整段 system"的回调。
        # 没给就用固定的 system_prompt —— 手写提示词的调用方（测试、demo）不该被迫
        # 认识 RepoMap。
        self.system_provider = system_provider
        self.repo_map = repo_map
        # 工作记忆里的笔记在**会话开始时冻结**：本轮跑出来的笔记进不了本轮的提示词，
        # 那是设计而不是缺陷 —— 历史里已经有 run_tests 的原始结果，再发一遍是纯开销。
        self.notes = notes
        self.max_turns = max_turns
        self.max_total_tokens = max_total_tokens
        # 默认值只有一处来源（config.DEFAULTS）。这里再拍一个数就会有两套预算 ——
        # v1 就是这么把 120,000 同时写进 config.py 和这里的。
        self.context = context or ContextManager()
        self.todos = todos or TodoList()
        self.on_event = on_event
        self.tracer = tracer
        self.price_per_mtokens = price_per_mtokens
        self.messages: list[Message] = []
        self.state = AgentState()
        self._last_error = ""
        self._run_span = ""
        self._turn_span = ""
        self._run_started = 0.0
        self._call_facts: list[CallFact] = []
        self._est_tokens: list[int] = []
        self._output_by_turn: dict[int, int] = {}
        self._last_call_key: tuple[str, str] | None = None

    # --------------------------------------------------------------- 公共

    def reset(self) -> None:
        self.messages.clear()
        self.state = AgentState()
        self.todos.items.clear()
        self._call_facts = []
        self._est_tokens = []
        self._output_by_turn = {}
        self._last_call_key = None
        if self.repo_map is not None:
            # /reset 后焦点归零：新任务不该继续沿用上一个任务读文件带出来的排序偏好。
            self.repo_map.forget_focus()

    def cancel(self) -> AgentResult:
        """用户在半途中断（Ctrl-C）时由 CLI 调用。

        历史**故意保留**：中断不该毁掉已经做的一半工作，紧接着输入
        "继续" 就能从当前位置往下走。
        """
        return self._finish(TerminationReason.CANCELLED)

    def current_system(self) -> str:
        """本轮实际会发出去的 system 文本，供 /context 和测试读取。"""
        return self._system()

    def _compact_if_needed(
        self, system: str, specs: list[Any], est_tokens: int, sent_chars: int
    ) -> tuple[int, int]:
        """跑压缩阶梯，返回**压缩之后**的估算值。改历史只在配对完好时才发生。

        每层都单独写一条 `context_compact`：报表要能回答"L2 试没试过、省了多少、
        这层自己花了多少 token"，这些只能按层记。
        """
        if self.context.level_for_pressure(self.context.pressure_from_chars(sent_chars)) is None:
            return est_tokens, sent_chars

        reports = self.context.run_ladder(
            system=system,
            tools=specs,
            messages=self.messages,
            summarizer=self._summarize_history,
        )
        for report in reports:
            payload = report.as_trace()
            payload["turn"] = self.state.turn
            self._trace(
                "context_compact",
                span_id=new_span_id(),
                parent_span_id=self._turn_span,
                **payload,
            )
            self._emit(EventKind.CONTEXT_COMPACT, **payload)
            if not report.pairing_ok:
                continue
            if report.changed:
                self.messages = report.messages
                self.state.record_compaction(
                    elided=report.elided_blocks, summary_tokens=report.summary_tokens
                )
        sent_chars = self.context.wire_chars(system=system, tools=specs, messages=self.messages)
        return self.context.estimate_from_chars(sent_chars), sent_chars

    def _trace_context_stop(
        self, line: str, est_tokens: int, sent_chars: int, threshold_tokens: int
    ) -> None:
        """把"这一轮的请求为什么根本没发出去"写进 trace。

        UI 上止损只是一行红字，但离线侧（`mcc trace --why-failed`、B2 的 A/B 证据）必须
        能只读 trace 就复现判断：越的是哪条线、阈值多少、当时估到多少、阶梯开着没有。
        没有这条记录时，一条被预算杀掉的会话在 trace 里就只是"第 4 轮突然没了下文"，
        而"关阶梯必败"这句话将无法与"某个开关碰巧拧小了"区分开。
        """
        self._trace(
            "context_refuse",
            span_id=new_span_id(),
            parent_span_id=self._turn_span,
            turn=self.state.turn,
            line=line,
            threshold_tokens=threshold_tokens,
            est_tokens=est_tokens,
            budget_tokens=self.context.budget,
            hard_limit_tokens=self.context.hard_limit,
            pressure=round(self.context.pressure_from_chars(sent_chars), 3),
            ladder_enabled=self.context.enabled,
        )

    def _summarize_history(self, dropped: list[Message], goal: str) -> tuple[str, int]:
        """L2 的实现细节：发一次不带工具的独立请求要摘要。返回 (文本, 自身开销 token)。

        这次调用必须记账（SPEC v2 §3.3）：不记的话压缩在报表上就是免费的，
        而它恰恰是全循环里最贵的一次单点开销。
        """
        request = [Message.user_text(build_summary_request(dropped, goal))]
        try:
            response = self.summarizer_llm.create(system=COMPACT_SYSTEM, messages=request, tools=[])
        except LLMError as exc:
            # 摘要失败不该让整个会话跟着死：交回空文本，context 层会用本地统计兜底，
            # 那条路径至少还带着"已改动文件"，比丢历史或中止都便宜。
            self._last_error = f"摘要请求失败：{exc}"
            self._trace(
                "error",
                span_id=new_span_id(),
                parent_span_id=self._turn_span,
                turn=self.state.turn,
                layer="compact",
                message=str(exc)[:500],
            )
            return "", 0
        self.state.record_usage(response.usage)
        return response.text(), response.usage.total

    def run(self, user_input: str, *, task_id: str | None = None) -> AgentResult:
        """处理一条用户请求，返回最终答复与全部计数。

        `task_id` 由 eval 层传入（同一个 Agent 在批跑里逐个任务复用），
        人肉使用时留空即可 —— 报表按 run_id 聚合，不按 task_id。
        """
        self.messages.append(Message.user_text(user_input))
        self.state = AgentState()
        self._last_error = ""
        self._run_span = new_span_id()
        self._turn_span = self._run_span
        self._run_started = time.perf_counter()
        self._call_facts = []
        self._est_tokens = []
        self._output_by_turn = {}
        self._last_call_key = None
        self._trace(
            "run_start",
            span_id=self._run_span,
            run_id=self._run_span,
            task_id=task_id,
            user_input_chars=len(user_input),
        )

        stall_signature: str | None = None
        stall_count = 0
        empty_replies = 0
        truncated_replies = 0

        while True:
            if self.state.turn >= self.max_turns:
                return self._finish(TerminationReason.MAX_TURNS)
            if self.state.usage.total >= self.max_total_tokens:
                self._emit(EventKind.WARNING, reason="token 预算耗尽")
                return self._finish(TerminationReason.MAX_TURNS)

            self.state.turn += 1
            # 轮 span 先开：压缩事件属于这一轮，挂在上一轮的 span 下就查不到是谁压的。
            self._turn_span = new_span_id()
            map_stamp = self.repo_map.refreshes if self.repo_map is not None else -1
            system = self._system()
            if self.repo_map is not None and self.repo_map.refreshes != map_stamp:
                # 只在地图**真的换了内容**时记一条。每轮都记就答不了"哪一轮开始模型看到的
                # 仓库形状变了"，而 B3 的轮数差值恰恰要归因到这里。
                self._trace(
                    "repo_map",
                    span_id=new_span_id(),
                    parent_span_id=self._turn_span,
                    turn=self.state.turn,
                    **self.repo_map.stats.as_trace(),
                )
            specs = self.registry.specs()
            sent_chars = self.context.wire_chars(system=system, tools=specs, messages=self.messages)
            est_tokens = self.context.estimate_from_chars(sent_chars)
            self._est_tokens.append(est_tokens)

            # 先看熔断，再看阶梯：请求已经大到必然被拒时，压缩那点功夫不值得花。
            if self.context.over_hard_limit(est_tokens):
                self._emit(
                    EventKind.ERROR,
                    message=(
                        f"上下文估算 {est_tokens:,} tokens 已越过硬熔断线 "
                        f"{int(HARD_FUSE_RATIO * self.context.hard_limit):,}（CONTEXT_HARD_LIMIT 的 90%），"
                        "主动停止以避免请求被端点拒绝。请缩小任务范围或换更小的读取粒度。"
                    ),
                )
                self._trace_context_stop(
                    "hard_fuse", est_tokens, sent_chars,
                    int(HARD_FUSE_RATIO * self.context.hard_limit),
                )
                return self._finish(TerminationReason.CONTEXT_OVERFLOW)

            est_tokens, sent_chars = self._compact_if_needed(system, specs, est_tokens, sent_chars)
            pressure = self.context.pressure_from_chars(sent_chars)

            if pressure >= self.context.stop_pressure:
                self._emit(
                    EventKind.ERROR,
                    message=(
                        f"压缩之后上下文仍占预算的 {pressure:.0%}（估算 {est_tokens:,} / "
                        f"{self.context.budget:,} tokens），继续跑必然被拒。已主动停止。"
                    ),
                )
                self._trace_context_stop(
                    "l3_refuse", est_tokens, sent_chars,
                    int(self.context.stop_pressure * self.context.budget),
                )
                return self._finish(TerminationReason.CONTEXT_OVERFLOW)
            if pressure >= self.context.warn_pressure:
                self._emit(
                    EventKind.WARNING,
                    message=f"上下文使用率 {pressure:.0%}，压缩阶梯还能撑一会儿，但建议尽快收尾。",
                )

            self._trace(
                "turn_start",
                span_id=self._turn_span,
                parent_span_id=self._run_span,
                turn=self.state.turn,
                message_count=len(self.messages),
                est_tokens=est_tokens,
            )
            self._emit(EventKind.TURN_START, turn=self.state.turn)
            self._trace(
                "llm_request",
                span_id=self._turn_span,
                parent_span_id=self._run_span,
                turn=self.state.turn,
                message_count=len(self.messages),
                tools_count=len(specs),
                est_tokens=est_tokens,
                prefix_hash=prompt_hash(system + self.messages[0].text()[:PREFIX_SAMPLE_CHARS]),
            )
            requested = time.perf_counter()

            try:
                response = self.llm.create(system=system, messages=self.messages, tools=specs)
            except LLMError as exc:
                self._last_error = str(exc)
                self._trace(
                    "error",
                    span_id=self._turn_span,
                    parent_span_id=self._run_span,
                    turn=self.state.turn,
                    layer="llm",
                    message=str(exc)[:500],
                )
                self._emit(EventKind.ERROR, message=str(exc))
                return self._finish(TerminationReason.LLM_FAILURE)

            self.context.calibrate(response.usage.prompt_tokens, sent_chars)
            self.state.record_usage(response.usage)
            self.state.context_peak_tokens = max(self.state.context_peak_tokens, response.usage.prompt_tokens)
            message = response.as_message()
            repairs = dedupe_tool_use_ids(message, self.messages)
            self.messages.append(message)
            self._trace(
                "llm_response",
                span_id=self._turn_span,
                parent_span_id=self._run_span,
                turn=self.state.turn,
                stop_reason=response.stop_reason.value,
                blocks=[type(block).__name__ for block in response.blocks],
                usage={"prompt": response.usage.prompt_tokens, "completion": response.usage.completion_tokens},
                id_repairs=len(repairs),
                latency=round(time.perf_counter() - requested, 3),
            )

            if text := response.text().strip():
                self._emit(EventKind.ASSISTANT_TEXT, text=text)

            calls = message.tool_uses
            if not calls:
                if not text:
                    empty_replies += 1
                    if empty_replies > EMPTY_REPLY_RETRIES:
                        return self._finish(TerminationReason.STALLED)
                    self.messages.append(Message.user_text("你上一条回复是空的。请继续任务，或给出最终结论。"))
                    continue
                if response.stop_reason is StopReason.MAX_TOKENS:
                    truncated_replies += 1
                    if truncated_replies <= 2:
                        self.messages.append(
                            Message.user_text(
                                "上一条回复因长度上限被截断。请改用更小的读取范围（read_file 的 offset/limit）"
                                "或分多次输出，然后继续。"
                            )
                        )
                        continue
                    return self._finish(TerminationReason.STALLED)
                return self._finish(TerminationReason.COMPLETED)

            empty_replies = 0
            signature = _signature(calls)
            if signature == stall_signature:
                # 整组重复 —— 与下面 repeated_calls 的"逐调用同名同参"是两个口径，别混
                self.state.stalled_groups += 1
                stall_count += 1
            else:
                stall_count = 1
            stall_signature = signature
            if stall_count >= STALL_LIMIT:
                self._emit(
                    EventKind.ERROR,
                    message=f"连续 {stall_count} 轮重复完全相同的工具调用，判定为原地打转，已停止。",
                )
                return self._finish(TerminationReason.STALLED)

            results, all_denied = self._run_tools(calls)
            self.messages.append(Message.tool_results(results))

            if all_denied:
                self.state.rejections_in_a_row += 1
                if self.state.rejections_in_a_row >= REJECTION_STOP:
                    return self._finish(TerminationReason.USER_REJECTED)
            else:
                self.state.rejections_in_a_row = 0

    # --------------------------------------------------------------- 工具

    def _run_tools(self, calls: list[Any]) -> tuple[list[ToolResultBlock], bool]:
        """顺序执行本轮全部调用。刻意不并发 —— 副作用顺序必须与模型声明顺序一致。"""
        results: list[ToolResultBlock] = []
        executed_any = False

        for call in calls:
            call_span = new_span_id()
            args = call.input if isinstance(call.input, dict) else {}
            tool = self.registry.get(call.name)
            elapsed = 0.0
            if tool is None:
                result = ToolResult.err(
                    f"没有名为 {call.name!r} 的工具。可用工具：{', '.join(self.registry.names())}。"
                    "请勿虚构工具名。"
                )
                risk, executed = "unknown", False
                # 虚构工具名也是一次"没能执行的发起"。不落 permission 记录，离线侧
                # 就看不见这条调用，权限占比的分母会和实时算的对不上。
                self._trace(
                    "permission",
                    span_id=call_span,
                    parent_span_id=self._turn_span,
                    turn=self.state.turn,
                    tool=call.name,
                    decision="deny",
                    rule_hit="unknown-tool",
                )
            else:
                risk = tool.risk_level.value
                allowed, reason = self.gate.authorize(tool, call.input)
                self._trace(
                    "permission",
                    span_id=call_span,
                    parent_span_id=self._turn_span,
                    turn=self.state.turn,
                    tool=call.name,
                    decision="allow" if allowed else "deny",
                    rule_hit=reason,
                )
                self._emit(EventKind.PERMISSION, tool=call.name, allowed=allowed, reason=reason, call=call)
                if allowed:
                    executed_any = True
                    executed = True
                    self._emit(EventKind.TOOL_START, tool=call.name, args=call.input)
                    started = time.perf_counter()
                    result = tool.invoke(call)
                    elapsed = time.perf_counter() - started
                    self._emit(
                        EventKind.TOOL_END,
                        tool=call.name,
                        ok=not result.is_error,
                        elapsed=elapsed,
                        output=result.content,
                    )
                else:
                    executed = False
                    self.state.denied_actions += 1
                    result = ToolResult.err(
                        f"用户拒绝执行 {call.name}（{reason}）。"
                        "请不要重复同样的请求；可以改用只读手段继续，或向用户说明你需要什么权限后收尾。"
                    )

            ok = not result.is_error
            self.state.tool_calls += 1
            key = (call.name, _key_of(args))
            if key == self._last_call_key:
                self.state.repeated_calls += 1
            self._last_call_key = key
            if not ok:
                self.state.tool_errors += 1

            # 派生结论只在这里算一次，实时分类与落盘的记录因此共用同一份定义。
            verdict = verdict_of(call.name, result.content) if executed else None
            gaming = executed and drops_assertions(call.name, args)
            self._call_facts.append(
                CallFact(
                    turn=self.state.turn,
                    name=call.name,
                    ok=ok,
                    args=args,
                    verdict=verdict,
                    executed=executed,
                    drops_assert=gaming,
                )
            )
            if executed:
                # 与落盘的 output_chars 同一个数：两侧算的是同一件事，`context_growth`
                # 的实时判定和 `mcc trace` 的事后判定才不会各说各话。
                self._output_by_turn[self.state.turn] = self._output_by_turn.get(self.state.turn, 0) + len(
                    result.content
                )
                self._trace(
                    "tool_call",
                    span_id=call_span,
                    parent_span_id=self._turn_span,
                    turn=self.state.turn,
                    name=call.name,
                    tool_use_id=call.id,
                    risk=risk,
                    args=_digest(call.input),
                    ok=ok,
                    output_chars=len(result.content),
                    latency=round(elapsed, 3),
                    verdict=verdict,
                    drops_assert=gaming,
                )
                self._observe_repo_map(call.name, args, ok)

            results.append(
                ToolResultBlock(
                    tool_use_id=call.id,
                    content=_cap(result.content, MAX_OUTPUT_CHARS),
                    is_error=result.is_error,
                )
            )

            if call.name == "write_todos" and not result.is_error:
                self._trace("todo_update", turn=self.state.turn, items=self.todos.snapshot())
                self._emit(EventKind.TODO_UPDATE, items=self.todos.snapshot())

        return results, (bool(results) and not executed_any)

    def _observe_repo_map(self, name: str, args: dict[str, Any], ok: bool) -> None:
        """把刚才那次文件操作喂给地图：读/改过的都进焦点，改过的还要作废重建（§3.4 信号 ①）。

        只在工具**真的执行过**时调用 —— 被权限门拒掉的读写没碰到磁盘，算进焦点就是
        拿一个没发生的动作去影响下一轮的排序。
        """
        if self.repo_map is None:
            return
        path = str(args.get("path") or args.get("file_path") or "").strip()
        if not path:
            return
        if name in FOCUS_READ_TOOLS:
            self.repo_map.observe_paths([path])
        elif name in FOCUS_WRITE_TOOLS and ok:
            self.repo_map.observe_paths([path])
            self.repo_map.invalidate([path])

    # --------------------------------------------------------------- 内部

    def _system(self) -> str:
        """本轮实际发出去的 system。

        顺序是刻意的：稳定的装配产物在前（`system_provider` 会重渲染，但地图内部
        按仓库指纹缓存，所以前缀不会抖），会变的东西在后（笔记、当前任务清单）。
        提示词缓存吃的是最长公共前缀，把动的东西放后面能少付钱。
        """
        base = self.system_provider() if self.system_provider is not None else self.base_prompt
        parts = [base]
        if self.notes:
            parts.append(self.notes)
        if not self.todos.is_empty:
            parts.append(f"# 当前任务清单\n{self.todos.render()}")
        return "\n\n".join(parts)

    def _finish(self, reason: TerminationReason) -> AgentResult:
        text = self._closing_text(reason)
        self.state.status = "finished" if reason is TerminationReason.COMPLETED else "aborted"
        facts = self._facts(reason)
        findings = classify(facts)
        modes = [found.mode.value for found in findings]
        cost = self._cost_est()
        result = AgentResult(
            text=text,
            termination=reason,
            messages=list(self.messages),
            state=self.state,
            todos=self.todos.snapshot(),
            trace_path=getattr(self.tracer, "path", None),
            failure_modes=modes,
            cost_est=cost,
        )
        for found in findings:  # 标签之外还要留下证据：哪一轮、哪个调用、为什么这么判
            self._trace(
                "failure_mode",
                turn=found.turn or 0,
                mode=found.mode.value,
                why=found.why,
                prescription=found.prescription,
            )
        # run_end 必须是这次 run 的最后一条记录：S13 的 durable 续跑靠它判断"这轮跑完了"
        self._trace(
            "run_end",
            span_id=self._run_span or None,
            termination=reason.value,
            failure_modes=modes,
            cost_est=cost,
            wall_ms=int((time.perf_counter() - self._run_started) * 1000) if self._run_started else 0,
            todos=self.todos.snapshot(),
            **self.state.snapshot(),
        )
        self._emit(EventKind.FINISHED, termination=reason.value, summary=result.summary_line())
        return result

    def _facts(self, reason: TerminationReason) -> RunFacts:
        todos = self.todos.snapshot()
        return RunFacts(
            termination=reason.value,
            calls=list(self._call_facts),
            est_tokens=list(self._est_tokens),
            turn_output_chars=[self._output_by_turn.get(turn, 0) for turn in range(1, self.state.turn + 1)],
            turns=self.state.turn,
            repeated_calls=self.state.repeated_calls,
            stalled_groups=self.state.stalled_groups,
            denied_actions=self.state.denied_actions,
            todos_total=len(todos),
            todos_open=sum(1 for item in todos if item.get("status") not in {"done", "cancelled"}),
        )

    def _cost_est(self) -> float | None:
        """单价未知就报 null，不报 0 —— "没有成本"和"不知道成本"是两回事。"""
        if self.price_per_mtokens <= 0:
            return None
        return round(self.state.usage.total / 1_000_000 * self.price_per_mtokens, 4)

    def _closing_text(self, reason: TerminationReason) -> str:
        """非正常结束时，也要给用户一段能说清"停在哪、为什么、下一步怎么办"的话。"""
        last_said = next(
            (
                message.text().strip()
                for message in reversed(self.messages)
                if message.role is Role.ASSISTANT and message.text().strip()
            ),
            "",
        )
        tail = f"\n\n模型最后说的是：{last_said[:400]}" if last_said else ""
        if reason is TerminationReason.MAX_TURNS:
            return (
                f"已达到最大轮数（{self.max_turns}），任务未完成。"
                "请把任务拆小一点，或指定更明确的目标文件后重试。" + tail
            )
        if reason is TerminationReason.STALLED:
            return "Agent 连续重复同样的动作，或回复被反复截断，已停止以避免无意义消耗。" + tail
        if reason is TerminationReason.USER_REJECTED:
            return "你连续拒绝了所需的操作，无法继续。请改用只读模式或重新授权。"
        if reason is TerminationReason.CONTEXT_OVERFLOW:
            return "对话上下文已接近模型预算上限，已主动停止。请开新会话，或把目标范围收窄到具体文件。" + tail
        if reason is TerminationReason.LLM_FAILURE:
            # 端点原始错误必须透出来，否则用户只看到"失败了"，无从排查
            detail = f"：{self._last_error}" if self._last_error else ""
            return f"模型调用失败{detail}{tail}"
        if reason is TerminationReason.CANCELLED:
            return "已取消。" + tail
        return last_said

    def _emit(self, kind: EventKind, **payload: Any) -> None:
        if self.on_event is not None:
            self.on_event(AgentEvent(kind=kind, payload=payload))

    def _trace(self, kind: str, **payload: Any) -> None:
        log = getattr(self.tracer, "log", None)
        if log is not None:
            log(kind, **payload)


def _signature(calls: list[Any]) -> str:
    return json.dumps(
        sorted((call.name, json.dumps(_plain(call.input), sort_keys=True, ensure_ascii=False)) for call in calls),
        ensure_ascii=False,
    )


def _key_of(args: Any) -> str:
    """单个调用的规范化参数。与 `_signature` 的区别就是两个指标的区别：
    `repeated_calls` 问"这一次和上一次是否同名同参"，`stalled_groups` 问"整组是否重演"。"""
    return json.dumps(_plain(args), sort_keys=True, ensure_ascii=False)


def _plain(value: Any) -> Any:
    """给停滞签名用的稳定表示：不比较对象身份，只看内容。"""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _digest(args: dict[str, Any]) -> dict[str, Any]:
    """日志里不放整份文件内容 —— 只放可辨识的摘要。"""
    out: dict[str, Any] = {}
    for key, value in args.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        out[key] = text if len(text) <= 200 else f"{text[:200]}…({len(text)} chars)"
    return out


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    keep = limit // 2
    return f"{text[:keep]}\n\n[… 输出过大，中间 {len(text) - 2 * keep} 字符已省略 …]\n\n{text[-keep:]}"
