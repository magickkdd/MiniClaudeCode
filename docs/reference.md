# 参考手册：配置项、斜杠命令、失败模式标签

> 从 README 拆出。README 只留"最常拧的几个"与结论句，这里是完整表。原文逐字搬运，未改写。

## 1. 配置项

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
| `MCP_TOOL_TIMEOUT` | 600 | 只管 `tools/call`；握手与发现仍走 30s —— 起不来的服务要在会话开始时秒报错（§4.16）|
| `SKILLS_DIR` | `skills` | 技能根目录。目录不存在就等于没有技能（不报错、不装配 `load_skill`）|

## 2. 斜杠命令

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

## 3. 权限模式

| 模式 | 只读工具 | 写/执行 | 破坏性命令 |
|---|---|---|---|
| `ask`（默认） | 放行 | 逐次问你（`y` 一次 / `a` 本会话同类都放行 / `n` 拒绝） | 无条件拒绝 |
| `auto`（`-y`） | 放行 | 工作区内自动放行 | 无条件拒绝 |
| `readonly` | 放行 | 拒绝 | 拒绝 |

两个不可让的细节：

- **路径锁**在 `Workspace.resolve` 这一层，不在门后面。越界路径根本解析不出可写目标，`..` 逃逸和绝对路径外部写入一并挡住。
- **确认失败关闭**：没有确认渠道（管道输入、demo 里那个恒定返回"拒绝"的 confirmer）时，任何看不懂的答复都按拒绝处理，绝不默认放行。

## 4. 失败模式分类学

规则式而非模型式：8 条标签，每条都要能在一条真实 trace 上被人眼复核。判据宁可漏报不误报。

| 标签 | 一句话判据 | 处方 |
|---|---|---|
| `path_guessing` | 整轮里 ≥3 个**不同路径**的 `read_file` 失败（被拒的读不算猜） | 仓库地图缺失（→ S11） |
| `context_growth` | 单轮 token 涨幅 > 前 5 轮中位数的 3 倍、≥2000，且上一轮工具输出够解释一半 | 工具输出未截断 |
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
[`demos/results/failure-labels.md`](../demos/results/failure-labels.md) 机器核对：16 条轨迹逐条对
"人写的期望"，不一致（**包括压根没写过期望的**）就退出码 1。它同时打印盲区 —— 字段缺失时规则
不是判对了，是没参与。旧 trace 里落盘的 `failure_modes` 与当前规则算出的不同时，`mcc trace`
显式印出"规则口径变过"，而不是沉默地换一套答案。
