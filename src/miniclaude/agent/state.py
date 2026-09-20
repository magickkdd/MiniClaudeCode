"""Agent 的运行状态与结果类型。

`AgentResult` 的形状现在就定死，V2 的 eval/ 层直接消费它 ——
否则将来要为了统计去回头改核心循环。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from miniclaude.messages import Message, Usage


class TerminationReason(StrEnum):
    """循环为什么停下。**必须是返回值而不是异常**，调用方需要区分"做完了"和"崩了"。"""

    COMPLETED = "completed"          # 模型给出纯文本答复，任务收尾
    MAX_TURNS = "max_turns"          # 轮数耗尽，通常是任务过大或模型绕圈
    STALLED = "stalled"              # 连续重复同一动作，或反复空响应
    USER_REJECTED = "user_rejected"  # 用户连续拒绝，Agent 无法推进
    LLM_FAILURE = "llm_failure"      # 端点重试耗尽或报文被拒
    CONTEXT_OVERFLOW = "context_overflow"  # 上下文超预算，主动止损
    CANCELLED = "cancelled"          # 用户中断（Ctrl-C）
    INTERNAL_ERROR = "internal_error"  # 我们自己有 bug；CLI 兜住后会话仍可继续


@dataclass
class AgentState:
    """一次 run() 的全部可观测计数。trace 和 CLI 摘要都从它派生。"""

    turn: int = 0
    usage: Usage = field(default_factory=Usage)
    context_peak_tokens: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    redundant_calls: int = 0
    denied_actions: int = 0
    rejections_in_a_row: int = 0
    status: Literal["running", "finished", "aborted"] = "running"

    def record_usage(self, usage: Usage) -> None:
        self.usage.prompt_tokens += usage.prompt_tokens
        self.usage.completion_tokens += usage.completion_tokens

    def snapshot(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "usage": {"prompt": self.usage.prompt_tokens, "completion": self.usage.completion_tokens,
                      "total": self.usage.total},
            "context_peak_tokens": self.context_peak_tokens,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "redundant_calls": self.redundant_calls,
            "denied_actions": self.denied_actions,
            "status": self.status,
        }


@dataclass
class AgentResult:
    text: str
    termination: TerminationReason
    messages: list[Message] = field(default_factory=list, repr=False)
    state: AgentState = field(default_factory=AgentState)
    todos: list[dict[str, Any]] = field(default_factory=list)
    trace_path: Path | None = None

    @property
    def succeeded(self) -> bool:
        """只有 COMPLETED 算成功。MAX_TURNS/STALLED 都是失败，别把它们粉饰成"部分完成"。"""
        return self.termination is TerminationReason.COMPLETED

    def summary_line(self) -> str:
        s = self.state
        return (
            f"{self.termination.value} · {s.turn} 轮 · {s.tool_calls} 次工具调用"
            f"（{s.tool_errors} 次报错）· {s.usage.total:,} tokens"
        )
