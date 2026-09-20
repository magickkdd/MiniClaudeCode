# Mini Claude Code Agent System SPEC v1.0

版本：v1.0 · 日期：2026-09-19 · 状态：待确认
预算：56 净工时（两周 × 每天 4h）· 交付物：可用 CLI + README/架构图 + 3 个 demo
依据：本文所有取舍来自一轮结构化需求讨论，决策与放弃项记录见 §1.4。

---

# 1. Project Overview

## 1.1 项目目标

构建一个运行在终端里的 Python **Coding Agent**：给它一句自然语言描述的软件工程任务，它在真实仓库内自主完成"定位 → 修改 → 验证 → 自我修复"的闭环，并把全过程留成可审查的轨迹。

**一句话定位**：一个能接手陌生 Python 仓库、把失败测试改到通过的窄而深的 coding agent。

不做通用 agent framework，不做多语言，不做 benchmark 打榜。抽象层保持语言无关，但**实现只为 Python 负责**。

## 1.2 核心能力（MVP 必须交付）

1. **工具驱动的行动**：模型只能通过工具影响文件系统与终端，能力边界由注册表唯一决定。
2. **真实仓库导航**：正则跨文件检索 + glob 找文件 + 带行号读取，支撑"陌生仓库定位 bug"。
3. **精确编辑**：字符串唯一匹配替换，而非整文件重写（省 token、可审查、不会静默丢代码）。
4. **self-debugging 闭环**：测试失败与工具报错以**结构化**形式回传给模型，模型据此自我修正直至通过或明确放弃。
5. **可见的任务规划**：Agent 自己维护 todo 清单并逐项勾选，计划是**可断言的数据结构**而非模型的即兴文本。
6. **分级权限**：只读自动放行，写/执行需授权，破坏性命令强制单独确认，文件访问锁死在工作目录内。
7. **完整可观测**：每轮 LLM 请求/响应、每次工具调用、每个权限决策都落 JSONL，可事后回放。

## 1.3 验收线（定义"完成"）

| 编号 | 验收场景 | 判定方式 |
|---|---|---|
| **A1** | 在一个**非本项目**的真实 Python 仓库里，按一句话描述定位并修复一个 bug，测试由红转绿 | 全程无人工干预，`run_tests` 退出码为 0 |
| **A2** | 从零生成一个带单元测试的小模块（3–6 文件），`pytest` 全绿 | 同上 |
| **A3** | 连续工具调用链（读 → 检索 → 改 → 测 → 再改）中，权限门与 todo 状态始终正确 | 由 trace 回放人工审查 |
| **A4** | 注入一个必然失败的脚本任务，Agent 在预算内**明确放弃并报告原因**，而非无限重试 | 测试断言 `TerminationReason` |

**A1 是本项目的生死线。** A2 是它的副产物。A3/A4 是质量线。任一不过，不算 MVP 完成。

## 1.4 决策记录（做了什么选择、放弃了什么）

| # | 决策 | 采纳 | 放弃的选项与代价 |
|---|---|---|---|
| D1 | 项目定位 | 垂直可用的 coding agent | 放弃通用 framework（抽象厚而能力薄）、放弃教学型（文档优先于能力） |
| D2 | 验收线 | 陌生仓库修 bug + self-debug + 跨文件 + 从零写（全选，取最难） | 这条线迫使 `search_text`/`run_tests` 从"可选"变成"必需" |
| D3 | 编排 | `write_todos` 工具（3h）实现可见规划 | 放弃独立 Planner 阶段（12h）。**理由：显式 planner 对 coding agent 成功率提升存疑，Claude Code 本身无独立 planner；12h 换成工具质量和 A1 的调试时间更划算** |
| D4 | 时间 | 56h 硬预算，47h 计划 + 9h 缓冲 | 放弃"八项各 60%"的宽浅路线 |
| D5 | 评测 | MVP 只交"证据"（demo + trace） | 数字评测推 V2。代价：简历上少一个表格，换来 A1 真跑通 |
| D6 | 执行环境 | Windows 本机 + 会话内一次授权 | WSL2/容器沙箱推 V1。代价：评测无法无人值守批跑（本来也不在 MVP 范围） |
| D7 | Memory | **降级：MVP 不做**。系统提示里注入 ≤30 行的顶层目录树作为零成本近似 | 原先选的"仓库地图 working memory"推到 V1。**这是本次砍单唯一一处与你先前选择不一致的地方**，因为 56h 装不下它 + A1。它不取消，只是延后 |
| D8 | 供应商 | 单端点 OpenAI 兼容，`httpx` 裸调 | 放弃 openai SDK（少依赖、能看原始报文）。已实测通过，见 §3.6 |

