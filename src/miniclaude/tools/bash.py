"""bash —— 终端执行。四条硬约束：超时强杀、输出截断、cwd 锁定、退出码回传。

缺任何一条都会在生产里踩坑：不超时则一次卡住的命令永不自愈；不截断则
一个 `cat` 大文件就撑爆上下文；不锁目录则模型能翻出整个 home。

**is_error 语义**（与 run_tests 一致，评测指标依赖这条）：
非零退出码表示"被观察的对象失败了"，工具本身成功交付了观察结果 → is_error=False。
只有超时、无法启动这类"工具没能给出观察"的情况才置 is_error=True。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

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

    def __init__(self, workspace: Any, default_timeout: int = 60) -> None:
        super().__init__(workspace)
        self.default_timeout = default_timeout

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

        argv, shell, executable = self._build(command)
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        try:
            completed = subprocess.run(  # noqa: S603 - 命令由用户授权的 Agent 发起
                argv,
                shell=shell,
                executable=executable,
                cwd=str(self.ws.root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=limit,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.err(
                f"命令超时（{limit}s）被强制终止：{command!r}。"
                "请缩小执行范围，或把 timeout 调大；若它在等输入，改成非交互写法。"
            )
        except OSError as exc:
            return ToolResult.err(f"无法启动命令：{type(exc).__name__}: {exc}")

        body = "\n".join(part for part in (completed.stdout, completed.stderr) if part) or "(无输出)"
        header = f"$ {command}\n退出码：{completed.returncode}（{'成功' if completed.returncode == 0 else '失败'}）"
        return ToolResult.ok(self.ws.truncate(f"{header}\n\n{body.strip()}"))

    def _build(self, command: str) -> tuple[Any, bool, str | None]:
        """Windows 上优先走 Git Bash：cmd.exe 太弱，模型写的 `mkdir -p`、管道会直接失败。"""
        bash = shutil.which("bash")
        if bash:
            return [bash, "-c", command], False, None
        return command, True, None

    @staticmethod
    def _looks_interactive(command: str) -> bool:
        lowered = f" {command.lower()} "
        if any(hint in lowered for hint in _INTERACTIVE_HINTS):
            return True
        # `python` / `node` 后面什么都不带 = 进入交互解释器
        return bool(shutil.which("bash") and command.strip() in {"python", "python3", "node"})
