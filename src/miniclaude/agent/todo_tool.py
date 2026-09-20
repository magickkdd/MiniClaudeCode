"""write_todos 工具 —— 放在 agent 层，因为 `tools/` 不允许反向 import `agent/`（SPEC §6.1）。

它由 CLI 通过 ToolRegistry.default(extra_tools=[...]) 注入。
"""

from __future__ import annotations

from typing import Any

from miniclaude.agent.planner import TodoList
from miniclaude.tools.base import BaseTool, RiskLevel, ToolResult


class WriteTodosTool(BaseTool):
    name = "write_todos"
    description = (
        "创建或更新本次任务的待办清单，用于跟踪 3 步以上的多阶段工作。"
        "每次调用都要提供**完整的**清单（整体替换，不是增量）。"
        "开始一步时把它标为 in_progress，做完立刻标为 done。"
        "同一时刻最多一步 in_progress。"
        "单步就能看到结果的小任务不要用它 —— 直接做。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "完整的任务清单",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "一句祈使句描述这一步"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "done", "cancelled"],
                        },
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["todos"],
    }
    # 它只改 Agent 自己的内存状态，不碰外部世界
    risk_level = RiskLevel.READ

    def __init__(self, workspace: Any, todos: TodoList) -> None:
        super().__init__(workspace)
        self.todos = todos

    def run(self, *, todos: list[Any]) -> ToolResult:
        staged, parse_note = TodoList.from_api(todos)
        invariant_note = self.todos.replace(staged.items)

        notes = [note for note in (parse_note, invariant_note) if note]
        prefix = f"已更新清单。注意：{'; '.join(notes)}\n\n" if notes else ""
        return ToolResult.ok(prefix + self.todos.render())
