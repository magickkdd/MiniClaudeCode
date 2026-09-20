"""Planner —— 把"计划"从模型的即兴文本变成 Agent 持有的数据结构。

MVP 只做到"可见、可断言、可回放"（D3 的取舍：独立 Planner 阶段要 12h，
且对 coding 成功率提升存疑）。价值在于：
  · CLI 能显示"还剩哪几步"，人一眼看出 Agent 有没有跑偏
  · trace 里能事后计算"是否完成了自己声明的步骤"
  · 测试能断言不变式，而不是读一段自然语言猜
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

MAX_ITEMS = 25
MAX_CONTENT_CHARS = 200


class TodoStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


_MARKERS = {
    TodoStatus.PENDING: " ",
    TodoStatus.IN_PROGRESS: "~",
    TodoStatus.DONE: "x",
    TodoStatus.CANCELLED: "-",
}


@dataclass
class TodoItem:
    content: str
    status: TodoStatus = TodoStatus.PENDING

    def line(self) -> str:
        return f"- [{_MARKERS[self.status]}] {self.content}"


@dataclass
class TodoList:
    """模型每次调用 write_todos 都**整体替换**这份清单。

    整体替换而不是增量打勾：模型对"我现在认为完整的计划长什么样"的表达
    比"它记得住第几条已完成"可靠得多，也让状态收敛为一次赋值。
    """

    items: list[TodoItem] = field(default_factory=list)

    def replace(self, items: list[TodoItem]) -> str | None:
        """套用不变式后写入，返回对被静默修正之处的说明（会回给模型）。"""
        notes: list[str] = []

        cleaned = [item for item in items if item.content.strip()]
        if len(cleaned) != len(items):
            notes.append(f"忽略了 {len(items) - len(cleaned)} 条空描述")
        for item in cleaned:
            if len(item.content) > MAX_CONTENT_CHARS:
                item.content = item.content[:MAX_CONTENT_CHARS].rstrip() + "…"

        if len(cleaned) > MAX_ITEMS:
            notes.append(f"计划最多 {MAX_ITEMS} 条，已截断多余的 {len(cleaned) - MAX_ITEMS} 条")
            cleaned = cleaned[:MAX_ITEMS]

        active = [item for item in cleaned if item.status is TodoStatus.IN_PROGRESS]
        if len(active) > 1:
            for extra in active[1:]:
                extra.status = TodoStatus.PENDING
            notes.append(
                f"同一时刻只能有一步在进行中，已把另外 {len(active) - 1} 步退回待办"
            )

        self.items = cleaned
        return "; ".join(notes) if notes else None

    def mark(self, index: int, status: TodoStatus) -> None:
        self.items[index].status = status

    @property
    def in_progress(self) -> TodoItem | None:
        for item in self.items:
            if item.status is TodoStatus.IN_PROGRESS:
                return item
        return None

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def all_finished(self) -> bool:
        return bool(self.items) and all(
            item.status in (TodoStatus.DONE, TodoStatus.CANCELLED) for item in self.items
        )

    @property
    def remaining(self) -> int:
        return sum(1 for item in self.items if item.status is TodoStatus.PENDING)

    def render(self) -> str:
        if not self.items:
            return "(当前没有任务清单)"
        done = sum(1 for item in self.items if item.status is TodoStatus.DONE)
        body = "\n".join(item.line() for item in self.items)
        return f"任务清单（{done}/{len(self.items)} 已完成）：\n{body}"

    def snapshot(self) -> list[dict[str, Any]]:
        return [{"content": item.content, "status": item.status.value} for item in self.items]

    @classmethod
    def from_api(cls, raw: Any) -> tuple["TodoList", str | None]:
        """把模型传来的 todos 参数变成 TodoList，容忍它写的各种形状。"""
        if not isinstance(raw, list):
            return cls(), "todos 必须是数组，例如 [{\"content\": \"...\", \"status\": \"pending\"}]"
        items: list[TodoItem] = []
        problems: list[str] = []
        for position, entry in enumerate(raw):
            if isinstance(entry, str):
                items.append(TodoItem(content=entry))
                continue
            if not isinstance(entry, dict):
                problems.append(f"第 {position + 1} 条不是对象")
                continue
            content = str(entry.get("content") or entry.get("description") or entry.get("text") or "").strip()
            if not content:
                problems.append(f"第 {position + 1} 条缺少 content")
                continue
            status_raw = str(entry.get("status") or TodoStatus.PENDING).lower().replace("-", "_").replace(" ", "_")
            try:
                status = TodoStatus(status_raw)
            except ValueError:
                # 模型爱写 in progress / doing / complete，接受常见变体而不是报错
                status = {
                    "inprogress": TodoStatus.IN_PROGRESS,
                    "doing": TodoStatus.IN_PROGRESS,
                    "active": TodoStatus.IN_PROGRESS,
                    "complete": TodoStatus.DONE,
                    "completed": TodoStatus.DONE,
                    "finished": TodoStatus.DONE,
                    "canceled": TodoStatus.CANCELLED,
                    "todo": TodoStatus.PENDING,
                    "open": TodoStatus.PENDING,
                }.get(status_raw, TodoStatus.PENDING)
                if status is TodoStatus.PENDING and status_raw not in {"pending", "todo", "open"}:
                    problems.append(f"第 {position + 1} 条的 status={entry.get('status')!r} 无法识别，按 pending 处理")
            items.append(TodoItem(content=content, status=status))
        return cls(items=items), "; ".join(problems) if problems else None
