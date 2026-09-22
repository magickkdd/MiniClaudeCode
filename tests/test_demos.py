"""demo 也是回归资产 —— 能在我机器上跑通一次不算数，得每次都能跑通。

这里跑的全是 `--engine fake`：真端点那一份走 `python demos/run_demo.py
--all --engine live`，因为它不确定、要花钱，放进 pytest 会让"测试全绿"
变成一件看运气的事。
"""

from __future__ import annotations

import re
import sys

import pytest

import run_demo as rd

FAKE_DEMOS = [demo for demo in rd.DEMOS if demo.script is not None]
_SELF_REPORTED_COUNT = re.compile(r"\d+\s*(?:passed|failed|个用例|个测试)")


@pytest.mark.parametrize("demo", FAKE_DEMOS, ids=lambda demo: demo.id)
def test_demo_passes_without_a_network(demo, tmp_path):
    """每条判定都必须自己成立：pytest 退出码、行为探测、tests/ 未被改写。"""
    run = rd.run_demo(demo, engine="fake", work_root=tmp_path / ".work")
    failed = [check.line() for check in run.outcome.checks if not check.ok]
    assert run.outcome.ok, f"{demo.id} 判定未通过：\n" + "\n".join(failed)


def test_demo_actually_uses_the_tools_it_claims(tmp_path):
    """判定通过还不够，工具序列也得是那条任务该有的形状。"""
    run = rd.run_demo(rd.demo_by_id("red-tests"), engine="fake", work_root=tmp_path / ".work")
    sequence = run.stats["tool_sequence"]
    assert sequence.count("run_tests") >= 3, "没有反复重跑，就不叫 self-debugging"
    assert "edit_file" in sequence and "search_text" in sequence
    assert run.stats["tool_errors"] == 0, "工具本身不该报错（测试红不是工具失败）"
    assert run.result.state.turn == run.stats["turns"]


def test_evidence_file_carries_the_spec_fields(tmp_path):
    run = rd.run_demo(rd.demo_by_id("bug-hunt"), engine="fake", work_root=tmp_path / ".work")
    path = rd.write_evidence(run, quiet=True)
    text = path.read_text(encoding="utf-8")
    for field in ("| task |", "| repo/baseline |", "| expected |", "| actual |", "| turns / tokens |",
                  "| tool_calls |", "| trace |", "| 权限模式 |", "## 判定明细", "## diff"):
        assert field in text, f"证据文件缺少字段 {field}"
    assert "sk-" not in text, "证据文件里不该出现任何密钥片段"


def test_fixtures_stay_pristine_after_a_run(tmp_path):
    """跑完只留下工作副本；fixture 一旦被动过，第二次运行就不是同一个基线了。"""
    names = ("bug-hunt", "red-tests", "greenfield")
    before = {name: rd.tree_hash(rd.FIXTURES / name) for name in names}
    rd.run_demo(rd.demo_by_id("red-tests"), engine="fake", work_root=tmp_path / ".work")
    rd.run_demo(rd.demo_by_id("bug-hunt"), engine="fake", work_root=tmp_path / ".work")
    assert {name: rd.tree_hash(rd.FIXTURES / name) for name in names} == before


def test_bug_hunt_premise_holds():
    """Demo 2 的说服力全押在这一条上：bug 真实存在，但基线测试全绿。

    前提哪天不成立了（比如有人顺手补了覆盖），这个 demo 就退化成"照着
    traceback 改一行"，必须立刻知道。
    """
    code, counts, _ = rd.pytest_report(rd.FIXTURES / "bug-hunt")
    assert code == 0 and counts["failed"] == 0, "基线不再全绿，Demo 2 已失去意义"
    probe = "import sys; sys.path.insert(0, '.'); from duration import format_duration; print(format_duration(90000))"
    exit_code, output = rd.run_in(rd.FIXTURES / "bug-hunt", [sys.executable, "-c", probe], timeout=60)
    assert exit_code == 0 and output.strip().endswith("25d0h0m0s"), f"bug 不再复现：{output!r}"


def test_red_tests_premise_holds():
    _, counts, _ = rd.pytest_report(rd.FIXTURES / "red-tests")
    assert counts["failed"] == 1, "基线红用例数变了，Demo 3 的判定要重写"


def test_readonly_demo_runs_in_readonly_mode(tmp_path):
    run = rd.run_demo(rd.demo_by_id("readonly-qa"), engine="fake", work_root=tmp_path / ".work")
    assert run.mode == "readonly"
    assert run.result.state.denied_actions == 0, "READONLY 是靠模式挡住的，不是靠拒绝堆出来的"
    assert run.stats["tool_errors"] == 0


def test_unsupported_live_engine_is_refused(tmp_path):
    with pytest.raises(ValueError, match="不支持"):
        rd.run_demo(rd.demo_by_id("giveup"), engine="live", work_root=tmp_path / ".work")


def test_demo_table_covers_every_acceptance_line():
    covered = " ".join(demo.acceptance for demo in rd.DEMOS)
    for item in ("A1", "A2", "A3", "A4"):
        assert item in covered, f"验收项 {item} 没有对应的 demo"


@pytest.mark.parametrize("demo", FAKE_DEMOS, ids=lambda demo: demo.id)
def test_scripts_never_recite_a_number_the_judge_owns(demo):
    """模型（这里是我写的脚本）不许自述"N passed"——用例数由判定脚本回答。"""
    for response in demo.script():
        assert not _SELF_REPORTED_COUNT.search(response.text()), f"{demo.id} 的答复里出现了自述用例数"
