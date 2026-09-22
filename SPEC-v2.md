# Mini Claude Code Agent System SPEC v2.0

版本：v2.0-draft1 · 日期：2026-09-22（as-built 段落补记至 2026-09-23）· 状态：**Tier 1/2 收口** —— S8 ✅（E1/E2/E3 关闭、`test_metrics_have_producers` 绿、
B4 前半 16 条轨迹人工核对退出码 0）· S9 ✅ **B1 达成**（24 题 × 3 次 fake 全批 `pass@1=20/24`、退出码 0、基线入库）·
S10 ✅ **B2 两侧都签**（fake 12 条 + live 2 条判据全绿；我自己加的"压缩后成功率 ≥60%"改按**未量**记账，理由与被实测推翻的高区间假设见 §3.3.4）·
S11 ◐ **B3 未达成**（机制层 7/7 绿、因果层轮数 0%：一次有效的证伪，§3.4.2）·
S12 ⛔ **动手前就被自己的数据砍进 Tier 3**（可并行的只读轮只值 0.011% 墙钟，§3.5.1）·
S13 ✅ **B6 判定层达成、沙箱层未测**（12/12 格一致 + 8 条前提全绿，但本机无 docker、替身跑在宿主上，§3.6.2）·
S14 ✅（MCP bridge + Skills，D19/D20，§3.7.2）· S15-a ✅ **D21 那道 20% 的闸不支持动手**（verifier 搁置，§3.8.1）·
S15-b ✅（§3.9 trajectory exporter：832 行、丢弃 0、10/10 前提绿，顺手挖出并修掉两处上游缺陷 + 自己首版的一处越界，§3.9.1）·
**下一步 Tier 3**（§7.3：B3 在 ≥5 文件任务上重测、B5 复活评估、trace 落 observation）

基线：`main @ 6a73e32`（v1.0 已交付并推送 `magickkdd/MiniClaudeCode`）
预算：89 净工时（Tier 1/2/3 = 40+25+15 = 80h，缓冲 9h；每天 4h ≈ 22 天）· 交付物：可无人值守批跑的评测体系 + 六项能力升级 + 回归基线
关系：本文只写**增量**。SPEC v1.0 未被本文推翻的条款全部继续有效；两处勘误见 §0.3。
依据：本文所有能力取舍对齐 `JD总结.docx` 的 20 项矩阵，接口断言全部核对 v1.0 代码，端点结论全部来自 `scripts/probe_caps.py` 与 `scripts/probe_window.py` 实测（§0.4）。

---

# 0. v1.0 → v2.0：这一版为什么不加新功能

## 0.1 一句话定位

**v1.0 证明"这个 agent 能干活"；v2.0 要证明"我知道它干得好不好、什么时候会坏、以及改动有没有变好"。**

v1.0 的产物是一个 agent。v2.0 的产物是**围绕这个 agent 的那套系统**：度量、回归、上下文与记忆的预算控制、可恢复的长任务、外部工具生态的接入口。腾讯混元那个岗位的名字直接写明了这类工作的名称 —— **Agent Harness Engineer**，其岗位职责原文是"tracing & observability 系统 / 自动化 eval pipeline、A/B testing、regression detection / Agent debugging 工具 / 标注、SBS 评测、数据管道"。这五条恰好是 v1.0 交付物里最薄的地方，也是 v2.0 的主线。

这不是"加功能慢一点"的保守选择。恰恰相反：**没有度量层，v2 后面任何一项"改进"都无法与"自我感觉良好"区分**。压缩可能悄悄把 agent 变笨，RepoMap 可能只是让上下文涨得更快，并发可能把 trace 写成交错乱序 —— 而这些在 v1.0 的四个人工审查 demo 里全都看不出来。

## 0.2 v1.0 现状基线（2026-09-22 实测复核，不是引用 README）

| 项 | 实测值 | 来源 |
|---|---|---|
| `src/` 代码量 | 3423 行（最大 `loop.py` 370、`cli/main.py` 336、`openai_compat.py` 296、`permissions.py` 209） | `wc -l` |
| 测试收集数 | **201** collected（README 写的 195 是 `tests/test_hanoi.py` 的 6 项进入收集范围之前的数） | `pytest --collect-only -q` |
| 其中与 Agent 能力相关 | 195（`test_hanoi.py` 6 项是汉诺塔练习题，由可视化材料提交带入，非本项目能力证据） | 逐文件收集 |
| 工具数 | 8（`tools/` 7 个 + `agent/todo_tool.py` 的 `write_todos`） | `registry.py:70-78` |
| demo 与证据 | 5 个 demo、9 份证据文件、fake 5/5、live 4/4 | `demos/results/` |
| 已知局限 | 8 条（README §9）+ 8 条 SPEC 偏差记录（README §10） | README |
| 依赖 | `httpx` + `python-dotenv`，`rich` 可选；无 pydantic、无 SDK、无框架 | `pyproject.toml` |

**口径修正**：从此以后对外报的"测试数"必须写成"195 项 Agent 能力测试 + 6 项无关收集"，或直接按目录分组报。单一总数会被 `test_hanoi.py` 这种文件污染 —— 这正是 §3.1 要解决的"指标产地"问题的一个缩影。

## 0.3 v1.0 度量谎报清单（v2 的开工依据，必须先修）

这三条都是我这次逐行核对代码找出来的，不是推测。它们的共同点是：**报表里有这个数字，但没有任何地方生产这个数字。**

| # | 问题 | 证据 | 影响面 |
|---|---|---|---|
| **E1** | `redundant_calls` **恒为 0**。`AgentState.redundant_calls`（`state.py:39`）声明了、进了 `snapshot()`（`state.py:56`）、被 `summarize()` 读取（`trace.py:127`）、印在每份证据文件里（`run_demo.py:661`），但**全代码库没有任何一处对它 `+=`** | `grep -rn redundant_calls src/` 只有 4 处：声明、快照、读取、打印 | 9 份证据文件的 "redundant / denied" 列，redundant 那半边是默认值不是测量 |
| **E2** | 即使补上累加，v1 也没有可用来算它的口径。停滞签名 `_signature()`（`loop.py:341`）是"整组调用排序后的 JSON"，只有**整组完全相同**才让 `stall_count` 递增（`loop.py:191-193`）；而 SPEC v1 §3.7 对 `redundant_call_rate` 的定义是"连续相同（工具+参数）调用占比"。**两者不是同一个量**，一个都不能替代另一个 | `loop.py:191-199` vs SPEC v1 §3.7 | 指标定义与实现漂移 |
| **E3** | `kind="run_end"` 与 SPEC v1 §3.8 表格里的 `session_end` 不一致。实现是对的（`summarize()` 找 `run_end`），**规范是错的** | `loop.py:297`、`trace.py:121` vs SPEC v1 §3.8 | 任何按 SPEC 字面实现的消费方（比如未来的 OTLP 导出器）读不到终止记录 |

**勘误（不改 v1 文档正文，只在此登记）**
- 勘误 1：SPEC v1 §3.8 的 `session_end` → 实际事件名为 `run_end`。v2 采纳 `run_end`，并新增 §3.1 的 **schema 快照测试**，让这类漂移在 CI 里失败而不是在报表里静默。
- 勘误 2：SPEC v1 §3.7 的 `redundant_call_rate` 定义拆成两个指标（§3.1）：`repeated_call_rate`（逐调用：与前一次同名同参）与 `stalled_group_rate`（整组：命中停滞检测的轮次数）。

另有一条不算 bug 但影响解读的事实：**fake 引擎的轨迹里 `usage` 与 `context_peak_tokens` 全为 0**（`FakeLLM` 不返回 usage）。所以 README §7.3 表格里 fake 列本来就没有 token 数 —— 这是正确的，但同一张表把"轮数/调用数/报错数"混排，容易让人误读成 fake 也在证明成本行为。**v2 的证据模板必须按"这份证据证明了什么"分栏**（§6.4）。

## 0.4 端点能力实测（`scripts/probe_caps.py`，2026-09-22，`agnes-2.5-flash`）

v2 的每一项与模型交互的设计都取决于这些事实。共 12 次请求（含 3 次可复现性重复），总消耗 < 6k token。脚本已入库：`scripts/probe_caps.py`，随时可复跑。

| 探测项 | 结果 | 对 v2 的意义 |
|---|---|---|
| `usage` 字段 | 只有 `prompt_tokens / completion_tokens / total_tokens`，**无 `prompt_tokens_details`** | **`cached_tokens` 拿不到 → prompt 缓存收益在本端点不可测量**。见 D14，v2 不为缓存做优化，只保留"前缀稳定"的写法纪律 |
| `stream=true` + `stream_options.include_usage` | 可用：6 个 chunk、`[DONE]` 哨兵、`usage` 出现在流内、`delta.role` 有；**`delta.tool_calls` 未验证**（探测用的提示没触发工具） | 流式 UX（Tier 2 可选项）技术上可行；但"流式下增量拼接 tool_calls"必须先补一次探测才能动 `loop.py` |
| `POST /responses` | **200**，返回 `resp_...` 结构的 id | Responses API 在这台端点是活的。v2 **不迁移**（D8 的裸报文优势仍在），但登记为 V3 备选：`previous_response_id` 会把 §3.6 的 durable 会话大幅简化 |
| 工具 schema 带 `strict: true` + `enum` | 200，正常返回 1 个 `tool_calls`，`arguments` 仍是字符串 | `strict` **被接受 ≠ 被强制**。禁止把参数正确性外包给端点，`BaseTool.validate()`（`base.py:100`）仍是唯一防线 |
| `parallel_tool_calls: true` | 接受，且单轮返回 **2 个**调用 | 与 v1 实测一致，全量回填这条硬约束继续成立 |
| `max_tokens=32000` | 200，不报错 | 输出上限不是瓶颈；`MAX_TOKENS=4096` 是**我们自己**设的闸（`config.py:12`），压缩设计不受它约束 |
| Prompt caching（同一 2275-token 前缀连打两次） | 两次 `prompt_tokens` 都是 2275，`prompt_tokens_details` 为 `null` | 没有可观测的缓存命中。**结论见 D14：不把 todos 移出 system** |
| `temperature=0` 可复现性 | 同一请求 3 次，输出 `"7" / "7" / "7"` 全同 | 对 §1.3 B1 的批跑有意义（同配置可复现），但 n=3 单提示是**弱证据**，正式评测前必须在任务集上复测（§8 R6） |
| 响应额外字段 | 顶层多出 `metadata` | 未解读；v2 的 trace 可选记录其键名，不做依赖 |

**上下文窗口已实测（P1 关闭，2026-09-22 `scripts/probe_window.py`）**：`270,570 prompt_tokens` 的请求**被正常接受**（200，`finish_reason: length`，`completion_tokens: 1`），实测填充料 chars/token = 3.25。累计探测开销 ≈ 477k prompt token（在批准的 20–60 万区间内），证据文件 `demos/results/context-window.probe.json`。

这条实测**推翻了 §3.3 原来的立论前提**，而且推翻的方式很有代表性：

- 端点窗口 ≥ 270k，而 `TOKEN_BUDGET = 120000`（`config.py:15`）。也就是说这个预算**从来不是**"防止请求被拒"的安全闸 —— 它离硬边界还差一个数量级。
- 更要命的是：v1 全部 live 轨迹里最大的 `context_peak_tokens` 只有 **12,185**（codegen），压力 `12185 / 120000 = 0.10`。**按 §3.3 现在的触发线（L1 在 0.70 即 84k 才启动），压缩阶梯在我们自己的任务分布上是死代码。** 一个永不触发的机制会被当成"已实现"，而实际上一次也没被验证过 —— 这正是 §0.3 那类"报表里有数字但没人生产它"的架构版。
- 结论：v2 必须把两个被混在一个旋钮里的东西拆开。**`CONTEXT_HARD_LIMIT`（窗口安全，防 400）= 200000**；**`TOKEN_BUDGET`（成本与注意力质量预算）= 32000**，压缩阶梯挂后者。§3.3 与 §5.3 已按此改写。
- 副作用：压缩的真实目的从"避免被拒"变成"控制成本与长上下文下的质量衰减"，因此 **B2 必须人工构造一个真正的大上下文任务**（大仓库 + 宽 `search_text` 命中），否则这条验收线会对着一个永不进入的分支判"通过"。

（顺带一条新观测：响应体里还有 `provider_specific_fields`，与顶层 `metadata` 一样未解读，v2 不依赖。）

## 0.5 JD 20 项能力矩阵对照

★ 数量取自 `JD总结.docx` 的"出现频率"列。**覆盖度不是自我打分，每一项后面都指向一段可运行的代码或一条可执行的验收线。**

| # | 能力 | 频率 | v1.0 | v2.0 落点（文件 · Stage） | 由谁证明 |
|---|---|---|---|---|---|
| 1 | Agent 系统设计与开发 | ★★★★★ | ● | 增量：durable 会话、并发调度、子 agent 边界 · S12/S13 | B5、A1 不退化 |
| 2 | Python 工程能力 | ★★★★★ | ● | 增量：统计正确性、线程安全、schema 契约 · S8/S9 | B1、并发不变式测试 |
| 3 | LLM/Agent 原理理解 | ★★★★★ | ◐ | 压缩为什么破坏配对就 400、采样确定性与 pass@k、缓存不可测的作用域分析 · S10 | B2、§0.4 实测表 |
| 4 | Tool Calling | ★★★★☆ | ● | 增量：外部工具（MCP）同等校验、并发安全分级、`strict` 的边界 · S14 | B5、外部工具权限测试 |
| 5 | Planning 任务规划 | ★★★★☆ | ◐ | 保持 `write_todos`；**用数据决定**是否升级 `PlanStep` · S9 产出 | B1 的失败分类里"未收尾"占比 |
| 6 | Agent Framework 使用 | ★★★★☆ | ◐ | 交付 ≤200 行的框架等价性对照（我们的 registry+loop vs StateGraph）+ MCP 生态互操作 · S14 | 文档 + `test_mcp_bridge` |
| 7 | **Evaluation / 自动化评测** | ★★★★☆（新趋势） | ○ | **主线**：`eval/` 任务集、批跑、指标、A/B、回归检测 · S9 | **B1** |
| 8 | **Memory 系统** | ★★★☆☆（长期 Agent 关键） | ○ | **主线**：`RepoMap`（ast 符号图 + 预算裁剪）、`.mcc/` 工作记忆、压缩阶梯 · S10/S11 | **B2、B3** |
| 9 | RAG | ★★★☆☆ | ○ | **不做向量**；以符号检索替代，并写清重评触发条件 · S11 | §3.10 触发条件 |
| 10 | 多 Agent 协同 | ★★★☆☆ | ○ | 只做一种形态：`verifier` 子 agent · S15 | B4 |
| 11 | Code Agent 能力 | ★★★☆☆（明显增长） | ● | 增量：任务集换成含真实开源仓库、覆盖跨文件/多阶段 | B1 |
| 12 | Prompt Engineering | ★★★☆☆ | ● | 增量：结构化压缩摘要提示、工具描述 A/B | B1（提示改动必须有 delta） |
| 13 | **Debugging / Observability / Trace** | ★★★☆☆（工程热点） | ◐ | **主线**：span 化、失败模式分类学、`mcc trace` 诊断工具 · S8 | **B4** |
| 14 | **异步编程 / 并发** | ★★★☆☆（Agent Infra 需要） | ○ | 只读工具并发**测过之后砍掉**（§3.5.1：可省 162ms = live 墙钟的 0.011%）。留下的并发证据是「为什么不并」的量化，以及 D15/D16 那两条线程池 vs asyncio 的边界分析 · S12 → Tier 3 | **B5**（未排期，线不重述） |
| 15 | RL / RLHF | ★★★☆☆（加分） | ○ | 不训练；交付 trajectory exporter + SBS 标注闭环 · S15 | 导出条数与字段完整性 |
| 16 | Docker/K8S/部署 | ★★☆☆☆ | ○ | `ExecutionBackend` 抽象 + Docker 实现（K8S 不做） · S13 | **B6** |
| 17 | 分布式系统 | ★★☆☆☆ | ○ | **明确不做**（单机的并发正确性是它的最小前身） | §3.10 理由 |
| 18 | C++ 后台 | ★★☆☆☆ | ○ | **不做**（与本项目方向无关，硬凑只会稀释主线） | §3.10 理由 |
| 19 | 模型训练（SFT/Pretrain） | ★★☆☆☆ | ○ | **不做**；4GB 显存与无标注预算下训练不可行，改为交付数据管道 | §3.10 理由 |
| 20 | VLM/多模态 | ★★☆☆☆ | ○ | **不做**；只登记"加 `ImageBlock` 要改的 5 处"，不留空壳 | §3.10 清单 |

**v1.0：● 5 项 / ◐ 4 项 / ○ 11 项。v2.0 目标：● 11 项 / ◐ 5 项 / ○ 4 项 —— 16/20 有代码证据，剩下 4 项给出可辩护的"不做理由"（判断力也是 JD 第 1 项的一部分）。**

逐项目标可核对：● = 1,2,3,4,5,7,8,11,12,13,14 ｜ ◐ = 6,9,10,15,16 ｜ ○ = 17,18,19,20。v1 的 ● = 1,2,4,11,12 ｜ ◐ = 3,5,6,13 ｜ ○ = 其余 11 项。

---

# 1. Project Overview

## 1.1 目标

把 v1.0 的"可验证闭环"升级成**可持续验证的系统**：任何一次改动（提示词、工具描述、压缩策略、模型）都能在小时级内得到一个带置信区间的成功率差值，并且能被独立判据说出"是变好了还是碰巧"。

三条主线，按依赖顺序：

1. **让数字可信**（S8）—— 先修 §0.3 那三条，再谈任何指标。
2. **让改进可测**（S9）—— 评测集、批跑、回归基线、配对 A/B。
3. **让 agent 能干更大的活**（S10–S15）—— 上下文压缩、仓库地图、并发、长任务恢复、外部工具、子 agent。**每一项都必须在自己交付后的下一批评测里报 delta，报不出 delta 的按 §7.4 砍掉。**

## 1.2 核心能力（v2 必须交付）

1. **批量评测与回归检测**：`mcc eval --tasks eval/tasks --repeats 3`，无人值守、可中断续跑、每条数字可追到某个 trace 的某个字段。
2. **上下文干预**：三级压缩阶梯，触发即生效，配对绝不破坏。
3. **仓库工作记忆**：ast 符号图 + 预算裁剪的 `RepoMap`，落盘缓存、按文件失效，`.mcc/` 记命令与约定笔记。
4. **只读并发执行**：同一轮里多个 READ 调用并行，副作用调用保持顺序。
5. **可恢复会话与回滚**：session 快照 + `mcc resume` + shadow-git 检查点 + `/undo`。
6. **可诊断的观测层**：span 化轨迹、失败模式分类学、`mcc trace` 时间线诊断。
7. **受控的生态接入**：MCP 远端工具与 Skills 提示包，都走同一套校验与权限门。

## 1.3 验收线（定义 v2 "完成"）

B1 是 v2 的生死线，其余五条都建立在它的输出上。**v1 的 A1–A4 全部继续有效，且每次 v2 合并都要重跑（它们成为回归集的一部分）。**

| 编号 | 验收场景 | 判定方式（不看模型自述） |
|---|---|---|
| **B1** | 24 个任务 × 3 次重复**无人值守**跑完，中途 `Ctrl-C`/断电后能续跑，产出一份带置信区间的报表；报表里每个数字都能指到 trace 字段 | 判据独立执行 pytest / 行为探测（沿用 v1 的 `Check` 契约）；续跑用"已完成 run 不重复计数"断言；用 `test_metrics_have_producers`（§3.1）证明无孤儿指标 |
| **B2** | 构造一个必然超预算的长任务（预算 = §3.3 拆分后的 `TOKEN_BUDGET=32000`；**v1 最长轨迹只有 12,185，所以这条必须新造任务，不能拿现有 demo 充数**）：压缩关闭时失败，开启时成功 | 压缩后发出 **0 次**因配对破损导致的 400（`test_compact_preserves_pairing` + 真实端点各 5 次）；摘要里必须能 grep 到本轮已改文件名 |
| **B3** | 同一任务集，`RepoMap on` vs `off` 的配对比较 | A1 类任务 `steps_to_success` 中位数下降 **≥ 20%**，且 `context_peak` p95 上升 **≤ 15%**（map 自身字符计入估算）；两条同时成立才算数 → **实到 2026-09-22：未达成。第二句 ✓（+5.4%），第一句 ✗（6.0→6.0，降 0%）。36 次真模型运行、机制层 7/7 全绿，所以结论是"效应不存在"而不是"实验没做成"；线不动，重测计划见 §7.3-5。全表与逐题配对见 §3.4.2** |
| **B4** | 给 3 条真实失败轨迹，`mcc trace` 说清失败模式 | 输出的模式标签与人工判读一致（人工核对表进仓库）；渲染耗时 < 60s |
| **B5** | 只读并发不改变语义 | 墙钟 p50 下降 **≥ 15%**；**零**次"同一 ASK 问两遍或漏问"；回填顺序与 `tool_calls` 声明顺序逐位一致（测试钉）；trace 无交错坏行 → **实到 2026-09-22：未排期。** 动手前先测（§3.5.1）：46 次 live 运行、261 个工具轮，按线程池无限大的上界只值 **162ms = 运行墙钟的 0.011%（p50 0.009%）**，与 15% 差三个数量级；fake 引擎（工具即 99.4% 墙钟）也只到 p50 0.127%。按 §3.5 自己的 <20% 砍单条款推入 Tier 3，**线保持原样不重述** |
| **B6** | 同一任务在 local 与 docker backend 上判定一致 | 两后端各跑同一子集，`verdict` 与 `Check` 列表完全一致；docker 不可用时**明确降级并在报表标注**，不许静默换后端 → **实到 2026-09-22：达成（判定层），沙箱层未测。6 题 × 2 次 = 12 格，verdict / 终止原因 / `Check` 三元组 / 工具序列 12/12 全同，8 条前提全绿；但本机没有 docker，docker 臂用的是会真执行命令的替身，所以文件/网络隔离与镜像内容一次也没测过（`container_isolation_tested: false`）。全表与边界见 §3.6.2** |

