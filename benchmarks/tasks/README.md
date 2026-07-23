# CodeRadar 第三周基准任务协议

`tasks.jsonl` 固定包含 16 个 `week3-frozen-v2` 任务，覆盖 8 类任务，每类 2 个：函数补全、编译修复、测试修复、多文件修改、重构、代码解释、测试生成和安全审查。每项均已有 `../repositories/` 下的 starter、任务说明和版本化验证器。

## 可复现元数据

每条任务除题目、语言、难度和仓库路径外，还显式记录：

- `success_criteria`：可逐项判定的成功标准；
- `validation_method`：自动命令、人工量表或二者组合；
- `fairness_constraints`：统一起始材料、环境、交互轮次和人工干预边界；
- `task_revision`：单条任务修订版本；
- `protocol_version`：整套执行协议版本。

加载后的任务模型还会根据规范化元数据生成 `task_fingerprint`（SHA-256）。

如果调用方提交了 `task_fingerprint`，但任务内容已变化，Pydantic 校验会拒绝该任务，从而避免使用同一任务 ID 混合不同实验条件。

## 手工执行要求

1. 每个产品从同一冻结起始仓库开始，使用任务记录的验证方式。
2. 记录产品版本、模型、时间、交互轮次、延迟、人工干预和估算成本。
3. 不修改固定测试、黄金输出或攻击样例；不可配置的模型参数必须如实记录。
4. 结果写入 `../results/manual_runs.csv` 的字段格式；`run_id` 在一次实验和持久化文件中必须唯一。`sample_runs.csv` 仅演示格式，禁止进入正式比较。
5. 解释和教育类任务按冻结量表盲评；安全类任务只能在隔离沙箱执行。

`BenchmarkAgent.import_runs(..., persist=True)` 与 `import_csv(..., persist=True)` 会逐行隔离错误、校验任务 ID、去除重复 run，并通过同目录临时文件原子替换结果文件。默认 `idempotent=True`，重复导入同一 `run_id` 不会重复计入比较结果。

## 统一审计与验证

从项目根目录运行：

```powershell
python -m benchmarks.validators.run_task --audit
python -m benchmarks.validators.run_task --task-id bench_001
python -m benchmarks.validators.run_task --task-id bench_001 --candidate C:\path\candidate
```

Runner 只接受冻结 `validator.json` 中的命令参数数组，使用 `shell=False`，并输出包含任务指纹、验证器版本、协议摘要、受保护资产 SHA-256、退出码、耗时和日志的 JSON。候选的受保护文件缺失、被替换、变成目录或符号链接时会在启动子进程前失败；候选进程只获得最小化环境和一次性用户/临时目录。原始 starter 预期因目标缺陷返回非零；只有修复后的候选仓库才应通过。

上述机制用于保护实验协议，**不等同于操作系统沙箱**，也不限制候选代码的网络和文件系统能力。任何不可信候选都必须在一次性、最小权限、禁网或受控网络的容器/虚拟机内运行。

解释类任务在自动门槛通过后，仍须由 A/B 两名匿名评审使用各自模板打分，并按 `merge_rule.json` 处理分歧。C/C++/Java 任务分别需要 GCC、CMake+C++17 和 JDK 17+；工具缺失应记作环境错误，不得计为产品失败。
