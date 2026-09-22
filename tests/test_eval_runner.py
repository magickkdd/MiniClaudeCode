"""跑批器的测试 —— 用一份**临时**题集，不碰 `eval/fixtures`。

为什么不拿真题集来测：那会让"测试通过"依赖 24 道考题的内容，而考题是要随发现
改动的（本项目已经改过一次 fixture 了）。这里只钉跑批器自己的契约：

* 判绿靠的是 gold patch 真把用例翻绿了 —— 不是剧本声称成功；
* 工作副本隔离，基线目录跑一百次也不动；
* manifest 逐条落盘、可续跑，哈希对不上的记录**不复用**；
* 批次预算到点就停，并在报表里留下 `aborted_batch`；
* 篡改考卷（改测试让测试变绿）必须判 fail。

模型侧一律 `--engine fake`：真工具、真落盘、真判据，只是不打网络。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from miniclaude.eval.contract import tree_hash
from miniclaude.eval.regression import instrument_checks
from miniclaude.eval.runner import BatchReport, EvalRunner, RunRecord
from miniclaude.eval.taskset import TaskSet

BUGGY = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
SUITE = """import pytest
from calc import add, sub


def test_add():
    assert add(2, 3) == 5


def test_sub():
    assert sub(5, 3) == 2
"""

# `old_string` 在工作副本里必须唯一 —— `edit_file` 拒绝歧义匹配，一改不落地就成了
# "剧本说改完、判据说一个字没动"。
GOLD_FIXES = [{"path": "calc.py", "old": "def add(a, b):\n    return a - b", "new": "def add(a, b):\n    return a + b"}]
TAMPER_FIX = [{"path": "tests/test_calc.py", "old": "assert add(2, 3) == 5", "new": "assert True"}]


def task_json(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "toy-fix",
        "instruction": "`add` 算错了，修好它。别动 tests/ 下的东西。",
        "source": {"kind": "vendored", "path": "pkg"},
        "fail_to_pass": ["tests/test_calc.py::test_add"],
        "pass_to_pass": ["tests/test_calc.py::test_sub"],
        "protected": ["tests"],
        "min_changed_files": 1,
        "max_turns": 8,
        "fake": {"driver": "edit-verify", "args": {"fixes": GOLD_FIXES}},
        "tags": ["bugfix"],
    }
    base.update(over)
    return base


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """一份最小考题：`tmp/eval/tasks/*.json` + `tmp/pkg`（被判定的代码）。

    `TaskSet` 用 `tasks/.. /..` 反推仓库根，所以目录层级不能随便摆。
    """
    (tmp_path / "pkg" / "tests").mkdir(parents=True)
    (tmp_path / "eval" / "tasks").mkdir(parents=True)
    (tmp_path / "pkg" / "calc.py").write_text(BUGGY, encoding="utf-8")
    (tmp_path / "pkg" / "tests" / "test_calc.py").write_text(SUITE, encoding="utf-8")
    return tmp_path


def write_task(repo: Path, payload: dict[str, Any]) -> Path:
    path = repo / "eval" / "tasks" / f"{payload['id']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def runner_for(repo: Path, out: Path, **kwargs: Any) -> EvalRunner:
    kwargs.setdefault("engine", "fake")
    return EvalRunner(tasks=TaskSet.load(repo / "eval" / "tasks"), out=out, **kwargs)


def verdicts_of(report: Any) -> dict[str, str]:
    return {run.task_id: run.verdict for run in report.runs if run.repeat == 0}


def failed_checks(report: Any, task_id: str) -> list[str]:
    run = next(run for run in report.runs if run.task_id == task_id)
    return [check["label"] for check in run.checks if not check["ok"]]


# ---------------------------------------------------------------- 判绿是真判出来的


def test_gold_patch_actually_flips_the_whitelist_green(repo: Path, tmp_path: Path) -> None:
    """这条是整个评测层的地基：剧本说"改完了"不算，**跑测试**说了算。

    同一个 fixture 基线是红的（`test_add` 用 `a - b`），gold fix 落盘后才转绿，
    所以 pass 这个结论是代码给的。 fake 批次因此同时是 gold-patch 检查。
    """
    write_task(repo, task_json())
    report = runner_for(repo, tmp_path / "out", repeats=1).run()
    assert verdicts_of(report) == {"toy-fix": "pass"}
    assert report.summary["pass_at_1_raw"] == "1/1"
    # 改动落在工作副本里，判绿才有物证。
    assert (tmp_path / "out" / "work" / "toy-fix.r0" / "calc.py").read_text(encoding="utf-8") == FIXED
    # 判据明细里必须看得见 F2P 与 P2P 两条都过了，而不是只有一条"总分 100"。
    labels = next(run for run in report.runs if run.repeat == 0).checks
    assert any("FAIL_TO_PASS" in item["label"] and item["ok"] for item in labels)
    assert any("PASS_TO_PASS" in item["label"] and item["ok"] for item in labels)


def test_a_wrong_patch_fails_even_though_the_script_claims_success(repo: Path, tmp_path: Path) -> None:
    """剧本照样以"改完了，测试跑过"收尾，但改的是错的地方 —— 判定必须是 fail。

    这是上一条的反面证据：如果只有"剧本成功→报表成功"，整个评测层就是自评。
    """
    write_task(repo, task_json(**{"fake": {"driver": "edit-verify", "args": {"fixes": [
        {"path": "calc.py", "old": "def sub(a, b):", "new": "def sub(a, b):  # 只加了行注释"}
    ]}}}))
    report = runner_for(repo, tmp_path / "out", repeats=1).run()
    assert verdicts_of(report) == {"toy-fix": "fail"}
    assert any("FAIL_TO_PASS" in label for label in failed_checks(report, "toy-fix"))


def test_tampering_the_suite_is_caught_even_when_everything_then_passes(repo: Path, tmp_path: Path) -> None:
    """把断言改成 `assert True` 之后整个测试套件是绿的 —— 唯一能抓住它的是 `protected`。

    这条是"改考卷"的具象化：不抓的话，它就是评测层里最赚钱的作弊路径。
    """
    write_task(repo, task_json(fake={"driver": "tamper", "args": {"read": ["tests/test_calc.py"], "fixes": TAMPER_FIX}}))
    report = runner_for(repo, tmp_path / "out", repeats=1).run()
    assert verdicts_of(report) == {"toy-fix": "fail"}
    assert any("未被为了让测试变绿而改写" in label for label in failed_checks(report, "toy-fix"))


def test_negative_tagged_task_that_passes_raises_a_guard_alarm(repo: Path, tmp_path: Path) -> None:
    """判据自检用的上同一条路：把 must-fail 题的判据拆掉，`instrument_checks` 必须叫。

    这里直接把 tamper 任务打上 `must-fail` 标签，而它确实判红了 —— 所以不应有告警；
    真正的告警路径在 `test_eval_regression.py` 里用合成记录验。两条凑起来才是完整证据：
    题集侧"该红的红"，代码侧"红了会叫"。
    """
    write_task(repo, task_json(id="toy-tamper", tags=["negative", "must-fail", "tamper-detected"],
                               fake={"driver": "tamper", "args": {"fixes": TAMPER_FIX}}))
    report = runner_for(repo, tmp_path / "out", repeats=1).run()
    assert verdicts_of(report) == {"toy-tamper": "fail"}
    assert instrument_checks(report.runs) == []


# ---------------------------------------------------------------- 隔离


def test_the_baseline_tree_never_moves_however_many_times_we_run(repo: Path, tmp_path: Path) -> None:
    """评测跑在副本上。基线被碰过一次，"只增不删""逐字节未变"就全都失效了 ——
    而且失效的方向是让后面的批次**更容易**通过。
    """
    write_task(repo, task_json())
    before = tree_hash(repo / "pkg")
    (repo / "eval" / "tasks" / "toy-fix.json").read_text(encoding="utf-8")
    for repeat in (0, 1):
        report = runner_for(repo, tmp_path / f"out{repeat}", repeats=1).run()
        assert report.summary["runs"] == 1
    assert tree_hash(repo / "pkg") == before
    assert (repo / "pkg" / "calc.py").read_text(encoding="utf-8") == BUGGY


def test_two_repeats_get_two_independent_workdirs(repo: Path, tmp_path: Path) -> None:
    """r0 改过的目录不能给 r1 当前线，否则"重复三次"其实是同一份改动跑三遍。"""
    write_task(repo, task_json())
    report = runner_for(repo, tmp_path / "out", repeats=2).run()
    assert report.summary["repeats"] == 2
    dirs = sorted(path.name for path in (tmp_path / "out" / "work").iterdir())
    assert dirs == ["toy-fix.r0", "toy-fix.r1"]


# ---------------------------------------------------------------- manifest：续跑与作废


def test_manifest_records_each_run_as_it_finishes(repo: Path, tmp_path: Path) -> None:
    """逐条落盘是断点续跑的全部依据。跑完才写文件的话，中途崩了就什么都没剩下。"""
    write_task(repo, task_json())
    out = tmp_path / "out"
    runner_for(repo, out, repeats=2).run()
    lines = [json.loads(line) for line in (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(line["task_id"], line["repeat"], line["verdict"]) for line in lines] == [
        ("toy-fix", 0, "pass"),
        ("toy-fix", 1, "pass"),
    ]
    assert lines[0]["taskset_sha"] == TaskSet.load(repo / "eval" / "tasks").sha
    assert (out / "report.json").is_file() and (out / "report.md").is_file()


def test_resume_reuses_finished_runs_without_re_running_them(repo: Path, tmp_path: Path) -> None:
    """72 次 live 跑到第 50 次崩了要能接着跑 —— B1 的字面要求。"""
    write_task(repo, task_json())
    out = tmp_path / "out"
    runner_for(repo, out, repeats=1).run()

    calls: list[str] = []

    class NoRerun(EvalRunner):
        def run_task(self, task: Any, repeat: int) -> RunRecord:  # pragma: no cover - 走到就是失败
            calls.append(task.id)
            raise AssertionError("续跑不该重跑已完成的 run")

    report = NoRerun(tasks=TaskSet.load(repo / "eval" / "tasks"), out=out, engine="fake", repeats=1).run()
    assert calls == []
    assert report.summary["resumed"] == 1
    assert verdicts_of(report) == {"toy-fix": "pass"}


def test_manifest_records_from_a_different_taskset_are_dropped(repo: Path, tmp_path: Path) -> None:
    """考卷变了，旧成绩就没有意义 —— 宁可重跑，也不能拿旧 pass 冒充新 pass。"""
    write_task(repo, task_json())
    out = tmp_path / "out"
    runner_for(repo, out, repeats=1).run()
    manifest = out / "manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["taskset_sha"] = "deadbeefdead"
    manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    messages: list[str] = []
    runner = runner_for(repo, out, repeats=1, on_line=messages.append)
    report = runner.run()
    assert report.summary["resumed"] == 0
    assert any("哈希不符" in line for line in messages), "忽略了记录要在终端说一声，不能悄悄重跑"


def test_a_half_written_manifest_line_is_skipped_not_fatal(repo: Path, tmp_path: Path) -> None:
    """最后一行常被 Ctrl-C 截断。评测工具在这一点上必须能自愈。"""
    write_task(repo, task_json())
    out = tmp_path / "out"
    out.mkdir(parents=True)
    (out / "manifest.jsonl").write_text('{"task_id": "toy-fix", "rep\n', encoding="utf-8")
    report = runner_for(repo, out, repeats=1).run()
    assert verdicts_of(report) == {"toy-fix": "pass"}, "坏行不能污染这一批的判定"


# ---------------------------------------------------------------- 预算与失败关闭


class ScriptedRunner(EvalRunner):
    """替身：不跑 agent，只按预定 token 数记账 —— 预算闸是跑批循环的责任，
    不该靠把 token 数做进剧本里来测它。
    """

    def __init__(self, *args: Any, tokens: int = 500, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.unit = tokens
        self.calls: list[str] = []

    def run_task(self, task: Any, repeat: int) -> RunRecord:
        self.calls.append(f"{task.id}.r{repeat}")
        record = RunRecord(task.id, repeat, "fake", "pass", None, metrics={"tokens": self.unit, "turns": 1},
                           taskset_sha=self.tasks.sha, tags=task.tags)
        self.append_manifest(record)
        return record


def test_batch_budget_stops_the_batch_and_says_so(repo: Path, tmp_path: Path) -> None:
    """单任务的 `token_ceiling` 与整批 `budget_tokens` 是两回事。没有后者，
    一次失手的 live 批次能在一夜之间把预算花掉两倍。

    题集按文件名排序加载（`rglob` 的结果是排过序的），所以这里用 x1/x2/x3 命名，
    让"跑到哪一题停的"这件事在断言里没有歧义。
    """
    for name in ("b-x1", "b-x2", "b-x3"):
        write_task(repo, task_json(id=name))
    out = tmp_path / "out"
    runner = ScriptedRunner(tasks=TaskSet.load(repo / "eval" / "tasks"), out=out, engine="fake",
                            repeats=1, budget_tokens=600, tokens=500)
    report = runner.run()
    assert runner.calls == ["b-x1.r0", "b-x2.r0"], "预算在第三题前就该生效"
    assert report.summary["aborted_batch"] is True
    assert report.summary["runs"] == 2
    assert report.summary["tokens_spent"] == 1000


def test_the_budget_line_explains_an_overspend(repo: Path, tmp_path: Path) -> None:
    """闸只在任务边界生效，所以最后一题可以合法地把预算顶过去一点（live 冒烟实测：
    预算 300,000，实际 313,812）。报表不说破，那一行看起来就像闸坏了。

    `repeats=1` 时 `pass@k` 与 `pass@1` 是同一个数，并列印出来像两个指标。
    """
    write_task(repo, task_json())
    runner = runner_for(repo, tmp_path / "out", repeats=1, budget_tokens=300_000)
    report = BatchReport(
        runs=[],
        summary={"runs": 6, "tasks": 6, "repeats": 1, "engine": ["live"], "pass_at_1_raw": "4/6",
                 "pass_at_k_raw": "4/6", "success_rate_ci": [0.3, 0.903], "verdicts": {"pass": 4, "fail": 2, "error": 0},
                 "budget_tokens": 300_000, "tokens_spent": 313_812},
        per_tag={},
        taskset_sha=runner.tasks.sha,
    )
    text = runner.markdown(report)
    assert "超出 13,812" in text and "闸在任务边界生效" in text
    assert "pass@1 = 4/6（repeats=1" in text
    assert "pass@1 = 4/6，pass@1 = 4/6" not in text


def test_an_aborted_batch_keeps_its_own_wording_even_when_under_budget(repo: Path, tmp_path: Path) -> None:
    """`aborted_batch` 与"超了一点的最后一题"是两种结论：前者有任务没跑，后者没有。
    混成一行就会在报表里丢"这批其实不完整"这件事。
    """
    write_task(repo, task_json())
    runner = runner_for(repo, tmp_path / "out", repeats=1, budget_tokens=300_000)
    report = BatchReport(
        runs=[],
        summary={"runs": 3, "tasks": 3, "repeats": 1, "engine": ["live"], "pass_at_1_raw": "1/3",
                 "success_rate_ci": [0.0, 1.0], "budget_tokens": 300_000, "tokens_spent": 290_000,
                 "aborted_batch": True},
        per_tag={},
        taskset_sha=runner.tasks.sha,
    )
    text = runner.markdown(report)
    assert "ABORTED_BATCH" in text and "超出" not in text


def test_workers_must_stay_at_one(repo: Path, tmp_path: Path) -> None:
    """并行会把同一端点的限流抖动混进"配置 A vs 配置 B"的比较里。这条 ValueError
    是文档，也是防止有人"顺手优化"掉的锁。
    """
    write_task(repo, task_json())
    with pytest.raises(ValueError, match="workers"):
        runner_for(repo, tmp_path / "out", workers=4)
    with pytest.raises(ValueError, match="engine"):
        runner_for(repo, tmp_path / "out", engine="magic")
    with pytest.raises(ValueError, match="repeats"):
        runner_for(repo, tmp_path / "out", repeats=0)


def test_a_task_that_cannot_even_be_assembled_does_not_take_the_batch_down(repo: Path, tmp_path: Path) -> None:
    """装配失败（这里用不存在的 fake 驱动模拟）是**这一题**的结果，不是整批的异常。

    批跑没有人类确认者：任何一步抛出未捕获的异常，剩下的题就都不会跑，
    而报表上看起来像"这批只有两题"。所以错误必须落成一条 `verdict=error` 的记录。
    """
    write_task(repo, task_json(id="c-bad", fake={"driver": "no-such-driver"}))
    write_task(repo, task_json(id="c-good"))
    report = runner_for(repo, tmp_path / "out", repeats=1).run()
    assert verdicts_of(report) == {"c-bad": "error", "c-good": "pass"}
    assert report.summary["verdicts"] == {"pass": 1, "fail": 0, "error": 1}
    bad = next(run for run in report.runs if run.task_id == "c-bad")
    assert "装配失败" in bad.error and "no-such-driver" in bad.error
