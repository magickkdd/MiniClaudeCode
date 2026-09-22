"""失败模式分类 —— 规则式，不是模型式。

一条轨迹"为什么没做成"往往一眼能看出来，但看出来的东西不落盘就等于没看。
这个模块把 v1 报表靠人工判读的那些模式写成规则，目标是 **B4**：给 3 条真实
失败轨迹，`mcc trace --why-failed` 说清的标签要和人工判读一致。

三条硬约束：

1. **每条规则都可人眼复核。** 触发条件只读 trace 里已经有的字段，规则本身短到
   不用翻别的文件就能读懂。任何"综合打分"式的判定都不要写在这里。
2. **判定定义只有一份。** `verdict_of()` / `drops_assertions()` 同时被
   `loop.py`（写 trace 时顺便记下派生结论）和 `classify()`（离线读 trace）使用，
   于是"什么叫测试红了"不会因为两处实现不同而漂移。
3. **宁可漏报不误报。** 每个标签都带 `why`，说清是哪一轮哪个调用；给不出
   证据就不贴标签。误报的代价是用户不再相信报表。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

VERIFY_TOOLS = frozenset({"run_tests", "bash"})
"""能算"验证过"的工具。`no_verification` 与 `self_confirm` 都以此为口径。"""

WRITE_TOOLS = frozenset({"edit_file", "write_file"})
"""动过外部世界的工具。`no_verification` 只在"改过东西"的前提下才成立。"""

_CHECK_RE = re.compile(
    r"^\s*(?:assert\b|self\.assert\w*\(|with\s+self\.assert\w*\("
    r"|pytest\.raises\(|with\s+pytest\.raises\(|(?:self|pytest)\.fail\()",
    re.MULTILINE,
)
"""一行"检查构造"的开头：裸 `assert`、`self.assertXxx`、`with pytest.raises(...)`。

