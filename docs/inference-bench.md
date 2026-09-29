# inference-bench · 同一份权重跨三引擎（Ollama / llama.cpp / vLLM）实测 as-built

日期：2026-09-30 · 契约：`docs/inference-bench-spec.md` · 驱动：`scripts/bench_engines.py`
证据：`eval/results/inference-bench.json` + `eval/results/inference-bench-compat.json` +
`eval/results/inference-bench/**`（逐请求 JSONL 与九格原始 stdout/stderr）

**本文所有表格都由 `python scripts/bench_engines.py --report bench` / `--report compat` 从盘上证据现算，
没有一个数字是手填的。** 要改数字只能重跑那一组。

---

## 0. 一句话结论

单流吞吐：**llama.cpp 92.3 tok/s、Ollama 93.9 tok/s、vLLM 25.0 tok/s**（同一份 Qwen2.5-1.5B 权重、
同一批 12 条 prompt、`temperature=0`）。
并发 4 才是分水岭，而三个引擎的走向各不相同：**llama.cpp 因共享上下文把 60 条长 prompt 全打死、
Ollama 排队全过但 TTFT 涨 3.1 倍、vLLM 全过且 TTFT 几乎不动、墙钟吞吐 3.9 倍**。

**而"我的两个 Agent 项目能不能跑"不由吞吐决定**：MCC 的第一发请求固定开销 **5209 token**，
Insight 的 writer 节点要 **5527 token** —— 4096 上下文的本地引擎**结构性跑不了这两个项目**；
vLLM 还额外卡在另一件事上：MCC 一个字节都没发 `tool_choice`，而 vLLM 见到 `tools` 就按 `auto` 处理，
默认配置直接拒（§6）。

**D-1 外推限制（spec §2）**：本文全部结论**只对 1.5B 量级成立，不得外推到 7B**。
7B 只跑了 `llama.cpp -ngl 20` 的部分卸载参考行（§7 附表），vLLM / Ollama 的 7B 一律未测。

---

## 1. 跑测环境（as-installed，不是 as-planned）

| 引擎 | 版本 | 到位方式 | 与 spec §3 的差异 |
|---|---|---|---|
| Ollama | 0.34.4 | 官方 release 资产 `ollama-windows-amd64.zip`（1,461,155,106 B，SHA256 `535193f3…` = 官方 `sha256sum.txt`）解出 `ollama.exe` 直接 `serve`，`OLLAMA_MODELS` 指到 D 盘 | **没用 `OllamaSetup.exe`**：该 NSIS 包禁用了静默模式，`/S` 与 `/S /D=<dir>` 都只弹向导、不写文件（无 UAC 等待、无子进程、目标目录不出现），而本机非管理员 |
| llama.cpp | `0.5.0-dev (build 11256, commit c85b92c69)`，CUDA 12.4 x64 | `llama-server.exe -m <1.5B GGUF> -c 4096 -ngl 999 --port 8080` | 与 spec 一致（spec 命令里的 `-ngl 20` 是留给 §7 的 7B 参考行的） |
| vLLM | 0.30.0（torch 2.13.0+cu130，Python 3.10.21，WSL2 Ubuntu） | venv / `UV_CACHE_DIR` / `TMPDIR` **全在 `/mnt/d`**，`uv pip install --link-mode hardlink` | 显存参数比 spec 低一级（0.85 → 0.8），且必须显式指定预编译注意力后端，见 §5 |

**"装到 D 盘"本身就是环境结论。** C 盘只剩 13–14 GB（96% 满），而 WSL 的 `ext4.vhdx` 就落在 C 上。
想给 WSL 挂一块 D 上的 ext4 VHD 需要管理员（`wsl --mount` / diskpart 都要求提权，本机 `IsInRole(Administrator)=False`、
WSL 内 `sudo` 要密码）；新建独立发行版需要 `raw.githubusercontent.com` 上的发行版目录（本机拉不到）、
`cloud-images.ubuntu.com` 网络不通、TUNA 的 `ubuntu-cloud-images/wsl/noble/current/` 只镜像了 manifest 没有 rootfs。
于是落点是 **venv 在 DrvFs（9P）+ 硬链接安装**：实测 `drvfs` 支持 `CreateHardLink`，
所以 9.3 GiB 的 wheel 缓存与 7.9 GiB 的 venv **共享同一份磁盘块**，省掉一次 13 GiB 的二次复制；
删掉缓存之后 `import vllm` 复验仍通过。C 盘零增长。
**代价写在 §9：9P 上的 venv 与权重进了 vLLM 的启动路径，而本轮没有调优去把它剥离出来。**

