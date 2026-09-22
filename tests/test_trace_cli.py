"""`mcc trace` 的输出测试 —— 诊断工具的输出没人断言，就等于没人用过。

JD 里"Agent debugging 工具"这一项的验收方式不是"能跑"，而是"跑出来的东西
能不能读"。所以这里断言的是**具体行**：标题行的两个调用数、`--why-failed` 的
标签与处方、`--json` 的可解析性，以及三种"没配好/找不到"的退出码 2 路径。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from miniclaude.cli import trace_cmd
from miniclaude.config import Config
from miniclaude.infra.failure import classify, facts_from_records
from miniclaude.infra.trace import Tracer


@pytest.fixture
def trace_file(tmp_path: Path) -> Path:
    """两份会话：aaa 红着收尾（self_confirm），bbb 跑绿了收尾（干净）。"""
    path = tmp_path / "session.jsonl"
    _session(path, "aaa", verdict="red")
    _session(path, "bbb", verdict="green")
    return path


def _session(path: Path, session_id: str, *, verdict: str) -> None:
    body: list[tuple[str, dict[str, Any]]] = [
        ("turn_start", {"turn": 1, "est_tokens": 1200, "message_count": 1}),
        ("tool_call", {"turn": 1, "name": "edit_file", "ok": True, "risk": "write", "args": {"path": "calc.py"}}),
        ("turn_start", {"turn": 2, "est_tokens": 1500, "message_count": 3}),
        (
            "tool_call",
            {"turn": 2, "name": "run_tests", "ok": True, "risk": "execute", "args": {}, "verdict": verdict},
        ),
    ]
    end = {
        "termination": "completed",
        "turn": 2,
        "tool_calls": 2,
        "tool_errors": 0,
        "repeated_calls": 0,
        "stalled_groups": 0,
        "denied_actions": 0,
        "context_peak_tokens": 1500,
        "wall_ms": 940,
        "cost_est": None,
        "usage": {"total": 2700},
        "todos": [],
    }
    # 和 loop._finish 同一个顺序：先按整条轨迹（含 run_end）算标签，再把 run_end 写下去。
    probe = [{"kind": kind, "session": session_id, **payload} for kind, payload in body]
    probe.append({"kind": "run_end", "session": session_id, **end})
    end["failure_modes"] = [found.mode.value for found in classify(facts_from_records(probe))]

    tracer = Tracer(path, session_id=session_id)
    for kind, payload in body:
        tracer.log(kind, **payload)
    tracer.log("run_end", **end)


def config_for(path: Path | None) -> Config:
    return Config(
        base_url="https://mock.local/v1",
        api_key="sk-test-abcdefghijklmn",
        model="mock-model",
        project_root=path.parent if path else Path("."),
        trace_path=path,
    )


# --------------------------------------------------------------- 选择会话


def test_timeline_reports_both_call_counts(trace_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = trace_cmd.run(["aaa"], config_for(trace_file))
    out = capsys.readouterr().out

    assert code == 0
    assert "会话 aaa" in out
    assert "发起 2 次调用（执行 2 次" in out
    assert "第" not in out.splitlines()[0]  # 第一行是标题，不是轮次表


def test_latest_flag_picks_the_last_session_in_the_file(trace_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    trace_cmd.run(["--latest"], config_for(trace_file))
    out = capsys.readouterr().out

    assert "会话 bbb" in out
    assert "会话 aaa" not in out


def test_unknown_session_lists_what_exists(trace_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = trace_cmd.run(["nope"], config_for(trace_file))
    out = capsys.readouterr().out

    assert code == 2
    assert "找不到会话 'nope'" in out
    assert "aaa, bbb" in out


def test_missing_file_is_exit_two_not_a_traceback(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = trace_cmd.run([], config_for(tmp_path / "gone.jsonl"))

    assert code == 2
    assert "日志不存在" in capsys.readouterr().out


def test_unconfigured_trace_path_is_exit_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = trace_cmd.run([], config_for(None))
    out = capsys.readouterr().out

    assert code == 2
    assert "TRACE_PATH 未配置" in out
    assert "没有可读的日志" in out


# --------------------------------------------------------------- 三种视图


def test_why_failed_prints_label_evidence_and_prescription(trace_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    trace_cmd.run(["aaa", "--why-failed"], config_for(trace_file))
    out = capsys.readouterr().out

    assert "1. self_confirm" in out
    assert "第 2 轮 run_tests" in out
    assert "处方：" in out
    assert "退出码" in out  # 处方指向"判据只看退出码"


def test_why_failed_says_so_when_rules_find_nothing(trace_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """干净的轨迹必须留一句"规则看不出来"，不能静默 —— 静默会被读成"没问题"。"""
    trace_cmd.run(["bbb", "--why-failed"], config_for(trace_file))
    out = capsys.readouterr().out

    assert "没有命中任何失败模式规则" in out
    assert "不代表任务做对了" in out


def test_hot_shows_repeated_clusters_and_turn_costs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "hot.jsonl"
    tracer = Tracer(path, session_id="h")
    for turn in (1, 2, 3):
        tracer.log("turn_start", turn=turn, est_tokens=1000 * turn, message_count=turn)
        tracer.log("llm_response", turn=turn, usage={"prompt": 900, "completion": 100}, latency=1.2)
        tracer.log(
            "tool_call", turn=turn, name="read_file", ok=turn != 2, risk="read", args={"path": "a.py"}
        )
    tracer.log("run_end", termination="stalled", tool_calls=3, tool_errors=1, stalled_groups=2, denied_actions=0)

    trace_cmd.run(["--hot"], config_for(path))
    out = capsys.readouterr().out

    assert "read_file × 3" in out  # 同名同参连续簇
    assert "第 3 轮" in out  # 最贵一轮排在最前
    assert "read_file × 1" in out  # 报错最多的工具


def test_json_output_is_machine_readable_and_matches_the_report(trace_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    trace_cmd.run(["aaa", "--json"], config_for(trace_file))
    payload = json.loads(capsys.readouterr().out)

    assert payload["session"] == "aaa"
    assert payload["termination"] == "completed"
    assert payload["tool_calls"] == 2
    assert payload["tool_executed"] == 2
    assert payload["tokens"] == 0  # 合成轨迹没写 llm_response，报表就该是 0 而不是缺键
    assert payload["failure_modes"] == ["self_confirm"]


def test_limit_truncates_the_timeline(trace_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    trace_cmd.run(["aaa", "--limit", "1"], config_for(trace_file))
    out = capsys.readouterr().out

    assert "另有 1 轮未显示" in out


def test_old_schema_trace_says_which_rules_stayed_blind(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """v1 轨迹没有 `est_tokens` / `output_chars` / `verdict`。工具必须说"这些规则没参与"，
    不能沉默着给个空结果 —— 空结果看起来像"没问题"。"""
    path = tmp_path / "v1.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {"seq": 1, "ts": 1.0, "session": "old", "kind": "turn_start", "turn": 1},
                {"seq": 2, "ts": 1.1, "session": "old", "kind": "run_end", "termination": "completed"},
            )
        )
        + "\n",
        encoding="utf-8",
    )

    trace_cmd.run([], config_for(path))
    out = capsys.readouterr().out

    assert "schema 不是 2.0" in out
    assert "是没参与，不是判对了" in out


def test_stored_labels_that_the_current_rules_no_longer_give_are_flagged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """规则改过之后，旧 trace 里落盘的 `failure_modes` 不会自己重算。

    沉默地按新规则打印 = 让人以为这条轨迹在当前口径下也那样；沉默地照抄旧标签
    = 让人以为当前规则还会这么判。两种都不行，所以要显式说"口径变过"。
    """
    path = tmp_path / "stale.jsonl"
    _session(path, "aaa", verdict="red")
    lines = path.read_text(encoding="utf-8").splitlines()
    end = json.loads(lines[-1])
    end["failure_modes"] = []
    lines[-1] = json.dumps(end, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    trace_cmd.run(["aaa"], config_for(path))
    out = capsys.readouterr().out

    assert "规则口径变过" in out
    assert "当时落盘 （无标签）" in out
    assert "现在算出 self_confirm" in out
