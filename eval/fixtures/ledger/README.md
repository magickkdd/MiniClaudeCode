# ledger — 一个刻意做大的会计包

`ledger/part_01.py … part_08.py`，每个模块 100 个公开函数，各自带一个 `AUDIT_TAG`。
`tests/test_ledger.py` 在基线状态下**全绿**。

## 要加的汇总层

在 `reports/` 下为**每个** `ledger/part_NN.py` 写一个 `part_NN_summary.py`，内容只有三件事，
但必须与源文件逐字一致：

```python
"""Summary of ledger/part_01.py."""

SOURCE = "ledger/part_01.py"
AUDIT_TAG = "L01-xxxxxx"          # 源文件里那串，不能编
API = [
    ("compute_credit_000", "def compute_credit_000(rate, amount, fee)", "first docstring line..."),
    # ... 源文件里全部公开函数，按名字排序，一个不能少
]
```

再加一个 `tests/test_rollups.py`：用 `ast` 把每个 summary 的 `API` 与源文件比对，
公开函数的判定是"名字不以 `_` 开头的顶层函数"。签名用 `ast.unparse` 归一化后逐字比。

跑 `python -m pytest tests -q` 全绿即完成。

## 为什么这题在评测里

这个包**故意大到装不进 32k 的上下文预算**：八次全文读入 ≈ 13 万字符，
八份汇总写出去又是 11 万字符。设计目的见 `SPEC-v2.md` §3.3 与 `scripts/b2_compact_ab.py` ——
压缩阶梯关掉时它必须失败，开着时必须做完。
