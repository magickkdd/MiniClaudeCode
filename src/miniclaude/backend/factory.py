"""后端选择与**显式降级**（SPEC v2 §3.6 里 B6 的全部风险都集中在这十几行）。

`EXECUTION_BACKEND=docker` 而机器上没有 docker 时，正确做法既不是崩掉也不是悄悄用 local，
而是"用 local，并且把这件事写在终端、trace 和报表里"。为什么降级是允许的而静默不是：
沙箱是**加强项**，缺了它任务仍可判定；但一个没人知道的降级会让 B6 的一致性结论
指向一个根本没跑过的后端。所以这里返回的 `BackendChoice` 一定带着 `degraded` 原因串，
装配层爱怎么印就怎么印，trace 里必须有一条 `backend` 事件 —— 契约由
`tests/test_backend_factory.py` 和 trace 快照共同盯住。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from miniclaude.backend.checkpoints import Checkpointer, for_workspace
from miniclaude.backend.docker import DEFAULT_IMAGE, DockerBackend
from miniclaude.backend.local import LocalBackend
from miniclaude.backend.protocol import ExecutionBackend
from miniclaude.config import BACKEND_NAMES

# BACKEND_NAMES 的产地是 config（它要在启动时拒绝打错的名字），这里只是把它带进
# 执行层的命名空间，`from miniclaude.backend import BACKEND_NAMES` 才不用绕路。


@dataclass(frozen=True)
class BackendChoice:
    """一次装配的结论：用哪个后端、点名要的是哪个、为什么换了。"""

    backend: ExecutionBackend
    requested: str
    degraded: str = ""

    @property
    def name(self) -> str:
        return self.backend.name

    def note(self) -> str:
        """给人看的一行话。降级时这句话必须自带原因，否则终端上就只剩一个后端名。"""
        if self.degraded:
            return f"本次用 {self.backend.name} 后端（{self.requested} 不可用：{self.degraded}）"
        return f"本次用 {self.backend.name} 后端"

    def as_trace(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"requested": self.requested, "backend": self.backend.name}
        payload.update(self.backend.stats())
        payload["degraded"] = self.degraded or None
        payload["note"] = self.note()
        return payload


def select_backend(
    requested: str = "local",
    *,
    workspace_root: Path | str,
    memory_dir: Path | str,
    ignored_dirs: Sequence[str] = (),
    image: str = DEFAULT_IMAGE,
    network: str = "none",
    checkpoints: bool = True,
    docker_binary: str | None = None,
) -> BackendChoice:
    """构造后端。`checkpoints=False` 时后端仍可用，但 `snapshot()` 恒返回空 rev。"""
    name = (requested or "local").strip().lower()
    if name not in BACKEND_NAMES:
        # 打错的 EXECUTION_BACKEND 会静默变成 local —— 那正是本模块禁止的那件事，
        # 所以启动就失败，不留到报表里解释。
        raise ValueError(f"EXECUTION_BACKEND 只能是 {' / '.join(BACKEND_NAMES)}，当前值：{requested!r}")

    checkpointer = checkpointer_for(workspace_root, memory_dir, ignored_dirs) if checkpoints else None

    if name == "docker":
        docker = DockerBackend(
            workspace=Path(workspace_root),
            image=image,
            network=network,
            binary=docker_binary,
            checkpointer=checkpointer,
        )
        ok, reason = docker.available()
        if ok:
            return BackendChoice(backend=docker, requested=name)
        return BackendChoice(backend=LocalBackend(checkpointer=checkpointer), requested=name, degraded=reason)

    return BackendChoice(backend=LocalBackend(checkpointer=checkpointer), requested="local")


def checkpointer_for(
    workspace_root: Path | str, memory_dir: Path | str, ignored_dirs: Sequence[str] = ()
) -> Checkpointer:
    names = [str(item).strip("/") for item in ignored_dirs if str(item).strip("/")]
    return for_workspace(workspace_root, memory_dir, extra_excluded=names)
