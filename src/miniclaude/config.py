"""全项目唯一读取环境变量的地方：其余模块只接收 Config 对象。"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, int] = {
    "MAX_TURNS": 25,
    "MAX_TOKENS": 4096,
    "BASH_TIMEOUT": 60,
    "TOOL_OUTPUT_LIMIT": 30_000,
    "TOKEN_BUDGET": 120_000,
    "MAX_TOTAL_TOKENS": 800_000,
    "LLM_REQUEST_TIMEOUT": 120,
}


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
    max_total_tokens: int = DEFAULTS["MAX_TOTAL_TOKENS"]
    request_timeout: int = DEFAULTS["LLM_REQUEST_TIMEOUT"]
    trace_path: Path | None = None

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

        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            project_root=root,
            max_turns=_int("MAX_TURNS"),
            max_tokens=_int("MAX_TOKENS"),
            bash_timeout=_int("BASH_TIMEOUT"),
            tool_output_limit=_int("TOOL_OUTPUT_LIMIT"),
            token_budget=_int("TOKEN_BUDGET"),
            max_total_tokens=_int("MAX_TOTAL_TOKENS"),
            request_timeout=_int("LLM_REQUEST_TIMEOUT"),
            trace_path=trace_path,
        )

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
            "trace_path": str(self.trace_path) if self.trace_path else None,
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
