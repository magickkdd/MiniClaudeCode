# 深入验证：每一层的判据、实到数字与不证明什么

> 从 README §4.7–§4.16 拆出。README 只留一张索引表；细节在这里。**原文逐字搬运，未改写** —— 数字与证据文件都仍以盘上那一份为准。
>
> 编号保持不变：别处说的 `README §4.7` 指的就是本文的 §4.7。
| § | 主题 | 一句话结论 | 证据 |
|---|---|---|---|
| [4.7](#47-上下文压缩阶梯长任务为什么不炸) | 上下文压缩阶梯 | 达成（配对 400 = 0）；"压缩后成功率 ≥60%" 按**未量**记账 | `../eval/results/b2-compact-ab.json` |
| [4.8](#48-仓库符号地图与工作记忆memory接手陌生仓库) | 仓库符号地图 | 机制 7/7 绿，**因果未达成**（轮数 0%，线 ≥20%） | `../eval/results/b3-repomap-ab.json` |
| [4.9](#49-为什么没有并发一次动手前的砍单s12-tier-3) | 只读工具并发 | **动手前被数据砍掉**（只值 live 墙钟 0.011%） | `../scripts/probe_parallel_share.py` |
| [4.10](#410-命令在哪儿跑沙箱后端可回退检查点与崩了接着跑backends13) | 沙箱后端 / 检查点 / 续跑 | 达成 12/12 一致，**沙箱本身没测到**（本机无 docker） | `../eval/results/b6-backend-ab.json` |
| [4.11](#411-把第三方能力接进来mcp-桥与按需加载的技能exts14) | MCP 桥 + 按需技能 | 20/20 前提全绿，对手是两个（敌意 fixture + 官方 SDK） | `../eval/results/s14-mcp-skills.json` |
| [4.12](#412-为什么没有多-agent第二次数值上的砍单s15-a-tier-3) | 多 Agent | **动手前被数据砍掉**（连 SPEC 点名的那份数据也不过线） | `../scripts/probe_verifier_gate.py` |
| [4.13](#413-把跑过的轨迹导成-rl-数据一次先证明不够的导出evalexportrlpys15-b) | 轨迹 → RL 数据 | 12/12 前提成立，但它首先证明的是**不够** | `../eval/results/s15-export.json` |
| [4.14](#414-接-jaeger-之前先证明翻译没骗人otlp-导出器infraotelpy73-3) | OTLP 导出器 | 18 条判据 17 ✓ + 1 未量，顺手挖出**五个谎** | `../eval/results/t3-otlp.json` |
| [4.15](#415-同一份权重放到三个本地推理引擎上吞吐以及这个项目到底跑不跑得起) | 三个本地推理引擎 | MCC 固定开销 **5,209 token** 把 4096 上下文顶爆 | `../docs/inference-bench.md` |
| [4.16](#416-把自研-research-agent-接成外部工具mcp-桥的第一次真实握手mcp-link) | 接自研 Research Agent | 接上了；一根 30s 超时线劈成握手 30s / 调用 600s | `../docs/mcp-link-spec.md` |

## 4.7 上下文压缩阶梯（长任务为什么不炸）

v1 的选择是"只观测、不干预"：超过 `TOKEN_BUDGET` 的 95% 直接 `context_overflow` 止损，因为压缩一旦删错一组 `tool_calls`/`tool` 配对，端点就回 400，排查成本比一次明确的"任务太大"更高。v2 把这把刀接上了，接法是两个旋钮 + 三级：

| 层 | 触发（`pressure = est / TOKEN_BUDGET`） | 动作 | 额外 LLM 调用 |
|---|---|---|---|
| **L1 elide** | ≥ 0.70 | 把**已完成轮次**的工具输出换成一行 `[elided: read_file src/a.py 4213 chars — 需要时重新读取]`，压到 0.65 收手。消息数量不变，内容能从磁盘重读 | 0 |
| **L2 summarize** | ≥ 0.85 | 一次独立请求把最老 60% 历史压成结构化纪要（7 个字段，含"已改动文件""被否决的路径"），整组替换 | 1 |
| **L3 refuse** | ≥ 0.95 | 保持 v1 行为：停止并发出中文说明。**这条不可关**，它是最后的地基 | 0 |
| 熔断 | `est ≥ 0.9 × CONTEXT_HARD_LIMIT` | 与阶梯无关的独立止损，防的是"请求被端点拒收" | 0 |

三处值得单独说的实现细节：

- **配对是硬不变式，判定只有一份**（`messages.pairing_problems` / `assert_pairing`）。每层压完先跑它，破损就**放弃这次压缩并照旧落一条 `context_compact{pairing_ok:false, note:"…配对破损…"}`** —— 静默放弃与"阶梯没生效"在报表上长一样，所以说明必须上线路（终端同样打印）。一层放弃后 `run_ladder` 直接 `break`：L1 已经证明这段历史压不得，再付 L2 那次调用买不来任何东西。
- **`dedupe_tool_use_ids` 在消息入历史前改名**（`<原 id>~<位置>`，不用 uuid，否则 trace 与基线不再逐字节可比）。B2 首跑就是死在跨轮撞 `call_0` 上：撞车让判定函数认为**原始**历史就非法，于是每次压缩都被放弃，看起来像"压缩无效"。改了几个记在 `llm_response.id_repairs`。
- **L2 的账必须记**。`context_compact.summary_tokens` 进报表的"压缩开销"一列，`mcc eval` 的 `tokens` 也把它加回来 —— 不记的话，全循环最贵的一次单点开销在报表上是免费的。

```bash
mcc eval --tasks eval/tasks-b2 --repeats 1                    # 实验组：默认阶梯
mcc eval --tasks eval/tasks-b2 --repeats 1 --no-compact       # 对照组：阶梯一次都不动手
mcc eval --tasks eval/tasks-b2 --repeats 1 --context-budget 12000 --context-hard-limit 15000   # 负向对照
PYTHONPATH="src;demos" python -X utf8 scripts/b2_compact_ab.py # 三臂 + 判据 → eval/results/b2-compact-ab.json
PYTHONPATH="src;demos" python -X utf8 scripts/b2_compact_ab.py --live --live-repeats 5   # 真端点那半条（花额度）
PYTHONPATH="src;demos" python -X utf8 scripts/b2_compact_ab.py --probe                  # 高预算反向对照（花额度）
```

实测（`lc-rollup-api`：`ledger/` 八模块 194,356 字符，逐字抄签名再写汇总层；数字是 2026-09-23 于 S15-b 之后重跑的）：关阶梯第 5 轮 `est=32,611` 越 `l3_refuse`（阈值 30,400）判 `context_overflow`；开阶梯 19 轮全绿、14 次压缩（L1 11 · L2 3）、0 次因配对放弃、0 个配对 400。这条死因不是靠人转述的 —— `context_refuse` 记录自带 `line / threshold_tokens / est_tokens / ladder_enabled`，因为 `turn_start` 每轮只在请求前采样一次，光看 est 序列会把"预算杀掉的会话"读成"模型自己停了"。

一个后来才发现的耦合：system 里换上符号地图，把这几格数字都推动了，而且方向不一致 —— off 臂更早死（越线 est 31,287 → 32,611），on 臂峰值反而降了（26,278 → 21,635，阶梯提前动手），代价是 L2 的摘要开销从 3,900 涨到 5,850 tokens。12 条判据两种配置下都全绿，结论没变，但"地图挤占上下文预算"这件事第一次有了数值形状。

**live 那一半已于 2026-09-23 签完，签的是 SPEC 原文那句**：`--only gf-calculator`、真端点 5 次、27 个请求，llm 层报错 **0**（429 没再出现），阶梯 **5/5 条 run 都动手**共 17 次压缩（L1 10 · L2 7），**因配对破损导致的 400 = 0**、因配对放弃 0。2026-09-22 那次被 `HTTP 429` 打断的账仍在 `eval/results/b2-live-blocked.json` 与 SPEC §3.3.3，同一趟抓到的 bug（`mcc eval --engine live` 用 `supports_live` 全集覆盖了 `--only`，把 1 题探针放大成 17 题）已修 + 三条回归测试。

**可"压缩后成功率 ≥60%"这句我们按未量记账 —— 它是自己加的口径，而且实测证明它在这个题上没有稳定的被量对象。** 6,000 预算下 0/5 全 `context_overflow`；让"L1 还动手"和让"跑得完"的两个预算区间不相交（≤7,598 对 ≥18,778，越线值实测 5,926~17,839）。于是去高区间跑了两趟反向对照（`--probe` → `eval/results/b2-live-budget-probe.json`），**两趟结论相反**：样本 1 是 10 轮、压缩 **0** 次、1/1 通过；样本 2 是 18 轮、模型自己把估算顶到 19,291（> L1 线 16,800）、压了 6 次手，最后因 `max_turns` 判负 —— 而那 18 轮其实把活干完了（`76 passed`、四个应有文件齐全、行为探测通过）。换句话说大预算下的成功/失败解释的是模型这一趟怎么走、外加我们自己的轮数上限，不是压缩救没救回来。与其拧一个刚好能过的预算，不如把"这条量不到"写成一条带数字的判据：`success_rate_unmeasurable`，`ok: null`（未量的第三种状态在证据脚本里是显式的一格，`?` 打在终端上，不参与 `pass` 的计算）。

## 4.8 仓库符号地图与工作记忆（`memory/`，接手陌生仓库）

v1 给模型的是那张 30 行广度优先目录树 —— 它说"有哪些目录"，不说"这个仓库里有什么可以用"。S11 换成 **ast 符号地图**（`memory/repo_map.py`）：每个 `.py` 一行文档 + 模块级函数/类/方法签名 + 常量，按 `REPO_MAP_TOKENS` 预算裁剪，超出部分明确写出"未列出的是哪几个"。零新依赖（不引 tree-sitter），因为本项目只对 Python 负责。

三条设计决定值得单独说：

- **排序不做 PageRank，用三个能解释的廉价信号**：① 与最近读/改的目标同目录或直接命中；② 文件名出现在当前 TodoList 文本里；③ 被仓库内 `import` 的次数。每个条目自带它入选的原因，`mcc trace` 里的 `repo_map` 事件带 `reasons` —— 否则"地图为什么给了这个、没给那个"只能靠猜。
- **地图只在指纹变化时重画**（`mtime+size`），一次读文件不会让 system 变样。这条不是性能优化，是**稳定性要求**：system 每轮都在发，抖动一次就等于给模型换了份上下文。`test_a_read_never_re_renders_the_map_mid_session` 拿"读一轮之后两次请求的 system 字符串逐字相同"把它钉住。
- **`.mcc/` 是派生物，不是第二个事实来源**。缓存坏了、版本不认识、被删 —— 一律降级成"没有缓存"重建（`MemoryStore.degraded` 报原因），绝不报错停机；`TEST_COMMAND` 类笔记只展示、不自动执行，记忆内容没有任何一条路径能走到 `eval`/`exec`/`subprocess`（`test_a_test_command_note_says_it_is_display_only` 用 AST 扫这一层的 import 与调用做静态检查）。

一个踩过的坑值得写下来：**只读模式必须一个字节都不写**。READONLY 问答跑完，`.mcc/memory.json` 出现在工作副本里，A3（"只读承诺"）当场被自己的缓存打破。修法是只读臂**根本不建 store**（`cli/main.py`），而不是"写了再让判据白名单掉"——后者是在自己挖的坑上再盖一层布。同时 `.mcc` 进判据侧的 `NOISE`，AUTO 批跑留下的缓存不会被算成"改了源码"。

```bash
mcc eval --repeats 3                       # 实验组：system 里是符号地图
mcc eval --repeats 3 --no-repo-map         # 对照组：system 里是 v1 目录树
PYTHONPATH="src;demos" python -X utf8 scripts/b3_repomap_ab.py          # 只核机制，不花额度
PYTHONPATH="src;demos" python -X utf8 scripts/b3_repomap_ab.py --live   # B3 的因果半条（6 题 × 3 次 × 2 臂）
```

B3 的两句判据写在 `eval/results/b3-repomap-ab.json` 里，两臂只差 `--no-repo-map` 一个开关（脚本会先证明这件事：同题集、同重复数、每题 system 哈希跨臂不同、`llm_request` 次数两臂相等）。

**实到结果：机制层 7 条全绿，因果层判"地图没用"。** 6 道 A1 题 × 3 次 × 2 臂 = 36 次真模型运行、1,260,511 tokens：

| | 线 | 实到 |
|---|---|---|
| `context_peak` p95 涨幅 | ≤ 15% | **+5.4%**（7,711 → 8,129，地图自己的 token 计进了分母） |
| `steps_to_success` 中位降幅 | ≥ 20% | **0%**（6.0 → 6.0） |
| 额外 LLM 调用 | 0 | 两臂各 33 / 33 |
| 通过数（防幸存者偏差） | on ≥ off | **13/18 vs 14/18** ✗ |

逐题看更诚实：3 题变快（`rt-checkout-bulk` 5→4、`session-fix-humanize-only` 6→4、`slug-dedup-clean-rule` 7→6.5）、2 题变慢（`session-fix-ttl-units` 7.5→8.0、`taxed-fix-eu-vat` 6→8）、1 题（`bh-format-duration`）两臂 3 次全灭所以对中位数贡献为 0 —— 那份中位数实际是 5 题的。结论写成一句话：**地图很便宜，但它在 A1 这种"搜一个符号名 → 读两个文件"的形状上不是那条杠杆**；这类题的定位路径目录树已经给够了。20% 这条线保持原样，不改成能过的数，重测计划记在 SPEC §7.3-5（要换考卷：跨 ≥5 文件的改动题）。机制本身保留 —— 它几乎免费，且是 §3.4 三条排序信号的落地处。

时间开销的账在同一个证据文件的 `budget_lines` 里（不进 B3 判定，SPEC §4 没写它们）：每轮附加开销 **0.0003ms** ✓；进程内 memo 重画 4.0~9.4ms；跨进程命中磁盘缓存 27.9~99.4ms ✗（≤5ms 那条要 `stat` 142 个文件的指纹，压到 5ms 只能不信指纹，而那是 §2.3-1 禁止的）；冷建 142 文件 557~1,381ms、总中位 **829ms** ✗ 一条 800ms 的线 —— 而且这条线配"<2000 文件"隐含 0.4ms/文件，实测 4~7ms/文件，**规格自己的两个数差一个数量级**。详见 SPEC §6.2.1。

## 4.9 为什么没有并发：一次动手前的砍单（S12 → Tier 3）

SPEC v2 §3.5 原本排了 5 小时做"只读工具并发"（`ThreadPoolExecutor` + 五个竞争写点 + 7 项并发测试），并且写死了砍单条件：**可并行轮占比 < 20% 就不做，这条判断由数据做，不由我做**。S12-a 先把这道闸做成了脚本（`scripts/probe_parallel_share.py`，只读盘上轨迹、不写一行调度代码），结论比砍单条件更硬：

| 闸 | 线 | 实测（46 次 live 运行 / 261 个工具轮） |
|---|---|---|
| 可并行轮占比 | ≥ 20% | 合计 **21.5%**，最新一层 b3-live 单独看 **19.8%** —— 一层过一层不过，差一个轮 |
| 墙钟 p50 下降（B5 那句话） | ≥ 15% | 线程池**无限大**的上界也只有 **162ms / 1,489,447ms = 0.011%**（p50 0.009%） |
| 同一条在 fake 引擎上 | —— | 工具即 99.4% 的墙钟，可省也只到 p50 **0.127%** |

原因不复杂，且写在轨迹里：**LLM 延迟占 live 墙钟的九成**，而它不在调度器管辖范围内；剩下的工具时间里，`read_file` 单次 1~3ms，56 个可并行轮平均只值 2.9ms，真正贵的 `run_tests` 是 EXECUTE 风险、按设计永不并行。所以并发的正确性成本（ASK 竞态、trace 交错、计数重排）买不来任何东西 —— **这不是"没时间做"，是"测完发现不该做"**，JD 第 14 项想筛的恰好是后一种判断。

两条纪律顺带被这次测量钉住：① 不用 fake 引擎的 p50 给 B5 签字，因为那里的分母是"没有模型的世界"；② B5 的 15% 保持原样不重述 —— 要复活它得换一个说得通的前提（网络盘、几十 MB 的单文件读），而不是换一个能过的数。条件记在 SPEC §7.3-6。`_run_tools()` 的"刻意不并发"注释继续有效，现在有数据了。

## 4.10 命令在哪儿跑：沙箱后端、可回退检查点与崩了接着跑（`backend/`，S13）

S13 之前，`bash` 与 `run_tests` 直接 `subprocess.run` 在宿主上 —— 模型跑一句 `rm -rf` 炸的是我的笔记本，而"跑到一半被杀掉"就只能从头再来。这一节是三件事：把"在哪儿跑"抽成一个接口、把每次写入变成可回退点、把会话变成能续的现场。

**`ExecutionBackend` 只有四个方法**（`available` / `exec` / `snapshot` / `restore`），两个实现：

| | `LocalBackend` | `DockerBackend` |
|---|---|---|
| 行为 | v1 现状**一字不改**（必须继承 `os.environ`，否则找不到 python） | `docker run --rm --name mcc-<hex> --network none -v <工作区>:/work -w /work` 一次一条命令，不驻留容器 |
| 命令形态 | `str` 走 shell、序列走 `execv` | 同上；宿主解释器只把 **argv[0]** 换成镜像里的 `python3`，模型自己写的 `./venv/bin/python` 原样保留 |
| 环境变量 | 全量继承 | **只透传显式点名的键** —— 沙箱的一个实际收益是 `.env` 里的密钥不出现在被评测进程的 `/proc/self/environ` |
| 超时 | 杀进程 | 杀 CLI 之后还要 `docker rm -f` 兜残留（`docker run` 被杀不等于容器停了） |

**降级不会是静默的**：`--backend docker` 而机器上没有 docker 时，用 local，同时把原因写在终端、trace 的 `backend` 事件和报表里。允许降级是因为沙箱是加强项（缺了它任务仍可判定），不允许静默是因为一个没人看见的降级会让 B6 的结论指向一个根本没跑过的后端。

**检查点是影子 git**：`--git-dir=<记忆目录>/snapshots --work-tree=<工作区>` 的独立索引，**绝不在用户工作区 `init`**（那会在别人的仓库里留一个 `.git` 冲突）。每次 `write_file`/`edit_file` 成功后提交一条 rev 并记进 `checkpoint` 事件，`/undo` 就是 `restore(prev_rev)`，`/backend` 面板列出栈深。两个后端共用**同一份**影子仓库 —— 各存一份会出现"在 docker 臂 undo 回到 local 臂的某个状态"这种跨臂时间旅行。

**Durable 会话**：每轮把 `SessionSnapshot`（消息、todos、`AgentState`、脱敏 config、`last_checkpoint_rev`、**`done_call_ids`**）落进记忆目录，`mcc resume --list` 看还有谁的现场，`mcc resume <id>` 接着跑。恢复时已完成轮次的工具**绝不重放** —— 重复一次 `bash` 的副作用不可接受。代价是口径要写清楚：`run_end.tool_calls` 是**会话级**的账（跨进程续算），trace 里的 `tool_call` 记录是**进程级**的观察。不承诺 exactly-once（那需要后端事务），承诺 at-most-once 副作用 + 可审计现场。

```bash
mcc --backend docker -y "把失败的测试修好"      # 命令进容器；没 docker 就明说降级成了 local
mcc eval --backend docker --repeats 2           # B6 的对照臂
mcc resume --list && mcc resume --latest        # 崩了之后接着跑
/backend                                        # REPL 里：后端、检查点栈、会话现场三合一面板
```

**B6 实到：12 / 12 格判定一致（6 道会执行命令的题 × 2 次 × fake 引擎），8 条前提全绿，32 次容器命令行。** 两臂的 verdict、终止原因、**判据清单的 `(label, ok, detail)` 三元组**、`(工具名, ok)` 逐格序列完全相同，宿主解释器没出现在任何一条容器命令里。这条比 B2/B3 便宜是有原因的：后端不改模型看到的任何东西，所以 fake 引擎上的判定分歧**只可能是后端 bug** —— 不像 B3 那样"fake 的 Δ 是我写的剧本的 Δ"。

**这条线没测到的是沙箱本身。** 这台开发机上没有 docker，脚本临时造了一个会真的执行命令的假 `docker` 替身：它按 docker 语义收命令行、只透传 `-e`、把 `-v` 的宿主目录当 `/work`，然后在**宿主上**跑那条命令。所以文件隔离、网络隔离、镜像里装了什么，一次也没测过 —— 结果文件里 `container_isolation_tested: false` 就是这句话的机器可读版本，墙钟那 +4.0s 量的也是"多起一个本机 python 进程"而不是容器启动。真 docker 到位后原样重跑 `scripts/b6_backend_ab.py`，才会第一次看到沙箱自己的代价。

第一次试跑时 B6 报出过一处分歧，凶手是替身自己：`-v` 按**第一个**冒号切分，Windows 的 `D:\...` 当场被切成空挂载目录，于是"容器"在替身自己的 cwd 里跑了整套仓库测试然后超时。它表现成"两个后端结论不同"，实际两臂跑的根本不是同一份代码。前提清单里"容器的工作目录就是这一题隔离出来的那份目录"是这次加的，加完立刻红、修完才绿 —— **一致性判据必须连自己的测量工具一起怀疑**，否则 B6 会通过一个假分歧失败、也会通过一个假一致成功（后者更糟，所以降级臂默认不产出一致率，必须 `--allow-degraded` 显式声明）。

## 4.11 把第三方能力接进来：MCP 桥与按需加载的技能（`ext/`，S14）

JD 第 6 项写的是"LangGraph / MCP 生态"。我们没有用 LangGraph（理由和代价的逐条对照在 `docs/framework-equivalence.md`，78 行），但 MCP 这一半是能落地的机制，而它真正的考点不是"能不能连上"，是**连上之后谁信谁**。

**三条硬要求（SPEC §3.7）**：① 远端工具一律改名成 `mcp__<server>__<tool>` 才进注册表 —— 一个远端报出 `read_file` 也覆盖不了本地那个（实测：远端广告 `['read_file','write_file']`，注册成 `['mcp__fx__read_file','mcp__fx__write_file']`，本地 `read_file` 仍读出磁盘真内容，冒名者只回一句"〈远端的 read_file，不是本地那一个〉"）；② **远端自己声明的风险等级不可信**（D19）—— 它三个工具里报了 `read`/`read`/`destructive`，我们三条全部采纳 `execute`，声明值只作为 `declared_risk` 显示出来给人看；③ 远端工具走的是**同一个** `BaseTool.invoke()` 校验链，参数不合法在出网之前就失败（`text=12` → 「参数校验失败：'text' 应为 string，实际是 int」，盘上没写、连接还能继续用）。

**权限闸门**：AUTO 模式对本地写是 `allow`、对 `mcp__` 外部工具是 `ask`，理由是「外部工具（MCP）在工作区之外执行，不在自动放行的语义范围内，需要单独确认。」这句不是装饰 —— 证据脚本里那个 `write_note` 工具把文件写到**工作区之外**（本地路径锁完全管不到它），拒绝臂的结果是"工作区外没有那个文件 + trace 里没有 `tool_call` 记录（只有 `permission/decision=deny`）"，授权臂才落下 `['这一行应当出现']`。**READONLY 更硬：连桥都不建**，`extra` 里一个外部工具都没有，`mcp` 事件带 `skip_reason=只读模式：没有启动任何 MCP 服务，外部工具未装配` —— 因为"发现"本身就要起一个第三方进程，那是副作用，不该为"只是列一下工具"破例。

**只有一条装配路径**：外部能力全部经 `ToolRegistry.default(extra_tools=...)` 进来，`main.py:181` 是唯一的调用点（用 ast 数出来的，不是 grep —— 文本里还有 2 处只是文档字符串提到）。开第二条通道的话，权限门与 trace 就得各修一遍才追得上。

环境边界同样是被测出来的而不是被承诺的：子进程 `has_api_key=false`、`has_pythonpath=false`、`has_path=true`（不给 PATH 就没法起解释器），点名要的 `MCP_FIXTURE_MARKER` 才透传，`dropped_env=['LLM_API_KEY','MCP_DEFINITELY_NOT_SET_ANYWHERE']`。握手参数里带的密钥在 trace 里是 `‹已脱敏 24 字符›`，`config.redacted()` 只回服务名 `['fx']`。服务崩/沉默/吐垃圾都有去处：退出 → 「MCP server fx 关掉了输出（进程退出码 …）：fixture: 我不干」（子进程的 stderr 被带进原因里），不回答 → 「回答超时（3s）」，而 `close()` 之后 `children_after_close=0` —— 一个起不来的服务被跳过时是**明确报错**，不是静默少几个工具让用户以为功能还在。

另一半是**技能**：一个技能 = 一个目录里的一个 `SKILL.md`，system 里只放目录（名字 + 一句话），正文要 `load_skill` 按需展开。这套经济性是被数字钉住的：一份 5,200 字符的正文，目录只有 **99 字符 / 3 行**；再加第二个技能目录只贵 **19 字符**（正文涨了 8,800 字符）—— 常驻目录 / 展开后 = **0.019**。仓库自带的 2 个技能：目录 4 行 240 字符，正文合计 2,130 字符。**不读技能自带的其他文件、不执行技能脚本**（D20），同目录有别的文件就只报名字。

```bash
mcc mcp                       # 发现并列出外部工具（会真的握手一次）；--json 给机器读的那份
mcc skills                    # 技能目录 + 常驻/按需的字符数
/mcp  /skills                 # REPL 里同名的两条：前者逐工具列出"远端自报风险 vs 我们采信的档位"
MCP_SERVERS='[{"name":"fx","endpoint":"python","args":["server.py"],"env":["TOKEN"]}]' mcc -y "…"
```

**实到：20 / 20 条前提全绿，verdict=PASS，6.4s**（`scripts/s14_ext_demo.py` → `eval/results/s14-mcp-skills.json`），并且是**对着两个对手**量的：自写的敌意 fixture（故意叫 `"bad name"`、故意缺 `inputSchema`、故意在风险字段上撒谎），以及一个用**官方 SDK** `FastMCP` 写的正常服务（`sdk-fx @ 2024-11-05`，`properties=['text','times']` 由 SDK 生成而不是我们手写，`tools/call` 回来的是 `TextContent` dataclass 而不是 dict，中文 `你好 MCP` 原样往返）。只测前者，"解析正确"完全可能只是"我自己的两边错得一致"。

去重后 4 个工具采纳、4 个跳过并各带原因：`远端工具名不合法：'bad name'` / `没有 inputSchema，参数没法校验` / `inputSchema 没有 properties，参数会被静默丢弃` / `同名工具已经注册（远端报了两个同名的）`。后三条都是**实测逼出来的**：`tools/base.py::_coerce` 会静默丢掉没在 `properties` 里点名的参数，所以一个没有 `properties` 的 schema 必须整条不装配，否则"校验过了"意味着"什么都没校验"。

**这条线没测到的是**：真第三方生态服务（只有自己的两个）、HTTP/SSE 传输（按 §7.4 顺位 2 砍到 Tier 3）、技能自带脚本（D20 明确不做），以及"模型会不会主动去 `load_skill`"—— fake 引擎里取技能是剧本写好的，真实分布要看 live。

## 4.12 为什么没有多 Agent：第二次数值上的砍单（S15-a → Tier 3）

SPEC v2 §3.8 给多 Agent 留的口子极窄：不做 planner/worker/critic 三件套，只做一种形态 —— `spawn_agent` 派一个**独立上下文**的子 agent 去跑测试，父 agent 只收"失败用例名 + 精简原因"。而 §7.3-1 给这条写了前置条件：**只在 B1 的失败分布支持时做**（D21 那条线：`no_verification` + `self_confirm` 合计 > 20%）。S15-a 因此先写探针再决定动不动手（`scripts/probe_verifier_gate.py`，一行调度代码都没写）。

| 闸 | 线 | 实测 |
|---|---|---|
| ① 占比（全部 run 分母，签字用这个） | > 20% | live **1/75 = 1.3%** |
| ① 占比（只看判 fail 的 run） | > 20% | live **0/32 = 0.0%** |
| ① D21 点名的那份数据（B1 fake 批） | > 20% | 它自己 **12/72 = 16.7%** —— 也不过线 |
| ② §3.8 那句"200 行 pytest 输出"（折成 14,000 字符） | 验证输出值不值得隔离 | 验证类输出占工具总字符 **41.6%**（每格中位 20.7%）→ 贵；但 148 次验证调用里只有 **2 次**越过那条线 |

三道都不支持动手，**连 D21 自己点名的那份数据都不支持**。但这次测量真正的收获在口径上，三条都是先不钉住就会得出相反结论的地方：

1. **分母没写**。D21 只写"占比"。同一份 fake 数据在两个分母下是 16.7% 与 0.0%，差一个数量级 —— 哪个都不容疑就签字，是在用读表人的默认值做架构决策。
2. **分子记的是行为，不是代价**。`self_confirm` 的判据是"宣告完成时最后一次验证是红的"。把命中的 run 单独对一遍判据结论：live `{'pass': 1}`、fake `{'pass': 12}`，**一份都没判负**。而 verifier 能救的只有"因此把任务做砸"那部分 —— 这条线即使过了，它数出来的也不是要买的东西。反方向也测了：判据放宽成"中途见过红 + 最后宣告完成"，占比立刻 35/75 = **46.7%** 过线，可那个数没意义，正常调试本来就会红几次再改绿。
3. **"200 行"有产地，但产地不是我们的设计**。结构化那条路（`run_tests` 自己数通过/失败、只留失败用例名）95 次调用**单次最大 4,263 字符、0 次过线**；两次过线的（28,062 与 17,352 字符）全部是模型用 `bash` 直接跑 `python -m pytest -v`，**绕开**了我们自己已经精简过的那条路。所以"验证输出没被隔离"的正确修法在提示词与工具描述，不在再加一层上下文 —— 而"验证很贵"（41.6%）与"隔离不划算"两句同时成立的原因，是贵的是**累计**、隔离只能按**单次**省。

**决定**：`spawn_agent` 连同 §3.8 那三条约束（不得绕过权限门、token 预算计入父、子轨迹独立 session 可展开成树）一起留在 Tier 3，S15 交付物改为只剩 §3.9 的轨迹导出器。**D21 的 20% 不改**：它的错不是数字太大，是分母没写、分子没接代价，这两处现在都被实测钉住了（SPEC §3.8.1 末尾给了三条重评触发条件）。与 §4.9 同一条纪律的第二次应用 —— 而且这次的结论比 S12 更值得说：**两次砍单主要收益都不是省下的工时，是发现判据本身写错了的地方**。一个不动手的探针没有这个副作用。

## 4.13 把跑过的轨迹导成 RL 数据：一次先证明"不够"的导出（`eval/export_rl.py`，S15-b）

SPEC §3.9 要求把轨迹导成 `(state, action, reward)`，并且**不做训练**（D23：4GB 显存 + 无标注预算，投入产出为负）。这一节的重点不在导出器能导出什么，在它查出来我们**缺什么**。

规格那句话默认了一个不成立的前提：**trace 里有观测**。恰恰相反 —— §6.4 的 trace 只存计数与类型名（`output_chars`、`blocks` 的类型、`user_input_chars`），为的是日志体积，也为的不把仓库内容抄进日志。所以 `state` 只能有两个产地，导出器把这件事做成了行上的可判定字段，而不是一句注释：

| 产地 | 凭什么 | 有多少 |
|---|---|---|
| **A：会话快照前缀** | §4.10 的 `SessionSnapshot.messages` 是全文。配对可验证：第 t 轮那条 assistant 必须坐在 `messages[message_count]`，且它的 `tool_use` id 序列与这一轮 trace 里的**逐个相等** | 62 个 run / **314 行带正文** |
| **B：结构指纹** | 只剩 trace：`message_count` / `est_tokens` / `prefix_hash` / 本轮供给的工具数 | 144 个 run / **720 行**（69.6%），每行自述"为什么没有正文" |

配对是**整份 run 全有或全无**：一轮对不上，这个 session 的所有行都退回产地 B。这是 §4.6 那条判据纪律（"剧本说改完了"不算，跑测试说了算）换到数据层的样子 —— **一份错位的 (state, action) 比导不出来更坏，因为它看起来是好的**。

```powershell
# 13 个批次 → 一份 JSONL + 一份自证前提
mcc export-rl --batch eval/.work/fake --batch eval/.work/b6-ab/local ... `
              --out eval/.work/rl-all.jsonl --evidence eval/results/s15-export.json
```

实到（2026-09-23 重跑，含 §7.3-7 那道闸门补的那批带指纹的 fake 全量批）：**输入 206 个 run → 1,034 行 / 2.34 MB / 20.51s，丢弃 0，12/12 条前提成立**。reward 只有 `pass=1.0` / `fail=0.0` 两种终局，步级折扣 `value = terminal × 0.97^steps_to_end`；`error` / `aborted` **根本不进数据** —— 把我们自己的故障喂进奖励函数，等于教模型躲避 bug。失败标签读 trace 里 as-run 的 `failure_mode` 记录，绝不用今天的分类器重算（阈值在本项目里已经收窄过一次，重算就是给历史数据贴现在的标签）。成对偏好这块接口有（`--labels`），盘上人工标注 **0 条**，54 对全部是"同题重跑"自动配出来的 —— 手写剧本的核对表核对的是分类器，不是两个动作谁更好，拿它当 SBS 是在把"我审过代码"写成"人标过偏好"。

**这一节（导出器这条线）真正的产出是三处数据完整性问题**：前两处在上游、由导出器的校验抓出来，第三处是导出器自己首版的越界、被它自己的测试抓到。同一批交付还修掉两处不在导出器里的缺陷 —— 免重放账本被本进程自己污染、跑一次 `pytest` 会改写已入库的 demo 证据，各自记在 §10 表第 28 / 23 行：

1. **manifest 与 trace 在重跑之后不再一一对应**。trace 文件名按 `(task, repeat, engine)` 定，重跑是**覆盖**上一份，而 `append_manifest` 是纯 append —— 于是旧行指向的已经是别人的轨迹。12 个批次里 195 个 run 有 **28 个**被导出前丢弃（`duplicate-trace` 14 + `turn-mismatch` 14），全集中在 `b2-ab/live`（42 行 manifest、28 个键）。已修在源头：`append_manifest` 改成按 `(task, repeat)` 取代旧行 + `os.replace` 原子重写。**代价说清楚**：修好之后新批次 1:1，但修之前那批的中间态不可复原，同一道题的重跑数据只能取最后一次。
2. **`steps_from()` 造了一个不存在的决策步**。循环先 `state.turn += 1` 再算预算（`loop.py:388` 在硬熔断与 L3 之前），所以 `context_refuse` 带的是**下一轮**的轮号；把它当一步用，`trace_steps` 就比 manifest 的轮数多 1，于是**每一口死在预算线上的 run 都被判"轮数对不上"整条丢弃** —— 被丢的恰好是最有信息量的那批样本（阶梯真的动过手的那些）。修法是要求一步至少留下 `llm_request` 或 `llm_response` 之一，回归一条：`test_a_refusal_turn_that_never_sent_a_request_is_not_a_step`。两处源头都修完之后重跑：`dropped_runs.count = 0`，输入 run 从 195 折到 158（差额是 manifest 去重折叠掉的历史重跑行）。
3. **快照查找曾经会爬出批次目录**。一开始写成"往上找三级"，测试立刻抓到它跑进系统临时目录、把一次运行的 session 配上了**另一次运行**的快照。现在只允许从 trace 路径上一级往下找：找不到时的正确结论是"这行是产地 B"，不是"去更远的地方再看看"。

**第 1 条只修了一半，另一半隔了一天补**（SPEC §7.3-7）。取代旧行解决的是"这行指向哪一份轨迹"，解决不了"盘上这份就是判据当时看的那一份"—— 轮数那道校验挡得住"少了几轮"，挡不住"同样轮数的另一份轨迹"，而后者配出来的 reward 看起来完全合理。现在 `append_manifest` 在**唯一的落盘出口**给 trace 盖内容指纹（非空行数 + 字节数 + 全文件 sha256 前 16 位，`infra/trace.fingerprint()`），并附一份**相对批次目录**的路径；`export_rl.identity_of()` 在 join **之前**比对，三态分开：`verified` 进数据、`drift` 整条丢弃并写进同一本丢弃账（丢弃码 `trace-drift`，带两边的 sha 与行数）、指纹上线之前的 12 个历史批次标 `unverified` 并**逐行自述**没被校验过。审计端因此多两条会红的前提：「每一行都自述同一性来源，且没有一行带着未校验的指纹冒充已校验」与「凡 verified 的行，盘上那份 trace **现在**仍是判据当时看的那份」—— 后者按文件去重**回读磁盘重算哈希**，所以"导出之后有人改文件"也响（`test_the_read_back_premise_goes_red_when_the_trace_changes_after_export` 就是靠故意改一个字节来证明它不是空转的）。绝对路径那半边由 `RunRecord.from_dict(..., base=批次目录)` 兜：指空时按相对路径找回文件，所以**整个批次目录搬走之后**续跑与导出都还认得现场。实到：新闸门在 13 个批次上挡下 **0 行**（`verified` 202 / `unverified` 832，那 832 行恰好等于上一版交付的全部行数）—— 它是哨兵不是筛子，这一天的收益是"以后改了会响"，不是"现在抓到多少"。规格原文写的"末行哈希"换成了全文件哈希，因为末行查不出中间行被改（`test_an_edited_middle_line_is_drift_too` 摆的正是行数与末行都不动的状态），而为了数行数本来就要读一遍文件，边际成本是 0。`mcc eval --baseline` 那半边没做，理由是**规格前提不成立**：基线文件按 §3.2 的规矩刻意不存 trace 路径，它从不读轨迹，给它加 trace 校验等于先塞给它一个它故意没有的依赖。

**这份证据不证明的**：不证明这批数据够训模型 —— 它首先证明的是**不够**。要变成能训的数据，缺的不是算力，是一次 §2 级别的改动（观测正文进盘的体积上限、以及"仓库内容进不进日志"的边界重开）。§9 的诚实清单里有一条对应着它。

---

## 4.14 接 Jaeger 之前先证明翻译没骗人：OTLP 导出器（`infra/otel.py`，§7.3-3）

SPEC v2 §3.1 从第一天就把话说死：**埋点代码不引入 OTel SDK**，"能接入 Jaeger"是导出器的一层翻译，不是全项目的架构前提。这一节把那层翻译补上，并给规格那句「这一步只有翻译」标了价。证据不证明"能导出"（94 条单测已经证明），它证明的是翻译**没丢、没编、没自作主张**。

```powershell
mcc trace <会话> --otel                                        # OTLP/JSON 打到 stdout
mcc trace <会话> --otel --out trace.otlp.json                  # 落盘
mcc trace <会话> --otel --endpoint http://localhost:4318/...   # 校验不过就一个字节都不发
python -X utf8 scripts/t3_otlp_export.py                      # 24 份已入库轨迹 → eval/results/t3-otlp.json
```

三个形状上的决定，每一个都是"照抄 OTLP 会丢东西"逼出来的：

1. **一个 `span_id` = 一个 OTLP span。** 同一 id 上的多条记录合并进一个 span；同名字段撞车时**不覆盖**，后到的改挂 `mcc.<kind>.<字段>` —— `turn_start.est_tokens` 与 `llm_request.est_tokens` 压缩之后就是不相等，而"差多少"恰恰是要看的东西。
2. **没有 `span_id` 的记录不许丢。** 5 种 kind 天生不带 id（`backend` / `failure_mode` / `mcp` / `skills` / `todo_update`），它们变 event，落在同一轮的 turn span 上，找不到就退到它前面的 run span。v1 那 4 份轨迹**整批**没有 id，于是给每份一个确定性的 `mcc.unhosted` 承载 span（id = `sha256("orphan:" + trace)[:16]`，同一份文件两次导出同 id）。合起来：1,210 条记录里 **208 条是以 event 的形式落地的**，未记账 **0**。
3. **协议装不下的东西要留下字据。** `AnyValue` 没有 null 分支 → null 的下场是"属性缺席 + 进 `coverage.unrepresentable_nulls` + 写进 `gaps()` 的中文说明"，不是塞一个空串；没有对象分支 → 嵌套结构变 JSON 字符串（不用 collector 侧已标 deprecated 的 `kvlistValue`）；`int64` 与时间戳是十进制**字符串**。`validate()` 在发送前把这些会整批拒收的错先变成能读懂的中文报错。

**证据的形状**（`eval/results/t3-otlp.json`，24 份已入库轨迹 / 1,210 条记录 / **0 个模型请求、0 token** / 5.4s，其中 3s 是等本机收集端应答）：18 条判据，**17 ✓ + 1 条按未量记账**。四本账全部从 payload **反查**，不读导出器自己的账本 —— 否则它算错什么我就跟着信什么：

| 判什么 | 怎么判 | 实到 |
|---|---|---|
| 值不丢 | 逐字段按 `FIELD_MAP` 算出该挂的名字，再核对**编码后的值** | **6,964 个字段值，丢 0、改名 0**；另 213 个 null 三处账本相等（导出器 = 脚本独立计数 = `gaps()` 那条） |
| 结构不编 | span 数 == 源 `span_id` 数 + 承载数；每条父子边在 trace 里找出处；每个属性名有出处 | 480 = 476 + 4（承载只在 4 份 v1 里，v2 的 20 份为 0）；**436 条声明边全部照搬，改写 0、丢 0**，补挂的 20 条全部落在会话/run 锚点上；8,987 个属性 / 103 个名字，凭空造的 0、裸名 0 |
| 时间不造 | 按 `start=min(ts, ts-latency)`、`end=max(ts)` 逐 span 重算端点 | **480/480 一致**，208/208 个 event 时刻与它那一条记录的 `ts` 一致；零时长 span **101** 个照实计数；时间来源自述与重算不符 0 |
| 两条路一个字节 | `mcc trace --otel --out` 的产物 vs 库函数对同一会话的产物 | **24/24 逐字节相同**（907,650 B、gaps 提示 168 行）；`validate()` 问题 0；两次 `translate()` 24/24 确定性；24 份输入跑前跑后 sha256 全同 |

**「只有翻译」的代价是量出来的**：trace 474,114 B → payload **907,626 B（×1.91）**，展开成 KeyValue 数组 + 每属性自述编码的净开销；**313 个 `stringValue` 里装的是 JSON**（占属性 3%）。这一层真正花钱的地方不是写代码，是别把这些当成免费的。

**每条判据的 detector 都先挨过一次假错**：10 种错做进 payload 的深拷贝（真实轨迹一个字节不动），对应判据必须红 —— 吃值、造名、空串冒充 null、漏密钥、改边、错挂锚点、伪造时长、抹记账戳、资源属性与 trace 不符、往收集端探针的 payload 里塞一条绝对路径，**10/10 全红**。红不了的 detector 给出的 ✓ 等于零，这一节是给上面那些 ✓ 定价的。最后那一种做在手写探针 payload 上（前九种做在语料 payload 上），并且只在"清洁侧扫出 0"时才算红 —— 否则它是白给的。同一道"证据不许悄悄变薄"的守卫（§10 第 27 / 30 行那条）在这里是第二处：文件数、记录数、核对字段数、属性数、判据条数任何一样缩水就拒写。

**四个真错是这一节的实际产出**，同一个视野盲区：`_resource()` 与 `mcc.span.record_kinds` 早先只扫 `span.records`，而 v1 的记录全长在 `events` 上。于是① 一份真有一个会话的 v1 轨迹，资源里 `mcc.session.count` 报 **0**；② 没人报过模型时 `gen_ai.request.model` 落成 **`""`**；③ `mcc.trace.schema_version` 缺省兜到当前版本，**四条 v1 轨迹集体自报 "2.0"**（它们从没自报过版本）；④ `mcc.unhosted` 的 `record_kinds` 是空串。①②④ 被"payload 空串数 == trace 里本来就有空串数"那条判据的差额（10 vs 6）逮到，③ 任何按名字核对的判据都看不见 —— 它是字符串形状的谎，是比对同一份 payload 的 v1/v2 两侧时看出来的，所以给它**新加了一条判据**：`the_resource_only_says_what_the_trace_says`（报出的必须 trace 里真有，没报的必须 trace 里真没有；`0 个会话` 是测出来的数要留着，`""` 不是）。四条都由 `tests/test_otel_export.py` 钉住，含一条直接读盘上那份 v1 轨迹的。

**第五处谎在这份证据自己身上**，而且是写完前四处之后回头才看见的：`collector_probe` 记的是 `attempted: false / "没给 --collector，一次都没试"`，而**同一份文件**的判据详情里躺着一句"本机 4318 有人应答但回 502" —— 那句话是早先手动跑过一次 `--collector` 时抄下来的，脚本自己没测。测的与说的各说各话，正是这一格对着别人的证据挑了四遍的那处毛病，所以修法不是把句子改软，是**让默认路径真的去试一次**：`_collector()` 现在默认打 `localhost:4318`，非本机 host 仍然拒绝代发。发出去的不是语料，是 `_PROBE_RECORDS` 那 **4 条手写记录**翻出来的 2,987 B（`leaks: {绝对路径 0, 端点 0}` 由同一把尺子 `_leak_scan` 量，闸门在 POST **之前**：手写的记录里本来就没有路径，探到路径等于导出器在从环境里捞内容 —— 那种 payload 更要留在本机）。实测结论没变（HTTP 502、响应体为空），变的是这句现在是量出来的。**未量仍按未量记**：有人应答但不说 OTLP，既不能记成"我们的 payload 被拒"，也没有可查日志说明是谁回的 502。

**这一节不证明的**：① **真实收集端收下并画出树**未量 —— 本机 4318 有人应答但回 502（空响应体，没有可查日志，实测见 `collector_probe`），没有第三方收集端可试；`validate()` 挡得住协议层**拒收**，挡不住收集端**解释**的差异（INTERNAL kind 被平铺、JSON 字符串按纯文本显示）。② 不证明跨机发送安全：payload 里带着 **59 个绝对本地路径**和 **24 个端点样字符串**（`mcc.session.config` 那一类）。`--endpoint` 指向非本机时 CLI 在 stderr 点名这三样东西**然后照发** —— 那是用户明确要的出口，一条命令不该替他改主意；证据脚本的试发则**拒绝**非本机 host，而且它自己那一发用的是不含仓库内容的手写 payload（一份为了量泄露面而生的脚本不该自己当泄露源）。这两种待遇的差别就是 §10 第 34 行。③ 不证明字段语义被对方按 GenAI 约定理解 —— 名字对齐由 `FIELD_MAP` 与单测钉住。

---

## 4.15 同一份权重放到三个本地推理引擎上：吞吐、以及"这个项目到底跑不跑得起"

JD 里那句"本地安装大模型推理平台 Ollama / vLLM / llama.cpp"一直在，但本仓库此前只证明过"走 OpenAI 兼容端点"，没证明过**MCC 装配出来的请求在本地引擎上真能跑**。§4.5 那句"换引擎只改三行配置"是一句承诺，这一节把它变成读数。契约 `docs/inference-bench-spec.md`，as-built 与全部失败形态 `docs/inference-bench.md`，驱动 `scripts/bench_engines.py`。

```powershell
python scripts/bench_engines.py --check --engine ollama,llamacpp,vllm   # prompt 档位 + 端点核对
python scripts/bench_engines.py --engine llamacpp --conc 1,4            # 12 prompts × 10 次 × 两档并发
python scripts/bench_engines.py --compat --engine vllm                  # MCC 计算器 / readonly-qa / Insight research
python scripts/bench_engines.py --report all                           # 表格从盘上证据现算
```

一张表（RTX 3050 Laptop 4GB，Qwen2.5-1.5B-Instruct q4_K_M，`temperature=0`、`max_tokens=256`、同一批 prompt）：

| 引擎 | 并发 | 成功率 | TTFT p50/p95 (ms) | 吞吐 p50 (tok/s) | 墙钟吞吐 (tok/s) |
|---|---|---|---|---|---|
| llama.cpp | 1 | 120/120 | 2087.7 / 2549.6 | 92.3 | 41.91 |
| llama.cpp | 4 | 64/120 | 2174.8 / 2898.9 | 72.9 | 66.93 |
| Ollama | 1 | 120/120 | 2085.0 / 2542.0 | 93.9 | 36.42 |
| Ollama | 4 | 120/120 | 6556.1 / 8468.8 | 95.4 | 91.55 |
| vLLM | 1 | 120/120 | 2141.9 / 2614.8 | 25.0 | 19.15 |
| vLLM | 4 | 120/120 | 2159.7 / 2197.1 | 24.5 | 74.65 |

四个读数，其中三个是本项目自己的问题：

1. **"换引擎只改三行"这句话在 4096 上下文的本地引擎上不成立，而且原因在 MCC 自己身上。** 只读问答这一发，服务端报的 `n_prompt_tokens` 是 **5296（llama.cpp）/ 5278（Ollama）**，而用户文本只有 58 token、聊天模板 29 token —— 剩下 **5209 token 是 MCC 的固定开销**（系统提示 + 8–9 个工具模式 + 仓库符号地图）。三个引擎的 4096 配置都因此直接 400 掉这一发。修法不是把引擎换成更贵的，是把 `--max-turns`/`tools` 装配按本地档裁剪，或者把上下文要到 8K——这条已记进 §9 的诚实清单。
2. **vLLM 拒的还不是同一件事。** 它两科 MCC 都死在 `"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set`。注意措辞背后的事实：**MCC 一个字节都没发 `tool_choice`**（`grep tool_choice src/` 为空），是 vLLM 看见 `tools` 就按 auto 处理、又默认不带工具解析器。**给 vLLM 加两个 flag 才谈得上跑 Agent**，这不是可选优化。另一条同源的检查点：MCC 的客户端**只走非流式**（`openai_compat.py` 里没有 `stream`），所以 spec 问的"流式/非流式差异"在本项目里答案是"MCC 侧没有流式通路，流式只在 bench 客户端上被检验"——三个引擎的流式 `usage` 都正常，MCC 用的非流式路径工具模式也正常，两件事分别成立。
3. **同一个 400 有三种信封。** llama.cpp 把它塞在 **HTTP 200 的流里**（`data: {"error":{"code":500,…}}`，个别 slot 还先吐一个 `(` 才断流）；Ollama 把整段错误 JSON 再包一层字符串；vLLM 用第三种措辞。所以"按状态码判成功"在本地引擎上是**会出假成功**的——脚本因此把成功定为 200 + 正常收流 + `finish_reason ∈ {stop,length}`，并把流内错误单独记成 `server_error` 字段（那一组 56 发就是这么抓出来的）。
4. **客户端计量与引擎自报对得上，对不上的部分能解释。** completion 偏差中位 0.43%、最大单样本 3.57%；prompt 侧 22% 的中位偏差全部由恒定的 **+29 token 模板开销**解释（短 prompt 相对偏差 42–47%、长 prompt 只有 2.3–2.6%）。顺带纠正一条抄来的旧结论：spec 预言的"Ollama usage 粒度坑"**本轮未观测到**，0.34.4 认 `stream_options.include_usage`，0 次 400 回退。

至于判据自己的弱点：`mcc-calc` 这一科按 spec 用退出码判定，而 llama.cpp 上出现过一次 **退出码 0、工作区一个文件都没写**（轨迹 `tools_offered=8 / tool_calls_executed=0`，模型把计划讲了一遍就结束），同一科另一次运行则 `exit=1`。两次结局相反都由 `previous_runs` 留在证据里。**A2 那种"退出码即完成"的判据挡不住只叙述不动手的模型**——这是这轮实测里最值钱的负面结果。

**这一节不证明的**：① 不证明 vLLM 在这张卡上的上限（`--enforce-eager` 关 CUDA graph 是 4G 下的必要妥协、权重与 venv 都在 9P 挂载上、且 spec §7 明写不调优，25.0 tok/s 里这四件事分不开）；② **不外推到 7B**——4G 显存只跑得出 7B Q4_K_M `-ngl 20` 的参考行（12.7 tok/s、24/24），它进附表不进主表（D-2）；③ 不证明三引擎输出内容一致，只比吞吐与时延；④ 兼容九格在 1.5B 上做 planner/writer 的**质量**不作判据（报告空洞是实测事实，见 `report_chars` / `claims`）。


## 4.16 把自研 Research Agent 接成外部工具：MCP 桥的第一次真实握手（mcp-link）

§4.11 证的是"桥接得住敌意 fixture 与官方 SDK"；这一节把对端换成**另一个自己写的系统**——Insight Agent（LangGraph 九节点研究 Agent，自带 `mcp_server.py`），回答三件事：能不能接上、分钟级长任务的边界在哪、接上之后值不值得写进简历。契约与 as-built：`docs/mcp-link-spec.md`（预检、判据、实测数字全在那里回填），新增代码只有一支替身回放脚本与 6 项测试（`tests/test_mcp_bridge_link.py`）。

```
MCC 主循环 (agent/loop.py)
   └─ registry：本地 8 工具 + 桥注册的外部工具（mcp__ 前缀）
                   │ ext/mcp.py（stdio 子进程 + JSON-RPC 2.0）
                   ▼
       insight-agent mcp_server（uv run 子进程）→ gate → recall → planner
       → Send×N research_one → compress → gap_analyzer → writer → verify → archive
```

**P1 预检把一根线拆成了两根。** 桥原本所有请求共用 `timeout=30.0` 一根线——initialize、tools/list、tools/call 都是它。research fast 档直跑实测 **89.3s**、standard **90.9s**：固定 30s 下分钟级长任务**必被误杀**，而且死法最难看（模型等到的是"回答超时"）。修法是 `MCP_TOOL_TIMEOUT`（缺省 600s）只管 tools/call，握手与发现仍走 30s——起不来的服务要在会话开始时秒报错，不是让用户等十分钟才知道配置错了。回归钉住的是**报文里带着生效的那条预算**：会话线 25s、预算 2s 的替身返回「回答超时（2s）」（证明 tools/call 走的是新线），超时后同一服务上的秒级工具立刻可用（一次超时不报废整个会话）。

**接线本身撞出一个 spec 没预料到的真问题：§2 的原配置握得了手，起不了 research。** 子进程 env 由桥的最小白名单构成，`*api_key*` 类变量名**一律扣下**（B6 那条：`.env` 里那把 key 不该因为装了第三方服务就流进别人的进程）；而 insight-agent 的 `load_settings(".env")` 按**子进程 cwd** 找 `.env`——子进程继承的是 MCC 的 cwd，读到的是 MCC 的 `.env`，缺 `LLM_MODEL_ID`/`TAVILY_API_KEY`。实测探针：`tools/call research` → server 端 `RuntimeError: 缺少配置项` → SDK 包成 `isError` → 桥把它变成 `ToolResult` 回给模型自愈。修法零协议改动：args 里加一层 `-c` 包装，子进程起手先 `os.chdir` 回自家项目根读自己的 `.env`——密钥不复制、不透传，两项目各持各的钥匙。**§4.11 风险表里"远端进程报错 → is_error → 模型自愈"那条路径，由这次真实故障钉住。**

接上之后的实到数字（2026-10-01，全链路 live）：

| 判据 | 实测 |
|---|---|
| 握手与发现（T1） | `mcc mcp`：握上手 1 个 · 外部工具 **4 个** · 弃用 0（spec 草稿写 3——`research_async` 是草稿之后加的，as-built 记实数） |
| 工具同框 | `/tools`：本地 8 工具 + `mcp__insight-agent__{research, research_async, get_notes, list_archives}`，远端全部标 execute + `[MCP/insight-agent]` 前缀 |
| 功能 E2E（T2） | 任务"调研 httpx 超时配置的最佳实践，把结论写进 notes.md"：`notes.md` 落盘含 **9 个引用 URL**（判据读文件不读模型自述）；trace 里 `permission`×2——research 是 **allow（人点头的确认）**、write_file 是 **allow（AUTO 对本地写放行）**，同框就是 §6.3-1 的对照组 |
| 档案落点 | 研究档案落在 **insight-agent 项目根** `data/notes/httpx超时配置最佳实践.json`——chdir 包装生效的旁证：MCC 工作区只收报告文本，记忆归对端管 |
| 长任务边界（T3） | research(standard) 过桥：第一次 64.9s 死于残余的云端 429（29 字符 `Error executing tool research`），**模型原参数重发，第二次 ≈300s 完成**——报告回填、`standard-notes.md` 落 5 个引用 URL，两次都在 600s 预算内；fast 档完成形态见 T2（126.6s）。深度不等于墙钟：运行间波动大于 fast/standard 的差 |

**这条线没测到的**：多远端 server 编排（单 server 先跑通）、HTTP/streamable-http 传输（§7.4 顺位 2 维续砍）、Insight Agent 反向调用 MCC（它不需要 Coding Agent）、多客户端并发（server 侧信号量=1，会排队而非并行）。演示口径：**演示用 fast（≈90s）+ `get_notes`（秒级）补第二轮，standard 离线跑**——长任务的正确用法不是"让演示冷场 5 分钟"。
