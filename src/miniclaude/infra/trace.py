"""会话追踪 —— 每行一条 JSONL，schema v2。

调试 Agent 必须有这个：模型为什么绕了三个工具、哪一步参数是幻觉、
上下文在哪轮涨爆，靠 print 永远查不清。它同时是 v2 评测的数据源，
所以字段设计要能直接算出 SPEC v2 §3.1 的指标。

v2 的三条纪律：

1. **每个数字都有产地。** `summarize()` 只准读记录里已有的字段；哪些字段真的
   被写过由 `tests/test_trace_contract.py::test_metrics_have_producers` 盯着。
   v1 的 `redundant_calls` 是反面教材：定义了、进 trace 了、印在证据文件里，
   从未被累加过。
2. **字段形状有快照。** `tests/schema_v2.json` 记着每个 kind 的全部键名，
   加字段/改名/删字段不同步改快照就是 CI 红。SPEC v1 §3.8 写 `session_end`
   而代码写 `run_end` 那种漂移不再可能重演（勘误见 SPEC v2 §0.3）。
3. **派生结论随记录落盘。** `verdict`（验证红绿）、`drops_assert`（删断言）这类
   判定在写 trace 时就算好，离线诊断与实时诊断因此共用同一份定义。

字段命名对齐 OTel GenAI 语义约定的层级（trace/span 两级、`usage.prompt` 之分类），
但埋点不引入 OTel SDK —— 导出映射留给 `infra/otel.py`（S15）。

写盘失败一律静默降级 —— 日志绝不能拖垮主流程。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2.0"

# 任何字段里出现这类形状的串都要掩掉，不只看 key 名
_SECRET_LIKE = re.compile(r"(sk|pk|key|token|secret|bearer)[-_][A-Za-z0-9]{8,}", re.IGNORECASE)
_SENSITIVE_KEYS = ("api_key", "authorization", "token", "secret", "password")

# 与 `agent.loop.MAX_OUTPUT_CHARS` 是同一个数。这里不 import 它：`loop` 依赖 `trace`，
# 反向 import 就是循环导入；两边不一致由
# `tests/test_trace_contract.py::test_output_cap_matches_the_loop` 钉住。
OUTPUT_CAP = 30_000
MAX_STRING = 2000


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_span_id() -> str:
    """run / turn / tool 三级跨度各自一个 id。16 hex 够去重，又不至于让每行胖一圈。"""
    return uuid.uuid4().hex[:16]


def prompt_hash(text: str) -> str:
    """系统提示的指纹。两次跑批之间提示词改没改，看这个字段而不是看 diff。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class Tracer:
    """追加式 JSONL 记录器。None 路径表示禁用（测试与只读演练用）。"""

    def __init__(
        self,
        path: Path | None = None,
        *,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.trace_id = trace_id or new_trace_id()
        self._seq = 0
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.path = None

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def log(
        self,
        kind: str,
        *,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        **payload: Any,
    ) -> None:
        if self.path is None:
            return
        self._seq += 1
        record: dict[str, Any] = {
            "seq": self._seq,
            "ts": round(time.time(), 3),
            "session": self.session_id,
            "kind": kind,
            "trace_id": self.trace_id,
            "schema_version": SCHEMA_VERSION,
            **_scrub(payload),
        }
        if span_id is not None:
            record["span_id"] = span_id
        if parent_span_id is not None:
            record["parent_span_id"] = parent_span_id
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            self.path = None  # 一次写失败就不再尝试，避免每轮都撞盘

    def start_session(
        self,
        *,
        model: str,
        tools: list[str],
        config: dict[str, Any],
        system_prompt_hash: str | None = None,
    ) -> None:
        self.log(
            "session_start",
            span_id=new_span_id(),
            model=model,
            tools=tools,
            config=config,
            system_prompt_hash=system_prompt_hash,
        )

    def close(self) -> None:
        return None

    def __enter__(self) -> "Tracer":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _scrub(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in _SENSITIVE_KEYS:
            clean[key] = "***"
        else:
            clean[key] = _scrub_value(value)
    return clean


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        masked = _SECRET_LIKE.sub(lambda m: m.group(0)[:4] + "***", value)
        return masked if len(masked) <= MAX_STRING else masked[:MAX_STRING] + f"…({len(masked)} chars)"
    if isinstance(value, dict):
        return _scrub(value)
    if isinstance(value, (list, tuple)):
        return [_scrub_value(item) for item in value]
    return value


def replay(path: Path) -> list[dict[str, Any]]:
    """读回一次会话轨迹，容忍中途写入失败留下的坏行。

    "坏行"包括合法但不是对象的 JSON：`json.loads("1")` 成功，而下游每条记录都要
    `.get("kind")`，收下它等于把一次崩溃留给以后的某条命令。
    """
    records: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def fingerprint(path: Path) -> dict[str, Any]:
    """一份轨迹的内容指纹：非空行数 + 字节数 + 全文 sha256 的前 16 位。

    SPEC v2 §7.3-7 要的东西：「manifest 那行判据所依据的那份轨迹」这件事得有个可校验的
    凭据。规格原文写的是"行数 + **末行**哈希"，这里换成全文件哈希 —— 末行哈希查不出中间行
    被改过（缺口 ① 点名的正是"手工改动"），而为了数行数本来就要把文件整个读一遍，
    多算一次哈希的边际成本是 0。截到 16 个十六进制位 = 64 比特：要挡的是覆盖与手改，
    不是有意的伪造。
    """
    raw = Path(path).read_bytes()
    return {
        "lines": sum(1 for line in raw.splitlines() if line.strip()),
        "bytes": len(raw),
        "sha": hashlib.sha256(raw).hexdigest()[:16],
    }


def of_session(records: list[dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
    return [record for record in records if record.get("session") == session_id]


def sessions(path: Path) -> list[str]:
    """文件里出现过的会话 id，按先后顺序去重。`mcc trace --latest` 的最后一条就是它。"""
    seen: dict[str, None] = {}
    for record in replay(path):
        session = record.get("session")
        if isinstance(session, str):
            seen.setdefault(session, None)
    return list(seen)


def summarize(path: Path) -> dict[str, Any]:
    """把轨迹文件压成一份报告 —— 报表里的数字全部来自这里，不靠手填。"""
    return summarize_records(replay(path))


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """同一套口径，但吃已过滤的记录列表。`mcc trace <id>` 只看单个会话时必须走它 ——
    两份算法就会有两套数字。

    这里返回的每个键都必须能追到一处写入代码；这条纪律由
    `test_metrics_have_producers` 机器化执行（SPEC v2 §3.1）。
    """
    tools = [r for r in records if r.get("kind") == "tool_call"]
    turns = [r for r in records if r.get("kind") == "turn_start"]
    responses = [r for r in records if r.get("kind") == "llm_response"]
    compactions = [r for r in records if r.get("kind") == "context_compact"]
    usage = sum(
        (r.get("usage", {}) or {}).get("prompt", 0) + (r.get("usage", {}) or {}).get("completion", 0)
        for r in responses
    )
    end = next((r for r in reversed(records) if r.get("kind") == "run_end"), {})
    output_chars_total = sum(int(tool.get("output_chars") or 0) for tool in tools)
    # `_cap` 保留首尾各 `limit // 2`，所以被省略的是 `len - 2 * (limit // 2)`。
    keep = OUTPUT_CAP // 2
    output_chars_omitted = sum(
        max(0, int(tool.get("output_chars") or 0) - 2 * keep) for tool in tools
    )
    return {
        "session": records[0]["session"] if records else None,
        "termination": end.get("termination", "unknown"),
        "turns": len(turns),
        # 两个数不是一个意思：attempts 是模型发起了几次，executed 是权限门放行后真跑了几次。
        # v1 只有 `tool_calls` 一个名字，两处各指一头 —— 这里把名字分开钉死（SPEC v2 §3.1）。
        "tool_calls": int(end.get("tool_calls", len(tools))),
        "tool_executed": len(tools),
        "tool_errors": int(end.get("tool_errors", sum(1 for tool in tools if not tool.get("ok", True)))),
        "repeated_calls": end.get("repeated_calls", 0),
        "stalled_groups": end.get("stalled_groups", 0),
        "denied_actions": end.get("denied_actions", 0),
        # L2 摘要那次请求不发 `llm_response`（它没有工具、也不占轮次），只把开销记在
        # `context_compact.summary_tokens` 上。这里必须加回来，否则 `mcc eval` 的
        # tokens_spent 会系统性低报，而压缩恰恰是全循环最贵的一次单点开销。
        "tokens": usage + sum(int(r.get("summary_tokens") or 0) for r in compactions),
        "context_peak_tokens": end.get("context_peak_tokens", 0),
        "context_compactions": int(end.get("context_compactions", 0)),
        "context_elided_blocks": int(end.get("context_elided_blocks", 0)),
        "context_summary_tokens": int(end.get("context_summary_tokens", 0)),
        "wall_ms": end.get("wall_ms", 0),
        "cost_est": end.get("cost_est"),
        "failure_modes": list(end.get("failure_modes", []) or []),
        "tool_sequence": [tool.get("name") for tool in tools],
        # 派生量，产地是 `tool_call` 记录本身（与 `tool_executed` 同一个路子）。
        # 只统计循环层 `_cap` 省掉的字符：workspace 层更早截断，它丢的原始长度没进 trace。
        "output_chars": output_chars_total,
        "omitted_output_chars": output_chars_omitted,
    }


def hotspots(records: list[dict[str, Any]]) -> dict[str, Any]:
    """`mcc trace --hot` 的数据：最贵 3 轮、报错最多的工具、重复调用簇。"""
    per_turn: dict[int, dict[str, Any]] = {}

    def slot(turn: int) -> dict[str, Any]:
        return per_turn.setdefault(turn, {"turn": turn, "tokens": 0, "tool_calls": 0, "errors": 0, "est_tokens": 0})

    for record in records:
        kind = record.get("kind")
        turn = int(record.get("turn") or 0)
        if kind == "llm_response":
            used = record.get("usage") or {}
            slot(turn)["tokens"] += int(used.get("prompt", 0)) + int(used.get("completion", 0))
        elif kind == "turn_start":
            slot(turn)["est_tokens"] = max(slot(turn)["est_tokens"], int(record.get("est_tokens") or 0))
        elif kind == "tool_call":
            slot(turn)["tool_calls"] += 1
            if not record.get("ok", True):
                slot(turn)["errors"] += 1

    ranked = sorted(per_turn.values(), key=lambda item: (-item["tokens"], -item["tool_calls"]))
    error_by_tool = Counter(
        str(record.get("name")) for record in records
        if record.get("kind") == "tool_call" and not record.get("ok", True)
    )
    return {
        "costliest_turns": ranked[:3],
        "error_prone_tools": error_by_tool.most_common(3),
        "repeated_clusters": repeated_clusters(records),
    }


def repeated_clusters(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同名同参的连续调用簇。这是 `repeated_calls` 的案发现场，光看总数看不出模式。"""
    clusters: list[dict[str, Any]] = []
    for record in records:
        if record.get("kind") != "tool_call":
            continue
        name = str(record.get("name"))
        args = json.dumps(record.get("args"), sort_keys=True, ensure_ascii=False, default=str)
        turn = int(record.get("turn") or 0)
        current = clusters[-1] if clusters else None
        if current is not None and current["name"] == name and current["args"] == args:
            current["count"] += 1
            current["last_turn"] = turn
            continue
        clusters.append({"name": name, "args": args, "count": 1, "first_turn": turn, "last_turn": turn})
    return [
        {"name": item["name"], "args": item["args"], "count": item["count"], "turns": _span(item)}
        for item in clusters
        if item["count"] >= 2
    ]


def _span(cluster: dict[str, Any]) -> str:
    first, last = cluster["first_turn"], cluster["last_turn"]
    return str(first) if first == last else f"{first}–{last}"
