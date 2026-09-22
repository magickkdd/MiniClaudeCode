"""跑批器 —— 把任务集按重复数跑完、边跑边落盘、随时可续、到预算就停。

四个设计决定都是**代价换确定性**，写在这里免得日后被"顺手优化"掉：

* `workers=1`：并行打同一端点会把限流抖动混进"配置 A vs 配置 B"的比较里。
* **每条 run 一落盘就 append 到 `manifest.jsonl`**：断点续跑靠的是这行，
  不是靠跑完再写。72 次 live 跑到第 50 次崩了要能接着跑，这是 B1 的字面要求。
* **批次预算** `budget_tokens` 是整批硬上限，与单任务的 `token_ceiling` 不是一回事。
  没有它，一次失手的 live 批次能在一夜之间把预算花掉两倍。
* **失败关闭**：批跑没有人类确认者，`confirmer` 恒返回"拒绝"，权限模式取
  `AUTO`/`READONLY`（路径锁死在工作副本），**永远不开 ASK** —— 开了就是拿一个
  没人回答的问题挂住整批评测。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from miniclaude.agent.permissions import Answer, PermissionMode
from miniclaude.agent.state import AgentResult
from miniclaude.cli.main import build_session
from miniclaude.cli.render import Renderer
from miniclaude.config import Config
from miniclaude.eval import drivers, judge as judge_mod, regression
from miniclaude.eval.contract import Context, isolate
from miniclaude.eval.metrics import per_tag, summarize_batch
from miniclaude.eval.taskset import TaskInstance, TaskSet
from miniclaude.infra.trace import summarize
from miniclaude.llm.openai_compat import OpenAICompatClient

VERDICTS = ("pass", "fail", "error", "aborted")


@dataclass
class RunRecord:
    """一次运行的全部事实。报表与基线文件里的每一行都是它。"""

    task_id: str
    repeat: int
    engine: str
    verdict: str
    trace_path: Path | None
    metrics: dict[str, Any] = field(default_factory=dict)
    failure_modes: list[str] = field(default_factory=list)
    wall_ms: int = 0
    cost_est: float | None = None
    taskset_sha: str = ""
    tags: tuple[str, ...] = ()
    checks: list[dict[str, Any]] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)
    error: str = ""
    over_token_ceiling: bool = False

    @property
    def tokens(self) -> int:
        return int(self.metrics.get("tokens") or 0)

    def metric(self, key: str) -> int:
        return int(self.metrics.get(key) or 0)

    @property
    def key(self) -> tuple[str, int]:
        return (self.task_id, self.repeat)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "repeat": self.repeat,
            "engine": self.engine,
            "verdict": self.verdict,
            "trace_path": str(self.trace_path) if self.trace_path else None,
            "metrics": self.metrics,
            "failure_modes": self.failure_modes,
            "wall_ms": self.wall_ms,
            "cost_est": self.cost_est,
            "taskset_sha": self.taskset_sha,
            "tags": list(self.tags),
            "checks": self.checks,
            "broken": self.broken,
            "error": self.error,
            "over_token_ceiling": self.over_token_ceiling,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RunRecord":
        path = raw.get("trace_path")
        return cls(
            task_id=str(raw["task_id"]),
            repeat=int(raw.get("repeat", 0)),
            engine=str(raw.get("engine", "unknown")),
            verdict=str(raw.get("verdict", "error")),
            trace_path=Path(path) if path else None,
            metrics=dict(raw.get("metrics") or {}),
            failure_modes=list(raw.get("failure_modes") or []),
            wall_ms=int(raw.get("wall_ms") or 0),
            cost_est=raw.get("cost_est"),
            taskset_sha=str(raw.get("taskset_sha") or ""),
            tags=tuple(raw.get("tags") or ()),
            checks=list(raw.get("checks") or []),
            broken=list(raw.get("broken") or []),
            error=str(raw.get("error") or ""),
            over_token_ceiling=bool(raw.get("over_token_ceiling")),
        )


@dataclass
class BatchReport:
    runs: list[RunRecord]
    summary: dict[str, Any]
    per_tag: dict[str, dict[str, Any]]
    regressions: list[Any] = field(default_factory=list)
    taskset_sha: str = ""
    out: Path | None = None

    def verdict_of(self, task_id: str, repeat: int = 0) -> str:
        for run in self.runs:
            if run.task_id == task_id and run.repeat == repeat:
                return run.verdict
        return "missing"

    def first_per_task(self) -> list[RunRecord]:
        return [run for run in self.runs if run.repeat == 0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskset_sha": self.taskset_sha,
            "summary": self.summary,
            "per_tag": self.per_tag,
            "regressions": [item.to_dict() for item in self.regressions],
            "runs": [run.to_dict() for run in self.runs],
        }


class EvalRunner:
    def __init__(
        self,
        *,
        tasks: TaskSet,
        out: Path,
        repeats: int = 3,
        workers: int = 1,
        engine: str = "live",
        budget_tokens: int = 4_000_000,
        resume: bool = True,
        only: Sequence[str] = (),
        verbose: bool = False,
        on_line: Callable[[str], None] | None = None,
        config: Config | None = None,
        baseline: dict[str, Any] | None = None,
    ) -> None:
        if engine not in ("live", "fake"):
            raise ValueError(f"engine 只能是 live/fake，收到 {engine!r}")
        if workers != 1:
            raise ValueError(
                "workers 只能是 1：并行会把同一端点的限流抖动混进配置对比里，"
                "先串行拿干净数据（SPEC v2 §3.2）"
            )
        if repeats < 1:
            raise ValueError("repeats 至少 1")
        self.tasks = tasks.filtered(ids=only) if only else tasks
        self.out = Path(out)
        self.repeats = repeats
        self.engine = engine
        self.budget_tokens = budget_tokens
        self.resume = resume
        self.verbose = verbose
        self.emit = on_line or (lambda text: None)
        self.config = config
        self.baseline = baseline
        self.spent_tokens = 0
        self.aborted = False
        self.manifest = self.out / "manifest.jsonl"

    # ------------------------------------------------------------- 主流程

    def run(self) -> BatchReport:
        (self.out / "traces").mkdir(parents=True, exist_ok=True)
        (self.out / "work").mkdir(parents=True, exist_ok=True)
        runs: list[RunRecord] = []
        done: dict[tuple[str, int], RunRecord] = self.load_manifest() if self.resume else {}
        if done:
            self.emit(f"续跑：manifest 里已有 {len(done)} 条记录，直接复用")

        for repeat in range(self.repeats):
            for task in self.tasks:
                key = task.id, repeat
                if key in done:
                    record = done[key]
                    if record.verdict != "aborted":
                        runs.append(record)
                    continue
                if self.spent_tokens >= self.budget_tokens:
                    self.aborted = True
                    break
                record = self.run_task(task, repeat)
                runs.append(record)
                self.spent_tokens += record.tokens
        if self.aborted:
            self.emit(f"批次预算 {self.budget_tokens:,} tokens 已用尽（实际 {self.spent_tokens:,}），剩余任务未跑")

        summary = summarize_batch(runs)
        summary["budget_tokens"] = self.budget_tokens
        summary["tokens_spent"] = self.spent_tokens
        summary["aborted_batch"] = self.aborted
        summary["taskset_sha"] = self.tasks.sha
        summary["engine"] = [self.engine]
        summary["resumed"] = len(done)
        report = BatchReport(
            runs=runs,
            summary=summary,
            per_tag=per_tag(runs),
            taskset_sha=self.tasks.sha,
            out=self.out,
        )
        # 对比要在落盘**之前**做：报表文件是这批评测对外的唯一凭据，
        # 退步结论只打在终端上等于没跑。
        report.regressions = regression.instrument_checks(runs)  # 不需要基线，无条件跑
        if self.baseline is not None:
            report.regressions += regression.compare(self.baseline, report)
        # 同一份结论进 summary：CI 里 `jq -e '.summary.regressions == []'` 就能拦住退步，
        # 不用去解析 markdown 报表。
        summary["regressions"] = [item.to_dict() for item in report.regressions]
        self.write_report(report)
        return report

    def run_task(self, task: TaskInstance, repeat: int) -> RunRecord:
        baseline = task.source.resolve(self.tasks.repo_root)
        workdir = isolate(baseline, self.out / "work" / f"{task.id}.r{repeat}")
        trace = self.out / "traces" / f"{task.id}.r{repeat}.{self.engine}.jsonl"
        trace.unlink(missing_ok=True)

        lines: list[str] = []
        renderer = Renderer(verbose=self.verbose, write=lines.append, use_rich=False)
        config = self.config_for(task, workdir, trace)
        try:
            session = build_session(
                config=config,
                mode=PermissionMode.READONLY if task.is_readonly else PermissionMode.AUTO,
                verbose=self.verbose,
                renderer=renderer,
                confirmer=lambda name, summary: Answer.NO,  # 没人能回答 → 失败关闭，不挂住
                llm=self.llm_for(config, task, workdir),
                use_rich=False,
            )
        except Exception as exc:  # noqa: BLE001 - 装配失败也是这道题的结果
            return self.record(task, repeat, verdict="error", trace=None, error=f"装配失败 {type(exc).__name__}: {exc}")

        started = time.perf_counter()
        try:
            result: AgentResult = session.agent.run(task.instruction, task_id=task.id)
        except Exception as exc:  # noqa: BLE001 - 一次崩掉不能带走整批
            wall = int((time.perf_counter() - started) * 1000)
            record = self.record(task, repeat, verdict="error", trace=trace, error=f"{type(exc).__name__}: {exc}")
            record.wall_ms = wall
            self.append_manifest(record)
            return record

        wall_ms = int((time.perf_counter() - started) * 1000)
        ctx = Context(engine=self.engine, workdir=workdir, baseline=baseline, result=result, console=lines)
        judgement = judge_mod.judge(task, ctx)
        stats = summarize(trace) if trace.exists() else {}
        record = RunRecord(
            task_id=task.id,
            repeat=repeat,
            engine=self.engine,
            verdict=judgement.verdict,
            trace_path=trace,
            metrics={
                key: stats.get(key)
                for key in (
                    "turns", "tool_calls", "tool_executed", "tool_errors", "repeated_calls", "stalled_groups",
                    "denied_actions", "tokens", "context_peak_tokens", "wall_ms", "output_chars",
                    "omitted_output_chars", "termination",
                )
                if key in stats
            },
            failure_modes=list(stats.get("failure_modes") or result.failure_modes),
            wall_ms=wall_ms,
            cost_est=result.cost_est,
            taskset_sha=self.tasks.sha,
            tags=task.tags,
            checks=judgement.to_dict()["checks"],
            broken=judgement.broken,
            over_token_ceiling=bool(task.token_ceiling and int(stats.get("tokens") or 0) > task.token_ceiling),
        )
        self.append_manifest(record)
        self.emit(f"{task.id} r{repeat} → {record.verdict} · {record.metric('turns')} 轮 · {wall_ms} ms")
        return record

    # ------------------------------------------------------------- 断点与配置

    def load_manifest(self) -> dict[tuple[str, int], RunRecord]:
        """读回已完成的 run。哈希对不上的记录**不复用** —— 考卷变了，旧答案没意义。"""
        out: dict[tuple[str, int], RunRecord] = {}
        if not self.manifest.is_file():
            return out
        stale = 0
        for line in self.manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = RunRecord.from_dict(json.loads(line))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                stale += 1
                continue
            if record.taskset_sha and record.taskset_sha != self.tasks.sha:
                stale += 1
                continue
            out[record.key] = record
        if stale:
            self.emit(f"manifest 里有 {stale} 条记录与当前任务集哈希不符，已忽略（不冒充新数据）")
        return out

    def append_manifest(self, record: RunRecord) -> None:
        with self.manifest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            handle.flush()

    def config_for(self, task: TaskInstance, workdir: Path, trace: Path) -> Config:
        base = self.config or (
            Config.from_env(self.tasks.repo_root / ".env")
            if self.engine == "live"
            else Config(
                base_url="http://fake.invalid/v1",
                api_key="sk-fake-not-a-real-key-000000",
                model="fake-llm(scripted)",
                project_root=self.tasks.repo_root,
            )
        )
        # 只能从已有配置派生：`Config.redacted()` 会把 api_key 掩码，拿它重建一份
        # 就等于给真实引擎装上一把假钥匙 —— 那种失败会以"网络错误"的形式出现，
        # 排查起来完全看不出根因。
        return replace(base, project_root=workdir, trace_path=trace, max_turns=task.max_turns)

    def llm_for(self, config: Config, task: TaskInstance, workdir: Path) -> Any:
        if self.engine == "fake":
            from fakes import FakeLLM  # 剧本用真工具真落盘，只是不打网络

            return FakeLLM(drivers.build(task, workdir))
        return OpenAICompatClient(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            max_tokens=config.max_tokens,
            timeout=config.request_timeout,
        )

    def record(self, task: TaskInstance, repeat: int, **kwargs: Any) -> RunRecord:
        record = RunRecord(
            task_id=task.id,
            repeat=repeat,
            engine=self.engine,
            verdict=kwargs.pop("verdict", "error"),
            trace_path=kwargs.pop("trace", None),
            taskset_sha=self.tasks.sha,
            tags=task.tags,
            **kwargs,
        )
        self.append_manifest(record)
        return record

    def write_report(self, report: BatchReport) -> None:
        (self.out / "report.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.out / "report.md").write_text(self.markdown(report), encoding="utf-8")

    def markdown(self, report: BatchReport) -> str:
        """报表。**先给 CI 与样本量，再给点估计** —— 24 道题的通过率没有区间就是噪声。"""
        summary = report.summary
        low, high = (summary.get("success_rate_ci") or [0.0, 1.0])[:2]
        spent, budget = summary.get("tokens_spent", 0) or 0, summary.get("budget_tokens", 0) or 0
        if summary.get("aborted_batch"):
            budget_note = "；**预算用尽，剩余任务未跑**（ABORTED_BATCH）"
        elif budget and spent > budget:
            # 闸只在任务边界生效：最后一题已经开跑就得让它跑完。不说破的话，
            # "313,812 / 300,000" 看起来像闸坏了，而不是像设计如此。
            budget_note = f"；超出 {spent - budget:,}（闸在任务边界生效，最后一题已经开跑）"
        else:
            budget_note = ""
        repeats = summary.get("repeats")
        rates = f"pass@1 = {summary.get('pass_at_1_raw')}"
        if repeats and repeats > 1:
            rates += f"，pass@{repeats} = {summary.get('pass_at_k_raw')}"
        else:
            rates += "（repeats=1，pass@k 与它同一份，不重复列）"
        lines = [
            "# 评测批次报表",
            "",
            f"- 任务集哈希：`{report.taskset_sha}` · 引擎 {'/'.join(summary.get('engine') or [])}"
            f" · 重复 {repeats} × {summary.get('tasks')} 任务 = {summary.get('runs')} 次运行",
            f"- 批次预算 {budget:,} tokens，实际花费 {spent:,} tokens{budget_note}",
            f"- {rates}",
            f"- 单次成功率 Wilson 95% CI：[{low:.3f}, {high:.3f}]"
            + ("（区间宽到容得下两种相反的结论，别拿点估计说话）" if high - low > 0.25 else ""),
            "",
            "| 任务 | 重复 | 判定 | 轮数 | 工具(报错) | 终止 | tokens | 失败模式 | 红掉的用例 |",
            "|---|---:|---|---:|---|---|---:|---|---|",
        ]
        for run in report.runs:
            errors = f"{run.metric('tool_calls')}({run.metric('tool_errors')})"
            lines.append(
                f"| {run.task_id} | {run.repeat} | {run.verdict} | {run.metric('turns')} | {errors} | "
                f"{run.metrics.get('termination', '—')} | {run.tokens:,} | "
                f"{'、'.join(run.failure_modes) or '—'} | {', '.join(run.broken[:2]) or '—'} |"
            )
        lines += ["", "## 按标签", "", "| tag | runs | pass@1 | 失败模式 |", "|---|---:|---|---|"]
        for tag, stats in report.per_tag.items():
            lines.append(f"| {tag} | {stats['runs']} | {stats['pass_at_1_raw']} | {'、'.join(stats['failure_modes']) or '—'} |")
        lines += ["", "## 指标表（SPEC v2 §3.2）", "", "```json", json.dumps(summary, ensure_ascii=False, indent=2), "```", ""]
        if report.regressions:
            lines += ["## 与基线的差值", ""]
            for item in report.regressions:
                lines.append(f"- {item.line()}")
            lines.append("")
        failures = [run for run in report.runs if run.checks and run.verdict == "fail"]
        if failures:
            lines += ["## 失败任务的判据明细", ""]
            for run in failures[:8]:
                lines.append(f"### {run.task_id} · repeat {run.repeat}")
                for check in run.checks:
                    lines.append(f"- [{'x' if check['ok'] else ' '}] {check['label']} —— {check['detail']}")
                lines.append("")
        return "\n".join(lines)
