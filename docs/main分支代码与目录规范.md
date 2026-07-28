# CodeRadar `main` 分支代码与目录规范

> 审计基线：`main@d7b80a1`，2026-07-27。本文同时记录当前仓库事实、后续提交必须遵守的规则，以及尚未补齐的工程化能力。

## 1. 审计结论

当前仓库的业务分层总体清晰：采集、处理、检索、Agent、API、Schema、脚本、测试和前端均有独立目录；Python 依赖集中在 `requirements.txt`，前端依赖集中在 `frontend/package.json`；业务边界大量使用 Pydantic 模型，测试以 pytest 和 Vitest 为主。

本次检查发现并处理了以下目录与配置问题：

- Mini-RAG 配置原先会随终端当前目录变化，现在相对路径统一以项目根目录解析；
- SQLite 相对 URL 原先会随启动目录变化，现在固定解析到项目目录；
- `.env.example` 原先混入 `/app/CodeRadar` 和 `/root` 等容器绝对路径，现在本地与 Compose 共用项目相对路径；
- 当前本地 `.env` 原先存在重复键，后定义值会覆盖前定义值，现已整理为单一配置；
- `.vscode/settings.json` 原先写死其他机器的 `A:` 盘路径，现使用 `${workspaceFolder}`；
- 后端基准 CSV 导入原先相对于进程当前目录解析，现统一相对于项目根目录解析；
- 显式指定的 YAML，或真实仓库根目录的 `formal` profile 文件不存在时，现会立即报出缺失路径；仅在未指定配置的临时项目根目录中保留模型默认值兼容行为。

仍存在两项工程化缺口：

1. 仓库没有 `pyproject.toml`、Ruff/Black/Mypy、ESLint/Prettier 或 pre-commit 配置，代码格式目前依赖人工一致性；
2. 仓库没有 `.github/workflows` 等持续集成配置，合并门禁尚未自动化；

## 2. 运行环境基线

| 项目 | 规范 |
|---|---|
| Python | 3.11.9，使用 Conda 环境 `CodeRadar` |
| Python 依赖 | 只维护根目录 `requirements.txt`，不要恢复 `requirement.txt` 或拆出无说明的重复依赖文件 |
| 前端 | Node 24+；依赖、脚本均由 `frontend/package.json` 管理 |
| 文本编码 | UTF-8；中文文件名和中文内容均直接保存为 UTF-8 |
| Python 启动方式 | 在仓库模块边界上使用 `python -m ...`，例如 `python -m scripts.build_index` |
| 本地配置 | 复制 `.env.example` 为 `.env`；`.env` 不提交，`.env.example` 必须保持无密钥且可直接理解 |

进入环境：

```powershell
conda activate CodeRadar
python --version
python -m pip install -r requirements.txt
```

## 3. 目录职责

