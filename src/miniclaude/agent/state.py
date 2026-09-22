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
    """一次 run() 的全部可观测计数。trace 和 CLI 摘要都从它派生。

    v2 起每个字段都必须有产地（`tests/test_trace_contract.py::test_metrics_have_producers`
    盯着这件事）。v1 的 `redundant_calls` 是这个项目的第一个假数字：定义了、进 trace 了、
    印在证据文件里，但从没被累加过。它被拆成下面两个口径不同、且都有写入点的字段。
    """

    turn: int = 0
    usage: Usage = field(default_factory=Usage)
    context_peak_tokens: int = 0
    context_compactions: int = 0     # 阶梯真的改了历史的次数（放弃的那些不算）
    context_elided_blocks: int = 0   # L1：被换成省略标记的工具结果块
    context_summary_tokens: int = 0  # L2：摘要请求自己烧的 token，不记就等于说压缩免费
    tool_calls: int = 0
    tool_errors: int = 0
    repeated_calls: int = 0        # 逐调用：与前一次同名同参（SPEC v2 §3.1）
    stalled_groups: int = 0        # 整组：命中停滞检测的轮次数，与上面那个不是同一个量
    denied_actions: int = 0
    rejections_in_a_row: int = 0
    status: Literal["running", "finished", "aborted"] = "running"

    def record_usage(self, usage: Usage) -> None:
        self.usage.prompt_tokens += usage.prompt_tokens
        self.usage.completion_tokens += usage.completion_tokens

    def record_compaction(self, *, elided: int, summary_tokens: int) -> None:
        """一次**被采用**的压缩。放弃的那些不走这里 —— 否则计数又在替代码表功。"""
        self.context_compactions += 1
        self.context_elided_blocks += elided
        self.context_summary_tokens += summary_tokens

    def snapshot(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "usage": {"prompt": self.usage.prompt_tokens, "completion": self.usage.completion_tokens,
                      "total": self.usage.total},
            "context_peak_tokens": self.context_peak_tokens,
            "context_compactions": self.context_compactions,
            "context_elided_blocks": self.context_elided_blocks,
            "context_summary_tokens": self.context_summary_tokens,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "repeated_calls": self.repeated_calls,
            "stalled_groups": self.stalled_groups,
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
    failure_modes: list[str] = field(default_factory=list)
    cost_est: float | None = None

    @property
    def succeeded(self) -> bool:
        """只有 COMPLETED 算成功。MAX_TURNS/STALLED 都是失败，别把它们粉饰成"部分完成"。"""
        return self.termination is TerminationReason.COMPLETED

    def summary_line(self) -> str:
        s = self.state
        line = (
            f"{self.termination.value} · {s.turn} 轮 · 发起 {s.tool_calls} 次调用"
            f"（{s.tool_errors} 次报错）· {s.usage.total:,} tokens"
        )
        if self.failure_modes:
            line += f" · {', '.join(self.failure_modes)}"
        return line