---

# 2. System Architecture

```
                        ┌─────────────────────────────────────────────┐
   user task ──────────▶│                 CLI / REPL                  │  cli/
   trace  ─────────────▶│  渲染事件 · 权限确认交互 · 斜杠命令          │
                        └───────────────────┬─────────────────────────┘
                                            │ run(user_input)
                        ┌───────────────────▼─────────────────────────┐
                        │              AGENT CORE  (loop.py)          │
                        │                                             │
                        │   while turn < max_turns:                   │
                        │     resp = llm.create(system, msgs, tools)  │
                        │     if resp.stop_reason != TOOL_USE: break  │
                        │     for call in resp.tool_uses:  ───────┐   │
                        │        gate ──▶ execute ──▶ backfill ◀──┘   │
                        │   termination: completed/max_turns/stalled  │
                        │        │                    │               │
                        │  ┌─────▼─────┐        ┌─────▼──────┐        │
                        │  │  Planner  │        │ Permission │        │
                        │  │ TodoList  │        │   Gate     │        │
                        │  │(write_todos│       │ risk+路径锁 │        │
                        │  └───────────┘        └────────────┘        │
                        └──────┬───────────────────────┬──────────────┘
                               │ specs()/invoke()      │ create()
                    ┌──────────▼───────────┐  ┌────────▼─────────────┐
                    │      TOOL SYSTEM     │  │     LLM ADAPTER      │
                    │ registry + BaseTool  │  │ LLMClient (Protocol) │
                    ├──────────────────────┤  ├──────────────────────┤
                    │ read_file  search    │  │ OpenAICompatClient   │
                    │ find_files edit      │  │  (httpx + 重试 +     │
                    │ write_file bash      │  │   报文归一化)         │
                    │ run_tests write_todos│  └────────┬─────────────┘
                    └──────────┬───────────┘           │
                               │                ┌──────▼──────────────┐
                    ┌──────────▼───────────┐    │  CONTEXT MANAGER    │
                    │  EXECUTION BACKEND   │    │ token 估算/校准/告警 │
                    │  (MVP: 本机 shell)    │    │ V1: compact         │
                    │  V1: WSL2/容器        │    └─────────────────────┘
                    └──────────────────────┘
                    ════════════════════════════════════════════════════
                       infra/trace.py   ◀── 每一层的每个决策点都写事件
                       memory/ (V1)     ◀── 仓库地图，只读注入，不检索
                    ════════════════════════════════════════════════════
```

**两条不可违反的结构约束**

1. **状态只有一处**：`Agent.messages` 是对话的唯一事实来源。工具不持有跨调用状态，CLI 不额外缓存历史。任何"记住点什么"的设计都要先回答"为什么不放进 messages"。
2. **依赖单向向下**：`cli → agent → (tools | llm) → messages/config`。`messages.py` 与 `config.py` 不 import 任何本项目的模块。`tools/` 不得 import `agent/`。循环依赖一旦出现，说明分层切错了。

---

# 3. Module Design

## 3.1 Agent Core — `agent/loop.py`

**职责**：驱动"问模型 → 执行工具 → 回填结果"的循环；拥有对话历史；判定终止条件。**不做**：权限判断（交 Gate）、参数校验（交工具）、渲染（交 CLI）。

**输入**：`user_input: str`
**输出**：`AgentResult{ text, termination: TerminationReason, turns, usage, todo_snapshot, trace_path }`

```python
class Agent:
    def __init__(self, *, llm: LLMClient, registry: ToolRegistry, gate: PermissionGate,
                 context: ContextManager, config: Config, todos: TodoList | None = None,
                 on_event: EventHandler | None = None, tracer: Tracer | None = None) -> None: ...
    def run(self, user_input: str) -> AgentResult: ...
    def _step_tools(self, calls: list[ToolCall]) -> list[ToolResultBlock]: ...   # 顺序执行，全部回填
    def _detect_stall(self, calls: list[ToolCall]) -> bool: ...
    def reset(self) -> None: ...

class TerminationReason(StrEnum):
    COMPLETED / MAX_TURNS / STALLED / USER_REJECTED / LLM_FAILURE / CONTEXT_OVERFLOW / CANCELLED
```

**四个必须实现的控制策略**（它们才是"工程能力"的证据，模型能力之外）：

