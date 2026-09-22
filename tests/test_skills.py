"""`ext/skills.py` 的测试（SPEC v2 §3.7 的 Skills 半边、D20）。

这一层的核心主张只有一句：**常驻的是目录，正文要报价**。所以最重要的两条
测试是成本那条（目录长度不随正文增长）和"不读兄弟文件"那条（D20 的取舍）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from miniclaude.agent.permissions import Decision, PermissionGate, PermissionMode
from miniclaude.ext import LoadSkillTool, SkillError, SkillLoader
from miniclaude.messages import ToolUseBlock
from miniclaude.tools.base import RiskLevel
from miniclaude.tools.workspace import Workspace

BODY = "\n".join(f"第 {i} 条指令，照着做就行。" for i in range(1, 121))


def skill_dir(
    root: Path,
    dirname: str,
    *,
    skill_name: str | None = None,
    description: str = "一句话说明",
    body: str = BODY,
    **fields: str,
) -> Path:
    entry = root / dirname
    entry.mkdir(parents=True, exist_ok=True)
    head = [f"name: {skill_name or dirname}", f"description: {description}"]
    head += [f"{key}: {value}" for key, value in fields.items()]
    (entry / "SKILL.md").write_text("---\n" + "\n".join(head) + "\n---\n" + body, encoding="utf-8")
    return entry


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / "ws", output_limit=30_000)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "skills"
    path.mkdir()
    return path


# --------------------------------------------------------------- 延迟加载的成本


def test_skill_lazy_load_costs(root: Path, tmp_path: Path) -> None:
    """目录的体积只跟**技能个数**有关，与正文长度无关。

    这是 Skills 这一层存在的全部理由：如果正文跟着目录一起进 system，
    技能写得越详细，每轮的固定开销就越大，最后没人敢写技能。
    """
    lean = tmp_path / "lean"
    lean.mkdir()
    skill_dir(lean, "long", body="就一句话。")
    skill_dir(root, "long", body=BODY * 6)

    assert SkillLoader(lean).catalog() == SkillLoader(root).catalog()
    # 同一份目录，取正文的成本却是目录的几十倍 —— 所以要按需
    loaded = SkillLoader(root).load("long")
    assert len(loaded) > len(SkillLoader(root).catalog()) * 4


def test_catalog_respects_its_line_budget(root: Path) -> None:
    """预算是硬的：超了就少列几条，但标题、溢出说明、成本提示一行都不能少。"""
    from miniclaude.ext.skills import CATALOG_HINT

    for index in range(5):
        skill_dir(root, f"skill-{index}", body="正文")
    trimmed = SkillLoader(root, max_catalog_lines=1)  # 1 会被抬到下限 4
    lines = trimmed.catalog().splitlines()
    assert len(lines) == 4
    assert lines[-1] == CATALOG_HINT
    assert "另有 4 个技能未列出" in lines[-2]


def test_catalog_never_exceeds_its_line_budget(root: Path) -> None:
    from miniclaude.ext.skills import CATALOG_HINT

    for index in range(40):
        skill_dir(root, f"skill-{index:02d}")
    loader = SkillLoader(root)
    lines = loader.catalog().splitlines()
    assert len(lines) == loader.max_catalog_lines
    assert "另有 13 个技能未列出" in lines[-2]
    assert lines[-1] == CATALOG_HINT


def test_catalog_is_empty_when_there_is_nothing_to_offer(root: Path) -> None:
    assert SkillLoader(root).catalog() == ""
    assert SkillLoader(root / "nope").catalog() == ""


# --------------------------------------------------------------- 发现与拒绝


def test_missing_description_is_rejected_with_a_reason(root: Path) -> None:
    skill_dir(root, "ok")
    bad = root / "no-desc"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: no-desc\n---\n正文有，说明没有。\n", encoding="utf-8")

    loader = SkillLoader(root)
    assert [row.name for row in loader.manifests()] == ["ok"]
    assert loader.rejected == [{"dir": "no-desc", "reason": loader.rejected[0]["reason"]}]
    assert "description" in loader.rejected[0]["reason"]


def test_directories_without_a_skill_file_are_rejected_not_fatal(root: Path) -> None:
    (root / "no-file").mkdir()
    skill_dir(root, "good", body="能跑就行")
    loader = SkillLoader(root)
    assert [row.name for row in loader.manifests()] == ["good"]
    assert loader.rejected == [{"dir": "no-file", "reason": "没有 SKILL.md"}]


def test_the_loader_only_sees_its_own_directory(root: Path, tmp_path: Path) -> None:
    """根目录之外的技能不存在 —— 发现阶段就没有穿越，加载阶段更只有一个名字可传。"""
    skill_dir(tmp_path, "outside", body="不该被看见")
    skill_dir(root, "inside", body="该被看见")
    loader = SkillLoader(root)
    assert [row.name for row in loader.manifests()] == ["inside"]
    with pytest.raises(SkillError):
        loader.load("outside")


def test_unsafe_skill_name_is_rejected(root: Path) -> None:
    """名字是 load_skill 唯一的寻址方式 —— 进不了安全字符集就没有"取的时候不越界"。"""
    entry = root / "weird"
    entry.mkdir()
    (entry / "SKILL.md").write_text(
        "---\nname: ../../etc/passwd\ndescription: 越界\n---\n正文\n", encoding="utf-8"
    )
    loader = SkillLoader(root)
    assert loader.manifests() == []
    assert "不合法" in loader.rejected[0]["reason"]


def test_duplicate_skill_names_keep_the_first(root: Path) -> None:
    skill_dir(root, "a-copy", skill_name="same", description="先来", body="甲")
    skill_dir(root, "b-copy", skill_name="same", description="后来", body="乙")
    loader = SkillLoader(root)
    assert [row.description for row in loader.manifests()] == ["先来"]
    assert "重复" in loader.rejected[0]["reason"]


def test_dot_and_underscore_dirs_are_not_skills_at_all(root: Path) -> None:
    (root / ".git").mkdir()
    (root / "_draft").mkdir()
    skill_dir(root, "real", body="真的")
    loader = SkillLoader(root)
    assert [row.name for row in loader.manifests()] == ["real"]
    assert loader.rejected == []


def test_empty_body_is_rejected(root: Path) -> None:
    skill_dir(root, "hollow", body="   \n")
    loader = SkillLoader(root)
    assert loader.manifests() == []
    assert "空" in loader.rejected[0]["reason"]


# --------------------------------------------------------------- 按需展开


def test_unknown_name_lists_what_is_available(root: Path) -> None:
    skill_dir(root, "alpha", body="甲")
    loader = SkillLoader(root)
    with pytest.raises(SkillError, match="alpha"):
        loader.load("beta")


def test_load_cannot_be_handled_a_path(root: Path) -> None:
    """参数里只有名字，没有路径 —— 所以 `../` 这条路根本不存在。"""
    skill_dir(root, "safe", body="安全")
    loader = SkillLoader(root)
    for hostile in ("../etc/passwd", "safe/../../windows/win.ini", "C:\\Windows\\win.ini"):
        with pytest.raises(SkillError):
            loader.load(hostile)


def test_sibling_files_are_reported_but_never_loaded(root: Path) -> None:
    """D20：不执行技能脚本、不读技能自带的其他文件。这里要能**看见**它们。"""
    entry = skill_dir(root, "with-extra", body="只看正文")
    (entry / "helper.py").write_text("import os; os.system('rm -rf /')", encoding="utf-8")
    (entry / "reference.md").write_text("参考", encoding="utf-8")

    loader = SkillLoader(root)
    text = loader.load("with-extra")
    assert "helper.py" in text and "reference.md" in text
    assert "rm -rf" not in text
    assert "D20" in text


def test_oversized_body_is_truncated_not_streamed(root: Path) -> None:
    from miniclaude.ext.skills import MAX_SKILL_CHARS

    skill_dir(root, "huge", body="字" * (MAX_SKILL_CHARS + 5000))
    text = SkillLoader(root).load("huge")
    assert len(text) < MAX_SKILL_CHARS + 1000
    assert "已截断" in text


def test_frontmatter_without_closing_fence_is_just_body(root: Path) -> None:
    entry = root / "no-fence"
    entry.mkdir()
    (entry / "SKILL.md").write_text("---\nname: no-fence\ndescription: 没闭合\n正文开头\n", encoding="utf-8")
    loader = SkillLoader(root)
    assert loader.manifests() == []
    assert "description" in loader.rejected[0]["reason"]


def test_quoted_frontmatter_values_are_unquoted(root: Path) -> None:
    entry = root / "quoted"
    entry.mkdir()
    (entry / "SKILL.md").write_text(
        '---\nname: quoted\ndescription: "带引号：冒号也算正文"\nwhen_to_use: 需要时\n---\n正文\n',
        encoding="utf-8",
    )
    row = SkillLoader(root).manifests()[0]
    assert row.description == "带引号：冒号也算正文"
    assert row.when_to_use == "需要时"


# --------------------------------------------------------------- 工具接线


def test_load_skill_tool_is_read_only(ws: Workspace, root: Path) -> None:
    """READ 级：无人值守跑批不能因为"模型想看技能"而停在确认上。"""
    skill_dir(root, "alpha", body="甲乙丙")
    tool = LoadSkillTool(ws, SkillLoader(root))
    assert tool.risk_level is RiskLevel.READ

    gate = PermissionGate(workspace=ws, mode=PermissionMode.AUTO)
    decision, _ = gate.check(tool, {"name": "alpha"})
    assert decision is Decision.ALLOW


def test_load_skill_returns_a_tool_result_not_an_exception(ws: Workspace, root: Path) -> None:
    skill_dir(root, "alpha", body="甲乙丙")
    tool = LoadSkillTool(ws, SkillLoader(root))

    ok = tool.invoke(ToolUseBlock(id="t1", name="load_skill", input={"name": "alpha"}))
    assert ok.is_error is False and "甲乙丙" in ok.content

    missing = tool.invoke(ToolUseBlock(id="t2", name="load_skill", input={"name": "nope"}))
    assert missing.is_error is True and "nope" in missing.content

    bad = tool.invoke(ToolUseBlock(id="t3", name="load_skill", input={}))
    assert bad.is_error is True and "name" in bad.content


def test_load_skill_output_respects_the_truncation_cap(ws: Workspace, root: Path) -> None:
    from miniclaude.ext.skills import MAX_SKILL_CHARS

    skill_dir(root, "big", body="字" * (MAX_SKILL_CHARS + 5000))
    tiny = Workspace(ws.root, output_limit=2000)
    result = LoadSkillTool(tiny, SkillLoader(root)).invoke(
        ToolUseBlock(id="t1", name="load_skill", input={"name": "big"})
    )
    assert len(result.content) <= 2000 + 200
    assert "output truncated" in result.content


def test_loader_caches_and_stats_describe_the_real_state(root: Path) -> None:
    skill_dir(root, "alpha", body="甲" * 100)
    skill_dir(root, "beta", body="乙" * 50)
    (root / "broken").mkdir()
    loader: Any = SkillLoader(root)
    stats = loader.stats()
    assert stats["skills"] == 2 and stats["rejected"] == 1
    assert stats["names"] == ["alpha", "beta"]
    assert stats["body_chars_total"] == 150
    assert stats["catalog_lines"] <= stats["cap_lines"]
    assert stats["catalog_chars"] < stats["body_chars_total"]
