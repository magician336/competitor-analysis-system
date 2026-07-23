# CodeRadar

CodeRadar 是面向 AI 编程助手竞品的证据驱动分析系统。项目围绕 CodeMate Campus 的产品决策，持续监控 Cursor、GitHub Copilot、Trae、通义灵码和 CodeGeeX，将官网、更新日志、价格页与公开 GitHub 信息加工为可追溯证据，再由 LangChain Multi-Agent 生成情报卡片、D1—D7 能力快照、基准对比与 Markdown 简报。

当前仓库完成了第一至第三周的核心工程工作，以及第四周前四个阶段：SQLite 持久化、异步 Workflow、正式 API 收口和 Vue 3 前端功能骨架。系统提供证据问答、异步模型分析、分页查询、Comparison 持久化、CodeMate 产品定义基线、API Key、限流、审计、统一错误，以及趋势首页、AI 情报问答和模型分析三个核心前端入口；视觉美化、浏览器 E2E、生产观测和正式发布仍待后续阶段完成。

```text
多源采集 → 清洗/去重/版本化 → Mini-RAG 检索与引用校验
                                  ├─ Price Agent
                                  ├─ Product Agent   ┐
                                  └─ Risk Agent      ├─ Multi-Agent 汇总
                                                      ├─ 情报卡片
规则标签 ───────────────── Dimension Agent ──────────┤
手工基准结果 ───────────── Benchmark Agent ─ Compare Agent ─ 能力快照
                                                      └─ Briefing Agent ─ Markdown 简报
```

## 第一至第三周完成情况

| 阶段 | 已具备能力 | 主要位置 |
|---|---|---|
| 第一周：范围与模型 | 五个竞品配置；E1—E3 事件体系；D1—D7 能力体系；来源等级、数据模型与评分配置 | `config/`、`schemas/`、`docs/` |
| 第二周：数据与 RAG | 官网、Changelog、Pricing、GitHub 采集；正文清洗、去重、历史版本；规则标注；Elasticsearch 上的 BM25 + Dense Retrieval + RRF + 重排；元数据过滤、引用校验、冲突识别、检索轨迹与评测 | `crawler/`、`processing/`、`mini_rag/`、`scripts/` |
| 第三周：Agent 与对标 | 四类核心 Prompt；Pydantic 严格输出；Price/Product/Risk/Dimension Agent；并行 Multi-Agent；事件级情报卡片；证据与基准共同驱动的能力快照；D1—D7 跨产品矩阵；16 个可执行冻结任务、统一验证器与手工结果导入；Trace、Markdown 简报和一键验收 | `prompts/`、`agents/`、`schemas/`、`benchmarks/`、`artifacts/week3/` |

事件标签为 E1 `pricing_change`、E2 `product_release`、E3 `risk_experience`。七类能力标签为：

- D1 `code_intelligence`：代码智能与生成质量；
- D2 `agent_context`：项目级上下文与 Agent 自主性；
- D3 `ide_ecosystem`：IDE、工具链与开发生态；
- D4 `model_extensibility`：模型接入与扩展能力；
- D5 `performance_cost`：性能、稳定性与成本效率；
- D6 `security_compliance`：安全、隐私与企业合规；
- D7 `education_fit`：用户体验与教育场景适配。

## 第三周技术实现

### 真实 LangChain 链路

第三周不是自定义的“类 LangChain”包装。运行时直接依赖 `langchain`、`langchain-core` 和 `langchain-deepseek`：

- Price、Product、Risk Agent 使用 LCEL Runnable 串联“请求校验 → Mini-RAG `StructuredTool` → 引用守卫 → Pydantic 情报卡片”；
- DeepSeek 通过 `ChatDeepSeek` 接入，并使用 `with_structured_output(PydanticModel, method="json_mode")` 约束输出；
- RAG 调用和模型调用均配置有限重试，执行过程由 LangChain Callback 记录阶段、耗时、模型使用与回退状态；
- 检索文本被视为不可信数据，Prompt 明确隔离其中的指令；模型只能引用本次检索白名单里的 `chunk_id`；
- 模型不能直接决定卡片 ID、证据对象、优先级公式或预警等级。模型输出会再次经过完整 Pydantic 校验，非法引用或调用失败时安全回退到确定性规则；
- `MultiAgentOrchestrator` 使用 `RunnableParallel` 并行执行三个专业分支，提供单分支故障隔离、部分成功结果、稳定工作流 ID、相同请求去重和有界缓存。

离线 `rules` 模式也仍然运行真实 LCEL 与 StructuredTool，只是不调用远程聊天模型，因此适合测试和无密钥演示。

### Agent 职责

