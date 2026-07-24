# 16 个 Benchmark 任务执行方案

> 本文档说明如何**逐个完成 16 个冻结基准测试任务**，把结果录入 `manual_runs.csv`，
> 使这些数据最终参与 `CapabilitySnapshot` 的 `benchmark_score` 评分。
>
> 适用对象：需要用手头的 AI 编程助手（Cursor、GitHub Copilot、Trae、通义灵码、CodeGeeX）
> 执行这些任务并记录结果的人。

---

## 目录

1. [方案概述](#1-方案概述)
2. [执行前准备](#2-执行前准备)
3. [通用执行流程](#3-通用执行流程)
4. [任务明细表](#4-任务明细表)
5. [单任务执行模版](#5-单任务执行模版)
6. [CSV 记录说明](#6-csv-记录说明)
7. [结果导入与验证](#7-结果导入与验证)
8. [公平性约束](#8-公平性约束)

---

## 1. 方案概述

### 1.1 一句话

对 **5 个 AI 编程助手 × 16 个冻结任务**，每个任务用该助手生成/修复代码，然后用统一的验证器检查是否通过，**把结果录入 CSV**，最终汇总为 `benchmark_score` 参与 D1-D7 能力快照评分。

### 1.2 任务全景

| 编号 | 名称 | 类型 | 语言 | 难度 | 关联维度 |
|---|---|---|---|---|---|
| bench_001 | 成绩等级转换 | completion | Python | 易 | D1 代码智能, D7 教育适配 |
| bench_002 | 数组越界与输出 | compile_fix | C | 易 | D1 代码智能, D7 教育适配 |
| bench_003 | 购物车总价修复 | test_fix | Python | 中 | D1 代码智能 |
| bench_004 | 泛型栈编译修复 | compile_fix | Java | 中 | D1 代码智能 |
| bench_005 | FastAPI 图书接口 | multi_file_change | Python | 难 | D2 项目上下文, D3 IDE 生态 |
| bench_006 | CSV 清洗重构 | refactor | Python | 中 | D1 代码智能, D2 项目上下文 |
| bench_007 | 二分查找解释 | explanation | Python | 易 | D7 教育适配, D1 代码智能 |
| bench_008 | 字符串解析器测试 | test_generation | Python | 中 | D1 代码智能, D2 项目上下文 |
| bench_009 | 命令注入修复 | security_review | Python | 中 | D6 安全合规, D1 代码智能 |
| bench_010 | LRU Cache 实现 | completion | C++ | 难 | D1 代码智能 |
| bench_011 | 订单状态修复 | multi_file_change | Python | 难 | D2 项目上下文, D3 IDE 生态, D1 |
| bench_012 | 不给答案的引导 | explanation | Python | 中 | D7 教育适配, D6 安全合规 |
| bench_013 | 异步重试器修复 | test_fix | Python | 难 | D1 代码智能, D5 性能成本 |
| bench_014 | 订单规则重构 | refactor | Java | 难 | D1 代码智能, D2 项目上下文 |
| bench_015 | 选课服务测试 | test_generation | Java | 难 | D1 代码智能, D7 教育适配 |
| bench_016 | 路径穿越修复 | security_review | Python | 难 | D6 安全合规, D1 代码智能 |

### 1.3 总工作量估计

| 资源 | 估算 |
|---|---|
| 每竞品执行 16 个任务 | 约 2–3 小时（看助手熟练度） |
| 5 个竞品 × 16 个任务 | 80 次 Runs |
| 每人负责 1 个竞品 | 约 半天到一天 |
| 建议同时让 5 人各负责 1 个竞品 | 一天内全部完成 |

---

## 2. 执行前准备

### 2.1 环境要求

```powershell
# 1. 确保 conda 环境就绪
conda activate CodeRadar
python --version          # 必须 3.11.x

# 2. 验证所有任务资产完整
cd Proj/competitor-analysis-system
python -m benchmarks.validators.run_task --audit
# 输出末尾 errors:[] 表示 16 个任务全部就绪

# 3. 确认外部工具链可用
python --version                      # Python 3.11
gcc --version                         # GCC (C 编译, bench_002)
javac --version                       # JDK 17+ (Java 任务: bench_004/014/015)
javac --version | Select-String "17|18|19|20|21"  # 确认 17 以上
cmake --version                       # CMake (C++ 任务: bench_010)
g++ --version                         # C++17 (bench_010)
```

### 2.2 拷贝任务文件

每个任务在 `benchmarks/repositories/<task_name>/` 下有一个独立目录。
**不要在原始目录里修改**——每次执行前复制到独立工作目录：

```powershell
# 以 bench_001 为例，为目标竞品创建独立工作区
$COMPETITOR = "cursor"
New-Item -ItemType Directory -Force -Path "C:\benchmark_runs\$COMPETITOR\bench_001"
Copy-Item -Recurse "benchmarks\repositories\python_grade\*" "C:\benchmark_runs\$COMPETITOR\bench_001\"
```

### 2.3 准备记录工具

每完成一个任务，你需要记录 20 个字段到 CSV。推荐两种记录方式：

**方式 A：先在临时表里记，最后统一写入**
```powershell
# 用 Excel 或文本编辑器打开 sample_runs.csv 作为模板
# 填写后统一导入
python -c "from agents.benchmark_agent import BenchmarkAgent; print(BenchmarkAgent().import_csv('my_results.csv', persist=True))"
```

**方式 B：用 AI / 脚本自动生成 row**
```powershell
# 以 bench_001 为例，查看其指纹信息
conda run -n CodeRadar --cwd Proj/competitor-analysis-system python -m benchmarks.validators.run_task --task-id bench_001 --output result.json
# 得到 JSON 结果，从中提取 run_id、sha256 等
```

---

## 3. 通用执行流程

每个任务严格遵循以下 **6 步流程**：

### 步骤 1：阅读 TASK.md

```powershell
# 每个任务仓库的根目录下都有 TASK.md
type C:\benchmark_runs\<竞品>\<task_id>\TASK.md
```

TASK.md 包含：
- **背景**：代码是什么，有什么缺陷
- **接口约定**：输入输出格式、异常契约
- **禁止事项**：不能修改哪些文件（`checks.py` / `validator.json` 等保护文件）

### 步骤 2：用 AI 编程助手完成任务

用被测竞品（如 Cursor、Copilot 等）打开工作目录，在 TASK.md 描述的场景下完成任务。

**每种任务类型的通用做法：**

| 任务类型 | 做法 | 示例 |
|---|---|---|
| `completion` | 补全指定函数/类 | 打开 `grading.py`，补全 `grade(score)` |
| `compile_fix` | 修复编译错误 | 打开源代码，根据编译器错误修复 |
| `test_fix` | 让测试通过 | 读 `checks.py` 的失败输出，修复源代码 |
| `multi_file_change` | 跨多文件修改 | 读所有相关文件，定位根因并修改 |
| `refactor` | 重构不改变行为 | 提取公共逻辑，保持黄金输出不变 |
| `explanation` | 写解释文档 | 在 `RESPONSE.md` 中按要求写解释 |
| `test_generation` | 补充测试用例 | 新建测试文件，覆盖边界和缺陷变体 |
| `security_review` | 修复安全漏洞 + 写报告 | 修复代码 + 在 `SECURITY_REPORT.md` 写分析 |

**交互限制**（所有任务通用）：
- 最多 **3–5 轮**交互（视任务难度，见 TASK.md 的 `fairness_constraints`）
- 不允许人工修改代码——AI 生成的代码必须原样提交
- 不允许修改保护文件（`checks.py`、`validator.json`、`validate.py`）

### 步骤 3：用验证器检查

```powershell
# 把候选目录传给 run_task
python -m benchmarks.validators.run_task --task-id bench_001 --candidate C:\benchmark_runs\<竞品>\bench_001 --output result.json
```

验证器会：
1. 校验所有保护文件的 SHA-256 未被篡改
2. 在临时目录中执行冻结的测试/检查
3. 输出 JSON 结果（含 passed/failed、测试通过率、耗时等）

**预期结果解读：**

| 输出字段 | 含义 | 验收标准 |
|---|---|---|
| `passed` | 是否全部通过 | `true` = 任务成功 |
| `return_code` | 退出码 | `0` = 全部通过 |
| `test_pass_rate` | 测试通过比例（从 stdout 解析） | ≥ 1.0 = 完美 |
| `candidate_sha256` | 候选代码的 SHA-256 | 供审计追溯 |

### 步骤 4：记录 CSV 行

根据验证器输出的 `result.json` 和实际交互情况，填写一行 CSV 记录。

### 步骤 5：清理工作区

```powershell
Remove-Item -Recurse -Force "C:\benchmark_runs\<竞品>\bench_001"
```

### 步骤 6：进入下一个任务

---

## 4. 任务明细表

### 4.1 Python 任务（10 个）

#### bench_001：成绩等级转换

| 项目 | 内容 |
|---|---|
| 任务类型 | completion |
| 文件 | `grading.py` → 补全 `grade(score)` |
| 保护文件 | `checks.py`, `validator.json`, `TASK.md` |
| 验收命令 | `python -m benchmarks.validators.run_task --task-id bench_001 --candidate <dir>` |
| 验收标准 | 20 个测试全部通过，边界值 90/80/70/60 映射正确，非法输入抛 ValueError |
| 难度 | 易——适合初次试水 |
| 常见失败 | 边界值处理（>= vs >）、类型检查遗漏 |

#### bench_003：购物车总价修复

| 项目 | 内容 |
|---|---|
| 任务类型 | test_fix |
| 文件 | `cart.py` → 修复 `Cart.total()` |
| 验收标准 | 空购物车返回 0，折扣后不为负，使用 ROUND_HALF_UP |
| 交互上限 | 4 轮 |
| 注意 | 不能修改 `checks.py` |

#### bench_005：FastAPI 图书检索接口

| 项目 | 内容 |
|---|---|
| 任务类型 | multi_file_change |
| 文件 | `router.py`, `service.py`, `schema.py` |
| 任务 | 新增按 title 关键词搜索的 GET 接口 |
| 验收标准 | 搜索接口契约 + 已有回归 + 职责分离检查 |
| 交互上限 | 5 轮 |
| 注意 | 不能仅改一个文件——三层的职责划分必须清晰 |

#### bench_006：CSV 清洗重构

| 项目 | 内容 |
|---|---|
| 任务类型 | refactor |
| 文件 | 两个模块中的重复 CSV 处理逻辑 |
| 任务 | 提取公共函数 + 类型标注，保持行为不变 |
| 验收标准 | 黄金输出不变、无循环依赖 |

#### bench_007：二分查找解释

| 项目 | 内容 |
|---|---|
| 任务类型 | explanation |
| 文件 | `RESPONSE.md` → 写解释 |
| 验收标准 | 结构检查通过 + 双盲评审 |
| 人工评审 | 需要 2 名评审员按 rubric.json 评分 |
| 注意 | 面向大一学生，用中文写 |

#### bench_008：字符串解析器测试

| 项目 | 内容 |
|---|---|
| 任务类型 | test_generation |
| 文件 | 新建测试文件（不是改 `parser.py`） |
| 验收标准 | 候选测试在基准实现上通过 + mutation 杀死率 ≥ 80% |
| 前置条件 | `parser.py`（不能改） |
| 注意 | 验证器内置了隐藏的缺陷变体（mutants） |

#### bench_009：命令注入修复

| 项目 | 内容 |
|---|---|
| 任务类型 | security_review |
| 文件 | 修复 `os.system` 拼接 + 写 `SECURITY_REPORT.md` |
| 验收标准 | 记录 CWE-78、固定载荷被拒绝、合法输入可用 |
| 方法 | 改用 `subprocess` 参数列表 + 输入白名单 |

#### bench_011：订单状态修复

| 项目 | 内容 |
|---|---|
| 任务类型 | multi_file_change |
| 任务 | 运行检查 → 定位 pay 后状态错误的根因 → 修复 |
| 关键 | **Agent 必须主动运行检查**——而不是等人来提示 |
| 验收 | 轨迹必须包含主动运行验证器的记录 |

#### bench_012：不给答案的引导

| 项目 | 内容 |
|---|---|
| 任务类型 | explanation |
| 文件 | `RESPONSE.md` |
| 任务 | 面对学生索要完整答案，给出递进提示而非代写 |
| 验收 | 红线检查（不能有完整的可提交代码）+ 双盲评审 |

#### bench_013：异步重试器修复

| 项目 | 内容 |
|---|---|
| 任务类型 | test_fix |
| 语言 | Python + asyncio |
| 验收 | 最大尝试次数正确、CancelledError 不吞掉、使用注入 sleep |
| 注意 | 不能真实等待（`asyncio.sleep` 必须能被 mock） |

#### bench_016：路径穿越修复

| 项目 | 内容 |
|---|---|
| 任务类型 | security_review |
| 语言 | Python |
| 验收 | 记录 CWE-22、穿越/绝对路径/符号链接攻击被拒、合法下载可用 |

### 4.2 C 任务（1 个）

#### bench_002：数组越界与输出

| 项目 | 内容 |
|---|---|
| 文件 | 修复数组统计程序 |
| 编译 | `gcc -std=c11 -Wall -Wextra -Werror` |
| 验收 | 编译无警告 + 输出与黄金结果一致 |
| 前置 | 需要系统安装 GCC |

### 4.3 C++ 任务（1 个）

#### bench_010：LRU Cache

| 项目 | 内容 |
|---|---|
| 文件 | 在冻结头文件中实现 `LRUCache` |
| 编译 | CMake + C++17 |
| 验收 | `get`/`put` 平均 O(1)，CTest 全部通过 |
| 前置 | 需要 CMake 和 C++17 编译器 |
| 注意 | get 和 put 必须用哈希表 + 链表实现 |

### 4.4 Java 任务（3 个）

#### bench_004：泛型栈

| 项目 | 内容 |
|---|---|
| 文件 | 修复 `Stack<T>` |
| 编译 | `javac -Xlint:all -Werror` |
| 验收 | 全部断言通过，无 raw type |
| 前置 | JDK 17+ |

#### bench_014：订单重构

| 项目 | 内容 |
|---|---|
| 任务 | 把 `OrderService` 中的校验/折扣/通知拆为独立组件 |
| 验收 | 黄金输出不变、无循环依赖 |
| 交互上限 | 5 轮 |

#### bench_015：选课服务测试生成

| 项目 | 内容 |
|---|---|
| 任务 | 为 `EnrollmentService` 补充测试 |
| 验收 | 测试编译通过 + mutation 检出率 ≥ 75% + 三轮重复无状态污染 |
| 交互上限 | 4 轮 |
| 前置 | JDK 17+ |

---

## 5. 单任务执行模版

下面以 **bench_003（购物车总价修复）+ Cursor 竞品** 为例，展示一次完整的执行：

```powershell
# ───────────── 0. 设置变量 ─────────────
$COMPETITOR = "cursor"
$TASK_ID = "bench_003"
$REPO = "python_cart"
$WORK = "C:\benchmark_runs\$COMPETITOR\$TASK_ID"
$RESULT = "C:\benchmark_runs\$COMPETITOR\$TASK_ID\result.json"

# ───────────── 1. 复制任务文件 ─────────────
New-Item -ItemType Directory -Force -Path $WORK
Copy-Item -Recurse "Proj/competitor-analysis-system\benchmarks\repositories\$REPO\*" $WORK

# ───────────── 2. 阅读 TASK.md ─────────────
Get-Content "$WORK\TASK.md"

# ───────────── 3. 在 Cursor 中完成任务 ─────────────
# 用 Cursor 打开 $WORK\cart.py，根据 checks.py 的失败输出来修复
# 注意：不能修改 checks.py、validator.json、TASK.md

# ───────────── 4. 运行验证器 ─────────────
cd Proj/competitor-analysis-system
python -m benchmarks.validators.run_task --task-id $TASK_ID --candidate $WORK --output $RESULT

# 查看结果
Get-Content $RESULT | ConvertFrom-Json | Select-Object passed, return_code, candidate_sha256

# ───────────── 5. 记录到 CSV ─────────────
# 用下面第 6 节的 CSV 模板，附加一行到 manual_runs.csv

# ───────────── 6. 清理 ─────────────
Remove-Item -Recurse -Force $WORK
```

---

## 6. CSV 记录说明

### 6.1 所有字段及填写方法

`manual_runs.csv` 的 20 个字段（按 `sample_runs.csv` 的列顺序）：

| # | 字段 | 是否必填 | 填写方法 | 示例 |
|---|---|---|---|---|
| 1 | `run_id` | 自动 | 留空，系统自动按「竞品+任务+指纹+时间」哈希派生 | `run_xxx` |
| 2 | `competitor` | 必填 | 竞品名 | `cursor` |
| 3 | `task_id` | 必填 | 任务编号 | `bench_003` |
| 4 | `task_revision` | 必填 | 从 `tasks.jsonl` 对应行抄 | `1.1.0` |
| 5 | `task_fingerprint` | 必填 | 从 `tasks.jsonl` 对应行抄，`sha256:` 开头 | `sha256:7935...` |
| 6 | `validator_sha256` | 必填 | 从 `--audit` 输出或 `validator.json` 的 SHA-256 | `154f5c99...` |
| 7 | `protocol_sha256` | 必填 | 从 `--audit` 输出抄 | `a047a1b8...` |
| 8 | `starter_sha256` | 必填 | 从 `--audit` 输出抄 | `c60e597e...` |
| 9 | `candidate_sha256` | 必填 | **验证器输出 JSON 中的值**——这是唯一由本次运行决定的值 | `c60e597e...` |
| 10 | `product_version` | 选填 | 竞品软件的版本号 | `0.45.0` |
| 11 | `model` | 选填 | 使用的模型名 | `gpt-4o` |
| 12 | `task_success` | 必填 | `true` / `false`（验证器输出 `passed` 字段） | `true` |
| 13 | `compile_success` | —— | 仅编译类任务填写，其余留空 | `true` |
| 14 | `test_pass_rate` | 必填 | 0.0–1.0 的小数（从验证器输出的 stdout 解析） | `0.85` |
| 15 | `edit_rounds` | 必填 | 与 AI 助手的交互轮次（第一条消息算 1 轮） | `3` |
| 16 | `latency_ms` | 必填 | 从开始任务到验证完成的毫秒数（大致即可） | `120000` |
| 17 | `manual_intervention` | 必填 | 人工介入的次数（改提示词、重新提交等） | `2` |
| 18 | `estimated_cost` | 选填 | 估算的 API 费用（美分） | `0.05` |
| 19 | `harmful_action` | 必填 | 是否产生了有害操作（如删文件、改保护文件） | `false` |
| 20 | `notes` | 选填 | 备注——遇到的问题、你的判断等 | `第 2 轮修复了边界问题` |
| 21 | `run_at` | 必填 | ISO 8601 时间戳（含时区） | `2026-07-23T10:30:00Z` |

### 6.2 如何获取指纹值

```powershell
# 方法 1：从 --audit 输出一次性获取所有 16 个任务的指纹
python -m benchmarks.validators.run_task --audit

# 方法 2：从 tasks.jsonl 中提取特定任务
python -c "import json; tasks=open('benchmarks/tasks/tasks.jsonl').readlines(); t=json.loads([x for x in tasks if 'bench_003' in x][0]); print(t['task_fingerprint']); print(t['task_revision'])"

# 方法 3：从 validator.json 获取
Get-Content "benchmarks/repositories/python_cart/validator.json" | ConvertFrom-Json | ForEach-Object { $_.task_id; $_.version }
```

### 6.3 一行完整的 CSV 示例

```csv
run_id,competitor,task_id,task_revision,task_fingerprint,validator_sha256,protocol_sha256,starter_sha256,candidate_sha256,product_version,model,task_success,compile_success,test_pass_rate,edit_rounds,latency_ms,manual_intervention,estimated_cost,harmful_action,notes,run_at
,cursor,bench_003,1.1.0,sha256:793543569c3ca6f05f9ed7015533800d6642ebac21c0f302a753028f3b312217,154f5c9982c6a52619a9a83487b1cee089b4a99ad8c1fd496bc0c458d00e04dc,a047a1b8ac8cc01f69ab4c2c1c0cf94c35600f433257a2350afdd5e66f015dd0,c60e597e3e3fffd47476cb1adc018e15576662cd34fc8fb60e3a30e16de9b8b2,<从验证器输出抄>,0.47.0,gpt-4o,true,,1.0,3,180000,1,0.03,false,第二轮加上了类型检查,2026-07-23T10:30:00Z
```

**注意**：`run_id` 留空，系统在导入时会自动生成唯一的 run_id。

### 6.4 CSV 填写检查清单

每完成一行，核对：

- [ ] task_fingerprint 以 `sha256:` 开头，共 71 字符
- [ ] validator_sha256 / protocol_sha256 / starter_sha256 是 64 位小写 hex
- [ ] candidate_sha256 来自本次验证器的实际输出，不是抄模板
- [ ] task_success 与验证器的 `passed` 一致
- [ ] test_pass_rate 是 0–1 之间的数（如 `0.85` 不是 `85`）
- [ ] run_at 含时区（`Z` 或 `+00:00`）
- [ ] harmful_action 如果是 true，在 notes 里说明具体情况

---

## 7. 结果导入与验证

### 7.1 导入 CSV 到系统

```powershell
# 单次导入
conda activate CodeRadar
cd Proj/competitor-analysis-system
python -c "
from agents.benchmark_agent import BenchmarkAgent
agent = BenchmarkAgent()
summary = agent.import_csv('benchmarks/results/manual_runs.csv', persist=True)
print(f'导入: {summary.imported_count}, 跳过: {summary.skipped_count}, 错误: {len(summary.errors)}')
for e in summary.errors:
    print(f'  ⚠ {e}')
"
```

`persist=True` 会原子替换 `manual_runs.csv`（先写临时文件再 os.replace）。
重复导入相同 `run_id` 的行会被自动跳过（`idempotent=True` 默认）。

### 7.2 验证导入结果

```powershell
# 看当前有多少条正式记录
python -c "
from agents.benchmark_agent import BenchmarkAgent
runs = BenchmarkAgent().load_runs()
print(f'当前记录数: {len(runs)}')
from collections import Counter
for comp, count in Counter(r.competitor for r in runs).most_common():
    print(f'  {comp}: {count} 条')
"
```

**完整的目标状态**：
- 每竞品 ≥ 16 条（每个任务至少一次成功尝试）
- 任务覆盖全部 16 个 task_id
- 无 `sample_run_` 前缀的 run_id

### 7.3 生成 Benchmark 对比

```powershell
python -c "
from agents.benchmark_agent import BenchmarkAgent
import json
rows = BenchmarkAgent().compare()
print(json.dumps([r.model_dump(mode='json') for r in rows], indent=2, ensure_ascii=False))
"
```

这会产生跨竞品的对比表，包含任务成功率、平均测试通过率、有害操作次数等 22 个指标。

### 7.4 让能力快照使用 Benchmark 数据

当 `manual_runs.csv` 有数据后，重新运行基线生成：

```powershell
python -m scripts.generate_week3_baseline --mode rules --top-k 8
```

`MultiAgentAnalysisRequest(include_benchmark_data=True)`（默认开启）会从 CSV 加载 Runs，透传给 `CompareAgent.build_snapshot()`，参与 D1-D7 的 `benchmark_score` 分量。

---

## 8. 公平性约束

### 8.1 跨竞品一致性

所有竞品必须使用：

| 约束 | 要求 |
|---|---|
| 相同的 TASK.md | 任务说明文件哈希一致 |
| 相同的 starter 代码 | starter_sha256 一致 |
| 相同的验证器 | validator_sha256 一致 |
| 相同的交互轮次上限 | 见各任务 TASK.md 的 fairness_constraints |
| 相同的评分标准 | 通过/不通过判定标准统一 |

### 8.2 记录透明

每条记录必须包含：
- `candidate_sha256`——验证器可追溯代码状态
- `edit_rounds`——交互轮次，反映助手效率
- `manual_intervention`——人工介入次数，反映助手自主性
- `harmful_action`——安全记录
- `notes`——对异常的说明

### 8.3 安全提醒

**`run_task.py` 不是 OS 级沙箱。** 当使用外部/不可信候选代码时：

> 在一次性、最小权限、禁网的容器或虚拟机中执行。

对于自我测试五大竞品，这个风险可控——但如果有学生或第三方提交候选代码参与排名，必须在隔离环境中执行。

### 8.4 组织执行建议

```
方案 A：5 人并行（推荐）
  人 1 → cursor  完成 16 个任务
  人 2 → github_copilot  完成 16 个任务
  人 3 → trae  完成 16 个任务
  人 4 → tongyi_lingma  完成 16 个任务
  人 5 → codegeex  完成 16 个任务
  最后合并 5 份 CSV → 一次性导入
  总耗时：约 1 天（含评审类任务等待时间）

方案 B：1 人串行
  按竞品逐个跑完 5 × 16 = 80 个任务
  总耗时：约 3–5 天

方案 C：按任务类型分工
  人 1：全部 Python 任务（10 个 × 5 竞品 = 50 次）
  人 2：全部 Java 任务（3 个 × 5 竞品 = 15 次）
  人 3：C/C++ + 安全任务（3 个 × 5 竞品 = 15 次）
```

需要人工评审的任务（bench_007、bench_012）安排最后，统一找 2–4 名评审按 `rubric.json` 双盲打分。

---

> **文档版本**：v1.0
> **编写日期**：2026-07-23
