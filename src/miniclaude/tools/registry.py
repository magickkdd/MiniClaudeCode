"""工具注册表：名字 -> 工具实例，以及"有哪些工具可以告诉模型"。"""

from __future__ import annotations

from typing import Iterable

from miniclaude.backend.protocol import ExecutionBackend
from miniclaude.tools.base import BaseTool, ToolSpec
from miniclaude.tools.bash import BashTool
from miniclaude.tools.edit_file import EditFileTool
from miniclaude.tools.find_files import FindFilesTool
from miniclaude.tools.read_file import ReadFileTool
from miniclaude.tools.run_tests import RunTestsTool
from miniclaude.tools.search_text import SearchTextTool
from miniclaude.tools.workspace import Workspace
from miniclaude.tools.write_file import WriteFileTool


class DuplicateToolError(ValueError):
    """重名注册。宁可启动就失败，也不要让后加载的模块悄悄覆盖前面的工具。"""


class ToolRegistry:
    """持有工具实例，并导出 API 需要的 tools 字段。"""

    def __init__(self, tools: Iterable[BaseTool] = ()) -> None:
        self._tools: dict[str, BaseTool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: BaseTool) -> None:
        if not tool.name:
            raise ValueError(f"{type(tool).__name__} 没有声明 name")
        if tool.name in self._tools:
            raise DuplicateToolError(f"工具名重复：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """按名取工具。模型会幻觉出不存在的工具名，这里返回 None 由上层兜底。"""
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[BaseTool]:
        return list(self._tools.values())

    def specs(self) -> list[ToolSpec]:
        """转成发给模型的工具声明列表。顺序稳定，避免同样输入产生不同报文。"""
        return [self._tools[name].spec() for name in sorted(self._tools)]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    @classmethod
    def default(
        cls,
        workspace: Workspace,
        *,
        bash_timeout: int = 60,
        extra_tools: Iterable[BaseTool] = (),
        backend: ExecutionBackend | None = None,
    ) -> "ToolRegistry":
        """装配 MVP 的七个执行/检索工具。

        write_todos 不在这里 —— 它需要 TodoList 实例，由调用方通过
        extra_tools 注入，避免工具层反向依赖 agent 层。

        `backend=None` 时两个执行工具各自退回 LocalBackend：注册表不替调用方
        决定"命令在哪儿跑"，但也不要求每个 caller 都先想清楚这件事。
        """
        tools: list[BaseTool] = [
            ReadFileTool(workspace),
            SearchTextTool(workspace),
            FindFilesTool(workspace),
            EditFileTool(workspace),
            WriteFileTool(workspace),
            BashTool(workspace, default_timeout=bash_timeout, backend=backend),
            RunTestsTool(workspace, backend=backend),
        ]
        tools.extend(extra_tools)
        return cls(tools)
