"""S12 的数据闸：把只读调用放在线程池里，到底能省下多少？（SPEC v2 §3.5 的砍单条件）

§3.5 写着"收益先测再改 —— 若可并行轮占比 < 20%，把 S12 整段推到 Tier 3"，并且假设
S8 已经在 trace 里加了 `parallelizable_in_round` 字段。**那个字段没有实现**，所以这里
只能从已有记录反推：一条 `tool_call` 记录自带 `turn` / `name` / `risk` / `latency`，
按轮分组就还原出"这一轮发了几个调用、分别是什么风险、各跑了多久"。

两道闸一起看，因为它们问的不是同一件事：
① **轮占比闸**（§3.5 的 20%）—— 调度器会碰到多大比例的轮；
② **墙钟闸**（B5 的 ≥15%）—— 真的并行了能省多少时间。`run_end.wall_ms` 在盘上，
   每一轮的 `latency` 也在盘上，所以这条不用等实现就能算。

判据与将来 `parallelizable()` 的定义同源，三条一起才算：
① 同一轮 ≥2 个调用；② 全部 `risk == "read"`；③ 不含 `write_todos`
（它的 risk_level 就是 READ，却改 `self.todos` —— 只看风险等级会把它放进线程池）。

分母取"执行过至少一个工具的轮"：调度器要改的就是这些轮，拿全部轮数当分母会把
模型直接回话的轮也算进来，稀释成一个大得多的假占比。

只看 live 轨迹。fake 剧本是人手写出来考 agent 的，"这一轮发三个只读调用"是我们
安排的，不是模型的行为 —— 用它测分布等于自己出题自己答。

可省时间的上界取"线程池够大"：一轮里 Σlatency 变 max(latency)，即
`saveable_ms = Σ_轮 (Σ − max)`。真实收益只会比它小（`MAX_PARALLEL_READS` 有限、
GIL 之下磁盘读也不完全重叠），所以它是**对 S12 最有利的那个假设**。

用法：python scripts/probe_parallel_share.py            # 盘上现有的轨迹
      python scripts/probe_parallel_share.py --include-fake
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "eval" / "results" / "s12-parallel-share.json"
GATE = 0.20  # SPEC v2 §3.5：低于这个占比就不做并发
B5_WALL_P50 = 0.15  # SPEC v2 §4 的 B5：墙钟 p50 下降 ≥15%
SERIAL_TOOLS = frozenset({"write_todos"})  # 与 §3.5 的 parallelizable() 同一份名单


def discover(*, include_fake: bool) -> dict[str, list[Path]]:
    """按来源分组找轨迹。分组是刻意的：不同来源的行为可信度不一样。"""
    groups = {
        "b3-live": sorted((ROOT / "eval/.work/b3-ab").glob("live-*/traces/*.jsonl")),
        "earlier-live": sorted((ROOT / "eval/.work/live/traces").glob("*.jsonl")),
        "demo-live": sorted((ROOT / "demos/traces").glob("*live*.jsonl")),
    }
    if include_fake:
        groups["fake"] = sorted((ROOT / "eval/.work/fake/traces").glob("*.jsonl"))
    return {name: paths for name, paths in groups.items() if paths}


def scan(path: Path) -> dict[str, Any]:
    """一份轨迹 → 轮分组 + 三段墙钟（工具 / LLM / 整次运行）。"""
    by_turn: dict[int, list[dict[str, Any]]] = defaultdict(list)
    llm_ms = 0.0
    wall_ms: list[float] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = record.get("kind")
            if kind == "tool_call":
                by_turn[int(record.get("turn") or 0)].append(record)
            elif kind == "llm_response":
                llm_ms += float(record.get("latency") or 0.0) * 1000.0
            elif kind == "run_end":
                wall_ms.append(float(record.get("wall_ms") or 0.0))
    rounds = [by_turn[turn] for turn in sorted(by_turn)]
    return {
        "rounds": rounds,
        "llm_ms": llm_ms,
        "tool_ms": sum(float(c.get("latency") or 0.0) * 1000.0 for r in rounds for c in r),
        "run_wall_ms": sum(wall_ms),
        "runs": len(wall_ms),
    }


def parallelizable(calls: list[dict[str, Any]]) -> bool:
    if len(calls) < 2:
        return False
    return all(c.get("risk") == "read" and c.get("name") not in SERIAL_TOOLS for c in calls)


def saveable_ms(calls: list[dict[str, Any]]) -> float:
    """这一轮在"线程池够大"的上界下能省下的毫秒数。"""
    if not parallelizable(calls):
        return 0.0
    lat = [float(c.get("latency") or 0.0) * 1000.0 for c in calls]
    return max(0.0, sum(lat) - max(lat))


def tally(scans: list[dict[str, Any]]) -> dict[str, Any]:
    rounds = [r for s in scans for r in s["rounds"]]
    ok = [r for r in rounds if parallelizable(r)]
    multi = [r for r in rounds if len(r) >= 2]
    saveable = sum(saveable_ms(r) for r in rounds)
    tool_ms = sum(s["tool_ms"] for s in scans)
    run_ms = sum(s["run_wall_ms"] for s in scans)
    llm_ms = sum(s["llm_ms"] for s in scans)
    # 每次运行各自的收益比例，p50 才是 B5 那句话量的口径（它写的是 p50，不是均值）
    per_run = [
        (sum(saveable_ms(r) for r in s["rounds"]) / s["run_wall_ms"])
        for s in scans
        if s["run_wall_ms"] > 0
    ]
    combos = Counter("+".join(sorted(str(c.get("name")) for c in calls)) for calls in multi)
    ok_combos = Counter("+".join(sorted(str(c.get("name")) for c in calls)) for calls in ok)
    sizes = Counter(str(len(calls)) for calls in rounds)
    return {
        "rounds_with_tools": len(rounds),
        "rounds_with_2plus_calls": len(multi),
        "parallelizable_rounds": len(ok),
        "share_of_tool_rounds": round(len(ok) / len(rounds), 4) if rounds else 0.0,
        "share_of_multi_call_rounds": round(len(ok) / len(multi), 4) if multi else 0.0,
        "calls_per_round_histogram": dict(sorted(sizes.items())),
        "extra_calls_available": sum(len(c) - 1 for c in ok),  # 并发最多能省下的串行次数
        "wall_clock": {
            "run_wall_ms": round(run_ms, 1),
            "llm_ms": round(llm_ms, 1),
            "tool_ms": round(tool_ms, 1),
            "tool_ms_share_of_run": round(tool_ms / run_ms, 5) if run_ms else 0.0,
            "llm_ms_share_of_run": round(llm_ms / run_ms, 5) if run_ms else 0.0,
            "saveable_ms_upper_bound": round(saveable, 1),
            "saveable_share_of_run_wall": round(saveable / run_ms, 5) if run_ms else 0.0,
            "saveable_share_of_tool_wall": round(saveable / tool_ms, 5) if tool_ms else 0.0,
            "saveable_share_of_run_wall_p50": round(statistics.median(per_run), 5) if per_run else 0.0,
            "runs_sampled": len(per_run),
        },
        "top_combos_of_multi_call_rounds": [
            {"combo": combo, "count": count} for combo, count in combos.most_common(8)
        ],
        "top_combos_of_parallelizable_rounds": [
            {"combo": combo, "count": count} for combo, count in ok_combos.most_common(6)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="统计真实轨迹里可并行轮的占比与可省墙钟（S12 的数据闸）")
    parser.add_argument("--include-fake", action="store_true", help="把 fake 批次也算进来（默认只算 live）")
    parser.add_argument("--out", type=Path, default=RESULT, help="证据文件路径")
    args = parser.parse_args()

    groups = discover(include_fake=args.include_fake)
    if not groups:
        print("盘上没有可用的轨迹（eval/.work/ 与 demos/traces/ 都空）", file=sys.stderr)
        return 2

    scanned = {name: [scan(p) for p in paths] for name, paths in groups.items()}
    per_source = {name: tally(s) for name, s in scanned.items()}
    all_scans = [s for scans in scanned.values() for s in scans]
    overall = tally(all_scans)
    share = overall["share_of_tool_rounds"]
    wall = overall["wall_clock"]
    upside = wall["saveable_share_of_run_wall_p50"]
    fresh = per_source.get("b3-live")
    fresh_share = fresh["share_of_tool_rounds"] if fresh else None
    keep = share >= GATE and fresh is not None

    payload = {
        "schema": 1,
        "spec": "SPEC v2 §3.5 / §7.2 行 12 —— 收益先测再改",
        "question": "把只读调用放进线程池，能影响到多大比例的轮、能省下多少墙钟？",
        "definition": {
            "parallelizable_round": "同一轮 ≥2 个调用，且全部 risk=read 且不含 write_todos",
            "denominator": "执行过至少一个工具的轮（不是全部轮：模型直接回话的轮调度器碰不到）",
            "why_not_risk_level_alone": "write_todos 的 risk_level 就是 READ，但它改 self.todos",
            "note_field_missing": (
                "§3.5 假设 S8 加了 parallelizable_in_round，实际没加（src/ 里 grep 为空），"
                "所以这里从 tool_call 的 turn/name/risk/latency 反推，规则与将来 parallelizable() 同源"
            ),
            "saveable_ms": "每个可并行轮 (Σ latency − max latency) 之和，即线程池无限大时的上界",
        },
        "gate": GATE,
        "gate_b5_wall_p50": B5_WALL_P50,
        "sources": {name: len(paths) for name, paths in groups.items()},
        "per_source": per_source,
        "overall": overall,
        "live_only": not args.include_fake,
        "decision": (
            f"闸①（§3.5 的轮占比 {GATE:.0%}）：合计 {share:.1%} → "
            + ("够线" if share >= GATE else "不够线")
            + (f"；但最新的一层 b3-live 单独看是 {fresh_share:.1%} → "
               + ("够线" if fresh_share >= GATE else "差一线") if fresh_share is not None else "")
        ),
        "decision_wall_clock": (
            f"闸②（B5 的墙钟 p50 {B5_WALL_P50:.0%}）：可省上限只占每次运行墙钟的 "
            f"{upside:.3%}（p50，n={wall['runs_sampled']}）—— "
            + ("可达" if upside >= B5_WALL_P50 else "不可达")
            + f"。原因在轨迹里：工具执行只占运行墙钟的 {wall['tool_ms_share_of_run']:.2%}，"
              f"LLM 延迟占 {wall['llm_ms_share_of_run']:.2%}。"
        ),
        "keep_s12": keep,
        "caveats": [
            "trace 里的 tool_call 只有**执行过**的调用：被权限门拒掉的调用当场就有结论，"
            "不占线程，所以把它们排除在统计之外是对的。",
            "分母是「轮」，但并发的实际收益单位是**被省下的串行执行次数**（extra_calls_available），"
            "只读调用本身只有几毫秒，占比高也不必然意味着墙钟降 15%（B5 量的是墙钟）。",
            "墙钟上界假设线程池无限大；真实收益更小。它仍然过不了 B5 那条线的话，"
            "结论与实现质量无关，是任务形状决定的。",
            "fake 批次的墙钟另说：那里 LLM 是瞬时的，工具时间≈全部时间，所以并发在 fake 上"
            "能省的比例远高于 live —— 拿 fake 的 p50 给 B5 签字会是自欺。",
        ],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )

    for name, stats in per_source.items():
        w = stats["wall_clock"]
        print(
            f"{name:14s} 轮 {stats['rounds_with_tools']:5d} · 可并行 {stats['parallelizable_rounds']:4d} · "
            f"占比 {stats['share_of_tool_rounds']:6.1%} · 工具墙钟占运行 "
            f"{w['tool_ms_share_of_run']:7.3%} · 可省 {w['saveable_ms_upper_bound']:8.1f}ms"
        )
    print(
        f"\n合计：{overall['rounds_with_tools']} 轮执行过工具，其中 {overall['parallelizable_rounds']} 轮"
        f"（{share:.1%}）可并行；≥2 调用的轮里可并行的占 {overall['share_of_multi_call_rounds']:.1%}"
    )
    print(f"每轮调用数分布：{overall['calls_per_round_histogram']}")
    print("可并行轮里最常见的组合：")
    for row in overall["top_combos_of_parallelizable_rounds"]:
        print(f"  {row['count']:4d} × {row['combo']}")
    print(
        f"\n墙钟账（live 轨迹）：运行 {wall['run_wall_ms']:,.0f}ms = LLM {wall['llm_ms']:,.0f}ms"
        f"（{wall['llm_ms_share_of_run']:.1%}）+ 工具 {wall['tool_ms']:,.0f}ms"
        f"（{wall['tool_ms_share_of_run']:.2%}）+ 其余"
    )
    print(
        f"  只读并发最多省 {wall['saveable_ms_upper_bound']:,.1f}ms = 工具时间的 "
        f"{wall['saveable_share_of_tool_wall']:.1%} = 运行时间的 "
        f"{wall['saveable_share_of_run_wall']:.3%}（每次运行 p50 {upside:.3%}，n={wall['runs_sampled']}）"
    )
    print(f"\n→ {payload['decision']}")
    print(f"→ {payload['decision_wall_clock']}")
    print(f"证据：{args.out if args.out.is_absolute() else args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
