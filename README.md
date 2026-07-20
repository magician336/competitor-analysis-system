# CodeRadar

CodeRadar 是面向 AI 编程助手竞品的证据驱动分析系统。项目围绕 CodeMate Campus 的产品决策，持续监控 Cursor、GitHub Copilot、Trae、通义灵码和 CodeGeeX，将官网、更新日志、价格页与公开 GitHub 信息加工为可追溯证据，再由 LangChain Multi-Agent 生成情报卡片、D1—D7 能力快照、基准对比与 Markdown 简报。

当前仓库完成了第一至第三周的核心工程工作，重点交付为“数据与 Mini-RAG → LangChain Agent → 情报卡片/能力快照/简报”的闭环。FastAPI 目前只是第三周能力的薄适配层；前端、数据库持久化、完整容器化部署、端到端测试和生产发布属于第四周，本文不将它们标记为已完成。

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
| 第三周：Agent 与对标 | 四类 Prompt；Pydantic 严格输出；Price/Product/Risk/Dimension Agent；并行 Multi-Agent；情报卡片；证据与基准共同驱动的能力快照；16 项基准任务与手工结果导入；Markdown 简报 | `prompts/`、`agents/`、`schemas/`、`benchmarks/` |

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
| `DimensionTaggingAgent` | E1—E3 与 D1—D7 混合标签 | 规则判断与独立模型判断分开保留，支持共识/并集策略，分歧强制复核 |
| `CompareAgent` | 生成能力快照和历史差值 | LCEL 管线；按证据等级、时效、置信度及基准结果计算，去重 Chunk 和 Run |
| `BenchmarkAgent` | 加载任务、导入手工运行、竞品汇总 | 严格任务协议、逐行错误隔离、幂等去重、原子写入 CSV |
| `BriefingAgent` | 汇总卡片和快照 | LCEL 生成可审阅 Markdown，保留证据链接和人工复核项 |
| `MultiAgentOrchestrator` | 并行调度与结果汇合 | Price/Product/Risk 并行，随后可选 Compare、Benchmark 与 Briefing 阶段 |

### 情报卡片与能力快照

`IntelligenceCard` 不是自由文本。它包含事件、变化前后、影响、机会、威胁、建议动作、置信度、优先级、预警等级、结构化发现、D1—D7 影响方向以及完整证据引用。

关键约束包括：

- 每条发现和能力影响必须引用卡片证据内已存在的 `chunk_id`；
- 无证据结果会降低置信度和优先级，标记 `review_required`，不会伪装成确定结论；
- 优先级固定按事件影响 35%、紧急程度 25%、证据置信度 20%、产品相关性 20% 计算；
- 威胁等级与预警等级分离，卡片 ID 由规范化内容稳定生成；
- 检索冲突、分析假设、运行模式、模型名、RAG 查询 ID 和执行 Trace 可供审计。

能力快照由 `config/scoring.yaml` 控制。目前的 `week3-evidence-v2` 规则将证据分与基准分按 60%/40% 合并；证据侧考虑 A—D 权威等级、发布时间、置信度和正负影响，基准侧考虑任务成功、测试通过、编译结果、人工干预及危险操作。没有证据或基准结果的维度标记为 `insufficient_evidence`，不会填充虚构分数。

### 16 项基准任务

`benchmarks/tasks/tasks.jsonl` 固定提供 16 项任务，覆盖 8 类场景，每类 2 项：函数补全、编译修复、测试修复、多文件修改、重构、代码解释、测试生成和安全审查。

每个任务记录成功标准、验证方式、公平性约束、任务修订号、协议版本和 SHA-256 指纹。手工运行记录包括产品/模型版本、成功状态、编译与测试结果、交互轮次、延迟、人工干预、成本、危险操作和带时区的运行时间。详细执行协议见 `benchmarks/tasks/README.md`。

## 目录结构

