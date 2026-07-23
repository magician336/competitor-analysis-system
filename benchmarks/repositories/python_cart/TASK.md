# bench_003：购物车总价修复

修复 `cart.py` 中 `Cart.total()` 的金额计算。

## 接口约定

- `Cart.add(unit_price, quantity)` 添加商品行；
- `total(discount=...)` 中的 `discount` 是固定金额优惠；
- 空购物车返回精确的 `Decimal("0.00")`；
- 最终金额不得小于零；
- 所有金额按财务常用的四舍五入规则保留两位小数。

不得修改验收文件。应保持现有公开行为，并处理验收中的边界条件。
