"""demo 用的 FakeLLM 脚本 —— 让三个 demo 在**没有 API、不花一分钱**的前提下也能跑。

这些脚本不是"假装成功"：每一条工具调用都真的落在 `demos/.work/` 里的 fixture
副本上，成功与否由 `run_demo.py` 的独立判定（跑 pytest、比对基线哈希）决定，
跟"模型说它成功了没有"无关。所以：

* `--engine fake` 证明**工程闭环**成立（循环、权限、工具、trace、判定）；
* `--engine live` 证明**真实模型**能在同一套骨架上完成同样的任务。

两者的区别会原样写进生成的证据文件，不含糊过去。
"""

from __future__ import annotations

from typing import Any

from miniclaude.messages import LLMResponse

from fakes import scripted_final_text, scripted_tool_calls

# --------------------------------------------------------------- Demo 1 代码生成

CALC_INIT = '''"""calculator —— 四则运算与无括号表达式求值。"""

from .core import (
    DivideByZeroError,
    add,
    divide,
    evaluate,
    multiply,
    subtract,
)

__all__ = ["DivideByZeroError", "add", "subtract", "multiply", "divide", "evaluate"]
__version__ = "0.1.0"
'''

CALC_HEAD = '''"""四则运算与表达式求值。"""

from __future__ import annotations

import re


class DivideByZeroError(ValueError):
    """除数为零。继承 ValueError，调用方按参数错误处理即可。"""


def add(left: float, right: float) -> float:
    return round(left + right, 10)


def subtract(left: float, right: float) -> float:
    return round(left - right, 10)


def multiply(left: float, right: float) -> float:
    return round(left * right, 10)


def divide(left: float, right: float) -> float:
    if right == 0:
        raise DivideByZeroError("除数不能为 0")
    return round(left / right, 10)


_TOKEN_RE = re.compile(r"\\d+(?:\\.\\d+)?|[+\\-*/]")


def _tokenize(expression: str) -> list[str]:
    raw = (expression or "").strip()
    if not raw:
        raise ValueError("表达式不能为空")
    tokens = _TOKEN_RE.findall(raw)
    if "".join(tokens) != re.sub(r"\\s+", "", raw):
        raise ValueError(f"无法解析表达式：{expression!r}")
    return tokens


def _apply(op: str, left: float, right: float) -> float:
    if op == "+":
        return add(left, right)
    if op == "-":
        return subtract(left, right)
    if op == "*":
        return multiply(left, right)
    return divide(left, right)
'''

# 第一版故意写成"从左到右一路算下来"：这是没测过优先级时最容易写出的实现。
CALC_CORE_NAIVE = (
    CALC_HEAD
    + '''

def evaluate(expression: str) -> float:
    tokens = _tokenize(expression)
    total = float(tokens[0])
    for index in range(1, len(tokens), 2):
        total = _apply(tokens[index], total, float(tokens[index + 1]))
    return round(total, 10)
'''
)

CALC_CORE_OK = (
    CALC_HEAD
    + '''

def evaluate(expression: str) -> float:
    """两遍扫描：先折叠乘除，再从左到右做加减，所以 `2 + 3 * 4` 是 14。"""
    tokens = _tokenize(expression)

    folded: list[Any] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"*", "/"} and folded and index + 1 < len(tokens):
            left = float(folded.pop())
            index += 1
            folded.append(_apply(token, left, float(tokens[index])))
        else:
            folded.append(token)
        index += 1

    if len(folded) % 2 == 0:
        raise ValueError(f"表达式不合法：{expression!r}")

    total = float(folded[0])
    for position in range(1, len(folded), 2):
        total = _apply(folded[position], total, float(folded[position + 1]))
    return round(total, 10)
'''
).replace(
    "import re", "import re\nfrom typing import Any", 1
)

CALC_TESTS = '''"""calculator 的行为测试。"""

import pytest

from calculator import (
    DivideByZeroError,
    add,
    divide,
    evaluate,
    multiply,
    subtract,
)


def test_four_operations():
    assert add(2, 3) == 5
    assert subtract(2, 3) == -1
    assert multiply(2, 3) == 6
    assert divide(6, 3) == 2


def test_divide_by_zero_is_explicit():
    with pytest.raises(DivideByZeroError):
        divide(1, 0)


def test_floats_stay_readable():
    assert add(0.1, 0.2) == 0.3
    assert multiply(1.5, 2) == 3.0


def test_evaluate_respects_precedence():
    assert evaluate("2 + 3 * 4") == 14
    assert evaluate("10 - 6 / 2") == 7
    assert evaluate("2 * 3 + 4") == 10


def test_evaluate_is_left_associative():
    assert evaluate("10 - 2 - 3") == 5
    assert evaluate("100 / 5 / 2") == 10


@pytest.mark.parametrize("bad", ["", "   ", "1 +", "abc", "2 ^ 3", "* 3"])
def test_evaluate_rejects_garbage(bad):
    with pytest.raises(ValueError):
        evaluate(bad)
'''

