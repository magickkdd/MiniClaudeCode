"""`mcc eval` 的测试 —— CLI 自己不做判定，但它对外只有一门语言：退出码。

三条写法上的约束：

* 一律显式给 `--tasks / --out / --baselines-dir`。仓库根下**真的**有 `eval/baselines/`
  与 `eval/.work/`，测试一旦依赖默认值，就会把合成基线写进 B1 的证据目录里。
* 断言终端上那几行人话，不去 import `_finish` 之类的内部函数：读报表的人看到的就是这些字。
* 退出码 0 / 1 / 2 各要有一条真走到的用例。"这批能不能拿去汇报"如果只写在 docstring 里，
  CI 就没有任何东西拦得住一次退步。

模型侧仍然全 fake：这里验的是装配与出口，不是判据（判据在 `test_eval_judge.py`）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from miniclaude.cli import eval_cmd
from miniclaude.config import ConfigError
from miniclaude.eval.taskset import TaskSet

from test_eval_runner import (  # noqa: E402 - 同一份玩具考题，别再抄一遍 fixture 内容
    TAMPER_FIX,
    repo,  # noqa: F811 - pytest fixture，按名注入
    task_json,
    write_task,
)


def run_cli(repo: Path, *argv: str, capsys: Any) -> tuple[int, str, str]:
    """跑一次 CLI。`--tasks` 之外一律指到 tmp 里，尤其 `--baselines-dir` ——
    真仓库的 `eval/baselines/` 是 B1 的证据目录，测试既不该往里写，也不该依赖读到什么。
    """
    code = eval_cmd.run(
        ["--tasks", str(repo / "eval" / "tasks"), "--out", str(repo / "out"),
         "--baselines-dir", str(repo / "baselines"), *argv]
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ---------------------------------------------------------------- 只读开关


def test_list_prints_every_task_and_the_set_hash(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_task(repo, task_json())
    write_task(repo, task_json(id="toy-two", tags=["readonly"]))
    code, out, _ = run_cli(repo, "--list", capsys=capsys)
    assert code == 0
    assert "toy-fix" in out and "toy-two" in out
    assert "共 2 题 · 题集哈希" in out
    # 逐题那行得给出判据规模 —— 决定"这一题值多少额度"的就是 f2p/p2p 与 fake 驱动。
    assert "f2p=1 p2p=1" in out and "fake=edit-verify" in out


def test_lint_passes_a_task_whose_whitelist_matches_the_baseline(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """考题自检：判据**可能为真**才配拿去跑 live。"""
    write_task(repo, task_json())
    code, out, _ = run_cli(repo, "--lint", capsys=capsys)
    assert code == 0
    assert "0 条判据有问题" in out


def test_lint_fails_a_task_that_gives_points_away(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """把 FAIL_TO_PASS 写成基线里**已经绿**的用例，等于送分题。

    坏题不会报错，只会安静地抬高 pass 率，所以这里必须响，而不是"跑起来也算 pass"。
    """
    write_task(repo, task_json(fail_to_pass=["tests/test_calc.py::test_sub"], pass_to_pass=["tests/test_calc.py::test_add"]))
    code, out, _ = run_cli(repo, "--lint", capsys=capsys)
    assert code == 1
    assert "[坏题] toy-fix" in out
    assert "送分用例" in out


# ---------------------------------------------------------------- 用法错误 → 2


def test_unknown_flag_is_a_usage_error(repo: Path) -> None:
    write_task(repo, task_json())
    with pytest.raises(SystemExit) as boom:
        eval_cmd.run(["--tasks", str(repo / "eval" / "tasks"), "--engien", "fake"])
    assert boom.value.code == 2, "argparse 的用法错误码要和我们的 2 对上，CI 才分得清"


def test_missing_tasks_directory_exits_two_without_tracing(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = eval_cmd.run(["--tasks", str(repo / "nope" / "tasks"), "--out", str(repo / "out")])
    captured = capsys.readouterr()
    assert code == 2
    assert "题集加载失败" in captured.err
    assert "评测开始" not in captured.out, "加载失败就不该开跑 —— 报错留在 stderr，别混进报表"


def test_a_filter_that_matches_nothing_exits_two(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_task(repo, task_json())
    code, out, err = run_cli(repo, "--only", "no-such-task", capsys=capsys)
    assert code == 2
    assert "筛选后没有任务" in err
    assert "评测开始" not in out


def test_live_without_model_config_exits_two(repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 `.env` 的机器上，live 批次要在**花钱之前**停下来。"""

    def boom() -> Any:
        raise ConfigError("OPENAI_API_KEY 未设置")

    monkeypatch.setattr(eval_cmd, "get_config", boom)
    write_task(repo, task_json())
    code, out, err = run_cli(repo, "--engine", "live", "--repeats", "1", capsys=capsys)
    assert code == 2
    assert "live 引擎需要完整的模型配置" in err
    assert "评测开始" not in out


