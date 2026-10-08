# mcp-link 演示脚本 · MCC ⇄ Insight Agent 的 MCP 真实互通

对应 `docs/mcp-link-spec.md` §5。所有输出为 2026-10-01 实跑捕获，非手写；
trace 与现场文件都在仓库里，可复核。运行前置：两端 `.env` 各自可用
（MCC 的 LLM key；insight-agent 的 LLM + Tavily key），MCC `.env` 里配好
`MCP_SERVERS`（见 `.env.example` 的 insight-agent 条目）。

## 1. 开场（两句话）

- mini-claude-code：一个可验证的 Coding Agent harness——权限门、上下文压缩阶梯、
  双后端对照，所有结论有 trace 与回归测试背书。
- Insight Agent：一个可验证的 Multi-Agent 研究 pipeline——规划→联网取证→验证→
  结构化报告，幻觉率与覆盖率进报告本体。

## 2. `/tools`：本地 8 工具 + 命名空间化的 4 个远端工具同框

```
$ printf '/tools\n/exit\n' | mcc
工具 12 个：bash edit_file find_files mcp__insight-agent__get_notes
            mcp__insight-agent__list_archives mcp__insight-agent__research
            mcp__insight-agent__research_async read_file run_tests search_text
            write_file write_todos
  …（本地 8 个略）…
  mcp__insight-agent__list_archives execute  [MCP/insight-agent] 列出全部已研究主题及其档案信息。
  mcp__insight-agent__get_notes      execute  [MCP/insight-agent] 获取某主题的研究档案（覆盖度目录 + 最近报告）。
  mcp__insight-agent__research       execute  [MCP/insight-agent] 对指定主题执行完整研究，返回结构化 Markdown 报告…
  mcp__insight-agent__research_async execute  [MCP/insight-agent] 异步版 research：在线程池里跑同步图，不阻…
```

`mcc mcp`（握手面板，配置写错时这里是第一现场）：

```
配置了 1 个服务：insight-agent
握上手 1 个 · 外部工具 4 个 · 弃用 0 个
  insight-agent（stdio） → 可用
    mcp__insight-agent__research       远端自报 未声明      采信 execute（每次都要确认，AUTO 也不例外）
    …
```

## 3. 任务演示：research 写 notes.md（T2 全链路）

```
$ printf 'y\ny\n…' | mcc -y -t "先调用 mcp__insight-agent__research（depth=fast）做调研，
  然后把结论写进 notes.md，保留引用 URL。"

  需要授权 · mcp__insight-agent__research：…research(topic=httpx 超时配置最佳实践, depth=fast)
  [y] 只做这一次   [a] 本会话内同类都允许   [n] 拒绝
  你的选择 > ✓ mcp__insight-agent__research topic=httpx 超时配置最佳实践 depth=fast  126.6s
✓ write_file  notes.md  1ms
已完成。将研究报告的完整内容写入了 notes.md，所有引用 URL 均保留（共 6 个来源）。
用量：3 轮 · 输入 18,161 + 输出 2,675 = 20,836 tokens
```

演示点：

- **external 确认**：AUTO 模式下本地 write_file 没问，外部 research 问了——§6.3-1 的
  对照组同框；拒绝臂的语义见 `tests/test_mcp_bridge.py`。
- **判据读文件**：`demos/.work/mcp-link/notes.md` 含 9 个引用 URL
  （含 `https://www.python-httpx.org/advanced/timeouts`），模型自述只是对照。
- **记忆归对端管**：研究档案落在 insight-agent 项目根
  `data/notes/httpx超时配置最佳实践.json`，MCC 工作区只有报告文本。
- **trace 全链路**（`demos/traces/mcp-link-t2.jsonl`）：`mcp`（握手+发现）→
  `permission`（research allow）→ `tool_call`（126.6s）→ `permission`（write_file allow）→
  `tool_call`（notes.md）→ `run_end`。

## 4. `get_notes` 第二轮（秒级，不冷场）

```
$ printf 'y\n/exit\n' | mcc -t "用 mcp__insight-agent__get_notes 查询主题 httpx 超时配置最佳实践"
```

秒级返回该主题档案（覆盖度目录 + 最近报告），演示"长任务研究一次、秒级工具反复读"的用法。

## 5. 收尾：边界与故障形态（as-built 数字）

- 长任务超时分层：握手/发现 30s（起不来秒报错），tools/call 走 `MCP_TOOL_TIMEOUT`
  （缺省 600s）；research fast 直跑 89.3s、过桥 126.6s；standard 直跑 90.9s、
  过桥 ≈300s 完成（第一次调用 64.9s 撞残余云端 429，模型原参数重发后成功）——
  **远端故障以 is_error 的形状出现在工具结果里，模型据此自愈**，这就是 T3 的现场。
- 替身回归钉住的三条路径（`tests/test_mcp_bridge_link.py`）：namespace 注册 /
  external 确认 / 超预算调用在预算处失败且立刻可恢复。
- 真实故障（接线当天撞上的）：spec 原配置下 research 死于「子进程 cwd + 密钥不透传」，
  server 端 RuntimeError 以 `isError` 回给模型自愈——远端故障的形状就是工具结果里的
  is_error，这正是 §6.3-1 那套权限与兜底设计要接住的东西。

## 存档清单

| 文件 | 内容 |
|---|---|
| `demos/traces/mcp-link-t2.jsonl` | T2 全链路 trace（mcp / permission / tool_call 事件） |
| `demos/traces/mcp-link-t2.console.log` | T2 控制台原始输出 |
| `demos/traces/mcp-link-t3-standard.jsonl` | T3：额度烧穿期 5 次失败 + 额度恢复后失败-重发-成功的完整历史 |
| `demos/traces/mcp-link-t3.console.log` | T3 控制台原始输出（最后一次被杀的等待尝试） |
| `demos/.work/mcp-link/notes.md` | T2 判据现场（工作区原件；.work 不入库） |
| `demos/results/mcp-link-t2-notes.md` | 同上一份的入库归档（判据：含报告引用 URL） |
| `demos/results/mcp-link-t3-standard-notes.md` | T3 判据现场的入库归档（5 个引用 URL） |
| insight-agent `data/notes/httpx超时配置最佳实践.json` | 研究档案落点（chdir 旁证；T2） |
| insight-agent `data/notes/httpx重试与连接池配置.json` | 研究档案落点（T3） |
