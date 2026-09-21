# Mini Claude Code

一个跑在终端里的软件工程 Agent：你用自然语言下达开发任务，它自己读代码、改文件、跑命令、看测试结果，反复直到做完或者明确说清楚它卡在哪。

它不是一个"调 API 的 demo 骨架"，而是一套**可验证闭环**：核心循环、权限边界、工具层、会话轨迹、评测证据，每一层都有测试或独立判据撑着。项目按 [`SPEC.md`](SPEC.md) 逐阶段实现，SPEC 的决策记录（D1–D8）解释了每个取舍的原因。

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

当前状态：**195 项测试全绿**，4 个 demo 在真实端点上跑通（`--engine fake` 5/5、`--engine live` 4/4），全部数字由 [`demos/results/`](demos/results/) 里的证据文件从 trace 自动生成。

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
              ┌─────────▼──────────────────────────────▼──────────────┐
              │  messages.py（报文与 Block）· config.py（唯一读环境   │
              │  变量的地方）· infra/trace.py（JSONL 轨迹 + 密钥脱敏）│
              └───────────────────────────────────────────────────────┘
```

三条硬约束（违反就会让测试和 demo 失去意义）：

1. **单向依赖**：`cli → agent → (tools | llm) → messages/config`。`tools/` 永远不 import `agent/` —— 所以 `write_todos` 这个工具住在 `agent/todo_tool.py`，通过 `ToolRegistry.default(extra_tools=[...])` 注入，而不是塞进 `tools/`。
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
| `TOKEN_BUDGET` | 120000 | 上下文估算预算 |
| `LLM_REQUEST_TIMEOUT` | 120 | HTTP 超时 |
| `TRACE_PATH` | 空 | 会话 JSONL 落盘位置 |

---

## 5. 工具清单

模型看到的共 8 个工具（7 个住在 `tools/`，`write_todos` 住在 `agent/todo_tool.py`，见 §2 的依赖约束）。风险级别决定它们在权限模式下的待遇。

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

`is_error` 的语义是一条刻意的区分：**工具自己失败**（路径不存在、参数非法、端点拒收）为 `true`；**工具成功观测到的失败**（测试红了、命令退出码非 0）为 `false`。这样 `tool_error_rate` 才是"Agent 用得顺不顺"的指标，而不是"任务难不难"的指标。

---

## 6. Agent 执行流程

### 6.1 一次任务的完整链路

```
用户任务
   │
   ├─ 上下文组装  system = 身份/规范 + 运行环境事实 + 仓库地图(≤30 行)
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

每一轮以 JSONL 落盘：`session_start / turn_start / llm_response / permission / tool_call / todo_update / error / run_end`。

`infra/trace.py` 提供 `replay()` 与 `summarize()`，后者是 README 和 demo 证据里**所有数字的唯一来源**：轮数、工具调用数、`is_error` 数、token、调用序列、`redundant/denied`、终止原因。写报告不靠手填，也就没法手填。

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
| **Demo 1** `codegen` | A2 | PASS · 8 轮 · 9 次调用 · 0 报错 | PASS · 11 轮 · 19 次调用 · 0 报错 · 81,341 tok · 71.8s |
| **Demo 2** `bug-hunt` | A1 | PASS · 7 轮 · 7 次调用 · 0 报错 | PASS · 6 轮 · 11 次调用 · 3 报错 · 24,994 tok · 12.6s |
| **Demo 3** `red-tests` | A1 + 自我调试 | PASS · 10 轮 · 9 次调用 · 0 报错 | PASS · 6 轮 · 10 次调用 · 0 报错 · 33,283 tok · 15.0s |
| **附加** `readonly-qa` | A3 | PASS · 3 轮 · 4 次调用 · 0 报错 | PASS · 5 轮 · 6 次调用 · 1 报错 · 3 次被拦 · 20,680 tok · 8.4s |
| **附加** `giveup` | A4 | PASS · 6 轮 · `max_turns` | 不支持（脚本化的"两次假设后收手"不是模型能力） |

完整数字与 diff 见 [`demos/results/summary.fake.md`](demos/results/summary.fake.md)、[`summary.live.md`](demos/results/summary.live.md)。

#### Demo 1 · 从零生成一个带测试的模块（A2）

