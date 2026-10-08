# 测试：怎么跑、每个文件覆盖什么

> 从 README §8 拆出。README 只留命令清单与"用哪个解释器"那条警告；逐文件的覆盖明细在这里。原文逐字搬运。

## 8. 测试

```bash
python -m pytest -q                # 849 passed
python -m pytest tests/test_loop_with_fake_llm.py -q
python -m pytest tests/test_eval_runner.py tests/test_eval_cli.py -q   # 评测层（不联网）
python scripts/b4_label_check.py   # 失败模式标签的人工核对，退出码 0 才算过
python scripts/b2_compact_ab.py    # B2 三臂 A/B + 判据账本（fake 侧 12 条，加 --live 再长 3 条），退出码 0 才算过（--live 补真端点那半条、--probe 跑 §3.3.4 的高预算反向对照，只有这两个花额度）
python scripts/b3_repomap_ab.py    # B3 两臂 A/B：机制判据离线核，因果两条判据要 --live
python scripts/probe_parallel_share.py  # S12 的数据闸：盘上轨迹里可并行的轮占多少、值多少毫秒（§4.9）
python scripts/b6_backend_ab.py    # B6 两臂 A/B：docker 臂降级时不产出一致率、直接退 1（§4.10）
python scripts/s14_ext_demo.py     # §3.7 的 20 条前提：对着两个 MCP 对手量桥与技能（§4.11）
python scripts/probe_verifier_gate.py  # S15-a 的数据闸：D21 那道 20% 在盘上支持吗（§4.12）
mcc export-rl --batch <目录> --out <文件>  # 跑过的轨迹 → RL 数据 + 12 条自证前提（§4.13，不联网）
python scripts/t3_otlp_export.py  # §7.3-3：24 份已入库轨迹 → OTLP/JSON + 18 条判据（0 个模型请求，§4.14）
mcc eval --repeats 3               # 24 题 fake 全批，见 §4.6
```