## 1.4 决策记录（做了什么选择、放弃了什么）

| # | 决策 | 采纳 | 放弃的选项与代价/理由 |
|---|---|---|---|
| D9 | v2 定位 | agent 的 **harness**（度量、可靠性、预算控制） | 放弃"再加交互/前端/GUI 功能"。理由：v1 的能力叙事已经够，缺的是让人信服的度量层；而度量层恰好是目标岗位的职责原文 |
| D10 | 顺序 | **度量先行**：S8 先修指标产地，S9 先建评测，然后才允许"改进"类 Stage 进入 | 放弃"先做压缩和记忆这些有意思的，再回头补测试"。代价：前 22h 对外看不出任何新能力。理由：否则 B3 这类"改进"只能靠感觉，而感觉在 §0.3 之后已被证明不可信 |
| D11 | 任务集结构 | 抄 SWE-bench 的实例形状：`repo + base_commit + instruction + FAIL_TO_PASS + PASS_TO_PASS`，每个任务自带 `verify` 脚本 | 放弃"手写 20 个小练习"（考自己出的题，测出 100% 不奇怪）。**PASS_TO_PASS 是防"改测试作弊"的关键**：v1 只有"基线哈希不变"这一条，拿到真仓库后不够 |
| D12 | 统计口径 | 配对比较 + 精确检验（McNemar / Wilson 区间），报点估计与区间，**不报单一均值** | 放弃"平均分"与 t 检验。理由：n=24 且结果是 0/1，均值与正态假设都不成立；同一任务两配置的差值才是有效信息 |
| D13 | 压缩算法 | 三级阶梯：L1 就地省略老工具结果 → L2 结构化摘要替换历史前段 → L3 拒绝并说明 | 放弃"一步 LLM 全量总结"作为首选。理由：摘要会丢"我已经改过哪些文件"，agent 于是重复劳动；L1 零额外调用、零风险，先吃满它的收益 |
| D14 | 前缀缓存 | **不动**。todos 继续留在 `system`（`loop.py:277`） | 放弃"为了 prompt cache 把易变内容移出 system"。理由：本端点拿不到 `cached_tokens`（§0.4），收益不可测；而移进 messages 会让历史单调增长 —— **在一个测不到的收益上用可测的代价换，不做**。重评条件：换到暴露缓存字段的端点 |
| D15 | 并发实现 | `ThreadPoolExecutor`，仅 `RiskLevel.READ`，上限 `max_parallel_reads` | 放弃 asyncio 重写。理由：真实瓶颈是文件与 subprocess 的 IO 并发，线程池拿到 90% 收益；asyncio 化要改 `BaseTool.run` + 7 个工具实现 + registry + loop + 适配器共 **11 处签名**，外加 25 条循环测试全改成 async，而它给不了任何线程池给不了的（`BaseTool.run` 是同步的，`ToolRegistry` 不感知异步）。**"知道什么时候不用异步"比"会用异步"更接近 JD 第 14 项想筛的人** |
| D16 | 并发与权限 | ASK 判定必须在**进入线程池之前**串行完成（用 `gate.check()`），被拒调用直接出结果 | 放弃"执行时各自申请权限"。理由：三个线程同时 `input()` 是不可用的 UX，且 `PermissionGate` 的会话级授权状态会被竞态污染 |
| D17 | 沙箱 | `ExecutionBackend` 协议 + `DockerBackend`；不可用时显式降级到 local 并在报表标注 | 放弃 WSL2。理由：Windows/WSL 的路径语义与文件系统差异会直接污染判据（`/mnt/d` 上 `os.path.realpath` 与符号链接行为不确定），而 §2 的路径锁正依赖它 |
| D18 | 长任务恢复 | session 快照（messages + todos + counters + turn）+ `mcc resume` + 按 `tool_call_id` 幂等去重；检查点用 shadow git | 放弃"每次写前复制整个工作区"。理由：真实仓库副本 GB 级，磁盘和时间都撑不住；shadow git 只存差异 |
| D19 | MCP | 做 bridge，但外部工具**降级为不信任输入**：命名空间前缀 `mcp__srv__tool`、默认 ASK、`BaseTool.validate()` 一样不少 | 放弃"信任远端声明的 risk_level"。理由：远端把 `bash` 标成 read-only 就是权限门失效 |
| D20 | Skills | 只做"按需注入的提示词包 + `load_skill` 工具" | 放弃"skill 自带脚本运行时"。理由：那等于给 bash 换个入口，权限模型不会因此变强 |
| D21 | 多 Agent | ~~只做 `verifier` 一种形态~~ → **一种也不做**，全部留在 Tier 3 | 放弃 planner/worker/critic 三件套（v1 D3 的结论继续有效）。**v2 用 B1 的失败分类复核**：若"未收尾/自我确认过早"占比 > 20% 再回来讨论 critic → **复核已完成（2026-09-22，§3.8.1）：live 1/75 = 1.3%，D21 点名的 fake 批自己 12/72 = 16.7%，都不过线**；且命中的 run 全部判 `pass`（原判据记行为、不记代价）。所以连"只做一种形态"这个折中也没有被触发 |
| D22 | RAG | 不做 embedding；`RepoMap` 的 ast 符号图就是检索结构 | 放弃向量检索。理由：代码语义检索在 <10 万行仓库上召回不稳，还引入 embedding 供应商。重评条件：任务集出现单文件 > 5 千行 或 `search_text` 空命中率 > 25% |
| D23 | RL / 后训练 | 交付 trajectory exporter：trace → `(context, action, reward)` | 放弃自己训模型。理由：4GB 显存 + 无标注预算，训练是纯负收益的时间黑洞。**参与这个话题的正确方式是生产别人要用的数据**，这恰好也是 JD 职责 4 的"数据管道" |
| D24 | VLM / 多模态 | 不实现，也不留空壳 | 放弃"先加个 `ImageBlock` 占位"。理由：空壳类型会让 `ContentBlock` 联合、`as_message`、适配器 wire、`wire_chars`、环境事实五处出现"看起来支持其实没支持"的假信号。改动清单写在 §3.10 |
| D25 | 分布式 / C++ | 不做 | 单进程单机没有分布式可谈。§3.5 的竞态分析与线程安全不变式是这一项的**最小前身**，宁可把这一小块做扎实 |
| D26 | 框架 | 不引入 LangChain/LangGraph；交付等价性对照文档 | 放弃"用 LangGraph 重写一遍循环"。理由：JD 第 6 项要的是**理解框架为什么这样设计**，不是会在简历上写框架名。对照文档能同时回答"为什么不用 LangGraph"和"StateGraph 的 checkpoint 思想我们怎么实现（§3.6）" |
| D27 | 判据与实现的先后 | 一条设计只要自带**可测的**砍单条件，就先花 1h 测它，测不过就不写实现；测的结果连口径缺陷一起写回设计（§3.5.1、§3.8.1 是同一件事的两次） | 放弃"按 SPEC 顺序把每阶段做完"。理由：v2 的产出是"我知道哪些不该做、并且拿得出数"，不是行数。两次砍单（S12 并发、S15 verifier）省下的 9h 全部转给了 §3.9 与 S13，而省下的实现成本不是主要收益 —— **主要收益是发现了判据本身写错的地方**（S12：B5 的 15% 挂在"读盘很贵"的隐含前提上；S15：D21 的分子记行为不记代价）。一个不动手的探针没有这个副作用 |

---

# 2. System Architecture

## 2.1 v2 增量（虚线框为新增，实线框为 v1 既有）

```
                          ┌──────────────────────────────────────────────┐
   用户 ─────────────────▶│ cli/   REPL · build_session() 唯一装配点      │
                          │        + 新子命令 eval / trace / resume / sbs │
                          └───────┬──────────────────────┬───────────────┘
                                  │ run(task)            │ 批量调度
              ┌───────────────────▼───────────────┐   ┌──▼──────────────────────────┐
              │ agent/loop.py                     │   │ eval/            ★ S9       │
              │  Ask → Act → Observe              │   │  tasks/ (24 任务+verify)    │
              │  ├─ 止损闸（turn/token/pressure） │   │  runner  并发K·断点·成本闸  │
              │  ├─ 压缩闸 ← ContextManager.compact│   │  metrics 12 指标·Wilson区间 │
              │  └─ checkpoint ← Backend           │   │  compare 配对 A/B + 回归    │
              └──┬──────────┬─────────┬────────────┘   │  sbs     并排标注视图       │
      ask(model) │          │ invoke  │ memory         └──┬──────────────────┬──────┘
        ┌────────▼────┐  ┌──▼─────────▼─────────┐         │ 消费             │ 消费
        │ llm/        │  │ tools/ + scheduler/  │         ▼                  ▼
        │ chat/compl. │  │  ★ READ 并行         │   infra/trace.py v2   demos/（复用判据）
        │ (responses  │  │  ★ ExecutionBackend  │   span 化 · 失败分类 · mcc trace
        │  = V3 备选) │  └──────────────────────┘         │
        └─────────────┘                            ┌──────▼──────────────────────────┐
              ┌──────────────────────────────┐     │ memory/  ★ S11                  │
              │ agent/context.py v2  ★ S10   │     │  repo_map.py（ast 符号图+预算） │
              │  L1 elide → L2 summarize     │     │  store.py（.mcc/ 缓存与笔记）   │
              │  配对不变式（硬约束）        │     │  notes.py（命令/约定）          │
              └──────────────────────────────┘     └─────────────────────────────────┘
              ═══════════════════════════════════════════════════════════════════════
              ext/mcp.py（远端工具→BaseTool）· ext/skills.py（提示包）· S14
              agent/subagent.py（verifier 形态，父分配 token 配额）· S15
              eval/export_rl.py（trajectory 导出：trace+判据 → (s,a,r)）· S15
```

## 2.2 质量飞轮（这是 v2 真正的架构，不是一堆功能）

```
 跑（runner 批量执行）
   │  产出 trace + verdict
   ▼
 量（metrics：成功率/区间/成本/失败模式分布）
   │  失败模式告诉你该改哪一层
   ▼
 诊（mcc trace：单条轨迹时间线 + 热点 + 模式标签）
   │  改一处：提示词 / 工具描述 / 压缩策略 / 工具集
   ▼
 比（compare：同任务集配对 A/B + 回归基线 diff）
   │  delta 显著 → 合入并更新基线；不显著/退化 → 砍掉
   ▼
 标（sbs 并排视图，人工给两个结果排序 → labels.jsonl）
   │  积累到阈值后可导出
   ▼
 数据（export_rl：(context, action, reward) —— 交给别人的训练管线，我们不训）
```

**这个环是 v1→v2 的全部差异。** v1.0 只有环的第二步（跑 + 一份人工看的证据文件）；改东西靠手感。v2.0 的每一项能力都必须挂在这个环上交付，否则它没有验收方式。

## 2.3 结构约束

v1 的两条继续有效，v2 再加三条：

1. **（v1）状态只有一处**：`Agent.messages` 是对话的唯一事实来源。→ **v2 补充**：`.mcc/` 与 session 快照都是**派生物**，永远可以从 messages + trace 重建；任何"以磁盘状态为准"的设计一律驳回。
2. **（v1）依赖单向向下**：`cli → agent → (tools | llm) → messages/config`。→ **v2 补充**：`eval/` 在 `cli` 之上（它 import 所有人，没人 import 它）；`memory/` 只能被 `agent/` 与 `cli/` 使用，`tools/` 不得 import `memory/`（否则又出现工具反向依赖 agent 层）。
3. **报文合法性由类型系统外移为不变式**（v2 新增）：任何修改 `messages` 的代码路径（压缩、resume、子 agent 回填）都必须能通过对 `assert_pairing(messages)` 的检查。这条是 §3.3 全部设计的地基。
4. **并发不改变可观察顺序**（v2 新增）：并行只允许发生在"无副作用且互不可见"的调用上；结果序列、trace 写入顺序、事件发射顺序必须与串行实现逐位一致。做不到就不要并发。
5. **一切指标必须有生产者**（v2 新增）：`summarize()` 与报表里出现的每个键，必须能指到一处写入它的代码。由 `test_metrics_have_producers` 机器保证（§3.1）。E1 就是这条约束缺失的代价。

## 2.4 依赖预算

| 依赖 | 用途 | 状态 |
|---|---|---|
| `httpx`、`python-dotenv`、`rich`(可选) | 现状 | 保留 |
| 标准库 `ast`、`concurrent.futures`、`sqlite3`(可选)、`difflib`、`statistics` | RepoMap、并发、导出、统计 | **v2 主体全部零新依赖** |
| `mcp`（官方 SDK） | MCP bridge | 可选 extra：`[mcp]`。缺失时 `ext/mcp.py` 不注册工具，CLI 明确提示 |
| `docker` SDK **或** `subprocess docker run` | 沙箱后端 | 优先用 subprocess（少一个重依赖，且 Windows 上 Docker Desktop 的 CLI 比 SDK 更稳） |
| `tree-sitter` | 跨语言符号提取 | **不做**（D22/§3.10）。Python-only 用 `ast` 足够，且零依赖 |
| pydantic / LangChain / LangGraph / openai SDK | — | 仍然禁止（v1 §6.1 继续有效） |

**新增可选依赖数量上限：2。** 每加一个依赖，就要在 §7 的砍单顺位里为它登记一次"能不能退回标准库实现"。

---

# 3. Module Design

## 3.1 度量修补 — `infra/trace.py` v2 + schema 契约（S8，8h）

**职责**：在加任何新指标之前，先让现有指标全部真实可追。这一步不产生新能力，但它决定后面 72h 的结论能不能被引用。

**改动清单**（✅ = 已按此落地；⚠ = 落地时与规格不同，原因写在后面）

```python
# ✅ 1) 补 E1：逐调用重复计数（与停滞检测的整组口径分开）—— loop.py `_run_tools` 末尾
key = (call.name, _key_of(args))          # ⚠ 不是 _digest_key：签名要稳定，args 排序后取
if key == self._last_call_key:            #    原文（不截断），而落盘的 args 是 200 字摘要
    self.state.repeated_calls += 1
self._last_call_key = key

# ✅ 2) 补 E2：整组停滞命中计数（组签名命中处，判定 STALLED 之前一处）
self.state.stalled_groups += 1     # 只有真正命中才加，不代表终止

# ⚠ 3) 孤儿指标检测器（tests/test_trace_contract.py）—— 实现比原规格严格一档：
#    grep `state.<key>` 会被 `redundant_calls += 0` 这类噪声骗过，所以改成 AST：
#    解析 src/ 全部 .py，收集**非平凡**属性写入路径（`x = 0` / `x = []` / `x = Usage()`
#    这类纯初始化不算产地），再与 AgentState.snapshot() 的键名取差集。
def test_metrics_have_producers():
    """snapshot() 的每个键都必须有一条非平凡写入路径。"""

def test_trace_schema_snapshot():
    """两次真会话（一次磕绊收尾、一次模型故障）产出的 {kind: sorted(keys)}
    与 tests/schema_v2.json 逐字段比对。重生成：MCC_REGEN_SCHEMA=1。"""

def test_every_kind_the_code_emits_is_pinned():
    """样例轨迹只走到它走到的 kind —— 再用 AST 扫出代码里所有 _trace("...") 的
    字面量 kind，要求 ⊆ 快照。新增 kind 而没同步快照就是红，不是"没人跑到就没事"。"""

def test_live_and_offline_classification_agree():
    """同一份轨迹：循环内 classify() 的标签 == 事后 facts_from_records() 的标签。
    两边不一致就说明有哪个判定只存在于内存里，没随记录落盘。"""
```

**trace v2 事件 schema**（★ 为 v2 新增字段；已有 kind 一个不改名，只加字段。
下表是 `tests/schema_v2.json` 的**人读版**，两处由 `test_trace_schema_snapshot` 钉在一起；
每条记录另带公共信封 `seq / ts / session / kind / trace_id / schema_version`）

| kind | 字段 |
|---|---|
| `session_start` | model, tools[], config(脱敏), **span_id**, **system_prompt_hash** |
| `run_start` ★ | **run_id**, **task_id**, user_input_chars, **span_id** |
| `turn_start` | turn, message_count, **est_tokens**, **span_id**, **parent_span_id** |
| `llm_request` ★ | turn, message_count, tools_count, est_tokens, **prefix_hash**, **span_id**, **parent_span_id** |
| `llm_response` | turn, stop_reason, blocks[], usage{prompt,completion}, **latency**, **span_id**, **parent_span_id**, **id_repairs** ★S10 |
| `context_compact` ★S10 | turn, level(elide\|summarize), before_est, after_est, saved_est, elided_blocks, dropped_blocks, pairing_ok, summary_tokens, **summary_files**, **note**, span_id, parent_span_id |
| `context_refuse` ★S10 | turn, **line**(hard_fuse\|l3_refuse), threshold_tokens, est_tokens, budget_tokens, hard_limit_tokens, pressure, **ladder_enabled**, span_id, parent_span_id |
| `tool_call` | turn, name, args{}, ok, output_chars, latency, **span_id**, **parent_span_id**, **tool_use_id**, **risk**, **verdict**, **drops_assert** |
| `permission` | turn, tool, decision, rule_hit, **span_id**, **parent_span_id** |
| `todo_update` | turn, items[] |
| `error` | turn, layer, message, **span_id**, **parent_span_id** |
| `failure_mode` ★ | turn, **mode**, **why**, **prescription** |
| `repo_map` ★S11 | turn, **modules_found**, **modules_parsed**, **unparsable**, **listed**, **omitted**, **chars**, **est_tokens**, **token_cap**, **focus[]**, **reasons{}**, **rebuilt**, **from_cache**, **note** |
| `backend` ★S13 | turn=0, **requested**, **backend**, **isolated**, **shell**, **checkpoints**{enabled, ready, git_dir, snapshots, restores, failures, degraded, last_rev, excluded[]}, **degraded**, **note** |
| `checkpoint` ★S13 | turn, **rev**, **tool**, **ok**, **backend**, **note**（`ok=false` 时 note 就是降级原因） |
| `session_snapshot` ★S13 | turn, **ok**, **writes**, **done_calls**, **last_rev**, 失败时 **reason** |
| `session_replay` ★S13 | turn, **name**, **tool_use_id**, **why**（重放**不**产生 `tool_call` 记录，见 §3.6.1） |
| `run_end` | termination, state.snapshot()（含 **repeated_calls** / **stalled_groups**）, todos[], **failure_modes[]**, **cost_est**, **wall_ms**, **span_id** |

`backend` 的字段集合是**快照里那份 = LocalBackend 交出来的形状**。`DockerBackend.stats()` 另外带
`image / network / available / availability_reason / launches / errors` 六个键，而它**没有** `shell` ——
两个后端的字段集本来就不相同，快照钉的是"被真实产地跑出来的那一支"，不是交集也不是并集。
这不是漏钉：`isolated` 与 `degraded` 才是 B6 要跨臂对比的量，镜像名与启动次数只在单臂内有意义。

两条落地时新增的口径，规格原文没写到、但必须记着：

1. **`tool_calls` 有两个意思，v1 用同一个名字两头指**。现在 `run_end.tool_calls` 是
   "模型发起了几次"（含被拒与虚构工具名），报表另出 `tool_executed` = `tool_call` 记录数。
   `permission_starved` 的分母统一用发起数，实时与离线同一个分母。
2. **虚构工具名也要落一条 `permission(decision="deny", rule_hit="unknown-tool")`**。
   否则离线侧看不见这次发起，两个口径的差值在实时与事后两侧对不上（`test_live_and_offline_classification_agree` 会红）。

这段的实到状态（S13 落地后复核）：`checkpoint` 与 `backend`/`session_snapshot`/`session_replay`
**已经进快照**（§3.6.1）；`context_op` 这个名字没有落地，S10 实际交出的两个 kind 是
`context_compact` 与 `context_refuse`，而"`pairing_ok` 为 `false` 的记录数恒等于 0"这条不变式
成立（B2 的 12 条判据里有它，`eval/results/b2-compact-ab.json`）；`tool_call.parallel_group_id`
与 `permission.ask_serialized` **仍然没有产地** —— S12 被数据砍进 Tier 3（§3.5.1），
所以这两个字段留着不钉：给一个不存在的实现预留字段形状，就是在文档里假装并发已经做完了。
`error` 的 `kind` 字段并入 `layer`，不再单列。

**字段命名对齐 OTel GenAI 语义约定**（`gen_ai.operation.name` / `gen_ai.tool.name` / `gen_ai.usage.input_tokens` 一类），落地方式：`infra/otel.py` 只做**导出映射**（`to_otel(record) -> span dict`），埋点代码不引入 OTel SDK。这样"能接入 Jaeger"是导出器的一层翻译，而不是全项目的架构前提。OTLP 实际导出放 Tier 3。

**`mcc trace` 诊断工具**（JD 职责 3"Agent debugging 工具"的直接答案）

```bash
mcc trace <session-id>              # 时间线：每轮上下文估算/回复 token/工具调用/延迟
mcc trace <session-id> --hot        # 热点：最贵 3 轮、报错最多的工具、重复调用簇
mcc trace <session-id> --why-failed # 失败模式标签 + 证据（哪一轮、哪个调用、为什么这么判）+ 处方
mcc trace --latest                  # 最近一次会话，无需知道 id（省略 session 时即此）
mcc trace <id> --json               # 机器可读汇总（就是 summarize_records 的那一份）
mcc trace --file demos/traces/giveup.fake.jsonl   # 不依赖 TRACE_PATH
mcc trace <id> --limit 10           # 时间线最多 10 轮
```

三条实现约束（`cli/trace_cmd.py` 的模块注释里也写着）：`trace` 在 `main()` 最前面分流，
不装配 Agent、不碰模型 —— 排查一次烧了 8 万 token 的会话时不该再依赖 LLM 配置可用
（配置读不到就直接退出码 2，并说明"日志路径也来自这份配置"）；输出不用 `rich`，
裸终端与 CI 日志里都要能读；`--why-failed` 一条规则都没命中时**必须**留一句
"这只说明规则看不出来，不代表任务做对了"，不能沉默。