CALC_README = '''# calculator

只依赖标准库的四则运算小模块，含一个不支持括号的表达式求值。

```python
from calculator import DivideByZeroError, divide, evaluate

evaluate("2 + 3 * 4")   # 14.0
evaluate("10 - 6 / 2")  # 7.0
divide(1, 0)            # raises DivideByZeroError
```

## 规则

- 乘除优先于加减，同级从左到右结合。
- **不支持括号**，也不支持一元负号；出现无法解析的字符直接 `ValueError`。
- 除数为零抛 `DivideByZeroError`（`ValueError` 的子类）。
- 结果统一保留 10 位小数，所以 `add(0.1, 0.2) == 0.3`。

## 跑测试

```
python -m pytest -q
```
'''

CODEGEN_TODOS_DONE = [
    {"content": "建 calculator 包：四则运算 + DivideByZeroError", "status": "completed"},
    {"content": "加 evaluate：乘除优先于加减", "status": "completed"},
    {"content": "写 pytest 用例并跑绿", "status": "completed"},
    {"content": "在 README 写清用法与不做什么", "status": "completed"},
]


def codegen_script() -> list[LLMResponse]:
    """清单 → 建包 → 测试红（优先级算错）→ 重写求值 → 全绿 → 补文档。"""
    return [
        scripted_tool_calls(
            [
                (
                    "write_todos",
                    {
                        "todos": [
                            {"content": "建 calculator 包：四则运算 + DivideByZeroError", "status": "in_progress"},
                            {"content": "加 evaluate：乘除优先于加减", "status": "pending"},
                            {"content": "写 pytest 用例并跑绿", "status": "pending"},
                            {"content": "在 README 写清用法与不做什么", "status": "pending"},
                        ]
                    },
                )
            ],
            text="任务里有三件事都得落地：包、求值、测试。先列清单再动手。",
        ),
        scripted_tool_calls(
            [
                ("write_file", {"path": "calculator/__init__.py", "content": CALC_INIT}),
                ("write_file", {"path": "calculator/core.py", "content": CALC_CORE_NAIVE}),
                ("write_file", {"path": "tests/test_calculator.py", "content": CALC_TESTS}),
            ],
            text="先建包：四个运算和表达式求值放 `calculator/core.py`，测试另放一处。",
        ),
        scripted_tool_calls([("run_tests", {"target": ""})]),
        scripted_tool_calls(
            [("write_file", {"path": "calculator/core.py", "content": CALC_CORE_OK})],
            text="`2 + 3 * 4` 得到 20，说明我按从左到右一路算了。改成两遍扫描：先折叠乘除，再算加减。",
        ),
        scripted_tool_calls([("run_tests", {"target": ""})]),
        scripted_tool_calls(
            [("write_file", {"path": "README.md", "content": CALC_README})],
            text="全绿。把用法和「不做括号」这条边界写进 README，省得后来人猜。",
        ),
        scripted_tool_calls([("write_todos", {"todos": CODEGEN_TODOS_DONE})]),
        scripted_final_text(
            "计算器项目已建好并全绿：\n"
            "- `calculator/core.py`：add/subtract/multiply/divide + `DivideByZeroError`，"
            "`evaluate()` 用两遍扫描实现乘除优先\n"
            "- `tests/test_calculator.py`：覆盖四个运算、除零、浮点、优先级、同级左结合、非法输入\n"
            "- `README.md`：用法与「不支持括号」这条边界\n\n"
            "`run_tests` 退出码 0。中途 `evaluate` 第一版把优先级算成了从左到右，"
            "是测试把它抓出来的，不是我看出来的。"
        ),
    ]


# --------------------------------------------------------------- Demo 2 Bug 修复

DAY_TESTS = '''"""跨天的格式化。README 表格里那两行一直没有测试覆盖。"""

from duration import format_duration


def test_exactly_one_day():
    assert format_duration(86400) == "1d0h0m0s"


def test_just_under_a_day():
    assert format_duration(86399) == "23h59m59s"


def test_readme_examples():
    assert format_duration(90000) == "1d1h0m0s"
    assert format_duration(90061) == "1d1h1m1s"


def test_days_and_hours_stay_separate():
    assert format_duration(86400 + 3600) == "1d1h0m0s"
    assert format_duration(3 * 86400 + 2 * 3600 + 61) == "3d2h1m1s"
'''