```text
competitor-analysis-system/
├── agents/                 # 第三周 LCEL Agent、回调、LLM 适配和编排器
├── artifacts/week3/        # 五家竞品首版卡片、能力快照、工作流摘要与简报
├── benchmarks/
│   ├── tasks/              # 16 项固定任务及执行协议
│   ├── results/            # 手工运行 CSV
│   ├── repositories/       # 基准起始仓库预留位置
│   └── validators/         # 自动验证器预留位置
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
├── scripts/                # 数据、索引、评测与环境检查入口
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
DEEPSEEK_MODEL=deepseek-v4-pro
CODERADAR_LLM_TIMEOUT_SECONDS=45
CODERADAR_LLM_MAX_TOKENS=1600
CODERADAR_LLM_MAX_RETRIES=2
```

| 模式 | 是否调用 DeepSeek | 行为 |
|---|---:|---|
| `rules` | 否 | 确定性 LCEL + Mini-RAG + Pydantic，适合离线测试和可重复基线 |
| `hybrid` | 是 | 推荐的在线模式；模型润色并补充结构化判断，非法输出或失败时回退规则 |
| `llm` | 是 | 当前同样保留 RAG 白名单、确定性评分和完整校验，最终卡片记录为受保护的混合分析，而不是无约束纯模型输出 |

未配置 `DEEPSEEK_API_KEY` 时，即使选择在线模式也不会构造远程模型客户端。模型端默认应使用支持结构化输出的 `deepseek-v4-pro`。不要把 Token、完整 Prompt 或原始敏感内容写入日志；当前 Callback 只记录执行元数据。

GitHub 匿名采集可以运行，但配置只读 `GITHUB_TOKEN` 能提高公开 API 请求限额：

```dotenv
GITHUB_TOKEN=你的只读令牌
```

## 数据与 Mini-RAG

查看采集计划，不发送请求：

```powershell
python -m scripts.data_pipeline crawl `
  --competitors cursor,github_copilot `
  --since-days 90 `
  --dry-run
```

执行采集和结构化处理：

```powershell
python -m scripts.data_pipeline crawl --competitors all --since-days 90
python -m scripts.data_pipeline process
```

规则变化后可以从已有原始响应重建派生数据；该操作不重新访问网站：

```powershell
python -m scripts.data_pipeline process --rebuild
```

原始数据位于 `data/raw/<competitor>/<source_type>/<crawl_run_id>/`，结构化文档位于 `data/cleaned/documents.jsonl`。处理器执行 URL/Unicode/空白规范化、SHA-256 去重、稳定 `document_id` 与版本化 `version_id` 管理，并写入事件、能力和证据等级。

Mini-RAG 需要可用的 Elasticsearch。仓库中的 Compose 配置可用于本地启动 Elasticsearch，但它只是开发基础设施，不代表第四周容器化部署已经交付：

```powershell
docker compose up -d elasticsearch
python -m scripts.audit_documents
python -m scripts.build_index
```

增量更新与命令行查询：

```powershell
python -m scripts.build_index --incremental
python .\rag_query.py "Cursor 最近有哪些 Agent 能力更新" --competitor Cursor --top-k 8
```

默认 `hash + lexical` 配置用于确定性冒烟测试；正式检索可以使用 `config/mini_rag.formal.yaml` 中的多语言 Embedding 和 Cross-Encoder。更换 Embedding 模型或维度后必须全量重建物理索引，避免不同向量空间混用。

## 运行第三周 Agent

### 启动薄 API

先确保 Elasticsearch 和读取别名可用，再启动：

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
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
  -Uri 'http://127.0.0.1:8000/api/agent/product' `
  -ContentType 'application/json; charset=utf-8' `
  -Body $body
```

Price、Product、Risk 的事件类型必须分别为 `pricing_change`、`product_release`、`risk_experience`；也可以省略 `event_type`，由所调用的专业 Agent 固定其事件范围。

### 直接运行 Multi-Agent

