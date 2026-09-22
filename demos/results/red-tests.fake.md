# Demo 3 · 测试红了之后自动修到绿（验收项 A1 / self-debugging）

> 本文件由 `python demos/run_demo.py --demo red-tests --engine fake` 生成。表格里的数字来自 trace 与判定脚本，不是手填的。
> 引擎：FakeLLM 脚本 + 真工具真落盘：证明工程闭环，不证明模型能力。

| 字段 | 值 |
|---|---|
| task | 这个仓库的测试现在是红的。找出原因、修好它、重跑测试直到全绿。注意：不要为了让测试通过而修改 tests/ 里的断言。 |
| repo/baseline | `demos/fixtures/red-tests` · baseline `be97c63545dc`（8 个文件） |
| expected | pytest 退出码 0；tests/ 逐字节未变；结算数值经独立脚本校验 |
| actual | `completed` · 判定 PASS（4/4） |
| turns / tokens | 10 轮 / 不适用（FakeLLM 不返回 usage） |
| tool_calls | 发起 9 次 · 执行 9 次，其中 is_error 0 次（0%） |
| 工具序列 | write_todos → run_tests → search_text → read_file → edit_file → run_tests → edit_file → run_tests → write_todos |
| denied / repeated / stalled | 0 次被拒 · 0 次逐调用重复 · 0 轮整组重演 |
| 失败模式 | — |
| 上下文峰值 | 0 tokens |
| 权限模式 | `auto`（工作副本在临时目录里，AUTO 不等于对用户仓库放开） |
| wall time | 10.3s |
| trace | `demos/traces/red-tests.fake.jsonl` |
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
✓ write_todos 3 步  0ms
任务清单
  [~] 跑一遍测试，拿到失败清单
  [ ] 定位失败断言对应的代码
  [ ] 改代码（不动 tests/）并重跑到全绿
先以退出码为准，看现在到底红在哪。
✓ run_tests     3.4s
折扣是 1.25 而不是 12.5，等于少乘了 10 倍 —— 先找 BULK10 定义在哪。
✓ search_text BULK10  18ms
✓ read_file   cart/pricing.py  3ms
README 写的是 10% off，常量表里写成 0.01。改这里，不动断言。
✓ edit_file   cart/pricing.py  2ms
✓ run_tests     3.1s
折扣修好后同一个用例继续失败：tax=10.00，期望 9.00。README 说税按折扣后金额计，而 checkout() 直接乘在小计上 —— 旁边那个 taxable_amount() 就是为这件事写的，只是没被调用。
✓ edit_file   cart/pricing.py  5ms
✓ run_tests     2.4s
✓ write_todos 3 步  1ms
任务清单
  [x] 跑一遍测试，拿到失败清单
  [x] 定位失败断言对应的代码
  [x] 改代码（不动 tests/）并重跑到全绿
全绿，`run_tests` 退出码 0，`tests/` 一行没动。

两个缺陷，都在 `cart/pricing.py`：
1. `COUPONS` 里 BULK10 写成 0.01，与 README 承诺的 10% 差 10 倍 → 改为 0.10；
2. `checkout()` 的税基用了未扣折扣的小计，而 `taxable_amount()` 定义了却没被调用 → 改成对折扣后的金额计税。

第 2 条是被第 1 条挡住的：折扣错时 `assert order.discount == 12.5` 先失败，修完才轮到 `assert order.tax == 9.0`。所以这里必须改一次跑一次，一次看完是看不全的。
```

## 模型的最后一段话

全绿，`run_tests` 退出码 0，`tests/` 一行没动。

两个缺陷，都在 `cart/pricing.py`：
1. `COUPONS` 里 BULK10 写成 0.01，与 README 承诺的 10% 差 10 倍 → 改为 0.10；
2. `checkout()` 的税基用了未扣折扣的小计，而 `taxable_amount()` 定义了却没被调用 → 改成对折扣后的金额计税。

第 2 条是被第 1 条挡住的：折扣错时 `assert order.discount == 12.5` 先失败，修完才轮到 `assert order.tax == 9.0`。所以这里必须改一次跑一次，一次看完是看不全的。

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
