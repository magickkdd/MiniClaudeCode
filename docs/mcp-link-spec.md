# mcp-link-spec · 把 Insight Agent 接成 MCC 的外部研究工具（MCP E2E 联动）

版本：v1.0-as-built · 日期：2026-09-29 起草 / 2026-10-01 实测回填 · 状态：**已执行（§9 为 as-built，数字全部来自当日实测）**
预算：4–6 净工时（含 demo 脚本）· 对齐 JD：晶远芯「多智能体系统」、九方智投「tools use」、所有 MCP 追问
关系：SPEC v2 §3.7（D19/D20，MCP 桥与 Skills）的**延伸验收**——桥的两端各自已交付（MCC 桥 + `tests/test_mcp_bridge.py`；Insight Agent 的 `mcp_server.py`），本文不新增协议代码，只做**真实互通**的接线、边界验证与演示。

---

## 0. 一句话定位

**不是新功能，是两个已交付系统的首次真实握手**：Insight Agent 已把研究能力注册成 MCP server（stdio），MCC 已实现 MCP 桥（client，stdio，命名空间隔离、远端 risk_level 不信任）。本文回答三件事：能不能接上、接上后长任务边界在哪、接上后它值不值得写进简历。

## 1. 拓扑

```
MCC 主循环 (agent/loop.py)
   └─ registry：本地 8 工具 + 桥注册的外部工具（命名空间前缀）
                   │ ext/mcp.py（stdio 子进程 + JSON-RPC 2.0）
                   ▼
       insight-agent mcp_server（uv run 子进程）
                   │ 转调内部 graph
                   ▼
       gate → recall → planner → Send×N research_one → compress
       → gap_analyzer → writer → verify(→ redteam) → archive
```

- 暴露的工具（mcp_server.py 已定）：`research(topic, depth)`（分钟级长任务）、`research_async(topic, depth)`、`get_notes(topic)`（秒级）、`list_archives()`（秒级）。
- 远端工具进 MCC 后：命名空间前缀（`ext/mcp.py::namespace`）防远端工具名劫持；`risk_level` 一律按 EXECUTE（D19：远端自报不可信）；`external=True` —— **AUTO 模式下仍需确认**（D19 的安全承诺：工作区内自动放行，外部工具外扩一步就越界）。

## 2. 配置（MCC `.env`，一个字段搞定）

```bash
MCP_SERVERS=[{"name":"insight-agent","endpoint":"uv","args":["run","--project","/path/to/insight-agent","python","-m","insight_agent.mcp_server"]}]
```

校验链（已有实现，不用改）：`config.py` 只做形状校验（JSON 数组/名字齐全）→ 语义校验收口在 `MCPServerSpec.from_mapping`（五键白名单、transport 白名单 = stdio、endpoint 必填）——"启动时就拒绝，不带病运行"。

Insight Agent 侧前置：其 `.env` 需要可用的 LLM 端点 + Tavily key；子进程环境由 `MCPServerSpec.env` 显式传键（桥刻意不带 PYTHONPATH——B6 条款）。

## 3. 预检清单（写代码前先实测的四件事，结果回填本文）

| # | 预检项 | 方法 | 通过标准 |
|---|---|---|---|
| P1 | 桥对单次工具调用有没有超时上限 | 只读 `ext/mcp.py` 调用路径 + grep timeout | 明确回答"有（值）/没有"；没有则 research 分钟级会挂住主循环——需要加 `MCP_TOOL_TIMEOUT`（默认建议 600s） |
| P2 | 命名空间前缀的实际形态 | 启动后 `/tools` 观察 | 记录最终工具名（如 `insight-agent__research`），确认与本地 8 工具无碰撞 |
| P3 | stdio 握手在 Windows + uv 下是否稳定 | 手动跑一次 tools/list | initialize 与 tools/list 各成功 1 次 |
| P4 | Insight Agent 单独跑通 | `python -m insight_agent.cli research "httpx timeout" --depth fast --json` | 先隔离 MCC 排除联合故障定位 |

## 4. 判据（四层；集成测试用替身 server，离线可跑）