- **全量回填**：单轮可能返回多个 `tool_calls`（实测端点会并行返回）。必须按顺序执行**全部**调用、把结果**一并**回填，漏一个会导致下一轮 400。
- **停滞检测**：连续 3 次出现（工具名 + 参数）完全相同的调用 → 判定 `STALLED` 并终止，同时报告"模型在重复同一动作"。这是防烧钱的关键闸门。
- **轮数与成本双上限**：`max_turns` 之外再加 `max_cost_tokens`，任一触发即终止。
- **失败不是终点**：工具 `is_error`、权限被拒、未知工具名，三种情况一律转成模型可读的失败描述继续循环；只有 `LLMError` 和上限触发才终止。

## 3.2 Planner — `agent/planner.py`

**MVP 职责**：把"计划"从模型的即兴文本变成 Agent 持有的数据结构，用于 CLI 展示、trace 记录、测试断言。**不做**：独立规划调用、步骤依赖图、自动重规划（那些是 D3 放弃的 12h 方案）。

```python
class TodoStatus(StrEnum): PENDING / IN_PROGRESS / DONE / CANCELLED

@dataclass
class TodoItem:
    content: str          # 一句祈使句，"run the failing test to confirm the fix"
    status: TodoStatus = PENDING

class TodoList:
    def replace(self, items: list[TodoItem]) -> None    # 模型每次调用整体替换
    def mark(self, index: int, status: TodoStatus) -> None
    def render(self) -> str                             # "- [~] xxx" 形式，回填给模型 + CLI 展示
    @property
    def in_progress(self) -> TodoItem | None
    def snapshot(self) -> dict[str, Any]                # 进 trace，供事后算"是否完成了自己声明的步骤"
```

**两条可断言的不变式**（写进测试）：
- 同一时刻 `in_progress` 数量 **≤ 1**；模型一次提交多个时，`replace()` 保留第一个、其余降级为 `PENDING`，并在返回给模型的结果里明确告知。
- `write_todos` 的返回值就是 `render()`，让模型在下一轮看到自己最新的计划 —— 计划必须回到上下文里，否则等于没写。

**提示词契约**（`prompts.py` 里的原文要求）：≥3 步的任务先调用 `write_todos` 列出计划；每完成一步立即更新；**不要**为了已一眼可见的单步操作创建 todo。

**V1 升级路径**：若 A1 稳定通过后仍有余量，加 `PlanStep{intent, action, verify}` 与"验证失败即重规划"，把 `TodoItem` 平滑扩展为 `PlanStep`（见 §7）。

## 3.3 Tool System — `tools/`

**职责**：定义 Agent 的全部能力边界。`registry` 决定模型能看见什么，`BaseTool.invoke` 保证任何工具失败都不炸循环。

```python
class BaseTool(ABC):
    name: str; description: str; input_schema: dict[str, Any]
    risk_level: RiskLevel                       # READ / WRITE / EXECUTE
    def spec(self) -> ToolSpec: ...
    def run(self, **kwargs) -> ToolResult: ...          # 子类实现
    def invoke(self, tool_use: ToolCall) -> ToolResult: ...  # 校验 + 兜底捕获，永不外抛
```

**MVP 工具集（8 个）**

| 工具 | 风险 | 关键语义 | 为什么这样设计 |
|---|---|---|---|
| `read_file(path, offset, limit)` | READ | 行号输出；目录/二进制/超大文件返回明确错误 | 行号让模型能引用位置并配合 `edit_file` 定位 |
| `search_text(pattern, path_glob, max_hits)` | READ | 返回 `file:line:content`，命中数与截断标记 | **A1 的生命线**：没有它，模型只能逐个 `read_file` 猜 |
| `find_files(glob)` | READ | 返回排序后的相对路径，排除 `.git/__pycache__/.venv` | 便宜、快速建立仓库形状感 |
| `edit_file(path, old_string, new_string, replace_all)` | WRITE | 唯一匹配；0 处/多处返回诊断文案而非失败 | 匹配数>1 时要告诉模型"扩大上下文"，这句话是它自愈的线索 |
| `write_file(path, content)` | WRITE | 覆盖已存在文件前报告将删除多少行 | 抑制模型"顺手重写整个文件"的破坏倾向 |
| `bash(command, timeout)` | EXECUTE | 合并 stdout/stderr、超时强杀、首尾截断、回传退出码 | 四条硬约束缺一条就会卡死或撑爆上下文 |
| `run_tests(target)` | EXECUTE | 结构化摘要：通过/失败计数 + 失败用例名 + `--tb=short` 片段 | **self-debug 的燃料**：把原始 stdout 变成可判定的失败原因 |
| `write_todos(todos)` | READ | 整体替换并回渲染结果 | 见 §3.2 |