权重与来源核验（全部对齐官方仓库清单里公布的 SHA256）：

| 文件 | 字节 | SHA256 | 结果 |
|---|---:|---|---|
| `qwen2.5-1.5b-instruct-q4_k_m.gguf` | 1,117,320,736 | `6a1a2eb6d156…` | **MATCH**（**混源**：前 601 MB 来自 hf-mirror、其余来自 ModelScope，最终仍与官方逐字节一致） |
| `Qwen2.5-1.5B-Instruct-AWQ/model.safetensors` | 1,614,553,840 | `5b29ed6f80e4…` | **MATCH** |
| 7B `q4_k_m` 两分卷 | 3,993,201,344 / 689,872,288 | `dfce12e3862a…` / — | **两卷均 MATCH** |
| 合并后 `…-merged.gguf` | 4,683,073,536 | GGUF v3 / 339 tensors | 魔数 + 张数校验通过 |
| `tokenizer.json`（客户端计量用） | 7,031,645 | `c0382117ea32…` | 写入协议指纹 |

**跨引擎的"同一模型"在本轮被收紧成"同一份权重"**：Ollama 侧不用 registry tag，而是
`ollama create qwen2.5:1.5b-instruct-q4_K_M -f Modelfile`（`FROM` 指向上表那份 GGUF），
于是 Ollama 的 blob sha 也是 `6a1a2eb6…`，与 llama.cpp 加载的文件逐字节相同。
省下 1.1 GB 下载只是副产品，真正的收益是比较里少了一个"两家量化不是同一个文件"的混杂变量。

---

## 2. 协议（as-run）

12 条 prompt 全部硬编码，同一段文本喂三个引擎。客户端 token 档位实测（`--check` 现算）：

| 档位 | 条数 | 实测输入 token（不含模板） | spec §4 要求 |
|---|---:|---|---|
| 短 | 6 | 58 / 62 / 63 / 63 / 68 / 69 | ≈50–100 ✅ |
| 长 | 6 | 1116 / 1116 / 1127 / 1140 / 1173 / 1264 | ≈1–2k ✅ |

长 prompt 的素材逐字节抄自 `demos/fixtures/bug-hunt/`、`eval/fixtures/slug-cli/`、`eval/fixtures/taxed-base/`；
`--verify-fixtures` 会把 22 段内嵌文本与盘上文件逐字节比对（当前 **22/22 一致**）。这条检查存在的理由是：
prompt 一改 `prompts_sha256`（`4fee4dbb…`）就变，跨引擎比较的同一性当场作废，
而人很容易在改别的代码时顺手碰到它。

每组 = 12 prompts × repeats（主表 10，附表 2）= 120 / 24 请求；`max_tokens=256`、`temperature=0`（D-3）；
并发 1 与 4（D-4）；请求级 timeout 300 s。

**指标定义（客户端计量为准）**：

- `TTFT` = 请求发出 → 第一个**非空 content delta**；
- `gen_ms` = 最后一个 chunk − 第一个 content chunk；`tps` = 客户端数的 completion token ÷ `gen_ms`；
  **逐 delta 数 token 是错的**（BPE 跨界合并会被数两遍，且三个引擎的分块边界各不相同）；
- `wall_tps` = 该组成功请求的 token ÷ 该组墙钟秒（含 prefill 与排队）——**并发下的真实交付率看这一列**；
- `template_overhead` = 服务端 `prompt_tokens` − 客户端数的 prompt 文本 token。
  把模板混进"客户端计量"就等于替引擎编一个数，所以它单独成一个可观测量。

**一条必须写进协议的测量条件：HTTP 客户端 `trust_env=False`。** 这台机器的代理写在注册表里
（`env` 里一个代理变量都没有），`httpx` 默认会经 `urllib.getproxies()` 取到它，于是**每一发
`127.0.0.1` 请求都过代理**：同一发本机 GET 经代理 **143 ms**、直连 **17 ms**，而闭合端口返回
**502** 而不是拒连。第一轮 llama.cpp 两组因此是带代理测的，改直连后整组重测（差值见 §8 第 1 条），
旧原始件归档在 `eval/results/inference-bench/superseded/`。

**判据侧两条纪律**：成功 = HTTP 200 **且**正常收流 **且** `finish_reason ∈ {stop,length}`
（只看 200 会把 llama.cpp 的流内 500 记成成功，这不是假设，那一组 56 发就是这个形状）；
失败请求照样占 JSONL 一行，`attempts` 恒等于协议规模，`write_evidence` 的守卫才有东西可比。