| # | 层 | 判据 | 实现 |
|---|---|---|---|
| T1 | 握手与发现 | 配置生效后：启动不报 `MCPSpecError`；`/tools` 列出命名空间化的 3 个远端工具；trace 出现 `mcp` 事件（handshake + tools/list） | 手动 + trace 断言 |
| T2 | 功能 E2E | 任务"调研 httpx 超时配置的最佳实践，把结论写进 notes.md"：工作区出现 `notes.md`，内容包含报告的引用 URL（**判据读文件，不读模型自述**）；permission trace 记录 external 调用的确认过程 | `demos/` 或手动 + trace |
| T3 | 长任务边界 | `research(depth=standard)`（分钟级）在 MCC 预算内的真实行为：完成 / 撞 P1 超时 / 用户中断，三种形态如实记录，形成"长任务的正确用法"结论（如：演示用 fast，standard 离线跑） | 手动，trace 留证 |
| T4 | 回归 | 849 项测试全绿不回退；`tests/test_mcp_bridge.py` 全绿；**新增**替身 server 集成测试：最小 stdio JSON-RPC 回放脚本（tools/list + 一次 call），钉住 namespace 注册、external 确认、无 timeout 死锁三条路径——CI 离线可跑，不依赖真 insight-agent | 对齐 test_docker_backend.py 的替身文化 |

T4 的替身是本文唯一的**新代码**：一个 ~50 行的 Python 脚本（读 stdin 写 stdout 的 JSON-RPC 回放），不是对真 server 的依赖——两个真实系统联动的部分属于 T1-T3 手动验收。

## 5. 演示脚本（价值兑现）

1. 开场：两个项目各自介绍一句话（Harness 工程 / 可验证 Multi-Agent）；
2. `/tools`：展示本地 8 工具 + 命名空间化的 3 个远端工具同框；
3. 任务演示：T2 的调研任务——重点展示 permission trace（external 确认）与 `mcp` 事件；
4. `get_notes` 快速工具做第二轮演示（秒级，不冷场）；
5. 收尾：trace 里 mcp 握手 → 调用 → 回填的全链路截图。

## 6. 非目标

- 不做 HTTP / streamable-http 传输（§7.4 砍单顺位第 2 条，配置出现即拒绝的现状不变）；
- 不做 Insight Agent 反向调用 MCC（它不需要 Coding Agent）；
- 不做 MCC 内多远端 server 的编排策略（单 server 先跑通）；
- 不改 Insight Agent 内部图（redteam 等升级见 REDTEAM_SPEC.md，本文只消费它的 MCP 接口）。

## 7. 风险清单

| 风险 | 缓解 / 记录义务 |
|---|---|
| research 分钟级撞工具超时（P1） | P1 预检先行；无超时机制则补 `MCP_TOOL_TIMEOUT`；演示降级用 fast 档 |
| 研究输出超长撑爆 MCC 上下文 | 恰好是压缩阶梯的实战素材：L1 elide / L2 摘要的真实压力测试，trace 留证 |
| 远端进程崩溃 / 提前退出 | 桥现有错误路径回填 is_error 给模型自愈；验证这条路径并记录 |
| uv 在目标机不可用 | endpoint 改为绝对路径 python + `-m`；`env` 字段显式传 |
| 信号量=1 的并发拒绝 | 单客户端演示无影响；文档标注"多客户端并发会排队" |

## 8. 交付物清单

- [x] `.env.example`：`MCP_SERVERS` 示例补 insight-agent 条目（注释含 fast/standard 时延提示 + chdir 包装的原因）
- [x] `tests/test_mcp_bridge_link.py`：替身 server 三条路径（T4；替身 = `tests/fixtures/mcp_link_stub_server.py`）
- [x] `docs/mcp-link-spec.md` 本文补 as-built 段（§9）
- [x] MCC 侧联动段（两项目互通 + 拓扑图；原 README §4.16，README 精简后落在 [`experiments.md`](experiments.md) §4.16）；Insight Agent README 回链（其 README「亮点」节）
- [x] 演示脚本与 trace 归档（`demos/mcp-link-demo.md` + `demos/traces/mcp-link-*.jsonl` + T2 工作区现场）
- [x] 简历联动：§9.5 给出升级后的条目文案（简历与八股手册文件不在本仓库，粘贴即用）

---

## 9. as-built（2026-10-01 实测回填）

### 9.1 预检结果（P1–P4）

