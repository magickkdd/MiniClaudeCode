"""统一消息模型 —— 全项目的通用语言。

Agent 循环、LLM 适配器、工具层只认这里的类，绝不传递任何厂商的原始 JSON。
换模型供应商时，改动只发生在 llm/ 目录内。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class StopReason(str, Enum):
    """模型本轮结束的原因 —— loop.py 判断"继续循环还是交付答案"的唯一依据。"""

    END_TURN = "end_turn"      # 模型认为任务完成，给出最终答复
    TOOL_USE = "tool_use"      # 模型要求调用工具，必须执行后继续
    MAX_TOKENS = "max_tokens"  # 被输出上限截断，需警告而非静默结束


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    """模型发出的"我要用这个工具"请求。id 用于和 ToolResultBlock 配对。"""

    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultBlock:
    """工具执行结果，回填给模型。is_error=True 时模型会看到失败并自我修正。"""

    tool_use_id: str
    content: str
    is_error: bool = False


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass
class Message:
    """一条对话记录。content 是块列表 —— 一次回复可以既有文本又有多个工具调用。"""

    role: Role
    content: list[ContentBlock] = field(default_factory=list)

    @classmethod
    def user_text(cls, text: str) -> "Message":
        return cls(Role.USER, [TextBlock(text)])

    @classmethod
    def assistant(cls, blocks: list[ContentBlock]) -> "Message":
        return cls(Role.ASSISTANT, list(blocks))

    @classmethod
    def tool_results(cls, results: list[ToolResultBlock]) -> "Message":
        """把一批工具结果作为**一条** user 消息回填 —— 模型看到的就是"用户提供了这些结果"。"""
        return cls(Role.USER, list(results))

    def text(self) -> str:
        """拼出本条消息里的纯文本，给 CLI 渲染和测试断言用。"""
        return "\n".join(b.text for b in self.content if isinstance(b, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    @property
    def results(self) -> list[ToolResultBlock]:
        """本条消息里的工具结果块。注意与构造器 tool_results() 区分。"""
        return [b for b in self.content if isinstance(b, ToolResultBlock)]


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResponse:
    """适配器返回给 Agent 的统一结果。raw 只用于 trace，业务逻辑不得读取。"""

    blocks: list[ContentBlock]
    stop_reason: StopReason
    usage: Usage = field(default_factory=Usage)
    raw: Any = None

    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks if isinstance(b, TextBlock))

    def text_blocks(self) -> list[TextBlock]:
        return [b for b in self.blocks if isinstance(b, TextBlock)]

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.blocks if isinstance(b, ToolUseBlock)]

    def as_message(self) -> Message:
        """转成可追加进历史的 assistant 消息。"""
        return Message(Role.ASSISTANT, list(self.blocks))


def new_tool_use_id() -> str:
    """生成 tool_use 的关联 id（FakeLLM 和适配器都需要）。"""
    return f"call_{uuid.uuid4().hex[:16]}"
