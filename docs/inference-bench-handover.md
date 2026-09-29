# inference-bench 执行交接 · 2026-09-30 00:10

状态：**环境搭建进行中被叫停（额度将尽），基准尚未开跑，bench 脚本尚未落盘**。
契约：`docs/inference-bench-spec.md`（v1.0-draft1，数字禁止预填）。
本文是执行现场快照，接手者按 §6 的顺序继续即可。

---

## 1. 已完成的侦察与决策（这些结论不用重查）

### 环境（已实测确认）
- GPU：RTX 3050 Laptop 4GB，空闲，驱动 CUDA 13.3（WDDM）。
- WSL2：Ubuntu 在跑（NAT 模式，有 localhost 转发警告但不影响），WSL 内 `nvidia-smi -L` **可见 GPU**（CUDA on WSL 可用），`/` 盘余 927G。
- WSL 内工具：Python 3.10.6 / 3.9，`uv` 在 `~/.local/bin/uv`。
- Windows：winget 可用；Ollama / llama.cpp / vLLM **原本都没装**。
- 网络：github.com 直连 200；hf-mirror.com 200；raw.githubusercontent **不通**。

### 网络坑（接手必读）
1. **git 配置了 127.0.0.1 代理但代理没开**：克隆要 `git -c http.proxy= -c https.proxy= clone …`。
2. **GitHub releases 下载直连会 SSL 失败（curl exit 35）**：llama.cpp 下载脚本里带了镜像回退链（ghfast.top / gh-proxy.com），照抄即可。
3. **hf-mirror 慢且可能停滞**：1.5B GGUF 下载疑似卡住（见 §2），断点续传 `-C -` 后重跑。
4. **winget 装 Ollama 必须加 `--source winget`**（默认多源歧义，静默退出 0 但没装）。

### 项目定位
- MCC：本仓库。一次性任务 `python main.py -t "…" --mode auto|readonly`；**真实环境变量优先于 .env**（`src/miniclaude/config.py::from_env`）→ 逐引擎覆盖用 `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL` 三个环境变量注入即可，不用改 .env。
- **Insight Agent：本地 `/d/insight-agent` 是空 stub（无 CLI），真项目已克隆到 `/d/insight-agent-full`**（GitHub `magickkdd/insight-agent`，Windows 端跑）。对接点已核实：
  - CLI：`uv run insight-agent research "主题" --depth fast --json`（`src/insight_agent/cli.py`）。
  - LLM 工厂：`graph/llm_factory.py`，`LLM_PROFILE=local` 时走 `LOCAL_BASE_URL` + `LOCAL_MODEL`（`config.py:62-64`，默认 `http://localhost:11434/v1` / `qwen3:8b`）。
  - Langfuse：key 未配置则 handler 不挂载 —— 预期行为，compat 里记 `langfuse_mounted=false(未配置)`。
  - 搜索会走 Tavily→DuckDuckGo 降级；TAVILY_API_KEY 可给 dummy 让它走 ddgs 兜底（这正是它"降级哲学"的实测机会）。
- 简历：`/d/Embodied Agent/resume-site/index.html`。要改两处：`id:"mcc"` 条目（技术栈行与「记忆与生态扩展」条）与 `id:"insight"` 条目（约 647 行起的 note 技术栈行），升级为「Ollama / llama.cpp / vLLM 三引擎实测与吞吐对比」。
- 证据笔：`scripts/_evidence.py::write_evidence(result, payload, metrics, exact=, note=, sort_keys=)`，证据落 `eval/results/`。**metrics 只比数值且只放"协议规模"（attempt 数），不放时延/成功率**（重跑变快会被误判变薄而拒写）。写 JSON 用 `write_bytes` + 结尾 `\n`（Windows 文本模式会把 \n 变 \r\n 破坏指纹）。

## 2. 进行中的后台任务（会话结束后可能已断，接手先逐条检查）

| 任务 | 判定完成的方式 | 若失败 |
|---|---|---|
| Ollama winget 安装 | `ls "/c/Users/czx/AppData/Local/Programs/Ollama/ollama.exe"`；没有就重跑 `winget install --id Ollama.Ollama -e --source winget --silent` | 直下 `https://ollama.com/download/OllamaSetup.exe` |
| llama.cpp b11256 CUDA 12.4 x64 | `/d/inference-bench/llama.cpp/llama-server.exe --version` | zip 在该目录（llama-bin.zip 已下到 170MB）；用 §1 的镜像回退链重下 + `unzip`，cudart 同理 |
| WSL vLLM（`~/vllm-env`，uv + 清华 PyPI） | `wsl -d Ubuntu -e bash -lc '~/vllm-env/bin/python -c "import vllm"'`；site-packages 里还没有 vllm/torch，**大概率没装完** | 重跑：`wsl -d Ubuntu -e bash -lc 'source ~/vllm-env/bin/activate && UV_HTTP_TIMEOUT=600 uv pip install vllm --index-url https://pypi.tuna.tsinghua.edu.cn/simple'` |
| GGUF 1.5B q4_k_m（~986MB） | `/d/inference-bench/models/qwen2.5-1.5b-instruct-q4_k_m.gguf` ≥ 950MB | **当前只有 2.4MB，疑似停滞**。`curl -fSL -C - -o … https://hf-mirror.com/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf` |
| GGUF 7B q4_k_m（2 个分卷，~4.7GB） | 两个分卷都 ≥ 3.8G/0.9G | part1 已 671MB；同 URL 前缀 `Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m-0000{i}-of-00002.gguf`，`-C -` 续传 |

