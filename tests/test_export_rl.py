"""§3.9 轨迹导出器的用例：产地判定、reward 形状、以及"宁可不导也不导错"。

这一层的风险只有一个：**导出一份错位的 (state, action, reward) 比导不出来更坏** —— 它看
起来是好数据。所以这里的用例大多是反面的：配对差一个 id 就不给正文、判据是 error 就不进
数据、manifest 与 trace 轮数不一致就整条丢掉并记账。正面用例只验一件事：丢完之后留下来的
那些行仍然说得清自己从哪来。

夹具全部走真实的序列化函数（`message_to_dict` / `replay`），不自己拼 JSON 形状 —— 快照格式
改过一次，手写的那份会在格式变更时继续"通过"。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from miniclaude.backend.sessions import _safe, message_to_dict
from miniclaude.eval.export_rl import (
    audit,
    export,
    find_snapshot,
    pair_snapshot,
    preference_pairs,
    read_batches,
    steps_from,
)
from miniclaude.eval.runner import RunRecord, trace_stamp
from miniclaude.infra.trace import fingerprint
from miniclaude.messages import Message, Role, TextBlock, ToolResultBlock, ToolUseBlock

SECRET = "sk-abcdefghijklmnop"


# --------------------------------------------------------------- 夹具


def trace_records(session: str = "sess0001", turns: int = 2, calls_per_turn: int = 1) -> list[dict[str, Any]]:
    """一条 n 轮的合成轨迹：每轮一次请求、一次带 `tool_use` 的响应、等量的 `tool_call`。

    `message_count` 按真实循环的语义给：请求带走的条数，不含模型自己那条回复。所以第 t 轮
    的回复落在 `messages[message_count]`，这正是 `pair_snapshot` 要验的那件事。
    """
    records: list[dict[str, Any]] = [{"kind": "run_start", "session": session, "turn": 0, "user_input_chars": 42}]
    message_count = 1
    for turn in range(1, turns + 1):
        records.append({"kind": "turn_start", "session": session, "turn": turn})
        records.append(
            {
                "kind": "llm_request",
                "session": session,
                "turn": turn,
                "message_count": message_count,
                "est_tokens": 900 + turn,
                "tools_count": 8,
                "prefix_hash": f"ph{turn:04d}",
            }
        )
        records.append({"kind": "llm_response", "session": session, "turn": turn, "stop_reason": "tool_use", "blocks": ["TextBlock", "ToolUseBlock"]})
        for index in range(calls_per_turn):
            call_id = f"call_{turn}_{index}"
            records.append(
                {
                    "kind": "tool_call",
                    "session": session,
                    "turn": turn,
                    "name": "run_tests" if index == 0 else "read_file",
                    "tool_use_id": call_id,
                    "args": {"command": "pytest -q"} if index == 0 else {"path": "src/a.py"},
                    "risk": "execute" if index == 0 else "read",
                    "ok": True,
                    "output_chars": 120,
                    "latency": 0.05,
                    "verdict": "green" if index == 0 else None,
                }
            )
        # 这一轮的回复 = 1 条 assistant + 1 条装工具结果的 user
        message_count += 2
    records.append({"kind": "run_end", "session": session, "turn": turns, "termination": "completed", "failure_modes": []})
    return records


def snapshot_messages(turns: int = 2, calls_per_turn: int = 1) -> list[dict[str, Any]]:
    """与 `trace_records` 逐项对得上的现场：user → assistant(tool_use…) → user(tool_result…)。"""
    messages: list[dict[str, Any]] = [message_to_dict(Message.user_text("修好它"))]
    for turn in range(1, turns + 1):
        blocks: list[Any] = [TextBlock(text=f"第 {turn} 轮的想法")]
        results: list[Any] = []
        for index in range(calls_per_turn):
            call_id = f"call_{turn}_{index}"
            blocks.append(
                ToolUseBlock(
                    id=call_id,
                    name="run_tests" if index == 0 else "read_file",
                    input={"command": "pytest -q"} if index == 0 else {"path": "src/a.py"},
                )
            )
            results.append(ToolResultBlock(tool_use_id=call_id, content="结果：PASSED" if index == 0 else "def a(): ...", is_error=False))
        messages.append(message_to_dict(Message(Role.ASSISTANT, blocks)))
        messages.append(message_to_dict(Message(Role.USER, results)))
    return messages


def write_trace(root: Path, records: list[dict[str, Any]], name: str = "demo.r0.fake.jsonl") -> Path:
    path = root / "traces" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
    return path


def write_snapshot(root: Path, session: str, messages: list[dict[str, Any]], **extra: Any) -> Path:
    directory = root / "work" / ".mcc" / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_safe(session)}.json"
    payload = {"session_id": session, "messages": messages, "todos": [], "state": {}, **extra}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def run_record(path: Path, *, verdict: str = "pass", turns: int = 2, task_id: str = "demo", repeat: int = 0) -> RunRecord:
    return RunRecord(
        task_id=task_id,
        repeat=repeat,
        engine="fake",
        verdict=verdict,
        trace_path=path,
        metrics={"turns": turns, "tool_calls": turns, "tokens": 100 * turns},
    )


def read_rows(out: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]


def premise(stats: dict[str, Any], needle: str) -> dict[str, Any]:
    """按话找前提。按序号取会因为前面加一条而全线错位 —— 那种测试只会保护它自己写错。"""
    hits = [item for item in stats["premises"] if needle in item["claim"]]
    assert len(hits) == 1, f"「{needle}」对到了 {len(hits)} 条前提"
    return hits[0]


def export_one(root: Path, runs: list[RunRecord], **kwargs: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out = root / "rl.jsonl"
    ledger: list[dict[str, Any]] = []
    export(runs, out=out, memory_roots=[root], dropped=ledger, **kwargs)
    return read_rows(out), ledger


# --------------------------------------------------------------- 产地 B：只有指纹


def test_rows_without_snapshot_stay_fingerprints(tmp_path: Path) -> None:
    trace = write_trace(tmp_path, trace_records())
    rows, _ = export_one(tmp_path, [run_record(trace)])
    assert len(rows) == 2
    for row in rows:
        state = row["state"]
        assert state["source"] == "trace-fingerprint"
        assert state["reconstructible"] is False
        assert state["messages_delta"] == []
        assert state["pairing_reasons"], "产地 B 必须自述为什么没有正文"
        assert row["action"]["source"] == "trace-fingerprint"
        assert row["action"]["text"] == ""
        # 指纹照样是信息：这些字段全部来自 trace 本身
        assert state["prefix_hash"].startswith("ph")
        assert state["tools_offered"] == 8
        assert row["action"]["calls"][0]["name"] == "run_tests"


def test_fingerprint_rows_keep_what_trace_knows(tmp_path: Path) -> None:
    trace = write_trace(tmp_path, trace_records(turns=3))
    rows, _ = export_one(tmp_path, [run_record(trace, turns=3)])
    assert [row["state"]["message_count"] for row in rows] == [1, 3, 5]
    assert [row["reward"]["steps_to_end"] for row in rows] == [2, 1, 0]
    assert [row["action"]["tool_uses"] for row in rows] == [1, 1, 1]


# --------------------------------------------------------------- 产地 A：可验的配对


def test_paired_snapshot_unlocks_the_bodies(tmp_path: Path) -> None:
    session = "sess0001"
    trace = write_trace(tmp_path, trace_records(session=session))
    write_snapshot(tmp_path, session, snapshot_messages())
    rows, _ = export_one(tmp_path, [run_record(trace)])
    assert all(row["state"]["source"] == "snapshot-prefix" for row in rows)
    assert all(row["state"]["pairing_ok"] for row in rows)
    assert [row["action"]["text"] for row in rows] == ["第 1 轮的想法", "第 2 轮的想法"]
    assert rows[0]["state"]["messages_delta"][0]["blocks"][0]["body"] == "修好它"


def test_one_mismatched_tool_use_id_rejects_the_whole_snapshot(tmp_path: Path) -> None:
    """半对半错的前缀会被下游当成好的那半，所以判定是全有或全无。"""
    session = "sess0002"
    trace = write_trace(tmp_path, trace_records(session=session, turns=3))
    messages = snapshot_messages(turns=3)
    next(block for block in messages[3]["content"] if block["type"] == "tool_use")["id"] = "call_TYPO"
    write_snapshot(tmp_path, session, messages)
    rows, _ = export_one(tmp_path, [run_record(trace, turns=3)])
    assert all(row["state"]["source"] == "trace-fingerprint" for row in rows)
    assert "配不上" in rows[0]["state"]["pairing_reasons"][0]


def test_pairing_needs_the_id_sequence_not_just_a_count() -> None:
    steps = steps_from(trace_records(turns=1, calls_per_turn=2))
    good = {"messages": snapshot_messages(turns=1, calls_per_turn=2)}
    assert pair_snapshot(good, steps) == []
    swapped = {"messages": snapshot_messages(turns=1, calls_per_turn=2)}
    swapped["messages"][1]["content"] = list(reversed(swapped["messages"][1]["content"]))
    assert pair_snapshot(swapped, steps), "同一轮里换个顺序也是错位 —— 并行调用的返回值会跟错工具"


def test_pairing_reports_a_schema_1_trace(tmp_path: Path) -> None:
    records = [item for item in trace_records(turns=1) if item["kind"] != "llm_request"]
    steps = steps_from(records)
    reasons = pair_snapshot({"messages": snapshot_messages(turns=1)}, steps)
    assert reasons and "llm_request" in reasons[0]


def test_find_snapshot_verifies_the_id_inside_the_file(tmp_path: Path) -> None:
    trace = write_trace(tmp_path, trace_records(session="sess0003"))
    write_snapshot(tmp_path, "someone-else", snapshot_messages())
    assert find_snapshot("sess0003", trace, [tmp_path]) is None
    write_snapshot(tmp_path, "sess0003", snapshot_messages())
    found = find_snapshot("sess0003", trace, [tmp_path])
    assert found is not None and found.name == f"{_safe('sess0003')}.json"


# --------------------------------------------------------------- 增量导出不重不漏


def test_deltas_reassemble_the_prefix_without_the_replies(tmp_path: Path) -> None:
    session = "sess0004"
    trace = write_trace(tmp_path, trace_records(session=session, turns=3))
    messages = snapshot_messages(turns=3)
    write_snapshot(tmp_path, session, messages)
    rows, _ = export_one(tmp_path, [run_record(trace, turns=3)])
    # 末步动作之后那条工具结果不属于任何 state —— 它是"如果还有下一步，模型会看见的东西"。
    wanted = [message for index, message in enumerate(messages[:5]) if index % 2 == 0]
    bodies = [block["body"] for row in rows for delta in row["state"]["messages_delta"] for block in delta["blocks"]]
    flat = [block.get("text") or block.get("content") for message in wanted for block in message["content"]]
    assert bodies == [str(item) for item in flat]
    assert [len(row["state"]["messages_delta"]) for row in rows] == [1, 1, 1]


def test_audit_catches_a_dropped_delta_message(tmp_path: Path) -> None:
    session = "sess0005"
    trace = write_trace(tmp_path, trace_records(session=session, turns=3))
    write_snapshot(tmp_path, session, snapshot_messages(turns=3))
    out = tmp_path / "rl.jsonl"
    export([run_record(trace, turns=3)], out=out, memory_roots=[tmp_path])
    rows = read_rows(out)
    rows[1]["state"]["messages_delta"] = []
    out.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    broken = audit(out, 2000)
    failed = [item["claim"] for item in broken["premises"] if item["ok"] is False]
    assert any("不重不漏" in claim for claim in failed)


# --------------------------------------------------------------- reward 的形状


@pytest.mark.parametrize("verdict", ["error", "aborted"])
def test_our_own_failures_never_enter_the_data(tmp_path: Path, verdict: str) -> None:
    trace = write_trace(tmp_path, trace_records())
    rows, ledger = export_one(tmp_path, [run_record(trace, verdict=verdict)])
    assert rows == []
    assert [(item["code"], item["verdict"]) for item in ledger] == [("verdict-not-terminal", verdict)]
    assert ledger[0]["key"] == str(trace)


def test_failed_runs_stay_in_and_score_zero(tmp_path: Path) -> None:
    trace = write_trace(tmp_path, trace_records(turns=2))
    rows, _ = export_one(tmp_path, [run_record(trace, verdict="fail")])
    assert [row["reward"]["value"] for row in rows] == [0.0, 0.0]
    kept, _ = export_one(tmp_path, [run_record(trace, verdict="fail")], include_failed=False)
    assert kept == []


def test_gamma_only_shapes_distance_to_the_finish(tmp_path: Path) -> None:
    trace = write_trace(tmp_path, trace_records(turns=3))
    rows, _ = export_one(tmp_path, [run_record(trace, turns=3)], gamma=0.5)
    assert [row["reward"]["value"] for row in rows] == [0.25, 0.5, 1.0]
    flat, _ = export_one(tmp_path, [run_record(trace, turns=3)], gamma=1.0)
    assert [row["reward"]["value"] for row in flat] == [1.0, 1.0, 1.0]


def test_terminal_verdict_travels_with_every_row(tmp_path: Path) -> None:
    trace = write_trace(tmp_path, trace_records())
    rows, _ = export_one(tmp_path, [run_record(trace)])
    assert {row["reward"]["verdict"] for row in rows} == {"pass"}
    assert rows[0]["reward"]["termination"] == "completed"
    assert rows[0]["provenance"]["verdict_source"] == "eval manifest → RunRecord.verdict"


# --------------------------------------------------------------- 压缩阶梯破产地 A


def test_compaction_is_marked_on_the_rows_it_touched(tmp_path: Path) -> None:
    session = "sess0006"
    records = trace_records(session=session, turns=3)
    records.append({"kind": "context_compact", "session": session, "turn": 2, "level": "elide", "pairing_ok": True})
    trace = write_trace(tmp_path, records)
    write_snapshot(tmp_path, session, snapshot_messages(turns=3))
    rows, _ = export_one(tmp_path, [run_record(trace, turns=3)])
    assert [bool(row["state"]["broken_by_compaction"]) for row in rows] == [False, True, True]
    assert "压缩阶梯" in rows[1]["state"]["broken_by_compaction"]
    assert [row["signals"]["compaction_at"] for row in rows] == [None, 2, None]


def test_refusing_to_compact_is_a_signal_too(tmp_path: Path) -> None:
    session = "sess0007"
    records = trace_records(session=session, turns=2)
    records.append({"kind": "context_refuse", "session": session, "turn": 2, "line": "l3_refuse"})
    trace = write_trace(tmp_path, records)
    rows, _ = export_one(tmp_path, [run_record(trace, turns=2)])
    assert [row["signals"]["context_refused"] for row in rows] == [False, True]


def test_a_refusal_turn_that_never_sent_a_request_is_not_a_step(tmp_path: Path) -> None:
    """死在 L3 线上的那个轮号不是决策步：模型既没看见那份上下文，也没给出动作。

    循环先 `turn += 1` 再算预算，所以 `context_refuse` 带的是**下一轮**的号。这一度被
    算成一个空的决策步，于是"预算见底而死"的 run 比 manifest 多出一格，整条轨迹被
    `turn-mismatch` 丢弃 —— 而那恰恰是压缩阶梯唯一真正破产过的样本（B2 live 臂 5 次
    连同 off / tight 两臂，一共 7 条 run、那次导出的全部丢弃）。死因不靠那一格承载：
    每行的 `termination` 是从 `run_end` 读的，`context_overflow` 还在。
    """
    session = "sess0007b"
    records = trace_records(session=session, turns=2)
    records[-1] = {**records[-1], "termination": "context_overflow"}  # 真端点上这批 run 的死法
    records.append({"kind": "context_refuse", "session": session, "turn": 3, "line": "l3_refuse"})
    steps = steps_from(records)
    assert [step.turn for step in steps] == [1, 2], "多出来的那一格里没有 (state, action)"
    trace = write_trace(tmp_path, records)
    rows, ledger = export_one(tmp_path, [run_record(trace, turns=2)])
    assert ledger == [], f"这一类 run 不该再被丢弃：{ledger}"
    assert [row["reward"]["termination"] for row in rows] == ["context_overflow"] * 2


# --------------------------------------------------------------- 密钥与截断


def test_secrets_are_masked_in_bodies_and_args(tmp_path: Path) -> None:
    session = "sess0008"
    records = trace_records(session=session, turns=1)
    for record in records:
        if record["kind"] == "tool_call":
            record["args"] = {"command": f"pytest -k {SECRET}"}
    trace = write_trace(tmp_path, records)
    messages = snapshot_messages(turns=1)
    messages[2]["content"][0]["content"] = f"失败输出里带 {SECRET}"
    write_snapshot(tmp_path, session, messages)
    out = tmp_path / "rl.jsonl"
    export([run_record(trace, turns=1)], out=out, memory_roots=[tmp_path])
    blob = out.read_text(encoding="utf-8")
    assert SECRET not in blob
    assert "sk-a***" in blob
    assert premise(audit(out, 2000), "形如密钥")["ok"] is True


def test_the_secret_premise_is_not_vacuous(tmp_path: Path) -> None:
    """遮不住的那一条，前提必须变红 —— 否则这条前提只是在自我表扬。"""
    trace = write_trace(tmp_path, trace_records(turns=1))
    out = tmp_path / "rl.jsonl"
    export([run_record(trace, turns=1)], out=out)
    rows = read_rows(out)
    rows[0]["action"]["calls"][0]["args_json"] = f'{{"token": "{SECRET}"}}'
    out.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    assert premise(audit(out, 2000), "形如密钥")["ok"] is False


def test_oversized_blocks_are_flagged_not_silently_cut(tmp_path: Path) -> None:
    session = "sess0009"
    trace = write_trace(tmp_path, trace_records(session=session))
    messages = snapshot_messages()
    messages[2]["content"][0]["content"] = "长" * 500
    write_snapshot(tmp_path, session, messages)
    rows, _ = export_one(tmp_path, [run_record(trace)], block_chars=64)
    result = next(
        block
        for row in rows
        for delta in row["state"]["messages_delta"]
        for block in delta["blocks"]
        if block["type"] == "tool_result"
    )
    assert result["truncated"] is True
    assert result["chars"] == 500
    assert len(result["body"]) == 64
    assert premise(audit(tmp_path / "rl.jsonl", 64), "截断标记")["ok"] is True


# --------------------------------------------------------------- 进不了数据的一律记账


def test_same_trace_cannot_be_claimed_twice(tmp_path: Path) -> None:
    trace = write_trace(tmp_path, trace_records())
    rows, ledger = export_one(tmp_path, [run_record(trace), run_record(trace, verdict="fail")])
    assert len(rows) == 2, "只有第一次那份进了数据"
    assert rows[0]["reward"]["verdict"] == "pass"
    assert [item["code"] for item in ledger] == ["duplicate-trace"]


def test_turn_count_mismatch_means_the_verdict_is_not_ours(tmp_path: Path) -> None:
    trace = write_trace(tmp_path, trace_records(turns=2))
    rows, ledger = export_one(tmp_path, [run_record(trace, turns=5)])
    assert rows == []
    assert ledger[0]["code"] == "turn-mismatch"
    assert ledger[0]["manifest_turns"] == 5 and ledger[0]["trace_steps"] == 2


def test_ledger_counts_add_up_with_inputs(tmp_path: Path) -> None:
    good = write_trace(tmp_path, trace_records(), name="a.r0.fake.jsonl")
    bad = write_trace(tmp_path, trace_records(), name="b.r0.fake.jsonl")
    rows, ledger = export_one(tmp_path, [run_record(good, task_id="a"), run_record(bad, task_id="b", verdict="error")])
    stats = audit(tmp_path / "rl.jsonl", 2000)
    assert stats["rows"] == len(rows) == 2
    assert 2 == stats["runs_paired"] + stats["runs_fingerprint"] + len(ledger)
    assert {row["task_id"] for row in rows} == {"a"}


# --------------------------------------------------------------- 判据与轨迹的同一性（§7.3-7）


def stamped(trace: Path, root: Path, **kwargs: Any) -> RunRecord:
    """一条**带内容指纹**的判据行 —— §7.3-7 之后新批次就是这个形状。"""
    record = run_record(trace, **kwargs)
    record.trace_fp = trace_stamp(trace, root)
    assert record.trace_fp, "指纹没盖上，下面的用例就全在验一个空对象"
    return record


def test_a_stamped_run_is_joined_and_says_so(tmp_path: Path) -> None:
    trace = write_trace(tmp_path, trace_records())
    rows, ledger = export_one(tmp_path, [stamped(trace, tmp_path)])
    assert ledger == []
    assert {row["provenance"]["trace_identity"]["status"] for row in rows} == {"verified"}
    first = rows[0]["provenance"]["trace_identity"]
    assert first["sha"] == fingerprint(trace)["sha"]
    assert first["rel"] == "traces/demo.r0.fake.jsonl", "相对路径要能脱离这台机器拼回去"


def test_a_mutated_trace_is_dropped_not_joined(tmp_path: Path) -> None:
    """轮数校验挡得住"少了几轮"，挡不住"同样轮数的另一份轨迹"。"""
    trace = write_trace(tmp_path, trace_records())
    record = stamped(trace, tmp_path)
    before = fingerprint(trace)
    trace.write_text(trace.read_text(encoding="utf-8") + json.dumps({"kind": "error", "session": "sess0001", "turn": 9}) + "\n", encoding="utf-8")
    rows, ledger = export_one(tmp_path, [record])
    assert rows == [], "指纹已经对不上了，这一行的 reward 属于另一条轨迹"
    assert ledger[0]["code"] == "trace-drift"
    assert ledger[0]["recorded_lines"] == before["lines"]
    assert ledger[0]["disk_lines"] == before["lines"] + 1
    assert ledger[0]["recorded_sha"] != ledger[0]["disk_sha"]


def test_an_edited_middle_line_is_drift_too(tmp_path: Path) -> None:
    """SPEC 原文要的是"行数 + 末行哈希"，这里换成全文件哈希就是为了这一条。

    改中间一行（把某轮的 `prefix_hash` 或 `est_tokens` 涂掉）行数不变、末行不变，
    末行哈希那份凭据会照样点头 —— 而它要挡的"手工改动"恰恰长这样。
    """
    trace = write_trace(tmp_path, trace_records())
    record = stamped(trace, tmp_path)
    before = fingerprint(trace)
    lines = trace.read_text(encoding="utf-8").splitlines()
    lines[2] = json.dumps({**json.loads(lines[2]), "est_tokens": 1})
    trace.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _, ledger = export_one(tmp_path, [record])
    assert [item["code"] for item in ledger] == ["trace-drift"]
    entry = ledger[0]
    assert entry["recorded_lines"] == entry["disk_lines"] == before["lines"], "行数一样 —— 只有内容哈希抓得到"
    assert entry["recorded_sha"] != entry["disk_sha"]


def test_an_unstamped_row_still_exports_but_cannot_claim_verification(tmp_path: Path) -> None:
    """盘上那 12 个历史批次没有指纹。它们该照旧进数据，但不许被读成"校验过"。"""
    trace = write_trace(tmp_path, trace_records())
    rows, ledger = export_one(tmp_path, [run_record(trace)])
    assert ledger == [] and len(rows) == 2
    identities = [row["provenance"]["trace_identity"] for row in rows]
    assert {item["status"] for item in identities} == {"unverified"}
    assert all(item["why"] for item in identities)
    stats = audit(tmp_path / "rl.jsonl", 2000)
    assert premise(stats, "盘上那份 trace 现在仍是判据当时看的那份")["ok"] is None, "没量过就该记未量"


def test_the_read_back_premise_goes_red_when_the_trace_changes_after_export(tmp_path: Path) -> None:
    """自证前提不能是摆设：导出之后有人改轨迹，这条必须响。

    这条测试是那条前提的**唯一**外部约束 —— 规则命中自己的导出结果就是循环论证，
    所以这里刻意在 export 完成后动手改盘上的文件，再重跑一次审计。
    """
    trace = write_trace(tmp_path, trace_records())
    out = tmp_path / "rl.jsonl"
    export([stamped(trace, tmp_path)], out=out, memory_roots=[tmp_path])
    stats = audit(out, 2000)
    item = premise(stats, "盘上那份 trace 现在仍是判据当时看的那份")
    assert item["ok"] is True, item["detail"]
    assert stats["rows_verified"] == 2 and stats["rows_unverified"] == 0

    before = trace.read_text(encoding="utf-8")
    trace.write_text(before.replace('"est_tokens": 901', '"est_tokens": 1'), encoding="utf-8")
    assert trace.read_text(encoding="utf-8") != before, "夹具没真的改动文件，这条测试就只是在验一个绿"
    again = audit(out, 2000)
    red = premise(again, "盘上那份 trace 现在仍是判据当时看的那份")
    assert red["ok"] is False
    assert "不符 1" in red["detail"], f"细节要说清是哪份、差在哪，实际：{red['detail']}"


def test_a_relocated_batch_is_still_joinable_by_its_relative_path(tmp_path: Path) -> None:
    """缺口 ②：绝对路径是这台机器的坐标。批次目录拷走之后不该整批 `trace-missing`。"""
    batch = tmp_path / "batch"
    trace = write_trace(batch, trace_records())
    manifest = batch / "manifest.jsonl"
    raw = stamped(trace, batch).to_dict()
    raw["trace_path"] = str(tmp_path / "已经不在这里了" / trace.name)
    manifest.write_text(json.dumps(raw, ensure_ascii=False) + "\n", encoding="utf-8")

    moved = tmp_path / "moved"
    shutil.copytree(batch / "traces", moved / "traces")
    (moved / "manifest.jsonl").write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")

    runs = read_batches([moved])
    assert runs[0].trace_path == moved / "traces" / "demo.r0.fake.jsonl", "该按批次内的相对路径找回来"
    out = moved / "rl.jsonl"
    ledger: list[dict[str, Any]] = []
    export(runs, out=out, memory_roots=[moved], dropped=ledger)
    assert ledger == [], f"换了坐标不该变成「数据没了」：{[item['code'] for item in ledger]}"
    assert {row["provenance"]["trace_identity"]["status"] for row in read_rows(out)} == {"verified"}


# --------------------------------------------------------------- 规则标签与人工标注


def test_rule_labels_are_read_from_the_trace_not_recomputed(tmp_path: Path) -> None:
    """重跑分类器会拿到"今天规则下的结论"。这条数据是当时那份，标签得跟着当时走。"""
    session = "sess0010"
    records = trace_records(session=session, turns=3)
    records.append({"kind": "failure_mode", "session": session, "turn": 2, "mode": "self_confirm"})
    trace = write_trace(tmp_path, records)
    rows, _ = export_one(tmp_path, [run_record(trace, turns=3)])
    assert rows[1]["signals"]["mode_labels"] == ["self_confirm"]
    assert rows[0]["signals"]["mode_labels"] == []
    assert rows[0]["labels"]["rule_modes"] == ["self_confirm"]


def test_human_labels_join_by_trace_file_name(tmp_path: Path) -> None:
    trace = write_trace(tmp_path, trace_records())
    rows, _ = export_one(tmp_path, [run_record(trace)], labels={"demo.r0.fake.jsonl": ["path_guessing"]})
    assert rows[0]["labels"]["human_modes"] == ["path_guessing"]


# --------------------------------------------------------------- 成对偏好


def test_pairs_need_same_task_and_repeat_and_opposite_verdicts(tmp_path: Path) -> None:
    passed = write_trace(tmp_path, trace_records(), name="t1.r0.fake.jsonl")
    failed = write_trace(tmp_path, trace_records(), name="t1.r1.fake.jsonl")
    again = write_trace(tmp_path, trace_records(), name="t1.r0.live.jsonl")
    missing = write_trace(tmp_path, trace_records(), name="t2.r0.fake.jsonl")
    pairs = preference_pairs(
        [
            run_record(passed, task_id="t1", repeat=0),
            run_record(failed, task_id="t1", repeat=1),
            run_record(again, task_id="t1", repeat=0, verdict="fail"),
            run_record(missing, task_id="t2", repeat=0, verdict="error"),
        ]
    )
    assert [(item["task_id"], item["repeat"]) for item in pairs] == [("t1", 0)]
    assert pairs[0]["kind"] == "same-task-different-run"
    assert pairs[0]["chosen"] == "t1.r0.fake" and pairs[0]["rejected"] == "t1.r0.live"


def test_a_duplicated_trace_cannot_pair_with_itself(tmp_path: Path) -> None:
    trace = write_trace(tmp_path, trace_records(), name="t1.r0.fake.jsonl")
    assert preference_pairs(
        [run_record(trace, task_id="t1", verdict="pass"), run_record(trace, task_id="t1", verdict="fail")]
    ) == []


# --------------------------------------------------------------- 自证前提本身是有效的


def test_audit_reports_every_premise_green_on_a_clean_export(tmp_path: Path) -> None:
    session = "sess0011"
    trace = write_trace(tmp_path, trace_records(session=session, turns=3))
    write_snapshot(tmp_path, session, snapshot_messages(turns=3))
    out = tmp_path / "rl.jsonl"
    export([stamped(trace, tmp_path, turns=3)], out=out, memory_roots=[tmp_path])
    stats = audit(out, 2000)
    assert [item["ok"] for item in stats["premises"]] == [True] * len(stats["premises"])
    assert stats["runs_paired"] == 1 and stats["rows"] == 3


def test_audit_rejects_a_row_without_its_provenance(tmp_path: Path) -> None:
    trace = write_trace(tmp_path, trace_records())
    out = tmp_path / "rl.jsonl"
    export([run_record(trace)], out=out)
    rows = read_rows(out)
    rows[0]["provenance"]["trace"] = None
    rows.append(dict(rows[0]))
    out.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    stats = audit(out, 2000)
    assert any(item["ok"] is False for item in stats["premises"]), "重复行 / 丢了产地，总得响一条"
