"""§7.3-3 OTLP 导出器的用例：一层翻译能骗人的四个地方。

翻译层最便宜的错法是把数据弄丢然后长得像成功。所以这四类各有一组用例：

1. **丢记录** —— 没有 `span_id` 的 kind、v1 轨迹、认不出角色的 kind，全都要在 payload 里
   出现，且 `coverage` 那本账要加得起来（`test_no_record_is_lost` 跑盘上全部真实轨迹）。
2. **丢字段** —— `tests/schema_v2.json` 里 20 个 kind 的每个键都必须在 `FIELD_MAP` 登记过名字；
   加埋点不登记就是 CI 红，这一条和 S8 的 `test_metrics_have_producers` 是同一个套路。
3. **编结构** —— 断掉的父亲不重挂、null 不塞空串、`latency` 反推的起点要写明来源。
   还有一类看不见的错：只扫 `span.records` 的聚合（`_resource`、`record_kinds`）看不见长在
   `events` 上的记录，v1 轨迹于是被报成"0 个会话、空模型"。导出层可以少给，不可以多给。
4. **编码错** —— int64 写成数字、时间戳写成秒、AnyValue 两个分支，收集端会整批拒收，
   所以 `validate()` 先把这些变成我们能读懂的中文报错。

还有一条是这个 Stage 存在的理由本身：**埋点不引入 OTel SDK**（SPEC v2 §3.1 的规矩），
由 `test_the_sdk_stays_out_of_the_instrumentation` 盯着 —— 不然"这一步只有翻译"就成了装饰。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from miniclaude.infra.otel import (
    ENVELOPE_KEYS,
    FIELD_MAP,
    SERVICE_NAME,
    gaps,
    post_otlp,
    render,
    spans_of,
    translate,
    validate,
)
from miniclaude.infra.trace import SCHEMA_VERSION, Tracer, replay

REPO = Path(__file__).resolve().parent.parent
SCHEMA_FILE = REPO / "tests" / "schema_v2.json"
TRACES = sorted((REPO / "demos" / "traces").rglob("*.jsonl")) + sorted((REPO / "eval" / "results").rglob("*.jsonl"))
SECRET = "sk-abcdefghijklmnop"

# 全是合法 hex：不合法的 id 有专门的用例，别让它在这里悄悄走派生分支。
SESSION = "51" * 8
RUN = "52" * 8
TURN = "53" * 8
CALL = "54" * 8
TRACE = "aa" * 16


def _spans(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [span for scope in payload["resourceSpans"][0]["scopeSpans"] for span in scope["spans"]]


def _attrs(node: dict[str, Any]) -> dict[str, Any]:
    """OTLP 的 attributes 是数组不是对象 —— 每次手写 `for item in ...` 就是错的温床。"""
    return {item["key"]: item["value"] for item in node.get("attributes", [])}


def _by_name(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [s for s in _spans(payload) if s["name"] == name]


@pytest.fixture
def traced(tmp_path: Path) -> Tracer:
    return Tracer(tmp_path / "trace.jsonl", session_id="sess0001", trace_id=TRACE)


def test_a_session_makes_a_tree_of_four_levels(traced: Tracer) -> None:
    traced.log("session_start", span_id=SESSION, model="m", tools=["read_file"], config={})
    traced.log("run_start", span_id=RUN, run_id="run1")
    traced.log("turn_start", span_id=TURN, parent_span_id=RUN, turn=1, est_tokens=10, message_count=1)
    traced.log("tool_call", span_id=CALL, parent_span_id=TURN, turn=1, name="read_file", ok=True)
    traced.log("run_end", span_id=RUN, turn=1, status="finished", termination="completed")

    payload, cov = translate(replay(traced.path))
    assert validate(payload) == []
    assert cov["spans_out"] == 4  # run_start 与 run_end 合并成同一个 run span
    by_id = {s["spanId"]: s for s in _spans(payload)}
    tool = by_id[CALL]
    turn = by_id[TURN]
    assert tool["name"] == "execute_tool read_file"
    assert turn["name"] == "chat m"
    assert tool["parentSpanId"] == TURN
    assert turn["parentSpanId"] == RUN
    assert by_id[RUN]["parentSpanId"] == SESSION  # run_start 自己不写父亲，层级从会话来
    assert by_id[SESSION]["name"] == "agent.session"
    assert "parentSpanId" not in by_id[SESSION]


def test_records_sharing_a_span_id_become_one_span(traced: Tracer) -> None:
    traced.log("permission", span_id=CALL, turn=1, tool="bash", decision="allow", rule_hit="always")
    traced.log("tool_call", span_id=CALL, turn=1, name="bash", ok=True, args={"command": "ls"})
    payload, cov = translate(replay(traced.path))
    assert cov["spans_out"] == 1
    assert cov["records_folded_into_spans"] == 2
    span = _spans(payload)[0]
    attrs = _attrs(span)
    assert attrs["mcc.span.record_kinds"]["stringValue"] == "permission+tool_call"
    assert attrs["mcc.permission.decision"]["stringValue"] == "allow"
    assert attrs["mcc.tool.ok"]["boolValue"] is True
    assert attrs["gen_ai.operation.name"]["stringValue"] == "execute_tool"


def test_the_run_span_carries_agent_semantics(traced: Tracer) -> None:
    traced.log("session_start", span_id=SESSION, model="agnes-2.5-flash", tools=["a"], config={})
    traced.log("run_start", span_id=RUN, run_id="run1")
    traced.log("run_end", span_id=RUN, status="finished", termination="completed",
               usage={"prompt": 100, "completion": 5, "total": 105})
    payload, _ = translate(replay(traced.path))
    attrs = _attrs(_by_name(payload, "invoke_agent agnes-2.5-flash")[0])
    assert attrs["gen_ai.operation.name"]["stringValue"] == "invoke_agent"
    assert attrs["gen_ai.usage.input_tokens"]["intValue"] == "100"
    assert attrs["gen_ai.usage.output_tokens"]["intValue"] == "5"
    assert attrs["gen_ai.usage.total_tokens"]["intValue"] == "105"
    assert attrs["gen_ai.conversation.id"]["stringValue"] == "sess0001"


def test_every_kind_in_the_snapshot_has_a_declared_name() -> None:
    """加字段不登记就是 CI 红 —— 和 `test_metrics_have_producers` 同一套纪律。"""
    snapshot = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert set(snapshot) == set(FIELD_MAP), "导出器不认识这个 kind，先加进 FIELD_MAP"
    for kind, keys in snapshot.items():
        payload_keys = {key for key in keys if key not in ENVELOPE_KEYS}
        assert payload_keys == set(FIELD_MAP[kind]), f"{kind} 的字段与登记表不一致"


def test_kinds_without_a_span_id_become_events_and_are_not_dropped(traced: Tracer) -> None:
    traced.log("todo_update", turn=1, items=["读代码", "改代码"])
    traced.log("failure_mode", turn=1, mode="path_guessing", why="猜了三次路径", prescription="先列目录")
    traced.log("backend", turn=0, backend="local", requested="docker", degraded="没有 docker",
               isolated=False, checkpoints=0, shell="bash", note="降级")
    payload, cov = translate(replay(traced.path))
    assert validate(payload) == []
    assert cov["spans_out"] == 1 and cov["roles"] == {"orphan": 1}  # 一个 span 也不许编出来
    assert cov["records_emitted_as_events"] == 3
    host = _spans(payload)[0]
    assert [event["name"] for event in host["events"]] == ["mcc.todo_update", "mcc.failure_mode", "mcc.backend"]
    assert _attrs(host["events"][1])["mcc.failure.mode"]["stringValue"] == "path_guessing"
    assert _attrs(host["events"][2])["mcc.backend.isolated"]["boolValue"] is False


def test_an_event_lands_on_the_span_of_its_own_turn(traced: Tracer) -> None:
    traced.log("run_start", span_id=RUN, run_id="run1")
    traced.log("turn_start", span_id="61" * 8, parent_span_id=RUN, turn=1, est_tokens=1, message_count=1)
    traced.log("turn_start", span_id="62" * 8, parent_span_id=RUN, turn=2, est_tokens=2, message_count=2)
    traced.log("todo_update", turn=2, items=["x"])
    payload, cov = translate(replay(traced.path))
    assert cov["spans_out"] == 3

    def turn_of(number: int) -> dict[str, Any]:
        return next(s for s in _spans(payload) if _attrs(s).get("mcc.turn.number", {}).get("intValue") == str(number))

    assert [event["name"] for event in turn_of(2)["events"]] == ["mcc.todo_update"]
    assert "events" not in turn_of(1)


def test_events_do_not_cross_sessions(tmp_path: Path) -> None:
    """一个日志文件躺着好几个会话；把它们混成一棵树比扁平更难读。"""
    for index, session in enumerate(("s1", "s2")):
        tracer = Tracer(tmp_path / "shared.jsonl", session_id=session, trace_id=f"{index + 1:032x}")
        tracer.log("session_start", span_id=f"{index + 1:016x}", model="m", tools=[], config={})
        tracer.log("run_start", span_id=f"{index + 11:016x}", run_id=session)
        tracer.log("todo_update", turn=0, items=[f"only-{session}"])
    payload, cov = translate(replay(tmp_path / "shared.jsonl"))
    assert cov["unaccounted"] == 0
    hosts = [span for span in _spans(payload) if span.get("events")]
    assert len(hosts) == 2
    for host in hosts:
        owner = _attrs(host)["gen_ai.conversation.id"]["stringValue"]
        values = _attrs(host["events"][0])["mcc.todo.items"]["arrayValue"]["values"]
        assert [v["stringValue"] for v in values] == [f"only-{owner}"]


def test_a_v1_trace_still_exports_but_flatly() -> None:
    """v1 轨迹没有 span_id：全都成 event，一条也不丢，且不编层级。"""
    records = replay(REPO / "demos" / "traces" / "v1-baseline" / "codegen.live.jsonl")
    payload, cov = translate(records)
    assert validate(payload) == []
    assert cov["records_folded_into_spans"] == 0
    assert cov["records_emitted_as_events"] == cov["records_in"]
    assert cov["roles"] == {"orphan": 1}
    assert cov["unaccounted"] == 0
    resource = _attrs(payload["resourceSpans"][0]["resource"])
    # 会话和模型在 v1 里是真的（记录只是没有 span_id），资源属性不许因为"记录都在 events 上"就当它们不存在
    assert resource["mcc.session.count"]["intValue"] == "1"
    assert resource["gen_ai.request.model"]["stringValue"] == "agnes-2.5-flash"
    # 反过来，v1 从没自报过 `schema_version`：拿当前版本替它填一个 "2.0" 是替它撒谎，宁可不写
    assert "mcc.trace.schema_version" not in resource


@pytest.mark.parametrize("path", TRACES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_record_is_lost(path: Path) -> None:
    """盘上每一份真实轨迹都要：账加得起来、校验挑不出毛病。"""
    records = replay(path)
    if not records:
        pytest.skip("空轨迹")
    payload, cov = translate(records)
    assert cov["unaccounted"] == 0, f"{path}：有记录既没进 span 也没进 event"
    assert cov["accounted_for"] == cov["records_in"]
    assert validate(payload) == []


def test_each_session_gets_its_own_tree(tmp_path: Path) -> None:
    """一个文件里第二个会话不能挂到第一个会话的树上。

    这是真实踩到的：连续跑 `--task` 把 6 个会话写进同一个文件，导出器拿全文件第一个
    session span 当唯一锚点，于是后面 5 个会话的 span 全被接到会话 1 底下 —— 收集端
    按 `traceId` 归组后发现父亲不在本trace，画出一棵断头树。`validate()` 会报出来，
    但那时 payload 已经发出去了。
    """
    records: list[dict[str, Any]] = []
    for index, session in enumerate(("s1", "s2")):
        trace = f"{index + 1:032x}"
        # span_id 必须逐会话唯一（真实轨迹就是这样），否则两组记录会被并成同一个 span，
        # 测的就不是"两棵树"而是"一棵残树"了。
        seed = (index + 1) * 4
        session_span, run_span, turn_span = (f"{seed + offset:016x}" for offset in (1, 2, 3))
        for seq, record in enumerate(
            (
                {"kind": "session_start", "span_id": session_span, "trace_id": trace, "session": session},
                {"kind": "run_start", "span_id": run_span, "trace_id": trace, "session": session},
                {"kind": "turn_start", "span_id": turn_span, "trace_id": trace, "session": session,
                 "parent_span_id": run_span},
                # 无 span_id 的记录：必须回到**自己**会话的 turn 上，不能就近抓别人家的
                {"kind": "todo_update", "trace_id": trace, "session": session, "turn": 1},
            ),
            start=index * 10 + 1,
        ):
            records.append({**record, "seq": seq})

    payload, cov = translate(records)
    assert cov["unaccounted"] == 0
    assert validate(payload) == []          # 断头树就在这里被拒

    spans = spans_of(records)
    owner = {span.span_id: span.trace_id for span in spans}
    for span in spans:
        if span.parent_span_id is not None:
            assert owner[span.parent_span_id] == span.trace_id, "跨 trace 的父子关系"

    # 每个 trace 恰好一棵以 session span 为根的树
    for session in ("s1", "s2"):
        mine = [s for s in spans if s.session == session]
        roots = [s for s in mine if s.parent_span_id is None]
        assert len(roots) == 1 and roots[0].role == "session"

    # 记录顺序不是语义：倒着喂同一批记录，长出来的树必须一模一样
    def shape(ordered: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
        return sorted(
            (s.span_id, s.parent_span_id) for s in spans_of(ordered)
        )

    assert shape(records) == shape(list(reversed(records)))


@pytest.mark.parametrize("path", TRACES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_time_never_runs_backwards(path: Path) -> None:
    payload, _ = translate(replay(path))
    for span in _spans(payload):
        assert int(span["startTimeUnixNano"]) <= int(span["endTimeUnixNano"])


def test_latency_reconstructs_the_start_of_a_span(traced: Tracer) -> None:
    traced.log("llm_response", span_id=TURN, turn=1, latency=2.5,
               usage={"prompt": 1, "completion": 1}, stop_reason="end_turn", blocks=[])
    payload, cov = translate(replay(traced.path))
    span = _spans(payload)[0]
    assert int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]) == 2_500_000_000
    assert _attrs(span)["mcc.span.time_source"]["stringValue"] == "latency-reconstructed"
    assert cov["time_reconstructed_spans"] == 1


def test_a_span_without_latency_is_honestly_zero_length(traced: Tracer) -> None:
    traced.log("run_start", span_id=RUN, run_id="run1")
    payload, cov = translate(replay(traced.path))
    assert cov["zero_duration_spans"] == 1
    span = _spans(payload)[0]
    assert span["startTimeUnixNano"] == span["endTimeUnixNano"]
    assert _attrs(span)["mcc.span.time_source"]["stringValue"] == "record-timestamps"


def test_missing_timestamps_are_counted_not_invented() -> None:
    records = [{"kind": "run_start", "session": "s", "trace_id": TRACE, "span_id": RUN, "run_id": "r"}]
    payload, cov = translate(records)
    span = _spans(payload)[0]
    assert cov["spans_without_timestamps"] == 1
    assert span["startTimeUnixNano"] == "0"
    assert _attrs(span)["mcc.span.time_source"]["stringValue"] == "absent"


def test_null_has_no_home_in_otlp_and_says_so(traced: Tracer) -> None:
    """`AnyValue` 没有 null 分支：属性缺席 + 计数入账，而不是塞一个假的空串。"""
    traced.log("tool_call", span_id=CALL, turn=1, name="run_tests", ok=True,
               verdict=None, drops_assert=None, args={})
    payload, cov = translate(replay(traced.path))
    attrs = _attrs(_spans(payload)[0])
    assert "mcc.tool.verdict" not in attrs
    assert cov["unrepresentable_nulls"] == 2
    assert cov["null_fields"] == ["tool_call.drops_assert", "tool_call.verdict"]
    assert all(value != {} for value in attrs.values())


def test_nested_structures_become_json_strings(traced: Tracer) -> None:
    traced.log("tool_call", span_id=CALL, turn=1, name="edit_file", ok=True,
               args={"path": "a/b.py", "replace_all": False})
    payload, _ = translate(replay(traced.path))
    value = _attrs(_spans(payload)[0])["gen_ai.tool.call.arguments"]["stringValue"]
    assert json.loads(value) == {"path": "a/b.py", "replace_all": False}


def test_scalar_lists_stay_arrays(traced: Tracer) -> None:
    traced.log("run_end", span_id=RUN, turn=3, status="aborted", termination="max_turns",
               failure_modes=["context_growth", "path_guessing"], todos=[])
    payload, _ = translate(replay(traced.path))
    values = _attrs(_spans(payload)[0])["mcc.failure.modes"]["arrayValue"]["values"]
    assert [item["stringValue"] for item in values] == ["context_growth", "path_guessing"]


def test_int64_is_a_decimal_string_and_time_is_nanoseconds(traced: Tracer) -> None:
    traced.log("run_end", span_id=RUN, turn=3, status="finished", termination="completed",
               wall_ms=1500, usage={"total": 4242})
    payload, _ = translate(replay(traced.path))
    span = _spans(payload)[0]
    assert _attrs(span)["mcc.run.wall_ms"] == {"intValue": "1500"}
    assert span["startTimeUnixNano"].isdigit() and len(span["startTimeUnixNano"]) >= 19
    assert isinstance(span["kind"], int)


def test_red_comes_from_the_recorded_verdicts_not_from_guesses(traced: Tracer) -> None:
    traced.log("tool_call", span_id=CALL, turn=1, name="bash", ok=False, args={})
    traced.log("tool_call", span_id="55" * 8, turn=1, name="bash", ok=True, args={})
    payload, cov = translate(replay(traced.path))
    assert cov["red_spans"] == 1
    codes = {s["spanId"]: s["status"]["code"] for s in _spans(payload)}
    assert codes[CALL] == 2 and codes["55" * 8] == 1


def test_denied_aborted_and_broken_pairing_are_all_red(traced: Tracer) -> None:
    traced.log("tool_call", span_id=CALL, turn=1, name="bash", ok=False, args={})
    traced.log("context_compact", span_id="66" * 8, turn=2, pairing_ok=False, level="L2")
    traced.log("run_end", span_id=RUN, turn=2, status="aborted", termination="context_refuse")
    payload, cov = translate(replay(traced.path))
    assert cov["red_spans"] == 3


def test_merged_records_that_disagree_keep_both_numbers(traced: Tracer) -> None:
    """`turn_start` 与 `llm_request` 共用一个 span，压缩之后两者的估算不相等 —— 谁都不许盖谁。"""
    traced.log("turn_start", span_id=TURN, turn=1, est_tokens=40000, message_count=9)
    traced.log("llm_request", span_id=TURN, parent_span_id=TURN, turn=1, est_tokens=31000,
               message_count=5, tools_count=8, prefix_hash="ph")
    payload, _ = translate(replay(traced.path))
    attrs = _attrs(_spans(payload)[0])
    assert attrs["mcc.context.est_tokens"]["intValue"] == "40000"
    # 先到的那条拿到正名，后到的挂上自己的 kind —— 两个数都还在
    assert attrs["mcc.llm_request.context.est_tokens"]["intValue"] == "31000"


def test_merging_identical_values_does_not_double_them(traced: Tracer) -> None:
    traced.log("turn_start", span_id=TURN, turn=4, est_tokens=1, message_count=1)
    traced.log("llm_request", span_id=TURN, parent_span_id=TURN, turn=4, est_tokens=1,
               message_count=1, tools_count=8, prefix_hash="ph")
    payload, _ = translate(replay(traced.path))
    attrs = _attrs(_spans(payload)[0])
    assert attrs["mcc.turn.number"]["intValue"] == "4"
    assert not any(key.startswith("mcc.turn_start.mcc.") for key in attrs)


def test_payload_is_deterministic(traced: Tracer) -> None:
    traced.log("session_start", span_id=SESSION, model="m", tools=["b", "a"], config={"max_turns": 9})
    traced.log("run_start", span_id=RUN, run_id="run1")
    traced.log("todo_update", turn=1, items=["x"])
    records = replay(traced.path)
    assert json.dumps(translate(records)[0]) == json.dumps(translate(records)[0])

    def spans_sorted(payload: dict[str, Any]) -> str:
        return json.dumps(sorted(_spans(payload), key=lambda s: s["spanId"]), ensure_ascii=False, sort_keys=True)

    forward = spans_sorted(translate(records)[0])
    backward = spans_sorted(translate(list(reversed(records)))[0])
    assert forward == backward  # span 的*顺序*跟着记录走，内容不跟


def test_a_malformed_id_is_derived_instead_of_emitted_verbatim() -> None:
    """手工改过的轨迹不能把收集端会拒的 id 原样发出去。"""
    records = [{"kind": "run_start", "session": "s", "trace_id": "not-a-trace-id", "span_id": "nope", "run_id": "r", "ts": 1.0}]
    payload, _ = translate(records)
    assert validate(payload) == []
    span = _spans(payload)[0]
    assert span["spanId"] not in {"nope", ""} and span["traceId"] != "not-a-trace-id"
    assert len(span["spanId"]) == 16 and len(span["traceId"]) == 32
    assert json.dumps(translate(records)[0]) == json.dumps(payload)  # 派生是确定的


def test_a_dangling_parent_is_detached_not_rehung(traced: Tracer) -> None:
    traced.log("run_start", span_id=RUN, run_id="run1")
    traced.log("checkpoint", span_id=CALL, parent_span_id="ff" * 8, turn=1, ok=True,
               rev="1", backend="local", tool="write_file", note="")
    payload, cov = translate(replay(traced.path))
    assert validate(payload) == []
    assert cov["detached_parents"] == 1
    assert cov["roots"] == 2  # run 与那个断头 span 各是一棵的根，不编父子
    attrs = _attrs(next(s for s in _spans(payload) if s["name"] == "mcc.checkpoint"))
    assert attrs["mcc.span.detached_parent_id"]["stringValue"] == "ff" * 8


def test_no_secret_survives_the_export() -> None:
    """写日志时脱过一遍，导出这层还要再来一遍 —— 手工拼的、别的来源的都算。"""
    records = [
        {"kind": "tool_call", "session": "s", "trace_id": TRACE, "span_id": CALL, "turn": 1,
         "name": "bash", "ok": True, "args": {"command": f"curl -H 'Authorization: Bearer {SECRET}'"}}
    ]
    payload, _ = translate(records)
    blob = json.dumps(payload, ensure_ascii=False)
    assert SECRET not in blob
    assert validate(payload) == []


# ------------------------------------------------------------------ validate


def _good_payload() -> dict[str, Any]:
    records = [
        {"kind": "session_start", "session": "s", "trace_id": TRACE, "span_id": SESSION, "ts": 2.0, "model": "m"},
        {"kind": "run_start", "session": "s", "trace_id": TRACE, "span_id": RUN, "ts": 3.0, "run_id": "1"},
    ]
    return translate(records)[0]


def test_the_clean_payload_passes() -> None:
    assert validate(_good_payload()) == []


@pytest.mark.parametrize(
    "mutate, signal",
    [
        (lambda s: s.update(spanId="zz"), "spanId"),
        (lambda s: s.update(traceId="ZZ" * 16), "traceId"),
        (lambda s: s.update(name=""), "name"),
        (lambda s: s.update(kind=99), "kind"),
        (lambda s: s.update(startTimeUnixNano=str(int(s["endTimeUnixNano"]) + 1)), "起点晚于终点"),
        (lambda s: s.update(parentSpanId="ab" * 8), "不在这条 trace 里"),
        (lambda s: s["attributes"][0]["value"].clear(), "一个分支"),
        (lambda s: s["attributes"][0]["value"].update({"stringValue": "a", "intValue": "1"}), "一个分支"),
        (lambda s: s["attributes"][0].update(value={"intValue": 5}), "十进制字符串"),
        (lambda s: s["attributes"].append(dict(s["attributes"][0])), "出现两次"),
    ],
)
def test_validate_catches_each_collector_rejection(mutate: Any, signal: str) -> None:
    payload = _good_payload()
    mutate(_spans(payload)[0])
    problems = validate(payload)
    assert problems, "这条本该报错"
    assert any(signal in problem for problem in problems), problems


def test_validate_rejects_a_numeric_timestamp_and_a_timeless_event() -> None:
    payload = _good_payload()
    session = next(s for s in _spans(payload) if s["name"] == "agent.session")
    session["startTimeUnixNano"] = 12345  # uint64 在 JSON 里必须是字符串
    session["events"] = [{"name": "mcc.backend", "attributes": []}]
    problems = validate(payload)
    assert any("unix nano" in problem for problem in problems)
    assert any("timeUnixNano" in problem for problem in problems)


def test_an_empty_payload_is_not_a_clean_one() -> None:
    assert validate({"resourceSpans": []})
    assert validate({})
    assert render([])["resourceSpans"][0]["scopeSpans"] == []


# ------------------------------------------------------------------ 发送


def test_post_refuses_a_non_http_endpoint_without_sending() -> None:
    receipt = post_otlp(_good_payload(), "localhost:4318/v1/traces")
    assert receipt["ok"] is False
    assert "http" in receipt["error"]


def test_post_sends_nothing_when_validation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("校验没过就不该碰网络")

    monkeypatch.setattr(httpx, "post", boom)
    payload = _good_payload()
    _spans(payload)[0]["name"] = ""
    receipt = post_otlp(payload, "http://localhost:4318/v1/traces")
    assert receipt["ok"] is False
    assert "校验未过" in receipt["error"]


def test_a_transport_failure_becomes_a_receipt_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", refuse)
    receipt = post_otlp(_good_payload(), "http://localhost:4318/v1/traces")
    assert receipt["ok"] is False
    assert "ConnectError" in receipt["error"]


def test_a_rejected_response_keeps_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class Response:
        status_code = 400
        text = "TracesData: needs to contain at least one ResourceSpan"

    monkeypatch.setattr(httpx, "post", lambda *a, **k: Response())
    receipt = post_otlp(_good_payload(), "http://localhost:4318/v1/traces")
    assert receipt["ok"] is False
    assert receipt["status_code"] == 400
    assert "ResourceSpan" in receipt["error"]


def test_a_local_endpoint_is_told_apart_from_a_remote_one() -> None:
    from miniclaude.infra.otel import is_local

    assert is_local("http://localhost:4318/v1/traces")
    assert is_local("http://127.0.0.1:4317/v1/traces")
    assert not is_local("https://collector.example.com/v1/traces")


# ------------------------------------------------------------------ SDK 边界


def test_the_sdk_stays_out_of_the_instrumentation() -> None:
    """SPEC v2 §3.1：埋点不引入 OTel SDK。导出这一层也不许 —— 它只做翻译。"""
    import sys

    import miniclaude.agent.loop  # noqa: F401
    import miniclaude.infra.otel  # noqa: F401

    offenders = [name for name in sys.modules if name.startswith("opentelemetry")]
    assert not offenders, f"埋点或导出器把 {offenders} 拖进来了"
    # 整个包里没有一行 import 它：`mcc trace --otel` 不装 collector 也能跑
    imports = [
        path.relative_to(REPO).as_posix()
        for path in (REPO / "src" / "miniclaude").rglob("*.py")
        if "import opentelemetry" in path.read_text(encoding="utf-8")
    ]
    assert imports == []


def test_gaps_declares_what_the_translation_cannot_carry() -> None:
    listed = {item["item"] for item in gaps()}
    assert {"null 值", "时间区间", "span 层级", "本地路径与端点地址"} <= listed
    for item in gaps():
        assert item["lost"] and item["handling"]


def test_the_scope_and_service_are_ours(traced: Tracer) -> None:
    traced.log("session_start", span_id=SESSION, model="m", tools=[], config={})
    payload = translate(replay(traced.path))[0]
    resource = payload["resourceSpans"][0]
    assert resource["scopeSpans"][0]["scope"] == {"name": SERVICE_NAME, "version": SCHEMA_VERSION}
    keys = {item["key"] for item in resource["resource"]["attributes"]}
    assert {"service.name", "mcc.trace.schema_version", "mcc.session.count"} <= keys
    assert spans_of(replay(traced.path))[0].records[0]["kind"] == "session_start"


def test_the_resource_also_reads_the_records_without_a_span_id(traced: Tracer) -> None:
    """v1 轨迹一条记录都没有 span_id，全住在 events 里。`_resource` 只扫 records 就会把
    一个真有会话、真问过模型的轨迹报成 0 个会话 + 空串模型 —— 那是编出来的数。"""
    traced.log("session_start", model="agnes-2.5-flash", tools=[], config={})
    traced.log("run_end", run_id="run1", ok=True, turns=1)
    payload, cov = translate(replay(traced.path))
    assert cov["roles"] == {"orphan": 1}
    attrs = _attrs(payload["resourceSpans"][0]["resource"])
    assert attrs["mcc.session.count"]["intValue"] == "1"
    assert attrs["gen_ai.request.model"]["stringValue"] == "agnes-2.5-flash"
    span = _spans(payload)[0]
    assert _attrs(span)["gen_ai.conversation.id"]["stringValue"] == "sess0001"
    # 一条记录也没折进 span，就不要报一个空的 kind 清单：event 上有自己的名字。
    assert "mcc.span.record_kinds" not in _attrs(span)
    assert validate(payload) == []


def test_a_resource_attribute_is_either_measured_or_absent() -> None:
    """`0 个会话` 是个测出来的数，要留着；没人报过模型就不写 `gen_ai.request.model`。
    区分这两件事靠"空串缺席、0 在场"，靠不了读代码的人的记性。"""
    records = [{"kind": "run_start", "trace_id": TRACE, "span_id": RUN, "run_id": "run1", "ts": 1.0}]
    payload, cov = translate(records)
    attrs = _attrs(payload["resourceSpans"][0]["resource"])
    assert attrs["mcc.session.count"]["intValue"] == "0"
    assert "gen_ai.request.model" not in attrs
    assert cov["records_in"] == 1
