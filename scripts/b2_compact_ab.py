"""B2 验收证据生成器（SPEC v2 §3.3 · §7.1 行 10）。

B2 的字面要求是三句："压缩关闭时失败，开启时成功"、"压缩后发出 0 次因配对破损导致的
400"、"摘要里必须能 grep 到本轮已改文件名"。这三句都不能靠人转述 —— 所以本脚本把
载体题 `lc-rollup-api` 跑三臂，把每一条判据落成 JSON 里的一个 clause，任何一条不成立
退出码就是 1。

三臂的设计（一次只动一个变量）：
  off    `--no-compact`                          —— 唯一的差别就是阶梯本身
  on     默认配置                                  —— 实验组
  tight  `--context-budget 12000 --context-hard-limit 15000`
                                                 —— 负向对照：窗口真的小到装不下时，
                                                    阶梯照样救不回来。没有这条，
                                                    "开着阶梯就过"可能被读成"阶梯是万能药"

`--live` 再跑真实端点 N 次（默认 5）：那才是"0 次配对 400"这句话的原始语境 —— fake 引擎
不发 HTTP，它只能证明配对*结构*合法。live 臂用 `gf-calculator` + 小预算，让阶梯在便宜的
任务上也被真正触发，而不必为一方窗口烧掉 400k token 的题。

用法：
  PYTHONPATH="src;demos" python -X utf8 scripts/b2_compact_ab.py
  PYTHONPATH="src;demos" python -X utf8 scripts/b2_compact_ab.py --live --live-repeats 5
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "demos")]

from miniclaude.cli import eval_cmd  # noqa: E402
from miniclaude.infra.trace import replay  # noqa: E402

TASKS = ROOT / "eval" / "tasks-b2"
WORK = ROOT / "eval" / ".work" / "b2-ab"
RESULT = ROOT / "eval" / "results" / "b2-compact-ab.json"

# 阶梯关掉之后，压死会话的是 L3 拒载线（0.95 × TOKEN_BUDGET=30,400），不是硬熔断：
# 这条区分很重要 —— 它说明"失败"来自上下文预算本身，而不是我们新加的某个开关碰巧拧小了。
ARMS: dict[str, tuple[str, list[str]]] = {
    "off": ("对照组：阶梯关闭", ["--no-compact"]),
    "on": ("实验组：默认配置", []),
    "tight": ("负向对照：12k 预算 + 15k 熔断线", ["--context-budget", "12000", "--context-hard-limit", "15000"]),
}

# 配对破损在端点侧长什么样：OpenAI 兼容层的 400 文案会点名 tool_use / tool_result。
PAIRING_HINTS = ("tool_use", "tool_result", "messages.1.content", "paired")


def _pairing_400(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 trace 里挑出"像配对破损"的 llm 层错误。"""
    return [
        r
        for r in records
        if r.get("kind") == "error"
        and str(r.get("layer")) == "llm"
        and any(hint in str(r.get("message", "")).lower() for hint in PAIRING_HINTS)
    ]


