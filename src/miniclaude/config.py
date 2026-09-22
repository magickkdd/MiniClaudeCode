"""全项目唯一读取环境变量的地方：其余模块只接收 Config 对象。"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "MAX_TURNS": 25,
    "MAX_TOKENS": 4096,
    "BASH_TIMEOUT": 60,
    "TOOL_OUTPUT_LIMIT": 30_000,
    # 两个旋钮，不是一根线（SPEC v2 §0.4 / §3.3）。v1 把它们混在 TOKEN_BUDGET=120000 里，
    # 结果两头都不成立：120000 既不是真实边界（实测窗口 ≥ 270,570），也不是合理预算
    # （v1 全部 live 轨迹峰值仅 12,185，压力 0.10 → 压缩阶梯是死代码）。
    "TOKEN_BUDGET": 32_000,          # 成本与注意力质量预算：压缩阶梯挂它
    "CONTEXT_HARD_LIMIT": 200_000,   # 只防一件事：请求被端点拒收
    "CONTEXT_COMPACT": 1,            # 0 = 关掉阶梯（B2 要"压缩关闭时失败"这一半对照）
    "REPO_MAP": 1,                   # 0 = 退回 30 行目录树（B3 的对照臂）
    "REPO_MAP_TOKENS": 1_500,        # 地图自己的 token 预算，计入 system 与估算
    "MAX_TOTAL_TOKENS": 800_000,
    "LLM_REQUEST_TIMEOUT": 120,
    # §3.6：默认 local 而不是 auto。"有 docker 就用"会让同一个 .env 在不同机器上
    # 跑出不同后端，而 B6 要问的恰恰是"换后端会不会换结论"。
    "EXECUTION_BACKEND": "local",
    "DOCKER_IMAGE": "python:3.12-slim",
    "DOCKER_NETWORK": "none",        # 空串 = 不传 --network（宿主网络），仅本地排障用
    "CHECKPOINTS": 1,                # 0 = 不建影子 git（A3 只读演练与容器内跑批用）
    "MEMORY_DIR": ".mcc",            # 记忆 / 快照 / 会话快照的唯一落盘目录名
}

# 字符串型旋钮的缺省值与整数型共用 DEFAULTS 这一张表，由 `_str()` 读取。
# 拆成两张表就会出现"改了表 A 忘了表 B"，而这类漂移在配置层是静默的。

# 合法的后端名。实现住在 `backend/factory.py`，这里只放契约 —— 配置层要能在启动时
# 拒绝打错的名字，而不该为此 import 执行层。两边一致由
# `tests/test_backend_factory.py::test_every_advertised_name_has_an_implementation` 钉住。
BACKEND_NAMES: tuple[str, ...] = ("local", "docker")


class ConfigError(RuntimeError):
    """配置缺失或非法 —— 启动时立刻失败，不要带病运行。"""


def _mask(secret: str) -> str:
    """密钥脱敏。任何打印或落盘都必须走这里。"""
    if len(secret) <= 12:
        return "***"
    return f"{secret[:4]}...{secret[-4:]}"


@dataclass(frozen=True)
class Config:
    """运行期配置。frozen 保证它在整个 Agent 生命周期内不可被悄悄改掉。"""

    base_url: str
    api_key: str
    model: str
    project_root: Path
    max_turns: int = DEFAULTS["MAX_TURNS"]
    max_tokens: int = DEFAULTS["MAX_TOKENS"]
    bash_timeout: int = DEFAULTS["BASH_TIMEOUT"]
    tool_output_limit: int = DEFAULTS["TOOL_OUTPUT_LIMIT"]
    token_budget: int = DEFAULTS["TOKEN_BUDGET"]
    context_hard_limit: int = DEFAULTS["CONTEXT_HARD_LIMIT"]
    context_compact: bool = bool(DEFAULTS["CONTEXT_COMPACT"])
    repo_map: bool = bool(DEFAULTS["REPO_MAP"])
    repo_map_tokens: int = DEFAULTS["REPO_MAP_TOKENS"]
    max_total_tokens: int = DEFAULTS["MAX_TOTAL_TOKENS"]
    request_timeout: int = DEFAULTS["LLM_REQUEST_TIMEOUT"]
    price_per_mtokens: float = 0.0
    trace_path: Path | None = None
    execution_backend: str = str(DEFAULTS["EXECUTION_BACKEND"])
    docker_image: str = str(DEFAULTS["DOCKER_IMAGE"])
    docker_network: str = str(DEFAULTS["DOCKER_NETWORK"])
    checkpoints: bool = bool(DEFAULTS["CHECKPOINTS"])
    memory_dir: str = str(DEFAULTS["MEMORY_DIR"])

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "Config":
        """读 .env 与环境变量。真实环境变量优先于 .env，便于 CI 覆盖。"""
        cwd = Path.cwd()
        candidate = Path(env_file) if env_file else cwd / ".env"
        if candidate.is_file():
            _load_dotenv(candidate)

        base_url = _required("LLM_BASE_URL").rstrip("/")
        model = _required("LLM_MODEL")
        api_key = _required("LLM_API_KEY")
        root = _resolve(_get("PROJECT_ROOT") or ".", cwd)

        trace_raw = _get("TRACE_PATH")
        trace_path = _resolve(trace_raw, root) if trace_raw else None

        budget = _int("TOKEN_BUDGET")
        hard_limit = _int("CONTEXT_HARD_LIMIT")
        if budget >= hard_limit:
            # 预算线压在硬熔断之上，阶梯就永远轮不到出手 —— 每次都先被端点拒收。
            raise ConfigError(
                f"TOKEN_BUDGET({budget}) 必须小于 CONTEXT_HARD_LIMIT({hard_limit})："
                "前者是压缩阶梯挂的预算，后者是防拒收的熔断，调反了等于关掉压缩。"
            )
        map_tokens = _int("REPO_MAP_TOKENS")
        if map_tokens < 1:
            # SPEC §5.3 原写"`REPO_MAP_TOKENS=0` 即关闭"。as-built 改成两个字段各司其职：
            # 预算字段只管大小，开关是 REPO_MAP=0。留 0 这条路会得到一张只有页脚的地图。
            raise ConfigError(
                f"REPO_MAP_TOKENS({map_tokens}) 至少 1：要关地图请用 REPO_MAP=0（B3 的对照臂走的就是它）。"
            )
        backend = _str("EXECUTION_BACKEND").lower()
        if backend not in BACKEND_NAMES:
            # 打错的后端名绝不能静默变成 local —— 那正是 §3.6 禁止的"换了后端没人知道"。
            raise ConfigError(
                f"EXECUTION_BACKEND 只能是 {' / '.join(BACKEND_NAMES)}，当前值：{backend!r}"
            )
        memory_dir = _str("MEMORY_DIR")
        if not memory_dir or ".." in Path(memory_dir).parts:
            raise ConfigError(f"MEMORY_DIR 不能为空或包含 ..，当前值：{memory_dir!r}")

        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            project_root=root,
            max_turns=_int("MAX_TURNS"),
            max_tokens=_int("MAX_TOKENS"),
            bash_timeout=_int("BASH_TIMEOUT"),
            tool_output_limit=_int("TOOL_OUTPUT_LIMIT"),
            token_budget=budget,
            context_hard_limit=hard_limit,
            context_compact=bool(_int("CONTEXT_COMPACT")),
            repo_map=bool(_int("REPO_MAP")),
            repo_map_tokens=map_tokens,
            max_total_tokens=_int("MAX_TOTAL_TOKENS"),
            request_timeout=_int("LLM_REQUEST_TIMEOUT"),
            price_per_mtokens=_float("PRICE_PER_MTOKENS"),
            trace_path=trace_path,
            execution_backend=backend,
            docker_image=_str("DOCKER_IMAGE") or str(DEFAULTS["DOCKER_IMAGE"]),
            docker_network=_str("DOCKER_NETWORK"),
            checkpoints=bool(_int("CHECKPOINTS")),
            memory_dir=memory_dir,
        )

    @property
    def memory_root(self) -> Path:
        """记忆 / 快照 / 会话快照的唯一落盘位置。§3.4、§3.6 的所有路径都从这里派生。"""
        mem = Path(self.memory_dir).expanduser()
        return mem if mem.is_absolute() else self.project_root / mem

    def redacted(self) -> dict[str, Any]:
        """可安全打印 / 写进 trace 的配置视图 —— 密钥永不进日志。"""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key": _mask(self.api_key),
            "project_root": str(self.project_root),
            "max_turns": self.max_turns,
            "max_tokens": self.max_tokens,
            "max_total_tokens": self.max_total_tokens,
            "token_budget": self.token_budget,
            "context_hard_limit": self.context_hard_limit,
            "context_compact": self.context_compact,
            "repo_map": self.repo_map,
            "repo_map_tokens": self.repo_map_tokens,
            "price_per_mtokens": self.price_per_mtokens,
            "trace_path": str(self.trace_path) if self.trace_path else None,
            "execution_backend": self.execution_backend,
            "docker_image": self.docker_image,
            "docker_network": self.docker_network,
            "checkpoints": self.checkpoints,
            "memory_root": str(self.memory_root),
        }

    def with_root(self, project_root: Path) -> "Config":
        """派生一份只改工作目录的配置（测试里指向 tmp_path）。"""
        return replace(self, project_root=Path(project_root).resolve())


def _load_dotenv(path: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return  # 没装 python-dotenv 时退回纯环境变量，不是致命错误
    load_dotenv(path, override=False)


def _get(name: str) -> str:
    return os.getenv(name, "").strip()


def _required(name: str) -> str:
    value = _get(name)
    if not value:
        raise ConfigError(
            f"缺少环境变量 {name}。请复制 .env.example 为 .env 并填写，或先 export 该变量。"
        )
    return value


def _int(name: str) -> int:
    raw = _get(name)
    if not raw:
        return DEFAULTS[name]
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数，当前值：{raw!r}") from exc


def _str(name: str) -> str:
    """字符串型旋钮。区分"没设"与"设成空串"：`DOCKER_NETWORK=` 是刻意的"不限制网络"，
    用 `_get()` 的 `or 缺省` 会把这个空值吃回 "none"，于是用户怎么写都不生效 ——
    不生效的配置项比缺失的配置项更坏（SPEC v2 §7.2 行 12 的教训）。"""
    raw = os.getenv(name)
    if raw is None:
        return str(DEFAULTS[name])
    return raw.strip()


def _float(name: str) -> float:
    """目前只有单价用浮点：token 计价天然是小数，用 int 会把它压成 0 或 1。"""
    raw = _get(name)
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是数字，当前值：{raw!r}") from exc


def _resolve(raw: str, base: Path) -> Path:
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


_CACHE: Config | None = None


def get_config(env_file: str | Path | None = None) -> Config:
    """读取配置，首次调用后缓存。"""
    global _CACHE
    if _CACHE is None:
        _CACHE = Config.from_env(env_file)
    return _CACHE


def reset_config_cache() -> None:
    """清掉缓存，仅测试使用。"""
    global _CACHE
    _CACHE = None
