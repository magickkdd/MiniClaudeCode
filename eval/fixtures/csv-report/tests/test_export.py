"""导出功能还没做 —— 见 `docs/known-issues.md`。

这个文件**故意**留在仓库里红着：它不属于任何任务的白名单，用来检验评测层能不能
在"仓库里有已知红用例"的前提下仍然给出正确的结论。拿"pytest 退出码为 0"当判据的
老口径在这道题上永远判不出 pass，用例级白名单才分得清"该红的"和"改坏的"。
"""

from __future__ import annotations


def test_write_csv_emits_header(tmp_path):
    from report.export import write_csv

    target = tmp_path / "out.csv"
    write_csv([], target)
    assert target.read_text(encoding="utf-8").splitlines()[0] == "region,amount"
