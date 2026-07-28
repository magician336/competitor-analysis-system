# CodeRadar — AI 编程助手竞品智能分析系统

[![Python 3.11](https://img.shields.io/badge/Python-3.11.9-blue)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-0.139-green)]()
[![Vue 3](https://img.shields.io/badge/Vue_3-3.5-4FC08D)]()
[![License](https://img.shields.io/badge/License-MIT-yellow)]()

面向 **AI 编程助手 / AI IDE / 代码生成 Agent** 赛道的证据驱动竞品分析系统。采集 Cursor、GitHub Copilot、Trae、通义灵码、CodeGeeX 等多源数据，通过 **Mini-RAG 混合检索** 提供可验证证据，由 **LangChain Multi-Agent** 生成情报卡片、D1–D7 能力快照、基准对比与 Markdown 简报。

```text
多源采集 → 清洗/去重/版本化 → Mini-RAG（BM25 + Dense + RRF + 重排）
                                  ├─ Price Agent ─┐
                                  ├─ Product Agent├─ Multi-Agent → 情报卡片 / 能力快照 / 简报
                                  └─ Risk Agent  ─┘
规则标签 → DimensionAgent ──────────┘
手工基准 → BenchmarkAgent ─ CompareAgent → 对比矩阵
```

## 项目结构

```
competitor-analysis-system/
├── agents/                 # LangChain LCEL Agent、编排器、回调
├── alembic/                # 数据库迁移（5 个版本）
├── artifacts/week3/        # 五家基线制品（卡片、快照、简报、验收报告）
├── backend/                # FastAPI 服务：正式 API、RAG、Workflow、Vue 后端
├── benchmarks/             # 16 项冻结任务、验证器、手工运行 CSV
├── config/                 # 竞品、维度、Mini-RAG、评分 YAML 配置
├── crawler/                # 官网、Changelog、Pricing、GitHub 采集器
├── data/                   # 原始响应、清洗文档、评测集、运行时数据
├── docs/                   # 开发与运行文档
├── mini_rag/               # 分块、嵌入、索引、检索、重排、证据、评测
├── processing/             # 清洗、规范化、规则标注、去重、版本管理
├── prompts/                # Agent Prompt 模板
├── schemas/                # Pydantic 数据契约（跨层共享）
├── scripts/                # 数据管线、索引、基线、标签、验收 CLI
├── tests/                  # 离线单元测试 + 显式网络测试
├── frontend/               # Vue 3 + TypeScript + Element Plus + D3
├── .env.example            # 环境变量模板
└── requirements.txt        # Python 3.11 依赖清单
```

## 快速开始

### 环境准备

```powershell
# 创建 Conda 环境（Python 3.11.9）
conda create -n CodeRadar python=3.11.9 -y
conda activate CodeRadar
python -m pip install -r requirements.txt
python -m pip check

# 配置环境变量
Copy-Item .env.example .env
# 编辑 .env：设置 GITHUB_TOKEN、CODERADAR_API_KEY 等
```

### 数据采集与处理

```powershell
# 诊断本地配置（只读，不发网络请求）
python -m scripts.data_pipeline doctor

# 采集竞品数据（--dry-run 预览任务）
python -m scripts.data_pipeline crawl --competitors cursor,github_copilot --since-days 90 --dry-run
python -m scripts.data_pipeline crawl --competitors all --since-days 90

# 处理为结构化文档
python -m scripts.data_pipeline process
# 或是全量重建
python -m scripts.data_pipeline process --rebuild
```

### Mini-RAG 索引与检索

```powershell
# 启动 Elasticsearch
docker compose up -d elasticsearch

# 构建索引
python -m scripts.build_index

# 查询
python rag_query.py "Cursor 最近有哪些 Agent 能力更新" --competitor Cursor --top-k 8
```

### 启动完整服务

```powershell
# Docker Compose 一键部署（3 服务）
docker compose up -d --build
# API: http://127.0.0.1:8001 | 前端需单独启动

# 或手动启动
conda activate CodeRadar
$env:CODERADAR_AGENT_MODE = 'rules'
python -m alembic upgrade head
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 异步 Worker（另开终端）
python -m scripts.run_workflow_worker

# 前端（另开终端）
cd frontend
Copy-Item .env.example .env.local
npm install
npm run dev   # http://127.0.0.1:5173
```

## 核心架构

### 数据流（4 周迭代）

| 周次 | 职责 | 核心输入 | 核心输出 |
|------|------|----------|----------|
| 上游 | 采集竞品公开数据并清洗 | `config/competitors.yaml`、网络来源 | `data/cleaned/documents.jsonl` |
| Week2 | 文档分块 → ES 索引 → 混合检索 + 证据引用 | documents.jsonl、RAGQuery | ES 索引 + RAGResponse |
| Week3 | LangChain Multi-Agent 生成情报/快照/简报 | 编排请求 + Mini-RAG 证据 | IntelligenceCard、CapabilitySnapshot、简报 |
| Week4 | 产物落 SQLite、异步 Worker、正式 API、Vue 前端 | Week3 产物 + HTTP 请求 | 数据库、正式 API、前端、docker-compose |

### 竞品与维度体系

**5 个固定竞品**：`cursor`、`github_copilot`、`trae`、`tongyi_lingma`、`codegeex`

**事件标签 E1–E3**：`pricing_change`（价格变化）、`product_release`（产品发布）、`risk_experience`（风险体验）

**能力标签 D1–D7**：
| 维度 | 含义 |
|------|------|
| D1 `code_intelligence` | 代码智能与生成质量 |
| D2 `agent_context` | 项目级上下文与 Agent 自主性 |
| D3 `ide_ecosystem` | IDE、工具链与开发生态 |
| D4 `model_extensibility` | 模型接入与扩展能力 |
| D5 `performance_cost` | 性能、稳定性与成本效率 |
| D6 `security_compliance` | 安全、隐私与企业合规 |
| D7 `education_fit` | 用户体验与教育场景适配 |

### Agent 架构（第三周）

| Agent | 职责 | 特点 |
|-------|------|------|
| **PriceAgent** | 价格/套餐/优惠变化 | 仅接受 E1 事件 |
| **ProductAgent** | 功能升级/Agent 能力 | 仅接受 E2 事件 |
| **RiskAgent** | Issue/安全/合规风险 | 仅接受 E3 事件，证据负向影响 |
| **DimensionTaggingAgent** | E1–E3 + D1–D7 混合标签 | 规则/模型双通道，分歧标记复核 |
| **CompareAgent** | 能力快照、历史趋势、对比矩阵 | 双 LCEL 管线，证据+基准评分 |
| **BenchmarkAgent** | 16 项冻结任务执行与导入 | SHA-256 指纹、逐行隔离、幂等去重 |
| **BriefingAgent** | Markdown 简报 | 保留证据链接与人工复核项 |
| **MultiAgentOrchestrator** | 并行调度 Price/Product/Risk | RunnableParallel，故障隔离 |

### Agent 模式

| 模式 | 是否调用 DeepSeek | 适用场景 |
|------|:---:|----------|
| `rules` | 否 | 离线确定性基线，测试/可复现快照 |
| `hybrid` | 是 | **推荐在线**：模型润色 + 失败回退规则 |
| `llm` | 是 | 受保护混合分析，保留 RAG 白名单与校验 |

未配置 `DEEPSEEK_API_KEY` 时仅能用 `rules`。

### 第四周新增组件

| 组件 | 说明 |
|------|------|
| **SQLite 持久化** | SQLAlchemy + Alembic 迁移，卡片/快照/简报/Workflow 落库 |
| **异步 Workflow** | 单例 Worker，租约+心跳领任务，部分重试与取消 |
| **正式 API** | `formal_api.py`：分页查询、API-Key 认证、限流、审计、Problem Details |
| **Vue 3 前端** | 趋势首页、AI 情报问答、模型分析三大入口 |
| **Docker Compose** | 3 服务：`elasticsearch` / `api` / `worker` |

## 常用命令速查

### 数据管线

```powershell
python -m scripts.data_pipeline doctor               # 诊断配置
python -m scripts.data_pipeline crawl ...             # 采集
python -m scripts.data_pipeline process [--rebuild]   # 处理
python -m scripts.data_pipeline all ...               # 采集+处理
```

### 索引与检索

```powershell
python -m scripts.build_index                         # 全量构建索引
python -m scripts.build_index --incremental           # 增量同步
python rag_query.py "<问题>" [--competitor X]         # 命令行检索
```

### Agent 分析

```powershell
$env:CODERADAR_AGENT_MODE = 'rules'

# 生成五家基线
python -m scripts.generate_week3_baseline --mode rules --top-k 8 --as-of 2026-07-20

# 生成对标矩阵
python -m scripts.generate_week3_comparison

# 一键验收
python -m scripts.verify_week3_delivery

# 单条标签测试
python -m scripts.tag_dimensions single --mode rules --source-type changelog --title 'Agent update' --content '新增项目级 Agent'
```

### Benchmark

```powershell
python -m benchmarks.validators.run_task --audit            # 审计全部资产
python -m benchmarks.validators.run_task --task-id bench_001 # 运行单项任务
```

### 测试

```powershell
$env:CODERADAR_AGENT_MODE = 'rules'
python -m pytest -m "not network" -q
python -m pytest -m "not network" --cov=crawler --cov=processing --cov=schemas --cov=mini_rag --cov=backend
```

### 前端

```powershell
cd frontend
npm install
npm run dev          # 开发服务器（127.0.0.1:5173）
npm test             # Vitest 组件单测
npm run build        # 生产构建
```

## API 概览

正式 API 端口 **8001**（容器），认证默认开启（`X-API-Key` 请求头）。

| 端点 | 用途 |
|------|------|
| `GET /health`、`GET /ready` | 存活与就绪检查 |
| `POST /api/rag/query` | 混合检索 |
| `POST /api/workflows` | 提交异步分析（返回 202） |
| `GET /api/workflows/{id}` | 查询 Workflow 状态 |
| `GET /api/cards` | 情报卡片分页查询 |
| `GET /api/snapshots` | 能力快照分页查询 |
| `GET/POST /api/comparisons` | 对比矩阵查询与生成 |
| `GET /api/briefings` | 简报列表 |
| `POST /api/agent/price|product|sentiment-risk` | 同步运行专业 Agent |
| `POST /api/benchmarks/runs/import` | 导入手工基准结果 |

完整接口合同见 [`docs/API接口文档.md`](docs/API接口文档.md)。

## 关键约束

- **两套向量空间不可混用**：`hash`（离线开发测试）与 BGE-M3（正式评测）需全量重建索引后切换读别名。
- **raw 不可变，处理可重放**：清洗规则或标签调整后用 `process --rebuild` 从 raw 重建，不发网络请求。
- **Agent 证据可追溯**：所有结论必须追溯到 `chunk_id`、原文片段与来源 URL；无证据不得给高置信结论，无证据维度标 `insufficient_evidence`。
- **评测集人工复核**：`human_review_status=complete` 后才作为正式金标。
- **Benchmark 公平性**：正式比较前须确保相同任务指纹、协议、starter、交互轮次与验证环境。Runner 非安全沙箱，不可信候选在隔离容器/VM 中运行。

## 详细文档

| 文档 | 内容 |
|------|------|
| [`docs/AI编程助手.md`](docs/AI编程助手.md) | 项目规划、产品定位、双层维度体系 |
| [`docs/程序架构文档.md`](docs/程序架构文档.md) | 6 层架构详解 |
| [`docs/项目运行全流程-Week2-4输入输出.md`](docs/项目运行全流程-Week2-4输入输出.md) | 端到端输入/输出/配置 |
| [`docs/API接口文档.md`](docs/API接口文档.md) | 完整 REST API 参考 |
| [`docs/Docker使用指南.md`](docs/Docker使用指南.md) | Docker Compose 部署与运维 |
| [`docs/容器化部署与配置架构.md`](docs/容器化部署与配置架构.md) | 3 层配置覆盖系统、数据分离 |
| [`docs/交付.md`](docs/交付.md) | 资产清单与接续建议 |
| [`docs/main分支代码与目录规范.md`](docs/main分支代码与目录规范.md) | 代码与目录规约 |
| [`docs/Benchmark任务执行方案.md`](docs/Benchmark任务执行方案.md) | 16 项冻结任务执行方案 |
| [`docs/miniRAG-正式评测报告.md`](docs/miniRAG-正式评测报告.md) | 检索评测报告（nDCG@10=0.880） |

## 安全约定

- `.env`、模型缓存、运行时输出、原始抓取响应不进入版本库
- GitHub Token 只需公开仓库只读权限；采集器遵守 `robots.txt`、按域名限速
- 检索文本视为不可信数据，Prompt 隔离其中指令；Agent 模型只能引用白名单内的 `chunk_id`
- Benchmark 比较需相同任务版本与验收方式
- 生产汇报前必须复核 `review_required` 项、冲突与低置信结论

---

> **CodeRadar** — 南京大学 2026 智能体实训项目。产品名 CodeRadar，虚拟自研产品 **CodeMate Campus**（面向高校学生的 AI 编程学习助手）。
