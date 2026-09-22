# Mini Claude Code

一个跑在终端里的软件工程 Agent：你用自然语言下达开发任务，它自己读代码、改文件、跑命令、看测试结果，反复直到做完或者明确说清楚它卡在哪。

它不是一个"调 API 的 demo 骨架"，而是一套**可验证闭环**：核心循环、权限边界、工具层、会话轨迹、评测证据，每一层都有测试或独立判据撑着。项目按 [`SPEC.md`](SPEC.md) 与 [`SPEC-v2.md`](SPEC-v2.md) 逐阶段实现，SPEC 的决策记录（v1 的 D1–D8、v2 的 D9–D27）解释了每个取舍的原因。

下面这段是 `demos/results/codegen.fake.md` 里的真实终端输出（Demo 1，任务只有一句话："创建一个 Python 计算器项目，并编写测试"）：

```text
你 > 在当前目录创建一个 Python 计算器项目……写完整的 pytest 测试并跑到全绿
✓ write_todos 4 步
任务清单
  [~] 建 calculator 包：四则运算 + DivideByZeroError
  [ ] 加 evaluate：乘除优先于加减
  ...
✓ write_file  calculator/core.py
✓ write_file  tests/test_calculator.py
✓ run_tests     863ms          ← 红了
`2 + 3 * 4` 得到 20，说明我按从左到右一路算了。改成两遍扫描：先折叠乘除，再算加减。
✓ write_file  calculator/core.py
✓ run_tests     980ms          ← 全绿
```

注意最后那句自我更正：**优先级 bug 是测试抓出来的，不是模型看出来的**。这就是 `run_tests` 作为判据而不是装饰的意义。

当前状态：**680 项测试全绿**。v2.0 的 S8 把"数字怎么来的"修成可核对的口径（trace schema 2.0、发起数与执行数分离、8 条失败模式规则、`mcc trace --why-failed`、[`demos/results/failure-labels.md`](demos/results/failure-labels.md) 的 16 条人工核对表）；S9 交付了评测层（**B1**）：24 道考题 × 3 次的 fake 全批 72 次运行 `pass@1=20/24`、退出码 0，基线 `eval/baselines/fake-0935fa95ca49.json` 已入库，另有 6 题 live 冒烟 `4/6`（证据与两道失败各自的成因见 [`eval/results/`](eval/results/)）；S10 交付了上下文压缩阶梯（**B2 的 fake 侧**，见 §4.7）：同一道必然超预算的题，关阶梯第 5 轮死在 `l3_refuse`、开阶梯 19 轮全绿，12 条判据与三臂数字落在 [`eval/results/b2-compact-ab.json`](eval/results/b2-compact-ab.json)；真实端点那 5 次今天签不了字 —— 试跑被 `HTTP 429`（免费档速率限制）打断，过程与一个已修的 `--only` bug 记在 [`eval/results/b2-live-blocked.json`](eval/results/b2-live-blocked.json)，所以这一条验收线只算完成一半；S11 交付了仓库符号地图与 `.mcc/` 工作记忆（见 §4.8）：两臂只差 `--no-repo-map` 一个开关，7 条机制判据（地图**换掉**目录树、地图计入 `context_peak`、零额外 LLM 调用、两臂 system 不同）离线全绿；真实模型那半条跑完了，结论是 **B3 未达成** —— token 侧 ✓（`context_peak` p95 涨幅 +5.4%，线是 ≤15%），轮数侧 ✗（`steps_to_success` 中位 6.0 → 6.0，降幅 0%，线是 ≥20%），通过数还从 14/18 掉到 13/18。这是一次有效的证伪而不是无效实验（脚本先证明了配对成立），20% 这条线保持原样，重测计划记在 SPEC §7.3-5，全部数字见 [`eval/results/b3-repomap-ab.json`](eval/results/b3-repomap-ab.json)。 S12（只读工具并发）在动手前被自己的数据闸砍进 Tier 3：可并行的只读轮只值 live 墙钟的 0.011%（§4.9）。S13 交付了执行后端：`ExecutionBackend` 两实现（local / docker）+ 影子 git 检查点 + `mcc resume` 的幂等续跑，**B6 达成** —— 同一批题在两后端上判定逐格一致 12/12，8 条前提全绿，而这台机器上没有 docker，所以那半条线是被一个"会真的在宿主上执行命令"的假 docker 证掉的，`container_isolation_tested: false` 就是这句话（§4.10、[`eval/results/b6-backend-ab.json`](eval/results/b6-backend-ab.json)）。S14 交付了第三方能力：`ext/mcp.py`（stdio 桥、`mcp__` 命名空间、远端自报风险一律不信）与 `ext/skills.py`（常驻只有目录、正文按需展开），§3.7 的 6 项安全测试扩到 85 项，证据脚本 **20/20 条前提全绿**、对手是"自写的敌意服务 + 官方 SDK 服务"两个（§4.11、[`eval/results/s14-mcp-skills.json`](eval/results/s14-mcp-skills.json)）；LangGraph 那一半交付的是 ≤200 行的概念对照与不用它的理由（[`docs/framework-equivalence.md`](docs/framework-equivalence.md)）。S15-a 是多 Agent 那一格的**第二次动手前砍单**：`spawn_agent` 的前置条件（D21「未收尾/自我确认过早占比 > 20%」）被 `scripts/probe_verifier_gate.py` 在 75 次 live 运行上量成 **1/75 = 1.3%**（D21 点名的 B1 fake 批自己也只有 12/72 = 16.7%），12 条前提全绿、verdict=cut，S15 交付物改为只剩 §3.9 的轨迹导出器；测量顺带钉出两处口径缺陷 —— 分子记的是行为不是代价（命中的 run 全部判 pass），以及 §3.8 那句"200 行 pytest 输出"在盘上只出现在模型**绕开** `run_tests` 用 `bash` 直跑 pytest 的那 2 次（结构化那条路 95 次调用单次最大 4,263 字符）（§4.12、[`eval/results/s15-verifier-gate.json`](eval/results/s15-verifier-gate.json)）。4 个 demo 仍在真实端点上跑通（`--engine fake` 5/5、`--engine live` 4/4），全部数字由脚本从 trace 自动生成。路线图见 [`SPEC-v2.md`](SPEC-v2.md)。

---

## 1. 项目介绍

### 1.1 目标

垂直可用：在一台机器上，对着一个陌生 Python 仓库，把"自然语言任务"变成"验证过的代码改动"。四件事必须真的成立：

| | 能力 | 怎么被证明 |
|---|---|---|
| A1 | 在陌生仓库按一句话描述修 bug，修到测试转绿 | `demos/results/bug-hunt.*.md`、`red-tests.*.md`：独立跑 pytest 看退出码 |
| A2 | 从零生成一个带测试的可运行模块 | `codegen.*.md`：独立脚本校验 `evaluate` 语义，不看模型自述 |
| A3 | 只读模式下能读懂代码并回答"这段对不对" | `readonly-qa.*.md`：工作区哈希与基线逐位一致 |
| A4 | 需求自相矛盾时在预算内放弃，不谎报成功 | `giveup.fake.md`：终止原因必须是 `max_turns`，且没改测试作弊 |

### 1.2 明确不做（V1/V2 的事）

多 Agent、VLM、RL、GUI、大规模 RAG、自动上下文压缩、跨会话记忆。SPEC §3.5 的结论是 MVP 阶段**只观测上下文，不干预**：压缩会破坏 `assistant.tool_calls` 与 `role=tool` 的配对，端点直接 400，排查成本远高于一次明确的"任务太大，请缩小范围"。

> 这份"不做"清单里有两条在 v2.0 被推翻了：自动上下文压缩 = S10（§4.7，先把配对不变式 `assert_pairing` 做出来再压），仓库地图与 `.mcc/` 工作记忆 = S11（§4.8）。**跨会话的"经验教训"仍然不做** —— SPEC v2 §6.3-2 的理由成立：一次错误观察会持续误导后续会话，而它没有判据能把自己纠正回来。

---

## 2. 架构图

```
              ┌──────────────────────────────────────────────────────┐
   用户 ────▶ │  cli/     REPL · argparse · 斜杠命令 · Renderer       │
              │           build_session() 是全项目唯一装配点          │
              └──────────────────────┬───────────────────────────────┘
                                     │ Agent.run(任务) → AgentResult
              ┌──────────────────────▼───────────────────────────────┐
              │  agent/                                              │
              │   loop.py         Ask → Act → Observe 循环           │
              │   planner.py      TodoList（多阶段任务的状态）       │
              │   prompts.py      系统提示：环境事实 + 仓库地图      │
              │   context.py      token 估算、压力告警               │
              │   permissions.py  权限门 ASK / AUTO / READONLY       │
              │   state.py        TerminationReason + 全部计数       │
              └───────┬───────────────────────────────┬──────────────┘
        ask(model)    │                               │  invoke(call)
              ┌───────▼────────────┐      ┌───────────▼──────────────┐
              │  llm/              │      │  tools/                  │
              │  base.py   协议    │      │  registry.py   注册表    │
              │  openai_compat.py  │      │  read_file  write_file   │
              │  httpx，不装 SDK   │      │  edit_file  search_text  │
              └───────┬────────────┘      │  find_files bash         │
                        │                  │  run_tests               │
                        │                  │  workspace.py  路径锁    │
                        │                  └───────────┬──────────────┘
              ┌─────────────────────────────────────────▼────────────┐
              │  memory/ 工作记忆 + 符号地图   ext/ 第三方能力        │
              │                    两者都只经 extra_tools=[…] 注入    │
              │                    （ext: mcp.py 桥 · skills.py 目录）│
              └─────────────────────────────────────────┬────────────┘
              ┌─────────▼──────────────────────────────▼──────────────┐
              │  messages.py（报文与 Block）· config.py（唯一读环境   │
              │  变量的地方）· infra/trace.py（JSONL 轨迹 + 密钥脱敏）│
              └───────────────────────────────────────────────────────┘
```

三条硬约束（违反就会让测试和 demo 失去意义）：

1. **单向依赖**：`cli → agent → (tools | llm | memory | ext) → messages/config`。`tools/` 永远不 import `agent/` —— 所以 `write_todos` 这个工具住在 `agent/todo_tool.py`，通过 `ToolRegistry.default(extra_tools=[...])` 注入，而不是塞进 `tools/`。S11 的 `memory/` 同一条规矩：它只 import `tools/workspace.py` 拿路径锁，不认识 `agent/`，地图由 `agent/loop.py` 反过来喂焦点。S14 的 `ext/` 是同一条规矩的第二次应用：`RemoteTool` 与 `LoadSkillTool` 都是普通 `BaseTool`，权限门与 trace 因此**不需要为外部能力改一行**。
2. **一个装配点**：`build_session()` 同时服务 REPL、`--task` 一次性模式和 `demos/run_demo.py`。否则"demo 跑通的东西"和"用户手上跑的东西"就不是同一个东西。
3. **CLI 层零业务逻辑**：本文件里没有 `while` 循环控制 Agent，交互归 CLI，控制归 `agent/loop.py`。

