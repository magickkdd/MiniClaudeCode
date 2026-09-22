# 框架等价性对照：mini-claude-code 的 registry + loop vs LangGraph StateGraph

SPEC v2 · D26 的交付物，同时是 JD 第 6 项（Agent Framework 使用）的回答。
结论一句话：**我们没有用 LangGraph，但我们实现的那几件事和 StateGraph 抽象的是同一批问题；
下面逐格说清楚哪几格是等价的、哪几格我们真的缺、哪几格是它的卖点对我们是负资产。**

对 LangGraph 的描述取自其公开 API（0.2 / 0.3 一线：`StateGraph`、checkpointers、`ToolNode`、
`interrupt`、`Send`、`get_state_history`）。本文对照的是**设计选择**，不是版本号；具体签名以官方文档为准。

## 1. 概念对齐

| LangGraph 里的东西 | 我们这里的对应物 | 等价到什么程度 |
| --- | --- | --- |
| `StateGraph` + 节点函数 + 边 | `Agent.run()` 里那个 while 循环：一次模型调用是一个节点，一批工具调用是下一个节点 | 形状等价，表达方式不同：它是**声明图**，我们是**固定的循环**。我们的图只有一种拓扑（模型→工具→模型），所以不需要图 DSL |
| state + channel reducer（并发更新怎么合并） | `AgentState`（turn / tool_calls / denied_actions / tokens…）+ `history: list[Message]` | 我们**故意不做并发合并**：一条 assistant 消息里的多个 `tool_use` 按序执行、按序回填。§3.5 的数据闸量出可并行轮占比不足 20%，于是那套 reducer 被砍掉了 |
| `ToolNode` + `bind_tools` | `ToolRegistry.default(workspace, bash_timeout, extra_tools, backend)` + `BaseTool.invoke()` | 等价，而且我们多一层：`invoke()` 里 schema 校验 → `_coerce` 只放行 schema 点名的参数 → 输出过 `Workspace.truncate`。工具**永不抛异常**是这条链的产物，不是约定 |
| `interrupt()` / human-in-the-loop | `PermissionGate`（ask / auto / readonly）+ `confirmer` 回调 | 语义不同，值得注意：LangGraph 的 interrupt 是**把控制权交回调用方并停住图**，靠 checkpointer 存住现场等恢复；我们的门是**同步一问一答**，没人可问就保守拒绝。前者能撑几分钟到几天的人工审批，后者不能 |
| checkpointer（`thread_id` + Sqlite/Postgres saver） | 两套不同的东西，我们拆开了：① `SessionLog` + `SessionSnapshot`（对话现场，`.mcc/sessions`）；② shadow git 检查点（**工作区**的字节状态，`/undo`） | LangGraph 只管①。②是它的空白：它能让你回到某一步的**消息**，回不到那一步的**文件**。我们的 `/undo` 撤的是磁盘 |
| `get_state_history` / 时间旅行 / fork | `mcc resume`（从最近现场继续）+ `agent.undo_last_write()`（回退一次写入） | 我们只有"线性回退"，没有"任选历史一步分叉"。这条差距见 §3 |
| superstep / Pregel 批处理 | while 循环的"一轮" | 概念重合但不等价：它的 superstep 是并行执行的单位，我们的一轮是串行的 |
| `Send` API（map-reduce 式动态 fan-out） | 无。S15 的 `spawn_agent` 是**一个** verifier 子 agent，不是动态 fan-out | 真缺 |
| LangSmith / 可观测性 | `trace.jsonl` v2 + schema 契约 + `mcc trace` / `mcc eval` | 这里是我们的强项：20 种事件、每种都有**真实产地**的 fixture 钉住字段，schema 一变测试就红。LangSmith 是产品，我们是一套自己的契约 |
| `langchain-mcp-adapters` | `ext/mcp.py`：`MCPBridge` + `RemoteTool` | 都做"把远端工具接进本地注册表"。差别在信任：适配器基本按远端声明转写，我们**一律按 EXECUTE 采信**（D19），远端自报只进展示字段 |

## 2. 为什么不用 LangGraph（按代价说）

1. **抽象层会吃掉可归因性。** 这套代码的全部目的是量出"上下文涨了多少、哪一轮问了人、
   哪次压缩省了多少 token"。框架替你把消息合并进 state 之后再想拿回"这个数是我算的还是它算的"，
   就要往它的内部结构里钻 —— 到那一步，读它的源码比写自己的循环更贵。
2. **checkpoint 格式不由我们定。** §3.6 的检查点要和 shadow git 的 rev、trace 的 `checkpoint`
   事件、`done_call_ids` 的幂等账本三样东西严格对齐。用它的 saver，这三者的对齐就得靠"猜它的序列化时机"。
