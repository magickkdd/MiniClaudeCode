"""DockerBackend 的离线证明（SPEC v2 §3.6）。

**这台开发机上没有 docker，所以真实容器路径一次也没跑过。** 这个文件能钉住的是三件事：
我们构造出来的命令行、可用性探测的降级判定、以及"两个后端共用同一个宿主侧影子 git"。
钉不住的是"镜像里真的跑得起 pytest"——那条边界在 SPEC §3.6 的 as-built 里同样写着，
B6 的证据文件也不会假装它成立。

做法是一个替身 `docker` 可执行文件：它把收到的 argv 原样写进一个日志文件，
于是"我们到底拼出了什么命令行"变成可以断言的东西，而不是读代码相信。
`test_backend_factory.py` 复用这里的 `write_docker_stub`。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from miniclaude.backend.checkpoints import for_workspace
from miniclaude.backend.docker import CONTAINER_WORKDIR, DEFAULT_IMAGE, DockerBackend

STUB_SOURCE = '''
import json
import os
import sys

args = sys.argv[1:]
log = os.environ.get("MCC_DOCKER_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": args}, ensure_ascii=False) + "\\n")

mode = os.environ.get("MCC_DOCKER_MODE", "ok")
if mode == "down":
    print("Cannot connect to the Docker daemon at unix:///var/run/docker.sock.", file=sys.stderr)
    raise SystemExit(1)
head = args[:1]
if head == ["version"]:
    print("27.1.1")
    raise SystemExit(0)
if head == ["rm"]:
    raise SystemExit(0)
print("STUB-OUT " + " ".join(args))
print("STUB-ERR " + os.environ.get("MCC_STUB_NOTE", ""), end="")
raise SystemExit(int(os.environ.get("MCC_STUB_RC", "0")))
'''


def write_docker_stub(root: Path) -> Path:
    """造一个会自报家门的假 docker。Windows 上 CreateProcess 能直接跑 .bat。"""
    script = root / "docker_stub.py"
    script.write_text(STUB_SOURCE, encoding="utf-8")
    batch = root / "docker.bat"
    batch.write_text(f'@echo off\n"{Path(sys.executable)}" "{script}" %*\n', encoding="ascii")
    return batch


@pytest.fixture
def stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    log = tmp_path / "docker-calls.jsonl"
    monkeypatch.setenv("MCC_DOCKER_LOG", str(log))
    monkeypatch.delenv("MCC_DOCKER_MODE", raising=False)
    return {"binary": write_docker_stub(tmp_path), "log": log, "root": tmp_path}


def calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line)["argv"] for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------- 命令行怎么拼


def test_build_argv_carries_the_isolation_flags(tmp_path: Path) -> None:
    """`--rm` / `--network none` / 只挂工作区 —— 沙箱的边界全在这几行 argv 里。"""
    b = DockerBackend(workspace=tmp_path, image="python:3.12-slim", network="none")
    argv, name = b._build("docker", "pytest -q", cwd=tmp_path, env=None)
    text = " ".join(argv)
    assert argv[:3] == ["docker", "run", "--rm"], "驻留容器会让两次执行共享状态，B6 的对照就废了"
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert f"-v {tmp_path}:{CONTAINER_WORKDIR}" in text
    assert argv[argv.index("-w") + 1] == CONTAINER_WORKDIR
    assert argv[argv.index("python:3.12-slim") + 1 :] == ["bash", "-c", "pytest -q"], "镜像必须排在容器命令之前"
    assert name.startswith("mcc-"), "没有 --name 就没法在超时时收掉容器"


def test_string_command_goes_through_a_shell_in_the_container(tmp_path: Path) -> None:
    """模型写的 `mkdir -p a && pytest` 必须有 shell，序列命令必须没有。"""
    b = DockerBackend(workspace=tmp_path)
    inner, entrypoint = b._translate("echo hi && ls")
    assert (inner, entrypoint) == (["bash", "-c", "echo hi && ls"], "")
    argv, _ = b._build("docker", "echo hi", cwd=tmp_path, env=None)
    assert argv[-3:] == ["bash", "-c", "echo hi"]


def test_host_interpreter_is_translated_and_nothing_else(tmp_path: Path) -> None:
    """`run_tests` 传的是 `sys.executable`，容器里没有这个文件，所以换成镜像的 python3。

    只认宿主解释器这一个位置：`./venv/bin/python` 是模型自己挑的解释器，替它换掉就是
    篡改意图（也是 B6 里最难查的那种假失败）。
    """
    b = DockerBackend(workspace=tmp_path)
    assert b._translate([sys.executable, "-m", "pytest", "tests"]) == (["-m", "pytest", "tests"], "python3")
    assert b._translate(["./venv/bin/python", "-c", "x"]) == (["./venv/bin/python", "-c", "x"], "")
    assert b._translate([]) == (["true"], "")


def test_host_environment_does_not_leak_into_the_container(tmp_path: Path) -> None:
    """`.env` 里的密钥不该出现在被评测进程的 environ 里 —— 只透传显式给的键。"""
    b = DockerBackend(workspace=tmp_path)
    argv, _ = b._build("docker", ["pytest"], cwd=tmp_path, env={"PYTHONDONTWRITEBYTECODE": "1"})
    passed = [argv[i + 1] for i, item in enumerate(argv) if item == "-e"]
    assert passed == ["PYTHONDONTWRITEBYTECODE=1"]
    assert not any(key.startswith(("OPENAI", "ANTHROPIC", "AWS")) for key in passed)


# --------------------------------------------------------------- 探测与降级


def test_probe_success_and_caching(stub: dict[str, Path]) -> None:
    b = DockerBackend(workspace=stub["root"], binary=str(stub["binary"]))
    ok, reason = b.available()
    assert ok and "27.1.1" in reason and DEFAULT_IMAGE in reason
    b.available()
    assert len(calls(stub["log"])) == 1, "每条命令前都探测一遍会把延迟花在无关的地方"


def test_probe_reports_daemon_down_verbatim(stub: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI 在但守护进程没应答时，原因要原样带出去 —— 写成"docker 不可用"会让人去装已经装好的东西。"""
    monkeypatch.setenv("MCC_DOCKER_MODE", "down")
    b = DockerBackend(workspace=stub["root"], binary=str(stub["binary"]))
    ok, reason = b.available()
    assert not ok
    assert "守护进程" in reason and "Cannot connect" in reason


