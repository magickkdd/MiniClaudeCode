# Demo 3 · 测试红了之后自动修到绿（验收项 A1 / self-debugging）

> 本文件由 `python demos/run_demo.py --demo red-tests --engine live` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。
> 引擎：真实端点 `agnes-2.5-flash`，会受模型随机性影响。

| 字段 | 值 |
|---|---|
| task | 这个仓库的测试现在是红的。找出原因、修好它、重跑测试直到全绿。注意：不要为了让测试通过而修改 tests/ 里的断言。 |
| repo/baseline | `demos/fixtures/red-tests` · baseline `be97c63545dc`（8 个文件） |
| expected | pytest 退出码 0；tests/ 逐字节未变；结算数值经独立脚本校验 |
| actual | `completed` · 判定 PASS（4/4） |
| turns / tokens | 6 轮 / 36,041 tokens |
| tool_calls | 13 次，其中 is_error 0 次（0%） |
| 工具序列 | read_file → read_file → read_file → read_file → read_file → read_file → read_file → read_file → run_tests → read_file → edit_file → edit_file → run_tests |
| denied / repeated / stalled | 0 次被拒 · 0 次逐调用重复 · 0 轮整组重演 |
| 失败模式 | context_growth |
| 上下文峰值 | 7,542 tokens |
| 权限模式 | `auto`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |
| wall time | 31.9s |
| trace | `demos/traces/red-tests.live.jsonl` |
| 工作副本 | `demos/.work/red-tests`（判定就在这个目录跑） |

**说明** fixture 里埋了两个缺陷，第二个被第一个挡住：折扣改对之前，`assert order.tax == 9.0` 根本执行不到。所以「改一次跑一次」不是形式主义。

## 判定明细

- [x] 原本红的套件现在全绿 —— 基线 1 failed → 现在 17 passed，退出码 0
- [x] 结算数值与 README 一致（独立脚本校验） —— 结算规则与 README 一致
- [x] `tests/` 未被为了让测试变绿而改写 —— tests/ 原有文件逐字节未变
- [x] 改动确实落在 `cart/` —— 价格代码被修改过

退出码判据：判定脚本在 `red-tests` 里独立运行 pytest 与行为探测，不看模型最后那段话。

## 终端输出（原样）

```text
✓ read_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/red-tests/R…  3ms
✓ read_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/red-tests/c…  2ms
✓ read_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/red-tests/c…  2ms
✓ read_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/red-tests/c…  3ms
✓ read_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/red-tests/c…  4ms
✓ read_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/red-tests/t…  2ms
✓ read_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/red-tests/t…  2ms
✓ read_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/red-tests/t…  2ms
✓ run_tests   extra_args=['-v', '--tb=short'] timeout=60  1.2s
✓ read_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/red-tests/c…  1ms
两个 bugs in `cart/pricing.py`:

1. **Line 11**: `COUPONS` 中 `BULK10` 值是 `0.01`（1%），应为 `0.10`（10%）
2. **Line 86**: 税的计算用的是 `subtotal * TAX_RATE`（折扣前），应为 `taxable_amount(subtotal, discount) * TAX_RATE`（折扣后）
✓ edit_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/red-tests/c…  4ms
✓ edit_file   D:/Embodied Agent/Agent-JD/mini-claude-code/demos/.work/red-tests/c…  3ms
✓ run_tests   extra_args=['-v', '--tb=short'] timeout=60  1.3s
全部 17 个测试通过。

**修复了两处 bug（`cart/pricing.py`）：**
1. `BULK10` 优惠券比例从 `0.01`（1%）改为 `0.10`（10%）
2. `checkout()` 中税额改为按折扣后的金额计算：`taxable_amount(subtotal, discount) * TAX_RATE`，而非折扣前
```

## 模型的最后一段话

全部 17 个测试通过。

**修复了两处 bug（`cart/pricing.py`）：**
1. `BULK10` 优惠券比例从 `0.01`（1%）改为 `0.10`（10%）
2. `checkout()` 中税额改为按折扣后的金额计算：`taxable_amount(subtotal, discount) * TAX_RATE`，而非折扣前

## diff：基线 → 运行后

```diff
--- a/cart/pricing.py
+++ b/cart/pricing.py
@@ -9,5 +9,5 @@
 FREE_SHIPPING_FROM = 100.00
 
-COUPONS = {"BULK10": 0.01, "WELCOME": 0.05}
+COUPONS = {"BULK10": 0.10, "WELCOME": 0.05}
 
 
@@ -84,5 +84,5 @@
     subtotal = cart.subtotal
     discount = discount_for(subtotal, coupon)
-    tax = round(subtotal * TAX_RATE, 2)
+    tax = round(taxable_amount(subtotal, discount) * TAX_RATE, 2)
     return Order(
         subtotal=subtotal,
```