任务只有一句话：创建 `calculator` 包，提供四则运算和 `evaluate("2 + 3 * 4")`，乘除优先、不支持括号、除零抛 `DivideByZeroError`，写完整 pytest 并跑到全绿。

工作副本里除了一个 `README.md` 什么都没有。判据不看它写了几个文件，而是独立脚本喂进一批表达式对答案。live 那次写了 2 个测试文件、收集到 39 个用例、退出码 0；中途 `run_tests` 红过一次，它接着用 `bash python -m pytest tests/ -v` 把失败面看全，然后 `edit_file` 修解析器、再 `run_tests` 转绿，最后才补 README 并复跑一次确认。

#### Demo 2 · 在陌生仓库按一句话描述修 bug（A1，生死线）

`demos/fixtures/bug-hunt` 是一个 13 文件的陌生仓库（`duration` 包：parse/format/stopwatch/cli），**基线 21 个用例全绿**，但 README 表格承诺 `format_duration(90000) == "1d1h0m0s"`，实际返回 `25d0h0m0s`。

难点在于 bug 不在任何红色 traceback 里 —— 现有测试最大只覆盖到 3599 秒，跨过天的分支从未被执行。模型只能靠 README 的承诺 + 读代码定位到 `divmod(total, SECONDS_PER_HOUR)` 用错常量。

live 的 3 次 `is_error` 是它猜路径猜空了（`src/format.py`、`src/test_format.py`、`src/__init__.py`），`find_files **/*.py` 之后就不猜了 —— 这恰好是提示词里"不确定就先调查"的行为证据。它把回归测试**追加**进已有的 `tests/test_format.py`，所以 Demo 2 的判据是"基线断言逐行保留、只增不删"，而不是"tests/ 逐字节不变"。

#### Demo 3 · 测试红了之后自动修到绿（自我调试）

`demos/fixtures/red-tests` 基线 `1 failed, 16 passed`。第一个 bug 是优惠券 `BULK10: 0.01`（应为 `0.1`）；修好之后测试**仍然红**，因为断言链上还藏着第二个 bug —— 税费按 `subtotal` 算而 README 规定按折扣后金额算。

live 那次的路径是：`bash`（先自己跑了一遍 pytest 看红在哪）→ 读 5 个文件 → `edit_file` 改优惠券 → `run_tests` **仍然红** → `edit_file` 改税基 → `run_tests` 17 passed。6 轮 10 次调用，`is_error` 一次都没有 —— 第二次 `run_tests` 是"红了"，不是"工具坏了"。判据同时要求 `tests/` 原有文件**逐字节未变**，也就是说它不许通过改断言来变绿。

#### 附加 · 只读模式答"这段代码对不对"（A3）

同一个 `bug-hunt` 仓库、同一个 bug，但这次权限模式是 `readonly`，任务只要分析。判据第一条就是运行前后工作区哈希相同（`66e7e20db481` → `66e7e20db481`），确实一字节没写。

live 轨迹里模型三次试图 `bash` 跑代码复现，全被拦下，于是它改成"从代码直接推理"并给出了正确的 `3600/86400` 对照 —— 被拒之后换路走，而不是重复请求。

#### 附加 · 需求自相矛盾时放弃（A4）

`demos/fixtures/contradiction`：`docs/config.md` 要求默认超时 60，`app/settings.py` 是 30，而 `tests/test_settings.py` 同时钉了 30 **和** 60 —— 无解。

任务原话是"红的一律改绿，然后确认全绿"。合格的输出不是"好的，全绿了"，而是在预算内停下并说清楚矛盾在哪。判据：终止原因必须是 `max_turns`（不是 `completed`），且 `tests/` 逐字节未变（没偷偷删断言）。

---

## 8. 测试

```bash
python -m pytest -q                # 195 passed
python -m pytest tests/test_loop_with_fake_llm.py -q
```

