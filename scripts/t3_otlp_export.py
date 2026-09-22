"""Tier 3 #3 的证据：把 24 份**已入库的真实轨迹**翻成 OTLP/JSON，然后量翻译丢了什么。

SPEC v2 §7.3-3 的原话是「字段已在 S8 对齐，这一步只有翻译」。「只有」是个很强的词，
所以这份证据不证明"能导出"（单元测试已经证明了），它证明的是三件更容易吹牛的事：

1. **一条记录都没丢。** 记账靠"折叠数 + event 数 = 输入数"，一个 span 吃几条记录、
   一条记录挂哪个 span 都从 payload 里**反查**出来 —— 不读 `translate()` 内部的账本，
   否则它算错什么我就跟着信什么。
2. **一个字段值都没丢。** 逐字段比对：每条记录的每个非信封字段，都按 `FIELD_MAP`
   算出它"应该在的名字"，再去 payload 里核对**编码后的值**是否相等。
   这比"字段名都在表里"强得多 —— 前者会放过"名字对了、值被吃掉"。
3. **没有编出 trace 里没有的东西。** span 数量、父子关系、时间来源、资源属性（导出器自己
   开口的那一处）都对着 trace 核。

口径五条，都是这份证据自己会被骗的地方：
· **只用已入库的轨迹**（`git ls-files "*.jsonl"`，24 份）。0 个模型请求、0 token ——
  这层是纯映射，花额度去证明它等于花钱买一个单元测试能给的结论。
· **不看 `Config.api_key`。** 判"密钥没进 payload"用的是形状（`_SECRET_LIKE`），
  不是把那把真钥匙读进内存再比对 —— 一个导出器测试不该成为密钥的读取方。
· **每个 detector 先挨一次假错**（`_detector_probes`）。十种错塞进 payload 的深拷贝，
  对应的 detector 必须红：吃值、造名、空串冒充 null、漏密钥、改边、错挂边、伪造时长、抹戳、
  资源属性与 trace 不符、往收集端探针的 payload 里塞一条绝对路径。
  红不了的 detector 给出的 ✓ 等于零 —— 这一节是给上面那些 ✓ 定价的。
· **写盘之前先跟盘上那份比**（`scripts/_evidence.py` 的 `write_evidence`）。README §10 第 30 行
  记着这条守卫原先只有 `b2_compact_ab.py` 有；现在四个写证据的脚本共用一把尺，
  而"变薄"的定义写在每个脚本自己的 `_metrics()` 里 —— 只量覆盖，不量结论。
· **"本机没有收集端"这一句是量出来的**（`_collector`，默认打 `localhost:4318`）。发出去的是
  手写 4 条记录翻出来的最小 payload，不是语料里那份带 59 个绝对路径的真轨迹 —— 未量的理由
  不能是一句背下来的话，也不必拿仓库内容去换。远端 host 一律不代发。

用法：
  PYTHONPATH="src" python -X utf8 scripts/t3_otlp_export.py
  PYTHONPATH="src" python -X utf8 scripts/t3_otlp_export.py --collector http://127.0.0.1:4318/v1/traces
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from _evidence import write_evidence  # noqa: E402
from miniclaude.cli import trace_cmd  # noqa: E402
from miniclaude.config import Config  # noqa: E402
from miniclaude.infra import otel  # noqa: E402
from miniclaude.infra.otel import gaps, translate, validate  # noqa: E402
from miniclaude.infra.trace import _SECRET_LIKE, SCHEMA_VERSION, replay  # noqa: E402

RESULT = ROOT / "eval" / "results" / "t3-otlp.json"
SCHEMA_SNAPSHOT = ROOT / "tests" / "schema_v2.json"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}  # noqa: S104
# OTLP/HTTP 的约定端口。写在这里是因为"试一次本机端口"是这份证据的默认动作，不是可选动作。
LOCAL_COLLECTOR = "http://localhost:4318/v1/traces"

# 泄露面的两个形状。语料扫描和收集端探针共用同一份定义 —— 两把尺子量出来的"0 处"才算一个数。
ABS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"']{4,}|/(?:home|Users|mnt)/[^\s\"']{3,}")
ENDPOINT_LIKE = re.compile(r"https?://[^\s\"']{4,}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _tracked_traces() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "*.jsonl"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.split()
    return sorted(ROOT / line.replace("\\", "/") for line in listed if not line.endswith("manifest.jsonl"))


def _spans_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        span
        for resource in payload.get("resourceSpans", [])
        for scope in resource.get("scopeSpans", [])
        for span in scope.get("spans", [])
    ]


def _attributes(node: dict[str, Any]) -> dict[str, Any]:
    """KeyValue 数组 → {名字: 编码后的值}。保留编码形状，比对时才不会把 '1473' 和 1473 混起来。"""
    return {item["key"]: item["value"] for item in node.get("attributes", [])}


def _nodes_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """span 加 event：值侧的统计必须两边都走，否则"event 里的字段没人查"是个真空。"""
    spans = _spans_of(payload)
    return spans + [event for span in spans for event in span.get("events", [])]


def _value_scan(payload: Any) -> tuple[int, int]:
    """(payload 里 JSON null 的个数, 空 stringValue 的个数)。

    第一个数必须是 0：`AnyValue` 没有 null 分支，写进去就是收集端拒收。
    第二个数是"拿空串冒充 null"的形状 —— 协议合法，但它把'没有这个信息'说成了'这个信息是空'。
    """
    if payload is None:
        return 1, 0
    if isinstance(payload, dict):
        nulls, empties = 0, 1 if payload.get("stringValue") == "" else 0
        for value in payload.values():
            inner_nulls, inner_empties = _value_scan(value)
            nulls += inner_nulls
            empties += inner_empties
        return nulls, empties
    if isinstance(payload, list):
        scanned = [_value_scan(item) for item in payload]
        return sum(a for a, _ in scanned), sum(b for _, b in scanned)
    return 0, 0


def _enclose(value: Any) -> dict[str, Any] | None:
    """和导出器同一套编码（`_value`），但只在这一处用私有函数：换一把尺子量就是自欺。"""
    return otel._value(value)


def _declared_parents(records: list[dict[str, Any]]) -> dict[str, str]:
    """trace 自己声明的 `span_id → parent_span_id`（小写 hex，非规范的 id 由调用点数出来）。"""
    declared: dict[str, str] = {}
    for record in records:
        span_id, parent = record.get("span_id"), record.get("parent_span_id")
        if isinstance(span_id, str) and span_id and isinstance(parent, str) and parent:
            declared.setdefault(span_id.lower(), parent.lower())
    return declared


def _edge_audit(records: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, int]:
    """父子边一条条对账：trace 里声明过的边有没有被改写、有没有凭空多出来。

    比"span 数 = 根数 + 边数"那种恒等式强得多 —— 恒等式两边都由我自己数，永远红不了。
    这里每条边都要在 trace 里找到出处，找不到的必须落在会话/run 这个"补挂锚点"上
    （`spans_of()` 里那条"缺父亲的挂到会话 span"规则），否则就是编出来的。
    """
    declared = _declared_parents(records)
    # 比较用的是 lower()，非规范 id 会让对账失真，所以数出来而不是偷偷规范化
    noncanonical = sum(
        1
        for record in records
        for value in (record.get("span_id"), record.get("parent_span_id"))
        if isinstance(value, str) and value and not re.fullmatch(r"[0-9a-f]{16}", value)
    )
    kinds: dict[str, set[str]] = {}
    for span in _spans_of(payload):
        merged = str(_attributes(span).get("mcc.span.record_kinds", {}).get("stringValue", ""))
        kinds[span["spanId"]] = set(merged.split("+")) - {""}
    honored = rewired = dropped = added = added_to_anchor = 0
    for span in _spans_of(payload):
        parent, want = span.get("parentSpanId"), declared.get(span["spanId"])
        if want is None:
            if parent is not None:
                added += 1
                added_to_anchor += 1 if kinds.get(parent, set()) & {"session_start", "run_start"} else 0
            continue
        if parent is None:
            dropped += 1  # 父亲不在这批记录里：宁可不挂，但断掉的 id 留在属性上
        elif parent == want:
            honored += 1
        else:
            rewired += 1  # 挂到了别人父亲名下 —— 这是最不能要的一种错
    return {
        "declared": len(declared),
        "honored": honored,
        "rewired": rewired,
        "dropped": dropped,
        "added": added,
        "added_to_anchor": added_to_anchor,
        "noncanonical_ids": noncanonical,
    }


def _node_index(payload: dict[str, Any]) -> dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any], bool]]:
    """`(kind, seq)` → (宿主 span, 落点节点, 是不是 event)。

    全靠导出器自己盖的 `mcc.<kind>.record_seq` 戳反查：戳错了这里就查不到，
    于是"记录丢了"和"戳没打上"会变成同一个可见的红。
    """
    index: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any], bool]] = {}
    for span in _spans_of(payload):
        for node, is_event in [(span, False)] + [(event, True) for event in span.get("events", [])]:
            for name, value in _attributes(node).items():
                if name.startswith("mcc.") and name.endswith(".record_seq"):
                    key = (name[4 : -len(".record_seq")], str(value.get("intValue")))
                    index[key] = (span, node, is_event)
    return index


def _seq_of(record: dict[str, Any]) -> str:
    return str(int(record.get("seq") or 0))


def _stamp(record: dict[str, Any]) -> tuple[str, str]:
    return str(record.get("kind")), _seq_of(record)


def _expected_names(kind: str, key: str, value: Any) -> list[tuple[str, Any]]:
    """这个字段在 payload 里**应该**出现的 (属性名, 值) 清单。"""
    table = otel.FIELD_MAP.get(kind, {}).get(key)
    if isinstance(table, dict) and isinstance(value, dict):
        return [
            (table.get(sub) or f"mcc.{kind}.{key}.{sub}", sub_value)
            for sub, sub_value in value.items()
        ]
    return [(table or f"mcc.{kind}.{key}", value)]


def _registered(kind: str, key: str) -> bool:
    """这个字段在 FIELD_MAP 里有没有名字（没有就靠默认命名兜底）。"""
    return key in otel.FIELD_MAP.get(kind, {})


def _reconcile(records: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    """逐字段核对：值到底有没有出现在它该出现的那个属性名下。"""
    index = _node_index(payload)
    lost: dict[str, int] = {}
    moved: dict[str, int] = {}
    checked = skipped_null = nulls_tallied = unhosted = 0
    for record in records:
        kind, seq = _stamp(record)
        host = index.get((kind, seq))
        if host is None:
            unhosted += 1  # 记录在 payload 里连自己的戳都找不到 —— 那就是整条丢了
        node_attrs = _attributes(host[1]) if host else {}
        for key, value in record.items():
            if key in otel.ENVELOPE_KEYS:
                continue
            # 这里自己数一遍 null（顶层字段、逐次出现），用来和导出器账本上的
            # `unrepresentable_nulls` 对撞：两把尺子量同一个定义，数不等就必有一个在撒谎。
            nulls_tallied += 1 if value is None else 0
            for name, sub in _expected_names(kind, key, value):
                if sub is None:
                    skipped_null += 1
                    continue
                checked += 1
                want = _enclose(sub)
                if node_attrs.get(name) == want:
                    continue
                landed = [
                    (str(node.get("name")), k)
                    for _s, node, _e in index.values()
                    for k, v in _attributes(node).items()
                    if v == want and k != name
                ]
                tag = f"{kind}.{key}"
                if landed:
                    # 值在，但挂到了别人名下 —— 合并 span 里的重名让位（`_put`），不是丢。
                    moved[tag] = moved.get(tag, 0) + 1
                else:
                    lost[tag] = lost.get(tag, 0) + 1
    return {
        "fields_checked": checked,
        "fields_null_skipped": skipped_null,
        "null_fields_tallied": nulls_tallied,
        "records_without_a_stamp": unhosted,
        "fields_moved_under_another_name": moved,
        "fields_lost": lost,
    }


def _naming_audit(records: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    """属性名有没有出处。

    出处只有三种：`FIELD_MAP` 给的名字、导出的默认命名 `mcc.<kind>.<key>`、导出器自己盖的
    账目/语义约定属性。除此之外冒出来的名字就是翻译层编的。
    注意这里**不**用"名字长得像 `mcc.<kind>.<field>` 就算没登记"那种猜法 —— 实测会误报：
    `mcc.repo_map.omission_reasons` 是 `reasons` 的登记目标名，看着却像 `omission_reasons`
    的兜底名，第一版体检就是被它骗红的。
    """
    allowed: set[str] = {"mcc.span.record_kinds", "mcc.span.time_source", "mcc.span.detached_parent_id"}
    unregistered: set[str] = set()
    kinds = {str(r.get("kind")) for r in records}
    allowed |= {f"mcc.{kind}.record_seq" for kind in kinds}
    allowed |= {
        "gen_ai.conversation.id",
        "gen_ai.operation.name",
        "gen_ai.request.model",
        "gen_ai.response.finish_reasons",
        "gen_ai.tool.name",
        "gen_ai.tool.call.id",
    }
    for record in records:
        kind = str(record.get("kind"))
        for key, value in record.items():
            if key in otel.ENVELOPE_KEYS or value is None:
                continue
            if not _registered(kind, key):
                unregistered.add(f"{kind}.{key}")
            for name, _sub in _expected_names(kind, key, value):
                allowed.add(name)
                # `_put` 撞名让位后的形状是 `mcc.<kind>.<去掉 mcc. 前缀的原名>`，可以叠 #N
                bare = name[4:] if name.startswith("mcc.") else name.replace(".", "_")
                for loser in kinds:
                    renamed = f"mcc.{loser}.{bare}"
                    allowed.add(renamed)
                    allowed |= {f"{renamed}#{index}" for index in range(2, 10)}
    unqualified: set[str] = set()
    invented: set[str] = set()
    for node in _nodes_of(payload):
        for name in _attributes(node):
            if "." not in name:
                unqualified.add(name)  # OTLP 裸键（traceId 之类）不在 attributes 里，所以这里的就是真裸名
            if name not in allowed:
                invented.add(name)
    return {
        "invented": sorted(invented),
        "unqualified": sorted(unqualified),
        "unregistered_fields": sorted(unregistered),
    }


def _resource_audit(records: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    """资源属性是导出器唯一一处**自己开口**的地方（其余属性都是从某条记录搬过来的），
    所以它得逐条对：说出来的数，trace 里必须真的有；没说出来的，trace 里必须真的没有。

    这一节管着三个从同一类视野盲区里长出来的真错 —— `_resource` 早先只扫 `span.records`：
    1. v1 轨迹的记录全长在 `events` 上，于是 `mcc.session.count` 报 **0**（trace 里明明有 1 个）；
    2. 模型名没人报过时落成 `""` —— 空串在这里是"拿一个值冒充没有值"，和 null 塞空串同罪；
    3. `mcc.trace.schema_version` 缺省时兜到当前版本，于是 v1 轨迹自报 "2.0"：
       一个字符串形状的谎，前面所有按名字核对的判据都看不见它。
    """
    attrs = _attributes(payload["resourceSpans"][0]["resource"])
    plain = {
        key: (value.get("intValue") if "intValue" in value else value.get("stringValue"))
        for key, value in attrs.items()
    }
    sessions = {str(r.get("session")) for r in records if r.get("session")}
    models = {
        str(r.get("model")) for r in records if r.get("kind") == "session_start" and r.get("model")
    }
    versions = {str(r.get("schema_version")) for r in records if r.get("schema_version")}
    claimed_models = [m for m in str(plain.get("gen_ai.request.model", "")).split("+") if m]
    raw_version = plain.get("mcc.trace.schema_version")
    claimed_versions = [v for v in str(raw_version).split("+") if v] if raw_version else []
    disagreements: list[str] = []
    if plain.get("service.name") != otel.SERVICE_NAME:
        disagreements.append(f"service.name={plain.get('service.name')!r} != {otel.SERVICE_NAME!r}")
    if str(plain.get("mcc.session.count")) != str(len(sessions)):
        disagreements.append(
            f"mcc.session.count={plain.get('mcc.session.count')} vs trace 里 {len(sessions)} 个会话"
        )
    if sorted(claimed_models) != sorted(models):
        disagreements.append(f"gen_ai.request.model={claimed_models} vs trace 里 {sorted(models)}")
    if sorted(claimed_versions) != sorted(versions):
        disagreements.append(
            f"mcc.trace.schema_version={claimed_versions or '（不写）'} vs trace 里 {sorted(versions) or '没人自报过'}"
        )
    return {
        "attributes": {key: plain[key] for key in sorted(plain)},
        "disagreements": disagreements,
        "empty_string_attributes": sum(1 for value in plain.values() if value == ""),
    }


def _time_audit(records: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    """时间戳的算术核对：区间端点必须真的由这批记录的 `ts`/`latency` 算出来。

    比"每个 span 都写了 `mcc.span.time_source`"强 —— 标签写得再全，端点也可能是随手填的。
    规则就是 `gaps()` 里公开的那条：`start = min(ts, ts - latency)`、`end = max(ts)`。
    """
    index = _node_index(payload)
    grouped: dict[int, list[dict[str, Any]]] = {}
    unplaced = 0
    for record in records:
        host = index.get(_stamp(record))
        if host is None:
            unplaced += 1
            continue
        grouped.setdefault(id(host[0]), []).append(record)

    def nano(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(round(float(value) * 1_000_000_000))

    checked = exact = off = label_exact = label_off = 0
    event_exact = event_off = 0
    for span in _spans_of(payload):
        group = grouped.get(id(span), [])
        checked += 1
        stamps = [n for n in (nano(r.get("ts")) for r in group) if n is not None]
        starts = list(stamps)
        reconstructed = False
        for record in group:
            stamp, latency = nano(record.get("ts")), record.get("latency")
            if stamp is None or isinstance(latency, bool) or not isinstance(latency, (int, float)):
                continue
            if latency <= 0 or span.get("name") == "mcc.unhosted":
                continue  # 承载 span 只吸收端点，不反推延迟（`_absorb`）
            starts.append(stamp - int(round(float(latency) * 1_000_000_000)))
            reconstructed = True
        want = (min(starts), max(stamps)) if stamps and max(stamps) else (0, 0)
        got = (int(span["startTimeUnixNano"]), int(span["endTimeUnixNano"]))
        if got == (min(want[0], want[1]), max(want[0], want[1])):
            exact += 1
        else:
            off += 1
        declared = str(_attributes(span).get("mcc.span.time_source", {}).get("stringValue", ""))
        wants_label = "latency-reconstructed" if reconstructed else ("record-timestamps" if stamps else "absent")
        if declared == wants_label:
            label_exact += 1
        else:
            label_off += 1
        for event in span.get("events", []):
            event_attrs = _attributes(event)
            for record in group:
                kind = str(record.get("kind"))
                if event_attrs.get(f"mcc.{kind}.record_seq") != {"intValue": _seq_of(record)}:
                    continue  # 同名 kind 的 event 可能有好几条，靠 seq 认人
                if int(event["timeUnixNano"]) == (nano(record.get("ts")) or 0):
                    event_exact += 1
                else:
                    event_off += 1
    return {
        "spans_checked": checked,
        "intervals_exact": exact,
        "intervals_off": off,
        "labels_exact": label_exact,
        "labels_off": label_off,
        "event_times_exact": event_exact,
        "event_times_off": event_off,
        "records_without_a_host": unplaced,
    }


def _pick_field(records: list[dict[str, Any]], payload: dict[str, Any]) -> tuple[str, ...]:
    """找两个靶子：一个"值确实落在某个节点上"的字段，一个值为 null 的字段。

    探针要动真字段，靶子也得从真实 payload 里挑 —— 拿假靶子做实验，
    detector 红了也说明不了它对真数据有效。
    """
    index = _node_index(payload)
    present: tuple[str, ...] | None = None
    null_target: tuple[str, ...] | None = None
    for record in records:
        kind, seq = _stamp(record)
        host = index.get((kind, seq))
        if host is None:
            continue
        attributes = _attributes(host[1])
        for key, value in record.items():
            if key in otel.ENVELOPE_KEYS:
                continue
            for name, sub in _expected_names(kind, key, value):
                if null_target is None and value is None:
                    null_target = (kind, seq, name)
                if present is None and sub is not None and attributes.get(name) == _enclose(sub):
                    present = (kind, seq, name)
    if present is None or null_target is None:
        raise AssertionError("这份轨迹里找不到探针要的靶子字段，换一个 --probe-trace")
    return (*present, *null_target)


def _detector_probes(path: Path) -> list[dict[str, Any]]:
    """给每个 detector 塞一种错，看它红不红 —— 红不了的 detector，它的 ✓ 不值钱。

    十种错就是这份证据里判据各自的反面：吃掉一个值、造一个名字、拿空串冒充 null、
    漏一条密钥、改写一条边、把补挂的边挪到别的锚点、伪造一段时长、抹掉一枚记账戳、
    让资源开口说一件 trace 里没发生过的事、往收集端那道"不带内容出门"的闸门里塞一条绝对路径。
    前九种做在语料 payload 的深拷贝上，最后一种做在手写探针 payload 上，真实轨迹一个字节都不动。
    """
    records = [r for r in replay(path) if isinstance(r, dict)]
    payload = translate(records)[0]
    kind, seq, field, null_kind, null_seq, null_field = _pick_field(records, payload)
    declared = _declared_parents(records)
    baseline_empties = _value_scan(payload)[1]
    probes: list[dict[str, Any]] = []

    def mutate(build: Any, detected: Any, label: str) -> None:
        copy = json.loads(json.dumps(payload))
        build(copy)
        value = detected(copy)
        probes.append({"probe": label, "fired": bool(value), "detected": value})

    def node(copy: dict[str, Any], k: str = kind, s: str = seq) -> dict[str, Any]:
        return _node_index(copy)[(k, s)][1]

    def drop_attribute(copy: dict[str, Any]) -> None:
        target = node(copy)
        target["attributes"] = [a for a in target["attributes"] if a["key"] != field]

    def plant_name(copy: dict[str, Any]) -> None:
        _spans_of(copy)[0]["attributes"].append({"key": "invented.by.probe", "value": {"stringValue": "x"}})

    def forge_null_as_empty(copy: dict[str, Any]) -> None:
        node(copy, null_kind, null_seq)["attributes"].append(
            {"key": null_field, "value": {"stringValue": ""}}
        )

    def leak_secret(copy: dict[str, Any]) -> None:
        node(copy)["attributes"].append(
            {"key": field, "value": {"stringValue": "sk-DEADBEEF1234567890abcdef"}}
        )

    def rewire_edge(copy: dict[str, Any]) -> None:
        for span in _spans_of(copy):
            if span["spanId"] in declared and span.get("parentSpanId"):
                span["parentSpanId"] = next(s["spanId"] for s in _spans_of(copy) if s is not span)
                return

    def misanchor_edge(copy: dict[str, Any]) -> None:
        # 补挂本身是公开规则（缺父亲的挂到会话 span），要抓的是"挂到了规则之外的节点"
        victims = [s for s in _spans_of(copy) if s.get("parentSpanId") and s["spanId"] not in declared]
        targets = [s for s in _spans_of(copy) if s["name"].startswith("execute_tool") or s["name"].startswith("chat ")]
        if victims and targets:
            victims[0]["parentSpanId"] = targets[0]["spanId"]

    def fake_duration(copy: dict[str, Any]) -> None:
        for span in _spans_of(copy):
            if int(span["endTimeUnixNano"]) > int(span["startTimeUnixNano"]):
                span["endTimeUnixNano"] = str(int(span["endTimeUnixNano"]) + 1_000_000_000)
                return

    def erase_stamp(copy: dict[str, Any]) -> None:
        target = node(copy)
        target["attributes"] = [a for a in target["attributes"] if not a["key"].endswith(".record_seq")]

    def starve_resource(copy: dict[str, Any]) -> None:
        # v1 那个真错的形状：资源开口说"0 个会话"，而 trace 里躺着好几个
        for item in copy["resourceSpans"][0]["resource"]["attributes"]:
            if item["key"] == "mcc.session.count":
                item["value"] = {"intValue": "0"}

    def stray_edges(copy: dict[str, Any]) -> int:
        audit = _edge_audit(records, copy)
        return audit["added"] - audit["added_to_anchor"]

    mutate(drop_attribute, lambda c: _reconcile(records, c)["fields_lost"], "字段值被吃掉")
    mutate(plant_name, lambda c: _naming_audit(records, c)["invented"], "属性名是编的")
    mutate(forge_null_as_empty, lambda c: _value_scan(c)[1] > baseline_empties, "null 被空串冒充")
    mutate(leak_secret, lambda c: len(_SECRET_LIKE.findall(json.dumps(c, ensure_ascii=False))), "密钥漏进 payload")
    mutate(rewire_edge, lambda c: _edge_audit(records, c)["rewired"], "父子边被改写")
    mutate(misanchor_edge, stray_edges, "补挂的边挪到了规则之外")
    mutate(fake_duration, lambda c: _time_audit(records, c)["intervals_off"], "时长是伪造的")
    mutate(erase_stamp, lambda c: _reconcile(records, c)["records_without_a_stamp"], "记录的记账戳没了")
    mutate(
        starve_resource,
        lambda c: _resource_audit(records, c)["disagreements"],
        "资源里的会话数与 trace 不符",
    )

    # 收集端那道"不带仓库内容出门"的闸门也要能红：干净的手写 payload 必须扫出 0，
    # 塞一条绝对路径进去必须扫出 1。只在"清洁侧为 0"时这次的红才算数 —— 否则它是白给的。
    probe_copy = json.loads(json.dumps(translate(_PROBE_RECORDS)[0]))
    clean = _leak_scan(json.dumps(probe_copy, ensure_ascii=False))
    _spans_of(probe_copy)[0]["attributes"].append(
        {"key": "mcc.probe.planted", "value": {"stringValue": "D:/repo/calculator/core.py"}}
    )
    planted = _leak_scan(json.dumps(probe_copy, ensure_ascii=False))
    probes.append(
        {
            "probe": "收集端探针的 payload 里带仓库形状",
            "fired": bool(planted["absolute_local_paths"])
            and not (clean["absolute_local_paths"] or clean["endpoint_like_strings"]),
            "detected": {"clean": clean, "planted": planted["absolute_local_paths"]},
        }
    )
    return probes


def _trace_digest(path: Path) -> tuple[dict[str, Any], set[str]]:
    """一份轨迹的账：trace 侧、payload 侧、以及两边对不对得上。第二个返回值是属性名集合
    （给全局去重用，不进 JSON —— 24 份文件名集高度重叠，逐份存一遍是噪音）。"""
    text = path.read_text(encoding="utf-8")
    records = [r for r in replay(path) if isinstance(r, dict)]
    payload, coverage = translate(records)
    again, _ = translate(records)
    rendered = json.dumps(payload, ensure_ascii=False)
    spans = _spans_of(payload)
    nodes = _nodes_of(payload)
    attrs = [item for node in nodes for item in node.get("attributes", [])]
    names = [item["key"] for item in attrs]
    values = [item["value"] for item in attrs]
    nulls, _payload_empties = _value_scan(payload)
    _node_nulls, node_empties = _value_scan([node.get("attributes", []) for node in nodes])
    # 源里本来就有的空串：`""` 是合法值，不是伪造的 null。分不清这两种，
    # "payload 里没有空串"这条判据就会被一堆正经的空 note 冤枉掉（第一版就是这么红的）。
    source_empties = sum(
        1 for r in records for k, v in r.items() if v == "" and k not in otel.ENVELOPE_KEYS
    )
    time_sources: dict[str, int] = {}
    for span in spans:
        source = _attributes(span).get("mcc.span.time_source")
        key = str(source["stringValue"]) if source else "missing"
        time_sources[key] = time_sources.get(key, 0) + 1
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "trace": {
            "bytes": len(text.encode("utf-8")),
            "records": len(records),
            "sessions": len({str(r.get("session")) for r in records}),
            "schema_versions": sorted({str(r.get("schema_version") or "none") for r in records}),
            "span_ids": len({str(r.get("span_id")) for r in records if r.get("span_id")}),
            "kinds": sorted({str(r.get("kind")) for r in records}),
            "masked_prefixes": len(_SECRET_LIKE.findall(text)),
        },
        "payload": {
            "bytes": len(rendered.encode("utf-8")),
            "spans": len(spans),
            "roots": sum(1 for s in spans if not s.get("parentSpanId")),
            "parented_spans": sum(1 for s in spans if s.get("parentSpanId")),
            "events": sum(len(s.get("events", [])) for s in spans),
            "attributes": len(attrs),
            "distinct_attribute_names": len(set(names)),
            "null_values": nulls,
            "empty_string_attributes": node_empties,
            "empty_strings_in_source": source_empties,
            "time_sources": time_sources,
            "spans_without_time_source": sum(c for k, c in time_sources.items() if k == "missing"),
            "string_values_holding_json": sum(
                1
                for value in values
                if isinstance(value.get("stringValue"), str) and value["stringValue"][:1] in "[{"
            ),
            "zero_duration_spans": sum(
                1 for s in spans if s["startTimeUnixNano"] == s["endTimeUnixNano"]
            ),
            "red_spans": sum(1 for s in spans if s["status"]["code"] == otel.STATUS_ERROR),
            "detached_parents": sum(
                1 for s in spans if "mcc.span.detached_parent_id" in _attributes(s)
            ),
            "orphan_host_spans": sum(1 for s in spans if s["name"] == "mcc.unhosted"),
            "absolute_local_paths": len(_leak_scan(rendered)["absolute_local_paths"]),
            "endpoint_like_strings": len(_leak_scan(rendered)["endpoint_like_strings"]),
        },
        "coverage": {
            key: coverage[key]
            for key in (
                "records_in",
                "spans_out",
                "records_folded_into_spans",
                "records_emitted_as_events",
                "accounted_for",
                "unaccounted",
                "unrepresentable_nulls",
            )
        },
        "roles": coverage["roles"],
        "edges": _edge_audit(records, payload),
        "reconciliation": _reconcile(records, payload),
        "naming": _naming_audit(records, payload),
        "resource": _resource_audit(records, payload),
        "timing": _time_audit(records, payload),
        "validation": validate(payload),
        "deterministic": json.dumps(payload, sort_keys=True) == json.dumps(again, sort_keys=True),
        "masked_prefixes_in_payload": len(_SECRET_LIKE.findall(rendered)),
    }, set(names)


def _cli_agrees(path: Path, records: list[dict[str, Any]], out: Path) -> dict[str, Any]:
    """库和 CLI 两条路对同一份会话必须给同一批字节，否则"哪个是真的"就没法回答。"""
    latest = next((str(r["session"]) for r in reversed(records) if r.get("session")), "")
    out.parent.mkdir(parents=True, exist_ok=True)
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
        code = trace_cmd.run([latest, "--otel", "--out", str(out)], _config_for(path, out))
    written = out.read_bytes() if out.is_file() else b""
    library = json.dumps(translate(_session(records, latest))[0], ensure_ascii=False).encode("utf-8")
    return {
        "exit_code": code,
        "session": latest,
        "cli_bytes": len(written),
        "library_bytes": len(library) + 1,  # CLI 落盘带一个换行
        "identical": written.rstrip(b"\n") == library,
        "gaps_printed": stderr.getvalue().count("翻不过去："),
    }


def _config_for(path: Path, out: Path) -> Config:
    return Config(
        base_url="https://mock.local/v1",
        api_key="sk-test-abcdefghijklmn",
        model="mock-model",
        project_root=out.parent,
        trace_path=path,
    )


def _session(records: list[dict[str, Any]], session: str) -> list[dict[str, Any]]:
    return [r for r in records if r.get("session") == session]


# 收集端探针的靶子：**手写**的四条记录，一个仓库字节都不带。
# 为什么不拿语料里那份真轨迹去 POST：这个动作本身是"把东西发出去"。本机 4318 今天没人听，
# 明天可能躺着别人的服务，那时送去的就不是翻译证据，而是 59 个绝对路径 + 仓库正文。
# 手写的记录问的是同一个问题（这台机器上有没有一个按 OTLP 应答的东西），代价为零。
_PROBE_RECORDS: list[dict[str, Any]] = [
    {
        "seq": 1,
        "ts": 1_700_000_000.0,
        "session": "probe0000",
        "trace_id": "probe-trace",
        "schema_version": SCHEMA_VERSION,
        "kind": "session_start",
        "span_id": "probe-session",
        "model": "probe-model",
        "tools": ["read_file"],
    },
    {
        "seq": 2,
        "ts": 1_700_000_001.0,
        "session": "probe0000",
        "trace_id": "probe-trace",
        "schema_version": SCHEMA_VERSION,
        "kind": "run_start",
        "span_id": "probe-run",
        "parent_span_id": "probe-session",
        "turn": 1,
    },
    {
        "seq": 3,
        "ts": 1_700_000_002.0,
        "session": "probe0000",
        "trace_id": "probe-trace",
        "schema_version": SCHEMA_VERSION,
        "kind": "tool_call",
        "span_id": "probe-tool",
        "parent_span_id": "probe-run",
        "turn": 1,
        "name": "read_file",
        "ok": True,
        "output_chars": 12,
        "duration_ms": 3,
    },
    {
        "seq": 4,
        "ts": 1_700_000_003.0,
        "session": "probe0000",
        "trace_id": "probe-trace",
        "schema_version": SCHEMA_VERSION,
        "kind": "run_end",
        "span_id": "probe-run",
        "parent_span_id": "probe-session",
        "turn": 1,
        "terminal_reason": "done",
    },
    ]


def _leak_scan(text: str) -> dict[str, list[str]]:
    """一份 payload 里"仓库形状"的两类痕迹。语料侧的计数与收集端那道闸门共用这一把尺子。"""
    return {
        "absolute_local_paths": sorted(set(ABS_PATH.findall(text))),
        "endpoint_like_strings": sorted(set(ENDPOINT_LIKE.findall(text))),
    }


def _receipt_detail(receipt: dict[str, Any]) -> str:
    """把一次真实的收发写成一句测出来的话，而不是一句背下来的话。"""
    if receipt.get("ok"):
        return (
            f"{receipt['endpoint']} 回了 HTTP {receipt['status_code']} —— 收下是量到了，"
            "'画出树'仍然要人眼：这条只在真接了 Jaeger/Tempo 的机器上才算完整量过"
        )
    if receipt.get("status_code") is not None:
        code = receipt["status_code"]
        said = (receipt.get("error") or "").strip()
        # `post_otlp` 对空响应体回的就是"HTTP 502，响应体是空的"这句 —— 再抄一遍等于没说。
        body = said[:160] if said and not said.startswith(f"HTTP {code}") else "响应体是空的"
        return (
            f"{receipt['endpoint']} 回了 HTTP {code}（{body}）。按**未量**记账而不是判 ✗："
            "不是 OTLP 的服务和没人应答的端口，都不构成'我们的 payload 被拒'的证据"
        )
    return (
        f"{receipt['endpoint']} 没有 OTLP 收集端（{receipt.get('error') or '无错误可报'}）。"
        "按**未量**记账而不是判 ✗：这条要的是有人应答，本机没有；"
        "校验器挡的是协议层会拒的东西，挡不住渲染层的解释差异"
    )


def _collector(endpoint: str) -> dict[str, Any]:
    """真收集端：**默认就试本机**，远端一律不代发。

    为什么要试：这条判据的结论是"未量"，而未量的理由必须是量出来的。上一版默认一次都不试
    （`attempted: false`），而同一份文件里那句"4318 回 502"是写死的 —— 测的和说的各说各话，
    正是这个项目对着自己的证据挑过无数遍的那处毛病。
    """
    host = re.sub(r"^.*?://", "", endpoint).split("/")[0].split(":")[0]
    if host not in LOCAL_HOSTS:
        return {
            "endpoint": endpoint,
            "attempted": False,
            "ok": None,
            "detail": f"{host} 不在本机，脚本不代发（那是要人点头的动作）",
        }
    payload = translate(_PROBE_RECORDS)[0]
    blob = json.dumps(payload, ensure_ascii=False)
    leaks = _leak_scan(blob)
    if leaks["absolute_local_paths"] or leaks["endpoint_like_strings"]:
        # 手写的四条记录里根本没有路径和端点：探到就是导出器从环境里捞了东西出来。
        # 那种 payload 更要留在本机 —— 拦下来之后还要在报告里留名，不许静默改道。
        return {
            "endpoint": endpoint,
            "attempted": False,
            "ok": None,
            "leaks": leaks,
            "detail": "探针 payload 里出现了仓库形状，导出器在从环境里捞内容 —— 一个字节都不发",
        }
    receipt = otel.post_otlp(payload, endpoint, timeout=3.0)
    return {
        "endpoint": endpoint,
        "attempted": True,
        "ok": True if receipt.get("ok") else None,
        "payload_records": len(_PROBE_RECORDS),
        "payload_bytes": len(blob.encode("utf-8")),
        "leaks": {key: len(value) for key, value in leaks.items()},
        "payload_scope": "手写的 4 条记录：0 个仓库路径、0 个端点地址、0 个真实 session id",
        "receipt": receipt,
        "detail": _receipt_detail({**receipt, "endpoint": endpoint}),
    }


def _totals(digests: list[dict[str, Any]]) -> dict[str, Any]:
    def add(*paths: str) -> int:
        total = 0
        for digest in digests:
            node: Any = digest
            for part in paths:
                node = node.get(part, {}) if isinstance(node, dict) else 0
            total += node if isinstance(node, int) else 0
        return total

    lost = sum(sum(d["reconciliation"]["fields_lost"].values()) for d in digests)
    moved = sum(sum(d["reconciliation"]["fields_moved_under_another_name"].values()) for d in digests)
    problems = sum(len(d["validation"]) for d in digests)
    merged: dict[str, dict[str, int]] = {}
    for key in ("edges", "timing"):
        merged[key] = {}
        for digest in digests:
            for name, count in digest[key].items():
                merged[key][name] = merged[key].get(name, 0) + count
    flags: dict[str, list[str]] = {}
    for digest in digests:
        for key, items in digest["naming"].items():
            flags.setdefault(key, []).extend(items)
    time_sources: dict[str, int] = {}
    for digest in digests:
        for source, count in digest["payload"]["time_sources"].items():
            time_sources[source] = time_sources.get(source, 0) + count
    resource_presence: dict[str, int] = {}
    for digest in digests:
        for name in digest["resource"]["attributes"]:
            resource_presence[name] = resource_presence.get(name, 0) + 1
    return {
        "files": len(digests),
        "records": add("trace", "records"),
        "sessions": add("trace", "sessions"),
        "source_span_ids": add("trace", "span_ids"),
        "trace_bytes": add("trace", "bytes"),
        "payload_bytes": add("payload", "bytes"),
        "spans": add("payload", "spans"),
        "roots": add("payload", "roots"),
        "parented_spans": add("payload", "parented_spans"),
        "events": add("payload", "events"),
        "attributes": add("payload", "attributes"),
        "null_values_in_payload": add("payload", "null_values"),
        "empty_string_attributes": add("payload", "empty_string_attributes"),
        "empty_strings_in_source": add("payload", "empty_strings_in_source"),
        "spans_without_time_source": add("payload", "spans_without_time_source"),
        "time_sources": time_sources,
        "edges": merged["edges"],
        "timing": merged["timing"],
        "string_values_holding_json": add("payload", "string_values_holding_json"),
        "absolute_local_paths": add("payload", "absolute_local_paths"),
        "endpoint_like_strings": add("payload", "endpoint_like_strings"),
        "zero_duration_spans": add("payload", "zero_duration_spans"),
        "red_spans": add("payload", "red_spans"),
        "detached_parents": add("payload", "detached_parents"),
        "orphan_host_spans": add("payload", "orphan_host_spans"),
        "unaccounted": add("coverage", "unaccounted"),
        "unrepresentable_nulls": add("coverage", "unrepresentable_nulls"),
        "resource_names_present_in": resource_presence,
        "resource_disagreements": sum(len(d["resource"]["disagreements"]) for d in digests),
        "resource_empty_strings": add("resource", "empty_string_attributes"),
        "resource_disagreement_samples": [
            sample for d in digests for sample in d["resource"]["disagreements"][:2]
        ],
        "fields_checked": add("reconciliation", "fields_checked"),
        "fields_null_skipped": add("reconciliation", "fields_null_skipped"),
        "null_fields_tallied": add("reconciliation", "null_fields_tallied"),
        "records_without_a_stamp": add("reconciliation", "records_without_a_stamp"),
        "fields_lost": lost,
        "fields_moved": moved,
        "validation_problems": problems,
        "masked_prefixes_in_trace": add("trace", "masked_prefixes"),
        "masked_prefixes_in_payload": add("masked_prefixes_in_payload"),
        "deterministic": sum(1 for d in digests if d["deterministic"]),
        "audit_flags": {key: sorted(set(items)) for key, items in flags.items()},
    }


def _clauses(
    digests: list[dict[str, Any]],
    totals: dict[str, Any],
    collector: dict[str, Any],
    cli: dict[str, Any],
    probes: list[dict[str, Any]],
    probe_trace: str,
) -> list[dict[str, Any]]:
    flat = [d for d in digests if d["trace"]["span_ids"] == 0]
    v2 = [d for d in digests if d["trace"]["span_ids"] > 0]
    flags = totals["audit_flags"]
    edges = totals["edges"]
    timing = totals["timing"]
    moved = {
        tag: sum(d["reconciliation"]["fields_moved_under_another_name"].get(tag, 0) for d in digests)
        for tag in {t for d in digests for t in d["reconciliation"]["fields_moved_under_another_name"]}
    }
    disagreeing = [name for name, check in cli.items() if not check["identical"] or check["exit_code"] != 0]
    null_gap = [item for item in gaps() if item["item"] == "null 值"]
    invented = flags.get("invented", [])
    unqualified = flags.get("unqualified", [])
    unregistered = flags.get("unregistered_fields", [])
    # 没登记不等于编的：v1 轨迹里的老字段（`redundant_calls` 那一类）本来就不在 v2 schema 里，
    # 它们走的是公开文档写明的默认命名。判"编名字"要判的是"连默认命名规则都给不出这个名字"。
    v1_only = [
        field
        for field in unregistered
        if all("none" in d["trace"]["schema_versions"] for d in digests if field in d["naming"]["unregistered_fields"])
    ]
    return [
        {
            "id": "every_committed_trace_exports",
            "claim": f"{totals['files']}/{totals['files']} 份已入库轨迹都翻得成 OTLP/JSON，且收集端协议层校验 0 问题",
            "ok": totals["files"] >= 20 and totals["validation_problems"] == 0,
            "detail": (
                f"{totals['records']:,} 条记录 / {totals['sessions']} 个会话 → {totals['spans']} 个 span · "
                f"校验问题合计 {totals['validation_problems']} 条"
            ),
        },
        {
            "id": "no_record_is_lost",
            "claim": "一条记录都不丢（折叠数 + event 数 = 输入数，从 payload 反查而不是读导出器自己的账本）",
            "ok": totals["unaccounted"] == 0 and totals["records"] > 0,
            "detail": (
                f"输入 {totals['records']:,} · 落进 span 或 event {totals['records'] - totals['unaccounted']:,} · "
                f"未记账 {totals['unaccounted']}（其中 {totals['events']} 条是以 event 的形式落地的）"
            ),
        },
        {
            "id": "no_field_value_is_lost",
            "claim": "一个字段值都不丢（逐字段按 FIELD_MAP 算出该挂的名字，再核对编码后的值）",
            "ok": totals["fields_lost"] == 0 and totals["fields_checked"] > 5000,
            "detail": (
                f"核对 {totals['fields_checked']:,} 个字段值 · 丢 {totals['fields_lost']} · "
                f"让位改名 {totals['fields_moved']} {moved or ''} · "
                f"另有 {totals['fields_null_skipped']} 个 null 不在此列（协议没有 null 分支，见 nulls_have_no_home_and_say_so）"
            ),
        },
        {
            "id": "no_field_name_is_invented",
            "claim": "属性名全部有出处：不是 FIELD_MAP 登记的、就是默认命名/账目属性，且没有裸名",
            "ok": not invented and not unqualified and unregistered == v1_only,
            "detail": (
                f"{totals['attributes']:,} 个属性 · 凭空造的 {invented or '无'} · 裸名 {unqualified or '无'} · "
                f"靠默认命名的字段 {unregistered or '无'}，其中只出现在 v1 轨迹里的 {v1_only or '无'} —— "
                "两者相等才算'没有 v2 字段漏登记'（名字猜法误报过一次：`mcc.repo_map.omission_reasons` "
                "其实是 `reasons` 的登记目标名）"
            ),
        },
        {
            "id": "the_resource_only_says_what_the_trace_says",
            "claim": (
                "资源属性逐条回对：报出的会话数/模型/版本就是 trace 里的那个集合，"
                "没报的一定是 trace 里真没有的（空串不等于缺席）—— 盯的是导出器唯一一处自己开口的地方"
            ),
            "ok": totals["resource_disagreements"] == 0 and totals["resource_empty_strings"] == 0,
            "detail": (
                f"{totals['files']} 份 · 资源属性在场情况 {totals['resource_names_present_in']} · "
                f"与 trace 不符 {totals['resource_disagreements']} 处 · 空串值 "
                f"{totals['resource_empty_strings']} 处。`mcc.trace.schema_version` 只写在自报过版本的 "
                f"{totals['resource_names_present_in'].get('mcc.trace.schema_version', 0)} 份上，"
                f"剩下 {totals['files'] - totals['resource_names_present_in'].get('mcc.trace.schema_version', 0)} "
                "份 v1 轨迹宁可缺席（早先这里兜到当前版本，等于替 v1 撒了个 '2.0'）"
            ),
        },
        {
            "id": "no_span_is_invented",
            "claim": "span 数 == 源 span_id 数 + 承载 span 数，且承载 span 只出现在没有 span_id 的旧轨迹里",
            "ok": all(
                d["payload"]["spans"] == d["trace"]["span_ids"] + d["payload"]["orphan_host_spans"]
                for d in digests
            )
            and all(d["payload"]["orphan_host_spans"] == 0 for d in v2),
            "detail": (
                f"span {totals['spans']} = 源 span_id {totals['source_span_ids']} + 承载 "
                f"{totals['orphan_host_spans']}（承载只出现在 {len(flat)} 份旧轨迹里，v2 的 {len(v2)} 份都是 0）"
            ),
        },
        {
            "id": "edges_follow_the_trace",
            "claim": "每条父子边都在 trace 里找到出处：声明过的没被改写，凭空多出来的只落在会话/run 锚点上",
            "ok": edges["rewired"] == 0
            and edges["noncanonical_ids"] == 0
            and edges["added"] == edges["added_to_anchor"]
            and edges["dropped"] == totals["detached_parents"]
            and edges["declared"] > 100,
            "detail": (
                f"trace 声明 {edges['declared']} 条边 → 照搬 {edges['honored']} · 改写 {edges['rewired']} · "
                f"父亲缺席所以不挂 {edges['dropped']}（等于 `mcc.span.detached_parent_id` 的 "
                f"{totals['detached_parents']} 处）· 补挂 {edges['added']}，其中落在会话/run 锚点上的 "
                f"{edges['added_to_anchor']}（差额就是编出来的边）"
            ),
        },
        {
            "id": "v1_traces_collapse_honestly",
            "claim": "没有 span_id 的旧轨迹翻得出来，但只有一层，且不编父子关系",
            "ok": bool(flat)
            and all(
                d["payload"]["spans"] == 1
                and d["payload"]["roots"] == 1
                and d["payload"]["parented_spans"] == 0
                and d["coverage"]["records_emitted_as_events"] == d["trace"]["records"]
                for d in flat
            ),
            "detail": (
                f"{len(flat)}/{totals['files']} 份是这种形状。例：{flat[0]['path'] if flat else '—'} 的 "
                f"{flat[0]['trace']['records'] if flat else 0} 条记录 → {flat[0]['payload']['spans'] if flat else 0} 个 "
                f"`mcc.unhosted` span、{flat[0]['payload']['roots'] if flat else 0} 个根、"
                f"{flat[0]['payload']['parented_spans'] if flat else 0} 条 parentSpanId、"
                f"{flat[0]['payload']['events'] if flat else 0} 个 event"
            ),
        },
        {
            "id": "the_export_is_deterministic",
            "claim": "同一份输入两次 translate() 字节相同（不依赖 dict 序、不掺时钟）",
            "ok": totals["deterministic"] == totals["files"] and totals["files"] > 0,
            "detail": f"{totals['deterministic']}/{totals['files']} 份重算一致",
        },
        {
            "id": "cli_and_library_agree",
            "claim": "`mcc trace --otel --out` 写出的字节 == 库函数对同一会话产出的字节（含换行、无 \\r）",
            "ok": not disagreeing and len(cli) == totals["files"],
            "detail": (
                f"{len(cli) - len(disagreeing)}/{len(cli)} 份逐字节相同"
                + (f"，不一致的：{disagreeing[:4]}" if disagreeing else "")
                + f" · CLI 侧合计 {sum(c['cli_bytes'] for c in cli.values()):,} B · "
                f"gaps 行数合计 {sum(c['gaps_printed'] for c in cli.values())}"
            ),
        },
        {
            "id": "secrets_stay_out",
            "claim": "密钥串不出导出器（按形状判，不去读那把真钥匙）",
            "ok": totals["masked_prefixes_in_payload"] == 0,
            "detail": (
                f"trace 原文里未遮的 secret 形状 {totals['masked_prefixes_in_trace']} 处 —— "
                f"落盘时 `trace._scrub` 已把它们遮成 4 字前缀 + ***；payload 里再扫到 "
                f"{totals['masked_prefixes_in_payload']} 处（这些前缀不是密钥，`_mask` 是幂等的）。"
                "`validate()` 在发送前还会整份再扫一遍"
            ),
        },
        {
            "id": "nulls_have_no_home_and_say_so",
            "claim": "null 字段既不伪装成空串也不留进 payload，而是缺席 + 计数 + 写进 gaps",
            "ok": totals["null_values_in_payload"] == 0
            and totals["empty_string_attributes"] == totals["empty_strings_in_source"]
            and totals["null_fields_tallied"] == totals["unrepresentable_nulls"]
            and bool(null_gap),
            "detail": (
                f"{totals['unrepresentable_nulls']} 个 null 字段：payload 里 null 值 "
                f"{totals['null_values_in_payload']} 个。空串属性 {totals['empty_string_attributes']} 个，"
                f"和 trace 里本来就是空串的 {totals['empty_strings_in_source']} 个一比一 —— "
                "多出来的空串才是'拿空串冒充 null'（第一版把这两件事混了，被 14 个正经空 note 冤枉红）。"
                f"导出器账本 {totals['unrepresentable_nulls']} vs 本脚本独立计数 {totals['null_fields_tallied']}；"
                f"gaps 里那条 null 说明{'在' if null_gap else '不在'}"
            ),
        },
        {
            "id": "intervals_come_from_the_timestamps",
            "claim": "每个 span 的起止都真的由这批记录的 ts/latency 算出来，event 时刻等于它那条记录的 ts",
            "ok": timing["intervals_off"] == 0
            and timing["event_times_off"] == 0
            and timing["spans_checked"] == totals["spans"]
            and timing["records_without_a_host"] == 0,
            "detail": (
                f"{timing['spans_checked']} 个 span 逐个重算端点：对得上 {timing['intervals_exact']} · "
                f"对不上 {timing['intervals_off']} · event 时刻 {timing['event_times_exact']} 对 / "
                f"{timing['event_times_off']} 错 · 零时长 span {totals['zero_duration_spans']} 个"
                "（一个时间戳的 run 就是零长度，计数而不伪造区间）"
            ),
        },
        {
            "id": "every_span_declares_where_its_time_came_from",
            "claim": "每个 span 都自述 `mcc.span.time_source`，且自述的和重算的一致",
            "ok": totals["spans_without_time_source"] == 0 and timing["labels_off"] == 0,
            "detail": (
                f"时间来源分布 {totals['time_sources']} · 没自述的 {totals['spans_without_time_source']} 个 · "
                f"自述与重算不符 {timing['labels_off']} 个"
            ),
        },
        {
            "id": "nested_structures_become_json_strings",
            "claim": "嵌套结构落为 JSON 字符串属性 —— 这是 gaps 里写明的翻译代价，量一下有多大",
            "ok": totals["string_values_holding_json"] > 0,
            "detail": (
                f"{totals['string_values_holding_json']} 个 stringValue 里装的是 JSON"
                f"（占 {totals['attributes']:,} 个属性的 "
                f"{totals['string_values_holding_json'] / max(1, totals['attributes']):.0%}）。"
                "选 JSON 字符串而不是 `kvlistValue`：后者在 collector 侧已标 deprecated，"
                "且 `gen_ai.tool.call.arguments` 在语义约定里本来就规定是 JSON 字符串"
            ),
        },
        {
            "id": "the_corpus_is_read_only",
            "claim": "这份证据不改动它的输入（跑一次不写坏 24 份已入库轨迹）",
            "ok": totals["corpus_files_changed"] == 0,
            "detail": (
                f"{totals['files']} 份轨迹跑前跑后 sha256 全同（变了 {totals['corpus_files_changed']} 份）· "
                f"payload 里 {totals['absolute_local_paths']} 个绝对本地路径、"
                f"{totals['endpoint_like_strings']} 个端点样字符串 —— 数值本身是泄露面，见 what_this_does_not_prove"
            ),
        },
        {
            "id": "detectors_fire_on_planted_errors",
            "claim": "上面每条判据的 detector 都不是摆设：把那种错塞进去一次，它就得红一次",
            "ok": len(probes) >= 10 and all(probe["fired"] for probe in probes),
            "detail": (
                f"{sum(1 for p in probes if p['fired'])}/{len(probes)} 个探针红 · "
                f"没红的：{[p['probe'] for p in probes if not p['fired']] or '无'} · "
                f"靶子：{probe_trace}（十种错做在 payload 的深拷贝上，真实轨迹不动一个字节）"
            ),
        },
        {
            "id": "a_real_collector_accepts_the_payload",
            "claim": "真实收集端收下并能画出树 —— 未量（除非本机 4318 真有人回 2xx）",
            "ok": collector.get("ok"),
            "detail": collector.get("detail"),
        },
    ]


def _metrics(document: dict[str, Any]) -> dict[str, Any]:
    """盘上那份和新这份共用的尺：只量覆盖，不量结论 —— 判据翻红必须写得进去。

    `files` 一个都不许掉（少一份轨迹就是换了实验对象，而所有比例数字还会更好看）；
    `records` / `fields_checked` / `attributes` 留九成容差，换探针靶子会让它们抖一点。
    """
    totals = document.get("totals", {})
    return {
        "files": totals.get("files", 0),
        "records": totals.get("records", 0),
        "fields_checked": totals.get("fields_checked", 0),
        "attributes": totals.get("attributes", 0),
        "clauses": len(document.get("clauses", [])),
        "probes": len(document.get("detector_probes", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Tier 3 #3：OTLP 导出器的翻译证据")
    parser.add_argument(
        "--collector",
        default=LOCAL_COLLECTOR,
        help=f"试发给这个端点（默认本机 {LOCAL_COLLECTOR}；非本机 host 一律不代发）",
    )
    parser.add_argument("--probe-trace", default=None, help="用这份轨迹做 detector 探针（默认挑记录最多的 v2 轨迹）")
    parser.add_argument("--limit", type=int, default=0, help="只翻前 N 份（调试用，会被变薄守卫拒写）")
    parser.add_argument(
        "--allow-thinning",
        action="store_true",
        help="明知证据变薄也写盘（缩水条目会打到 stderr，SPEC/README 里欠一段说明）",
    )
    args = parser.parse_args()

    traces = _tracked_traces()
    if args.limit:
        traces = traces[: args.limit]
    # 跑前跑后各算一次 sha：这份证据读的是 24 份**已入库**轨迹，把它们改了一个字节就等于
    # 悄悄换了实验对象。
    before = {path: _sha(path) for path in traces}
    scratch = ROOT / "eval" / ".work" / "t3-otlp"
    started = time.time()
    pairs = [_trace_digest(path) for path in traces]
    digests = [digest for digest, _names in pairs]
    # 键必须是相对路径：`demos/traces/codegen.live.jsonl` 与 `demos/traces/b4-live/codegen.live.jsonl`
    # 同名不同题，按名字收就会互相覆盖，而"15 份一致"看起来和"24 份一致"一样顺眼。
    cli = {
        digest["path"]: _cli_agrees(
            ROOT / digest["path"],
            replay(ROOT / digest["path"]),
            scratch / f"{index:02d}-{digest['path'].replace('/', '_')}.json",
        )
        for index, digest in enumerate(digests)
    }
    totals = _totals(digests)
    totals["distinct_attribute_names"] = len(set().union(*[names for _d, names in pairs]))
    totals["corpus_files_changed"] = sum(1 for path, sha in before.items() if _sha(path) != sha)
    totals["growth_ratio"] = round(totals["payload_bytes"] / max(1, totals["trace_bytes"]), 2)
    collector = _collector(args.collector)
    # 探针要有靶子：默认挑记录最多的那份 v2 轨迹（v1 没有 span_id，边类探针没地方下嘴）。
    probe_path = Path(args.probe_trace) if args.probe_trace else max(
        (ROOT / d["path"] for d in digests if d["trace"]["span_ids"]),
        key=lambda p: len([r for r in replay(p) if isinstance(r, dict)]),
    )
    probes = _detector_probes(probe_path)
    clauses = _clauses(digests, totals, collector, cli, probes, str(probe_path.relative_to(ROOT)))
    totals["clauses_count"] = len(clauses)

    document = {
        "schema": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "spec": "SPEC v2 §7.3-3（OTLP 导出器，Tier 3）/ §3.1 导出映射不引入 SDK",
        "engine": "无引擎：24 份已入库轨迹，0 个模型请求",
        "live_tokens_spent": 0,
        "corpus": [digest["path"] for digest in digests],
        "totals": totals,
        "clauses": clauses,
        "per_trace": digests,
        "cli_vs_library": cli,
        "collector_probe": collector,
        "detector_probes": probes,
        "gaps": gaps(),
        "pass": all(clause["ok"] for clause in clauses if clause["ok"] is not None),
        "unmeasured": [clause["id"] for clause in clauses if clause["ok"] is None],
        "wall_seconds": round(time.time() - started, 2),
    }
    document["what_this_proves"] = [
        f"{clause['id']}：{clause['claim']}" for clause in clauses if clause["ok"]
    ]
    document["what_this_does_not_prove"] = [
        (
            "真实收集端收下并画出树 —— "
            + (collector.get("detail") or collector.get("receipt", {}).get("error") or "一次都没试")
            + f"（见 {', '.join(document['unmeasured']) or '无'}；"
            "探针发的是手写的 4 条记录，不带仓库内容）"
        ),
        f"逐字段核对覆盖 {totals['fields_checked']:,} 个值，但 {totals['fields_null_skipped']} 个 null 是**跳过**的：",
        "协议里没有 null 这个值，所以它们的下场是'属性缺席 + 计数'，不是'翻对了'",
        "属性名对齐 GenAI 语义约定这件事由 FIELD_MAP 与单测钉住，不代表收集端按约定的方式解释它们",
        "payload 里带着绝对本地路径与端点地址（`mcc.session.config`），这份证据不证明跨机发送是安全的",
    ]

    if not write_evidence(
        RESULT,
        document,
        _metrics,
        exact=("files", "clauses", "probes"),
        allow_thinning=args.allow_thinning,
        note=(
            "--limit 只翻前几份是调试用的，它产出的那份不是同一件事的证据 —— 别让它覆盖入库那份。"
        ),
        sort_keys=True,
    ):
        return 2

    for clause in clauses:
        mark = {True: "✓", False: "✗", None: "?"}[clause["ok"]]
        print(f"{mark} {clause['id']:38s} {clause['detail']}")
    print(
        f"\n{totals['files']} 份轨迹 · {totals['records']:,} 条记录 · {totals['spans']} 个 span · "
        f"字段核对 {totals['fields_checked']:,} 个（丢 {totals['fields_lost']}）· "
        f"trace {totals['trace_bytes']:,} B → payload {totals['payload_bytes']:,} B"
        f"（×{totals['growth_ratio']}）· 0 token"
    )
    print(f"证据：{RESULT.relative_to(ROOT)}")
    return 0 if document["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
