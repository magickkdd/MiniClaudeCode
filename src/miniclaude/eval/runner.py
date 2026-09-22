"""跑批器 —— 把任务集按重复数跑完、边跑边落盘、随时可续、到预算就停。

四个设计决定都是**代价换确定性**，写在这里免得日后被"顺手优化"掉：

* `workers=1`：并行打同一端点会把限流抖动混进"配置 A vs 配置 B"的比较里。
* **每条 run 一落盘就重写 `manifest.jsonl`（同名键取代旧行）**：断点续跑靠的是这行，
  不是靠跑完再写。72 次 live 跑到第 50 次崩了要能接着跑，这是 B1 的字面要求。
  取代而非叠加，是因为 trace 文件名按 `(task, repeat, engine)` 定、重跑会覆盖上一份轨迹；
  纯 append 就是让一行判据指向一条已经不存在的轨迹。
* **取代只保证"指向哪一份"，指纹才保证"就是那一份"**：每一行落盘前给 trace 按内容盖一个
  戳（行数 + 字节数 + sha256 前 16 位 + 批次内相对路径）。没有它，一行判据在手工改过的、或者被
  第三次覆盖过的轨迹上照样看起来完好 —— §7.3-7 的缺口 ①，导出器只能靠"轮数对不对"间接撞见。
* **批次预算** `budget_tokens` 是整批硬上限，与单任务的 `token_ceiling` 不是一回事。
  没有它，一次失手的 live 批次能在一夜之间把预算花掉两倍。
* **失败关闭**：批跑没有人类确认者，`confirmer` 恒返回"拒绝"，权限模式取
  `AUTO`/`READONLY`（路径锁死在工作副本），**永远不开 ASK** —— 开了就是拿一个
  没人回答的问题挂住整批评测。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from miniclaude.agent.permissions import Answer, PermissionMode
from miniclaude.agent.state import AgentResult
from miniclaude.cli.main import build_session
from miniclaude.cli.render import Renderer
from miniclaude.config import BACKEND_NAMES, Config
from miniclaude.eval import drivers, judge as judge_mod, regression
from miniclaude.eval.contract import Context, isolate
from miniclaude.eval.metrics import per_tag, summarize_batch
from miniclaude.eval.taskset import TaskInstance, TaskSet
from miniclaude.infra.trace import fingerprint, summarize
from miniclaude.llm.openai_compat import OpenAICompatClient

VERDICTS = ("pass", "fail", "error", "aborted")


def trace_stamp(path: Path | None, root: Path) -> dict[str, Any] | None:
    """给一条 run 的轨迹按内容留证，并附一份**相对于批次目录**的路径。

    绝对路径是这台机器的坐标：`eval/.work/` 被 .gitignore 扫掉，批次目录拷到别的机器、
    别的 checkout、甚至只是换个盘符，那一行就指空了（§7.3-7 缺口 ②）。相对路径 +
    内容指纹一起写，下游才既找得到文件、又验得出"这就是判据当时看的那份"（缺口 ①）。

    文件不在（装配失败、或 trace 被禁用）就不留证：**没有指纹**和**指纹不符**是两件事，
    前者只能标记成"这一行未经校验"，后者才该把整行挡在数据外面。
    """
    if path is None:
        return None
    resolved = Path(path)
    if not resolved.is_file():
        return None
    stamp = fingerprint(resolved)
    try:
        stamp["rel"] = Path(os.path.relpath(resolved, root)).as_posix()
    except ValueError:  # 跨盘符（Windows）没有相对路径可言
        stamp["rel"] = resolved.name
    return stamp


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
    # 判据落盘那一刻 trace 的内容指纹（行数/字节/sha256 前 16 位 + 批次内相对路径）。
    # `None` = 这行写于 §7.3-7 上线之前，或当时没有 trace 文件 —— 它与"指纹不符"不同，
    # 只能被标成未经校验，不能拿来做同一性判定。
    trace_fp: dict[str, Any] | None = None

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
            "trace_fp": self.trace_fp,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, base: Path | None = None) -> "RunRecord":
        path = raw.get("trace_path")
        trace_path = Path(path) if path else None
        stamp = raw.get("trace_fp")
        if base is not None and trace_path is not None and not trace_path.is_file():
            # 绝对路径找不到 ≠ 这份轨迹没了。批次目录被拷走时它还在，只是换了坐标 ——
            # 指纹里那份相对路径就是为这一刻留的。换了坐标也不会认错文件：同一性是
            # sha 判的，路径只负责把文件找到（`export_rl.identity_of`）。
            rel = str((stamp or {}).get("rel") or "") if isinstance(stamp, dict) else ""
            if rel:
                candidate = Path(base) / rel
                if candidate.is_file():
                    trace_path = candidate
        return cls(
            task_id=str(raw["task_id"]),
            repeat=int(raw.get("repeat", 0)),
            engine=str(raw.get("engine", "unknown")),
            verdict=str(raw.get("verdict", "error")),
            trace_path=trace_path,
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
            trace_fp=dict(stamp) if isinstance(stamp, dict) else None,
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
        context_compact: bool | None = None,
        context_budget: int | None = None,
        context_hard_limit: int | None = None,
        repo_map: bool | None = None,
        repo_map_tokens: int | None = None,
        backend: str | None = None,
        checkpoints: bool | None = None,
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
        self.context_compact = context_compact
        self.context_budget = context_budget
        self.context_hard_limit = context_hard_limit
        self.repo_map = repo_map
        self.repo_map_tokens = repo_map_tokens
        self.backend = backend
        self.checkpoints = checkpoints
        self._base_config: Config | None = None
        if context_budget is not None and context_budget < 1:
            raise ValueError("context_budget 至少 1；要让阶梯不生效请用 context_compact=False")
        if context_hard_limit is not None and context_hard_limit < 1:
            raise ValueError("context_hard_limit 至少 1；要关掉止损闸门请抬高它，别设 0")
        if repo_map_tokens is not None and repo_map_tokens < 1:
            raise ValueError("repo_map_tokens 至少 1；要让地图完全不上身请用 repo_map=False（B3 的对照臂）")
        if backend is not None and backend not in BACKEND_NAMES:
            # B6 的两臂是按后端名点名的，打错一个字母就变成"两臂都是 local"，
            # 而报表会把它读成"两个后端判定一致"。
            raise ValueError(f"backend 只能是 {' / '.join(BACKEND_NAMES)}，收到 {backend!r}")
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
        # 记忆目录名要在复制之前就知道：`isolate` 排除它，判定也排除它，两处必须是同一个
        # 名字 —— 否则 `MEMORY_DIR` 一改，派生缓存就变成"模型改了源码"。
        memory_dir = self.base_config().memory_dir
        workdir = isolate(baseline, self.out / "work" / f"{task.id}.r{repeat}", memory_dir=memory_dir)
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
                summarizer_llm=self.summarizer_for(),
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
        ctx = Context(
            engine=self.engine,
            workdir=workdir,
            baseline=baseline,
            result=result,
            console=lines,
            memory_dir=memory_dir,
        )
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
                record = RunRecord.from_dict(json.loads(line), base=self.out)
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
        """落一条 run。**同名键（task, repeat）的旧行被这一行取代**，不是叠加。

        trace 文件名按 (task, repeat, engine) 定，重跑是**覆盖**上一份。所以纯 append 会让
        早先那行指向一条已经不存在的轨迹：报表把一条轨迹数成两条，§3.9 的导出器则会给它
        配上别人的 reward —— 已经在这批数据上抓到 26 例。整份原子重写（tmp + `os.replace`）：
        写坏了最多是退回上一版，不会留下一半的一份。读不懂的行原样留着 —— 那是证据，
        哪怕它已经不是一条可用的记录。

        取代只解决"指向哪一份"，解决不了"这份就是判据看的那份"：手工改过、或者被第三次
        覆盖，行看起来仍然完好。所以落盘前按内容盖一个指纹（`trace_stamp`）。这是**唯一的**
        盖指纹的地方 —— 判据从这里出去，指纹就必须从这里出去，两条路各自算就会漂。
        """
        if record.trace_fp is None:
            record.trace_fp = trace_stamp(record.trace_path, self.out)
        lines: list[str] = []
        if self.manifest.is_file():
            for line in self.manifest.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    raw = None
                if isinstance(raw, dict) and (raw.get("task_id"), raw.get("repeat")) == record.key:
                    continue
                lines.append(json.dumps(raw, ensure_ascii=False) if raw is not None else line)
        lines.append(json.dumps(record.to_dict(), ensure_ascii=False))
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.manifest)

    def base_config(self) -> Config:
        """跑批这一臂的基配置（还没按任务改写 project_root / max_turns 的那一份）。

        缓存是必要的，不是为了省时间：`run_task` 在**复制工作副本之前**就要知道记忆目录
        叫什么（`isolate` 得把它排除掉，否则上一次跑批留下的缓存会被判成"改动了源码"），
        而这个答案只有 Config 里有。不缓存就是每个任务读两遍 `.env`。
        """
        if self._base_config is None:
            self._base_config = self.config or (
                Config.from_env(self.tasks.repo_root / ".env")
                if self.engine == "live"
                else Config(
                    base_url="http://fake.invalid/v1",
                    api_key="sk-fake-not-a-real-key-000000",
                    model="fake-llm(scripted)",
                    project_root=self.tasks.repo_root,
                )
            )
        return self._base_config

    def config_for(self, task: TaskInstance, workdir: Path, trace: Path) -> Config:
        base = self.base_config()
        # 只能从已有配置派生：`Config.redacted()` 会把 api_key 掩码，拿它重建一份
        # 就等于给真实引擎装上一把假钥匙 —— 那种失败会以"网络错误"的形式出现，
        # 排查起来完全看不出根因。
        budget = base.token_budget if self.context_budget is None else self.context_budget
        limit = base.context_hard_limit if self.context_hard_limit is None else self.context_hard_limit
        if budget >= limit:
            raise ValueError(
                f"CONTEXT_HARD_LIMIT({limit:,}) 必须 > token_budget({budget:,})："
                "熔断线会先于阶梯生效，这批跑的全是 CONTEXT_OVERFLOW"
            )
        overrides: dict[str, Any] = {}
        if self.context_compact is not None:
            overrides["context_compact"] = self.context_compact
        if self.context_budget is not None:
            overrides["token_budget"] = self.context_budget
        if self.context_hard_limit is not None:
            overrides["context_hard_limit"] = self.context_hard_limit
        if self.repo_map is not None:
            overrides["repo_map"] = self.repo_map
        if self.repo_map_tokens is not None:
            overrides["repo_map_tokens"] = self.repo_map_tokens
        if self.backend is not None:
            overrides["execution_backend"] = self.backend
        if self.checkpoints is not None:
            overrides["checkpoints"] = self.checkpoints
        return replace(
            base, project_root=workdir, trace_path=trace, max_turns=task.max_turns, **overrides
        )

    def summarizer_for(self) -> Any:
        """L2 摘要的客户端。fake 引擎必须给独立队列，live 用主客户端（None 即复用）。

        共用一个 `FakeLLM` 时，摘要请求会从主剧本里 pop 掉一条响应并当成纪要 ——
        剧本少一步、落盘少一个文件，判据却把这笔账算到题目头上。真实端点按内容回答，
        不存在这条通路，所以 live 臂返回 None 让它复用。
        """
        if self.engine == "fake":
            from fakes import FakeSummarizer

            return FakeSummarizer()
        return None

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