| Agent | 职责 | 实现特点 |
|---|---|---|
| `PriceAgent` | 价格、套餐、额度、教育优惠和商业化变化 | 仅接受 E1 事件，重点分析 D4/D5/D6/D7 |
| `ProductAgent` | 产品发布、功能升级、Agent 与工程能力变化 | 仅接受 E2 事件，覆盖产品能力和生态影响 |
| `RiskAgent` | Issue、体验、稳定性、安全与合规风险 | 仅接受 E3 事件；风险证据对能力分采用负向影响 |
| `DimensionTaggingAgent` | E1—E3 与 D1—D7 混合标签 | 规则与模型判断分别保留，支持共识/并集策略、分歧复核，并提供单条/JSONL CLI |
| `CompareAgent` | 生成能力快照、跨产品矩阵和历史趋势 | 双 LCEL 管线；证据/基准评分、D1—D7 N/A 单元、覆盖率门槛、增长/差距/行业标配推断均保留规则依据 |
| `BenchmarkAgent` | 加载任务、审计资产、导入手工运行、竞品汇总 | 冻结协议、任务指纹、逐行错误隔离、幂等去重、原子 CSV 与 shell-free 验证器 |
| `BriefingAgent` | 汇总卡片和快照 | LCEL 生成可审阅 Markdown，保留证据链接和人工复核项 |
| `MultiAgentOrchestrator` | 并行调度与结果汇合 | Price/Product/Risk 并行，随后可选 Compare、Benchmark 与 Briefing 阶段 |

### 情报卡片与能力快照

`IntelligenceCard` 不是自由文本。它包含事件、变化前后、影响、机会、威胁、建议动作、置信度、优先级、预警等级、结构化发现、D1—D7 影响方向以及完整证据引用。

关键约束包括：

- 每条发现和能力影响必须引用卡片证据内已存在的 `chunk_id`；
- 情报卡按 `document_id + version_id` 聚合为事件级卡片，不同文档/版本不会被揉成一张；单分支默认最多输出 5 张；
- 证据中的竞品和事件类型必须与卡片一致，跨竞品、跨事件引用会被 Schema 拒绝；
- 无证据结果会降低置信度和优先级，标记 `review_required`，不会伪装成确定结论；
- 优先级固定按事件影响 35%、紧急程度 25%、证据置信度 20%、产品相关性 20% 计算；
- 威胁等级与预警等级分离，卡片 ID 由规范化内容稳定生成；
- 检索冲突、分析假设、运行模式、模型名、RAG 查询 ID 和执行 Trace 可供审计。

能力快照由 `config/scoring.yaml` 控制。目前的 `week3-evidence-v2` 规则将证据分与基准分按 60%/40% 合并；证据侧考虑 A—D 权威等级、发布时间、置信度和正负影响，基准侧考虑任务成功、测试通过、编译结果、人工干预及危险操作。没有证据或基准结果的维度标记为 `insufficient_evidence`，不会填充虚构分数。

### 16 项基准任务

`benchmarks/tasks/tasks.jsonl` 固定提供 16 项任务，覆盖 8 类场景，每类 2 项：函数补全、编译修复、测试修复、多文件修改、重构、代码解释、测试生成和安全审查。每项都已有真实 starter、`TASK.md`、`validator.json` 和自包含检查；解释任务另含冻结量表与双人盲评模板，测试生成任务包含固定 mutation set。

统一入口 `python -m benchmarks.validators.run_task` 使用参数数组和 `shell=False`，可审计全部资产或运行单项候选仓库。冻结验证器、检查、量表、mutation 与支持资产受 SHA-256 保护；候选进程使用最小化环境和一次性用户/临时目录。16 个原始 starter 已在 CodeRadar 环境验证为“因目标缺陷失败”，不存在语法、导入或测试收集错误。

这些措施保护实验协议完整性，但**不是操作系统级安全沙箱**，也不能阻断候选代码访问网络或工作区外文件。不可信候选必须在一次性、最小权限、禁网或受控网络的容器/虚拟机中运行；第四周不得把本地 Runner 直接暴露为可提交任意代码的公共 API。

每个任务记录成功标准、验证方式、公平性约束、任务修订号、协议版本和 SHA-256 指纹。Runner 同时输出验证器、完整协议、原始 starter 和候选目录摘要。正式运行写入空白起步的 `manual_runs.csv`；三条格式演示单独放在 `sample_runs.csv`，不得用于正式排名。每条正式 Run 必须带完整来源字段，以及产品/模型版本、成功状态、编译与测试结果、交互轮次、延迟、人工干预、成本、危险操作和带时区的运行时间。

## 目录结构