def test_missing_binary_is_reported_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    b = DockerBackend(workspace=tmp_path)
    ok, reason = b.available()
    assert not ok and "docker" in reason
    result = b.exec("echo hi", cwd=tmp_path, timeout=5)
    assert result.ran is False and result.launch_error


# --------------------------------------------------------------- 真的跑一次


def test_exec_runs_and_counts_launches(stub: dict[str, Path]) -> None:
    b = DockerBackend(workspace=stub["root"], binary=str(stub["binary"]))
    result = b.exec("echo hi", cwd=stub["root"], timeout=30)
    assert result.ran and result.returncode == 0
    assert "STUB-OUT" in result.stdout
    assert b.launches == 1 and b.errors == 0
    assert "docker run" in result.detail and CONTAINER_WORKDIR in result.detail


def test_nonzero_exit_is_an_observation_not_a_failure(stub: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """退出码非 0 是"测试没通过"，不是"后端坏了"。混起来会让 bash 工具报成工具故障。"""
    monkeypatch.setenv("MCC_STUB_RC", "1")
    b = DockerBackend(workspace=stub["root"], binary=str(stub["binary"]))
    result = b.exec("pytest", cwd=stub["root"], timeout=30)
    assert result.ran and result.returncode == 1


def test_reap_asks_for_the_named_container(stub: dict[str, Path]) -> None:
    """超时之后要尽力把容器收掉。这里直接测 `_reap` 拼出的命令，不真等 10 秒。"""
    b = DockerBackend(workspace=stub["root"], binary=str(stub["binary"]))
    b._reap(str(stub["binary"]), "mcc-abc123")
    recorded = calls(stub["log"])
    assert recorded[-1][:3] == ["rm", "-f", "--time"], recorded[-1]
    assert recorded[-1][-1] == "mcc-abc123"


# --------------------------------------------------------------- 与宿主共用检查点


def test_checkpoints_are_the_same_host_side_shadow_git(stub: dict[str, Path]) -> None:
    """B6 的"两后端回滚行为一致"是构造保证的：docker 侧不自己实现 git。

    容器里写的文件落在 bind mount 的宿主目录上，所以快照必须在宿主侧做 —— 两边各自
    实现一遍 git，就等于同一件事有两份实现和两个 bug。
    """
    root = stub["root"]
    (root / "app.py").write_text("print('a')\n", encoding="utf-8")
    cp = for_workspace(root, root / ".mcc")
    b = DockerBackend(workspace=root, binary=str(stub["binary"]), checkpointer=cp)
    rev, why = b.snapshot()
    assert rev, why
    assert b.history() == cp.history()
    (root / "app.py").write_text("print('b')\n", encoding="utf-8")
    assert b.restore(rev)[0]
    assert (root / "app.py").read_text(encoding="utf-8") == "print('a')\n"


def test_stats_admit_isolation(stub: dict[str, Path]) -> None:
    b = DockerBackend(workspace=stub["root"], binary=str(stub["binary"]), image="img:1", network="bridge")
    stats = b.stats()
    assert stats["isolated"] is True and stats["image"] == "img:1" and stats["network"] == "bridge"
    assert stats["available"] is True
    assert set(stats["checkpoints"]) >= {"enabled", "ready", "snapshots", "restores", "failures"}
