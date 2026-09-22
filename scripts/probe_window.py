"""上下文窗口硬边界实测（SPEC v2 §0.4 的外部前置条件 P1 / 风险 R1）。

策略不是从下往上二分 —— 那要为每次成功的探测付整段 prompt 的钱。
先直接打一次远超预算的请求：**被拒时端点通常会把真实上限写进错误消息**，
一次请求、几乎零成本就拿到数字。只有错误消息里读不到数字时才退化成二分。

预算硬闸：累计探测 token 超过 BUDGET_TOKENS 立即停止并如实报告"未测定"。

用法：python scripts/probe_window.py            # 只读探测，不写任何业务文件
      python scripts/probe_window.py --budget 600000
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from miniclaude.config import Config  # noqa: E402

# 探测用的填充文本：纯 ASCII 行，让 chars/token 比例稳定可预测。
FILLER_LINE = "def helper_{i}(a: int, b: str) -> tuple[int, str]:  # boundary handling and fallback path\n"
_LIMIT_RE = re.compile(
    r"(?:maximum|max|context|window|prompt)[^\d]{0,24}(\d{4,7})[^\d]{0,24}"
    r"(?:tokens|context|window)",
    re.IGNORECASE,
)
_ANY_NUMBER_RE = re.compile(r"(\d{4,7})\s*tokens", re.IGNORECASE)


def filler(target_tokens: int, chars_per_token: float) -> str:
    """构造约 target_tokens 长的填充文本（按实测 chars/token 反推字符数）。"""
    chars = max(len(FILLER_LINE), int(target_tokens * chars_per_token))
    repeat = chars // len(FILLER_LINE) + 1
    return "".join(FILLER_LINE.format(i=n % 100000) for n in range(repeat))[:chars]


def send(cfg: Config, target_tokens: int, chars_per_token: float) -> tuple[int, str, dict[str, Any]]:
    """发一次只要求 1 个输出 token 的超长请求。返回 (状态码, 文本片段, usage)。"""
    body = {
        "model": cfg.model,
        "max_tokens": 1,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "以下是仓库签名清单，请原样保留：\n"
                                          + filler(target_tokens, chars_per_token)},
            {"role": "user", "content": "回答 OK"},
        ],
    }
    with httpx.Client(timeout=max(60, cfg.request_timeout)) as client:
        r = client.post(
            f"{cfg.base_url}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {cfg.api_key}"},
        )
    usage = (r.json().get("usage") or {}) if r.status_code < 400 and _is_json(r) else {}
    return r.status_code, r.text[:600], usage


def _is_json(r: httpx.Response) -> bool:
    return "json" in (r.headers.get("content-type") or "")


def calibrate(cfg: Config) -> float:
    """实测填充文本的 chars/prompt_token，让探测长度可预测（比例用真填充料校准）。"""
    probe = filler(2500, 3.5)
    body = {
        "model": cfg.model,
        "max_tokens": 1,
        "messages": [{"role": "system", "content": probe}, {"role": "user", "content": "OK"}],
    }
    with httpx.Client(timeout=60) as client:
        r = client.post(f"{cfg.base_url}/chat/completions", json=body,
                        headers={"Authorization": f"Bearer {cfg.api_key}"})
    used = (r.json().get("usage") or {}).get("prompt_tokens") or 0
    return (len(probe) / used) if used else 3.5


def extract_limit(text: str) -> int | None:
    for pattern in (_LIMIT_RE, _ANY_NUMBER_RE):
        m = pattern.search(text or "")
        if m:
            return int(m.group(1))
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="实测端点上下文窗口硬边界")
    ap.add_argument("--budget", type=int, default=600_000, help="累计探测 token 上限")
    ap.add_argument("--start-tokens", type=int, default=200_000, help="首探目标 prompt token")
    args = ap.parse_args(argv)

    cfg = Config.from_env()
    report: dict[str, Any] = {"model": cfg.model, "base_url": cfg.base_url}

    cpt = calibrate(cfg)
    report["chars_per_token_measured"] = round(cpt, 2)
    report["calibration_cost_note"] = "校准请求本身 prompt 约 8000 字符，成本可忽略"

    spent = 0
    lo_ok, hi_fail, err_text = 0, 0, ""
    # ① 先高打一次：被拒通常直接吐出真实上限
    sc, text, usage = send(cfg, args.start_tokens, cpt)
    spent += (usage.get("prompt_tokens") or 0) or (args.start_tokens if sc >= 400 else 0)
    report["first_probe"] = {"target_tokens": args.start_tokens, "status": sc,
                             "prompt_tokens": usage.get("prompt_tokens"), "body": text[:300]}
    limit = extract_limit(text) if sc >= 400 else None
    if sc >= 400:
        hi_fail = args.start_tokens
        err_text = text
    else:
        lo_ok = usage.get("prompt_tokens") or args.start_tokens

    report["limit_from_error"] = limit
    # ② 错误里没数字才二分；每步都花真钱，所以步数与预算双闸
    steps: list[dict[str, Any]] = []
    if limit is None and hi_fail:
        report["method"] = "binary_search"
        for _ in range(4):
            if spent > args.budget:
                report["aborted"] = f"超预算 {args.budget} tokens"
                break
            mid = (lo_ok + hi_fail) // 2
            if mid - lo_ok < 2000:
                break
            sc, text, usage = send(cfg, mid, cpt)
            got = usage.get("prompt_tokens") or mid
            spent += got
            steps.append({"target": mid, "status": sc, "prompt_tokens": usage.get("prompt_tokens")})
            if sc >= 400:
                hi_fail = mid
                err_text = text
                limit = extract_limit(text) or limit
            else:
                lo_ok = got
            if limit:
                break
        report["binary_steps"] = steps
    else:
        report["method"] = "error_message" if limit else "single_probe_succeeded"

    report["tokens_spent_estimate"] = spent
    report["lower_bound_known_good"] = lo_ok or None
    report["upper_bound_known_bad"] = hi_fail or None
    report["error_excerpt"] = err_text[:400] if err_text else None
    report["window_conclusion"] = limit or (f">= {lo_ok}" if lo_ok else "未测定")
    report["token_budget_current"] = cfg.token_budget
    report["verdict"] = (
        "TOKEN_BUDGET 安全" if limit is None or limit >= cfg.token_budget * 1.15
        else f"TOKEN_BUDGET={cfg.token_budget} 偏高，真实上限约 {limit}，§3.3 阈值需按此重算"
    )

    out = Path("demos/results/context-window.probe.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n已写入 {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
