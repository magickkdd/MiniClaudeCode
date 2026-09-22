"""记忆层测试（SPEC v2 §3.4 / §6.1 的 RepoMap 与 MemoryStore 那两行）。

这个文件盯的是三条容易出事的性质：

1. **地图是仓库的地图，不是"有符号的文件的地图"** —— 一个只有 `print()` 的脚本也必须
   出现在里面。少一个名字，模型就多一轮猜路径（`path_guessing` 就是这么来的）。
2. **一切落盘的东西都必须能坏** —— `.mcc/` 是派生物（§2.3-1），损坏、被删、跨机器搬来
   指纹对不上，统统降级成"没有缓存"，而不是把会话带着一起死。
3. **地图进的是 system，所以它不能抖** —— §3.4 写的是"只在指纹变化时更新"：读文件不
   改仓库，所以读只影响**下一次重画时**的排序。跟着每轮重画就等于每轮打掉前缀缓存。

`mcc eval` 的 B3 对照臂靠 `repo_map=False` 走回目录树，所以"关"这条路也在下面钉住了：
关掉之后 system 里必须还有 `# 仓库形状`，而不是什么都没有。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Callable

import pytest
from fakes import FakeLLM, scripted_final_text, scripted_tool_calls
from miniclaude.agent.loop import Agent
from miniclaude.agent.permissions import PermissionGate, PermissionMode
from miniclaude.agent.planner import TodoItem, TodoList
from miniclaude.agent.prompts import build_system_prompt
from miniclaude.infra.trace import Tracer, prompt_hash, replay
from miniclaude.memory import MEMORY_DIRNAME, MemKind, MemoryStore, RepoMap, analyze_source
from miniclaude.memory.repo_map import (
    REASON_DIRECT,
    REASON_IMPORT,
    REASON_PACKAGE,
    REASON_SAME_DIR,
    REASON_TODO,
    build_import_index,
    extract_symbols,
    function_signature,
    parse_imports,
    render_file_lines,
)
from miniclaude.tools.registry import ToolRegistry
from miniclaude.tools.workspace import Workspace

PROMPT = "记忆层测试用提示词。"

MODULE = (
    '"""结算模块。"""\n\nTAX_RATE = 0.1\n\n\ndef checkout(cart, coupon=None):\n'
    '    """按券结算。"""\n    return 0\n\n\nclass Cart:\n    """购物车。"""\n\n'
    '    def add(self, sku):\n        """加一件。"""\n'
)
SCRIPT = "print('hi')\n"
NEW_MODULE = '"""新。"""\n\ndef fresh(x):\n    """新的函数。"""\n    return x\n'


def seed(root: Path, files: dict[str, str]) -> Workspace:
    """在 tmp 里铺一个小仓库。写父目录，因为信号 ① 测的就是带目录的仓库。"""
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Workspace(root)


def store_for(root: Path) -> MemoryStore:
    return MemoryStore.for_project(root)


def reasons_by_file(mapping: RepoMap) -> dict[str, list[str]]:
    return {item["file"]: list(item["reasons"]) for item in mapping.stats.reasons}


def make_agent(
    ws: Workspace,
    responses: list[Any],
    *,
    tmp_path: Path,
    mode: PermissionMode = PermissionMode.AUTO,
    repo_map: RepoMap | None = None,
    notes: str = "",
    todos: TodoList | None = None,
) -> tuple[Agent, FakeLLM]:
    """真工具 + 假模型 + 真 system 渲染。`llm.calls[i]["system"]` 就是第 i 轮发出去的 system。"""
    registry = ToolRegistry.default(ws, bash_timeout=30)
    llm = FakeLLM(responses)
    trace = tmp_path / "trace.jsonl"
    tracer = Tracer(trace, session_id="memory")
    tracer.start_session(
        model="fake", tools=list(registry.names()), config={}, system_prompt_hash=prompt_hash(PROMPT)
    )
    agent = Agent(
        llm=llm,
        registry=registry,
        gate=PermissionGate(workspace=ws, mode=mode, confirmer=None),
        system_prompt=PROMPT,
        max_turns=8,
        todos=todos,
        tracer=tracer,
        repo_map=repo_map,
        notes=notes,
        system_provider=(
            (lambda: f"{PROMPT}\n\n{repo_map.map_for_prompt()}") if repo_map is not None else None
        ),
    )
    return agent, llm


# ================================================================ store.py


def test_store_round_trips_across_instances(tmp_path: Path) -> None:
    first = store_for(tmp_path)
    first.put(MemKind.CONVENTION, "commit", "提交信息写中文")
    first.flush()
    assert first.writes == 1
    assert store_for(tmp_path).get(MemKind.CONVENTION, "commit") == "提交信息写中文"
    assert store_for(tmp_path).fingerprint_of(MemKind.CONVENTION, "commit") == {}


def test_a_corrupt_store_degrades_to_no_cache_instead_of_crashing(tmp_path: Path) -> None:
    """§2.3-1：磁盘状态永远是派生物。坏 JSON 的归宿是重建，不是报错停机。"""
    store = store_for(tmp_path)
    store.put(MemKind.GOTCHA, "win", "Windows 下 NUL 删不掉")
    store.flush()
    (tmp_path / MEMORY_DIRNAME / "memory.json").write_text("{ 这不是 JSON", encoding="utf-8")

    rebuilt = store_for(tmp_path)
    assert rebuilt.get(MemKind.GOTCHA, "win") is None
    assert rebuilt.degraded.startswith("JSONDecodeError")
    rebuilt.put(MemKind.GOTCHA, "win", "重写一遍就回来了")
    rebuilt.flush()
    assert store_for(tmp_path).get(MemKind.GOTCHA, "win") == "重写一遍就回来了"


def test_an_unknown_version_is_discarded_not_guessed(tmp_path: Path) -> None:
    """将来改 schema 时旧文件必须整份作废：拿 v1 的形状读 v2 会读出半个地图。"""
    directory = tmp_path / MEMORY_DIRNAME
    directory.mkdir(parents=True)
    (directory / "memory.json").write_text(
        json.dumps({"version": 99, "entries": {"a": {"kind": "gotcha", "key": "a", "value": "x"}}}),
        encoding="utf-8",
    )
    store = store_for(tmp_path)
    assert store.get(MemKind.GOTCHA, "a") is None
    assert "版本或形状不认识" in store.degraded
    assert store.stats()["entries"] == 0


def test_notes_are_capped_by_chars_not_by_count(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    for index in range(6):
        store.put(MemKind.CONVENTION, f"c{index}", "约定" * 200)
    assert store.stats()["by_kind"] == {"convention": 6}
    text = store.notes_for_prompt(cap=300)
    assert "工作记忆" in text and "笔记预算已满" in text
    assert len(text) < 900, "笔记进的是每轮都发的 system，超预算必须截断"


def test_a_test_command_note_says_it_is_display_only(tmp_path: Path) -> None:
    """§6.3-2：一次错误观察若能驱动 agent 自动去跑命令，错误就自成了。

    文字承诺之外再加一条静态检查：这一层只要真的 import 了 `subprocess`，
    "只展示"就只是一句注释。
    """
    store = store_for(tmp_path)
    store.put(MemKind.TEST_COMMAND, "run", "pytest -q tests/test_x.py")
    rendered = store.notes_for_prompt()
    assert "只展示，不会自动执行" in rendered
    assert "pytest -q tests/test_x.py" in rendered

    source = (
        Path(__file__).resolve().parents[1] / "src" / "miniclaude" / "memory" / "store.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert "subprocess" not in imported, "记忆层不许有可执行路径"
    called = {
        node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"eval", "exec", "compile", "__import__", "system"}, sorted(called)


def test_repo_map_entries_never_render_as_notes(tmp_path: Path) -> None:
    """同一份信息在 system 里发两遍是纯开销：地图由 RepoMap 渲染，笔记区只放笔记。"""
    store = store_for(tmp_path)
    store.put(MemKind.REPO_MAP, "pkg/a.py", json.dumps({"lines": ["· x"], "imports": []}))
    assert store.notes_for_prompt() == ""


def test_forget_removes_persistently(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    store.put(MemKind.REPO_MAP, "pkg/a.py", "x")
    store.put(MemKind.REPO_MAP, "pkg/b.py", "y")
    store.flush()
    assert store.forget(MemKind.REPO_MAP, "pkg/a.py") == 1
    assert store_for(tmp_path).keys(MemKind.REPO_MAP) == ["pkg/b.py"]
    assert store.forget(MemKind.REPO_MAP) == 1
    assert store_for(tmp_path).keys(MemKind.REPO_MAP) == []


def test_an_overlong_value_is_clipped_not_dropped(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    store.put(MemKind.CONVENTION, "big", "字" * 9_000)
    value = store.get(MemKind.CONVENTION, "big") or ""
    assert len(value) <= 4_002 and value.endswith("…")


# ================================================================ repo_map.py


def test_map_lists_a_script_with_no_symbols(tmp_path: Path) -> None:
    """地图的第一职责是"这文件存在"。只有 `print()` 的脚本一个符号也没有，照样得出现。"""
    mapping = RepoMap(seed(tmp_path, {"hello.py": SCRIPT, "cart.py": MODULE}))
    text = mapping.build()
    assert "hello.py" in text and "（无模块级符号）" in text
    assert "def checkout(cart, coupon=None)" in text and "TAX_RATE = 0.1" in text


def test_map_within_token_cap(tmp_path: Path) -> None:
    files = {f"pkg/mod_{index:02d}.py": MODULE for index in range(12)}
    mapping = RepoMap(seed(tmp_path, files), token_cap=120)
    text = mapping.build()
    assert mapping.stats.listed < 12, "预算这么小还全列出来，就是没把裁剪当真"
    assert mapping.stats.chars <= 120 * 3.5
    footer = next(line for line in text.splitlines() if line.startswith("未列出（"))
    named = footer.split("：", 1)[1].split("，另有")[0]
    assert named, "落选名单必须点名，否则'为什么没给那个文件'就没法回答"
    for item in named.split("、"):
        assert item.split("(")[0] in files, f"落选名单里出现了不存在的文件：{item}"


def test_map_entry_explains_inclusion(tmp_path: Path) -> None:
    mapping = RepoMap(seed(tmp_path, {"pkg/a.py": MODULE, "pkg/b.py": MODULE, "other/c.py": MODULE}))
    text = mapping.relevant(["pkg/a.py"])
    assert " · [recent]" in text
    reasons = reasons_by_file(mapping)
    assert reasons["pkg/a.py"] == [REASON_DIRECT]
    assert reasons["pkg/b.py"] == [REASON_SAME_DIR]
    assert reasons["other/c.py"] == []
    assert all(item["listed"] for item in mapping.stats.reasons), "三个文件都该进这一版地图"


def test_the_three_ranking_signals_are_independent(tmp_path: Path) -> None:
    """三个信号各自能单独把一个文件顶上来，且理由写进条目。"""
    mapping = RepoMap(
        seed(
            tmp_path,
            {
                "core/__init__.py": '"""门面。"""\n',
                "core/store.py": MODULE,
                "core/lonely.py": MODULE,
                "core/uses_store.py": '"""用一下。"""\nfrom . import store\n',
                "tasks/render.py": MODULE,
                "unrelated.py": '"""没人碰。"""\n',
            },
        )
    )
    mapping.relevant(["core/store.py"], todo_text="改 render 那部分")
    reasons = reasons_by_file(mapping)
    assert reasons["core/store.py"] == [REASON_DIRECT, REASON_IMPORT.format(n=1)]
    assert reasons["core/lonely.py"] == [REASON_SAME_DIR]
    assert reasons["tasks/render.py"] == [REASON_TODO]
    assert reasons["unrelated.py"] == []
    assert REASON_PACKAGE in reasons["core/__init__.py"]


def test_import_edges_survive_the_cache(tmp_path: Path) -> None:
    """热缓存里必须连 import 边一起取回，否则信号 ③ 在第二个会话上全变 0。"""
    seed(tmp_path, {"a.py": '"""A。"""\nimport b\n', "b.py": MODULE})
    first = RepoMap(Workspace(tmp_path), store=store_for(tmp_path))
    first.build()
    assert first.stats.modules_parsed == 2

    second = RepoMap(Workspace(tmp_path), store=store_for(tmp_path))
    second.build()
    assert second.stats.from_cache == 2 and second.stats.modules_parsed == 0
    assert reasons_by_file(second)["b.py"] == [REASON_IMPORT.format(n=1)]


def test_a_stale_fingerprint_rebuilds_only_that_file(tmp_path: Path) -> None:
    seed(tmp_path, {"a.py": '"""A。"""\nimport b\n', "b.py": MODULE, "c.py": MODULE})
    RepoMap(Workspace(tmp_path), store=store_for(tmp_path)).build()

    (tmp_path / "b.py").write_text(MODULE + "\ndef extra():\n    pass\n", encoding="utf-8")
    second = RepoMap(Workspace(tmp_path), store=store_for(tmp_path))
    second.build()
    assert second.stats.modules_parsed == 1 and second.stats.from_cache == 2
    assert "def extra()" in second.build()


def test_map_invalidates_on_edit(tmp_path: Path) -> None:
    """写完文件必须立刻能看见，否则 agent 下一轮不知道它刚建了什么，会重写一遍。"""
    mapping = RepoMap(seed(tmp_path, {"a.py": MODULE}), store=store_for(tmp_path))
    mapping.build()
    assert "new.py" not in mapping.build()

    (tmp_path / "new.py").write_text(NEW_MODULE, encoding="utf-8")
    mapping.invalidate(["new.py"])
    assert "def fresh(x)" in mapping.build()
    assert store_for(tmp_path).keys(MemKind.REPO_MAP) == ["a.py", "new.py"]


def test_map_skips_memory_dir(tmp_path: Path) -> None:
    """§3.4：agent 把自己的缓存当代码读进去，就会开始给自己写笔记。"""
    mapping = RepoMap(seed(tmp_path, {"a.py": MODULE, f"{MEMORY_DIRNAME}/stale.py": MODULE}))
    text = mapping.build()
    assert "stale.py" not in text and MEMORY_DIRNAME not in text
    assert mapping.stats.modules_found == 1


def test_unparsable_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    """语法错误是常态（agent 正改到一半），不是故障。"""
    mapping = RepoMap(seed(tmp_path, {"good.py": MODULE, "broken.py": "def (:\n"}))
    text = mapping.build()
    assert "def checkout" in text and "broken.py" not in text
    assert mapping.stats.unparsable == 1


def test_build_is_the_same_map_as_relevant_without_focus(tmp_path: Path) -> None:
    mapping = RepoMap(seed(tmp_path, {"a.py": MODULE}))
    assert mapping.build() == mapping.relevant([])
    with pytest.raises(ValueError):
        mapping.build(Workspace(tmp_path / "elsewhere"))


def test_map_for_prompt_does_not_jitter(tmp_path: Path) -> None:
    """§3.4：只按仓库指纹更新。焦点每轮都在动，跟着重画就等于每轮打掉前缀缓存。"""
    mapping = RepoMap(seed(tmp_path, {"a.py": MODULE, "b.py": SCRIPT}))
    first = mapping.map_for_prompt()
    assert mapping.refreshes == 1
    mapping.observe_paths(["b.py"])
    assert mapping.map_for_prompt() is first
    assert mapping.refreshes == 1, "地图重画了，但仓库没变 —— 抖动就是从这里长出来的"

    (tmp_path / "c.py").write_text(MODULE, encoding="utf-8")
    mapping.invalidate(["c.py"])
    second = mapping.map_for_prompt()
    assert "c.py" in second and second is not first and mapping.refreshes == 2


def test_forget_focus_reranks_on_the_next_render(tmp_path: Path) -> None:
    mapping = RepoMap(seed(tmp_path, {"deep/pkg/a.py": MODULE, "top.py": MODULE}))
    mapping.observe_paths(["deep/pkg/a.py"])
    with_focus = mapping.map_for_prompt()
    mapping.forget_focus()
    without_focus = mapping.map_for_prompt()
    assert with_focus != without_focus
    assert "[recent]" in with_focus and "[recent]" not in without_focus
    assert mapping.stats.focus == ()


def test_two_instances_over_the_same_repo_agree(tmp_path: Path) -> None:
    seed(tmp_path, {f"m{index}.py": MODULE for index in range(5)})
    assert RepoMap(Workspace(tmp_path)).build() == RepoMap(Workspace(tmp_path)).build()


# ------------------------------------------------------------ 纯函数


def test_signature_matches_the_eval_canonical_form() -> None:
    """剧本与判据共用一个"规范签名"定义，否则"逐字一致"就变成两边各写一套归一化规则。"""
    func = next(
        node
        for node in ast.parse(MODULE).body
        if isinstance(node, ast.FunctionDef) and node.name == "checkout"
    )
    assert function_signature(func) == f"def checkout({ast.unparse(func.args)})"


def test_import_index_resolves_relative_and_qualified_imports() -> None:
    index = build_import_index(["pkg/__init__.py", "pkg/store.py", "pkg/deep/leaf.py"])
    assert parse_imports("from . import store", rel="pkg/__init__.py", index=index) == ["pkg/store.py"]
    assert parse_imports("from .deep import leaf", rel="pkg/__init__.py", index=index) == [
        "pkg/deep/leaf.py"
    ]
    assert parse_imports("import os.path", rel="pkg/store.py", index=index) == []
    symbols, targets = analyze_source("import json\n", rel="pkg/store.py", index=index)
    assert targets == [] and symbols[0].kind == "module"


def test_private_names_and_overflow_are_left_out() -> None:
    source = "\n\n".join(f"def _h{i}():\n    pass\n\ndef pub{i}():\n    pass" for i in range(20))
    body = render_file_lines(extract_symbols(source), max_symbols=6)[1:]
    assert not [line for line in body if line.startswith("def _h")]
    assert len(body) == 7 and "另有 14 个符号未列" in body[-1]


# ================================================================ 接线


def test_map_replaces_the_tree_when_provided(tmp_path: Path) -> None:
    ws = seed(tmp_path, {"a.py": MODULE})

    def assemble(provider: Callable[[], str] | None) -> str:
        return build_system_prompt(
            project_root=ws.root,
            platform="win32",
            model="m",
            python_executable="python",
            tool_names=["read_file"],
            workspace=ws,
            map_provider=provider,
        )

    on = assemble(RepoMap(ws).map_for_prompt)
    off = assemble(None)
    assert "# 仓库地图（ast 符号" in on and "# 仓库形状" not in on
    assert "# 仓库形状" in off and "a.py" in off, "对照臂必须是一张真能跑的树，不是把段落删掉"


def test_notes_and_todos_come_after_the_map(tmp_path: Path) -> None:
    """提示词缓存吃最长公共前缀：会话内越稳定的东西越该往前放。"""
    mapping = RepoMap(seed(tmp_path, {"a.py": MODULE}))
    todos = TodoList()
    todos.replace([TodoItem("读 a.py")])
    agent, _ = make_agent(
        mapping.ws, [], tmp_path=tmp_path, repo_map=mapping, notes="# 工作记忆\n- [c] 约定", todos=todos
    )
    system = agent.current_system()
    assert system.index("# 仓库地图") < system.index("# 工作记忆") < system.index("# 当前任务清单")


def test_a_read_never_rerenders_the_map_mid_session(tmp_path: Path) -> None:
    """读文件不改仓库：地图照旧，焦点只记着，等下一次真该重画时再用。"""
    mapping = RepoMap(seed(tmp_path, {"pkg/a.py": MODULE, "other/b.py": MODULE}))
    agent, llm = make_agent(
        mapping.ws,
        [scripted_tool_calls([("read_file", {"path": "pkg/a.py"})]), scripted_final_text("看清了")],
        tmp_path=tmp_path,
        repo_map=mapping,
    )
    agent.run("看 pkg/a.py")
    assert llm.calls[0]["system"] == llm.calls[1]["system"]
    assert mapping.refreshes == 1


def test_observed_reads_shape_the_next_rerender(tmp_path: Path) -> None:
    mapping = RepoMap(seed(tmp_path, {"pkg/a.py": MODULE, "other/b.py": MODULE}))
    agent, llm = make_agent(
        mapping.ws,
        [
            scripted_tool_calls([("read_file", {"path": "pkg/a.py"})]),
            scripted_tool_calls([("write_file", {"path": "new.py", "content": NEW_MODULE})]),
            scripted_final_text("建好了"),
        ],
        tmp_path=tmp_path,
        repo_map=mapping,
    )
    agent.run("先看 pkg/a.py，再新建 new.py")
    assert "[recent]" not in llm.calls[0]["system"], "第一轮还没有任何观察"
    assert "def fresh(x)" in llm.calls[2]["system"] and "[recent]" in llm.calls[2]["system"]
    assert mapping.stats.focus == ("new.py", "pkg/a.py")


def test_a_denied_write_leaves_the_map_alone(tmp_path: Path) -> None:
    """被权限门拒掉的调用没碰磁盘。把它算进焦点，就是拿一个没发生的动作影响排序。"""
    mapping = RepoMap(seed(tmp_path, {"a.py": MODULE}))
    agent, llm = make_agent(
        mapping.ws,
        [
            scripted_tool_calls([("write_file", {"path": "new.py", "content": "def fresh(): pass\n"})]),
            scripted_final_text("被拒了"),
        ],
        tmp_path=tmp_path,
        mode=PermissionMode.ASK,
        repo_map=mapping,
    )
    result = agent.run("新建 new.py")
    assert not (tmp_path / "new.py").exists()
    assert result.state.denied_actions == 1
    assert llm.calls[0]["system"] == llm.calls[1]["system"]
    assert mapping.stats.focus == () and mapping.refreshes == 1


def test_reset_clears_the_focus_of_the_previous_task(tmp_path: Path) -> None:
    mapping = RepoMap(seed(tmp_path, {"pkg/a.py": MODULE, "pkg/b.py": MODULE}))
    agent, _ = make_agent(
        mapping.ws,
        [scripted_tool_calls([("read_file", {"path": "pkg/a.py"})]), scripted_final_text("结束")],
        tmp_path=tmp_path,
        repo_map=mapping,
    )
    agent.run("看 pkg/a.py")
    # 读本身不重画（地图不该因为一次读就抖），所以这里另起一次重画才看得见焦点。
    (tmp_path / "trigger.py").write_text(MODULE, encoding="utf-8")
    mapping.invalidate(["trigger.py"])
    assert "[recent]" in mapping.map_for_prompt()
    assert mapping.stats.focus == ("pkg/a.py",), "重画之前焦点确实记着上一个任务读过的文件"
    agent.reset()
    assert "[recent]" not in mapping.map_for_prompt(), "新任务不该被上一个任务读过的文件带着排"
    assert mapping.stats.focus == ()


def test_repo_map_events_land_in_the_trace(tmp_path: Path) -> None:
    mapping = RepoMap(seed(tmp_path, {"a.py": MODULE}))
    agent, _ = make_agent(
        mapping.ws,
        [
            scripted_tool_calls([("write_file", {"path": "new.py", "content": NEW_MODULE})]),
            scripted_final_text("好了"),
        ],
        tmp_path=tmp_path,
        repo_map=mapping,
    )
    agent.run("建个文件")
    events = [record for record in replay(agent.tracer.path) if record["kind"] == "repo_map"]
    assert [event["turn"] for event in events] == [1, 2]
    assert events[0]["listed"] == 1 and events[1]["listed"] == 2
    assert events[1]["rebuilt"] is True and events[1]["focus"] == ["new.py"]