---

## 3. 主对比表

（`--report bench` 现算）

| 引擎 | 档 | 并发 | attempts | 成功率 | TTFT p50/p95 (ms) | 吞吐 p50/p95 (tok/s) | 墙钟吞吐 (tok/s) | usage 中位偏差 | 偏差>5% 样本 |
|---|---|---|---|---|---|---|---|---|---|
| llamacpp | main | c1 | 120 | 100% | 2087.7 / 2549.6 | 92.3 / 95.6 | 41.91 | 0.0043 | 60 |
| llamacpp | main | c4 | 120 | 53% | 2174.8 / 2898.9 | 72.9 / 96.2 | 66.93 | 0.0088 | 60 |
| ollama | main | c1 | 120 | 100% | 2085.0 / 2542.0 | 93.9 / 99.2 | 36.42 | 0.0043 | 60 |
| ollama | main | c4 | 120 | 100% | 6556.1 / 8468.8 | 95.4 / 109.9 | 91.55 | 0.0043 | 60 |
| vllm | main | c1 | 120 | 100% | 2141.9 / 2614.8 | 25.0 / 26.2 | 19.15 | 0.0046 | 60 |
| vllm | main | c4 | 120 | 100% | 2159.7 / 2197.1 | 24.5 / 25.2 | 74.65 | 0.0046 | 60 |

**四条读数：**

1. **单流上 llama.cpp 与 Ollama 等价**（92.3 vs 93.9 tok/s，TTFT 2088 vs 2085 ms）。两者加载的是
   **同一个文件**、模板开销同为 +29 token，所以这一行的意义是"包装层不同不影响生成本身"。
2. **vLLM 单流只有 25.0 tok/s（llama.cpp 的 27%），但这个数不能整笔归给引擎。** 至少四件事混在里面，
   本轮没拆：`--enforce-eager` 关掉 CUDA graph（spec §7 明写的必要妥协，且规定**不调优**）、
   AWQ Marlin 内核在小 batch 下的形状、**9P 文件系统上的权重与 venv**（vLLM 自己打了
   `Filesystem type for checkpoints: 9P … Auto-prefetch is disabled`）、以及 WSL2 的驱动路径。
   所以 25.0 的正确读法是"按 spec 给的命令在这台机器上能跑到 25.0"，**不是"vLLM 的上限"**。
3. **并发 4 把三种设计暴露成三个方向。**
   - llama.cpp：`-c 4096` 是 **4 个 slot 共享**同一份上下文（在线服务端 `/props` 自报
     `total_slots=4`），4×(≈1100 输入 + 256 输出) ≈ 5.4k > 4096 ⇒ **60 条长 prompt 里 56 条被打死、
     60 条短 prompt 全活**（这就是 53% 的来源）。
   - Ollama：单上下文排队 ⇒ 120/120 全过，代价是 TTFT 2085 → **6556 ms（3.1×）**。
   - vLLM：paged KV（实测 `Available KV cache memory: 1.76 GiB`、`GPU KV cache size: 65,808 tokens,
     Maximum concurrency for 4,096 tokens per request: 16.07x`）⇒ 全过、TTFT 几乎不动
     （2141.9 → 2159.7 ms）、墙钟吞吐 19.15 → **74.65（3.9×）**。
     **这是全表里唯一"并发真的买到东西"的一行**，即使它的单流最慢——服务引擎的本职就在这。
4. **`wall_tps` 与 `tps` 不同向，引用时别混。** c1 的墙钟（41.9 / 36.4 / 19.2）远低于单流 p50，
   差值是 prefill 与调度空隙。而 llamacpp:c4 的墙钟 66.93 看着不低，**部分是因为失败的 56 发
   几乎不产 token 就结束、把分母压小了**——那一行不能当并发加速比引用。

**每组的服务端原始计数**（用于自查，不含推断）：c1 三组分别产出 22,309 / 22,366 / 21,991 个
completion token，墙钟 532.3 / 614.1 / 1148.4 s；`finish_reason` 分布 llamacpp:c1 是 70 stop / 50 length，
ollama:c4 是 60 / 60，vllm:c4 是 69 / 51。

---

## 4. 交叉核对（spec §4 的第二判据）

