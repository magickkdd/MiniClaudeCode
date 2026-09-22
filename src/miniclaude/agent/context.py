"""上下文管理 —— MVP 只观测，不干预。

为什么不自动压缩：压缩会改变模型看到的历史，一旦处理不好
（破坏 assistant.tool_calls 与 role=tool 的配对）端点直接 400，
排查成本远高于"这次任务太大，请缩小范围"的一次明确失败。
V1 的压缩算法见 SPEC §3.5。
"""

from __future__ import annotations

from typing import Any, Iterable

from miniclaude.messages import Message, TextBlock, ToolResultBlock, ToolUseBlock

_WARN_PRESSURE = 0.8
_STOP_PRESSURE = 0.95


class ContextManager:
    """估算这轮请求要发多少 token，并用端点回的真实 usage 校准系数。"""

    def __init__(self, *, budget: int, chars_per_token: float = 3.5) -> None:
        self.budget = budget
        self.chars_per_token = chars_per_token
        self.last_actual_prompt_tokens = 0

    # ------------------------------------------------------------ 估算

    def wire_chars(
        self, *, system: str, tools: Iterable[Any] = (), messages: Iterable[Message] = ()
    ) -> int:
        """按**实际要发出去的报文规模**计字符数，包含 system 与工具声明。

        校准必须用同一个口径，否则 estimate 与真实 prompt_tokens 对不上，
        系数会被越校越偏。
        """
        total = len(system)
        for spec in tools:
            total += len(getattr(spec, "name", "")) + len(getattr(spec, "description", ""))
            total += len(str(getattr(spec, "input_schema", "")))
        for message in messages:
            for block in message.content:
                if isinstance(block, TextBlock):
                    total += len(block.text)
                elif isinstance(block, ToolUseBlock):
                    total += len(block.name) + len(str(block.input))
                elif isinstance(block, ToolResultBlock):
                    total += len(block.content)
        return total

    def estimate(
        self, *, system: str = "", tools: Iterable[Any] = (), messages: Iterable[Message] = ()
    ) -> int:
        return self.estimate_from_chars(self.wire_chars(system=system, tools=tools, messages=messages))

    def estimate_from_chars(self, chars: int) -> int:
        """已经有字符数时别再扫一遍历史 —— 一轮里估算三次是白烧 CPU。"""
        return int(chars / self.chars_per_token)

    def pressure_from_chars(self, chars: int) -> float:
        if self.budget <= 0:
            return 0.0
        return self.estimate_from_chars(chars) / self.budget

    def pressure(
        self, *, system: str = "", tools: Iterable[Any] = (), messages: Iterable[Message] = ()
    ) -> float:
        return self.pressure_from_chars(self.wire_chars(system=system, tools=tools, messages=messages))

    def calibrate(self, actual_prompt_tokens: int, sent_chars: int) -> None:
        """用一次真实往返的 usage 修正系数（取滑动平均，避免单次抖动）。

        中文字符/token 比英文代码低得多，固定 3.5 对中文仓库会严重低估，
        所以每轮都校一次。actual 里还含 system 与工具声明的 token，
        因此算出的系数天然偏保守 —— 这正是我们想要的方向。
        """
        if actual_prompt_tokens <= 0 or sent_chars <= 0:
            return
        self.last_actual_prompt_tokens = actual_prompt_tokens
        observed = sent_chars / actual_prompt_tokens
        self.chars_per_token = max(1.2, min(12.0, 0.7 * self.chars_per_token + 0.3 * observed))

    # ------------------------------------------------------------ 状态判断

    @property
    def warn_pressure(self) -> float:
        return _WARN_PRESSURE

    @property
    def stop_pressure(self) -> float:
        return _STOP_PRESSURE

    def should_warn(
        self, *, system: str = "", tools: Iterable[Any] = (), messages: Iterable[Message] = ()
    ) -> bool:
        return self.pressure(system=system, tools=tools, messages=messages) >= _WARN_PRESSURE

    def should_stop(
        self, *, system: str = "", tools: Iterable[Any] = (), messages: Iterable[Message] = ()
    ) -> bool:
        return self.pressure(system=system, tools=tools, messages=messages) >= _STOP_PRESSURE

    def snapshot(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "chars_per_token": round(self.chars_per_token, 2),
            "last_actual_prompt_tokens": self.last_actual_prompt_tokens,
        }