```text
competitor-analysis-system/
├── agents/                 # 第三周 LCEL Agent、回调、LLM 适配和编排器
├── artifacts/week3/        # 五家基线、Trace、验收报告及 CodeMate 对标矩阵
├── benchmarks/
│   ├── tasks/              # 16 项固定任务及执行协议
│   ├── results/            # 手工运行 CSV
│   ├── repositories/       # 16 个冻结 starter、检查、量表和 mutation set
│   └── validators/         # 资产审计与 shell-free 统一执行器
├── backend/                # FastAPI 薄适配：RAG、Agent、Benchmark 路由
├── config/                 # 竞品、维度、Mini-RAG 和能力评分配置
├── crawler/                # 官网、更新日志、价格与 GitHub 采集器
├── data/
│   ├── raw/                # 原始响应与采集元数据
│   ├── cleaned/            # 版本化结构文档 documents.jsonl
│   ├── samples/            # Mini-RAG 固定评测集和人工复核表
│   ├── runtime/            # 检索轨迹与评测结果
│   └── snapshots/          # HTTP 增量采集状态
├── docs/                   # 需求、验收和阶段接续文档
├── mini_rag/               # 切分、索引、检索、重排、证据与评测
├── processing/             # 清洗、规范化、规则标注、去重和版本管理
├── prompts/                # Price/Product/Risk/Dimension/Benchmark/Briefing Prompt
├── schemas/                # 严格 Pydantic 数据契约
├── scripts/                # 数据/索引/评测，以及标签、基线、矩阵和交付验收入口
├── tests/                  # 离线单元、集成和显式网络测试
├── rag_query.py            # Mini-RAG 命令行查询入口
└── requirement.txt         # Python 3.11 依赖清单
```

## 使用 Conda 环境 `CodeRadar`

项目目标 Python 版本为 3.11.9。已创建环境时，在项目根目录执行：

```powershell
conda activate CodeRadar
python --version
python -m pip install -r requirement.txt
python -m pip check
python -m scripts.data_pipeline doctor
```

如果 PowerShell 尚未加载 Conda，可先使用本机安装路径加载 Hook。当前开发机示例为：

```powershell
& 'D:\Miniconda\shell\condabin\conda-hook.ps1'
conda activate CodeRadar
```

也可以完全不激活环境，直接执行：

```powershell
& 'D:\Miniconda\Scripts\conda.exe' run -n CodeRadar python -m scripts.data_pipeline doctor
```

若环境尚不存在，再创建一次：

```powershell
conda create -n CodeRadar python=3.11.9 -y
conda activate CodeRadar
python -m pip install -r requirement.txt
```

`doctor` 只读取本地配置，不发送网络请求。它检查 Python/Conda 环境、依赖清单、LangChain 组件、五个竞品配置、D1—D7 标签配置、数据目录和 GitHub Token 状态。

## 环境变量与 Agent 模式

从模板创建本地配置；`.env` 已被 Git 忽略，不要提交真实密钥：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

建议的 Agent 配置如下：

```dotenv
CODERADAR_AGENT_MODE=rules
CODERADAR_LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
CODERADAR_LLM_TIMEOUT_SECONDS=45
CODERADAR_LLM_MAX_TOKENS=1600
CODERADAR_LLM_MAX_RETRIES=2
```

| 模式 | 是否调用 DeepSeek | 行为 |
|---|---:|---|
| `rules` | 否 | 确定性 LCEL + Mini-RAG + Pydantic，适合离线测试和可重复基线 |
| `hybrid` | 是 | 推荐的在线模式；模型润色并补充结构化判断，非法输出或失败时回退规则 |
| `llm` | 是 | 当前同样保留 RAG 白名单、确定性评分和完整校验，最终卡片记录为受保护的混合分析，而不是无约束纯模型输出 |

未配置 `DEEPSEEK_API_KEY` 时只能使用 `rules`；若显式选择 `llm` 或 `hybrid`，程序会立即报错，避免把离线回退误记为在线分析。模型端默认使用支持当前结构化输出链路的 `deepseek-chat`。不要把 Token、完整 Prompt 或原始敏感内容写入日志；当前 Callback 只记录执行元数据。

GitHub 匿名采集可以运行，但配置只读 `GITHUB_TOKEN` 能提高公开 API 请求限额：

```dotenv
GITHUB_TOKEN=你的只读令牌
```

## 数据与 Mini-RAG

每个竞品均显式配置 11 类来源。启用项必须提供 HTTPS 地址或 GitHub 仓库；禁用项保留原因说明，`doctor` 会检查类型、证据等级和目标地址。表中 GitHub 任务会分别生成 GitHub Release 和 GitHub Issue 结构化来源。

