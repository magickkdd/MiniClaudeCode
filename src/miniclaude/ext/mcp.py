"""MCP bridge —— 把远端工具包成 `BaseTool`，校验链路一条都不能少（SPEC v2 §3.7、D19）。

三条硬要求，每条都有对应的测试钉着：

① **命名空间前缀** `mcp__<server>__<tool>`：远端永远叫不出 `read_file` 这个名字，
   所以它没有"覆盖本地工具"这条路。
② **远端自报的 risk_level 不可信**（D19）：一律按 `EXECUTE` 处理，自报值只作展示。
   一个自称 `read` 的远端工具可以在它的进程里做任何事 —— 风险不由它自己声明。
③ 走 `BaseTool.invoke()` 的全套校验（参数校验、类型检查、`_coerce`、`_normalize`、
   截断、异常兜底）。远端返回不是字符串时照样兜住。

传输只实现 `stdio`（换行分隔的 JSON-RPC 2.0，即 MCP 的 stdio transport）。
其余传输方式在 §7.4 的砍单顺位第 2 条里，配置中出现即拒绝 —— 静默接受一个
不生效的 `transport` 字段，等于让用户以为沙箱在跑。

装配只有一条路：`ToolRegistry.default(workspace, extra_tools=bridge.discover())`
（`tools/registry.py:58`）。这里**不新增第二条装配路径**。
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from miniclaude.tools.base import BaseTool, RiskLevel, ToolResult
from miniclaude.tools.workspace import Workspace

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "mini-claude-code", "version": "0.1.0"}

NAMESPACE = "mcp"
NAME_SEP = "__"

# 只有 stdio 落地了（§7.4 顺位 2）。这个名字与 `config.MCP_TRANSPORTS` 的一致性由
# `tests/test_mcp_bridge.py` 钉住 —— 和 BACKEND_NAMES 同一个套路：配置层不 import 执行层。
SUPPORTED_TRANSPORTS: tuple[str, ...] = ("stdio",)

# 工具名要进 API 报文的 `name` 字段，字符集收紧到端点普遍接受的范围。
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")

# 子进程环境的最小集合：只给"不起进程就起不来"的那些。
# 刻意不含 PYTHONPATH —— 宿主的路径配置不该出现在远端进程的视野里（B6 那条
# "宿主 env 一条都不该透传"的判据在沙箱外同样成立）。
_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
    }
)

# 名字长得像密钥的环境变量，即使用户把它写进 `env` 也不透传：`.env` 里那把 key
# 不该因为我们装了第三方 MCP 服务就流进别人的进程。
_SECRETISH = re.compile(r"(api[-_]?key|token|secret|password|credential|passwd)", re.I)

# `--token ghp_xxx` 这种写法在服务配置里太常见了，而 `mcp` 事件与 `mcc mcp` 都要打印
# 命令行。trace 的 `_scrub` 认的是"值本身长得像密钥"，认不出"这个位置的值是密钥" ——
# 所以旗标形态在这里自己处理，别指望下游兜住。
_SECRET_FLAG = re.compile(r"^-{1,2}[A-Za-z0-9_.-]*(key|token|secret|password|passwd|credential|auth)[A-Za-z0-9_.-]*$", re.I)
_SECRET_SHAPE = re.compile(r"^(sk|ghp|github_pat|xox|AKIA|glpat)[-_][A-Za-z0-9]{6,}")

MAX_STDERR_CHARS = 4000


class MCPError(RuntimeError):
    """一次远端调用失败。工具的报错是模型的输入，所以只有装配期用它。"""


class MCPSpecError(ValueError):
    """配置里的 server 描述不合法 —— 启动时就拒绝，不带病运行。"""


@dataclass(frozen=True)
class MCPServerSpec:
    """一个 MCP 服务端的描述。`endpoint` 在 stdio 下就是可执行文件路径。"""

    name: str
    transport: str = "stdio"
    endpoint: str = ""
    args: tuple[str, ...] = ()
    env: tuple[str, ...] = ()

    @property
    def namespace(self) -> str:
        return self.name

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "MCPServerSpec":
        """从 config 交来的字典构造。**语义**校验住在这里（形状校验住在 config）。"""
        if not isinstance(raw, dict):
            raise MCPSpecError(f"MCP_SERVERS 的每一项必须是对象，当前是 {type(raw).__name__}")
        unknown = set(raw) - {"name", "transport", "endpoint", "args", "env"}
        if unknown:
            # 打错的键（`command` 写成了 `endPoint`）如果被判合法，服务会"配了但没起来"。
            raise MCPSpecError(f"MCPServerSpec 不认识这些键：{sorted(unknown)}")
        name = str(raw.get("name", "")).strip()
        if not _SAFE_SEGMENT.match(name):
            raise MCPSpecError(
                f"MCP server 名字只能是字母/数字/._- 且不以符号开头（要进工具名），当前值：{name!r}"
            )
        transport = str(raw.get("transport", "stdio")).strip().lower() or "stdio"
        if transport not in SUPPORTED_TRANSPORTS:
            raise MCPSpecError(
                f"MCP transport 只实现了 {' / '.join(SUPPORTED_TRANSPORTS)}，当前值：{transport!r}"
            )
        endpoint = str(raw.get("endpoint", "")).strip()
        if not endpoint:
            raise MCPSpecError(f"MCP server {name!r} 没有 endpoint：stdio 传输要给出可执行文件路径")
        args = raw.get("args", ()) or ()
        if not isinstance(args, (list, tuple)):
            raise MCPSpecError(f"MCP server {name!r} 的 args 必须是数组")
        env = raw.get("env", ()) or ()
        if not isinstance(env, (list, tuple)):
            raise MCPSpecError(f"MCP server {name!r} 的 env 必须是数组（要透传的环境变量**名**）")
        return cls(
            name=name,
            transport=transport,
            endpoint=endpoint,
            args=tuple(str(item) for item in args),
            env=tuple(str(item) for item in env),
        )

    def as_trace(self) -> dict[str, Any]:
        """可落盘的描述。args 逐位过 `_mask_arg`：`["--token","ghp_xxx"]` 这种写法
        在服务配置里很常见，而 `mcp` 事件和 `mcc mcp` 都会把它打出来。"""
        return {
            "name": self.name,
            "transport": self.transport,
            "endpoint": self.endpoint,
            "args": [part for part in _mask_args(self.args)],
            "env": list(self.env),
        }


def tool_name(server: str, remote: str) -> str:
    return f"{NAMESPACE}{NAME_SEP}{server}{NAME_SEP}{remote}"


def _declared_risk(item: Mapping[str, Any]) -> str:
    """远端**自报**的风险档位。只用于展示与记录，永远不参与判定（D19）。

    字段名有两种现实：MCP 规范给的是 `annotations.readOnlyHint` / `destructiveHint`
    （官方 SDK 写的服务就是这个形状），自报一个 `riskLevel` 则是一些服务的私有写法。
    两种都读出来拼成一句 —— 面板上要看得见"它说它只读"，而我们照旧按 EXECUTE 办。
    """
    claims: list[str] = []
    raw = item.get("riskLevel") or item.get("risk_level")
    if raw:
        claims.append(str(raw).strip().lower())
    annotations = item.get("annotations")
    if isinstance(annotations, dict):
        if annotations.get("readOnlyHint"):
            claims.append(RiskLevel.READ.value)
        if annotations.get("destructiveHint"):
            claims.append("destructive")
    return "+".join(dict.fromkeys(claim for claim in claims if claim))


def _mask_args(args: Sequence[str]) -> list[str]:
    masked: list[str] = []
    armed = False  # 上一个位置是 `--token` 这类旗标，那么下一个位置就是它的值
    for part in args:
        if armed:
            masked.append(_hidden(part))
            armed = False
            continue
        if _SECRET_FLAG.match(part):
            masked.append(part)
            armed = True
            continue
        head, sep, value = part.partition("=")
        if sep and _SECRET_FLAG.match(head):
            masked.append(f"{head}{sep}{_hidden(value)}")
            continue
        masked.append(part if not _SECRET_SHAPE.match(part) else _hidden(part))
    return masked


def _hidden(value: str) -> str:
    return f"‹已脱敏 {len(value)} 字符›"


def parse_env(spec: MCPServerSpec) -> tuple[dict[str, str], list[str]]:
    """子进程环境 + 被剔除的变量名。

    返回 `(env, dropped)`：剔除必须留痕，不能静默。用户写了 `env: ["LLM_API_KEY"]`
    却发现服务连不上时，他有权知道是我们扣下的。
    """
    env = {key: value for key, value in os.environ.items() if key.upper() in _ENV_ALLOWLIST}
    # MCP 的 stdio 传输按规范是 UTF-8，所以这一条不是偏好而是协议要求。Windows 上的
    # Python 子进程默认按 ANSI 代码页（中文机器是 cp936）写 stdout/stderr，不钉住它，
    # 远端返回的中文就会变成乱码 —— 而且是在**我们**这侧解坏的，看起来像远端的错。
    env["PYTHONIOENCODING"] = "utf-8"
    dropped: list[str] = []
    for name in spec.env:
        if _SECRETISH.search(name):
            dropped.append(name)
            continue
        if name in os.environ:
            env[name] = os.environ[name]
        else:
            dropped.append(name)  # 没设的变量传空串会让服务以为"配置为空"，不如不传
    return env, dropped


class _StdioSession:
    """一个子进程 + 换行分隔的 JSON-RPC。同步读写：一轮一问一答，没有并发。"""

    def __init__(self, spec: MCPServerSpec, *, timeout: float = 30.0) -> None:
        self.spec = spec
        self.timeout = timeout
        self.proc: subprocess.Popen[Any] | None = None
        self.server_info: dict[str, Any] = {}
        self.dropped_env: list[str] = []
        self._next_id = 0
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._stderr = tempfile.TemporaryFile(mode="w+b")
        self._reader: threading.Thread | None = None

    # ------------------------------------------------------------- 生命周期

    def start(self) -> dict[str, Any]:
        if self.proc is not None:
            raise MCPError(f"MCP server {self.spec.name} 已经在跑")
        env, dropped = parse_env(self.spec)
        self.dropped_env = dropped
        argv = [self.spec.endpoint, *self.spec.args]
        try:
            # shell=False 是刻意的：参数永远是参数，拼不成一条 shell 命令行。
            self.proc = subprocess.Popen(  # noqa: S603
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
                env=env,
                bufsize=0,
            )
        except OSError as exc:
            raise MCPError(f"起不来 MCP server {self.spec.name}：{exc}（endpoint={self.spec.endpoint!r}）") from exc
        self._reader = threading.Thread(target=self._pump, name=f"mcp-{self.spec.name}", daemon=True)
        self._reader.start()

        info = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        self.server_info = info if isinstance(info, dict) else {}
        self.notify("notifications/initialized")
        return self.server_info

    def _pump(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        stream = self.proc.stdout
        while True:
            try:
                line = stream.readline()
            except (OSError, ValueError):  # 进程被 kill 后管道先炸
                break
            if not line:
                break
            try:
                text = line.decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                continue
            if text:
                self._lines.put(text)
        self._lines.put(None)  # EOF 哨兵：等待方据此报"对端关掉了输出"

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            try:
                self.proc.kill()
            except OSError:
                pass
        self.proc = None

    # ------------------------------------------------------------- 报文

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """一次问答。`timeout` 是这一条的单独预算（不写就用会话缺省）：
        tools/call 的分钟级长任务与握手的"起不来要快点知道"不该共用一根线。"""
        if self.proc is None:
            raise MCPError(f"MCP server {self.spec.name} 没在跑")
        self._next_id += 1
        call_id = self._next_id
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": call_id, "method": method}
        if params is not None:
            payload["params"] = dict(params)
        self._write(payload)
        budget = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + budget
        while True:
            line = self._readline(deadline, budget)
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # 有的服务会往 stdout 吐日志行：跳过它，别把它当成回答
            if not isinstance(message, dict) or message.get("id") != call_id:
                continue  # 通知、或给别人的回答
            if "error" in message:
                err = message.get("error") or {}
                raise MCPError(
                    f"MCP server {self.spec.name} 报 {method} 失败："
                    f"{err.get('code')} {str(err.get('message'))[:200]}"
                )
            return message.get("result")

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = dict(params)
        self._write(payload)

    def _write(self, payload: Mapping[str, Any]) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        if self.proc.poll() is not None:
            raise MCPError(
                f"MCP server {self.spec.name} 已经退出（退出码 {self.proc.returncode}）："
                f"{self.stderr_text()}"
            )
        # ensure_ascii=True：报文里只有 ASCII 字节，任何代码页的 pipe 都能原样透传。
        # 非 ASCII 由 JSON 的 \uXXXX 转义承载，解码后仍然正确 —— 这是 Windows 上
        # 唯一不依赖子进程 stdout 编码的写法（另一半靠 PYTHONIOENCODING）。
        data = (json.dumps(payload, ensure_ascii=True) + "\n").encode("ascii")
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except OSError as exc:
            raise MCPError(f"写给 MCP server {self.spec.name} 时断了：{exc}") from exc

    def _readline(self, deadline: float, budget: float) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MCPError(self._fault(f"回答超时（{budget:g}s）"))
        try:
            line = self._lines.get(timeout=remaining)
        except queue.Empty as exc:
            raise MCPError(self._fault(f"回答超时（{budget:g}s）")) from exc
        if line is None:
            raise MCPError(self._fault(f"关掉了输出（进程退出码 {self._exit_code()}）"))
        return line

    def _fault(self, what: str) -> str:
        """把 stderr 拼进故障说明。

        一个崩掉的服务**只**通过 stderr 说话；把它丢掉，用户看到的就是
        "关掉了输出（进程退出码 None）"这种没法行动的信息，只能来问我们。
        """
        detail = self.stderr_text()
        base = f"MCP server {self.spec.name} {what}"
        return f"{base}：{detail}" if detail else base

    def _exit_code(self) -> int | None:
        return self.proc.returncode if self.proc is not None else None

    def stderr_text(self) -> str:
        """有限量地读回 stderr —— 它是唯一的诊断渠道，也是唯一的泄密渠道。"""
        try:
            self._stderr.seek(0)
            raw = self._stderr.read(MAX_STDERR_CHARS * 4)
        except (OSError, ValueError):
            return ""
        text = raw.decode("utf-8", "replace").strip()
        return text[-MAX_STDERR_CHARS:] if len(text) > MAX_STDERR_CHARS else text


class RemoteTool(BaseTool):
    """一个远端工具。风险等级由我们定，不由它自报。"""

    risk_level = RiskLevel.EXECUTE  # D19：不可信远端的默认档位
    external = True  # 权限门据此在 AUTO 模式下也要求确认（§6.3-1）

    def __init__(
        self,
        workspace: Workspace,
        *,
        bridge: "MCPBridge",
        server: str,
        remote_name: str,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
        declared_risk: str = "",
    ) -> None:
        super().__init__(workspace)
        self.bridge = bridge
        self.server = server
        self.remote_name = remote_name
        self.name = tool_name(server, remote_name)
        self.declared_risk = declared_risk  # 只作展示，永不参与判定
        head = f"[MCP/{server}]"
        if declared_risk and declared_risk != RiskLevel.EXECUTE.value:
            head += f"（远端自报风险 {declared_risk}，未采信）"
        self.description = f"{head} {description}".strip()
        self.input_schema = input_schema or {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> ToolResult:
        return self.bridge.call(self.server, self.remote_name, kwargs)


@dataclass
class MCPBridge:
    """持有若干 `_StdioSession`，把 tools/list 变成 BaseTool 列表。

    两根线各管一头（mcp-link-spec P1）：`timeout` 管握手与发现 —— 配置错了要在
    会话开始时就知道；`tool_timeout` 管单次 tools/call —— insight-agent 的 research
    fast 档实测 ≈90s，fixed 30s 的会话线必然误杀它。`MCP_TOOL_TIMEOUT` 是后者的配置名。
    """

    servers: Sequence[MCPServerSpec]
    workspace: Workspace
    timeout: float = 30.0
    tool_timeout: float = 600.0
    _sessions: dict[str, _StdioSession] = field(default_factory=dict, repr=False)
    status: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def discover(self) -> list[BaseTool]:
        tools: list[BaseTool] = []
        seen: set[str] = set()
        for spec in self.servers:
            entry: dict[str, Any] = {**spec.as_trace(), "ok": False, "tools": [], "skipped": [], "error": ""}
            self.status.append(entry)
            try:
                session = self._open(spec)
                listing = session.request("tools/list")
            except MCPError as exc:
                entry["error"] = str(exc)
                self._drop(spec.name)
                continue
            advertised = listing.get("tools") if isinstance(listing, dict) else None
            if not isinstance(advertised, list):
                entry["error"] = "tools/list 的 result 里没有 tools 数组"
                self._drop(spec.name)
                continue
            for item in advertised:
                problem, built = self._wrap(spec, item, seen)
                if problem is not None:
                    entry["skipped"].append(problem)
                    continue
                assert built is not None
                seen.add(built.name)
                tools.append(built)
                entry["tools"].append({"name": built.name, "declared_risk": built.declared_risk})
            entry["ok"] = True
            entry["server_info"] = {
                "protocolVersion": session.server_info.get("protocolVersion"),
                "name": (session.server_info.get("serverInfo") or {}).get("name"),
                "title": (session.server_info.get("serverInfo") or {}).get("title"),
            }
            if session.dropped_env:
                entry["dropped_env"] = list(session.dropped_env)
        return tools

    def _wrap(
        self, spec: MCPServerSpec, item: Any, seen: Iterable[str]
    ) -> tuple[dict[str, str] | None, RemoteTool | None]:
        """把一个 tools/list 条目变成 RemoteTool，或者给出**放弃它的理由**。"""
        if not isinstance(item, dict):
            return {"name": "?", "reason": "条目不是对象"}, None
        remote = str(item.get("name", "")).strip()
        if not _SAFE_SEGMENT.match(remote):
            # 名字要拼进工具名，非法字符不能靠"顺手洗一下"糊过去：洗过的名字模型调不动。
            return {"name": remote or "?", "reason": f"远端工具名不合法：{remote!r}"}, None
        composed = tool_name(spec.name, remote)
        if composed in seen:
            return {"name": composed, "reason": "同名工具已经注册（远端报了两个同名的）"}, None
        schema = item.get("inputSchema") or item.get("input_schema")
        if not isinstance(schema, dict):
            return {"name": composed, "reason": "没有 inputSchema，参数没法校验"}, None
        if "properties" not in schema:
            # `_coerce()` 只把 schema 里点名的参数传给 run()。缺 properties 的对象型参数
            # 会被整批丢掉，工具收到空参数却"调用成功" —— 这是最难查的那种错。
            return {"name": composed, "reason": "inputSchema 没有 properties，参数会被静默丢弃"}, None
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return {"name": composed, "reason": "inputSchema.properties 不是对象"}, None
        declared = _declared_risk(item)
        return None, RemoteTool(
            self.workspace,
            bridge=self,
            server=spec.name,
            remote_name=remote,
            description=str(item.get("description", "")),
            input_schema={"type": "object", "properties": properties},
            declared_risk=str(declared),
        )

    def call(self, server: str, remote_name: str, args: Mapping[str, Any]) -> ToolResult:
        """远端调用。**永不抛异常** —— 报错是模型的输入（tools/base.py 的铁律）。"""
        try:
            session = self._existing(server)
            result = session.request(
                "tools/call", {"name": remote_name, "arguments": dict(args)}, timeout=self.tool_timeout
            )
        except MCPError as exc:
            self._drop(server)  # 挂死的服务不能留着毒化后面每一次调用
            return ToolResult.err(str(exc))
        return _decode_content(result)

    # ------------------------------------------------------------- 内部

    def _open(self, spec: MCPServerSpec) -> _StdioSession:
        session = self._sessions.get(spec.name)
        if session is None:
            session = _StdioSession(spec, timeout=self.timeout)
            session.start()
            self._sessions[spec.name] = session
        return session

    def _existing(self, server: str) -> _StdioSession:
        session = self._sessions.get(server)
        if session is None:
            spec = next((item for item in self.servers if item.name == server), None)
            if spec is None:
                raise MCPError(f"没有配置过名为 {server!r} 的 MCP server")
            return self._open(spec)
        return session

    def _drop(self, server: str) -> None:
        session = self._sessions.pop(server, None)
        if session is not None:
            session.close()

    def available(self) -> list[str]:
        return sorted(self._sessions)

    def stats(self) -> dict[str, Any]:
        return {
            "configured": [spec.name for spec in self.servers],
            "running": self.available(),
            "servers": self.status,
            "tools": sum(len(entry["tools"]) for entry in self.status),
            "skipped": sum(len(entry["skipped"]) for entry in self.status),
        }

    def close(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()


def _decode_content(result: Any) -> ToolResult:
    """把 tools/call 的 result 变成 ToolResult。远端给什么形状都要接得住。"""
    if isinstance(result, dict):
        blocks = result.get("content")
        texts: list[str] = []
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(str(block.get("text", "")))
                elif isinstance(block, dict):
                    # 图片/资源块我们暂时不消费，但也不能当没看见：模型需要知道有东西没给它。
                    texts.append(f"〈{block.get('type', 'unknown')} 内容块，本客户端不渲染〉")
                else:
                    texts.append(str(block))
        elif isinstance(blocks, str):
            texts.append(blocks)
        elif blocks is not None:
            texts.append(json.dumps(blocks, ensure_ascii=False)[:2000])
        body = "\n".join(texts).strip() or json.dumps(result, ensure_ascii=False)[:2000]
        structured = result.get("structuredContent")
        if structured is not None and not _already_in_text(structured, body):
            body += "\n" + json.dumps(structured, ensure_ascii=False)
        is_error = bool(result.get("isError"))
        return ToolResult(body, is_error=is_error) if body else ToolResult.err("远端返回了空内容")
    if isinstance(result, str):
        return ToolResult.ok(result)
    if result is None:
        return ToolResult.ok("")
    return ToolResult.ok(json.dumps(result, ensure_ascii=False, default=str))


def _already_in_text(structured: Any, body: str) -> bool:
    """`structuredContent` 的叶子值是不是全都已经在正文里出现过。

    官方 SDK 把 `-> str` 的返回值同时写进 `content[text]` 和
    `structuredContent["result"]` —— 两份一模一样。都发给模型就是让同一句话付两次
    token，而这一层的存在理由恰恰是"上下文是预算资源"。真有额外信息（数字、布尔、
    表）时照样追加。
    """
    leaves: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)
        else:
            leaves.append(str(node))

    walk(structured)
    return bool(leaves) and all(leaf in body for leaf in leaves)
