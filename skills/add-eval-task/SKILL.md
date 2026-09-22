---
name: add-eval-task
description: 往 eval/ 题集里加一道带判据的评测任务，跑通 fake 引擎并可写进基线
when_to_use: 用户要求"加一道评测任务/补一题/扩题集"时
---

# 加一道评测任务

判据先于任务：一题的价值在于**它能失败**。写完先问自己"什么样的实现会被判错"，
答不出来就还没到动手的时候。

1. 读一份现有任务作模板：`eval/tasks/taxed-guard-regression-test.json`。
   字段语义住在 `src/miniclaude/eval/taskset.py`，判分住在 `judge.py` —— 改行为改这两处，
   不要在任务 JSON 里发明新字段。
2. 需要初始代码树时，把 fixture 放进 `eval/fixtures/<名字>`，`source` 写成
   `{"kind": "vendored", "path": "eval/fixtures/<名字>"}`。目前只有 `vendored` 能本地跑：
   别的 kind 要走网络取仓库，那部分在 Tier 2 里还没做（`taskset.py` 会直接拒绝）。
3. 判据字段：`probe`（在当前目录求值的 python 表达式，量"做没做到"）、`must_exist`、
   `added_only`、`min_changed_files`、`expected_termination`、`pass_to_pass`。
   `pass_to_pass` 从 fixture 的 `python -m pytest --collect-only -q` 输出里**抄真实 node id**，
   不要凭印象手写 —— 抄错 node id 的题会一直"通过"，而它其实什么都没测。
4. `fake.driver` 决定假模型怎么走流程，清单在 `src/miniclaude/eval/drivers.py` 的 `DRIVERS`
   （现值：`answer` `edit-verify` `write-verify` `read-claim` `guess-paths` `repeat-stall`
   `tamper` `long-report` 与几个 `demo:*`）。live 与 fake 共用同一份判据：驱动只决定
   "假模型怎么演"，不决定"判不判得过"。
5. `difficulty` 1–5、`supports_live` 标明这题能不能真跑。

自检（两条都必须过）：

```bash
mcc eval --lint            # 题集结构自检，不花钱
mcc eval --engine fake --only <新题 id>
```

`--lint` 报"probe 跑不出结果"通常就是 cwd 或 node id 写错。fake 绿了再谈 live：
live 要打真实端点，会花额度，一次只跑 `--only` 指定的那几题。
