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

from miniclaude.agent.context import ContextManager
from miniclaude.agent.permissions import PermissionGate
from miniclaude.agent.planner import TodoList
from miniclaude.agent.state import AgentResult, AgentState, TerminationReason
from miniclaude.llm.base import LLMClient
from miniclaude.llm.openai_compat import LLMError
from miniclaude.messages import Message, Role, StopReason, ToolResultBlock
from miniclaude.tools.base import ToolResult
from miniclaude.tools.registry import ToolRegistry

STALL_LIMIT = 3
EMPTY_REPLY_RETRIES = 2
REJECTION_STOP = 2
MAX_OUTPUT_CHARS = 30_000


class EventKind(StrEnum):
    TURN_START = "turn_start"
    ASSISTANT_TEXT = "assistant_text"
    PERMISSION = "permission"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TODO_UPDATE = "todo_update"
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
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.gate = gate
        self.base_prompt = system_prompt
        self.max_turns = max_turns
        self.max_total_tokens = max_total_tokens
        self.context = context or ContextManager(budget=120_000)
        self.todos = todos or TodoList()
        self.on_event = on_event
        self.tracer = tracer
        self.messages: list[Message] = []
        self.state = AgentState()
        self._last_error = ""

    # --------------------------------------------------------------- 公共

    def reset(self) -> None:
        self.messages.clear()
        self.state = AgentState()
        self.todos.items.clear()

    def cancel(self) -> AgentResult:
        """用户在半途中断（Ctrl-C）时由 CLI 调用。

        历史**故意保留**：中断不该毁掉已经做的一半工作，紧接着输入
        "继续" 就能从当前位置往下走。
        """
        return self._finish(TerminationReason.CANCELLED)

    def current_system(self) -> str:
        """本轮实际会发出去的 system 文本，供 /context 和测试读取。"""
        return self._system()

    def run(self, user_input: str) -> AgentResult:
        """处理一条用户请求，返回最终答复与全部计数。"""
        self.messages.append(Message.user_text(user_input))
        self.state = AgentState()
        self._last_error = ""
        self._trace("run_start", user_input_chars=len(user_input))

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
            system = self._system()
            specs = self.registry.specs()

            if self.context.should_stop(system=system, tools=specs, messages=self.messages):
                self._emit(EventKind.ERROR, message="上下文已接近预算上限，主动停止以避免请求被端点拒绝。")
                return self._finish(TerminationReason.CONTEXT_OVERFLOW)
            if self.context.should_warn(system=system, tools=specs, messages=self.messages):
                self._emit(EventKind.WARNING, message="上下文使用率超过 80%，建议尽快收尾或缩小任务范围。")

            self._trace("turn_start", turn=self.state.turn, message_count=len(self.messages))
            self._emit(EventKind.TURN_START, turn=self.state.turn)
            sent_chars = self.context.wire_chars(system=system, tools=specs, messages=self.messages)

            try:
                response = self.llm.create(system=system, messages=self.messages, tools=specs)
            except LLMError as exc:
                self._last_error = str(exc)
                self._trace("error", layer="llm", message=str(exc)[:500])
                self._emit(EventKind.ERROR, message=str(exc))
                return self._finish(TerminationReason.LLM_FAILURE)

            self.context.calibrate(response.usage.prompt_tokens, sent_chars)
            self.state.record_usage(response.usage)
            self.state.context_peak_tokens = max(self.state.context_peak_tokens, response.usage.prompt_tokens)
            self.messages.append(response.as_message())
            self._trace(
                "llm_response",
                turn=self.state.turn,
                stop_reason=response.stop_reason.value,
                blocks=[type(block).__name__ for block in response.blocks],
                usage={"prompt": response.usage.prompt_tokens, "completion": response.usage.completion_tokens},
            )

            if text := response.text().strip():
                self._emit(EventKind.ASSISTANT_TEXT, text=text)

            calls = response.tool_uses
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
            stall_count = stall_count + 1 if signature == stall_signature else 1
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
            tool = self.registry.get(call.name)
            if tool is None:
                result = ToolResult.err(
                    f"没有名为 {call.name!r} 的工具。可用工具：{', '.join(self.registry.names())}。"
                    "请勿虚构工具名。"
                )
            else:
                allowed, reason = self.gate.authorize(tool, call.input)
                self._trace("permission", turn=self.state.turn, tool=call.name, decision=reason)
                self._emit(EventKind.PERMISSION, tool=call.name, allowed=allowed, reason=reason, call=call)
                if allowed:
                    executed_any = True
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
                    self._trace(
                        "tool_call",
                        turn=self.state.turn,
                        name=call.name,
                        args=_digest(call.input),
                        ok=not result.is_error,
                        output_chars=len(result.content),
                        latency=round(elapsed, 3),
                    )
                else:
                    self.state.denied_actions += 1
                    result = ToolResult.err(
                        f"用户拒绝执行 {call.name}（{reason}）。"
                        "请不要重复同样的请求；可以改用只读手段继续，或向用户说明你需要什么权限后收尾。"
                    )

            self.state.tool_calls += 1
            if result.is_error:
                self.state.tool_errors += 1
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

    # --------------------------------------------------------------- 内部

    def _system(self) -> str:
        """把当前任务清单注入系统提示。

        计划必须回到上下文里，否则模型下一轮就不记得自己承诺过什么步骤。
        """
        if self.todos.is_empty:
            return self.base_prompt
        return f"{self.base_prompt}\n\n# 当前任务清单\n{self.todos.render()}"

    def _finish(self, reason: TerminationReason) -> AgentResult:
        text = self._closing_text(reason)
        self.state.status = "finished" if reason is TerminationReason.COMPLETED else "aborted"
        result = AgentResult(
            text=text,
            termination=reason,
            messages=list(self.messages),
            state=self.state,
            todos=self.todos.snapshot(),
            trace_path=getattr(self.tracer, "path", None),
        )
        self._trace("run_end", termination=reason.value, **self.state.snapshot())
        self._emit(EventKind.FINISHED, termination=reason.value, summary=result.summary_line())
        return result

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