**工具 description 编写规范**（description 就是 prompt，直接决定调用正确率，JD 第 12 项的落点）：每个 description 必须覆盖四问 —— ①什么时候用 ②用之前该做什么（前置条件，如"改之前必须先读"）③失败意味着什么 ④不要用它做什么（如"局部修改请改用 edit_file"）。禁止只写一句功能名词。

**输出大小策略**：工具自行按语义截断（`read_file` 按行、`bash` 按字符首尾各半），`Agent._step_tools` 再兜底二次截断到 `tool_output_limit`，并附 `[output truncated: N chars omitted]`。

## 3.4 Memory — `memory/`（**V1，MVP 不做**）

**立场**：MVP 用"提示词里注入 ≤30 行顶层目录树"近似仓库认知 —— 零额外调用，覆盖 80% 价值。真正的仓库地图推到 V1。

**V1 接口预先定义（避免日后改核心）**：

```python
@dataclass
class MemoryItem:
    kind: MemKind            # REPO_MAP / CONVENTION / COMMAND_NOTE
    key: str                 # 相对路径 或 主题
    value: str               # 一行说明
    source: str              # "scan" | "agent_observation"
    built_at: float; ttl: float

class RepoMap:
    def build(self, root: Path, max_entries: int = 400) -> list[MemoryItem]: ...
    def render_for_prompt(self) -> str: ...     # 每个文件一行：路径 — 一句话用途
    def invalidate(self, changed_paths: list[str]) -> None: ...
```

**触发时机（明确不做向量检索）**：
- 注入：会话开始时一次性注入系统提示；`write_file`/`edit_file` 之后只更新受影响条目（`invalidate`）。
- **不引入** retrieval：单仓库地图体量在预算内可直接全量注入，加检索只会增加失效模式。
- 不做 episodic（跨会话记失败经验）：写坏一次就会持续误导后续所有会话，且 MVP 无法验证其收益。

## 3.5 Context Manager — `agent/context.py`

**职责**：让"上下文快满了"从事故变成可预期、可报告的状态。MVP **只观测不干预**。

```python
class ContextManager:
    def estimate(self, messages: list[Message]) -> int: ...
    def calibrate(self, actual_prompt_tokens: int) -> None: ...   # 用端点真实 usage 修系数
    def pressure(self) -> float: ...                              # 0.0–1.0
    def compact(self, messages) -> list[Message]: ...             # V1
```

- 起始估算系数 `chars/3.5`，每次响应后按真实 `prompt_tokens` 校准（实测该端点返回真实 usage）。
- `pressure > 0.8` → 发事件警告用户；`> 0.95` → MVP 直接以 `CONTEXT_OVERFLOW` 终止并说明，**不静默丢消息**。
- V1 压缩算法：从最老的工具结果开始，把 `content` 替换为 `[elided N chars from read_file(a.py)]`，**保留 `role=tool` 消息本体**。
  **硬约束**：绝不删除 assistant 的 `tool_calls` 或其配对的 tool 消息 —— 破坏配对会让端点直接 400，这是所有 context 压缩实现最常见的一次性事故。

## 3.6 LLM Adapter — `llm/`（**已完成并实测**）

**职责**：把 `messages.py` 的领域对象与 OpenAI 报文双向翻译；重试；归一化。全项目唯一知道供应商格式的地方。

```python
class LLMClient(Protocol):
    def create(self, *, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse: ...
```

**已实测的端点事实（2026-09-19，`scripts/smoke_llm.py` 4/4 通过）**：
- tools 声明、`finish_reason=tool_calls` 翻译、`role=tool` 回填、真实 usage 返回 → 全部可用；
- **单轮会并行返回多个 tool_calls** → Agent Core 必须按列表处理（§3.1）；
- `arguments` 为字符串且可能非法 JSON → 已在 `_load_args` 转成畸形标记交工具层报错。

**边界**：`LLMError` 只在重试耗尽或 4xx 确定性错误时抛；重试限 429/5xx，指数退避 ≤3 次，尊重 `Retry-After`。4xx（模型名错、报文不合法）不重试 —— 重试它只会白等。

## 3.7 Evaluation — `eval/`（**V2，MVP 只交证据**）

**MVP 交付形态：证据包 `demos/`**，每个 case 一个 markdown，字段固定：

```
task            原始指令文本
repo/commit     目标仓库与基线
expected        判定标准（测试名或人眼标准）
actual          结果 + 终止原因
turns/tokens    来自 trace 的客观数字
tool_calls      次数与其中 is_error 的比例
trace           指向 .traces/ 的相对路径
diff            before/after 的代码差异
```