**用哪个解释器跑不是小事**：得用项目的 `.venv`。全局解释器少装了 dev extra 里的官方 `mcp`，`tests/test_mcp_bridge.py` 那条"对端是 SDK 写的服务"就会**静默跳过**，报出来的是 `848 passed, 1 skipped` 而不是 `849 passed` —— 仍然全绿，绿的格数却少一格。同一类能力依赖在证据脚本那边更要紧：拿那个全局解释器跑 `scripts/s14_ext_demo.py`，SDK 那一臂直接没了，而它过去会照旧覆盖掉签着 20/20 的入库证据。**现在这条路被拦住了**（退出码 2、`measured：20 → 17`、文件一个字节不动），五个写证据的脚本共用同一道闸，见 §10 表第 30 行。

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
| `test_resume.py`（15） | 幂等重放：`done_call_ids` 里的调用**绝不重跑**、重放回填一句说明而不是输出、重放记 `session_replay` 而不记 `tool_call`、中间断裂的现场拒绝恢复并留在盘上、计数与状态跨进程续算、不重复拍基线、别的项目的现场被守卫挡下、**本进程自己记的账不算重放守卫**（没恢复过现场时写入必须真发生、一条 `session_replay` 都不许有）、缺现场时提示去哪儿找 |
| `test_memory_dir.py`（15） | `MEMORY_DIR` 改名成 `.brain` 后**七个消费者一起跟上**：现场/快照/记忆文件落在新名里、旧名一个都不建、检索与 `find_files` 看不见它、`tracked_files` 两种口径对称、env 覆盖生效、`../outside` 这类敌对值启动期拒绝、默认名只有一个产地 |
| `test_tools.py`（43） | 每个工具的正常路径与失败形态：越界路径、目录当文件读、非法正则、无匹配、`old_string` 不唯一/不匹配、bash 超时、**非零退出算观测不算工具报错**、参数校验、多余参数丢弃、注册表去重 |
| `test_failure_rules.py`（44） | 8 条失败模式规则各自的命中与**不命中**：真实形状逐条钉住（含"context_growth 在旧 schema 上彻底失明"这条已知盲区），并检查 `scripts/b4_label_check.py` 的 EXPECT 覆盖到盘上每一条 trace |
| `test_cli.py`（30） | `build_session` 装配、密钥不进 trace 与提示词、REPL 分发与 EOF/Ctrl-C、渲染逐行语义（一行一次调用、标签取识别参数、被拒才打印）、一次性任务的退出码映射、确认器答复翻译 |
| `test_eval_metrics.py`（22） | 报表 25 个键逐个重算（`steps_to_success_median`、`wasted_output_ratio`、p95 都手算对一遍）、键名集合快照与 SPEC §3.2 同步、Wilson 区间手算值、`per_tag` 里结构性失败不许被抹平、空批 |
| `test_eval_regression.py`（21） | 基线对比四条：考卷变了**只拒绝对比不给 Δ**、逐题翻红才算 blocker、McNemar 精确二项的手算值、`must-fail` 判绿出"判据告警"、"没做对比 ≠ 没有差异" |
| `test_eval_judge.py`（19） | 十步判据逐条独立验：作弊判据排第一、白名单内外口径分开、`probe` 与 `verify_cmd` 只认退出码（打印 SUCCESS 不算过）、只读题碰盘即 fail、8 种终止原因参数化全覆盖 |
| `test_eval_cli.py`（25） | `mcc eval` 退出码三档各有真走到的用例、fake 引擎不碰 `.env`、基线按哈希自动匹配、哈希不符时拒绝开跑与 `--force` 照跑仍标"无法对比"、`must-fail` 与 `negative` 在终端上的分工、**live 批次里 `--only` 不许被 `supports_live` 默认覆盖**（点名点到负样本时终端先警告再花钱） |
| `test_eval_taskset.py`（17） | 题集契约：未知字段/无判据/驱动名不存在一律拒绝加载、**题面不许泄漏答案**（gold 与 probe 都不许出现在 instruction 里）、只读题必须有答复关键字、`edit/write` 剧本必须真跑测试、`lint_task` 认送分题 |
| `test_eval_runner.py`（22） | gold patch 真把用例翻绿（错 patch 判红）、篡改考卷被 `protected` 抓、工作副本隔离且基线树跑一百次不动、manifest 逐条落盘 / 续跑不重跑 / 旧哈希记录作废并提示、**重跑同一 `(task, repeat)` 时旧行被取代而不是叠成两条**、**每行带着它所判那份轨迹的内容指纹**（按 `rel` 回读重算必须逐位相等）、**批次目录整体搬走之后续跑仍认得现场**、坏行留着当证据、批次预算到点即停、装配失败只崩这一题不崩整批 |
| `test_openai_compat.py`（19） | 报文形状（tools 声明、tool_result 配对顺序与 name）、arguments 字符串解析、可重试状态码与传输错误、4xx 不重试、usage 归一化、`LLMClient` 协议一致 |
| `test_export_rl.py`（35） | §3.9 两条产地各自钉死：**配对靠的是逐轮 `tool_use` id 序列而不是条数**（改一个 id 就整份退回指纹）、**快照只在 trace 上一级目录里找**（放到更远处的夹具必须配不上，实测过会爬进系统临时目录）、**一个决策步至少留下 `llm_request` 或 `llm_response` 之一**（否则带下一轮轮号的止损记录会造出幽灵步，把真死在预算线上的 run 整条判成"轮数对不上"丢掉）、增量导出不重不漏的那条算式、`error`/`aborted` 不进数据、失败标签读 as-run 不重算、超限块留标记、**"没有密钥"这条前提不是空转的**（换成正则遮不住的夹具时它必须变红）、同一份 trace 不能被认领两次、轮数对不上则判据作废；§7.3-7 的同一性：指纹对得上才 join、**中间行被改过也算 drift**（行数与末行都不动）、没有指纹的行照旧导出但自述未校验、**导出之后再改盘上那份轨迹那条前提会红**、批次目录搬走后仍按相对路径 join 得上 |
| `test_demos.py`（19） | 5 个 demo 离线跑通、工具确被执行、证据含 SPEC §3.7 字段且无密钥、fixtures 跑完仍纯净、两个 bug 的前提未被顺手修掉、A1–A4 全覆盖、**跑测试绝不改写仓库里已入库的那份证据**（临时目录里只生成一份，watch 到的 committed 文件逐字节不动） |
| `test_b2_evidence_guard.py`（3） | 证据文件自己不许悄悄变薄：已签着 live 臂时不带 `--live` 的重跑**拒绝覆盖**（退出码 2、字节不动），没有 live 臂时 fake 侧照常可重跑，以及**仓库里那份证据确实带着 2 条签好的 live 判据 + 1 条未量**（README/SPEC 那几段话的生产者） |
| `test_evidence_guard.py`（21） | 五个证据脚本共用的那支笔（`scripts/_evidence.py`）：红得了（少 3 条判据拒写且文件字节不动、条数不少但 3 条变成未量也拒）、**不挡真失败**（判据翻红照写）、绿得起（等量 / 更厚 / 第一次签字 / 新加的尺上次没这根，全放行）、绕过必留痕（`--allow-thinning` 把缩水条目打到 stderr）、写字节不留 `\r\n`；外加两道闸 —— 任何脚本自己 `RESULT.write_text(` 就红，每条尺子对着盘上那份真文件量出的数必须全 > 0（读数全 0 的守卫永远不会红） |
| `test_permissions.py`（17） | 三种模式 × 三种风险、路径锁在所有模式下生效、破坏性命令在 AUTO 下仍拒、写 `.env` 需显式放行、会话级授权不能吞掉密钥警告、无确认渠道时失败关闭 |
| `test_planner.py`（13） | 清单不变量、回填、状态机 |
| `test_trace_cli.py`（23） | `mcc trace` 渲染：时间线/热点/`--why-failed`、schema 不匹配时点名缺哪些字段、旧 trace 落盘标签与当前规则不一致时打印"规则口径变过"；`--otel` 那条路另 11 条：树按 trace 声明的样子出来、失败的 tool span 标红、**没有 `span_id` 的记录发成 event 而不是进垃圾桶**、stdout 保持一份可 parse 的 JSON（报表走 stderr）、`--out` 写的是**平台无关字节**（Windows 上不许长出 `\r`，§10 第 32 行）、**校验不过一个字节都不写**、目录不存在退 2、`--endpoint` 出本机前先警告、发送失败退 1、没有 `--endpoint` 时逐条打印 `gaps()`；再加 `python -m miniclaude` **必须把退出码传出去**（起子进程量，§10 第 31 行） |
| `test_prompts.py`（12） | 环境事实是否被注入（Windows/POSIX/macOS 各钉一批关键词）、工具清单回灌且无名字时仍禁止编造、提示词跨调用字节稳定、`REPO_MAP=0` 时那棵退回的目录树：只画形状不画噪声、广度优先、行数预算花完要留截断提示、空工作区 |
| `test_trace.py`（10） | 落盘与脱敏、replay 容忍非对象 JSON、summarize 只读已记录的字段 |
| `test_context.py`（10） | 估算与实测校准、压力分档 |
| `test_trace_contract.py`（11） | **度量契约**：每个报表键都有生产者、发起数≠执行数、在线与离线分类共用同一份定义、schema 快照、`output_chars` 只能从 `tool_call` 记录加出来（含"省略量为 0 是真算了 0"这条）、"孤儿键"检测器自己能抓到 planted 样例 |
| `test_otel_export.py`（94） | §7.3-3 翻译层最容易骗人的四类，每类一组：**丢记录**（5 种无 `span_id` 的 kind 变 event 且入账、event 落在**同一轮**的宿主 span 上、跨会话不串宿、v1 整批无 id 也翻得出来、盘上 24 份真实轨迹逐个跑"账加得起来 + `validate()` 0 问题"、时间不许倒流）；**丢字段**（`tests/schema_v2.json` 里 20 个 kind 的每个键都必须在 `FIELD_MAP` 登记过名字 —— 加埋点不登记就是 CI 红）；**编结构**（断掉的父亲不重挂但留下那个 id、null 不塞空串而是缺席 + 计数、`latency` 反推的起点写明 `time_source`、没有 `latency` 的 span 就零长度、合并不了的同名值改挂 kind 前缀两个都留、**`_resource` 只扫 records 会把 v1 报成"0 个会话 + 空模型 + 自报 2.0"，三条各钉一条**）；**编码错**（`int64` 是十进制字符串、时间戳是纳秒、`AnyValue` 只许一个分支、非法 id 走 sha 派生而不是原样发出、`validate()` 每种拒收信号一条中文报错且**空 payload 不算干净 payload**）。另加 `post_otlp()` 四条（非 http 不发、校验不过不发、传输失败变回执不抛异常、被拒时保留响应体）与这个 Stage 存在理由本身：**埋点不许 import OTel SDK**（`sys.modules` 与 `src/` 全文两处查） |
| `test_mcp_bridge.py`（44） | §3.7 的三条硬要求逐条钉：远端广告 `read_file` 也覆盖不了本地那个（前缀隔离 + 本地仍读出真磁盘内容）、远端自报 `read`/`destructive` 一律采纳 `execute` 而声明值只做展示、参数校验在**出网之前**（`text=12` 拒、连接还能用）、`MCP_SERVERS` 形状与语义各一个产地、子进程 env 白名单（`LLM_API_KEY` 不透传、点名才给）、握手参数里的密钥不进 trace、服务崩/沉默/吐垃圾各自的原因带 stderr 且 `close()` 后不留子进程、没有 `properties` 的 schema 整条不装配、**官方 SDK `FastMCP` 服务与自写敌意服务两条发现路径共用同一份断言**（中文往返、`TextContent` dataclass 而不是 dict） |
| `test_skills.py`（21） | 目录与正文分家：5,200 字符正文渲出 3 行目录（**长度与正文无关**这条由测试自己造两个技能量出来，不是看着像）、超预算时宁少列一个技能也不丢掉成本提示、技能名进不了安全字符集就不装（`load_skill` 按名字取，参数里没有路径就没有越界）、同目录别的文件只报名字不读不执行（D20）、frontmatter 手写解析不引 YAML（未闭合的头整篇当正文） |
| `test_cli_ext.py`（20） | 装配只有一条路径：`build_session` 里 MCP/技能都从 `extra_tools` 进、READONLY 下桥根本不建（`skip_reason` 非空且外部工具为 `[]`）、AUTO 对 `mcp__` 是 ask 而对本地写是 allow、**被拒的远端调用在盘上不留副作用**（写到工作区之外的那个文件不存在、trace 里只有 `permission/deny` 没有 `tool_call`）、授权臂作为对照真落一行、`mcp`/`skills` 两条事件的字段集合与 schema 契约对齐 |
| `test_hanoi.py`（6） | 外部引入的算法测试，与 Agent 主线无关，保留原样 |

评测层那六个文件用的是 `tests/test_eval_runner.py` 里的**临时玩具题集**（一个算错的 `add`），不依赖 `eval/fixtures` 的 24 道真考题 —— 考题内容改了不需要跟着改测试，而跑批器自己的契约仍然被钉住。真题集只在 `test_eval_taskset.py` 里被结构性地检查（题面不泄漏答案、判据齐不齐）。

`tests/fakes.py` 提供 `FakeLLM`：按脚本吐响应，不联网。**没有真实 API 也能测完整个循环**，这是 SPEC §6.2 里"不要因为没有真实 API 就跳过测试"这条的执行方式。

`demos/fixtures/red-tests` 是故意红的，所以项目根 `conftest.py` 用 `collect_ignore_glob` 把 fixtures 挡在收集范围外 —— 否则 `pytest .` 会被"用来考 Agent 的烂仓库"污染。