**失败模式分类学**（`infra/failure.py`，规则式而非模型式 —— 每条规则都要能在一条真实 trace 上被人眼复核）

| 标签 | 触发规则（✅ 为 S8 落地后的实际判据，与初版的差别都来自 B4 的人眼核对） | 对应处方 |
|---|---|---|
| `path_guessing` | ✅ 整轮里 ≥3 个**不同路径**的 `read_file` 失败（被拒的读不算猜）。不要求连续、不要求换目录 | 仓库地图缺失 / 提示词里"先 find 再读"约束太弱 |
| `context_growth` | ✅ 单轮 est_tokens 增幅 > 前序涨幅中位数（**至少 3 个样本**，取最近 5 个）的 3 倍、**涨幅 ≥ 2000 tokens**、**且上一轮工具输出（`output_chars`）够解释一半以上** | 工具输出未截断；某次 `search_text` 命中过宽 |
| `no_verification` | ✅ `COMPLETED` 且**执行过写操作**却没有一次已执行的 `run_tests`/`bash` | `SELF_DEBUG_PROMPT` 不生效 —— v1 A4 判据的行为学扩展 |
| `self_confirm` | ✅ `COMPLETED` 且最后一次验证 `verdict=red`（红过又跑绿 = 正常收敛，不贴） | 模型无视退出码，纯靠叙述收尾 |
| `test_gaming` | ✅ `edit_file` 落在 `tests/` 且**检查点变少**（数 `assert` / `self.assertX(` / `pytest.raises(` 的个数，随记录落盘为 `drops_assert`） | 权限/提示/判据三层都要复查（v1 §10-7 的扩展） |
| `thrashing` | ✅ `stalled_groups > 0` | 换假设的提示不够具体 |
| `permission_starved` | ✅ `denied_actions / 发起数 > 0.4` 且 `denied ≥ 2` 且**没有** `COMPLETED` | 模式选错，不是模型错 |
| `budget_exhausted` | ✅ `MAX_TURNS` 且（`todos` 未完成项 > 50% **或**整轮没写过任务清单） | 任务切得太大，或压根没先列计划 |

**B4 的实际收获（四条，全部来自人眼读真实轨迹，纸上想不出来）**：

1. `readonly-qa` 的两次跑（fake 与 live）都被 `no_verification` 贴了标签 —— 可它是只读问答，
   验收标准里没有"跑测试"这项；`readonly-qa.live` 又被 `permission_starved` 贴了标签 ——
   可它跑在**故意选的**只读模式下，"被拒"正是闸门在按设计工作，而且任务做完了。
   收窄后的判据分别加了"动过手"和"没做成"两个前提。
2. `path_guessing` 原来是"连续 ≥2 次且换目录"，`b4-live/bug-hunt.live` 直接证伪：5 次读失败里
   4 个不同路径，全带同一个多余的 `bug-hunt/` 前缀，中间还被第 2 轮成功的 `find_files` 打断
   —— 两个条件各漏一半。
   改成数"整轮里几个不同路径没读到"后，同一条 trace 从漏报变命中，只猜错两次就自己纠正的
   `readonly-qa.live` 仍然不贴；连 v1 的 `bug-hunt` 旧轨迹（3 个 `src/` 下的路径）也被新判据抓到了，
   而它当时是漏报的。
3. `context_growth` 原来只看涨幅，两处冤枉人（数字取自当前留档的 trace，可对
   `demos/traces/{codegen,red-tests}.live.jsonl` 逐轮重算）：codegen.live 第 5 轮涨 7,651 tokens
   （≈26.8k 字符），可上一轮的工具输出只有 12,997 字符 —— 大头是模型自己 `write_file` 写进去的
   长测试文件，判成"输出没截断"是冤枉；red-tests.live 第 3 轮涨 2,940 tokens、也确实超过了基线
   中位数（792）的 3 倍，但当时基线只有 1 个样本，而那只是一次读 5 个文件的正常探索。
   于是加上"上一轮 `output_chars` 要够解释一半以上涨幅"与"基线至少 3 个样本"两条前提。
   代价是：旧轨迹没有 `output_chars` 时这条规则彻底不参与（记进盲区列）。
4. 已知**抓不到**的形状（读自更早一批 live codegen 的第 6 轮 —— 那次的 trace 已被后续重跑覆盖，
   所以这个 diff 现在是手抄进测试当钉子的，不在 `demos/traces/` 里）：
   `assert evaluate("-1 + 3") == 2`
   被改成 `assert evaluate("0 - 1 + 3") == 2` —— 检查点数量没变、被测输入被换掉了，
   即"躲开一个失败用例"而不是"删掉一个断言"。纯正则口径区分不了它和"把写错的期望值改对"
   （同批第 2 次编辑就是后者），所以留给 S9/S12 的行为探测，不在分类学里硬造规则。
   那个 diff 现在以"断言规则**不**命中"的形式钉在
   `tests/test_failure_rules.py::test_drops_assertions_only_counts_test_files` 里。

`drops_assert` 与 `run_end.failure_modes` 是**写日志时算好落盘**的，规则改了旧 trace 不会重算；
`mcc trace` 因此会把"当时落盘"与"现在算出"不一致这件事显式印出来，而不是沉默地给一套新答案。

核对过程与结果由 `python scripts/b4_label_check.py` 生成 `demos/results/failure-labels.md`，
其中"独立判据"列来自 demo 在工作副本里跑的 pytest/行为探测，与分类器不是同一份实现；
没写过人工期望值的 trace 直接记 ✗（`tests/test_failure_rules.py` 里同有一条测试盯着覆盖）。
当前覆盖 `demos/traces/` 下全部 16 条轨迹（5 fake + 4 live + 3 条 b4-live 失败批次 +
4 条 v1-baseline），脚本退出码 0 才算 B4 前半过关。

同一份核对表也记着**盲区**：`v1-baseline/` 的 4 条旧轨迹里没有 `turn_start.est_tokens`、
`tool_call.output_chars` 与 `tool_call.verdict`，所以 `context_growth` / `self_confirm` /
`test_gaming` 在那批数据上**不是判对了，是没参与** —— 表里"盲区"列逐条写明是哪几条没参与。
这就是 §3.1 快照契约存在的理由。

## 3.2 Evaluation — `eval/`（S9，14h · **B1 生死线**）

**职责**：把 v1 的 `demos/run_demo.py` 里已经存在且被 18 个测试盯着的判据契约（`Check` / `Outcome` / `Context` / `Demo.judge`）抽成可批量执行的评测层。**不是新写一套判据。**

v1 已有的资产（直接复用，不要重写）：

- `Check(name, ok, detail)` + `Outcome.verdict()`（`run_demo.py:77-107`）—— 判据的最小单元。
- `judge_x(ctx) -> list[Check]` 的函数形状 —— 一个任务 = 一个 judge。
- `prepare(demo, work_root)` 的隔离副本机制（`run_demo.py:521`）。
- "判据不许把改动写回考题"的 pristine 测试、"脚本不得自报判据数字"的检测器测试。
- `summarize(path)` / `summarize_records(records)` —— 所有客观数字的唯一来源（前者是后者的读文件壳）。

**数据结构**

```python
@dataclass(frozen=True)
class TaskInstance:
    id: str                      # "swe-duration-day-carry" | "fix-taxed-base"
    source: TaskSource           # REPO(url, base_commit) | VENDORED(fixture_path)
    instruction: str             # 给模型的一句话，禁止透露修法
    setup: str                   # 建副本、还原 bug、装依赖
    fail_to_pass: list[str]      # 修好后必须转绿的用例名 ← 成功判据
    pass_to_pass: list[str]      # 必须保持绿的用例名     ← 防回归判据
    verify_cmd: str              # 唯一真相：退出码 0 即成功；不读 result.text
    tags: tuple[str, ...]        # ["bugfix","cross-file","multi-stage"]
    max_turns: int = 20
    token_ceiling: int = 120_000
    difficulty: Literal[1, 2, 3] = 1

@dataclass
class RunRecord:
    task_id: str; repeat: int; engine: str            # engine: "live" | "fake"
    trace_path: Path; verdict: Literal["pass","fail","error"]
    metrics: dict[str, float]                          # 全部来自 summarize()
    failure_modes: list[str]                           # 来自 §3.1 分类器
    wall_ms: int; cost_est: float | None               # 没配单价就是 None，不是 0
    taskset_sha: str                                   # 任务集内容哈希，防"考题被改"

@dataclass
class BatchReport:
    runs: list[RunRecord]
    summary: dict[str, Any]                            # 按 §3.2 指标表
    per_tag: dict[str, dict[str, float]]
    regressions: list[Regression]                      # 与基线比对的差值
```

**`FAIL_TO_PASS` / `PASS_TO_PASS` 是这一节最重要的设计**，不是照抄名词。它把 v1 判据的一个真实缺口补上：v1 只能检查"基线测试内容逐字节不变"或"只增不删"，这防的是**篡改考卷**；而 `PASS_TO_PASS` 防的是**改坏了别处** —— Demo 2 里模型若把 `format_duration` 的分支改对但顺手让小时换算错位，v1 判据看不出来（它只跑一次全量然后看退出码，粒度是"仓库级"而非"用例级"）。用例级白名单能把这类情况精确钉住。

**runner 设计要点**

```python
class EvalRunner:
    def __init__(self, *, tasks: TaskSet, out: Path, repeats: int = 3,
                 workers: int = 1, engine: str = "live",
                 budget_tokens: int = 4_000_000, resume: bool = True) -> None: ...
    def run(self) -> BatchReport: ...
```

1. **`workers=1` 是默认值，不是妥协。** 并行打同一端点会让限流噪声混进"配置 A vs 配置 B"的比较里。先串行拿到干净数据，再讨论并行。
2. **续跑**：`out/manifest.jsonl` 记录每个完成的 `RunRecord`；重启时跳过已存在的 `(task_id, repeat)`。B1 的"断电可续"就是靠它。
3. **成本闸**：`budget_tokens` 是整批的硬上限（不是单任务的 `token_ceiling`）。到线即停并标 `ABORTED_BATCH`，报表如实写明跑了多少。**没有这道闸，72 次 live 跑可能一夜之间烧掉预算的两倍。**
4. **失败关闭继承 v1**：批跑没有人类确认者，`confirmer` 恒返回 `Answer.NO`（`run_demo.py:576` 已是这个语义），因此评测默认用 `AUTO` 模式 + 路径锁，**绝不开 `ASK`**。
5. **任务集版本化**：`taskset_sha` = 任务目录内容哈希，进入每条记录与基线文件。考题被改过，所有旧基线自动失效。
6. **判据独立执行**：与 v1 同一条铁律 —— 判定脚本不读 `result.text` 来判成功（`judge_readonly` 这类"必须读答复"的判据例外，且例外要在代码里注明为什么）。

**指标表（v1 §3.7 的扩展，★ 为新增）**

| 指标 | 定义 | 产地 |
|---|---|---|
| `pass_at_1` | 首次重复通过率 | RunRecord.verdict |
| `pass_at_k` ★ | k 次重复里至少一次通过（诚实标注：这是"多次尝试"能力，不是单次能力） | 同上，按 task_id 分组 |
| `success_rate_ci` ★ | Wilson 95% 区间（n=24 时点估计几乎必然误导） | BatchReport.summary |
| `steps_to_success` | 成功用例轮数中位数（不报均值） | trace |
| `tool_error_rate` | `is_error` / 总调用（衡量工具描述质量） | trace |
| `repeated_call_rate` ★ | 逐调用同名同参连续重复占比 | `state.repeated_calls`（S8 补） |
| `stalled_group_rate` ★ | 命中整组停滞的轮次占比 | `state.stalled_groups` |
| `context_peak_p95` | 单任务最大 prompt tokens 的 p95 | trace |
| `tokens_per_success` ★ | 总 token / 通过数（**真正的成本指标**；不是"平均 token"） | RunRecord |
| `wasted_output_ratio` ★ | 被 `_cap` 省略的字符 / 工具输出总字符（`loop.py:366`、`workspace.py:92`） | trace `tool_call.output_chars` 需加 `raw_chars` |
| `denial_rate` ★ | `denied_actions` / 总调用 | trace |
| `failure_mode_dist` ★ | 各失败模式占比 | §3.1 分类器 |
| `wall_time p50/p95` | 端到端耗时 | RunRecord |

**回归检测与 A/B（JD 职责 2 的字面实现）**

```bash
mcc eval --tasks eval/tasks --repeats 3 --tag eval/baselines/main-6a73e32   # 建基线
mcc eval --tasks eval/tasks --repeats 3 --compare eval/baselines/main-6a73e32
mcc eval-ab --a repo_map=off --b repo_map=on --tasks eval/tasks/bugfix      # 配对比较
mcc sbs --task fix-taxed-base --run-a 954b17 --run-b 6bee37                 # 并排人工标注
```

- `compare` 输出**逐任务**表格（`pass→fail` 标红），而不是只输出总差值。总差值 +8% 可能来自 3 个任务变好 1 个变坏，那是退化不是改进。
- 显著性用配对检验（同一任务的两次配置成对，McNemar 精确二项）；n=24 时明确写出"仅点估计，不足以下结论"的门槛（`|Δ| < 12.5%` 一律判"不可区分"，因为 1/24 = 4.2% 就是单次翻转的量级）。
- `--tag` 生成的基线文件进版本库。**回归 = 两份基线文件的 diff**，可被 CI 消费。
- `mcc sbs` 产出并排 HTML/markdown，人工把排序结果写进 `eval/labels.jsonl` —— 这就是 JD 说的"标注平台"与"SBS 评测"的最小可用形态，也是 §3.9 导出数据唯一的偏好来源。

**S9 实交付记录（as-built，2026-09-22）**

上面那四行命令是本节写的时候的理想形状。真实落地的只有前两条的语义，且旗标名不同 —— 差异全部记下，不留"文档说的一套、代码做的一套"：

| 计划 | 实际 | 为什么 |
|---|---|---|
| `--compare <file>` + `--tag <name>` | `--save-baseline` 写 `eval/baselines/<engine>-<taskset_sha>.json`；后续批次**自动按题集哈希**找它，也可 `--baseline <file>` 显式指定 | 基线的身份就是"这张考卷的成绩"，再套一个人类起的 tag 名只会引入"拿错基线"这条错误路径 |
| `mcc eval-ab --a/--b` | 未做成子命令。配对比较落在 `scripts/b2_compact_ab.py` / `scripts/b3_repomap_ab.py`，两臂只差 `mcc eval` 的一个旗标 | 原计划说"等 S11 的开关"。开关到了，子命令还是没做：A/B 的产物是**一份带判据的证据文件 + 一个退出码**，那是实验脚本的形状，不是日常命令的形状。塞进 CLI 只会让 `mcc eval --help` 长出十个只服务于一次实验的旗标 |
| `mcc sbs` | 未做，S14 随偏好数据一起做 | 没有 `labels.jsonl` 的来源之前，并排 UI 只是给人看的 |
| `judge_x(ctx) -> list[Check]`，一任务一函数 | `judge.py` 里**一条十步固定顺序**判据流水线，任务只提供参数 | 24 个 judge 函数会长出 24 种"什么算成功"的定义。搬成参数之后判据只有一处，判据 bug 也只有一处 |

实测口径修正（三条，都是跑出来的，不是想的）：

1. **标签语义分成两层**：`negative` = 模拟坏行为的题（判定结果不拘），`must-fail` = 其中"判据必须抓住"的一类，被判绿即 `instrument_checks` 报 `guard`。第一次跑真批次时自检用的是 `negative`，于是 `bh-repeat-stall`、`contra-over-budget-loop` 各挨了一条误告警 —— 那两道题老实停下来就是**做对了**。
2. **`PASS_TO_PASS` 用诚实口径**：白名单外允许存在"本来就该红"的用例，判定只报"白名单内是否全绿 + 白名单外红了多少"，不假装整个仓库是绿的。
3. **fake 批次里没有 token 数**（`FakeLLM` 不产 usage），所以 `_cost_row` 的 tokens 一项会自动跳过，只剩墙钟与上下文峰值可比。live 批次才有完整的成本行。

`mcc eval` 的退出码是这一层的对外语言：`0` 这批可汇报；`1` 有崩/该做对没做对/退步或判据告警；`2` 用法或配置不对（一格数据都没产生）。fake 引擎**不读 `.env`**，所以没有密钥的机器（CI、别人的笔记本）也能把 24 题秒级跑完。

**6 题 live 冒烟（2026-09-22，`mcc eval --smoke --budget-tokens 300000`）**

证据：`eval/results/live-smoke.2026-09-22.md` / `.json` / `.txt` 与 `eval/results/live-smoke.traces/`（live 轨迹不可复现，所以随报表一起入库）。结果 `pass@1 = 4/6`，Wilson 95% CI `[0.300, 0.903]` —— 这个区间宽到容得下两种相反的结论，n=6 不支撑任何"能力水平"的说法，它只回答"live 引擎在这条管线上跑不跑得通"。答案：跑得通，6 次运行 0 error、0 装配失败、续跑与预算闸都按设计生效。

两道 fail 都值得记，因为它们是两种完全不同的失败：

- `bh-format-duration`：代码改对了、8 个 P2P 用例仍绿、`tests/` 逐行保留 —— 挂在**行为探测**上："用例数 22，基线 21，至少补两条跨天的防回归用例"。模型修了 bug 但只补了一条测试。判据没坏，是任务要求的第二件事没做。
- `gf-calculator`：模型自己写的 38 条测试全绿（`verify_cmd` 退出码 0），独立 probe 却在第一步就 `ImportError: cannot import name 'DivideByZeroError' from 'calculator'` —— 它把类建在了 `calculator.core` 而没有在包根 re-export。**这一条就是"判据不许读模型自述"的正面证据**：如果只跑模型自己写的测试，这个 pass 会被写进汇报，而用户按题面 `from calculator import ...` 会立刻撞墙。同一次运行花了 210,537 tokens / 13 轮，标了 `context_growth` 并超出单题上限 —— S10 压缩阶梯的第一条真实动因数据点。

预算这一行也记下来：闸**只在任务边界生效**，所以最后一题可以合法地把整批顶过线（预算 300,000，实际 313,812）。报表现在明说这件事，而不是留一行看起来像闸坏了的数字。

## 3.3 Context — 三级压缩阶梯（S10，10h · **B2**）

**职责**：把"上下文快满了"从止损事件变成可干预过程。v1 只有观测（`context.py` 的 `estimate/pressure/calibrate/should_warn/should_stop`，**没有 `compact` 存根**）。

```python
class ContextManager:                       # 现状扩展，方法签名向后兼容
    def compact(self, *, system: str, tools: Iterable[Any],
                messages: list[Message], *, level: Literal["elide","summarize"]) -> CompactReport: ...

@dataclass
class CompactReport:
    level: str; before_est: int; after_est: int
    elided_blocks: int; dropped_blocks: int
    pairing_ok: bool                        # 恒真；为假时调用方必须放弃本次压缩
    summary_message: Message | None
```

**两个旋钮，不是一根线（§0.4 实测的直接后果）**

| 旋钮 | 值 | 管什么 | 谁读它 |
|---|---|---|---|
| `TOKEN_BUDGET` | **32000**（v1 拍的 120000 下调） | 成本与注意力质量预算 —— 压缩阶梯**挂这个** | `pressure = est / TOKEN_BUDGET`、L1/L2/L3 触发点 |
| `CONTEXT_HARD_LIMIT` | **200000**（实测窗口 ≥ 270,570，留 26% 安全边距） | 只防一件事：请求被端点拒收 | 独立熔断 `est ≥ 0.9 × CONTEXT_HARD_LIMIT` → 无条件 `CONTEXT_OVERFLOW`，不看阶梯 |

拆开之前它们混在 `TOKEN_BUDGET=120000` 一个数里，结果是两头都不成立：120000 既不是硬边界（真实边界更高），也不是合理的预算（v1 全部 live 轨迹最大 `context_peak_tokens` = **12,185**，压力 0.10 → 阶梯是死代码）。拆开后：压缩在 32k 预算下于长任务里会真的触发（12k 任务的下一步就是它），而 400 风险由另一条线单独守住。

**阶梯与触发点**（当前判定在 `loop.py:136`）

| 层 | 触发 pressure（= est / 32000） | 动作 | 额外 LLM 调用 |
|---|---|---|---|
| **L1 elide** | ≥ 0.70（≈ 22.4k） | 把**已完成轮次**的 `ToolResultBlock.content` 就地换成 `[elided: read_file src/a.py 4213 chars — 需要时重新读取]`；从最老的开始，直到估算降到目标线以下 | 0 |
| **L2 summarize** | ≥ 0.85（≈ 27.2k） | 用一次 LLM 调用把最老 60% 历史压成**结构化摘要**，替换那批消息；保留最近 N 轮全量 | 1 |
| **L3 refuse** | ≥ 0.95（≈ 30.4k）且 L1/L2 已无可压缩空间 | 保持 v1 行为：`CONTEXT_OVERFLOW` + 明确说明 | 0 |

目标线：L1 压到 0.65 再收手（留缓冲，避免压完下一轮又超）。**为什么先做 L1**：零额外调用、零信息丢失风险（内容可从磁盘重读）、绝不改变消息数量 —— 唯一"绝对安全"的一层，而它通常能吃掉 60–80% 的超额（工具输出是上下文大头，v1 已经用 `TOOL_OUTPUT_LIMIT=30000` 说明我们清楚这一点）。

**配对不变式（本节全部风险的地基）**

```python
def assert_pairing(messages: list[Message]) -> None:
    """三条规则，违反任何一条，端点会直接 400：
    1. 每个 assistant 的 ToolUseBlock.id 必须在其后恰好一条 role=tool 结果里出现一次；
    2. 每个 ToolResultBlock.tool_use_id 必须能回溯到一个已存在的 ToolUseBlock；
    3. 工具结果块只出现在 user 消息里，且该 user 消息不得混入 TextBlock。
    压缩、resume、子 agent 回填三条路径全部要过这个函数。"""
```