编排器和能力标签 Agent 已作为第三周核心类实现，但尚未暴露专用 HTTP 路由。可以直接调用：

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
python -m scripts.generate_week3_baseline --mode rules --top-k 8
```

命令会先校验 Mini-RAG 索引与 Embedding 是否兼容，再从真实检索证据生成 `artifacts/week3/`：

- `manifest.json`：生成模式、评分版本、物理索引、Embedding、任务/运行数量；
- `intelligence_cards.json`：Price/Product/Risk 的严格结构化情报卡片；
- `capability_snapshots.json`：五家竞品的 D1—D7 首版能力快照；
- `workflow_summary.json`：分支状态、证据量、覆盖率和降级警告；
- `briefings/*.md`：五份可人工复核的竞争态势简报。

仓库当前基线使用可复现的 `rules` 模式，包含 5 家竞品、15 张卡片、5 份快照和 5 份简报。某个专业分支未检索到证据时仍会返回低置信复核卡，但不会伪造证据；对应工作流摘要会保留警告。需要在线模型措辞时可改用 `hybrid`，但正式对比前应固定模型版本并重新完成人工复核。

## 手工基准结果导入

CSV 表头必须为：

```text
run_id,competitor,task_id,product_version,model,task_success,compile_success,test_pass_rate,edit_rounds,latency_ms,manual_intervention,estimated_cost,harmful_action,notes,run_at
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
  -Uri 'http://127.0.0.1:8000/api/benchmarks/runs/import' `
  -ContentType 'application/json; charset=utf-8' `
  -Body $body
```

导入逐行校验：错误行被隔离，未知 `task_id` 被拒绝，重复 `run_id` 幂等跳过；成功结果通过同目录临时文件原子更新 `benchmarks/results/manual_runs.csv`。

## 当前 API（第三周薄适配）

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
| `GET /api/agent/cards`、`GET /api/agent/cards/{card_id}` | 查询当前进程内的情报卡片 |
| `POST /api/agent/compare`、`GET /api/agent/snapshots` | 生成/查询当前进程内的能力快照 |
| `POST /api/agent/briefing` | 生成 Markdown 简报 |
| `GET /api/benchmarks/tasks`、`GET /api/benchmarks/results` | 查询任务与手工运行 |
| `POST /api/benchmarks/runs/import`、`GET /api/benchmarks/compare` | 持久化导入与竞品汇总 |

需要特别注意：卡片和能力快照目前保存在 API 进程内存中，重启会丢失；Benchmark 手工结果已经持久化为 CSV。这是有意保留给第四周数据库与任务编排工作的边界。

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

第三周测试覆盖真实 LangChain Runnable/StructuredTool、结构化输出重试与非法引用回退、无证据降级、冲突记录、Dimension 规则/模型合并、16 项 Benchmark 协议与幂等导入、Compare 评分、Briefing 证据链接，以及 Multi-Agent 并行、故障隔离、缓存和工作流稳定性。

显式网络测试默认不会运行。需要自行检查公开网络时：

```powershell
$env:CODERADAR_RUN_NETWORK_TESTS = '1'
python -m pytest tests\test_network_smoke.py -m network -q
Remove-Item Env:CODERADAR_RUN_NETWORK_TESTS
```

## 第三周边界与第四周接续

第三周交付的是可测试的领域核心和薄 API，不包含以下第四周工作：

- React/Vue 等前端、图表、筛选交互和完整视觉验收；
- Intelligence Card、Snapshot、Report、Workflow Trace 的数据库持久化和迁移；
- Multi-Agent/Dimension 的正式 API、异步任务、队列、取消、进度与并发限流；
- 身份认证、权限、速率限制、审计日志脱敏和生产密钥托管；
- LLM Token/成本统计、预算熔断、线上观测告警与回放工具；
- 完整 Docker 镜像、环境编排、健康策略和部署文档；
- 浏览器端到端测试、真实模型契约测试、压力测试和发布验收。

第四周应在不改写第三周 Pydantic 契约和证据守卫的前提下，优先完成持久化、异步执行、API 收口、前端展示、可观测性、E2E 与部署。详细验收证据和接续建议见 `docs/第三周验收与第四周开发接续说明.md`。

## 安全与可复现约定

- `.env`、Token、模型缓存、运行时输出和原始抓取响应按 `.gitignore` 管理；
- GitHub Token 只需要公开仓库只读权限；采集器遵守 `robots.txt`、按域名限速并使用有限重试；
- Benchmark 比较必须使用相同任务修订、起始材料、产品/模型记录和验收方式；
- Agent 结论必须能回到 `chunk_id`、原文片段和来源 URL；无证据不得给出高置信结论；
- 生产或正式汇报前，必须复核冲突、低置信、危险操作和 `review_required` 项。