**V2 设计先行写在这里**，因为 MVP 的 trace schema 必须能支撑它算出这些指标 —— 现在不留字段，以后就要重跑。

| 指标 | 定义 |
|---|---|
`success_rate` | 判定脚本通过的用例数 / 总用例数（唯一硬指标） |
`steps_to_success` | 成功用例的平均轮数（越低越好，且只与同批成功用例均值比） |
`tool_error_rate` | `is_error` 的工具调用数 / 总工具调用数（衡量工具描述质量） |
`redundant_call_rate` | 连续相同（工具+参数）调用占比（衡量是否原地打转） |
`stall_rate` | 以 `STALLED`/`MAX_TURNS` 终止的比例 |
`context_peak` | 单任务最大 prompt tokens（成本主因） |
`wall_time` | p50/p95 |
`fixed_test 判定` | 由 `run_tests` 退出码判定，**不由模型自述判定** —— 让模型自评成功率是评测造假 |

任务集：15–20 个，从真实仓库的历史 fix commit 反向构造（还原 bug + 保留当时的测试），每个带 `verify.sh`。

## 3.8 Logging — `infra/trace.py`

**职责**：让任何一次失败都可离线回放。**这是 Agent 项目最不可压缩的部分**：没有轨迹，§3.7 的所有数字都无从计算，A1 的调试只能靠猜。

事件类型（每行一条 JSONL，含 `ts`/`seq`/`session_id`）：

| kind | 字段 |
|---|---|
`session_start` | model, tools[], system_prompt_hash, config（脱敏） |
`turn_start` | turn, message_count, est_tokens |
`llm_response` | turn, stop_reason, block_kinds[], usage, latency |
`tool_call` | turn, name, args_digest, ok, output_chars, latency |
`permission` | turn, tool, decision, rule_hit |
`todo_update` | turn, items[]（status 快照） |
`error` | turn, layer, kind, message（截断） |
`session_end` | termination, turns, usage_total, cost_estimate |

规则：库代码禁止 `print`，只有 `cli/` 可以写终端；密钥字段必须走 `Config.redacted()`；trace 写盘失败静默降级，**绝不因日志拖垮主流程**；`args_digest` 存哈希 + 前 200 字符，避免整份文件内容进日志。

---

# 4. Agent Execution Flow

一次任务的完整生命周期，含所有分支：

```
 1. CLI 收到 user_input，构造 Agent（若不存在），把输入 append 进 messages
 2. Agent 组装请求：system(环境事实 + 工具规范 + todo 现状) + messages + registry.specs()
 3. ContextManager 估算 → pressure 超 0.95 直接以 CONTEXT_OVERFLOW 终止（不浪费一次调用）
 4. 调 llm.create()。LLMError → 记 error 事件 → TerminationReason.LLM_FAILURE → 报告并退出
 5. 响应作为 assistant 消息 append（保留 TextBlock 与 ToolUseBlock 的原始顺序）
 6. 分支判定：
    5a. stop_reason = END_TURN        → 交付文本，session_end，结束
    5b. stop_reason = MAX_TOKENS      → 不视为完成；追加一次"请继续或改用更小的读取范围"提示（最多 1 次）
    5c. stop_reason = TOOL_USE        → 进入第 7 步
 7. 停滞检测：本轮 calls 与上一轮 (name,args) 全等 → 计数；连续 3 次 → STALLED，终止
 8. 逐个处理 tool_calls（**顺序**，不并发）：
    8a. registry.get(name) 为 None → is_error="未知工具 X，可用：[...]"（不抛异常）
    8b. gate.decide(tool, args)：
        ALLOW → 8c
        DENY  → is_error="用户禁止该操作：<原因>"，继续下一个调用
        ASK   → CLI 交互：一次性 y / 本会话内同类 y / n
                n → is_error="用户拒绝了这次 <tool> 调用"，模型可选择换路径或就此收尾
    8c. tool.invoke(call)：参数校验 → run() → 兜底 except → ToolResult
    8d. 输出二次截断到 tool_output_limit；写 tool_call 事件；发 on_event 给 CLI 渲染
 9. 全部结果打包成**一条** user 消息（role=tool 若干）append；本轮成本与 turns 记账
10. 若本轮含 write_todos：TodoList 应用替换并执行不变式，todo_update 入 trace
11. turn += 1，回到第 3 步
12. 循环退出条件命中 → AgentResult 返回 CLI → CLI 打印答复 + 用量摘要
```

**A1（修 bug）的典型轨迹**，说明第 7–12 步在真实任务里如何咬合：