L2 的消息删除算法**只能整组删**：一个 assistant 的 `tool_calls` 与它对应的全部结果块属于同一"轮组"，要么都保留要么都替换 —— 半删必炸。v1 里"漏回填一个就 400"是同一条物理规律的两个方向。

**摘要必须是结构化字段，不是自由文本。** 强制模板：

```
[上下文已压缩 · 第 1–14 轮]
目标：<用户原始任务一句话>
已改动文件：<path — 做了什么>            ← 丢这项 agent 就会重写已有文件
已确认事实：<读代码得到的约束>
当前 TodoList 状态：<render 原文>
未验证的假设：<假设 — 验证方式>
最后一次测试结论：<退出码 / 失败用例名>
被否决的路径：<试过什么、为什么不行>     ← 丢这项就会重走死路（thrashing 的直接来源）
```

摘要消息只能作为 `Role.USER` 的 TextBlock 插入（`messages.py:15` 的 Role 只有 USER/ASSISTANT，**没有 SYSTEM 角色**），文本以 `[上下文已压缩]` 开头让模型知道这不是它说的话。这一点是 v1 数据结构的既成事实，不要为了"看起来正确"去加 Role 枚举值。

**摘要生成用 `max_tokens` 上限 + 温度 0**，且生成摘要的那次调用本身要计入 `usage`（L2 不是免费的：v1 报表要新增"压缩开销"一列）。

**不做**：自动压缩历史里的 `TextBlock` 叙述（模型的分析文字里常有关键推理）、删除 user 消息、任何"滑动窗口截断"（等价于让 agent 失忆且不会告知它）。

### 3.3.1 实到（S10 落地后补，2026-09-22）

规格里的 `CompactReport` 只有"这次压了多少"，落地时缺了调用方真正需要的三样，as-built 加上：
`messages`（压缩后的**新列表** —— 不给这个，`pairing_ok` 为真时调用方也无从下手）、
`summary_tokens`（L2 那次调用自身的开销，§7 报表的"压缩开销"列挂它）、
`summary_files` + `note`（前者是代码算出的本地改动清单、受 12 项上限约束，后者是"这层没白干但白干了什么"的中文说明）。
**摘要里能 grep 到已改文件名这条判据押在 `summary_files` 上，不押在模型是否照模板写字段上** —— 模型给空文本时（`test_a_summary_that_says_nothing_still_pays_and_still_shrinks`）清单照样进纪要、账照样记。

四处规格没预见、但必须由代码决定的事：

1. **`run_ladder` 在一层因配对破损被放弃后就 `break`。** L1 免费所以无条件先跑；一旦它证明"这段历史压不得"，再付 L2 那次 LLM 调用买不来任何东西。副作用：被放弃的压缩只留 `elide` 一条记录，报表要能回答"L2 到底试没试过"靠的是每层各一条，而不是只回最后一条。
2. **`dedupe_tool_use_ids`（`messages.py`）在消息入历史之前改名。** B2 首跑的真实死法：脚本每轮都发 `call_0`，跨轮撞车让 `_guard_pairing` 判定**原始**历史就非法，于是每一次压缩都被放弃 —— 阶梯整体空转，看起来像"压缩无效"。改名用 `<原 id>~<位置>` 而非 uuid，否则 trace 与 eval 基线不再逐字节可比。`llm_response.id_repairs` 记数。
3. **L2 的摘要请求走 `summarizer_llm`，默认与主 `llm` 同一个客户端。** FakeLLM 每次 `create()` 弹出一条剧本，摘要若共用队列就会偷吃后续轮次（B2 首跑少写 `part_06` 汇总模块的直接原因）；因此 `demos/fakes.py::FakeSummarizer` 自带队列，`EvalRunner.summarizer_for()` 只在 fake 引擎下换上它，live 时两头都是真端点。
4. **止损必须自己落盘（`context_refuse`）。** 阶梯关掉后，越线发生在那一轮工具结果**回填之后**，而 `turn_start` 每轮只在请求前采样一次 —— 只看 est 序列，off 臂最后一个采样是 23,871，低于 30,400 的拒载线，会被读成"模型自己停了"。现在这条记录带 `line / threshold_tokens / est_tokens / ladder_enabled`，"关阶梯必败"与"某个开关被拧小了"在离线侧可分。

### 3.3.2 B2 实测（`scripts/b2_compact_ab.py` → `eval/results/b2-compact-ab.json`，fake 侧 12 条 + live 侧 2 条判据全绿，1 条按未量记账）

载体题 `eval/tasks-b2/lc-rollup-api.json`（`ledger/` 八模块 194,356 字符 + 八份各约 14k 字符的汇总写出）。三臂只差一个变量：

| 臂 | 参数 | 结果 | 轮数 | 峰值 est | 压缩 | 关键数 |
|---|---|---|---|---|---|---|
| off | `--no-compact` | **fail / context_overflow** | 4（第 5 轮请求前拒载） | 25,195 采样 / **32,611 越线** | 0 次 | `line=l3_refuse, threshold=30,400, ladder_enabled=false` |
| on | 默认（32k 预算） | **pass / completed** | 19 | 21,635 | 14 次（L1 11 · L2 3） | L1 省略 8 块、L2 摘要 28 块、省 95,541 est、摘要开销 5,850 tokens、**0 次因配对放弃**、15 次 id 改名 |
| tight | `--context-budget 12000 --context-hard-limit 15000` | fail / context_overflow | 2 | 10,382 | 2 次 | 负向对照：窗口真不够时阶梯照样救不回来 —— 而且死的位置不一样：越的是 `hard_fuse`（17,794 对 13,500），不是 L3 拒载线 |

摘要清单里存活 6 个汇总文件名（`reports/part_01..06_summary.py`），判据取 ≥6/8 —— 阶梯压掉的是最老的轮组，最后两份还没被摘要接管。
`test_compact.py + test_pairing.py` 收集 51 项（SPEC 要求 ≥8）全绿。

**上表是 2026-09-23 于 S15-b 之后的重跑**（`eval/results/b2-compact-ab.json` 随代码一起再生，不留旧数），与 S11 那一次相比只有 2 个 token 的漂移（仓库自身长了几行），方向性结论不变。S10→S11 那次才是有内容的耦合：两级数字都被符号地图推动着变了，而且方向不一致 —— system 里多了地图，off 臂的越线 est 从 31,287 抬到 **32,611**（更早死），on 臂的峰值却从 26,278 掉到 **21,635**（阶梯提前动手、少留了几轮原始历史），代价是 L2 的摘要开销从 3,900 涨到 **5,850 tokens**、tight 臂的压缩次数从 1 涨到 2。12 条判据在这三种配置下都全绿 —— 结论没变，"地图挤占了上下文预算"这件事第一次有了数值形状。

**live 侧（真实端点 5 次）已于 2026-09-23 签字，签的是 SPEC 那句话；"成功率 ≥60%" 那句改按未量记账。** 2026-09-22 那次被 `HTTP 429` 打断（42 次运行里 28 次 `llm_failure`）之后额度窗口恢复，重跑结果：

| 事实 | 数 |
|---|---|
| 真发出去的请求 | **27 个**（5 次运行），llm 层报错 **0** 次 —— 429 没有再出现 |
| 阶梯是否动手 | **5/5 条 run 都动了**，共 17 次压缩（L1 10 · L2 7，单条 run 2~6 次） |
| 因配对破损导致的 400 | **0**（SPEC §2 行 B2 那句「0 次 …（`test_compact_preserves_pairing` + 真实端点各 5 次）」到此签完） |
| 因配对放弃的压缩 | **0** 次 |
| 判负的另一条自加口径 | 成功率：0/5，5 次全部 `context_overflow` —— 按**未量**记，理由见下 |
| 这一臂的开销 | 118,917 tokens |

未量的理由不是"跑不过"，是**这个预算下那条判据量的不是压缩**（数字都在证据文件的 `success_rate_unmeasurable` 判据里）：`gf-calculator` 在真端点上压缩前的自然峰值只有 3,150~5,319 est，要让 L1 触发（0.70 × 预算）就得把预算拧到 ≤ **7,598**；而同一批 run 里一条工具结果就能把估算顶到 5,926 / 6,023 / 7,718 / 9,477 / **17,839**，要让最坏那条也越不过 L3 拒载线（0.95 × 预算）就得把预算放到 ≥ **18,778**。两个区间不相交，中间隔着 2.5 倍：拧小了解释的是窗口有多小，拧大了会怎样 —— §3.3.4 在高区间实测了两趟，两趟互相矛盾。SPEC §2 行 B2 的原文只把「真实端点各 5 次」绑在「0 次配对 400」上，那一句已经签了；≥60% 是 §7.1 行 10 自己加的口径，改判为未量比拧一个刚好能过的预算诚实 —— 这条修订连同推导写在证据文件的 `amendments[0]`（那条 `why` 里写的小节号 §3.3.3 是当时的编号，指的是同一句话）。

顺带被这条 live 臂钉出来的一件事：**拒载记录带的轮号是下一轮**（循环先 `turn += 1` 再算预算，表里 `turns=5` 而 `refused_at.turn=6`）。它不影响 B2 的判据，但让 §3.9 的导出器把"死在预算线上"的 run 全算成轮数对不上，见 §3.9.1。

### 3.3.3 live 探针的 as-built（`eval/results/b2-live-blocked.json`）

| 事实 | 数 |
|---|---|
| 计划范围 | `--only gf-calculator`，1 题 × 5 次 |
| 实际范围 | 17 题（`_select()` 在 `engine==live` 分支把 `--only` 算出的 ids 整个换成 `supports_live` 全集） |
| 花费 | 357,295 tokens，42 次运行，`llm_failure` 28 / `completed` 12 / `context_overflow` 2 |
| 端点回执 | 19 条 `error` 记录，全部 `HTTP 429`，每条重试 3 次后放弃 |
| 修复 | 默认跳过只在没人点名时生效；点名点到 `supports_live=false` 的题时终端先警告。回归三条在 `tests/test_eval_cli.py` |
| 待办 | ~~额度窗口恢复后重跑 `--live --live-repeats 5`~~ **已结（2026-09-23）**：`--only` 真的只管 1 题，5 次运行 118,917 tokens，见 §3.3.2 的 live 表 |

一条纪律被这次事故验证了：**跑批期间不能同时改代码，也不能同时留两个写同一 `--out` 的批次**。后台那次 `--live-repeats 5` 在工具报告"已完成"之后其实还活着，继续往同一个目录追加 manifest，并且会在收尾时把 B2 的证据文件重写成 429 的成绩 —— 中止它之后 `b2-compact-ab.json` 才是上面这份。

### 3.3.4 高预算反向对照：为什么"≥60%"不能靠拧预算签（`scripts/b2_compact_ab.py --probe` → `eval/results/b2-live-budget-probe.json`）

§3.3.2 那个"≥18,778"的高区间当时只是从 L1 触发线推出来的。这一档把它跑成实测：同一道题（`gf-calculator`）、同一个真端点、`--context-budget 24000`（L1 线 16,800、L3 线 22,800），只把预算这一个旋钮拧高。两趟各 n=1：

| 样本 | 判定 | 轮数 | 压缩 | 峰值 est | 终止 | tokens |
|---|---|---|---|---|---|---|
| 1 | **1/1 通过** | 10 | **0 次** | 11,629 | `completed` | 76,364 |
| 2 | **0/1 判负** | 18 | **6 次**（L1 5 · L2 1） | 19,291 | `max_turns` | 182,613 |

两趟互相矛盾，而矛盾本身就是要记的东西：

1. **"拧大了阶梯就不动手"不成立** —— 样本 2 里模型自己把估算顶过了 L1 线（19,291 > 16,800），阶梯照样动了 6 次手。峰值取决于这一趟工具结果多大，不取决于预算调多大。
2. **在高区间上"成功"与"失败"连死法都不是同一个** —— 样本 2 的判负理由是 `termination != completed`：它其实把活干完了（`76 passed`、四个应有文件齐全、行为探测通过），只因为 18 轮撞到轮数上限而判负。也就是说这条自加判据的分子里，混进了我们自己的轮数上限。
3. **所以那句 ≥60% 在这个题上没有稳定的被量对象**。两趟的方向相反，说明"通过与否"由模型这一趟怎么走决定；把预算当旋钮去凑一个能过的数，凑出来的解释的是模型（或轮数上限），不是压缩阶梯。证据文件因此按**累加样本**记账而不是覆盖 —— 只留最后一个样本等于把随机性藏起来（`samples_disagree: true`、`conclusion` 那句就是这么来的）。

这一档**不推翻**任何已签的判据：配对 400 = 0 那条只依赖"请求真发出去了、端点没报配对错"，两趟一共 28 个请求（10 + 18）打到端点，`pairing_400_errors` 0、`endpoint_errors` 0、因配对放弃的压缩 0。它推翻的是我自己写给 §7.1 行 10 的那句成功率口径 —— 修订记在 §3.3.2、`b2-compact-ab.json` 的 `amendments[0]`，以及 README §10 的偏离表。

顺带被这一档钉住的还有一条**证据自己变薄的退化路径**：`b2_compact_ab.py` 不带 `--live` 也会重写同一份 `b2-compact-ab.json`，而 live 那 3 条只有真端点跑得出来 —— 一次"只想看看 fake 侧"的重跑就把签过的格子静默削成 12 条，而文件形状看起来仍然完整（`pass: true`、`schema: 1`，谁也不会去数列数）。现在这道守卫在写盘**之前**拒绝（退出码 2、字节不动），并由 `tests/test_b2_evidence_guard.py` 从两头钉：不许静默削薄，也不许把 fake 侧锁死到改不动（没有 live 臂时照旧可重跑）。

## 3.4 Memory — `memory/`（S11，8h · **B3**）

**职责**：让 agent 在陌生仓库少花轮数。**MVP 的替代品是 `prompts.py:145 render_repo_map(max_lines=30)` 的广度优先目录树** —— 它按"目录形状"给 30 行，看不到文件用途，深目录尾部直接看不见（README §9 已承认）。

**为什么用 `ast` 而不是 tree-sitter**：本项目"实现只为 Python 负责"（v1 §1.1），`ast.parse` 足以拿到模块级函数/类/方法名/文档串首行/import 关系，零依赖、零语法文件维护。跨语言是 V3 的事，届时再评估 `tree-sitter`。

```python
@dataclass(frozen=True)
class Symbol:
    kind: Literal["module","class","def","method","const"]
    name: str; qualname: str; lineno: int; signature: str
    doc_first_line: str

@dataclass
class RepoMap:
    """aider 式"排序 + 预算裁剪"的 Python 版，但用 ast 而非 tree-sitter。"""
    def build(self, ws: Workspace, *, token_cap: int = 1500) -> str: ...
    def relevant(self, focus: list[str], *, token_cap: int = 1500) -> str: ...
    def invalidate(self, changed: list[str]) -> None: ...

# memory/store.py —— .mcc/ 工作记忆（可重建的派生物，见 §2.3-1）
@dataclass
class MemoryStore:
    root: Path                                  # <project>/.mcc/
    def put(self, kind: MemKind, key: str, value: str) -> None: ...
    def get(self, kind: MemKind, key: str) -> str | None: ...
    def notes_for_prompt(self, *, cap: int = 2000) -> str: ...
```

`MemKind = REPO_MAP | TEST_COMMAND | CONVENTION | GOTCHA`。落盘 JSON（不用 sqlite —— 一个 <200KB 的缓存不值得引入 schema 迁移问题），带 `built_at` 与每个文件的 `mtime+size` 指纹，任一文件变化只重建受影响条目。

**排序信号（`relevant()` 的关键）**：不做 PageRank（那需要跨文件引用图 + 迭代收敛，`ast` 只能拿到 import 名，边不准）。用三个可解释的廉价信号：① 与最近 `edit_file`/`read_file` 目标文件同目录；② 文件名出现在当前 TodoList 文本里；③ 被 `import` 次数。**每个条目带上它入选的原因**，因为 `mcc trace` 要能回答"地图给了它为什么没给那个文件"。

**注入位置**：`build_system_prompt`（`prompts.py:89`）的 `max_map_lines`（`prompts.py:97`）参数换成 `map_provider: Callable[[], str]`，保持该函数注释里"system 里只放整个会话内稳定的内容"这条纪律（`prompts.py:102`）；每轮只在**指纹变化时**更新，避免地图抖动。

**`.mcc/` 必须是 `Workspace.ignored_dirs` 的新成员**（`workspace.py:15`），否则 agent 会把自己的记忆缓存当代码读进去，然后开始给自己写笔记 —— 一条真实的自我强化回路。

### 3.4.1 实到（S11 落地后补，2026-09-22）

规格这节给的接口形状基本照搬落地了（`build/relevant/invalidate` 三个方法、三信号排序、条目带理由、
`.mcc` 进忽略表）。落地时规格没预见、但必须由代码决定的六件事：

1. **`map_provider` 还不够，得有 `Agent(system_provider)`。** 规格说"把 `max_map_lines` 换成 `map_provider`"
   就够了 —— 但地图的内容会随**写文件**变（新文件要出现在图里），而 `prompts.py` 的纪律是"system 里只放
   整个会话内稳定的内容"。两者只 reconcile 得了一次：把整段 system 做成一个 provider，由 loop 在指纹变化
   时重取（`loop._system()` = base/`system_provider()` + notes + todos），其余轮沿用同一个字符串。
   `test_a_read_never_re_renders_the_map_mid_session` 钉的就是这个不抖。
2. **能解析的文件必须永远占一行。** `render_file_lines()` 原本在"没有模块级符号"时返回 `[]`，而 `_score()`
   跳过空条目 —— 于是 `hello.py` 这种纯脚本**从地图上消失了**，测试是 `test_build_session_wires_everything`
   逮到的。现在第 0 行固定是文档哨兵（无文档时 `·`）：地图的第一职责是"这个文件存在"，符号是第二职责。
   只留"解析被跳过"（语法错误、超字节上限）才可能空。
3. **信号 ① 按规格写会死在实现里。** 规格说焦点取自"最近 `edit_file`/`read_file` 目标"，但读文件**不触发重画**
   （第 1 条那条纪律），所以只喂读就等于喂了一个永远看不见的信号。as-built：`observe_paths()` 同时接受读与
   **成功的写**（`FOCUS_READ_TOOLS` / `FOCUS_WRITE_TOOLS`，`loop._observe_repo_map`），被权限门拒绝的写不喂
   —— 没发生的动作不该影响排序（`test_a_denied_write_leaves_the_map_alone`）。另外 `reset()` 清空焦点：
   新任务不该被上一个任务读过的文件带着排（`test_reset_clears_the_focus_of_the_previous_task`）。
4. **`READONLY` 臂不落盘，`.mcc` 同时从判定眼里消失。** demo 的只读问答一跑，`.mcc/memory.json` 就写进了
   工作副本，A3（"只读模式真的没改任何文件"）当场破。两层各管一件事，缺一不可：① 只读模式**根本不建 store**
   （`cli/main.py: writable = mode is not READONLY`）—— A3 靠构造成立，而不是靠"写了但判据白名单掉"；
   ② `.mcc` 进 `eval/contract.py:NOISE`，AUTO 批跑自己留下的缓存不能被算成"改了源码"（同一份 NOISE 还给
   `isolate()` 用，所以考题工作副本永远从空缓存起步，两臂都是冷启）。
5. **`REPO_MAP_TOKENS` 是预算，不是开关。** 规格表里写的"`0` = 关闭"取消：关地图只有一个开关 `REPO_MAP=0`
   （CLI `--no-repo-map`），预算 `REPO_MAP_TOKENS` 至少 1，`<1` 在 config / CLI / `EvalRunner` 三处各自拒绝。
   理由是 B3 要求"两臂只差一个开关"——如果预算 0 也能关地图，就有两条路径产出"看起来一样但来源不同"的
   对照组，报表读不出这批是怎么关的。
6. **`repo_map` 是 trace 的第 14 种事件，字段进快照契约。** `turn=0` 一条（建图）+ 每次重画一条，
   `tests/schema_v2.json` 钉 26 个字段；`test_repo_map_events_land_in_the_trace` 要求一次"写文件"的会话
   恰好两条、轮次 `[1,2]`。地图若开始每轮抖，这条契约先红。

**未落地的规格项**：`MemoryStore` 的 `TEST_COMMAND/CONVENTION/GOTCHA` 三类笔记这一版只有读渲染
（`notes_for_prompt`）与手工 `put`，**没有产生者** —— 观察→写笔记的回路挂在 S13（它要和
`MEMORY_DIR`、快照一起设计才有意义：跨会话写盘的每一步都是 §6.3-2 的攻击面）。当前唯一自动写的是
`REPO_MAP` 缓存。

### 3.4.2 B3 实测（2026-09-22，`scripts/b3_repomap_ab.py --live`，证据 `eval/results/b3-repomap-ab.json`）

**考卷**：`tag=bugfix` 的 6 道 A1 题 × 3 次 × 2 臂 = **36 次真模型运行**，两臂只差 `--no-repo-map` 一个开关。
额度实际花掉 off 臂 649,744 + on 臂 610,767 = **1,260,511 tokens**。

**机制层（7 条，全绿）**

| 判据 | 实到 |
|---|---|
| `arms_differ_by_exactly_one_switch` | 两臂 argv 只差 `['--no-repo-map']`，题集/重复数/引擎/预算全同 |
| `map_replaces_the_tree_not_adds_to_it` | system 净增 +783 ~ +1,377 字符/题（不是把目录树+地图叠起来） |
| `map_is_counted_into_context` | on 臂首轮 `est_tokens` 比 off 高 +235 ~ +406 → 地图确实计进了 `context_peak` 口径 |
| `off_arm_renders_no_map_at_all` | off 臂 `repo_map` 事件 **0** 条 · on 臂 14 条 |
| `two_arms_do_not_share_a_system_prompt` | 6 题逐题哈希跨臂都不同（`bh:d94487≠2c0872` …） |
| `zero_extra_llm_calls` | 两臂各 **33 / 33** 次 `llm_request` |
| `map_lists_every_a1_module` | 6 题的公开模块**未列出 0 项**，预算统一 1,500 tokens，最大一题只用 427 est tokens |

