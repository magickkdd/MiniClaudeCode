"""检查点 —— 一个独立索引的 git 仓库，绝不 `init` 用户的工作区（SPEC v2 §3.6）。

为什么是 git 而不是自己拷贝文件树：回滚要正确，就得处理"改过的、新增的、删掉的"三类
文件，自己写这个逻辑必然漏第三类。git 的索引天生就是这件事的实现，而且它给了我们要的
副产品 —— 一条 append-only 的状态历史，`/undo` 因此不需要我们再维护第二份事实。

三条硬约束：

1. **`--git-dir` 指向 `<工作区>/<记忆目录>/snapshots`，`--work-tree` 指向工作区。**
   绝不对用户工作区执行 `git init`：那会在别人的仓库里留下 `.git` 冲突，
   而 `.git` 冲突是不可逆的（它会让用户自己的 git 从此报错）。
2. **`info/exclude` 里写死 `.git/`、记忆目录与 `IGNORED_DIRS`。** 排除只作用于未跟踪
   文件，而 `.git/` 永远不会被跟踪，所以恢复时的 `read-tree -u` 不会去碰它 ——
   这条链条是"用户仓库安全"的全部依据，`tests/test_checkpoints.py` 直接盯着它。
3. **恢复用 `read-tree --reset -u`，不用 `checkout <rev> -- .`。** 实测差别在删除：
   `checkout` 只覆盖它在目标树里看得见的文件，检查点之后新建的文件会留在原地；
   `read-tree -u` 会把索引里存在、目标树里没有的条目从工作树删掉，这才叫回到那个状态。
   HEAD 不动，所以检查点历史是一条只增的线。

一切都可能失败（没有 git、只读盘、磁盘满），失败一律降级成 `(False, 原因)`：
一次记不下检查点不该让写文件的工具报错 —— 那等于让度量层有能力否决任务。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

SNAPSHOTS_DIRNAME = "snapshots"
GIT_TIMEOUT = 120
MAX_REASON_CHARS = 300

# 提交者身份与换行策略写死：不能依赖用户的全局 git config（CI 与裸机器上它常常是缺的，
# 缺了就是 exit 128，而"记不下检查点"这件事必须和"这台机器没配 git"解耦）。
# core.autocrlf=false 不是洁癖：Windows 上装了 Git 的机器默认 autocrlf=true（这台开发机上
# `git config core.autocrlf` 实测就是 true），那样 `read-tree -u` 会把存进去的 LF 换成
# CRLF 再写回工作树 —— /undo 承诺"回到那一刻的字节"，结果却把整个仓库的换行重排一遍，
# diff 里全是噪音。实测探针里那句 "LF will be replaced by CRLF" 就是这件事的现场证据。
_SAFE_OPTS = (
    "-c", "user.name=mcc",
    "-c", "user.email=mcc@local",
    "-c", "commit.gpgsign=false",
    "-c", "core.autocrlf=false",
)


def _clean(reason: str) -> str:
    text = " ".join(str(reason).split())
    return text if len(text) <= MAX_REASON_CHARS else text[:MAX_REASON_CHARS] + "…"


@dataclass
class CheckpointStats:
    """检查点这一层的自述。`/backend`、trace 与 B6 证据读的都是它。

    `enabled` 与 `ready` 是两件事，分开记才不会骗人：前者是"这一层被装配进来了吗"
    （构造出来就是 yes，只读模式下根本不会构造），后者是"影子仓库真的建起来了吗"。
    仓库是懒建的，所以组装期那条 `backend` trace 事件必然 ready=False —— 那不是你
    想看到的"检查点没开"。
    """

    enabled: bool = False
    ready: bool = False      # 影子仓库已就位（首次用时才成立）
    git_dir: str = ""
    snapshots: int = 0       # 成功提交的 rev 数
    restores: int = 0        # 成功回滚的次数
    failures: int = 0        # 记不下 / 回不了的次数
    degraded: str = ""       # 非空 = 这次为什么没记成，必须能报出来
    last_rev: str = ""
    excluded: list[str] = field(default_factory=list)

    def as_trace(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            # git_dir 进 trace 是为了 B6 的现场审计：要确认"影子仓库没有落在用户仓库里"，
            # 读这一条比读代码可信。
            "git_dir": self.git_dir or None,
            "snapshots": self.snapshots,
            "restores": self.restores,
            "failures": self.failures,
            "degraded": self.degraded or None,
            "last_rev": self.last_rev or None,
            "excluded": list(self.excluded),
        }


@dataclass
class Checkpointer:
    """宿主侧的影子 git。两个后端共用它，所以"B6 里两后端的回滚行为一致"是构造保证的。"""

    workspace: Path
    git_dir: Path
    excluded: tuple[str, ...] = (".git",)
    stats: CheckpointStats = field(default_factory=CheckpointStats)

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()
        self.git_dir = Path(self.git_dir)
        self.excluded = tuple(dict.fromkeys(str(item).strip("/") for item in self.excluded if str(item).strip("/")))
        self.stats.excluded = list(self.excluded)
        self.stats.git_dir = str(self.git_dir)
        # 构造即决定：调用方（工厂）只在 CHECKPOINTS 打开且可写时才建 Checkpointer，
        # 所以这里置 True 是如实报告"这一层在"，而不是提前替 git 的成功背书。
        self.stats.enabled = True
        self._git = shutil.which("git") or ""
        self._ready = False

    # ------------------------------------------------------------ 对外

    def available(self) -> tuple[bool, str]:
        if not self._git:
            return False, "PATH 里没有 git，检查点与 /undo 不可用"
        return self._ensure_repo()

    def snapshot(self, note: str = "") -> tuple[str, str]:
        """提交当前工作树，返回 (rev, 原因)。rev 为空即失败，原因必须可打印。"""
        ok, reason = self._ensure_repo()
        if not ok:
            self.stats.failures += 1
            self.stats.degraded = reason
            return "", reason
        add_code, _, add_err = self._git_cmd("add", "-A")
        if add_code != 0:
            self.stats.failures += 1
            self.stats.degraded = _clean(f"git add 失败：{add_err}")
            return "", self.stats.degraded
        # --allow-empty 是刻意的：每次成功的写都对应一条 rev，检查点栈因此和"模型改了几次
        # 文件"一一对应。去掉它，/undo 遇到一次内容没变的写入就会跳过一格。
        code, _, err = self._git_cmd("commit", "-q", "--allow-empty", "-m", f"mcc checkpoint {note}".strip())
        if code != 0:
            self.stats.failures += 1
            self.stats.degraded = _clean(f"git commit 失败：{err}")
            return "", self.stats.degraded
        _, rev, _ = self._git_cmd("rev-parse", "HEAD")
        self.stats.snapshots += 1
        self.stats.last_rev = rev.strip()
        self.stats.degraded = ""
        return self.stats.last_rev, ""

    def restore(self, rev: str) -> tuple[bool, str]:
        ok, reason = self._ensure_repo()
        if not ok:
            return False, reason
        if not rev:
            return False, "没有可回退的检查点"
        code, _, err = self._git_cmd("read-tree", "--reset", "-u", rev)
        if code != 0:
            self.stats.failures += 1
            self.stats.degraded = _clean(f"回滚失败：{err}")
            return False, self.stats.degraded
        self.stats.restores += 1
        return True, ""

    def history(self) -> list[str]:
        """已知 rev，最新的在前。少于两条时 `/undo` 无事可做。"""
        ok, _ = self._ensure_repo()
        if not ok:
            return []
        code, out, _ = self._git_cmd("rev-list", "--first-parent", "HEAD")
        return out.split() if code == 0 else []

    # ------------------------------------------------------------ 内部

    def _ensure_repo(self) -> tuple[bool, str]:
        if self._ready:
            return True, ""
        if not self._git:
            return False, "PATH 里没有 git，检查点与 /undo 不可用"
        try:
            if not (self.git_dir / "HEAD").exists():
                self.git_dir.mkdir(parents=True, exist_ok=True)
                code, _, err = self._raw("init", "--bare", "-q", str(self.git_dir))
                if code != 0:
                    return False, _clean(f"影子仓库建不起来：{err or 'git init 退出码 ' + str(code)}")
                # bare 仓库默认拒绝 --work-tree；不设这一条，后面每个命令都是
                # "fatal: this operation must be run in a work tree"。
                self._raw("config", "core.bare", "false", git_dir=str(self.git_dir))
                # 同一条规矩也写进仓库本身：手动对着这个 git-dir 跑 git 的人，
                # 不该因为没带 -c 就拿到一份换行被重排的工作树。
                self._raw("config", "core.autocrlf", "false", git_dir=str(self.git_dir))
            # 每次会话都重写排除表：IGNORED_DIRS 改过之后旧排除表会把新目录漏进来。
            self._write_exclude()
        except OSError as exc:
            return False, f"影子仓库目录不可写：{type(exc).__name__}: {exc}"
        self._ready = True
        self.stats.ready = True
        return True, ""

    def _write_exclude(self) -> None:
        info = self.git_dir / "info"
        info.mkdir(parents=True, exist_ok=True)
        # 前导 / 表示"只匹配仓库根"，否则一个叫 src/ 的子目录会把全仓库所有同名目录都排除掉。
        lines = [f"/{name}/" for name in self.excluded]
        (info / "exclude").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _git_cmd(self, *args: str) -> tuple[int, str, str]:
        """跑一条影子 git 命令。返回 (退出码, stdout, stderr)，永不抛。

        退出码放第一个是有原因的：这层绝大多数调用只关心"成没成、为什么没成"，
        三个返回值排成一个形状就不会有人再按两种顺序去解包它。
        """
        return self._raw(*_SAFE_OPTS, f"--git-dir={self.git_dir}", f"--work-tree={self.workspace}", *args)

    def _raw(self, *args: str, git_dir: str | None = None) -> tuple[int, str, str]:
        argv = [self._git or "git", "--no-optional-locks", *args]
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
        if git_dir is not None:  # init/config 阶段还没有 work tree，别带 --work-tree
            env["GIT_DIR"] = git_dir
        try:
            completed = subprocess.run(  # noqa: S603 - 固定 argv，不过 shell
                argv,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GIT_TIMEOUT,
                env=env,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return -1, "", f"{type(exc).__name__}: {exc}"
        return completed.returncode, completed.stdout or "", completed.stderr or ""


def for_workspace(
    workspace_root: Path | str,
    memory_dir: Path | str,
    *,
    extra_excluded: Sequence[str] = (),
) -> Checkpointer:
    """装配点唯一知道的构造方式：影子仓库永远躺在记忆目录下，绝不落在别处。"""
    root = Path(workspace_root)
    mem = Path(memory_dir)
    if not mem.is_absolute():
        mem = root / mem
    names = [".git", Path(mem).name, *extra_excluded]
    return Checkpointer(workspace=root, git_dir=mem / SNAPSHOTS_DIRNAME, excluded=tuple(names))
