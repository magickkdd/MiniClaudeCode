"""后端工厂与 LocalBackend（SPEC v2 §3.6 的 B6 风险集中地）。

`EXECUTION_BACKEND=docker` 而机器上没有 docker 时，`select_backend` 必须既**不崩**也
**不静默**。这个文件钉的就是"不静默"那半边：降级的事同时出现在 `note()`、`as_trace()`
和 stats 里，三处都是同源的一个 `degraded` 串 —— 少一处，报表上就会写着 docker 而跑的
是本机，那批数字全是假的。

LocalBackend 那半边钉的是"现状行为一字不改"：字符串过 shell（优先 Git Bash）、
序列不过 shell、超时算工具超时而不是工具报错、环境里补 UTF-8。这几条都是 v1 已有
的行为，搬进后端之后必须还能测出来。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from test_docker_backend import write_docker_stub  # noqa: E402 - 同一个假 docker，别抄第二遍

from miniclaude.backend.checkpoints import SNAPSHOTS_DIRNAME
from miniclaude.backend.factory import select_backend
from miniclaude.backend.local import LocalBackend, merge_env
from miniclaude.backend.protocol import NO_CHECKPOINT_REASON
from miniclaude.config import BACKEND_NAMES

HAS_BASH = shutil.which("bash") is not None


# --------------------------------------------------------------- 选择与降级


def test_default_is_local_and_not_degraded(tmp_path: Path) -> None:
    choice = select_backend("local", workspace_root=tmp_path, memory_dir=tmp_path / ".mcc")
    assert choice.name == "local" and choice.requested == "local"
    assert choice.degraded == ""
    assert choice.note() == "本次用 local 后端"
    assert choice.as_trace()["degraded"] is None


@pytest.mark.parametrize("name", ["", "LOCAL", " local "])
def test_names_are_normalised(tmp_path: Path, name: str) -> None:
    choice = select_backend(name, workspace_root=tmp_path, memory_dir=tmp_path / ".mcc")
    assert choice.requested == "local"


def test_a_typo_fails_at_startup(tmp_path: Path) -> None:
    """打错的名字变成 local 就是静默换后端 —— 本模块存在的理由正是禁止它。"""
    with pytest.raises(ValueError) as excinfo:
        select_backend("podman", workspace_root=tmp_path, memory_dir=tmp_path / ".mcc")
    assert "podman" in str(excinfo.value)
    assert all(name in str(excinfo.value) for name in BACKEND_NAMES)


def test_docker_without_docker_degrades_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    choice = select_backend("docker", workspace_root=tmp_path, memory_dir=tmp_path / ".mcc")
    assert choice.name == "local", "降级是允许的"
    assert choice.requested == "docker", "但点名过的事必须留在结论里"
    assert "docker" in choice.degraded
    assert "local" in choice.note() and "docker" in choice.note()
    payload = choice.as_trace()
    assert payload["degraded"] == choice.degraded
    assert payload["requested"] == "docker" and payload["backend"] == "local"


def test_docker_with_a_working_binary_is_chosen(tmp_path: Path) -> None:
    """有可用的 docker（这里是替身）时就不能退到 local —— 反方向的静默同样是 bug。"""
    choice = select_backend(
        "docker",
        workspace_root=tmp_path,
        memory_dir=tmp_path / ".mcc",
        docker_binary=str(write_docker_stub(tmp_path)),
    )
    assert choice.name == "docker" and choice.degraded == ""
    assert choice.as_trace()["isolated"] is True


def test_docker_probe_failure_degrades_to_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCC_DOCKER_MODE", "down")
    choice = select_backend(
        "docker",
        workspace_root=tmp_path,
        memory_dir=tmp_path / ".mcc",
        docker_binary=str(write_docker_stub(tmp_path)),
    )
    assert choice.name == "local"
    assert "守护进程" in choice.degraded


# --------------------------------------------------------------- 检查点开关


def test_checkpoints_off_keeps_the_backend_usable(tmp_path: Path) -> None:
    """`--no-checkpoints` 只关掉撤销能力，不关掉执行。"""
    choice = select_backend(
        "local", workspace_root=tmp_path, memory_dir=tmp_path / ".mcc", checkpoints=False
    )
    backend = choice.backend
    assert backend.snapshot() == ("", NO_CHECKPOINT_REASON)
    assert backend.restore("deadbeef") == (False, NO_CHECKPOINT_REASON)
    assert backend.history() == []
    if HAS_BASH:
        assert backend.exec("echo hi", cwd=tmp_path, timeout=20).ran


def test_both_states_report_the_same_stat_fields(tmp_path: Path) -> None:
    """没装配检查点时也交回同一份字段：读报表的人不该为两种形状各写一个分支。"""
    on = select_backend("local", workspace_root=tmp_path, memory_dir=tmp_path / ".mcc").backend.stats()
    off = select_backend(
        "local", workspace_root=tmp_path, memory_dir=tmp_path / ".mcc", checkpoints=False
    ).backend.stats()
    assert set(on["checkpoints"]) == set(off["checkpoints"])
    assert on["checkpoints"]["enabled"] is True and off["checkpoints"]["enabled"] is False


def test_shadow_repo_lands_under_the_memory_dir(tmp_path: Path) -> None:
    choice = select_backend(
        "local",
        workspace_root=tmp_path,
        memory_dir=tmp_path / "custom-memory",
        ignored_dirs=["node_modules", ".venv"],
    )
    stats = choice.backend.stats()["checkpoints"]
    assert Path(str(stats["git_dir"])).name == SNAPSHOTS_DIRNAME
    assert "custom-memory" in str(stats["git_dir"])
    assert "node_modules" in stats["excluded"]


# --------------------------------------------------------------- LocalBackend


def test_local_backend_is_always_available(tmp_path: Path) -> None:
    ok, reason = LocalBackend().available()
    assert ok and "隔离" in reason


@pytest.mark.skipif(not HAS_BASH, reason="没有 bash 时走的是 shell=True 分支，行为不同")
def test_string_command_runs_through_bash(tmp_path: Path) -> None:
    """模型写的 `&&`、管道、`mkdir -p` 都要求 POSIX shell；cmd.exe 会当场失败。"""
    result = LocalBackend().exec("echo a && echo b", cwd=tmp_path, timeout=30)
    assert result.ran and result.returncode == 0
    assert result.stdout.split() == ["a", "b"]
    assert "bash" in result.detail


def test_sequence_command_does_not_go_through_a_shell(tmp_path: Path) -> None:
    """`run_tests` 今天是 argv 直跑。过一遍 shell 就改变 quoting，那是一处行为漂移。"""
    result = LocalBackend().exec(
        [sys.executable, "-c", "import sys; print('|'.join(sys.argv[1:]))", "a b", "c;d"],
        cwd=tmp_path,
        timeout=60,
    )
    assert result.ran, result.output
    assert "a b|c;d" in result.stdout, "两个参数被 shell 拆开或合并了 —— 序列必须原样 execv"


@pytest.mark.skipif(not HAS_BASH, reason="超时用例需要一个可预测的 sleep")
def test_timeout_is_a_tool_timeout_not_a_tool_error(tmp_path: Path) -> None:
    result = LocalBackend().exec("sleep 5", cwd=tmp_path, timeout=1)
    assert result.timed_out and result.ran is False


def test_a_command_that_cannot_launch_is_reported_as_such(tmp_path: Path) -> None:
    result = LocalBackend().exec([str(tmp_path / "definitely-not-here.exe")], cwd=tmp_path, timeout=10)
    assert result.ran is False and result.launch_error


def test_merge_env_forces_utf8_and_unbuffered(tmp_path: Path) -> None:
    """没有 PYTHONIOENCODING 时 Windows 上的 pytest 会吐 GBK，模型看到的失败原因是乱码。"""
    env = merge_env({"EXTRA": "1"})
    assert env["PYTHONUNBUFFERED"] == "1" and env["PYTHONIOENCODING"] == "utf-8"
    assert env["EXTRA"] == "1"
    assert merge_env(None)["PATH"] == env["PATH"]


def test_stats_carry_the_chosen_shell(tmp_path: Path) -> None:
    stats = LocalBackend().stats()
    assert stats["backend"] == "local" and stats["isolated"] is False
    assert stats["shell"]
