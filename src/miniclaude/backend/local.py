"""LocalBackend —— 本机执行，也就是 v1 到现在的行为（SPEC v2 §3.6）。

这个类的存在意义是"现状有了一个名字"：`exec()` 里的 subprocess 调用、Git Bash 优先的
shell 选择、超时强杀、编码兜底，全部是从 `bash.py` / `run_tests.py` 原样搬进来的。
搬动本身不改语义，这样 B6 的对照才有意义 —— 如果换后端顺手改了三五处行为，
"两后端判定一致"就成了空话。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence

from miniclaude.backend.checkpoints import Checkpointer, CheckpointStats
from miniclaude.backend.protocol import NO_CHECKPOINT_REASON, BackendResult, Command


def merge_env(extra: dict[str, Any] | None) -> dict[str, str]:
    """所有后端共用的环境基线：子进程必须行缓冲、输出必须是 UTF-8。

    没有 PYTHONIOENCODING 时，Windows 上的 pytest 会往 stderr 吐 GBK 字节，
    我们按 utf-8 解码就得到一串乱码 —— 模型看到的失败原因因此是假的。
    """
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    for key, value in (extra or {}).items():
        env[str(key)] = str(value)
    return env


class LocalBackend:
    """在工作区目录里直接跑命令。没有隔离，但也不要求机器上有 docker。"""

    name = "local"

    def __init__(self, *, checkpointer: Checkpointer | None = None) -> None:
        self.checkpointer = checkpointer

    def available(self) -> tuple[bool, str]:
        return True, "本机执行，无隔离"

    def shell_note(self) -> str:
        return f"bash={shutil.which('bash') or '无（退回 shell=True）'}"

    def exec(self, command: Command, *, cwd: Path, timeout: int, env: dict | None = None) -> BackendResult:
        argv, shell, executable = self._build(command)
        try:
            completed = subprocess.run(  # noqa: S603 - 命令由用户授权的 Agent 发起
                argv,
                shell=shell,
                executable=executable,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1, int(timeout)),
                env=merge_env(env),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return BackendResult.timeout(detail=self.shell_note())
        except OSError as exc:
            return BackendResult.failed_to_launch(f"无法启动命令：{type(exc).__name__}: {exc}")
        return BackendResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            detail=self.shell_note(),
        )

    # ------------------------------------------------------------ 检查点

    def snapshot(self) -> tuple[str, str]:
        if self.checkpointer is None:
            return "", NO_CHECKPOINT_REASON
        return self.checkpointer.snapshot()

    def restore(self, rev: str) -> tuple[bool, str]:
        if self.checkpointer is None:
            return False, NO_CHECKPOINT_REASON
        return self.checkpointer.restore(rev)

    def history(self) -> list[str]:
        return self.checkpointer.history() if self.checkpointer is not None else []

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "isolated": False,
            "shell": shutil.which("bash") or "shell=True",
            # 没有 checkpointer 时也交回同一份字段：报表和测试不该为"这一层没装配"
            # 准备第二种取值形状。
            "checkpoints": (
                self.checkpointer.stats.as_trace() if self.checkpointer else CheckpointStats().as_trace()
            ),
        }

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _build(command: Command) -> tuple[Any, bool, str | None]:
        """Windows 上优先走 Git Bash：cmd.exe 太弱，模型写的 `mkdir -p`、管道会直接失败。

        序列不过 shell —— `run_tests` 现状就是 argv 直跑，这里改了就是一处行为漂移。
        """
        if isinstance(command, str):
            bash = shutil.which("bash")
            if bash:
                return [bash, "-c", command], False, None
            return command, True, None
        argv: Sequence[str] = [str(item) for item in command]
        return list(argv), False, None
