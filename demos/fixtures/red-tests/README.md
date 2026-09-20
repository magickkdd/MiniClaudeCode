# cart —— 结算价格引擎（教学用最小实现）

一个购物车与订单结算模块。价格规则全部写在下面这份文档里，代码以文档为准。

## 结算规则

| 规则 | 取值 |
|---|---|
| 税率 `TAX_RATE` | 8%，**按折扣后的金额计** |
| 运费 | 小计 ≥ `100.00` 免运费，否则固定 `9.90`（空车也收，已知行为） |
| 优惠券 `WELCOME` | 5% off |
| 优惠券 `BULK10` | 10% off（满 10 件的批发券） |
| 未知优惠券 | 抛 `ValueError`，不静默打折 |

金额一律四舍五入到 2 位小数。`Order.total = subtotal - discount + tax + shipping`。

## 用法

```python
from cart import Cart, checkout

c = Cart()
c.add("mug", price=12.50, qty=10)
order = checkout(c, coupon="BULK10")
order.subtotal  # 125.00
order.discount  # 12.50
order.tax       # 9.00  —— 112.50 的 8%
order.shipping  # 0.0
order.total     # 121.50
```

## 约束

- `add()` 的 `price`、`qty` 必须为正数，否则 `ValueError`。
- 同一 SKU 重复加入会合并数量，不新增行。
- 本模块不碰数据库、不做并发保证。