**因果层（B3 的那两句，判定：不过）**

| 判据 | 线 | 实到 | 判定 |
|---|---|---|---|
| `steps_to_success_median_drops_20pct` | 中位轮数降 ≥20% | off **6.0** → on **6**（降幅 0%） | ✗ |
| `context_peak_p95_rise_le_15pct` | 峰值涨幅 ≤15% | 7,711 → 8,129（**+5.4%**） | ✓ |
| `on_arm_does_not_win_by_losing_tasks` | on 臂通过数 ≥ off 臂 | 14/18 → **13/18** | ✗ |
| `every_run_in_both_arms_is_judged` | 无 error/aborted | 崩掉的 run：无 | ✓ |

逐题配对（steps 中位数、通过次数、平均峰值，off→on）：

| 题 | steps | 通过 | peak |
|---|---|---|---|
| `bh-format-duration` | — → — | **0/3 → 0/3** | 5,005→4,992 |
| `rt-checkout-bulk` | 5 → **4** | 3/3 → 3/3 | 7,036→7,105 |
| `session-fix-humanize-only` | 6 → **4** | 3/3 → 3/3 | 4,953→**4,538** |
| `session-fix-ttl-units` | 7.5 → 8.0 | 2/2 → 2/2 | 6,866→7,033 |
| `slug-dedup-clean-rule` | 7 → 6.5 | 3/3 → **2/3** | 6,083→6,787 |
| `taxed-fix-eu-vat` | 6 → 8 | 3/3 → 3/3 | 6,337→6,820 |

**结论：B3 未达成，而且这是一次有效的证伪，不是一次失败的实验。**

1. 地图**很便宜**：p95 只涨 5.4%（线是 15%），零额外调用，且它的 token 是按口径计进 `context_peak` 的 —— 便宜不是靠漏记买来的。
2. 地图**没有把 A1 变快**：中位数纹丝不动 6.0→6.0。6 题里 3 题变快（`rt-checkout-bulk` 5→4、`session-fix-humanize-only` 6→4、`slug-dedup-clean-rule` 7→6.5）、2 题变慢（`session-fix-ttl-units` 7.5→8.0、`taxed-fix-eu-vat` 6→8）、1 题两边都没有通过样本。中位数对这种"两两抵消"的形状天然迟钝，所以逐题表必须一起看 —— 但换任何聚合都变不出一个 20% 的下降。
3. **有效样本比 6 题小**：`bh-format-duration` 两臂 3 次全灭，`steps_to_success` 只统计通过的 run，它贡献 0 —— 这份中位数实际是 **5 题 × 3 次**的。on 臂还少一次通过，所以"没降"不是幸存者偏差造成的。
4. 那道全灭的题值得单独记：两臂都在同一处栽（与地图无关），说明它是**考卷或能力**的问题而不是对照实验的问题 —— 已进 S12 之后的待查项。
5. **线保持原样**。不改 20%、不加"或者至少某几题变快"这种后半句。§6.2 那三条时间线的账在 §6.2.1，它们是另一回事（且其中冷建那条也不过）。

**由此产生的规格判断（写下来，免得下次靠感觉）**：`RepoMap` 的价值主张在 A1（"定位一个已知存在的 bug"）上不被支持，因为这类题的定位本来就是"搜一个符号名 → 读两个文件"，目录树和符号地图给的是同一条路径。它可能真正起作用的地方是**跨文件的大仓库**（JD 第 8 项的"陌生大仓库"、A3 只读问答的广度扫描），而这恰好是本题集里没有的形状。所以：机制保留（它几乎免费，且在 `session-fix-humanize-only` 与 `rt-checkout-bulk` 上各带来 −2 轮），**不再为 B3 追加额度重跑**，把它降级成"待验证假设"记在 §7.3；下一批若要重测，考卷要换成跨 ≥5 文件的改动题。

## 3.5 Scheduler — 只读工具并发（S12，5h · **B5**）

**现状**：`_run_tools()`（`loop.py:213`）单 for 循环，注释明确写着"刻意不并发 —— 副作用顺序必须与模型声明顺序一致"。v2 只在这个理由**不成立**的那部分放开并发。

**改造**（保持四段结构，不要写成一个大函数）

```python
def _run_tools(self, calls):
    decided = [(c, self.gate.check(tool_of(c), c.input)) for c in calls]   # ① 串行权限判定
    results: dict[int, ToolResult] = {}
    serial  = [(i,c) for i,(c,_) in enumerate(decided) if not parallelizable(c, _)]
    parallel= [(i,c) for i,(c,_) in enumerate(decided) if      parallelizable(c, _)]
    with ThreadPoolExecutor(max_workers=self.max_parallel_reads) as pool:  # ② READ 并行
        for i, fut in ...: results[i] = fut.result()
    for i, c in serial: results[i] = ...                                    # ③ 其余按序
    return [results[i] for i in range(len(calls))]                          # ④ 顺序重组
```

```python
def parallelizable(call, decision) -> bool:
    return (decision[0] is Decision.ALLOW
            and tool.risk_level is RiskLevel.READ
            and call.name not in SERIAL_TOOLS)     # write_todos 永远串行
```

**必须处理的 5 个竞争写点**（全部位于 `loop.py:218-273` 的循环体内）：

| 写点 | 位置 | 处理 |
|---|---|---|
| `state.tool_calls += 1` / `tool_errors += 1` / `denied_actions += 1` | `loop.py:252,258,260` | 移到 ④ 之后统一结算（并行区不碰 state） |
| `self._trace(...)` | `loop.py:227,242` | `Tracer.log` 目前每次 `open("a")`（`trace.py:59`），多线程会交错。**加 `threading.Lock`，并缓冲后按 group 顺序写** |
| `self._emit(EventKind.TOOL_END,...)` | `loop.py:235` | CLI 渲染层必须看到与串行一致的顺序 → 同样延后到 ④ 之后发射 |
| `self.todos` 变更（`write_todos`） | `loop.py:269` | `write_todos` 列入 `SERIAL_TOOLS` |
| `Workspace` | `workspace.py` | frozen dataclass 且方法无状态 → 只读安全，无需改动 |

**收益先测再改**：§3.5 原本写"S8 的 trace 新增 `parallelizable_in_round`，先在真实轨迹上统计分布，**若可并行轮占比 < 20% 把 S12 整段推到 Tier 3**，这条判断由 S9 的数据做，不由我做"。

> **S11 时核对的缺口**：`parallelizable_in_round` 这个字段 S8 并没有实现（`grep -rn parallelizable src/` 为空，`tests/schema_v2.json` 里也没有）。S12-a 决定**不补这个字段**：这道闸的价值恰恰在于它是从 `tool_call` 的 `turn`/`name`/`risk`/`latency` **独立反推**出来的，如果让调度器自己上报"这轮我并行了几个"，闸就成了被考核者自己填的表 —— 一个 `parallelizable()` 里的 bug 会同时污染分子和判据。字段与调度器一起留在 Tier 3，真要开工时同日落地。

### 3.5.1 S12-a 实测：两道闸都不站在 S12 这边（`scripts/probe_parallel_share.py` → `eval/results/s12-parallel-share.json`）

样本 46 次 live 运行（`b3-ab` 两臂 + S9 冒烟 + 4 个 demo）+ 72 次 fake 运行，共 261 个"执行过工具的轮"（live 口径）。可省时间按**线程池无限大**算：每个可并行轮 `Σlatency − max(latency)`，这是对本提案最有利的假设。

| 闸 | 线 | 实测 | 结论 |
|---|---|---|---|
| ① 可并行轮占比 | ≥20% | 合计 **21.5%**（56/261）；最新的一层 b3-live 单独看 **19.8%**（41/207） | 卡在线本身上：一层过、一层不过，差 1 个轮 |
| ② 墙钟 p50（B5 的话） | 降 ≥15% | live：可省 **162ms / 1,489,447ms** 运行墙钟 = **0.011%**（p50 0.009%） | **差三个数量级**，与实现质量无关 |

为什么 ① 过了也没用 —— 拆开看时间都花在哪：

| 来源 | 运行墙钟 | 工具占 | LLM 占 | 可省上界 | 占工具时间 | 占运行时间（p50） |
|---|---|---|---|---|---|---|
| b3-live（36 次） | 1,118,331ms | 10.51% | 89.2% | 120.0ms | 0.102% | 0.009% |
| earlier-live（6） | 169,598ms | 7.22% | 92.7% | 23.0ms | 0.188% | 0.017% |
| demo-live（4） | 201,518ms | 6.46% | 93.5% | 19.0ms | 0.146% | 0.011% |
| fake（72，工具即全部） | 129,427ms | **99.37%** | ≈0 | 231.0ms | 0.180% | 0.127% |

三点读法：

1. **LLM 延迟占 live 墙钟的九成**，而它不在调度器的管辖范围里。把只读调用并到极限，动的只是那 6~10% 里的一小部分。
2. **可并行轮的内容几乎全是 `read_file` 叠加**：56 个可并行轮里 54 个是 2~7 个 `read_file` 的组合，只有 2 个掺了 `find_files`，一个 `search_text` 都没有。单次读盘 1~3ms —— 省下来的串行次数是真的（56 轮 × 平均每轮 2.9ms），但那是 162 毫秒，不是 15%。
3. **fake 引擎不是逃生口**：那里 LLM 瞬时、工具即 99.4% 的墙钟，看起来终于"轮到并发说话"，实测也只省到 p50 0.127%。拿 fake 的 p50 给 B5 签字会是最容易犯的一种自欺 —— 因为分母被换成了"没有模型的世界"。

**决定**：S12 的调度器改造按 §3.5 自己写的砍单条款推入 Tier 3（§7.3 第 6 项），Tier 2 交付物改为只剩 B6。**B5 不重述、不改线**：它现在的 15% 是照着"读盘很贵"的成本模型写的，而本项目的真实成本模型是"模型很慢、读盘很快"。谁要复活 B5，先要拿出一条**换了也说得通**的前提（网络盘 / 单文件几十 MB 的读、或把 `run_tests` 与模型下一轮重叠 —— 那已经不是 §3.5 的范围），而不是把 15% 改成 0.01%。

不变的是那三条正确性约束本身，它们仍然有效，只是目前没有需要被它们约束的代码：`gate.check()` 一律在进线程池之前串行完成（D16）、结果序列与 `tool_calls` 声明顺序逐位一致、`write_todos` 永不并行。`loop.py:_run_tools()` 的注释继续写着"刻意不并发"，并且现在有数据支撑这句话。

## 3.6 ExecutionBackend — 沙箱与 Durable 会话（S13，10h · **B6**）

**现状**：`bash.py` 与 `run_tests.py` 直接 `subprocess.run` 在本机工作区。

```python
class ExecutionBackend(Protocol):
    name: str
    def available(self) -> tuple[bool, str]: ...
    def exec(self, command: str, *, cwd: Path, timeout: int, env: dict) -> BackendResult: ...
    def snapshot(self) -> str: ...                       # 返回 rev
    def restore(self, rev: str) -> None: ...

class LocalBackend:   ...   # 现状行为，一字不改
class DockerBackend:  ...   # subprocess 调 docker run；挂载工作区；--network none（可配）
```

**`DockerBackend.available()` 失败时不是崩，而是明确降级**：报表与终端都写"本次用 local 后端（docker 不可用：<原因>）"。静默换后端等于让 B6 的一致性判定失去意义。

**检查点用 shadow git**：`git --git-dir=<repo>/.mcc/snapshots --work-tree=<workspace>` 的独立索引，**绝不 `init` 用户的工作区**（那会在别人的仓库里留下 `.git` 冲突）。每次 `write_file`/`edit_file` 成功后提交一条 rev，`rev` 记进 trace 的 `checkpoint` 事件。`/undo` = `restore(prev_rev)`。

**Durable session**

```python
@dataclass
class SessionSnapshot:
    session_id: str; turn: int; messages: list[dict]; todos: list[dict]
    state: dict                    # AgentState.snapshot()
    config: dict                   # Config.redacted()
    backend: str; last_checkpoint_rev: str
    done_call_ids: list[str]       # 幂等重放依据
    ts: float
```

`mcc resume <session-id>`：读快照 → `assert_pairing(messages)`（不配对就拒绝恢复，把坏现场留在文件里让人看）→ 继续循环。**恢复时已完成轮次的工具绝不重放**（重复执行 `bash` 类副作用不可接受），靠 `done_call_ids` 去重。

"Durable" 这个词在 PCG 那份 JD 里指的是长任务框架。我们的最小可用版本就是"**跑到一半被杀掉还能接着跑，且不重复已发生的写操作**"。不承诺 exactly-once（那需要后端事务），承诺 at-most-once 副作用 + 可审计现场。

### 3.6.1 实到（S13 落地后补，2026-09-22）

`backend/` 五个模块（`protocol` / `local` / `docker` / `checkpoints` / `factory`）+ `sessions.py`，
S13 的测试合计 **115 例**（`test_sessions` 22 · `test_undo` 19 · `test_backend_factory` 18 ·
`test_checkpoints` 15 · `test_memory_dir` 10 函数→15 例 · `test_docker_backend` 12 ·
`test_resume` 14），全批 595 passed。

**三处刻意不照抄规格，都是落地时规格自身说不通的地方：**

| 规格写法 | 实到 | 为什么改 |
|---|---|---|
| `exec(command: str, ...)` | `command: Command = str \| Sequence[str]` | 字符串必须过 shell（`bash -c`，模型写的管道才有效），序列必须走 `execv`（参数里带空格的路径才不会被二次解释）。一个签名兜不住两件事，混着传就会把 `run_tests` 的 argv 拆坏。 |
| `snapshot() -> str` | `-> tuple[str, str]`，`restore(rev) -> tuple[bool, str]` | 只返回 rev 的话，"这次没拍成"和"拍成了但 rev 是空串"在 trace 里长得一样。第二格是**原因串**（`NO_CHECKPOINT_REASON` 等），它直接进 `checkpoint` 事件的 `note`。 |
| 每后端各自实现检查点 | 两臂共用**同一份**宿主侧 `Checkpointer` | 影子 git 的 work-tree 是宿主目录，容器里那份 `/work` 是同一个 bind mount。各存一份会出现"在 docker 臂 undo 回到 local 臂的某个状态"这种跨臂时间旅行。 |

`LocalBackend` 的行为与 S13 之前逐字相同 —— 它必须继承 `os.environ`（否则找不到 python），
这条由 `test_backend_factory` 钉住，而不是靠"我没改那几行"的回忆。

**检查点**：`git --git-dir=<MEMORY_DIR>/snapshots --work-tree=<workspace>` 的独立索引，
用户工作区里**不出现** `.git`（`test_checkpoints` 直接断言这一点）。每次 `write_file`/`edit_file`
成功后一条 `checkpoint` 事件（`rev` / `tool` / `ok` / `backend`）；`/undo` = `restore(prev_rev)`，
`/backend` 面板顺带列出栈深与降级原因。影子仓库建不起来时**降级而不是失败**：写工具照常成功，
`checkpoint` 事件带着原因落盘，报表里 `checkpoints.degraded` 非空。

**Durable 会话**：`SessionSnapshot` 的字段与规格一致（`done_call_ids` 是幂等**记账**的依据；
**免重放的判据**是它的一份只在 restore 时种下的副本 `_restored_call_ids` —— 这是 S15-b 期间
改的，下面第二段说为什么）。
`mcc resume [<id>|--latest]` / `--list` 三条路径都在，`--list` 每条带工作区 —— 共享
`MEMORY_DIR` 或多份 clone 时，只有 session id 无法回答"这份现场是哪个仓库的"。

现场恢复的形状必须写清楚，因为它是测试与实现最容易各说各话的地方：
`restore_session` **只承认一种破损** —— 前缀消息全部配对、最后一条是声明了 tool_use 而
零结果回填的助手消息。其余破损（中间断、结果多余、配对错乱）一律拒绝恢复，把坏现场留在
文件里给人看。恢复时 `_dangling_batch()` 把未完成批次过一遍 `_run_tools`，
`done_call_ids` 里的 id 直接跳过并回一句 `REPLAY_NOTE`，发 `session_replay` 事件、
**不写** `tool_call` 记录、**不重复** `state.tool_calls` 计数。于是同一个数字在两处口径不同，
这是刻意的：`run_end.tool_calls` 是**会话级**的账（跨进程续算），trace 文件里的 `tool_call`
记录是**进程级**的观察（这个进程真执行了几次）。`test_resume` 里两条断言并排写着，
免得后来人把差异当 bug 修掉。

**但"跳过"的判据不能读 `done_call_ids` 本身**（实到改动，S15-b 期间）：那份账本在**本进程**
每执行一次调用就会增长，而 `dedupe_tool_use_ids` 发出的名字（`call_0` → `call_0~0` → …）在
L2 摘要把带旧名的助手消息整组删掉之后，会重新发一个干净的 `call_0` —— 于是同一进程里一次
**全新**的写入被读成"上一个进程做过"，直接跳过。B2 的 on 臂在第 13~18 轮就是这样栽的（判据
fail，trace 却一切正常；那份现场被修复后的重跑覆盖，可复现形式是
`test_the_ledger_of_this_process_is_not_a_replay_guard`）。修法是把两个角色分开：账照旧记进
`done_call_ids` 并写进快照，免重放的判据读一份**只在 restore 时种下**的副本。

**MEMORY_DIR 收敛**：一个产地（`config.DEFAULTS["MEMORY_DIR"]` → `memory.MEMORY_DIRNAME`），
七个消费者（MemoryStore、影子 git 的 `snapshots/`、`sessions/`、`Workspace.ignored_dirs`、
RepoMap 遍历、eval 的 `noise_names`、`/backend` 面板文案）。S13-d 把它做成**可改的**并
把七处全部串起来，因为"写盘目录与检索排除目录不是同一个"是自我强化回路的入口：地图读到
`.mcc/` 里缓存的地图，几轮之后模型看到的是自己的输出被当成代码。改名成 `.brain` 之后
七处一起跟上（`test_memory_dir` 15 例，含 `Config.from_env` 的首轮覆盖与
`../outside` / `a/../../b` 这类敌对值的启动期拒绝）。

### 3.6.2 B6 实测（`scripts/b6_backend_ab.py` → `eval/results/b6-backend-ab.json`，2026-09-22）

B6 的字面要求：**同一任务在 local 与 docker 两个后端上判定结论一致。**
这条比 B2/B3 便宜，而且便宜是有原因的：后端只改"命令在哪儿跑"，不改模型看到的任何东西
（system、工具 schema、上下文全不变），所以 fake 引擎上的**判定分歧只可能是后端 bug** ——
不像 B3 那样"fake 的 Δ 是我写的剧本的 Δ"。

`tag=bugfix`（会真执行命令的 6 题）× repeats 2 × fake 引擎：

| 项 | 实测 |
|---|---|
| 可比对格数 | 12 |
| 判定一致 | **12 / 12（100%）** —— verdict、终止原因、**`Check` 列表的 `(label, ok, detail)` 三元组**、`(工具名, ok)` 逐格序列全同 |
| 两臂判定 | 各 12 pass / 0 fail / 0 error，`pass@1` 6/6 |
| 容器命令行构造次数 | 32 次 `run` + 12 次 `version` 探测 + 0 次超时清理 |
| 前提检查 | **8 / 8 全绿**（含"docker 臂真的用上 docker""两臂同为 local 的假一致"拒绝） |
| 墙钟 | local 42.5s · docker 46.5s（**这个数字不能读成容器启动开销**，见下） |

argv 实证（替身日志原样抓的一条）：

```
docker run --rm --name mcc-8180c9acf36c --network none
  -v <workspace>:/work -w /work -e PYTHONDONTWRITEBYTECODE=1
  --entrypoint python3 python:3.12-slim -m pytest -q --no-header -rf --tb=short
```

宿主解释器 `D:\...\python.exe` 没有出现在任何一条容器命令里（`_translate()` 只在 **argv[0]**
上换名，模型自己写的 `./venv/bin/python` 原样保留 —— 替它换解释器是篡改意图）。

**这条线没测到什么**：这台开发机上没有 docker，替身是在宿主上执行命令的假 docker。
所以被证明的是**命令行构造 + 降级判定 + rev 共用 + 两臂判定一致**，
**没被证明的是文件隔离、网络隔离、镜像内容** —— 结果文件里 `container_isolation_tested: false`
就是这一句话的机器可读版本。墙钟那 +4.0s 量的也是"多起一个本机 python 进程"，不是
容器启动；真 docker 到位后原样重跑本脚本，届时才会第一次看到沙箱本身的代价。
降级防线是脚本的**默认**行为：docker 臂实际用上 local 时整条脚本退 1、不产出一致率，
只有显式 `--allow-degraded` 才允许把降级跑成一版报表 —— 两臂同为 local 的"100% 一致"
是这场对照最省事的假结论，它必须是 opt-in 而不是默认。

**这批判出来的三个缺陷**（都是先有测试/前提、后有修复，不是顺手重构）：

1. `mcc resume --latest` 在零现场时 `IndexError` —— 用户敲的第一条恢复命令换来 traceback。
2. `-v` 挂载路径按**第一个**冒号切，Windows 盘符 `D:` 当场把它切成空目录，于是"容器"在替身
   自己的 cwd 里跑了整套仓库测试、超时、报出 `run_tests` 失败。表现完全像"两个后端结论不同"，
   实际两臂跑的根本不是同一份代码。**B6 第一条分歧的证据是自己写的替身有 bug** ——
   这条如果只比 verdict、不核 workdir，就会被记成"后端不一致"。前提清单里那条
   "容器的工作目录就是这一题隔离出来的那份目录"是这次加的，加完就红了、修完才绿。
3. `--list` 不报工作区（见上），以及 eval 的 `noise_names` 把 `.mcc` 写死 —— 改名后现场会被
   当成题面的一部分参与哈希比对。

## 3.7 Extensibility — MCP bridge 与 Skills（S14，6h）

