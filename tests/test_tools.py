"""工具层测试：全部在 tmp_path 里造临时仓库，不打网络。

覆盖 SPEC §6.2 的要求：每个工具至少 3 例，且**必含错误路径** ——
工具的失败文案是模型唯一的自愈线索，它比成功路径更值得测。
"""

from __future__ import annotations

import sys

import pytest

from miniclaude.messages import ToolUseBlock
from miniclaude.tools import (
    BashTool,
    EditFileTool,
    FindFilesTool,
    PathEscape,
    ReadFileTool,
    RiskLevel,
    RunTestsTool,
    SearchTextTool,
    ToolRegistry,
    ToolResult,
    Workspace,
    WriteFileTool,
)


@pytest.fixture()
def ws(tmp_path) -> Workspace:
    return Workspace(root=tmp_path, output_limit=2000, max_read_lines=50)


@pytest.fixture()
def repo(ws, tmp_path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir(exist_ok=True)
    return tmp_path


def use(name: str, **args) -> ToolUseBlock:
    return ToolUseBlock(id="call_test", name=name, input=args)


# ------------------------------------------------------------------ read_file


def test_read_file_returns_numbered_lines(ws, repo):
    result = ReadFileTool(ws).run(path="src/calc.py")
    assert not result.is_error
    assert "1\tdef add(a, b):" in result.content
    assert "5\tdef sub(a, b):" in result.content


def test_read_file_offset_and_limit(ws, repo):
    result = ReadFileTool(ws).run(path="src/calc.py", offset=4, limit=2)
    assert not result.is_error
    assert "4\t" in result.content and "5\t" in result.content
    assert "6\tdef sub" not in result.content


def test_read_file_missing_path_is_error_not_exception(ws):
    result = ReadFileTool(ws).invoke(use("read_file", path="nope.py"))
    assert result.is_error and "文件不存在" in result.content


def test_read_file_rejects_escape_outside_root(ws):
    result = ReadFileTool(ws).invoke(use("read_file", path="../../etc/passwd"))
    assert result.is_error and "工作区之外" in result.content


def test_read_file_on_directory_lists_entries(ws, repo):
    result = ReadFileTool(ws).run(path="src")
    assert result.is_error and "calc.py" in result.content


# ------------------------------------------------------------------ search_text


def test_search_text_finds_definition_with_location(ws, repo):
    result = SearchTextTool(ws).run(pattern="def sub")
    assert not result.is_error
    assert "src/calc.py:5:" in result.content


def test_search_text_respects_path_glob(ws, repo):
    (repo / "notes.txt").write_text("def sub in prose\n", encoding="utf-8")
    py_only = SearchTextTool(ws).run(pattern="def sub", path_glob="**/*.py")
    assert "notes.txt" not in py_only.content
    both = SearchTextTool(ws).run(pattern="def sub", path_glob="**/*")
    assert "notes.txt" in both.content


def test_search_text_reports_no_match_and_bad_regex(ws, repo):
    assert "没有匹配" in SearchTextTool(ws).run(pattern="nothing_here_zzz").content
    bad = SearchTextTool(ws).run(pattern="(unclosed")
    assert bad.is_error and "正则表达式非法" in bad.content


def test_search_text_skips_binary_and_ignored_dirs(ws, repo):
    (repo / "blob.png").write_bytes(b"\x89PNG\x00\x01def sub")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "x.py").write_text("def sub_hidden(): pass\n", encoding="utf-8")
    result = SearchTextTool(ws).run(pattern="def sub")
    assert "blob.png" not in result.content
    assert "__pycache__" not in result.content


# ------------------------------------------------------------------ find_files


def test_find_files_matches_glob(ws, repo):
    result = FindFilesTool(ws).run(pattern="**/*.py")
    assert not result.is_error and "src/calc.py" in result.content


def test_find_files_skips_noise_dirs(ws, repo):
    (repo / ".venv").mkdir()
    (repo / ".venv" / "lib.py").write_text("x = 1\n", encoding="utf-8")
    assert "lib.py" not in FindFilesTool(ws).run(pattern="**/*.py").content


