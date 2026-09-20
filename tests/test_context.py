"""ContextManager 测试 —— 预算判断错了，要么白烧 token，要么该跑完的任务被提前掐掉。

重点是**校准口径必须和发送口径一致**（wire_chars），否则系数越校越偏：
这是本模块最容易写错、也最难从外部看出来的一处。
"""

from __future__ import annotations

from miniclaude.agent.context import ContextManager
from miniclaude.messages import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from miniclaude.tools.base import ToolSpec

SPEC = ToolSpec(name="read_file", description="读取文件内容", input_schema={"type": "object", "properties": {"path": {"type": "string"}}})


def cm(budget: int = 1000, cpt: float = 3.5) -> ContextManager:
    return ContextManager(budget=budget, chars_per_token=cpt)


def history() -> list[Message]:
    return [
        Message.user_text("读一下 a.py"),
        Message.assistant([TextBlock("我看一下"), ToolUseBlock(id="c1", name="read_file", input={"path": "a.py"})]),
        Message.tool_results([ToolResultBlock(tool_use_id="c1", content="1: print(1)", is_error=False)]),
    ]


# --------------------------------------------------------------- 估算口径


def test_wire_chars_covers_system_tools_and_every_block_type() -> None:
    manager = cm()
    without_tools = manager.wire_chars(system="s", tools=[], messages=history())
    with_tools = manager.wire_chars(system="s", tools=[SPEC], messages=history())
    longer_system = manager.wire_chars(system="s" * 10, tools=[SPEC], messages=history())

    assert with_tools > without_tools
    assert longer_system > with_tools
    # 工具声明真的很大（八个工具的 schema），漏算它会让压力永远显示很低
    assert with_tools - without_tools > len(SPEC.name) + len(SPEC.description)


def test_estimate_divides_by_current_ratio() -> None:
    manager = cm(budget=1000, cpt=4.0)
    args = {"system": "12345678", "tools": [], "messages": []}
    assert manager.wire_chars(**args) == 8
    assert manager.estimate(**args) == 2


def test_pressure_uses_budget() -> None:
    manager = cm(budget=100, cpt=1.0)
    assert manager.pressure(system="x" * 50, tools=[], messages=[]) == 0.5
    assert cm(budget=0).pressure(system="x" * 50) == 0.0  # 预算没配 ≠ 立刻爆炸


def test_warn_and_stop_thresholds() -> None:
    manager = cm(budget=100, cpt=1.0)

    def flags(chars: int) -> tuple[bool, bool]:
        args = {"system": "x" * chars, "tools": [], "messages": []}
        return manager.should_warn(**args), manager.should_stop(**args)

    assert flags(70) == (False, False)
    assert flags(80) == (True, False)     # 80% 先提醒，让人有机会收尾
    assert flags(94) == (True, False)
    assert flags(95) == (True, True)      # 95% 直接停，省一次必然被拒的请求


# --------------------------------------------------------------- 校准


def test_calibration_moves_toward_observed_ratio_by_ema() -> None:
    manager = cm(budget=1000, cpt=3.5)
    manager.calibrate(actual_prompt_tokens=1000, sent_chars=2000)  # 观测到 2.0 chars/token
    assert manager.chars_per_token == 3.5 * 0.7 + 2.0 * 0.3
    assert manager.last_actual_prompt_tokens == 1000


def test_calibration_converges_but_not_in_one_shot() -> None:
    """单次抖动（某轮恰好中英混排）不该把系数拉飞，所以要平滑。"""
    manager = cm(budget=1000, cpt=3.5)
    for _ in range(30):
        manager.calibrate(actual_prompt_tokens=1000, sent_chars=1500)
    assert abs(manager.chars_per_token - 1.5) < 0.01


def test_calibration_is_clamped() -> None:
    high = cm(budget=1000, cpt=11.9)
    high.calibrate(actual_prompt_tokens=1, sent_chars=1000)      # 观测 1000 chars/token
    assert high.chars_per_token == 12.0

    low = cm(budget=1000, cpt=1.3)
    low.calibrate(actual_prompt_tokens=1000, sent_chars=100)     # 观测 0.1
    assert low.chars_per_token == 1.2


def test_calibration_ignores_useless_samples() -> None:
    manager = cm(budget=1000, cpt=3.5)
    manager.calibrate(actual_prompt_tokens=0, sent_chars=5000)   # 端点没回 usage
    manager.calibrate(actual_prompt_tokens=500, sent_chars=0)
    manager.calibrate(actual_prompt_tokens=-1, sent_chars=100)
    assert manager.chars_per_token == 3.5
    assert manager.last_actual_prompt_tokens == 0


def test_calibration_makes_next_estimate_accurate() -> None:
    """校准的全部意义：中文为主的负载真实 token 比 3.5 高一大截，未校准会严重低估。"""
    manager = cm(budget=100_000, cpt=3.5)
    args = {"system": "系统提示" * 300, "tools": [SPEC], "messages": [Message.user_text("中文说明" * 500)]}

    chars = manager.wire_chars(**args)
    reality = chars / 1.8                              # 端点实测：这批内容约 1.8 字符/token
    assert manager.estimate(**args) < reality * 0.75   # 起手式低估 ~49%

    errors: list[float] = []
    for _ in range(20):
        manager.calibrate(actual_prompt_tokens=int(reality), sent_chars=chars)
        errors.append(abs(manager.estimate(**args) - reality) / reality)

    assert errors[0] < 0.45                            # 一轮就见效
    assert errors[-1] < 0.05                           # 十几轮后贴住真值
    assert errors == sorted(errors, reverse=True)      # 且误差单调下降，不会来回震荡


def test_snapshot_is_loggable() -> None:
    manager = cm()
    manager.calibrate(actual_prompt_tokens=300, sent_chars=900)
    snapshot = manager.snapshot()
    assert snapshot["budget"] == 1000
    assert snapshot["chars_per_token"] == round(manager.chars_per_token, 2)
    assert snapshot["last_actual_prompt_tokens"] == 300