模型文件名已用 hf-mirror API 核实过（上面 URL 不会 404）。还缺：**AWQ**（`HF_ENDPOINT=https://hf-mirror.com` + vLLM 装好后用其 venv 的 `huggingface-cli download Qwen/Qwen2.5-1.5B-Instruct-AWQ`，或直接 curl 该 repo 的 safetensors/config/tokenizer 等文件）；**Qwen tokenizer.json**（客户端计 token 用，`https://hf-mirror.com/Qwen/Qwen2.5-1.5B-Instruct/resolve/main/tokenizer.json`，~11MB，配 `pip install tokenizers`）。

Ollama 装好后：`ollama pull qwen2.5:1.5b-instruct-q4_K_M`（registry 直连，若慢可考虑环境变量镜像，先直连试）。

## 3. bench_engines.py 的设计（已定稿，直接照此写）

落点 `scripts/bench_engines.py`，单文件，无三方依赖除 `httpx`（MCC 环境已有）+ `tokenizers`（需 pip 装）。

- **ENGINE 预设**（§3 spec 原文）：
  - `ollama`: `http://localhost:11434/v1`，model `qwen2.5:1.5b-instruct-q4_K_M`
  - `llamacpp`: `http://localhost:8080/v1`，model `qwen2.5-1.5b-instruct-q4_k_m`（`llama-server -m <gguf> -c 4096 -ngl 999`，1.5B 权重 1GB 全进卡；spec 命令里的 `-ngl 20` 是给 7B 参考行的）
  - `vllm`: `http://localhost:8000/v1`，model `Qwen/Qwen2.5-1.5B-Instruct-AWQ`（WSL 内启动，命令见 spec；OOM 降级序列 `--gpu-memory-utilization 0.8` → `--max-model-len 2048` → `--swap-space 0`）
  - `llamacpp-7b`: 同 llamacpp 端点但换 7B GGUF + `-ngl 20`，`tier="appendix"`，只跑 conc=1、repeats=2
- **Prompt 集 12 条硬编码**：6 短（50–100 token，从 MCC 任务指令风格改写，中英混合：calculator 需求改写 / 只读问答 / pytest 参数化用例 / temperature=0 解释 / 系统提示压缩）；6 长（1–2k token）：问题 + 硬编码 fixture 源码拼接（素材已选定：`demos/fixtures/bug-hunt/` 全套 4 模块 + tests + README，`eval/fixtures/slug-cli/` core+cli，`eval/fixtures/taxed-base/` pricing——源码已在会话中读过，写脚本时再 `cat` 一次贴进去）。每条带稳定 id（short-01…long-06），同一段喂所有引擎。
- **协议**：`POST {base}/chat/completions`，`stream=true` + `stream_options.include_usage=true`；若 400（老版 Ollama 不认 stream_options）→ 重试去掉该字段并记 `usage=null`（这正是 §4 说的 Ollama usage 粒度坑，是兼容性数据不是 bug）。`max_tokens=256`、`temperature=0`。
- **客户端计量（为准）**：TTFT = 首 content delta 时刻−请求发出；`gen_ms = 末 chunk − 首 content chunk`；completion_tokens 用 **Qwen tokenizer 数完整拼接文本**（禁止逐 delta 数）；prompt_tokens 同法。服务端 `usage` 与客户端比，`|Δ|/client > 5%` 记 `flag`。
- **成功判据**：HTTP 200 + 正常收流 + `finish_reason ∈ {stop,length}`。
- **并发**：`ThreadPoolExecutor(conc)`，conc∈{1,4}，每组 12×repeats(10)=120 请求。请求级 timeout 300s（7B 参考行 CPU 卸载慢）。
- **落盘**：原始 JSONL `eval/results/inference-bench/{engine}.c{conc}.jsonl`（一条一请求，失败也记 → attempt 数恒等于协议规模）；汇总 `eval/results/inference-bench.json` 走 `write_evidence`，metrics 只放 `{engine}:{conc}` 的 attempt 计数；payload 里带协议指纹（prompt 集文本 sha256、tokenizer 文件 sha256、repeats、max_tokens、温度、引擎命令原文）。stdout 打 markdown 汇总表（引擎×并发：TTFT p50/p95、吞吐 p50、成功率、usage 偏差率）。
- **`--compat <engine>` 模式**（同一脚本）：
  - MCC①计算器：在临时目录 `python main.py -t "<最小计算器任务>" --mode auto --max-turns 8`，判据=退出码（spec §5 原文），附结果文本核对。
  - MCC②readonly-qa：在 MCC 仓库 `python main.py -t "…" --readonly --max-turns 6`，判据=退出 0 + **工作区文件哈希前后不变**（排除 `.mcc/.traces/__pycache__`）+ 答案含预期关键词。两例都解析 `.traces/session.jsonl` 记录：请求是否带 tools、tool_calls 是否被解析执行、多轮链、`is_error` 出现形态。
  - InsightAgent：cwd=`/d/insight-agent-full`，env `LLM_PROFILE=local / LOCAL_BASE_URL=<endpoint> / LOCAL_MODEL=<model> / TAVILY_API_KEY=local-bench-dummy / LLM_* 填 dummy`，`uv run insight-agent research "<主题>" --depth fast --json`；判据=退出 0 + JSON 含 report/verification/metrics 键。1.5B 当 planner/writer 质量下降**如实记录不作判据**（spec §5）。
  - 证据：`eval/results/inference-bench-compat.json`（同样走 `write_evidence`），原始 stdout/stderr 尾巴存 `eval/results/inference-bench/compat/`。
