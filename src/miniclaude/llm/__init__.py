"""LLM 层对外出口。

后续若要接 Anthropic 原生 API，新增 anthropic.py 实现同一个 LLMClient 协议即可，
上层零改动。
"""

from miniclaude.llm.base import LLMClient
from miniclaude.llm.openai_compat import LLMError, OpenAICompatClient

__all__ = ["LLMClient", "LLMError", "OpenAICompatClient"]
