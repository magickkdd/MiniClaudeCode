"""bash —— 终端执行。四条硬约束：超时强杀、输出截断、cwd 锁定、退出码回传。

缺任何一条都会在生产里踩坑：不超时则一次卡住的命令永不自愈；不截断则
一个 `cat` 大文件就撑爆上下文；不锁目录则模型能翻出整个 home。

**is_error 语义**（与 run_tests 一致，评测指标依赖这条）：
非零退出码表示"被观察的对象失败了"，工具本身成功交付了观察结果 → is_error=False。
只有超时、无法启动这类"工具没能给出观察"的情况才置 is_error=True。

S13 起本工具不再直接 `subprocess.run`：那件事交给 `ExecutionBackend`（SPEC v2 §3.6）。
留在这里的只有"模型意图层"的判断 —— 空命令、交互式命令、输出格式。
"""

from __future__ import annotations

import shutil
from typing import Any

from miniclaude.backend.local import LocalBackend
from miniclaude.backend.protocol import ExecutionBackend
from miniclaude.tools.base import BaseTool, RiskLevel, ToolResult

# 明显会挂住等输入的形态。宁可漏判，也不要误伤正常命令
_INTERACTIVE_HINTS: tuple[str, ...] = (
    "vim ", "vi ", "nano ", "less ", "more ", "tail -f", "top", "htop",
    "python -i", "python3 -i", "node -i", "ipython", "read -p", "pause",
)


class BashTool(BaseTool):
    name = "bash"
    description = (
        "在工作区根目录下执行一条 shell 命令，返回退出码与合并后的输出。"
        "用于运行脚本、查看项目状态、安装依赖、跑构建。"
        "跑测试请优先用 run_tests（它会把失败信息结构化）。"
        "不要用于任何需要交互输入的命令。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的完整命令"},
            "timeout": {"type": "integer", "description": "秒，默认 60，上限 300"},
        },
        "required": ["command"],
    }
    risk_level = RiskLevel.EXECUTE

    def __init__(
        self,
        workspace: Any,
        default_timeout: int = 60,
        backend: ExecutionBackend | None = None,
    ) -> None:
        super().__init__(workspace)
        self.default_timeout = default_timeout
        # 默认值在这里，不在注册表：`BashTool(ws)` 必须仍然能用（测试与脚本都这么写），
        # 而它要的语义就是"在我这台机器上跑"。
        self.backend: ExecutionBackend = backend or LocalBackend()

    def run(self, *, command: str, timeout: int | None = None) -> ToolResult:
        command = command.strip()
        if not command:
            return ToolResult.err("command 不能为空。")
        if self._looks_interactive(command):
            return ToolResult.err(
                f"命令 {command!r} 看起来需要交互式输入，会永久挂住。"
                "请改成非交互形式（例如 `python x.py < input.txt`，或直接读取文件而不是进 REPL）。"
            )

        try:
            limit = int(timeout) if timeout else self.default_timeout
        except (TypeError, ValueError):
            limit = self.default_timeout
        limit = max(1, min(limit, 300))

        result = self.backend.exec(command, cwd=self.ws.root, timeout=limit)
        if result.timed_out:
            return ToolResult.err(
                f"命令超时（{limit}s）被强制终止：{command!r}。"
                "请缩小执行范围，或把 timeout 调大；若它在等输入，改成非交互写法。"
            )
        if result.launch_error:
            return ToolResult.err(result.launch_error)

        body = result.output
        header = f"$ {command}\n退出码：{result.returncode}（{'成功' if result.returncode == 0 else '失败'}）"
        return ToolResult.ok(self.ws.truncate(f"{header}\n\n{body.strip()}"))

    @staticmethod
    def _looks_interactive(command: str) -> bool:
        lowered = f" {command.lower()} "
        if any(hint in lowered for hint in _INTERACTIVE_HINTS):
            return True
        # `python` / `node` 后面什么都不带 = 进入交互解释器
        return bool(shutil.which("bash") and command.strip() in {"python", "python3", "node"})
