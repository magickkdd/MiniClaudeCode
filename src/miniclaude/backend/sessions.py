"""Durable session —— 跑到一半被杀掉还能接着跑，且不重复已发生的写操作（SPEC v2 §3.6）。

"不承诺 exactly-once，承诺 at-most-once 副作用 + 可审计现场"这句话的具体含义：

- 快照**在工具执行完之后**才记这条调用为 done。于是崩在中间最多得到"这个调用做完了
  但没人知道"，反过来（先记再做）就会得到"重放一次已经做过的 `rm`"。两者不对称，
  所以选前者。
- 恢复时先 `pairing_problems()`。悬空的工具调用批次（助手声明了 3 个、只回填了 1 个）
  是**可修的**：done 的那个补一条"未重放"的结果，没做的照常执行。其余任何配对破损
  一律拒绝恢复 —— 把坏现场留在文件里让人看，比抹掉它再跑一轮安全。
- 快照落在 `<记忆目录>/sessions/<session-id>.json`，session id 与 trace 的 `session`
  字段同一个，所以"这条会话的日志"和"这条会话的现场"天然对得上，不需要第三份索引。

密钥纪律：`config` 字段存的是 `Config.redacted()`，写盘前就已经脱敏；这里不再做任何
"原样落盘再说"的事。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from miniclaude.messages import Message, Role, TextBlock, ToolResultBlock, ToolUseBlock

SESSIONS_DIRNAME = "sessions"
SNAPSHOT_SCHEMA = 1
MAX_SESSIONS_KEPT = 50

REPLAY_NOTE = (
    "（该调用在上一进程已执行完成，结果未留存。为避免重复副作用，本次未重放；"
    "需要它的输出请重新读取现状，不要要求重跑。）"
)


def message_to_dict(message: Message) -> dict[str, Any]:
    """内部消息模型 → JSON 友好的 dict。刻意不复用 llm/ 里的厂商格式：
    现场要的是"模型当时看到什么"，而厂商格式会为了某个端点的怪癖丢掉信息。"""
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            blocks.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        elif isinstance(block, ToolResultBlock):
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                }
            )
    return {"role": message.role.value, "content": blocks}


def message_from_dict(raw: dict[str, Any]) -> Message:
    role = Role(str(raw.get("role") or Role.USER.value))
    blocks: list[Any] = []
    for item in raw.get("content") or []:
        kind = item.get("type")
        if kind == "text":
            blocks.append(TextBlock(str(item.get("text") or "")))
        elif kind == "tool_use":
            payload = item.get("input")
            blocks.append(
                ToolUseBlock(
                    id=str(item.get("id") or ""),
                    name=str(item.get("name") or ""),
                    input=payload if isinstance(payload, dict) else {},
                )
            )
        elif kind == "tool_result":
            blocks.append(
                ToolResultBlock(
                    tool_use_id=str(item.get("tool_use_id") or ""),
                    content=str(item.get("content") or ""),
                    is_error=bool(item.get("is_error")),
                )
            )
    return Message(role, blocks)


@dataclass
class SessionSnapshot:
    """一次会话的可恢复现场。字段形状就是 SPEC §3.6 里那张表。"""

    session_id: str
    turn: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)
    todos: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    backend: str = "local"
    last_checkpoint_rev: str = ""
    done_call_ids: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)
    schema: int = SNAPSHOT_SCHEMA
    termination: str = ""       # 空串 = 上一进程没能跑到收尾，这正是 resume 要救的那种现场
    resumed_from: str = ""      # 非空 = 这份现场本身就是恢复出来的（链条要能追）

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SessionSnapshot":
        known = {key: raw[key] for key in cls.__dataclass_fields__ if key in raw}  # type: ignore[attr-defined]
        return cls(**known)

    def load_messages(self) -> list[Message]:
        return [message_from_dict(item) for item in self.messages]

    def load_todos(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.todos]


@dataclass
class SessionLog:
    """`<记忆目录>/sessions/` 的读写。坏文件一律当没有，绝不报错停机。"""

    root: Path
    writes: int = 0
    degraded: str = ""

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def path_for(self, session_id: str) -> Path:
        return self.root / f"{_safe(session_id)}.json"

    def save(self, snapshot: SessionSnapshot) -> bool:
        """原子替换。写不进去（只读盘、目录被删）只让 `degraded` 变红：
        度量层没有否决任务的权力 —— 这条纪律与 MemoryStore、Tracer 一致。"""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.path_for(snapshot.session_id)
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(snapshot.as_dict(), ensure_ascii=False, indent=1, default=str),
                encoding="utf-8",
            )
            os.replace(tmp, target)
        except (OSError, TypeError, ValueError) as exc:
            self.degraded = f"{type(exc).__name__}: {exc}"[:200]
            return False
        self.writes += 1
        self.prune()
        return True

    def load(self, session_id: str) -> tuple[SessionSnapshot | None, str]:
        """返回 (快照, 原因)。快照为 None 时原因必须说清是"没有"还是"坏了" ——
        `mcc resume` 对这两件事说的话完全不同。"""
        path = self.path_for(session_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, f"没有找到会话 {session_id} 的现场（{path.name} 不存在）"
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"现场文件读坏了：{type(exc).__name__}: {exc}"
        if not isinstance(raw, dict):
            return None, "现场文件不是一个对象"
        if int(raw.get("schema") or 0) != SNAPSHOT_SCHEMA:
            return None, f"现场格式版本 {raw.get('schema')} 与当前 {SNAPSHOT_SCHEMA} 不匹配，拒绝猜测"
        try:
            return SessionSnapshot.from_dict(raw), ""
        except (TypeError, ValueError) as exc:
            return None, f"现场字段不合法：{exc}"

    def ids(self) -> list[str]:
        """有现场的会话 id，最近的在前。`mcc resume`（不带 id）与报表用它。"""
        found: list[tuple[float, str]] = []
        for path in sorted(self.root.glob("*.json")) if self.root.is_dir() else []:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict) and isinstance(raw.get("session_id"), str):
                found.append((float(raw.get("ts") or 0), raw["session_id"]))
        return [session_id for _, session_id in sorted(found, reverse=True)]

    def prune(self, keep: int = MAX_SESSIONS_KEPT) -> int:
        """只留最近 N 份。快照是**可审计现场**而不是备份：真要留史的是 trace（追加式、
        一份几百 KB），快照留着的是"能接着跑"这一件事，超过 50 份就没人会去 resume 了。"""
        paths = sorted(
            (path for path in self.root.glob("*.json") if not path.name.endswith(".tmp")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        removed = 0
        for path in paths[keep:]:
            try:
                path.unlink()
                removed += 1
            except OSError:
                break
        return removed

    def stats(self) -> dict[str, Any]:
        return {"root": self.root.name, "writes": self.writes, "degraded": self.degraded or None}


def _safe(session_id: str) -> str:
    """会话 id 会变成文件名 —— 只留字母数字下划线和连字符，别给路径穿越留门。"""
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in str(session_id))
    return cleaned[:64] or "session"


@dataclass
class SessionRecorder:
    """把 Agent 的现场抄成一份快照。放在这一层而不是 `agent/`，是因为"现场长什么样"
    和"落盘规则"属于同一件事；Agent 只需要知道自己有一个 recorder。

    `agent` 用鸭子类型（messages / todos / state / done_call_ids）：会话层反过来 import
    Agent 就会和 `agent/loop.py` 成环。
    """

    log: SessionLog
    session_id: str
    config: dict[str, Any] = field(default_factory=dict)
    backend: str = "local"
    last_checkpoint_rev: str = ""
    saves: int = 0
    resumed_from: str = ""

    def record_checkpoint(self, rev: str) -> None:
        if rev:
            self.last_checkpoint_rev = rev

    def snapshot_of(self, agent: Any, *, termination: str = "") -> SessionSnapshot:
        return SessionSnapshot(
            session_id=self.session_id,
            turn=int(getattr(agent.state, "turn", 0) or 0),
            messages=[message_to_dict(message) for message in agent.messages],
            todos=list(agent.todos.snapshot()),
            state=agent.state.snapshot(),
            config=dict(self.config),
            backend=self.backend,
            last_checkpoint_rev=self.last_checkpoint_rev,
            done_call_ids=sorted(agent.done_call_ids),
            ts=time.time(),
            termination=termination,
            resumed_from=self.resumed_from,
        )

    def save(self, agent: Any, *, termination: str = "") -> bool:
        ok = self.log.save(self.snapshot_of(agent, termination=termination))
        if ok:
            self.saves += 1
        return ok

    def stats(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "writes": self.saves,
            "backend": self.backend,
            "resumed_from": self.resumed_from or None,
            "last_checkpoint_rev": self.last_checkpoint_rev or None,
            **self.log.stats(),
        }
