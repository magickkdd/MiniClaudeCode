"""B4 的前半 —— 拿真实轨迹核对失败模式标签贴得对不对。

`--why-failed` 的标签如果只由"我写的规则命中我自己造的测试用例"来证明，那是循环
论证。这个脚本换成两条外部对照：

1. **判据是独立的。** demo 的 PASS/FAIL 由 `run_demo.py` 在工作副本里独立跑 pytest
   和行为探测得出，不看分类器，也不看模型最后那段话。标签与判据互相打脸就是 bug。
2. **期望是人写的。** `EXPECT` 里每个 trace 一行"人眼读完这条轨迹认为该贴什么标签"，
   脚本只负责检查代码有没有给出同样的答案 —— 不一致就退出码 1。漏写一条也算不一致：
   没被人工核对过的轨迹不许冒充"核对通过"。覆盖范围是 `demos/traces/` 下全部
   `.jsonl`，含 `v1-baseline/`（schema 1.x 的旧轨迹，用来暴露规则的盲区）。

输出 `demos/results/failure-labels.md`（UTF-8）。控制台是 GBK 的话别在终端读它。

    python scripts/b4_label_check.py            # 核对并写文件
    python scripts/b4_label_check.py --write     # 接受当前结果，把它固化为期望值
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from miniclaude.infra.failure import classify, facts_from_records  # noqa: E402
from miniclaude.infra.trace import replay, summarize_records  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "demos" / "traces"
RESULTS = ROOT / "demos" / "results"
OUT = RESULTS / "failure-labels.md"

# 人眼核对结论。key = trace 文件名，value = (期望标签, 为什么这么判)。
# 只写"这条轨迹里看得见的事实"，不写"我希望模型怎样"。
EXPECT: dict[str, tuple[list[str], str]] = {
    "codegen.fake.jsonl": (
        [],
        "写实现→跑测试→再写→再跑，最后 completed；两次 run_tests 都绿，无标签",
    ),
    "bug-hunt.fake.jsonl": (
        [],
        "search_text 定位后读了两个文件再改，改完跑了 run_tests 且为绿",
    ),
    "red-tests.fake.jsonl": (
        [],
        "改的是 cart/ 实现，tests/ 未被改写；最后一次验证是绿的",
    ),
    "readonly-qa.fake.jsonl": (
        [],
        "只读问答，一个字节没写 —— 不该要求它跑测试（v1 规则在这里误报过）",
    ),
    "giveup.fake.jsonl": (
        ["budget_exhausted"],
        "max_turns 收尾且待办全未完成；这正是 A4 设计出来的失败形状",
    ),
    # ---- 下面是真端点跑出来的失败轨迹（schema 2.0），b4-live/ 冻结批次 ----
    # b4-live/ 是 2026-09-22 用 `--max-turns` 刻意压短的失败批次，冻结留档不会再变。
    "b4-live/bug-hunt.live.jsonl": (
        ["path_guessing", "budget_exhausted"],
        "4 个不同路径没读到（`bug-hunt/README.md` 猜了两次），全都带 `bug-hunt/` 这个多余前缀"
        "（工作目录本身就是 bug-hunt）—— 第 2 轮 "
        "find_files 已经成功列过目录，第 3 轮照旧猜，第 4 轮 `cat README.md` 成功是反证。4 轮烧完，全程没调过 write_todos",
    ),
    "b4-live/codegen.live.jsonl": (
        ["budget_exhausted"],
        "先写了 3 项待办，3 轮只够 mkdir + 写三个文件，一次测试都没跑就 max_turns；"
        "待办 3/3 未完成，`no_verification` 没贴是对的 —— 它没声称完成",
    ),
    "b4-live/red-tests.live.jsonl": (
        [],
        "第 1 轮 run_tests 判红，第 4 轮判绿，随后 completed：这是收敛。"
        "`self_confirm` 的'红过又跑绿'逃逸分支在真实轨迹上生效了",
    ),
    # ---- 顶层 *.live.jsonl：最近一次 `--all --engine live` 的真端点轨迹 ----
    # 会随重跑更新；EXPECT 说的是"这一批我人眼读成了什么"，重跑变了就该重读并改写这里。
    "bug-hunt.live.jsonl": (
        [],
        "9 轮 20 次调用、0 次失败的读。第 3 轮改完就 run_tests 绿；第 5、6 轮两次 `bash` 判红"
        "（直接对 `duration/format.py` 跑 pytest）后第 7 轮回到绿才 completed —— 红过又跑绿是收敛，"
        "`self_confirm` 不该贴",
    ),
    "codegen.live.jsonl": (
        [],
        "第 4 轮 pytest 判红，随后两次 edit_file 都落在实现 `calculator/core.py` 的 `_tokenize` 上，"
        "第 6 轮转绿、README 落盘、待办全 done。测试从头到尾没被回改 —— `test_gaming` 沉默是对的",
    ),
    "red-tests.live.jsonl": (
        [],
        "第 3 轮 run_tests 红 → 改 COUPONS 与税额计算（都在 cart/ 实现侧）→ 第 5 轮绿 → completed。"
        "trace 里存的 failure_modes 还是收窄前的 `['context_growth']`，`mcc trace` 会打印'规则口径变过' —— "
        "那是规则变了，不是标签算错",
    ),
    "readonly-qa.live.jsonl": (
        [],
        "只读问答、判据 PASS。第 1 轮猜错两个路径（`format.py`、`test_format.py`）后自己 find_files 纠正："
        "2 个 < 阈值 3，不贴 `path_guessing`；这一轮没有权限拒绝，也不该要求它跑测试",
    ),
    # ---- v1（schema 1.x）留档：规则只能读到 5/8 条，剩下的记在盲区列，不算判对 ----
    "v1-baseline/bug-hunt.live.jsonl": (
        ["path_guessing"],
        "判据 PASS 但过程有模式：连猜 3 个 `src/` 下的不存在路径（`src/format.py`、`src/test_format.py`、"
        "`src/__init__.py`）才 find_files。v1 的判据（要求连续且换目录）在这条上漏报，改成数不同路径才看见",
    ),
    "v1-baseline/codegen.live.jsonl": (
        [],
        "11 轮 19 次调用最后跑绿收尾：轮数多不是模式，`thrashing`/`repeated_calls` 都是 0",
    ),
    "v1-baseline/readonly-qa.live.jsonl": (
        [],
        "只读问答，v1 的老规则在这里误贴过 `no_verification`；收窄到'改过东西且声称完成'后不贴",
    ),
    "v1-baseline/red-tests.live.jsonl": (
        [],
        "改→跑→再改→再跑，最后 completed、判据 PASS。但 v1 轨迹没有 verdict 字段，"
        "这条只能证明'没被误报'，证不了'规则真的看得见红绿'",
    ),
    # ---- mcp-link 联动（docs/mcp-link-spec.md 的 as-built 那两条）----
    "mcp-link-t2.jsonl": (
        ["no_verification"],
        "3 轮 2 次调用：第 1 轮 `mcp__insight-agent__research`（ok）→ 第 2 轮 `write_file` 落 "
        "notes.md → 第 3 轮 completed。贴 `no_verification` 是对的：写完之后**一次都没回读** "
        "落盘结果，报告里那 9 个 URL 对不对它自己不知道，判据是从盘上读文件验的、"
        "不是从模型自述读的 —— 这正是这条规则要抓的东西。`thrashing` 不贴：只有一次 research。",
    ),
    "mcp-link-t3-standard.jsonl": (
        ["no_verification"],
        "**一个文件里 6 个会话**（前 5 个各死于云端 HTTP 429，终止原因 `llm_failure`，标签为空），"
        "第 6 个会话 4 轮跑完：第 1 轮 research 撞 429 → 第 2 轮**原参数重试成功** → 第 3 轮 "
        "write_file → completed。"
        "`thrashing` 原来贴在这里，是误报：重试一轮就成功正是 `STALL_LIMIT=3` 这套机制的预期行为，"
        "读下来是恢复不是空转，阈值已从 `>=1` 抬到 `>=2`。`no_verification` 保留，理由同 t2。"
        "这条也是 `facts_from_records` 按会话分的第一个实测样本（run_end 的计数逐会话覆盖）。",
    ),
}

_ACTUAL_RE = re.compile(r"\| actual \| `(?P<term>[^`]+)` · 判定 (?P<verdict>PASS|FAIL)")


def labels_for(path: Path) -> tuple[dict[str, Any], list[str], list[str]]:
    records = replay(path)
    findings = classify(facts_from_records(records))
    # 盲区按"该规则要读的那类记录里有没有这个字段"算，不看整个文件的键并集 ——
    # 否则一条不相干的记录带了同名键，盲区就被藏起来了。
    def has(kind: str, field: str) -> bool:
        return any(field in record for record in records if record.get("kind") == kind)

    blind = [
        label
        for label, present in (
            ("context_growth 看不到 est_tokens", has("turn_start", "est_tokens")),
            ("context_growth 看不到 output_chars", has("tool_call", "output_chars")),
            ("self_confirm / test_gaming 看不到 verdict", has("tool_call", "verdict")),
        )
        if not present
    ]
    return summarize_records(records), [found.mode.value for found in findings], blind


def judge_verdict(trace_name: str) -> str:
    """去证据文件里读那条独立判据（`v1-baseline/` 里的轨迹读同名的备份证据）。"""
    evidence = RESULTS / f"{trace_name[:-len('.jsonl')]}.md"
    if not evidence.is_file():
        return "（无证据文件）"
    match = _ACTUAL_RE.search(evidence.read_text(encoding="utf-8"))
    return f"{match.group('verdict')} / {match.group('term')}" if match else "（判据行没找到）"


def markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# B4 前半 · 失败模式标签的人工核对",
        "",
        "> 本文件由 `python scripts/b4_label_check.py` 生成。",
        "> 「分类器标签」是规则从 trace 里算的；「人工期望」是人读完这条 trace 写进 `EXPECT` 的",
        "> 判断 —— 不一致就是 ✗ 并退出码 1，**没写过核对的轨迹也算 ✗**。",
        "> 「独立判据」是 demo 在工作副本里跑 pytest 得出的 PASS/FAIL，既不看分类器也不看模型自述。",
        "",
        "| trace | 轮/调用 | 独立判据 | 分类器标签 | 人工期望 | 一致？ | 盲区 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        expected = "（缺人工核对）" if row["expected"] is None else (", ".join(row["expected"]) or "（无）")
        lines.append(
            f"| `{row['name']}` | {row['turns']}/{row['calls']} | {row['judge']} | "
            f"{', '.join(row['actual']) or '（无）'} | {expected} | "
            f"{'✓' if row['ok'] else '✗'} | {row['blind'] or '—'} |"
        )
    lines += [
        "",
        "## 逐条依据",
        "",
        *[f"- `{name}`：{why}" for name, (_, why) in sorted(EXPECT.items())],
        "",
        "**盲区**列说的是：这条轨迹里没有该规则要读的字段，所以规则不可能命中 ——",
        "不是规则判对了，是它没参与。v1 的 live 轨迹全部如此（那时尚未记录",
        "`turn_start.est_tokens` 与 `tool_call.verdict`）。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="把当前标签固化为期望值")
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for path in sorted(TRACES.rglob("*.jsonl")):
        name = path.relative_to(TRACES).as_posix()
        report, actual, blind = labels_for(path)
        expected = list(actual) if args.write else (EXPECT[name][0] if name in EXPECT else None)
        rows.append(
            {
                "name": name,
                "turns": report["turns"],
                "calls": report["tool_calls"],
                "judge": judge_verdict(name),
                "actual": actual,
                "expected": expected,
                "ok": actual == expected,
                "blind": "、".join(blind),
            }
        )

    if args.write:
        body = [
            "EXPECT: dict[str, tuple[list[str], str]] = {",
            *(
                f'    "{row["name"]}": ({row["actual"]!r}, "（--write 生成，请人改写理由）"),'
                for row in rows
            ),
            "}",
        ]
        print("\n".join(body))
        return 0

    OUT.write_text(markdown(rows), encoding="utf-8")
    bad = [row for row in rows if not row["ok"]]
    print(f"核对 {len(rows)} 条轨迹，{len(bad)} 条与人工期望不一致 → {OUT.relative_to(ROOT)}")
    for row in bad:
        print(f"  ✗ {row['name']}: 代码={row['actual']} 人工={row['expected']}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
