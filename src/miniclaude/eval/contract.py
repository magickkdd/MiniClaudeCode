"""判定原语 —— 从 `demos/run_demo.py` 抽出来的那一层，不新写一套判据。

v1 的 demo 判据已经被 18 个测试盯着了，评测层要的是**同一批原语**能被批量调用，
所以这里是搬家而不是重写：`Check` / `Outcome` / `Context` 与 `pytest_report` /
`behavior` / `subset_*` 只此一份，`run_demo.py` 反过来 import 它。

搬家时补了评测层才需要的两件事（都围着同一条铁律：**判定由我们跑代码得出，
不看模型最后那段话**）：

- `node_ids()` 把 pytest 的结果落到**用例级**，`fail_to_pass` / `pass_to_pass`
  白名单因此可表达 —— v1 只有"仓库级退出码"，抓不到"改对了这处、弄坏了别处"。
- `verify_nodes()` 只跑白名单里的那些用例，而不是"整个仓库再跑一遍"。
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from miniclaude.agent.state import AgentResult
from miniclaude.memory import MEMORY_DIRNAME

# 判定眼里的"不算仓库内容"。最后那一项是 agent 自己写的派生缓存（SPEC v2 §3.4）：
# 把它算进树哈希，一次 AUTO 跑批就会自己把自己留下的缓存当成"改动了源码"。
# 目录名本身是可配置的（`MEMORY_DIR`，§3.6），所以这里只放**默认名**，实际判定时
# 由 `noise_names()` 把当次会话真正用的那个名字补上 —— 只信 `.mcc` 的话，改一次目录名
# 就会让整套判据凭空多出一批"新增文件"。
NOISE = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache")


def noise_names(memory_dir: str | Path = MEMORY_DIRNAME) -> tuple[str, ...]:
    """`NOISE` 加上这一次真正生效的记忆目录名。"""
    name = Path(str(memory_dir)).name
    return NOISE + ((name,) if name else ())


CHILD_ENV = {
    **os.environ,
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
}
_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|errors?|error|skipped)\b")
_FAILED_NODE_RE = re.compile(r"^(?:FAILED|ERROR) (\S+::\S+)", re.MULTILINE)


# --------------------------------------------------------------- 判定最小单元


@dataclass
class Check:
    label: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        mark = "x" if self.ok else " "
        body = f"{self.label} —— {self.detail}" if self.detail else self.label
        return f"- [{mark}] {body}"


@dataclass
class Outcome:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    @property
    def passed(self) -> int:
        return sum(1 for check in self.checks if check.ok)

    def verdict(self) -> str:
        return "PASS" if self.ok else "FAIL"

    def lines(self) -> list[str]:
        return [check.line() for check in self.checks]


@dataclass
class Context:
    """判定能看到的一切。判定逻辑不许依赖模型的最后一段话。"""

    engine: str
    workdir: Path
    baseline: Path
    result: AgentResult
    console: list[str]
    # 这一次会话把派生缓存写在哪个目录下（`MEMORY_DIR`）。判定要把它当噪声排除，
    # 所以它必须跟着现场走，而不是由判定层再去问一遍环境 —— 两处取值一旦分叉，
    # "只读模式没碰盘"就会因为一个 `.mcc/` 而判假。
    memory_dir: str = MEMORY_DIRNAME


# --------------------------------------------------------------- 工作副本隔离


def isolate(source: Path, target: Path, *, memory_dir: str | Path = MEMORY_DIRNAME) -> Path:
    """把考题复制成一份干净的工作副本，返回真正可用的那个路径。

    判定必须在副本里跑：跑过一次之后目录就被改过了，第二次再拿它当基线，
    "只增不删""逐字节未变"这类判据会全部失效。demo 与评测层共用这一份实现，
    否则两边对"干净"的定义会分叉。

    复制时排除 `noise_names()`：把上一次跑批留下的记忆目录抄进新副本，等于让一次
    只读任务"看起来"改动了仓库。目录名从调用方拿（`MEMORY_DIR`），不写死。
    """
    if target.exists():
        try:
            shutil.rmtree(target)
        except OSError:
            # Windows 保留设备名（NUL、CON…）会留下 rm 删不掉的残留文件。
            # 与其让整批评测卡在一个 WinError 5 上，不如换一个带时间戳的副本继续。
            target = target.with_name(f"{target.name}.{time.strftime('%H%M%S')}")
            shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(*noise_names(memory_dir)))
    else:
        target.mkdir(parents=True)
    return target


# --------------------------------------------------------------- 子进程与 pytest


def run_in(workdir: Path, argv: list[str], timeout: int) -> tuple[int, str]:
    try:
        done = subprocess.run(  # noqa: S603 - 判定脚本由我们自己拼，不含用户输入
            argv,
            cwd=str(workdir),
            env=CHILD_ENV,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 124, f"{type(exc).__name__}: {exc}"
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()


def _pytest_argv(targets: Sequence[str]) -> list[str]:
    """被判定方绝不能继承**判定方**的 pytest 配置。

    本项目根目录的 `pyproject.toml` 设了 `testpaths`，`conftest.py` 又在忽略
    fixture 仓库；不加 `--confcutdir .` 与显式目标，被判定方就会收集到 0 个用例，
    然后给出一个假的"退出码 0"（v1 §10-6 踩过这条）。

    `--rootdir .` 是同一件事的另一半：不钉住它，node id 会变成
    `eval/fixtures/<name>/tests/test_x.py::test_y` —— 考题的标识里长出宿主仓库的
    目录前缀，任务 JSON 里的白名单就跟着机器变了。
    """
    return [
        sys.executable, "-m", "pytest", "-q", "--no-header",
        "-p", "no:cacheprovider", "--confcutdir", ".", "--rootdir", ".", *targets,
    ]


def pytest_report(workdir: Path, targets: Sequence[str] = ()) -> tuple[int, dict[str, int], str]:
    code, output = run_in(workdir, _pytest_argv([*(str(t) for t in targets)] or ["."]), timeout=300)
    counts: dict[str, int] = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for num, raw in _SUMMARY_RE.findall(output):
        key = raw if raw in counts else "errors" if raw.startswith("error") else ""
        if key:
            counts[key] = max(counts[key], int(num))
    counts["collected"] = (
        counts["passed"] + counts["failed"] + counts["errors"] + counts["skipped"]
    )
    if not counts["collected"]:
        # 一个用例都没收集到绝不等于"通过"。这条兜底让上面的配置泄漏立刻暴露。
        return 5, counts, output or "no tests were collected"
    return code, counts, output


def behavior(
    workdir: Path,
    probe: str,
    *,
    label: str = "函数行为符合任务承诺（不依赖模型自述）",
) -> Check:
    """把"任务里承诺的行为"写成一段独立脚本去跑。

    让模型自己说"我修好了"再抄进报告，就是"自评成功率 = 评测造假"。
    """
    code, output = run_in(workdir, [sys.executable, "-c", probe], timeout=120)
    last = output.splitlines()[-1][:160] if output else f"退出码 {code}，无输出"
    return Check(label=label, ok=code == 0, detail=last)


def node_ids(workdir: Path) -> tuple[list[str], list[str]]:
    """(当前通过的用例, 当前失败或用例级报错的用例) —— 用例级白名单的地基。

    `--collect-only -q` 给出全部 node id，短摘要里的 `FAILED/ERROR <id>` 给出坏的，
    两者相减即通过集合。不依赖任何 pytest 插件。

    这里**不能再叠一个 `-q`**：`_pytest_argv` 已经带了一个，安静等级到 2 之后
    `--collect-only` 改成只打印"文件: 条数"，一个 node id 都拿不到（0 绿 0 红，
    看上去像"这个仓库没测试"，实际是判据自己瞎了）。
    """
    listed_code, listing = run_in(workdir, _pytest_argv(["--collect-only"]), timeout=300)
    collected = [
        line.strip()
        for line in listing.splitlines()
        if "::" in line and not line.startswith(("FAILED", "ERROR", "="))
    ]
    if not collected:
        return [], []
    _, summary = run_in(workdir, _pytest_argv(["-rf", "--tb=no", "."]), timeout=300)
    broken = set(_FAILED_NODE_RE.findall(summary))
    if listed_code == 5 and not broken:
        # 收集得到但一条都没跑起来（导入错误之类在 collection 阶段就炸了）。
        return [], collected
    return [node for node in collected if node not in broken], sorted(broken)


def verify_nodes(workdir: Path, nodes: Sequence[str]) -> tuple[int, str]:
    """只跑白名单里的用例。空集合表示"这一项没有要求"，返回 0 而不是失败。"""
    wanted = [str(node) for node in nodes]
    if not wanted:
        return 0, "（白名单为空）"
    return run_in(workdir, _pytest_argv(wanted), timeout=300)


def verify_nodes_split(workdir: Path, nodes: Sequence[str]) -> tuple[list[str], list[str], str]:
    """跑白名单，按用例分成 (绿的, 红的, 输出摘要)。

    非 0 退出码却解析不出 `FAILED` 行时，整批算红：那通常是"用例 id 根本不存在"
    （pytest 退出码 4）或收集阶段就炸了，两种都不能算通过。
    """
    wanted = [str(node) for node in nodes]
    if not wanted:
        return [], [], "（白名单为空）"
    code, output = verify_nodes(workdir, wanted)
    broken = set(_FAILED_NODE_RE.findall(output))
    if code != 0 and not broken:
        tail = [line for line in output.splitlines() if line.strip()]
        return [], wanted, (tail[-1][:160] if tail else f"退出码 {code}，无输出")
    return [node for node in wanted if node not in broken], sorted(broken), f"退出码 {code}"


def run_verify_cmd(workdir: Path, command: str) -> tuple[int, str]:
    """跑任务自带的 `verify_cmd`。退出码 0 即成功，**不读模型的答复**。

    故意不经过 shell：判据要能对着一条含 `; rm -rf` 的任务描述也安全。
    代价是管道、重定向这类语法不支持 —— 需要它们就写成 probe 脚本。
    """
    argv = shlex.split(command.replace("\\", "/"))
    if not argv:
        return 127, "verify_cmd 为空"
    if argv[0] in {"python", "python3", "py"}:
        argv[0] = sys.executable
    return run_in(workdir, argv, timeout=600)


# --------------------------------------------------------------- 文件树与"别改考卷"


def tracked_files(root: Path, *, memory_dir: str | Path = MEMORY_DIRNAME) -> dict[str, bytes]:
    if not root.is_dir():
        return {}
    noise = noise_names(memory_dir)
    out: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in noise for part in path.parts) or path.suffix == ".pyc":
            continue
        out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def tree_hash(root: Path, *, memory_dir: str | Path = MEMORY_DIRNAME) -> str:
    digest = hashlib.sha256()
    for name, payload in tracked_files(root, memory_dir=memory_dir).items():
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()[:12]


def file_count(root: Path, *, memory_dir: str | Path = MEMORY_DIRNAME) -> int:
    return len(tracked_files(root, memory_dir=memory_dir))


def changed_in_subset(
    subset: str, baseline: Path, workdir: Path, *, memory_dir: str | Path = MEMORY_DIRNAME
) -> list[str]:
    """基线里就存在的文件被改动/删除的清单 —— 用来抓"改测试让测试变绿"。"""
    before = tracked_files(baseline / subset, memory_dir=memory_dir)
    after = tracked_files(workdir / subset, memory_dir=memory_dir)
    return sorted(name for name, payload in before.items() if after.get(name) != payload)


def verify_paths(
    workdir: Path,
    *,
    must_exist: Sequence[str] = (),
    must_absent: Sequence[str] = (),
) -> tuple[int, str]:
    """文件存在性判据。返回 (退出码, 明细) —— 与 `verify_nodes` 同一种形状，
    这样"要新建文件"的 greenfield 任务也能并进同一条**跑代码**的判定路径。
    """
    missing = [name for name in must_exist if not (workdir / name).exists()]
    present = [name for name in must_absent if (workdir / name).exists()]
    problems = [f"{name} 不存在" for name in missing] + [f"{name} 不该被创建" for name in present]
    detail = "; ".join(problems[:4]) if problems else (
        f"{len(must_exist)} 个应有文件都在" + (f"，{len(present)} 个禁建文件都没出现" if must_absent else "")
    )
    return (1 if problems else 0), detail


def subset_identical(subset: str, baseline: Path, workdir: Path, *, memory_dir: str | Path = MEMORY_DIRNAME) -> Check:
    """基线里就存在的文件必须逐字节不变。任务里明说"别动 tests/"的场合用它。"""
    broken = changed_in_subset(subset, baseline, workdir, memory_dir=memory_dir)
    detail = (
        f"{subset}/ 有 {len(broken)} 个原有文件被改动：{', '.join(broken[:3])}"
        if broken
        else f"{subset}/ 原有文件逐字节未变"
    )
    return Check(label=f"`{subset}/` 未被为了让测试变绿而改写", ok=not broken, detail=detail)


def subset_only_added(
    subset: str, baseline: Path, workdir: Path, *, memory_dir: str | Path = MEMORY_DIRNAME
) -> Check:
    """只许加不许删 —— 用于"补一个防回归测试"这类任务。

    字节级不变在这里太严：往既有的 `tests/test_x.py` 里加一个函数是完全正确的
    做法，判它失败等于逼模型去新建文件。这里改判"基线的每一行是否还在"，
    删断言、改断言、删文件都会被抓到。
    """
    before, after = (
        tracked_files(baseline / subset, memory_dir=memory_dir),
        tracked_files(workdir / subset, memory_dir=memory_dir),
    )
    problems: list[str] = []
    for name, payload in before.items():
        current = after.get(name)
        if current is None:
            problems.append(f"{name} 被删除")
            continue
        old = Counter(payload.decode("utf-8", "replace").splitlines())
        new = Counter(current.decode("utf-8", "replace").splitlines())
        lost = [line for line, count in old.items() if line.strip() and new.get(line, 0) < count]
        if lost:
            problems.append(f"{name} 少了 {len(lost)} 行，例如 {lost[0].strip()[:60]!r}")
    added = sum(before.get(name) is None for name in after)
    detail = "; ".join(problems[:3]) if problems else f"{subset}/ 基线内容逐行保留，新增 {added} 个文件"
    return Check(label=f"`{subset}/` 只增不删（没删断言换全绿）", ok=not problems, detail=detail)
