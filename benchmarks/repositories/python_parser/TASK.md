# bench_008：为键值行解析器生成测试

只修改 `test_generated.py`，为 `parser.parse_kv_line(line)` 编写 pytest 测试。

## 冻结接口

- 输入是由英文逗号分隔的 `key=value` 项；键和值两侧空白应被去除；
- 空行返回空字典；
- 缺少等号、空键、空项目或重复键应抛出 `ValueError`；
- 非字符串输入应抛出 `TypeError`；
- Unicode 键和值必须原样保留；值内部允许再出现等号。

## 验收

验证器先确认测试在 `parser.py` 基准实现上通过，再对 5 个冻结缺陷变体逐一运行。至少杀死 80% 的变体，且测试文件必须稳定、可独立执行。

不得修改 `parser.py`、`validator_assets/`、`validate.py` 或 `validator.json`。固定变体仅用于离线可复现验收，不代表全部真实缺陷。

