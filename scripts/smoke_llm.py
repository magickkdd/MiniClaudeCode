"""端点冒烟体检：用真实客户端跑通四种关键形态，验证供应商能力边界。

    python scripts/smoke_llm.py

它刻意只依赖 llm/ 与 messages.py，因此在 Agent 循环还不存在的阶段就能跑。
输出结论直接决定后续设计要不要为这个端点做特殊分支。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from miniclaude.config import Config, ConfigError  # noqa: E402
from miniclaude.llm.openai_compat import LLMError, OpenAICompatClient  # noqa: E402
from miniclaude.messages import (  # noqa: E402
    Message,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    new_tool_use_id,
)
from miniclaude.tools.base import ToolSpec  # noqa: E402

SYSTEM = "你是运行在终端里的软件工程助手。需要查看文件内容时必须调用工具，不要凭空编造。"

READ_FILE = ToolSpec(
    name="read_file",
    description="读取工作区内的文本文件，返回带行号的内容。修改文件前必须先读取。",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "limit": {"type": "integer", "description": "最多读取的行数"},
        },
        "required": ["path"],
    },
)

FAKE_FILE = "1\tdef main():\n2\t    run_agent()\n3\t\n4\tif __name__ == '__main__':\n5\t    main()"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail else ""))
    return ok


def timed(fn):
    start = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - start


def main() -> int:
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"配置错误：{exc}")
        return 2

    print(f"端点 {config.base_url}  模型 {config.model}  密钥 {config.redacted()['api_key']}")
    client = OpenAICompatClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        max_tokens=config.max_tokens,
        timeout=config.request_timeout,
    )
    all_ok = True

    print("\n[1] 纯文本对话（不带工具）")
    try:
        resp, elapsed = timed(
            lambda: client.create(system=SYSTEM, messages=[Message.user_text("只回复两个字：收到")], tools=[])
        )
        all_ok &= check("拿到回复", bool(resp.text_blocks()), f"{elapsed:.1f}s | {resp.text()[:80]!r}")
        print(f"         usage={resp.usage.prompt_tokens}+{resp.usage.completion_tokens} stop={resp.stop_reason.value}")
        all_ok &= check("返回了真实 usage（上下文估算要靠它校准）", resp.usage.total > 0)
    except LLMError as exc:
        check("请求成功", False, str(exc)[:300])
        return 1

    print("\n[2] 工具声明 + 期望模型主动调用")
    tool_use: ToolUseBlock | None = None
    try:
        resp, elapsed = timed(
            lambda: client.create(
                system=SYSTEM,
                messages=[Message.user_text("看一下 src/main.py 前 20 行写了什么。")],
                tools=[READ_FILE],
            )
        )
        uses = resp.tool_uses
        all_ok &= check("finish_reason 被正确翻译成 TOOL_USE", resp.stop_reason is StopReason.TOOL_USE,
                        f"{elapsed:.1f}s | 实际 {resp.stop_reason.value}")
        all_ok &= check("解析出 tool_calls", bool(uses), f"数量 {len(uses)}")
        if uses:
            tool_use = uses[0]
            print(f"         id={tool_use.id}  name={tool_use.name}  args={tool_use.input}")
            all_ok &= check(
                "arguments 已解析成 dict（而非字符串）",
                isinstance(tool_use.input, dict) and "__unparseable_arguments" not in tool_use.input,
            )
            all_ok &= check("工具名在声明范围内", tool_use.name == READ_FILE.name)
    except LLMError as exc:
        check("请求成功", False, str(exc)[:300])

    print("\n[3] 回填 tool 结果 -> 模型给出最终答复（决定多轮循环能否成立）")
    if tool_use is None:
        check("跳过：上一步未产生工具调用", False, "端点可能不支持 tools 字段，需改用 ReAct 文本协议")
        all_ok = False
    else:
        history = [
            Message.user_text("看一下 src/main.py 前 20 行写了什么。"),
            Message.assistant([tool_use]),
            Message.tool_results([ToolResultBlock(tool_use.id, FAKE_FILE)]),
        ]
        try:
            resp, elapsed = timed(
                lambda: client.create(system=SYSTEM, messages=history, tools=[READ_FILE])
            )
            text = resp.text()
            all_ok &= check("role=tool 报文被端点接受", resp.stop_reason is not StopReason.MAX_TOKENS, f"{elapsed:.1f}s")
            quoted = any(mark in text for mark in ("run_agent", "main", "5 行", "5行"))
            all_ok &= check("答复真的引用了回填内容（说明它读懂了）", quoted, text[:200])
        except LLMError as exc:
            check("请求成功", False, str(exc)[:300])

    print("\n[4] 一次请求多个工具（并行能力，第一阶段可以不支持）")
    try:
        resp, _ = timed(
            lambda: client.create(
                system=SYSTEM,
                messages=[Message.user_text("分别读 a.py 和 b.py 两个文件，要同时调用两次 read_file。")],
                tools=[READ_FILE],
            )
        )
        n = len(resp.tool_uses)
        print(f"  [INFO] 单轮返回 {n} 个 tool_calls —— "
              + ("支持并行；loop 里要按序执行并全部回填" if n > 1 else "不支持并行，模型会分多轮逐个调用"))
    except LLMError as exc:
        print(f"  [INFO] 该请求失败，不影响主流程：{str(exc)[:200]}")

    client.close()
    print("\n" + ("端点适配通过，可以进入步骤 2/3。" if all_ok else "存在阻塞项，先解决再往下走。"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
