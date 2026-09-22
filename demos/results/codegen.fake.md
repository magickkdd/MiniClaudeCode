# Demo 1 · 从零生成一个带测试的模块（验收项 A2）

> 本文件由 `python demos/run_demo.py --demo codegen --engine fake` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。
> 引擎：FakeLLM 脚本 + 真工具真落盘：证明工程闭环，不证明模型能力。

| 字段 | 值 |
|---|---|
| task | 在当前目录创建一个 Python 计算器项目：`calculator` 包提供 add/subtract/multiply/divide 和 `evaluate(expression)`，后者解析形如 `2 + 3 * 4` 的四则表达式，乘除优先于加减，同级从左到右，不支持括号；除数为零抛 `DivideByZeroError`。写完整的 pytest 测试并跑到全绿，最后把用法记进 README。 |
| repo/baseline | `demos/fixtures/greenfield` · baseline `72d1ba19cb66`（1 个文件） |
| expected | pytest 退出码 0，且 `evaluate` 的四则语义与除零行为经独立脚本校验 |
| actual | `completed` · 判定 PASS（4/4） |
| turns / tokens | 8 轮 / 不适用（FakeLLM 不返回 usage） |
| tool_calls | 发起 9 次 · 执行 9 次，其中 is_error 0 次（0%） |
| 工具序列 | write_todos → write_file → write_file → write_file → run_tests → write_file → run_tests → write_file → write_todos |
| denied / repeated / stalled | 0 次被拒 · 0 次逐调用重复 · 0 轮整组重演 |
| 失败模式 | — |
| 上下文峰值 | 0 tokens |
| 权限模式 | `auto`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |
| wall time | 3.6s |
| trace | `demos/traces/codegen.fake.jsonl` |
| 工作副本 | `demos/.work/codegen`（判定就在这个目录跑） |

**说明** 脚本里第一版 `evaluate` 故意写成从左到右一路算，让测试去抓它 —— 这一步同时是 self-debug 的证据。

## 判定明细

- [x] pytest 退出码 0 —— 退出码 0，11 passed / 0 failed
- [x] 函数行为符合任务承诺（不依赖模型自述） —— 行为校验通过
- [x] 真的写了测试，不是空壳 —— 测试文件 1 个，收集到 11 个用例
- [x] README 写了用法 —— README.md

退出码判据：判定脚本在 `codegen` 里独立运行 pytest 与行为探测，不看模型最后那段话。

## 终端输出（原样）

```text
任务里有三件事都得落地：包、求值、测试。先列清单再动手。
✓ write_todos 4 步  0ms
任务清单
  [~] 建 calculator 包：四则运算 + DivideByZeroError
  [ ] 加 evaluate：乘除优先于加减
  [ ] 写 pytest 用例并跑绿
  [ ] 在 README 写清用法与不做什么
先建包：四个运算和表达式求值放 `calculator/core.py`，测试另放一处。
✓ write_file  calculator/__init__.py  2ms
✓ write_file  calculator/core.py  1ms
✓ write_file  tests/test_calculator.py  1ms
✓ run_tests     1.3s
`2 + 3 * 4` 得到 20，说明我按从左到右一路算了。改成两遍扫描：先折叠乘除，再算加减。
✓ write_file  calculator/core.py  1ms
✓ run_tests     1.2s
全绿。把用法和「不做括号」这条边界写进 README，省得后来人猜。
✓ write_file  README.md  12ms
✓ write_todos 4 步  0ms
任务清单
  [x] 建 calculator 包：四则运算 + DivideByZeroError
  [x] 加 evaluate：乘除优先于加减
  [x] 写 pytest 用例并跑绿
  [x] 在 README 写清用法与不做什么
计算器项目已建好并全绿：
- `calculator/core.py`：add/subtract/multiply/divide + `DivideByZeroError`，`evaluate()` 用两遍扫描实现乘除优先
- `tests/test_calculator.py`：覆盖四个运算、除零、浮点、优先级、同级左结合、非法输入
- `README.md`：用法与「不支持括号」这条边界

`run_tests` 退出码 0。中途 `evaluate` 第一版把优先级算成了从左到右，是测试把它抓出来的，不是我看出来的。
```

## 模型的最后一段话

计算器项目已建好并全绿：
- `calculator/core.py`：add/subtract/multiply/divide + `DivideByZeroError`，`evaluate()` 用两遍扫描实现乘除优先
- `tests/test_calculator.py`：覆盖四个运算、除零、浮点、优先级、同级左结合、非法输入
- `README.md`：用法与「不支持括号」这条边界

`run_tests` 退出码 0。中途 `evaluate` 第一版把优先级算成了从左到右，是测试把它抓出来的，不是我看出来的。

## diff：基线 → 运行后

