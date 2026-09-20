"""系统提示测试 —— 盯的是"模型会不会写出跑不了的命令"。

提示词的内容不需要逐字断言（那会让每次措辞改动都红），只断言三类事实：
环境事实对不对、仓库地图是否守预算、以及是否泄露了不该进去的东西。
"""

from __future__ import annotations

from pathlib import Path

from miniclaude.agent.prompts import build_system_prompt, render_repo_map
from miniclaude.tools.workspace import Workspace


def make_prompt(root: Path, *, platform: str = "win32", tools: tuple[str, ...] = (), max_map_lines: int = 30) -> str:
    ws = Workspace(root)
    return build_system_prompt(
        project_root=root,
        platform=platform,
        model="mock-model",
        python_executable="C:/py/python.exe",
        tool_names=tools,
        workspace=ws,
        max_map_lines=max_map_lines,
    )


def test_windows_facts_are_injected(tmp_path: Path) -> None:
    prompt = make_prompt(tmp_path, platform="win32")
    assert ">/dev/null" in prompt  # bash 工具优先 Git Bash，所以 /dev/null 才是对的
    assert "不要 `>NUL`" in prompt  # 真端点曾据此建出一个删不掉的 NUL 文件（保留设备名）
    assert "不要在 shell 里用 cat" in prompt  # 否则模型会写 cat src/a.py
    assert "不是 `python3`" in prompt
    assert "Windows" in prompt


def test_posix_facts_replace_windows_ones(tmp_path: Path) -> None:
    prompt = make_prompt(tmp_path, platform="linux")
    assert ">NUL" not in prompt
    assert "Git Bash" not in prompt
    assert "python3" in prompt
    assert "保留设备名" not in prompt


def test_macos_gets_unix_treatment(tmp_path: Path) -> None:
    assert "Git Bash" not in make_prompt(tmp_path, platform="darwin")
    assert "python3" in make_prompt(tmp_path, platform="darwin")


def test_environment_and_identity_present(tmp_path: Path) -> None:
    prompt = make_prompt(tmp_path, tools=("read_file", "bash"))
    assert str(tmp_path.resolve()) in prompt
    assert "mock-model" in prompt
    assert "C:/py/python.exe" in prompt
    assert "Mini Claude Code" in prompt
    # 契约要点必须都在，缺一条模型就会少走一条弯路
    for needle in ("write_todos", "run_tests", "edit_file", "old_string", "密钥"):
        assert needle in prompt, needle


def test_tool_list_names_every_registered_tool(tmp_path: Path) -> None:
    """工具清单与 registry 必须同源 —— 漏一个名字，模型就少一个能力。"""
    names = ("bash", "edit_file", "find_files", "read_file", "run_tests", "write_todos")
    prompt = make_prompt(tmp_path, tools=names)
    line = next(line for line in prompt.splitlines() if line.startswith("你只有这些工具"))
    for name in names:
        assert name in line
    assert "不要虚构工具名" in prompt or "其余一律不存在" in prompt


def test_tool_section_without_names_still_forbids_invention(tmp_path: Path) -> None:
    prompt = make_prompt(tmp_path, tools=())
    assert "只有报文里声明的工具存在" in prompt


def test_prompt_is_stable_across_calls(tmp_path: Path) -> None:
    """同一会话内 system 必须逐字节稳定，否则每次请求的提示缓存都白费。"""
    assert make_prompt(tmp_path, tools=("read_file",)) == make_prompt(tmp_path, tools=("read_file",))


# --------------------------------------------------------------- 仓库地图


def build_tree(root: Path) -> Workspace:
    (root / "src" / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "app" / "core.py").write_text("x = 1\n", encoding="utf-8")
    (root / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (root / "tests" / "test_core.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / "src" / "__pycache__").mkdir()
    (root / "src" / "__pycache__" / "main.cpython-314.pyc").write_text("junk", encoding="utf-8")
    return Workspace(root)


def test_repo_map_shows_shape_not_noise(tmp_path: Path) -> None:
    ws = build_tree(tmp_path)
    rendered = render_repo_map(ws)

    assert "src/" in rendered
    assert "main.py" in rendered
    assert "test_core.py" in rendered
    # 这三类条目对模型毫无价值，还会把它引向歧路
    assert "__pycache__" not in rendered
    assert ".git" not in rendered
    assert ".env" not in rendered
    assert "SECRET" not in rendered


def test_repo_map_is_breadth_first(tmp_path: Path) -> None:
    """预算有限时先给"仓库有多宽"，而不是一条道走到某个子目录深处。"""
    ws = build_tree(tmp_path)
    tree = render_repo_map(ws, max_lines=20).splitlines()[1:]     # 去掉 "# 仓库形状"
    depth_of = {line.strip(): len(line) - len(line.lstrip()) for line in tree}
    order = [line.strip() for line in tree]

    assert depth_of["src/"] == 2 and depth_of["README.md"] == 2
    assert depth_of["main.py"] == 4          # src 的下一层
    # 同层的 README 排在下一层的 main.py 之前 —— 这才叫按层展开
    assert order.index("src/") < order.index("README.md") < order.index("main.py")


def test_repo_map_respects_line_budget(tmp_path: Path) -> None:
    ws = build_tree(tmp_path)
    for budget in (4, 8, 12, 30):
        rendered = render_repo_map(ws, max_lines=budget)
        # 标题不算预算，树本身（根名 + 条目 + 提示）必须守住
        assert len(rendered.splitlines()) - 1 <= budget, budget

    crowded = tmp_path / "crowded"
    crowded.mkdir()
    for index in range(60):
        (crowded / f"module_{index:02d}.py").write_text("x\n", encoding="utf-8")
    rendered = render_repo_map(Workspace(crowded), max_lines=10)
    assert "find_files" in rendered          # 必须告诉模型怎么继续查，而不是让它以为仓库只有这些
    assert len([line for line in rendered.splitlines() if "module_" in line]) == 8


def test_repo_map_of_empty_dir(tmp_path: Path) -> None:
    rendered = render_repo_map(Workspace(tmp_path))
    assert tmp_path.name in rendered
    assert "省略" not in rendered


def test_empty_workspace_root_name_falls_back(tmp_path: Path) -> None:
    """盘符根这类没有 name 的路径不能让地图变成空标题。"""
    ws = Workspace(tmp_path)
    assert render_repo_map(ws, header="(仓库根)").startswith("# 仓库形状\n(仓库根)")
