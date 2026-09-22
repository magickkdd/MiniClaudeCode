"""ExecutionBackend —— "命令在哪儿跑"从工具实现里剥出来（SPEC v2 §3.6）。

三条约束都不是为了好看：

1. **工具只认 `exec()` 的返回，不认 subprocess。** B6 问的是"同一任务在 local 与 docker
   上判定是否一致"。只要 `bash.py` 里还留着一条 `if docker:` 分支，这句话就只能靠读代码
   相信，永远测不出来。
2. **不可用时明确降级，不静默、不崩。** `available()` 必须带回**原因**，装配层把它同时
   写进终端和 trace。静默换后端等于让 B6 的一致性判定失去意义 —— 报表上写着 docker、
   跑的是本机，那批数字就全是假的。
3. **快照与回滚是协议的一部分。** docker 里写的文件同样落在 bind mount 的宿主目录上，
   所以两个后端共用同一个宿主侧 `Checkpointer`：一致性由"同一份代码"保证，而不是靠两边
   各自把 git 写对。

`Command` 是 `str | Sequence[str]` 而不是 SPEC 草图里的单个 `str`，理由很具体：
`run_tests` 今天就是 argv 直跑、不过 shell（`run_tests.py`），而 `bash` 必须过 shell
（模型写的是 `mkdir -p a && pytest`）。传字符串给前者会改变 quoting 行为，
"LocalBackend = 现状行为一字不改"这条就破了。类型即意图：**字符串 = 交给 shell，序列 = 直接 execv**。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

Command = str | Sequence[str]

# 两个后端在没有检查点时必须给出同一句话：这是用户会拿来判断"/undo 为什么说不认识"的
# 文本，两处措辞不同就变成两个 bug。
NO_CHECKPOINT_REASON = "本次会话没有启用检查点（--no-checkpoints 或只读模式）"


# 三态必须分开：跑完了但退出码非 0 是一次**成功的观察**（工具层置 is_error=False，
# 见 bash.py 顶部说明），超时和"根本没跑起来"才是工具故障（is_error=True）。
@dataclass(frozen=True)
class BackendResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    launch_error: str = ""
    detail: str = ""  # 后端自己的话：docker 是那串命令行，local 是选中的 shell

    @property
    def ran(self) -> bool:
        """命令真的跑过，退出码可信。"""
        return not self.timed_out and not self.launch_error

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part) or "(无输出)"

    @classmethod
    def timeout(cls, *, detail: str = "") -> "BackendResult":
        return cls(returncode=-1, timed_out=True, detail=detail)

    @classmethod
    def failed_to_launch(cls, message: str, *, detail: str = "") -> "BackendResult":
        return cls(returncode=-1, launch_error=message, detail=detail)


@runtime_checkable
class ExecutionBackend(Protocol):
    """一次会话里"命令在哪跑"的全部可变性。实现者不许持有对话状态。"""

    name: str

    def available(self) -> tuple[bool, str]:
        """(能不能用, 原因)。原因要么为空，要么短到能直接印在终端第一行。"""
        ...

    def exec(self, command: Command, *, cwd: Path, timeout: int, env: dict | None = None) -> BackendResult:
        """执行一条命令。永不抛异常 —— 与 `BaseTool.invoke()` 同一铁律。"""
        ...

    def snapshot(self) -> tuple[str, str]:
        """把当前工作树记成一个可回退点，返回 (rev, 原因)。

        SPEC 草图写的是 `-> str`。as-built 多带一个原因串：记不下检查点时必须能说出
        为什么（没 git / 盘只读 / 索引被锁），否则 trace 里那条失败的 checkpoint 事件
        就是一句没有主语的抱怨，而 B6 的现场审计要的恰恰是"哪一次写入没被保护、为什么"。
        """
        ...

    def restore(self, rev: str) -> tuple[bool, str]:
        """回到某个 rev。返回 (是否成功, 给人看的原因)。回滚失败必须说得出为什么。"""
        ...

    def history(self) -> list[str]:
        """已知检查点，最新的在前。`/undo` 与报表靠它，不必自己再记一份。"""
        ...

    def stats(self) -> dict:
        """可序列化的自述：写进 trace 的 `backend` 事件与 `/backend` 的输出。"""
        ...