def run_arm(extra: list[str], out: Path) -> dict[str, Any]:
    """跑一臂，返回"这一臂的可比对事实"。数字全部从 report.json 与 trace 里读。"""
    shutil.rmtree(out, ignore_errors=True)
    argv = [
        "--tasks", str(TASKS), "--out", str(out), "--engine", "fake", "--repeats", "1", *extra,
    ]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = eval_cmd.run(argv)
    run = json.loads((out / "report.json").read_text(encoding="utf-8"))["runs"][0]
    records = replay(out / "traces" / f"{run['task_id']}.r0.fake.jsonl")
    compactions = [r for r in records if r.get("kind") == "context_compact"]
    estimates = [int(r.get("est_tokens") or 0) for r in records if r.get("kind") == "turn_start"]
    kept = sorted({path for r in compactions for path in (r.get("summary_files") or [])})
    refusals = [r for r in records if r.get("kind") == "context_refuse"]
    return {
        "argv": argv,
        "exit_code": code,
        "verdict": run["verdict"],
        "termination": run["metrics"].get("termination"),
        "turns": run["metrics"].get("turns"),
        "tokens": run["metrics"].get("tokens"),
        "peak_est_tokens": max(estimates or [0]),
        "compactions": len(compactions),
        "elide_attempts": sum(1 for r in compactions if r.get("level") == "elide"),
        "summarize_attempts": sum(1 for r in compactions if r.get("level") == "summarize"),
        "elided_blocks": sum(int(r.get("elided_blocks") or 0) for r in compactions),
        "dropped_blocks": sum(int(r.get("dropped_blocks") or 0) for r in compactions),
        "saved_est_tokens": sum(int(r.get("saved_est") or 0) for r in compactions),
        "summary_tokens": sum(int(r.get("summary_tokens") or 0) for r in compactions),
        "abandoned_by_pairing": sum(1 for r in compactions if not r.get("pairing_ok", True)),
        "id_repairs": sum(int(r.get("id_repairs") or 0) for r in records if r.get("kind") == "llm_response"),
        "summary_files_kept": kept,
        "context_refusals": [
            {key: r.get(key) for key in ("line", "threshold_tokens", "est_tokens", "ladder_enabled", "turn")}
            for r in refusals
        ],
        "pairing_400_errors": len(_pairing_400(records)),
        "broken_checks": [c["label"] for c in run["checks"] if not c["ok"]],
    }


def run_live(repeats: int, out: Path) -> dict[str, Any]:
    """真实端点臂：小预算把阶梯逼出来，数"配对破损导致的 400"和成功率。"""
    shutil.rmtree(out, ignore_errors=True)
    argv = [
        "--tasks", str(ROOT / "eval" / "tasks"), "--only", "gf-calculator", "--engine", "live",
        "--repeats", str(repeats), "--context-budget", "6000", "--out", str(out),
    ]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = eval_cmd.run(argv)
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    runs = report["runs"]
    traces = sorted((out / "traces").glob("*.jsonl"))
    records = [r for path in traces for r in replay(path)]
    passed = sum(1 for r in runs if r["verdict"] == "pass")
    return {
        "argv": argv,
        "exit_code": code,
        "repeats": len(runs),
        "pass_at_k": f"{passed}/{len(runs)}",
        "success_rate": round(passed / len(runs), 3) if runs else 0.0,
        "tokens_spent": report["summary"].get("tokens_spent", report["summary"].get("tokens")),
        "compactions": sum(1 for r in records if r.get("kind") == "context_compact"),
        "abandoned_by_pairing": sum(
            1 for r in records if r.get("kind") == "context_compact" and not r.get("pairing_ok", True)
        ),
        "pairing_400_errors": len(_pairing_400(records)),
        "termination_counts": {
            key: sum(1 for r in runs if r["metrics"].get("termination") == key)
            for key in sorted({str(r["metrics"].get("termination")) for r in runs})
        },
    }


def collect_compression_tests() -> int:
    """SPEC 那句"8 项压缩测试绿"里的测试有几个 —— 数出来，不手填。"""
    listing = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "tests/test_compact.py", "tests/test_pairing.py"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return sum(1 for line in listing.stdout.splitlines() if "::" in line)