---

## 3. 安装

需要 Python 3.11+（用到 `StrEnum` 和 `is_relative_to`）。

```bash
git clone <repo> && cd mini-claude-code
python -m pip install -e ".[dev]"        # 只要核心：pip install -e .
cp .env.example .env                      # 填你自己的端点、密钥、模型名
```

`.env` 已在 `.gitignore` 里，密钥不会进版本库。任何打印路径都过 `Config.redacted()`，trace 落盘前过 `_scrub()`，所以密钥不可能出现在日志里。

不想安装也能跑：

```bash
python main.py                    # 零安装入口，直接进 REPL
python main.py --help
```

装了之后有三种等价入口：`mcc`、`python -m miniclaude`、`python main.py`。

---

## 4. 使用方式

### 4.1 交互模式

```bash
python main.py                              # 默认 ask 模式，逐次确认写操作
python main.py --root D:\path\to\repo       # 指定工作目录（唯一可写范围）
python main.py -y                           # auto：工作区内自动放行，破坏性命令仍拒
python main.py --readonly                   # readonly：只允许只读工具
python main.py -v                           # 打印轮次分隔线与工具输出摘要
```

进入 REPL 后直接说人话：

```
你 > tests/test_cart.py 里红的用例修一下，别改断言
你 > 这个仓库的 configure() 有没有资源泄漏？只说，别动手
```

第一次上手最容易跑通的一条，是一次性任务（下面是真实一次运行的完整输出，`-y` 表示工作区内自动放行）：

```console
$ python main.py --root .smoke/calculator-demo --no-trace -y "帮我创建一个Python计算器项目，并编写测试"
我来帮你创建Python计算器项目并编写测试。先看看当前目录的情况。
✓ bash        ls -la  46ms
目录是空的。我来创建计算器项目和测试文件。
✓ write_file  src/calculator.py
✓ write_file  tests/test_calculator.py
现在运行测试验证：
✓ run_tests   extra_args=['-v']  808ms
完成！创建了以下文件：
- src/calculator.py - 提供 add、subtract、multiply、divide，以及支持表达式字符串的 calculate
- tests/test_calculator.py - 29 个测试用例，覆盖基本运算、边界情况、错误处理
所有测试通过。
用量：4 轮 · 输入 14,491 + 输出 1,488 = 15,979 tokens
$ echo $?
0
```

判定方独立复跑那个目录：`29 passed`。

### 4.2 一次性任务（脚本 / CI 用）

```bash
python main.py --task "给 duration 模块补跨天的测试并跑到全绿" -y
echo $?        # 0 = COMPLETED，1 = 其他终止原因
```

`--task` 走的是同一条 `run_turn()` 路径，退出码直接来自 `AgentResult.succeeded`。

### 4.3 斜杠命令

| 命令 | 作用 |
|---|---|
| `/help` | 命令列表 |
| `/reset` | 清空对话历史与任务清单 |
| `/tools` | 工具名 + 风险级别 + 一行说明 |
| `/context` | 消息条数、估算 token / 预算、端点上次实测 prompt_tokens、累计 token / 上限、轮数 |
| `/todos` | 当前任务清单 |
| `/backend` | 执行后端 + 检查点栈 + 会话现场三合一面板（§4.10）|
| `/undo` | 回退到上一个检查点。撤的是磁盘，**对话历史不倒带** |
| `/mcp` | 外部工具清单：名字、远端自报的风险、我们实际采信的档位（§4.11）|
| `/skills` | 技能目录（正文不常驻，`load_skill` 按需取）|
| `/mode ask\|auto\|readonly` | 切权限模式 |
| `/trace` | 会话日志位置 + 轮数/工具数/报错数/终止原因 |
| `/exit` | 退出（Ctrl-D 同效） |

Ctrl-C 的语义是**放弃当前输入但保留历史**：中断不该毁掉已经做一半的工作，紧接着输入"继续"就能从当前位置往下走。

### 4.4 权限模式

| 模式 | 只读工具 | 写/执行 | 破坏性命令 |
|---|---|---|---|
| `ask`（默认） | 放行 | 逐次问你（`y` 一次 / `a` 本会话同类都放行 / `n` 拒绝） | 无条件拒绝 |
| `auto`（`-y`） | 放行 | 工作区内自动放行 | 无条件拒绝 |
| `readonly` | 放行 | 拒绝 | 拒绝 |

两个不可让的细节：

- **路径锁**在 `Workspace.resolve` 这一层，不在门后面。越界路径根本解析不出可写目标，`..` 逃逸和绝对路径外部写入一并挡住。
- **确认失败关闭**：没有确认渠道（管道输入、demo 里那个恒定返回"拒绝"的 confirmer）时，任何看不懂的答复都按拒绝处理，绝不默认放行。

### 4.5 配置项

真实环境变量优先于 `.env`，方便 CI 覆盖。

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | 必填 | OpenAI 兼容端点 |
| `LLM_API_KEY` | 必填 | 密钥 |
| `LLM_MODEL` | 必填 | 模型名 |
| `PROJECT_ROOT` | `.` | 工作目录，Agent 唯一可写范围 |
| `MAX_TURNS` | 25 | 单次任务最大轮数 |
| `MAX_TOKENS` | 4096 | 单次响应上限 |
| `MAX_TOTAL_TOKENS` | 800000 | 累计 token 止损线 |
| `BASH_TIMEOUT` | 60 | 单条命令秒级超时 |
| `TOOL_OUTPUT_LIMIT` | 30000 | 工具输出回填上限（字符） |
| `TOKEN_BUDGET` | 32000 | 上下文估算预算 —— **压缩阶梯挂这个**（v1 的 120000 已下调，理由见 §4.7） |
| `CONTEXT_HARD_LIMIT` | 200000 | 只防一件事：请求被端点拒收。独立熔断 `est ≥ 0.9 ×` 它，与 `TOKEN_BUDGET` 必须严格更大的小于关系由 `config.py` 加载时校验 |
| `CONTEXT_COMPACT` | 1 | 设 `0` 整个压缩阶梯不开（L3 拒载与硬熔断照旧）。评测里对应 `--no-compact` |
| `REPO_MAP` | 1 | 设 `0` 不画符号地图，system 退回 v1 的 30 行目录树。**关地图只有这一个开关**，评测里对应 `--no-repo-map`（B3 的对照组） |
| `REPO_MAP_TOKENS` | 1500 | 地图的 token 预算（纯预算，至少 1；`<1` 在 config/CLI/runner 三处各自拒绝）。它**计入** `context_peak`，所以 B3 的第二条判据量的是含地图的口径 |
| `LLM_REQUEST_TIMEOUT` | 120 | HTTP 超时 |
| `TRACE_PATH` | 空 | 会话 JSONL 落盘位置 |
| `EXECUTION_BACKEND` | `local` | `local` / `docker` / `auto`。默认不是 `auto`：那会让同一个 `.env` 在不同机器上跑出不同后端，而 B6 问的恰恰是"换后端换不换结论"（§4.10）。打错的名字启动即失败 |
| `DOCKER_IMAGE` | `python:3.12-slim` | `DockerBackend` 用哪个镜像 |
| `DOCKER_NETWORK` | `none` | 空串 = 不传 `--network`（走宿主网络），仅本地排障用 —— 默认无网络是沙箱语义的一部分 |
| `CHECKPOINTS` | 1 | 设 `0` 不建影子 git（只读演练与容器内跑批用），同时 `/undo` 会说清楚为什么不能用 |
| `MEMORY_DIR` | `.mcc` | 记忆 / 快照 / 会话快照的**唯一**落盘目录名，七个消费者一起跟上（§4.8）。`../outside` 这类敌对值启动期拒绝 |
| `MCP_SERVERS` | 空 | JSON 数组，每项 `{"name","endpoint","args","env"}`；空 = 一个外部工具都不接。形状在 `config.py` 校验、语义在 `MCPServerSpec.from_mapping`，**只有一个产地**（§4.11）|
| `SKILLS_DIR` | `skills` | 技能根目录。目录不存在就等于没有技能（不报错、不装配 `load_skill`）|

### 4.6 批量评测（`mcc eval`）

demo 是"4 个案例各跑一次给人看"，评测层是"24 道题 × 3 次，报一个能被反驳的数字"。两边的判据是**同一份实现**（`eval/contract.py`），所以不存在"评测层顺手放宽了标准"这种分叉。

```bash
mcc eval --list                        # 24 道题各自的模式 / 判据规模 / fake 剧本
mcc eval --lint                        # 考题自检：这道题**可能**被做对吗（送分用例、基线就红的 P2P）
mcc eval --repeats 3                   # 默认 engine=fake：秒级、不联网、不读 .env
mcc eval --repeats 3 --save-baseline   # 跑完写 eval/baselines/fake-<题集哈希>.json
mcc eval --smoke                       # 6 题 live 冒烟（自动跳过 supports_live=false）
mcc eval --engine live --budget-tokens 200000 --tag bugfix
mcc eval --only bh-format-duration --repeats 5 --no-resume
```

**退出码就是结论**：`0` 这批可以拿去汇报；`1` 有运行崩了 / 有该做对的题没做对 / 检出退步或判据告警；`2` 用法或配置不对（一格数据都没产生）。

| 产物 | 说明 |
|---|---|
| `eval/.work/<engine>/report.md` | 人读报表：逐题判定、失败模式分布、标签切片、与基线的差值 |
| `.../report.json` | 同一份内容的机器形状，CI 里 `jq -e '.summary.regressions == []'` 就能拦住退步 |
| `.../manifest.jsonl` | 一条 run 一行，**跑完即落盘** —— 断点续跑的全部依据 |
| `.../work/<task>.r<repeat>/` | 每题每次一份独立工作副本；基线树永不被写（已进 `.gitignore`） |

几条不是从嘴上来的口径：

- **fake 批次同时是 gold-patch 检查**。剧本走真 agent 循环、真工具落盘、真跑 pytest，所以 `pass` 是代码给的，不是剧本声称的；`edit-verify` 改错了地方照样判红。
- **负样本按设计判红**。题集里 4 道带 `must-fail`（改断言作弊、猜路径、只读题瞎改、宣称成功却不测试），判红才算数；被判绿会触发"判据告警"并占退出码 1。`negative` 与 `must-fail` 不是一回事 —— 前者只说"这题模拟坏行为"，后者说"判据必须抓住它"。
- **基线按题集哈希自动匹配**。改任何一道题的题面或 fixture，哈希就变，旧基线不再被拿来对比（默认拒绝开跑；`--force` 可强行跑，但报表照旧标"无法对比"）。
- **`|Δ| < 12.5%` 一律写"分辨不出"**。显著性用 McNemar 精确二项而不是卡方近似：n=24 时一次翻转就是 4.2%，卡方在小样本上恰好把 p 算得偏小。

