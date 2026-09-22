---
name: trace-triage
description: 从一次会话的 trace 里定位失败模式与开销热点，并把结论落到具体记录上
when_to_use: 用户问"这次为什么跑成这样/为什么这么贵/为什么没改对"时
---

# 读一次会话的 trace

先取事实再下判断。结论必须能指回某条记录，指不回去的猜测不写进报告。

1. 三条命令按顺序跑，别一上来就翻原始 jsonl：

   ```bash
   mcc trace --latest --why-failed   # 失败模式标签 + 证据 + 处方
   mcc trace --latest --hot          # 最贵 3 轮、报错最多的工具、重复调用簇
   mcc trace --latest --json         # 要算数的时候用这份
   ```

   日志路径来自 `TRACE_PATH`；跑过 `--no-trace` 的会话没有东西可读，先确认它。
2. 每条记录都带 `session / seq / kind / turn`。按 `turn` 分组就是一次往返：
   `turn_start`（估算 token）→ `llm_request` → `llm_response` → `permission` → `tool_call`。
   少了 `tool_call` 说明"发起了但没执行"，去看那条 `permission` 的 `decision`。
3. 常见误判：
   - `tool_calls` 与 `tool_executed` 是两个数，被拒和虚构工具名只进前者；
   - `repeated_calls` 逐调用统计，`stalled_groups` 整组统计，两个都不等于"跑废了"；
   - `context_compact` 出现说明阶梯动了手，`context_refuse` 出现说明是**预算止损**杀掉的，
     不是模型不行 —— 归因时这两者必须分开。
4. 字段含义以 `SPEC-v2.md` §3.1 的事件表为准；表里有、代码里没有（或反之）就是 bug，
   `tests/test_trace_contract.py` 应该已经为它变红，先修那一处再回来写结论。
5. 报结论时给三样东西：死因（`run_end.termination`）、最贵的一处开销、下一步动作。
   数字要能溯源到 `seq`，否则不写。
