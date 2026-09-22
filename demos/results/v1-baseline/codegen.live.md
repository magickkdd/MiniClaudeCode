# Demo 1 · 从零生成一个带测试的模块（验收项 A2）

> 本文件由 `python demos/run_demo.py --demo codegen --engine live` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。
> 引擎：真实端点 `agnes-2.5-flash`，会受模型随机性影响。

| 字段 | 值 |
|---|---|
| task | 在当前目录创建一个 Python 计算器项目：`calculator` 包提供 add/subtract/multiply/divide 和 `evaluate(expression)`，后者解析形如 `2 + 3 * 4` 的四则表达式，乘除优先于加减，同级从左到右，不支持括号；除数为零抛 `DivideByZeroError`。写完整的 pytest 测试并跑到全绿，最后把用法记进 README。 |
| repo/baseline | `demos/fixtures/greenfield` · baseline `72d1ba19cb66`（1 个文件） |
| expected | pytest 退出码 0，且 `evaluate` 的四则语义与除零行为经独立脚本校验 |
| actual | `completed` · 判定 PASS（4/4） |
| turns / tokens | 11 轮 / 81,341 tokens |
| tool_calls | 19 次，其中 is_error 0 次（0%） |
| 工具序列 | write_todos → bash → read_file → find_files → write_file → write_todos → bash → write_file → write_file → run_tests → bash → edit_file → run_tests → write_todos → edit_file → write_todos → run_tests → read_file → read_file |
| redundant / denied | 0 / 0 |
| 权限模式 | `auto`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |
| wall time | 71.8s |
| trace | `demos/traces/codegen.live.jsonl` |
| 工作副本 | `demos/.work/codegen`（判定就在这个目录跑） |

**说明** 脚本里第一版 `evaluate` 故意写成从左到右一路算，让测试去抓它 —— 这一步同时是 self-debug 的证据。

## 判定明细

- [x] pytest 退出码 0 —— 退出码 0，39 passed / 0 failed
- [x] 函数行为符合任务承诺（不依赖模型自述） —— 行为校验通过
- [x] 真的写了测试，不是空壳 —— 测试文件 2 个，收集到 39 个用例
- [x] README 写了用法 —— README.md

退出码判据：判定脚本在 `codegen` 里独立运行 pytest 与行为探测，不看模型最后那段话。

## 终端输出（原样）

```text
✓ write_todos 3 步  0ms
任务清单
  [~] Create calculator package with add/subtract/multiply/divide and evaluate()
  [ ] Write pytest tests and run them to green
  [ ] Update README with usage
✓ bash        mkdir -p D:/Embodied\ Agent/Agent-JD/mini-claude-code/demos/.work/c…  44ms
✓ read_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/codegen/REA…  2ms
✓ find_files  **/*.py  0ms
✓ write_file  D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/codegen/cal…  9ms
✓ write_todos 3 步  0ms
任务清单
  [x] Create calculator package with add/subtract/multiply/divide and evaluate()
  [~] Write pytest tests and run them to green
  [ ] Update README with usage
✓ bash        mkdir -p D:/Embodied\ Agent/Agent-JD/mini-claude-code/demos/.work/c…  53ms
✓ write_file  D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/codegen/tes…  1ms
✓ write_file  D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/codegen/tes…  1ms
✓ run_tests   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/codegen/tes…  925ms
✓ bash        python -m pytest tests/ -v 2>&1  709ms
✓ edit_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/codegen/cal…  1ms
✓ run_tests   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/codegen/tes…  811ms
✓ write_todos 3 步  0ms
任务清单
  [x] Create calculator package with add/subtract/multiply/divide and evaluate()
  [x] Write pytest tests and run them to green
  [~] Update README with usage
✓ edit_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/codegen/REA…  1ms
✓ write_todos 3 步  0ms
任务清单
  [x] Create calculator package with add/subtract/multiply/divide and evaluate()
  [x] Write pytest tests and run them to green
  [x] Update README with usage
✓ run_tests   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/codegen/tes…  1.0s
✓ read_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/codegen/cal…  1ms
✓ read_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/codegen/REA…  9ms
全部完成，39 个测试用例全部通过。

**创建的内容：**
- `calculator/__init__.py` — 提供 `add/subtract/multiply/divide` 四个基本运算函数，以及 `evaluate(expression)` 解析四则表达式（乘除优先、同级左到右、不支持括号、除零抛 `DivideByZeroError`）。
- `tests/test_calculator.py` — 覆盖基本运算、优先级、边界情况和错误处理共 39 个用例。
- `README.md` — 新增计算器用法说明。

下一步：可直接用 `python -m pytest tests/ -v` 验证，或在其他项目中 `from calculator import evaluate` 引入使用。
```

## 模型的最后一段话

全部完成，39 个测试用例全部通过。