def test_find_files_rejects_escaping_pattern(ws):
    result = FindFilesTool(ws).invoke(use("find_files", pattern="../**/*"))
    assert result.is_error and "非法 glob" in result.content


# ------------------------------------------------------------------ write_file


def test_write_file_creates_and_reports_lines(ws):
    result = WriteFileTool(ws).run(path="pkg/mod.py", content="import os\nprint(os.name)\n")
    assert not result.is_error and "新建" in result.content and "2 行" in result.content
    assert (ws.root / "pkg" / "mod.py").exists()


def test_write_file_overwrite_reports_old_line_count(ws, repo):
    result = WriteFileTool(ws).run(path="src/calc.py", content="x = 1\n")
    assert not result.is_error and "覆盖" in result.content and "6 行" in result.content


def test_write_file_rejects_escape(ws):
    assert WriteFileTool(ws).invoke(use("write_file", path="../evil.py", content="x")).is_error


# ------------------------------------------------------------------ edit_file


def test_edit_file_replaces_unique_match(ws, repo):
    result = EditFileTool(ws).run(path="src/calc.py", old_string="return a - b", new_string="return a - b  # noqa")
    assert not result.is_error
    assert "return a - b  # noqa" in (repo / "src" / "calc.py").read_text(encoding="utf-8")


def test_edit_file_zero_match_tells_model_to_reread(ws, repo):
    result = EditFileTool(ws).run(path="src/calc.py", old_string="def mul(a, b):", new_string="def mul(a,b):\n    pass")
    assert result.is_error
    assert "read_file" in result.content and "逐字符一致" in result.content


def test_edit_file_ambiguous_match_reports_line_numbers(ws, repo):
    (repo / "src" / "dup.py").write_text("v = 1\nv = 1\nv = 1\n", encoding="utf-8")
    result = EditFileTool(ws).run(path="src/dup.py", old_string="v = 1", new_string="v = 2")
    assert result.is_error and "3 处" in result.content and "1, 2, 3" in result.content


def test_edit_file_replace_all(ws, repo):
    (repo / "src" / "dup.py").write_text("v = 1\nv = 1\nv = 1\n", encoding="utf-8")
    result = EditFileTool(ws).run(path="src/dup.py", old_string="v = 1", new_string="v = 2", replace_all=True)
    assert not result.is_error and "替换 3 处" in result.content


def test_edit_file_missing_file_points_to_write_file(ws):
    result = EditFileTool(ws).invoke(use("edit_file", path="x.py", old_string="a", new_string="b"))
    assert result.is_error and "write_file" in result.content


# ------------------------------------------------------------------ bash


def test_bash_captures_stdout_and_exit_code(ws):
    result = BashTool(ws).run(command="python -c \"print('hi')\"" if sys.platform != "win32" else 'python -c "print(\'hi\')"')
    assert not result.is_error and "退出码：0" in result.content and "hi" in result.content


def test_bash_nonzero_exit_is_observation_not_tool_error(ws):
    result = BashTool(ws).run(command="python -c \"import sys; sys.exit(3)\"")
    assert not result.is_error          # 工具成功交付了观察
    assert "退出码：3" in result.content and "失败" in result.content


def test_bash_timeout_is_tool_error(ws):
    result = BashTool(ws).run(command="python -c \"import time; time.sleep(30)\"", timeout=2)
    assert result.is_error and "超时" in result.content


def test_bash_runs_inside_workspace_only(ws, repo):
    result = BashTool(ws).run(command='python -c "import os; print(os.getcwd())"')
    assert not result.is_error
    assert repo.name.lower() in result.content.lower()


def test_bash_blocks_obviously_interactive_commands(ws):
    result = BashTool(ws).invoke(use("bash", command="vim notes.txt"))
    assert result.is_error and "交互" in result.content


# ------------------------------------------------------------------ run_tests


