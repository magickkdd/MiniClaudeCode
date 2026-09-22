"""端点能力探测：SPEC v2 的可行性判断建立在这份实测结果上，而不是假设上。

只打印能力布尔值与字段名，永不打印密钥或 Authorization 头。

用法：python scripts/probe_caps.py
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import httpx

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from miniclaude.config import Config  # noqa: E402

# 一段 ~1200 token 的固定前缀，用来测 prompt caching 是否会命中。
LONG_SYSTEM = (
    "你是仓库索引服务。以下是若干文件的签名清单，请原样保留以便后续对话引用：\n"
    + "\n".join(
        f"def helper_{i}(a: int, b: str) -> tuple[int, str]: ...  # 处理第 {i} 组边界条件与回退路径"
        for i in range(60)
    )
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件",
            "strict": True,  # 探针：是否支持结构化输出约束
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "enum": ["a.py", "b.py"]},
                    "offset": {"type": "integer"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    }
]


def call(cfg: Config, payload: dict[str, Any], *, stream: bool = False) -> tuple[int, dict[str, Any], float]:
    body = {"model": cfg.model, **payload}
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    started = time.perf_counter()
    with httpx.Client(timeout=cfg.request_timeout) as client:
        if not stream:
            r = client.post(f"{cfg.base_url}/chat/completions", json=body, headers=headers)
            data = r.json() if r.status_code < 400 else {"_err": str(data_safe(r.text))}
            return r.status_code, data, time.perf_counter() - started
        chunks: list[dict[str, Any]] = []
        with client.stream(
            "POST", f"{cfg.base_url}/chat/completions", json=body, headers=headers
        ) as r:
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    chunks.append({"_sentinel": True})
                    continue
                try:
                    chunks.append(json.loads(raw))
                except json.JSONDecodeError:
                    chunks.append({"_bad": raw[:40]})
        return r.status_code, aggregate(chunks), time.perf_counter() - started


def data_safe(text: str) -> str:
    return text[:180].replace("\n", " ")


def aggregate(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """把 SSE 分片还原成一次响应的形状，只看结构不看内容。"""
    out: dict[str, Any] = {
        "chunks": len(chunks),
        "saw_done": any("_sentinel" in c for c in chunks),
        "bad_chunks": [c for c in chunks if "_bad" in c][:1],
        "usage_present": any(c.get("usage") for c in chunks),
        "tool_call_deltas": 0,
        "role_deltas": 0,
    }
    for c in chunks:
        for ch in c.get("choices") or []:
            d = ch.get("delta") or {}
            if d.get("tool_calls"):
                out["tool_call_deltas"] += 1
            if "role" in d:
                out["role_deltas"] += 1
        if c.get("usage"):
            out["usage_keys"] = sorted(c["usage"].keys())
    return out


def main() -> int:
    cfg = Config.from_env()
    report: dict[str, Any] = {"endpoint_kind": cfg.base_url.rsplit("/", 1)[-1], "model": cfg.model}

    # 1) 基础报文里 usage 到底有哪些字段（决定成本与缓存指标能不能算）
    sc, data, dt = call(cfg, {"messages": [{"role": "user", "content": "只回答 OK"}], "max_tokens": 8})
    usage = (data.get("usage") or {}) if sc < 400 else {}
    report["plain"] = {
        "status": sc,
        "usage_keys": sorted(usage.keys()),
        "usage_details": {k: v for k, v in usage.items() if isinstance(v, dict)},
        "response_keys": sorted(data.keys()) if sc < 400 else data.get("_err"),
        "latency_s": round(dt, 2),
    }

    # 2) 流式
    sc, agg, dt = call(
        cfg,
        {"messages": [{"role": "user", "content": "用三个字描述Python"}], "max_tokens": 24, "stream": True,
         "stream_options": {"include_usage": True}},
        stream=True,
    )
    report["stream"] = {"status": sc, **{k: v for k, v in agg.items() if k != "bad_chunks"},
                        "bad_chunks": agg["bad_chunks"], "latency_s": round(dt, 2)}

    # 3) /responses（Responses API）是否存在
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                f"{cfg.base_url}/responses",
                json={"model": cfg.model, "input": "只回答 OK"},
                headers={"Authorization": f"Bearer {cfg.api_key}"},
            )
            report["responses_api"] = {"status": r.status_code, "body": data_safe(r.text)[:120]}
    except Exception as exc:  # noqa: BLE001 - 探测脚本，任何异常都是结果的一部分
        report["responses_api"] = {"error": type(exc).__name__}

    # 4) 工具 schema 里的 strict + enum 会不会被拒
    sc, data, dt = call(
        cfg,
        {"messages": [{"role": "user", "content": "读 a.py"}], "tools": TOOLS, "max_tokens": 32},
    )
    tc = ((data.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
    report["tools_strict_enum"] = {
        "status": sc,
        "tool_calls": len(tc),
        "arguments_type": type(tc[0]["function"]["arguments"]).__name__ if tc else None,
        "err": None if sc < 400 else data.get("_err"),
    }

    # 5) parallel_tool_calls 显式参数
    sc, data, _ = call(
        cfg,
        {"messages": [{"role": "user", "content": "同时读 a.py 和 b.py，一次调用两个工具"}],
         "tools": TOOLS, "max_tokens": 128, "parallel_tool_calls": True},
    )
    tc = ((data.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
    report["parallel_tool_calls_param"] = {"status": sc, "tool_calls": len(tc)}

    # 6) max_tokens 上限（高值是否被裁剪/拒绝）——用一个必然早停的请求避免真实生成
    sc, data, _ = call(
        cfg, {"messages": [{"role": "user", "content": "只回答 OK"}], "max_tokens": 32000}
    )
    report["max_tokens_32000"] = {"status": sc, "err": None if sc < 400 else data.get("_err")}

    # 7) prompt caching：同一长前缀连打两次，看第二次的 cached_tokens
    msgs = [{"role": "system", "content": LONG_SYSTEM},
            {"role": "user", "content": "第 7 个函数叫什么？"}]
    first = call(cfg, {"messages": msgs, "max_tokens": 16})[1].get("usage") or {}
    time.sleep(1)
    second = call(cfg, {"messages": msgs, "max_tokens": 16})[1].get("usage") or {}
    report["prompt_cache"] = {
        "first": first, "second": second,
        "second_prompt_tokens_details": second.get("prompt_tokens_details"),
    }

    # 8) temperature=0 的可复现性（评测判据的地基）
    outs = []
    for _ in range(3):
        _, d, _t = call(cfg, {"messages": [{"role": "user", "content": "1到10里随机选一个整数，只输出数字"}],
                              "temperature": 0.0, "max_tokens": 8})
        outs.append(((d.get("choices") or [{}])[0].get("message") or {}).get("content"))
    report["temp0_repeat"] = {"outputs": outs, "all_same": len(set(map(str, outs))) == 1}

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
