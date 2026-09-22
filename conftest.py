"""pytest 根配置。

`demos/fixtures/` 与 `eval/fixtures/` 里是被刻意做成"有 bug / 测试是红的"的小仓库，
用来当 demo 与评测的靶子。它们不是本项目的测试，谁要是 `pytest .` 一把梭，
这些文件就会把结果污染成假失败 —— 所以在这里整体忽略。
"""

collect_ignore_glob = ["demos/fixtures/*", "eval/fixtures/*"]
