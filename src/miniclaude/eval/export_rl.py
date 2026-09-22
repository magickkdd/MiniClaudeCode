"""§3.9 的轨迹导出器：trace + 判据 → 每行一条 (state, action, reward)。

**这份数据不做训练**（D23）。它存在的理由有三层，都不依赖我们自己训模型：它是"我理解
后训练要什么数据形状"的证据；它是回归集的种子（失败轨迹正是下一批评测任务该长的地方）；
`no_verification` / `test_gaming` 这类模式标签是行为级标注，是偏好数据里最贵的那部分
人工判断的自动化前身。

一个绕不开的事实决定了这个文件的形状：**trace 里没有观测**。§3.1 做度量修补时把
`llm_response.blocks` 存成类型名（`["ToolUseBlock","ToolUseBlock"]`）、`run_start` 只存
`user_input_chars`，为的是日志体积、以及不把仓库内容抄进日志。所以"state"只有两个可能的产地：

· **产地 A —— 会话快照**（§3.6 的 durable resume 落的那份）。`messages` 是完整正文，
  于是 `state_t = snapshot.messages[:message_count[t]]`。这条配对是**可验**的：
  `llm_request.message_count` 记的是"这次请求带去多少条"，所以第 t 轮那段 assistant 回复
  恰好落在 `messages[message_count[t]]`，它的 `tool_use` id 序列应当与 trace 里该轮
  `tool_call.tool_use_id` 序列逐项相等。等式成立才给正文，不成立就不给 —— 导出一份错位
  的 (state, action) 比导不出来更坏，因为它看起来是好的。
· **产地 B —— 结构指纹**。没有快照时 state 退化成 `message_count` / `est_tokens` /
  `prefix_hash` / 这一轮发了哪些工具。这**不是**能喂给策略梯度的观测，所以行里明写
  `reconstructible: false`，别让下游猜。

压缩阶梯会破产地 A：L1/L2 改写的就是那份 history，快照里的 messages 不再是模型当时看见的。
所以某轮之后出现过 `context_compact`，那之后的每一行都带 `broken_by_compaction` 的原因串。

在产地之前还有一道**同一性**闸门（SPEC v2 §7.3-7）：manifest 那行判据落盘时给 trace 盖了指纹
（行数 + 全文件 sha256），join 之前先比对。这一步要挡的是"reward 配错了轨迹"里最难看见的那一种
—— 同一道题重跑过、或者有人手工改过轨迹，行数甚至轮数都还对，只有内容不是当时那份。三种状态
分开记：`verified` 进数据、`drift` 整条丢弃并记账、`unverified`（指纹上线之前的历史批次）照旧进
数据但每一行自述没被校验过。第三种不是折中，是这批数据的既有事实：那些 manifest 里没有可比哈希，
硬要"看起来都校验过"就得回头伪造指纹。

reward 的算法（§3.9 的原话是"verdict(0/1) 与 steps_to_success 的折扣项"）：

    terminal = 1.0 判 pass / 0.0 判 fail；error 与 aborted **不进数据** —— 那是我们自己的
               故障，喂进奖励函数等于教模型躲避我们的 bug
    value_t  = terminal * gamma ** (从这一步到终止还剩几步)

折扣只作用在"离成功还有多远"，不参与判定本身。`gamma=1.0` 时每步都等于终局（只看结果的
极端），调小则是给早停 shaping。数值连同 gamma、`steps_to_end` 一起写在每一行里，不藏在
默认值后面 —— 下游要能算出我们没算的那几种。

用法：
  python -m miniclaude.eval.export_rl --batch eval/.work/fake --out eval/.work/rl.jsonl
  mcc export-rl --batch eval/.work/b6-ab/local --evidence eval/results/s15-export.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from miniclaude.backend.sessions import _safe
from miniclaude.eval.runner import RunRecord
from miniclaude.infra.failure import VERIFY_TOOLS
from miniclaude.infra.trace import _SECRET_LIKE, _scrub_value, fingerprint, replay

SCHEMA = 1
DEFAULT_GAMMA = 0.97
DEFAULT_BLOCK_CHARS = 2000
# 丢弃账本进证据文件时最多列几条。上限存在只为挡住"一批全坏"时的病态体积，不是为了省字节：
# 这批 manifest 会被后续批次覆盖，这份账可能就是唯一记录，所以宁大不小。
_LEDGER_CAP = 200
# 判据没落盘的 run 不进导出：reward 是这份数据的骨架，缺了它每一行都是猜。
TERMINAL = {"pass": 1.0, "fail": 0.0}
# trace 里没有正文的这些 kind 仍然要归到某一步上 —— 它们决定 signals。
STEP_KINDS = ("tool_call", "permission", "llm_response", "context_compact", "context_refuse", "todo_update", "session_replay", "error")


@dataclass
class Step:
    """一轮决策：模型看见什么（state 的一半）、它做了什么（action）、值多少（reward 的一半）。"""

    turn: int
    message_count: int = 0
    est_tokens: int = 0
    prefix_hash: str = ""
    tools_count: int = 0
    has_request: bool = False
    has_response: bool = False
    stop_reason: str = ""
    text_blocks: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)
    denied: int = 0
    replays: int = 0
    compacted: bool = False
    refused: bool = False
    todos_open: int | None = None
    errors: int = 0


def steps_from(records: Sequence[dict[str, Any]]) -> list[Step]:
    """trace 记录 → 按轮聚合。

    锚在 `llm_request` 而不是 `llm_response`：请求代表"模型此刻看到的上下文形状"，而一次
    请求可以没有响应（429、超时、预算止损），那一轮仍然是数据 —— 它是 `llm_failure` 的产地，
    丢了就等于把失败模式最集中的一类样本删掉。schema 1.x 的老轨迹没有 `llm_request`，
    那时补一个空锚点并把 `message_count` 留 0：配对会因此失败并写明原因，而不是静默错位。

    反过来，**只有 `context_refuse` 的那一轮不是决策步**：拒载是我们自己在发出请求之前刹车，
    模型既没看见那份上下文、也没给出动作，这一格里没有 (state, action)。它记的是"这一轮为什么
    没有下一次"，属于终局，不属于样本。这条不是纸上推的：`context_refuse` 带的轮号是**下一轮**
    （循环先 `turn += 1` 再算预算），于是一路死在 L3 线上的 run 会凭空多出一个决策步，
    和 manifest 的轮数差一格 —— B2 live 臂那 5 次真端点运行连同 off/tight 两臂一起，
    曾因此被 `turn-mismatch` 整批丢掉（占那次导出的全部丢弃）。
    """
    by_turn: dict[int, Step] = {}

    def slot(turn: int) -> Step:
        if turn not in by_turn:
            by_turn[turn] = Step(turn=turn)
        return by_turn[turn]

    for record in records:
        kind = record.get("kind")
        turn = int(record.get("turn") or 0)
        if kind == "llm_request":
            step = slot(turn)
            step.has_request = True
            step.message_count = int(record.get("message_count") or 0)
            step.est_tokens = int(record.get("est_tokens") or 0)
            step.prefix_hash = str(record.get("prefix_hash") or "")
            step.tools_count = int(record.get("tools_count") or 0)
        elif kind in STEP_KINDS:
            step = slot(turn)
            if kind == "tool_call":
                step.calls.append(dict(record))
            elif kind == "permission" and str(record.get("decision")) == "deny":
                step.denied += 1
            elif kind == "llm_response":
                step.has_response = True
                step.stop_reason = str(record.get("stop_reason") or "")
                step.text_blocks = sum(1 for block in record.get("blocks") or [] if str(block) == "TextBlock")
            elif kind == "context_compact":
                step.compacted = True
            elif kind == "context_refuse":
                step.refused = True
            elif kind == "todo_update":
                items = record.get("items") or []
                step.todos_open = sum(1 for item in items if str(item.get("status")) != "completed")
            elif kind == "session_replay":
                step.replays += 1
            elif kind == "error":
                step.errors += 1
    return [step for step in (by_turn[turn] for turn in sorted(by_turn)) if step.has_request or step.has_response]


def find_snapshot(session_id: str, trace_path: Path | None, extra_roots: Iterable[Path] = ()) -> Path | None:
    """按 session id 找会话快照。找不到返回 None —— 那是产地 B 的入口，不是错误。

    用 rglob 而不是拼路径：跑批时快照落在**每次运行自己的工作副本**里
    （`eval/.work/<批>/<臂>/work/<task>.rN.<pid>/.mcc/sessions/`），那层目录名带进程号，
    拼不出来。rglob 实测 0.06s 一份，代价可以忽略。

    只往**上找一级**：trace 在 `<批>/<臂>/traces/`，快照在 `<批>/<臂>/work/<run>/.mcc/sessions/`，
    所以 `<批>/<臂>` 这一层就是它俩的最近公共祖先。曾经多爬两级，测试里当场抓到一次误配 ——
    那两级之外是系统的临时目录根，躺着别的跑批留下的同名快照，于是这一份数据配上了别人的现场。
    找不到的正确结论是"产地 B"，不是"去更远的地方再看看"。

    快照是一条 session 的最终现场（每轮覆盖写）。`--resume` 续跑时 messages 是被读回来
    接着长的，所以按 `message_count` 切片仍对齐；换 session id 的续跑另算一份现场，那要按
    trace 自己的 session 字段找，正是这个函数在做的事。真实记忆目录不在这次运行副本下，
    用 `--memory-root` 递给它。
    """
    if not session_id:
        return None
    name = f"{_safe(session_id)}.json"
    roots: list[Path] = [Path(item) for item in extra_roots]
    if trace_path is not None:
        roots[:0] = list(Path(trace_path).parents)[1:2]
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in root.rglob(f"sessions/{name}"):
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if str(raw.get("session_id") or "") == session_id:
                return candidate
    return None


def pair_snapshot(snapshot: dict[str, Any], steps: Sequence[Step]) -> list[str]:
    """产地 A 的配对检查。返回**不成立的原因**，空列表 = 逐轮全对得上。

    一份快照只有全部轮次都对得上才用。半对半错的前缀会被下游当成好的那半。
    """
    messages = snapshot.get("messages") or []
    reasons: list[str] = []
    for step in steps:
        if not step.has_request:
            reasons.append(f"turn {step.turn}: trace 里没有 llm_request（schema 1.x？），不知道上下文有多长")
            continue
        index = step.message_count
        if not 0 <= index < len(messages):
            reasons.append(f"turn {step.turn}: message_count={index} 落在快照之外（快照 {len(messages)} 条）")
            continue
        want = [str(call.get("tool_use_id") or "") for call in step.calls if call.get("tool_use_id")]
        if not want:
            continue
        content = messages[index].get("content")
        have = [
            str(block.get("id"))
            for block in (content if isinstance(content, list) else [])
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        if have != want:
            reasons.append(f"turn {step.turn}: trace 要 {want} · 快照那条给 {have}")
    return reasons[:6]


def clip(text: str, limit: int, *, key: str = "body") -> dict[str, Any]:
    """过 `_scrub_value` 再截断。截断必须留痕：静默截掉的正文会让下游以为拿到的是全部。"""
    clean = _scrub_value(text if isinstance(text, str) else str(text))
    value = clean if isinstance(clean, str) else json.dumps(clean, ensure_ascii=False, sort_keys=True)
    if len(value) <= limit:
        return {key: value, "chars": len(value), "truncated": False}
    return {key: value[:limit], "chars": len(value), "truncated": True}


def state_delta(snapshot: dict[str, Any], step: Step, previous: int, limit: int) -> list[dict[str, Any]]:
    """这一步**新出现**在上下文里的观测（增量导出）。

    逐步导出全量前缀是 O(n²) 体积，而下游真要全量按 `message_count` 自己拼即可。切到
    `message_count` 为止：那之后的第一条是模型自己的回复，属于 action，不在 state 里。
    """
    messages = snapshot.get("messages") or []
    out: list[dict[str, Any]] = []
    for message in messages[max(0, previous) : max(0, step.message_count)]:
        content = message.get("content")
        blocks: list[dict[str, Any]] = []
        if isinstance(content, str):
            blocks.append({"type": "text", **clip(content, limit)})
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                kind = str(block.get("type") or "")
                if kind == "tool_use":
                    item = {"type": "tool_use", "name": str(block.get("name") or ""), "call_id": str(block.get("id") or "")}
                    item.update(clip(json.dumps(block.get("input") or {}, ensure_ascii=False, sort_keys=True), limit, key="args_json"))
                elif kind == "tool_result":
                    item = {"type": "tool_result", "call_id": str(block.get("tool_use_id") or ""), "is_error": bool(block.get("is_error"))}
                    item.update(clip(str(block.get("content") or ""), limit))
                else:
                    item = {"type": kind or "unknown"}
                    item.update(clip(str(block.get("text") or ""), limit))
                blocks.append(item)
        out.append({"role": str(message.get("role") or ""), "blocks": blocks})
    return out


def reply_text(snapshot: dict[str, Any], step: Step, limit: int) -> dict[str, Any]:
    """模型这一轮说的那段话 —— 只在产地 A 拿得到，trace 里从来没有正文。"""
    messages = snapshot.get("messages") or []
    index = step.message_count
    if not 0 <= index < len(messages):
        return {"text": "", "chars": 0, "truncated": False, "source": "trace-fingerprint"}
    content = messages[index].get("content")
    joined = "\n".join(
        str(block.get("text") or "")
        for block in (content if isinstance(content, list) else [])
        if isinstance(block, dict) and str(block.get("type")) == "text"
    )
    out = clip(joined, limit, key="text")
    out["source"] = "snapshot"
    return out


def action_of(step: Step, limit: int, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    calls = []
    for call in step.calls:
        name = str(call.get("name") or "")
        item: dict[str, Any] = {
            "name": name,
            "call_id": str(call.get("tool_use_id") or ""),
            "risk": str(call.get("risk") or ""),
            "ok": bool(call.get("ok")),
            "latency_ms": round(float(call.get("latency") or 0.0) * 1000.0, 1),
            "output_chars": int(call.get("output_chars") or 0),
            # 红绿只有 run_tests / bash 给得出（`verdict_of` 挂在这两个工具上）；
            # 其余工具这里是 null，不是 "green" —— 空手回执算奖励会把没验证当成验证过。
            "verification": call.get("verdict") if name in VERIFY_TOOLS else None,
        }
        item.update(clip(json.dumps(call.get("args") or {}, ensure_ascii=False, sort_keys=True), limit, key="args_json"))
        calls.append(item)
    action: dict[str, Any] = {"stop_reason": step.stop_reason, "tool_uses": len(step.calls), "calls": calls}
    action.update(reply_text(snapshot, step, limit) if snapshot is not None else {"text": "", "chars": 0, "truncated": False, "source": "trace-fingerprint"})
    return action


def signals_of(step: Step, labels_at_turn: dict[int, list[str]]) -> dict[str, Any]:
    """每步的过程信号。它们是这份数据里唯一**不靠终局判定**的奖励候选（§3.9 第三层价值）。"""
    seen = [str(call.get("verdict")) for call in step.calls if call.get("verdict")]
    return {
        "verification": "red" if "red" in seen else ("green" if seen else "none"),
        "tool_errors": sum(1 for call in step.calls if not call.get("ok")),
        "denied": step.denied,
        "idempotent_skips": step.replays,
        "compaction_at": step.turn if step.compacted else None,
        "context_refused": step.refused,
        "todos_open": step.todos_open,
        "loop_errors": step.errors,
        "mode_labels": labels_at_turn.get(step.turn, []),
    }


def modes_from_trace(records: Sequence[dict[str, Any]]) -> tuple[list[str], dict[int, list[str]]]:
    """失败模式标签取 trace 里的 `failure_mode` 记录 —— 那是**运行时**分类器写下的，
    带它当时判到的那一轮。在这里重跑一遍 `classify()` 会拿到一份"今天规则下的结论"，
    而规则的阈值改过（`_self_confirm` 收窄过一次），重算出来的标签就不是这条轨迹的标签了。
    """
    at_turn: dict[int, set[str]] = {}
    every: set[str] = set()
    for record in records:
        if record.get("kind") != "failure_mode":
            continue
        label = str(record.get("mode") or "")
        if not label:
            continue
        every.add(label)
        at_turn.setdefault(int(record.get("turn") or 0), set()).add(label)
    return sorted(every), {turn: sorted(labels) for turn, labels in sorted(at_turn.items())}


def export(
    runs: Sequence[RunRecord],
    *,
    out: Path,
    include_failed: bool = True,
    gamma: float = DEFAULT_GAMMA,
    block_chars: int = DEFAULT_BLOCK_CHARS,
    memory_roots: Iterable[Path] = (),
    labels: dict[str, list[str]] | None = None,
    dropped: list[dict[str, Any]] | None = None,
) -> int:
    """trace + 判据 → JSONL，一行一个决策步。返回写出的行数（不是 run 数）。

    `dropped` 给一个列表就往里追加每一次"这条运行没进数据"的原因。丢东西必须留下条数
    和理由：一份只报行数的导出会让人以为输入等于输出。
    """
    roots = list(memory_roots)
    given = labels or {}
    ledger = dropped if dropped is not None else []
    claimed: set[str] = set()
    rows = 0
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with Path(out).open("w", encoding="utf-8") as handle:
        for run in runs:
            rows += _write_run(
                run,
                handle,
                include_failed=include_failed,
                gamma=gamma,
                block_chars=block_chars,
                roots=roots,
                human=given,
                claimed=claimed,
                ledger=ledger,
            )
    return rows


def identity_of(run: RunRecord, path: Path) -> dict[str, Any]:
    """判据那行记下的内容指纹 vs 盘上现在这份。三种状态，没有一种可以混着读：

    * **verified** —— sha 与行数都对得上：这一行的 reward 确实属于手里这份轨迹。
    * **drift** —— 对不上。要么被手工改过，要么被第二次重跑覆盖了（`append_manifest`
      只保证同名键一行，不保证文件从没被换过）。这种 run **不进数据**：轮数校验挡得住
      "少了几轮"，挡不住"同样轮数的另一份轨迹"，而后者配出来的 reward 看起来完全合理。
    * **unverified** —— manifest 写于 §7.3-7 上线之前，没有可比哈希。照旧导出，但每一行
      自述这件事，统计里单列一格：盘上那 12 个历史批次因此不可能被误读成校验过的样本。
    """
    stamp = run.trace_fp if isinstance(run.trace_fp, dict) else None
    rel = str((stamp or {}).get("rel") or "")
    if not stamp or not str(stamp.get("sha") or ""):
        return {
            "status": "unverified",
            "rel": "",
            "lines": 0,
            "sha": "",
            "why": "manifest 这行写于 trace 指纹（SPEC v2 §7.3-7）上线之前，没有可比的内容哈希",
        }
    live = fingerprint(path)
    recorded = str(stamp["sha"])
    if live["sha"] != recorded or int(live["lines"]) != int(stamp.get("lines") or -1):
        return {
            "status": "drift",
            "rel": rel,
            "lines": int(live["lines"]),
            "sha": live["sha"],
            "recorded_lines": int(stamp.get("lines") or 0),
            "recorded_sha": recorded,
            "why": "盘上这份已经不是判据当时看的那份",
        }
    return {
        "status": "verified",
        "rel": rel or path.name,
        "lines": int(live["lines"]),
        "sha": live["sha"],
        "bytes": int(live["bytes"]),
        "why": "",
    }


def _write_run(
    run: RunRecord,
    handle: Any,
    *,
    include_failed: bool,
    gamma: float,
    block_chars: int,
    roots: list[Path],
    human: dict[str, list[str]],
    claimed: set[str],
    ledger: list[dict[str, Any]],
) -> int:
    def drop(code: str, why: str, **extra: Any) -> int:
        ledger.append({"key": key, "code": code, "run": _name_of(run), "engine": run.engine, "verdict": run.verdict, "why": why, **extra})
        return 0

    key = str(run.trace_path or f"{run.task_id}.r{run.repeat}")

    if key in claimed:
        # 重跑覆盖同名 trace，所以同一份轨迹在 manifest 里只能有一行。两处源头都修了：
        # 跑批器的 `append_manifest` 改成按 (task, repeat) 取代旧行，`steps_from` 不再把
        # "只有拒载记录的那一轮"算成决策步。盘上这 12 个批次现在一条都不掉进这里 ——
        # 这条判定留着当哨兵：宁可少一条样本，也不把一份轨迹当成两条带不同 reward 的样本。
        return drop("duplicate-trace", "同一份 trace 已经被前一行认领（重跑覆盖了同名轨迹，manifest 还留着上一代）")
    claimed.add(key)
    terminal = TERMINAL.get(run.verdict)
    if terminal is None:
        return drop("verdict-not-terminal", f"判据是 {run.verdict}，不是 pass/fail —— 我们自己的故障不该进奖励函数")
    if terminal == 0.0 and not include_failed:
        return drop("only-passed", "--only-passed 丢掉了判 fail 的运行")
    path = Path(run.trace_path) if run.trace_path else None
    if path is None or not path.is_file():
        return drop("trace-missing", "manifest 里没有 trace 路径，或者那个文件已经不在了")
    identity = identity_of(run, path)
    if identity["status"] == "drift":
        return drop(
            "trace-drift",
            f"manifest 记 {identity['recorded_sha']} / {identity['recorded_lines']} 行，"
            f"盘上这份是 {identity['sha']} / {identity['lines']} 行 —— 判据与轨迹不是同一份，不进数据",
            recorded_sha=identity["recorded_sha"],
            disk_sha=identity["sha"],
            recorded_lines=identity["recorded_lines"],
            disk_lines=identity["lines"],
        )
    records = replay(path)
    steps = steps_from(records)
    if not steps:
        return drop("no-decision-steps", "trace 里一条 llm_request 都没有（schema 1.x？），没有可对齐的决策步")
    turns = run.metric("turns")
    if turns != len(steps):
        # 判据是从 manifest 拿的，轨迹是从盘上读的，两者轮数不一致 = 这一行的 reward
        # 属于另一条轨迹。这种行进去，训的是我们自己的文件系统竞态。
        return drop("turn-mismatch", f"manifest 记 {turns} 轮，盘上这份 trace 只有 {len(steps)} 轮 —— 判据与轨迹对不上", manifest_turns=turns, trace_steps=len(steps))
    session_id = str(next((record.get("session") for record in records if record.get("session")), "") or "")
    snapshot_path = find_snapshot(session_id, path, roots)
    snapshot: dict[str, Any] | None = None
    reasons: list[str] = []
    if snapshot_path is None:
        reasons = [f"找不到 session {session_id or '?'} 的快照：state 只能给结构指纹（产地 B）。快照是 S13（§3.6）才上线的，之前的批次没有"]
    else:
        loaded = json.loads(snapshot_path.read_text(encoding="utf-8"))
        reasons = pair_snapshot(loaded, steps)
        snapshot = loaded if not reasons else None
        if reasons:
            reasons = [f"快照 {snapshot_path.name} 配不上：{reasons[0]}"] + reasons[1:]
    compacted_at = max((step.turn for step in steps if step.compacted), default=None)
    modes, turn_modes = modes_from_trace(records)
    human_labels = human.get(path.name, []) if path is not None else []
    paired = snapshot is not None
    rows = 0
    previous = 0
    for index, step in enumerate(steps):
        remaining = len(steps) - index - 1
        row = {
            "schema": SCHEMA,
            "row_id": f"{run.task_id}.r{run.repeat}:{session_id}:{step.turn}",
            "task_id": run.task_id,
            "repeat": run.repeat,
            "engine": run.engine,
            "session_id": session_id,
            "step": index,
            "state": {
                "source": "snapshot-prefix" if paired else "trace-fingerprint",
                "reconstructible": paired,
                "pairing_ok": paired,
                "pairing_reasons": reasons,
                # 压缩阶梯改写的就是 history：那之后的前缀不再是模型当时看见的东西。
                # 单列一格而不是混进 pairing_reasons，是因为"配不上"和"配上了但被改写过"
                # 是两件事 —— 后者是数据质量警告，前者是产地判定。
                "broken_by_compaction": (
                    f"turn {compacted_at} 及之后经过压缩阶梯，快照前缀≠模型当时所见"
                    if compacted_at is not None and step.turn >= compacted_at
                    else ""
                ),
                "message_count": step.message_count,
                "est_tokens": step.est_tokens,
                "prefix_hash": step.prefix_hash,
                "tools_offered": step.tools_count,
                "todos_open": step.todos_open,
                "messages_delta": state_delta(snapshot, step, previous, block_chars) if paired else [],
            },
            "action": action_of(step, block_chars, snapshot if paired else None),
            "reward": {
                "verdict": run.verdict,
                "terminal": terminal,
                "gamma": gamma,
                "steps_to_end": remaining,
                # 判负的 run 每一步都是 0：折扣项不该把"失败了"洗成"失败得比较晚所以还行"。
                "value": round(terminal * (gamma ** remaining), 6),
                "termination": _termination(records),
            },
            "signals": signals_of(step, turn_modes),
            "labels": {"rule_modes": modes, "manifest_modes": sorted(run.failure_modes), "human_modes": sorted(human_labels)},
            "provenance": {
                "trace": str(path) if path else None,
                "snapshot": str(snapshot_path) if snapshot_path else None,
                # 判据与轨迹的同一性来源。drift 走不到这里（整行在进数据前就被丢掉），
                # 所以盘上只会出现 verified / unverified 两种 —— 审计据此回读第三态。
                "trace_identity": identity,
                "verdict_source": "eval manifest → RunRecord.verdict",
                "reward_rule": "terminal * gamma ** steps_to_end（SPEC §3.9）",
                "taskset_sha": run.taskset_sha,
            },
        }
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        previous = max(previous, step.message_count + 1)
        rows += 1
    return rows


def _termination(records: Sequence[dict[str, Any]]) -> str:
    for record in reversed(list(records)):
        if record.get("kind") == "run_end" and record.get("termination"):
            return str(record["termination"])
    return ""


def _count_reasons(ledger: Sequence[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in ledger:
        code = str(item.get("code") or "unknown")
        out[code] = out.get(code, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def load_labels(path: Path | None) -> dict[str, list[str]]:
    """§3.2 的 SBS 人工标注：每行 `{"trace": "<文件名>", "modes": [...]}`。"""
    if path is None or not Path(path).is_file():
        return {}
    out: dict[str, list[str]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = str(raw.get("trace") or raw.get("name") or "")
        if name:
            out[Path(name).name] = [str(item) for item in (raw.get("modes") or raw.get("labels") or [])]
    return out


def read_batches(paths: Sequence[Path]) -> list[RunRecord]:
    """从若干批次的 `manifest.jsonl` 读回 RunRecord。跑过的那批才导得出来。

    `base=manifest.parent`：绝对路径指空时（批次目录被拷到别的机器、别的 checkout）用指纹里
    那份相对路径把轨迹找回来。§7.3-7 缺口 ② —— 修之前这些行会整批掉进 `trace-missing`，
    看起来像"数据没了"，其实只是坐标换了。
    """
    runs: list[RunRecord] = []
    for item in paths:
        manifest = Path(item)
        if manifest.is_dir():
            manifest = manifest / "manifest.jsonl"
        if not manifest.is_file():
            raise FileNotFoundError(f"没有 manifest：{manifest}（这一批还没跑过，或者被 .gitignore 扫掉了）")
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                runs.append(RunRecord.from_dict(raw, base=manifest.parent))
    return runs


def preference_pairs(runs: Sequence[RunRecord]) -> list[dict[str, Any]]:
    """成对偏好：同一 (task, repeat) 上判据不同的两次运行。

    这一格的语义要写清楚：它配对的是"换个种子/换个配置重跑一次"，**不是**"同一个上下文里
    两个动作哪个好"。后者才叫 SBS，需要 §3.2 的人工标注，本项目真实数据里目前一条没有 ——
    导出时按 0 报，不拿合成对凑数。
    """
    groups: dict[tuple[str, int], list[RunRecord]] = {}
    seen: set[str] = set()
    for run in runs:
        key = str(run.trace_path or f"{run.task_id}.r{run.repeat}")
        if run.verdict not in TERMINAL or key in seen:
            continue
        seen.add(key)
        groups.setdefault(run.key, []).append(run)
    pairs: list[dict[str, Any]] = []
    for (task_id, repeat), group in sorted(groups.items()):
        for winner in [run for run in group if run.verdict == "pass"]:
            for loser in [run for run in group if run.verdict == "fail"]:
                pairs.append({
                    "kind": "same-task-different-run",
                    "task_id": task_id,
                    "repeat": repeat,
                    "chosen": _name_of(winner),
                    "rejected": _name_of(loser),
                    "chosen_steps": winner.metric("turns"),
                    "rejected_steps": loser.metric("turns"),
                    "chosen_tokens": winner.tokens,
                    "rejected_tokens": loser.tokens,
                })
    return pairs


def _name_of(run: RunRecord) -> str:
    """`bh-lint.r0.fake.jsonl` → `bh-lint.r0.fake`：够追到 trace，又不把整条绝对路径写进数据。
    引擎后缀要留着 —— 同一道题的两个对照臂只差它，掉了就成了两个名字指两条不同的轨迹。
    """
    name = Path(run.trace_path).name if run.trace_path else f"{run.task_id}.r{run.repeat}"
    return name[: -len(".jsonl")] if name.endswith(".jsonl") else name


def audit(out: Path, block_chars: int) -> dict[str, Any]:
    """回读刚写出去的文件，检查它自己还说得出产地。

    导出时顺手自证是**不够**的 —— 那等于被考核者自己填表。这里只读盘上的 JSONL：
    密钥有没有漏进去、reward 是不是只有两种终局、步数连不连续、行不行的自述对不对。
    """
    lines = [line for line in out.read_text(encoding="utf-8").splitlines() if line.strip()] if out.is_file() else []
    parsed = [json.loads(line) for line in lines]
    blob = "\n".join(lines)
    steps_per_run: dict[str, list[int]] = {}
    for row in parsed:
        steps_per_run.setdefault(str(row["provenance"]["trace"]), []).append(int(row["step"]))
    paired_rows = [row for row in parsed if row["state"]["reconstructible"]]
    terminals = sorted({str(row["reward"]["verdict"]) for row in parsed})
    blocks: list[dict[str, Any]] = []
    for row in parsed:
        blocks.append(row["action"])
        blocks.extend(row["action"]["calls"])
        blocks.extend(block for message in row["state"]["messages_delta"] for block in message["blocks"])
    over_cap = [row for row in blocks if int(row.get("chars") or 0) > block_chars and not row.get("truncated")]
    unpaired = [row for row in parsed if not row["state"]["reconstructible"]]
    rows_with_body = [
        row for row in parsed if row["state"]["messages_delta"] or row["action"].get("source") == "snapshot"
    ]
    # 增量导出的自证：第 i 行的增量条数 == 上下文长了多少，再减去模型自己那条回复。
    # 这条只吃盘上的 message_count 与 delta 长度，所以 `previous` 游标一旦漂移就会响。
    delta_ok = True
    last_count: dict[str, int] = {}
    for row in paired_rows:
        session, index = str(row["session_id"]), int(row["step"])
        count = int(row["state"]["message_count"])
        expect = count if index == 0 else count - last_count[session] - 1
        if expect != len(row["state"]["messages_delta"]):
            delta_ok = False
        last_count[session] = count
    paired_sessions = len({str(row["session_id"]) for row in paired_rows})
    run_heads = [row for row in parsed if int(row["step"]) == 0]
    trace_dupes = len(run_heads) - len({str(row["provenance"]["trace"]) for row in run_heads})
    identities = [row["provenance"].get("trace_identity") for row in parsed]
    verified = [row for row in parsed if (row["provenance"].get("trace_identity") or {}).get("status") == "verified"]
    unverified = [row for row in parsed if (row["provenance"].get("trace_identity") or {}).get("status") == "unverified"]
    # 同一性回读：只信盘上现在这份。导出时"对得上"不等于写完就没人动过 —— 这份 JSONL 是
    # 要给日后的人看的，而轨迹此刻还在原处，所以顺手重算一次哈希是免费的。改过一行就响。
    # 按**文件**去重再回读：一个 run 有 n 行、每行都重复了同一个指纹，逐行数会把
    # "一份轨迹被改过"报成 n 份，而细节里那份文件名只会出现一次。
    stamps: dict[str, dict[str, Any]] = {}
    for row in verified:
        trace = str(row["provenance"].get("trace") or "")
        stamps.setdefault(trace, row["provenance"]["trace_identity"])
    drift_after: list[str] = []
    for trace, stamp in stamps.items():
        try:
            live = fingerprint(Path(trace))
        except OSError as exc:
            drift_after.append(f"{Path(trace).name}：读不到了（{type(exc).__name__}）")
            continue
        if live["sha"] != stamp["sha"] or live["lines"] != stamp["lines"]:
            drift_after.append(
                f"{Path(trace).name}：导出时 {stamp['sha']}/{stamp['lines']} 行，现在 {live['sha']}/{live['lines']} 行"
            )
    premises = [
        {
            "claim": "每一行都有 state / action / reward 三格，且 row_id 唯一",
            "ok": bool(parsed)
            and all({"state", "action", "reward"} <= set(row) for row in parsed)
            and len({row.get("row_id") for row in parsed}) == len(parsed),
            "detail": f"{len(parsed)} 行 · row_id 去重后 {len({row.get('row_id') for row in parsed})} 个",
        },
        {
            "claim": "reward 只有 pass/fail 两种终局（error / aborted 不进数据）",
            "ok": bool(parsed) and set(terminals) <= {"pass", "fail"},
            "detail": f"出现的终局：{terminals or '无'}",
        },
        {
            "claim": "凡带正文的行都通过了逐轮 tool_use id 配对",
            "ok": all(row["state"]["reconstructible"] for row in rows_with_body),
            "detail": f"有正文的行 {len(rows_with_body)} · reconstructible 的行 {len(paired_rows)}",
        },
        {
            "claim": "只有指纹的行都带着「为什么没有正文」",
            "ok": all(row["state"]["pairing_reasons"] for row in unpaired),
            "detail": f"指纹行 {len(unpaired)} 份，全部带 pairing_reasons",
        },
        {
            "claim": "导出文件里没有形如密钥的东西（扫的是盘上那份，不是导出时的内存）",
            "ok": not _SECRET_LIKE.search(blob),
            "detail": f"盘上命中 {len(_SECRET_LIKE.findall(blob))} 处 · 用的就是 `_scrub_value` 那条正则：它会遮的东西，一条都不该留在盘上",
        },
        {
            "claim": "每个 run 的步数是连续的 0..n-1（没有静默丢轮）",
            "ok": bool(parsed) and all(sorted(rows) == list(range(len(rows))) for rows in steps_per_run.values()),
            "detail": f"{len(steps_per_run)} 个 run · 前六个 run 的步数 {sorted((len(rows) for rows in steps_per_run.values()))[:6]}",
        },
        {
            "claim": "增量导出不重不漏：delta 条数 == message_count 的增量 − 1（那一条是模型自己的回复）",
            "ok": None if not paired_rows else delta_ok,
            "detail": f"{paired_sessions} 个产地 A 的 session · 逐行核对 {len(paired_rows)} 行",
        },
        {
            "claim": "每份 trace 只被一个 run 认领（重跑覆盖同名轨迹的那些没被当成两条样本）",
            "ok": not trace_dupes,
            "detail": f"{len(run_heads)} 个 run 头 · 同一份 trace 重复认领 {trace_dupes} 次",
        },
        {
            "claim": "超限的块一律留了截断标记",
            "ok": not over_cap,
            "detail": f"上限 {block_chars} 字符/块 · 超而未标记 {len(over_cap)} 块 · 已标记截断 {sum(1 for row in blocks if row.get('truncated'))} 块",
        },
        {
            # 每一行必须说清"判据凭什么是这条轨迹"。第三种状态不该出现在盘上：
            # drift 在进数据前就整行被丢掉并记账，出现在这里说明有一道判定漏了。
            "claim": "每一行都自述判据与轨迹的同一性来源，且没有一行带着未校验的指纹冒充已校验",
            "ok": bool(parsed)
            and all((item or {}).get("status") in ("verified", "unverified") for item in identities)
            and all((item or {}).get("sha") for item in identities if (item or {}).get("status") == "verified")
            and all((item or {}).get("why") for item in identities if (item or {}).get("status") == "unverified"),
            "detail": f"verified {len(verified)} 行 · unverified {len(unverified)} 行 · 两者之外 {len(parsed) - len(verified) - len(unverified)} 行",
        },
        {
            "claim": "凡 verified 的行，盘上那份 trace 现在仍是判据当时看的那份（导出之后再被改动也响）",
            "ok": None if not verified else not drift_after,
            "detail": (
                f"{len(stamps)} 份轨迹（{len(verified)} 行）逐个回读 sha + 行数 · "
                f"不符 {len(drift_after)}" + (f"：{drift_after[0]}" if drift_after else "")
            )
            + ("" if verified else f" · 本批 {len(unverified)} 行全部写于指纹上线前，没有可回读的对象"),
        },
    ]
    return {
        "rows": len(parsed),
        "runs_paired": sum(1 for row in parsed if row["state"]["reconstructible"] and int(row["step"]) == 0),
        "runs_fingerprint": sum(1 for row in parsed if not row["state"]["reconstructible"] and int(row["step"]) == 0),
        "truncated_blocks": sum(1 for row in blocks if row.get("truncated")),
        "rows_verified": len(verified),
        "rows_unverified": len(unverified),
        "bytes": out.stat().st_size if out.is_file() else 0,
        "premises": premises,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcc export-rl", description="SPEC v2 §3.9：trace + 判据 → (state, action, reward) JSONL")
    parser.add_argument("--batch", action="append", default=[], help="批次目录或 manifest.jsonl，可给多次")
    parser.add_argument("--out", type=Path, required=True, help="导出的 JSONL 路径")
    parser.add_argument("--only-passed", action="store_true", help="丢掉判 fail 的 run（默认保留 —— 失败轨迹是回归集的种子）")
    parser.add_argument("--gamma", type=float, default=DEFAULT_GAMMA, help="折扣系数；1.0 = 只看终局")
    parser.add_argument("--block-chars", type=int, default=DEFAULT_BLOCK_CHARS, help="单块正文的截断长度")
    parser.add_argument("--memory-root", action="append", default=[], help="额外找会话快照的目录，可给多次")
    parser.add_argument("--labels", type=Path, default=None, help="§3.2 的 SBS 人工标注 JSONL")
    parser.add_argument("--evidence", type=Path, default=None, help="把统计与前提写成一份 JSON")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else [])
    if not args.batch:
        print("至少给一个 --batch（跑过的批次目录）。没有输入就没有数据。", file=sys.stderr)
        return 2
    try:
        runs = read_batches([Path(item) for item in args.batch])
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"读批次失败：{exc}", file=sys.stderr)
        return 2
    labels = load_labels(args.labels)
    ledger: list[dict[str, Any]] = []
    started = time.perf_counter()
    rows = export(
        runs,
        out=args.out,
        include_failed=not args.only_passed,
        gamma=args.gamma,
        block_chars=args.block_chars,
        memory_roots=[Path(item) for item in args.memory_root],
        labels=labels,
        dropped=ledger,
    )
    stats = audit(args.out, args.block_chars)
    # 偏好对只能建在真进了数据的 run 上：拿被丢掉的那批配对，等于用同一份轨迹给自己当对照。
    dropped_keys = {str(item.get("key") or "") for item in ledger}
    kept = [run for run in runs if str(run.trace_path or f"{run.task_id}.r{run.repeat}") not in dropped_keys]
    pairs = preference_pairs(kept)
    premises = stats["premises"]
    # 这一条只能由调用方补：账外的那部分（被丢弃的 run）压根不在盘上的 JSONL 里。
    exported = stats["runs_paired"] + stats["runs_fingerprint"]
    premises.append(
        {
            "claim": "账对得上：输入 run 数 == 进导出的 run 数 + 被丢弃的 run 数",
            "ok": len(runs) == exported + len(ledger),
            "detail": f"输入 {len(runs)} = 进数据 {exported} + 丢弃 {len(ledger)}（{_count_reasons(ledger) or '无'}）",
        }
    )
    counts = {
        "premises": len(premises),
        "passed": sum(1 for item in premises if item["ok"] is True),
        "failed": sum(1 for item in premises if item["ok"] is False),
        "not_measured": sum(1 for item in premises if item["ok"] is None),
    }
    payload = {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "acceptance": "S15-b（SPEC v2 §3.9）",
        "spec": "§3.9 / D23",
        "question": "盘上真实跑过的轨迹能导成什么形状的 RL 数据，以及**缺**哪一块",
        "inputs": {"batches": list(args.batch), "runs_in": len(runs), "human_labels": len(labels)},
        "out": str(args.out),
        "rows": rows,
        "dropped_runs": {
            "count": len(ledger),
            "by_reason": _count_reasons(ledger),
            # 逐条列全：这批盘上的 manifest 会被后续批次覆盖，这份账可能就是唯一记录。
            "detail": ledger[:_LEDGER_CAP],
            "detail_truncated": len(ledger) > _LEDGER_CAP,
        },
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "gamma": args.gamma,
        "block_chars": args.block_chars,
        "include_failed": not args.only_passed,
        "audit": {key: value for key, value in stats.items() if key != "premises"},
        "premises": premises,
        "counts": counts,
        "preference_pairs": {
            "same-task-different-run": len(pairs),
            "sbs-human": len(labels),
            "pairs": pairs[:12],
            "note": "第一格配的是「换配置/换种子重跑」，不是「同一上下文里两个动作哪个好」。后者要 §3.2 的人工标注。",
        },
        "what_this_proves": (
            "每一行都能追到一条 trace 记录 + 一份判据；state 的产地是**可判定**的（有快照、"
            "且逐轮 tool_use id 对得上才给正文，对不上就整份不给）；reward 只有 pass/fail 两种终局；"
            "判据与轨迹轮数不一致、同一份 trace 被重复认领的那些，是在导出前就被丢掉并记账的。"
            "自 §7.3-7 起还多一件：verified 的行带着 manifest 当时盖的内容指纹，join 之前先比对，"
            "对不上就是 `trace-drift`、整行不进数据；指纹上线之前的行一律标 unverified，不冒充校验过。"
        ),
        "what_this_does_not_prove": (
            "不证明这批数据够训模型 —— 它首先证明的是**不够**：trace 按设计不含观测正文"
            "（§3.1 为了日志体积、也为了不把仓库内容抄进日志），所以 state 的正文只能来自 §3.6 的"
            "会话快照，而快照是 S13 才上线的 —— 那之前的批次结构性地只有指纹。要拿这个 agent 做"
            "真·后训练，第一步不是训，是让 trace 把 observation 落盘：那是 §3.1 没覆盖的改动，"
            "代价与隐私边界都得重开一次评估。第二件事不在**这批**数据里，但它是上一批的真实经历："
            "manifest 与 trace 曾不是一一对应（见 amendments 第 4 条），重跑过的批次里早先那几行指向的"
            "是别人的轨迹，那部分**同一道题的重跑数据已经不可复原** —— 现在盘上这 12 个批次量到的是"
            "0 个 run 进不了导出，因为两处源头都修了；修不了的是历史。"
        ),
        "amendments": [
            {
                "item": "§3.9 的文件路径",
                "spec_said": "# eval/export_rl.py",
                "as_built": "src/miniclaude/eval/export_rl.py，另有 `mcc export-rl` 入口",
                "why": "`eval/` 在这个项目里是数据目录（tasks / fixtures / results / baselines），"
                "评测代码全部在包里；放进数据目录会多出一条「代码在哪儿」的分叉",
            },
            {
                "item": "§3.9 的一条样本 = (state, action, reward)",
                "spec_said": "每行一条 (state, action, reward)",
                "as_built": "一行一个**决策步**（锚在 llm_request），run 级信息（判据、失败模式、"
                "trace 路径）在每一行里重复一遍",
                "why": "步级才是 RL 的单位；run 级 join 要下游自己做，而重复几十字段的代价远小于"
                "让每个下游各写一遍 join",
            },
            {
                "item": "§3.9 的 labels.jsonl",
                "spec_said": "SBS 人工标注提供成对偏好信号",
                "as_built": f"接口实现（--labels）。盘上人工标注 {len(labels)} 条；"
                f"合成对 {len(pairs)} 对，其判据来自 manifest 而非人工",
                "why": "手写剧本的 16 条核对表核对的是分类器、不是两个动作谁更好；拿它当 SBS 是在"
                "把「我审过代码」写成「人标过偏好」",
            },
            {
                "item": "trace 与 manifest 的对应关系",
                "spec_said": "trace + 判据 → 每行一条 (state, action, reward)（默认两者一一可 join）",
                "as_built": "join 前先按 trace 路径去重、再按轮数校验，不通过的整条不进数据并记账"
                f"（这次丢了 {len(ledger)} 个 run：{_count_reasons(ledger) or '无'}）",
                "why": "trace 文件名按 (task, repeat, engine) 定，重跑是**覆盖**上一份，而当时的 "
                "manifest 是纯 append 写出来的。所以重跑过的批次里，早先那些行指向的已经是别人的轨迹 "
                "—— 直接 join 会把一份轨迹当成两条带不同 reward 的样本。两个源头都修了：runner 的 "
                "append_manifest 改成按 (task, repeat) 取代旧行；`steps_from` 不再把「只有拒载记录的那一轮」 "
                "算成决策步（循环先 `turn += 1` 再算预算，那一格的号比 manifest 大 1，于是死在 L3 线上的 "
                "run 整批被 `turn-mismatch` 丢掉）。本条记的是修之前那批数据的既有事实：那次量到 28 个 "
                "run 进不了导出",
            },
            {
                "item": "§7.3-7 的同一性指纹形状",
                "spec_said": "在 manifest 行里补 trace 的「行数 + 末行哈希」，导出器与 `mcc eval --baseline` 都改成先校验再 join",
                "as_built": "全文件 sha256 前 16 位 + 行数 + 字节数 + 批次内相对路径；校验只在导出器做",
                "why": "末行哈希查不出中间行被改过，而缺口 ① 点名的正是「手工改动」；为了数行数本来"
                "就要读一遍文件，多算一次全文件哈希的边际成本是 0。`--baseline` 那半边没有可校验的对象："
                "基线文件按 §3.2 的规矩**根本不存 trace 路径**（`regression.baseline_from_report`），它 join 的是"
                "任务 id → 判据/轮数/token，不读轨迹 —— 给它加一道 trace 校验要先给它一个它刻意没有的依赖",
            },
        ],
    }
    marks = {True: "x", False: "!", None: "?"}
    for item in premises:
        print(f"  [{marks[item['ok']]}] {item['claim']} —— {item['detail']}")
    print(
        f"\n导出 {rows} 行 / {stats['bytes']:,} 字节 → {args.out}｜输入 {len(runs)} 次运行："
        f"产地 A（有快照且配对）{stats['runs_paired']} · 产地 B（只有指纹）{stats['runs_fingerprint']}"
        f" · 丢弃 {len(ledger)}"
    )
    print(
        f"成对偏好：同题重跑 {payload['preference_pairs']['same-task-different-run']} 对 · "
        f"SBS 人工标注 {len(labels)} 条"
    )
    print(
        f"同一性：verified {stats['rows_verified']} 行（sha 与 manifest 一致）· "
        f"unverified {stats['rows_unverified']} 行（写于指纹上线前）· "
        f"drift {len([item for item in ledger if item['code'] == 'trace-drift'])} 个 run 被挡在数据外"
    )
    print(f"前提：{counts['passed']}/{counts['premises']} 成立（不成立 {counts['failed']} · 未量 {counts['not_measured']}）")
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"证据：{args.evidence}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