| 轮 | 模型动作 | 系统行为 |
|---|---|---|
1 | `find_files("**/test_*.py")` + `search_text("def parse_")` | 两个并行调用顺序执行，一并回填 |
2 | `read_file(src/parser.py, offset=40, limit=60)` | READ 自动放行 |
3 | `run_tests("tests/test_parser.py")` | EXECUTE → ASK → 用户"本会话允许"；返回 `2 failed` + traceback |
4 | `edit_file(...)` 第一处修改 | WRITE → ASK；回填"1 replacement" |
5 | `run_tests(...)` | 仍失败，但**失败的用例名变了** → 说明改对了一半（这正是模型能自愈的原因） |
6 | `search_text("_coerce", path_glob="src/**")` 继续定位 | — |
7–8 | 第二次 `edit_file` + `run_tests` | `12 passed, exit 0` |
9 | 纯文本答复："改了 X，因为 Y；建议补一条边界用例" | `stop_reason=END_TURN` → 终止并汇报 |

注意第 5 轮：**结构化的失败信息（哪些用例、什么异常、退出码）是 self-debugging 成立的前提**。如果只回填一坨原始 stdout，模型会在第 5 轮之后开始瞎猜 —— 这是 D2 那条验收线真正的技术难点。

---

# 5. Data Structure

## 5.1 已实现（Stage 1 冻结，不改动）

```python
Role        = USER | ASSISTANT
StopReason  = END_TURN | TOOL_USE | MAX_TOKENS
TextBlock       { text }
ToolUseBlock    { id, name, input: dict }          # ← 即 §5 里所称的 ToolCall
ToolResultBlock { tool_use_id, content, is_error }
Message         { role, content: list[Block] }     # user 与 assistant 都可为 TextBlock|ToolResultBlock 列表
LLMResponse     { blocks, stop_reason, usage, raw }
Usage           { prompt_tokens, completion_tokens, total }
Config          frozen dataclass，唯一环境变量入口
```

命名统一：SPEC 后续提及 **`ToolCall`** 时即 `ToolUseBlock`；实施时在 `messages.py` 加一行别名 `ToolCall = ToolUseBlock`，不改既有代码。

**关键约定**：一批工具结果作为**一条 user 消息**回填（`Message.tool_results([...])`），而不是每条结果一个 user 消息 —— 与 OpenAI 报文的对应关系由适配器展开，语义层保持"用户一次性提供了这些结果"。

## 5.2 待新增

```python
# agent/state.py
class TerminationReason(StrEnum): COMPLETED MAX_TURNS STALLED USER_REJECTED LLM_FAILURE CONTEXT_OVERFLOW CANCELLED

@dataclass
class AgentState:
    turn: int
    messages: list[Message]
    todos: TodoList
    usage: Usage                 # 累计
    cost_tokens_peak: int
    denied_actions: int          # 用户拒绝次数，A4 与评测都要用
    status: Literal["running", "finished", "aborted"]

@dataclass
class AgentResult:
    text: str
    termination: TerminationReason
    state: AgentState
    trace_path: Path | None

# agent/planner.py
@dataclass TodoItem: content: str; status: TodoStatus = PENDING

# memory/repo_map.py（V1）
@dataclass MemoryItem: kind; key; value; source; built_at; ttl

# eval/runner.py（V2）
@dataclass Task:
    id: str                       # "t07-parse-int-carry"
    repo_url: str; base_commit: str
    instruction: str              # 给模型的一句话
    setup_cmd: str                # 装依赖、还原 bug
    verify_cmd: str               # 唯一真相：退出码 0 即成功
    tags: list[str]               # ["bugfix","cross-file"]，分维度看成功率
    max_turns: int = 30
```

`Task` 与 `AgentResult` 是 MVP 与 V2 之间的接口契约：**MVP 不实现 `eval/`，但 `AgentResult` 必须现在就长这样**，否则 V2 要回头改核心循环。

---

# 6. Engineering Requirements

## 6.1 代码规范

- Python **3.11+**，全函数带类型注解；公共接口禁止裸 `Any`（`raw` 字段例外并注明）。
- 数据结构一律 `@dataclass`（frozen 优先），**不引入 pydantic**：本项目不需要运行时 schema 推导，多一个重依赖就多一个学习障碍。
- `StrEnum` 而非魔法字符串；`TerminationReason`/`RiskLevel`/`TodoStatus` 全部如此。
- 依赖方向严格遵守 §2 的单向约束；`tools/` import `agent/` 即视为设计缺陷，需重构而非绕过。
- `ruff` 默认规则 + 单文件 ≤400 行软上限；`loop.py` 目标 ≤150 行，超出说明控制策略在往核心里塞。
- 注释只写"为什么"，尤其那些反直觉的约束（如"绝不删除 assistant 的 tool_calls"）—— 这类注释是本项目最有价值的部分之一。

