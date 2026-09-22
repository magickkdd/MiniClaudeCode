"""`mcc trace` —— 把日志文件变成能读懂的会话诊断。

JD 里"Agent debugging 工具"这一项的直接答案。三条设计约束：

1. **只读已有事实。** 这个命令不重新推导任何东西，`verdict` / `drops_assert`
   这些判定是 `loop.py` 写日志时就算好落盘的（定义在 `infra/failure.py`），
   所以"实时看到的"和"事后查到的"必然是同一个口径。
2. **没有 `rich` 也要能用。** 调试工具在最坏的环境（裸终端、CI 日志）里
   恰恰最需要跑得起来，因此这里只用 str.format 排版。
3. **说不出失败原因时要说"规则没看出来"**，而不是沉默 —— 没命中规则
   不等于任务做对了。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from miniclaude.config import Config
from miniclaude.infra.failure import Finding, RunFacts, classify, facts_from_records
from miniclaude.infra.trace import (
    SCHEMA_VERSION,
    hotspots,
    of_session,
    replay,
    sessions,
    summarize_records,
)

MAX_ROWS = 40


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcc trace",
        description="读会话日志：时间线 / 热点 / 失败模式。",
    )
    parser.add_argument("session", nargs="?", default=None, help="会话 id（省略时用 --latest）")
    parser.add_argument("--latest", action="store_true", help="最近一次会话，无需知道 id")
    parser.add_argument("--hot", action="store_true", help="最贵 3 轮、报错最多的工具、重复调用簇")
    parser.add_argument("--why-failed", action="store_true", help="失败模式标签 + 证据 + 处方")
    parser.add_argument("--json", dest="as_json", action="store_true", help="输出机器可读的汇总")
    parser.add_argument("--file", type=Path, default=None, help="日志路径（默认取 TRACE_PATH）")
    parser.add_argument("--limit", type=int, default=MAX_ROWS, help="时间线最多显示多少轮")
    return parser


def run(argv: Sequence[str], config: Config) -> int:
    args = build_parser().parse_args(argv)
    path = args.file or config.trace_path
    if path is None:
        print("没有可读的日志：TRACE_PATH 未配置，运行时会话也不会写日志（或用 --file 指定）。")
        return 2
    path = Path(path)
    if not path.is_file():
        print(f"日志不存在：{path}")
        return 2

    records = replay(path)
    if not records:
        print(f"日志是空的：{path}")
        return 2

    session_id = args.session or _latest_session(records)
    selected = of_session(records, session_id)
    if not selected:
        known = sessions(path)
        asked = args.session or session_id
        print(f"找不到会话 {asked!r}。这个文件里有 {len(known)} 个会话：{', '.join(known[-10:])}")
        return 2

    if args.as_json:
        print(json.dumps(summarize_records(selected), ensure_ascii=False, indent=2, default=str))
        return 0
    print(_render_records(selected, path, args))
    return 0


def _latest_session(records: list[dict[str, Any]]) -> str:
    """`run_end` 之后才算"下一次会话"，所以取最后一条带 session 的记录。"""
    for record in reversed(records):
        session = record.get("session")
        if session:
            return str(session)
    return ""


def _render_records(records: list[dict[str, Any]], path: Path, args: argparse.Namespace) -> str:
    lines = [f"日志 {path} · 会话 {records[0].get('session')} · schema {records[0].get('schema_version')}"]
    if records[0].get("schema_version") != SCHEMA_VERSION:
        found = records[0].get("schema_version") or "无版本号"
        lines.append(
            f"注意：这份日志的 schema 不是 {SCHEMA_VERSION}（读到的是 {found}）。"
            "`context_growth` / `self_confirm` / `test_gaming` 依赖 `turn_start.est_tokens`、"
            "`tool_call.output_chars` 与 `tool_call.verdict`，旧轨迹里没有这些字段时它们不会命中 —— "
            "是没参与，不是判对了。"
        )
    end = next((r for r in reversed(records) if r.get("kind") == "run_end"), {})
    if end:
        executed = sum(1 for r in records if r.get("kind") == "tool_call")
        lines.append(
            "结束于 {term} · {turn} 轮 · 发起 {calls} 次调用（执行 {exec} 次、报错 {errors} 次、"
            "重复 {rep} 次、整组重演 {stall} 次） · {tok:,} tokens · {ms} ms".format(
                term=end.get("termination", "unknown"),
                turn=end.get("turn", 0),
                calls=end.get("tool_calls", 0),
                exec=executed,
                errors=end.get("tool_errors", 0),
                rep=end.get("repeated_calls", 0),
                stall=end.get("stalled_groups", 0),
                tok=(end.get("usage") or {}).get("total", 0),
                ms=end.get("wall_ms", 0),
            )
        )
        if end.get("cost_est") is not None:
            lines.append(f"成本估算 {end['cost_est']}（按 PRICE_PER_MTOKENS 折算；未配单价时这一行不出现）")

    facts = facts_from_records(records)
    findings = classify(facts)
    stored = end.get("failure_modes")
    if isinstance(stored, list):
        now = [found.mode.value for found in findings]
        if now != stored:
            lines.append(
                f"规则口径变过：当时落盘 {'、'.join(stored) or '（无标签）'}，"
                f"现在算出 {'、'.join(now) or '（无标签）'}。"
                "`drops_assert` 这类结论是写日志时算好落盘的，旧 trace 不重算 —— "
                "要按当前规则判读就重跑一次。"
            )
    lines.append("")

    if args.why_failed:
        return "\n".join(lines + _why_failed(facts, findings))
    if args.hot:
        return "\n".join(lines + _hot(records))
    return "\n".join(lines + _timeline(records, args.limit))


def _timeline(records: list[dict[str, Any]], limit: int) -> list[str]:
    turns = {int(r.get("turn") or 0): r for r in records if r.get("kind") == "turn_start"}
    responses = {int(r.get("turn") or 0): r for r in records if r.get("kind") == "llm_response"}
    calls: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("kind") == "tool_call":
            calls.setdefault(int(record.get("turn") or 0), []).append(record)

    out = ["轮次  上下文→  回复  工具调用                                    延迟"]
    for turn in sorted(turns)[: max(0, limit)]:
        est = turns[turn].get("est_tokens", 0)
        used = (responses.get(turn, {}).get("usage") or {}).get("completion", 0)
        latency = responses.get(turn, {}).get("latency")
        summary = " ".join(
            f"{item.get('name')}{'!' if not item.get('ok', True) else ''}" for item in calls.get(turn, [])
        )
        out.append(f"{turn:>4}  {est:>7,}  {used:>5,}  {summary:<44}  {'' if latency is None else f'{latency}s'}")
    if len(turns) > limit:
        out.append(f"…另有 {len(turns) - limit} 轮未显示（--limit 调大）")
    return out


def _hot(records: list[dict[str, Any]]) -> list[str]:
    data = hotspots(records)
    out = ["最贵的 3 轮（按该轮 usage 合计）"]
    for item in data["costliest_turns"]:
        out.append(
            f"  第 {item['turn']} 轮 · {item['tokens']:,} tokens · {item['tool_calls']} 次调用"
            f"（{item['errors']} 次报错）· 上下文估算 {item['est_tokens']:,}"
        )
    out.append("")
    out.append("报错最多的工具")
    if not data["error_prone_tools"]:
        out.append("  （没有工具报错）")
    for name, count in data["error_prone_tools"]:
        out.append(f"  {name} × {count}")
    out.append("")
    out.append("重复调用簇（同名同参连续 ≥2 次）")
    clusters = data["repeated_clusters"]
    if not clusters:
        out.append("  （没有连续重复）")
    for item in clusters[:5]:
        args = item["args"] if len(item["args"]) <= 90 else item["args"][:90] + "…"
        out.append(f"  {item['name']} × {item['count']}（第 {item['turns']} 轮）{args}")
    return out


def _why_failed(facts: RunFacts, findings: list[Finding]) -> list[str]:
    out = []
    if not findings:
        out.append(
            f"没有命中任何失败模式规则（终止于 {facts.termination}）。"
            "注意：这只说明规则看不出来，不代表任务做对了。"
        )
        return out
    for index, found in enumerate(findings, 1):
        out.append(f"{index}. {found.line()}")
    return out
