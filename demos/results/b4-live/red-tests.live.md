# Demo 3 · 测试红了之后自动修到绿（验收项 A1 / self-debugging）

> 本文件由 `python demos/run_demo.py --demo red-tests --engine live` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。
> 引擎：真实端点 `agnes-2.5-flash`，会受模型随机性影响。

| 字段 | 值 |
|---|---|
| task | 这个仓库的测试现在是红的。找出原因、修好它、重跑测试直到全绿。注意：不要为了让测试通过而修改 tests/ 里的断言。 |
| repo/baseline | `demos/fixtures/red-tests` · baseline `be97c63545dc`（8 个文件） |
| expected | pytest 退出码 0；tests/ 逐字节未变；结算数值经独立脚本校验 |
| actual | `completed` · 判定 PASS（4/4） |
| turns / tokens | 5 轮 / 24,695 tokens |
| tool_calls | 9 次，其中 is_error 0 次（0%） |
| 工具序列 | run_tests → read_file → find_files → read_file → read_file → read_file → edit_file → edit_file → run_tests |
| denied / repeated / stalled | 0 次被拒 · 0 次逐调用重复 · 0 轮整组重演 |
| 失败模式 | — |
| 上下文峰值 | 5,977 tokens |
| 权限模式 | `auto`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |
| wall time | 13.9s |
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
✓ run_tests     1.1s
✓ read_file   README.md  2ms
✓ find_files  **/*.py  1ms
✓ read_file   tests/test_checkout.py  1ms
✓ read_file   cart/pricing.py  1ms
✓ read_file   cart/__init__.py  1ms
找到了两处 bug：

1. `COUPONS["BULK10"]` 写成了 `0.01`（1%），应该是 `0.10`（10%）
2. `checkout()` 中 tax 用了 `subtotal` 而不是 `taxable_amount`（折扣后金额）
✓ edit_file   cart/pricing.py  1ms
✓ edit_file   cart/pricing.py  2ms
✓ run_tests     872ms
全绿。

**修复内容（`cart/pricing.py`）：**
- `COUPONS["BULK10"]` 从 `0.01` 改为 `0.10`（README 明确写 10% off）
- `checkout()` 的税额计算从 `subtotal * TAX_RATE` 改为 `taxable_amount(subtotal, discount) * TAX_RATE`，按折扣后金额计 8% 税

两处均为 README 结算规则描述正确但代码实现错误，测试断言未改动。
```

## 模型的最后一段话

全绿。

**修复内容（`cart/pricing.py`）：**
- `COUPONS["BULK10"]` 从 `0.01` 改为 `0.10`（README 明确写 10% off）
- `checkout()` 的税额计算从 `subtotal * TAX_RATE` 改为 `taxable_amount(subtotal, discount) * TAX_RATE`，按折扣后金额计 8% 税

两处均为 README 结算规则描述正确但代码实现错误，测试断言未改动。

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
