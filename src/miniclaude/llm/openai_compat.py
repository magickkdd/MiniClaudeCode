"""OpenAI 兼容端点适配器（DeepSeek / 通义 / vLLM / 自建网关等）。

这里是全项目唯一知道 OpenAI 报文格式的地方。两套语义的映射关系：

    我们的模型                      OpenAI 报文
    ----------------------------    --------------------------------
    system 参数                     {"role": "system"}
    TextBlock                       message.content
    ToolUseBlock                    assistant.tool_calls[i]
    ToolResultBlock                 {"role": "tool", "tool_call_id": ...}
    StopReason.TOOL_USE             finish_reason == "tool_calls"

三个必须处理的坑：
1. 历史里的 tool_calls 要原样回传，否则 role=tool 找不到配对，端点直接 400。
   部分端点还要求 tool 消息带 name，所以回填时要反查工具名。
2. 兼容端点质量参差：content 与 tool_calls 可能同时为空，finish_reason 可能缺失，
   归一化时必须给 StopReason 兜底，不能抛 KeyError。
3. arguments 是**字符串**不是对象，且模型有概率吐出非法 JSON —— 解析失败要转成
   让模型能自愈的错误描述，而不是让 Agent 崩在 json.loads 上。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

import httpx

from miniclaude.messages import (
    LLMResponse,
    Message,
    Role,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    new_tool_use_id,
)
from miniclaude.tools.base import ToolSpec

_FINISH_REASON_MAP: dict[str, StopReason] = {
    "stop": StopReason.END_TURN,
    "end_turn": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "function_call": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
}

_RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


class LLMError(RuntimeError):
    """重试耗尽后的模型调用失败，由 loop 转成一次可读的终止。"""

    def __init__(self, message: str, *, status: int | None = None, attempts: int = 1) -> None:
        super().__init__(message)
        self.status = status
        self.attempts = attempts


class OpenAICompatClient:
    """满足 llm.base.LLMClient 协议的真实客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_tokens: int = 4096,
        temperature: float | None = None,
        timeout: int = 120,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout,
            transport=transport,  # 测试注入 MockTransport，即可离线跑真实映射逻辑
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "OpenAICompatClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------------------------------------------------------------- 主入口

    def create(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._wire_messages(system, messages),
            "max_tokens": self.max_tokens,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if tools:
            payload["tools"] = self._wire_tools(tools)

        last_error: str = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._http.post("/chat/completions", json=payload)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self._backoff(attempt)
                continue

            if response.status_code in _RETRY_STATUS:
                last_error = f"HTTP {response.status_code}: {_clip(response.text)}"
                self._backoff(attempt, response.headers.get("retry-after"))
                continue

            if response.status_code >= 400:
                # 4xx 里除重试集合外都是确定性错误（模型名错、报文不合法），再试也没用
                raise LLMError(
                    f"端点拒绝请求 HTTP {response.status_code}: {_clip(response.text)}",
                    status=response.status_code,
                    attempts=attempt,
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise LLMError(
                    f"响应不是 JSON: {_clip(response.text)}", status=response.status_code
                ) from exc
            return self._normalize(data)

        raise LLMError(
            f"调用失败，已重试 {self.max_retries} 次。最后一次错误：{last_error}",
            attempts=self.max_retries,
        )

    # ------------------------------------------------------------ 报文映射

    def _wire_messages(self, system: str, messages: list[Message]) -> list[dict[str, Any]]:
        """Message 列表 -> OpenAI messages 数组。"""
        wire: list[dict[str, Any]] = []
        if system:
            wire.append({"role": "system", "content": system})

        # tool_call_id -> 工具名。部分端点要求 role=tool 必须带 name。
        names: dict[str, str] = {
            use.id: use.name for msg in messages for use in msg.tool_uses
        }

        for message in messages:
            if message.role is Role.ASSISTANT:
                entry: dict[str, Any] = {"role": "assistant", "content": message.text() or None}
                tool_calls = [
                    {
                        "id": use.id,
                        "type": "function",
                        "function": {
                            "name": use.name,
                            "arguments": _dump_args(use.input),
                        },
                    }
                    for use in message.tool_uses
                ]
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                wire.append(entry)
                continue

            # 一条 user Message 可能同时含文本和工具结果，必须拆成多条报文，
            # 且 role=tool 要紧跟在带 tool_calls 的 assistant 之后 —— 顺序即配对。
            text = message.text()
            if text:
                wire.append({"role": "user", "content": text})
            for result in message.results:
                tool_wire: dict[str, Any] = {
                    "role": "tool",
                    "tool_call_id": result.tool_use_id,
                    "content": result.content,
                }
                if names.get(result.tool_use_id):
                    tool_wire["name"] = names[result.tool_use_id]
                wire.append(tool_wire)

        return wire

    def _wire_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        """ToolSpec 列表 -> OpenAI tools 数组（外面要再包一层 function）。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.input_schema,
                },
            }
            for spec in tools
        ]

    def _normalize(self, data: dict[str, Any]) -> LLMResponse:
        """OpenAI 响应 -> LLMResponse。这是适配器唯一的出口。"""
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"响应缺少 choices：{_clip(json.dumps(data, ensure_ascii=False))}")

        choice = choices[0]
        message = choice.get("message") or {}

        blocks: list[Any] = []
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            blocks.append(TextBlock(content))

        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            blocks.append(
                ToolUseBlock(
                    id=call.get("id") or new_tool_use_id(),
                    name=function.get("name") or "",
                    input=_load_args(function.get("arguments")),
                )
            )

        stop_reason = _FINISH_REASON_MAP.get(str(choice.get("finish_reason") or "").lower())
        if stop_reason is None:
            # finish_reason 缺失或私有取值：按有没有工具调用来兜底判断，
            # 绝不能在这里抛异常 —— 那会让整轮对话白费。
            stop_reason = (
                StopReason.TOOL_USE
                if any(isinstance(b, ToolUseBlock) for b in blocks)
                else StopReason.END_TURN
            )

        usage = data.get("usage") or {}
        return LLMResponse(
            blocks=blocks,
            stop_reason=stop_reason,
            usage=Usage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
            ),
            raw=data,
        )

    # ---------------------------------------------------------------- 重试

    def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        """指数退避；端点给了 Retry-After 就听它的。"""
        if attempt >= self.max_retries:
            return
        try:
            delay = float(retry_after) if retry_after else min(2.0 ** (attempt - 1) * 0.5, 8.0)
        except ValueError:
            delay = 1.0
        self._sleep(max(delay, 0.0))


def _load_args(raw: Any) -> dict[str, Any]:
    """arguments 字符串 -> dict。非法 JSON 不抛错，转成模型能看懂的畸形输入。"""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {"__unparseable_arguments": str(raw)[:2000]}
    if isinstance(parsed, dict):
        return parsed
    return {"__unexpected_arguments_type": parsed}


def _dump_args(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False)


def _clip(text: str, limit: int = 800) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…(截断)"
