"""考题契约的测试 —— 判据本身必须是可检验的数据，不是代码。

`tests/test_eval_runner.py` 已经验过"跑批器判得对"。这里验的是考卷这一侧：

* **未知字段拒绝加载**：`fail_to_pas` 打错一个字母，那道题就退化成"没有成功判据的题"，
  而报表上它照样是一个 pass 率数字；
* **答案不许泄漏**：`gold`（标准答案）与 `probe` 只喂驱动，一个字都不许进 `instruction`；
* **题集哈希覆盖 fixture 内容**：只哈希 JSON 的话，改 fixture 里那行 bug 看不出来，
  旧基线于是还在跟一份不同的考卷比分数；
* **坏题要在开跑前被认出来**（`lint_task`）：送分用例、基线就红的 PASS_TO_PASS。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from miniclaude.eval import drivers
from miniclaude.eval.judge import lint_task
from miniclaude.eval.taskset import TaskInstance, TaskSet, TaskSetError
from miniclaude.messages import ToolUseBlock

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO_ROOT / "eval" / "tasks"


@pytest.fixture(scope="module")
def taskset() -> TaskSet:
    return TaskSet.load(TASKS_DIR)


def payload(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "toy-one",
        "instruction": "把 add 修好",
        "source": {"kind": "vendored", "path": "pkg"},
        "fail_to_pass": ["tests/test_calc.py::test_add"],
        "fake": {"driver": "edit-verify"},
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- 加载即校验


def test_a_misspelled_criterion_is_refused_not_ignored() -> None:
    """`fail_to_pas` 会被静默忽略，于是那道题再也没有成功判据 —— 加载时就报错。"""
    with pytest.raises(TaskSetError, match="不认识这些字段"):
        TaskInstance.from_dict(payload(fail_to_pas=["tests/test_calc.py::test_add"]))


def test_a_task_without_any_criterion_is_refused() -> None:
    """只有 instruction 的题等于没有题：它永远"通过"，因为没有任何东西可否决它。"""
    with pytest.raises(TaskSetError, match="没有任何成功判据"):
        TaskInstance.from_dict(payload(fail_to_pass=[], fake={"driver": "edit-verify"}))


def test_a_task_without_a_fake_driver_is_refused() -> None:
    """fake 引擎是断点续跑与报表的回归网；缺剧本的题在离线 CI 里根本跑不到。"""
    with pytest.raises(TaskSetError, match="fake.driver"):
        TaskInstance.from_dict(payload(fake={}))


def test_case_ids_must_look_like_pytest_nodes() -> None:
    """`test_add` 少了 `tests/test_calc.py::` 前缀，白名单就会命中 0 个用例并判"全绿"。"""
    with pytest.raises(TaskSetError, match="不像用例 id"):
        TaskInstance.from_dict(payload(fail_to_pass=["test_add"]))


def test_unknown_source_field_is_refused() -> None:
    with pytest.raises(TaskSetError, match="source"):
        TaskInstance.from_dict(payload(source={"kind": "vendored", "path": "pkg", "comit": "abc"}))


# ---------------------------------------------------------------- 真题集


def test_the_shipped_taskset_loads_and_every_task_has_a_real_driver(taskset: TaskSet) -> None:
    assert len(taskset) == 24, "题集数量是 SPEC v2 §7.1 行 9 的退出标准，改数量要连 SPEC 一起改"
    assert len({task.id for task in taskset}) == len(taskset)
    for task in taskset:
        assert task.fake.driver in drivers.DRIVERS, f"{task.id} 的剧本没注册"
        assert task.source.resolve(taskset.repo_root).is_dir()


def test_no_answer_leaks_into_any_instruction(taskset: TaskSet) -> None:
    """gold patch、probe 脚本、出题备注都只喂驱动，一个字不许出现在进上下文的那段话里。

    这是"带标准答案的评测"最容易漏的地方：把答案写进题面，题就送出去了，
    而分数看上去完全正常。判据只有一处例外（`answer_keywords`），它读的是答复，
    不是题目。
    """
    for task in taskset:
        leaked: list[str] = []
        for fix in task.fake.args.get("fixes", []) or []:
            new = str(fix.get("new", "")).strip()
            if len(new) > 8 and new in task.instruction:
                leaked.append(f"gold fix: {new[:40]}")
        if task.probe.strip() and task.probe.strip() in task.instruction:
            leaked.append("probe 脚本")
        if task.notes.strip() and task.notes.strip() in task.instruction:
            leaked.append("notes")
        assert not leaked, f"{task.id} 的题面泄漏了答案：{leaked}"


def test_readonly_questions_have_an_answer_criterion(taskset: TaskSet) -> None:
    """问答题的"对错"只在答复里。没有 `answer_keywords` 的话，判定就退化成
    "它没写盘" —— 那是必要条件，不是充分条件。
    """
    for task in taskset:
        if task.is_readonly:
            assert task.answer_keywords, f"{task.id} 是问答题却没有 answer_keywords"
            assert task.min_changed_files == 0


def test_readonly_tasks_do_not_ask_for_writes(taskset: TaskSet) -> None:
    """只读题如果题面要求"修好它"，权限门会把每一次写都拒了 —— 那不是能力问题，是出题事故。"""
    for task in taskset:
        if task.is_readonly:
            assert "不要修改" in task.instruction or "只读" in task.instruction, (
                f"{task.id} 是 readonly 但题面没说要只读"
            )


def test_negative_samples_are_a_fifth_of_the_set(taskset: TaskSet) -> None:
    """判据必须有东西可抓到：`must-fail` 至少 4 道，否则"全绿"仍然是可疑的。"""
    must_fail = [task.id for task in taskset if "must-fail" in task.tags]
    assert len(must_fail) >= 4
    assert all("negative" in task.tags for task in taskset if "must-fail" in task.tags)


def test_every_edit_or_write_script_actually_runs_the_tests(taskset: TaskSet) -> None:
    """edit/write 类剧本必须含"跑测试"这一步，否则 fake 批次会教出"改完就宣布成功"的习惯。

    `read-claim` 是刻意反例（no_verification 样本），所以它在白名单里。
    """
    allowed_no_test = {"read-claim", "guess-paths", "answer", "repeat-stall", "over-budget"}
    for task in taskset:
        if task.fake.driver in allowed_no_test or task.fake.driver.startswith("demo:"):
            continue
        names = [
            block.name
            for reply in drivers.build(task, Path("."))
            for block in reply.blocks
            if isinstance(block, ToolUseBlock)
        ]
        assert "run_tests" in names, f"{task.id} 的剧本没有跑测试那一步：{names}"


# ---------------------------------------------------------------- 哈希与作废


def test_taskset_sha_covers_fixture_contents(tmp_path: Path) -> None:
    """只哈希 JSON 不够：fixture 里那行 bug 改没改，JSON 上一个字都没变。"""
    (tmp_path / "pkg" / "tests").mkdir(parents=True)
    (tmp_path / "eval" / "tasks").mkdir(parents=True)
    (tmp_path / "pkg" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "pkg" / "tests" / "test_calc.py").write_text("def test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    (tmp_path / "eval" / "tasks" / "toy.json").write_text(json.dumps(payload()), encoding="utf-8")

    first = TaskSet.load(tmp_path / "eval" / "tasks")
    (tmp_path / "pkg" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    second = TaskSet.load(tmp_path / "eval" / "tasks")
    assert first.sha != second.sha, "fixture 变了哈希没变，旧基线就还在跟一张不同的考卷比分数"

    (tmp_path / "eval" / "tasks" / "extra.json").write_text(
        json.dumps(payload(id="toy-two", source={"kind": "vendored", "path": "pkg"})), encoding="utf-8"
    )
    third = TaskSet.load(tmp_path / "eval" / "tasks")
    assert third.sha != second.sha, "多了一道题就是另一张考卷，旧基线不该继续可比"


def test_filtered_subset_keeps_the_sha_and_ids_are_validated(taskset: TaskSet) -> None:
    """筛选保留全量哈希（这是同一张考卷的一部分），但选空了必须报错而不是给一个空批次。"""
    subset = taskset.filtered(ids=[taskset.tasks[0].id])
    assert len(subset) == 1 and subset.sha == taskset.sha
    with pytest.raises(TaskSetError, match="一个任务都没有"):
        taskset.filtered(ids=["not-a-task"])


def test_source_kind_repo_is_honest_about_not_being_implemented(tmp_path: Path) -> None:
    """Tier 2 的 `repo` 后端还没做。`resolve()` 必须直说，而不是回一个不存在的路径。"""
    task = TaskInstance.from_dict(payload(source={"kind": "repo", "url": "https://example.invalid/x"}))
    with pytest.raises(TaskSetError, match="Tier 2"):
        task.source.resolve(tmp_path)


# ---------------------------------------------------------------- 考题自检


@pytest.fixture
def lint_repo(tmp_path: Path) -> Path:
    """一份可控的 fixture：test_add 绿、test_sub 红、`test_new` 压根不存在。"""
    (tmp_path / "pkg" / "tests").mkdir(parents=True)
    (tmp_path / "pkg" / "calc.py").write_text("def ok(a):\n    return a\n", encoding="utf-8")
    (tmp_path / "pkg" / "tests" / "test_calc.py").write_text(
        "from calc import ok\n\n\ndef test_add():\n    assert ok(1) == 1\n\n\ndef test_sub():\n    assert ok(1) == 2\n",
        encoding="utf-8",
    )
    return tmp_path / "pkg"


def test_lint_flags_a_free_gift_and_a_dead_whitelist(lint_repo: Path) -> None:
    """两种坏题：把已经绿的用例写进 FAIL_TO_PASS（不用修也 pass），
    以及把基线就红的用例写进 PASS_TO_PASS（要求"保持绿"但它是红的）。
    """
    task = TaskInstance.from_dict(
        payload(fail_to_pass=["tests/test_calc.py::test_add"], pass_to_pass=["tests/test_calc.py::test_sub"])
    )
    checks = {check.label: check for check in lint_task(task, lint_repo)}
    f2p = next(check for label, check in checks.items() if "FAIL_TO_PASS" in label)
    p2p = next(check for label, check in checks.items() if "PASS_TO_PASS" in label)
    assert not f2p.ok and "test_add" in f2p.detail
    assert not p2p.ok and "test_sub" in p2p.detail


def test_lint_lets_a_test_to_be_written_through(lint_repo: Path) -> None:
    """"补一个防回归测试"这类题的 FAIL_TO_PASS 在基线里压根不存在 —— 那是合法形态，
    行为由 `probe` 去验。判它坏就会逼着出题人删掉这条声明。
    """
    task = TaskInstance.from_dict(payload(fail_to_pass=["tests/test_new.py::test_regression"]))
    (check,) = lint_task(task, lint_repo)
    assert check.ok and "需新建" in check.detail


def test_lint_stays_quiet_without_case_criteria(lint_repo: Path) -> None:
    assert lint_task(TaskInstance.from_dict(payload(fail_to_pass=[], must_exist=["calc.py"])), lint_repo) == []
