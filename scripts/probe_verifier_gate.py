"""S15 的数据闸：verifier 子 agent 到底对症吗？（SPEC v2 D21 / §3.8 的前置条件）

§7.3-1 写着「`spawn_agent` verifier 形态 —— **只在 B1 失败分布支持时做**」，D21 给了那条线：
「若『未收尾/自我确认过早』占比 > 20% 再回来讨论 critic」。所以这个脚本一行调度代码都不写，
只回答两个问题：

闸①（行为占比）：盘上真实轨迹里，`no_verification` + `self_confirm` 占多大比例？
闸②（§3.8 自己的经济学）：那条设计说「把 200 行 pytest 输出压缩成 5 行」是子 agent
   唯一明显划算的场景 —— 这句话在**我们的**轨迹里值多少字符？如果验证输出本来就只占工具
   输出的个位数百分比，那「没验证」就不是被上下文成本逼出来的，把它隔离进子上下文里
   救不回闸①的任何一次失败。

两道闸问的不是同一件事，各自成立不等于通过：占比过线但验证很便宜 → 该修的是"让模型读它
已经看得见的红"（提示词与判据层），不是再加一层上下文。

口径四条，都是这个脚本自己会被骗的地方：
· 标签**用当前规则重算**（`infra/failure.classify`），不吃 manifest 里落盘那份 —— 规则从
  S8 起改过，读旧标签等于拿旧尺子量新结论。两份都算，并检查**换尺子会不会翻转闸①**。
· 分母两个都算（全部 run / 只看判 fail 的 run）。D21 只写了「占比」没写分母，而 fake 层
  两个分母分别是 16.7% 与 0.0% —— 差着一个数量级，选哪个直接决定结论，所以这不是形式问题。
· 分子还要再问一句「它记的是行为还是代价」：`self_confirm` 的判据是「最后一步验证是红就
  收尾」，与「因此判负」不等价。所以命中的 run 单独按 verdict 数一遍（`gate_hits_by_verdict`），
  否则一个 20% 的过线结论可能全是判 pass 的 run 撑起来的 —— 那加 verifier 什么也救不回来。
· fake 与 live 分栏，**签字只看 live**：fake 剧本是我手写出来考 agent 的，「改完没跑测试
  就收尾」是我安排的剧情，不是模型的行为 —— 拿它测分布等于自己出题自己答。这与 §3.5.1
  拒绝用 fake 的 p50 给 B5 签字是同一条纪律。

用法：
  PYTHONPATH="src;demos" python -X utf8 scripts/probe_verifier_gate.py
  PYTHONPATH="src;demos" python -X utf8 scripts/probe_verifier_gate.py --include-fake
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from miniclaude.infra.failure import VERIFY_TOOLS, classify, facts_from_records  # noqa: E402
from miniclaude.infra.trace import replay  # noqa: E402

RESULT = ROOT / "eval" / "results" / "s15-verifier-gate.json"
GATE_D21 = 0.20  # D21：占比过这条线才回来讨论 critic/verifier
GATE_LABELS = ("no_verification", "self_confirm")
# §3.8 的原话是「200 行 pytest 输出」。按 70 字符/行折成字符，当闸②的标尺。
SPEC_VERIFIER_LINES = 200
CHARS_PER_LINE = 70
SPEC_VERIFIER_CHARS = SPEC_VERIFIER_LINES * CHARS_PER_LINE
MIN_LIVE_SAMPLE = 20  # §6.4：n < 20 不许写「提升了 X%」，只能写「X/Y 通过（n=Y）」


@dataclass
class Layer:
    """一批同源轨迹。`manifest` 是 verdict 的唯一产地，没有就是没量到。"""

    name: str
    engine: str
    traces: list[Path]
    manifest: Path | None = None
    spec_note: str = ""
    verdicts: dict[str, str] = field(default_factory=dict, repr=False)
    recorded: dict[str, str] = field(default_factory=dict, repr=False)


def discover(*, include_fake: bool) -> list[Layer]:
    """按来源分组找轨迹 —— 不同来源的行为可信度不一样，混进一格签字就是假的。"""
    layers = [
        Layer(
            "b2-live", "live",
            sorted((ROOT / "eval/.work/b2-ab/live/traces").glob("*.jsonl")),
            ROOT / "eval/.work/b2-ab/live/manifest.jsonl",
            "B2 的真实端点臂（压缩开着、必然超预算的长任务）",
        ),
        Layer(
            "b3-live-off", "live",
            sorted((ROOT / "eval/.work/b3-ab/live-off/traces").glob("*.jsonl")),
            ROOT / "eval/.work/b3-ab/live-off/manifest.jsonl",
            "B3 对照臂（无符号地图）—— 6 道 bugfix 题，最接近 D21 说的『改完没收尾』语境",
        ),
        Layer(
            "b3-live-on", "live",
            sorted((ROOT / "eval/.work/b3-ab/live-on/traces").glob("*.jsonl")),
            ROOT / "eval/.work/b3-ab/live-on/manifest.jsonl",
            "B3 实验臂（有符号地图），与上一臂只差一个开关",
        ),
        Layer(
            "smoke-live", "live",
            sorted((ROOT / "eval/.work/live/traces").glob("*.jsonl")),
            ROOT / "eval/.work/live/manifest.jsonl",
            "6 题 live 冒烟（`pass@1=4/6` 那一批）",
        ),
        Layer(
            "demo-live", "live",
            sorted((ROOT / "demos/traces").glob("*live*.jsonl")),
            None,
            "4 个 demo 的真实端点轨迹 —— 没有 manifest，所以它只进『全部 run』那个分母",
        ),
    ]
    if include_fake:
        layers.insert(
            0,
            Layer(
                "b1-fake", "fake",
                sorted((ROOT / "eval/.work/fake/traces").glob("*.jsonl")),
                ROOT / "eval/.work/fake/manifest.jsonl",
                "B1 本批（24 题 × 3）—— D21 点名的就是这份分布，但它是**手写剧本**",
            ),
        )
    return [layer for layer in layers if layer.traces]


def key_of(path: Path) -> str:
    """`bh-format-duration.r0.live.jsonl` → `bh-format-duration.r0`，用来对 manifest。"""
    stem = path.name
    for suffix in (".live.jsonl", ".fake.jsonl", ".jsonl"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def attach_records(layer: Layer) -> None:
    """把 manifest 里的 verdict / 落盘标签按三种键都存一遍：路径、文件名、`task.rN`。

    不照命名规则拼路径去查，是因为规则改过一次：拼错的键查不到，于是那一格静默变成
    "没有失败样本"，比报错更糟。
    """
    if layer.manifest is None or not layer.manifest.is_file():
        return
    for line in layer.manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        verdict = str(row.get("verdict") or "")
        task_id = str(row.get("task_id") or "")
        modes = ",".join(sorted(str(item) for item in (row.get("failure_modes") or [])))
        trace = str(row.get("trace_path") or "")
        for key in filter(None, (trace, Path(trace).name if trace else "", f"{task_id}.r{row.get('repeat')}")):
            if verdict:
                layer.verdicts[key] = verdict
            layer.recorded[key] = modes


def lookup(table: dict[str, str], path: Path) -> str:
    for key in (str(path), path.name, key_of(path)):
        if key in table:
            return table[key]
    return ""


def labels_of(records: list[dict[str, Any]]) -> tuple[list[str], str]:
    """当前规则下的标签集合。跑两遍取第二遍 —— 确定性这条由调用方单独钉。"""
    classify(facts_from_records(records))
    found = classify(facts_from_records(records))
    return sorted({item.mode.value for item in found}), ",".join(sorted({item.mode.value for item in found}))


def scan(path: Path, layer: Layer) -> dict[str, Any]:
    """一份轨迹 → 标签（当前规则重算）+ 验证输出的字符账。"""
    records = replay(path)
    facts = facts_from_records(records)
    modes, mode_key = labels_of(records)
    calls = [record for record in records if record.get("kind") == "tool_call"]
    verify = [record for record in calls if str(record.get("name")) in VERIFY_TOOLS]
    chars = lambda rows: sum(int(row.get("output_chars") or 0) for row in rows)  # noqa: E731
    sizes = sorted((int(row.get("output_chars") or 0) for row in verify), reverse=True)
    # 按工具名拆开：`run_tests`（结构化、我们自己精简过）和 `bash`（原样转发 pytest 的
    # stdout）都算"验证调用"，但 §3.8 那句「200 行输出」只可能来自后者。不拆就看不出
    # 那个经济学理由在**我们这个** agent 上到底还成不成立。
    by_tool: dict[str, list[int]] = {}
    for row in verify:
        by_tool.setdefault(str(row.get("name")), []).append(int(row.get("output_chars") or 0))
    return {
        "path": path,
        "layer": layer.name,
        "engine": layer.engine,
        "verdict": lookup(layer.verdicts, path),
        "recorded_modes": lookup(layer.recorded, path),
        "modes": modes,
        "mode_key": mode_key,
        "gate_hits": [mode for mode in modes if mode in GATE_LABELS],
        "termination": facts.termination,
        "turns": facts.turns,
        "calls": len(calls),
        "verify_calls": len(verify),
        "output_chars": chars(calls),
        "verify_chars": chars(verify),
        "biggest_verify_chars": sizes[0] if sizes else 0,
        "verify_sizes_by_tool": by_tool,
        "writes": sum(1 for row in calls if str(row.get("name")) in {"write_file", "edit_file"}),
        "saw_red": any(row.get("verdict") == "red" for row in verify),
        "saw_green": any(row.get("verdict") == "green" for row in verify),
        "stray_verdict": sorted({str(row.get("name")) for row in calls if row.get("verdict") not in (None, "") and str(row.get("name")) not in VERIFY_TOOLS}),
    }


def tally(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """一格（某层或合计）的全部数字。两个分母都给，因为选哪个会换结论。"""
    known = [row for row in runs if row["verdict"]]
    failed = [row for row in known if row["verdict"] == "fail"]
    hits = [row for row in runs if row["gate_hits"]]
    failed_hits = [row for row in failed if row["gate_hits"]]
    output = sum(row["output_chars"] for row in runs)
    verify = sum(row["verify_chars"] for row in runs)
    biggest = sorted(row["biggest_verify_chars"] for row in runs if row["verify_calls"])
    shares = [row["verify_chars"] / row["output_chars"] for row in runs if row["output_chars"]]
    # 按工具名汇总的验证输出：`max` 是它单次能吐多少字符，`over_spec` 是越过 §3.8 那把
    # 14,000 字符标尺的次数。这一格决定「200 行 pytest 输出」在本项目里到底存不存在。
    pooled: dict[str, list[int]] = {}
    for row in runs:
        for name, sizes in row["verify_sizes_by_tool"].items():
            pooled.setdefault(name, []).extend(sizes)
    by_tool = {
        name: {
            "calls": len(sizes),
            "chars": sum(sizes),
            "single_max": max(sizes) if sizes else 0,
            "single_p95": _pct(sizes, 0.95) or 0,
            "over_spec_calls": sum(1 for size in sizes if size >= SPEC_VERIFIER_CHARS),
        }
        for name, sizes in sorted(pooled.items())
    }
    # 同一格用**落盘标签**再算一遍：换一把尺子，闸①会不会翻。翻了就说明结论挂在规则版本上。
    hits_then = [row for row in runs if any(label in GATE_LABELS for label in row["recorded_modes"].split(",")) and row["recorded_modes"]]
    return {
        "runs": len(runs),
        "runs_with_verdict": len(known),
        "failed_runs": len(failed),
        "gate_labelled_runs": len(hits),
        "share_of_all_runs": _rate(len(hits), len(runs)),
        "share_of_failed_runs": _rate(len(failed_hits), len(failed)),
        "share_by_recorded_labels": _rate(len(hits_then), len(runs)),
        "label_breakdown": {
            label: sum(1 for row in runs if label in row["gate_hits"]) for label in GATE_LABELS
        },
        # 命中的那些 run **判没判负**：分子记的是行为（最后一步验证是红就收尾），
        # D21 关心的是代价（因此把任务做砸）。两者不等价，得单独数一遍才知道差多少。
        "gate_hits_by_verdict": _count(row["verdict"] or "unknown" for row in hits),
        "gate_hits_known_verdict": sum(1 for row in hits if row["verdict"]),
        "all_modes": _count(mode for row in runs for mode in row["modes"]),
        "verification_economics": {
            "verify_calls": sum(row["verify_calls"] for row in runs),
            "verify_output_chars": verify,
            "total_output_chars": output,
            "verify_share_of_output": _rate(verify, output),
            "per_run_share_median": round(statistics.median(shares), 4) if shares else None,
            "biggest_single_verify_p50": _pct(biggest, 0.50),
            "biggest_single_verify_p95": _pct(biggest, 0.95),
            "biggest_single_verify_max": biggest[-1] if biggest else 0,
            "spec_200_lines_chars": SPEC_VERIFIER_CHARS,
            "p95_over_spec": _rate(_pct(biggest, 0.95) or 0, SPEC_VERIFIER_CHARS),
            "by_tool": by_tool,
        },
        "termination_dist": _count(row["termination"] for row in runs),
        "runs_that_saw_red": sum(1 for row in runs if row["saw_red"]),
        "runs_saw_red_and_claimed_done": sum(
            1 for row in runs if row["saw_red"] and row["termination"] == "completed"
        ),
    }


def _rate(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return int(ordered[index])


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[str(value)] = out.get(str(value), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def build_premises(
    layers: list[Layer],
    live: dict[str, Any],
    live_runs: list[dict[str, Any]],
    per_layer: dict[str, dict[str, Any]],
    fake: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """签字之前先证明这把尺子没问题。`ok=None` 是**今天没量**，不是过了。"""
    empty = [row["path"].name for row in live_runs if not row["turns"] and not row["calls"]]
    stray = sorted({name for row in live_runs for name in row["stray_verdict"]})
    determinism = [
        row["path"].name
        for row in live_runs
        if row["mode_key"] != labels_of(replay(row["path"]))[1]
    ]
    direction_now = (live["share_of_all_runs"] or 0.0) >= GATE_D21
    direction_then = (live["share_by_recorded_labels"] or 0.0) >= GATE_D21
    econ = live["verification_economics"]
    return [
        {
            "claim": "盘上有 live 轨迹可读",
            "ok": live["runs"] > 0,
            "detail": f"{len([layer for layer in layers if layer.engine == 'live'])} 层 · {live['runs']} 份",
        },
        {
            # §6.4 那条「n < 20 禁止写百分比」的执行形式：样本不够 → ok=None（未量），
            # 而不是"过了"。闸①的百分比照产出，但带 n。
            "claim": f"live 样本量够单独签「> {GATE_D21:.0%}」这条线（n ≥ {MIN_LIVE_SAMPLE}）",
            "ok": live["runs"] >= MIN_LIVE_SAMPLE,
            "detail": f"n={live['runs']}",
        },
        {
            "claim": "每份轨迹都 replay 得出内容（没有静默的空格）",
            "ok": not empty,
            "detail": f"{len(live_runs)} 份里 {len(empty)} 份既无轮次也无调用" + (f"：{empty[:3]}" if empty else ""),
        },
        {
            "claim": "重算是确定的：同一份轨迹扫两遍，标签逐格相同",
            "ok": not determinism,
            "detail": f"不确定的轨迹：{determinism[:3] or '无'}（分类器不读消息历史、不吃时间，所以它**不该**不确定）",
        },
        {
            "claim": "『fail』这个分母有产地（来自 manifest，不是脚本自己下的判定）",
            "ok": live["runs_with_verdict"] > 0,
            "detail": f"live {live['runs_with_verdict']}/{live['runs']} 份对上了 manifest"
            "；demo-live 那层没 manifest，只进『全部 run』分母",
        },
        {
            "claim": "换尺子不翻转闸①：当前规则 vs manifest 落盘标签，过线方向一致",
            "ok": direction_now == direction_then,
            "detail": f"重算 {live['share_of_all_runs']} vs 落盘 {live['share_by_recorded_labels']}"
            f" → 两侧都判「{'过线' if direction_now else '不过线'}」"
            if live["share_by_recorded_labels"] is not None
            else f"落盘标签这一格是空的（manifest 没记），只能单侧签字：重算 {live['share_of_all_runs']}",
        },
        {
            "claim": "红绿结论只有一个产地（非验证工具不带 verdict 字段）",
            "ok": not stray,
            "detail": f"越界的 verdict 来自：{stray or '无'}",
        },
        {
            "claim": "闸②有东西可量：live 轨迹里真的发生过验证调用",
            "ok": econ["verify_calls"] > 0,
            "detail": f"{econ['verify_calls']} 次验证调用、{econ['verify_output_chars']} 字符输出",
        },
        {
            "claim": "每一层单独看过一遍（不被大层稀释，也不被小层抬高）",
            "ok": all(row["runs"] > 0 for row in per_layer.values()),
            "detail": " · ".join(
                f"{name} {row['gate_labelled_runs']}/{row['runs']}" for name, row in per_layer.items()
            ),
        },
        {
            "claim": "fake 层不进签字合计（它是手写剧本，不是模型行为）",
            "ok": (live["runs"] + (fake["runs"] if fake else 0)) == sum(row["runs"] for row in per_layer.values()),
            "detail": f"live {live['runs']} + fake {fake['runs'] if fake else 0}"
            f" = 各层之和 {sum(row['runs'] for row in per_layer.values())}"
            f"；fake 那一格的占比是 {per_layer.get('b1-fake', {}).get('share_of_all_runs')}，只作对照",
        },
        {
            # 闸②光有"验证很贵"还不够，得问贵在哪一路。§3.8 那句「200 行 pytest 输出」
            # 指的是原始 stdout；本项目有条结构化路径（run_tests 自己数通过/失败、只留
            # 失败用例名）。若过线的调用全部来自 bash，那句理由描述的就是一个已经补好的洞。
            "claim": "§3.8 的『200 行输出』有产地：越过标尺的验证调用来自哪条路径",
            "ok": None if "run_tests" not in econ["by_tool"] else econ["by_tool"]["run_tests"]["over_spec_calls"] == 0,
            "detail": " · ".join(
                f"{name} {row['calls']} 次/最大 {_fmt_chars(row['single_max'])}/过线 {row['over_spec_calls']} 次"
                for name, row in econ["by_tool"].items()
            ),
        },
        {
            # 分子测的是**行为**还是**代价**，决定这道闸在问什么。命中的 run 若全都判 pass，
            # 那 20% 这条线即使过了，加 verifier 也救不回任何一次失败 —— 因为没有失败可救。
            "claim": "闸①的分子能对上代价：命中的 run 每一份都有判据结论可查",
            "ok": live["gate_labelled_runs"] == live["gate_hits_known_verdict"],
            "detail": f"live 命中 {live['gate_labelled_runs']} 份，其中判据结论可查 "
            f"{live['gate_hits_known_verdict']} 份 → 分布 {live['gate_hits_by_verdict'] or '无'}；"
            f"fake 那格 {fake['gate_hits_by_verdict'] if fake else '无'}。"
            "分子记的是『最后一步验证是红就收尾』，与『因此判负』不是一回事",
        },
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D21 / §3.8 的数据闸：verifier 到底对症吗")
    parser.add_argument("--include-fake", action="store_true", help="把 B1 的 fake 批也列出来（默认只列 live）")
    parser.add_argument("--out", type=Path, default=RESULT)
    args = parser.parse_args(argv)

    layers = discover(include_fake=True)  # 永远发现：fake 那一格是**对照组**，不是合计的一部分
    if not layers:
        print("盘上没有可用轨迹（eval/.work/ 与 demos/traces/ 都空）", file=sys.stderr)
        return 2
    for layer in layers:
        attach_records(layer)

    runs_by_layer = {layer.name: [scan(path, layer) for path in layer.traces] for layer in layers}
    per_layer = {name: tally(runs) for name, runs in runs_by_layer.items()}
    live_runs = [row for layer in layers if layer.engine == "live" for row in runs_by_layer[layer.name]]
    fake_runs = [row for layer in layers if layer.engine == "fake" for row in runs_by_layer[layer.name]]
    live = tally(live_runs)
    fake = tally(fake_runs) if fake_runs else None
    shown = {name: row for name, row in per_layer.items() if args.include_fake or name != "b1-fake"}
    premises = build_premises(layers, live, live_runs, per_layer, fake)

    share = live["share_of_all_runs"]
    econ = live["verification_economics"]
    gate_open = share is not None and share >= GATE_D21
    cheap = (econ["verify_share_of_output"] or 0.0) < 0.10
    # 「200 行」这句话的来源：结构化那一路（run_tests）与裸转发那一路（bash）分开数。
    tool_rows = econ["by_tool"]
    structured = tool_rows.get("run_tests")
    raw_over = sum(row["over_spec_calls"] for name, row in tool_rows.items() if name != "run_tests")
    provenance = (
        f"过线的 {sum(row['over_spec_calls'] for row in tool_rows.values())} 次里，"
        f"结构化 run_tests 占 {structured['over_spec_calls'] if structured else '—'} 次"
        f"（它 {structured['calls'] if structured else 0} 次调用的最大单次输出 "
        f"{_fmt_chars(structured['single_max']) if structured else '无'}）"
        f"，其余 {raw_over} 次来自 bash 直接跑 pytest"
        if tool_rows
        else "没有验证调用"
    )
    if not gate_open:
        verdict = "cut"
    elif cheap:
        verdict = "not-the-right-lever"
    else:
        verdict = "build-it"

    decision = (
        f"闸①（D21 的 {GATE_D21:.0%}）：live 轨迹 {live['gate_labelled_runs']}/{live['runs']} 份命中 "
        f"no_verification 或 self_confirm = {_fmt(share)}（n={live['runs']}）→ "
        f"{'过线' if gate_open else '不过线'}；只看判 fail 的 run 是 {_fmt(live['share_of_failed_runs'])}"
        f"（n={live['failed_runs']}）。"
        f"而且这份命中里判 pass 的有 {live['gate_hits_by_verdict'].get('pass', 0)} 份、"
        f"判 fail 的 {live['gate_hits_by_verdict'].get('fail', 0)} 份 —— 分子记的是行为，不是代价。"
        f"闸②（§3.8 那句『200 行 pytest 输出』= {SPEC_VERIFIER_CHARS} 字符）：实测单次验证输出 "
        f"p50 {_fmt_chars(econ['biggest_single_verify_p50'])}、p95 {_fmt_chars(econ['biggest_single_verify_p95'])}"
        f"、最大 {_fmt_chars(econ['biggest_single_verify_max'])}；验证输出占工具总输出的 "
        f"{_fmt(econ['verify_share_of_output'])}（每格中位 {_fmt(econ['per_run_share_median'])}）→ "
        f"{'验证确实贵，隔离它才划算' if not cheap else '验证本来就不贵，父上下文没为它付钱'}。"
        f"拆开看：{provenance}。"
    )

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "acceptance": "S15 前置闸（D21 / §7.3-1）",
        "spec": "§3.8 / D21",
        "question": "给 agent 配一个独立上下文跑测试的 verifier 子 agent，D21 那条 20% 的线支持吗？",
        "gate": {
            "d21_label_share_line": GATE_D21,
            "min_live_sample": MIN_LIVE_SAMPLE,
            "labels_counted": list(GATE_LABELS),
            "share_denominators": {
                "all_runs": "模型有 N 次『可以不验证』的机会，所以问行为频率就用全部 run",
                "failed_runs": "问『失败里多少是这一类』才用 fail 的 run",
                "why_both": "D21 只写了『占比』没写分母，而 fake 层两个分母差出一个数量级 —— 选哪个直接换结论",
            },
        },
        "layers": {
            layer.name: {
                "engine": layer.engine,
                "traces": len(layer.traces),
                "has_manifest": bool(layer.manifest and layer.manifest.is_file()),
                "note": layer.spec_note,
            }
            for layer in layers
        },
        "per_layer": shown,
        "live_total": live,
        "fake_total": tally(fake_runs) if fake_runs else None,
        "premises": premises,
        "counts": {
            "premises": len(premises),
            "passed": sum(1 for item in premises if item["ok"] is True),
            "failed": sum(1 for item in premises if item["ok"] is False),
            "not_measured": sum(1 for item in premises if item["ok"] is None),
        },
        "verdict": verdict,
        "decision": decision,
        "what_this_proves": (
            f"真实端点跑出的 {live['runs']} 份轨迹上，用**当前**规则重算的 no_verification / "
            "self_confirm 频率（两个分母各一个），以及验证类输出在上下文里的真实字符占比 —— "
            "也就是 §3.8 那条经济学理由的数量级。"
        ),
        "what_this_does_not_prove": (
            "没证明『加 verifier 会把失败变成成功』。那要真跑一个 verifier 才知道，而这正是这道闸"
            "要省掉的成本。另外 live 样本来自 B2/B3 那几批**为别的判据设计**的题，不是为『验证行为』"
            "抽的样；样本里 6 道题只覆盖 Python + pytest，跨语言的验证行为没测过。"
        ),
        "amendments": [
            {
                "item": "D21 的『占比』",
                "spec_said": "未收尾/自我确认过早 占比 > 20% 再回来讨论 critic",
                "as_built": "两个分母各自算、各自出结论；签字用『全部 run』",
                "why": "fail-only 分母天然偏高（只有失败才会被翻出来找失败原因），拿它签字几乎永远过线",
            },
            {
                "item": "§3.8 的『200 行 pytest 输出』",
                "spec_said": "跑测试并把 200 行 pytest 输出压缩成 5 行，是上下文隔离唯一明显划算的场景",
                "as_built": f"这句话在本项目有产地、但不在结构化那条路上："
                f"run_tests 的 {structured['calls'] if structured else 0} 次调用最大单次 "
                f"{_fmt_chars(structured['single_max']) if structured else '无'}、"
                f"{structured['over_spec_calls'] if structured else 0} 次过 {SPEC_VERIFIER_CHARS} 这条线；"
                f"过线的 {raw_over} 次全部是 bash 直接跑 `python -m pytest -v`（最大 "
                f"{_fmt_chars(max([row['single_max'] for name, row in tool_rows.items() if name != 'run_tests'] or [0]))}）"
                f" —— 也就是说那个『200 行』是 agent 绕开自己的结构化工具换来的，不是工具吐出来的",
                "why": "如果隔离省下的东西本来就只出现在『已经有一条便宜替代路』的行为里，verifier 的"
                "成本收益要重算一遍：便宜的那版（把 run_tests 的输出再压一遍）省不下 200 行，"
                "而贵的那版该修的是『为什么它不用 run_tests』",
            },
            {
                "item": "D21 点名的『B1 的失败分布』",
                "spec_said": "用 B1 的失败分类复核",
                "as_built": "B1 是 fake 批（24 题 × 3），标签由手写剧本产生；脚本把它单列成对照组，签字只用 live",
                "why": "fake 里『改完没跑测试就收尾』是我安排的剧情 —— 拿它测模型行为是自己出题自己答（同 §3.5.1）",
            },
            {
                "item": "D21 的『未收尾/自我确认过早』",
                "spec_said": "这类失败占比 > 20% 再回来讨论 critic",
                "as_built": f"这句话读起来是『因此判负的 run 里有多少是这一类』，可 `self_confirm` 的判据是"
                f"行为（termination=completed 且最后一次验证是红、之后没有绿）。盘上命中的 run "
                f"live {live['gate_hits_by_verdict']}、fake {fake['gate_hits_by_verdict'] if fake else '—'}"
                f" —— 判负的有 {live['gate_hits_by_verdict'].get('fail', 0) + (fake['gate_hits_by_verdict'].get('fail', 0) if fake else 0)} 份",
                "why": "所以这道闸就算过了线也不自动构成做 verifier 的理由：verifier 救的是『失败的 run』，"
                "只有落在 verdict=fail 那一格的那部分才是可救的量。重评时的口径应是**条件在 fail 上**的那一版",
            },
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for name, row in shown.items():
        econ_row = row["verification_economics"]
        engine = next(layer.engine for layer in layers if layer.name == name)
        print(
            f"{name:14s}[{engine:4s}] run {row['runs']:3d} · fail {row['failed_runs']:2d} · "
            f"闸内 {row['gate_labelled_runs']:3d} · 对全部 run {_fmt(row['share_of_all_runs']):>7s}"
            f" · 对 fail {_fmt(row['share_of_failed_runs']):>7s}"
            f" · 验证输出占 {_fmt(econ_row['verify_share_of_output']):>7s}"
        )
    print()
    print(f"→ {decision}")
    marks = {True: "x", False: "!", None: "?"}
    for item in premises:
        print(f"  [{marks[item['ok']]}] {item['claim']} —— {item['detail']}")
    counts = payload["counts"]
    print(
        f"\n合计：{counts['passed']}/{counts['premises']} 条量过并成立"
        f"（不成立 {counts['failed']} · 未量 {counts['not_measured']}）→ verdict={verdict}"
    )
    print(f"证据：{args.out.relative_to(ROOT)}")
    return 1 if counts["failed"] else 0


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _fmt_chars(value: int | None) -> str:
    return "—" if value is None else f"{value:,} 字符"


if __name__ == "__main__":
    raise SystemExit(main())