| 路径 | 职责 | 可以放入 | 不应放入 |
|---|---|---|---|
| `agents/` | LangChain Agent、编排、LLM 客户端 | Agent 实现、回调、分支编排 | HTTP 路由、数据库迁移、采集器 |
| `backend/` | FastAPI、服务层、持久化和认证 | 路由、服务、Repository、SQLAlchemy 模型 | 抓取逻辑、前端资源、一次性脚本 |
| `crawler/` | 外部公开来源采集 | HTTP 客户端、来源适配器、robots 处理 | 数据清洗、Agent 推理 |
| `processing/` | 清洗、去重、标注、版本化、审计 | 可复用的数据处理组件 | API 路由、部署配置 |
| `mini_rag/` | Mini-RAG 完整领域包 | 配置、切片、嵌入、索引、检索、排序、证据与评测 | 具体前端或认证逻辑 |
| `schemas/` | 跨层数据契约 | Pydantic 模型、枚举、校验器 | 数据库连接、网络调用 |
| `config/` | 可提交的业务和检索配置 | 无密钥 YAML；相对项目根目录的路径 | API Key、机器绝对路径、运行产物 |
| `prompts/` | 可审阅的 Prompt 模板 | 按 Agent 职责拆分的模板 | Python 业务代码 |
| `scripts/` | 命令行入口与流程编排 | 薄入口、参数解析、调用领域模块 | 重复实现 `backend/` 或 `mini_rag/` 的核心逻辑 |
| `alembic/` | 数据库版本迁移 | 只增不改的迁移版本 | 运行数据库文件 |
| `tests/` | Python 自动化测试 | 与业务模块对应的测试、固定小型 fixture | 真实密钥、网络下载结果、大体积运行产物 |
| `frontend/src/` | Vue 3 应用 | `api/`、`components/`、`composables/`、`stores/`、`views/`、`types/`、`test/` | 后端配置和服务端密钥 |
| `benchmarks/` | 冻结任务、验证器和基准仓库 | 可复现任务协议、验证资产、受控结果 | 日常 pytest fixture 或临时实验 |
| `artifacts/` | 经确认的交付产物 | 带 manifest/版本信息的稳定产物 | 无来源说明的临时输出 |
| `data/samples/` | 小型、可复现的评测输入 | 人工复核集、说明文件 | 密钥、个人数据、无界增长数据 |
| `data/raw/`、`data/runtime/` | 本地采集和运行状态 | `.gitkeep`；其余由运行时生成 | 普通业务代码和应长期维护的配置 |
| `docs/` | 架构、启动、部署和规范说明 | UTF-8 Markdown | 密钥、数据库、缓存和二进制运行产物 |

新增文件前先判断它属于“领域实现、接口契约、命令入口、配置、测试、文档还是运行产物”。无法明确归属时，不要直接堆到仓库根目录。

历史 `artifacts/week3` 中存在用于审计溯源的机器绝对路径。这些字段不是运行配置，不应由应用读取；直接改写会破坏既有产物哈希。新产物应优先记录项目相对路径，并把机器信息放入独立、可选的环境元数据。

## 4. 配置和路径规范

### 4.1 唯一根目录规则

代码不得用 `Path.cwd()` 推断仓库位置，也不得假设调用者先执行 `cd`。包内应从 `__file__` 推导项目根目录，命令行传入的相对路径再以该根目录解析。

允许：

```python
PROJECT_ROOT = Path(__file__).resolve().parents[1]
config_path = (PROJECT_ROOT / "config" / "mini_rag.yaml").resolve()
```

禁止：

```python
config_path = Path.cwd() / "config" / "mini_rag.yaml"
config_path = Path("C:/某个用户/项目/config/mini_rag.yaml")
```

### 4.2 `.env` 与 YAML

- `config/*.yaml` 保存可提交、无密钥、跨机器的基线值；
- `.env.example` 解释所有常用环境变量，但敏感值必须留空；
- `.env` 保存本机密钥和运行模式，不进入 Git；
- 文件路径使用 `/` 分隔的项目相对形式，例如 `data/cleaned/documents.jsonl`；
- 不在共享配置中写 Windows 盘符、用户名、`/app/CodeRadar` 或 `/root`；
- 只有确实位于项目外部的资源才使用绝对路径，并在部署环境中单独覆盖。

Mini-RAG 配置优先级从低到高为：

1. Pydantic 模型默认值；
2. `MINIRAG_PROFILE` 选择的 YAML；
3. 显式参数或 `MINIRAG_CONFIG_PATH` 指定的 YAML；
4. 单项 `MINIRAG_*` 环境变量。

因此，使用 `MINIRAG_PROFILE=formal` 时应让 `MINIRAG_CONFIG_PATH` 保持未设置或空值。切换嵌入模型后必须全量重建索引。

SQLite 使用：

```ini
CODERADAR_DATABASE_URL=sqlite:///data/runtime/coderadar.db
```

应用会将其解析为项目根目录下的数据库。PostgreSQL 等完整网络 URL 保持原样，不参与文件路径转换。

## 5. Python 代码规范

