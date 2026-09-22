"""`mcc trace` 的输出测试 —— 诊断工具的输出没人断言，就等于没人用过。

JD 里"Agent debugging 工具"这一项的验收方式不是"能跑"，而是"跑出来的东西
能不能读"。所以这里断言的是**具体行**：标题行的两个调用数、`--why-failed` 的
标签与处方、`--json` 的可解析性，以及三种"没配好/找不到"的退出码 2 路径。

`--otel` 那一段断言的是**结构**而不是文本：树有没有翻歪、event 有没有替代丢弃、
以及"校验红就一个字节都不出去"这条承诺 —— 后者要能观察到 `--out` 文件不存在，
所以不能只看返回值。最后一条测试起子进程量 `python -m miniclaude` 的退出码：
那是解释器给的东西，在测试进程里调 `main()` 量不到。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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


def _payload_spans(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [span for rs in payload["resourceSpans"] for scope in rs["scopeSpans"] for span in scope["spans"]]


def _attrs(span: dict[str, Any]) -> dict[str, Any]:
    """KeyValue 数组 → 普通 dict。OTLP 的每个 value 都是"一个键的 union"，取那个键即可。"""
    return {item["key"]: next(iter(item["value"].values())) for item in span.get("attributes", [])}


def _otel_trace(path: Path, session_id: str) -> dict[str, str]:
    """一条形状正确的 v2 轨迹：session → run → turn → tool，外加一条没有 span_id 的记录。

    id 写死而不是 `new_span_id()`：测试要看的就是"父子关系有没有被翻译层保留"，
    随机的 id 只能断言"存在某个父亲"，断言不了"就是这个父亲"。
    """
    ids = {"session": "11" * 8, "run": "22" * 8, "turn": "33" * 8, "tool": "44" * 8}
    tracer = Tracer(path, session_id=session_id, trace_id="aa" * 16)
    tracer.log(
        "session_start",
        span_id=ids["session"],
        model="mock-model",
        tools=["read_file", "run_tests"],
        config={"token_budget": 8000},
        system_prompt_hash="9f3a",
    )
    tracer.log(
        "run_start",
        span_id=ids["run"],
        run_id="r1",
        task_id=None,
        resumed_from=None,
        user_input_chars=11,
    )
    tracer.log(
        "turn_start",
        span_id=ids["turn"],
        parent_span_id=ids["run"],
        turn=1,
        est_tokens=1200,
        message_count=1,
    )
    tracer.log(
        "llm_response",
        span_id=ids["turn"],
        parent_span_id=ids["run"],
        turn=1,
        usage={"prompt": 900, "completion": 100, "total": 1000},
        latency=1.5,
        blocks=["tool_use"],
        stop_reason="tool_use",
        id_repairs=0,
    )
    tracer.log(
        "permission",
        span_id=ids["tool"],
        parent_span_id=ids["turn"],
        turn=1,
        tool="read_file",
        decision="allow",
        rule_hit=None,
    )
    tracer.log(
        "tool_call",
        span_id=ids["tool"],
        parent_span_id=ids["turn"],
        turn=1,
        name="read_file",
        ok=False,
        risk="read",
        args={"path": "a.py"},
        tool_use_id="tu_1",
        output_chars=40,
        latency=0.2,
        verdict="red",
        drops_assert=False,
    )
    # failure_mode 没有 span_id —— 它只能是 event，但绝不能因此消失。
    tracer.log("failure_mode", turn=1, mode="self_confirm", why="绿了但没断言", prescription="判据加断言")
    tracer.log(
        "run_end",
        span_id=ids["run"],
        termination="completed",
        status="ok",
        turn=1,
        tool_calls=1,
        tool_errors=1,
        repeated_calls=0,
        stalled_groups=0,
        denied_actions=0,
        context_peak_tokens=1200,
        context_compactions=0,
        context_elided_blocks=0,
        context_summary_tokens=0,
        wall_ms=900,
        cost_est=None,
        usage={"prompt": 900, "completion": 100, "total": 1000},
        todos=[],
        failure_modes=["self_confirm"],
    )
    return ids


# ------------------------------------------------------------------ OTLP 导出
#
# `--otel` 的输出不是给人读的，是给收集端读的 —— 所以这里断言的是结构：树有没有翻歪、
# 收不到字节的那条路（校验红）是不是真的一个字节都没出去。


def test_otel_keeps_the_tree_the_trace_described(tmp_path: Path) -> None:
    path = tmp_path / "otel.jsonl"
    out = tmp_path / "otel.json"
    ids = _otel_trace(path, "x")

    code = trace_cmd.run(["x", "--otel", "--out", str(out)], config_for(path))
    payload = json.loads(out.read_text(encoding="utf-8"))
    spans = {span["name"]: span for span in _payload_spans(payload)}

    assert code == 0
    assert set(spans) == {"agent.session", "invoke_agent mock-model", "chat mock-model", "execute_tool read_file"}
    assert spans["chat mock-model"]["parentSpanId"] == ids["run"]
    assert spans["execute_tool read_file"]["parentSpanId"] == ids["turn"]
    assert spans["agent.session"]["traceId"] == "aa" * 16
    # permission + tool_call 合并成一个 span，而不是两个 —— trace 里它们本来就同一个 span_id。
    assert _attrs(spans["execute_tool read_file"])["mcc.span.record_kinds"] == "permission+tool_call"


def test_otel_marks_the_failing_tool_span_red(tmp_path: Path) -> None:
    path = tmp_path / "otel.jsonl"
    _otel_trace(path, "x")
    trace_cmd.run(["x", "--otel", "--out", str(tmp_path / "o.json")], config_for(path))

    payload = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))
    spans = {span["name"]: span for span in _payload_spans(payload)}
    codes = {name: span["status"]["code"] for name, span in spans.items()}

    assert codes["execute_tool read_file"] == 2  # ERROR
    assert codes["chat mock-model"] == 1  # 这一轮自己没报错，红的是它下面的工具


def test_otel_sends_records_without_a_span_id_as_events_not_into_the_bin(
    tmp_path: Path,
) -> None:
    path = tmp_path / "otel.jsonl"
    _otel_trace(path, "x")
    trace_cmd.run(["x", "--otel", "--out", str(tmp_path / "o.json")], config_for(path))

    payload = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))
    spans = {span["name"]: span for span in _payload_spans(payload)}
    events = spans["chat mock-model"].get("events", [])

    assert [event["name"] for event in events] == ["mcc.failure_mode"]
    assert _attrs(events[0])["mcc.failure.mode"] == "self_confirm"


def test_otel_keeps_stdout_parseable_and_puts_the_report_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """stdout 是给人 `| jq` 的，stderr 是给人看的。混在一起两边都坏。"""
    path = tmp_path / "otel.jsonl"
    _otel_trace(path, "x")

    trace_cmd.run(["x", "--otel"], config_for(path))
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert len(_payload_spans(payload)) == 4
    assert "OTLP/JSON：" in captured.err
    assert "1 个红 span" in captured.err
    assert "4 个 null 字段在协议里没有位置" in captured.err  # task_id / resumed_from / rule_hit / cost_est


def test_otel_out_is_platform_independent_bytes(tmp_path: Path) -> None:
    """导出物的 sha 必须跨平台一致 —— Windows 的文本模式会在结尾塞一个 `\\r`。

    这条不是在挑格式：同一份轨迹在两台机器上导出得到两个 sha，§7.3-7 那道
    "指纹对上才算同一份"的闸门就没有意义了。
    """
    path = tmp_path / "otel.jsonl"
    _otel_trace(path, "x")
    out = tmp_path / "o.json"

    trace_cmd.run(["x", "--otel", "--out", str(out)], config_for(path))
    written = out.read_bytes()

    assert b"\r" not in written
    assert written.endswith(b"}\n")
    assert json.loads(written.decode("utf-8"))["resourceSpans"]


def test_otel_validates_before_emitting_a_single_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "otel.jsonl"
    out = tmp_path / "o.json"
    _otel_trace(path, "x")

    monkeypatch.setattr(trace_cmd, "validate", lambda payload: ["spanId 不是 16 位小写十六进制：zz"])
    code = trace_cmd.run(["x", "--otel", "--out", str(out)], config_for(path))
    err = capsys.readouterr().err  # 只读一次：readouterr() 会把缓冲区抽干

    assert code == 1
    assert not out.exists()
    assert "校验未过" in err
    assert "没有导出" in err


def test_otel_declares_the_gaps_it_cannot_bridge(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "otel.jsonl"
    _otel_trace(path, "x")

    trace_cmd.run(["x", "--otel", "--out", str(tmp_path / "o.json")], config_for(path))
    err = capsys.readouterr().err

    assert "翻不过去：" in err
    assert "span links" in err  # 7 条之一：跨 trace 的因果在 v1 轨迹里根本没有


def test_otel_sends_and_reports_a_failure_as_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "otel.jsonl"
    _otel_trace(path, "x")
    sent: list[str] = []

    def fake_post(payload, endpoint, *, timeout=10.0):  # noqa: ANN001, ANN202
        sent.append(endpoint)
        return {"ok": False, "error": "HTTP 502，响应体是空的", "bytes": 0, "status_code": 502}

    monkeypatch.setattr(trace_cmd, "post_otlp", fake_post)
    code = trace_cmd.run(
        ["x", "--otel", "--endpoint", "http://localhost:4318/v1/traces"], config_for(path)
    )

    assert code == 1
    assert sent == ["http://localhost:4318/v1/traces"]
    err = capsys.readouterr().err
    assert "发送失败：HTTP 502" in err
    assert "泄露面" not in err  # 本机端点不该被警告


def test_otel_warns_before_the_payload_leaves_the_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "otel.jsonl"
    _otel_trace(path, "x")
    monkeypatch.setattr(
        trace_cmd, "post_otlp", lambda payload, endpoint, **kw: {"ok": True, "bytes": 12, "status_code": 200}
    )

    code = trace_cmd.run(["x", "--otel", "--endpoint", "https://otel.example.com/v1/traces"], config_for(path))
    err = capsys.readouterr().err

    assert code == 0
    assert "endpoint 不在本机" in err
    assert "泄露面" in err
    assert "翻不过去：" not in err  # 发了就别再念一遍限制，那是没发时的读法


def test_otel_out_into_a_missing_directory_is_exit_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "otel.jsonl"
    _otel_trace(path, "x")

    code = trace_cmd.run(["x", "--otel", "--out", str(tmp_path / "nope" / "o.json")], config_for(path))

    assert code == 2
    assert "写不出去" in capsys.readouterr().err


def test_python_m_entry_propagates_the_exit_code(tmp_path: Path) -> None:
    """README §4 说 `mcc` / `python -m miniclaude` / console script 三个入口等价。

    "等价"里最容易掉的是退出码：`__main__.py` 少了 `sys.exit()` 时 `-m` 永远退 0，
    CI 里就等于"失败被读成成功"。这里直接起子进程量它，因为退出码是解释器给的，
    在测试进程里调 `main()` 量不到。
    """
    path = tmp_path / "otel.jsonl"
    _otel_trace(path, "x")
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "LLM_BASE_URL": "https://mock.local/v1",
        "LLM_MODEL": "mock-model",
        "LLM_API_KEY": "sk-test-abcdefghijklmn",
        "TRACE_PATH": str(path),
    }

    bad = subprocess.run(
        [sys.executable, "-m", "miniclaude", "trace", "x", "--otel", "--out", str(tmp_path / "no" / "o.json")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    good = subprocess.run(
        [sys.executable, "-m", "miniclaude", "trace", "x", "--otel", "--out", str(tmp_path / "o.json")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert bad.returncode == 2, bad.stderr
    assert good.returncode == 0, good.stderr
    assert json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))["resourceSpans"]


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