- 控制台输出统一 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`（GBK 控制台会炸中文）。

## 4. 服务起停（脚本外运维，写进 as-built）

- Ollama：装完即服务（11434）。`ollama pull qwen2.5:1.5b-instruct-q4_K_M`
- llama.cpp：`/d/inference-bench/llama.cpp/llama-server.exe -m /d/inference-bench/models/qwen2.5-1.5b-instruct-q4_k_m.gguf -c 4096 -ngl 999 --port 8080`（7B 行换 `-ngl 20`）
- vLLM：`wsl -d Ubuntu -e bash -lc 'source ~/vllm-env/bin/activate && vllm serve Qwen/Qwen2.5-1.5B-Instruct-AWQ --max-model-len 4096 --gpu-memory-utilization 0.85 --enforce-eager'`（AWQ 需先下到 WSL 可见处；HF 缓存建议放 WSL ext4。模型经 `/mnt/d/...` 也能读，慢一点）
- **显存互斥：三引擎不同时起**，跑完一个关一个。

## 5. 交付物清单（spec §6）与状态

- [ ] `scripts/bench_engines.py` — 设计已定稿（§3），未写
- [ ] `docs/inference-bench.md` — 未写；必须含：主对比表、D-1 外推限制声明（**结论只对 1.5B 有效**）、兼容矩阵、vLLM OOM/失败形态、7B 附表（D-2）、`enforce-eager` 吞吐损失声明
- [ ] MCC README 推理段 +1 节（挂 §4.14 之后、§5 之前的编号顺延）；Insight Agent README 联动 1 行（`/d/insight-agent-full/README.md` LLM 工厂段落）
- [ ] MCC `.env.example` 注释：三引擎 base_url 示例
- [ ] 简历两处（§1 路径已给）
- 实测数字全部来自 JSONL/汇总 JSON，**禁止手填预估**

## 6. 接手后的执行顺序

1. 检查 §2 五项 → 补齐：Ollama / llama.cpp / vLLM / 三个 GGUF / AWQ / tokenizer.json + `pip install tokenizers`（MCC 的 python）
2. 写 `scripts/bench_engines.py`（§3 照抄）→ `--check` 各引擎健康
3. 逐引擎起服务 → `--engine ollama --conc 1,4` → 换下一个（显存互斥）
4. `--compat` 三轮（MCC 计算器 / readonly-qa / InsightAgent research，逐引擎）
5. 7B 参考行（appendix，conc=1 repeats=2）
6. 汇总表 → `docs/inference-bench.md`（全部数字从 JSONL 现算）→ README×2 + .env.example + 简历
7. git 提交（注意：spec 与本交接文档当前均未入库）

## 7. 本次会话已消耗的试错（别重复）

- git 克隆 / winget / GitHub releases 下载的三种网络坑各踩过一次（解法见 §1）。
- `write_evidence` 的 metrics 语义约束（只比协议规模）已推演，别把时延放进去。
- `/d/insight-agent` stub 别删别改（untracked），真克隆在 `/d/insight-agent-full`。
