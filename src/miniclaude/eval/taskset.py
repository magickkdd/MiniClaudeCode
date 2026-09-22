"""任务集 —— 把"考题"变成可版本化、可哈希、可自检的数据。

一道题 = 一个 JSON 文件，字段就是判据本身（SPEC v2 §3.2）。为什么不是 Python：
Python 里能写 `if`，于是"这道题怎么算过"会悄悄变成"这段代码怎么算过"，
读的人看不出差分。JSON 只能声明，声明的东西全部由 `judge.py` 一处解释。

三件事在这一层就锁死，不给后面留坑：

1. **未知字段直接报错**。打错一个键（`fail_to_pas`）如果会被静默忽略，那道题
   就退化成了"没有成功判据的题"，而报表上它照样是一个 pass 率数字。
2. **任务集内容哈希**（`taskset_sha`）覆盖任务 JSON *与* 被引用的 fixture 文件树。
   只哈希 JSON 的话，改 `eval/fixtures/x/duration.py` 里的那行 bug 不会改变哈希，
   旧基线于是还在跟一份已经不同的考卷比分数。
3. **`gold` 只喂 fake 引擎**。任务里可以带"标准答案"（SWE-bench 也带 gold patch），
   它永远不会进模型上下文 —— `instruction` 是唯一进上下文的东西，这一点由
   `tests/test_eval_taskset.py` 用"答案文本不出现在任何任务指令里"的测试钉住。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal, Sequence

from miniclaude.eval.contract import noise_names, tracked_files

SourceKind = Literal["vendored", "repo"]
TerminationExpect = Literal["completed", "non_completed", "any"]
MODES = ("auto", "readonly")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,40}$")
NODE_RE = re.compile(r"^[\w./\\-]+::[\w\[\].:\-]+$")


class TaskSetError(ValueError):
    """考题本身有问题。宁可在开跑前 0.1 秒失败，也不要跑完 72 次之后发现判据是空的。"""


@dataclass(frozen=True)
class TaskSource:
    """这道题的代码从哪来。

    `vendored` 是仓内 fixture（v2 现阶段全部用它：离线、秒级、不受网络与上游影响）；
    `repo` 留给 Tier 2 的"真实开源仓库"（`url` + `base_commit` 才能复现），
    现在只保证能序列化、能哈希，不保证能跑。
    """

    kind: SourceKind = "vendored"
    path: str = ""
    url: str = ""
    base_commit: str = ""

    def resolve(self, repo_root: Path) -> Path:
        if self.kind != "vendored":
            raise TaskSetError(
                f"source.kind={self.kind!r} 需要联网取仓库（Tier 2 的 `repo` 后端还没做），"
                "现在只能跑 vendored 任务"
            )
        if not self.path:
            raise TaskSetError("vendored 任务必须给 source.path")
        path = (repo_root / self.path).resolve()
        if not path.is_dir():
            raise TaskSetError(f"source.path 不是一个目录：{self.path}")
        return path


@dataclass(frozen=True)
class FakeSpec:
    """fake 引擎的剧本。`driver` 在 `eval/drivers.py` 注册，`args` 由驱动自取。"""

    driver: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskInstance:
    """一道题的完整契约。字段的解释只有一处：`miniclaude.eval.judge`。"""

    id: str
    instruction: str
    source: TaskSource
    setup: str = ""
    # —— 用例级白名单：这一节最重要的设计（SPEC v2 §3.2）——
    fail_to_pass: tuple[str, ...] = ()      # 修好后必须变绿的用例
    pass_to_pass: tuple[str, ...] = ()      # 必须保持绿的用例
    # —— 其它跑代码的判据 ——
    verify_cmd: str = ""
    probe: str = ""
    must_exist: tuple[str, ...] = ()
    must_absent: tuple[str, ...] = ()
    protected: tuple[str, ...] = ()         # 逐字节不许改（防"改考卷"）
    added_only: tuple[str, ...] = ()       # 只增不删（"补一个防回归测试"这类）
    min_changed_files: int = 0             # 改动必须真的落到代码里
    # —— 收尾与语义 ——
    expected_termination: TerminationExpect = "completed"
    answer_keywords: tuple[str, ...] = ()  # 唯一允许读模型答复的判据，见 judge 的说明
    mode: str = "auto"
    tags: tuple[str, ...] = ()
    max_turns: int = 20
    token_ceiling: int = 120_000
    difficulty: Literal[1, 2, 3] = 1
    supports_live: bool = True
    fake: FakeSpec = field(default_factory=FakeSpec)
    notes: str = ""
    path: Path | None = None               # 来自哪个 JSON（不在磁盘契约里）

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not ID_RE.match(self.id):
            raise TaskSetError(f"任务 id 非法：{self.id!r}（要小写、字母数字与 . _ -）")
        if not self.instruction.strip():
            raise TaskSetError(f"{self.id}: instruction 不能为空")
        if self.mode not in MODES:
            raise TaskSetError(f"{self.id}: mode 只能是 {'/'.join(MODES)}，当前 {self.mode!r}")
        if self.expected_termination not in ("completed", "non_completed", "any"):
            raise TaskSetError(f"{self.id}: expected_termination 非法：{self.expected_termination!r}")
        if self.difficulty not in (1, 2, 3):
            raise TaskSetError(f"{self.id}: difficulty 只能是 1/2/3")
        if self.max_turns <= 0:
            raise TaskSetError(f"{self.id}: max_turns 必须是正数")
        for name in ("fail_to_pass", "pass_to_pass"):
            for node in getattr(self, name):
                if not NODE_RE.match(node):
                    raise TaskSetError(
                        f"{self.id}: {name} 里 {node!r} 不像用例 id（形如 tests/test_x.py::test_y）"
                    )
        if not self.has_requirement:
            raise TaskSetError(
                f"{self.id}: 这道题没有任何成功判据。至少要给 fail_to_pass / pass_to_pass / "
                "probe / verify_cmd / must_exist / answer_keywords 之一"
            )
        if not self.fake.driver:
            raise TaskSetError(
                f"{self.id}: 缺 fake.driver —— fake 引擎是断点续跑、成本闸与报表的回归网，"
                "每道题都必须能被确定性跑一遍"
            )

    @property
    def has_requirement(self) -> bool:
        """这道题有没有"跑得出来的"判据。只有 instruction 的题等于没有题。"""
        return bool(
            self.fail_to_pass
            or self.pass_to_pass
            or self.verify_cmd
            or self.probe
            or self.must_exist
            or self.answer_keywords
            or self.expected_termination == "non_completed"
        )

    @property
    def is_readonly(self) -> bool:
        return self.mode == "readonly"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("path", None)
        source = {key: value for key, value in payload["source"].items() if value}
        payload["source"] = {"kind": self.source.kind, **source}
        payload["tags"] = list(self.tags)
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, path: Path | None = None) -> "TaskInstance":
        known = {f.name for f in fields(cls)} - {"path", "source", "fake"}
        unknown = sorted(set(raw) - known - {"source", "fake"})
        if unknown:
            raise TaskSetError(
                f"{raw.get('id', '?')}: 不认识这些字段：{', '.join(unknown)}。"
                "拼错的判据会被静默忽略，所以这里拒绝加载"
            )
        source_raw = dict(raw.get("source") or {})
        unknown_source = sorted(set(source_raw) - {"kind", "path", "url", "base_commit"})
        if unknown_source:
            raise TaskSetError(f"{raw.get('id', '?')}: source 里有不认识的字段：{unknown_source}")
        fake_raw = dict(raw.get("fake") or {})
        unknown_fake = sorted(set(fake_raw) - {"driver", "args"})
        if unknown_fake:
            raise TaskSetError(f"{raw.get('id', '?')}: fake 里有不认识的字段：{unknown_fake}")
        kwargs: dict[str, Any] = {key: raw[key] for key in known if key in raw}
        for key in ("fail_to_pass", "pass_to_pass", "must_exist", "must_absent", "protected",
                    "added_only", "answer_keywords", "tags"):
            if key in kwargs:
                kwargs[key] = tuple(kwargs[key])
        kwargs["source"] = TaskSource(**source_raw)
        kwargs["fake"] = FakeSpec(driver=str(fake_raw.get("driver", "")), args=dict(fake_raw.get("args") or {}))
        kwargs["path"] = path
        try:
            return cls(**kwargs)
        except TypeError as exc:  # 缺必填字段
            raise TaskSetError(f"{raw.get('id', '?')}: 任务文件不完整：{exc}") from exc


@dataclass(frozen=True)
class TaskSet:
    """一组任务 + 它的内容哈希。哈希变了，旧基线就该作废。"""

    tasks: tuple[TaskInstance, ...]
    root: Path
    repo_root: Path
    sha: str

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for task in self.tasks:
            if task.id in seen:
                raise TaskSetError(f"任务 id 重复：{task.id}")
            seen.add(task.id)

    def __len__(self) -> int:
        return len(self.tasks)

    def __iter__(self):
        return iter(self.tasks)

    def by_id(self, task_id: str) -> TaskInstance:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(f"任务集里没有 {task_id!r}，可选：{'、'.join(t.id for t in self.tasks)}")

    def filtered(self, *, ids: Sequence[str] = (), tags: Sequence[str] = ()) -> "TaskSet":
        picked = [task for task in self.tasks if _picked(task, ids, tags)]
        if not picked:
            raise TaskSetError("筛选后一个任务都没有 —— 检查 --only / --tag 拼写")
        return TaskSet(tasks=tuple(picked), root=self.root, repo_root=self.repo_root, sha=self.sha)

    @classmethod
    def load(cls, root: Path, *, repo_root: Path | None = None) -> "TaskSet":
        root = Path(root).resolve()
        repo = (repo_root or _repo_root(root)).resolve()
        if not root.is_dir():
            raise TaskSetError(f"任务目录不存在：{root}")
        files = sorted(root.rglob("*.json"))
        if not files:
            raise TaskSetError(f"{root} 里没有任何任务 JSON")
        tasks: list[TaskInstance] = []
        for file in files:
            try:
                raw = json.loads(file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise TaskSetError(f"{file.name}: 不是合法 JSON：{exc}") from exc
            if not isinstance(raw, dict):
                raise TaskSetError(f"{file.name}: 顶层必须是对象")
            task = TaskInstance.from_dict(raw, path=file)
            task.source.resolve(repo)  # 现在就确认考题的目录在，别等跑批跑到它
            tasks.append(task)
        return cls(tasks=tuple(tasks), root=root, repo_root=repo, sha=content_sha(root, tasks, repo_root=repo))


def _picked(task: TaskInstance, ids: Sequence[str], tags: Sequence[str]) -> bool:
    if ids and task.id not in set(ids):
        return False
    if tags and not set(tags) & set(task.tags):
        return False
    return True


def _repo_root(tasks_dir: Path) -> Path:
    """eval/tasks → eval → 仓库根。任务里的 source.path 相对仓库根写。"""
    return tasks_dir.parent.parent


def content_sha(tasks_dir: Path, tasks: Sequence[TaskInstance], *, repo_root: Path) -> str:
    """任务 JSON + 被引用的 fixture 文件树的内容哈希。

    只哈希 JSON 是不够的：fixture 里的 bug 改没改，JSON 上看不出任何变化，
    而分数含义已经完全不同了。哈希不一致时 `compare` 直接拒绝对比 ——
    这比"悄悄跟一份不同的考卷比"值钱。
    """
    digest = hashlib.sha256()
    for file in sorted(Path(tasks_dir).rglob("*.json")):
        _feed(digest, file, base=Path(tasks_dir))
    seen: set[str] = set()
    for task in tasks:
        if task.source.kind != "vendored" or not task.source.path:
            continue
        root = (repo_root / task.source.path).resolve()
        key = str(root)
        if key in seen or not root.is_dir():
            continue
        seen.add(key)
        for name, payload in sorted(tracked_files(root).items()):
            digest.update(f"{task.source.path}/{name}\0".encode("utf-8"))
            digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()[:12]


def _feed(digest, file: Path, *, base: Path) -> None:
    # 用 `noise_names()` 而不是 `NOISE`：默认那四项是工具缓存，第五项（记忆目录）
    # 是 agent 自己的派生物。任务集指纹不该因为谁在本机改过 `MEMORY_DIR` 而变 ——
    # 这里要的只是"考卷内容没被人动过"。
    if any(part in noise_names() for part in file.parts):
        return
    rel = file.relative_to(base).as_posix()
    digest.update(f"{rel}\0".encode("utf-8"))
    digest.update(hashlib.sha256(file.read_bytes()).digest())
