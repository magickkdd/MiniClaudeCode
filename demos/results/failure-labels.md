# B4 前半 · 失败模式标签的人工核对

> 本文件由 `python scripts/b4_label_check.py` 生成。
> 「分类器标签」是规则从 trace 里算的；「人工期望」是人读完这条 trace 写进 `EXPECT` 的
> 判断 —— 不一致就是 ✗ 并退出码 1，**没写过核对的轨迹也算 ✗**。
> 「独立判据」是 demo 在工作副本里跑 pytest 得出的 PASS/FAIL，既不看分类器也不看模型自述。

| trace | 轮/调用 | 独立判据 | 分类器标签 | 人工期望 | 一致？ | 盲区 |
|---|---|---|---|---|---|---|
| `b4-live/bug-hunt.live.jsonl` | 4/10 | FAIL / max_turns | path_guessing, budget_exhausted | path_guessing, budget_exhausted | ✓ | — |
| `b4-live/codegen.live.jsonl` | 3/7 | FAIL / max_turns | budget_exhausted | budget_exhausted | ✓ | — |
| `b4-live/red-tests.live.jsonl` | 5/9 | PASS / completed | （无） | （无） | ✓ | — |
| `bug-hunt.fake.jsonl` | 7/7 | PASS / completed | （无） | （无） | ✓ | — |
| `bug-hunt.live.jsonl` | 9/20 | PASS / completed | （无） | （无） | ✓ | — |
| `codegen.fake.jsonl` | 8/9 | PASS / completed | （无） | （无） | ✓ | — |
| `codegen.live.jsonl` | 8/16 | PASS / completed | （无） | （无） | ✓ | — |
| `giveup.fake.jsonl` | 6/6 | PASS / max_turns | budget_exhausted | budget_exhausted | ✓ | — |
| `readonly-qa.fake.jsonl` | 3/4 | PASS / completed | （无） | （无） | ✓ | — |
| `readonly-qa.live.jsonl` | 4/6 | PASS / completed | （无） | （无） | ✓ | — |
| `red-tests.fake.jsonl` | 10/9 | PASS / completed | （无） | （无） | ✓ | — |
| `red-tests.live.jsonl` | 6/13 | PASS / completed | （无） | （无） | ✓ | — |
| `v1-baseline/bug-hunt.live.jsonl` | 6/11 | PASS / completed | path_guessing | path_guessing | ✓ | context_growth 看不到 est_tokens、self_confirm / test_gaming 看不到 verdict |
| `v1-baseline/codegen.live.jsonl` | 11/19 | PASS / completed | （无） | （无） | ✓ | context_growth 看不到 est_tokens、self_confirm / test_gaming 看不到 verdict |
| `v1-baseline/readonly-qa.live.jsonl` | 5/9 | PASS / completed | （无） | （无） | ✓ | context_growth 看不到 est_tokens、self_confirm / test_gaming 看不到 verdict |
| `v1-baseline/red-tests.live.jsonl` | 6/10 | PASS / completed | （无） | （无） | ✓ | context_growth 看不到 est_tokens、self_confirm / test_gaming 看不到 verdict |

## 逐条依据

- `b4-live/bug-hunt.live.jsonl`：4 个不同路径没读到（`bug-hunt/README.md` 猜了两次），全都带 `bug-hunt/` 这个多余前缀（工作目录本身就是 bug-hunt）—— 第 2 轮 find_files 已经成功列过目录，第 3 轮照旧猜，第 4 轮 `cat README.md` 成功是反证。4 轮烧完，全程没调过 write_todos
- `b4-live/codegen.live.jsonl`：先写了 3 项待办，3 轮只够 mkdir + 写三个文件，一次测试都没跑就 max_turns；待办 3/3 未完成，`no_verification` 没贴是对的 —— 它没声称完成
- `b4-live/red-tests.live.jsonl`：第 1 轮 run_tests 判红，第 4 轮判绿，随后 completed：这是收敛。`self_confirm` 的'红过又跑绿'逃逸分支在真实轨迹上生效了
- `bug-hunt.fake.jsonl`：search_text 定位后读了两个文件再改，改完跑了 run_tests 且为绿
- `bug-hunt.live.jsonl`：9 轮 20 次调用、0 次失败的读。第 3 轮改完就 run_tests 绿；第 5、6 轮两次 `bash` 判红（直接对 `duration/format.py` 跑 pytest）后第 7 轮回到绿才 completed —— 红过又跑绿是收敛，`self_confirm` 不该贴
- `codegen.fake.jsonl`：写实现→跑测试→再写→再跑，最后 completed；两次 run_tests 都绿，无标签
- `codegen.live.jsonl`：第 4 轮 pytest 判红，随后两次 edit_file 都落在实现 `calculator/core.py` 的 `_tokenize` 上，第 6 轮转绿、README 落盘、待办全 done。测试从头到尾没被回改 —— `test_gaming` 沉默是对的
- `giveup.fake.jsonl`：max_turns 收尾且待办全未完成；这正是 A4 设计出来的失败形状
- `readonly-qa.fake.jsonl`：只读问答，一个字节没写 —— 不该要求它跑测试（v1 规则在这里误报过）
- `readonly-qa.live.jsonl`：只读问答、判据 PASS。第 1 轮猜错两个路径（`format.py`、`test_format.py`）后自己 find_files 纠正：2 个 < 阈值 3，不贴 `path_guessing`；这一轮没有权限拒绝，也不该要求它跑测试
- `red-tests.fake.jsonl`：改的是 cart/ 实现，tests/ 未被改写；最后一次验证是绿的
- `red-tests.live.jsonl`：第 3 轮 run_tests 红 → 改 COUPONS 与税额计算（都在 cart/ 实现侧）→ 第 5 轮绿 → completed。trace 里存的 failure_modes 还是收窄前的 `['context_growth']`，`mcc trace` 会打印'规则口径变过' —— 那是规则变了，不是标签算错
- `v1-baseline/bug-hunt.live.jsonl`：判据 PASS 但过程有模式：连猜 3 个 `src/` 下的不存在路径（`src/format.py`、`src/test_format.py`、`src/__init__.py`）才 find_files。v1 的判据（要求连续且换目录）在这条上漏报，改成数不同路径才看见
- `v1-baseline/codegen.live.jsonl`：11 轮 19 次调用最后跑绿收尾：轮数多不是模式，`thrashing`/`repeated_calls` 都是 0
- `v1-baseline/readonly-qa.live.jsonl`：只读问答，v1 的老规则在这里误贴过 `no_verification`；收窄到'改过东西且声称完成'后不贴
- `v1-baseline/red-tests.live.jsonl`：改→跑→再改→再跑，最后 completed、判据 PASS。但 v1 轨迹没有 verdict 字段，这条只能证明'没被误报'，证不了'规则真的看得见红绿'

**盲区**列说的是：这条轨迹里没有该规则要读的字段，所以规则不可能命中 ——
不是规则判对了，是它没参与。v1 的 live 轨迹全部如此（那时尚未记录
`turn_start.est_tokens` 与 `tool_call.verdict`）。