```python
# ext/mcp.py
class MCPBridge:
    def __init__(self, servers: list[MCPServerSpec], gate: PermissionGate) -> None: ...
    def discover(self) -> list[BaseTool]: ...        # 每个远端工具包成一个 RemoteTool
class RemoteTool(BaseTool):
    risk_level = RiskLevel.EXECUTE                   # 默认最严，远端自报的 risk 只作展示
    name = f"mcp__{server}__{tool}"
```

三条硬要求：① 命名空间前缀，杜绝覆盖 `read_file`；② **远端声明的 risk_level 不可信**（D19），默认 `EXECUTE` → `ask` 模式下逐次问、`auto` 模式下仍要问（外部工具不属于"工作区内自动放行"的语义范围）；③ `BaseTool.invoke()` 的校验链路一个都不能少，远端返回不是字符串时 `_normalize` 照样兜住。注册点仍是 `ToolRegistry.default(extra_tools=...)`（`registry.py:58`）—— **不新增第二条装配路径**。

```python
# ext/skills.py —— 一个目录 = SKILL.md（描述 + 指令）+ 可选引用文件
class SkillLoader:
    def catalog(self) -> str                          # ≤30 行，注入 system：只有名字和一句话
    def load(self, name: str) -> str                  # 通过 load_skill 工具按需读进上下文
```

Skills 的架构含义是**延迟加载的提示词**：常驻只有目录（几十 token），需要时才展开。这与 §3.3 是同一个思路的两面 —— 上下文是预算资源，谁进来都要报价。

**不实现 skill 自带脚本**（D20）。

### 3.7.1 实到（S14 落地后补，2026-09-22）

`ext/mcp.py` + `ext/skills.py`，S14 的测试合计 **85 例**（`test_mcp_bridge` 44 · `test_cli_ext` 20 ·
`test_skills` 21），另外 `test_trace_contract` 的 `extended` 夹具把 `mcp` / `skills` 两种装配期事件的
**真实产地**接进了快照（schema 现有 20 个 kind、7 份夹具）。全批 680 passed。

**六处刻意不照抄规格，都是落地时规格自身接不上现实的地方：**

| 规格写法 | 实到 | 为什么改 |
|---|---|---|
| `MCPBridge(servers, gate)` | `MCPBridge(servers, workspace, timeout)`，**不接 gate** | bridge 只管传输与包装。判定放两处就会出现"bridge 与 gate 各持一份档位真相"，与 §3.3 砍重复裁剪同一个理由 |
| 「远端声明的 `risk_level` 不可信」 | 读 `annotations.readOnlyHint / destructiveHint / openWorldHint`（规范字段）与私有 `riskLevel`，两者**只进 `declared_risk` 展示字段**，采信值恒为 `EXECUTE` | 官方 SDK 压根不给 `risk_level` 这个字段名。按规格字面读会得到"永远读不到、于是永远像已采信"的假安全 —— 这一条是互操作臂逼出来的 |
| `servers: list[MCPServerSpec]` | `Sequence` + `MCPServerSpec.from_mapping()` 是**语义校验的唯一产地**；config 只管 JSON 形状与重名 | 与 `BACKEND_NAMES` 同一条纪律：契约名单住 config，语义判定只有一处。否则 `{"name":"ok","transport":"sse"}` 会在两个地方各报一次、措辞还不一样 |
| 「注册点仍是 `ToolRegistry.default(extra_tools=...)`（`registry.py:58`）」 | 同一处，但判据换成**"src 里 `ToolRegistry.default(...)` 的 ast 调用点恰好 1 处"**，由 `scripts/s14_ext_demo.py` 量 | 行号会漂，写死行号的判据只会变成"改一行代码就得改一行文档"。ast 数的是真调用点，文档字符串里那两处提及不算 |
| 坏工具条目"跳过" | 跳过 + **逐条理由**，且非法名那条记的是**原始名**（`bad name` 而不是编一个 `mcp__fx__bad_name`） | 洗过的名字模型调不动，等于把一次拒绝伪装成一次成功。名字没能组成 `mcp__` 名，就照抄给用户看 |
| trace 里"这条扩展没参与"的原因叫 `skipped` | 改名 `skip_reason` | `stats()` 已经用 `skipped` 表示"被跳过的工具条数"（int）。同名字段两种类型会让 §3.1 的 schema 契约失去意义，而那份契约是 S8 全部价值的来源 |

**规格里没写、落地时必须有的五件**（每一件都对应一次真实的失败或乱码）：

1. `_ENV_ALLOWLIST`（子进程只看得见"不起进程就起不来"的那几个键）+ `_SECRETISH`（名字像密钥的，
   用户点名也不透传）+ `dropped_env` 留痕 —— 剔除必须报出来，否则用户只会看到"服务连不上"。
2. `PYTHONIOENCODING=utf-8` 由我们注入子进程，客户端发送侧 `ensure_ascii=True` + `.encode("ascii")` ——
   Windows 上远端默认 cp936，中文是在**我们这侧**被解坏的。
3. `_fault()`：EOF 与超时的报错带上 stderr。`MCP server fx 关掉了输出` 这一句没有任何诊断价值，
   加上 `fixture: 我不干` 才知道是服务自己拒干。
4. `_already_in_text()`：`structuredContent` 与 `content[].text` 是同一句话时不双份进上下文 ——
   官方 SDK 的 `-> str` 工具就是双份的（这一条同样是互操作臂逼出来的）。
5. `discover()` 对缺 `properties` 的 schema 给理由并跳过：`_coerce()` 只放行 schema 点名的参数，
   于是"参数全被丢掉而调用成功"是最难查的那种错。

**Skills 的经济性实测住在这里**：`catalog()` 有行数预算（`MAX_CATALOG_LINES=30`），溢出时少列技能
也保留"另有 N 个未列出"与那句"别一次全取进来"；`load_skill` 是 `READ` 级，所以 AUTO 跑批不会卡在它上面
（`ask` 模式下它也不该问 —— 读一个本地 markdown 不是外部副作用）。`load(name)` **参数里没有路径**，
所以"取的时候越界"这件事没有入口；越界的目录名在发现期就被 `_SAFE_NAME` 挡掉。

### 3.7.2 S14 实测（`scripts/s14_ext_demo.py` → `eval/results/s14-mcp-skills.json`，2026-09-22）

行 14 的原话是「接一个真实 MCP server 跑通，6 项安全测试绿」。这份证据把 6 项 + 14 项附带判据
在**真子进程**上重新量一遍（引擎是 fake：这一段测接线与边界，不测模型能力）。**20/20 条量过并成立，
用时 6.4s**，`verdict=pass`。对端有两个，各自证不同的事：

| 对端 | 证到什么 | 关键实测 |
|---|---|---|
| `tests/fixtures/mcp_fixture_server.py`（自写、故意敌意） | 坏远端接得住 | 4 收 / 4 跳（逐条理由）；`shadow` 模式下远端**逐字**报 `read_file`/`write_file` → 注册成 `mcp__fx__read_file`，本地 `read_file` 仍读到盘上真内容而冒名者读不到；`exit`/`silent`/`garbage` 三臂 `close()` 之后残留进程 0 |
| `tests/fixtures/mcp_sdk_server.py`（**官方 SDK 写的**） | 「真实 MCP server 跑通」这一句 | `sdk-fx @ 2024-11-05` → 3 个工具；`inputSchema` 由第三方从函数签名生成（`properties = ['text','times']`）；它自报 `readOnlyHint` → `declared_risk='read'` 而采信仍是 `execute`；中文往返 `'你好 MCP 你好 MCP'` 无损 |

安全侧最值钱的六条：

| 判据 | 实测 |
|---|---|
| AUTO 下外部工具仍问、本地写仍放行（§6.3-1） | `外部 ask / 本地写 allow`，理由「外部工具（MCP）在工作区之外执行，不在自动放行的语义范围内，需要单独确认。」；`confirmer=None` 时 `authorize=False` |
| **被拒的那一次在工作区之外没有留下文件** | `write_note` 的落盘路径由 `MCP_FIXTURE_MARKER` 决定、在工作区之外；拒绝臂 → 文件不存在且 trace 里**没有** `tool_call`（只有那条 `permission/deny`）；点同意臂 → 同一支工具写出 `['这一行应当出现']`。两臂只差人的一个回答，这才叫对照组 |
| 宿主 env 不外泄 | 点名 `LLM_API_KEY` + `MCP_FIXTURE_NAMED` + 一个不存在的名字 → 子进程 `has_api_key=false`、`has_pythonpath=false`、`has_path=true`、点名的普通变量拿到了；`dropped_env = ['LLM_API_KEY','MCP_DEFINITELY_NOT_SET_ANYWHERE']` |
| 密钥不进 trace | 服务 args 里的 `--token sk-live-…` → trace 全文含明文 `false`、含「已脱敏」`true`，`redacted()["mcp_servers"] == ['fx']`（名字照留，否则查不到连的是谁） |
| 校验在上线之前 | `text=12` → 「参数校验失败：'text' 应为 string，实际是 int」，**不落盘**，且之后 `echo` 仍通（连接没被这次坏参数弄坏） |
| 只读模式结构性无副作用 | READONLY 臂 `bridge` 根本没建、registry 里 `mcp__` 工具 `[]`、起了的进程 `[]`，事件里只有 `configured` + `skip_reason`，没有运行计数 |

延迟加载那笔账（`skills/` 是仓库自带的两个技能）：

| 项 | 实测 |
|---|---|
| 常驻目录 vs 展开正文 | 一个正文 **5,200** 字符的技能，目录只占 **99 字符 / 3 行**；再加一个技能，目录**多 19 字符**（与正文长度无关）；常驻/展开 = **0.019** |
| 仓库自带的 `skills/` | 2 个技能（`add-eval-task`、`trace-triage`）· 目录 4 行 / 240 字符 · 正文合计 2,130 字符（1,190 + 940）· 目录里不含任何正文行 |
| 装配路径 | `ToolRegistry.default(...)` 在 src 里的 ast 调用点 = **1 处**（`cli/main.py:181`），外部工具与 `load_skill` 都从 `extra_tools` 进 |

**没证到的**（写在结果文件的 `what_this_does_not_prove` 里，别只抄上面那张表）：① 真端点下模型会不会
**滥用**外部工具（剧本是我们写的）；② MCP 的 `resources` / `prompts` 两类能力与进度、取消、订阅
（实现里一行都没有）；③ 非 stdio 传输（§7.4 顺位 2）；④ 真实第三方生态的兼容面 —— SDK 那臂只有
一个服务、三个工具；⑤ 多服务并发握手的耗时分布（现在是串行，最坏 30s × N）。
8 条 as-built 取舍全部记在同一份 JSON 的 `amendments` 里。

**D26 的对照文档**：`docs/framework-equivalence.md`（78 行）—— registry+loop 与 LangGraph StateGraph
逐格对齐，含"我们真的缺的四件"与"它的卖点对我们是负资产的四件"，并给出迁移时的三个硬冲突点
（权限默认值、幂等账本、度量产地）。JD 第 6 项的回答形态从"用过 X 框架"换成了"能说出不用它买到了什么"。

## 3.8 Multi-agent — 只做 verifier（S15，4h）

```python
@dataclass
class SubAgentSpec:
    system_prompt: str; tools: tuple[str, ...]; max_turns: int; token_budget: int

class SpawnAgentTool(BaseTool):
    name = "spawn_agent"
    # input: {spec: "verifier", task: str}
    # 返回：只回传"失败用例名 + 精简原因"，绝不回传子 agent 全过程
```

为什么只有这一种形态值得做：**子 agent 的价值在于上下文隔离，而"跑测试并把 200 行 pytest 输出压缩成 5 行"恰好是上下文隔离唯一明显划算的场景**（父 agent 的上下文里只进摘要）。planner/worker/critic 的分工在 coding 任务上会让 worker 看不到全局历史 —— 这正是 v1 D3 的判断，v2 用 B1 的失败分布复核（D21）。

三条约束：① 子 agent 复用同一 `Workspace` 与 `PermissionGate`（**不得绕过权限门**，子 agent 写文件与父 agent 写文件必须同样被确认）；② token 预算由父分配，计入父的 `max_total_tokens`（否则一个子 agent 能烧穿整条止损线）；③ 子轨迹写独立 `session_id` + `parent_session_id`，`mcc trace` 能展开成树。

### 3.8.1 S15-a 实测：D21 那道线不支持动手（`scripts/probe_verifier_gate.py` → `eval/results/s15-verifier-gate.json`）

§7.3-1 写的是「只在 B1 失败分布支持时做」，所以这一节的产出是**一个判据**而不是一份实现。样本：5 层 live 轨迹共 75 次运行（b2-ab live 29 · b3 两臂 18+18 · S9 冒烟 6 · demo 4），外加 D21 点名的 B1 fake 批 72 次**只作对照组**。闸②不需要新数据：验证输出占多少字符，`tool_call.output_chars` 在盘上就有。

| 闸 | 线 | 实测 | 结论 |
|---|---|---|---|
| ① `no_verification`+`self_confirm` 占比（全部 run 分母） | >20% | live **1/75 = 1.3%**；fail-only 分母 **0/32 = 0.0%** | 差一个数量级，两个分母同向 |
| ① 对照：D21 原句点名的 B1 批 | >20% | fake **12/72 = 16.7%** | 连它点名的那份数据自己都不过线 |
| ② 「200 行 pytest 输出」= 14,000 字符 | 验证输出值不值得隔离 | 验证类输出占工具总输出 **41.6%**（每格中位 20.7%）→ 贵；但单次最大只有 **2/148 次**过那条线 | 贵是真的，**贵在哪一路**与原假设不同 |

三处口径如果不钉住，这道闸会给出三个不同答案：

1. **分母**：D21 只写「占比」。fake 层在两个分母下分别是 16.7% 与 0.0% —— 差一个数量级。签字用「全部 run」，因为问的是行为频率；fail-only 天然偏高（只有失败才会被翻出来找原因），拿它签字几乎永远过线。
2. **尺子版本**：标签一律用**当前**规则重算（`infra/failure.classify`），并同时读 manifest 落盘那份做交叉 —— 两侧都判「不过线」（0.0133 vs 0.0133），所以结论不挂在规则版本上。
3. **分子记的是行为还是代价**：`self_confirm` 的判据是「termination=completed 且最后一次验证是红、之后没有绿」。命中的 run 单独按判据结论数一遍：live `{'pass': 1}`、fake `{'pass': 12}` —— **一份都没判负**。也就是说 20% 这条线即使过了，它数出来的是"模型收尾时眼前最后一次验证是红的"，而 verifier 能救的只有"因此把任务做砸"的那部分。这两格之间目前没有重叠的样本。

闸②的拆分是本节最有用的副产品。§3.8 那句「把 200 行 pytest 输出压缩成 5 行」在本项目**有产地，但不在我们设计的那条路上**：

| 路径 | 次数 | 单次最大 | 过 14,000 字符的次数 |
|---|---|---|---|
| `run_tests`（结构化：通过/失败计数 + 失败用例名 + 精简 traceback） | 95 | 4,263 字符 | **0** |
| `bash` 直接跑 `python -m pytest -v` | 53 | 28,062 字符 | **2** |

那两次过线的输出全部来自模型**绕开**我们自己的结构化工具。所以「200 行没被隔离」不是缺一个子 agent，而是有一个工具没被用上 —— 该修的地方在提示词与工具描述（为什么它宁用 `bash`），不是再加一层上下文。这也解释了为什么"验证输出占 41.6%"和"verifier 不划算"两句话同时成立：贵的是**累计**（148 次调用、162,014 字符），单次并不离谱，而上下文隔离只能按单次省。

另一个方向也要说清楚，免得这句话被读成"模型会自查"：如果判据放宽成「中途见过红 + 最后宣告完成」，占比立刻变成 **35/75 = 46.7%** —— 过线。但那个数没有意义，正常调试循环本来就会红几次再改绿。线该划在哪，取决于"过早"怎么定义，而 46.7% 那一版定义的分子里包含全部成功的修复过程。

**决定**：`spawn_agent` 与 §3.8 的三条约束一起留在 Tier 3（§7.3 第 1 项），Tier 2/S15 交付物改为**只剩 §3.9 的 trajectory exporter**（S15-b）。**D21 那条 20% 不改**：改线等于承认先前那条是随手写的，而它现在的错法是"分母没写、分子没接代价"，这两处都在 §3.8.1 里被实测钉住了，比换数字有用。重评触发条件（三条，任一成立即回来）：① live 样本换一批**为验证行为设计**的题（现在的样本来自 B2/B3，只覆盖 Python + pytest 的 6 道题），且按上面第 3 点接上代价后占比 >20%；② 任务集出现**没有**结构化工具的生态（前端/构建系统/HTTP 服务），那 41.6% 会直接落到父上下文里；③ `bash` 绕过 `run_tests` 的次数经提示词修正后仍居高 —— 那时隔离才有对象可隔离。

那三条约束（不得绕过权限门、token 预算计入父、子轨迹独立 session 可展开成树）继续有效，只是目前没有需要被它们约束的代码 —— 与 §3.5 那三条并发不变式同一个处理方式，`SPEC` 里保留设计、不保留空壳。

## 3.9 Trajectory exporter — 数据管道，不做训练（S15，4h）

```python
# eval/export_rl.py
def export(runs: list[RunRecord], *, out: Path, include_failed: bool = True) -> int:
    """trace + 判据 → JSONL：每行一条 (state, action, reward)。"""
```

一条样本的 `reward` = `verdict`(0/1) 与 `steps_to_success` 的折扣项；`labels.jsonl`（§3.2 的 SBS 人工标注）提供**成对偏好**信号。

**这份数据不做训练。** 理由见 D23。它的价值有三层，都不依赖我们自己训模型：① 它是"我理解后训练需要什么数据形状"的证据（比一个训不动的 LoRA 更有说服力）；② 它是回归集的种子 —— 失败轨迹正是下一批评测任务该长的地方；③ `no_verification` / `test_gaming` 这类模式标签是**行为级标注**，是偏好数据里最贵的那部分人工判断的自动化前身。

### 3.9.1 实到（S15-b 落地后补，2026-09-23）

规格里那一句「trace + 判据 → 每行一条 (state, action, reward)」默认了一个不成立的前提：**trace 里有观测**。
§3.1 的设计恰恰相反 —— `llm_response.blocks` 只存类型名、`tool_call` 存 `output_chars` 不存正文、
`run_start` 存 `user_input_chars`。所以 `state` 只能有两个产地，导出器把这件事写成可判定的字段而不是注释：

| 产地 | 凭什么 | 落到行上 |
|---|---|---|
| **A：快照前缀** | §3.6 的 `SessionSnapshot.messages` 是全文。配对**可验证**：第 t 轮那条 assistant 必须坐在 `messages[llm_request.message_count]`，且它的 `tool_use` id 序列与这一轮 trace 里的 `tool_call.tool_use_id` 序列**逐个相等** | `state.source="snapshot-prefix"`、`reconstructible=true`、带 `messages_delta` |
| **B：结构指纹** | 只有 trace：`message_count` / `est_tokens` / `prefix_hash` / 本轮供给的工具数 | `state.source="trace-fingerprint"`、`reconstructible=false`、`pairing_reasons` 写明为什么没有正文 |

配对是**整份 run 全有或全无**的：一轮对不上，那个 session 的所有行都退回产地 B。理由和 §3.2 拒绝
"剧本说改完"是同一条 —— 一份错位的 (state, action) 比导不出来更坏，因为它看起来是好的。

七条 as-built 取舍：

1. **一行 = 一个决策步**，锚在 `llm_request`，不是规格暗示的"一行 = 一条轨迹"。run 级字段（判据、
   失败模式、trace 路径）在每行重复，让下游不必自己 join。
2. **增量导出**：`messages_delta` 只放"这一步新出现的那几条"，期望条数 = `message_count` 的增量 − 1
   （多出来那条是模型自己的回复，它是 action 不是 state）。这条算式反过来就是审计：26 个产地 A 的
   session、138 行逐行核过（证据里那条前提叫「增量导出不重不漏」）。
3. **reward**：`terminal` 只有 pass=1.0 / fail=0.0，`value_t = terminal * gamma ** steps_to_end`
   （`gamma` 默认 0.97）。`error` / `aborted` **不进数据** —— 喂进奖励函数等于教模型躲避我们的 bug。
4. **失败标签读 as-run 的 `failure_mode` 轨迹记录**，绝不用 `classify()` 重算。分类器阈值在本项目里
   已经收窄过一次（`_no_verification` / `_self_confirm` / `_permission_starved`），重算会给历史数据贴
   今天的标签。三格 `labels` 分开存：规则 / manifest / 人工。
5. **压缩是"降级"不是"作废"**：`context_compact` 之后的快照前缀不等于模型当时所见，但这一步的动作
   仍然是模型真实做的。所以记成行内的 `state.broken_by_compaction` 字符串（可由下游过滤），
   而不是进 blocking 的 `pairing_reasons`。
6. **`verification` 只在 `run_tests` / `bash` 上有值**，其余工具一律 `null`。把"没验证"写成"绿"是
   §3.8.1 闸②那种错误的镜像。
7. **密钥扫描扫的是盘上那份文件**，不是导出时的内存对象；用的就是 `infra/trace.py` 的
   `_SECRET_LIKE` 同一条正则。测试里专门有一条反向用例：夹具换成不被它遮的字符串，前提必须变红。

**丢跑批的账**（这是这一节最该被记住的部分）：join 之前先按 trace 路径去重、再按轮数校验。第一次
在 12 个批次上跑时，195 个 run 里丢了 28 个（`duplicate-trace` 14 + `turn-mismatch` 14），全部
集中在 `b2-ab/live` —— 那个目录里 42 行 manifest 只有 28 个不同的 `(task, repeat)` 键。根因不在导出器：
trace 文件名按 `(task, repeat, engine)` 定，**重跑是覆盖上一份**，而 `append_manifest` 是纯 append，
于是旧行指向的已经是别人的轨迹。已在源头修掉（`runner.append_manifest` 改为按 `(task, repeat)` 取代
旧行 + `os.replace` 原子重写，`tests/test_eval_runner.py::test_rerunning_a_run_supersedes_its_manifest_row`）。
**代价说清楚**：修好之后新批次 1:1，但修之前那批的中间态不可复原 —— 同一道题的重跑数据只能取最后一次。
这条已写进 `eval/results/s15-export.json` 的 `amendments` 第 4 项。

