"""轨迹 JSONL → OTLP/JSON：一层翻译，不是一个架构前提。

SPEC v2 §3.1 立的规矩是：埋点代码不许 import OTel SDK，字段命名对齐 GenAI 语义约定，
于是"能接 Jaeger"这件事就只剩导出器里的一层映射（Tier 3 #3）。这里就是那一层。

三条纪律：

1. **翻译不发明结构。** trace 里一个 `span_id` = OTLP 一个 span。盘上的真实形状是三种
   合并：`run_start`+`run_end` 同一个 span、`turn_start`+`llm_request`+`llm_response` 同一个、
   `permission`+`tool_call` 同一个。没有 `span_id` 的 kind（`backend` / `failure_mode` /
   `mcp` / `skills` / `todo_update`，以及 v1 时代的全部记录）挂成所属 span 的 **event**，
   不静默丢弃 —— "一条记录都不许丢"由 `test_no_record_is_lost` 机器化。
2. **每个字段都要有一个登记过的名字。** `FIELD_MAP` 逐 kind 把 trace 键映射到 `gen_ai.*`
   或 `mcc.*`；表里没有的键走 `mcc.<kind>.<key>` 兜底，但快照那 20 个 kind 的每个键都必须
   在表里出现过 —— 加字段不登记就是 CI 红（`test_every_traced_field_has_a_name`）。
3. **翻不过去的东西要写下来。** `AnyValue` 没有 null、嵌套结构只有 JSON 字符串一种稳定
   表达、`ts` 是"写日志那一刻"而不是区间起点。这些进 `gaps()`，消费方读到的是限制，
   不是猜出来的完整。

时间是反推的：`latency` 在调用返回之后才测得，所以起点是 `ts - latency`。每个 span 带一个
`mcc.span.time_source` 说明这个区间是量出来的、减出来的，还是压根没有时间戳。

导出是纯函数：同一批记录两次 `translate()` 字节相同（`test_payload_is_deterministic`）。
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from miniclaude.infra.trace import SCHEMA_VERSION, _SECRET_LIKE

SERVICE_NAME = "miniclaude"
SPAN_KIND_INTERNAL = 1
STATUS_OK = 1
STATUS_ERROR = 2

# 每条记录都有的信封字段：它们落在 OTLP 的结构位上（id / 时间 / resource 属性），
# 不进 `FIELD_MAP`，所以完整性测试要先减掉它们。
ENVELOPE_KEYS = frozenset(
    {"seq", "ts", "kind", "session", "trace_id", "schema_version", "span_id", "parent_span_id"}
)

# 没有 `span_id` 的 kind —— 它们只能是 event，不能是 span。
EVENT_KINDS = frozenset({"backend", "failure_mode", "mcp", "skills", "todo_update"})

# trace 键 → OTLP 属性名。`gen_ai.*` 是 OTel GenAI 语义约定里的名字，`mcc.*` 是我们自己的。
# 值是 dict 时表示这条记录里那个键是个映射（只有 `usage` 一处），按子键逐个改名。
FIELD_MAP: dict[str, dict[str, Any]] = {
    "backend": {
        "backend": "mcc.backend.name",
        "turn": "mcc.turn.number",
        "requested": "mcc.backend.requested",
        "shell": "mcc.backend.shell",
        "isolated": "mcc.backend.isolated",
        "degraded": "mcc.backend.degraded_reason",
        "checkpoints": "mcc.backend.checkpoints",
        "note": "mcc.backend.note",
    },
    "checkpoint": {
        "turn": "mcc.turn.number",
        "backend": "mcc.backend.name",
        "rev": "mcc.checkpoint.rev",
        "tool": "mcc.checkpoint.trigger_tool",
        "ok": "mcc.checkpoint.ok",
        "note": "mcc.checkpoint.note",
    },
    "context_compact": {
        "turn": "mcc.turn.number",
        "level": "mcc.compact.level",
        "before_est": "mcc.compact.before_est_tokens",
        "after_est": "mcc.compact.after_est_tokens",
        "saved_est": "mcc.compact.saved_est_tokens",
        "dropped_blocks": "mcc.compact.dropped_blocks",
        "elided_blocks": "mcc.compact.elided_blocks",
        "summary_tokens": "mcc.compact.summary_tokens",
        "summary_files": "mcc.compact.summary_files",
        "pairing_ok": "mcc.compact.pairing_ok",
        "note": "mcc.compact.note",
    },
    "context_refuse": {
        "turn": "mcc.turn.number",
        "est_tokens": "mcc.context.est_tokens",
        "budget_tokens": "mcc.context.budget_tokens",
        "hard_limit_tokens": "mcc.context.hard_limit_tokens",
        "threshold_tokens": "mcc.context.threshold_tokens",
        "line": "mcc.context.refused_at_line",
        "pressure": "mcc.context.pressure",
        "ladder_enabled": "mcc.context.ladder_enabled",
    },
    "error": {
        "turn": "mcc.turn.number",
        "layer": "mcc.error.layer",
        "message": "mcc.error.message",
    },
    "failure_mode": {
        "turn": "mcc.turn.number",
        "mode": "mcc.failure.mode",
        "why": "mcc.failure.why",
        "prescription": "mcc.failure.prescription",
    },
    "llm_request": {
        "turn": "mcc.turn.number",
        "est_tokens": "mcc.context.est_tokens",
        "message_count": "mcc.context.message_count",
        "tools_count": "gen_ai.request.tool_count",
        "prefix_hash": "mcc.llm.prefix_hash",
    },
    "llm_response": {
        "turn": "mcc.turn.number",
        "usage": {
            "prompt": "gen_ai.usage.input_tokens",
            "completion": "gen_ai.usage.output_tokens",
            "total": "gen_ai.usage.total_tokens",
        },
        "stop_reason": "gen_ai.response.finish_reasons",
        "blocks": "mcc.llm.block_types",
        "id_repairs": "mcc.llm.id_repairs",
        "latency": "mcc.llm.latency_seconds",
    },
    "mcp": {
        "turn": "mcc.turn.number",
        "configured": "mcc.mcp.configured",
        "running": "mcc.mcp.running",
        "skipped": "mcc.mcp.skipped",
        "skip_reason": "mcc.mcp.skip_reason",
        "tools": "mcc.mcp.tools",
        "servers": "mcc.mcp.servers",
    },
    "permission": {
        "turn": "mcc.turn.number",
        "tool": "gen_ai.tool.name",
        "decision": "mcc.permission.decision",
        "rule_hit": "mcc.permission.rule_hit",
    },
    "repo_map": {
        "turn": "mcc.turn.number",
        "chars": "mcc.repo_map.chars",
        "est_tokens": "mcc.repo_map.est_tokens",
        "token_cap": "mcc.repo_map.token_cap",
        "modules_parsed": "mcc.repo_map.modules_parsed",
        "modules_found": "mcc.repo_map.modules_found",
        "listed": "mcc.repo_map.listed",
        "omitted": "mcc.repo_map.omitted",
        "unparsable": "mcc.repo_map.unparsable",
        "from_cache": "mcc.repo_map.from_cache",
        "rebuilt": "mcc.repo_map.rebuilt",
        "focus": "mcc.repo_map.focus",
        "reasons": "mcc.repo_map.omission_reasons",
        "note": "mcc.repo_map.note",
    },
    "run_end": {
        "turn": "mcc.turn.number",
        "termination": "mcc.run.termination",
        "status": "mcc.run.status",
        "usage": {
            "prompt": "gen_ai.usage.input_tokens",
            "completion": "gen_ai.usage.output_tokens",
            "total": "gen_ai.usage.total_tokens",
        },
        "wall_ms": "mcc.run.wall_ms",
        "cost_est": "mcc.run.cost_estimate",
        "tool_calls": "mcc.run.tool_call_attempts",
        "tool_errors": "mcc.run.tool_errors",
        "repeated_calls": "mcc.run.repeated_calls",
        "stalled_groups": "mcc.run.stalled_groups",
        "denied_actions": "mcc.run.denied_actions",
        "context_peak_tokens": "mcc.context.peak_tokens",
        "context_compactions": "mcc.compact.count",
        "context_elided_blocks": "mcc.compact.elided_blocks",
        "context_summary_tokens": "mcc.compact.summary_tokens",
        "failure_modes": "mcc.failure.modes",
        "todos": "mcc.run.todos",
    },
    "run_start": {
        "run_id": "mcc.run.id",
        "task_id": "mcc.run.task_id",
        "resumed_from": "mcc.run.resumed_from",
        "user_input_chars": "mcc.run.user_input_chars",
    },
    "session_replay": {
        "turn": "mcc.turn.number",
        "name": "gen_ai.tool.name",
        "tool_use_id": "gen_ai.tool.call.id",
        "why": "mcc.replay.why",
    },
    "session_snapshot": {
        "turn": "mcc.turn.number",
        "ok": "mcc.snapshot.ok",
        "writes": "mcc.snapshot.writes",
        "done_calls": "mcc.snapshot.done_calls",
        "last_rev": "mcc.snapshot.last_rev",
    },
    "session_start": {
        "model": "gen_ai.request.model",
        "tools": "mcc.tools.declared",
        "config": "mcc.session.config",
        "system_prompt_hash": "mcc.system_prompt.hash",
    },
    "skills": {
        "turn": "mcc.turn.number",
        "root": "mcc.skills.root",
        "skills": "mcc.skills.catalog",
        "names": "mcc.skills.loaded",
        "rejected": "mcc.skills.rejected",
        "catalog_lines": "mcc.skills.catalog_lines",
        "catalog_chars": "mcc.skills.catalog_chars",
        "body_chars_total": "mcc.skills.body_chars_total",
        "cap_lines": "mcc.skills.cap_lines",
    },
    "todo_update": {
        "turn": "mcc.turn.number",
        "items": "mcc.todo.items",
    },
    "tool_call": {
        "turn": "mcc.turn.number",
        "name": "gen_ai.tool.name",
        "tool_use_id": "gen_ai.tool.call.id",
        "args": "gen_ai.tool.call.arguments",
        "risk": "mcc.tool.risk",
        "ok": "mcc.tool.ok",
        "output_chars": "mcc.tool.output_chars",
        "latency": "mcc.tool.latency_seconds",
        "verdict": "mcc.tool.verdict",
        "drops_assert": "mcc.tool.drops_assertions",
    },
    "turn_start": {
        "turn": "mcc.turn.number",
        "est_tokens": "mcc.context.est_tokens",
        "message_count": "mcc.context.message_count",
    },
}

# span 名字按"角色"取，而不是按 kind：合并后的 span 里第一个 kind 未必是命名那个。
_ROLE_OF_KIND = {
    "session_start": "session",
    "run_start": "run",
    "run_end": "run",
    "turn_start": "turn",
    "llm_request": "turn",
    "llm_response": "turn",
    "permission": "tool",
    "tool_call": "tool",
}


@dataclass
class Span:
    """一个 OTLP span，还带着它吃掉的那些记录 —— 记账和测试都要用。"""

    span_id: str
    trace_id: str
    name: str
    role: str
    session: str = ""
    parent_span_id: str | None = None
    start_ns: int = 0
    end_ns: int = 0
    time_source: str = "record-timestamps"
    detached_parent: str | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


def translate(records: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """JSONL 记录 → (OTLP/JSON payload, 记账)。"""
    spans = spans_of(records)
    return render(spans), coverage(records, spans)


def spans_of(records: list[dict[str, Any]]) -> list[Span]:
    """按 `span_id` 分组建 span，没有 id 的记录挂到所属 span 的 event 上。"""
    clean = [r for r in records if isinstance(r, dict)]
    groups: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
    headless: list[dict[str, Any]] = []
    for record in clean:
        raw = record.get("span_id")
        if isinstance(raw, str) and raw:
            groups.setdefault(_trace_hex(raw, 16), []).append(record)
        else:
            headless.append(record)

    spans = [_make_span(span_id, group) for span_id, group in groups.items()]
    orphans: dict[str, Span] = {}
    # 根必须**按 trace 各算各的**。一个日志文件可以躺着好几个会话（连续跑 `--task`、
    # 多客户端并排），拿全文件第一个 session span 当唯一锚点，会把后面几个会话的 span
    # 挂到前一个会话的树上 —— 那是跨 trace 编出一棵树，收集端画出来是断头的。
    # 这个 bug 在只有单会话轨迹的批次里看不见，第一次被 `mcp-link-t3-standard.jsonl`
    # （6 个会话躺在同一个文件）钉出来。
    roots: dict[str, Span] = {}
    for role in ("session", "run"):
        for span in spans:
            if span.parent_span_id is None and span.role == role:
                # 同一 trace 里 `session` 优先于 `run`；同级多个候选取最早那条。
                # 选根不能依赖"先遇到哪个" —— span 的顺序由记录顺序决定，
                # 倒着喂同一批记录必须长出同一棵树（`test_payload_is_deterministic`）。
                current = roots.get(span.trace_id)
                if current is None or _seq(span.records[0]) < _seq(current.records[0]):
                    roots[span.trace_id] = span
    fallback = next((s for s in spans if s.role == "session"), None) or next(
        (s for s in spans if s.role == "run"), None
    ) or (spans[0] if spans else None)

    known = {s.span_id for s in spans}
    for span in spans:
        if span.parent_span_id is not None and span.parent_span_id not in known:
            # 父亲不在这批记录里（会话被截断、或者手工改过文件）。宁可不挂也不编一个父亲出来，
            # 但那个断掉的 id 要留在属性里 —— 丢了它就没有任何线索可查。
            span.detached_parent = span.parent_span_id
            span.parent_span_id = None
        elif span.parent_span_id is None:
            # `run_start` 与 `session_start` 都不写 parent（会话只有一个 run），
            # 但层级要成树，所以缺父亲的统一挂到**本条 trace 自己的**根上；
            # 本 trace 连 session/run span 都没有时，它自己就是根（parent 留空，不硬凑）。
            root = roots.get(span.trace_id)
            span.parent_span_id = root.span_id if root is not None and root is not span else None

    model = _model_of(clean)
    for span in spans:
        if span.role == "run":
            span.name = f"invoke_agent {model}" if model else "invoke_agent"
        elif span.role == "turn" and model:
            span.name = f"chat {model}"

    for record in headless:
        host = _host_for(record, spans, roots, fallback)
        if host is None:
            # 一个 span 都没有（v1 轨迹全是无 id 记录）：造一个承载 span，
            # 否则这些记录就只能被丢掉 —— 而丢掉记录正是这里唯一不允许的事。
            trace = _trace_hex(str(record.get("trace_id") or record.get("session") or "unknown"), 32)
            host = orphans.get(trace)
            if host is None:
                host = _orphan_span(str(record.get("session") or "unknown"), trace)
                orphans[trace] = host
                spans.append(host)
        host.events.append(record)
        _absorb(host, record)
    return spans


def _make_span(span_id: str, group: list[dict[str, Any]]) -> Span:
    roles = {_ROLE_OF_KIND.get(str(r.get("kind")), "detail") for r in group}
    role = next(iter(roles)) if len(roles) == 1 else "merged"
    kinds = [str(r.get("kind")) for r in group]
    parent = next(
        (_trace_hex(r["parent_span_id"], 16) for r in group if isinstance(r.get("parent_span_id"), str) and r["parent_span_id"]),
        None,
    )
    start, end, source = _span_time(group)
    return Span(
        span_id=span_id,
        trace_id=_trace_hex(str(group[0].get("trace_id") or _session_of(group)), 32),
        name=_span_name(role, kinds, group),
        role=role,
        session=_session_of(group),
        parent_span_id=parent,
        start_ns=start,
        end_ns=end,
        time_source=source,
        records=list(group),
    )


def _span_name(role: str, kinds: list[str], group: list[dict[str, Any]]) -> str:
    if role == "session":
        return "agent.session"
    if role == "run":
        return "invoke_agent"
    if role == "turn":
        return "chat"
    if role == "tool":
        name = next(
            (str(r.get("name") or r.get("tool") or "?") for r in group if r.get("kind") == "tool_call"),
            next((str(r.get("tool")) for r in group if r.get("tool")), "?"),
        )
        return f"execute_tool {name}"
    if role == "detail":
        return f"mcc.{kinds[0]}"
    return "mcc." + "+".join(sorted(set(kinds)))


def _model_of(records: list[dict[str, Any]]) -> str:
    for record in records:
        if record.get("kind") == "session_start" and isinstance(record.get("model"), str):
            return str(record["model"])
    return ""


def _session_of(group: list[dict[str, Any]]) -> str:
    for record in group:
        session = record.get("session")
        if session:
            return str(session)
    return "unknown"


def _host_for(
    record: dict[str, Any],
    spans: list[Span],
    roots: dict[str, Span],
    fallback: Span | None,
) -> Span | None:
    """无 span_id 的记录找归属：先看同一轮的 turn span，再退到它前面的 run span。

    只在本会话里找 —— 一个日志文件可以躺着好几个会话，把它们混成一棵树比扁平更难读。
    """
    turn = record.get("turn")
    seq = _seq(record)
    session = str(record.get("session") or "")
    mine = [s for s in spans if not session or not s.session or s.session == session]
    for span in reversed(mine):
        if span.role == "turn" and isinstance(turn, (int, float)) and _record_turn(span) == turn and _seq(span.records[0]) <= seq:
            return span
    best: Span | None = None
    for span in mine:
        if span.role in ("session", "run") and _seq(span.records[0]) <= seq:
            if best is None or _seq(span.records[0]) >= _seq(best.records[0]):
                best = span
    if best is not None:
        return best
    # 退路也要限定在本 trace 内：拿别的会话的 span 当宿主，等于凭空造一条跨 trace 的边。
    trace = _trace_hex(str(record.get("trace_id") or record.get("session") or "unknown"), 32)
    root = roots.get(trace)
    if root is not None:
        return root
    if fallback is not None and fallback.trace_id == trace:
        return fallback
    return None


def _record_turn(span: Span) -> Any:
    for record in span.records:
        if "turn" in record:
            return record["turn"]
    return None


def _seq(record: dict[str, Any]) -> int:
    try:
        return int(record.get("seq") or 0)
    except (TypeError, ValueError):
        return 0


def _orphan_span(session: str, trace: str) -> Span:
    """一份全是无 id 记录的轨迹（v1）只需要一个宿主 span，id 由 trace 派生：确定性，
    而且同一份文件两次导出得到同一个 id。"""
    return Span(
        span_id=hashlib.sha256(f"orphan:{trace}".encode()).hexdigest()[:16],
        trace_id=trace,
        name="mcc.unhosted",
        role="orphan",
        session=session,
        time_source="absent",
    )


def _absorb(span: Span, record: dict[str, Any]) -> None:
    """把 event 的时间收进宿主 span 的区间 —— 它确实发生在这段跨度之内。"""
    stamp = _ts(record)
    if stamp is None:
        return
    if span.start_ns == 0 and span.end_ns == 0:
        span.start_ns = span.end_ns = stamp
        span.time_source = "record-timestamps"
        return
    span.start_ns = min(span.start_ns, stamp)
    span.end_ns = max(span.end_ns, stamp)


def _span_time(group: list[dict[str, Any]]) -> tuple[int, int, str]:
    """起点是 `ts - latency`：延迟在返回之后才测得，不减掉它 span 就只有终点。"""
    stamps = [_ts(r) for r in group]
    if all(s is None for s in stamps):
        return 0, 0, "absent"
    starts = [s for s in stamps if s is not None]
    start, end = min(starts), max(starts)
    source = "record-timestamps"
    for record in group:
        latency = record.get("latency")
        stamp = _ts(record)
        if stamp is None or not isinstance(latency, (int, float)) or isinstance(latency, bool):
            continue
        if latency <= 0:
            continue
        start = min(start, stamp - int(round(float(latency) * 1_000_000_000)))
        end = max(end, stamp)
        source = "latency-reconstructed"
    return start, max(start, end), source


def _ts(record: dict[str, Any]) -> int | None:
    value = record.get("ts")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(float(value) * 1_000_000_000))


def _trace_hex(value: str, width: int) -> str:
    """OTLP 的 id 只能是 16/32 位小写十六进制，且不许是全零。

    我们的 `new_trace_id()`/`new_span_id()` 本来就生成长度对的 hex，所以这条路径正常不触发；
    它挡的是手工改过的、或者别的来源的轨迹。改过的都走 sha256 派生：同一份输入永远同一个
    id，因此合并关系不变、两次导出字节相同。
    """
    text = value.strip().lower()
    if len(text) == width and all(c in "0123456789abcdef" for c in text) and set(text) != {"0"}:
        return text
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:width]


# ---------------------------------------------------------------- 渲染

def render(spans: list[Span]) -> dict[str, Any]:
    """OTLP/JSON：`resourceSpans → scopeSpans → spans`，一个 traceId 一个 scopeSpans。"""
    by_trace: "OrderedDict[str, list[Span]]" = OrderedDict()
    for span in spans:
        by_trace.setdefault(span.trace_id, []).append(span)
    resource = {
        "attributes": [
            _attribute(key, value)
            for key, value in _resource(spans).items()
            if value is not None and value != ""
        ]
    }
    return {
        "resourceSpans": [
            {
                "resource": resource,
                "scopeSpans": [
                    {
                        "scope": {"name": SERVICE_NAME, "version": SCHEMA_VERSION},
                        "spans": [_span_payload(span) for span in group],
                    }
                    for group in by_trace.values()
                ],
            }
        ]
    }


def _resource(spans: list[Span]) -> dict[str, Any]:
    """资源属性描述整条轨迹，所以 records 和 events 都要扫：v1 轨迹一条记录都没有 span_id，
    全住在 events 里，只扫 records 会把"一个会话三个模型"报成 0 个 —— 那是编出来的数，
    不是缺席的数。"""
    records = [r for s in spans for r in (*s.records, *s.events)]
    sessions = [str(r.get("session")) for r in records if r.get("session")]
    models = sorted({str(r.get("model")) for r in records if r.get("kind") == "session_start" and r.get("model")})
    versions = sorted({str(r.get("schema_version")) for r in records if r.get("schema_version")})
    return {
        "service.name": SERVICE_NAME,
        # 没有一条记录自报版本就不写：拿当前版本替 v1 轨迹填一个 "2.0" 是替它撒的谎
        # （`scope.version` 才是我们自己的版本，那一条不用它开口）。
        "mcc.trace.schema_version": "+".join(versions),
        "mcc.session.count": len(dict.fromkeys(sessions)),
        "gen_ai.request.model": "+".join(models),
    }


def _span_payload(span: Span) -> dict[str, Any]:
    attributes = _span_attributes(span)
    payload: dict[str, Any] = {
        "traceId": span.trace_id,
        "spanId": span.span_id,
        "name": span.name,
        "kind": SPAN_KIND_INTERNAL,
        "startTimeUnixNano": str(span.start_ns),
        "endTimeUnixNano": str(span.end_ns),
        "attributes": attributes,
        "status": {"code": STATUS_ERROR if _is_error(span) else STATUS_OK},
    }
    if span.parent_span_id:
        payload["parentSpanId"] = span.parent_span_id
    if span.events:
        payload["events"] = [_event_payload(record) for record in span.events]
    return payload


def _event_payload(record: dict[str, Any]) -> dict[str, Any]:
    kind = str(record.get("kind"))
    attributes = _attributes_for(kind, record)
    attributes[f"mcc.{kind}.record_seq"] = _seq(record)
    stamp = _ts(record) or 0
    return {
        "name": f"mcc.{kind}",
        "timeUnixNano": str(stamp),
        "attributes": [_attribute(key, value) for key, value in attributes.items()],
    }


def _span_attributes(span: Span) -> list[dict[str, Any]]:
    merged: dict[str, Any] = {}
    for record in span.records:
        kind = str(record.get("kind"))
        attrs = _attributes_for(kind, record)
        attrs[f"mcc.{kind}.record_seq"] = _seq(record)
        for key, value in attrs.items():
            _put(merged, key, value, kind)
    kinds = sorted({str(r.get("kind")) for r in span.records})
    if kinds:
        merged["mcc.span.record_kinds"] = "+".join(kinds)
    merged["mcc.span.time_source"] = span.time_source
    if span.detached_parent:
        merged["mcc.span.detached_parent_id"] = span.detached_parent
    _add_semconv(merged, span)
    return [_attribute(key, value) for key, value in merged.items()]


def _put(merged: dict[str, Any], key: str, value: Any, kind: str) -> None:
    """同名字段撞车时不覆盖，改挂 kind 前缀 —— 合并之后谁的账都不能糊。

    真会撞：`turn_start.est_tokens` 与 `llm_request.est_tokens` 同一个 span 里两个名字，
    压缩之后两者不相等，而"差多少"恰恰是要看的东西。
    """
    if key not in merged:
        merged[key] = value
        return
    if merged[key] == value:
        return
    renamed = _fallback(kind, key)
    if renamed not in merged:
        merged[renamed] = value
        return
    index = 2
    while f"{renamed}#{index}" in merged:
        index += 1
    merged[f"{renamed}#{index}"] = value


def _fallback(kind: str, key: str) -> str:
    bare = key[4:] if key.startswith("mcc.") else key.replace(".", "_")
    return f"mcc.{kind}.{bare}"


def _is_error(span: Span) -> bool:
    """红不红只看记录里已经写下来的判定，不在导出层重新推断。

    被权限门拒绝的调用，`tool_call.ok` 本来就是 False，所以这里不需要认 `decision`；
    `run_end.status == "aborted"` 是止损（轮数/token 用尽），那也是这一层的红。
    """
    for record in span.records:
        if record.get("kind") == "error":
            return True
        if record.get("ok") is False:
            return True
        if record.get("kind") == "context_refuse":
            return True
        if record.get("pairing_ok") is False:
            return True
        if record.get("kind") == "run_end" and record.get("status") == "aborted":
            return True
    return False


def _add_semconv(attributes: dict[str, Any], span: Span) -> None:
    """`gen_ai.operation.name` 与 `gen_ai.conversation.id` 从角色来，不从某个字段来。"""
    session = next(
        (str(r.get("session")) for r in (*span.records, *span.events) if r.get("session")),
        None,
    )
    if session:
        attributes.setdefault("gen_ai.conversation.id", session)
    if span.role == "run":
        attributes["gen_ai.operation.name"] = "invoke_agent"
    elif span.role == "turn":
        attributes["gen_ai.operation.name"] = "chat"
    elif span.role == "tool":
        attributes["gen_ai.operation.name"] = "execute_tool"


def _attributes_for(kind: str, record: dict[str, Any]) -> dict[str, Any]:
    """trace 键 → 属性名。`None` 在这里就被丢掉：`AnyValue` 没有 null 分支，
    与其塞一个假的空串，不如让属性缺席 —— 缺席是被数过的（`coverage.unrepresentable_nulls`）。"""
    table = FIELD_MAP.get(kind, {})
    out: dict[str, Any] = {}
    for key, value in record.items():
        if key in ENVELOPE_KEYS:
            continue
        target = table.get(key)
        if isinstance(target, dict):
            for sub, name in target.items():
                if isinstance(value, dict) and value.get(sub) is not None:
                    out[name] = value[sub]
            if isinstance(value, dict):
                for sub, sub_value in value.items():
                    if sub not in target and sub_value is not None:
                        out[f"mcc.{kind}.{key}.{sub}"] = sub_value
        elif isinstance(target, str):
            if value is not None:
                out[target] = value
        elif value is not None:
            out[f"mcc.{kind}.{key}"] = value
    return out


def _attribute(key: str, value: Any) -> dict[str, Any]:
    return {"key": key, "value": _value(value) or {"stringValue": "<unrepresentable>"}}


def _value(value: Any) -> dict[str, Any] | None:
    """Python 值 → OTLP `AnyValue`。`None` 返回 None：协议里没有 null，这条进 `gaps()`。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": _mask(value)}
    if isinstance(value, (list, tuple)):
        items = [_value(item) for item in value]
        if any(item is None or "arrayValue" in item for item in items):
            return {"stringValue": _json(value)}
        return {"arrayValue": {"values": items}}
    if isinstance(value, dict):
        return {"stringValue": _json(value)}
    return {"stringValue": _mask(str(value))}


