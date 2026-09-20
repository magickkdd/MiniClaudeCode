"""权限门 —— 在工具执行**之前**拦截，而不是执行完之后补救。

模型可以自由思考，但每一次改变外部世界的动作都要过这道闸。
三种运行模式（CLI 与 demo 脚本分别对应）：

  ASK      交互确认，可选"本会话内始终允许"        —— 人在场时用
  AUTO     工作区内一律放行，破坏性动作直接拒绝     —— demo / 无人值守
  READONLY 只允许 READ 级工具                      —— 演练与截图

路径边界不依赖模式：任何模式下都强制锁在 Workspace.root 内。
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from miniclaude.tools.base import BaseTool, RiskLevel
from miniclaude.tools.workspace import Workspace

_PATH_ARGS = ("path", "file", "target", "filename")

# 一律拒绝：不可逆或明显越界
DESTRUCTIVE_PATTERNS: tuple[str, ...] = (
    "rm -rf /", "rm -rf ~", "rm -rf ./*", "mkfs", "dd if=", ":(){", "shutdown", "reboot",
    "git reset --hard", "git clean -f", "git clean -fd", "git push --force", "git push -f",
    "git checkout .", "git restore .", "del /f /s /q", "rmdir /s /q", "remove-item -recurse -force",
    "chmod -r 777", "chown -r", "> /dev/sda",
)

# 管道到解释器必须用正则：`curl url | sh` 里 url 长度不定，字面量子串匹配不上
_PIPE_TO_INTERPRETER = re.compile(r"(?:curl|wget|iwr|invoke-webrequest)[^|]+\|\s*(?:sudo\s+)?(?:ba|z)?sh\b")

# 密钥与配置：AUTO 模式下直接拒绝
PROTECTED_NAMES = (".env", "id_rsa", "id_ed25519", "credentials.json", "secrets.json")
PROTECTED_SUFFIXES = (".pem", ".key", ".p12")


class Decision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class Answer(StrEnum):
    """用户对一次确认的三种回答。与 Decision 分开：一个是闸门的判断，一个是人的意志。"""

    ONCE = "once"
    ALWAYS = "always"
    NO = "no"


class PermissionMode(StrEnum):
    ASK = "ask"
    AUTO = "auto"
    READONLY = "readonly"


@dataclass(frozen=True)
class PermissionRule:
    """会话级规则。bash 用命令前缀匹配，其余工具整类放行。"""

    tool_name: str
    pattern: str = "*"
    decision: Decision = Decision.ALLOW

    def matches(self, tool_name: str, args: dict[str, Any]) -> bool:
        if self.tool_name != tool_name:
            return False
        if self.tool_name != "bash":
            return True
        command = str(args.get("command", ""))
        if self.pattern == "*":
            return True
        return command.strip().startswith(self.pattern)


@dataclass
class PermissionGate:
    """风险分级 + 路径边界 + 破坏性识别 + 会话规则，产出 Decision。"""

    workspace: Workspace
    mode: PermissionMode = PermissionMode.ASK
    auto_allow: frozenset[RiskLevel] = frozenset({RiskLevel.READ})
    confirmer: Callable[[str, str], Answer] | None = None  # (工具名, 一行摘要) -> 人的回答
    _rules: list[PermissionRule] = field(default_factory=list, repr=False)

    grants: list[str] = field(default_factory=list)

    # --------------------------------------------------------------- 主入口

    def check(self, tool: BaseTool, args: dict[str, Any]) -> tuple[Decision, str]:
        """返回 (决定, 给人或模型看的理由)。"""
        escaped = self._escape_reason(tool, args)
        if escaped:
            return Decision.DENY, escaped

        command = str(args.get("command", "")) if tool.name == "bash" else ""
        if command and self.is_destructive(command):
            return Decision.DENY, f"该命令被列为破坏性操作，Agent 不会执行：{command!r}"

        if tool.name in ("write_file", "edit_file") and self._is_protected(str(args.get("path", ""))):
            if self.mode is PermissionMode.AUTO:
                return Decision.DENY, "非交互模式下拒绝写入密钥/凭据类文件。"
            return Decision.ASK, "即将修改可能包含密钥的文件，请单独确认。"

        for rule in self._rules:
            if rule.matches(tool.name, args):
                return rule.decision, f"命中已授权规则 {rule.tool_name}:{rule.pattern}"

        if tool.risk_level in self.auto_allow:
            return Decision.ALLOW, "只读工具，自动放行"

        if self.mode is PermissionMode.READONLY:
            return Decision.DENY, f"当前是只读模式，{tool.risk_level} 级操作被禁用。"
        if self.mode is PermissionMode.AUTO:
            return Decision.ALLOW, "非交互模式，工作区内自动放行"
        return Decision.ASK, f"{tool.risk_level} 级操作，需要用户确认"

    def authorize(self, tool: BaseTool, args: dict[str, Any]) -> tuple[bool, str]:
        """给出最终可否执行的结论；ASK 时走 confirmer。"""
        decision, reason = self.check(tool, args)
        if decision is Decision.DENY:
            return False, reason
        if decision is Decision.ALLOW:
            return True, reason

        if self.confirmer is None:
            # 没有交互能力却需要确认 —— 保守拒绝比擅自执行安全
            return False, "需要用户确认，但当前没有可用的确认渠道。"

        verdict = self.confirmer(tool.name, self.describe(tool, args))
        if verdict is Answer.NO:
            return False, "用户拒绝了这次操作。"
        if verdict is Answer.ALWAYS:
            prefix = self._prefix_for_always(tool, args)
            self.add_rule(PermissionRule(tool_name=tool.name, pattern=prefix))
            self.grants.append(f"{tool.name}:{prefix}")
            return True, "用户授权本会话内同类操作。"
        return True, "用户确认执行本次操作。"

    # --------------------------------------------------------------- 辅助

    def add_rule(self, rule: PermissionRule) -> None:
        self._rules.append(rule)

    def rules(self) -> list[PermissionRule]:
        return list(self._rules)

    def describe(self, tool: BaseTool, args: dict[str, Any]) -> str:
        """生成一行摘要，让人 1 秒内判断该不该放行。"""
        if tool.name == "bash":
            return f"$ {str(args.get('command', '')).strip()}"
        path = str(args.get("path", ""))
        if tool.name in ("write_file", "edit_file") and path:
            try:
                path = self.workspace.rel(path)
            except ValueError:
                pass
            if tool.name == "edit_file":
                old = str(args.get("old_string", "")).strip().replace("\n", "\\n")
                return f"编辑 {path}：{old[:60]}{'…' if len(old) > 60 else ''}"
            return f"写入 {path}（{len(str(args.get('content', '')).splitlines())} 行）"
        return f"{tool.name}({', '.join(f'{k}={str(v)[:40]}' for k, v in args.items())})"

    def is_destructive(self, command: str) -> bool:
        normalized = " ".join(command.lower().split())
        if any(pattern in normalized for pattern in DESTRUCTIVE_PATTERNS):
            return True
        return bool(_PIPE_TO_INTERPRETER.search(normalized))

    def _is_protected(self, path: str) -> bool:
        name = Path(path).name.lower()
        return name in PROTECTED_NAMES or name.endswith(PROTECTED_SUFFIXES)

    def _escape_reason(self, tool: BaseTool, args: dict[str, Any]) -> str | None:
        """工具入参里的路径必须落在工作区内 —— 这条不受 mode 影响。"""
        for key in _PATH_ARGS:
            value = args.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            if key == "target" and tool.name == "run_tests" and "::" in value:
                value = value.split("::", 1)[0]
            if not self.workspace.contains(value):
                return f"路径 {value!r} 超出工作区 {self.workspace.root}，已拒绝。"
        return None

    @staticmethod
    def _prefix_for_always(tool: BaseTool, args: dict[str, Any]) -> str:
        """"始终允许"只授权到命令前缀，不授权整个 bash 工具。

        用户说"以后 pytest 别再问我"，不该变成"以后任何 shell 命令都不用问"。
        """
        if tool.name != "bash":
            return "*"
        try:
            tokens = shlex.split(str(args.get("command", "")))
        except ValueError:
            tokens = str(args.get("command", "")).split()
        if not tokens:
            return "*"
        # 取前两段：`python -m pytest ...`、`git diff`、`ls -la`
        prefix = " ".join(tokens[:2]) if tokens[0] in {"python", "python3", "git", "pip", "npm"} else tokens[0]
        return prefix