`turn-mismatch` 那 14 个的根因是第二个上游缺陷，而且它咬的正好是最值得看的那批轨迹：**死在预算线上的
run**。循环先 `state.turn += 1` 再算预算（`loop.py:388` 在硬熔断和 L3 之前），所以 `context_refuse`
带的是**下一轮**的轮号；`steps_from()` 拿它当一步用，于是 `trace_steps = manifest_turns + 1`，每一口
阶梯之死都被记成"轮数对不上"然后整条丢掉 —— 也就是说被丢弃的正好是最有信息量的那批样本：阶梯在真端点
上亲手压出来的那些 run，和它们 `context_overflow` 的终止原因，一条都进不了奖励函数。
修法是让一步必须至少留下 `llm_request` 或 `llm_response` 之一
（`export_rl.py::Step.has_response`），并配一条回归：拒载轮没发过请求就不该是步
（`tests/test_export_rl.py::test_a_refusal_turn_that_never_sent_a_request_is_not_a_step`）。
两处源头都修完之后重跑：**丢弃 0**（`dropped_runs.count`），输入 run 从 195 折到 158 —— 差额就是
manifest 去重折叠掉的历史重跑行，那部分数据按上面的代价说明放弃。

另一处修在源头的是快照查找的作用域：`find_snapshot` 只允许从 trace 路径**上一级**（`<批>/<臂>`）
往下找。一开始写成往上三级，测试立刻抓到它爬进了 `Temp/pytest-of-czx/`，把一次运行的 session 配上了
另一次运行的快照 —— 「找不到」的正确结论是"这行是产地 B"，不是"去更远的地方再看看"。

**实到数字**（`mcc export-rl`，12 个批次 → `eval/.work/rl-all.jsonl`，证据 `eval/results/s15-export.json`）：
832 行 / 1,698,325 字节 / 4.35s；输入 158 个 run = 进数据 158 + 丢弃 **0**；产地 A 26 个 run（138 行带正文）、
产地 B 132 个 run（694 行只有指纹，全部自述原因，占 83.4%）；`row_id` 832/832 唯一；8 个块超限并留标记；
成对偏好 43 对（全部是"同题重跑"，判据来自 manifest），SBS 人工标注 **0 条**；**10/10 条前提成立**。

**这份证据不证明的**：不证明这批数据够训模型 —— 它首先证明的是**不够**。S13 之前的批次结构性地没有
快照，所以只有指纹；要拿这个 agent 做真·后训练，第一步不是训，是让 trace 把 observation 落盘，那是
§3.1 没覆盖的改动，日志体积与"仓库内容进不进日志"的边界都得重开一次评估。D23 的"不做训练"不变。


## 3.10 明确不做（附重评触发条件）

| 项 | 为什么不做 | 什么条件下重新评估 |
|---|---|---|
| **向量 RAG** | 代码 embedding 在 <10 万行仓库召回差、依赖外部供应商、失效模式隐蔽 | 任务集出现单文件 >5000 行，或 `search_text` 空命中率 >25% |
| **后训练 / SFT / LoRA** | 4GB 显存 + 无标注预算，投入产出为负 | 拿到 GPU 或能借到训练资源，且 exporter 已积累 >1000 条 |
| **多 Agent 泛化（planner/critic/辩论）** | 上下文隔离对 coding 多数时候是负担（v1 D3）。**2026-09-22 补：连最保守的那一种形态（verifier）也没被数据支持** —— §3.8.1 实测 live 1/75、D21 点名的 fake 批自己 12/72，都不过线 | B1 失败分布里 `no_verification`+`self_confirm` 合计 >20%，**且**命中的 run 里判 `verdict=fail` 的那部分自己也过 20%（这一半是 §3.8.1 新加的：原判据记行为不记代价，全部命中都判 pass） |
| **分布式 / K8S** | 单机单进程没有分布式问题 | 出现多机 worker 池需求 |
| **VLM / 多模态 / GUI** | 与"读懂代码并改对"主线无关 | 决定做 browser agent 时（届时 `ImageBlock` 要改 5 处：`messages.py:51` 联合、`as_message`、`_wire_messages`、`wire_chars`、`_environment` 环境事实 —— 现在不留空壳） |
| **asyncio 重写** | 见 D15 | 出现"必须维持大量并发长连接"的真实需求 |
| **LangChain/LangGraph 迁移** | 见 D26 | 永不做（对照文档即交付物） |
| **`/responses` API 迁移** | 裸 chat/completions 的报文可见性价值仍在（v1 D8） | 需要服务端会话托管时：`previous_response_id` 会让 §3.6 的快照机制变冗余，届时评估 |
| **prompt 缓存优化** | 见 D14 | 端点暴露 `prompt_tokens_details.cached_tokens` |

---

# 4. Agent Execution Flow（v2 一轮的完整时序）

只列 v2 新增/变化的步骤，其余与 v1 §4 相同：

```
 1. 装配：build_session(memory=..., backend=..., scheduler=..., tracer=v2)
 2. system = 稳定事实 + RepoMap(预算裁剪，仅指纹变化时更新) + 工具清单 + 规范
    ⚠ todos 仍在 system（D14），RepoMap 在 system 里的行数预算替代 v1 的 30 行目录树
 3. ContextManager.estimate → pressure
      ≥0.70 → compact(L1 elide)   ──┐
      ≥0.85 → compact(L2 summarize)─┤→ assert_pairing() → 失败则放弃本次压缩并记 context_op(pairing_ok=false)
      ≥0.95 且无可压缩 → CONTEXT_OVERFLOW（v1 行为）
 4. llm.create() → calibrate（同 v1）→ 记 usage（含压缩调用自己的开销）
 5. 分支判定同 v1；MAX_TOKENS 提示重试、空响应重试都不变
 6. ★ 权限判定：全部 calls 串行过 gate.check()（并发前完成，ASK 只问一次）
 7. ★ 执行：READ+ALLOW 走线程池，其余按声明顺序串行；state 计数与事件发射延后到顺序重组之后
 8. ★ 工具内部：write/edit 成功 → backend.snapshot() → checkpoint 事件；bash → ExecutionBackend.exec
 9. ★ 结果打包成一条 user 消息（同 v1）+ 配对断言 + 幂等记账 done_call_ids
10. ★ 若 spawn_agent：子 Agent 用独立 session 跑，只有摘要进父上下文，父记子配额
11. 每轮止损：turn / total_tokens / stalled_group / pressure（同 v1）+ ★ 批次预算（eval 模式）
12. ★ 收尾：failure_modes 分类 → run_end(含 cost_est、wall_ms) → eval 落 RunRecord
```

**压缩前后的行为差异如何被观察到**：`context_op` 事件 + `pairing_ok` 恒真 + B2 的同一任务对照。三者缺一，压缩就是一个"看起来省 token 实际可能把 agent 压傻"的黑箱改动。

---

# 5. Data Structure

## 5.1 v1 类型全部保持（不改动即兼容）

`Role/StopReason/TextBlock/ToolUseBlock/ToolResultBlock/Message/Usage/LLMResponse/Config` 维持现状。v1 §5.1 的约定继续有效，特别是"一批工具结果作为**一条** user 消息回填"（`messages.py:69`）—— 这条与配对不变式（§3.3）是同一物理规律。

## 5.2 v2 新增

```python
# infra/trace.py v2
SpanIds      = {trace_id, span_id, parent_span_id}          # 16/32 hex，挂在每条记录上
@dataclass FailureMode(StrEnum): PATH_GUESSING CONTEXT_GROWTH NO_VERIFICATION
                               SELF_CONFIRM TEST_GAMING THRASHING PERMISSION_STARVED
                               BUDGET_EXHAUSTED

# agent/context.py v2
@dataclass CompactReport: level before_est after_est elided_blocks dropped_blocks pairing_ok summary_message

# memory/
@dataclass Symbol / @dataclass MemKind(StrEnum): REPO_MAP TEST_COMMAND CONVENTION GOTCHA
class RepoMap / class MemoryStore

# agent/scheduler.py
@dataclass ParallelPlan: serial: list[int] parallel: list[int] max_workers: int

# eval/
@dataclass TaskInstance / RunRecord / BatchReport / Regression / TaskSet(sha)
@dataclass LabelRecord: task_id run_a run_b winner reason   # SBS 人工标注

# backend/
@dataclass BackendResult: exit_code output truncated duration
class LocalBackend / DockerBackend / ExecutionBackend(Protocol)
@dataclass SessionSnapshot

# ext/
@dataclass MCPServerSpec: name transport endpoint args env
@dataclass SkillManifest: name description path when_to_use

# agent/subagent.py
@dataclass SubAgentSpec
```

**`AgentResult` 保持 v1 形状**，只加可选字段：`failure_modes: list[str] = []`、`cost_est: float | None = None`
（没配单价就是 `None` —— "不知道成本"和"成本为 0"是两个数，报表上必须分得开）。v1 §5.2 那句"`AgentResult` 的形状现在就定死了，V2 直接消费"兑现了 —— `eval/runner` 消费它不需要回头改循环。

## 5.3 配置项新增（仍然只有 `config.py` 读环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `TOKEN_BUDGET` | ~~120000~~ → **`32000`** | **v2 改的是已有默认值**（`config.py:15`）。语义收窄为"成本与注意力质量预算"，压缩阶梯挂它；依据见 §3.3 与 §0.4 |
| `CONTEXT_HARD_LIMIT` | `200000` | 新增。窗口安全线，只用于"别让请求被拒收"的独立熔断，**不参与压缩阶梯判定**。`config.py` 加载时就校验 `TOKEN_BUDGET < CONTEXT_HARD_LIMIT`，否则熔断线先于阶梯生效、整批跑的全是 `CONTEXT_OVERFLOW` |
| `CONTEXT_COMPACT` | `1` | 新增。`0` = 整个阶梯不开（L3 拒载与硬熔断照旧）—— B2 的对照组靠它，命令行是 `mcc eval --no-compact` |
| ~~`COMPACT_LEVEL`~~ | — | **未落地，S10 as-built 改动**：层级触发点是 `context.py` 的常量（`L1_ELIDE_PRESSURE=0.70 / L1_TARGET_PRESSURE=0.65 / L2_SUMMARIZE_PRESSURE=0.85 / L3_REFUSE_PRESSURE=0.95 / HARD_FUSE_RATIO=0.90`），由 `test_compact.py` 逐条钉住。再叠一个 `off/l1/l2` 环境变量只会让"报表里那次跑的是哪套阈值"变成猜 —— A/B 需要的是**一次一个变量**，所以给的是 `--no-compact` / `--context-budget` / `--context-hard-limit` 三个评测旗标，而不是五个旋钮 |
| ~~`COMPACT_TARGET`~~ | — | 同上，`L1_TARGET_PRESSURE` 是常量 |
| `REPO_MAP` | `1` | **S11 实到新增**（规格里只有下面那个预算项）。`0` = 不画符号地图，system 退回 v1 的 30 行目录树 —— B3 的对照组只有这一个开关，命令行 `mcc eval --no-repo-map` |
| `REPO_MAP_TOKENS` | `1500` | ~~`0` = 关闭~~ → **S11 as-built：纯预算，至少 1**。`<1` 在 `config.from_env` / CLI / `EvalRunner` 三处各自拒绝，关闭只走 `REPO_MAP=0`（原因见 §3.4.1-5：对照组必须只有一个来源） |
| `MEMORY_DIR` | `.mcc` | 工作记忆目录（自动进 `IGNORED_DIRS`） |
| `MAX_PARALLEL_READS` | `1` | **未落地**（S12 推 Tier 3，§3.5.1）。规划语义不变：默认 1 = 不并发，显式设 >1 才启用，B5 由 A/B 决定默认值。`config.py` 里没有这个键，读环境变量时也不接受它 —— 静默接受一个不生效的旋钮比缺这个旋钮更糟 |
| `EXECUTION_BACKEND` | `local` | `local` / `docker` / `auto` |
| `SNAPSHOT_ENABLED` | `1` | shadow git 检查点 |
| `MCP_SERVERS` | 空 | JSON 数组，见 `MCPServerSpec` |
| `EVAL_BUDGET_TOKENS` | `4000000` | 整批评测硬预算 |
| `PRICE_PER_MTOKENS` | `0` | 成本核算；0 时 `cost_est` 报 `null` 而非 0（**不许把"不知道"报成 0**） |
| `LLM_TEMPERATURE` | 不设 | 评测跑批时显式设 `0`（§0.4 初测可复现，正式跑前按 R6 复测） |

这张表是**规格**，不是现状。纪律与 §3.1 的孤儿指标同一条：**旋钮必须和消费它的代码同批落地**，
所以 S8 只加了 `PRICE_PER_MTOKENS`（当轮就被 `_cost_est()` 消费）。其余各自随 Stage 进：
`TOKEN_BUDGET` 下调与 `CONTEXT_HARD_LIMIT` 在 S10（压缩阶梯挂上去之前，把预算从 120000 砍到
32000 只会让长任务在没有兜底机制时提前 `CONTEXT_OVERFLOW`），`CONTEXT_COMPACT` 在 S10（实到，
见上表：`COMPACT_LEVEL/COMPACT_TARGET` 被合并成常量），
`REPO_MAP_TOKENS` 在 S11，`MAX_PARALLEL_READS` 在 S12，`MEMORY_DIR` 在 S13，MCP/评测类在 S14/S9。

---

# 6. Engineering Requirements

v1 §6 全部继续有效（类型注解、frozen dataclass 优先、`StrEnum`、注释只写为什么、`ruff`）。v2 增补：

## 6.1 测试矩阵（每层"必须钉住的不变式"，不是"要有测试"）

| 层 | 新增测试 | 数量目标 |
|---|---|---|
| trace 契约 | `test_metrics_have_producers`（孤儿指标检测）、`test_trace_schema_snapshot`（字段快照比对） | 3 |
| 压缩 | `test_compact_preserves_pairing`（随机历史 + fuzz）、`test_elide_keeps_tool_messages`、`test_summary_contains_changed_files`、`test_l2_uses_one_extra_call` | 8 |
| RepoMap | `test_map_within_token_cap`、`test_map_invalidates_on_edit`、`test_map_skips_memory_dir`（`.mcc` 不得出现在地图里）、`test_map_entry_explains_inclusion` | 6 |
| 并发 | **未落地**（随 S12 推 Tier 3）：`test_results_order_matches_declaration`、`test_ask_is_serialized`（假确认器计数）、`test_write_tools_never_parallel`、`test_state_counts_after_reorder`、`test_trace_lines_not_interleaved`。今天真正在钉的是它们的反面 —— `_run_tools()` 单循环、结果顺序即声明顺序（`test_loop_with_fake_llm.py` 的全量回填与顺序那组） | (7) |
| 后端 | `test_backend_unavailable_degrades_explicitly`、`test_snapshot_restore_roundtrip`、`test_resume_rejects_unpaired_snapshot`、`test_resume_does_not_replay_writes` | 8 |
| eval | `test_metrics_recompute_from_trace`（报表数字必须能从 trace 重算出来）、`test_resume_skips_completed_runs`、`test_taskset_sha_detects_tampering`、`test_judges_never_read_result_text`（v1 那条检测器的推广） | 9 |
| MCP/Skills | `test_remote_tool_default_execute_risk`、`test_name_collision_rejected`、`test_skill_lazy_load_costs` | 6 |

**新增测试目标 ≥ 47 项**，全部 FakeLLM 驱动、**零网络**。

铁律不变：**确定性测试永不打网络**（v1 §6.2）。这条在 v2 更重要 —— 批跑一旦进 CI，端点抖动会被误读成"agent 退化了"。评测只在显式命令下跑 live。

## 6.2 性能与开销预算

新增机制的开销上限（超出即视为回归，不是"可接受的代价"）：

| 机制 | 每轮额外 CPU | 额外 LLM 调用 |
|---|---|---|
| trace 写入 + 脱敏 | ≤ 3 ms/事件 | 0 |
| `RepoMap.build`（全量，<2000 文件） | ≤ 800 ms，且只在建图与失效时；命中缓存 ≤ 5 ms | 0 |
| `compact(L1)` | ≤ 20 ms | 0 |
| `compact(L2)` | ≤ 20 ms | **1（必须计入 usage 与报表）** |
| 并发调度（线程池开销） | ≤ 2 ms/轮 | **0 —— 未落地**（S12 推 Tier 3，§3.5.1）。这条预算连同它要防的对手一起没了：实测一个可并行的只读轮平均只值 2.9ms |
| `backend.snapshot()` | ≤ 150 ms（shadow git 小仓库） | 0 |
| `failure` 分类（收尾一次） | ≤ 50 ms | 0 |

**每轮新增开销上限：50 ms**（`snapshot` 与 L2 只在触发时计）。这条逼着我们把"每次改动都全量重建地图""每次写都提交 git"这类偷懒实现挡在前面。

### 6.2.1 S11 as-built 实测（2026-09-22，证据：`eval/results/b3-repomap-ab.json` 的 `timings` / `budget_lines` / `amendments`）

量的是**本仓库自己**，口径 = `RepoMap.stats.modules_found`（142 个 `.py`；`.venv`、`__pycache__`、`.mcc`、隐藏目录按 `_python_files` 排除 —— 把第三方库算进预算只会把线说得比实际更宽松，`find` 到的 2,334 个 `.py` 里绝大多数是依赖）。每次调用取 5 个样本的中位数，跨 7 次独立调用：

| 线 | 实到 | 判定 |
|---|---|---|
| 冷建 `RepoMap.build` ≤800ms | 7 次调用的中位数依次 556.8 / 602.2 / 711.9 / 829.1 / 941.6 / 968.5 / 1,381.3ms，**总中位 829.1ms**；最后一次调用内 5 个样本 858~1,105ms | **不过**（超线 ~3.6%） |
| 缓存命中建图 ≤5ms | 进程内 memo 六次采样 4.0 / 4.0 / 4.1 / 5.7 / 7.8 / 9.4ms；跨进程命中磁盘缓存 27.9~99.4ms | memo **半数达标**（同样负载决定）；跨进程**不过** |
| 每轮附加开销 ≤50ms | 每轮至多一次渲染，中位 **0.0003ms/轮** | 达标（离线 5 个数量级） |

三条结论，都不是把线改成能过的数：

1. **冷建这条线真正的约束是"别每轮重建"，而不是"冷建必须多快"。** 冷建一次的成本 = 文件数 × 每文件成本，实测每文件 **4~7ms**（读 + `ast.parse` + 打分），而 `≤800ms @ <2000 文件` 隐含 **0.4ms/文件** —— 规格自己的两个数不自洽，差一个数量级。按实测斜率，这条线的天花板在 **~120 文件**，本仓库 142 个已经在外面。要撑到 2000 文件得换机制（增量/懒建 + 只解析改动文件），不是换个数字。
2. **这条线在 Windows 上是负载敏感的**：同一份代码，空载 556.8ms、边跑 477 项测试 829.1ms、仓库被索引时 968.5~1,381.3ms。所以 §6.2 的判定应当永远写"几次独立调用的分布"而不是单点 —— 单点会随机外负载翻成"过/不过"。`budget_lines` 因此按**本次样本中位数**判，并保留 `cold_build_history_ms_across_invocations` 全列。
3. **跨进程缓存命中做不到 5ms 是因为指纹纪律**：§3.4 要求"以磁盘状态为准"，命中前必须对每个文件 `stat` 一次比 `mtime+size`，142 个文件就是 27.9~99.4ms。压到 5ms 只能靠不信指纹 —— 那正是 §2.3-1 禁止的"以缓存为准"。**所以这条线拆成两条记账**：进程内 memo（达标）与跨进程冷启（不达标，且刻意不达标）。

增量重建本身是做到了的：`test_a_stale_fingerprint_rebuilds_only_that_file` 钉住"只有指纹过期的那一个文件被重新解析"，`test_map_for_prompt_does_not_jitter` 钉住"每轮不重画"。也就是说 §6.2 写这条线时要防的那件事（每轮全量重建）没有发生，发生的是"一次冷建比我愿意承认的更贵"。

## 6.3 安全边界（v2 新增攻击面）

1. **MCP 远端工具**：默认 `EXECUTE` 风险 + 命名空间前缀 + 不得覆盖本地工具名（测试钉）。
   → **实到（S14，§3.7.2）加了五条规格没写的**：① 子进程 env 白名单，名字像密钥的即使被用户点名也不透传，
   且剔除要留痕（`dropped_env`）；② 服务配置 `args` 里 `--token sk-…` 按**位置**脱敏（`_scrub` 只认值的形状，
   认不出"这一位是上一条旗标的值"）；③ **只读模式连发现阶段都不起进程** —— 起一个第三方进程本身就是"变"；
   ④ 外部工具在 `auto` 下仍要问（AUTO 的承诺是"工作区内自动放行"，外部不在那个集合里），
   没有确认渠道时保守拒绝；⑤ 判据落在盘上而不是日志上：被拒的调用不许在**工作区之外**留下文件。
2. **`.mcc/` 记忆污染**：记忆条目由 agent 观察产生，一次错误观察会持续误导后续会话。因此 ① 记忆内容永不进 diff 之外的可执行路径；② `TEST_COMMAND` 类笔记**只展示、不自动执行**；③ v1 对 episodic memory 的顾虑（v1 §3.4）依然成立，本次仍不实现跨会话"经验教训"。
3. **快照泄漏**：shadow git 在工作区内 → 必须落 `.gitignore`，且**不得包含 `.env`**（`PROTECTED_NAMES` 已有，`permissions.py:40`；快照用同一份排除表）。
4. **Docker 逃逸的反面**：沙箱的默认配置必须 `--network none`、只读根文件系统 + 可写工作区卷。放宽要显式配置，且 CLI 要打印当前沙箱参数。
5. **密钥纪律不变**：`Config.redacted()` + `_scrub()`（`trace.py:20-21` 的正则掩码对新字段自动生效）。**v2 新日志字段一律走 `_scrub`，不得新增绕过路径。**