```diff
--- a/README.md
+++ b/README.md
@@ -1,9 +1,24 @@
-# 新项目起点
+# calculator
 
-这个目录里目前只有说明文件，没有任何代码。按任务要求从零建立包、测试与用法说明。
+只依赖标准库的四则运算小模块，含一个不支持括号的表达式求值。
 
-## 环境约定
+```python
+from calculator import DivideByZeroError, divide, evaluate
 
-- Python 3.11+，只允许标准库，不引第三方依赖。
-- 测试放 `tests/`，用 `python -m pytest` 运行。
-- 结论文本里不要出现"应该能跑"，跑过再说。
+evaluate("2 + 3 * 4")   # 14.0
+evaluate("10 - 6 / 2")  # 7.0
+divide(1, 0)            # raises DivideByZeroError
+```
+
+## 规则
+
+- 乘除优先于加减，同级从左到右结合。
+- **不支持括号**，也不支持一元负号；出现无法解析的字符直接 `ValueError`。
+- 除数为零抛 `DivideByZeroError`（`ValueError` 的子类）。
+- 结果统一保留 10 位小数，所以 `add(0.1, 0.2) == 0.3`。
+
+## 跑测试
+
+```
+python -m pytest -q
+```
--- a/calculator/__init__.py
+++ b/calculator/__init__.py
@@ -0,0 +1,13 @@
+"""calculator —— 四则运算与无括号表达式求值。"""
+
+from .core import (
+    DivideByZeroError,
+    add,
+    divide,
+    evaluate,
+    multiply,
+    subtract,
+)
+
+__all__ = ["DivideByZeroError", "add", "subtract", "multiply", "divide", "evaluate"]
+__version__ = "0.1.0"
--- a/calculator/core.py
+++ b/calculator/core.py
@@ -0,0 +1,76 @@
+"""四则运算与表达式求值。"""
+
+from __future__ import annotations
+
+import re
+from typing import Any
+
+
+class DivideByZeroError(ValueError):
+    """除数为零。继承 ValueError，调用方按参数错误处理即可。"""
+
+
+def add(left: float, right: float) -> float:
+    return round(left + right, 10)
+
+
+def subtract(left: float, right: float) -> float:
+    return round(left - right, 10)
+
+
+def multiply(left: float, right: float) -> float:
+    return round(left * right, 10)
+
+
+def divide(left: float, right: float) -> float:
+    if right == 0:
+        raise DivideByZeroError("除数不能为 0")
+    return round(left / right, 10)
+
+
+_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?|[+\-*/]")
+
+
+def _tokenize(expression: str) -> list[str]:
+    raw = (expression or "").strip()
+    if not raw:
+        raise ValueError("表达式不能为空")
+    tokens = _TOKEN_RE.findall(raw)
+    if "".join(tokens) != re.sub(r"\s+", "", raw):
+        raise ValueError(f"无法解析表达式：{expression!r}")
+    return tokens
+
+
+def _apply(op: str, left: float, right: float) -> float:
+    if op == "+":
+        return add(left, right)
+    if op == "-":
+        return subtract(left, right)
+    if op == "*":
+        return multiply(left, right)
+    return divide(left, right)
+
+
+def evaluate(expression: str) -> float:
+    """两遍扫描：先折叠乘除，再从左到右做加减，所以 `2 + 3 * 4` 是 14。"""
+    tokens = _tokenize(expression)
+
+    folded: list[Any] = []
+    index = 0
+    while index < len(tokens):
+        token = tokens[index]
+        if token in {"*", "/"} and folded and index + 1 < len(tokens):
+            left = float(folded.pop())
+            index += 1
+            folded.append(_apply(token, left, float(tokens[index])))
+        else:
+            folded.append(token)
+        index += 1
+
+    if len(folded) % 2 == 0:
+        raise ValueError(f"表达式不合法：{expression!r}")
+
+    total = float(folded[0])
+    for position in range(1, len(folded), 2):
+        total = _apply(folded[position], total, float(folded[position + 1]))
+    return round(total, 10)
--- a/tests/test_calculator.py
+++ b/tests/test_calculator.py
@@ -0,0 +1,46 @@
+"""calculator 的行为测试。"""
+
+import pytest
+
+from calculator import (
+    DivideByZeroError,
+    add,
+    divide,
+    evaluate,
+    multiply,
+    subtract,
+)
+
+
+def test_four_operations():
+    assert add(2, 3) == 5
+    assert subtract(2, 3) == -1
+    assert multiply(2, 3) == 6
+    assert divide(6, 3) == 2
+
+
+def test_divide_by_zero_is_explicit():
+    with pytest.raises(DivideByZeroError):
+        divide(1, 0)
+
+
+def test_floats_stay_readable():
+    assert add(0.1, 0.2) == 0.3
+    assert multiply(1.5, 2) == 3.0
…（diff 共 177 行，已截断，完整差异见工作副本）
```