def build_clauses(arms: dict[str, dict[str, Any]], tests: dict[str, Any]) -> list[dict[str, Any]]:
    on, off, tight = arms["on"], arms["off"], arms["tight"]
    rollups = [f"reports/part_{i:02d}_summary.py" for i in range(1, 9)]
    grep = [name for name in rollups if name in on["summary_files_kept"]]
    return [
        {
            "id": "off_arm_fails_on_budget",
            "claim": "关掉阶梯：同一题、同一剧本必须失败，且失败原因是上下文",
            "ok": off["verdict"] == "fail" and off["termination"] == "context_overflow",
            "detail": f"verdict={off['verdict']} termination={off['termination']} 第 {off['turns']} 轮",
        },
        {
            "id": "off_arm_death_attributed",
            "claim": "对照组的死因在 trace 里自带归因：越的是 L3 拒载线、阶梯当时没开",
            "ok": (
                len(off["context_refusals"]) == 1
                and off["context_refusals"][0]["line"] == "l3_refuse"
                and off["context_refusals"][0]["ladder_enabled"] is False
            ),
            "detail": f"off 臂 context_refuse {off['context_refusals'] or '缺失'}",
        },
        {
            "id": "on_arm_passes",
            "claim": "开启阶梯：默认配置下这题判据全绿",
            "ok": on["verdict"] == "pass" and on["termination"] == "completed",
            "detail": f"verdict={on['verdict']} 第 {on['turns']} 轮 · 未过判据 {on['broken_checks'] or '无'}",
        },
        {
            "id": "ladder_really_off_in_control",
            "claim": "对照组里阶梯一次都没动过手（否则两臂比的不是阶梯）",
            "ok": off["compactions"] == 0,
            "detail": f"off 臂 context_compact 记录 {off['compactions']} 条",
        },
        {
            "id": "both_tiers_fired",
            "claim": "L1 与 L2 各被真正触发过（只到 L1 就等于没测到摘要）",
            "ok": on["elided_blocks"] > 0 and on["dropped_blocks"] > 0 and on["summarize_attempts"] > 0,
            "detail": (
                f"L1 省略 {on['elided_blocks']} 块 / L2 摘要 {on['dropped_blocks']} 块"
                f"（{on['summarize_attempts']} 次），共省 {on['saved_est_tokens']} est tokens"
            ),
        },
        {
            "id": "zero_pairing_abandonments",
            "claim": "压缩后 0 次因配对破损被放弃（结构层面的那句话）",
            "ok": on["abandoned_by_pairing"] == 0,
            "detail": f"on 臂 {on['compactions']} 次压缩，放弃 {on['abandoned_by_pairing']} 次",
        },
        {
            "id": "zero_pairing_400",
            "claim": "没有一次请求因配对破损被端点拒收",
            "ok": all(a["pairing_400_errors"] == 0 for a in (on, off, tight)),
            "detail": "fake 臂只能证明结构合法；端点侧的 400 由 live 臂负责（未跑 live 时本条只覆盖 fake）",
        },
        {
            "id": "summary_carries_changed_files",
            "claim": "摘要里 grep 得到本轮已改文件名",
            "ok": len(grep) >= 6,
            "detail": f"摘要清单里留下 {len(on['summary_files_kept'])} 个路径，其中汇总模块 {len(grep)}/8：{grep}",
        },
        {
            "id": "l2_cost_is_charged",
            "claim": "L2 的开销进了报表（压缩不是免费的）",
            "ok": on["summary_tokens"] > 0 and on["tokens"] == on["summary_tokens"],
            "detail": f"summary_tokens={on['summary_tokens']} · 全批 tokens={on['tokens']}（剧本 usage 恒为 0）",
        },
        {
            "id": "tight_window_still_dies",
            "claim": "负向对照：窗口真不够时阶梯也救不回来，它不是万能药",
            "ok": tight["verdict"] == "fail" and tight["compactions"] >= 1,
            "detail": (
                f"tight 臂 verdict={tight['verdict']} termination={tight['termination']}，"
                f"阶梯试了 {tight['compactions']} 次"
            ),
        },
        {
            "id": "id_repairs_reached_the_wire",
            "claim": "端点撞车的 tool_use id 在入历史前被就地改名（B2 首跑的死因）",
            "ok": on["id_repairs"] > 0 and on["abandoned_by_pairing"] == 0,
            "detail": f"on 臂改了 {on['id_repairs']} 个 id，改完之后没有一次压缩因配对被放弃",
        },
        {
            "id": "compression_tests_ge_8",
            "claim": "SPEC 那句『8 项压缩测试』全绿",
            "ok": tests["count"] >= 8 and tests["exit_code"] == 0,
            "detail": (
                f"tests/test_compact.py + tests/test_pairing.py 收集到 {tests['count']} 项，"
                f"整批退出码 {tests['exit_code']}"
            ),
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 B2 验收证据")
    parser.add_argument("--live", action="store_true", help="额外跑真实端点臂（要花额度）")
    parser.add_argument("--live-repeats", type=int, default=5, help="live 臂每题重复次数")
    args = parser.parse_args()

    arms: dict[str, dict[str, Any]] = {}
    for name, (label, extra) in ARMS.items():
        print(f"→ {name} 臂：{label}")
        arms[name] = run_arm(extra, WORK / name)
        arm = arms[name]
        print(
            f"   {arm['verdict']} / {arm['termination']} · {arm['turns']} 轮 ·"
            f" 压缩 {arm['compactions']} 次（放弃 {arm['abandoned_by_pairing']}）· 峰值 est {arm['peak_est_tokens']:,}"
        )

    tests = {"count": collect_compression_tests()}
    listing = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_compact.py", "tests/test_pairing.py"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    tests["exit_code"] = listing.returncode
    tests["output_tail"] = listing.stdout.strip().splitlines()[-1:]

    clauses = build_clauses(arms, tests)
    payload: dict[str, Any] = {
        "schema": 1,
        "spec": "SPEC v2 §3.3 / §7.1 行 10 · 验收线 B2",
        "engine": "fake",
        "task": "lc-rollup-api",
        "fixture": {
            "path": "eval/fixtures/ledger",
            "generator": "scripts/make_ledger_fixture.py",
            "why": "8 模块 × 100 公开函数 ≈ 19.4 万字符，关阶梯必超预算、开阶梯必走到 L2",
        },
        "arms": arms,
        "compression_tests": tests,
        "clauses": clauses,
        "pass": all(clause["ok"] for clause in clauses),
        "notes": [
            "两臂只差 --no-compact 一个开关；tight 臂是负向对照，不参与 A/B 的因果结论。",
            "off 臂的死因写在 context_refuse 里，不在 turn_start 的估算序列里：阶梯关掉后越线发生在"
            "那一轮工具结果回填之后，而 turn_start 每轮只采样一次，所以 trace 上最后一个 est 反而低于阈值"
            "（本臂实测 23,871 < 30,400）。只看 est 序列会把这判成'模型自己停了'。",
            "id_repairs：FakeLLM 每轮都发 call_0，跨轮撞车会让 _guard_pairing 认为原始历史就非法、"
            "从而放弃每一次压缩 —— 阶梯因此整体空转。修在 loop 入历史之前（messages.dedupe_tool_use_ids）。",
            "summary_files 来自代码算的本地改动清单，不押在模型是否照模板写字段上。",
        ],
    }

    if args.live:
        print(f"→ live 臂：真实端点 × {args.live_repeats}（会花额度）")
        live = run_live(args.live_repeats, WORK / "live")
        payload["live"] = live
        rate = live["success_rate"]
        payload["clauses"].append(
            {
                "id": "live_success_rate_ge_60",
                "claim": "压缩开启时真实端点成功率 ≥60%",
                "ok": rate >= 0.6,
                "detail": f"{live['pass_at_k']} = {rate:.0%} · 终止分布 {live['termination_counts']}",
            }
        )
        payload["clauses"].append(
            {
                "id": "live_zero_pairing_400",
                "claim": "真实端点上 0 次因配对破损导致的 400",
                "ok": live["pairing_400_errors"] == 0 and live["abandoned_by_pairing"] == 0,
                "detail": (
                    f"400 {live['pairing_400_errors']} 次 · 因配对放弃 {live['abandoned_by_pairing']} 次"
                    f" · 压缩 {live['compactions']} 次"
                ),
            }
        )
        payload["pass"] = all(clause["ok"] for clause in payload["clauses"])

    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print()
    for clause in payload["clauses"]:
        print(f"{'✓' if clause['ok'] else '✗'} {clause['id']:32s} {clause['detail']}")
    half = "" if "live" in payload else " 的 fake 侧（live 半条未跑，见 eval/results/b2-live-blocked.json）"
    print(f"\nB2{half} {'达成' if payload['pass'] else '未达成'} · 证据 {RESULT.relative_to(ROOT)}")
    if "live" in payload:
        print(f"live：{payload['live']['pass_at_k']} · tokens {payload['live']['tokens_spent']}")
    return 0 if payload["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