- 文件、函数和变量使用 `snake_case`；类使用 `PascalCase`；常量使用 `UPPER_SNAKE_CASE`；
- 新模块应有简短模块 docstring，并默认使用 `from __future__ import annotations`；
- 公共函数、跨层函数和复杂返回值必须有类型标注；
- 文件系统操作统一使用 `pathlib.Path`，不要混用字符串拼接和当前目录假设；
- 导入按标准库、第三方、项目内模块三组排列，组间空一行；
- API、Agent 输出、持久化 payload 和配置边界使用 Pydantic 或明确的数据类校验；
- 核心业务逻辑放在可测试的领域模块，`scripts/` 和 FastAPI 路由只做参数/协议转换与调用；
- 不使用裸 `except:`，异常应保留可定位的原因；对外 API 再转换成安全、稳定的错误结构；
- 日志、异常和测试输出不得包含 Token、Cookie、密码或完整私密请求；
- 新代码以每行不超过 100 字符为目标。当前历史代码仍有超长行，在引入统一格式化工具前不要进行无关的全仓格式化。

## 6. 前端代码规范

- Vue 页面放在 `frontend/src/views/`，可复用组件放在 `components/`；
- 请求封装统一放在 `api/`，状态放在 `stores/`，复用交互逻辑放在 `composables/`；
- 跨组件数据结构放在 `types/`，避免在多个页面重复定义接口；
- 测试放在 `frontend/src/test/`，命名为 `*.spec.ts`；
- 浏览器端只允许 `VITE_*` 公开配置，绝不能把服务端密钥写入前端环境变量；
- `.vscode`、Vite 和 TypeScript 配置只能使用工作区相对路径。

当前没有 ESLint/Prettier 门禁，因此至少必须通过 TypeScript 检查、Vitest 和生产构建。

## 7. 测试与合并门禁

提交前的最低检查：

```powershell
conda activate CodeRadar

# Python：默认排除真实网络和 Elasticsearch 集成测试
python -m pytest -m "not network and not es_integration" -q

# 配置与 Compose
docker compose config --quiet

# 前端
cd frontend
npm run typecheck
npm run test
npm run build
```

测试规则：

- 单元测试不得继承开发者真实 LLM 模式或 API Key；
- 真实网络测试必须显式标记并默认跳过；
- 测试数据使用 `tests/fixtures/` 或 `data/samples/` 中的固定小样本；
- 禁止通过放宽核心断言来掩盖产物变化；产物数量、manifest 或协议变化应先确认新基线；
- 修复缺陷时同时增加能在修复前失败、修复后通过的回归测试。

## 8. `main` 分支与提交规范

- `main` 只接收可运行、已验证、文档同步的变更；日常开发使用短生命周期分支；
- 分支建议使用 `feat/`、`fix/`、`refactor/`、`docs/`、`test/` 前缀；
- 提交信息沿用仓库现有风格：`feat:`、`fix:`、`refactor:`、`docs:`、`test:`；
- 一个提交只解决一个可说明的问题，不混入无关格式化、产物刷新或本机配置；
- Schema、API、YAML 字段、环境变量或目录职责变化必须同步 README/专题文档和测试；
- 不提交 `.env`、数据库、模型缓存、检索轨迹、日志、`node_modules`、`__pycache__`。

合并请求说明至少回答：

1. 改了什么边界，为什么；
2. 是否改变配置、路径、Schema、API 或数据产物；
3. 执行了哪些验证，结果如何；
4. 是否存在未解决的失败、外部依赖或迁移步骤。

## 9. 后续工程化建议

建议后续用独立变更补齐，而不是与业务功能混合：

1. 新增 `pyproject.toml`，统一 Ruff 格式、静态检查和 pytest marker；
2. 为前端加入 ESLint/Prettier 及对应脚本；
3. 建立持续集成，执行 Python 非网络测试、前端类型检查/测试/构建、Compose 配置校验；
4. 评估 `data/backups/` 中已跟踪的大体积历史快照，保留必要交付样本，其余迁移到外部制品存储。