| 来源类型 | Cursor | GitHub Copilot | Trae | 通义灵码 | CodeGeeX |
| --- | :---: | :---: | :---: | :---: | :---: |
| 官网与博客 `official` | ✓ | ✓ | ✓ | ✓ | ✓ |
| 更新日志 `changelog` | ✓ | ✓ | ✓ | ✓ | — |
| 定价页面 `pricing` | ✓ | ✓ | ✓ | ✓ | — |
| 产品文档 `product_docs` | ✓ | ✓ | ✓ | ✓ | ✓ |
| 状态页 `status_page` | ✓ | ✓ | — | — | — |
| GitHub `github` | — | ✓ | ✓ | — | ✓ |
| 插件市场 `plugin_marketplace` | — | ✓ | ✓ | ✓ | ✓ |
| 社区平台 `community` | ✓ | ✓ | — | — | — |
| 视频与测评 `review` | — | — | — | — | — |
| 安全与隐私 `security_privacy` | ✓ | ✓ | ✓ | ✓ | — |
| 基准测试 `benchmark` | ✓ | ✓ | ✓ | — | ✓ |

第三方视频、测评和社区地址需要先建立人工确认的 URL 允许列表，因此未验证的入口保持禁用。将可靠地址写入 `config/competitors.yaml` 的 `urls`，再把 `enabled` 改为 `true`，无需新增采集代码。

查看采集计划，不发送请求：

```powershell
python -m scripts.data_pipeline crawl `
  --competitors cursor,github_copilot `
  --since-days 90 `
  --dry-run
```

只查看新增来源任务，不发送请求：

```powershell
python -m scripts.data_pipeline crawl `
  --competitors all `
  --sources product_docs,status_page,plugin_marketplace,community,review,security_privacy,benchmark `
  --since-days 365 `
  --dry-run
```

现有 `data/raw` 已包含官网、更新日志、定价和 GitHub 数据时，只采集新增来源并重建结构化结果：

```powershell
python -m scripts.data_pipeline crawl `
  --competitors all `
  --sources product_docs,status_page,plugin_marketplace,community,review,security_privacy,benchmark `
  --since-days 365 `
  --force `
  --log-level INFO

python -m scripts.data_pipeline process --rebuild
python -m scripts.audit_documents
```

`--force` 使本轮对全部启用的新增 URL 发出完整请求。`process --rebuild` 会同时读取已有原始响应和新增原始响应，生成一个一致的 `documents.jsonl`。

执行采集和结构化处理：

```powershell
python -m scripts.data_pipeline crawl --competitors all --since-days 90
python -m scripts.data_pipeline process
```

原始数据位于 `data/raw/<competitor>/<source_type>/<crawl_run_id>/`，结构化文档位于 `data/cleaned/documents.jsonl`。处理器执行 URL/Unicode/空白规范化、SHA-256 去重、稳定 `document_id` 与版本化 `version_id` 管理，并写入事件、能力和证据等级。

规则变化后可以从已有原始响应重建派生数据；该操作不重新访问网站：

```powershell
python -m scripts.data_pipeline process --rebuild
```

`--rebuild` 会原子替换 `data/cleaned/documents.jsonl`，不会把规则修正记录为竞品内容的新历史版本。该命令只读取本地 `data/raw`，不发送网络请求。

采集并处理全部五个竞品：

```powershell
python -m scripts.data_pipeline all `
  --competitors all `
  --since-days 90
```

常用范围控制参数：

- `--sources`：选择 `official`、`changelog`、`pricing`、`product_docs`、`status_page`、`github`、`github_release`、`github_issue`、`plugin_marketplace`、`community`、`review`、`security_privacy` 或 `benchmark`；多个类型使用逗号分隔。
- `--max-issues 100`：设置每个仓库最多采集的 Issue 数量。
- `--max-comments 20`：设置每条 Issue 最多采集的评论数量。
- `--dry-run`：只校验并输出任务清单。
- `--force`：忽略 ETag 和 Last-Modified 条件缓存，重新请求来源。
- `--rebuild`：`process` 或 `all` 从全部原始响应重建清洗结果。
- `--log-level DEBUG|INFO|WARNING|ERROR`：设置日志级别。

采集器遵守 `robots.txt`，按域名限速，并使用 15 秒超时和最多 3 次指数退避。HTML Changelog 列表会提取带日期的同源条目链接，按照 `--since-days` 过滤，跟随下一页并抓取详情；每个任务默认最多访问 20 个列表页和 200 个详情条目。对于日期位于子页面正文的目录型 Changelog，配置通过层叠样式表选择器（Cascading Style Sheets Selector，CSS Selector）、同源约束、路径前缀和标题模式限定目录链接。采集器校验重定向后的最终地址，读取子页面正文中的显式日期标题或表格日期；处理器按日期段拆分文档，并只保留 `--since-days` 窗口内的条目。目录返回 HTTP 304 时会无条件刷新一次目录正文，以继续检查独立更新的子页面。列表页、无可靠日期边界和超出日期范围的候选响应继续保留用于审计。配置了 `browser_fallback` 的 JavaScript 动态官网在静态正文不足时使用 Playwright 和无头 Chromium 获取渲染后的 HTML；其他页面保持同步 `requests` 采集。浏览器失败时保留静态响应、`needs_browser` 和失败原因，单个来源失败不会中断其他来源。配置了 `empty_result_markers` 的 Changelog 页面在匹配官方空状态提示时记录合法空结果，不生成虚构更新。