| # | 预检项 | 结果 |
|---|---|---|
| P1 | 桥对单次工具调用的超时上限 | **有，但只有一根线**：`_StdioSession.timeout`（缺省 30.0s，装配点 `cli/main.py` 未传参）管**所有**请求——initialize、tools/list、tools/call 共用。research fast 档直跑实测 89.3s ⇒ 固定 30s 必误杀。已补 `MCP_TOOL_TIMEOUT`（缺省 600s）：只管 tools/call；握手与发现仍走 30s 那根线（起不来的服务在会话开始时秒报错）。回归：`test_over_budget_call_fails_at_the_budget_and_recovers_immediately`——会话线 25s、预算 2s 的替身返回「回答超时（2s）」（报文里带的是**生效的那条预算**），超时后同服务的秒级工具立刻可用 |
| P2 | 命名空间前缀的实际形态 | `mcp__insight-agent__{research, research_async, get_notes, list_archives}`——**4 个**工具（本文 v1.0-draft1 写 3 个：`research_async` 是草稿之后加进 mcp_server.py 的，as-built 记实数）。与本地 8 工具零碰撞（重名注册表当场拒绝） |
| P3 | stdio 握手在 Windows + uv 下 | 稳定。`mcc mcp` 对真 server：initialize + tools/list 各成功 1 次起；当天 E1/T2/T3 多轮会话全部一次握手成功，未复现过握手失败 |
| P4 | Insight Agent 单独跑通 | `python -m insight_agent.cli research "httpx timeout" --depth fast --json` → **89.3s** 完成（exit 0）；`--depth standard` → **90.9s**。其 `.env` 的 LLM 端点与 Tavily key 可用 |

### 9.2 接线中的真问题：§2 原配置握得了手、起不了 research

§2 逐字配置下：握手 ✓、tools/list ✓、`get_notes`/`list_archives` 可用，**research 必失败**。根因是两件事的夹缝：

1. 子进程 env = 桥的最小白名单 + `spec.env` 点名，而 `_SECRETISH` 把 `*api_key*` 类名字**一律扣下**（B6：`.env` 里那把 key 不该因为装了第三方 MCP 服务就流进别人的进程）——`LLM_API_KEY`、`TAVILY_API_KEY` 点名也传不过去，**这是设计不是缺陷**；
2. insight-agent 的 `load_settings(".env")` 按**子进程 cwd** 解析，子进程继承 MCC 的 cwd ⇒ 它读到的是 MCC 的 `.env`，缺 `LLM_MODEL_ID` / `TAVILY_API_KEY`。

实测（E1，spec 原配置 + 一次性任务 + 管道确认）：`tools/call research` → server 端 `RuntimeError: 缺少配置项: …` → 官方 SDK 包成 `isError` 报文（正文只有 29 字符的 `Error executing tool research`）→ 桥 `_decode_content` 变 `ToolResult(is_error)` 回给模型，模型自述三种可能原因后收尾。**§7 风险表第 3 行（远端报错 → is_error → 模型自愈）由这次真实故障钉住**——不是替身摆的，是真撞上的。

**修法（零协议改动）**：args 里加一层 `-c` 包装，子进程起手先 `os.chdir` 回 insight-agent 项目根再 `main()`，让它读自己的 `.env`——密钥不复制、不透传、两项目各持各的钥匙。最终配置见 `.env.example` 的 insight-agent 条目（注释写明 chdir 的原因）。

### 9.3 T1 / T2 / T4：真实 E2E 实测（2026-10-01）

- **T1 握手与发现**：`mcc mcp` → 「配置了 1 个服务：insight-agent / 握上手 1 个 · 外部工具 4 个 · 弃用 0 个」，四个工具各标「远端自报 未声明，采信 execute（每次都要确认，AUTO 也不例外）」；会话 trace 落 `mcp` 事件（turn=0，含各 server 状态与工具清单）。
- **`/tools` 同框**：本地 8 工具（read_file / search_text / find_files / edit_file / write_file / bash / run_tests / write_todos）+ 4 个 `mcp__insight-agent__*`，远端全部 `execute` + `[MCP/insight-agent]` 描述前缀。存档：`demos/mcp-link-demo.md`。
- **秒级工具直连探针**：`get_notes(topic=httpx超时配置最佳实践)` 回证据笔记（≥4000 字符截断），`list_archives` 的档案列表里就有 T2 刚写入的主题——四个远端工具中三个有真 server 实调证据；`research_async` 未单独实调（同一 graph 的 to_thread 包装）。
- **T2 功能 E2E**：任务「先调用 mcp__insight-agent__research（depth=fast）做调研，然后把结论写进 notes.md」，工作区 = `demos/.work/mcp-link`，trace = `demos/traces/mcp-link-t2.jsonl`：
  - `notes.md` 落盘，含 **9 个引用 URL**（含 `https://www.python-httpx.org/advanced/timeouts`）——**判据读文件，不读模型自述**；
  - trace：`permission`×2 = `mcp__insight-agent__research → allow`（管道喂的确认）+ `write_file → allow`（AUTO 对本地写放行，同会话内的 §6.3-1 对照组）；`tool_call`×2；`mcp`×1；
  - 研究档案落在 **insight-agent 项目根** `data/notes/httpx超时配置最佳实践.json`——chdir 包装生效的旁证：MCC 工作区只收报告文本，记忆归对端管；
  - 用量：3 轮 · 输入 18,161 + 输出 2,675 tokens（MCC 侧，模型 agnes-2.5-flash）。
