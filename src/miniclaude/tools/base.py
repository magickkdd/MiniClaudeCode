"""工具协议 —— Agent 能力边界的唯一定义处。

铁律：invoke() 永不向外抛异常。工具报错是**模型的输入**，不是程序故障 ——
一次报错打断整个 Agent，是最糟糕的设计。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from miniclaude.messages import ToolUseBlock
from miniclaude.tools.workspace import PathEscape, Workspace

_JSON_TYPE_CHECKS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


class RiskLevel(StrEnum):
    READ = "read"        # 只读，自动放行
    WRITE = "write"      # 修改工作区文件
    EXECUTE = "execute"  # 跑命令，风险最高


@dataclass(frozen=True)
class ToolSpec:
    """发给模型的工具说明书。description 就是 prompt，见各工具里的写法。"""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolResult:
    content: str
    is_error: bool = False

    @classmethod
    def ok(cls, content: Any) -> "ToolResult":
        return cls(str(content), is_error=False)

    @classmethod
    def err(cls, message: Any) -> "ToolResult":
        """业务级失败：文件不存在、替换未命中、命令退出码非 0。"""
        return cls(str(message), is_error=True)


class BaseTool(ABC):
    """所有工具的基类。子类只需实现 run()，并声明四个类属性。"""

    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {}
    risk_level: RiskLevel = RiskLevel.READ
    # True = 这个工具的动作发生在**工作区之外**（MCP 远端进程）。AUTO 模式的承诺是
    # "工作区内自动放行"，外扩一步就越过了它自己的定义 —— 所以外部工具在 AUTO 下仍要确认。
    # 见 SPEC v2 §3.7 D19 与 §6.3-1；判定住在 permissions.check()。
    external: bool = False

    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, input_schema=self.input_schema)

    @abstractmethod
    def run(self, **kwargs: Any) -> ToolResult:
        """真正的动作。参数已解包，返回值永远是 ToolResult。"""

    # ------------------------------------------------------------ 统一入口

    def invoke(self, tool_use: ToolUseBlock) -> ToolResult:
        args = tool_use.input if isinstance(tool_use.input, dict) else {}
        malformed = _malformed_args(args)
        if malformed:
            return ToolResult.err(malformed)
        problems = self.validate(args)
        if problems:
            return ToolResult.err(
                f"参数校验失败：{problems} 工具 {self.name} 的 schema 是 "
                f"{self._schema_hint()}"
            )
        clean = self._coerce(args)
        try:
            return self._normalize(self.run(**clean))
        except PathEscape as exc:
            return ToolResult.err(f"路径被拒绝：{exc}")
        except TypeError as exc:
            # 解包签名不匹配，等同于参数问题，让模型自己纠正
            return ToolResult.err(f"参数不匹配：{exc}")
        except Exception as exc:  # noqa: BLE001 - 兜底正是这个方法的职责
            return ToolResult.err(f"{self.name} 执行异常：{type(exc).__name__}: {exc}")

    def validate(self, args: dict[str, Any]) -> str | None:
        """必填项 + 类型检查。返回错误描述串，全部合法时返回 None。"""
        schema = self.input_schema or {}
        properties: dict[str, Any] = schema.get("properties", {})
        required: list[str] = schema.get("required", [])
        errors: list[str] = []

        for key in required:
            if key not in args or args[key] is None:
                errors.append(f"缺少必填参数 {key!r}")

        for key, value in args.items():
            if key not in properties or value is None:
                continue
            expected = properties[key].get("type")
            checks = _JSON_TYPE_CHECKS.get(expected if isinstance(expected, str) else "")
            if checks and not isinstance(value, checks):
                errors.append(f"{key!r} 应为 {expected}，实际是 {type(value).__name__}")
            # bool 是 int 的子类，会让 integer 检查漏过去，单独拦
            if expected in ("integer", "number") and isinstance(value, bool):
                errors.append(f"{key!r} 应为 {expected}，不能是布尔")

        return "; ".join(errors) if errors else None

    def _coerce(self, args: dict[str, Any]) -> dict[str, Any]:
        """丢掉 schema 里没有的多余参数。

        刻意只丢不报错：模型偶尔会塞一个无害的多余字段，为这个烧掉一整轮
        对话不值得 —— 但也不会把它传给 run()。
        """
        allowed = set((self.input_schema or {}).get("properties", {}))
        return {k: v for k, v in args.items() if k in allowed}

    def _normalize(self, result: Any) -> ToolResult:
        if isinstance(result, ToolResult):
            result.content = self.ws.truncate(result.content)
            return result
        return ToolResult.ok(self.ws.truncate(str(result)))

    def _schema_hint(self) -> str:
        props = (self.input_schema or {}).get("properties", {})
        required = set((self.input_schema or {}).get("required", []))
        parts = [
            f"{key}:{(spec.get('type') or 'any')}{'*' if key in required else ''}"
            for key, spec in props.items()
        ]
        return json.dumps({self.name: ", ".join(parts)}, ensure_ascii=False) + "（* 为必填）"


def _malformed_args(args: dict[str, Any]) -> str | None:
    """识别适配器在参数解析失败时打上的标记（见 llm/openai_compat._load_args）。

    端点返回的 arguments 是字符串，模型有概率吐出非法 JSON 或一个非对象 JSON。
    这种情况必须在入口拦下并教模型重发，而不是让 run() 收到畸形结构后崩掉。
    """
    raw = args.get("__unparseable_arguments")
    if raw is not None:
        return (
            "参数不是合法 JSON，无法解析，请重新发起这次调用并保证参数是有效的 JSON 对象。"
            f"收到的原始参数片段：{str(raw)[:200]}"
        )
    unexpected = args.get("__unexpected_arguments_type")
    if unexpected is not None:
        return f"参数必须是 JSON 对象，实际收到 {json.dumps(unexpected, ensure_ascii=False)[:200]}。请重新发起调用。"
    return None