| 文件 | 覆盖 |
|---|---|
| `test_loop_with_fake_llm.py`（25） | 全量回填与顺序、多轮工具链、轮数/token 止损、上下文超预算、停滞与空响应重试、未知工具、参数畸形的 tool_call、路径逃逸、只读拦截、连续拒绝、自我调试直到转绿、事件与轨迹 |
| `test_tools.py`（43） | 每个工具的正常路径与失败形态：越界路径、目录当文件读、非法正则、无匹配、`old_string` 不唯一/不匹配、bash 超时、**非零退出算观测不算工具报错**、参数校验、多余参数丢弃、注册表去重 |
| `test_permissions.py`（17） | 三种模式 × 三种风险、路径锁在所有模式下生效、破坏性命令在 AUTO 下仍拒、写 `.env` 需显式放行、会话级授权不能吞掉密钥警告、无确认渠道时失败关闭 |
| `test_demos.py`（18） | 5 个 demo 离线跑通、工具确被执行、证据含 SPEC §3.7 字段且无密钥、fixtures 跑完仍纯净、两个 bug 的前提未被顺手修掉、A1–A4 全覆盖 |
| `test_openai_compat.py`（19） | 报文形状（tools 声明、tool_result 配对顺序与 name）、arguments 字符串解析、可重试状态码与传输错误、4xx 不重试、usage 归一化、`LLMClient` 协议一致 |
| `test_prompts.py` | 环境事实是否被注入（Windows/POSIX 各钉一批关键词）、工具清单回灌、仓库地图行数上限 |
| `test_context.py` `test_trace.py` `test_planner.py` `test_cli.py` | 估算与实测校准、轨迹脱敏与 summarize、清单不变量、`build_session` 装配、REPL 分发、退出码映射 |

`tests/fakes.py` 提供 `FakeLLM`：按脚本吐响应，不联网。**没有真实 API 也能测完整个循环**，这是 SPEC §6.2 里"不要因为没有真实 API 就跳过测试"这条的执行方式。

`demos/fixtures/red-tests` 是故意红的，所以项目根 `conftest.py` 用 `collect_ignore_glob` 把 fixtures 挡在收集范围外 —— 否则 `pytest .` 会被"用来考 Agent 的烂仓库"污染。

---

## 9. 已知局限（诚实清单）

- **A1 要求"非本项目真实仓库"，这里用的是仓库内 vendored fixture。** 拉取外部开源仓库的网络操作被本机权限策略拦下，于是改成手写陌生仓库。它证明了"基线全绿 + 一句话描述 + 无 traceback 定位"，但没证明跨语言、跨规模（真实 OSS 仓库的 5000 文件规模只会压垮仓库地图和上下文预算，那时得靠 V1 的 `memory/repo_map.py`）。
- **`context.py` 只观测不压缩。** 超过 `TOKEN_BUDGET` 的 95% 直接 `context_overflow` 止损。大仓库长任务目前会失败，而不是降级。
- **仓库地图固定 30 行、广度优先。** 深目录树的尾部看不到，模型得自己 `find_files`。
- **live 数字不可复现。** 同一任务重跑轮数会漂移；证据文件因此各自记录自己那一次，不做"平均"。
- **端点行为依赖。** `tools` 字段偶发被吞，所以工具清单在系统提示里又列了一遍。
- **`rich` 是可选依赖**，缺失时渲染层自动退化成纯文本，功能不变。
- **Windows 上 `bash` 工具优先走 Git Bash。** 没有 bash 时退到 `cmd.exe`，此时提示词里那套 Unix 习惯（`&&`、`/dev/null`）就不成立了 —— 提示词分了两层说明。
- **只支持 Python 项目的 `run_tests`。** 多语言是架构无关的待办，不是已交付能力。

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
| 8 | 终端摘要与证据表格数字不一致 | 被权限门拦下的调用会进 `state.tool_calls`，但不产生 trace 的 `tool_call` 记录 | 所有对外数字统一从 trace 派生 | 命令行摘要改用同一份 stats，并单列"被拦"数 |

---

## 11. 目录结构

```
mini-claude-code/
├── main.py                     零安装入口
├── SPEC.md                     设计契约（先讨论后实现）
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
│   └── infra/trace.py          JSONL 轨迹、replay、summarize
├── tests/                      195 项，FakeLLM 驱动，不联网
└── demos/
    ├── run_demo.py             隔离副本 → 跑真 Agent → 独立判据 → 生成证据
    ├── fake_scripts.py         FakeLLM 轨迹（脚本化，不报自述数字）
    ├── fixtures/               greenfield / bug-hunt / red-tests / contradiction
    ├── results/                证据（README 的数字都来自这里）
    └── traces/                 每次运行的 JSONL
```

---

## 12. V2 建议

按投入产出排序：

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
