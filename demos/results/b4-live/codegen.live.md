# Demo 1 · 从零生成一个带测试的模块（验收项 A2）

> 本文件由 `python demos/run_demo.py --demo codegen --engine live` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。
> 引擎：真实端点 `agnes-2.5-flash`，会受模型随机性影响。

| 字段 | 值 |
|---|---|
| task | 在当前目录创建一个 Python 计算器项目：`calculator` 包提供 add/subtract/multiply/divide 和 `evaluate(expression)`，后者解析形如 `2 + 3 * 4` 的四则表达式，乘除优先于加减，同级从左到右，不支持括号；除数为零抛 `DivideByZeroError`。写完整的 pytest 测试并跑到全绿，最后把用法记进 README。 |
| repo/baseline | `demos/fixtures/greenfield` · baseline `72d1ba19cb66`（1 个文件） |
| expected | pytest 退出码 0，且 `evaluate` 的四则语义与除零行为经独立脚本校验 |
| actual | `max_turns` · 判定 FAIL（1/4） |
| turns / tokens | 3 轮 / 10,994 tokens |
| tool_calls | 7 次，其中 is_error 0 次（0%） |
| 工具序列 | write_todos → bash → read_file → bash → write_file → write_file → write_file |
| denied / repeated / stalled | 0 次被拒 · 0 次逐调用重复 · 0 轮整组重演 |
| 失败模式 | budget_exhausted |
| 上下文峰值 | 3,527 tokens |
| 权限模式 | `auto`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |
| wall time | 13.4s |
| trace | `demos/traces/codegen.live.jsonl` |
| 工作副本 | `demos/.work/codegen`（判定就在这个目录跑） |

**说明** 脚本里第一版 `evaluate` 故意写成从左到右一路算，让测试去抓它 —— 这一步同时是 self-debug 的证据。

## 判定明细

- [ ] pytest 退出码 0 —— 退出码 5，0 passed / 0 failed
- [x] 函数行为符合任务承诺（不依赖模型自述） —— 行为校验通过
- [ ] 真的写了测试，不是空壳 —— 测试文件 0 个，收集到 0 个用例
- [ ] README 写了用法 —— README.md

退出码判据：判定脚本在 `codegen` 里独立运行 pytest 与行为探测，不看模型最后那段话。

## 终端输出（原样）

```text
✓ write_todos 3 步  0ms
任务清单
  [~] 创建 calculator 包结构 (__init__.py, core.py, calculator.py)
  [ ] 写 pytest 测试并跑通
  [ ] 更新 README.md 说明用法
✓ bash        ls "D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/codegen"  76ms
✓ read_file   README.md  3ms
✓ bash        mkdir -p "D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/c…  84ms
✓ write_file  calculator/__init__.py  1ms
✓ write_file  calculator/core.py  1ms
✓ write_file  calculator/calculator.py  1ms
```

## 模型的最后一段话

已达到最大轮数（3），任务未完成。请把任务拆小一点，或指定更明确的目标文件后重试。

## diff：基线 → 运行后

```diff
--- a/calculator/__init__.py
+++ b/calculator/__init__.py
@@ -0,0 +1,19 @@
+"""Calculator package — add, subtract, multiply, divide and expression evaluation."""
+
+from .core import (
+    DivideByZeroError,
+    add,
+    subtract,
+    multiply,
+    divide,
+    evaluate,
+)
+
+__all__ = [
+    "add",
+    "subtract",
+    "multiply",
+    "divide",
+    "evaluate",
+    "DivideByZeroError",
+]
--- a/calculator/calculator.py
+++ b/calculator/calculator.py
@@ -0,0 +1,24 @@
+"""Standalone calculator CLI — not used by tests."""
+
+import sys
+from calculator.core import evaluate
+
+
+def main() -> int:
+    if len(sys.argv) != 2:
+        print("Usage: python calculator.py <expression>", file=sys.stderr)
+        return 2
+    try:
+        result = evaluate(sys.argv[1])
+    except ZeroDivisionError as e:
+        print(f"Error: {e}", file=sys.stderr)
+        return 1
+    except ValueError as e:
+        print(f"Error: {e}", file=sys.stderr)
+        return 1
+    print(result)
+    return 0
+
+
+if __name__ == "__main__":
+    raise SystemExit(main())
--- a/calculator/core.py
+++ b/calculator/core.py
@@ -0,0 +1,98 @@
+"""Core calculator operations and expression evaluator."""
+
+
+class DivideByZeroError(ZeroDivisionError):
+    """Raised when division by zero is attempted."""
+
+
+def add(a: float, b: float) -> float:
+    return a + b
+
+
+def subtract(a: float, b: float) -> float:
+    return a - b
+
+
+def multiply(a: float, b: float) -> float:
+    return a * b
+
+
+def divide(a: float, b: float) -> float:
+    if b == 0:
+        raise DivideByZeroError("division by zero")
+    return a / b
+
+
+def evaluate(expression: str) -> float:
+    """Evaluate a simple arithmetic expression without parentheses.
+
+    Supports ``+``, ``-``, ``*``, ``/`` with standard precedence
+    (multiplication/division before addition/subtraction) and left-to-right
+    associativity within the same precedence level.
+
+    Whitespace around numbers and operators is ignored.
+    """
+    tokens = _tokenize(expression)
+    result = _parse_add_sub(tokens)
+    if tokens:
+        raise ValueError(f"unexpected token: {tokens[0]!r}")
+    return result
+
+
+def _tokenize(expression: str):
+    """Split an expression string into numeric and operator tokens."""
+    tokens = []
+    i = 0
+    expr = expression.strip()
+    while i < len(expr):
+        if expr[i].isspace():
+            i += 1
+            continue
+        if expr[i] in "+-*/":
+            tokens.append(expr[i])
+            i += 1
+        elif expr[i].isdigit() or expr[i] == ".":
+            j = i
+            while j < len(expr) and (expr[j].isdigit() or expr[j] == "."):
+                j += 1
+            tokens.append(float(expr[i:j]))
+            i = j
+        else:
+            raise ValueError(f"unsupported character: {expr[i]!r}")
+    return tokens
+
+
+def _parse_add_sub(tokens):
+    """Parse addition and subtraction (lowest precedence)."""
+    left = _parse_mul_div(tokens)
+    while tokens and tokens[0] in "+-":
+        op = tokens.pop(0)
+        right = _parse_mul_div(tokens)
+        if op == "+":
+            left = add(left, right)
+        else:
+            left = subtract(left, right)
+    return left
+
+
+def _parse_mul_div(tokens):
+    """Parse multiplication and division (higher precedence)."""
+    left = _parse_primary(tokens)
+    while tokens and tokens[0] in "*/":
+        op = tokens.pop(0)
+        right = _parse_primary(tokens)
+        if op == "*":
+            left = multiply(left, right)
+        else:
+            left = divide(left, right)
+    return left
+
+
+def _parse_primary(tokens):
+    """Parse a numeric literal."""
+    if not tokens:
+        raise ValueError("unexpected end of expression")
+    tok = tokens.pop(0)
+    if isinstance(tok, (int, float)):
+        return float(tok)
+    raise ValueError(f"expected number, got {tok!r}")
```