## 数据输出

每次采集创建唯一的 `crawl_run_id`，原始响应与元数据保存为：

```text
data/raw/<competitor>/<source_type>/<crawl_run_id>/
├── <raw_record_id>.html|json|xml
└── <raw_record_id>.meta.json
```

清洗结果保存为：

```text
data/cleaned/documents.jsonl
```

结构化处理会规范化统一资源定位符（Uniform Resource Locator，URL）、Unicode 和空白，计算 SHA-256 内容哈希，并按照“竞品、来源类型、规范 URL、内容哈希”去重。`document_id` 标识稳定来源实体，`version_id` 标识内容版本。内容变化会关闭当前版本的有效区间并保留历史版本。

来源类型写入 E1—E3 事件标签，正文按 `config/dimensions.yaml` 的关键词写入 D1—D7 多标签结果，同时保存置信度、命中原因和人工复核标记。

## Mini-RAG 索引

Mini-RAG 需要可用的 Elasticsearch。只启动检索服务时可以执行：

```powershell
docker compose up -d elasticsearch
python -m scripts.audit_documents
python -m scripts.build_index
```

完成数据库迁移与首次初始化后，可以用 Compose 同时启动 API、独立 Workflow Worker 和 Elasticsearch：

```powershell
$env:CODERADAR_API_KEY = 'replace-with-a-long-random-value'
docker compose up -d --build
```

API 默认监听 `http://127.0.0.1:8001`，容器内部仍使用 8000。需要其他宿主端口时可在 PowerShell 中覆盖：

```powershell
$env:CODERADAR_API_PORT = '8010'
$env:CODERADAR_API_KEY = 'replace-with-a-long-random-value'
docker compose up -d api
```

API 与 Worker 共享 `./data` 卷中的 SQLite 数据库，队列不依赖 Redis。一次只能运行一个 Worker；同时启动本地 Worker 和容器 Worker 时，只有取得 SQLite 单例租约的进程会执行任务。

增量更新与命令行查询：

```powershell
python -m scripts.build_index --incremental
python .\rag_query.py "Cursor 最近有哪些 Agent 能力更新" --competitor Cursor --top-k 8
```

默认 `hash + lexical` 配置用于确定性冒烟测试；正式检索可以使用 `config/mini_rag.formal.yaml` 中的多语言 Embedding 和 Cross-Encoder。更换 Embedding 模型或维度后必须全量重建物理索引，避免不同向量空间混用。

## 运行第三周 Agent

### 初始化 SQLite 并启动 API

首次运行或迁移版本变化后，先显式升级数据库。下面的初始化命令可重复执行，重复制品不会产生重复记录：

```powershell
python -m alembic upgrade head
python -m scripts.init_database --seed-competitors --seed-codemate --import-artifacts artifacts/week3
```

数据库默认位于 `data/runtime/coderadar.db`，可通过 `CODERADAR_DATABASE_URL` 覆盖。API 启动时只检查连接与 Alembic 版本，不会自动建表或导入数据。正式 API 默认要求 `CODERADAR_API_KEY`；只有离线测试或受控本地调试才应显式关闭认证：

```powershell
$env:CODERADAR_API_KEY = 'replace-with-a-long-random-value'
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
```

异步 Multi-Agent Workflow 由独立 Worker 执行。另开一个终端启动：

```powershell
python -m scripts.run_workflow_worker
```

Worker 使用 SQLite 持久化队列，一次只运行一个 Workflow；Workflow 内选择的 Price、Product、Risk 分支继续并行执行。默认轮询、租约、心跳和超时分别为 1、30、10 和 300 秒，可通过 `CODERADAR_WORKFLOW_POLL_SECONDS`、`CODERADAR_WORKFLOW_LEASE_SECONDS`、`CODERADAR_WORKFLOW_HEARTBEAT_SECONDS`、`CODERADAR_WORKFLOW_TIMEOUT_SECONDS` 覆盖。取消采用协作式语义，运行中的外部调用返回后才会停止下游汇总。

提交异步分析：

```powershell
$workflow = Invoke-RestMethod `
  -Method Post `
  -Uri 'http://127.0.0.1:8001/api/workflows' `
  -ContentType 'application/json; charset=utf-8' `
  -Headers @{ 'X-API-Key' = $env:CODERADAR_API_KEY } `
  -Body (@{
    competitor = 'Cursor'
    correlation_id = 'readme-workflow-001'
    include_snapshot = $true
    include_briefing = $true
  } | ConvertTo-Json)

Invoke-RestMethod `
  "http://127.0.0.1:8001$($workflow.status_url)" `
  -Headers @{ 'X-API-Key' = $env:CODERADAR_API_KEY }
