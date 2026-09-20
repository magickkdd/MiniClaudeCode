"""会话追踪 —— 每行一条 JSONL。

调试 Agent 必须有这个：模型为什么绕了三个工具、哪一步参数是幻觉、
上下文在哪轮涨爆，靠 print 永远查不清。它同时是 V2 评测的数据源，
所以字段设计要能直接算出 SPEC §3.7 的指标。

写盘失败一律静默降级 —— 日志绝不能拖垮主流程。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

# 任何字段里出现这类形状的串都要掩掉，不只看 key 名
_SECRET_LIKE = re.compile(r"(sk|pk|key|token|secret|bearer)[-_][A-Za-z0-9]{8,}", re.IGNORECASE)
_SENSITIVE_KEYS = ("api_key", "authorization", "token", "secret", "password")
MAX_STRING = 2000


class Tracer:
    """追加式 JSONL 记录器。None 路径表示禁用（测试与只读演练用）。"""

    def __init__(self, path: Path | None = None, *, session_id: str | None = None) -> None:
        self.path = Path(path) if path else None
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self._seq = 0
        self._buffer: list[str] = []
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.path = None

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def log(self, kind: str, **payload: Any) -> None:
        if self.path is None:
            return
        self._seq += 1
        record = {
            "seq": self._seq,
            "ts": round(time.time(), 3),
            "session": self.session_id,
            "kind": kind,
            **_scrub(payload),
        }
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            self.path = None  # 一次写失败就不再尝试，避免每轮都撞盘

    def start_session(self, *, model: str, tools: list[str], config: dict[str, Any]) -> None:
        self.log("session_start", model=model, tools=tools, config=config)

    def close(self) -> None:
        return None

    def __enter__(self) -> "Tracer":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _scrub(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in _SENSITIVE_KEYS:
            clean[key] = "***"
        else:
            clean[key] = _scrub_value(value)
    return clean


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        masked = _SECRET_LIKE.sub(lambda m: m.group(0)[:4] + "***", value)
        return masked if len(masked) <= MAX_STRING else masked[:MAX_STRING] + f"…({len(masked)} chars)"
    if isinstance(value, dict):
        return _scrub(value)
    if isinstance(value, (list, tuple)):
        return [_scrub_value(item) for item in value]
    return value


def replay(path: Path) -> list[dict[str, Any]]:
    """读回一次会话轨迹，容忍中途写入失败留下的坏行。"""
    records: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def summarize(path: Path) -> dict[str, Any]:
    """把轨迹压成一份报告 —— demo 文档里的数字全部来自这里，不靠手填。"""
    records = replay(path)
    tools = [r for r in records if r.get("kind") == "tool_call"]
    turns = [r for r in records if r.get("kind") == "turn_start"]
    responses = [r for r in records if r.get("kind") == "llm_response"]
    usage = sum(
        (r.get("usage", {}) or {}).get("prompt", 0) + (r.get("usage", {}) or {}).get("completion", 0)
        for r in responses
    )
    end = next((r for r in reversed(records) if r.get("kind") == "run_end"), {})
    return {
        "session": records[0]["session"] if records else None,
        "turns": len(turns),
        "tool_calls": len(tools),
        "tool_errors": sum(1 for tool in tools if not tool.get("ok", True)),
        "redundant_calls": end.get("redundant_calls", 0),
        "denied_actions": end.get("denied_actions", 0),
        "tokens": usage,
        "termination": end.get("termination", "unknown"),
        "tool_sequence": [tool.get("name") for tool in tools],
    }
