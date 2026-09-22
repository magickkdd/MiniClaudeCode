"""生成 `eval/fixtures/ledger/` —— B2 那条"必然超预算"的长任务仓库。

为什么要脚本而不手写：这份仓库的价值全在**规模**（8 个模块 × 100 个公开函数 ≈ 13 万字符），
手写既写不出也改不动。生成器同时是"这个 fixture 为什么长这样"的说明：
每个模块的 `AUDIT_TAG`、函数名、签名、docstring 都由 `random.Random(模块序号)` 决定，
重跑一次逐字节一致 —— 任务集哈希才不会莫名其妙漂移。

规模不是随手挑的，它由 §3.3 的触发线反推：
- 关掉阶梯时，第 6~7 轮读完就撞 `0.95 × TOKEN_BUDGET`，必须死在 `CONTEXT_OVERFLOW`；
- 开着阶梯时，L1 把读进来的 16k 字符换成一行标记，但 `write_file` 的 `content`
  参数在 **assistant 消息**里，L1 碰不到 → 阶梯必须走到 L2 才救得回来。
  这正是 B2 第三句判据（摘要里要能 grep 到已改文件名）要盯的场景。
"""

from __future__ import annotations

import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "ledger"

MODULES = 8
FUNCS_PER_MODULE = 100

NOUNS = [
    "credit", "debit", "ledger", "invoice", "deposit", "withdrawal", "voucher", "remit",
    "escrow", "dividend", "royalty", "customs", "tariff", "quota", "tranche", "collateral",
    "principal", "accrual", "amortize", "depreciate", "reconcile", "settle", "endorse",
    "warrant", "bond", "option", "swap", "spread", "margin", "premium", "deduct", "allocate",
]
VERBS = [
    "apply", "compute", "normalize", "round", "clamp", "convert", "aggregate", "project",
    "validate", "adjust", "merge", "split", "scale", "offset", "reverse", "book",
]
PARAMS = ["amount", "rate", "base", "floor", "ceiling", "periods", "factor", "fee", "skew", "cap"]

# 6 个函数体模板：生成时算得出期望值，测试就能写真断言而不是 `isinstance` 这种空话。
# 每个模板自带它真正引用的参数 —— 签名里多一个少一个都会在"求期望值"那步 KeyError。
BODIES = [
    ("round(base * rate - fee, 2)",
     lambda v: round(v["base"] * v["rate"] - v["fee"], 2),
     ["base", "rate", "fee"]),
    ("max(floor, min(cap, amount + fee * rate))",
     lambda v: max(v["floor"], min(v["cap"], v["amount"] + v["fee"] * v["rate"])),
     ["amount", "fee", "rate", "floor", "cap"]),
    ("amount / (1.0 + rate) if rate else amount",
     lambda v: v["amount"] / (1.0 + v["rate"]) if v["rate"] else v["amount"],
     ["amount", "rate"]),
    ("round((amount - base) * factor + periods, 4)",
     lambda v: round((v["amount"] - v["base"]) * v["factor"] + v["periods"], 4),
     ["amount", "base", "factor", "periods"]),
    ("(amount + base) / 2.0 if ceiling > floor else amount - base",
     lambda v: (v["amount"] + v["base"]) / 2.0 if v["ceiling"] > v["floor"] else v["amount"] - v["base"],
     ["amount", "base", "ceiling", "floor"]),
    ("abs(round(amount, 2)) % (rate * 7.0 + 1.0)",
     lambda v: abs(round(v["amount"], 2)) % (v["rate"] * 7.0 + 1.0),
     ["amount", "rate"]),
]


def _signature(name: str, params: list[str]) -> str:
    """规范签名形态 —— 与 `f"def {name}({ast.unparse(node.args)})"` 逐字相同。

    README 里要求汇总层照这个写法记录，probe 也用它反查：两侧唯一的公共定义。
    """
    return f"def {name}({', '.join(params)})"


def _docstring(body_index: int, tag: str, name: str) -> str:
    theme = [
        "Apply the settlement rule before the cut-off stamp is written to the audit trail",
        "Normalize a raw posting so downstream reconciliation stays bit-stable across periods",
        "Project the balance forward under the frozen rate table agreed with finance",
        "Clamp a posted figure into the band the risk policy allows for this account class",
        "Reverse a booking without touching the original journal entry or its evidence hash",
        "Validate a candidate posting against the ledger invariants held by this module",
    ][body_index]
    return f'{theme} (tagged {tag} in {name}).'