def _make_tests(repo, body_pass: bool):
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_calc.py").write_text(
        "from src.calc import add\n\n\ndef test_add_ok():\n    assert add(2, 3) == 5\n\n\n"
        + ("def test_trivial():\n    assert True\n" if body_pass else "def test_sub():\n    from src.calc import sub\n    assert sub(5, 3) == 1\n"),
        encoding="utf-8",
    )
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tests" / "__init__.py").write_text("", encoding="utf-8")


def test_run_tests_reports_green(ws, repo):
    _make_tests(repo, body_pass=True)
    result = RunTestsTool(ws).run()
    assert not result.is_error and "PASSED" in result.content and "退出码 0" in result.content


def test_run_tests_names_the_failing_case(ws, repo):
    _make_tests(repo, body_pass=False)
    result = RunTestsTool(ws).run()
    assert not result.is_error            # 同上：红测试不是工具故障
    assert "FAILED" in result.content and "1 个失败" in result.content
    assert "test_sub" in result.content


def test_run_tests_targets_one_case(ws, repo):
    _make_tests(repo, body_pass=False)
    result = RunTestsTool(ws).run(target="tests/test_calc.py::test_add_ok")
    assert "PASSED" in result.content and "范围：tests/test_calc.py::test_add_ok" in result.content


def test_run_tests_handles_import_error_without_output(ws, repo):
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_broken.py").write_text("import nonexistent_module_xyz\n", encoding="utf-8")
    result = RunTestsTool(ws).run()
    assert "FAILED" in result.content


# ------------------------------------------------------------------ 注册与契约


def test_registry_default_has_seven_tools_and_stable_specs(ws):
    registry = ToolRegistry.default(ws)
    assert set(registry.names()) == {
        "read_file", "search_text", "find_files", "edit_file", "write_file", "bash", "run_tests",
    }
    assert [spec.name for spec in registry.specs()] == sorted(registry.names())


def test_registry_rejects_duplicate_names(ws):
    registry = ToolRegistry.default(ws)
    with pytest.raises(Exception):
        registry.register(ReadFileTool(ws))


def test_risk_levels_are_declared_per_tool(ws):
    registry = ToolRegistry.default(ws)
    assert registry.get("read_file").risk_level is RiskLevel.READ
    assert registry.get("write_file").risk_level is RiskLevel.WRITE
    assert registry.get("bash").risk_level is RiskLevel.EXECUTE


@pytest.mark.parametrize(
    "tool_cls,args",
    [
        (ReadFileTool, {"path": 12345}),
        (ReadFileTool, {}),
        (SearchTextTool, {"pattern": "[bad"}),
        (EditFileTool, {"path": "a.py", "old_string": "x"}),
        (BashTool, {"command": ""}),
        (FindFilesTool, {"pattern": None}),
        (WriteFileTool, {"path": "x", "content": {"not": "a string"}}),
    ],
)
def test_tool_invoke_never_raises(ws, repo, tool_cls, args):
    """核心契约：任何坏参数都只能换来一个 is_error 结果，绝不能打断 Agent。"""
    tool = tool_cls(ws)
    result = tool.invoke(use(tool.name, **args))
    assert isinstance(result, ToolResult)


def test_validate_rejects_wrong_types_and_missing_required(ws):
    tool = ReadFileTool(ws)
    assert "缺少必填参数" in tool.validate({})
    assert "应为 string" in tool.validate({"path": 42})
    assert "应为 integer" in tool.validate({"path": "a.py", "limit": "many"})
    assert tool.validate({"path": "a.py"}) is None


def test_unknown_extra_args_are_dropped_not_forwarded(ws, repo):
    result = ReadFileTool(ws).invoke(use("read_file", path="src/calc.py", encoding="latin-1", mode="rb"))
    assert not result.is_error


def test_malformed_json_marker_becomes_actionable_error(ws):
    result = ReadFileTool(ws).invoke(
        use("read_file", __unparseable_arguments='{"path": "a.py"')
    )
    assert result.is_error and "合法 JSON" in result.content


def test_path_escape_helper_rejects_absolute_outside(ws):
    with pytest.raises(PathEscape):
        ws.resolve(sys.executable)