def _json(value: Any) -> str:
    return _mask(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _mask(text: str) -> str:
    return _SECRET_LIKE.sub(lambda m: m.group(0)[:4] + "***", text)


def coverage(records: list[dict[str, Any]], spans: list[Span]) -> dict[str, Any]:
    """一条记录都不许丢 —— 这个账本就是把"没丢"变成能加、能核对的两个数。"""
    folded = sum(len(span.records) for span in spans)
    events = sum(len(span.events) for span in spans)
    kinds: dict[str, int] = {}
    for record in records:
        kinds[str(record.get("kind"))] = kinds.get(str(record.get("kind")), 0) + 1
    nulls = [
        f"{record.get('kind')}.{key}"
        for record in records
        for key, value in record.items()
        if value is None and key not in ENVELOPE_KEYS
    ]
    return {
        "records_in": len([r for r in records if isinstance(r, dict)]),
        "spans_out": len(spans),
        "records_folded_into_spans": folded,
        "records_emitted_as_events": events,
        "accounted_for": folded + events,
        "unaccounted": len([r for r in records if isinstance(r, dict)]) - folded - events,
        "roles": _role_counts(spans),
        "kinds": kinds,
        "zero_duration_spans": sum(1 for s in spans if s.start_ns == s.end_ns),
        "time_reconstructed_spans": sum(1 for s in spans if s.time_source == "latency-reconstructed"),
        "spans_without_timestamps": sum(1 for s in spans if s.time_source == "absent"),
        "roots": sum(1 for s in spans if not s.parent_span_id),
        "red_spans": sum(1 for s in spans if _is_error(s)),
        "detached_parents": sum(1 for s in spans if s.detached_parent),
        "unrepresentable_nulls": len(nulls),
        "null_fields": sorted(set(nulls))[:20],
    }


def _role_counts(spans: list[Span]) -> dict[str, int]:
    out: dict[str, int] = {}
    for span in spans:
        out[span.role] = out.get(span.role, 0) + 1
    return out


# ---------------------------------------------------------------- 校验

def validate(payload: dict[str, Any]) -> list[str]:
    """收集端会拒的东西，这里先自己拒掉。空列表 = 这份 payload 可以发。

    规则按 OTLP/JSON 的编码要求写：id 是定长小写 hex、int64/uint64 是十进制字符串、
    区间不许倒过来、父亲必须活在同一条 trace 里、`AnyValue` 只能有一个 oneof 分支。
    """
    problems: list[str] = []
    seen_span_ids: set[str] = set()
    traces = payload.get("resourceSpans")
    if not isinstance(traces, list) or not traces:
        return ["resourceSpans 缺失或为空"]
    for resource in traces:
        scopes = resource.get("scopeSpans")
        if not isinstance(scopes, list) or not scopes:
            problems.append("某个 resourceSpans 里没有 scopeSpans")
            continue
        for scope in scopes:
            spans = scope.get("spans")
            if not isinstance(spans, list):
                problems.append("某个 scopeSpans 里没有 spans")
                continue
            ids = {str(s.get("spanId")) for s in spans if isinstance(s, dict)}
            for span in spans:
                _validate_span(span, ids, seen_span_ids, problems)
    blob = json.dumps(payload, ensure_ascii=False)
    if _SECRET_LIKE.search(blob):
        problems.append("payload 里还有没被遮住的 secret 形状字符串（掩码失效了）")
    return problems


def _validate_span(span: Any, ids: set[str], seen: set[str], problems: list[str]) -> None:
    if not isinstance(span, dict):
        problems.append("span 不是对象")
        return
    label = str(span.get("spanId") or "?")
    trace_id = span.get("traceId")
    span_id = span.get("spanId")
    if not _is_hex(trace_id, 32):
        problems.append(f"span {label}: traceId 不是 32 位小写 hex：{trace_id!r}")
    if not _is_hex(span_id, 16):
        problems.append(f"span {label}: spanId 不是 16 位小写 hex：{span_id!r}")
    if span_id in seen:
        problems.append(f"span {label}: spanId 在这份 payload 里出现了两次")
    seen.add(str(span_id))
    if not isinstance(span.get("name"), str) or not span["name"]:
        problems.append(f"span {label}: name 必须是非空字符串（低基数，别把轮次塞进去）")
    kind = span.get("kind")
    if not isinstance(kind, int) or isinstance(kind, bool) or not 0 <= kind <= 5:
        problems.append(f"span {label}: kind 是 0–5 的整数，读到 {kind!r}")
    start = _unix_nano(span.get("startTimeUnixNano"))
    end = _unix_nano(span.get("endTimeUnixNano"))
    if start is None or end is None:
        problems.append(f"span {label}: start/end 时间戳必须是十进制字符串形式的 unix nano")
    elif start > end:
        problems.append(f"span {label}: 起点晚于终点（{start} > {end}）")
    _validate_attributes(span.get("attributes"), f"span {label}", problems)
    parent = span.get("parentSpanId")
    if parent is not None:
        if not _is_hex(parent, 16):
            problems.append(f"span {label}: parentSpanId 不是 16 位 hex：{parent!r}")
        elif str(parent) not in ids:
            problems.append(f"span {label}: 父亲 {parent} 不在这条 trace 里（收集端会画出一棵断头树）")
    events = span.get("events", [])
    if not isinstance(events, list):
        problems.append(f"span {label}: events 必须是数组")
        return
    for event in events:
        if not isinstance(event, dict) or not event.get("name"):
            problems.append(f"span {label}: 每条 event 都要有 name")
            continue
        if _unix_nano(event.get("timeUnixNano")) is None:
            problems.append(f"span {label}: event {event.get('name')} 缺 timeUnixNano")
        _validate_attributes(event.get("attributes"), f"event {event.get('name')}", problems)


def _validate_attributes(attributes: Any, where: str, problems: list[str]) -> None:
    if attributes is None:
        return
    if not isinstance(attributes, list):
        problems.append(f"{where}: attributes 必须是 KeyValue 数组")
        return
    keys: set[str] = set()
    for item in attributes:
        if not isinstance(item, dict) or not item.get("key"):
            problems.append(f"{where}: 有条 attribute 没有 key")
            continue
        key = str(item["key"])
        if key in keys:
            problems.append(f"{where}: attribute {key} 出现两次")
        keys.add(key)
        value = item.get("value")
        branches = _value_branches(value)
        if len(branches) != 1:
            problems.append(f"{where}: {key} 的 AnyValue 该恰好有一个分支，实际 {branches or '无'}")
            continue
        _validate_branch(value[branches[0]], branches[0], f"{where}: {key}", problems)


def _validate_branch(value: Any, branch: str, where: str, problems: list[str]) -> None:
    """proto3 的 JSON 映射：int64/uint64 是十进制字符串，double 是数字，两者都不能反着写。"""
    if branch == "intValue" and not (isinstance(value, str) and value.lstrip("-").isdigit()):
        problems.append(f"{where}: intValue 必须是十进制字符串，写成 {value!r} 收集端会拒")
    elif branch == "stringValue" and not isinstance(value, str):
        problems.append(f"{where}: stringValue 必须是字符串")
    elif branch == "doubleValue" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        problems.append(f"{where}: doubleValue 必须是数字")
    elif branch == "boolValue" and not isinstance(value, bool):
        problems.append(f"{where}: boolValue 必须是布尔")
    elif branch == "arrayValue":
        if not isinstance(value, dict) or not isinstance(value.get("values"), list):
            problems.append(f"{where}: arrayValue 要有 values 数组")
        else:
            for index, element in enumerate(value["values"]):
                _validate_attributes([{"key": f"[{index}]", "value": element}], where, problems)


def _value_branches(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    return [k for k in value if k in _BRANCHES]


_BRANCHES = ("stringValue", "boolValue", "intValue", "doubleValue", "arrayValue", "kvlistValue", "bytesValue")


def _is_hex(value: Any, width: int) -> bool:
    return isinstance(value, str) and len(value) == width and all(c in "0123456789abcdef" for c in value)


def _unix_nano(value: Any) -> int | None:
    if isinstance(value, str) and value.isdigit():
        return int(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return None  # uint64 在 JSON 里必须是字符串，写成数字就是编码错了
    return None


# ---------------------------------------------------------------- 缺口

def gaps() -> list[dict[str, str]]:
    """翻译层丢掉了什么、为什么丢。`--otel` 与证据脚本都要把这份清单原样打出去。"""
    return [
        {
            "item": "null 值",
            "lost": "OTLP 的 `AnyValue` 没有 null 分支（只有 string/bool/int64/double/array/kvlist/bytes）。",
            "handling": "`verdict: null` 这类字段整个不导出，数量记在 `coverage.unrepresentable_nulls`，"
                        "消费方看到的是属性缺失，而不是一个假的空串。",
        },
        {
            "item": "嵌套结构",
            "lost": "`config` / `args` / `todos` / `skills` / `servers` 这类映射，以及混了 null 的列表。",
            "handling": "落为 JSON 字符串属性。这不是偷懒：`gen_ai.tool.call.arguments` 在语义约定里"
                        "本来就规定是 JSON 字符串，而 `kvlistValue` 在 collector 侧已标 deprecated。",
        },
        {
            "item": "时间区间",
            "lost": "trace 每条记录只有一个 `ts`（写下那一刻），没有开始/结束两个端点。",
            "handling": "`latency` 存在时反推起点（`ts - latency`），span 打 `mcc.span.time_source` = "
                        "`latency-reconstructed`；不存在时 start==end，是个零长度 span，"
                        "计数在 `coverage.zero_duration_spans`。",
        },
        {
            "item": "span 层级",
            "lost": "一次 ReAct 轮次里的'模型调用'和'这一步的思考'在 trace 里共用同一个 `span_id`。",
            "handling": "合成一个 span（`chat {model}`），不凭空造父子关系 —— 要分开就得先改 §3.1 的埋点。",
        },
        {
            "item": "provider 与端点",
            "lost": "`gen_ai.provider.name`、`server.address`、`server.port`。",
            "handling": "trace 里没有这些字段（`config.base_url` 有，但那是会话配置不是 span 归属），"
                        "宁可不填也不猜。",
        },
        {
            "item": "本地路径与端点地址",
            "lost": "不丢 —— 正相反，这条是警告。",
            "handling": "`mcc.session.config` 里带着 `project_root`、`trace_path`、`base_url`。"
                        "发到本机之外的 collector 就是泄露面，所以 `--endpoint` 指向非 localhost 时"
                        "`mcc trace --otel` 会先打印一行警告再发。",
        },
        {
            "item": "span links",
            "lost": "`tool_use_id` 与它对应的 `session_replay` / `checkpoint` 之间的引用关系。",
            "handling": "只作为属性字符串共存，不生成 `links` —— 我们要的对照信息在属性里就够读，"
                        "而伪造 link 会引入一个 trace 本身没有的因果断言。",
        },
    ]


# ---------------------------------------------------------------- 发送

def post_otlp(payload: dict[str, Any], endpoint: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """POST 到 OTLP/HTTP collector。校验不过就一个字节都不发。"""
    receipt: dict[str, Any] = {"endpoint": endpoint, "ok": False, "status_code": None, "error": None}
    if not endpoint.startswith(("http://", "https://")):
        receipt["error"] = "只走 http(s)：OTLP/HTTP 的 endpoint 长这样 http://localhost:4318/v1/traces"
        return receipt
    problems = validate(payload)
    if problems:
        receipt["error"] = f"校验未过，未发送：{problems[0]}"
        receipt["problems"] = problems
        return receipt
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    receipt["bytes"] = len(body)
    try:
        import httpx  # 只在真的要发的时候才拉这个依赖
    except ImportError:  # pragma: no cover
        receipt["error"] = "没装 httpx，发不出去"
        return receipt
    try:
        response = httpx.post(
            endpoint,
            content=body,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
    except Exception as exc:  # 网络层什么都可能抛，这里不能拖垮 CLI
        # Windows 上的 httpx 异常常常没有 message，只留类型名等于什么也没说。
        receipt["error"] = f"{type(exc).__name__}: {str(exc).strip() or repr(exc)}"
        return receipt
    receipt["status_code"] = response.status_code
    receipt["ok"] = 200 <= response.status_code < 300
    if not receipt["ok"]:
        # 一个空的 4xx 响应体也要说清是谁拒的 —— "发送失败："后面什么都没有，等于没报错。
        receipt["error"] = (response.text or "").strip()[:400] or f"HTTP {response.status_code}，响应体是空的"
    return receipt


def is_local(endpoint: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(endpoint).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or host.endswith(".local")
