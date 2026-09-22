"""B6 验收证据生成器（SPEC v2 §3.6 · §4 验收线 B6）。

B6 的字面要求：**同一个任务在 local 与 docker 两个后端上，判定结论一致。**

这条比 B2/B3 便宜，而且便宜是有原因的：后端只改"命令在哪儿跑"，不改模型看到的东西
（system、工具 schema、上下文全都不变），所以 fake 引擎上跑出的**判定分歧**只可能是
后端自己的 bug —— 不像 B3 那样"fake 的 Δ 是我写的剧本的 Δ"。于是这里分两层：

一致性层（fake，确定性、随时可跑）
    两臂跑同一批题、同一份剧本，逐题比对 verdict / 终止原因 / 工具序列 / 每次执行的
    ok 标志。任何一处分歧都是一次可复现的后端缺陷。

沙箱层（本机跑不了，明说）
    这台开发机上**没有 docker**（`docker` 不在 PATH 里）。为了让"命令行拼成什么样、
    退出码与输出怎么回到工具层、两个后端是否共用同一个宿主侧影子 git"这几件事可比，
    脚本临时造一个会真的执行命令的 `docker` 替身（见 `DOCKER_STUB`）。它按 docker 的
    语义收命令行、只透传 `-e` 里显式给的键、把 `-v` 的宿主目录当作容器里的 `/work`，
    然后在宿主上跑那条命令。**它不是容器**：文件隔离、网络隔离、镜像内容这三件事
    一次也没被测过，结果文件里的 `container_isolation_tested` 就是 false。

顺带被这条线钉住的还有降级：`--backend docker` 而 docker 真的不可用时，`--require-chosen`
会让脚本直接失败，而不是悄悄跑成两臂 local 然后报告"完全一致"。那种"一致"是假的。

用法：
  PYTHONPATH="src;demos" python -X utf8 scripts/b6_backend_ab.py
  PYTHONPATH="src;demos" python -X utf8 scripts/b6_backend_ab.py --all
  PYTHONPATH="src;demos" python -X utf8 scripts/b6_backend_ab.py --engine live --repeats 2
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "demos")]

from miniclaude.cli import eval_cmd  # noqa: E402
from miniclaude.eval.runner import RunRecord  # noqa: E402
from miniclaude.infra.trace import replay  # noqa: E402

WORK = ROOT / "eval" / ".work" / "b6-ab"
RESULT = ROOT / "eval" / "results" / "b6-backend-ab.json"

# B6 默认只跑会真的执行命令的题：不 exec 任何命令的题在两臂之间没有可比的东西，
# 把它们算进分母会把一致率稀释成一个好看的空数。
DEFAULT_TAG = "bugfix"

# 替身的 argv 里出现这些就说明"容器命令行"确实被拼出来了，而不是被降级绕过了。
ISOLATION_FLAGS = ("--rm", "--network", "-v", "-w")

# 一个会真执行命令的假 docker。
#
# 它做四件真 docker 做的事：认 `version` 探测、按 `run` 的旗标解析出（挂载目录、工作目录、
# 显式环境变量、镜像、容器内命令）、**不带宿主环境**地起子进程、把子进程的退出码原样交回。
# 它不做的一件事是隔离 —— 子进程跑在宿主上。
DOCKER_STUB = r'''
import json
import os
import subprocess
import sys
from pathlib import Path

SELF = str(Path(sys.executable).resolve()).lower()


def log(payload):
    path = os.environ.get("MCC_DOCKER_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def parse(argv):
    """-> (workdir, 容器内工作目录, env, image, inner)。只认我们后端会拼的那几种旗标。

    `-v` 按**最后一个**冒号切：宿主路径在 Windows 上是 `D:\\...\\repo`，从前面切会得到
    host=`D` —— 于是 workdir 变成空、命令在替身的当前目录里跑，判定分歧看起来像
    "两个后端结论不同"，实际是替身把目录弄丢了。真 docker 认识盘符，替身必须认识得一样准。
    """
    workdir = ""
    windir = ""
    env = {}
    image = ""
    entrypoint = ""
    inner = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"--rm", "-i"}:
            pass
        elif token == "--name":
            index += 1
        elif token == "--network":
            index += 1
        elif token == "-e":
            index += 1
            key, _, value = argv[index].partition("=")
            env[key] = value
        elif token == "-v":
            index += 1
            host, _, container = argv[index].rpartition(":")
            if container.rstrip("/") == "/work":
                workdir = host
        elif token == "-w":
            index += 1
            windir = argv[index]
        elif token == "--entrypoint":
            index += 1
            entrypoint = argv[index]
        elif not image:
            image = token
        else:
            inner.append(token)
        index += 1
    if entrypoint:
        inner = [entrypoint, *inner]
    return workdir, windir, env, image, inner


def container_env(explicit):
    """容器视角的环境：只有 PATH 与显式 `-e`。宿主环境一条都不带 —— 那正是沙箱的卖点。"""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMPDIR": os.environ.get("TEMP", ""),
    }
    env.update(explicit)
    return {key: value for key, value in env.items() if value}


def main(argv):
    head = argv[:1]
    if head == ["version"]:
        log({"argv": argv, "probe": True})
        print("stub-server-0.0.0")
        return 0
    if head == ["rm"]:
        log({"argv": argv, "reaped": argv[2] if len(argv) > 2 else ""})
        return 0
    if head != ["run"]:
        print(f"stub docker 不认识的命令：{argv[:2]}", file=sys.stderr)
        return 2
    workdir, windir, explicit, image, inner = parse(argv[1:])
    # 这条记录是 B6 的前提证据：命令行拼对了还不够，命令得跑在该跑的那个目录里。
    log({
        "argv": argv,
        "run": True,
        "workdir": workdir,
        "windir": windir,
        "image": image,
        "inner": inner,
        "env_keys": sorted(explicit),
    })
    if not inner:
        return 0
    if not workdir:
        print("stub docker 没从 -v 里认出挂载目录，拒绝执行（否则会跑在错误的目录里）", file=sys.stderr)
        return 2
    if windir and windir != "/work":
        print(f"stub docker 只把宿主目录挂到 /work，收到 -w {windir}", file=sys.stderr)
        return 2
    if inner[0] in {"python3", "python"} or Path(inner[0]).resolve().lower() == SELF:
        inner = [sys.executable, *inner[1:]]
    elif inner[0] == "bash":
        shell = shutil.which("bash")
        if not shell:
            print("stub docker 需要 bash 来跑字符串命令，本机 PATH 里没有 bash", file=sys.stderr)
            return 127
        inner = [shell, *inner[1:]]
    done = subprocess.run(  # noqa: S603
        inner,
        cwd=workdir or None,
        env=container_env(explicit),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    sys.stdout.write(done.stdout or "")
    sys.stderr.write(done.stderr or "")
    return done.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001 - 替身崩了也要留话，不能只给一个退出码
        log({"crash": f"{type(exc).__name__}: {exc}"})
        print(f"stub docker 自己崩了：{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(70)
'''


def write_stub(root: Path, *, log: Path) -> dict[str, Path]:
    """把替身装进一个临时 bin 目录，返回 PATH 该用的目录与日志位置。

    `log` 必须由调用方指到 root **外面**：这个 bin 目录在 `--keep` 关掉时会被整个删掉，
    把 argv 证据放在里面，等于跑完就把证据烧了。
    """
    script = root / "docker_stub.py"
    script.write_text(DOCKER_STUB, encoding="utf-8")
    if os.name == "nt":
        (root / "docker.bat").write_text(
            f'@echo off\n"{Path(sys.executable)}" "{script}" %*\n', encoding="ascii"
        )
    else:
        shell = root / "docker"
        shell.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="ascii")
        shell.chmod(0o755)
    return {"bin": root, "log": log, "script": script}


def logged_calls(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _under(raw: str, root: Path) -> bool:
    """目录是不是在那一根下面。盘符大小写在 Windows 上不稳，所以两边都归一化。"""
    if not raw:
        return False
    try:
        Path(raw).resolve().relative_to(root)
    except (ValueError, OSError):
        return False
    return True


# ------------------------------------------------------------------ 跑一臂


def run_arm(name: str, out: Path, *, engine: str, repeats: int, tag: str, only: str, budget: int) -> dict[str, Any]:
    """跑一臂。`--backend` 是两臂唯一允许的差别，其余参数逐字相同。"""
    shutil.rmtree(out, ignore_errors=True)
    argv = [
        "--tasks", str(ROOT / "eval" / "tasks"), "--engine", engine,
        "--repeats", str(repeats), "--budget-tokens", str(budget),
        "--backend", name, "--out", str(out),
    ]
    if tag:
        argv += ["--tag", tag]
    if only:
        argv += ["--only", only]
    buf = io.StringIO()
    started = time.perf_counter()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = eval_cmd.run(argv)
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    runs = [RunRecord.from_dict(raw) for raw in payload["runs"]]
    traces = {}
    for run in runs:
        # 用 run 自己记的 trace_path，不照命名规则拼：规则改了这里会静默变成"两边都没 trace"，
        # 于是工具序列比对退化成"空 == 空"，那正是 B6 要防的那种假一致。
        path = run.trace_path
        traces[f"{run.task_id}.r{run.repeat}"] = replay(path) if path and Path(path).exists() else []
    summary = payload.get("summary", {})
    return {
        "argv": argv,
        "exit_code": code,
        "wall_seconds": round(time.perf_counter() - started, 1),
        "runs": runs,
        "traces": traces,
        # 报表里没有单一"score"字段，所以挑几个能被 B6 用到的：判定分布、token 与上下文峰值。
        "headline": {
            "verdicts": summary.get("verdicts"),
            "pass_at_1": summary.get("pass_at_1_raw"),
            "tokens_total": summary.get("tokens_total"),
            "context_peak_p95": summary.get("context_peak_p95"),
            "tool_error_rate": summary.get("tool_error_rate"),
        },
        "stdout": buf.getvalue(),
    }


def backend_events(arm: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for records in arm["traces"].values() for r in records if r.get("kind") == "backend"]


def chosen_backend(arm: dict[str, Any]) -> tuple[str, str]:
    """(这一臂实际用上的后端, 降级原因)。装配期那条 `backend` 事件是唯一产地。"""
    events = backend_events(arm)
    if not events:
        return "", "trace 里没有 backend 事件"
    names = {str(event.get("backend")) for event in events}
    degraded = sorted({str(event.get("degraded")) for event in events if event.get("degraded")})
    if len(names) > 1:
        return "|".join(sorted(names)), f"同一臂里出现了多个后端：{sorted(names)}"
    return names.pop(), "; ".join(degraded)


def call_sequence(records: list[dict[str, Any]]) -> list[tuple[str, bool]]:
    """(工具名, 是否成功) 的序列。两臂这个序列必须逐格相同 —— 它就是"判定一致"的粗判据。"""
    return [
        (str(record.get("name")), bool(record.get("ok")))
        for record in records
        if record.get("kind") == "tool_call"
    ]


def check_triples(run: RunRecord) -> list[tuple[str, bool, str]]:
    """(判据文字, 过没过, 说明)。B6 的原话是"verdict 与 Check 列表完全一致"，
    所以这里逐格比三元组，不比 dicts —— 少一格、多一格、翻一格都算分歧。"""
    return [
        (str(item.get("label")), bool(item.get("ok")), str(item.get("detail")))
        for item in run.checks
    ]


def compare(left: dict[str, Any], right: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """逐 (题, 次) 比对。返回 (分歧清单, 可比对的总格数)。"""
    a_runs = {run.task_id: run for run in left["runs"]}
    b_runs = {run.task_id: run for run in right["runs"]}
    keys = sorted(set(left["traces"]) | set(right["traces"]))
    divergent: list[dict[str, Any]] = []
    for key in keys:
        task_id = key.rsplit(".r", 1)[0]
        first, second = a_runs.get(task_id), b_runs.get(task_id)
        notes: list[str] = []
        if first is None or second is None:
            notes.append("某一臂没跑出这条 run")
        else:
            if first.verdict != second.verdict:
                notes.append(f"verdict {first.verdict} != {second.verdict}")
            if first.metrics.get("termination") != second.metrics.get("termination"):
                notes.append(
                    f"终止原因 {first.metrics.get('termination')} != {second.metrics.get('termination')}"
                )
            a_checks, b_checks = check_triples(first), check_triples(second)
            if a_checks != b_checks:
                # 判据条数与逐格三元组都进来说话：只有"哪一格不同"能告诉人去查判据还是去查后端。
                at = next(
                    (i for i, (x, y) in enumerate(zip(a_checks, b_checks)) if x != y),
                    min(len(a_checks), len(b_checks)),
                )
                notes.append(
                    f"Check 列表第 {at} 格起不同（条数 {len(a_checks)}/{len(b_checks)}）："
                    f" local={a_checks[at:at + 1]} docker={b_checks[at:at + 1]}"
                )
        a_calls = call_sequence(left["traces"].get(key, []))
        b_calls = call_sequence(right["traces"].get(key, []))
        if a_calls != b_calls:
            # 只报第一处不同 + 各自长度。把两条完整序列并排贴出来，读者要自己找差异，
            # 而"哪一格开始不同"才是修这条 bug 的入口。
            at = next((i for i, (x, y) in enumerate(zip(a_calls, b_calls)) if x != y), min(len(a_calls), len(b_calls)))
            notes.append(
                f"工具序列在第 {at} 格起不同：local {a_calls[at:at + 1]} vs docker {b_calls[at:at + 1]}"
                f"（长度 {len(a_calls)}/{len(b_calls)}）"
            )
        if notes:
            divergent.append({"run": key, "why": notes})
    return divergent, len(keys)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="b6_backend_ab", description=__doc__.splitlines()[0])
    parser.add_argument("--engine", choices=("fake", "live"), default="fake")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--tag", default=DEFAULT_TAG, help=f"默认只跑 {DEFAULT_TAG}（会执行命令的题）")
    parser.add_argument("--all", action="store_true", help="跑整个题集（等价于 --tag ''）")
    parser.add_argument("--only", default="", help="逗号分隔的任务 id")
    parser.add_argument("--budget-tokens", type=int, default=4_000_000)
    parser.add_argument(
        "--allow-degraded",
        action="store_true",
        help="允许 docker 臂降级成 local（默认拒绝：两臂同为 local 的'一致'是假的）",
    )
    parser.add_argument("--keep", action="store_true", help="保留 eval/.work/b6-ab 里的中间产物")
    args = parser.parse_args(list(argv) if argv is not None else None)

    tag = "" if args.all else args.tag
    WORK.mkdir(parents=True, exist_ok=True)
    stub_home = WORK / "stub-bin"
    stub_home.mkdir(parents=True, exist_ok=True)
    stub = write_stub(stub_home, log=WORK / "docker-calls.jsonl")
    stub["log"].unlink(missing_ok=True)

    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{stub_home}{os.pathsep}{old_path}"
    os.environ["MCC_DOCKER_LOG"] = str(stub["log"])
    # 真 docker 如果在，替身就轮不到它 —— 那这一臂测的就不是"我们造的这条路径"。
    shadowing = shutil.which("docker", path=old_path)
    try:
        local = run_arm("local", WORK / "local", engine=args.engine, repeats=args.repeats, tag=tag, only=args.only, budget=args.budget_tokens)
        docker = run_arm("docker", WORK / "docker", engine=args.engine, repeats=args.repeats, tag=tag, only=args.only, budget=args.budget_tokens)
    finally:
        os.environ["PATH"] = old_path
        os.environ.pop("MCC_DOCKER_LOG", None)
        if not args.keep:
            shutil.rmtree(stub_home, ignore_errors=True)

    local_backend, local_note = chosen_backend(local)
    docker_backend, docker_note = chosen_backend(docker)
    calls = logged_calls(stub["log"])
    runs = [call for call in calls if call.get("run")]
    version_probes = [call for call in calls if call.get("probe")]
    reaped = [call for call in calls if "reaped" in call]
    # 容器该落在哪：docker 臂每题一份隔离目录，都在这个根下面。
    docker_work = (WORK / "docker" / "work").resolve()
    stray = sorted(
        {
            str(call.get("workdir") or "")
            for call in runs
            if not _under(call.get("workdir") or "", docker_work)
        }
    )

    # 前提检查：不成立就不许产出"一致率"这个数字。
    premises: list[dict[str, Any]] = [
        {
            "claim": "docker 臂真的用上了 docker（没有静默降级）",
            "ok": docker_backend == "docker" and not docker_note,
            "detail": f"实际后端 {docker_backend or '未知'}" + (f" · {docker_note}" if docker_note else ""),
        },
        {
            "claim": "local 臂没有被替身污染",
            "ok": local_backend == "local" and not local_note,
            "detail": f"实际后端 {local_backend or '未知'}" + (f" · {local_note}" if local_note else ""),
        },
        {
            "claim": "替身确实被调用过（命令走过 docker 的 argv）",
            "ok": bool(runs),
            "detail": f"{len(runs)} 次 run、{len(version_probes)} 次探测",
        },
        {
            "claim": "容器命令行带着隔离旗标",
            "ok": bool(runs) and all(
                flag in call["argv"] for call in runs for flag in ISOLATION_FLAGS
            ),
            "detail": f"抽查 {len(runs)} 条 argv",
        },
        {
            "claim": "宿主解释器没有被写进容器命令（sys.executable → python3）",
            "ok": bool(runs) and all(Path(sys.executable).name not in " ".join(call["argv"]) for call in runs),
            "detail": "argv 里不应出现宿主 python 的文件名",
        },
        {
            # 这一条是第一次试跑逼出来的：`-v` 按第一个冒号切，Windows 的 `D:\\...` 当场把
            # 挂载目录切成空，于是"容器"在替身自己的 cwd 里跑了整套仓库测试 —— 表现出来
            # 像"两个后端结论不同"，其实两臂跑的根本不是同一份代码。
            "claim": "容器的工作目录就是这一题隔离出来的那份目录",
            "ok": bool(runs) and not stray,
            "detail": f"根 {docker_work}" + (f" · 越界 {len(stray)} 处：{stray[:2]}" if stray else " · 逐条对上"),
        },
        {
            "claim": "容器命令按 `/work` 进工作目录，环境变量只有点名的那些",
            "ok": bool(runs)
            and all(call.get("windir") in {"", "/work"} for call in runs)
            and all(set(call.get("env_keys") or []) <= {"PYTHONDONTWRITEBYTECODE"} for call in runs),
            "detail": "宿主 env 一条都不该透传进容器",
        },
    ]
    if shadowing:
        premises.append(
            {
                "claim": "本机没有真 docker 抢在替身之前",
                "ok": False,
                "detail": f"PATH 上已经有一个 {shadowing}：这一臂跑的可能是真容器，替身的 argv 断言不适用",
            }
        )
    divergent, compared = compare(local, docker)
    premises.append(
        {
            "claim": "两臂可比对的 run 数不为零",
            "ok": compared > 0,
            "detail": f"{compared} 格",
        }
    )

    # 降级的那一臂里两臂同为 local，比出来的 100% 与后端无关 —— 所以那个数**不产出**，
    # 而不是"产出但标一下"。要拿到 rate 就必须让 docker 臂真的用上 docker。
    degraded_docker = docker_backend != "docker"
    rate_suppressed = degraded_docker and not args.allow_degraded

    consistent = compared - len(divergent)
    payload: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "acceptance": "B6",
        "spec": "§3.6",
        "engine": args.engine,
        "repeats": args.repeats,
        "tag": tag or "（整个题集）",
        "arms": {
            "local": {"backend": local_backend, "degraded": local_note or None, "exit_code": local["exit_code"],
                      "wall_seconds": local["wall_seconds"], "headline": local["headline"],
                      "runs": {run.task_id: run.verdict for run in local["runs"]}},
            "docker": {"backend": docker_backend, "degraded": docker_note or None, "exit_code": docker["exit_code"],
                       "wall_seconds": docker["wall_seconds"], "headline": docker["headline"],
                       "runs": {run.task_id: run.verdict for run in docker["runs"]}},
        },
        "verdict": "",
        "agreement": {
            "compared": compared,
            "consistent": consistent,
            "divergent": divergent,
            "rate": None if rate_suppressed else (round(consistent / compared, 4) if compared else None),
            "rate_suppressed": rate_suppressed,
        },
        "premises": premises,
        "docker_argv_sample": runs[0]["argv"] if runs else [],
        "docker_launches": len(runs),
        "docker_probes": len(version_probes),
        # 超时后被 `docker rm -f` 收掉的容器数。非零不是失败（两臂都会拿到同一个"超时"结论），
        # 但它是"这条路径真跑过、且残留有兜底"的旁证，所以进结果文件。
        "docker_reaps": len(reaped),
        # 这一行就是本脚本的全部诚实所在：被证明的是命令行与判定，不是沙箱。
        "container_isolation_tested": False,
        "what_this_proves": (
            "同一批任务在两个后端上判定逐格相同，且 docker 臂真的走完了我们拼的那条命令行"
            "（--rm / --network none / -v 宿主:/work / 宿主解释器换成 python3 / 只透传 -e）。"
        ),
        "what_this_does_not_prove": (
            f"替身是在宿主上执行命令的假 docker（本机没有 docker：{shadowing or 'PATH 里找不到'}）。"
            "文件隔离、网络隔离、镜像里装了什么，全都没测过；"
            "真 docker 到位后应当原样重跑本脚本，届时的差异只会来自沙箱本身。"
        ),
    }
    payload["verdict"] = (
        "pass" if all(item["ok"] for item in premises) and not divergent else "fail"
    )

    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"两臂实际后端：local={local_backend or '?'} docker={docker_backend or '?'} · "
        f"替身 run {len(runs)} 次 · 超时清理 {len(reaped)} 次"
    )
    for item in premises:
        print(f"  [{'x' if item['ok'] else ' '}] {item['claim']} —— {item['detail']}")
    rate = payload["agreement"]["rate"]
    print(
        f"判定一致率：{consistent}/{compared}"
        + (f"（{rate * 100:.1f}%）" if rate is not None else " —— 已抑制（docker 臂降级，两臂同为 local）")
    )
    for row in divergent[:10]:
        print(f"  · {row['run']}：" + "；".join(row["why"]))
    print(f"结果写入 {RESULT.relative_to(ROOT)}")
    if docker_backend != "docker" and not args.allow_degraded:
        print(
            "docker 臂没能用上 docker —— 两臂同为 local 的\"一致\"没有意义。"
            "要看降级长什么样，加 --allow-degraded 重跑。",
            file=sys.stderr,
        )
    return 0 if payload["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
