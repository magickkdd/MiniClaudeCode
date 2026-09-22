"""影子 git 检查点测试（SPEC v2 §3.6）。

这个文件盯的四条，每条都对应一个"错了就不可逆"的场景：

1. **用户自己的 `.git` 不能被碰。** 我们只把 `--git-dir` 指向 `<记忆目录>/snapshots`。
   如果哪天真在工作区 `git init` 了一次，用户仓库从此报错，而这个破坏没有 undo。
2. **恢复要覆盖"改过 / 新增 / 删除"三类文件。** 只测第一类的实现看着都对，
   第二类留下孤儿文件、第三类根本不复原 —— `checkout <rev> -- .` 就是死在这上面，
   所以这里第三类单独占一条测试。
3. **字节级复原。** 这台机器的全局 `core.autocrlf=true`（实测），不钉住
   `core.autocrlf=false` 的话，一次 `/undo` 会把整个工作树的换行重排一遍。
4. **失败一律降级成原因串。** 没有 git、盘不可写、索引被锁，都不能让写文件的工具报错：
   度量层没有否决任务的权力（§2.3-1）。

真 git 的用例统一挂在 `git_available` 上；没有 git 的机器会跳过它们而不是假装通过。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from miniclaude.backend.checkpoints import SNAPSHOTS_DIRNAME, Checkpointer, for_workspace

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="这台机器上没有 git，检查点走降级路径")


def run_git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return (completed.stdout or "").strip()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    # 用 write_bytes 而不是 write_text：Windows 上文本模式会把 "\n" 换成 "\r\n"，
    # 那么"字节级复原"这条测试断言的就是 git 之外的另一件事了。
    (tmp_path / "app.py").write_bytes(b"print('original')\n")
    return tmp_path


@pytest.fixture
def checkpoint_dir(workspace: Path) -> Path:
    return workspace / ".mcc" / SNAPSHOTS_DIRNAME


@needs_git
def test_snapshot_then_restore_returns_the_exact_bytes(workspace: Path, checkpoint_dir: Path) -> None:
    """改过的文件要回到旧内容，而且是一个字节都不差。"""
    cp = for_workspace(workspace, workspace / ".mcc")
    first, why = cp.snapshot("baseline")
    assert first, f"第一次快照就该成功：{why}"
    (workspace / "app.py").write_text("print('rewritten with a much longer line')\n", encoding="utf-8")
    ok, why = cp.restore(first)
    assert ok, why
    assert (workspace / "app.py").read_bytes() == b"print('original')\n"


@needs_git
def test_restore_deletes_files_created_after_the_rev(workspace: Path) -> None:
    """第三类变化单独钉：`checkout <rev> -- .` 会漏掉它，SPEC 因此指定 read-tree。

    这条是 `/undo` 的承诺本身 —— 撤销一次"新建文件"的写入，那个文件必须消失。
    """
    cp = for_workspace(workspace, workspace / ".mcc")
    base, _ = cp.snapshot("baseline")
    (workspace / "new.py").write_text("hello\n", encoding="utf-8")
    after, _ = cp.snapshot("write new.py")
    assert (workspace / "new.py").exists()
    ok, why = cp.restore(base)
    assert ok, why
    assert not (workspace / "new.py").exists(), "回滚之后还留着新文件，等于 /undo 只撤了一半"
    assert after and base != after


@needs_git
def test_restore_also_removes_a_deleted_file(workspace: Path) -> None:
    """删掉的文件要回来。三类变化少测一类，就有一类是没人看守的。"""
    cp = for_workspace(workspace, workspace / ".mcc")
    base, _ = cp.snapshot("baseline")
    (workspace / "app.py").unlink()
    assert cp.restore(base)[0]
    assert (workspace / "app.py").read_text(encoding="utf-8") == "print('original')\n"


@needs_git
def test_users_own_repository_is_never_touched(workspace: Path) -> None:
    """用户工作区本身是个 git 仓库时，快照与回滚都不能改它的任何一件东西。

    看的是三处：HEAD 指向的 commit、reflog 的长度、以及 `git status` 的结论。
    只查 HEAD 不够 —— 往用户索引里 `add` 过东西的话 HEAD 不变而暂存区已经脏了。
    status 比较时把记忆目录本身剔掉：`.mcc/` 出现在未跟踪列表里是**预期**的（它和
    MemoryStore 的缓存是同一回事，SPEC §2.3-1 定义它是派生物），要守的是"恢复不许
    让用户自己的文件变脏"。
    """
    def status() -> str:
        return "\n".join(
            line for line in run_git(workspace, "status", "--porcelain").splitlines() if ".mcc/" not in line
        )

    run_git(workspace, "init", "-q")
    run_git(workspace, "-c", "user.name=u", "-c", "user.email=u@local", "commit", "-q",
            "--allow-empty", "-m", "user commit")
    head_before = run_git(workspace, "rev-parse", "HEAD")
    reflog_before = run_git(workspace, "reflog", "--all", "--oneline").splitlines()
    status_before = status()

    cp = for_workspace(workspace, workspace / ".mcc")
    rev, why = cp.snapshot("baseline")
    assert rev, why
    (workspace / "extra.py").write_text("x = 1\n", encoding="utf-8")
    second, _ = cp.snapshot("write extra.py")
    assert cp.restore(rev)[0] and second

    assert run_git(workspace, "rev-parse", "HEAD") == head_before
    assert run_git(workspace, "reflog", "--all", "--oneline").splitlines() == reflog_before
    assert status() == status_before, "回滚不该让用户仓库的 status 变脏"
    assert not (workspace / "extra.py").exists(), "影子仓库该管到的那次写入没管到"
    assert (workspace / ".git" / "config").read_text(encoding="utf-8").count("mcc") == 0


@needs_git
def test_excludes_are_root_anchored(workspace: Path, checkpoint_dir: Path) -> None:
    """排除表必须写死 `/.git/`、记忆目录，而且是根锚定的。

    根锚定这条不是格式洁癖：不带前导 `/` 的 `.mcc/` 会连工作区里任意深度的同名子目录
    一起排除掉，那意味着用户在 `vendor/.mcc/` 下的真文件永远不会被检查点保护。
    """
    cp = for_workspace(workspace, workspace / ".mcc")
    assert cp.snapshot("baseline")[0]
    lines = (checkpoint_dir / "info" / "exclude").read_text(encoding="utf-8").splitlines()
    assert "/.git/" in lines
    assert "/.mcc/" in lines
    assert all(line.startswith("/") and line.endswith("/") for line in lines if line)


@needs_git
def test_snapshot_directory_lives_under_the_memory_dir(workspace: Path, checkpoint_dir: Path) -> None:
    """影子仓库的位置本身就是契约：它只能在记忆目录下，不能落在别处。"""
    cp = for_workspace(workspace, workspace / ".mcc")
    assert cp.git_dir == checkpoint_dir
    assert cp.snapshot("baseline")[0]
    assert (checkpoint_dir / "HEAD").exists()


@needs_git
def test_line_endings_survive_a_restore(workspace: Path) -> None:
    """LF 进 LF 出。这台机器的全局 autocrlf=true，不设 false 就是每次 /undo 重排换行。"""
    target = workspace / "lf.txt"
    target.write_bytes(b"line one\nline two\n")
    cp = for_workspace(workspace, workspace / ".mcc")
    rev, _ = cp.snapshot("baseline")
    target.write_bytes(b"changed\n")
    assert cp.restore(rev)[0]
    assert target.read_bytes() == b"line one\nline two\n", "恢复改写了换行风格，diff 里就全是噪音"


@needs_git
def test_history_is_newest_first_and_grows_one_per_snapshot(workspace: Path) -> None:
    """每次快照一条 rev，最新的在前 —— `/undo` 的游标就是按下标走这条线的。"""
    cp = for_workspace(workspace, workspace / ".mcc")
    first, _ = cp.snapshot("a")
    second, _ = cp.snapshot("b")
    assert cp.history() == [second, first]
    (workspace / "app.py").write_text("print('changed')\n", encoding="utf-8")
    third, _ = cp.snapshot("c")
    assert cp.history() == [third, second, first]


@needs_git
def test_unchanged_working_tree_still_produces_a_rev(workspace: Path) -> None:
    """内容没变也要记一条。去掉 `--allow-empty`，/undo 遇到一次"写了但没改"就跳过一格。"""
    cp = for_workspace(workspace, workspace / ".mcc")
    first, _ = cp.snapshot("one")
    second, _ = cp.snapshot("two")
    assert first and second and first != second
    assert len(cp.history()) == 2


@needs_git
def test_restore_without_a_rev_returns_a_reason(workspace: Path) -> None:
    cp = for_workspace(workspace, workspace / ".mcc")
    assert cp.snapshot("baseline")[0]
    ok, why = cp.restore("")
    assert not ok and why


@needs_git
def test_unwritable_shadow_repo_degrades_loudly(tmp_path: Path) -> None:
    """记忆目录被一个普通文件占住时：不抛、不崩，交回一句能印的原因，并把失败记进 stats。

    这里刻意不用 `chmod` 造不可写盘 —— Windows 上它不生效，测出来是个假绿。
    """
    blocked = tmp_path / "notes.txt"
    blocked.write_text("占位\n", encoding="utf-8")
    cp = Checkpointer(workspace=tmp_path, git_dir=blocked / SNAPSHOTS_DIRNAME)
    ok, reason = cp.available()
    assert not ok and reason
    rev, why = cp.snapshot("baseline")
    assert rev == "" and why
    assert cp.stats.failures >= 1
    assert cp.stats.degraded
    assert cp.history() == []


def test_missing_git_binary_degrades_without_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PATH 里没有 git：可用性问题要说得出原因，写文件的工具不能因此报错。"""
    cp = for_workspace(tmp_path, tmp_path / ".mcc")
    monkeypatch.setattr(cp, "_git", "")
    ok, reason = cp.available()
    assert not ok and "git" in reason
    assert cp.snapshot("baseline") == ("", reason)
    assert cp.restore("deadbeef")[0] is False
    assert cp.stats.enabled is True, "装配了就是开了，用不了是另一件事（ready 才反映仓库状态）"
    assert cp.stats.ready is False