| 量 | 实测 |
|---|---|
| `completion` 偏差中位 | 0.0043（llamacpp c1、ollama 两档）/ 0.0088（llamacpp c4）/ 0.0046（vllm 两档）/ 0.0058（7B）；**单样本最大 0.0357** |
| `prompt` 偏差中位 | 0.2232（除 llamacpp:c4 = 0.4603） |
| `template_overhead` | **三引擎 + 附表恒为 +29 token** |
| 偏差>5% 的样本 | 60/120，**全部落在短 prompt（实测 42–47%）**；长 prompt 只有 2.3–2.6% |
| `stream_options.include_usage` | **三引擎全部支持，0 次 400 回退** |

三件事要说白：

- **那 60 个被标记的样本不是错误，是同一条阈值在两个档位上的含义不同。** 短 prompt 客户端数
  58–69 token、服务端报 92（= 文本 + 29 模板 + 控制位），相对偏差自然 42–47%；长 prompt 客户端
  1116–1264，多出来的还是那 29，偏差只剩 2.3–2.6%。所以表里保留的是"被标记的样本数"，
  **不是"错误数"**。llamacpp:c4 的 prompt 偏差中位涨到 0.4603，是因为那一组**只有短样本活下来**。
- **spec §8 预言的"Ollama 的 usage 粒度差异是已知坑"本轮未观测到。** Ollama 0.34.4 认
  `stream_options.include_usage`，没有 400、没有缺字段。这条在本文里的正确写法是**未观测到**，
  不能拿旧结论冒充实测。
- **`completion` 侧才是"两把尺子是否同一把"的地方**：中位 0.43%、最大 3.57% —— 客户端 Qwen
  tokenizer 与服务端自报在输出一侧基本对得上。llamacpp:c4 的 `usage_support` 里多出一个
  `no-usage-in-stream`，那是 56 发流内失败的副产物（服务端没走到 usage 块），**不能记在 usage 粒度账上**。

---

## 5. vLLM 的启动序列（spec §3 的降级 ladder 真用了，但拦路石不是容量）

| 次序 | 配置 | 结果 |
|---|---|---|
| 1 | `--gpu-memory-utilization 0.85`（spec 原样） | **起不来**：`ValueError: Free memory on device cuda:0 (3.22/4.0 GiB) on startup is less than desired GPU memory utilization (0.85, 3.4 GiB).` 4G 卡被 Windows 桌面常驻占掉约 0.78 GiB，**0.85 在这张卡上物理不可能** |
| 2 | 降到 `--gpu-memory-utilization 0.8`（ladder 第 1 级） | 权重加载成功（`Model loading took 1.1 GiB memory and 46.264 s`）、KV 池 1.76 GiB / 65,808 token，然后**死于另一件事**：`RuntimeError: Could not find nvcc and default cuda_home='/usr/local/cuda' doesn't exist`（flashinfer 的 JIT 编译路径要 CUDA toolkit，而 WSL 里只有驱动库） |
| 3 | 同 `0.8` + `VLLM_ATTENTION_BACKEND=FLASH_ATTN` + `VLLM_USE_FLASHINFER_SAMPLER=0` | **起来**：`Using MarlinLinearKernel for AutoAWQMarlinLinearMethod` → `Using FLASH_ATTN attention backend out of potential backends: ['FLASH_ATTN','FLASHINFER','TRITON_ATTN','FLEX_ATTENTION']` → `Application startup complete` |

ladder 的第 2、3 级（`--max-model-len 2048`、`--swap-space 0`）**最终没用上**——因为拦路的不是容量而是依赖。
**spec §8 把 vLLM 的风险写成"4G 上 OOM"，实测告诉我们拦路石可能是依赖而不是容量**，
这条差异本身就是"本地安装推理平台"最有说服力的材料。

两个服务端名字细节会直接毁掉一次跑测：不加 `--served-model-name`，`/v1/models` 报回的是
`/mnt/d/inference-bench/models/AWQ` 这个路径；llama.cpp 不给 `--alias`，served id 就是那条 GGUF 绝对路径。
**照 spec §3 的模型名去请求就会 404**，所以脚本的 `resolve_model` 在预设与服务端不一致时打印告警并
改用服务端的名字（每一组的 stdout 里都留着这条告警）。

---

## 6. 兼容矩阵（spec §5 的第二判据）

（`--report compat` 现算）