## 6.2 测试要求

| 层 | 方式 | 必须覆盖 |
|---|---|---|
工具 | `pytest` + `tmp_path` | 每个工具 ≥3 例，**必含错误路径**（不存在的路径、`../` 逃逸、匹配 0/多处、超时） |
工具契约 | 1 个通用测试 | 任意坏参数下 `invoke()` 永不外抛，返回 `is_error` |
适配器 | `httpx.MockTransport` | 报文映射、非法 JSON arguments、`finish_reason` 缺失兜底、429 重试 |
核心循环 | `tests/fakes.py` 的 **FakeLLM** | 单工具往返、并行多调用、未知工具名、`STALLED`、`MAX_TURNS`、todo 不变式 |
端到端 | 打真实端点，`-m live` 显式标记 | 1 个最小真实任务；默认跳过，**禁止进 CI** |

铁律：**确定性测试永不打网络**。真实模型行为靠 `scripts/smoke_llm.py` 与手工 demo 验证，靠 FakeLLM 回归保护。这条决定了你能不能在 4h/天 的节奏里稳定迭代。

## 6.3 异常处理（三层边界，不可混淆）

1. **工具层**：`invoke()` 捕获一切 → `ToolResult.err()`。工具报错是**模型的输入**，不是程序故障。
2. **LLM 层**：429/5xx 退避重试 ≤3；4xx 立即抛 `LLMError`（重试无意义）。
3. **CLI 层**：捕获任何未预期异常 → 打印可读错误 + 指出 trace 路径 + **保留已写轨迹**。用户最不该遇到的事是"崩了而且什么都没留下"。

禁止：裸 `except:`；把 traceback 塞进给模型的 tool_result（会污染上下文，只给 `type: message` 一行）；用异常做流程控制（`TerminationReason` 走正常返回值）。

## 6.4 配置与日志

- 只有 `config.py` 读环境变量；其余模块接收 `Config`。测试用 `Config.with_root(tmp_path)` 派生。
- 密钥只存在于 `.env`（已 gitignore）；任何打印/落盘走 `Config.redacted()`。
- 终端可读性（rich，V1 可降级为纯文本）与机器可读性（JSONL）分开，互不替代。
- 时间预算紧时的降级顺位见 §7.4 —— **trace 与 FakeLLM 测试在顺位之外，永不砍**。

---

# 7. Development Roadmap

计划 47h + 缓冲 9h = 56h。Stage 1 已完成（8h 已花掉，计入缓冲池）。

## 7.1 MVP（56h 预算内，交付 D1 定位与 A1–A4 验收）

| Stage | 内容 | 工时 | 退出标准 |
|---|---|---:|---|
| **2** | `tools/base`(invoke/validate) + `registry` + `read_file`/`find_files`/`search_text`/`write_file`/`edit_file`/`bash` | 14h | 问"仓库里哪个文件定义了 X 函数"→ 模型自己 search 并答对 |
| **3** | `permissions.py`（路径锁 + 会话授权 + 破坏性检测） | 3h | `../` 被拒；`bash rm -rf` 强制单独确认 |
| **4** | `agent/loop.py` + `fakes.FakeLLM` + 循环测试 + `run_tests` 工具 | 10h | FakeLLM 6 条测试全绿；`STALLED` 能触发 |
| **5** | `prompts.py` 调优 + `write_todos`/`TodoList` + `context.py` 估算校准 | 6h | 真端点跑完 §4 的九轮轨迹，todo 状态与 trace 一致 |
| **6** | `cli/main.py` + `render.py` + `infra/trace.py` | 6h | 终端 REPL 可用，`/tools /context /reset` 生效，轨迹能回放 |
| **7** | **A1 硬化**：在真实陌生 Python 仓库修 ≥1 个 bug 到转绿，反复迭代工具与提示 | 8h | A1 连续 2 次通过（不同 bug） |
| 缓冲 | 模型行为不可预测带来的返工 | 9h | — |

**Stage 7 是整个计划唯一真正的风险敞口。** 前六个 Stage 都在写自己控制的代码，Stage 7 要面对真实仓库和真实模型 —— 它也是这个项目唯一值得花时间的地方。若超时，按 §7.4 砍单，**不要压缩它**。

## 7.2 V1（+25h，两周之后）