3. **消息配对不变式在框架里没人管。** 我们的 `assert_pairing` 会在任何一次发给模型的上下文上跑：
   每个 `tool_use` 必须有配对的 `tool_result`，压缩之后也不许漏。这件事在框架里如果出错，
   表现为端点 400，而在我们这里表现为一条带上下文的断言失败。
4. **依赖预算。** v1 §6.1 禁 pydantic / LangChain / openai SDK，v2 只放开了一个 `mcp`，
   而且只放 dev extra。一个 coding agent 的 CLI 若装不上，最可能的原因是它的依赖树。

这四条的**反面**也成立：如果目标是两周内交付一个能演示的多分支工作流，上面每一条都是奢侈品，
该用 LangGraph。判断依据不是"哪个更好"，而是"这次要的是理解还是交付速度"。

## 3. 我们真的缺的东西（不粉饰）

| 缺什么 | 代价是什么 | 要补的话最小做什么 |
| --- | --- | --- |
| 动态 fan-out / 并行分支（`Send`） | 大规模并行探索（同一 bug 试 5 个补丁再投票）跑不出来 | §3.5 那条并发闸重开：先让批次调度器支持一轮内多分支，再谈 reducer |
| 历史任意点分叉 | 能做"撤销上一次写入"，做不了"从第 7 步重新走一遍另一条路" | `SessionSnapshot` 已经带 `session_id` 和消息历史，缺的是按步索引 + 一份不共享的 workspace 快照 |
| 声明式条件边 | 拓扑写死在循环里，加一支"失败就转 verifier 再回主循环"要改循环本体 | S15 之后如果子 agent 用得多，把 `_dispatch` 抽成可注册的边表 |
| 流式输出的中断/续写 | 只有整块返回 | `LLMClient` 加 SSE，同时配对不变式要在流式路径上重跑一遍 |

## 4. 它的卖点对我们是负资产的东西

- **图 DSL 的表达力**：我们只有一个拓扑，买了表达力就要维护它的校验器。
- **多后端 checkpointer**：Postgres/SQLite saver 的价值在生产多副本部署；这是一个单人 CLI。
- **预置 agent 模板**（`create_react_agent`）：模板省的那 200 行，正是这个项目要写的 200 行。

## 5. 要在两者之间迁移，接口在哪

| 迁移点 | 我们的边界 | 它的接口 | 硬冲突 |
| --- | --- | --- | --- |
| 工具 | `BaseTool`（name / input_schema / risk_level / run） | `StructuredTool` | 无：字段可一一直译 |
| 权限 | `PermissionGate.authorize()` 同步返回 bool | `interrupt()` + 恢复执行 | **有**：我们的门在 AUTO 下会"问到空气就拒绝"，它没有这个默认；照搬会把安全边界丢掉 |
| 现场 | `SessionLog.save/load` + `done_call_ids` | checkpointer + `thread_id` | **有**：幂等账本在我们这侧，恢复时不重放已完成的调用；它的语义是"从 state 继续"，得确认它不会把 tool_use 再发一遍 |
| 循环 | `Agent.run()` | `graph.invoke()` | 无 |
| 度量 | `trace.jsonl` 20 种 kind | LangSmith runs | **有**：schema 契约要求每种 kind 都有真实产地。换过去等于把"这个数谁写的"这个问题重新问一遍 |

**等价性怎么验，而不是怎么说**：`mcc eval` 那 24 道题不读引擎内部，只读终止态和判据。
同一个剧本喂给两边，判定结论应当相同 —— 不同的话，差异一定出在上表三个「有」里。
这就是 B6（双后端一致性）用的同一套方法，只是拿它来对照框架。

## 6. 三分钟口径（面试真会问的那种）

- "为什么不用 LangGraph？" → 我要的是能归因的度量。框架替我合并 state 之后，"上下文涨了 4k"
  这个数就不在我的账上了。我把它的四件事各自实现了：工具节点（registry）、人在环（gate）、
  现场（SessionLog）、可观测（trace 契约），并且能逐格说清哪件我没做。
- "那它的什么设计你觉得值得抄？" → superstep 的并行批处理，和我的 §3.5 完全对得上，
  我用真实 trace 量了可并行轮占比，不足 20% 就砍掉，够高就该重开。这个判据本身就是从它的抽象里学来的。
- "checkpoint 呢？" → 它只管消息，不管磁盘。我的 agent 会改文件，所以我的检查点必须是
  shadow git 的 rev，加上和 `done_call_ids` 的对齐，否则恢复现场时会重放一次写入。这是 LangGraph 帮不上的一层。