def build_module(index: int) -> tuple[str, dict[str, dict[str, str]], list[tuple[str, str, float]], str]:
    """返回 (源码, 该模块的公开 API 事实, 可断言的测试样本, audit tag)。"""
    rng = random.Random(index)
    tag = f"L{index:02d}-{rng.randrange(16 ** 6):06x}"
    lines = [
        '"""Ledger part {index:02d}: postings, clamps and reversals for one account class.'.format(index=index),
        "",
        "This module is one slice of a deliberately large package: the audit tag below is",
        "the only stable identifier for it, and a rollup module under `reports/` is expected",
        'to carry the tag plus the complete public API of this file.',
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from decimal import Decimal  # kept by a previous refactor; still used by helpers",
        "",
        f'AUDIT_TAG = "{tag}"',
        f'ACCOUNT_CLASS = "{index:02d}"',
        "TOLERANCE = 0.005",
        "",
        "",
        "def _quantize(value):",
        '    """Round to cents with the module policy; private helpers stay out of the API."""',
        '    return float(Decimal(str(value)).quantize(Decimal("0.01")))',
        "",
    ]
    facts: dict[str, dict[str, str]] = {}
    samples: list[tuple[str, str, float]] = []
    used: set[str] = set()
    for slot in range(FUNCS_PER_MODULE):
        body_index = slot % len(BODIES)
        expr, evaluator, required = BODIES[body_index]
        while True:
            name = f"{rng.choice(VERBS)}_{rng.choice(NOUNS)}_{slot:03d}"
            if name not in used:
                used.add(name)
                break
        params = list(required)
        rng.shuffle(params)  # 参数顺序按函数变化，签名就必须逐字读而不是靠套路猜
        lines.append("")
        lines.append(_signature(name, params) + ":")
        lines.append(f'    """{_docstring(body_index, tag, name)}"""')
        lines.append(f"    return {expr}")
        facts[name] = {
            "signature": _signature(name, params),
            "doc": _docstring(body_index, tag, name),
        }
        if slot % 12 == 0:  # 每个模块挑 8~9 个函数写真值断言
            values = {p: round(rng.uniform(1.0, 9.0), 2) for p in PARAMS}
            kwargs = {p: values[p] for p in params}
            samples.append((name, repr(kwargs), evaluator(values)))
    text = "\n".join(lines) + "\n"
    assert len(text.splitlines()) <= 500, f"part_{index:02d}.py 超出 read_file 默认行数上限"
    assert len(text) <= 30_000, f"part_{index:02d}.py 超出工具输出字符上限，读了个残"
    return text, facts, samples, tag


def write_files() -> dict[str, dict[str, dict[str, str]]]:
    """落盘，并返回每个模块的公开 API 事实（`main` 用它报告规模）。"""
    (ROOT / "ledger").mkdir(parents=True, exist_ok=True)
    (ROOT / "reports").mkdir(parents=True, exist_ok=True)
    (ROOT / "tests").mkdir(parents=True, exist_ok=True)
    all_facts: dict[str, dict[str, dict[str, str]]] = {}
    tests: list[str] = []
    for index in range(1, MODULES + 1):
        source, facts, samples, tag = build_module(index)
        module = f"part_{index:02d}"
        (ROOT / "ledger" / f"{module}.py").write_text(source, encoding="utf-8", newline="\n")
        all_facts[module] = facts
        tests.append(f"\n\ndef test_{module}_audit_tag():")
        tests.append(f'    assert {module}.AUDIT_TAG == "{tag}"')
        for name, kwargs, expected in samples:
            tests.append(f"\n\ndef test_{module}_{name}():")
            tests.append(f"    assert {module}.{name}(**{kwargs}) == {expected!r}")
    (ROOT / "conftest.py").write_text(
        '"""让 `import ledger` 在工作目录根下可用（评测把仓库拷进临时目录再跑）。"""\n\n'
        "import sys\nfrom pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n",
        encoding="utf-8",
        newline="\n",
    )
    (ROOT / "ledger" / "__init__.py").write_text(
        '"""Deliberately flat package: each part module owns one account class."""\n',
        encoding="utf-8",
        newline="\n",
    )
    (ROOT / "reports" / "__init__.py").write_text(
        '"""Rollup modules land here, one per ledger part."""\n', encoding="utf-8", newline="\n"
    )
    head = [
        '"""Behaviour tests for the ledger package. Green at baseline."""',
        "",
        # 显式 import 每个 part：`import ledger` 不会把子模块挂到命名空间上，
        # 靠 `ledger.part_02` 取值会让 8 个模块里 7 个报 AttributeError。
        "from ledger import " + ", ".join(f"part_{i:02d}" for i in range(1, MODULES + 1)),
        "",
    ]
    (ROOT / "tests" / "test_ledger.py").write_text(
        "\n".join(head) + "\n".join(tests) + "\n", encoding="utf-8", newline="\n"
    )
    return all_facts


def write_readme() -> None:
    (ROOT / "README.md").write_text(README, encoding="utf-8", newline="\n")


README = '''# ledger — 一个刻意做大的会计包

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
'''


def main() -> None:
    facts = write_files()
    write_readme()
    total = sum(len(f) for f in facts.values())
    chars = sum(len(p.read_text(encoding="utf-8")) for p in (ROOT / "ledger").glob("*.py"))
    print(f"生成 {MODULES} 个模块 · {total} 个公开函数 · ledger/ 共 {chars:,} 字符")


if __name__ == "__main__":
    main()
