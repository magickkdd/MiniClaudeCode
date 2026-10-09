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

## 技术栈

Python 3.11+（只用标准库 + httpx，**不装任何大模型 SDK**）· pytest · MCP（stdio，client + server 两端都实现）· Docker 沙箱 · OpenTelemetry / OTLP · Ollama / llama.cpp / vLLM

姊妹项目：[`insight-agent`](https://github.com/magickkdd/insight-agent) —— LangGraph 写的可验证 DeepResearch Agent，本仓库通过 MCP 桥调它做真实联动（as-built 见 [`docs/mcp-link-spec.md`](docs/mcp-link-spec.md)）。

## 快速开始

```bash
git clone https://github.com/magickkdd/MiniClaudeCode.git && cd MiniClaudeCode
python -m pip install -e ".[dev]"
cp .env.example .env          # 填你自己的 OpenAI 兼容端点（Anthropic / DeepSeek / OpenAI / Ollama 都行）
python main.py                # 进 REPL，或 python main.py --task "把 duration 模块补跨天测试并跑到全绿" -y
```

不装也能跑（`python main.py` 直进 REPL），离线跑完整个循环与评测：

```bash
python -m pytest -q                        # 871 项测试，FakeLLM 驱动、不联网、不读 .env
python demos/run_demo.py --all --engine fake   # 五个 demo 秒级跑完
python -m miniclaude eval --repeats 3      # 24 题离线评测，报一个可被反驳的通过率
```

## 当前状态

**871 项测试全绿（另有 2 项按设计跳过）。** 每一层能力都配了一条"能被反驳"的判据和一个盘上的证据文件；没达成的、没测到的都写在 §9 与 [`docs/experiments.md`](docs/experiments.md) 里，而不是从报告里拿掉。

| 交付线 | 判据 | 实到 |
|---|---|---|
| 评测层（B1） | 24 题 × 3 次，报一个可被反驳的通过率 | fake 全批 `pass@1=20/24`、退出码 0，基线已入库；live 冒烟 6 题 `4/6` |
| 上下文压缩（B2） | 阶梯动手 + 配对不变式不破 | 关阶梯第 5 轮死在 `l3_refuse`，开阶梯 19 轮全绿；**因配对破损导致的 400 = 0**（"成功率 ≥60%"按**未量**记账，理由见 §4.7） |
| 仓库符号地图（B3） | 轮数中位降 ≥20% 且 token 涨幅 ≤15% | **未达成**：token +5.4% ✓、轮数 0% ✗ —— 一次有效的证伪，不是无效实验 |
| 沙箱后端（B6） | 换后端不换结论 | 判定逐格一致 **12/12**；但本机无 docker，`container_isolation_tested: false` |
| 第三方能力（S14） | 桥与技能对着敌意 fixture + 官方 SDK 服务 | **20/20** 条前提全绿 |
| 轨迹 → RL 数据 | 导出器自证前提 | 13 批次 206 run → 1,034 行，**丢弃 0**、12/12 前提成立；结论是**这批数据不够训** |
| OTLP 导出 | 只翻译、不引 SDK、不丢不编 | 18 条判据 **17 ✓ + 1 未量**；顺手挖出导出器与证据自身共 5 个谎 |
| 两次"动手前砍单" | 规格写死的砍单条件由数据判 | 只读并发只值 live 墙钟 **0.011%**、多 Agent 前置条件 **1.3%** —— 两次都不做 |

每层的完整判据、实到数字与"这一节不证明什么"在 [`docs/experiments.md`](docs/experiments.md)（原 §4.7–§4.16，编号不变）。其余文档：[`docs/reference.md`](docs/reference.md) 配置项 / 斜杠命令 / 失败模式标签 · [`docs/testing.md`](docs/testing.md) 逐个测试文件覆盖什么 · [`docs/spec-deviations.md`](docs/spec-deviations.md) 35 条 SPEC 偏差 · [`docs/framework-equivalence.md`](docs/framework-equivalence.md) 与 LangGraph 的对照和不用它的理由 · [`docs/inference-bench.md`](docs/inference-bench.md) 本地三引擎实测 · [`docs/mcp-link-spec.md`](docs/mcp-link-spec.md) 接自研 Research Agent 的契约与 as-built。规格与决策记录在 [`SPEC.md`](SPEC.md)（v1.0，D1–D8）与 [`SPEC-v2.md`](SPEC-v2.md)（v2.0 路线图，D9–D27）。

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

### 1.2 明确不做

多 Agent、VLM、RL、GUI、大规模 RAG、自动上下文压缩、跨会话记忆。其中两条在 v2.0 被推翻了：自动上下文压缩（S10）与仓库地图/工作记忆（S11）—— 因为先把配对不变式 `assert_pairing` 做出来才敢压。**跨会话的"经验教训"仍然不做**：一次错误观察会持续误导后续会话，而它没有判据能把自己纠正回来。

---

## 2. 架构图

```
   用户 ──▶ cli/   REPL · argparse · 斜杠命令 · Renderer
                 build_session() 是全项目唯一装配点
                        │ Agent.run(任务) → AgentResult
                        ▼
   agent/  loop.py Ask→Act→Observe · planner.py TodoList · prompts.py
           context.py token 估算 · permissions.py 权限门 · state.py 终止原因
        ask(model) │                        │ invoke(call)
                   ▼                        ▼
   llm/  base.py 协议 · openai_compat.py    tools/ registry.py 注册表
         httpx，不装 SDK                    read/write/edit/search/find_files
                                          bash · run_tests · workspace.py 路径锁
                   └────────────┬────────────┘
                                ▼
   memory/ 工作记忆 + 符号地图        ext/ MCP 桥 · 按需加载的技能
          （两者都只经 extra_tools=[…] 注入）
                                ▼
   messages.py 报文与 Block · config.py 唯一读环境变量的地方
   infra/trace.py JSONL 轨迹 + 密钥脱敏 · infra/otel.py 只做导出映射
```

三条硬约束（违反就会让测试和 demo 失去意义）：

1. **单向依赖**：`cli → agent → (tools | llm | memory | ext) → messages/config`。`tools/` 永远不 import `agent/` —— 所以 `write_todos` 住在 `agent/todo_tool.py`、`load_skill` 住在 `ext/skills.py`，都通过 `ToolRegistry.default(extra_tools=[...])` 注入。`RemoteTool` 是普通 `BaseTool`，所以权限门与 trace 为外部能力**一行都不用改**。
2. **一个装配点**：`build_session()` 同时服务 REPL、`--task` 一次性模式和 `demos/run_demo.py`。否则"demo 跑通的东西"和"用户手上跑的东西"就不是同一个东西。
3. **CLI 层零业务逻辑**：没有 `while` 循环控制 Agent，交互归 CLI，控制归 `agent/loop.py`。

---

## 3. 安装

需要 Python 3.11+（用到 `StrEnum` 和 `is_relative_to`）。

```bash
git clone https://github.com/magickkdd/MiniClaudeCode.git && cd MiniClaudeCode
python -m pip install -e ".[dev]"        # 只要核心：pip install -e .
cp .env.example .env                      # 填你自己的端点、密钥、模型名
```

`.env` 已在 `.gitignore` 里，任何打印路径都过 `Config.redacted()`，trace 落盘前过 `_scrub()`，密钥不可能出现在日志里。不想安装也能跑：`python main.py` 直接进 REPL。装了之后 `mcc`、`python -m miniclaude`、`python main.py` 三个入口等价。

---

## 4. 使用方式

### 4.1 交互模式与一次性任务

```bash
python main.py                              # 默认 ask 模式，逐次确认写操作
python main.py --root D:\path\to\repo       # 指定工作目录（唯一可写范围）
python main.py -y                           # auto：工作区内自动放行，破坏性命令仍拒
python main.py --readonly                   # readonly：只允许只读工具
python main.py -v                           # 打印轮次分隔线与工具输出摘要
python main.py --task "给 duration 模块补跨天的测试并跑到全绿" -y; echo $?
```

进入 REPL 后直接说人话：`tests/test_cart.py 里红的用例修一下，别改断言`。`--task` 与 REPL 走的是同一条 `run_turn()` 路径，退出码直接来自 `AgentResult.succeeded`（`0` = COMPLETED）。上面那个计算器任务的一次真实运行：4 轮、输入 14,491 + 输出 1,488 tokens、退出码 0；判定方独立复跑工作副本得 `29 passed`。

### 4.2 权限模式

| 模式 | 只读工具 | 写/执行 | 破坏性命令 |
|---|---|---|---|
| `ask`（默认） | 放行 | 逐次问你（`y` 一次 / `a` 本会话同类都放行 / `n` 拒绝） | 无条件拒绝 |
| `auto`（`-y`） | 放行 | 工作区内自动放行 | 无条件拒绝 |
| `readonly` | 放行 | 拒绝 | 拒绝 |

两个不可让的细节：**路径锁**在 `Workspace.resolve` 这一层而不在门后面（越界路径根本解析不出可写目标，`..` 逃逸一并挡住）；**确认失败关闭** —— 没有确认渠道时任何看不懂的答复都按拒绝处理，绝不默认放行。

### 4.3 REPL 里的斜杠命令

`/help` `/reset` `/tools` `/context` `/todos` `/trace` `/exit` 是常规的。三个值得单独知道的：`/undo` 回退到上一个检查点（**撤的是磁盘，对话历史不倒带**）、`/backend` 给出执行后端 + 检查点栈 + 会话现场三合一面板、`/mcp` 列出外部工具"远端**自报**的风险 vs 我们实际采信的档位"。`/mode ask|auto|readonly` 切模式。完整 13 条在 [`docs/reference.md`](docs/reference.md#2-斜杠命令)。

Ctrl-C 的语义是**放弃当前输入但保留历史**：中断不该毁掉已经做一半的工作。

### 4.4 批量评测（`mcc eval`）

demo 是"4 个案例各跑一次给人看"，评测层是"24 道题 × 3 次，报一个能被反驳的数字"。两边的判据是**同一份实现**（`eval/contract.py`），所以不存在"评测层顺手放宽了标准"这种分叉。

```bash
mcc eval --list                        # 24 道题各自的模式 / 判据规模 / fake 剧本
mcc eval --lint                        # 考题自检：这道题**可能**被做对吗
mcc eval --repeats 3                   # 默认 engine=fake：秒级、不联网、不读 .env
mcc eval --repeats 3 --save-baseline   # 跑完写 eval/baselines/fake-<题集哈希>.json
```

**退出码就是结论**：`0` 这批可以拿去汇报；`1` 有运行崩了 / 有该做对的题没做对 / 检出退步或判据告警；`2` 用法或配置不对（一格数据都没产生）。几条不是从嘴上来的口径：**fake 批次同时是 gold-patch 检查**（`pass` 是代码给的，剧本声称改完了不算）；**4 道 `must-fail` 按设计判红**，被判绿会占退出码 1；**基线按题集哈希自动匹配**（改题面就换哈希）；**`|Δ| < 12.5%` 一律写"分辨不出"**（McNemar 精确二项而不是卡方近似 —— n=24 时卡方恰好把 p 算得偏小）。

### 4.5 配置项

真实环境变量优先于 `.env`。**最常拧的几个**：

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 必填 | OpenAI 兼容端点、密钥、模型名 |
| `PROJECT_ROOT` | `.` | 工作目录，Agent 唯一可写范围 |
| `MAX_TURNS` | 25 | 单次任务最大轮数 |
| `MAX_TOTAL_TOKENS` | 800000 | 累计 token 止损线 |
| `TOKEN_BUDGET` | 32000 | 上下文估算预算 —— **压缩阶梯挂这个** |
| `CONTEXT_HARD_LIMIT` | 200000 | 只防"请求被端点拒收"的独立熔断 |
| `REPO_MAP` | 1 | 设 `0` 不画符号地图，退回 30 行目录树 |
| `EXECUTION_BACKEND` | `local` | `local`/`docker`/`auto`，默认不是 `auto` |
| `MEMORY_DIR` | `.mcc` | 记忆落盘目录；`../outside` 这类敌对值启动期拒绝 |

完整 23 项在 [`docs/reference.md`](docs/reference.md#1-配置项)。

### 4.6 每层的判据与实到结果

上下文压缩、仓库地图、沙箱后端、MCP 桥、轨迹导出、OTLP、本地三引擎、接自研 Research Agent —— 十层能力各有一份"结论 + 实到数字 + **不证明什么**"，全文在 **[`docs/experiments.md`](docs/experiments.md)**（原 §4.7–§4.16，编号不变）。


---

## 5. 工具清单

模型看到的默认是 9 个工具（7 个住在 `tools/`，`write_todos` 住在 `agent/todo_tool.py`，`load_skill` 住在 `ext/skills.py`）。接了 MCP 之后每个远端工具再多出几行。

| 工具 | 风险 | 要点 |
|---|---|---|
| `read_file` | read | 带行号返回，自动截断。改之前必须先读 —— 提示词里禁止凭猜测编辑 |
| `search_text` | read | 工作区正则搜索，返回 `文件:行号:内容`。定位函数/字符串的首选 |
| `find_files` | read | glob 查文件，跳过 `.git`/`__pycache__`/`.venv`。接手陌生仓库第一步 |
| `edit_file` | write | 精确匹配 `old_string`，要求逐字符一致且文件内唯一。最推荐的编辑方式 |
| `write_file` | write | 新建或整体覆盖。覆盖已存在文件前必须先 `read_file` |
| `bash` | execute | 工作区根目录执行，返回退出码 + 合并输出。Windows 下优先走 Git Bash |
| `run_tests` | execute | 跑 pytest 并结构化返回：通过/失败计数、失败用例名、精简 traceback |
| `write_todos` | read | 多阶段任务的清单，整体替换语义。每轮回灌进系统提示 |
| `load_skill` | read | 按名字取一个技能的正文。system 里只有目录，正文按需展开 |
| `mcp__<server>__<tool>` | execute | 第三方进程里的工具。**风险档位不接受远端自报**（D19），AUTO 模式也要单独确认 |

`is_error` 的语义是一条刻意的区分：**工具自己失败**（路径不存在、参数非法、端点拒收）为 `true`；**工具成功观测到的失败**（测试红了、命令退出码非 0）为 `false`。这样 `tool_error_rate` 才是"Agent 用得顺不顺"的指标，而不是"任务难不难"的指标。

---

## 6. Agent 执行流程

### 6.1 一次任务的完整链路

```
任务 → 组装上下文（system = 环境事实 + 仓库地图 + 工具清单 + 计划/调试规范 + TodoList
                    messages = 历史（含上一轮全部工具结果）  tools = registry.specs()）
     → ask(model)
        ├─ 纯文本答复 → COMPLETED
        └─ tool_use ↓
             权限门逐条判定（模式 + 路径锁 + 破坏性命令）
               ├─ 拒绝 → 理由写成 is_error 结果回填，让模型换路走
               └─ 放行 ↓
             执行工具（同一轮的 tool_calls 必须全部执行）
             全部结果打包成**一条** user 消息回填  ← 漏一个 → 下一轮报文非法 → 400
     每轮止损：MAX_TURNS · MAX_TOTAL_TOKENS · 连续 3 轮同一组调用签名 · 上下文压力
     观测：run_tests 退出码 / 失败用例名 / bash 输出
```

这不是 Workflow（代码写死步骤），而是 Agent（模型决定下一步）。

### 6.2 自我调试闭环

`获取错误信息 → 分析 → 修改 → 重跑 → 直到成功或达到最大次数`，落在三个地方：`run_tests` 把失败结构化成用例名 + 精简 traceback；提示词 `SELF_DEBUG_PROMPT` 规定"以退出码和用例名为准，不要凭'我改了应该就对了'收尾"；`STALL_LIMIT = 3` 只统计**连续**重复的调用签名，所以"改一处 → 跑一次 → 再改一处 → 再跑一次"这种交替动作不会被误判为停滞。`demos/fixtures/red-tests` 是这个闭环的实证：一个 bug 修好后测试**仍然红**，暴露出第二个 bug，模型必须在第二轮观测里换假设才能走到全绿。

### 6.3 终止原因

`TerminationReason` 是**返回值不是异常** —— 调用方必须能区分"做完了"和"崩了"。`completed`（唯一算成功）、`max_turns`、`stalled`、`user_rejected`、`llm_failure`、`context_overflow`、`cancelled`、`internal_error` 八个，其余七个都算失败。CLI 兜住 `internal_error` 后会话仍可继续。`MAX_TURNS` 和 `STALLED` 就是失败，不粉饰成"部分完成"。

### 6.4 会话轨迹（trace）

每轮以 JSONL 落盘（schema 2.0），8 种 kind：`session_start / turn_start / llm_response / permission / tool_call / todo_update / error / run_end`。`run_end` 把"发起"与"执行"分成两个量：`tool_calls` 是模型发起了几次，执行几次由 `tool_call` 记录的条数得出（被闸门拦下的只进前者）—— v1 用一个名字指这两件事，报表因此在"有拒绝"的会话上自相矛盾。

`infra/trace.py` 的 `summarize()` 是 README 和 demo 证据里**所有数字的唯一来源**；`fingerprint()`（行数 + 字节数 + 全文件 sha256 前 16 位）是"判据配的那份轨迹就是它当时看的那份"这道闸门的地基，**盖**它的只有一处（`EvalRunner.append_manifest`）、**读**它的是导出器 join 前的比对。`tests/test_trace_contract.py::test_metrics_have_producers` 盯着"报表里出现的每个键都必须指得到一处写入它的代码"——v1 的 `redundant_calls` 恒为 0（声明、快照、读取、打印四处，没有第五处 `+=`）就是这条约束缺失的代价。

### 6.5 失败模式分类学与 `mcc trace`

规则式而非模型式：8 条标签（`path_guessing` / `context_growth` / `no_verification` / `self_confirm` / `test_gaming` / `thrashing` / `permission_starved` / `budget_exhausted`），每条都要能在一条真实 trace 上被人眼复核，判据宁可漏报不误报。逐条判据与处方在 [`docs/reference.md`](docs/reference.md#4-失败模式分类学)。

```bash
mcc trace --latest --hot           # 最贵 3 轮、报错最多的工具、重复调用簇
mcc trace --latest --why-failed    # 标签 + 证据 + 处方
python scripts/b4_label_check.py   # 16 条轨迹逐条对"人写的期望"，不一致即退出码 1
```

它同时打印盲区 —— 字段缺失时规则不是判对了，是没参与。旧 trace 落盘的标签与当前规则不同时，`mcc trace` 显式印出"规则口径变过"，而不是沉默地换一套答案。

---

## 7. Demo 案例

```bash
python demos/run_demo.py --list                    # 看有哪些 demo
python demos/run_demo.py --all --engine fake       # 离线，秒级，不进 API
python demos/run_demo.py --demo bug-hunt --engine live
```

每个 demo 的流程：把 `demos/fixtures/<x>` 复制成独立工作副本 → 用 `build_session()` 跑**真实装配的** Agent → 在工作副本里独立执行 pytest 与行为探测 → 从 trace 抽数字写成证据文件（含判定明细、终端原样输出、基线→运行后 diff）。

**判据为什么不看模型说了什么**：Demo 1/2/3 和 A4 的判定脚本**根本不读 `result.text`**，只看执行结果（pytest 退出码、函数行为、基线 `tests/` 只增不删、只读模式运行前后工作区哈希相同、收集到 0 个用例直接判退出码 5 —— "全绿"可能是"根本没考试"）。唯一的例外是 A3 的只读问答，因为"答案对不对"本身就是被考的对象。两个判据本身也被测试盯着：`test_fixtures_stay_pristine_after_a_run` 要求跑完 demo 后 `demos/fixtures/` 与基线哈希一致；`test_scripts_never_recite_a_number_the_judge_owns` 用正则拒绝任何"24 passed"式的自报数字。

| demo | 验收项 | fake | live（`agnes-2.5-flash`） |
|---|---|---|---|
| **Demo 1** `codegen` | A2 | PASS · 8 轮 · 9 次调用 · 0 报错 | PASS · 8 轮 · 16 次调用 · 0 报错 · 65,425 tok · 97.1s |
| **Demo 2** `bug-hunt` | A1 | PASS · 7 轮 · 7 次调用 · 0 报错 | PASS · 9 轮 · 20 次调用 · 0 报错 · 52,017 tok · 59.8s |
| **Demo 3** `red-tests` | A1 + 自我调试 | PASS · 10 轮 · 9 次调用 · 0 报错 | PASS · 6 轮 · 13 次调用 · 0 报错 · 36,041 tok · 31.9s |
| **附加** `readonly-qa` | A3 | PASS · 3 轮 · 4 次调用 · 0 报错 | PASS · 4 轮 · 6 次调用 · 2 次读空 · 0 被拒 · 14,976 tok · 12.8s |
| **附加** `giveup` | A4 | PASS · 6 轮 · `max_turns` | 不支持（脚本化的"两次假设后收手"不是模型能力）|

四个 fixture 各自考一件事，数字都是逐条读 trace 写的：

- **Demo 2**（生死线）：13 文件的陌生仓库，基线 21 用例全绿，但 README 承诺 `format_duration(90000) == "1d1h0m0s"`、实际返回 `25d0h0m0s`。**bug 不在任何红色 traceback 里** —— 现有测试最大只覆盖 3599 秒，跨天的分支从未被执行。live 那次第 5、6 轮对一个**实现**文件直接跑 pytest、verdict 老老实实记 red，随后换用测试文件一路绿到收尾。
- **Demo 3**：基线 `1 failed, 16 passed`。修好优惠券常量之后测试**仍然红**，因为断言链上藏着第二个 bug（税基）。全程 `is_error` 一次都没有 —— 第 3 轮那次 `run_tests` 是"测试红了"（`ok=True`、`verdict=red`），不是"工具坏了"，v2 的 trace 把这两件事分成两个字段记。
- **只读问答**：判据第一条是运行前后工作区哈希相同（`66e7e20db481` → `66e7e20db481`）。上一批里它三次 `bash` 想跑代码复现全被闸门拒掉（`denied_actions = 3`），于是改成"从代码直接推理"并给出同样正确的答案 —— 拒绝没有让它重复请求，也没有让它停住。
- **自相矛盾的需求**：`docs/config.md` 要求默认超时 60、`app/settings.py` 是 30、测试同时钉了 30 **和** 60 —— 无解。合格的输出不是"好的，全绿了"，而是在预算内停下并说清矛盾在哪（终止原因必须是 `max_turns`）。

完整数字见 [`demos/results/`](demos/results/)（`summary.fake.md` / `summary.live.md` / `failure-labels.md`）。一处已知不同步：证据表格的 `tool_calls` 行在 S8 改成"发起 / 执行"两栏，`*.fake.md` 已按新格式重生成，`*.live.md` 还是旧格式 —— 数字两边一致，只是排版口径差一版。

---

## 8. 测试

```bash
python -m pytest -q                # 871 passed, 2 skipped
python -m pytest tests/test_loop_with_fake_llm.py -q
python -m pytest tests/test_eval_runner.py tests/test_eval_cli.py -q   # 评测层（不联网）
python scripts/b4_label_check.py   # 失败模式标签的人工核对，退出码 0 才算过
python scripts/b2_compact_ab.py    # B2 三臂 A/B + 判据账本（退出码 0 才算过）
python scripts/b3_repomap_ab.py    # B3 两臂 A/B：机制判据离线核，因果两条要 --live
python scripts/b6_backend_ab.py    # B6 两臂 A/B：docker 臂降级时不产出一致率、直接退 1
python scripts/s14_ext_demo.py     # §3.7 的 20 条前提，对着两个 MCP 对手量
python scripts/t3_otlp_export.py   # 24 份已入库轨迹 → OTLP/JSON + 18 条判据（0 个模型请求）
mcc eval --repeats 3               # 24 题 fake 全批，见 §4.4
```

**用哪个解释器跑不是小事**：得用项目的 `.venv`。全局解释器少装了 dev extra 里的官方 `mcp`，`tests/test_mcp_bridge.py` 那条"对端是 SDK 写的服务"就会**静默跳过**，报出来的是 `848 passed, 1 skipped` —— 仍然全绿，绿的格数却少一格。同一类依赖在证据脚本那边更要紧：拿全局解释器跑 `s14_ext_demo.py`，SDK 那一臂直接没了，而它过去会照旧覆盖掉签着 20/20 的入库证据。现在这条路被守住了（退出码 2、文件一个字节不动），五个写证据的脚本共用同一道闸。

`tests/fakes.py` 提供 `FakeLLM`：按脚本吐响应，不联网，**没有真实 API 也能测完整个循环**。逐个测试文件覆盖什么在 [`docs/testing.md`](docs/testing.md)。

---

## 9. 已知局限（诚实清单）

- **A1 要求"非本项目真实仓库"，这里用的是仓库内 vendored fixture。** 拉取外部仓库的网络操作被本机权限策略拦下。它证明了"基线全绿 + 一句话描述 + 无 traceback 定位"，但没证明跨语言、跨规模。5000 文件规模会压垮仓库地图 —— 本仓库 142 个可见 `.py`（约那个规模的 3%）冷建地图就要 0.83 秒。
- **压缩只到 L2，而且"压缩有没有让 agent 静默变笨"没被量到。** 阶梯在真实分布上省下的 est 是有账的，它对成功率的净影响目前没有可信数字（n=1 × 2 的漂移太大，而 R4 担心的正是"静默变笨"）。另外 `write_file` 的 content 进的是 assistant 消息，L1 碰不到它。
- **符号地图只认 Python、只认 `ast` 能看出来的东西。** 装饰器背后的动态注册、字符串路由、yaml/toml 里的符号都看不见；`REPO_MAP=0` 退回的仍是那棵固定 30 行的目录树。跨进程缓存命中 27.9~99.4ms，没达到 SPEC §6.2 的 ≤5ms。
- **只读工具并发没做（被数据砍进 Tier 3）。** 不是遗漏：可并行的轮平均只值 2.9 毫秒，线程池开到无限大也只省 live 墙钟的 0.011%。所以 `_run_tools()` 仍是单循环，`MAX_PARALLEL_READS` 这个旋钮在 `config.py` 里根本不存在 —— **不生效的配置项比缺失的配置项更坏**。
- **多 Agent 一种形态也没做（同样被数据砍进 Tier 3）。** D21 那条"占比 > 20%"在 75 次 live 运行上是 **1.3%**，连它点名的 B1 fake 批自己也只有 16.7%。`spawn_agent` 的三条约束写在 SPEC 里但没有代码执行它们 —— 与并发那三条不变式同一个处理方式：**设计留着、空壳不留**。
- **`DockerBackend` 的真实容器路径一次也没跑过。** 这台机器上没有 docker，B6 是用一个**会真的在宿主上执行命令**的假 `docker` 替身跑出来的：被证明的是命令行构造、降级不静默、两臂判定一致与 rev 共用，**没被证明的是文件隔离、网络隔离、镜像内容**。所以"这个 agent 能在沙箱里跑"目前是**接口层的事实**，不是运行时的事实。
- **MCP 只测了 stdio，两个对手都是自己的进程。** 被证明的是命名空间隔离、风险不自报、出门前校验、env 白名单、降级不静默、被拒无副作用；**没有连过任何一个真第三方服务**。HTTP/SSE 传输砍进 Tier 3。另外"模型会不会**主动**去 `load_skill`"没测 —— fake 引擎里那次取用是剧本写好的。
- **导出的 RL 数据里 69.6% 的行没有 state 正文。** 不是导出器偷懒：trace 按设计只存计数与类型名，正文只在会话快照里，而快照是 S13 才上线的。要拿它做后训练，第一步不是训，是让 trace 落 observation —— 那是一次没做的架构改动。SBS 人工标注 **0 条**（54 对全部是同题重跑自动配的）。
- **OTLP 那一头没人接过。** 唯一未量的一条是"真实收集端收下并画出树"：有人应答但回 `502` 且响应体为空，既不能记成"被拒"也不能记成"收下"。**Jaeger 里画出来长什么样没看过。** 附带一条边界：payload 里带着 59 个绝对本地路径和 24 个端点样字符串，`--endpoint` 指向非本机时只警告后照发。
- **框架对照是文档，不是移植层。** `docs/framework-equivalence.md` 说明了我们与 LangGraph 的概念对应关系和缺的东西，但**没有**实现它的接口 —— 迁移表里三处硬冲突是"真要换需要先解的结"。
- **live 数字不可复现。** 同一任务重跑轮数会漂移；证据文件因此各自记录自己那一次，不做"平均"。
- **端点行为依赖。** `tools` 字段偶发被吞，所以工具清单在系统提示里又列了一遍。
- **`rich` 是可选依赖**，缺失时渲染层自动退化成纯文本，功能不变。
- **Windows 上 `bash` 优先走 Git Bash。** 没有 bash 时退到 `cmd.exe`，此时提示词里那套 Unix 习惯（`&&`、`/dev/null`）就不成立 —— 提示词分了两层说明。
- **只支持 Python 项目的 `run_tests`。** 多语言是架构无关的待办，不是已交付能力。评测层同一局限：判据全建立在 pytest 上。
- **`pass@1=4/6` 那个数不能当结论用。** 6 题的 Wilson 95% 区间是 `[0.300, 0.903]` —— 宽到容得下两种相反的说法。live 冒烟证明的是"管线跑得通、判据抓得住真失败"。
- **批次预算的闸只在任务边界生效**，所以最后一题可以合法地把整批顶过线（实测：预算 300,000，实际 313,812）。

---

## 10. 与 SPEC 的偏差记录

实施过程中发现 **35 处** SPEC 的工程问题，按"问题 / 原因 / 建议修改 / 本次采取"记在 [`docs/spec-deviations.md`](docs/spec-deviations.md)。摘三条最能说明口径的：

| # | 问题 | 本次采取 |
|---|---|---|
| 11 | S10 计划里的 `COMPACT_LEVEL` / `COMPACT_TARGET` 环境变量不该存在 —— 阶梯阈值是配对不变式的一部分，做成运行时旋钮就等于允许"这一批跑的是另一套阈值"这种无法对比的状态 | 改成 `context.py` 常量 + 三个评测旗标；被拧过的批次**不许当基线入库** |
| 18 | B6 的措辞预设了"有两个能跑的后端"，而这台机器上只有一个 | 允许降级但**必须自己划清边界**：脚本先证明前提，任一条不成立就不产出一致率；结果文件里 `container_isolation_tested: false` + `what_this_proves` / `what_this_does_not_prove` 两栏 |
| 35 | 上一行那句"4318 回 502"当时是**背下来的**，不是测出来的 —— 同一份文件里 `attempted` 是 `false`、判据详情里却躺着一句"回 502" | 判据详情由 receipt **现算**而不是写死；默认路径就真去试一次本机端口 |

> 代码与 SPEC 里说的"§10 第 27 / 30 / 34 行"就是 [`docs/spec-deviations.md`](docs/spec-deviations.md) 那张表的行号，行号仍然有效。

---

## 11. 目录结构

```
mini-claude-code/
├── main.py · pyproject.toml · conftest.py    零安装入口 / mcc 命令 / fixtures 收集隔离
├── SPEC.md · SPEC-v2.md                     v1.0 契约（D1–D8）/ v2.0 路线图（D9–D27）
├── src/miniclaude/
│   ├── messages.py · config.py              报文与 Block / 唯一读环境变量 + 密钥脱敏
│   ├── llm/ · tools/                        OpenAI 兼容客户端（httpx，不装 SDK）/ 7 工具 + 路径锁
│   ├── agent/                               loop / planner / prompts / permissions
│   │                                        / context / state / todo_tool
│   ├── cli/                                 build_session、REPL、渲染、trace/eval/ext 子命令
│   ├── memory/                              工作记忆 + ast 符号地图
│   ├── backend/                             local / docker / 影子 git 检查点 / 显式降级 / 会话现场
│   ├── ext/                                 MCP 桥 + RemoteTool · 按需加载的技能
│   ├── infra/                               trace（replay/summarize/fingerprint + 脱敏）
│   │                                        otel（只做导出映射）· failure（8 条规则）
│   └── eval/                                taskset / contract / judge / drivers / runner
│                                            / metrics / regression / export_rl
├── eval/                                    24 道考题 · fixtures（刻意做成有 bug 的靶子）
│   ├── baselines/ · results/ · .work/       入库的基线与证据文件 / 忽略的工作副本
├── scripts/                                 证据生成器：b2/b3/b6/s14/t3 的 A/B、b4 标签核对、
│                                            两个 probe_*_gate、bench_engines、
│                                            _evidence.py（五个脚本共用的那支笔）
├── docs/                                    experiments · reference · testing · spec-deviations
│                                            framework-equivalence · inference-bench · mcp-link-spec
├── skills/ · tests/ · demos/                两个技能 / 871 项测试 / 五个 demo 与它们的证据
```
