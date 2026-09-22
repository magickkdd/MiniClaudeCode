"""DockerBackend —— 把命令搬进容器跑（SPEC v2 §3.6）。

这台开发机上没有 docker，所以本文件的**真实容器路径没有被运行过**。这不是不写它的理由
（协议要落地就得有两个实现，否则"抽象"只是 LocalBackend 的一个别名），但也不是可以
含糊的地方：能离线证明的是"我们构造的 docker 命令行、降级判定、rev 共用"这三件事
（`tests/test_docker_backend.py` 用一个替身 `docker` 可执行文件把它们全钉住），
不能证明的是"镜像里真的跑得起 pytest"。这条边界在 SPEC §3.6 的 as-built 里同样写着。

三处刻意的不照抄：

1. **不把宿主环境变量带进容器。** LocalBackend 必须继承 `os.environ`（否则找不到 python），
   Docker 侧只透传调用方显式给的键 —— 沙箱的一个实际收益就是 `.env` 里的密钥不会出现在
   被评测进程的 `/proc/self/environ` 里。
2. **宿主解释器路径不做成容器内的路径。** `run_tests` 传的是 `sys.executable`，那是
   `D:\\...\\python.exe`，容器里没有这个文件。`_translate()` 把它换成镜像里的 `python3`，
   并且**只认第一个参数**——剩下的参数是测试路径，走 bind mount 的相对路径。
3. **超时不只是杀 CLI。** `docker run` 被杀不代表容器停了，所以要一个 `--name`，
   超时后尽力 `docker rm -f` 兜掉残留。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Sequence

from miniclaude.backend.checkpoints import Checkpointer, CheckpointStats
from miniclaude.backend.protocol import NO_CHECKPOINT_REASON, BackendResult, Command

DEFAULT_IMAGE = "python:3.12-slim"
CONTAINER_WORKDIR = "/work"
CONTAINER_PYTHON = "python3"
DOCKER_TIMEOUT_GRACE = 10  # 给 --rm 一点收尾时间，否则每次都留一个僵尸容器


class DockerBackend:
    """`docker run --rm` 一次一条命令：不驻留容器，状态只通过挂载的工作区传递。

    代价说清楚：每条命令都要付一次容器启动（真 docker 上是几百毫秒量级），
    换来的是"模型跑 `rm -rf` 也炸不到宿主"。本项目里命令本身只占墙钟的 6~10%，
    这笔交换在延迟上付得起，在评测语义上值不值要 B6 自己回答。
    """

    name = "docker"

    def __init__(
        self,
        *,
        workspace: Path,
        image: str = DEFAULT_IMAGE,
        network: str = "none",
        binary: str | None = None,
        checkpointer: Checkpointer | None = None,
        probe_timeout: int = 15,
    ) -> None:
        self.workspace = Path(workspace)
        self.image = image or DEFAULT_IMAGE
        self.network = network
        self._binary = binary
        self.checkpointer = checkpointer
        self.probe_timeout = probe_timeout
        self._verdict: tuple[bool, str] | None = None
        self.launches = 0
        self.errors = 0

    # ------------------------------------------------------------ 可用性

    @property
    def binary(self) -> str:
        return self._binary or shutil.which("docker") or ""

    def available(self) -> tuple[bool, str]:
        """探测一次就缓存：每条命令前问一遍 docker 会把延迟花在无关的地方。"""
        if self._verdict is None:
            self._verdict = self._probe()
        return self._verdict

    def _probe(self) -> tuple[bool, str]:
        exe = self.binary
        if not exe:
            return False, "PATH 里没有 docker 可执行文件"
        try:
            completed = subprocess.run(  # noqa: S603
                [exe, "version", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.probe_timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, f"docker version 跑不动：{type(exc).__name__}: {exc}"
        if completed.returncode != 0:
            # 最常见的是 Docker Desktop 没启动。原因原样带出去，不要写成"docker 不可用"——
            # 那会让用户去装已经装好的东西。
            detail = " ".join((completed.stderr or completed.stdout or "").split())[:200]
            return False, f"docker CLI 在，但守护进程没应答：{detail or '退出码 ' + str(completed.returncode)}"
        return True, f"docker server {(completed.stdout or '').strip()[:40]} · 镜像 {self.image} · 网络 {self.network}"

    # ------------------------------------------------------------ 执行

    def exec(self, command: Command, *, cwd: Path, timeout: int, env: dict | None = None) -> BackendResult:
        exe = self.binary
        if not exe:
            self.errors += 1
            return BackendResult.failed_to_launch("PATH 里没有 docker 可执行文件")

        argv, name = self._build(exe, command, cwd=cwd, env=env)
        try:
            completed = subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=int(timeout) + DOCKER_TIMEOUT_GRACE,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._reap(exe, name)
            return BackendResult.timeout(detail=f"容器 {name} 已请求清理")
        except OSError as exc:
            self.errors += 1
            return BackendResult.failed_to_launch(f"无法启动 docker：{type(exc).__name__}: {exc}")
        self.launches += 1
        return BackendResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            detail=f"docker run --name {name} --network {self.network} -v {self.workspace}:{CONTAINER_WORKDIR}",
        )

    def _build(
        self, exe: str, command: Command, *, cwd: Path, env: dict | None
    ) -> tuple[list[str], str]:
        name = f"mcc-{uuid.uuid4().hex[:12]}"
        argv = [
            exe,
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            self.network or "none",
            "-v",
            f"{self.workspace}:{CONTAINER_WORKDIR}",
            "-w",
            CONTAINER_WORKDIR,
        ]
        for key, value in (env or {}).items():
            argv += ["-e", f"{key}={value}"]
        inner, entrypoint = self._translate(command)
        if entrypoint:
            argv += ["--entrypoint", entrypoint]
        argv.append(self.image)
        argv += inner
        return argv, name

    def _translate(self, command: Command) -> tuple[list[str], str]:
        """(容器里的命令参数, --entrypoint)。字符串走 shell，序列走 execv。"""
        if isinstance(command, str):
            return ["bash", "-c", command], ""
        argv = [str(item) for item in command]
        if not argv:
            return ["true"], ""
        if _is_host_python(argv[0]):
            # 宿主解释器在镜像里不存在：换成镜像自带的 python3，参数原样保留。
            return argv[1:], CONTAINER_PYTHON
        return argv, ""

    def _reap(self, exe: str, name: str) -> None:
        """超时后尽力收掉容器。失败也不报 —— 主流程的结论已经定了，这里是打扫。"""
        try:
            subprocess.run(  # noqa: S603
                [exe, "rm", "-f", "--time", "1", name],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            self.errors += 1

    # ------------------------------------------------------------ 检查点：与 local 共用宿主侧影子 git

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
        ok, reason = self.available()
        return {
            "backend": self.name,
            "isolated": True,
            "image": self.image,
            "network": self.network or "none",
            "available": ok,
            "availability_reason": reason,
            "launches": self.launches,
            "errors": self.errors,
            "checkpoints": (
                self.checkpointer.stats.as_trace() if self.checkpointer else CheckpointStats().as_trace()
            ),
        }


def _is_host_python(raw: str) -> bool:
    """是不是"当前这台机器上的这个解释器"。只认 sys.executable 与它的 basename。

    刻意不用 `raw.endswith("python.exe")` 放宽：模型写的 `bash` 命令里出现
    `./venv/bin/python` 时，那是它自己指定的解释器，替它换掉就是篡改意图。
    """
    candidate = Path(str(raw)).name.lower()
    return str(raw) == sys.executable or candidate == Path(sys.executable).name.lower()
