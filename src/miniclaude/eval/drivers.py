"""fake 引擎的剧本 —— 让 24 道题在**不联网、不花钱、秒级**的前提下各跑一遍。

这些剧本不是"假装成功"。它们只是几段固定序列的真工具调用，落盘之后由 `judge.py`
独立判定：判红就是判红。所以 fake 批次里**大部分任务本来就该失败** —— 它们的价值
在于把隔离、续跑、成本闸、报表、失败分类这条管线全程走一遍（SPEC v2 §7.4 说得很
明白：如果 24 道题全绿，第一反应应该是怀疑判据）。

五个 `demo:*` 剧本直接复用 `demos/fake_scripts.py`：同一份剧本在 demo 与 eval 两边
产生同一个结论，这本身就是"判据被搬家而不是被重写"的证据。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Callable, Sequence

from miniclaude.eval.taskset import TaskInstance
from miniclaude.messages import LLMResponse


def _add_demos_to_path() -> None:
    """fake 剧本住在 `demos/`，eval 层复用它们 —— 但路径由本文件位置反推，
    不要求调用方记得改 `sys.path`（`mcc eval`、pytest、脚本三种入口都要能直接 import）。
    """
    demos = Path(__file__).resolve().parents[3] / "demos"
    if demos.is_dir() and str(demos) not in sys.path:
        sys.path.insert(0, str(demos))


_add_demos_to_path()

from fakes import scripted_final_text, scripted_tool_calls  # noqa: E402

Driver = Callable[[TaskInstance, Path], list[LLMResponse]]


def _reads(*paths: str, text: str = "") -> LLMResponse:
    return scripted_tool_calls([("read_file", {"path": path}) for path in paths], text=text)


def _edits(fixes: Sequence[dict[str, str]]) -> LLMResponse:
    return scripted_tool_calls(
        [("edit_file", {"path": fix["path"], "old_string": fix["old"], "new_string": fix["new"]}) for fix in fixes]
    )


def _writes(files: Sequence[dict[str, str]]) -> LLMResponse:
    return scripted_tool_calls([("write_file", {"path": item["path"], "content": item["content"]}) for item in files])


def answer(task: TaskInstance, workdir: Path) -> list[LLMResponse]:
    """只读问答：读两个文件，然后给出答复。是否判过由 `answer_keywords` 说了算。"""
    args = task.fake.args
    return [
        _reads(*args.get("read", ["README.md"])),
        scripted_final_text(args.get("text", "")),
    ]


def edit_verify(task: TaskInstance, workdir: Path) -> list[LLMResponse]:
    """读 → 改 → **跑测试** → 收尾。修得对就 pass，修不对就 fail。"""
    args = task.fake.args
    fixes = list(args.get("fixes", []))
    out: list[LLMResponse] = [_reads(*(args.get("read") or [fix["path"] for fix in fixes]))]
    if fixes:
        out.append(_edits(fixes))
    out.append(scripted_tool_calls([("run_tests", {})]))
    out.append(scripted_final_text(args.get("text", "改完了，测试跑过。")))
    return out


def write_verify(task: TaskInstance, workdir: Path) -> list[LLMResponse]:
    """写新文件 → 跑测试 → 收尾。greenfield 与"补功能"类任务用它。"""
    args = task.fake.args
    return [
        _reads(*args.get("read", ["README.md"])),
        _writes(list(args.get("files", []))),
        scripted_tool_calls([("run_tests", args.get("run_tests", {}))]),
        scripted_final_text(args.get("text", "新增文件已写好，测试跑过。")),
    ]


def read_claim(task: TaskInstance, workdir: Path) -> list[LLMResponse]:
    """读了两眼就宣布成功，一次测试都没跑 —— `no_verification` 的标准样本。"""
    args = task.fake.args
    return [
        _reads(*args.get("read", ["README.md"])),
        scripted_final_text(args.get("text", "已经修好了，行为符合 README 的承诺。")),
    ]


def guess_paths(task: TaskInstance, workdir: Path) -> list[LLMResponse]:
    """照着想象的目录结构猜路径 —— `path_guessing` 的标准样本。"""
    args = task.fake.args
    decoys: list[str] = list(args.get("decoys", ["src/main.py", "lib/core.py", "app.py"]))
    calls = [(name, {"path": path}) for name, path in zip(["read_file"] * len(decoys), decoys)]
    calls.append(("find_files", {"pattern": "**/*.py"}))
    turns = [scripted_tool_calls([call]) for call in calls]
    turns.append(scripted_final_text(args.get("text", "找不到问题所在，先按现状答复。")))
    return turns


def tamper(task: TaskInstance, workdir: Path) -> list[LLMResponse]:
    """把断言改松让测试变绿 —— 判据必须抓到，抓不到就是判据的 bug。"""
    args = task.fake.args
    return [
        _reads(*args.get("read", ["tests/test_x.py"])),
        _edits(list(args.get("fixes", []))),
        scripted_tool_calls([("run_tests", {})]),
        scripted_final_text(args.get("text", "测试已经全绿了。")),
    ]


def repeat_stall(task: TaskInstance, workdir: Path) -> list[LLMResponse]:
    """同一组调用原样重演三轮 —— 循环的停滞检测该在这里把它掐掉。"""
    args = task.fake.args
    call = dict(args.get("call", {"name": "run_tests", "args": {}}))
    turn = scripted_tool_calls([(call["name"], dict(call.get("args", {})))])
    return [turn] * 5


def over_budget(task: TaskInstance, workdir: Path) -> list[LLMResponse]:
    """每轮都换一个不同的调用，一直绕到轮数耗尽 —— 止损该在预算内发生。"""
    args = task.fake.args
    paths = list(args.get("paths", ["README.md"]))
    turns: list[LLMResponse] = []
    for index in range(task.max_turns + 2):
        turns.append(scripted_tool_calls([("read_file", {"path": paths[index % len(paths)] + ("." * index)})]))
    return turns


def _public_api(source: Path) -> list[tuple[str, str, str]]:
    """按 README 约定的规范形态取出模块的公开 API：(名字, 签名, docstring 首行)。

    规范签名 = `f"def {name}({ast.unparse(node.args)})"` —— 剧本与判据共用这一个定义，
    否则"逐字一致"会变成两边各写一套归一化规则、比出来全是假红。
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    out: list[tuple[str, str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        doc = (ast.get_docstring(node) or "").splitlines()
        out.append((node.name, f"def {node.name}({ast.unparse(node.args)})", doc[0].strip() if doc else ""))
    out.sort()
    return out


def _rollup_source(workdir: Path, relative: str) -> str:
    """按 README 的格式生成一份 `reports/part_NN_summary.py`。"""
    source = workdir / relative
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tag = next(
        (node.value.value for node in tree.body
         if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "AUDIT_TAG"),
        "",
    )
    rows = "\n".join(
        f"    ({name!r}, {signature!r}, {doc!r})," for name, signature, doc in _public_api(source)
    )
    return (
        f'"""Summary of {relative}."""\n\n'
        f'SOURCE = "{relative}"\n'
        f'AUDIT_TAG = "{tag}"\n'
        "API = [\n"
        f"{rows}\n"
        "]\n"
    )


ROLLUP_CONTRACT_TEST = '''"""Contract test for the rollup layer: every summary must match its source module."""

import ast
from pathlib import Path

LEDGER = Path("ledger")
REPORTS = Path("reports")


def _public(module: Path):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    rows = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            doc = (ast.get_docstring(node) or "").splitlines()
            rows.append((node.name, f"def {node.name}({ast.unparse(node.args)})",
                         doc[0].strip() if doc else ""))
    return sorted(rows)


def _summary(module: Path):
    namespace: dict = {}
    exec(compile(ast.parse(module.read_text(encoding="utf-8")), str(module), "exec"), namespace)
    return sorted((tuple(row) for row in namespace["API"])), namespace


def _tag(module: Path) -> str:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "AUDIT_TAG":
            return node.value.value
    raise AssertionError(f"{module} 里没有 AUDIT_TAG")


def test_every_part_has_a_summary():
    sources = sorted(LEDGER.glob("part_*.py"))
    assert sources, "ledger/ 里没有 part 模块，题目本身坏了"
    for source in sources:
        assert (REPORTS / f"{source.stem}_summary.py").is_file(), f"缺 {source.stem} 的汇总模块"


def test_summaries_match_their_sources_exactly():
    for source in sorted(LEDGER.glob("part_*.py")):
        summary = REPORTS / f"{source.stem}_summary.py"
        if not summary.is_file():
            continue
        rows, namespace = _summary(summary)
        assert rows == _public(source), f"{summary.name} 的 API 与源文件不一致"
        assert namespace["SOURCE"] == str(source).replace("\\\\", "/"), namespace["SOURCE"]
        assert namespace["AUDIT_TAG"] == _tag(source), f"{summary.name} 的 AUDIT_TAG 抄错了"


def test_rollup_layer_covers_the_whole_surface():
    total = sum(len(_public(source)) for source in sorted(LEDGER.glob("part_*.py")))
    done = 0
    for summary in sorted(REPORTS.glob("part_*_summary.py")):
        rows, _ = _summary(summary)
        done += len(rows)
    assert total and done == total, f"汇总了 {done} 个函数，源里一共 {total} 个"
'''


def long_report(task: TaskInstance, workdir: Path) -> list[LLMResponse]:
    """一轮读一个模块、一轮写一份汇总 —— B2（SPEC v2 §3.3）的载体剧本。

    这个剧本的存在理由不是"演一个长任务"，而是把阶梯的两层**各逼到一次**：
    读进来的大块工具输出让 L1 有活干；`write_file` 的 `content` 参数在 **assistant
    消息**里，L1 碰不到，所以只能靠 L2 整组摘要 —— 于是"摘要里能不能 grep 到已改
    文件名"这条判据才真的被测到。八个模块约 19 万字符，32k 预算下必然越线。
    """
    args = task.fake.args
    count = int(args.get("modules", 8))
    sources = [f"ledger/part_{index:02d}.py" for index in range(1, count + 1)]
    turns: list[LLMResponse] = [scripted_tool_calls([("read_file", {"path": path})]) for path in sources]
    for path in sources:
        rollup = f"reports/{Path(path).stem}_summary.py"
        turns.append(scripted_tool_calls([("write_file", {"path": rollup, "content": _rollup_source(workdir, path)})]))
    turns.append(scripted_tool_calls([("write_file", {"path": "tests/test_rollups.py", "content": ROLLUP_CONTRACT_TEST})]))
    turns.append(scripted_tool_calls([("run_tests", {})]))
    turns.append(scripted_final_text(args.get("text", "八份汇总模块与契约测试都写好了，测试全绿。")))
    return turns


def from_demo(script_name: str) -> Driver:
    """复用 `demos/fake_scripts.py` 里已经调好的剧本。"""

    def driver(task: TaskInstance, workdir: Path) -> list[LLMResponse]:
        import fake_scripts  # 只有 demo:* 驱动需要它，别的任务不必为它付 import 成本

        try:
            script = getattr(fake_scripts, script_name)
        except AttributeError as exc:  # pragma: no cover - 出题时写错名字就会撞上
            raise KeyError(f"demos/fake_scripts.py 里没有 {script_name!r}") from exc
        return list(script())

    return driver


DRIVERS: dict[str, Driver] = {
    "answer": answer,
    "edit-verify": edit_verify,
    "write-verify": write_verify,
    "read-claim": read_claim,
    "guess-paths": guess_paths,
    "tamper": tamper,
    "repeat-stall": repeat_stall,
    "over-budget": over_budget,
    "long-report": long_report,
    "demo:codegen": from_demo("codegen_script"),
    "demo:bug-hunt": from_demo("bug_hunt_script"),
    "demo:red-tests": from_demo("red_tests_script"),
    "demo:readonly-qa": from_demo("readonly_qa_script"),
    "demo:giveup": from_demo("giveup_script"),
}


def resolve(name: str) -> Driver:
    try:
        return DRIVERS[name]
    except KeyError as exc:
        raise KeyError(f"没有 {name!r} 这个 fake 驱动，可选：{'、'.join(sorted(DRIVERS))}") from exc


def build(task: TaskInstance, workdir: Path) -> list[LLMResponse]:
    return resolve(task.fake.driver)(task, workdir)
