# bench_014：拆分订单规则职责

`OrderService.java` 当前把校验、折扣计算和通知混在一个方法中。保持全部可观察行为不变，将职责拆分为可独立测试的组件。

## 必须保持的行为

- 负金额或空邮箱的订单被拒绝，且不能发送通知；
- 学生订单金额达到 10000 分时享受 10% 折扣，其他订单不打折；
- 成功订单只发送一次通知；
- `OrderService(NotificationPort)` 这一既有入口继续可用；
- `OrderServiceTest` 的黄金样例全部通过。

## 目标结构

- `OrderValidator.java`：提供 `validate(Order)`；
- `DiscountPolicy.java`：提供 `discountedTotal(Order)`；
- `OrderNotifier.java`：提供 `notifyPlaced(Order, OrderResult)`；
- `OrderService` 组合以上职责，不再直接包含校验、折扣和通知细节；
- 不得引入循环依赖或第三方库。

不得修改 `OrderServiceTest.java`、`validate.py` 或 `validator.json`。验证器先执行行为测试，再检查职责拆分；starter 行为正确，但会因仍是单体实现而失败。