| 引擎 | 科目 | 退出码 | 判据 | 失败形态（服务端/上游原话） |
|---|---|---|---|---|
| llamacpp | mcc-calc | 0 | ✓（另有 1 次结局相反） 退出码==0（spec §5 MCC①） | `退出码 0 但零文件产出，tool_calls_executed=0（模型只叙述不动手，退出码判据挡不住）` |
| llamacpp | mcc-readonly | 1 | ✗ 退出 0 + 工作区摘要不变 + 答案含 ValueError（spec §5 MCC②） | `{"error":{"code":400,"message":"request (5296 tokens) exceeds the available context size (4096 tokens), try increasing i` |
| llamacpp | insight-research | 1 | ✗（另有 2 次结局相反） 退出 0 + JSON 含 ['topic', 'report', 'brief', 'verification', ' | `ImportError: cannot import name 'main' from 'insight_agent' (D:\insight-agent-full\src\insight_agent\__init__.py)` |
| ollama | mcc-calc | 0 | ✓ 退出码==0（spec §5 MCC①） | `—` |
| ollama | mcc-readonly | 1 | ✗ 退出 0 + 工作区摘要不变 + 答案含 ValueError（spec §5 MCC②） | `{"error":{"message":"{\"error\":{\"code\":400,\"message\":\"request (5278 tokens) exceeds the available context size (40` |
| ollama | insight-research | 1 | ✗ 退出 0 + JSON 含 ['topic', 'report', 'brief', 'verification', ' | `ImportError: cannot import name 'main' from 'insight_agent' (D:\insight-agent-full\src\insight_agent\__init__.py)` |
| vllm | mcc-calc | 1 | ✗ 退出码==0（spec §5 MCC①） | `termination=llm_failure · tool_errors=0 · 产出 0 个文件` |
| vllm | mcc-readonly | 1 | ✗ 退出 0 + 工作区摘要不变 + 答案含 ValueError（spec §5 MCC②） | `{"error":{"message":"\"auto\" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set","type":"B` |
| vllm | insight-research | 1 | ✗ 退出 0 + JSON 含 ['topic', 'report', 'brief', 'verification', ' | `ImportError: cannot import name 'main' from 'insight_agent' (D:\insight-agent-full\src\insight_agent\__init__.py)` |

### 6.1 逐格的真实成因

成因的原始凭据在 `eval/results/inference-bench/compat/<引擎>/<科目>.{stdout,stderr}.txt`；
矩阵那一列只装得下一句，所以下面逐格说。

1. **两个 MCC 科目撞的墙与引擎无关，vLLM 撞的是另一堵。** MCC 只读问答这一发，服务端自报
   `n_prompt_tokens` = **5296（llama.cpp）/ 5278（Ollama）**，而用户文本实测只有 **58 token**、模板
   **29 token** ⇒ **固定开销 ≈ 5209 token**，装的是系统提示 + 8–9 个工具 JSON Schema + 仓库符号地图
   （`tools_offered` 在计算器那科是 8、只读问答那科是 9，因为 `load_skill` 只在技能目录非空时装配）。
   结论很硬：**4096 上下文的本地引擎结构性跑不了 MCC 的只读问答**，`-c 8192` /
   `OLLAMA_CONTEXT_LENGTH=8192` / `--max-model-len 8192` 才是能跑的配置。
   另外**同一发请求在两个引擎上差 18 token**（5296 vs 5278）：工具定义进不同 chat template 后
   长度不同——跨引擎算 prefill 开销时不能把"同一发"当成"同一个 token 数"。
   三科里工作区摘要**每一次都没变**（`digest_before == digest_after`，排除清单也记在证据里），
   说明只读模式的不写盘这一条在三个引擎上都成立。
2. **vLLM 走不到上下文这一步，它先拒了工具调用本身。** 原话是
   `"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set`。
   注意事实：**MCC 一个字节都没发 `tool_choice`**（`grep -rn tool_choice src/` 为空），
   是 vLLM 见到 `tools` 就按 `auto` 处理、又默认不带工具解析器。**要 vLLM 跑 Agent 必须先加
   `--enable-auto-tool-choice --tool-call-parser hermes`**（Qwen 系用 hermes 解析器），
   这不是可选优化。spec §5 写在"已知风险"里的那条"vLLM 的 tools 字段差异"实测兑现，
   而且**比原预言更硬**：不是报文形状差异，是默认直接拒。
3. **同一个 400 在三个引擎里有三种信封、三种措辞。** llama.cpp 把 500 塞在 **HTTP 200 的流里**；
   Ollama 把整段错误 JSON 再包一层字符串（`{"error":{"message":"{\"error\"…"`）；
   vLLM 是标准 OpenAI 错误体但换成 `maximum context length is 4096 tokens`。
   任何"按错误文本决定要不要重试"的客户端代码在这里都会分化——这是跨引擎接客户端最实际的一处坑。
4. **`mcc-calc` 这一格暴露的是判据自己的弱点。** llamacpp 上 MCC **退出 0、工作区一个文件都没写**
   （轨迹：`tools_offered=8`、`turn_start=1`、`tool_call=0`——模型把"先写 calculator.py、再写测试、
   再跑 pytest"完整叙述了一遍就结束），而 spec §5 给这一科的判据恰好就是退出码，于是它 ✓。
   同一科另一次运行是 `exit=1`（`termination=llm_failure`、`tool_errors=1`）。**两次结局相反都由
   `previous_runs` 留在证据里。"退出码判据挡不住只叙述不动手的模型"不是推测，是这一格躺着的记录。**
   Ollama 上同一科退出 0 且真写出了 `src/calculator.py`（`tool_calls_executed=1`，注意落点是 `src/`
   而不是任务要求的根目录），同时 MCC 自己的失败分类在 `run_end` 里给出
   `failure_modes=["no_verification"]`——**它知道自己没验证过**。
5. **Insight Agent 这一格先撞上的是上游仓库自己的入口是坏的，与引擎无关。**
   `pyproject.toml:29` 写 `insight-agent = "insight_agent:main"`，而 `insight_agent/__init__.py` 里没有
   `main` ⇒ `uv sync` 装好的 console script **一发就 ImportError**。本轮改按
   `python -m insight_agent.cli` 调（`cli.py` 底部有 `if __name__ == "__main__": app()`），
   **两种形态都记在证据的 `console_script_entry` 字段里**。
   第二处不一致：`--json` 的 help 自称输出 `report+verification+metrics`，而 `cli.py:51-57` 真正拼出的
   payload 是 `topic / report / brief / verification / latency_s`——**没有 `metrics`**。判据因此按实现
   重定义（`missing_vs_help` 字段专门记这个差）。**判据不能抄工具对自己的描述。**
6. **flaky 是实测事实，但本文只引还留在盘上的那一份。** llamacpp/insight-research 共 4 次运行，
   结果 2 通过（`claims=4` 与 `claims=1`）/ 2 失败，逐次结局由 `previous_runs` 留痕。
   **当轮原始 stderr 里可核的失败形态是**：llama.cpp `Error code: 400 … 5204 tokens …
   exceeds the available context size (4096 tokens)`、Ollama `5527 tokens`、vLLM
   `4097 tokens … maximum context length is 4096 tokens` —— **同一科在三个引擎上的请求长度是
   5204 / 5527 / 4097，又一次说明"同一发请求"跨引擎并不等长**。早先某次运行还出现过
   `LengthFinishReasonError` 这个形态，但**它的原始 stderr 已被后续运行覆盖，所以本文不把它
   当证据引用**（覆盖问题本身记在 §8 第 9 条，已在脚本里修掉）。本地 1.5B 做 planner/writer 的
   **质量**按 spec §5 不作判据（报告确实空洞，只留 `report_chars` / `claims` 两个观测量），
   但**上下文撞墙是硬失败**，照记。
7. **`langfuse_mounted` 九格全为 `false`**：没给 key，handler 就不挂载——这是预期行为而非缺陷，
   写在这里是为了让 spec §5 那一格有一句话答案。
8. **这一列自身的局限要声明。** 三格 `insight-research` 的"失败形态"显示的是那条**每次都在的**
   坏入口 ImportError，而不是当轮真正的 `BadRequestError`——因为这些记录写于 stderr 签名提取
   （`stderr_signature`）加入之前。**本文不回填、不手改证据**，改成在此点名，成因见上面第 5–6 条。

---

## 7. 附表：7B Q4_K_M 部分卸载（D-2，只回答"能不能跑"）

（`--report bench` 现算，`tier=appendix`）

| 引擎 | 档 | 并发 | attempts | 成功率 | TTFT p50/p95 (ms) | 吞吐 p50/p95 (tok/s) | 墙钟 (tok/s) | usage 中位偏差 | 偏差>5% 样本 |
|---|---|---|---|---|---|---|---|---|---|
| llamacpp-7b | appendix | c1 | 24 | 100% | 2246.3 / 4049.0 | 12.7 / 13.5 | 10.2 | 0.0058 | 12 |

命令：`llama-server.exe -m …qwen2.5-7b-instruct-q4_k_m-merged.gguf -c 4096 -ngl 20 --port 8080`，
12 prompts × repeats=2 = 24 请求（D-2 规定只跑 conc=1、只进附表）。

- **能跑，而且全活**：24/24，`finish_reasons` = 12 stop / 12 length，服务端 `model_ftype=Q4_K - Medium`。
- **慢是预期**：12.7 tok/s ⇒ 256 token 满输出约 20 秒；MCC 那种 8 轮工具链在这里是分钟级。
  **这一行是"4G 卡跑 7B 有真实可行路径"的功能性证据，不是性能证据。**
- llama.cpp 启动时就警告过 `common_fit_params: failed to fit params to free device memory:
  n_gpu_layers already set by user to 20, abort`——**`-ngl 20` 是人指定的，不是这套显存的自动最优解**，
  所以拿这一行去说"7B 在 3050 上就是 12 tok/s"是不成立的。
- 合并分卷那一步踩到一个足够重要的坑，写在 §8 第 4 条。

---

## 8. 失败形态与工具性缺陷（spec §8 风险清单之外，本轮新增）

1. **注册表代理劫持本机回环，而 `env` 查不出来**（影响测量有效性）。没有 `HTTP_PROXY` 类环境变量，
   但 `httpx` 默认 `trust_env=True` 会经 `urllib.getproxies()` 读到 Windows 系统代理。第一轮
   llama.cpp 两组因此全部经代理跑，客户端改 `trust_env=False` 后整组重测：
   **TTFT p50 2088.8 → 2087.7 ms（几乎不动，prefill 主导），吞吐 p50 89.2 → 92.3 tok/s（+3.4%）**。
   旧一轮原始件与旧汇总整组留在 `eval/results/inference-bench/superseded/`。
   正确修法是客户端 `trust_env=False`，**不是"记得设 NO_PROXY"**——后者对本机回环不保证生效，
   换台机器就忘。条件本身记进协议指纹（`protocol.http_client`）。
2. **协议块曾经冻结在第一次写入。** `merge_and_write` 合并时只更新 `groups/raw`，`protocol` 保持第一次
   写入的内容，于是盘上 `commands.ollama` 一度还写着 `ollama pull …`、`commands.vllm` 写着
   `--gpu-memory-utilization 0.85`——**都是已经不成立的文本**。已修（每次写入都按当前预设重写
   `protocol`），并加 `--refresh-protocol`：只重写协议块、**不动任何测量值**，`write_evidence` 的守卫
   照旧比 attempt 数，因此它绕不过任何数字。
3. **llama.cpp 把服务端 500 塞进 HTTP 200 的流里。**
   `data: {"error":{"code":500,"message":"Context size has been exceeded.","type":"server_error"}}`，
   个别 slot 还会先吐一个 `(` 再断流。只看状态码的客户端会把这 56 发记成成功。脚本现在把流内错误
   单独记成 `server_error` 字段，汇总里 `server_errors` 给出计数（56）。
4. **`llama-gguf-split --merge` 在磁盘写满时仍然逐行打印 `done`。** 第一次合并 7B 时 D 盘 100% 满，
   产出一个 4,142,317,568 字节的**全零文件**，日志却写着 `gguf_merge: writing tensors … done` /
   `merged from 2 split with 339 tensors`。是 GGUF 魔数校验（前 4 字节不是 `GGUF`）把它抓出来的；
   腾出空间后重跑，得到 4,683,073,536 字节 / GGUF v3 / 339 tensors 的可用文件。
   **工具自报的 done 不是产物可用的凭据**——任何"生成大文件"的步骤后面都得跟一次结构校验。
5. **winget 交付了一份哈希不符的安装包。** 大小与 `Content-Length` 一致（1,571,115,536 B），SHA256 却是
   `58946a23…`，而官方 `InstallerSha256` / `sha256sum.txt` 都是 `4A651432…`；随后 winget 进程自行退出、
   什么都没装。**尺寸对得上不等于内容对得上**，装完还要行为级复验（`ollama --version`）。
6. **NSIS 安装包禁用了静默模式**（§1 第 1 条）：`/S`、`/S /D=` 均只弹向导。非管理员 +
   无 CLI 解包器 ⇒ 只能用 ZIP 资产。**JD 里"本地安装推理平台"这句话的真实难度有一半在这类安装细节上。**
7. **分段下载器被"服务端无视 Range"坑过。** 有的镜像对带 `Range` 的请求回 `200 + 整个文件`，
   按偏移追加就写错位（现场表现：分片总量超过应有字节数）。修法：**只接受 206**，并在每轮失败后
   把分片截回本轮开始前的长度。1.5B GGUF 与 AWQ 是在这个 bug 修好之前下的，但两者的最终尺寸与
   官方 SHA256 都逐字节相符，所以**已入库的数字不受影响**——这是运气，不是正确性，所以记在这里。
8. **ModelScope / hf-mirror / GitHub 三类源都限在 0.5–2 MB/s，多连接不分摊**（6 连接 ≈ 1 连接）。
   本轮约 12 GB 下载占了净工时的多半，§1 里"Ollama 改用本地 GGUF 导入""vLLM 改硬链接安装"
   两个决定都是被这条逼出来的。

---

9. **兼容科目的原始件曾经每轮覆盖，flaky 的早期样本因此丢了凭据。** `cmd_compat` 原来只写
   `<科目>.stdout.txt` / `.stderr.txt` 两个固定名字，重跑就把上一次的原文盖掉——`previous_runs`
   留住了"上次 exit=1、claims=4"这样的摘要，**但留不住原文**。本文在 §6.1 第 6 条因此只能引用
   当轮原文。已修：每次运行同时写带时间戳的副本，路径记在该次的 `raw_stdout_run` 里，
   摘要与原文现在成对留痕。

## 9. 本节不证明的

- **不证明 vLLM 在这张卡上的上限。** `--enforce-eager`、9P 上的 venv 与权重、AWQ 单卡
  `tensor_parallel=1`、以及 spec §7 明写的"不调优"，四个因素混在 25.0 tok/s 里分不开。
  要拿这个数字与别人的 vLLM 比较，必须先说明这四个条件。
- **不外推到 7B（D-1）。** 7B 只有 llama.cpp 的部分卸载一行，且它是"能跑"级别的证据；
  vLLM / Ollama 的 7B 一律未测。
- **不证明"并发 4"是服务端并发能力的度量。** D-4 只测客户端 1/4 两条水位；llamacpp c4 的 53%
  说明的是**给定 `-c 4096` 时的容量**，不是"llama.cpp 不能并发"。同理 `wall_tps` 在失败占比高时
  会被失败请求缩短的分母抬高（§3 第 4 点）。
- **不证明三引擎输出内容一致。** `temperature=0` 之下仍可能因内核与数值差异出不同文本；本轮只记
  `response_sha256` 供事后比对，**没有把"输出相同"设为判据**。
- **不证明 Insight Agent 在本地模型上"可用"。** 只证明它的 LLM 工厂 local 档能把请求发到三个
  OpenAI 兼容端点，以及在哪一格、因为什么失败。
- **不回填 flaky。** 同一科多次相反结局由 `previous_runs` 保留，但每格只留**最新一次**判定；
  要按"多次运行的分布"下结论得改判据形态，本轮没改。

---

## 10. 复现

```bash
# 0) 核对（不产生任何证据）：prompt 档位、各引擎端点、内嵌素材是否仍与盘上逐字节一致
python scripts/bench_engines.py --check --engine ollama,llamacpp,vllm
python scripts/bench_engines.py --verify-fixtures          # 22/22 才算素材没被碰过

# 1) 起服务（显存互斥，跑完一个关一个）
D:/inference-bench/ollama/ollama.exe serve                    # 11434，模型见 §1 的 create
D:/inference-bench/llama.cpp/llama-server.exe -m <1.5B.gguf> -c 4096 -ngl 999 --port 8080
wsl -d Ubuntu -e bash -lc 'VLLM_ATTENTION_BACKEND=FLASH_ATTN VLLM_USE_FLASHINFER_SAMPLER=0 \
  /mnt/d/inference-bench/vllm-env/bin/vllm serve /mnt/d/inference-bench/models/AWQ \
  --served-model-name Qwen/Qwen2.5-1.5B-Instruct-AWQ \
  --max-model-len 4096 --gpu-memory-utilization 0.8 --enforce-eager'

# 2) 吞吐（主表每组 120 请求；附表 24 请求）
python scripts/bench_engines.py --engine ollama      --conc 1,4
python scripts/bench_engines.py --engine llamacpp    --conc 1,4
python scripts/bench_engines.py --engine vllm        --conc 1,4
python scripts/bench_engines.py --engine llamacpp-7b

# 3) 兼容九格（判据是退出码 / 工作区摘要 / 键集，不吃计时，可与安装类任务并行）
python scripts/bench_engines.py --compat --engine llamacpp
python scripts/bench_engines.py --compat --engine ollama
python scripts/bench_engines.py --compat --engine vllm

# 4) 表格现算；协议块单独刷新（不动任何测量值）
python scripts/bench_engines.py --report all
python scripts/bench_engines.py --refresh-protocol
```

重跑某一组时 `write_evidence` 会拿 attempt 数与盘上那份比：**少一组就拒绝写盘**。
`--allow-thinning` 是留给"确实换了判据"的出口，本轮没用过。