### 4.7 上下文压缩阶梯（长任务为什么不炸）

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
PYTHONPATH="src;demos" python -X utf8 scripts/b2_compact_ab.py # 三臂 + 12 条判据 → eval/results/b2-compact-ab.json
```

实测（`lc-rollup-api`：`ledger/` 八模块 194,356 字符，逐字抄签名再写汇总层；数字是 S11 之后重跑的）：关阶梯第 5 轮 `est=32,609` 越 `l3_refuse`（阈值 30,400）判 `context_overflow`；开阶梯 19 轮全绿、14 次压缩、0 次因配对放弃、0 个配对 400。这条死因不是靠人转述的 —— `context_refuse` 记录自带 `line / threshold_tokens / est_tokens / ladder_enabled`，因为 `turn_start` 每轮只在请求前采样一次，光看 est 序列会把"预算杀掉的会话"读成"模型自己停了"。

一个后来才发现的耦合：system 里换上符号地图，把这几格数字都推动了，而且方向不一致 —— off 臂更早死（越线 est 31,287 → 32,609），on 臂峰值反而降了（26,278 → 21,633，阶梯提前动手），代价是 L2 的摘要开销从 3,900 涨到 5,850 tokens。12 条判据两种配置下都全绿，结论没变，但"地图挤占上下文预算"这件事第一次有了数值形状。

**live 那一半（真实端点 5 次、成功率 ≥60%）今天签不了字**：试跑撞上 `HTTP 429 您已达到免费用户的 API 速率限制`，42 次运行里 28 次终止于 `llm_failure` —— 分母是速率限制打出来的，不是阶梯打出来的，所以它既不算达成也不算证伪。这一趟的净收益有两条：意外跑到的 30 次真端点压缩里 **0 次因配对放弃、0 个配对 400**（旁证，不替 live 臂签字），以及抓到一个会把 1 题探针放大成 17 题的 bug（`mcc eval` 在 `--engine live` 下用 `supports_live` 全集覆盖了 `--only`，已修 + 三条回归测试）。过程与重跑命令记在 `eval/results/b2-live-blocked.json` 与 SPEC §3.3.3。

### 4.8 仓库符号地图与工作记忆（`memory/`，接手陌生仓库）

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

### 4.9 为什么没有并发：一次动手前的砍单（S12 → Tier 3）

SPEC v2 §3.5 原本排了 5 小时做"只读工具并发"（`ThreadPoolExecutor` + 五个竞争写点 + 7 项并发测试），并且写死了砍单条件：**可并行轮占比 < 20% 就不做，这条判断由数据做，不由我做**。S12-a 先把这道闸做成了脚本（`scripts/probe_parallel_share.py`，只读盘上轨迹、不写一行调度代码），结论比砍单条件更硬：

| 闸 | 线 | 实测（46 次 live 运行 / 261 个工具轮） |
|---|---|---|
| 可并行轮占比 | ≥ 20% | 合计 **21.5%**，最新一层 b3-live 单独看 **19.8%** —— 一层过一层不过，差一个轮 |
| 墙钟 p50 下降（B5 那句话） | ≥ 15% | 线程池**无限大**的上界也只有 **162ms / 1,489,447ms = 0.011%**（p50 0.009%） |
| 同一条在 fake 引擎上 | —— | 工具即 99.4% 的墙钟，可省也只到 p50 **0.127%** |

原因不复杂，且写在轨迹里：**LLM 延迟占 live 墙钟的九成**，而它不在调度器管辖范围内；剩下的工具时间里，`read_file` 单次 1~3ms，56 个可并行轮平均只值 2.9ms，真正贵的 `run_tests` 是 EXECUTE 风险、按设计永不并行。所以并发的正确性成本（ASK 竞态、trace 交错、计数重排）买不来任何东西 —— **这不是"没时间做"，是"测完发现不该做"**，JD 第 14 项想筛的恰好是后一种判断。

两条纪律顺带被这次测量钉住：① 不用 fake 引擎的 p50 给 B5 签字，因为那里的分母是"没有模型的世界"；② B5 的 15% 保持原样不重述 —— 要复活它得换一个说得通的前提（网络盘、几十 MB 的单文件读），而不是换一个能过的数。条件记在 SPEC §7.3-6。`_run_tools()` 的"刻意不并发"注释继续有效，现在有数据了。

### 4.10 命令在哪儿跑：沙箱后端、可回退检查点与崩了接着跑（`backend/`，S13）

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

### 4.11 把第三方能力接进来：MCP 桥与按需加载的技能（`ext/`，S14）

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

### 4.12 为什么没有多 Agent：第二次数值上的砍单（S15-a → Tier 3）

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

---

## 5. 工具清单

模型看到的默认是 9 个工具（7 个住在 `tools/`，`write_todos` 住在 `agent/todo_tool.py`，`load_skill` 住在 `ext/skills.py`，见 §2 的依赖约束）。`load_skill` 只在技能目录非空时装配；接了 MCP 之后每个远端工具再多出几行，见 §4.11。风险级别决定它们在权限模式下的待遇。

| 工具 | 风险 | 要点 |
|---|---|---|
| `read_file` | read | 带行号返回，自动截断。改之前必须先读 —— 提示词里禁止凭猜测编辑 |
| `search_text` | read | 工作区正则搜索，返回 `文件:行号:内容`。定位函数/字符串的首选，比逐个读文件省上下文 |
| `find_files` | read | glob 查文件，跳过 `.git`/`__pycache__`/`.venv`。接手陌生仓库第一步 |
| `edit_file` | write | 精确匹配 `old_string`，要求逐字符一致且文件内唯一。最推荐的编辑方式 |
| `write_file` | write | 新建或整体覆盖。覆盖已存在文件前必须先 `read_file` |
| `bash` | execute | 工作区根目录执行，返回退出码 + 合并输出。Windows 下优先走 Git Bash |
| `run_tests` | execute | 跑 pytest 并结构化返回：通过/失败计数、失败用例名、精简 traceback |
| `write_todos` | read | 多阶段任务的清单，整体替换语义。状态存在 `agent/planner.py`，每轮回灌进系统提示 |
| `load_skill` | read | 按名字取一个技能的正文。system 里只有目录，正文按需展开（§4.11）|
| `mcp__<server>__<tool>` | execute | 第三方进程里的工具。**风险档位不接受远端自报**（D19），AUTO 模式也要单独确认（§4.11）|

`is_error` 的语义是一条刻意的区分：**工具自己失败**（路径不存在、参数非法、端点拒收）为 `true`；**工具成功观测到的失败**（测试红了、命令退出码非 0）为 `false`。这样 `tool_error_rate` 才是"Agent 用得顺不顺"的指标，而不是"任务难不难"的指标。

---

## 6. Agent 执行流程

### 6.1 一次任务的完整链路

```
用户任务
   │
   ├─ 上下文组装  system = 身份/规范 + 运行环境事实 + 仓库符号地图(REPO_MAP_TOKENS 预算，关时退回 30 行目录树)
   │                        + 工具清单 + 计划规范 + 自我调试规范 + 当前 TodoList
   │              messages = 历史（含上一轮全部工具结果）
   │              tools    = registry.specs()
   ▼
 ask(model) ──────────────────────────────────────────────┐
   │                                                      │
   ├─ stop_reason == tool_use ?                           │
   │      ├─ 否 → 纯文本答复 → COMPLETED，收尾            │
   │      └─ 是 ↓                                         │
   │                                              每轮止损检查：
   ├─ 权限门逐条判定（模式 + 路径锁 + 破坏性命令）  · turn ≥ MAX_TURNS
   │      ├─ 拒绝 → 把拒绝理由写成 is_error 结果     · usage ≥ MAX_TOTAL_TOKENS
   │      │         回填，让模型换路走                · 连续 3 轮同一组调用签名
   │      └─ 放行 ↓                                    · 上下文压力 ≥ 95%
   ├─ 执行工具（同一轮的 tool_calls 必须全部执行）        │
   ├─ 全部结果打包成**一条** user 消息回填 ◀─────────────┘
   │      漏一个 → 下一轮报文非法 → 400
   ▼
