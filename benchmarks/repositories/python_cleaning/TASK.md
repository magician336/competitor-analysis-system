# bench_006：CSV 清洗逻辑重构

在保持现有输出完全一致的前提下，消除 `csv_cleaner/customers.py` 与
`csv_cleaner/orders.py` 中重复的字段清洗逻辑。

## 验收重点

- 既有客户与订单清洗结果不得变化；
- 公共清洗逻辑应被两个入口复用；
- 公共函数参数和返回值具有类型标注；
- 包内依赖保持单向且不得产生循环导入。

不得修改 `checks.py`、黄金输出或 `validator.json`。
