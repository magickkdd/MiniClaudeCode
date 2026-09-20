"""适配器测试 —— 用 httpx.MockTransport 把网络换掉，映射逻辑本身全跑真的。

这里盯的是最容易在换端点时炸掉的四件事：
role=tool 的配对与顺序、arguments 是字符串、finish_reason 缺失兜底、
重试只发生在可重试的错误上。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from miniclaude.llm.base import LLMClient
from miniclaude.llm.openai_compat import LLMError, OpenAICompatClient
from miniclaude.messages import (
    Message,
    Role,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from miniclaude.tools.base import ToolSpec
from miniclaude.tools.read_file import ReadFileTool
from miniclaude.tools.workspace import Workspace

BASE = "https://mock.local/v1"


def make_client(
    handler: Any,
    *,
    max_retries: int = 3,
    sleep: Any = None,
) -> tuple[OpenAICompatClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def counting_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        outcome = handler(request)
        if isinstance(outcome, Exception):
            raise outcome  # 让"抛连接错误"这种脚本写法直接可用
        return outcome

    client = OpenAICompatClient(
        base_url=BASE,
        api_key="sk-test-abcdef0123456789",
        model="mock-model",
        max_retries=max_retries,
        transport=httpx.MockTransport(counting_handler),
        sleep=sleep or (lambda seconds: None),
    )
    return client, requests


def body(text: str, *, finish: str | None = "stop", usage: dict[str, int] | None = None) -> httpx.Response:
    payload: dict[str, Any] = {
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish}]
    }
    if usage:
        payload["usage"] = usage
    return httpx.Response(200, json=payload)


def sent_payload(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode("utf-8"))


# --------------------------------------------------------------- 请求映射


def test_request_carries_model_system_and_tools() -> None:
    client, requests = make_client(lambda request: body("你好"))
    client.create(system="你是 Agent。", messages=[Message.user_text("读文件")], tools=[])

    payload = sent_payload(requests[0])
    assert payload["model"] == "mock-model"
    assert payload["max_tokens"] == 4096
    assert payload["messages"][0] == {"role": "system", "content": "你是 Agent。"}
    assert payload["messages"][1] == {"role": "user", "content": "读文件"}
    assert "tools" not in payload  # 空工具列表不要发 []，部分端点会当成函数调用被禁用
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer sk-test-abcdef0123456789"


def test_temperature_omitted_when_unset() -> None:
    client, requests = make_client(lambda request: body("ok"))
    client.create(system="s", messages=[Message.user_text("u")], tools=[])
    assert "temperature" not in sent_payload(requests[0])

    client2, requests2 = make_client(lambda request: body("ok"), )
    client2.temperature = 0.2
    client2.create(system="s", messages=[Message.user_text("u")], tools=[])
    assert sent_payload(requests2[0])["temperature"] == 0.2


def test_tool_result_pairing_order_and_name() -> None:
    """漏掉 tool_calls 或把 role=tool 的顺序弄乱，端点会直接 400。"""
    history = [
        Message.user_text("读 a.py"),
        Message.assistant([TextBlock("我看一下"), ToolUseBlock(id="call_1", name="read_file", input={"path": "a.py"})]),
        Message.tool_results([ToolResultBlock(tool_use_id="call_1", content="1: print(1)", is_error=False)]),
    ]
    client, requests = make_client(lambda request: body("读完了"))
    client.create(system="s", messages=history, tools=[])

    wire = sent_payload(requests[0])["messages"]
    assert [entry["role"] for entry in wire] == ["system", "user", "assistant", "tool"]
    assistant = wire[2]
    assert assistant["content"] == "我看一下"
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert assistant["tool_calls"][0]["function"]["name"] == "read_file"
    # arguments 必须是**字符串**，不是对象 —— 这是 OpenAI 报文的规定
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"path": "a.py"}'
    assert wire[3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "1: print(1)",
        "name": "read_file",
    }


def test_assistant_without_text_sends_null_not_empty_string() -> None:
    """有些端点会因为 content="" 判定"空回复"，必须是 null。"""
    history = [Message.assistant([ToolUseBlock(id="c1", name="bash", input={"command": "ls"})])]
    client, requests = make_client(lambda request: body("ok"))
    client.create(system="s", messages=history, tools=[])
    wire = sent_payload(requests[0])["messages"]
    assert wire[1]["content"] is None


def test_tool_wire_shape() -> None:
    specs = [
        ToolSpec(name="read_file", description="读文件", input_schema={"type": "object", "properties": {}}),
        ToolSpec(name="bash", description="跑命令", input_schema={"type": "object"}),
    ]
    client, requests = make_client(lambda request: body("ok"))
    client.create(system="s", messages=[Message.user_text("u")], tools=specs)

    tools = sent_payload(requests[0])["tools"]
    assert tools[0] == {
        "type": "function",
        "function": {"name": "read_file", "description": "读文件", "parameters": {"type": "object", "properties": {}}},
    }
    assert [tool["function"]["name"] for tool in tools] == ["read_file", "bash"]


# --------------------------------------------------------------- 响应归一化


def test_normalize_text_and_usage() -> None:
    client, _ = make_client(
        lambda request: body("  这是答复  ", finish="stop", usage={"prompt_tokens": 120, "completion_tokens": 8})
    )
    response = client.create(system="s", messages=[Message.user_text("u")], tools=[])

    assert response.stop_reason is StopReason.END_TURN
    assert response.text() == "  这是答复  "
    assert response.usage.prompt_tokens == 120
    assert response.usage.total == 128
    assert response.raw["choices"][0]["finish_reason"] == "stop"  # raw 只给 trace 用


def test_normalize_tool_calls_parses_arguments_string() -> None:
    handler = lambda request: httpx.Response(  # noqa: E731
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": "我先读两个文件",
                        "tool_calls": [
                            {"id": "a", "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}},
                            {"id": "b", "function": {"name": "read_file", "arguments": '{"path":"b.py"  \n}'}},
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    )
    client, _ = make_client(handler)
    response = client.create(system="s", messages=[Message.user_text("u")], tools=[])

    assert response.stop_reason is StopReason.TOOL_USE
    assert [block.text for block in response.text_blocks()] == ["我先读两个文件"]
    calls = response.tool_uses
    assert [(call.id, call.name, call.input) for call in calls] == [
        ("a", "read_file", {"path": "a.py"}),
        ("b", "read_file", {"path": "b.py"}),   # 带换行的合法 JSON 也要能吞下
    ]


def test_missing_ids_and_finish_reason_still_normalize() -> None:
    """兼容端点质量参差：缺字段是常态，这里绝不能抛 KeyError。"""
    client, _ = make_client(
        lambda request: httpx.Response(
            200,
            json={"choices": [{"message": {"tool_calls": [{"function": {"name": "bash", "arguments": ""}}]}}]},
        )
    )
    response = client.create(system="s", messages=[Message.user_text("u")], tools=[])

    assert response.stop_reason is StopReason.TOOL_USE      # 由"有 tool_calls"反推
    assert response.tool_uses[0].id.startswith("call_")      # 自补 id，否则无法回填
    assert response.tool_uses[0].input == {}


def test_end_turn_without_content_normalizes_to_empty_blocks() -> None:
    client, _ = make_client(lambda request: body("   "))
    response = client.create(system="s", messages=[Message.user_text("u")], tools=[])
    assert response.blocks == []
    assert response.stop_reason is StopReason.END_TURN


def test_unparseable_arguments_become_teaching_tool_error(tmp_path: Any) -> None:
    """非法 JSON 不能崩在 json.loads：要变成模型看得懂、能自愈的失败描述。"""
    client, _ = make_client(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"tool_calls": [{"id": "x", "function": {"name": "read_file", "arguments": '{"path": '}}]},
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )
    )
    response = client.create(system="s", messages=[Message.user_text("u")], tools=[])
    call = response.tool_uses[0]
    assert call.input == {"__unparseable_arguments": '{"path": '}

    result = ReadFileTool(Workspace(tmp_path)).invoke(
        ToolUseBlock(id=call.id, name=call.name, input=call.input)
    )
    assert result.is_error is True
    assert "不是合法 JSON" in result.content


def test_non_object_arguments_flagged() -> None:
    client, _ = make_client(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"tool_calls": [{"id": "x", "function": {"name": "bash", "arguments": "[1,2]"}}]},
                     "finish_reason": "tool_calls"}
                ]
            },
        )
    )
    response = client.create(system="s", messages=[Message.user_text("u")], tools=[])
    assert response.tool_uses[0].input == {"__unexpected_arguments_type": [1, 2]}


# --------------------------------------------------------------- 重试与失败


def test_retries_retryable_status_then_succeeds() -> None:
    responses = [
        httpx.Response(429, json={"error": "rate limited"}, headers={"retry-after": "2"}),
        httpx.Response(503, text="gateway down"),
        body("终于成功"),
    ]
    delays: list[float] = []
    client, requests = make_client(
        lambda request: responses.pop(0), sleep=delays.append
    )
    response = client.create(system="s", messages=[Message.user_text("u")], tools=[])

    assert response.text() == "终于成功"
    assert len(requests) == 3
    assert delays == [2.0, 1.0]  # 第一次听 Retry-After，第二次走指数退避 0.5→1.0


def test_transport_errors_are_retried() -> None:
    responses: list[Any] = [httpx.ConnectError("boom"), body("恢复了")]
    client, requests = make_client(lambda request: responses.pop(0))
    response = client.create(system="s", messages=[Message.user_text("u")], tools=[])

    assert len(requests) == 2
    assert response.text() == "恢复了"


def test_deterministic_4xx_raises_without_retrying() -> None:
    """400 是报文本身有问题，重试只是烧钱。"""
    client, requests = make_client(
        lambda request: httpx.Response(400, json={"error": {"message": "model not found"}})
    )
    with pytest.raises(LLMError) as excinfo:
        client.create(system="s", messages=[Message.user_text("u")], tools=[])

    assert len(requests) == 1
    assert excinfo.value.status == 400
    assert "model not found" in str(excinfo.value)


def test_retries_exhausted_reports_attempts() -> None:
    client, requests = make_client(lambda request: httpx.Response(500, text="server exploded"), max_retries=3)
    with pytest.raises(LLMError) as excinfo:
        client.create(system="s", messages=[Message.user_text("u")], tools=[])

    assert len(requests) == 3
    assert excinfo.value.attempts == 3
    assert "已重试 3 次" in str(excinfo.value)
    assert "500" in str(excinfo.value)


def test_non_json_success_body_raises_llmerror() -> None:
    client, _ = make_client(lambda request: httpx.Response(200, text="<html>代理网关的登录页</html>"))
    with pytest.raises(LLMError) as excinfo:
        client.create(system="s", messages=[Message.user_text("u")], tools=[])
    assert "不是 JSON" in str(excinfo.value)


def test_empty_choices_raises() -> None:
    client, _ = make_client(lambda request: httpx.Response(200, json={"choices": []}))
    with pytest.raises(LLMError) as excinfo:
        client.create(system="s", messages=[Message.user_text("u")], tools=[])
    assert "choices" in str(excinfo.value)


# --------------------------------------------------------------- 接口契约


def test_client_satisfies_llm_protocol() -> None:
    client, _ = make_client(lambda request: body("ok"))
    assert isinstance(client, LLMClient)


def test_history_roles_survive_round_trip() -> None:
    """一轮工具往返后，历史里角色序列必须是 user/assistant/user，不能出现连续 assistant。"""
    client, requests = make_client(lambda request: body("ok"))
    history = [
        Message.user_text("任务"),
        Message.assistant([ToolUseBlock(id="1", name="read_file", input={"path": "a"})]),
        Message.tool_results([ToolResultBlock(tool_use_id="1", content="内容", is_error=True)]),
        Message.assistant([TextBlock("答案")]),
        Message.user_text("再来"),
    ]
    client.create(system="s", messages=history, tools=[])
    wire = sent_payload(requests[0])["messages"][1:]
    assert [(entry["role"], entry.get("content")) for entry in wire] == [
        ("user", "任务"),
        ("assistant", None),
        ("tool", "内容"),
        ("assistant", "答案"),
        ("user", "再来"),
    ]
    assert wire[2]["tool_call_id"] == "1"
    assert wire[2]["name"] == "read_file"
    assert [m.role for m in history] == [Role.USER, Role.ASSISTANT, Role.USER, Role.ASSISTANT, Role.USER]
