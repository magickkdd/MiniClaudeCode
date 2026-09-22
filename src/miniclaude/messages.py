"""统一消息模型 —— 全项目的通用语言。

Agent 循环、LLM 适配器、工具层只认这里的类，绝不传递任何厂商的原始 JSON。
换模型供应商时，改动只发生在 llm/ 目录内。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Sequence


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


def dedupe_tool_use_ids(message: "Message", history: Sequence["Message"]) -> list[tuple[str, str]]:
    """就地改掉本条消息里与历史（或自身）撞车的 tool_use id，返回 (原名, 新名) 清单。

    端点并不保证 id 全局唯一：有些实现按响应内序号发 `call_0`/`call_1`，跨轮必然撞车。
    撞了以后坏的不是"这一轮有点乱"，而是三重连锁：历史过不了 `pairing_problems` 第 1 条
    → 下一轮请求被 400 拒；压缩阶梯的 `_guard_pairing` 会认为**原始**历史就是非法的，
    于是每次压缩都放弃 → 止损机制整体失效。一个命名习惯就能同时打死会话和它的保险丝，
    所以这件事在消息进历史之前就修，而不是留给下游兜底。

    新 id 用 `<原名>~<块下标>` 而不是 `new_tool_use_id()` 的 uuid：本项目的 trace 与
    eval 基线都要求同一脚本跑出同一份字节，uuid 会让每次复现都对不上。
    """
    taken = {block.id for past in history for block in past.tool_uses}
    renamed: list[tuple[str, str]] = []
    fresh: list[ContentBlock] = []
    for position, block in enumerate(message.content):
        if not isinstance(block, ToolUseBlock) or block.id not in taken:
            if isinstance(block, ToolUseBlock):
                taken.add(block.id)
            fresh.append(block)
            continue
        original = block.id
        candidate = f"{original}~{position}"
        seed = 0
        while candidate in taken:
            seed += 1
            candidate = f"{original}~{position}#{seed}"
        taken.add(candidate)
        renamed.append((original, candidate))
        fresh.append(replace(block, id=candidate))
    message.content = fresh
    return renamed


class PairingError(RuntimeError):
    """报文配对破损 —— 端点会直接 400，所以在发出去之前就拦下。"""


def pairing_problems(messages: Sequence["Message"]) -> list[str]:
    """列出全部配对违规，不抛异常。

    压缩/resume/子 agent 回填三条路径共用这一份判定：`assert_pairing` 用它拦人，
    `CompactReport.pairing_ok` 用它决定这次压缩要不要放弃。写成两处就会有两套口径。

    扫两趟是有原因的：先建"调用登记表"再看结果，才能把"结果跑到了调用前面"
    和"这条调用压根不存在"分开报 —— 单趟扫描会把前者误判成后者。
    """
    found: list[tuple[int, str]] = []
    # id -> (首次出现下标, 工具名)。留下标才能判断顺序错乱。
    declared: dict[str, tuple[int, str]] = {}
    answered: dict[str, int] = {}

    for index, message in enumerate(messages):
        for block in message.content:
            if not isinstance(block, ToolUseBlock):
                continue
            if block.id in declared:
                found.append((index, f"#{index} 工具调用 id {block.id} 重复声明"))
            else:
                # 连角色不合法的消息也照样登记：下一轮回填了它就该算答过，
                # 否则一条归属错误会连带刷出一屏"从未回填"，把真正的问题埋掉。
                declared[block.id] = (index, block.name)

    for index, message in enumerate(messages):
        blocks = message.content
        if message.role is Role.USER and message.tool_uses:
            found.append((index, f"#{index} user 消息里出现了工具调用请求"))
        if message.role is Role.ASSISTANT and message.results:
            found.append((index, f"#{index} assistant 消息里回填了工具结果"))
        # 结果块必须独占一条 user 消息：混进文本会让模型把叙述当成工具产出。
        if message.results and any(isinstance(block, TextBlock) for block in blocks):
            found.append((index, f"#{index} 工具结果与文本混在同一条消息里"))

        for block in blocks:
            if not isinstance(block, ToolResultBlock):
                continue
            origin = declared.get(block.tool_use_id)
            if origin is None:
                found.append((index, f"#{index} 结果 {block.tool_use_id} 找不到对应的工具调用"))
                continue
            if origin[0] > index:
                found.append((index, f"#{index} 结果 {block.tool_use_id} 出现在它的调用之前"))
            elif block.tool_use_id in answered:
                found.append((index, f"#{index} 调用 {block.tool_use_id} 被回填了两次"))
            # 顺序错的那次也算"有人应答了"：再补一条"从未回填"只会把病根埋进噪声里。
            answered.setdefault(block.tool_use_id, index)

    for tool_use_id, (index, name) in declared.items():
        if tool_use_id not in answered:
            found.append((index, f"#{index} {name} 的调用 {tool_use_id} 从未回填结果"))
    # 按消息下标排序（稳定）：读的人要看最早坏掉的那条，而不是发现顺序。
    return [text for _, text in sorted(found, key=lambda pair: pair[0])]


def assert_pairing(messages: Sequence["Message"]) -> None:
    """三条规则的守门函数：违反任何一条，端点会直接 400。

    1. 每个 `ToolUseBlock.id` 必须在**其后**恰好一条工具结果里出现一次；
    2. 每个 `ToolResultBlock.tool_use_id` 必须能回溯到一个已存在的调用；
    3. 工具结果块只出现在 user 消息里，且该消息不得混入 `TextBlock`。
    """
    problems = pairing_problems(messages)
    if problems:
        shown = "；".join(problems[:6])
        more = f"（共 {len(problems)} 处）" if len(problems) > 6 else ""
        raise PairingError(f"消息配对不合法：{shown}{more}")