```

分析一个产品发布事件：

```powershell
$body = @{
  competitor = 'Cursor'
  question = '最近有哪些产品和 Agent 能力更新？'
  event_type = 'product_release'
  dimension_tags = @('agent_context', 'ide_ecosystem')
  evidence_levels = @('A', 'B')
  current_only = $true
  top_k = 8
  correlation_id = 'readme-product-001'
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri 'http://127.0.0.1:8001/api/agent/product' `
  -ContentType 'application/json; charset=utf-8' `
  -Body $body
```

Price、Product、Risk 的事件类型必须分别为 `pricing_change`、`product_release`、`risk_experience`；也可以省略 `event_type`，由所调用的专业 Agent 固定其事件范围。

### 直接运行 Multi-Agent

编排器和能力标签 Agent 已作为第三周核心类实现；异步 HTTP 调用使用 `/api/workflows`，也可以在 Python 中直接调用：

```python
from agents.orchestrator import MultiAgentOrchestrator
from backend.services.rag_service import get_rag_service
from schemas.orchestration import MultiAgentAnalysisRequest

orchestrator = MultiAgentOrchestrator(get_rag_service())
result = orchestrator.run(
    MultiAgentAnalysisRequest(
        competitor="Cursor",
        question="汇总最近 90 天的价格、产品和风险变化",
        correlation_id="weekly-cursor-001",
        include_snapshot=True,
        include_briefing=True,
        include_benchmark_data=True,
    )
)

print(result.status)
print(result.snapshot.model_dump_json(indent=2) if result.snapshot else "no snapshot")
print(result.briefing or "no briefing")
```

编排器会并行执行 Price/Product/Risk；一个分支失败不会抹掉其他分支的有效卡片。相同请求可以通过稳定指纹命中缓存，最终结果明确标记 `success`、`partial_failure` 或 `failed`。

### 生成五家竞品首版基线

在 Elasticsearch 读取别名就绪后，可用同一条第三周工作流重新生成五家竞品基线：

```powershell
$env:CODERADAR_AGENT_MODE = 'rules'
python -m scripts.generate_week3_baseline `
  --mode rules --top-k 8 --as-of 2026-07-20 --window-days 90
```

命令会先校验 Mini-RAG 索引与 Embedding 是否兼容，再从真实检索证据生成 `artifacts/week3/`：

- `manifest.json`：生成/有效模式、90 天窗口、输入哈希、评分版本、物理索引、卡片质量和文件清单；
- `intelligence_cards.json`：Price/Product/Risk 的严格结构化情报卡片；
- `capability_snapshots.json`：五家竞品的 D1—D7 首版能力快照；
- `workflow_summary.json`：分支状态、证据量、覆盖率和降级警告；
- `trace_summaries.json`：15 个专业分支的脱敏 LCEL/RAG 执行摘要；
- `briefings/*.md`：五份可人工复核的竞争态势简报。

仓库当前冻结基线使用可复现的 `rules` 模式，包含 5 家竞品、35 张事件级卡片（26 张证据支持、9 张降级复核）、5 份快照、15 条 Trace 和 5 份简报。某个专业分支未检索到证据时仍会返回低置信复核卡，但不会伪造证据。

生成 CodeMate Campus 对标矩阵：

```powershell
python -m scripts.generate_week3_comparison
```

结果位于 `artifacts/week3/comparison/`。当前没有正式 CodeMate Campus 实测快照，因此生成器只创建 0 覆盖、全部 N/A 的证据不足基线，不填造分数，并在 `comparison_provenance.json` 中写明 `official_ranking_ready=false`。第四周录入真实 CodeMate 快照后可用同一命令重算正式差距。

最后执行一键验收：

```powershell
python -m scripts.verify_week3_delivery
# 只有显式需要并已配置密钥时才执行一个最小在线结构化探针：
python -m scripts.verify_week3_delivery --live-llm `
  --output artifacts/week3/live_llm_report.json
```

当前 `acceptance_report.json` 为 CodeRadar/Python 3.11.9 下 5/5 通过、0 警告；单独的在线报告为 6/6 通过，且不保存密钥或完整 Prompt。

### 运行能力标签 CLI

```powershell
python -m scripts.tag_dimensions single `
  --mode rules --source-type changelog `
  --title 'Agent update' --content '新增项目级 Agent 与终端工具'

python -m scripts.tag_dimensions batch `
  --mode rules --input input.jsonl --output tagged.jsonl
```

批处理只有在所有行均校验成功后才原子替换输出；任一错误都会保留原文件并报告行号。

### 审计与运行 Benchmark

```powershell
python -m benchmarks.validators.run_task --audit
python -m benchmarks.validators.run_task --task-id bench_001
python -m benchmarks.validators.run_task `
  --task-id bench_001 --candidate C:\path\to\candidate
```

第二条命令运行故意有缺陷的 starter，预期返回非零；修复后的候选仓库才应通过。输出中的 `task_fingerprint`、`validator_sha256`、`protocol_sha256`、`starter_sha256` 和 `candidate_sha256` 必须原样写入正式 Run。正式比较前必须确保每个产品使用相同任务指纹、协议、starter、交互轮次和验证环境。

## 手工基准结果导入

CSV 表头必须为：

```text
run_id,competitor,task_id,task_revision,task_fingerprint,validator_sha256,protocol_sha256,starter_sha256,candidate_sha256,product_version,model,task_success,compile_success,test_pass_rate,edit_rounds,latency_ms,manual_intervention,estimated_cost,harmful_action,notes,run_at
```

将待导入文件放在 `benchmarks/results/`，然后直接使用 Agent：

```python
from agents.benchmark_agent import BenchmarkAgent

agent = BenchmarkAgent()
summary = agent.import_csv(
    "benchmarks/results/to_import.csv",
    persist=True,
    idempotent=True,
)
print(summary.model_dump_json(indent=2))
print([row.model_dump() for row in agent.compare()])
```

也可以通过 API 导入。为防止任意文件读取，`csv_path` 必须位于配置的 `benchmarks/results/` 目录内：

```powershell
$body = @{ csv_path = 'benchmarks/results/to_import.csv' } | ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri 'http://127.0.0.1:8001/api/benchmarks/runs/import' `
  -ContentType 'application/json; charset=utf-8' `
  -Body $body
```

导入逐行校验：错误行被隔离，未知 `task_id`、旧表头、缺失来源字段、过期任务指纹或与当前资产审计不一致的协议/starter 哈希都会被拒绝，重复 `run_id` 幂等跳过；比较阶段会再次拒绝跨协议混排。成功结果通过同目录临时文件原子更新 `benchmarks/results/manual_runs.csv`。

## 当前 API

| 接口 | 用途 |
|---|---|
| `GET /health`、`GET /ready` | 进程存活与 Mini-RAG 就绪检查 |
| `POST /api/rag/query` | 混合检索，返回证据、冲突和轨迹 |
| `GET /api/rag/evidence/{chunk_id}` | 按 Chunk 读取原始证据 |
| `GET /api/rag/trace/{query_id}` | 读取检索阶段、候选量和延迟 |
| `GET /api/rag/index/status` | 查看 Elasticsearch 与索引状态 |
| `POST /api/rag/rebuild`、`POST /api/rag/index/incremental` | 全量/增量索引 |
| `POST /api/rag/citations/validate`、`POST /api/rag/evaluate` | 引用校验与检索评测 |
| `POST /api/agent/price`、`/product`、`/sentiment-risk` | 运行三个专业 Agent |
| `GET /api/agent/cards`、`GET /api/agent/cards/{card_id}` | 从 SQLite 查询持久化情报卡片 |
| `POST /api/agent/compare`、`GET /api/agent/snapshots` | 生成/查询持久化能力快照 |
| `POST /api/agent/briefing` | 生成 Markdown 简报 |
| `POST /api/workflows` | 提交异步 Multi-Agent Workflow，返回 202 |
| `GET /api/workflows/{workflow_id}` | 查询状态、进度、分支错误和最终结果 |
| `POST /api/workflows/{workflow_id}/cancel` | 请求取消排队或运行中的 Workflow |
| `POST /api/workflows/{workflow_id}/retry` | 只重试失败或未完成分支 |
| `GET /api/cards`、`GET /api/cards/{card_id}` | 卡片分页、组合过滤、详情和 Evidence Links |
| `GET /api/evidence/{chunk_id}` | Evidence 与关联 Card IDs 反查 |
| `GET /api/snapshots`、`GET /api/snapshots/{snapshot_id}` | 快照分页、历史过滤、来源与 provenance |
| `GET/POST /api/comparisons`、`GET /api/comparisons/latest` | 幂等生成并查询持久化矩阵 |
| `GET /api/briefings`、`GET /api/briefings/{id}/content` | 简报列表、详情和 Markdown 下载 |
| `GET/POST/PUT/DELETE /api/competitors` | 竞品查询、创建、更新和软停用 |
| `GET /api/dimensions` | 读取冻结的 D1—D7 定义和权重 |
| `GET /api/benchmarks/tasks`、`GET /api/benchmarks/results` | 查询任务与手工运行 |
| `POST /api/benchmarks/runs/import`、`GET /api/benchmarks/compare` | 持久化导入与竞品汇总 |

Agent 运行产生的卡片、证据、快照、Workflow、分支、Trace、简报和比较矩阵写入 SQLite，API 或 Worker 重启后仍可查询。正式 API 使用 `X-API-Key`、统一 Problem Details 和脱敏审计。完整接口合同见 [`docs/API接口文档.md`](docs/API接口文档.md)。本阶段不新增或持久化 Benchmark 数据。

## 运行 Vue 前端

前端使用 Vue 3、TypeScript、Element Plus 和 D3，主导航包含趋势首页、AI 情报问答和模型分析。首页聚合趋势事件流、能力星图、能力矩阵和参考排序；模型分析结果以 Markdown 简报为主产物，并提供能力快照、情报卡片及分栏阅读页。按照第四周范围约定，前端没有 Benchmark 菜单、路由、组件或请求。

确保 API、Worker 和 Elasticsearch 已启动后运行：

```powershell
cd frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

默认打开 `http://127.0.0.1:5173`，开发代理连接 `http://127.0.0.1:8001`。如果后端使用其他端口，请在 `.env.local` 中修改 `VITE_API_TARGET`。演示前端自动使用 `VITE_API_KEY` 中的默认演示 Key，不提供浏览器输入交互；DeepSeek 密钥只保存在后端环境中。该方式只适用于本地演示，正式部署应替换密钥并改用服务端会话或网关认证。

顶部工具栏可为新 Workflow 选择 `Rules`、`Hybrid` 或 `LLM`。选择值保存在当前浏览器会话，并随 Workflow 请求持久化；已提交任务和重试不会被后续切换影响。`Hybrid` 支持模型失败回退，`LLM` 会如实报告模型调用失败。在线模式要求 API 与 Worker 均配置 `DEEPSEEK_API_KEY`。

前端质量检查：

```powershell
npm run typecheck
npm run test
npm run build
```

## Mini-RAG 评测

候选集准备、人工复核和标准评测命令为：

```powershell
python -m scripts.prepare_evaluation_set
python -m scripts.validate_evaluation_set
# 在 data/samples/评测集人工复核.csv 完成人工字段后：
python -m scripts.finalize_evaluation_review
python -m scripts.validate_evaluation_set
python -m scripts.evaluate_rag --output data/runtime/mini_rag_evaluation.json
```

评测覆盖 Recall@K、MRR、nDCG、过滤准确率、引用正确率、历史版本误召回率、平均延迟与 P95 延迟，并支持 A—F 消融实验。人工复核未完成的候选集只用于工程诊断，不应作为正式质量结论。

## 测试

默认使用离线规则模式，避免本地 `.env` 或 API Key 让单元测试发起模型请求：

```powershell
$env:CODERADAR_AGENT_MODE = 'rules'
python -m pytest -m "not network" -q
python -m pytest tests -k week3 -q
Remove-Item Env:CODERADAR_AGENT_MODE -ErrorAction SilentlyContinue
```

第三周测试覆盖真实 LangChain Runnable/StructuredTool/ChatDeepSeek、结构化输出重试与非法引用回退、事件级拆卡、跨字段引用约束、无证据降级、冲突记录、Dimension CLI、16 项可执行 Benchmark、D1—D7 跨产品矩阵与趋势、基线/Trace 制品、一键验收，以及 Multi-Agent 并行、故障隔离、缓存和工作流稳定性。测试还覆盖 Alembic 升降级、SQLite 外键/WAL、Manifest 导入、Pydantic 往返校验、事务回滚、异步提交、分支重试、协作式取消、租约恢复、正式 API 分页、认证、限流、审计和 Comparison 幂等。当前全项目离线回归为 `363 passed, 1 skipped`。

显式网络测试默认不会运行。需要自行检查公开网络时：

```powershell
$env:CODERADAR_RUN_NETWORK_TESTS = '1'
python -m pytest tests\test_network_smoke.py -m network -q
Remove-Item Env:CODERADAR_RUN_NETWORK_TESTS
```

## 第四周后续边界

当前仍不包含以下后续阶段工作：

- 前端统一主题、细节美化、复杂动画和完整视觉验收；
- JWT 用户体系、多租户和生产密钥托管；
- 完整成本换算、预算熔断、线上观测告警与回放工具；
- 完整 Docker 镜像、环境编排、健康策略和部署文档；
- 浏览器端到端测试、大规模真实模型质量评估、压力测试和发布验收。

后续阶段应在不改写既有 Pydantic 契约和证据守卫的前提下，继续完成视觉优化、可观测性、E2E 与部署。完整资产清单、复现命令、非 Git 数据交付要求和接续建议见 `docs/交付.md`。

## 安全与可复现约定

- `.env`、Token、模型缓存、运行时输出和原始抓取响应按 `.gitignore` 管理；
- GitHub Token 只需要公开仓库只读权限；采集器遵守 `robots.txt`、按域名限速并使用有限重试；
- Benchmark 比较必须使用相同任务修订、起始材料、产品/模型记录和验收方式；
- Agent 结论必须能回到 `chunk_id`、原文片段和来源 URL；无证据不得给出高置信结论；
- 生产或正式汇报前，必须复核冲突、低置信、危险操作和 `review_required` 项。