## 6.4 证据与报表纪律

- 所有对外数字来自 `summarize()` 或 `metrics.py`，**禁止手写**。v1 已有两条检测器（fixtures 纯净、脚本不自报判据数字），v2 推广到报表层：`test_report_numbers_regenerable`。
- fake 与 live 证据**分栏且标注各自证明了什么**。fake 列不再显示 token 类指标（§0.3 末尾），改为"工程闭环：通过/失败"。
- 每个百分比必须带样本量；n < 20 时禁止写"提升了 X%"，改写成"X/Y 通过（n=Y）"。
- live 数字**不做平均**：每次运行各自成档（v1 已如此），报表注明"同配置重跑会漂移"。

---

# 7. Development Roadmap

总计 80h + 缓冲 9h = 89h（≈22 天 × 4h）。按 §1.4 的依赖顺序，**Tier 1 不完成就不许开工 Tier 2**（这是 D10 的可执行形式）。

## 7.1 Tier 1 — 让数字可信、让改进可测（40h，交付 B1）

| Stage | 内容 | 工时 | 退出标准 |
|---|---|---:|---|
| **8** ✅ | §3.1 度量修补 + trace v2 + schema 契约 + 失败分类学 + `mcc trace` | 8h | E1/E2/E3 关闭；`test_metrics_have_producers` 绿；对 3 条真实失败轨迹人工核对模式标签（B4 的前半）→ **实到 16 条全核对、`scripts/b4_label_check.py` 退出码 0** |
| **9** ✅ | §3.2 `eval/`：从 `run_demo.py` 抽 `judge/Check/prepare` → `eval/`；任务集 24 个；runner + 指标 + 断点续跑 + 批次预算 | 14h | 24 任务 fake 引擎全跑通（秒级）+ 6 任务 live 冒烟；一份 `eval/baselines/` 基线文件入库（**B1**）→ **实到：fake 72 次运行 `pass@1=20/24`，12 个 fail 全是 `must-fail` 负样本，退出码 0，p50 930ms / p95 6,880ms；基线 `eval/baselines/fake-0935fa95ca49.json` 已入库；live 冒烟 4/6（证据见 §3.2 末尾与 `eval/results/`）；评测层 113 项测试（全仓 382）** |
| **10** ✅ | §3.3 压缩阶梯 L1+L2 + `assert_pairing` | 10h → **实到 2026-09-23（live 侧于 S15-b 期间签完）** | **B2**：超预算任务 0 个 400、压缩后成功率 ≥60%；8 项压缩测试绿 · **实到（2026-09-23 重跑，`eval/results/b2-compact-ab.json`，fake 侧 12 条判据全绿）**：off 臂第 5 轮 32,611 est 越 `l3_refuse`（阈值 30,400、`ladder_enabled=false`）→ fail/context_overflow；on 臂 19 轮 pass、14 次压缩（L1 11 · L2 3）、省 95,541 est、摘要开销 5,850 tokens、**0 次因配对放弃、0 个配对 400**、摘要清单存活 6/8 个已改文件名；压缩+配对测试 **51 项**绿（要求 ≥8）。system 里加了符号地图之后 off 臂更早死、on 臂峰值反而从 26,278 降到 21,635，这是 §3.3.2 记下的耦合。**live 那半条（2026-09-23 重跑成功）**：`--only gf-calculator` 真端点 5 次、27 个请求、llm 层报错 0（429 没再出现）、阶梯 **5/5 条 run 都动手**共 17 次压缩、**因配对破损导致的 400 = 0**、因配对放弃 0 —— SPEC §2 行 B2 那句原文到此签完（2026-09-22 那次被 429 打断、以及同一趟修掉的 `--only` 被 `supports_live` 覆盖的 bug，见 §3.3.3）。**"成功率 ≥60%" 改按未量记账**：它是我自己加在 §7.1 这一行的口径，在 6,000 预算下 0/5 全 `context_overflow`，而把预算拧高能签的两条互相排斥（§3.3.2 的两个区间）；§3.3.4 去高区间实测了两趟，**两趟结论相反**（一趟 0 压缩还通过、一趟压 6 次却因 `max_turns` 判负），所以这条判据在这个题上没有稳定的被量对象 —— 不拧预算凑数，按未量记。顺带被这条臂钉出来的缺陷：`context_refuse` 带的是下一轮的轮号，它让导出器把"死在预算线上"的 run 全丢了（§3.9.1） |

| **11** ◐ | §3.4 `RepoMap` + `MemoryStore` + `.mcc/` | 8h | **B3** 的 A/B 报告（有/无地图）产出真实 delta；地图开销在 §6.2 预算内 → **实到（2026-09-22，`eval/results/b3-repomap-ab.json`）**：36 次真模型运行（6 题 × 3 × 2 臂，1,260,511 tokens），两臂只差 `--no-repo-map`；机制层 **7/7 全绿**（地图换掉目录树、净增 +783~+1,377 字符/题、首轮 est +235~+406 计入 `context_peak`、off 臂 0 条 `repo_map` 事件、两臂 system 逐题哈希不同、`llm_request` 33/33 相等、6 题模块未列出 0 项）；因果层 **未达成** —— `context_peak` p95 **+5.4%** ✓ 而 `steps_to_success` 中位 **6.0→6.0（降 0%）** ✗，防幸存者那条也 ✗（通过 14→13）。**这是一次有效的证伪**：地图便宜到几乎免费，但没把 A1 变快，20% 这条线不被本模型 × 本题集支持，线保持原样（见 §3.4.2）。§6.2 的账另核：每轮开销 0.0003ms ✓、进程内 memo 4.0~9.4ms 半数达标、跨进程缓存命中 27.9~99.4ms ✗（指纹纪律所致）、冷建 557~1,381ms 总中位 829ms ✗（见 §6.2.1） |

**Tier 1 结束时该项目就已经回答了 JD 第 7、8、13 三项**，且带着别人抄不走的证据：一条完整的"改动 → 配对回归 → 数字差值"链路。

## 7.2 Tier 2 — 放大到真实规模（25h，交付 B5、B6）

| Stage | 内容 | 工时 | 退出标准 |
|---|---|---:|---|
| ~~**12**~~ ⛔ | §3.5 只读并发（先由数据确认可并行轮占比） | 5h → **实际投入约 1h 测量后砍** | **B5**；7 项并发测试绿；若可并行轮 <20% 则整段推 Tier 3 → **实到（2026-09-22，`eval/results/s12-parallel-share.json`）**：轮占比合计 21.5% 过线、最新一层 b3-live 单独看 19.8% 差一线；第二道闸（B5 的墙钟 p50）实测上限 **0.011%**，与 15% 差三个数量级，fake 引擎也仅 0.127% → **整段推 Tier 3**，Tier 2 交付物改为只剩 B6。测法与读法见 §3.5.1 |
| ~~**13**~~ ✅ | §3.6 `ExecutionBackend` + Docker + 快照/`/undo` + durable resume | 10h → **实到 2026-09-22** | **B6**；`mcc resume` 杀掉进程后续跑且不重放写操作 → **实到**：协议两实现 + 影子 git 检查点 + `/undo` + `mcc resume`/`--list`/`--latest` + `MEMORY_DIR` 七消费者收敛，S13 合计 **115 例测试**、全批 595 passed。B6 判定层 **12/12 格一致、8 条前提全绿**（`eval/results/b6-backend-ab.json`），沙箱层**未测**（本机无 docker，替身跑在宿主上）。签名三处改动与全部边界见 §3.6.1 / §3.6.2 |
| ~~**14**~~ ✅ | §3.7 MCP bridge + Skills | 6h → **实到 2026-09-22** | 接一个真实 MCP server 跑通，6 项安全测试绿 → **实到**：S14 合计 **85 例测试**（`test_mcp_bridge` 44 · `test_cli_ext` 20 · `test_skills` 21）+ `mcp`/`skills` 两种事件的真实产地进 schema 快照（20 kind / 7 夹具）。`scripts/s14_ext_demo.py` → **`eval/results/s14-mcp-skills.json`：20/20 条量过并成立、verdict=pass、6.4s**；对端两个（自写敌意夹具 + **官方 SDK 写的服务**，后者才对得起"真实"两个字），6 项安全判据逐条有数字，见 §3.7.2。诚实边界：`resources`/`prompts`/取消/订阅零实现，非 stdio 砍到 §7.4 顺位 2，SDK 那臂只有一个服务三个工具。取舍与偏差见 §3.7.1，D26 的框架对照在 `docs/framework-equivalence.md` |
| — | A1 换成真实开源仓库（网络解禁后）+ 任务集扩到 30 | 4h | B1 在真仓库子集上重跑 |
| **15** ✅ | §3.8 verifier（先过数据闸）+ §3.9 trajectory exporter | 8h → **§3.8 实到约 1h 测量后砍；§3.9 实到 2026-09-23** | §7.3-1 那句「只在 B1 失败分布支持时做」执行完毕 → **实到（2026-09-22，`eval/results/s15-verifier-gate.json`）**：闸① live **1/75 = 1.3%**、fail-only 分母 **0/32 = 0.0%**、D21 点名的 fake 批自己 **12/72 = 16.7%**，三道一起都不过 20%，且命中的 run **全部判 pass**（分子记行为、不记代价）；闸②验证输出占工具总字符 **41.6%**，但只有 **2/148 次**越过 §3.8 那句「200 行」折成的 14,000 字符，两次都是 `bash` 绕开 `run_tests` 直跑 pytest。**12 条前提全绿、verdict=cut**。S15 交付物改为只剩 §3.9 的 exporter → **实到（2026-09-23，`mcc export-rl` → `eval/results/s15-export.json`）**：12 批次 **158 个 run → 832 行 / 1,698,325 字节 / 4.35s，丢弃 0**，**10/10 条前提成立**。`state` 分两个产地（A 快照前缀 26 个 run / 138 行带正文，配对逐轮 `tool_use` id 可验证；B 结构指纹 132 个 run / 694 行、占 83.4%，每行自述为什么没有正文）；reward 只有 pass/fail 两种终局；成对偏好 43 对（全是同题重跑）、SBS 人工 **0 条**。**顺带抓到三处上游数据完整性问题**：① manifest 与 trace 重跑后不再 1:1（首跑 195 个 run 里 28 个被导出前丢弃，已在 `runner.append_manifest` 源头修成"取代"+ 原子重写）；② `steps_from()` 把 `context_refuse` 那轮当一步，于是**死在预算线上的 run 全部轮数对不上**、14 例被静默丢弃（修在导出器，回归一条）；③ 快照查找曾越出批次目录。两处修完之后重跑：`dropped_runs.count = 0`。取舍、口径与"这份证据不证明什么"见 §3.9.1 |

## 7.3 Tier 3 — 前沿但克制（15h）

1. **`spawn_agent` verifier 形态（4h）—— 2026-09-22 被 §3.8.1 的数据砍到这里**（原条件「只在 B1 失败分布支持时做」，D21）。三条判据都不支持动手，而且闸②暴露出一个更值钱的结论：那 200 行是我们自己的模型绕开 `run_tests` 换来的，不是工具吐出来的。它要复活得先满足 §3.8.1 末尾三条触发条件之一。**不改 D21 那条 20%**：错法不是数字太大，是"分母没写、分子没接代价"，这两处已经实测钉住了。
2. ~~trajectory exporter + SBS 标注闭环（4h）~~ → **上移到 S15-b 做**（§3.9），2026-09-23 **exporter 已交付**（§3.9.1）。留在 Tier 3 的只剩**闭环的前半**：`--labels` 接口有了，盘上人工标注 **0 条** —— SBS 需要两个人看同一条轨迹，这一项在我们的预算里排在 B1 重跑之后。
3. OTLP 导出器（3h）—— 字段已在 S8 对齐，这一步只有翻译
4. 弱/强模型对照实验（4h）—— "弱模型 + 好架构 vs 强模型 + 糙架构"，v1 §7.3 许的愿，现在有了跑批能力才真的能还
5. **B3 的重测：换考卷而不是换线（4h）** —— S11 把 B3 证伪在 A1 形状上（§3.4.2：地图 +5.4% 峰值、0% 轮数下降）。要判断"符号地图到底有没有用"，需要的是**跨 ≥5 文件的改动题**与**陌生大仓库的只读问答**这两类形状，而现有 24 题里没有。额度按 36 次 ≈ 126 万 tokens 的那次实到估：换考卷重测一次 ≈ 再花一倍。排在 Tier 3 而不是立刻做，因为先要把题做出来（S13 之后有 durable 会话才跑得起长题）。
6. **B5 的复活条件（0h 决定 / 5h 实现）** —— S12 在 2026-09-22 被 §3.5.1 的数据砍到这里：可并行的只读轮平均只值 2.9ms，线程池无限大也只省 0.011% 的墙钟。它要重新成立，前提得换成**读取本身很贵**的任务形状（网络盘 / 单文件几十 MB / 一次读十几个跨仓库依赖），或者把并发对象从 READ 换成"`run_tests` 与模型下一轮重叠"（那已经不是 §3.5 的范围，是新的正确性问题）。**不做的事**：把 B5 的 15% 改成能过的数，或者拿 fake 引擎的 p50 签字 —— 那里的 LLM 是瞬时的，分母换成了"没有模型的世界"。
7. **manifest ↔ trace 同一性（2h，S15-b 顺带提出）** —— `append_manifest` 现在按 `(task, repeat)` 取代旧行，新批次 1:1 了；但**没有任何东西校验 trace 文件的内容就是 manifest 那行判据所依据的那份**。两个具体缺口：① trace 文件名不含内容哈希，手工改动或第二次覆盖都查不出来（导出器只能靠"轮数对不对"间接发现，这次抓到 14 例）；② `RunRecord.trace_path` 是绝对路径，把批次目录拷到别的机器/别的 checkout 就全部指空。要做的是在 manifest 行里补 trace 的**行数 + 末行哈希**（并附一份相对路径），导出器与 `mcc eval --baseline` 都改成先校验再 join。**为什么现在不做**：字段一变，盘上已有的批次就得双读或重跑，而 B1 那 72 次 fake 运行是唯一被基线引用的判据来源。已入库的 `eval/baselines/fake-0935fa95ca49.json` 本身不带 trace 路径，所以作废的不是基线，是"这批数据还能被逐行追认"这件事。触发条件：下一次需要跨机器复用批次，或再抓到一例判据与轨迹错配。**这条已经第二次触发**：同一道轮数校验又抓到 `steps_from()` 的幽灵步（§3.9.1），修法在导出器而不是校验器 —— 所以缺口 ①（没有内容哈希）依旧敞着。
8. **让 trace 落 observation（未排期）** —— §3.9.1 的核心发现：现在这份 RL 数据里 694/832 行（83.4%）只有指纹，因为 §3.1 按设计不存观测正文。要把它变成能训的数据，缺的不是训练算力，是**一次 §3.1 的改动**（正文进盘的体积上限、以及"仓库内容进不进日志"的隐私边界）。D23 说"不做训练"没变，所以这条排在那里等的只有一个触发条件：真的决定要做后训练。


## 7.4 超时砍单顺位（现在就定）

1. Tier 3 全部（1+2+3+4+5+6）—— 第 6 项（B5/S12）不是"等着被砍"，它已经在 2026-09-22 被 §3.5.1 的数据主动砍进这里了
2. `mcp` bridge 的 stdio 之外的传输方式
3. Docker 后端（保留 `ExecutionBackend` 抽象与 local 实现 —— 抽象是设计证据，实现可砍）
4. `compact` 的 L2（保留 L1 —— 它零风险、零额外调用，收益占大头）
5. Skills 的多文件引用（保留单文件 `SKILL.md`）
6. 失败分类学里证据不足的 2 条规则（分类器保留，规则数可少）

**永不砍**（这些砍掉，v2 的叙事就塌回 v1）：**度量产地检测器**、`test_trace_schema_snapshot`、**eval 断点续跑与批次预算**、`assert_pairing`、判据独立性、任务集哈希锁、"孤儿指标"免疫测试。

**特别点名一条不许砍、也不许"为了跑通而放宽"的东西**：任务集里出现失败用例是正常的、是有价值的（`no_verification`、`thrashing` 这些模式标签只有从失败里才拿得到）。**如果 24 个任务全绿，第一反应应该是怀疑判据，而不是庆祝。**

---

# 8. Risks

| # | 风险 | 缓解 |
|---|---|---|
| **R1** | ~~上下文窗口未知~~ **已关闭（2026-09-22 实测）**：`270,570 prompt_tokens` 的请求被正常接受，窗口 ≥ 270k，所以风险不是"会被 400"，而是反过来的 —— 原 `TOKEN_BUDGET=120000` 下压缩阶梯在 v1 任务分布上是死代码（峰值 12,185，压力 0.10） | 已落实：拆成 `CONTEXT_HARD_LIMIT=200000`（防拒收）+ `TOKEN_BUDGET=32000`（压缩阶梯挂它），§3.3/§5.3 已改写；B2 的验收任务改为**必须构造真会超预算的任务**，否则是在判一条到不了的分支。探测开销 ≈ 477k prompt token，证据 `demos/results/context-window.probe.json` |
| **R2** | live 成本失控：24×3=72 次真任务，按 v1 单次 20k–81k token 估，总量 1.5M–6M token | 批次预算闸（`EVAL_BUDGET_TOKENS`）+ 先 fake 全跑、live 抽 6 任务冒烟 + `PRICE_PER_MTOKENS` 一填就有钱数报表 |
| **R3** | "非本项目真实仓库"的前置条件仍未落实（v1 §10-1 的遗留）。A1/B1 说服力上限受限于 vendored fixture | 已降级为 Tier 2 的一项，不阻塞主线；SPEC 里把判据措辞改成"非 Agent 工作副本的独立仓库" |
| **R4** | **压缩让 agent 静默变笨**（丢关键事实但成功率高矮看不出来） | 摘要模板强制 7 个字段（尤其"已改动文件""被否决的路径"）+ B2 要求同一任务压缩前后对照 + `thrashing` 标签作为哨兵 |
| **R5** | 判据本身腐坏：fixture 里的 bug 被某次顺手修掉，评测悄悄变成"永远 100%" | v1 的 pristine 测试推广为 `test_taskset_sha_detects_tampering`；每次批跑前重算基线红绿，前提不成立的任务标 `INVALID` 而不是算进分母 |
| **R6** | `temperature=0` 的"可复现"只有 n=3 单提示的证据 | S9 第一件小事：同一任务同配置连跑 3 次验输出一致性；不通过就把 `pass_at_k` 的方差正式写进报表口径 |
| **R7** | 模型/端点漂移：v1 遇到过 `tools` 字段偶发被吞 | 系统提示里继续列工具清单（v1 已有的缓解）；trace 的 `llm_request.tools_count` 让漂移可被发现而不是被猜到 |
| **R8** | 并发正确性事故（交错写 trace、竞态计数） | §3.5 的 5 个竞争写点逐一有测试；默认 `MAX_PARALLEL_READS=1`，**并发是显式打开的实验开关，不是默认行为** |
| **R9** | Windows 特异性：`>NUL` 事故（v1 §10-5）证明"环境事实写错"会真实污染工作区 | Docker/WSL 相关文案一律"以实际执行器为准"；沙箱不可用只降级不假装；新环境事实进 `test_prompts.py` 钉住 |
| **R10** | 预算再次乐观：v1 的 56h 里 Stage 7（真实世界遭遇）几乎吃掉全部缓冲 | Tier 1 = 40h 是**独立可交付**的；即使后面全没做，v2 也已经拿到 B1。砍单顺位 §7.4 预先定死，避免临场决策 |
| **R11** | 样本量太小导致假显著（24 任务里一次翻转 = 4.2%） | D12 的"不可区分阈值"+ 报表纪律 §6.4 禁止裸百分比 |

---

# 9. 对外叙事：三句可追问的证据

面试/简历上这个项目在 v2 之后的说法（**每一句都必须能被现场演示或文件路径反驳**，否则不写）：

1. **"我没有加功能，我先加了度量。"** 支撑：`test_metrics_have_producers` 是怎么来的 —— v1 的 `redundant_calls` 是个定义了、进 trace 了、印在 9 份证据文件里、但从未被累加过的字段。**能指出自己项目里的假数字并把它变成一条 CI 不变式**，比"我做了个 agent"强得多。
2. **"改一处能立刻知道有没有变好。"** 支撑：`mcc eval-ab --a repo_map=off --b repo_map=on` 的逐任务配对表，以及 B3 那两条同时成立才算数的判据（轮数降 20% **且** 上下文峰值不涨超 15%）。
3. **"我知道哪些不做。"** 支撑：§3.10 那张带触发条件的表 —— 向量 RAG 为什么不做、异步为什么不重写全链路（D15：线程池拿 90% 收益、20% 改动）、后训练为什么不自己训（D23：改成交付别人要用的数据管道）。

JD 第 1 项（★★★★★）真正筛的是**判断力**，而判断力的可观察证据只有两种：砍掉的东西、和为留下的东西准备的检验。这两样 v2 都成文了。

---

## 附：三问的当前状态（2026-09-22）

1. **R1 上下文窗口探针** —— **已答，已做**。你选了「跑受控二分探针」，实测 270,570 prompt token 被正常接受 ⇒ 窗口 ≥ 270k。结论与其对 §3.3 的冲击已写进正文（`TOKEN_BUDGET` 32000 / `CONTEXT_HARD_LIMIT` 200000 拆分）。**这次探测的结论是"设计前提被证伪"，不是"确认了原来的拍值"** —— 如果没跑，S10 会按一条永远不会触发的分支写完并当成已验证。
2. **`tests/test_hanoi.py`** —— 未决，我按"保留 + 报表按目录分组"处理（不动别人的提交）。你要是想让它离开这个仓库，说一声我移到 `demos/` 之外并保留历史。
3. **实施节奏** —— **已答**：你选了「S8 度量修补开工」。我按 Stage 交付：S8 做完出报告并立刻进 S9，不攒批。