def test_stats_report_enabled_at_construction(tmp_path: Path) -> None:
    """构造 = 这一层被装配进来了；`ready` 才是"影子仓库建起来了"。

    分开是因为组装期那条 `backend` trace 必然早于任何 git 操作 —— 两件事混成一个字段，
    终端上就会永远显示"检查点 关"，而它其实是开的。
    """
    cp = for_workspace(tmp_path, tmp_path / ".mcc")
    trace = cp.stats.as_trace()
    assert trace["enabled"] is True
    assert trace["ready"] is False
    if shutil.which("git"):
        assert cp.snapshot("baseline")[0]
        assert cp.stats.as_trace()["ready"] is True


def test_excluded_names_are_deduped_and_normalised(tmp_path: Path) -> None:
    """同一个目录名传三遍、带不带尾斜杠，进排除表时都只有一个。

    刻意**不**去掉首尾空白：目录名里带空格是合法的，替用户 strip 掉就会排错对象。
    """
    cp = Checkpointer(workspace=tmp_path, git_dir=tmp_path / "snap", excluded=(".git", "/.git/", "dist/"))
    assert cp.excluded == (".git", "dist")
    assert cp.stats.excluded == [".git", "dist"]


def test_memory_dir_may_be_absolute(tmp_path: Path) -> None:
    """MEMORY_DIR 给绝对路径时，影子仓库跟着它走，而不是被拼回工作区里。"""
    outside = tmp_path / "elsewhere" / "memory"
    root = tmp_path / "project"
    root.mkdir(parents=True)
    cp = for_workspace(root, outside)
    assert cp.git_dir == outside / SNAPSHOTS_DIRNAME