1. 仓库地图 `RepoMap`（D7 的延后项，7h）—— 预期直接降低 A1 的轮数
2. `context.compact()` 真压缩（5h，含配对不变式测试）
3. WSL2/容器 `ExecutionBackend` 抽象（6h）—— 为 V2 无人值守铺路
4. 第二个真实仓库验证 + demo 补到 5 个（4h）
5. 显式 `PlanStep` 重规划（3h，若 A1 已稳定）

## 7.3 V2（+30h）

`eval/` 层与 15–20 任务集 → §3.7 全部指标 → 报表与 README 数字 → 弱/强模型对照实验（"弱模型 + 好架构 vs 强模型 + 糙架构"是本项目最有辨识度的潜在叙事）。

## 7.4 超时砍单顺位（现在就定，避免临场决策）

1. demo 从 3 个减到 2 个
2. `run_tests` 降级为 `bash` + 提示词强制 `--tb=short`（风险：模型误判测试结果 —— 这条要在 README 里诚实写明）
3. `context.py` 只保留估算与警告，删掉 calibrate
4. CLI 斜杠命令只留 `/reset`
5. 工具集 8 → 6（去掉 `find_files`，用 `search_text` 的 glob 代替）

**永不砍**：`trace`、FakeLLM 循环测试、权限路径锁、`STALLED` 检测。砍掉这四样中的任何一个，项目立刻从"agent 工程"退化为"API demo"。

---

# 8. Future Extensions

每项都写明**接入点**，因为可扩展性就是这些接口现在长什么样的回报；也写明我对它是否值得做的判断，避免"能做"被误读成"该做"。

| 方向 | 接入点 | 我的判断 |
|---|---|---|
| **Multi-agent**（planner/worker/critic 分工） | `Agent` 已是可组合单元，子 Agent = 换一套 `system_prompt + registry` 的新 `Agent`，由父 Agent 以 `spawn_agent` 工具调用；轨迹需要 `parent_session_id` 字段 | **谨慎**。多数 coding 任务被上下文隔离拖慢而非加速。真正值得的场景是"一个写代码、一个专门跑测试并只回传失败摘要"。放 Advanced |
| **RAG 代码检索**（向量库语义找代码） | 把 `search_text` 换/并成一个 `RetrievalTool`，接口不变 | **大概率不值得**。`search_text` + 仓库地图对 <10 万行仓库足够，向量检索在代码上召回差、还引入 embedding 供应商依赖。仅当 V2 要处理超大仓库时才做 |
| **MCP 工具生态** | `BaseTool`/`ToolSpec` 已是 MCP 需要的三元组，加 `mcp/bridge.py` 把远端工具包成本地 `BaseTool` 注册进 registry | **值得，成本低**。它能证明你懂工具协议与生态互操作（JD 第 4、6 项）。V2 之后做 |
| **Browser Agent** | 新增 `tools/browser/`（navigate/click/screenshot），`ExecutionBackend` 扩出浏览器侧；VLM 承担 DOM 理解 | 值得作为**第二个 demo 场景**，能复用全部循环与权限设计，展示架构通用性。V2+ |
| **Embodied Agent** | 最自然的延伸：机器人动作/传感器读取实现成 `BaseTool`，`Task.verify_cmd` 换成仿真环境成功率，trace 变成 episode 数据；`run_tests` 的"结构化失败回传"模式直接对应"动作失败反馈" | 与工作区方向（Embodied Agent）同源。**先别做**，但 SPEC 里这条约束要守住：工具层不得假设"工具=文件/命令操作"。等 MVP 真跑通再评估 |
| **RL / self-improvement**（JD 第 15 项） | V2 的 trace JSONL 就是天然的偏好/结果数据源；轨迹→`(state, action, reward)` 的导出器 | 只在有真实轨迹数据之后讨论。现在写任何训练计划都是空的 |

---

## 附：确认前需要你回答的 3 件事

1. **A1 的目标仓库与 bug 从哪来** —— 这是唯一的**外部前置条件**，且直接决定 demo 的说服力。需要：一个真实 Python 开源仓库 + 一个已知失败测试（或一个能稳定复现的 bug 提交）。你自己有候选吗？没有的话我按"小而测试完备"的标准给 3 个候选。
2. **工具数 8 个**，比我前面说的"6 个"多两个（`run_tests`、`write_todos`）。`run_tests` 是 A1 的判定燃料、`write_todos` 是 D3 的载体 —— 但预算敏感的话，`run_tests` 可按 §7.4-2 降级。确认保留还是现在就砍？
3. **实施节奏**：SPEC 确认后，按 Stage 逐个实现并等你验收，还是我一次把 Stage 2–6 做完再交给你跑 A1？（我倾向**按 Stage 交付**：每步都能独立回滚，且模型行为问题发现得早。）
