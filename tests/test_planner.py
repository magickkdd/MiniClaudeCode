"""Planner 测试：重点是不变式与"模型乱写时的容忍度"。"""

from __future__ import annotations

from miniclaude.agent.planner import TodoItem, TodoList, TodoStatus
from miniclaude.agent.todo_tool import WriteTodosTool
from miniclaude.messages import ToolUseBlock
from miniclaude.tools import Workspace


def item(content: str, status: TodoStatus = TodoStatus.PENDING) -> TodoItem:
    return TodoItem(content=content, status=status)


def test_replace_returns_none_when_clean():
    todos = TodoList()
    assert todos.replace([item("a"), item("b", TodoStatus.IN_PROGRESS)]) is None
    assert len(todos.items) == 2


def test_only_one_step_may_be_in_progress():
    todos = TodoList()
    note = todos.replace(
        [item("a", TodoStatus.IN_PROGRESS), item("b", TodoStatus.IN_PROGRESS), item("c", TodoStatus.IN_PROGRESS)]
    )
    assert note and "只能有一步" in note
    assert todos.in_progress.content == "a"
    assert todos.items[1].status is TodoStatus.PENDING


def test_empty_items_are_dropped_and_reported():
    todos = TodoList()
    note = todos.replace([item("  "), item("real")])
    assert note and "空描述" in note
    assert [i.content for i in todos.items] == ["real"]


def test_oversized_plan_is_truncated():
    todos = TodoList()
    note = todos.replace([item(f"step {i}") for i in range(40)])
    assert note and "已截断" in note
    assert len(todos.items) == 25


def test_render_marks_progress_for_human_and_model():
    todos = TodoList()
    todos.replace([item("read", TodoStatus.DONE), item("fix", TodoStatus.IN_PROGRESS), item("test")])
    text = todos.render()
    assert "- [x] read" in text and "- [~] fix" in text and "- [ ] test" in text
    assert "1/3 已完成" in text


def test_remaining_and_all_finished():
    todos = TodoList()
    todos.replace([item("a", TodoStatus.DONE), item("b", TodoStatus.CANCELLED)])
    assert todos.all_finished and todos.remaining == 0
    todos.replace([item("a", TodoStatus.DONE), item("b")])
    assert not todos.all_finished and todos.remaining == 1


def test_snapshot_is_json_friendly_for_trace():
    todos = TodoList()
    todos.replace([item("a", TodoStatus.DONE)])
    assert todos.snapshot() == [{"content": "a", "status": "done"}]


# ------------------------------------------------------------------ 工具入口


def use(todos_payload):
    return ToolUseBlock(id="c1", name="write_todos", input={"todos": todos_payload})


def make_tool(tmp_path):
    return WriteTodosTool(Workspace(root=tmp_path), TodoList())


def test_tool_accepts_string_items_as_plain_pending(tmp_path):
    tool = make_tool(tmp_path)
    result = tool.invoke(use(["读代码", "改代码"]))
    assert not result.is_error
    assert "- [ ] 读代码" in result.content
    assert tool.todos.items[1].status is TodoStatus.PENDING


def test_tool_tolerates_model_spelling_variants(tmp_path):
    tool = make_tool(tmp_path)
    result = tool.invoke(
        use([
            {"content": "定位失败原因", "status": "completed"},
            {"content": "改代码", "status": "in progress"},
            {"content": "跑测试", "status": "PENDING"},
        ])
    )
    assert not result.is_error
    statuses = [i.status for i in tool.todos.items]
    assert statuses == [TodoStatus.DONE, TodoStatus.IN_PROGRESS, TodoStatus.PENDING]


def test_tool_replaces_whole_list_not_appends(tmp_path):
    tool = make_tool(tmp_path)
    tool.invoke(use([{"content": "a", "status": "pending"}, {"content": "b", "status": "pending"}]))
    tool.invoke(use([{"content": "a", "status": "done"}]))
    assert [i.content for i in tool.todos.items] == ["a"]


def test_tool_rejects_non_array_by_schema_before_touching_state(tmp_path):
    """坏结构在参数校验层就被拒，不该走到 from_api，更不该清空已有清单。"""
    tool = make_tool(tmp_path)
    tool.invoke(use([{"content": "keep me", "status": "pending"}]))
    result = tool.invoke(ToolUseBlock(id="c2", name="write_todos", input={"todos": "not a list"}))
    assert result.is_error and "array" in result.content
    assert [i.content for i in tool.todos.items] == ["keep me"]


def test_from_api_survives_non_list_input():
    todos, note = TodoList.from_api({"todos": "wrong shape"})
    assert todos.is_empty and note and "必须是数组" in note


def test_tool_result_feeds_current_plan_back_to_model(tmp_path):
    """计划必须回到上下文里，否则等于没写 —— 这条保证 write_todos 的返回值带渲染。"""
    tool = make_tool(tmp_path)
    result = tool.invoke(use([{"content": "跑测试", "status": "in_progress"}]))
    assert "任务清单（0/1 已完成）" in result.content