def test_fake_engine_never_reads_the_model_config(repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """模块 docstring 承诺"没有密钥的机器也能把全批秒级跑完" —— 这条钉住它。"""

    def boom() -> Any:
        raise AssertionError("fake 引擎不该碰 .env")

    monkeypatch.setattr(eval_cmd, "get_config", boom)
    write_task(repo, task_json())
    code, out, _ = run_cli(repo, "--engine", "fake", "--repeats", "1", capsys=capsys)
    assert code == 0
    assert "runs=1" in out


# ---------------------------------------------------------------- 跑一批


def test_a_clean_batch_exits_zero_and_leaves_the_three_artifacts(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_task(repo, task_json())
    code, out, _ = run_cli(repo, "--repeats", "1", capsys=capsys)
    assert code == 0
    assert "评测开始：1 题 × 1 次 · engine=fake" in out
    assert "pass@1=1/1" in out
    for name in ("report.md", "report.json", "manifest.jsonl"):
        assert (repo / "out" / name).is_file(), f"{name} 是这批评测对外的凭据，少了它等于没跑"
    # 没比基线要说清楚：报表上"无差异"与"没做对比"不是一回事。
    assert "未与基线对比" in out


# ---------------------------------------------------------------- 基线：存、自动匹配、拒绝


def test_save_baseline_writes_an_engine_hash_named_file(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_task(repo, task_json())
    sha = TaskSet.load(repo / "eval" / "tasks").sha
    code, out, _ = run_cli(repo, "--repeats", "1", "--save-baseline", capsys=capsys)
    assert code == 0
    path = repo / "baselines" / f"fake-{sha}.json"
    assert f"基线已写入：{path}" in out
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["engine"] == ["fake"] and payload["taskset_sha"] == sha
    assert payload["tasks"]["toy-fix"]["verdict"] == "pass"
    # 基线里不存 trace 路径：那是这台机器这一次的临时目录，对下一批没有任何意义。
    assert "trace_path" not in json.dumps(payload)


def test_the_matching_baseline_is_picked_up_without_being_told(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """第二次跑要自动找到基线并给出"无变化" —— 这一步没有实测过就等于没有。"""
    write_task(repo, task_json())
    assert run_cli(repo, "--repeats", "1", "--save-baseline", capsys=capsys)[0] == 0
    code, out, _ = run_cli(repo, "--repeats", "1", "--no-resume", capsys=capsys)
    assert code == 0
    assert "基线：" in out
    assert "未与基线对比" not in out
    assert "pass@1 无变化" in out
    assert "判定完全一致" in out


def test_a_subset_batch_does_not_auto_pair_with_the_full_baseline(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--only` / `--smoke` 出来的子集若自动去配全量基线，就会刷出一屏"这批没跑到"，
    把一次正常的冒烟报成退步 —— 而 `--smoke` 每次都这样，等于把告警喊哑。
    """
    write_task(repo, task_json())
    write_task(repo, task_json(id="toy-two"))
    assert run_cli(repo, "--repeats", "1", "--save-baseline", capsys=capsys)[0] == 0
    code, out, _ = run_cli(repo, "--repeats", "1", "--no-resume", "--only", "toy-fix", capsys=capsys)
    assert code == 0
    assert "1/2 题的子集" in out
    assert "基线：" not in out
    assert "pass@1 无变化" not in out, "没比过就不能印出比过的样子"
    assert "这批没跑到基线里的任务" not in out


def test_a_baseline_from_another_tasksheet_is_refused_before_spending(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """哈希对不上的基线比没有基线更危险 —— 默认不开跑。"""
    write_task(repo, task_json())
    fake = repo / "baselines" / "fake-deadbeefdeadbe.json"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(
        json.dumps({"schema": 1, "taskset_sha": "deadbeefdeadbe", "engine": "fake", "tasks": {"toy-fix": {"verdict": "pass"}}}),
        encoding="utf-8",
    )
    code, out, err = run_cli(repo, "--repeats", "1", "--baseline", str(fake), capsys=capsys)
    assert code == 2
    assert "题集哈希与基线不一致" in err
    assert "已拒绝开跑" in err
    assert "评测开始" not in out


def test_force_runs_anyway_but_still_reports_no_comparison(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_task(repo, task_json())
    fake = repo / "baselines" / "fake-deadbeefdeadbe.json"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(
        json.dumps({"schema": 1, "taskset_sha": "deadbeefdeadbe", "engine": "fake", "tasks": {"toy-fix": {"verdict": "pass"}}}),
        encoding="utf-8",
    )
    code, out, _ = run_cli(repo, "--repeats", "1", "--baseline", str(fake), "--force", capsys=capsys)
    assert code == 1, "强行跑了不可比的批次，退出码仍要说「不能拿去汇报」"
    assert "评测开始" in out
    assert "题集哈希变了" in out


def test_a_writeback_of_a_different_taskset_does_not_match_the_baseline(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """自动匹配只认哈希：目录里躺着一份别的考卷的基线时，宁可说"没做对比"，也不拿它比。"""
    write_task(repo, task_json())
    other = repo / "baselines" / "fake-000000000000.json"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text(json.dumps({"schema": 1, "taskset_sha": "000000000000", "engine": "fake", "tasks": {}}), encoding="utf-8")
    code, out, _ = run_cli(repo, "--repeats", "1", capsys=capsys)
    assert code == 0
    assert "未与基线对比" in out


# ---------------------------------------------------------------- 1 的两个来源


def test_a_regression_against_the_baseline_exits_one(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """同一张考卷、同一个剧本，判定却由 pass 变 fail —— 这就是要拦的东西。

    这里用 manifest 冒充"上一次的结果变了"：真实场景是改了代码或改了判据，
    而 CLI 只需要认这一件事 —— 逐条记录里那道题不是 pass 了。
    """
    write_task(repo, task_json())
    assert run_cli(repo, "--repeats", "1", "--save-baseline", capsys=capsys)[0] == 0
    manifest = repo / "out" / "manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["verdict"] = "fail"
    manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    code, out, _ = run_cli(repo, "--repeats", "1", capsys=capsys)
    assert code == 1
    assert "**退步**" in out
    assert "由 pass 变为 fail" in out


def test_a_must_fail_task_judged_red_exits_zero(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`must-fail` 按设计判红 —— 退出码必须是 0，否则每次跑 fake 批都得"红"着收尾。

    这批里 12 个 fail 全是这类题，所以 `_trouble` 得按 `must-fail` 放过它们；
    同时判据没抓到才算事（那条例外在 `test_a_must_fail_task_judged_green_exits_one`）。
    """
    write_task(
        repo,
        task_json(
            id="toy-tamper",
            tags=["negative", "must-fail"],
            fake={"driver": "tamper", "args": {"read": ["tests/test_calc.py"], "fixes": TAMPER_FIX}},
        ),
    )
    code, out, _ = run_cli(repo, "--repeats", "1", capsys=capsys)
    assert code == 0
    assert "verdicts={'pass': 0, 'fail': 1" in out
    assert "必须被抓坏的题" not in out, "抓到了还告警，等于把告警喊哑"
    assert "这些题按判据没做对" not in out


def test_a_must_fail_task_judged_green_exits_one(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """该被抓坏的题判成了 pass —— 这是量尺坏了，比分数低严重，所以要占退出码。

    做法是把这道 `must-fail` 题的剧本换成真会做对的 gold patch：判定 pass，
    而 `instrument_checks` 只看标签，不看剧本，所以它必须叫。
    """
    write_task(repo, task_json(id="toy-quiet", tags=["negative", "must-fail"]))
    code, out, _ = run_cli(repo, "--repeats", "1", capsys=capsys)
    assert code == 1
    assert "**判据告警**" in out
    assert "toy-quiet" in out


def test_a_negative_task_that_is_caught_still_counts_as_trouble(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """带 `negative` 但不带 `must-fail` 的题判红了，终端要说"按判据没做对"。

    这里用 `read-claim`：读两眼就宣称成功，`min_changed_files` 抓得住。放过它等于
    把"负样本"当成"永远不算失败"的免死金牌 —— 那两个标签的分工就没了。
    """
    write_task(
        repo,
        task_json(
            id="toy-claim",
            tags=["negative", "self-confirm"],
            fake={"driver": "read-claim", "args": {"read": ["calc.py"], "text": "已经修好了，行为符合承诺。"}},
        ),
    )
    code, out, _ = run_cli(repo, "--repeats", "1", capsys=capsys)
    assert code == 1
    assert "这些题按判据没做对：toy-claim" in out
    assert "必须被抓坏的题" not in out


# ---------------------------------------------------------------- 阶梯 A/B 的两臂


def test_the_tuned_batch_announces_which_knob_it_moved(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--no-compact` 跑出来的分数不是默认配置的分数，终端必须自报家门。

    两臂的差别只在这一个开关上；报表上看不出来，下一次读的人就会把它当成
    "阶梯开着也没区别"的那一批 —— 而 B2 要的恰恰是这两批的对比。
    """
    write_task(repo, task_json())
    code, out, _ = run_cli(repo, "--repeats", "1", "--no-compact", capsys=capsys)
    assert code == 0
    assert "上下文阶梯：已关闭" in out
    code, out, _ = run_cli(repo, "--repeats", "1", "--no-resume", "--context-budget", "8000", capsys=capsys)
    assert code == 0
    assert "上下文阶梯：开，预算 8,000 tokens" in out


def test_a_default_batch_stays_quiet_about_the_ladder(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_task(repo, task_json())
    code, out, _ = run_cli(repo, "--repeats", "1", capsys=capsys)
    assert code == 0
    assert "上下文阶梯" not in out, "没拧过旋钮就别刷屏 —— 告警喊哑了等于没有"


def test_a_tuned_batch_cannot_be_saved_as_the_baseline(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """基线是 B1 的那把尺子。拿关掉阶梯的一批去当尺子，之后每批都会被量错。"""
    write_task(repo, task_json())
    code, out, err = run_cli(repo, "--repeats", "1", "--no-compact", "--save-baseline", capsys=capsys)
    assert code == 2
    assert "不能当基线入库" in err
    assert "评测开始" not in out, "拒绝要发生在花钱之前"
    written = list((repo / "baselines").glob("*.json")) if (repo / "baselines").exists() else []
    assert written == [], f"证据目录里不该出现被拧过阶梯的批次：{written}"


# ---------------------------------------------------------------- live 选题：--only 说了算


def _tasks_with_a_live_only_and_a_negative(repo: Path) -> TaskSet:
    write_task(repo, task_json())
    write_task(repo, task_json(id="toy-negative", tags=["negative"], supports_live=False))
    return TaskSet.load(repo / "eval" / "tasks")


def test_explicit_only_survives_the_live_default(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--only` 在 live 下必须还是 `--only`。

    它被"live 只跑 supports_live"那句默认覆盖过一次：脚本里写着 `--only gf-calculator`，
    实际把 17 道题全跑了一遍。默认是给"没点名"的人省额度的，不是来推翻点名的。
    """
    tasks = _tasks_with_a_live_only_and_a_negative(repo)
    args = eval_cmd.build_parser().parse_args(["--only", "toy-fix"])
    picked = eval_cmd._select(args, tasks, engine="live")
    assert [task.id for task in picked] == ["toy-fix"]
    assert "跳过" not in capsys.readouterr().out, "点名了就不该再播报默认跳过"


def test_an_unnamed_live_batch_still_skips_the_negative_samples(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """没点名时默认照旧：负样本靠 fake 复现，不花额度。"""
    tasks = _tasks_with_a_live_only_and_a_negative(repo)
    args = eval_cmd.build_parser().parse_args([])
    picked = eval_cmd._select(args, tasks, engine="live")
    assert [task.id for task in picked] == ["toy-fix"]
    assert "跳过 1 道 supports_live=false" in capsys.readouterr().out


def test_a_named_negative_sample_says_so_before_it_costs_money(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """点名要点到负样本上也可以，但终端得先说清楚：这一道是真花额度的。"""
    tasks = _tasks_with_a_live_only_and_a_negative(repo)
    args = eval_cmd.build_parser().parse_args(["--only", "toy-negative"])
    picked = eval_cmd._select(args, tasks, engine="live")
    assert [task.id for task in picked] == ["toy-negative"]
    assert "supports_live=false，仍按点名跑在真实端点上" in capsys.readouterr().out
