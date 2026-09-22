"""FakeLLM —— 把调试成本降一个数量级的关键件。

它按预设脚本依次返回响应，于是整个 Agent 循环可以在**不花一分钱、
不碰网络、完全确定性**的前提下被测试和复现。

脚本条目有两种：
  - LLMResponse：正常返回它。
  - Exception 实例：抛出来。用于测 LLM_FAILURE 分支，无需再造一个假客户端。

id 采用可预测的 `<prefix>_<序号>` 形式，这样断言"结果是否回填到了正确的
tool_use_id"时能直接写出期望值。
"""

from __future__ import annotations

import re
from typing import Any

from miniclaude.messages import (
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    ToolUseBlock,
    Usage,
)
from miniclaude.tools.base import ToolSpec


class ScriptExhausted(AssertionError):
    """脚本用完还想再要 —— 说明循环没有在该停的地方停下来。"""


class FakeLLM:
    """满足 llm.base.LLMClient 协议的脚本化假客户端。"""

    def __init__(self, responses: list[LLMResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []  # 每次请求的 system/messages/tools

    def create(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
    ) -> LLMResponse:
        """弹出下一条脚本响应；脚本耗尽则抛错，防止测试里出现静默死循环。"""
        self.calls.append({"system": system, "messages": list(messages), "tools": list(tools)})
        if not self.responses:
            raise ScriptExhausted(
                f"FakeLLM 脚本已用尽（被调用 {len(self.calls)} 次）。"
                "循环本该在更早的地方终止 —— 检查 stop_reason 与停滞检测。"
            )
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]

    def sent_texts(self) -> list[str]:
        """展平所有请求里的文本，供"某句话是否进了上下文"这类断言使用。"""
        out: list[str] = []
        for call in self.calls:
            out.append(call["system"])
            for message in call["messages"]:
                out.append(message.text())
                for block in message.results:
                    out.append(block.content)
        return out


class FakeSummarizer:
    """压缩阶梯 L2 专用的确定性摘要客户端 —— 必须有独立队列。

    摘要请求走的是同一个 `self.llm`。拿主剧本的 `FakeLLM` 去服务它，每次 L2 都会
    **偷吃掉一条剧本条目**：B2 首跑就是因此少写了 `part_06` 的汇总模块，题目判 fail，
    病根却在评测框架。真实端点按请求内容回答，没这个毛病 —— 所以这个坑只在 fake
    引擎下存在，也必须只在 fake 引擎下堵住，别把补丁漏到主剧本那条路上。

    它只做抽取式纪要（把请求里出现的文件名抄进正文），不假装会总结：B2 要验的是
    "压缩请求携带了这些文件名、摘要替换历史之后它们还在上下文里"，这两件事一个
    正则就够证明；真要验模型的总结质量，那是 live 臂的活。
    """

    def __init__(
        self,
        *,
        prompt_tokens: int = 1_800,
        completion_tokens: int = 150,
        always_empty: bool = False,
    ) -> None:
        """`always_empty=True` 冒充"付了费却什么也没说"（真实端点被输出上限截断的形状）。

        负向对照要用它：只说好话的假摘要器证不了"文件名来自摘要文本、开销照记"这两件事。
        """
        self.calls: list[dict[str, Any]] = []
        self.usage = Usage(prompt_tokens, completion_tokens)
        self.always_empty = always_empty

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def create(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
    ) -> LLMResponse:
        self.calls.append({"system": system, "messages": list(messages), "tools": list(tools)})
        transcript = messages[-1].text() if messages else ""
        names = sorted(set(re.findall(r"[\w./-]+\.(?:py|json|md|txt)", transcript)))
        if self.always_empty:
            text = ""
        else:
            named = "、".join(names) or "（请求里没出现文件名）"
            text = (
                "目标：把被压掉的历史写成还能接着干活的纪要。\n"
                f"已确认事实：这段历史涉及 {len(names)} 个文件：{named}。\n"
                "未验证的假设：无\n最后一次测试结论：无\n被否决的路径：无"
            )
        return scripted_final_text(text, usage=self.usage)


def scripted_tool_calls(
    names_and_args: list[tuple[str, dict[str, Any]]],
    *,
    text: str = "",
    id_prefix: str = "call",
    usage: Usage | None = None,
) -> LLMResponse:
    """便捷构造：一条"模型要求调用这些工具"的响应（可并行多调用）。"""
    blocks: list[Any] = []
    if text:
        blocks.append(TextBlock(text))
    for index, (name, args) in enumerate(names_and_args):
        blocks.append(ToolUseBlock(id=f"{id_prefix}_{index}", name=name, input=dict(args)))
    return LLMResponse(blocks=blocks, stop_reason=StopReason.TOOL_USE, usage=usage or Usage())


def scripted_final_text(text: str, *, usage: Usage | None = None) -> LLMResponse:
    """便捷构造：一条"任务完成，这是答复"的响应。"""
    return LLMResponse(
        blocks=[TextBlock(text)],
        stop_reason=StopReason.END_TURN,
        usage=usage or Usage(),
    )


def scripted_empty(*, stop_reason: StopReason = StopReason.END_TURN, usage: Usage | None = None) -> LLMResponse:
    """便捷构造：一条既无文本也无工具调用的废回复（测截断/空回复兜底）。"""
    return LLMResponse(blocks=[], stop_reason=stop_reason, usage=usage or Usage())


def scripted_truncated(text: str) -> LLMResponse:
    """便捷构造：一条因输出上限被截断的响应。"""
    return LLMResponse(blocks=[TextBlock(text)], stop_reason=StopReason.MAX_TOKENS, usage=Usage())
