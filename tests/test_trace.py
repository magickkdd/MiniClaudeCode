"""trace 测试 —— 它是安全边界，不是"顺手记个日志"。

日志里最容易泄的两样东西：API key 和整份文件内容。两者都必须在写盘前
被处理掉，且写盘失败绝不能反过来拖垮 Agent。
"""

from __future__ import annotations

import json
from pathlib import Path

from miniclaude.infra.trace import MAX_STRING, Tracer, replay, summarize


def read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_disabled_tracer_is_inert() -> None:
    tracer = Tracer(None)
    assert tracer.enabled is False
    tracer.log("anything", secret="x")  # 不抛、不落盘
    assert tracer.path is None


def test_records_are_sequenced_and_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    tracer = Tracer(path, session_id="s1")
    tracer.log("turn_start", turn=1)
    tracer.log("tool_call", name="read_file", ok=True)

    records = read_records(path)
    assert [r["seq"] for r in records] == [1, 2]
    assert [r["kind"] for r in records] == ["turn_start", "tool_call"]
    assert {r["session"] for r in records} == {"s1"}
    assert all(isinstance(r["ts"], float) for r in records)


def test_sensitive_fields_are_masked(tmp_path: Path) -> None:
    path = tmp_path / "b.jsonl"
    tracer = Tracer(path)
    tracer.start_session(
        model="m",
        tools=["read_file"],
        config={"api_key": "sk-ABCDEFGHIJKLMNOP", "base_url": "https://x/v1", "token": "tok-1234567890"},
    )
    tracer.log("error", message="Authorization: Bearer sk-ABCDEFGHIJKLMNOP 被拒绝")

    record, second = read_records(path)
    assert record["config"]["api_key"] == "***"
    assert record["config"]["token"] == "***"
    assert record["config"]["base_url"] == "https://x/v1"  # 非敏感字段原样保留
    assert "sk-ABCDEFGHIJKLMNOP" not in path.read_text(encoding="utf-8")
    assert second["message"].startswith("Authorization: Bearer sk-A***")  # 值里嵌着的 key 也拦得住


def test_long_strings_are_clipped(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    Tracer(path).log("tool_call", args={"content": "x" * (MAX_STRING + 500)})
    stored = read_records(path)[0]["args"]["content"]
    assert stored.endswith("chars)")
    assert len(stored) < MAX_STRING + 20
    assert "2500" in stored


def test_nested_and_odd_values_survive_serialisation(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    tracer = Tracer(path)
    tracer.log(
        "run_end",
        usage={"prompt": 10, "nested": [{"deep": Path("a/b")}]},
        kinds={"a", "b"},
        nothing=None,
    )
    record = read_records(path)[0]
    assert record["usage"]["nested"][0]["deep"] in ("a\\b", "a/b")
    assert "a" in record["kinds"] and "b" in record["kinds"]  # 集合等非 JSON 类型退化成字符串，不抛
    assert record["nothing"] is None


def test_write_failure_disables_tracing_without_breaking_the_run(tmp_path: Path) -> None:
    path = tmp_path / "e.jsonl"
    tracer = Tracer(path)
    tracer.log("turn_start", turn=1)
    path.unlink()
    path.mkdir()  # 同名目录占位，之后的 open("a") 必然失败

    tracer.log("turn_start", turn=2)   # 不能抛
    assert tracer.enabled is False
    tracer.log("turn_start", turn=3)   # 已自我关闭，也不再尝试
    assert list(path.iterdir()) == []


def test_parent_that_cannot_be_created_disables_tracing(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("我是一个文件，不能当目录用", encoding="utf-8")
    tracer = Tracer(blocker / "trace.jsonl")
    assert tracer.enabled is False
    tracer.log("turn_start", turn=1)   # 静默跳过


def test_replay_skips_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / "f.jsonl"
    path.write_text(
        '{"seq": 1, "kind": "turn_start"}\n\n{"kind": "half-written\n{"seq": 2, "kind": "run_end"}\n',
        encoding="utf-8",
    )
    assert [r["kind"] for r in replay(path)] == ["turn_start", "run_end"]


def test_summarize_of_a_missing_session_is_harmless(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert summarize(path)["termination"] == "unknown"
    assert summarize(path)["turns"] == 0