**创建的内容：**
- `calculator/__init__.py` — 提供 `add/subtract/multiply/divide` 四个基本运算函数，以及 `evaluate(expression)` 解析四则表达式（乘除优先、同级左到右、不支持括号、除零抛 `DivideByZeroError`）。
- `tests/test_calculator.py` — 覆盖基本运算、优先级、边界情况和错误处理共 39 个用例。
- `README.md` — 新增计算器用法说明。

下一步：可直接用 `python -m pytest tests/ -v` 验证，或在其他项目中 `from calculator import evaluate` 引入使用。

## diff：基线 → 运行后

```diff
--- a/README.md
+++ b/README.md
@@ -8,2 +8,56 @@
 - 测试放 `tests/`，用 `python -m pytest` 运行。
 - 结论文本里不要出现"应该能跑"，跑过再说。
+
+---
+
+# calculator
+
+轻量级四则运算库，提供基本算术函数和表达式求值。
+
+## 安装
+
+无需安装，直接引入：
+
+```python
+from calculator import add, subtract, multiply, divide, evaluate, DivideByZeroError
+```
+
+## API
+
+### 基本运算
+
+| 函数 | 说明 |
+|------|------|
+| `add(a, b)` | 加法 |
+| `subtract(a, b)` | 减法 |
+| `multiply(a, b)` | 乘法 |
+| `divide(a, b)` | 除法；除数为零时抛 `DivideByZeroError` |
+
+### 表达式求值
+
+`evaluate(expression: str) -> float`
+
+解析并计算形如 `2 + 3 * 4` 的四则表达式。
+
+规则：
+- 乘除优先于加减
+- 同级运算符从左到右
+- 不支持括号
+- 除数为零抛 `DivideByZeroError`
+- 非法表达式抛 `ValueError`
+
+```python
+>>> from calculator import evaluate
+>>> evaluate("2 + 3 * 4")
+14.0
+>>> evaluate("10 / 2 - 1")
+4.0
+```
+
+## 运行测试
+
+```bash
+python -m pytest tests/ -v
+```
+
+全部 39 个测试用例通过。
--- a/calculator/__init__.py
+++ b/calculator/__init__.py
@@ -0,0 +1,108 @@
+"""Calculator package providing basic arithmetic operations and expression evaluation."""
+
+
+class DivideByZeroError(Exception):
+    """Raised when division by zero is attempted."""
+
+
+def add(a: float, b: float) -> float:
+    """Return a + b."""
+    return a + b
+
+
+def subtract(a: float, b: float) -> float:
+    """Return a - b."""
+    return a - b
+
+
+def multiply(a: float, b: float) -> float:
+    """Return a * b."""
+    return a * b
+
+
+def divide(a: float, b: float) -> float:
+    """Return a / b. Raises DivideByZeroError if b is zero."""
+    if b == 0:
+        raise DivideByZeroError("division by zero")
+    return a / b
+
+
+def _tokenize(expression: str) -> list:
+    """Split expression into numbers and operators.
+
+    Each token is either a numeric string or a single operator character (+, -, *, /).
+    """
+    tokens = []
+    i = 0
+    expr = expression.strip()
+    while i < len(expr):
+        ch = expr[i]
+        if ch in ' \t':
+            i += 1
+            continue
+        if ch in '+-*/':
+            tokens.append(ch)
+            i += 1
+            continue
+        if ch.isdigit() or ch == '.':
+            j = i
+            while j < len(expr) and (expr[j].isdigit() or expr[j] == '.'):
+                j += 1
+            tokens.append(expr[i:j])
+            i = j
+            continue
+        raise ValueError(f"unexpected character {ch!r}")
+    return tokens
+
+
+def evaluate(expression: str) -> float:
+    """Evaluate a simple arithmetic expression with +, -, *, /.
+
+    Rules:
+    - Multiplication and division have higher precedence than addition and subtraction.
+    - Operators of the same precedence are evaluated left-to-right.
+    - No parentheses are supported.
+    - Dividing by zero raises DivideByZeroError.
+    - Empty input or malformed expressions raise ValueError.
+
+    Examples:
+        >>> evaluate("2 + 3 * 4")
+        14.0
+        >>> evaluate("10 / 2")
+        5.0
+    """
+    tokens = _tokenize(expression)
+    if not tokens:
+        raise ValueError("empty expression")
+
+    def _parse_number() -> float:
+        nonlocal tokens
+        t = tokens.pop(0)
+        return float(t)
+
+    # First pass: handle * and / (left-to-right)
+    values = [_parse_number()]
+    ops = []
+    while tokens:
+        op = tokens.pop(0)
+        if not tokens:
+            raise ValueError("unexpected trailing operator")
+        next_val = _parse_number()
+        if op in '+-':
+            ops.append(op)
+            values.append(next_val)
+        else:  # * or /
+            left = values.pop()
+            if op == '*':
+                values.append(left * next_val)
+            else:
…（diff 共 494 行，已截断，完整差异见工作副本）
```
