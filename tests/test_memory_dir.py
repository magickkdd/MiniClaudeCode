"""`MEMORY_DIR` 的路径收敛（SPEC v2 §3.6）。

改一个目录名要同时改到七处才算数：记忆缓存、影子仓库、会话现场、搜索/列举的忽略清单、
仓库地图的遍历、评测判定的噪声清单，以及"除此之外工作区里不该多出第二个目录"。
这七处各自写死 `.mcc` 的话，改名的后果不是报错而是**静默分叉** —— 现场落在 A，
判定去 B 找，影子仓库被当成源码改动。所以这里逐个钉住，而不是只测 `Config.memory_root`。

`Config.from_env` 的这条线也在场：`MEMORY_DIR` 只能从环境进来，它读不到就等于没接线。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fakes import FakeLLM, scripted_final_text, scripted_tool_calls

from miniclaude.agent.permissions import PermissionMode
from miniclaude.backend.checkpoints import SNAPSHOTS_DIRNAME
from miniclaude.backend.sessions import SESSIONS_DIRNAME
from miniclaude.cli import main as cli
from miniclaude.config import Config, ConfigError
from miniclaude.eval.contract import isolate, tracked_files
from miniclaude.memory import MEMORY_DIRNAME
from miniclaude.tools.workspace import IGNORED_DIRS

CUSTOM = ".brain"
MARKER = "sneak_marker_zzz"


def write_session(root: Path, *, memory_dir: str = CUSTOM, **overrides: Any) -> cli.Session:
    """真装配一份会话，跑一次写入。工具、后端、现场目录全都按 §3.6 接好。"""
    config = Config(
        base_url="https://mock.invalid/v1",
        api_key="sk-not-a-real-key-0000000000",
        model="mock-model",
        project_root=root,
        trace_path=root / ".traces" / "memory-dir.jsonl",
        memory_dir=memory_dir,
        **overrides,
    )
    session = cli.build_session(
        config=config,
        mode=PermissionMode.AUTO,
        llm=FakeLLM(
            [
                scripted_tool_calls([("write_file", {"path": "made.py", "content": "MADE = 1\n"})]),
                scripted_final_text("写好了。"),
            ]
        ),
    )
    session.agent.run("写一个 made.py")
    return session


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root


# --------------------------------------------------------------- 装配


def test_every_derived_file_lands_under_the_custom_dir(project: Path) -> None:
    """一次跑批之后，工作区里多出来的只有 `.brain/`，一个 `.mcc/` 都不该有。"""
    session = write_session(project)
    memory = project / CUSTOM
    assert (memory / SESSIONS_DIRNAME / f"{session.tracer.session_id}.json").exists(), "现场没跟着改名"
    assert (memory / SNAPSHOTS_DIRNAME / "HEAD").exists(), "影子仓库没跟着改名"
    assert (memory / "memory.json").exists(), "记忆缓存没跟着改名"
    assert not (project / MEMORY_DIRNAME).exists(), f"还是在 {MEMORY_DIRNAME}/ 里写了东西"


def test_the_custom_dir_is_ignored_by_the_tools_the_model_can_call(project: Path) -> None:
    """忽略清单必须由**这次装配**的 workspace 给，不是由工具各自记一个 `.mcc`。

    造文件用的是 `sneak_marker_zzz` 这种一眼假的串：命中它才算"模型真的看得见记忆目录"，
    只比文件名的话，一次搜索没返回结果也可能是别的原因。
    """
    session = write_session(project)
    sneaky = project / CUSTOM / "sneak.py"
    sneaky.parent.mkdir(parents=True, exist_ok=True)
    sneaky.write_text(f"{MARKER} = 1\n", encoding="utf-8")

    assert CUSTOM in session.workspace.ignored_dirs
    search = session.registry.get("search_text").run(pattern=MARKER)
    assert "没有匹配" in search.content, f"搜索结果里出现了记忆目录：{search.content[:200]}"
    listing = session.registry.get("find_files").run(pattern=f"**/{sneaky.name}")
    # 空结果会把模式原样回显进文案，所以判"没找到"要判那句话，不能判文件名。
    assert "没有文件匹配" in listing.content


def test_the_repo_map_never_reads_its_own_cache(project: Path) -> None:
    """地图读到自己的缓存就是自我强化回路（§3.4）；改名之后这条更得成立。"""
    (project / CUSTOM).mkdir()
    (project / CUSTOM / "sneak.py").write_text(f"def {MARKER}():\n    return 1\n", encoding="utf-8")
    session = write_session(project)
    assert session.agent.repo_map is not None, "这次装配没建地图，那条断言就成了空话"
    rendered = session.agent.repo_map.map_for_prompt()
    assert MARKER not in rendered and CUSTOM not in rendered


def test_backend_panel_names_the_custom_dir(project: Path) -> None:
    """`/backend` 是用户查"我的东西写在哪"的地方，报一个没在用的目录名比不报更坏。"""
    session = write_session(project)
    panel = cli.render_backend(session)
    assert str(project / CUSTOM) in panel and MEMORY_DIRNAME not in panel


# --------------------------------------------------------------- 判定层


def test_eval_contract_ignores_the_configured_name_and_nothing_else(tmp_path: Path) -> None:
    """判定排除的是**这一次**用的那个目录名。

    反方向同样重要：`MEMORY_DIR=.brain` 时工作区里凭空多出一个 `.mcc/`，那不是派生物，
    而是真真切切的多出来的内容 —— 把它一起忽略掉，就是给"模型偷偷写了别的东西"开门。
    """
    (tmp_path / CUSTOM).mkdir()
    (tmp_path / CUSTOM / "memory.json").write_text("{}", encoding="utf-8")
    (tmp_path / MEMORY_DIRNAME).mkdir()
    (tmp_path / MEMORY_DIRNAME / "stray.json").write_text("{}", encoding="utf-8")
    (tmp_path / "real.py").write_text("VALUE = 1\n", encoding="utf-8")

    assert set(tracked_files(tmp_path, memory_dir=CUSTOM)) == {"real.py", f"{MEMORY_DIRNAME}/stray.json"}
    assert set(tracked_files(tmp_path)) == {"real.py", f"{CUSTOM}/memory.json"}, "默认口径只认 .mcc"


def test_isolate_does_not_carry_the_previous_run_over(tmp_path: Path) -> None:
    """复制考题时排除记忆目录，否则上一次跑批的缓存会被当成"基线里本来就有"。"""
    source = tmp_path / "exam"
    (source / CUSTOM).mkdir(parents=True)
    (source / CUSTOM / "memory.json").write_text("{}", encoding="utf-8")
    (source / "pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    work = isolate(source, tmp_path / "work", memory_dir=CUSTOM)
    assert (work / "pkg.py").exists()
    assert not (work / CUSTOM).exists()


# --------------------------------------------------------------- 配置入口


def _env(monkeypatch: pytest.MonkeyPatch, root: Path, **extra: str) -> None:
    """只留下这条测试要读的那几个变量。

    必须显式清掉 LLM_*：本机仓库根有一份真的 `.env`，而 `from_env` 在被要求读它之外
    还会看进程环境 —— 不清就会把真实端点配置漏进一个本该自给自足的用例。
    """
    for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "OPENAI_API_KEY", "MEMORY_DIR", "PROJECT_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://mock.invalid/v1")
    monkeypatch.setenv("LLM_MODEL", "mock-model")
    monkeypatch.setenv("LLM_API_KEY", "sk-not-a-real-key-0000000000")
    monkeypatch.setenv("PROJECT_ROOT", str(root))
    for name, value in extra.items():
        monkeypatch.setenv(name, value)


def test_memory_dir_comes_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, tmp_path, MEMORY_DIR=CUSTOM)
    config = Config.from_env(env_file=tmp_path / "absent.env")
    assert config.memory_dir == CUSTOM
    assert config.memory_root == tmp_path / CUSTOM


@pytest.mark.parametrize(
    "value",
    [".brain", "state/memory", "/abs/memory"],
    ids=["renamed", "nested", "absolute"],
)
def test_memory_root_follows_the_configured_shape(tmp_path: Path, value: str) -> None:
    """改名、套层、给绝对路径 —— 三种写法都只应有**一个**落盘点，就是 `memory_root`。

    嵌套名尤其要说清：忽略清单按目录名（`Path(...).name`）匹配，所以 `state/memory`
    要靠 `memory/` 这一段隐身；绝对路径则是把现场与工作区彻底分开，不能被拼回根里。
    """
    config = Config(
        base_url="https://mock.invalid/v1",
        api_key="sk-not-a-real-key-0000000000",
        model="m",
        project_root=tmp_path,
        memory_dir=value,
    )
    mem = Path(value)
    assert config.memory_root == (mem if mem.is_absolute() else tmp_path / mem)


@pytest.mark.parametrize("value", ["", "  ", "../outside", "a/../../b"])
def test_hostile_memory_dir_names_are_rejected_at_config_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """空串会让所有落盘点变成工作区本身，`..` 会把现场写到别人家里。

    这两种都必须在启动时炸，而不是等到第一次写盘才发现 —— 后者会先创建出一半的目录。
    """
    _env(monkeypatch, tmp_path, MEMORY_DIR=value)
    with pytest.raises(ConfigError) as excinfo:
        Config.from_env(env_file=tmp_path / "absent.env")
    assert "MEMORY_DIR" in str(excinfo.value)


def test_default_name_is_a_single_origin(tmp_path: Path) -> None:
    """缺省名只有一处产地（`config.DEFAULTS`），别处都是引用它。

    `IGNORED_DIRS` 里那一项是唯一一个不得不重复字面量的地方 —— 它是"默认忽略清单"的
    成员，而清单本身要在没有 Config 的场合（工具单测）也能用。这条测试钉住两边不打架。
    """
    config = Config(
        base_url="https://mock.invalid/v1",
        api_key="sk-not-a-real-key-0000000000",
        model="m",
        project_root=tmp_path,
    )
    assert config.memory_dir == MEMORY_DIRNAME == ".mcc"
    assert MEMORY_DIRNAME in IGNORED_DIRS
