"""run_tests —— self-debugging 的燃料。

为什么不直接让模型用 bash 跑 pytest：原始 stdout 又长又乱，模型从第
几轮开始就会误判"到底还有几个失败"。把结果压成
`通过/失败计数 + 失败用例名 + 精简 traceback`，才能支撑
"失败 → 分析 → 修改 → 重跑"的闭环，也让评测能用退出码客观判定成功。

is_error 语义与 bash 一致：测试失败不是工具失败（见 bash.py 顶部说明）。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from typing import Any

from miniclaude.tools.base import BaseTool, RiskLevel, ToolResult

# pytest 的计数出现在同一行里（"2 failed, 10 passed in 1.2s"），所以不能用行首锚定。
_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|errors?|error|skipped|xfailed|xfail)\b")
_COUNT_KEYS = {"error": "errors", "errors": "errors", "xfail": "xfailed"}
_NODEID_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


class RunTestsTool(BaseTool):
    name = "run_tests"
    description = (
        "运行 pytest 测试并返回结构化结果：通过/失败计数、失败用例名、精简 traceback。"
        "改完代码后必须用它验证，并**以这里的退出码为准**判断是否成功，不要凭修改内容臆测结果。"
        "target 可以留空（跑整个仓库）、指向文件、或精确到 `tests/a.py::test_b`。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "测试路径或用例 id，留空表示全量"},
            "extra_args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "附加 pytest 参数，如 ['-k', 'parser']",
            },
            "timeout": {"type": "integer", "description": "秒，默认 120"},
        },
    }
    risk_level = RiskLevel.EXECUTE

    def run(
        self,
        *,
        target: str = "",
        extra_args: list[str] | None = None,
        timeout: int = 120,
    ) -> ToolResult:
        probe = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pytest", "--version"],
            cwd=str(self.ws.root), capture_output=True, text=True, timeout=30, check=False,
        )
        if probe.returncode != 0:
            return ToolResult.err(
                "这个环境里跑不了 pytest。请先用 bash 执行 "
                f"`{sys.executable} -m pip install pytest`，再重试。"
            )

        argv = [sys.executable, "-m", "pytest", "-q", "--no-header", "-rf", "--tb=short"]
        if target.strip():
            argv.append(target.strip())
        for item in extra_args or []:
            argv.append(str(item))

        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
        started = time.perf_counter()
        try:
            completed = subprocess.run(  # noqa: S603
                argv, cwd=str(self.ws.root), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=max(5, min(int(timeout), 600)),
                env=env, check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.err("测试执行超时被终止。用 target 缩小范围，或加 -k 只跑相关用例。")
        except OSError as exc:
            return ToolResult.err(f"无法启动测试进程：{type(exc).__name__}: {exc}")

        elapsed = time.perf_counter() - started
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        return ToolResult.ok(self._format(completed.returncode, output, elapsed, target))

    def _format(self, code: int, output: str, elapsed: float, target: str) -> str:
        counts: dict[str, int] = {}
        for num, raw_name in _SUMMARY_RE.findall(output):
            key = _COUNT_KEYS.get(raw_name, raw_name)
            counts[key] = max(counts.get(key, 0), int(num))  # 同一行重复出现时取最大值
        passed = counts.get("passed", 0)
        failed = counts.get("failed", 0) + counts.get("errors", 0)
        skipped = counts.get("skipped", 0)

        if code == 0:
            verdict = f"PASSED —— {passed} 个用例通过"
            if skipped:
                verdict += f"，{skipped} 个跳过"
        else:
            verdict = f"FAILED —— {failed} 个失败/错误，{passed} 个通过"

        failures = [nodeid for nodeid in _NODEID_RE.findall(output) if "::" in nodeid or nodeid.endswith(".py")]
        lines = [f"结果：{verdict}（退出码 {code}，{elapsed:.1f}s）", f"范围：{target or '整个工作区'}"]
        if failures:
            shown = failures[:20]
            lines.append("\n失败用例：\n" + "\n".join(f"- {item}" for item in shown))
            if len(failures) > len(shown):
                lines.append(f"- …另有 {len(failures) - len(shown)} 个")
        if code != 0 and output:
            # traceback 的有用部分总在尾部，所以按"保尾"截而不是首尾各半
            tail = output[-4000:]
            lines.append("\npytest 输出（末尾片段）：\n" + tail)
        elif code != 0:
            lines.append("\npytest 没有任何输出 —— 很可能是收集阶段就报错，检查 import 与语法。")
        return "\n".join(lines)
