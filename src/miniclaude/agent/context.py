"""上下文管理 —— v2 起既观测也干预（SPEC v2 §3.3）。

v1 刻意只观测：压缩会改变模型看到的历史，配对一坏端点就 400，排查成本高于
"这次任务太大"的一次明确失败。有了 `assert_pairing` 之后，这个风险变成**可以先检查
再采用**的东西 —— 压缩于是从"不敢做"变成"可验证地做"。

两个旋钮，不是一根线（§0.4 实测的直接后果）：

| 旋钮 | 默认 | 管什么 |
|---|---|---|
| `budget`（`TOKEN_BUDGET`） | 32,000 | 成本与注意力质量预算，**压缩阶梯挂它** |
| `hard_limit`（`CONTEXT_HARD_LIMIT`） | 200,000 | 只防一件事：请求被端点拒收（实测窗口 ≥ 270,570） |

混在一个数里会两头不成立：120,000 既不是真实边界，也不是合理预算 —— v1 全部 live
轨迹峰值仅 12,185，压力 0.10，阶梯会是永不触发的死代码。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Literal, Sequence

from miniclaude.config import DEFAULTS
from miniclaude.messages import (
    Message,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    pairing_problems,
)

Level = Literal["elide", "summarize"]

# 阶梯触发点，全部是 `pressure = est / budget` 上的比例。
L1_ELIDE_PRESSURE = 0.70
L1_TARGET_PRESSURE = 0.65      # 压到这里才收手：留缓冲，避免下一轮又超
L2_SUMMARIZE_PRESSURE = 0.85
L3_REFUSE_PRESSURE = 0.95
HARD_FUSE_RATIO = 0.90         # 独立熔断：est ≥ 0.9 × hard_limit 就无条件止损

_WARN_PRESSURE = 0.8
_STOP_PRESSURE = L3_REFUSE_PRESSURE

_ELISION_TAG = "[elided:"
_SUMMARY_TAG = "[上下文已压缩"

# 只读工具：它们的结果从磁盘重读就能拿回来，所以 L1 省略它们几乎免费。
_REREADABLE = {"read_file", "search_text", "find_files", "bash", "run_tests"}


@dataclass
class CompactReport:
    """一次压缩的结果。

    `messages` 是**新列表**：调用方只在 `pairing_ok` 为真时才替换自己的历史。
    这个字段是 as-built 加的 —— SPEC 里的 `CompactReport` 不带压缩结果本身，
    那调用方拿到"压成功了"也无从下手。
    """

    level: str
    before_est: int
    after_est: int
    messages: list[Message] = field(default_factory=list)
    elided_blocks: int = 0
    dropped_blocks: int = 0
    pairing_ok: bool = True
    summary_message: Message | None = None
    summary_tokens: int = 0            # L2 不免费：这次摘要自己烧了多少
    summary_files: tuple[str, ...] = ()  # 替换后的摘要消息里真留下的文件名 —— B2 的 grep 判据读这个
    note: str = ""

    @property
    def saved(self) -> int:
        return max(0, self.before_est - self.after_est)

    @property
    def changed(self) -> bool:
        return self.elided_blocks > 0 or self.dropped_blocks > 0

    def as_trace(self) -> dict[str, Any]:
        """进 trace 的字段。`context_compact` 事件的口径以这里为准。"""
        return {
            "level": self.level,
            "before_est": self.before_est,
            "after_est": self.after_est,
            "saved_est": self.saved,
            "elided_blocks": self.elided_blocks,
            "dropped_blocks": self.dropped_blocks,
            "pairing_ok": self.pairing_ok,
            "summary_tokens": self.summary_tokens,
            "summary_files": list(self.summary_files),
            "note": self.note,
        }


class ContextManager:
    """估算这轮请求要发多少 token，用端点回的真实 usage 校准系数，并在超压时压缩。"""

    def __init__(
        self,
        *,
        budget: int = DEFAULTS["TOKEN_BUDGET"],
        hard_limit: int = DEFAULTS["CONTEXT_HARD_LIMIT"],
        chars_per_token: float = 3.5,
        enabled: bool = True,
        keep_recent_rounds: int = 1,
    ) -> None:
        self.budget = budget
        self.hard_limit = hard_limit
        self.chars_per_token = chars_per_token
        self.enabled = enabled
        # 最近 N 个轮组保持全量：模型正在依据的那份观察不能被抹，抹了它就开始瞎猜。
        self.keep_recent_rounds = max(1, keep_recent_rounds)
        self.last_actual_prompt_tokens = 0
        self.compactions = 0
        self.elided_total = 0
        self.summaries = 0
        self.summary_tokens_total = 0

    # ------------------------------------------------------------ 估算

    def wire_chars(
        self, *, system: str, tools: Iterable[Any] = (), messages: Iterable[Message] = ()
    ) -> int:
        """按**实际要发出去的报文规模**计字符数，包含 system 与工具声明。

        校准必须用同一个口径，否则 estimate 与真实 prompt_tokens 对不上，
        系数会被越校越偏。
        """
        total = len(system)
        for spec in tools:
            total += len(getattr(spec, "name", "")) + len(getattr(spec, "description", ""))
            total += len(str(getattr(spec, "input_schema", "")))
        for message in messages:
            for block in message.content:
                if isinstance(block, TextBlock):
                    total += len(block.text)
                elif isinstance(block, ToolUseBlock):
                    total += len(block.name) + len(str(block.input))
                elif isinstance(block, ToolResultBlock):
                    total += len(block.content)
        return total

    def estimate(
        self, *, system: str = "", tools: Iterable[Any] = (), messages: Iterable[Message] = ()
    ) -> int:
        return self.estimate_from_chars(self.wire_chars(system=system, tools=tools, messages=messages))

    def estimate_from_chars(self, chars: int) -> int:
        """已经有字符数时别再扫一遍历史 —— 一轮里估算三次是白烧 CPU。"""
        return int(chars / self.chars_per_token)

    def pressure_from_chars(self, chars: int) -> float:
        if self.budget <= 0:
            return 0.0
        return self.estimate_from_chars(chars) / self.budget

    def pressure(
        self, *, system: str = "", tools: Iterable[Any] = (), messages: Iterable[Message] = ()
    ) -> float:
        return self.pressure_from_chars(self.wire_chars(system=system, tools=tools, messages=messages))

    def calibrate(self, actual_prompt_tokens: int, sent_chars: int) -> None:
        """用一次真实往返的 usage 修正系数（取滑动平均，避免单次抖动）。

        中文字符/token 比英文代码低得多，固定 3.5 对中文仓库会严重低估，
        所以每轮都校一次。actual 里还含 system 与工具声明的 token，
        因此算出的系数天然偏保守 —— 这正是我们想要的方向。
        """
        if actual_prompt_tokens <= 0 or sent_chars <= 0:
            return
        self.last_actual_prompt_tokens = actual_prompt_tokens
        observed = sent_chars / actual_prompt_tokens
        self.chars_per_token = max(1.2, min(12.0, 0.7 * self.chars_per_token + 0.3 * observed))

    # ------------------------------------------------------------ 两个旋钮各管各的

    @property
    def warn_pressure(self) -> float:
        return _WARN_PRESSURE

    @property
    def stop_pressure(self) -> float:
        return _STOP_PRESSURE

    def should_warn(
        self, *, system: str = "", tools: Iterable[Any] = (), messages: Iterable[Message] = ()
    ) -> bool:
        return self.pressure(system=system, tools=tools, messages=messages) >= _WARN_PRESSURE

    def should_stop(
        self, *, system: str = "", tools: Iterable[Any] = (), messages: Iterable[Message] = ()
    ) -> bool:
        return self.pressure(system=system, tools=tools, messages=messages) >= _STOP_PRESSURE

    def over_hard_limit(self, est_tokens: int) -> bool:
        """与阶梯完全无关的一条线：到这里别再想压缩了，直接止损。

        压缩要花时间、L2 还要一次 LLM 调用；请求已经大到必然被拒时这些都不该发生。
        """
        return self.hard_limit > 0 and est_tokens >= HARD_FUSE_RATIO * self.hard_limit

    # ------------------------------------------------------------ 阶梯

    def level_for_pressure(self, pressure: float) -> Level | None:
        """这个压力该做哪一档。L3 不是一种动作，是"阶梯已经救不动"的判定，
        由调用方在跑完整条阶梯之后自己下结论。
        """
        if not self.enabled or self.budget <= 0:
            return None
        if pressure >= L2_SUMMARIZE_PRESSURE:
            return "summarize"
        if pressure >= L1_ELIDE_PRESSURE:
            return "elide"
        return None

    def compact(
        self,
        *,
        system: str,
        tools: Iterable[Any] = (),
        messages: Sequence[Message],
        level: Level,
        summarizer: Callable[[list[Message], str], tuple[str, int]] | None = None,
    ) -> CompactReport:
        """按 `level` 压一档，返回带**新消息列表**的报告；绝不原地改调用方的历史。

        `summarizer(dropped, goal) -> (文本, 消耗 token)` 只有 L2 需要：把"发一次请求"
        留在 loop 里，这个模块就不用认识 LLM 客户端。
        """
        specs = list(tools)
        before = self.estimate(system=system, tools=specs, messages=messages)
        # base 只带两个恒定字段：`messages` 每一层都要显式给，混在一起传会漏改。
        base = {"level": level, "before_est": before}
        if not self.enabled:
            return CompactReport(**base, after_est=before, messages=list(messages), note="压缩已关闭")
        if self.budget <= 0 or before <= L1_TARGET_PRESSURE * self.budget:
            return CompactReport(**base, after_est=before, messages=list(messages), note="已在目标线以下")

        if level == "elide":
            report = self._compact_elide(system=system, tools=specs, messages=messages, base=base)
        elif level == "summarize":
            if summarizer is None:
                raise ValueError("level=summarize 需要 summarizer 回调")
            report = self._compact_summarize(
                system=system, tools=specs, messages=messages, summarizer=summarizer, base=base
            )
        else:  # pragma: no cover - Literal 已穷举
            raise ValueError(f"未知的压缩层：{level!r}")

        if report.changed and report.pairing_ok:
            self.compactions += 1
            self.elided_total += report.elided_blocks
            self.summaries += 1 if report.dropped_blocks else 0
            self.summary_tokens_total += report.summary_tokens
        return report

    def run_ladder(
        self,
        *,
        system: str,
        tools: Iterable[Any],
        messages: Sequence[Message],
        summarizer: Callable[[list[Message], str], tuple[str, int]] | None = None,
    ) -> list[CompactReport]:
        """逐级往上压：L1 永远先做，只有它救不回触发线才付 L2 那次 LLM 调用。

        为什么 L1 无条件先跑，哪怕压力已经越过 L2 的线：它零额外调用、零信息丢失
        风险（内容能从磁盘重读），实测能吃掉 60–80% 的超额。先付免费的账。

        返回**每一层**的报告（包括没动作的那层）：loop 按层写 trace，报表要能回答
        "L2 到底试没试过"。只返回最后一条就答不了 —— 那是 v1 度量谎报的老路。
        """
        specs = list(tools)
        reports: list[CompactReport] = []
        current = list(messages)
        for level, trigger in (("elide", L1_ELIDE_PRESSURE), ("summarize", L2_SUMMARIZE_PRESSURE)):
            if self.pressure(system=system, tools=specs, messages=current) < trigger:
                continue   # 这层还轮不到；后面那层的线更高，自然也到不了
            report = self.compact(
                system=system, tools=specs, messages=current, level=level, summarizer=summarizer
            )
            reports.append(report)
            if not report.pairing_ok:
                break      # 上次压缩已经证明会压坏，别再往上加动作
            if report.changed:
                current = report.messages
        return reports

    # ------------------------------------------------------------ L1：只省工具输出

    def _compact_elide(
        self, *, system: str, tools: list[Any], messages: Sequence[Message], base: dict[str, Any]
    ) -> CompactReport:
        """L1：把老轮次的工具输出换成一行说明。零额外调用、零信息丢失风险。

        内容都能从磁盘重读，所以"丢"的代价是一次重新读取；消息数量完全不变 ——
        这是唯一不可能破坏配对的一层。
        """
        total_chars = self.wire_chars(system=system, tools=tools, messages=messages)
        target = L1_TARGET_PRESSURE * self.budget * self.chars_per_token
        labels = _call_labels(messages)
        protected = _protection_start(messages, self.keep_recent_rounds)
        out: list[Message] = []
        elided = 0

        for index, message in enumerate(messages):
            if index >= protected or total_chars <= target or not message.results:
                out.append(message)
                continue
            blocks: list[Any] = []
            for block in message.content:
                marker = _elision_marker(labels.get(block.tool_use_id, ("tool", "")), len(block.content))
                keep = (
                    not isinstance(block, ToolResultBlock)
                    or block.content.startswith(_ELISION_TAG)
                    # 标记本身比输出还长时就别换：L1 的意义是省，换成更长的字符串
                    # 是反向操作，短输出（"ok"、一行退出码）恰恰最容易撞上。
                    or len(marker) >= len(block.content)
                )
                if keep:
                    blocks.append(block)
                    continue
                total_chars += len(marker) - len(block.content)
                elided += 1
                blocks.append(replace(block, content=marker))
            out.append(replace(message, content=blocks))

        report = CompactReport(
            **base,
            after_est=self.estimate_from_chars(total_chars),
            elided_blocks=elided,
            messages=out,
            note="" if elided else "没有可省略的老输出",
        )
        return self._guard_pairing(report, base, messages)

    # ------------------------------------------------------------ L2：整组换成摘要

    def _compact_summarize(
        self,
        *,
        system: str,
        tools: list[Any],
        messages: Sequence[Message],
        summarizer: Callable[[list[Message], str], tuple[str, int]],
        base: dict[str, Any],
    ) -> CompactReport:
        """L2：把最老的一批轮组整组换成一条摘要消息。

        只能整组删：一个 assistant 的 tool_calls 与它的结果属于同一轮组，半删必炸 ——
        这条纪律不靠自觉，由返回前的 `_guard_pairing` 检查。
        """
        goal = next((m.text() for m in messages if m.role is Role.USER and m.text()), "")
        head, dropped, tail = self._split_for_summary(messages)
        if not dropped:
            return CompactReport(
                **base,
                after_est=base["before_est"],
                messages=list(messages),
                note="老历史只剩一轮，没有可摘要的内容",
            )

        text, tokens = summarizer(dropped, goal)
        digest, kept = _local_digest(dropped)
        first, last = _turn_bounds(head, dropped)
        body = f"{_SUMMARY_TAG} · 第 {first}–{last} 轮]"
        if text.strip():
            body += "\n" + text.strip()
        if digest:
            # 本地统计永远附在最后：`已改动文件` 丢了，agent 就会重写已有文件。
            body += ("\n\n" if text.strip() else "\n") + digest
        summary = Message.user_text(body)
        out = [*head, summary, *tail]

        report = CompactReport(
            **base,
            after_est=self.estimate(system=system, tools=tools, messages=out),
            dropped_blocks=sum(len(m.content) for m in dropped),
            messages=out,
            summary_message=summary,
            summary_tokens=tokens,
            summary_files=tuple(kept),
            note="" if text.strip() else "摘要器只给了占位文本，纪要里只剩本地改动清单",
        )
        return self._guard_pairing(report, base, messages)

    def _split_for_summary(
        self, messages: Sequence[Message]
    ) -> tuple[list[Message], list[Message], list[Message]]:
        """切成 保留头 / 压掉的中段 / 保留尾，**只按轮组边界切**。

        头部保留的是用户原始任务（第一条不带工具调用的 user 消息）：压掉它等于让
        agent 失去目标，摘要也补不回来。中段少于两个轮组时不压 —— 把唯一的现场
        变成摘要，模型就只剩下"我记得我做过"这种自述可看了。
        """
        groups = _round_groups(messages)
        head_groups: list[list[Message]] = []
        rest = list(groups)
        while rest and not any(message.tool_uses for message in rest[0]):
            head_groups.append(rest.pop(0))
        if len(rest) < 3:
            return list(messages), [], []

        tail_count = min(max(2, self.keep_recent_rounds + 1), len(rest) - 2)
        middle = rest[: len(rest) - tail_count]
        tail_groups = rest[len(rest) - tail_count :]
        dropped = [message for group in middle for message in group]
        tail = [message for group in tail_groups for message in group]
        head = [message for group in head_groups for message in group]
        return head, dropped, tail

    # ------------------------------------------------------------ 共用收尾

    def _guard_pairing(
        self, report: CompactReport, base: dict[str, Any], original: Sequence[Message]
    ) -> CompactReport:
        """配对坏了就放弃这次压缩，把**原历史**原样交回去。

        `CompactReport.pairing_ok` 之所以敢"恒真"，不是因为不检查，而是因为检查过：
        压缩改坏历史还继续发出去，代价是一次 400 加一整批废掉的评测。
        """
        problems = pairing_problems(report.messages)
        if problems:
            return CompactReport(
                **base,
                after_est=base["before_est"],
                messages=list(original),
                pairing_ok=False,
                note="压缩后配对破损，已放弃本次压缩：" + "；".join(problems[:3]),
            )
        return report

    def snapshot(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "hard_limit": self.hard_limit,
            "enabled": self.enabled,
            "chars_per_token": round(self.chars_per_token, 2),
            "last_actual_prompt_tokens": self.last_actual_prompt_tokens,
            "compactions": self.compactions,
            "elided_blocks": self.elided_total,
            "summaries": self.summaries,
            "summary_tokens": self.summary_tokens_total,
        }


# ---------------------------------------------------------------- 纯函数


def _call_labels(messages: Sequence[Message]) -> dict[str, tuple[str, str]]:
    """tool_use_id → (工具名, 主要参数)。

    省略标记必须说清"被抹掉的是哪个文件的输出"，模型才知道该重新读哪里，
    而不是从头乱找 —— 那是 thrashing 的一条真实来源。
    """
    labels: dict[str, tuple[str, str]] = {}
    for message in messages:
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                labels[block.id] = (block.name, _primary_arg(block))
    return labels


def _primary_arg(block: ToolUseBlock) -> str:
    args = block.input if isinstance(block.input, dict) else {}
    for key in ("path", "file_path", "pattern", "command", "target"):
        value = args.get(key)
        if value:
            return str(value)[:120]
    return ""


def _elision_marker(label: tuple[str, str], chars: int) -> str:
    name, arg = label
    shown = f"{name} {arg}".strip() or "tool"
    verb = "重新调用该工具即可取回" if name in _REREADABLE else "该结果无法重取，必要时改用工具重新验证"
    return f"[elided: {shown} 的 {chars} chars 输出已省略 —— {verb}]"


def _round_groups(messages: Sequence[Message]) -> list[list[Message]]:
    """把历史切成轮组：一条带 tool_calls 的 assistant 消息 + 紧跟的结果消息 = 一组。

    L2 只能按这个粒度删。组内切（比如只删结果不删调用）就是 400 的成因。
    """
    groups: list[list[Message]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.tool_uses:
            group = [message]
            if index + 1 < len(messages) and messages[index + 1].results:
                group.append(messages[index + 1])
                index += 1
            groups.append(group)
        else:
            groups.append([message])
        index += 1
    return groups


def _protection_start(messages: Sequence[Message], keep_rounds: int) -> int:
    """最后 `keep_rounds` 个轮组的起始下标 —— 这之后的消息一律保持原样。"""
    seen = 0
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].tool_uses:
            seen += 1
            if seen >= keep_rounds:
                return index
    return len(messages)


def _turn_bounds(head: Sequence[Message], dropped: Sequence[Message]) -> tuple[int, int]:
    """摘要标题里的轮次范围：被压掉的那批覆盖第几轮到第几轮。"""
    first = sum(1 for message in head if message.tool_uses) + 1
    rounds = sum(1 for message in dropped if message.tool_uses) or 1
    return first, first + rounds - 1


def _local_digest(dropped: Sequence[Message]) -> tuple[str, list[str]]:
    """由代码统计、不依赖模型自觉的那一项摘要字段。

    B2 要求"摘要里必须能 grep 到本轮已改文件名"。这条判据如果押在模型有没有照模板
    写字段上，它就还是在测模型的记性而不是我们的压缩 —— 所以文件名从这里算。
    第二个返回值是**文本里真的列出来的**那几个路径：条数有上限，"打算留"和"留下了"
    不是一回事，trace 里要记的是后者。
    """
    touched: dict[str, list[str]] = {}
    for message in dropped:
        for block in message.content:
            if not isinstance(block, ToolUseBlock) or block.name not in {"write_file", "edit_file"}:
                continue
            args = block.input if isinstance(block.input, dict) else {}
            path = str(args.get("path") or args.get("file_path") or "")
            if not path:
                continue
            if block.name == "edit_file":
                detail = f"edit {str(args.get('old_text') or args.get('find') or '')[:40]!r}"
            else:
                detail = f"write {len(str(args.get('content', '')))} chars"
            touched.setdefault(path, []).append(detail)
    if not touched:
        return "", []
    listed = sorted(touched.items())[:12]
    lines = [f"- {path} — {'；'.join(details[:3])}" for path, details in listed]
    hidden = len(touched) - len(lines)
    if hidden > 0:
        lines.append(f"- （另有 {hidden} 个文件的改动未列出，需要时用 read_file 确认）")
    return "已改动文件：\n" + "\n".join(lines), [path for path, _ in listed]
