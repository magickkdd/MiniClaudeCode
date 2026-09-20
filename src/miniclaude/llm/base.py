"""LLM 客户端协议 —— Agent 与模型之间唯一的接缝。

loop.py 只依赖这个 Protocol，因此真实客户端可以整体换成 FakeLLM，
这也是 tests/test_loop_with_fake_llm.py 能零成本复现整个循环的原因。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from miniclaude.messages import LLMResponse
from miniclaude.messages import Message as AgentMessage
from miniclaude.tools.base import ToolSpec


@runtime_checkable
class LLMClient(Protocol):
    """所有模型适配器必须满足的接口。"""

    def create(
        self,
        *,
        system: str,
        messages: list[AgentMessage],
        tools: list[ToolSpec],
    ) -> LLMResponse:
        """发一轮请求，拿回归一化后的响应。

        约定：
        - 网络/限流错误在这里重试；重试耗尽后抛 LLMError，由 loop 决定是否中止。
        - 返回值必须带 stop_reason，loop 依赖它判断是否继续。
        """
        ...
