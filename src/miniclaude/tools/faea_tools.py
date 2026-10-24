"""FAEA FBR 专用工具（MCC-R fork；SPEC_M9 §4.2）。

FAEA_FBR=1 时由 build_session 注册。设计约束：
- run_episode 白名单硬编码只接受 episode.py——探针脚本仍走 bash（既有审计面）；
  执行前隔离陈旧 meta.json；argv 列表调用（无 shell）；超时杀进程组；
  返回 stdout/stderr 尾部 + 本次新增的诊断全文 + 新 meta.json。
- read_diagnostics 按 try_index 取诊断并对照沙箱外哈希索引校验，损坏/缺失
  返回明确错误（runner 据此标 diagnostics_missing）。
- 完整审计：tool_call 事件照常记录 args 摘要与 output_chars（trace 由 loop 写）。
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any

from miniclaude.tools.base import BaseTool, RiskLevel, ToolResult

STDOUT_TAIL = 2000
STDERR_TAIL = 2000


def _diag_dir(workspace: Any) -> Path:
    return Path(workspace.root) / "diagnostics"


def _list_diags(workspace: Any) -> dict[int, Path]:
    """try_index -> 文件路径（按 diagnostics_try_NNN.json 命名约定）。"""
    out: dict[int, Path] = {}
    d = _diag_dir(workspace)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("diagnostics_try_*.json")):
        try:
            idx = int(p.stem.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            continue
        out[idx] = p
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RunEpisodeTool(BaseTool):
    name = "run_episode"
    description = (
        "Run the official episode.py with the fixed LIBERO interpreter and return: "
        "stdout/stderr tails, any NEW per-try diagnostics JSON (auto-generated at the "
        "end of each attempt), and the fresh meta.json if the script wrote one. "
        "Only accepts the script name 'episode.py'. Read the returned diagnostics "
        "BEFORE editing the code."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "script": {
                "type": "string",
                "description": "must be exactly 'episode.py' (whitelisted)",
            },
            "timeout_s": {
                "type": "integer",
                "description": "wall-clock limit in seconds, default 900, max 3600",
            },
        },
        "required": [],
    }
    risk_level = RiskLevel.EXECUTE

    def __init__(self, workspace: Any, *, libero_python: str, default_timeout: int = 900) -> None:
        super().__init__(workspace)
        self.libero_python = libero_python
        self.default_timeout = default_timeout

    def run(self, script: str = "episode.py", timeout_s: int | None = None) -> ToolResult:
        if script != "episode.py":
            return ToolResult.err(
                f"run_episode 只接受 'episode.py'（白名单）；探针脚本请用 bash，"
                f"收到的是 {script!r}"
            )
        if not self.libero_python:
            return ToolResult.err("FAEA_LIBERO_PYTHON 未设置，无法运行仿真脚本")
        script_path = Path(self.ws.root) / "episode.py"
        if not script_path.exists():
            return ToolResult.err("episode.py 不存在——先写好它再运行")

        try:
            limit = int(timeout_s) if timeout_s else self.default_timeout
        except (TypeError, ValueError):
            limit = self.default_timeout
        limit = max(1, min(limit, 3600))

        # 隔离陈旧 meta.json（防上次运行的 success 误读为本次结果）
        meta_path = Path(self.ws.root) / "meta.json"
        stale = Path(self.ws.root) / "meta.stale.json"
        had_stale_meta = False
        if meta_path.exists():
            try:
                os.replace(meta_path, stale)
                had_stale_meta = True
            except OSError:
                meta_path.unlink(missing_ok=True)

        before = set(_list_diags(self.ws))
        try:
            proc = subprocess.Popen(
                [self.libero_python, "episode.py"],
                cwd=str(self.ws.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.err(f"无法启动解释器: {type(exc).__name__}: {exc}")
        try:
            stdout, stderr = proc.communicate(timeout=limit)
            exit_code = proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:  # noqa: BLE001
                proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=30)
            except Exception:  # noqa: BLE001
                stdout, stderr = "", ""
            exit_code = -9
            timed_out = True

        new_diags = [
            (idx, path)
            for idx, path in sorted(_list_diags(self.ws).items())
            if idx not in before
        ]
        parts: list[str] = []
        if timed_out:
            parts.append(f"TIMEOUT after {limit}s (exit -9)")
        parts.append(f"exit_code={exit_code}")
        if had_stale_meta:
            parts.append("(上一次运行的旧 meta.json 已隔离为 meta.stale.json)")
        if stdout:
            parts.append("--- stdout (tail) ---\n" + stdout[-STDOUT_TAIL:])
        if stderr:
            parts.append("--- stderr (tail) ---\n" + stderr[-STDERR_TAIL:])
        for idx, path in new_diags:
            try:
                parts.append(
                    f"--- diagnostics_try_{idx:03d}.json (auto-generated) ---\n"
                    + path.read_text()
                )
            except OSError as exc:
                parts.append(f"--- diagnostics_try_{idx:03d}.json 读取失败: {exc} ---")
        meta = None
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                parts.append(f"--- meta.json ---\n{json.dumps(meta, ensure_ascii=False)}")
            except Exception:  # noqa: BLE001
                meta = None
        if not new_diags:
            parts.append(
                "(no new diagnostics file was generated — if the script ran a try, "
                "it may have crashed before any step; check stderr)"
            )
        return ToolResult.ok("\n".join(parts))


class ReadDiagnosticsTool(BaseTool):
    name = "read_diagnostics"
    description = (
        "Read a per-try diagnostics JSON produced by the simulation wrapper "
        "(diagnostics/diagnostics_try_NNN.json), verifying its sha256 against the "
        "out-of-workspace index. Default: the newest try. Returns the full JSON."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "try_index": {
                "type": "integer",
                "description": "the try number to read; default = newest",
            },
        },
        "required": [],
    }
    risk_level = RiskLevel.READ

    def __init__(self, workspace: Any, *, diag_index_path: str = "") -> None:
        super().__init__(workspace)
        self.diag_index_path = diag_index_path

    def run(self, try_index: int | None = None) -> ToolResult:
        diags = _list_diags(self.ws)
        if not diags:
            return ToolResult.err(
                "diagnostics/ 目录为空或不存在：还没有任何一次完整结束的 try。"
                "先运行一次 episode.py（用 run_episode）。"
            )
        if try_index is None:
            idx = max(diags)
        else:
            idx = int(try_index)
            if idx not in diags:
                return ToolResult.err(
                    f"try {idx} 的诊断不存在；现有的 try: {sorted(diags)[-10:]}"
                )
        path = diags[idx]
        digest = _sha256(path)
        # 完整性校验（沙箱外索引）。索引缺失/不可读时降级为"返回但不背书"。
        verified: str | None = None
        if self.diag_index_path:
            try:
                index = json.loads(Path(self.diag_index_path).read_text())
                expected = index.get(str(idx)) or index.get(idx)
                verified = "ok" if expected == digest else (
                    f"MISMATCH (expected {str(expected)[:12]}, got {digest[:12]}) "
                    "—— 该诊断可能被篡改，不要相信它的内容，并改用刚运行的输出"
                )
            except Exception:  # noqa: BLE001
                verified = None
        try:
            data = json.loads(path.read_text())
        except Exception as exc:  # noqa: BLE001
            return ToolResult.err(f"诊断文件损坏（JSON 解析失败: {exc}）——"
                                  "记为 diagnostics_missing，继续用其它证据")
        head = f"diagnostics_try_{idx:03d}.json sha256={digest[:16]}"
        if verified is not None:
            head += f" integrity={verified}"
        return ToolResult.ok(head + "\n" + json.dumps(data, ensure_ascii=False, indent=1))