def bug_hunt_script() -> list[LLMResponse]:
    """检索定位 → 读代码 → 改一行 → 补回归测试 → 跑绿。"""
    return [
        scripted_tool_calls(
            [
                (
                    "write_todos",
                    {
                        "todos": [
                            {"content": "定位 format_duration 的分段逻辑", "status": "in_progress"},
                            {"content": "对照 README 表格确认期望输出", "status": "pending"},
                            {"content": "修复并补防回归测试", "status": "pending"},
                        ]
                    },
                )
            ]
        ),
        scripted_tool_calls(
            [
                ("search_text", {"pattern": "def format_duration", "path_glob": "**/*.py"}),
                ("read_file", {"path": "README.md"}),
            ],
            text="先确认承诺的格式，再找实现落在哪个文件。",
        ),
        scripted_tool_calls([("read_file", {"path": "duration/format.py"})]),
        scripted_tool_calls(
            [
                (
                    "edit_file",
                    {
                        "path": "duration/format.py",
                        "old_string": "    days, rest = divmod(total, SECONDS_PER_HOUR)",
                        "new_string": "    days, rest = divmod(total, SECONDS_PER_DAY)",
                },
                )
            ],
            text="根因找到：切「天」那次 divmod 用的是每小时的秒数，90000 秒因此被切成 25 段。改成 SECONDS_PER_DAY。",
        ),
        scripted_tool_calls(
            [("write_file", {"path": "tests/test_format_days.py", "content": DAY_TESTS})],
            text="现有测试最远只到 3599 秒，所以这个 bug 一直是哑的。补上跨天的用例。",
        ),
        scripted_tool_calls([("run_tests", {"target": ""})]),
        scripted_final_text(
            "修好了，根因是单位换算用错常量：`duration/format.py` 里切「天」的那次 `divmod` "
            "传的是 `SECONDS_PER_HOUR`（3600）而不是 `SECONDS_PER_DAY`（86400），"
            "于是 90000 秒变成 `25d0h0m0s`。\n\n"
            "- 改动 1 行：`duration/format.py`\n"
            "- 新增 `tests/test_format_days.py`：卡住 86399/86400 边界与 README 的两条示例\n"
            "- `run_tests` 退出码 0\n\n"
            "顺带一句：`parse_duration` 早就支持 `d`，只有格式化侧漏了 —— "
            "README 的表格比测试更接近真实契约，这类不一致最容易被测试全绿的假象掩盖。"
        ),
    ]


# --------------------------------------------------------------- Demo 3 测试失败自动修复

def red_tests_script() -> list[LLMResponse]:
    """两轮「失败 → 分析 → 修改 → 重跑」。第二个缺陷被第一个挡着，必须迭代才看得见。"""
    return [
        scripted_tool_calls(
            [
                (
                    "write_todos",
                    {
                        "todos": [
                            {"content": "跑一遍测试，拿到失败清单", "status": "in_progress"},
                            {"content": "定位失败断言对应的代码", "status": "pending"},
                            {"content": "改代码（不动 tests/）并重跑到全绿", "status": "pending"},
                        ]
                    },
                )
            ]
        ),
        scripted_tool_calls([("run_tests", {"target": ""})], text="先以退出码为准，看现在到底红在哪。"),
        scripted_tool_calls(
            [("search_text", {"pattern": "BULK10", "path_glob": "**/*"})],
            text="折扣是 1.25 而不是 12.5，等于少乘了 10 倍 —— 先找 BULK10 定义在哪。",
        ),
        scripted_tool_calls([("read_file", {"path": "cart/pricing.py"})]),
        scripted_tool_calls(
            [
                (
                    "edit_file",
                    {
                        "path": "cart/pricing.py",
                        "old_string": 'COUPONS = {"BULK10": 0.01, "WELCOME": 0.05}',
                        "new_string": 'COUPONS = {"BULK10": 0.10, "WELCOME": 0.05}',
                    },
                )
            ],
            text="README 写的是 10% off，常量表里写成 0.01。改这里，不动断言。",
        ),
        scripted_tool_calls([("run_tests", {"target": ""})]),
        scripted_tool_calls(
            [
                (
                    "edit_file",
                    {
                        "path": "cart/pricing.py",
                        "old_string": "    tax = round(subtotal * TAX_RATE, 2)",
                        "new_string": "    tax = round(taxable_amount(subtotal, discount) * TAX_RATE, 2)",
                    },
                )
            ],
            text=(
                "折扣修好后同一个用例继续失败：tax=10.00，期望 9.00。README 说税按折扣后金额计，"
                "而 checkout() 直接乘在小计上 —— 旁边那个 taxable_amount() 就是为这件事写的，只是没被调用。"
            ),
        ),
        scripted_tool_calls([("run_tests", {"target": ""})]),
        scripted_tool_calls(
            [
                (
                    "write_todos",
                    {
                        "todos": [
                            {"content": "跑一遍测试，拿到失败清单", "status": "completed"},
                            {"content": "定位失败断言对应的代码", "status": "completed"},
                            {"content": "改代码（不动 tests/）并重跑到全绿", "status": "completed"},
                        ]
                    },
                )
            ]
        ),
        scripted_final_text(
            "全绿，`run_tests` 退出码 0，`tests/` 一行没动。\n\n"
            "两个缺陷，都在 `cart/pricing.py`：\n"
            "1. `COUPONS` 里 BULK10 写成 0.01，与 README 承诺的 10% 差 10 倍 → 改为 0.10；\n"
            "2. `checkout()` 的税基用了未扣折扣的小计，而 `taxable_amount()` 定义了却没被调用 → "
            "改成对折扣后的金额计税。\n\n"
            "第 2 条是被第 1 条挡住的：折扣错时 `assert order.discount == 12.5` 先失败，"
            "修完才轮到 `assert order.tax == 9.0`。所以这里必须改一次跑一次，一次看完是看不全的。"
        ),
    ]


