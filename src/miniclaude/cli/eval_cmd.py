"""`mcc eval` —— 跑批评测的入口。和 REPL 一样只是装配，不含判定逻辑。

放这里而不是塞进 `main.py` 的 argparse：评测有一整套自己的开关（题集、重复数、
预算、基线），混进交互命令的帮助里会把两边都读不清。分流时**延迟 import** ——
`eval.runner` 反过来要用 `cli.main.build_session`，顶层 import 会成环。

退出码说的是"这批能不能拿去汇报"，不是"题做对了没有"：

* `0` 跑完了，没有 error/aborted，没有退步，负样本按设计判红；
* `1` 有运行崩了 / 有该做对的题没做对 / 检出退步或判据告警；
* `2` 用法或配置不对（题集加载失败、live 模式缺 `.env`）—— 这种一格数据都没产生。

`--engine fake` 是默认值，且**不读 `.env`**：fake 引擎跑的是真工具、真落盘、真判据，
只是不打网络，所以没有密钥的机器（CI、别人的笔记本）也能把全批秒级跑完。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

from miniclaude.config import Config, ConfigError, get_config
from miniclaude.eval import regression
from miniclaude.eval.taskset import TaskSet, TaskSetError

SMOKE_TASKS = 6  # SPEC v2 §7.1 行 9 的"6 任务 live 冒烟"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcc eval", description="跑评测批次：fake 秒级回归 / live 真实模型。")
    parser.add_argument("--tasks", type=Path, default=None, help="任务目录，默认 <仓库根>/eval/tasks")
    parser.add_argument("--engine", choices=("fake", "live"), default="fake", help="fake 不打网络、不读 .env")
    parser.add_argument("--repeats", type=int, default=3, help="每题重复次数（pass@k 的 k）")
    parser.add_argument("--only", default="", help="只跑这些任务 id，逗号分隔")
    parser.add_argument("--tag", default="", help="只跑带这些标签的任务，逗号分隔")
    parser.add_argument("--budget-tokens", type=int, default=4_000_000, help="整批 token 硬上限，用尽即停")
    parser.add_argument("--out", type=Path, default=None, help="输出目录，默认 eval/.work/<engine>")
    parser.add_argument("--no-resume", action="store_true", help="不复用 manifest 里已完成的 run")
    parser.add_argument("--baseline", type=Path, default=None, help="对比这份基线（默认自动找题集哈希匹配的那份）")
    parser.add_argument("--baselines-dir", type=Path, default=None, help="基线目录，默认 <仓库根>/eval/baselines")
    parser.add_argument("--save-baseline", action="store_true", help="跑完把本批成绩写成 <engine>-<题集哈希>.json")
    parser.add_argument("--force", action="store_true", help="即使基线不可比也照跑（默认拒绝，避免白花时间与额度）")
    # 阶梯 A/B 用：B2 要求"关掉阶梯会失败、开着能通过"，两臂只能差这一个开关。
    parser.add_argument("--no-compact", action="store_true", help="关闭上下文压缩阶梯（阶梯对照的对照组）")
    parser.add_argument(
        "--context-budget",
        type=int,
        default=None,
        help="覆盖 TOKEN_BUDGET（阶梯的触发刻度）；不想等真长任务时用它把两臂拉开",
    )
    parser.add_argument(
        "--context-hard-limit",
        type=int,
        default=None,
        help="覆盖 CONTEXT_HARD_LIMIT（端点拒收前的止损线）：对照臂要靠它把'不压缩就撞墙'跑出来",
    )
    parser.add_argument("--lint", action="store_true", help="只做考题自检（判据可能为真的题）后退出")
    parser.add_argument("--list", dest="as_list", action="store_true", help="列出任务后退出")
    parser.add_argument(
        "--smoke",
        nargs="?",
        type=int,
        const=SMOKE_TASKS,
        default=None,
        help=f"live 冒烟：只跑 supports_live 的前 N 题（默认 {SMOKE_TASKS}），repeats 置 1",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每一轮的细节")
    return parser


def run(argv: Sequence[str], config: Config | None = None) -> int:
    from miniclaude.eval.runner import EvalRunner  # 延迟 import：见模块开头

    args = build_parser().parse_args(list(argv))
    engine = "live" if args.smoke is not None else args.engine
    try:
        tasks = _load_tasks(args)
        out = Path(args.out) if args.out else _repo_root() / "eval" / ".work" / engine
    except (TaskSetError, FileNotFoundError) as exc:
        print(f"题集加载失败：{exc}", file=sys.stderr)
        return 2

    if args.as_list:
        return _print_list(tasks)

    if args.lint:
        return _lint(tasks)

    try:
        selected = _select(args, tasks, engine=engine)
    except TaskSetError as exc:
        print(f"筛选后没有任务：{exc}", file=sys.stderr)
        return 2
    repeats = 1 if args.smoke is not None else args.repeats

    live_config = config
    if engine == "live" and live_config is None:
        try:
            live_config = get_config()
        except ConfigError as exc:
            print(f"live 引擎需要完整的模型配置：{exc}", file=sys.stderr)
            return 2

    try:
        baseline, baseline_path, skipped = _pick_baseline(args, selected, all_tasks=tasks)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"基线读不了：{exc}", file=sys.stderr)
        return 2

    if baseline is not None:
        refused = [item for item in regression.taskset_gate(baseline, selected) if item.blocker]
        for item in refused:
            print(f"- {item.line()}", file=sys.stderr)
        if refused and not args.force:
            print(
                "已拒绝开跑：这一批的分数无法与基线对比（--force 可强行跑，报表照样标'无法对比'）",
                file=sys.stderr,
            )
            return 2

    tuned = bool(args.no_compact or args.context_budget or args.context_hard_limit)
    if tuned and args.save_baseline and not args.force:
        print(
            "已拒绝：--no-compact / --context-budget / --context-hard-limit 改过阶梯，"
            "这批不能当基线入库（确实要就加 --force）",
            file=sys.stderr,
        )
        return 2

    runner = EvalRunner(
        tasks=selected,
        out=out,
        repeats=repeats,
        engine=engine,
        budget_tokens=args.budget_tokens,
        resume=not args.no_resume,
        verbose=args.verbose,
        on_line=print,
        config=live_config,
        baseline=baseline,
        context_compact=False if args.no_compact else None,
        context_budget=args.context_budget,
        context_hard_limit=args.context_hard_limit,
    )
    scope = (
        f"{len(selected)} 题 × {repeats} 次"
        if len(selected) == len(tasks)
        else f"{len(selected)}/{len(tasks)} 题 × {repeats} 次（子集）"
    )
    print(f"评测开始：{scope} · engine={engine} · 输出 {out}")
    if tuned:
        # 阶梯两臂只能差一个开关，所以被拧过的批次必须自报家门（SPEC v2 §3.3 B2）。
        if args.no_compact:
            ladder = "已关闭"
        else:
            ladder = (
                f"开，预算 {args.context_budget:,} tokens"
                if args.context_budget
                else "开，默认预算"
            )
        if args.context_hard_limit:
            ladder += f" · 熔断线 {args.context_hard_limit:,} tokens"
        print(f"上下文阶梯：{ladder} —— 这批的分数不与默认配置批混读")
    if baseline_path is not None:
        print(f"基线：{baseline_path}")
    report = runner.run()
    return _finish(report, runner, args, baseline_path, skipped)


# ------------------------------------------------------------------ 输出


def _finish(
    report: Any, runner: "EvalRunner", args: argparse.Namespace, baseline_path: Path | None, skipped: str = ""
) -> int:
    summary = report.summary
    print("")
    print(
        f"runs={summary.get('runs')} · verdicts={summary.get('verdicts')} · "
        f"pass@1={summary.get('pass_at_1_raw')}"
        + (f" · pass@{summary.get('repeats')}={summary.get('pass_at_k_raw')}" if (summary.get("repeats") or 1) > 1 else "")
    )
    spent, budget = summary.get("tokens_total", 0) or 0, summary.get("budget_tokens", 0) or 0
    print(
        f"tokens={spent:,} / 预算 {budget:,}"
        + (f"（超出 {spent - budget:,}：闸在任务边界生效，最后一题已经开跑）" if budget and spent > budget else "")
        + f" · 墙钟 p50={summary.get('wall_time_p50_ms'):,}ms p95={summary.get('wall_time_p95_ms'):,}ms"
    )
    if not report.regressions and baseline_path is None:
        # "没有差异"与"没做对比"是两件事：只报前者，读者会以为已经比过了。
        print(f"（未与基线对比：{skipped or '没给 --baseline，eval/baselines/ 里也没有匹配的文件'}）")
    for item in report.regressions:
        print(f"- {item.line()}")

    troubled = _trouble(report, runner)
    for line in troubled:
        print(f"· {line}")
    print(f"\n报表：{runner.out / 'report.md'}")
    print(f"明细：{runner.out / 'report.json'} · 逐条记录：{runner.manifest}")

    if args.save_baseline:
        path = regression.save_baseline(
            regression.baseline_from_report(report),
            _baselines_dir(args) / f"{runner.engine}-{report.taskset_sha}.json",
        )
        print(f"基线已写入：{path}")

    if any(item.blocker for item in report.regressions):
        return 1
    return 1 if troubled else 0


def _trouble(report: Any, runner: "EvalRunner") -> list[str]:
    """把"这批不该拿去汇报"说成一句人话。`must-fail` 按设计判红，所以不能只看 fail 数量。"""
    lines: list[str] = []
    crashed = [run for run in report.runs if run.verdict in ("error", "aborted")]
    for run in crashed[:6]:
        lines.append(f"{run.task_id} r{run.repeat} 判定 {run.verdict}：{run.error or '批次中止'}")
    must_fail = {task.id for task in runner.tasks if regression.MUST_FAIL_TAG in task.tags}
    soft = [
        run.task_id
        for run in report.first_per_task()
        if run.verdict != "pass" and run.task_id not in must_fail
    ]
    if soft:
        lines.append("这些题按判据没做对：" + ", ".join(soft))
    over = [run.task_id for run in report.runs if run.over_token_ceiling]
    if over:
        lines.append("超出单题 token 上限：" + ", ".join(sorted(set(over))))
    return lines


def _print_list(tasks: TaskSet) -> int:
    for task in tasks:
        print(
            f"{task.id:34s} d{task.difficulty} {task.mode:8s} live={'y' if task.supports_live else 'n'} "
            f"f2p={len(task.fail_to_pass)} p2p={len(task.pass_to_pass)} fake={task.fake.driver}"
        )
    print(f"共 {len(tasks)} 题 · 题集哈希 {tasks.sha}")
    return 0


def _lint(tasks: TaskSet) -> int:
    """考题自检：坏题不报错，只会变成一个看起来正常的 pass 率。所以开跑前值得单独问一次。"""
    from miniclaude.eval import judge as judge_mod

    bad = 0
    quiet = 0
    for task in tasks:
        checks = judge_mod.lint_task(task, task.source.resolve(tasks.repo_root))
        if not checks:
            quiet += 1
        for check in checks:
            if not check.ok:
                bad += 1
                print(f"[坏题] {task.id}：{check.label} —— {check.detail}")
    print(f"自检结束：{len(tasks)} 题，{bad} 条判据有问题，{quiet} 题没有用例级判据（靠 probe/verify_cmd/答复关键字）")
    return 1 if bad else 0


# ------------------------------------------------------------------ 参数


def _load_tasks(args: argparse.Namespace) -> TaskSet:
    root = Path(args.tasks) if args.tasks else _repo_root() / "eval" / "tasks"
    return TaskSet.load(root)


def _select(args: argparse.Namespace, tasks: TaskSet, *, engine: str) -> TaskSet:
    """把 `--only` / `--tag` / `--smoke` / live 自动跳过合成一个题集。

    live 批次默认只跑 `supports_live` 的题：负样本靠 fake 就能稳定复现，用真模型
    重跑它们既不改变结论又要花钱。
    """
    ids = [part.strip() for part in args.only.split(",") if part.strip()]
    tags = [part.strip() for part in args.tag.split(",") if part.strip()]
    if args.smoke is not None:
        ids = [task.id for task in tasks if task.supports_live][: max(1, args.smoke)]
    elif engine == "live":
        skipped = [task.id for task in tasks if not task.supports_live]
        if skipped:
            print(f"跳过 {len(skipped)} 道 supports_live=false 的题（负样本靠 fake 复现，不花额度）")
        ids = [task.id for task in tasks if task.supports_live]
    if ids or tags:
        return tasks.filtered(ids=ids, tags=tags)
    return tasks


def _baselines_dir(args: argparse.Namespace) -> Path:
    return Path(args.baselines_dir) if args.baselines_dir else _repo_root() / "eval" / "baselines"


def _pick_baseline(
    args: argparse.Namespace, tasks: TaskSet, *, all_tasks: TaskSet
) -> tuple[dict[str, Any] | None, Path | None, str]:
    """给了 `--baseline` 就用它；否则按**题集哈希**在基线目录里找。

    只按哈希匹配，不做"挑最新的那份"这种好事 —— 哈希对不上的基线比没有基线更危险，
    它给出的 Δ 是错的，而报表看上去像是比过了。

    子集批次（`--smoke` / `--only`）不自动配基线：基线是全量考卷的成绩，逐题配对会
    产出十几行"这批没跑到"，把一次正常的冒烟报成退步。要对比就显式 `--baseline`。
    """
    if args.baseline:
        path = Path(args.baseline)
        return regression.load_baseline(path), path, ""
    if len(tasks) != len(all_tasks):
        return None, None, f"这是 {len(tasks)}/{len(all_tasks)} 题的子集，不自动配全量基线；要对比请显式 --baseline"
    directory = _baselines_dir(args)
    if not directory.is_dir():
        return None, None, "eval/baselines/ 还不存在"
    for file in sorted(directory.glob("*.json"), reverse=True):
        try:
            raw = regression.load_baseline(file)
        except (ValueError, OSError):  # 手写/半截的基线文件：跳过它，别拿它当对比对象
            continue
        if str(raw.get("taskset_sha") or "") == tasks.sha:
            return raw, file, ""
    return None, None, "没给 --baseline，eval/baselines/ 里也没有题集哈希匹配的文件"


def _repo_root() -> Path:
    """题集与 fixtures 的根。先认 cwd（仓库里跑），再退到包的位置（安装后跑）。"""
    cwd = Path.cwd().resolve()
    if (cwd / "eval" / "tasks").is_dir():
        return cwd
    here = Path(__file__).resolve().parents[3]
    if (here / "eval" / "tasks").is_dir():
        return here
    raise TaskSetError("找不到 eval/tasks：请用 --tasks 指定，或在本仓库目录下运行")


__all__ = ["build_parser", "run"]
