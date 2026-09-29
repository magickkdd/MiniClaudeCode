# inference-bench-spec · 同一模型跨三引擎（Ollama / llama.cpp / vLLM）推理实测

版本：v1.0-draft1 · 日期：2026-09-29 · 状态：**待执行（本文是计划契约，所有实测数字留空，禁止预填）**
预算：6–8 净工时（半天–1 天）· 对齐 JD：晶远芯「本地安装大模型推理平台 Ollama, vLLM, llam.cpp」三引擎点名
关系：本文只写增量。执行环境硬约束与 SPEC-v2 §0.4 的端点结论（scripts/probe_caps.py / probe_window.py）承接；证据写盘走 `scripts/_evidence.py` 一支笔（§7.3-7 条款继续有效）。

---

## 0. 一句话定位

**不是性能跑分竞赛，是"同一模型、三种推理引擎"的功能兼容性与吞吐实测**：判据来自客户端计量与服务端 usage 字段交叉核对，不采用模型自述；结论直接回答"我的两个 Agent 项目在哪些本地引擎上能跑、跑得多快"。

## 1. 环境与硬约束

| 项 | 值 | 后果 |
|---|---|---|
| GPU | RTX 3050 Laptop 4GB | 7B fp16（~15GB）不可行；AWQ 7B 权重即占满 4G、无 KV cache 余量 → 见 D-1 |
| OS | Windows 11 | vLLM 无原生 Windows 支持 → WSL2（CUDA on WSL）或 Docker；Ollama / llama.cpp 原生 |
| 磁盘 | GGUF ~1GB/个、AWQ ~1.2GB | 模型下载走 `HF_ENDPOINT=https://hf-mirror.com` |

## 2. 决策记录

- **D-1 模型选 Qwen2.5-1.5B-Instruct（而非 7B）**：4G 显存约束下的唯一可行档。代价：**结论只对 1.5B 级别有效，不得外推到 7B**——as-built 文档必须带此限制声明。
- **D-2 llama.cpp 允许加一行 7B Q4_K_M 部分卸载（`-ngl 20`）参考行**：慢是预期，只进附表不进主对比表——展示"4G 卡跑 7B 的真实可行路径"（JD 三引擎之一的功能性证据）。
- **D-3 temperature=0**：吞吐测量去噪；两种 prompt 长度分开统计。
- **D-4 并发只测 1 与 4**：单卡笔记本场景的诚实边界，不假装测服务端并发。

## 3. 三引擎配置（全部暴露 OpenAI 兼容 /v1/chat/completions）

| 引擎 | 模型 | 启动命令 | 端点 |
|---|---|---|---|
| Ollama（基线） | `qwen2.5:1.5b-instruct-q4_K_M` | `ollama run qwen2.5:1.5b-instruct-q4_K_M` | `http://localhost:11434/v1` |
| llama.cpp | 同款 GGUF | `llama-server.exe -m qwen2.5-1.5b-instruct-q4_k_m.gguf -c 4096 -ngl 20` | `http://localhost:8080/v1` |
| vLLM（WSL2） | `Qwen/Qwen2.5-1.5B-Instruct-AWQ` | `vllm serve Qwen/Qwen2.5-1.5B-Instruct-AWQ --max-model-len 4096 --gpu-memory-utilization 0.85 --enforce-eager` | `http://localhost:8000/v1` |

vLLM OOM 降级序列（依次尝试）：`--gpu-memory-utilization 0.8` → `--max-model-len 2048` → 加 `--swap-space 0`；全败则如实记录失败形态（这本身是兼容性数据）。

## 4. 工作负载协议

- **prompt 集 12 条**：6 短（≈50–100 输入 token，取自 MCC demo 任务指令）+ 6 长（≈1–2k 输入 token，拼接 bug-hunt fixture 源码片段），中英混合，固定写死在脚本里（同一段 prompt 喂所有引擎，禁止各引擎用不同 prompt）。
- **每组**：12 prompts × 10 次生成，`max_tokens=256`，`temperature=0`；并发=1 与并发=4 两轮。
- **指标**：TTFT（首 token 延迟）、生成吞吐（completion tokens / 生成秒数）、成功率（HTTP 200 且完整返回）。
- **判据**：客户端计量为准，服务端 `usage` 字段交叉核对，偏差 >5% 的引擎-样本如实记录（Ollama 的 usage 粒度差异是已知坑，正好是兼容性数据）。

## 5. 兼容性矩阵（第二判据，与吞吐同表呈现）

| 验证项 | MCC（build_session 装配） | Insight Agent |
|---|---|---|
| 每引擎动作 | ① 最小 `--task` 计算器任务（A2 缩减版，退出码判据）② readonly-qa 只读问答（工作区哈希不变判据） | `research "…" --depth fast --json` 单次研究（chat 兼容 + usage 回调） |
| 检查点 | tool_calls 报文形状、多轮工具链完整性、`is_error` 语义、流式/非流式差异 | JSON 输出完整性、LLM 工厂 local 档对接、Langfuse trace 是否正常挂载 |
| 已知风险 | vLLM 的 tools 字段差异、llama.cpp 的 tools 支持开关 | 本地 1.5B 模型做 planner/writer 的质量下降（如实记录，不作合格判据） |

兼容性失败**本身就是交付物**：逐引擎记录失败形态与绕过方式（这正是"本地安装大模型推理平台"岗位能力的实证材料）。

## 6. 交付物

- [ ] `scripts/bench_engines.py`：prompt 集内置、三引擎驱动、JSONL 落盘、汇总表生成；证据写盘经 `scripts/_evidence.py`
- [ ] `docs/inference-bench.md`：as-built 表（吞吐/TTFT/成功率/兼容矩阵 + D-1 外推限制声明 + 失败形态记录）
- [ ] MCC README 推理段 +1 节（三引擎实测结论）；Insight Agent README 联动 1 行
- [ ] `.env.example` 注释更新（三引擎 base_url 示例）
- [ ] 简历联动：技术栈行与「记忆与生态扩展」条升级为「Ollama / llama.cpp / vLLM 三引擎实测与吞吐对比」

## 7. 非目标

不做 7B fp16 主对比（D-1）；不做张量并行/分布式；不调 CUDA kernel；不用云 API 计费跑分；不做多模态模型；不给 vLLM 调优吞吐（enforce-eager 关闭 CUDA graph 是 4G 下的必要妥协，如实记录其吞吐损失）。

## 8. 风险清单

| 风险 | 缓解 / 记录义务 |
|---|---|
| vLLM 在 4G 上 OOM | §3 降级序列；全败则记录失败形态，vLLM 行标记"功能性验证未完成" |
| WSL2 CUDA 不可用 | Docker `--gpus all` 备选；再失败则 vLLM 折线，如实记录 |
| AWQ/GGUF 下载失败 | `HF_ENDPOINT` 镜像；ModelScope 备选 |
| CPU 混合推理吞吐过低 | 预期内，如实记录（llama.cpp 7B 参考行本就是"能跑"证据而非性能证据） |
| 各引擎 usage 字段粒度不一 | §4 交叉核对条款：偏差 >5% 记录，不强行对齐 |