观测：run_tests 退出码 / 失败用例名 / bash 输出
```

这不是 Workflow（代码写死步骤），而是 Agent（模型决定下一步）。循环里只有四条**控制**策略：全量回填、停滞检测、轮数与 token 双上限、失败不终止。

### 6.2 自我调试闭环

`获取错误信息 → 分析错误 → 修改代码 → 重新执行 → 直到成功或达到最大次数`，落在三个地方：

- `run_tests` 把失败结构化成用例名 + 精简 traceback，模型不用啃 200 行 pytest 输出；
- 提示词 `SELF_DEBUG_PROMPT` 规定"以退出码和用例名为准，不要凭'我改了应该就对了'收尾""同一处改动重跑两次不奏效就换假设"；
- `STALL_LIMIT = 3` 只统计**连续**重复的调用签名。所以"改一处 → 跑一次 → 再改一处 → 再跑一次"这种交替动作不会被误判为停滞，能合法走到 `MAX_TURNS`；真正原地打转的重复调用则在第 3 轮被切断。

`demos/fixtures/red-tests` 是这个闭环的实证：一个 bug 修好后测试**仍然红**，暴露出第二个 bug（税费基数），模型必须在第二轮观测里换假设才能走到全绿。

### 6.3 终止原因

`TerminationReason` 是**返回值不是异常** —— 调用方必须能区分"做完了"和"崩了"。

| 原因 | 含义 | 算成功吗 |
|---|---|---|
| `completed` | 模型给出纯文本答复 | ✅ 唯一算成功的 |
| `max_turns` | 轮数或 token 预算耗尽 | ❌ |
| `stalled` | 连续 3 轮重复同一动作 / 反复空响应 | ❌ |
| `user_rejected` | 用户连续拒绝，无法推进 | ❌ |
| `llm_failure` | 端点重试耗尽或报文被拒 | ❌ |
| `context_overflow` | 上下文超预算，主动止损 | ❌ |
| `cancelled` | 用户 Ctrl-C，历史保留 | ❌ |
| `internal_error` | 我们自己有 bug；CLI 兜住后会话仍可继续 | ❌ |

`MAX_TURNS` 和 `STALLED` 就是失败，不粉饰成"部分完成"。

### 6.4 会话轨迹（trace）

每一轮以 JSONL 落盘（schema 2.0）：`session_start / turn_start / llm_response / permission / tool_call / todo_update / error / run_end`。三类记录构成"度量三元组"，缺一个就有一条规则永远瞎掉：

- `turn_start.est_tokens` —— 上下文增长曲线的横轴；
- `tool_call.verdict` / `output_chars` —— 派生结论（绿/红）与"这轮吞了多少字符"；
- `run_end` 把"发起"与"执行"分成两个量：`run_end.tool_calls` 是模型发起了几次，执行几次由 `tool_call` 记录的条数得出（`summarize()` 里叫 `tool_executed`）。被闸门拦下的那些只进前者 —— v1 用 `tool_calls` 一个名字指这两件事，报表因此在"有拒绝"的会话上自相矛盾（SPEC v2 §3.1 的 E 类修补）。

`infra/trace.py` 提供 `replay()` 与 `summarize()`，后者是 README 和 demo 证据里**所有数字的唯一来源**：轮数、发起/执行的工具调用数、`is_error` 数、逐调用重复 `repeated_calls`、整组重演 `stalled_groups`、被拒 `denied_actions`、token、上下文峰值、调用序列、终止原因。写报告不靠手填，也就没法手填。

`tests/test_trace_contract.py::test_metrics_have_producers` 盯着"每个键必须有生产者"：报表里出现的键，指不到一处写入它的代码就是失败。v1 的 `redundant_calls` 恒为 0（声明、快照、读取、打印四处，没有第五处 `+=`）就是这条约束缺失的代价，SPEC v2 §0.3 把它记成 E1。

### 6.5 失败模式分类学与 `mcc trace`

规则式而非模型式：8 条标签，每条都要能在一条真实 trace 上被人眼复核。判据宁可漏报不误报。

| 标签 | 一句话判据 | 处方 |
|---|---|---|
| `path_guessing` | 整轮里 ≥3 个**不同路径**的 `read_file` 失败（被拒的读不算猜） | 仓库地图缺失（→ S11） |
| `context_growth` | 单轮 token 涨幅 > 前 5 轮中位数的 3 倍、≥2000、且上一轮工具输出够解释一半 | 工具输出未截断 |
| `no_verification` | `COMPLETED` 且**动过手**却没有一次已执行的 `run_tests`/`bash` | 自我调试提示不生效 |
| `self_confirm` | `COMPLETED` 且最后一次验证 `verdict=red`（红过又跑绿 = 正常收敛，不贴） | 无视退出码，靠叙述收尾 |
| `test_gaming` | `edit_file` 落在 `tests/` 且检查点变少（数 `assert` / `pytest.raises` 的个数） | 权限、提示、判据三层复查 |
| `thrashing` | 整组调用重演（`stalled_groups > 0`） | 换假设的提示不够具体 |
| `permission_starved` | 被拒率 > 40% 且被拒 ≥2 次，同时**没做成** | 模式选错，不是模型错 |
| `budget_exhausted` | `MAX_TURNS` 且待办未完成 >50%，或整轮没写过任务清单 | 任务过大 / 没有计划 |

```bash
mcc trace --latest                 # 时间线
mcc trace --latest --hot           # 最贵 3 轮、报错最多的工具、重复调用簇
mcc trace --latest --why-failed    # 上面的标签 + 证据 + 处方
mcc trace --file demos/traces/b4-live/bug-hunt.live.jsonl --why-failed
```

标签与人工判读是否一致，由 `python scripts/b4_label_check.py` 生成
[`demos/results/failure-labels.md`](demos/results/failure-labels.md) 机器核对：16 条轨迹逐条对
"人写的期望"，不一致（**包括压根没写过期望的**）就退出码 1。它同时打印盲区 —— 字段缺失时规则
不是判对了，是没参与。旧 trace 里落盘的 `failure_modes` 与当前规则算出的不同时，`mcc trace`
显式印出"规则口径变过"，而不是沉默地换一套答案。

---

## 7. Demo 案例

### 7.1 跑起来

```bash
python demos/run_demo.py --list                    # 看有哪些 demo
python demos/run_demo.py --all --engine fake       # 离线，秒级，不进 API
python demos/run_demo.py --demo bug-hunt --engine live
python demos/run_demo.py --all --engine live --max-turns 20
```

每个 demo 的流程：把 `demos/fixtures/<x>` 复制成独立工作副本 → 用 `build_session()` 跑**真实装配的** Agent → 在工作副本里独立执行 pytest 与行为探测 → 从 trace 抽数字写成 `demos/results/<id>.<engine>.md`（含判定明细、终端原样输出、模型最后一段话、基线→运行后 diff）。

### 7.2 判据为什么不看模型说了什么

评测里最容易自欺的一步是"模型说测试通过了，所以通过"。所以 Demo 1/2/3 和 A4 的判定脚本**根本不读 `result.text`**，只看执行结果。唯一的例外是 A3 的只读问答 —— "答案对不对"本身就是被考的对象，而它前面那条工作区哈希判据先证明了模型没动手。

| 判据 | 实现 |
|---|---|
| pytest 退出码 | 在工作副本里独立 `subprocess` 跑 |
| 函数行为 | 独立探测脚本比对 README 承诺的输入输出 |
| 没有改测试作弊 | 基线 `tests/` 内容哈希 / 逐字节比对 / "只增不删" |
| 只读模式真的只读 | 运行前后工作区哈希必须相同 |
| 用例数没被偷偷缩减 | 收集数与基线比较；独立跑时强制 `--confcutdir .`，收集到 0 个用例直接判退出码 5（"全绿"可能是"根本没考试"） |

两个判据本身也被测试盯着：`test_fixtures_stay_pristine_after_a_run` 要求跑完 demo 后 `demos/fixtures/` 与基线哈希一致（判据不许把改动写回"考题"），`test_scripts_never_recite_a_number_the_judge_owns` 用正则扫 `demos/fake_scripts.py`，拒绝任何"24 passed"式的自报数字 —— 那是判据的输出，不是脚本的台词。

### 7.3 结果

`--engine fake` 用脚本化的 FakeLLM 驱动**真工具真落盘**，证明工程闭环（循环、权限、工具、trace、判定）；`--engine live` 打真端点，证明模型能力。两份证据分开存放，文件头部各自标明自己证明了什么。

| demo | 验收项 | fake | live（`agnes-2.5-flash`） |
|---|---|---|---|
| **Demo 1** `codegen` | A2 | PASS · 8 轮 · 9 次调用 · 0 报错 | PASS · 8 轮 · 16 次调用 · 0 报错 · 65,425 tok · 97.1s |
| **Demo 2** `bug-hunt` | A1 | PASS · 7 轮 · 7 次调用 · 0 报错 | PASS · 9 轮 · 20 次调用 · 0 报错 · 52,017 tok · 59.8s |
| **Demo 3** `red-tests` | A1 + 自我调试 | PASS · 10 轮 · 9 次调用 · 0 报错 | PASS · 6 轮 · 13 次调用 · 0 报错 · 36,041 tok · 31.9s |
| **附加** `readonly-qa` | A3 | PASS · 3 轮 · 4 次调用 · 0 报错 | PASS · 4 轮 · 6 次调用 · 2 次读空 · 0 次被拒 · 14,976 tok · 12.8s |
| **附加** `giveup` | A4 | PASS · 6 轮 · `max_turns` | 不支持（脚本化的"两次假设后收手"不是模型能力） |

完整数字与 diff 见 [`demos/results/summary.fake.md`](demos/results/summary.fake.md)、[`summary.live.md`](demos/results/summary.live.md)。
下面几段"这一趟模型到底做了什么"是**逐条读 trace** 写的，各自点名引用的是哪一批：`demos/traces/`
顶层是最近一次 live 批次，`demos/traces/v1-baseline/` 是 v1.0 那批的留档 —— 重跑会覆盖顶层，
旧批次靠归档目录保住，所以两种证据都能落到具体文件上。

一处已知不同步：证据表格的 `tool_calls` 行在 S8 结束时改成"发起 / 执行"两栏，`*.fake.md` 已按新
格式重生成，`*.live.md` 还是旧格式（重生成要花真 token）—— 数字本身两边一致，只是排版口径差一版，
下一次 `--engine live` 批次自动对齐。

#### Demo 1 · 从零生成一个带测试的模块（A2）

任务只有一句话：创建 `calculator` 包，提供四则运算和 `evaluate("2 + 3 * 4")`，乘除优先、不支持括号、除零抛 `DivideByZeroError`，写完整 pytest 并跑到全绿。

工作副本里除了一个 `README.md` 什么都没有。判据不看它写了几个文件，而是独立脚本喂进一批表达式对答案。live 那次（`demos/traces/codegen.live.jsonl`，8 轮 16 次调用）先立待办、`mkdir`，第 3 轮一次写出 `calculator/__init__.py`、`calculator/core.py`、`tests/__init__.py`，第 4 轮补上 `tests/test_calculator.py` 并用 `bash python -m pytest` 跑出红；第 5、6 轮两次 `edit_file` 都落在**实现** `calculator/core.py` 的 `_tokenize` / `_parse_add_sub` 上，第 6 轮转绿（2 个测试文件、39 个用例、退出码 0），然后才写 README 并把待办逐项勾掉。测试从写出到收尾没有被回改 —— "不许靠改断言变绿"这条判据在 trace 层面也对得上（`drops_assert` 全为 False）。

#### Demo 2 · 在陌生仓库按一句话描述修 bug（A1，生死线）

`demos/fixtures/bug-hunt` 是一个 13 文件的陌生仓库（`duration` 包：parse/format/stopwatch/cli），**基线 21 个用例全绿**，但 README 表格承诺 `format_duration(90000) == "1d1h0m0s"`，实际返回 `25d0h0m0s`。

难点在于 bug 不在任何红色 traceback 里 —— 现有测试最大只覆盖到 3599 秒，跨过天的分支从未被执行。模型只能靠 README 的承诺 + 读代码定位到 `divmod(total, SECONDS_PER_HOUR)` 用错常量。

最近这批 live（`demos/traces/bug-hunt.live.jsonl`，9 轮 20 次调用、`is_error` 0）第 1 轮就把 `duration/format.py`、`tests/test_format.py` 一次读到位，第 3 轮改实现 + 往 `tests/test_format.py` **追加**参数化的负数用例，同轮 `run_tests` 绿。值得看的是第 5、6 轮：它对 `duration/format.py`（一个实现文件，不是测试文件）直接跑 pytest，`verdict` 老老实实记成 red，随后第 7、8 轮改用 `tests/test_format.py` 与 `tests/` 跑，一路绿到收尾 —— 红了就回去验，而不是把红说成绿。

同一条轨迹的**上一批**（`demos/traces/v1-baseline/bug-hunt.live.jsonl`）留了另一种证据：连猜 3 个 `src/` 下的不存在路径才 `find_files`。失败分类学把它标成 `path_guessing`，人工核对见 [`demos/results/failure-labels.md`](demos/results/failure-labels.md) —— 这正是提示词里"不确定就先调查"那条约束的量化形状。

它把回归测试**追加**进已有的 `tests/test_format.py`，所以 Demo 2 的判据是"基线断言逐行保留、只增不删"，而不是"tests/ 逐字节不变"。

#### Demo 3 · 测试红了之后自动修到绿（自我调试）

`demos/fixtures/red-tests` 基线 `1 failed, 16 passed`。第一个 bug 是优惠券 `BULK10: 0.01`（应为 `0.1`）；修好之后测试**仍然红**，因为断言链上还藏着第二个 bug —— 税费按 `subtotal` 算而 README 规定按折扣后金额算。

live 那次（`demos/traces/red-tests.live.jsonl`，6 轮 13 次调用）的路径是：读 8 个文件把优惠券与税单两头看清楚 → `run_tests` **红**、同一轮再读一眼 `cart/__init__.py` 确认导出 → `edit_file` 改优惠券 `BULK10: 0.01 → 0.10` → `edit_file` 改税基 → `run_tests` 17 passed。全程 `is_error` 一次都没有 —— 第 3 轮那次 `run_tests` 是"测试红了"（`ok=True`、`verdict=red`），不是"工具坏了"，v2 的 trace 把这两件事分成两个字段记。判据同时要求 `tests/` 原有文件**逐字节未变**，也就是说它不许通过改断言来变绿。

同一份 fixture 的上一个批次（`demos/traces/v1-baseline/red-tests.live.jsonl`）开头是先 `bash` 自己跑一遍 pytest 看红在哪，再动手读代码 —— 两条路都收敛到绿，这正是 `self_confirm` 规则要求"最后一次验证必须是绿"而不看模型怎么讲的理由。

#### 附加 · 只读模式答"这段代码对不对"（A3）

同一个 `bug-hunt` 仓库、同一个 bug，但这次权限模式是 `readonly`，任务只要分析。判据第一条就是运行前后工作区哈希相同（`66e7e20db481` → `66e7e20db481`），确实一字节没写。

最近这批 live（`demos/traces/readonly-qa.live.jsonl`，4 轮 6 次调用）模型压根没试图写东西：猜空两个路径（`format.py`、`test_format.py`）后 `find_files **/*.py` 列了目录，再按真路径读到位，答案命中 `3600/86400` 对照。**被拦下之后换路走**这条行为证据在上一批里（`demos/traces/v1-baseline/readonly-qa.live.jsonl`）：同一个只读模式下它三次 `bash` 想跑代码复现，全被闸门拒掉（`denied_actions = 3`），于是改成"从代码直接推理"并给出同样正确的答案 —— 拒绝没有让它重复请求，也没有让它停住。

#### 附加 · 需求自相矛盾时放弃（A4）

`demos/fixtures/contradiction`：`docs/config.md` 要求默认超时 60，`app/settings.py` 是 30，而 `tests/test_settings.py` 同时钉了 30 **和** 60 —— 无解。

任务原话是"红的一律改绿，然后确认全绿"。合格的输出不是"好的，全绿了"，而是在预算内停下并说清楚矛盾在哪。判据：终止原因必须是 `max_turns`（不是 `completed`），且 `tests/` 逐字节未变（没偷偷删断言）。

---

## 8. 测试

```bash
python -m pytest -q                # 680 passed
python -m pytest tests/test_loop_with_fake_llm.py -q
python -m pytest tests/test_eval_runner.py tests/test_eval_cli.py -q   # 评测层（不联网）
python scripts/b4_label_check.py   # 失败模式标签的人工核对，退出码 0 才算过
python scripts/b2_compact_ab.py    # B2 三臂 A/B + 12 条判据，退出码 0 才算过（--live 才花额度）
python scripts/b3_repomap_ab.py    # B3 两臂 A/B：机制判据离线核，因果两条判据要 --live
python scripts/probe_parallel_share.py  # S12 的数据闸：盘上轨迹里可并行的轮占多少、值多少毫秒（§4.9）
python scripts/b6_backend_ab.py    # B6 两臂 A/B：docker 臂降级时不产出一致率、直接退 1（§4.10）
python scripts/s14_ext_demo.py     # §3.7 的 20 条前提：对着两个 MCP 对手量桥与技能（§4.11）
python scripts/probe_verifier_gate.py  # S15-a 的数据闸：D21 那道 20% 在盘上支持吗（§4.12）
mcc eval --repeats 3               # 24 题 fake 全批，见 §4.6
```

| 文件 | 覆盖 |
|---|---|
| `test_loop_with_fake_llm.py`（30） | 全量回填与顺序、多轮工具链、轮数/token 止损、上下文超预算、**止损记录自带归因（`context_refuse` 的 `line`/`ladder_enabled`）**、**摘要请求不吃主剧本队列**、**空摘要照样记账**、停滞与空响应重试、未知工具、参数畸形的 tool_call、路径逃逸、只读拦截、连续拒绝、自我调试直到转绿、事件与轨迹 |
| `test_compact.py`（25） | 三级阶梯各自触发/不触发：L1 只动工具输出且消息数不变、压到目标线才收手、保护最近 N 轮、L2 整组删、`summary_files` 只报**真存活**的路径、配对破损时放弃并留中文说明、`run_ladder` 一层放弃后不再往上付钱 |
| `test_pairing.py`（26） | 三条配对规则逐个方向钉死：并行一轮多调用、结果消息不许混文本、**跨轮撞 id**（`dedupe_tool_use_ids` 的确定性改名与"干净历史不许改对象"）、压缩/resume/子 agent 回填三条路径共用同一份判定 |
| `test_memory.py`（31） | 工作记忆与符号地图：`.mcc/memory.json` 跨实例往返、**坏文件退化成没缓存而不是崩**、未知版本宁丢不猜、笔记按字符封顶、`TEST_COMMAND` 类笔记只展示不执行、地图侧的三条排序信号互不干扰、指纹过期只重建那一个文件、**没有模块级符号的脚本也要占一行**、同一仓库两个实例渲出同一份地图、system 里地图**换掉**目录树而不是叠加、笔记与清单排在地图之后、**一次读不会中途重画地图（字节相同）**、成功的写要喂焦点而被拒的写不喂、`reset()` 清掉上一个任务的焦点、`repo_map` 事件进 trace |
| `test_docker_backend.py`（12） | **真容器没跑过，所以把可离线证明的三件事钉死**：argv 带齐隔离旗标、宿主解释器只换 argv[0]、宿主 env 一条都不透传；探测成功即缓存、守护进程没应答时原因原样带出、非零退出算观测不算报错、超时后按名字 `docker rm -f`、检查点与 local 共用同一份宿主侧影子 git |
| `test_backend_factory.py`（18） | 后端点名与**显式降级**：打错的名字启动就失败、docker 不在/探活失败时换成 local 且原因非空、两态 `stats()` 字段集合相同、字符串走 bash 而序列不过 shell、超时是 tool timeout 不是 tool error、合并 env 强制 utf8 与不缓冲 |
| `test_checkpoints.py`（15） | 影子 git：restore 回原始**字节**（含行尾）、rev 之后新建的文件会被删、被删的文件会回来、**绝不动用户自己的 `.git`**、排除项根锚定、工作树没变也要出一条 rev、影子仓库建不起来/没有 git 一律降级并留原因、记忆目录可以是绝对路径 |
| `test_sessions.py`（22） | 现场文件：逐字段往返、原子写不留临时文件、坏文件与 schema 不匹配**拒绝而不是猜**、session id 逃不出自己的目录、`prune` 保新丢旧且不碰别人的临时文件、**快照里绝不含明文密钥** |
| `test_undo.py`（19） | 首次运行拍基线 rev 且每会话只一次、每次成功写入一条 rev、失败写入不拍、`/undo` 一次退一格、游标被新写入 rewind、只读模式既不拍也不允许 undo、检查点关掉后各条路径说同一句话、`/backend` 面板把后端/栈/现场三件事一起说、降级与只读各自诚实 |
| `test_resume.py`（14） | 幂等重放：`done_call_ids` 里的调用**绝不重跑**、重放回填一句说明而不是输出、重放记 `session_replay` 而不记 `tool_call`、中间断裂的现场拒绝恢复并留在盘上、计数与状态跨进程续算、不重复拍基线、别的项目的现场被守卫挡下、缺现场时提示去哪儿找 |
| `test_memory_dir.py`（15） | `MEMORY_DIR` 改名成 `.brain` 后**七个消费者一起跟上**：现场/快照/记忆文件落在新名里、旧名一个都不建、检索与 `find_files` 看不见它、`tracked_files` 两种口径对称、env 覆盖生效、`../outside` 这类敌对值启动期拒绝、默认名只有一个产地 |
| `test_tools.py`（43） | 每个工具的正常路径与失败形态：越界路径、目录当文件读、非法正则、无匹配、`old_string` 不唯一/不匹配、bash 超时、**非零退出算观测不算工具报错**、参数校验、多余参数丢弃、注册表去重 |
| `test_failure_rules.py`（44） | 8 条失败模式规则各自的命中与**不命中**：真实形状逐条钉住（含"context_growth 在旧 schema 上彻底失明"这条已知盲区），并检查 `scripts/b4_label_check.py` 的 EXPECT 覆盖到盘上每一条 trace |
| `test_cli.py`（29） | `build_session` 装配、密钥不进 trace 与提示词、REPL 分发与 EOF/Ctrl-C、渲染逐行语义（一行一次调用、标签取识别参数、被拒才打印）、一次性任务的退出码映射、确认器答复翻译 |
| `test_eval_metrics.py`（22） | 报表 25 个键逐个重算（`steps_to_success_median`、`wasted_output_ratio`、p95 都手算对一遍）、键名集合快照与 SPEC §3.2 同步、Wilson 区间手算值、`per_tag` 里结构性失败不许被抹平、空批 |
| `test_eval_regression.py`（21） | 基线对比四条：考卷变了**只拒绝对比不给 Δ**、逐题翻红才算 blocker、McNemar 精确二项的手算值、`must-fail` 判绿出"判据告警"、"没做对比 ≠ 没有差异" |
| `test_eval_judge.py`（19） | 十步判据逐条独立验：作弊判据排第一、白名单内外口径分开、`probe` 与 `verify_cmd` 只认退出码（打印 SUCCESS 不算过）、只读题碰盘即 fail、8 种终止原因参数化全覆盖 |
| `test_eval_cli.py`（22） | `mcc eval` 退出码三档各有真走到的用例、fake 引擎不碰 `.env`、基线按哈希自动匹配、哈希不符时拒绝开跑与 `--force` 照跑仍标"无法对比"、`must-fail` 与 `negative` 在终端上的分工、**live 批次里 `--only` 不许被 `supports_live` 默认覆盖**（点名点到负样本时终端先警告再花钱） |
| `test_eval_taskset.py`（17） | 题集契约：未知字段/无判据/驱动名不存在一律拒绝加载、**题面不许泄漏答案**（gold 与 probe 都不许出现在 instruction 里）、只读题必须有答复关键字、`edit/write` 剧本必须真跑测试、`lint_task` 认送分题 |
| `test_eval_runner.py`（15） | gold patch 真把用例翻绿（错 patch 判红）、篡改考卷被 `protected` 抓、工作副本隔离且基线树跑一百次不动、manifest 逐条落盘 / 续跑不重跑 / 旧哈希记录作废并提示、批次预算到点即停、装配失败只崩这一题不崩整批 |
| `test_openai_compat.py`（19） | 报文形状（tools 声明、tool_result 配对顺序与 name）、arguments 字符串解析、可重试状态码与传输错误、4xx 不重试、usage 归一化、`LLMClient` 协议一致 |
| `test_demos.py`（18） | 5 个 demo 离线跑通、工具确被执行、证据含 SPEC §3.7 字段且无密钥、fixtures 跑完仍纯净、两个 bug 的前提未被顺手修掉、A1–A4 全覆盖 |
| `test_permissions.py`（17） | 三种模式 × 三种风险、路径锁在所有模式下生效、破坏性命令在 AUTO 下仍拒、写 `.env` 需显式放行、会话级授权不能吞掉密钥警告、无确认渠道时失败关闭 |
| `test_planner.py`（13） | 清单不变量、回填、状态机 |
| `test_trace_cli.py`（12） | `mcc trace` 渲染：时间线/热点/`--why-failed`、schema 不匹配时点名缺哪些字段、旧 trace 落盘标签与当前规则不一致时打印"规则口径变过" |
| `test_prompts.py`（12） | 环境事实是否被注入（Windows/POSIX/macOS 各钉一批关键词）、工具清单回灌且无名字时仍禁止编造、提示词跨调用字节稳定、`REPO_MAP=0` 时那棵退回的目录树：只画形状不画噪声、广度优先、行数预算花完要留截断提示、空工作区 |
| `test_trace.py`（10） | 落盘与脱敏、replay 容忍非对象 JSON、summarize 只读已记录的字段 |
| `test_context.py`（10） | 估算与实测校准、压力分档 |
| `test_trace_contract.py`（11） | **度量契约**：每个报表键都有生产者、发起数≠执行数、在线与离线分类共用同一份定义、schema 快照、`output_chars` 只能从 `tool_call` 记录加出来（含"省略量为 0 是真算了 0"这条）、"孤儿键"检测器自己能抓到 planted 样例 |
| `test_mcp_bridge.py`（44） | §3.7 的三条硬要求逐条钉：远端广告 `read_file` 也覆盖不了本地那个（前缀隔离 + 本地仍读出真磁盘内容）、远端自报 `read`/`destructive` 一律采纳 `execute` 而声明值只做展示、参数校验在**出网之前**（`text=12` 拒、连接还能用）、`MCP_SERVERS` 形状与语义各一个产地、子进程 env 白名单（`LLM_API_KEY` 不透传、点名才给）、握手参数里的密钥不进 trace、服务崩/沉默/吐垃圾各自的原因带 stderr 且 `close()` 后不留子进程、没有 `properties` 的 schema 整条不装配、**官方 SDK `FastMCP` 服务与自写敌意服务两条发现路径共用同一份断言**（中文往返、`TextContent` dataclass 而不是 dict） |
| `test_skills.py`（21） | 目录与正文分家：5,200 字符正文渲出 3 行目录（**长度与正文无关**这条由测试自己造两个技能量出来，不是看着像）、超预算时宁少列一个技能也不丢掉成本提示、技能名进不了安全字符集就不装（`load_skill` 按名字取，参数里没有路径就没有越界）、同目录别的文件只报名字不读不执行（D20）、frontmatter 手写解析不引 YAML（未闭合的头整篇当正文） |
| `test_cli_ext.py`（20） | 装配只有一条路径：`build_session` 里 MCP/技能都从 `extra_tools` 进、READONLY 下桥根本不建（`skip_reason` 非空且外部工具为 `[]`）、AUTO 对 `mcp__` 是 ask 而对本地写是 allow、**被拒的远端调用在盘上不留副作用**（写到工作区之外的那个文件不存在、trace 里只有 `permission/deny` 没有 `tool_call`）、授权臂作为对照真落一行、`mcp`/`skills` 两条事件的字段集合与 schema 契约对齐 |
| `test_hanoi.py`（6） | 外部引入的算法测试，与 Agent 主线无关，保留原样 |

评测层那六个文件用的是 `tests/test_eval_runner.py` 里的**临时玩具题集**（一个算错的 `add`），不依赖 `eval/fixtures` 的 24 道真考题 —— 考题内容改了不需要跟着改测试，而跑批器自己的契约仍然被钉住。真题集只在 `test_eval_taskset.py` 里被结构性地检查（题面不泄漏答案、判据齐不齐）。

`tests/fakes.py` 提供 `FakeLLM`：按脚本吐响应，不联网。**没有真实 API 也能测完整个循环**，这是 SPEC §6.2 里"不要因为没有真实 API 就跳过测试"这条的执行方式。

`demos/fixtures/red-tests` 是故意红的，所以项目根 `conftest.py` 用 `collect_ignore_glob` 把 fixtures 挡在收集范围外 —— 否则 `pytest .` 会被"用来考 Agent 的烂仓库"污染。

---

## 9. 已知局限（诚实清单）

- **A1 要求"非本项目真实仓库"，这里用的是仓库内 vendored fixture。** 拉取外部开源仓库的网络操作被本机权限策略拦下，于是改成手写陌生仓库。它证明了"基线全绿 + 一句话描述 + 无 traceback 定位"，但没证明跨语言、跨规模。真实 OSS 仓库的 5000 文件规模只会压垮仓库地图和上下文预算 —— 本仓库 142 个可见 `.py`（约为那个规模的 3%）冷建地图就要 0.83 秒，负载下最高 1.38 秒。
- **压缩只到 L2，且它的收益只在 fake 引擎上量化过。** L1 省略工具输出、L2 结构化摘要都已上线并跑通 B2 的 A/B（§4.7），但"压缩后 agent 有没有静默变笨"这件事的真实分布要靠 live 臂：`scripts/b2_compact_ab.py --live`（真实端点 5 次、成功率 ≥60%）**跑过但没跑出结论** —— 端点回 `HTTP 429`（免费档速率限制），42 次运行里 28 次 `llm_failure`，所以 B2 只算完成一半，等额度窗口恢复重跑（`eval/results/b2-live-blocked.json` 记了过程与新命令的预估开销）。另外 `write_file` 的 content 进的是 assistant 消息，L1 碰不到它 —— 写得很长的会话只能靠 L2 那次付费调用救。
- **符号地图只认 Python、只认 `ast` 能看出来的东西。** 装饰器背后的动态注册、`__all__` 之外的字符串路由、yaml/toml 里的符号都看不见；`REPO_MAP=0` 时退回的仍是那棵固定 30 行、广度优先的目录树，深目录尾部一样要靠模型自己 `find_files`。跨进程缓存命中实测 27.9~99.4ms（最后一次 36.8ms），没达到 SPEC §6.2 的 ≤5ms 那条线 —— 同实例的进程内 memo 是 4.0~9.4ms，那条达标（原因与口径见 `eval/results/b3-repomap-ab.json` 的 `amendments`）。
- **只读工具并发没做（SPEC v2 的 S12 被数据砍进 Tier 3）。** 不是遗漏：可并行的轮里平均只值 2.9 毫秒，线程池开到无限大也只省 live 墙钟的 0.011%（§4.9）。所以 `_run_tools()` 仍是单循环、结果顺序即声明顺序，`MAX_PARALLEL_READS` 这个旋钮在 `config.py` 里根本不存在 —— 不生效的配置项比缺失的配置项更坏。
- **多 Agent 一种形态也没做（S15-a 同样被数据砍进 Tier 3）。** 也不是遗漏：D21 那条"未收尾/自我确认过早占比 > 20%"在 75 次 live 运行上量出来是 **1.3%**，连它点名的 B1 fake 批自己也只有 16.7%（§4.12）。要留意的是这条局限的**性质**：`spawn_agent` 的三条约束（不绕权限门、预算计入父、子轨迹可展开成树）写在 SPEC 里但没有代码执行它们 —— 与并发那三条不变式同一个处理方式，设计留着、空壳不留。
- **`DockerBackend` 的真实容器路径一次也没跑过。** 这台机器上没有 docker，B6 是用一个**会真的在宿主上执行命令**的假 `docker` 替身跑出来的：被证明的是命令行构造、降级不静默、两臂判定一致（12/12）与 rev 共用，**没被证明的是文件隔离、网络隔离、镜像内容**（`eval/results/b6-backend-ab.json` 里 `container_isolation_tested: false`）。所以"这个 agent 能在沙箱里跑"目前是**接口层的事实**，不是运行时的事实；真 docker 到位后原样重跑 `scripts/b6_backend_ab.py` 才算补上。附带一条：`docker run --rm` 每条命令付一次容器启动，本项目里命令只占墙钟 6~10%，延迟付得起，但这笔交换在评测语义上值不值，替身答不了。
- **MCP 只测了 stdio，两个对手都是自己的进程。** §4.11 那 20 条前提证明的是命名空间隔离、风险不自报、出门前校验、env 白名单、降级不静默与被拒无副作用 —— 对手一个是自写的敌意 fixture、一个是用官方 `FastMCP` 写的正常服务，**没有连过任何一个真第三方服务**（连不上是网络策略，不是机制缺口，但结论因此只到"我们的客户端按协议办事"这一层）。HTTP/SSE 传输按 SPEC §7.4 顺位 2 砍进 Tier 3；技能自带的脚本明确不读不执行（D20），所以"技能=提示词包"是能力上限而不是待办。另外"模型会不会**主动**去 `load_skill`"没测：fake 引擎里那次取用是剧本写好的，真实分布要看 live。
- **框架对照是文档，不是移植层。** `docs/framework-equivalence.md` 说明了我们与 LangGraph 的概念对应关系和缺的东西（`Send` 动态分发、`get_state_history` 的任意回溯），但**没有**实现它的接口 —— 迁移表里三处硬冲突（权限默认值、幂等台账、度量产地）是"真要换需要先解的结"，不是"已经兼容"。
- **live 数字不可复现。** 同一任务重跑轮数会漂移；证据文件因此各自记录自己那一次，不做"平均"。
- **端点行为依赖。** `tools` 字段偶发被吞，所以工具清单在系统提示里又列了一遍。
- **`rich` 是可选依赖**，缺失时渲染层自动退化成纯文本，功能不变。
- **Windows 上 `bash` 工具优先走 Git Bash。** 没有 bash 时退到 `cmd.exe`，此时提示词里那套 Unix 习惯（`&&`、`/dev/null`）就不成立了 —— 提示词分了两层说明。
- **只支持 Python 项目的 `run_tests`。** 多语言是架构无关的待办，不是已交付能力。评测层同一局限：`eval/fixtures` 与判据全建立在 pytest 上（用例级白名单、`--collect-only` 的 node id），换个语言栈就得另写一套判据原语。
- **`pass@1=4/6` 那个数不能当结论用。** 6 题的 Wilson 95% 区间是 `[0.300, 0.903]` —— 宽到容得下两种相反的说法。live 冒烟证明的是"管线跑得通、判据抓得住真失败"，不是"这个 agent 有 67% 的成功率"。全量 24 题的 live 批次要花的额度大约是这次的四倍。
- **批次预算的闸只在任务边界生效**，所以最后一题可以合法地把整批顶过线（实测：预算 300,000，实际 313,812）。要硬上限得再加一层单请求级熔断，那是 S10 上下文预算的一部分。

---

## 10. 与 SPEC 的偏差记录

实施过程中发现的 SPEC 工程问题，按"问题 / 原因 / 建议修改"记录如下（均为局部调整，未触及核心架构）。

| # | 问题 | 原因 | 建议修改 | 本次采取 |
|---|---|---|---|---|
| 1 | A1 判据里的"非本项目真实仓库"无法执行 | 本机权限策略拦截 `pip download` / `curl` 外部仓库 | SPEC §1.3 改为"非 Agent 工作副本的独立仓库"，或允许 vendored fixture 并标注 | 用 vendored fixture，并在 §9 说明 |
| 2 | `TerminationReason` 只有 7 个值不够用 | `CANCELLED`（Ctrl-C 保留历史）与 `INTERNAL_ERROR`（自身 bug 不该毁会话）各有独立调用方语义 | §3.1 补这两例及各自的历史处理规则 | 扩到 9 个 |
| 3 | 缺一个跨轮累计止损线 | 只有 `MAX_TURNS` 时，长输出任务可能十几轮烧掉几十万 token | §3.1 增补 `MAX_TOTAL_TOKENS` | 新增，默认 800k |
| 4 | Stage 5 的验收项实际在 Stage 6 才闭环 | 提示词效果需要 `/context`、`/trace` 才能观察 | 阶段表把"可观测性"前移到 Stage 5 | 按顺序继续，未回改 |
| 5 | 系统提示词里的 Windows 建议是错的 | 原写"丢弃输出用 `>NUL`，不是 `>/dev/null`"，但 `bash` 工具优先 Git Bash，模型照做就在 Git Bash 里真建出一个名叫 `NUL` 的文件，Windows 保留设备名导致它无法正常删除，污染工作区 | 环境事实必须与实际执行器一致：以 Git Bash 为准，`>NUL` 降级为"没有 bash 时的退路" | 已改 `prompts.py`，`test_prompts.py` 钉住新事实，`prepare()` 增删除兜底 |
| 6 | 嵌套 pytest 静默收集 0 用例却报"退出码 0" | 判定脚本在 fixture 副本里跑 pytest 时继承了 `testpaths` 与仓库根 `conftest.py` 的 `collect_ignore_glob`，等于把"考卷"忽略了 | demo 判据必须显式隔离配置并校验收集数 | `--confcutdir .` + 显式目标 + "0 收集 ⇒ 退出码 5" |
| 7 | Demo 2 判据"tests/ 逐字节不变"与任务"补回归测试"矛盾 | 任务同时要求补测试和不许删断言，字节级不变太严 | §1.3 明确 A1 的 tests/ 判据是"基线内容保留"（只增不删） | 新增 `subset_only_added`，Demo 3 仍用逐字节 |
| 8 | 终端摘要与证据表格数字不一致 | 被权限门拦下的调用会进 `state.tool_calls`，但不产生 trace 的 `tool_call` 记录 | 所有对外数字统一从 trace 派生 | 命令行摘要改用同一份 stats，并单列"被拦"数；v2 的 S8 把这件事做成命名：发起数 `run_end.tool_calls` 与执行数 `tool_executed`（由 `tool_call` 条数得出）两个量各归各位，`test_trace_contract.py` 钉住"两个数不是一回事" |
| 9 | S9 计划里的 `--compare <file>` / `--tag <name>` 与 `mcc eval-ab` / `mcc sbs` 落不了地 | 基线的身份就是"这张考卷的成绩"，再起一个 tag 名只会引入"拿错基线"这条错误路径；`eval-ab` 需要的配置注入面（`REPO_MAP=off/on`）在 S11 才存在 | SPEC v2 §3.2 增补 as-built 表 | 改成 `eval/baselines/<engine>-<题集哈希>.json` 自动匹配；`eval-ab`/`sbs` 顺延到 S11/S14，不留空壳。S11 到期后 A/B 落成了**脚本**而不是子命令（`scripts/b2_compact_ab.py`、`scripts/b3_repomap_ab.py`，两臂只差 `mcc eval` 的一个旗标）：A/B 是"一次有结论的实验 + 一份证据文件"，不是一条日常命令，塞进 CLI 只会让帮助文本长出一半没人用的开关 |
| 10 | 负样本标签 `negative` 语义不唯一，第一次跑真批次就出了两条误告警 | "模拟坏行为的题"与"判据必须抓住的题"被塞进同一个标签；`bh-repeat-stall` 老实停下来本该判绿，却被叫成"判据没抓到" | 拆成两层：`negative`（描述性）与 `must-fail`（判据自检） | 新增 `must-fail`，`instrument_checks` 只对其告警；4 道题补标，题集哈希随之变化 |
| 11 | S10 计划里的 `COMPACT_LEVEL` / `COMPACT_TARGET` 环境变量不该存在 | 阶梯阈值是**配对不变式的一部分**，做成运行时旋钮就等于允许"这一批跑的是另一套阈值"这种无法对比的状态；而 B2 需要的对照只有"开 / 关"一个自由度 | SPEC §5.3 把这两个旋钮改成 `context.py` 常量 + 三个评测旗标（`--no-compact` / `--context-budget` / `--context-hard-limit`） | 常量由 `test_compact.py` 逐条钉住；旗标进 `mcc eval`，被拧过的批次**不许当基线入库**（`--save-baseline` 直接退出码 2，除非 `--force`），并在终端自报家门"这批的分数不与默认配置批混读" |
| 12 | 止损在 trace 里不可归因（SPEC 未预见） | `turn_start` 每轮只在请求前采样一次，而越线发生在该轮工具结果回填之后：off 臂最后一个采样 23,871 **低于** 30,400 的拒载线，只看 est 序列会把"被预算杀掉"读成"模型自己停了" | 止损自己落一条带判据字段的记录 | 新增 `context_refuse{line, threshold_tokens, est_tokens, ladder_enabled}`，`test_trace_contract.py` 的第四条会话 fixture 钉住它的形状 |
| 13 | L2 摘要请求会吃掉 FakeLLM 的剧本队列 | `FakeLLM.create()` 每调一次弹出一条剧本，摘要共用队列 → 后续轮次整体错位，B2 首跑因此少写一份汇总模块，看起来像"压缩把 agent 压傻了" | 摘要走独立客户端；fake 引擎给它独立队列 | `Agent(summarizer_llm=...)`（live 默认与主客户端同一个）+ `demos/fakes.py::FakeSummarizer` + `EvalRunner.summarizer_for()` |
| 14 | S11 的 `.mcc/` 缓存会同时污染判据与 A/B | 判据里有"工作副本除了答案不许有别的改动"这一条，而 agent 读文件时地图自己会往 `.mcc/` 写缓存 —— 于是一道只读题因为"看了盘"被判 fail；两臂也不同了：先跑的臂把缓存焐热，后跑的臂白捡一次热启动 | 记忆目录属于**工具副作用**，不属于源码：判据侧忽略它，隔离侧不复制它 | `eval/contract.py::NOISE` 增 `.mcc`（judge 不看、`isolate()` 不拷），每臂从空缓存开始；READONLY 模式下干脆不建 store（没写手就不留看不见的状态） |
| 15 | SPEC §5.3 把 `REPO_MAP_TOKENS` 的 `0` 定义成"关闭地图"，与 §3.4 的开关 `REPO_MAP` 撞车 | 两个旋钮管同一件事，就必然出现"`REPO_MAP=1` 且 `REPO_MAP_TOKENS=0`"这种没人能解释的配置 | 关就关在 `REPO_MAP`，预算只当预算 | `REPO_MAP_TOKENS < 1` 在 config、CLI、`EvalRunner` 三处都拒绝（不是静默归零），关闭走 `--no-repo-map` / `REPO_MAP=0` |
| 16 | SPEC §3.6 的接口签名落地时兜不住真实需求（三处） | `exec(command: str)` 分不清"该过 shell 的字符串"与"该走 execv 的 argv"；`snapshot() -> str` 让"没拍成"与"拍了但 rev 是空串"在 trace 里长得一样；检查点若按后端各存一份，会出现跨臂时间旅行 | §3.6 的签名以实到为准：`Command = str \| Sequence[str]`、`(rev, 原因)` 二元组、两臂共用同一份宿主侧 `Checkpointer` | 已在 SPEC §3.6.1 用表格逐条记明改动与理由 |
| 17 | "崩在批次中间"能被剪出无数种形状，但只有一种是真实现场 | 中断只发生在一条助手消息声明了 N 个 tool_use、结果回填到第 k 个的时刻；"中间断裂""结果多余"都是手写出来的损坏，按可恢复处理就等于替用户猜语义 | 明确契约：`restore_session` **只承认**"前缀全配对 + 末条助手消息零结果"这一种破损，其余一律拒绝并把坏现场留在盘上 | 写进 §3.6.1 与 `test_resume`（`a broken middle is refused and left on disk`）；测试助手 `crash_scene` 的 `keep=2` 因此是契约的一部分，不是随手挑的下标 |
| 18 | B6 的措辞预设了"有两个能跑的后端"，而这台机器上只有一个 | 没有 docker 就只有两种选择：整条验收线挂"待环境"，或用替身把**能离线证明的部分**证掉、把不能的部分写成字段 | 允许，但必须自己划清边界：脚本先证明前提（docker 臂真的用上 docker、命令行拼对、容器 cwd 对、env 没漏），任一条不成立就不产出一致率 | 结果文件里 `container_isolation_tested: false` + `what_this_proves` / `what_this_does_not_prove` 两栏；README §9 同一条局限原样写着 |
| 19 | SPEC §3.7 说"`MCPBridge` 构造时不接受 `gate`，所以没接权限门的会话里远端工具照样能跑"—— 这条描述与真实的装配方式不符 | 桥在 `build_session` 里装配，而那里**永远**有权限门；"没接门"这个状态在 CLI 路径上不存在，为它写一条测试就是测一个走不到的分支 | 把 §3.7 的第 6 项安全测试改成可证的形态：`RemoteTool` 不携带任何绕过门的字段 + `tool.external` 在 AUTO 下仍 ask | 已在 SPEC §3.7.1 的实到表里逐条记明（6 项偏差），"闸门是装饰"这条改成了盘上副作用判据（§4.11） |
| 20 | `_coerce` 会静默丢掉没在 `inputSchema.properties` 里点名的参数，于是"没有 properties"的远端工具看起来装配成功、实际**任何参数都传不过去** | 装配期只查了"有没有 inputSchema"，没查它里面有没有 properties；一个裸 `{"type":"object"}` 通过校验然后吃掉全部入参 | 没有 `properties` 的 schema 整条不装配，并把原因写进 `skipped` | 发现第 3 条跳过原因；证据脚本 4 采纳 / 4 跳过里能数到它 |
| 21 | 只对自己的 fixture 量"解析正确"是不成立的证据 | 自写服务端和客户端可能**错得一致**（例如两边都按 dict 读 content，就永远发现不了真 SDK 返回的是 dataclass） | 第二个对手用官方 SDK 写，两条发现路径共用同一份断言 | `tests/fixtures/mcp_sdk_server.py`（`FastMCP`，`protocolVersion 2024-11-05`）；`mcp` 只进 dev extra，运行时依赖预算（§2.4）不变 |

---

## 11. 目录结构

```
mini-claude-code/
├── main.py                     零安装入口
├── SPEC.md                     v1.0 设计契约（先讨论后实现）
├── SPEC-v2.md                  v2.0 增量规格：度量先行，S8–S16 路线图
├── pyproject.toml              mcc 命令、可选 rich、pytest 配置
├── conftest.py                 把故意红/带 bug 的 fixtures 挡在收集范围外
├── src/miniclaude/
│   ├── messages.py             Role / Block / Message / Usage / StopReason
│   ├── config.py               唯一读取环境变量的地方 + 密钥脱敏
│   ├── llm/                    LLMClient 协议、OpenAI 兼容实现（httpx）
│   ├── tools/                  7 个工具 + 注册表 + 工作区路径锁
│   ├── agent/                  loop / planner / prompts / permissions
│   │                           / context / state / todo_tool
│   ├── cli/                    build_session、REPL、渲染（rich 可选）
│   │                           / trace_cmd（`mcc trace` 三个视图）
│   │                           / eval_cmd（`mcc eval` 批跑入口，退出码=结论）
│   ├── memory/                 工作记忆与仓库地图（S11 · 目录名由 MEMORY_DIR 定）
│   │   ├── store.py            `memory.json`：四类记忆、指纹、原子写、坏文件退化成没缓存
│   │   └── repo_map.py         ast 符号图 + 三条排序信号 + token 预算裁剪 + 过期只重建单文件
│   ├── backend/                执行后端（S13）
│   │   ├── protocol.py         ExecutionBackend 协议 + `Command = str | Sequence[str]` + BackendResult
│   │   ├── local.py            现状行为，一字不改（必须继承 os.environ）
│   │   ├── docker.py           `docker run` 一次一条：不带宿主 env、argv[0] 换 python3、超时兜 rm -f
│   │   ├── checkpoints.py      影子 git（独立 --git-dir），两个后端共用同一份
│   │   ├── factory.py          点名 → 探测 → **显式**降级，原因进 trace 与终端
│   │   └── sessions.py         SessionSnapshot 落盘与读回（原子写、坏现场拒绝而不是猜）
│   ├── ext/                    第三方能力（S14）
│   │   ├── mcp.py              MCPBridge + RemoteTool：stdio 握手、`mcp__` 命名空间、风险不接受自报、env 白名单
│   │   └── skills.py           SkillLoader（目录/正文分家）+ `load_skill`，frontmatter 手写解析不引 YAML
│   ├── infra/
│   │   ├── trace.py            JSONL 轨迹、replay、summarize、密钥脱敏
│   │   └── failure.py          失败模式分类学（8 条规则，在线/离线共用）
│   └── eval/                   评测层（S9）
│       ├── taskset.py          题集加载与内容哈希，未知字段一律拒绝
│       ├── contract.py         判据原语：隔离副本、用例级白名单、只增不删（与 demo 共用）
│       ├── judge.py            十步固定顺序的判定流水线 + `lint_task` 考题自检
│       ├── drivers.py          fake 引擎的剧本（复用 demos/fake_scripts，不联网）
│       ├── runner.py           串行批跑、manifest 续跑、批次预算闸
│       ├── metrics.py          pass@1 / pass@k / Wilson 区间 / 失败模式分布
│       └── regression.py       基线读写与逐题配对比较、判据自检
├── eval/
│   ├── tasks/                  24 道考题（题面不泄漏修法；负样本标 must-fail）
│   ├── tasks-b2/               B2 的载体题 `lc-rollup-api`（必然超 32k 预算的长任务）
│   ├── fixtures/               被刻意做成有 bug / 测试是红的小仓库（考题的靶子）
│   │   └── ledger/             八模块 × 100 公开函数 ≈ 19.4 万字符，由脚本确定性生成
│   ├── baselines/              `fake-<题集哈希>.json` —— B1 的基线，进版本库
│   ├── results/                验收线的证据文件（b2/b3 的 A/B 判据、live 冒烟报表；数字不可复现所以入库）
│   └── .work/                  工作副本与逐条记录（忽略，报表与基线才提交）
├── scripts/                    probe_caps / probe_window / probe_parallel_share / probe_verifier_gate / b4_label_check / b2_compact_ab / b3_repomap_ab / b6_backend_ab / s14_ext_demo 等证据生成器
├── docs/
│   └── framework-equivalence.md  LangGraph ↔ 本项目的概念对照 + 为什么不用它（D26，≤200 行）
├── skills/                     技能目录（一个目录一个 SKILL.md，正文按需展开）
│   ├── add-eval-task/          给 eval/ 加一道新题时怎么写判据
│   └── trace-triage/           从 `mcc trace --why-failed` 的标签走到处方
├── tests/                      680 项，FakeLLM 驱动，不联网（schema_v2.json 是 trace 契约快照；fixtures/ 里两个 MCP 服务：自写的敌意版 + 官方 SDK 版）
└── demos/
    ├── run_demo.py             隔离副本 → 跑真 Agent → 独立判据 → 生成证据
    ├── fake_scripts.py         FakeLLM 轨迹（脚本化，不报自述数字）
    ├── fixtures/               greenfield / bug-hunt / red-tests / contradiction
    ├── results/                证据（README 的数字都来自这里；v1-baseline/、b4-live/ 是归档批次）
    └── traces/                 每次运行的 JSONL（顶层=最近一次，v1-baseline/ 与 b4-live/ 冻结留档）
```

---

## 12. V2 建议

按投入产出排序：

> 本节是 v1.0 收尾时写的展望。**v2.0 的实际计划以 [`SPEC-v2.md`](SPEC-v2.md) 为准**：下面第 1、2 项
> 分别对应 S10/S11（压缩阶梯、仓库地图）与 S9（`eval/` 评测层），S8 已经先把"数字怎么来的"这件事
> 修成可核对的口径（§6.4、§6.5）。**第 1 项的压缩那一半已在 S10 落地（§4.7，B2 的 fake 侧达成）；
> 仓库地图那一半已在 S11 落地（§4.8：`memory/repo_map.py` + `.mcc/` 工作记忆），两半判据都跑完了，
> 结果是 **B3 未达成**：机制层 7/7 绿，因果层轮数 0%（线 ≥20%）、token +5.4%（线 ≤15%）。
> 第 2 项已在 S9 落地（§4.6，B1）。S13 落地了沙箱后端 / 检查点 / 崩了续跑（§4.10，B6 达成 12/12），
> S14 落地了 MCP 桥与按需加载的技能（§4.11，20/20 前提），**S12 被自己的数据闸砍进了 Tier 3**
> （§4.9）—— 下面这份列表保留原样，它记录的是"v1 收尾时以为下一步该做什么"，不是待办。

1. **上下文压缩 + `memory/repo_map.py`**（解锁大仓库）。压缩必须保 `tool_calls`/`tool` 配对，且压缩前后跑同一批回归测试，否则就是把 400 换成静默变笨。
2. **`eval/` 层：把 demo 判据变成可批量跑的评测**。`AgentResult` 的形状现在就定死了，V2 直接消费，不用回改核心循环。目标是从"4 个 demo 各跑一次"升级到"20 个任务 × 5 次，报通过率与方差"。
3. **多语言 `run_tests`**：把 pytest 特化换成检测器 + 命令模板，工具层已经与语言无关，缺的只是配置。
4. **A1 换成真实开源仓库**：解禁网络后重跑，同时把仓库地图换成可持久化的 `repo_map`。
5. **VLM / 多 Agent / RL**：等 A1–A4 在真仓库上有稳定通过率再谈，现在加进去只会掩盖工程闭环的缺口。

---

## 13. 可视化学习材料

项目包含交互式可视化学习页面，帮助理解 MiniClaudeCode 的架构和机制。

### 13.1 面试通关之旅

`review/miniclaude-interview-guide.html` 包含：
- **架构可视化**：交互式展示 CLI → Agent → Tools/LLM 三层架构
- **循环动画**：Ask → Act → Observe 循环的实时演示
- **烧钱模拟器**：模拟轮数/token/上下文/停滞四种止损机制
- **权限攻防**：五级闸门的交互式演示
- **刷题闯关**：110 道面试题的翻卡游戏

### 13.2 美少女学园

`review/mmc-academy.html` 包含：
- **工具角色化**：8 个工具以二次元角色形式呈现
- **循环模拟器**：带动画的 Agent 执行流程演示
- **交互式学习**：通过游戏化方式理解核心概念

### 13.3 动漫可视化学习技能

[`anime-visual-learning-skill`](https://github.com/magickkdd/anime-visual-learning-skill) 提供动漫风格的可视化学习体验：
- **直接点击链接学习**：下载 zip 后解压，打开 HTML 文件即可开始
- **动漫角色引导**：通过二次元角色讲解 MiniClaudeCode 的核心概念
- **交互式练习**：游戏化的学习方式，边玩边学

### 13.4 使用方式

```bash
# 本地可视化学习材料
start review/miniclaude-interview-guide.html
start review/mmc-academy.html

# 在线动漫学习技能
# 访问 https://github.com/magickkdd/anime-visual-learning-skill
# 下载 anime-visual-learning.zip 并解压
```

所有可视化页面完全离线可用，无需服务器或网络连接。