# --------------------------------------------------------------- 附加：只读代码问答

READONLY_ANSWER = (
    "**超过一天的输出是错的。**\n\n"
    "`duration/format.py:15` 里切「天」用的除数是 `SECONDS_PER_HOUR`：\n\n"
    "```python\n"
    "days, rest = divmod(total, SECONDS_PER_HOUR)   # 应为 SECONDS_PER_DAY\n"
    "```\n\n"
    "于是 `format_duration(90000)` 返回 `25d0h0m0s`，而 README 的承诺是 `1d1h0m0s`"
    "（`SECONDS_PER_DAY = 86400` 就在同一文件第 7 行，定义了却没用上）。\n\n"
    "为什么测试没抓到：`tests/test_format.py` 最大只测到 3599 秒，`days` 恒为 0，"
    "这条分支从未被执行。`python -m duration 90000` 这个 CLI 路径同样受影响。"
)


def readonly_qa_script() -> list[LLMResponse]:
    """只读模式：检索 → 阅读 → 回答，不写任何文件。"""
    return [
        scripted_tool_calls(
            [
                ("find_files", {"pattern": "**/*.py"}),
                ("search_text", {"pattern": "format_duration", "path_glob": "**/*.py"}),
            ],
            text="先看仓库形状，再定位实现。",
        ),
        scripted_tool_calls(
            [
                ("read_file", {"path": "duration/format.py"}),
                ("read_file", {"path": "README.md"}),
            ]
        ),
        scripted_final_text(READONLY_ANSWER),
    ]


# --------------------------------------------------------------- 附加：有界放弃


def _flip_to(new: int) -> list[tuple[str, dict[str, Any]]]:
    old = 60 if new == 30 else 30
    return [
        (
            "edit_file",
            {
                "path": "app/settings.py",
                "old_string": f"DEFAULT_TIMEOUT = {old}",
                "new_string": f"DEFAULT_TIMEOUT = {new}",
            },
        )
    ]


def giveup_script() -> list[LLMResponse]:
    """两条断言互相矛盾，改哪边另一边就红。循环必须在预算内停下，而不是改测试作弊。"""
    return [
        scripted_tool_calls(
            [
                (
                    "write_todos",
                    {
                        "todos": [
                            {"content": "跑测试确认红的是哪一条", "status": "in_progress"},
                            {"content": "把 DEFAULT_TIMEOUT 对齐文档口径", "status": "pending"},
                        ]
                    },
                )
            ]
        ),
        scripted_tool_calls([("run_tests", {"target": ""})], text="红的是「60 秒」那条断言。"),
        scripted_tool_calls(_flip_to(60), text="按文档改成 60。"),
        scripted_tool_calls([("run_tests", {"target": ""})]),
        scripted_tool_calls(_flip_to(30), text="现在换成 30 那条红了。两条断言互相排斥，先改回去。"),
        scripted_tool_calls([("run_tests", {"target": ""})]),
    ]