- **T4 回归**：全量 **873 项全绿**（本文起草时为 849，差额来自其他批次的新增，无回退）；`tests/test_mcp_bridge.py` 44 项全绿；新增 `tests/test_mcp_bridge_link.py` 6 项全绿——替身 `tests/fixtures/mcp_link_stub_server.py`（~90 行，按真 server 工具面逐字回放，argv 给 research 应答延迟）钉住三条路径：namespace 注册（4 工具名 + 本地 8 工具同注册表）、external 确认（AUTO ask + 人点头后调用回填）、无 timeout 死锁（预算内长调用完整返回；超预算调用在预算处 `is_error` 且下一发秒级工具立即可用）。

### 9.4 T3：长任务边界——三种形态齐了，外加第四种如实记录

| 形态 | 实测 |
|---|---|
| **完成** | research(standard) 过桥：第一次调用 64.9s 后死于残余的云端 429（29 字符 `Error executing tool research` 的 isError），**模型原参数重发，第二次 ≈300s 完成**——报告 3,327 字符回填，`standard-notes.md` 落盘含 5 个引用 URL，两次调用都在 600s 预算内。research(fast) 的完成形态见 T2（126.6s）。「分钟级长任务挂住主循环」被 P1 拆出的第二根线证伪 |
| **撞超时** | 替身路径钉住（`tool_timeout=2s` + 延迟 10s 的替身 → 「回答超时（2s）」→ 下一发立刻可用）；真 server 未复现——600s 对两档都富余 |
| **用户中断** | 未实测（管道确认的自动化跑法里没有中断点）；桥侧已有路径：`call()` 永不抛异常，中断由会话层收尾时 `session.close()` 收进程 |
| **（第四种）云端配额** | 配额烧穿窗口内 research(standard) 过桥 5 次，全部死在 **insight-agent 内部**的 agnes 429；额度恢复后的第 6 次仍先撞了一次残余 429（64.9s），**模型重发后成功**——对端系统的故障会以 is_error 的形状出现在工具结果里，模型拿到的不是异常栈而是可以据此行动的一句话 |

时延汇总（注意：**深度不等于墙钟**——本样本里同一次 standard 先 64.9s 后 ≈300s，fast 过桥 126.6s，运行间波动大于 fast/standard 的差；研究 graph 的逐节点 LLM/Tavily 时延是主导项）：

| 路径 | 实测 |
|---|---|
| insight-agent 直跑 fast / standard | 89.3s / 90.9s |
| MCC→桥 research(fast) | 126.6s |
| MCC→桥 research(standard) | 64.9s（残余 429 失败）/ ≈300s（重发成功） |
| MCC 全任务（research fast + 写 notes.md） | 153.6s（3 轮） |
| 子进程冷启动（uv run + import，E1b 首次调用） | ≈6.3s |

**长任务的正确用法（演示口径）**：演示用 fast + `get_notes`（秒级）补第二轮；standard 离线跑——它的墙钟波动（1–5 分钟）不适合现场节奏。600s 预算对两档都富余；信号量=1 意味着多客户端会排队（server 返回「已有研究任务在执行中」），本文单客户端未测并发。

### 9.5 简历 / 八股联动文案（目标文件不在本仓库，粘贴即用）

**简历项目一「记忆与生态扩展」条升级为**：

> MCP 桥已接通自研 Research Agent（E2E）：stdio JSON-RPC 真实握手，命名空间隔离（远端工具永不能覆盖本地）+ 远端自报风险不信任（一律按 EXECUTE）+ 外部工具逐次确认（AUTO 也不例外）；长任务超时分层（握手 30s / 工具调用 600s 可配），替身回放钉住三条边界路径，873 项回归全绿。

**八股手册 §9 MCP 的【项目答】升级为**：

> 两端实现且真实互通：我既写过 MCP server（自研 Research Agent 暴露 research/get_notes/list_archives 四个工具），也写过 client（自己实现的 stdio JSON-RPC 桥，不依赖官方 SDK 的客户端部分，但用官方 SDK 写的对端验证过互操作）。接通时撞出的真问题有两个：一是我按安全条款不透传任何密钥类环境变量，而对方的配置按子进程工作目录找 `.env`，原样接线时 research 拿不到 key——解法是让子进程回自家项目根读自己的配置，而不是放宽密钥透传；二是分钟级长任务会撞固定 30s 的请求超时，解法是把握手与工具调用的预算拆成两根线。这些都以 trace 与回归测试留证。