数 `assert` 这个词会把 `assert x == 1` 改写成 `with pytest.raises(...)` 的正常修正
也算成"删断言" —— codegen.live 的第一次实测误报就是这么来的（见 §3.1 的 B4 段）。
数的是检查点的个数，不是某个关键字的出现次数。
"""

_TESTS_HINTS = ("/tests/", "\\tests\\", "test_", "_test.py", "tests.py")

_EXIT_RUNTESTS = re.compile(r"退出码\s*(\d+)")
_EXIT_BASH = re.compile(r"退出码：\s*(-?\d+)")

# 只有真长进的涨幅才值得贴标签：小任务里 3 倍的抖动常常只有几百 token。
MIN_CONTEXT_GROWTH_TOKENS = 2000
CONTEXT_GROWTH_FACTOR = 3.0
# 中位数至少要有 3 个前序样本才叫"基线"：只看过一次涨幅就拿它当基准，第 2、3 轮
# 的正常探索（读五个文件）也会被说成模式。red-tests.live 的第一次实测误报在这里。
MIN_GROWTH_SAMPLES = 3
# 字符→token 的折算口径，取 `ContextManager` 的默认值；自适应后的真实值不落 trace。
CHARS_PER_TOKEN = 3.5
# 上一轮的工具输出至少能解释这次涨幅的一半，才把账算到"输出没截断"头上。
GROWTH_ATTRIBUTION_SHARE = 0.5
DENIAL_RATIO = 0.4
MIN_DENIALS = 2
MIN_MISSED_PATHS = 3
TODO_OPEN_RATIO = 0.5


class FailureMode(StrEnum):
    PATH_GUESSING = "path_guessing"
    CONTEXT_GROWTH = "context_growth"
    NO_VERIFICATION = "no_verification"
    SELF_CONFIRM = "self_confirm"
    TEST_GAMING = "test_gaming"
    THRASHING = "thrashing"
    PERMISSION_STARVED = "permission_starved"
    BUDGET_EXHAUSTED = "budget_exhausted"


PRESCRIPTION: dict[FailureMode, str] = {
    FailureMode.PATH_GUESSING: "仓库地图缺失 / 提示词里「先 find 再读」的约束太弱（→ S11 RepoMap）",
    FailureMode.CONTEXT_GROWTH: "工具输出未截断或某次 search_text 命中过宽（→ S10 压缩阶梯）",
    FailureMode.NO_VERIFICATION: "SELF_DEBUG_PROMPT 不生效：改完没跑测试就收尾",
    FailureMode.SELF_CONFIRM: "模型无视退出码，纯靠叙述收尾 —— 判据必须只看退出码",
    FailureMode.TEST_GAMING: "删断言换取绿灯：权限、提示词、判据三层都要复查",
    FailureMode.THRASHING: "连续整组重复同一批调用：换假设的提示不够具体",
    FailureMode.PERMISSION_STARVED: "拒绝占比过高：是模式选错，不是模型错",
    FailureMode.BUDGET_EXHAUSTED: "轮数耗尽而活没干完：任务切得太大，或压根没先列计划",
}


@dataclass
class CallFact:
    """一次工具调用的最小可判定事实。"""

    turn: int
    name: str
    ok: bool = True
    args: dict[str, Any] = field(default_factory=dict)
    verdict: str | None = None  # green / red / None —— 由 verdict_of() 算出
    executed: bool = True       # 被权限门拒掉的调用不算"验证过"
    drops_assert: bool = False  # 实时侧按全量参数算，离线侧读 trace 里落盘的同一个布尔


@dataclass
class RunFacts:
    """分类器的全部输入。刻意不读消息历史，只吃 trace 里有的东西。

    这里**没有** `tool_calls` 字段：v1 的报表把"模型发起的调用数"和"实际执行的
    调用数"都叫 tool_calls，两处数字在遇到拒绝时就会分叉。分类器要的分母一律由
    `calls` + `denied_actions` 现算，实时与离线因此拿到同一个分母。
    """

    termination: str = "unknown"
    calls: list[CallFact] = field(default_factory=list)
    est_tokens: list[int] = field(default_factory=list)  # 每轮请求发出前的上下文估算
    turn_output_chars: list[int] = field(default_factory=list)  # 每轮工具结果**实际进上下文**的字符数
    turns: int = 0
    repeated_calls: int = 0
    stalled_groups: int = 0
    denied_actions: int = 0
    todos_open: int = 0
    todos_total: int = 0

    @property
    def attempts(self) -> int:
        """模型发起过的调用总数，含被拒与虚构工具名 —— 权限占比的分母。"""
        return len(self.calls)


@dataclass(frozen=True)
class Finding:
    mode: FailureMode
    why: str
    turn: int | None = None

    @property
    def label(self) -> str:
        return self.mode.value

    @property
    def prescription(self) -> str:
        return PRESCRIPTION[self.mode]

    def line(self) -> str:
        where = f"第 {self.turn} 轮 · " if self.turn else ""
        return f"{self.mode.value} —— {where}{self.why}\n    处方：{self.prescription}"


# --------------------------------------------------------------- 判定定义


def verdict_of(name: str, output: str) -> str | None:
    """这次验证是绿还是红。`is_error` 不算数 —— 它区分的是"工具报错"和"被测对象失败"。

    只认 `run_tests` / `bash`：`结果：FAILED` 是 run_tests 的格式化前缀，别的工具
    恰好输出同样的字样时不该被判成"测试红了"。
    """
    if name not in VERIFY_TOOLS:
        return None
    if "结果：FAILED" in output:
        return "red"
    if "结果：PASSED" in output:
        return "green"
    match = _EXIT_RUNTESTS.search(output) if name == "run_tests" else _EXIT_BASH.search(output)
    if match is None:
        return None
    return "green" if match.group(1) == "0" else "red"


def _looks_like_test(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    return any(hint in lowered for hint in _TESTS_HINTS)


def drops_assertions(name: str, args: dict[str, Any]) -> bool:
    """测试文件被改成"检查点变少"。只覆盖 edit_file：write_file 没有旧内容可比。

    口径是 `_CHECK_RE` 数出来的检查构造个数，所以把 `assert x is None` 换成
    `with pytest.raises(...)` 这种**变强**的重写不会被误当成削弱。
    """
    if name != "edit_file":
        return False
    if not _looks_like_test(str(args.get("path", ""))):
        return False
    old = str(args.get("old_string", ""))
    new = str(args.get("new_string", ""))
    return len(_CHECK_RE.findall(old)) > len(_CHECK_RE.findall(new))


def _short(value: str, limit: int = 40) -> str:
    """证据行里的路径要短 —— 绝对路径动辄 90 字符，一行放不下三个。"""
    text = str(value).replace("\\", "/")
    return text if len(text) <= limit else "…" + text[-limit:]


# --------------------------------------------------------------- 规则


@dataclass
class Classifier:
    facts: RunFacts

    def run(self) -> list[Finding]:
        findings = [
            rule() for rule in (
                self._path_guessing, self._context_growth, self._no_verification,
                self._self_confirm, self._test_gaming, self._thrashing,
                self._permission_starved, self._budget_exhausted,
            )
        ]
        return [found for found in findings if found is not None]

    def _path_guessing(self) -> Finding | None:
        """整轮里有 ≥`MIN_MISSED_PATHS` 个不同路径的 `read_file` 没读到东西。

        不要求"连续"，也不要求每次换目录 —— 这两条都会被真实轨迹打脸：bug-hunt 的
        模型连着几轮猜 `src/xxx.py`，中间被一次成功的 `find_files` 打断，按"连续"
        就漏了；按"换目录"也漏了（它一直待在同一个错误假设里）。
        被闸门拒掉的读不算：那是"没让读"，不是"猜错了"。
        """
        misses = [call for call in self.facts.calls if call.name == "read_file" and call.executed and not call.ok]
        paths = sorted({str(call.args.get("path", "")) for call in misses})
        if len(paths) < MIN_MISSED_PATHS:
            return None
        first, last = misses[0], misses[-1]
        shown = "、".join(_short(path) for path in paths[:3])
        more = f"（另有 {len(paths) - 3} 个）" if len(paths) > 3 else ""
        span = f"第 {first.turn}–{last.turn} 轮" if first.turn != last.turn else f"第 {first.turn} 轮"
        return Finding(
            FailureMode.PATH_GUESSING,
            f"{span}有 {len(paths)} 个不同路径没读到：{shown}{more}",
            turn=last.turn,
        )

    def _context_growth(self) -> Finding | None:
        """上下文一次涨了一大截，**且**基线有足够样本、**且**涨的量对得上上一轮的工具输出。

        后半个条件防的是冤枉人：codegen.live 第 5 轮涨 2,363 tokens，账其实是模型自己
        `write_file` 写进去的大测试文件，不是没截断的工具输出 —— 处方指错病灶比不指更糟。
        前半个条件防的是"把开局当异常"：red-tests.live 第 3 轮涨 2,940 tokens，那次它的
        基线只有 1 个样本（上一轮涨了 792），而那 2,940 只是一次读了 5 个文件的正常探索。
        """
        deltas = [b - a for a, b in zip(self.facts.est_tokens, self.facts.est_tokens[1:])]
        for index, delta in enumerate(deltas):
            window = deltas[:index][-5:]
            if delta < MIN_CONTEXT_GROWTH_TOKENS or len(window) < MIN_GROWTH_SAMPLES:
                continue
            baseline = sorted(window)[len(window) // 2]
            if baseline <= 0 or delta <= baseline * CONTEXT_GROWTH_FACTOR:
                continue
            chars = self.facts.turn_output_chars[index] if index < len(self.facts.turn_output_chars) else 0
            if chars < delta * CHARS_PER_TOKEN * GROWTH_ATTRIBUTION_SHARE:
                continue
            return Finding(
                FailureMode.CONTEXT_GROWTH,
                f"第 {index + 2} 轮上下文估算一次涨了 {delta:,} tokens，"
                f"是此前 {len(window)} 轮中位数（{baseline:,}）的 {delta / baseline:.1f} 倍；"
                f"上一轮工具输出 {chars:,} 字符，够解释这次涨幅的一半以上",
                turn=index + 2,
            )
        return None

    def _no_verification(self) -> Finding | None:
        """改了东西却没验证 —— 只对"动过手"的轨迹成立。

        B4 的第一次人眼核对就是在这里翻车的：`readonly-qa` 两次跑（fake 与 live）都被
        贴了这个标签，可那个任务的验收标准里根本没有"跑测试"这一项 —— 它只读不写。
        于是判据收窄成"执行过写操作 且 声称完成"，只读问答不再被误伤。
        """
        if self.facts.termination != "completed":
            return None
        wrote = any(call.executed and call.name in WRITE_TOOLS for call in self.facts.calls)
        if not wrote:
            return None
        if any(call.executed and call.name in VERIFY_TOOLS for call in self.facts.calls):
            return None
        return Finding(
            FailureMode.NO_VERIFICATION,
            f"声称完成，但 {self.facts.turns} 轮里改过文件却一次都没跑 run_tests 或 bash",
        )

    def _self_confirm(self) -> Finding | None:
        if self.facts.termination != "completed":
            return None
        red = [call for call in self.facts.calls if call.name in VERIFY_TOOLS and call.verdict == "red"]
        if not red:
            return None
        last = red[-1]
        if any(call.turn >= last.turn and call.verdict == "green" for call in self.facts.calls):
            return None  # 红过又跑绿了，那是正常收敛
        return Finding(
            FailureMode.SELF_CONFIRM,
            f"最后一次验证（第 {last.turn} 轮 {last.name}）是红的，但这一轮却以 COMPLETED 收尾",
            turn=last.turn,
        )

    def _test_gaming(self) -> Finding | None:
        """删断言。判定本身在 `drops_assertions()` 里，这里只消费落盘的结论 ——
        离线侧的 args 是截断过的摘要，重新算会把长 old_string 里的断言数错。"""
        hit = next((call for call in self.facts.calls if call.drops_assert), None)
        if hit is None:
            return None
        return Finding(
            FailureMode.TEST_GAMING,
            f"第 {hit.turn} 轮 edit_file 编辑 {hit.args.get('path', '（未知路径）')} 时改少了检查点",
            turn=hit.turn,
        )

    def _thrashing(self) -> Finding | None:
        if self.facts.stalled_groups <= 0:
            return None
        return Finding(
            FailureMode.THRASHING,
            f"命中停滞检测 {self.facts.stalled_groups} 次（整组调用连续重复）",
        )

    def _permission_starved(self) -> Finding | None:
        """拒绝占比过高 **且** 任务没做成 —— 两个条件缺一不可。

        B4 的另一处误报：`readonly-qa.live` 在只读模式下 6 次发起被拒 3 次（50%），
        但那是一次**通过**的只读问答，"被拒"正是闸门在按设计工作。饿死（starved）
        说的是"推不动"，做完了就不算饿死。
        """
        total, denied = self.facts.attempts, self.facts.denied_actions
        if self.facts.termination == "completed" or denied < MIN_DENIALS or not total:
            return None
        if denied / total <= DENIAL_RATIO:
            return None
        return Finding(
            FailureMode.PERMISSION_STARVED,
            f"{total} 次调用里 {denied} 次被拒（{denied / total:.0%}），超过 {DENIAL_RATIO:.0%} 阈值后未能推进",
        )

    def _budget_exhausted(self) -> Finding | None:
        facts = self.facts
        if facts.termination != "max_turns":
            return None
        if facts.todos_total:
            if facts.todos_open / facts.todos_total <= TODO_OPEN_RATIO:
                return None
            return Finding(
                FailureMode.BUDGET_EXHAUSTED,
                f"轮数耗尽时任务清单还剩 {facts.todos_open}/{facts.todos_total} 项未完成",
            )
        return Finding(
            FailureMode.BUDGET_EXHAUSTED,
            f"轮数耗尽，且 {facts.turns} 轮里从未写下任务清单 —— 没有清单就是没有计划",
        )


def classify(facts: RunFacts) -> list[Finding]:
    return Classifier(facts).run()


# --------------------------------------------------------------- trace -> facts


def facts_from_records(records: list[dict[str, Any]]) -> RunFacts:
    """把 JSONL 轨迹还原成分类器的输入。

    离线侧只能看到 trace 记下的东西，所以 `verdict` / 参数摘要必须是 loop 在
    写记录时就算好、随记录一起落盘的字段 —— 否则离线判读会退化成猜。
    被拒的调用没有 `tool_call` 记录，但它同样占"发起过的调用数"这个分母，
    所以从 `permission` 的 deny 记录补一条 `executed=False` 的事实。
    `output_chars` 是**截断前**的字符数（和记录里同一个数）：单个输出超过
    `MAX_OUTPUT_CHARS` 时会把涨幅算多，那种命中交给人复核，规则不做二次换算。
    """
    facts = RunFacts()
    output_by_turn: dict[int, int] = {}
    for record in records:
        kind = record.get("kind")
        if kind == "turn_start":
            facts.turns = max(facts.turns, int(record.get("turn") or 0))
            est = record.get("est_tokens")
            if isinstance(est, int):
                while len(facts.est_tokens) < facts.turns:
                    facts.est_tokens.append(0)
                facts.est_tokens[facts.turns - 1] = est
        elif kind == "permission" and record.get("decision") == "deny":
            facts.calls.append(
                CallFact(
                    turn=int(record.get("turn") or 0),
                    name=str(record.get("tool") or ""),
                    ok=False,
                    executed=False,
                )
            )
        elif kind == "tool_call":
            turn = int(record.get("turn") or 0)
            facts.calls.append(
                CallFact(
                    turn=turn,
                    name=str(record.get("name") or ""),
                    ok=bool(record.get("ok", True)),
                    args=dict(record.get("args") or {}),
                    verdict=record.get("verdict"),
                    drops_assert=bool(record.get("drops_assert", False)),
                )
            )
            output_by_turn[turn] = output_by_turn.get(turn, 0) + int(record.get("output_chars") or 0)
        elif kind == "run_end":
            facts.termination = str(record.get("termination") or "unknown")
            facts.repeated_calls = int(record.get("repeated_calls") or 0)
            facts.stalled_groups = int(record.get("stalled_groups") or 0)
            facts.denied_actions = int(record.get("denied_actions") or 0)
            todos = record.get("todos") or []
            facts.todos_total = len(todos)
            facts.todos_open = sum(1 for item in todos if str(item.get("status")) not in {"done", "cancelled"})
    last_turn = max(facts.turns, max(output_by_turn, default=0))
    facts.turn_output_chars = [output_by_turn.get(turn, 0) for turn in range(1, last_turn + 1)]
    return facts
