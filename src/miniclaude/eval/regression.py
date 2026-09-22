"""基线对比 —— "这一版比上一版好还是坏"必须拿证据回答。

SPEC v2 §3.2 的 B1 生死线：`eval/baselines/` 里存一份真跑出来的成绩，之后每批
自动检出退步。三条规矩决定了这个文件长这样：

1. **考卷不同就不比。** `taskset_sha` 覆盖任务 JSON *与* fixture 文件树；对不上时
   `compare()` 只返回一条"拒绝对比"，绝不给 Δ 数字。改一行 fixture 里的 bug 能让
   分数平白涨四个点，那种"进步"比没有进步坏得多 —— 它会被人写进汇报里。
2. **逐任务配对，不只看聚合通过率。** McNemar 要的是不一致对子数（b01 = 基线过
   现在挂，b10 = 基线挂现在过）。聚合数字把配对信息丢了：两次都 75% 可能意味着
   四道题换了人，也可能意味着一动不动，前者要查，后者不用。
3. **24 道题的 Δ 颗粒度是 1/24。** 差值小于 `INDISTINGUISHABLE`（12.5%，恰好一道题）
   时明说"分辨不出"。把 ±1 题说成趋势是这类报表最常见的谎。
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = 1
INDISTINGUISHABLE = 0.125  # 一道题 / 24 —— 小于它就是噪声
P_THRESHOLD = 0.05
SEVERITIES = ("regression", "refused", "improvement", "guard", "noise")
NEGATIVE_TAG = "negative"  # 模拟坏行为的题（不论判定）
MUST_FAIL_TAG = "must-fail"  # 其中"必须被抓住"的那一类：判绿即判据失效


@dataclass(frozen=True)
class Regression:
    """一行对比结论。`line()` 进报表，`to_dict()` 进 `report.json` 与基线文件。"""

    label: str
    detail: str
    severity: str = "noise"
    task_id: str = ""
    before: str = ""
    after: str = ""

    @property
    def blocker(self) -> bool:
        """True = 这批不能算"没问题"。拒绝对比也算，因为看不见退步不等于没退步。"""
        return self.severity in ("regression", "refused", "guard")

    def line(self) -> str:
        head = {
            "regression": "退步",
            "refused": "无法对比",
            "guard": "判据告警",
            "improvement": "进步",
            "noise": "变化",
        }[self.severity]
        return f"**{head}** {self.label} —— {self.detail}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "detail": self.detail,
            "severity": self.severity,
            "task_id": self.task_id,
            "before": self.before,
            "after": self.after,
        }


# ------------------------------------------------------------------ 基线文件


def baseline_path(directory: Path, *, engine: str, taskset_sha: str) -> Path:
    """基线文件名带题集哈希：同一目录下并存多份时，文件名自己就能说明是哪张考卷。"""
    return Path(directory) / f"{engine}-{taskset_sha}.json"


def baseline_from_report(report: Any, *, note: str = "") -> dict[str, Any]:
    """`BatchReport` → 可入库的基线。只存**配对所需的最小事实**，不存 trace 路径：
    工作副本每次都是新目录，那些路径出了这台机器就是垃圾，留着只会误导。
    """
    runs = _runs(report)
    tasks: dict[str, Any] = {}
    for run in runs:
        if run.repeat != 0:
            continue
        tasks[run.task_id] = {
            "verdict": run.verdict,
            "turns": run.metric("turns"),
            "tokens": run.tokens,
            "failure_modes": list(run.failure_modes),
            "broken": list(run.broken),
        }
    return {
        "schema": SCHEMA,
        "taskset_sha": getattr(report, "taskset_sha", "") or "",
        "engine": sorted({run.engine for run in runs}),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": note,
        "summary": dict(getattr(report, "summary", {}) or {}),
        "per_tag": dict(getattr(report, "per_tag", {}) or {}),
        "tasks": tasks,
    }


def save_baseline(payload: dict[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_baseline(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"基线文件不存在：{path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "tasks" not in raw:
        raise ValueError(f"基线文件不是本工具写的（缺 tasks 字段）：{path}")
    if int(raw.get("schema") or 0) != SCHEMA:
        raise ValueError(f"基线 schema={raw.get('schema')}，本工具只认 {SCHEMA}：{path}")
    return raw


# ------------------------------------------------------------------ 对比


def taskset_gate(baseline: dict[str, Any], tasks: Any) -> list[Regression]:
    """开跑前先看"考卷还是不是那张考卷"。

    `TaskSet.filtered()` 保留全量哈希，所以 `--only` / `--smoke` 出来的批次数的是
    四分之一的题，却带着完整 24 题的哈希 —— 单看哈希看不出这是子集。真实模型
    按 token 计费，花一半额度去验一个不存在的对比是最容易犯的错，因此这一条
    在跑**之前**就该响。
    """
    stored = {str(tid) for tid in (baseline.get("tasks") or {})}
    now = {task.id for task in tasks}
    out: list[Regression] = []
    sha = str(baseline.get("taskset_sha") or "")
    if sha and getattr(tasks, "sha", "") and sha != tasks.sha:
        out.append(
            Regression(
                label="题集哈希与基线不一致",
                detail=f"基线 {sha} → 当前 {tasks.sha}：题面或 fixture 改过，逐题判定不再可比",
                severity="refused",
            )
        )
    if stored and now and not (stored - now) and len(now) < len(stored):
        out.append(
            Regression(
                label=f"这批只覆盖了基线的一部分（{len(now)}/{len(stored)} 题）",
                detail="子集批次的 pass 率不能和全量基线并排读；要趋势就补齐缺失任务重跑",
                severity="noise",
            )
        )
    return out


def compare(baseline: dict[str, Any], report: Any) -> list[Regression]:
    """基线 vs 这一批。返回的每一行都要能追到具体任务，空列表意味着"完全一致"。"""
    runs = _runs(report)
    sha = getattr(report, "taskset_sha", "") or ""
    old_sha = str(baseline.get("taskset_sha") or "")
    if sha and old_sha and sha != old_sha:
        return [
            Regression(
                label="题集哈希变了",
                detail=f"基线 {old_sha} ≠ 本次 {sha}。考卷改了，分数不可比 —— 重跑一份基线再对比。",
                severity="refused",
            )
        ]

    old: dict[str, str] = {tid: str(item.get("verdict", "")) for tid, item in (baseline.get("tasks") or {}).items()}
    now: dict[str, str] = {run.task_id: run.verdict for run in runs if run.repeat == 0}
    out: list[Regression] = []

    flipped = [tid for tid in sorted(old) if tid in now and old[tid] == "pass" and now[tid] != "pass"]
    healed = [tid for tid in sorted(old) if tid in now and old[tid] != "pass" and now[tid] == "pass"]
    missing = [tid for tid in sorted(old) if tid not in now]
    added = [tid for tid in sorted(now) if tid not in old]

    for tid in flipped:
        out.append(
            Regression(
                label=f"{tid} 由 pass 变为 {now[tid]}",
                detail=_flip_detail(runs, tid, baseline),
                severity="regression",
                task_id=tid,
                before=old[tid],
                after=now[tid],
            )
        )
    for tid in healed:
        out.append(
            Regression(
                label=f"{tid} 由 {old[tid]} 转为 pass",
                detail=f"基线判定 {old[tid]}，本次通过。",
                severity="improvement",
                task_id=tid,
                before=old[tid],
                after=now[tid],
            )
        )
    if missing:
        out.append(
            Regression(
                label="这批没跑到基线里的任务",
                detail=f"缺：{', '.join(missing[:5])}（对比只在共同任务上做，不猜）",
                severity="refused",
            )
        )
    if added:
        out.append(
            Regression(
                label="基线里没有这些任务",
                detail=f"新增：{', '.join(added[:5])}（无历史可配，不计入显著性检验）",
                severity="noise",
            )
        )

    out.append(_rate_row(old, now, baseline, report))
    out.append(_cost_row(baseline, report))
    return out


def mcnemar_exact(b01: int, b10: int) -> float:
    """McNemar 精确二项双侧 p 值。n 小的时候这就是唯一诚实的算法 ——
    卡方近似在 b01+b10 < 10 时会把 p 算得偏小，正好是"最容易骗到你"的区间。
    """
    n = b01 + b10
    if n == 0:
        return 1.0
    k = min(b01, b10)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def _rate_row(old: dict[str, str], now: dict[str, str], baseline: dict[str, Any], report: Any) -> Regression:
    shared = [tid for tid in old if tid in now]
    b01 = sum(1 for tid in shared if old[tid] == "pass" and now[tid] != "pass")
    b10 = sum(1 for tid in shared if old[tid] != "pass" and now[tid] == "pass")
    old_pass = sum(1 for tid in shared if old[tid] == "pass")
    new_pass = sum(1 for tid in shared if now[tid] == "pass")
    delta = (new_pass - old_pass) / len(shared) if shared else 0.0
    p = mcnemar_exact(b01, b10)
    detail = (
        f"共同 {len(shared)} 题 pass@1：{old_pass}/{len(shared)} → {new_pass}/{len(shared)}"
        f"（Δ {delta:+.1%}）· 不一致对子 {b01}↘/{b10}↗ · McNemar 精确 p={p:.3f}"
    )
    if b01 + b10 == 0:
        return Regression(label="pass@1 无变化", detail=detail + "；判定完全一致", severity="noise")
    if abs(delta) < INDISTINGUISHABLE and p >= P_THRESHOLD:
        return Regression(
            label="pass@1 差值在噪声内",
            detail=detail + f"；Δ 小于 {INDISTINGUISHABLE:.1%}（一道题）且 p≥{P_THRESHOLD}，分辨不出真假",
            severity="noise",
        )
    return Regression(
        label="pass@1 有统计变化" if p < P_THRESHOLD else "pass@1 差值未达显著",
        detail=detail + f"；p {'<' if p < P_THRESHOLD else '≥'} {P_THRESHOLD}",
        severity="regression" if delta < 0 else "improvement",
    )


def _cost_row(baseline: dict[str, Any], report: Any) -> Regression:
    """成本也要比。判据修不出"烧了两倍 token 还算好消息"这种结论，只能靠这一行。"""
    summary = dict(getattr(report, "summary", {}) or {})
    old = dict(baseline.get("summary") or {})
    if not old or not summary:
        return Regression(label="成本无可对比", detail="缺少基线或本次汇总", severity="noise")
    pairs = (
        ("tokens_total", "tokens"),
        ("wall_time_p50_ms", "p50 墙钟 ms"),
        ("context_peak_p95", "上下文峰值 p95"),
    )
    bits: list[str] = []
    worse = 0
    moved = False
    for key, pretty in pairs:
        a, b = old.get(key), summary.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a:
            change = (b - a) / abs(a)
            bits.append(f"{pretty} {a:,.0f} → {b:,.0f}（{change:+.1%}）")
            moved = moved or change != 0.0
            if change > 0.25:
                worse += 1
    if not bits:
        return Regression(label="成本无可对比", detail="基线里没存这些指标", severity="noise")
    detail = "；".join(bits)
    if worse:
        label = f"{worse} 项成本指标涨过 25%"
    else:
        # 一个字没动还标题"成本变化"，读者会去明细里找一个并不存在的涨幅。
        label = "成本变化" if moved else "成本无变化"
    return Regression(label=label, detail=detail, severity="noise" if worse == 0 else "regression")


def _flip_detail(runs: Sequence[Any], task_id: str, baseline: dict[str, Any]) -> str:
    """退步行要说"为什么挂"，否则这条结论在报表里等于没写。"""
    run = next((r for r in runs if r.task_id == task_id and r.repeat == 0), None)
    if run is None:
        return "本次记录缺失"
    parts = [f"终止 {run.metrics.get('termination', '—')}", f"{run.metric('turns')} 轮"]
    if run.failure_modes:
        parts.append("失败模式 " + "、".join(run.failure_modes))
    failed = [c for c in run.checks if not c.get("ok")]
    if failed:
        parts.append("未过的判据：" + "; ".join(c["label"] for c in failed[:2]))
    if run.broken:
        parts.append("红掉的用例：" + ", ".join(run.broken[:3]))
    old_modes = ((baseline.get("tasks") or {}).get(task_id) or {}).get("failure_modes") or []
    if old_modes:
        parts.append("基线当时已标 " + "、".join(old_modes))
    return " · ".join(parts)


def _runs(report: Any) -> list[Any]:
    if isinstance(report, Iterable) and not hasattr(report, "runs"):
        return list(report)  # type: ignore[arg-type]
    return list(getattr(report, "runs", []) or [])


def blockers(items: Sequence[Regression]) -> list[Regression]:
    return [item for item in items if item.blocker]


def instrument_checks(runs: Sequence[Any]) -> list[Regression]:
    """判据自检：`must-fail` 的题被判**绿**，说明判据松了。

    坏题不会报错，它只会安静地变成一个好看的数字 —— 这类题存在的全部意义就是
    "判据一旦松了它必须叫"。注意它和 `negative` 不是一回事：`negative` 里有一半
    （repeat-stall / over-budget-loop）要的是"agent 老实停下来"，判绿是对的。
    所以告警只跟 `must-fail` 绑定：篡改考卷、瞎猜路径、口头宣称修好 —— 这三件事
    被判定成 pass，就是量尺坏了。
    """
    out: list[Regression] = []
    for run in runs:
        if run.repeat != 0 or MUST_FAIL_TAG not in run.tags:
            continue
        if run.verdict == "pass":
            out.append(
                Regression(
                    label=f"必须被抓坏的题 {run.task_id} 被判成 pass",
                    detail="这道题模拟的是该被判据抓住的坏行为，判绿说明判据抓不到它 —— 修判据，别庆祝。",
                    severity="guard",
                    task_id=run.task_id,
                    before="期望 fail",
                    after="pass",
                )
            )
    return out
