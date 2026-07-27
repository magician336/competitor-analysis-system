# Week4 启动文档（正式版）：BGE-M3 索引 + DeepSeek LLM + llm 模式

> 本文档适用于需要**完整在线能力**的正式部署场景：
> - RAG 检索使用 **BGE-M3** 多语言嵌入模型 + Cross-Encoder 重排序
> - Agent 分析使用 **DeepSeek 在线大模型**（`llm` 严格模式）
> - 需要 GPU 或 CPU torch + 模型下载

---

## 目录

1. [前置条件](#1-前置条件)
2. [方式一：手动分步启动（推荐）](#2-方式一手动分步启动推荐)
3. [方式二：docker-compose 启动](#3-方式二docker-compose-启动)
4. [验证正式版功能](#4-验证正式版功能)
5. [关键配置差异对比](#5-关键配置差异对比)
6. [常见问题](#6-常见问题)

---

## 1. 前置条件

### 1.1 硬件与系统

| 条件 | 要求 |
|---|---|
| Python | 3.11.9（conda 环境 `CodeRadar`） |
| 磁盘空间 | BGE-M3 模型 ~2.2 GB，Cross-Encoder ~1.5 GB，合计约 **4 GB** |
| 内存 | 最少 8 GB（加载两个模型 + ES） |
| 网络 | 首次需要访问 HuggingFace 下载模型 |
| Node | 24+（前端） |
| Docker | 运行 ES 需要 |

### 1.2 DeepSeek API Key

注册 [DeepSeek 平台](https://platform.deepseek.com/) 获取 API Key，充值后可用。

### 1.3 安装依赖

```powershell
conda activate CodeRadar

# 全量依赖（含 torch + sentence-transformers，需数分钟）
python -m pip install -r requirements.txt

# 验证 torch 安装成功
python -c "import torch; print(torch.__version__)"
```

### 1.4 数据前提

```powershell
# Week1-2 采集清洗的文档必须存在
ls data/cleaned/documents.jsonl

# Week3 基线产物（可选但有更好）
ls artifacts/week3/manifest.json
```

---

## 2. 方式一：手动分步启动（推荐）

### 2.1 配置 `.env`

```powershell
copy .env.example .env
```

编辑 `.env`，填入以下内容：

```ini
# ─── Mini-RAG：BGE-M3 正式检索 ───
MINIRAG_ELASTICSEARCH_URL=http://localhost:9200
MINIRAG_PROFILE=formal                       # ← 一次选择整套正式配置
MINIRAG_CONFIG_PATH=                        # ← 留空，避免覆盖 profile
MINIRAG_INSTALL_ML=true
MINIRAG_MODEL_CACHE_DIR=.cache/models

# ─── Agent：DeepSeek 在线严格模式 ───
CODERADAR_AGENT_MODE=llm                        # ← llm 严格模式
CODERADAR_LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-你的真实Key                # ← 必填，否则报错
DEEPSEEK_MODEL=deepseek-chat
CODERADAR_LLM_TIMEOUT_SECONDS=60
CODERADAR_LLM_MAX_TOKENS=4096
CODERADAR_LLM_MAX_RETRIES=2

# ─── API 认证 ───
CODERADAR_AUTH_ENABLED=true
CODERADAR_API_KEY=coderadar-phase3-local-demo
CODERADAR_READ_RATE_PER_MINUTE=120
CODERADAR_WRITE_RATE_PER_MINUTE=30
```

路径配置规则：

- `.env` 和 YAML 中的相对文件路径统一以项目根目录为基准，不受终端当前目录、IDE 或任务调度器影响；
- 本地与 Docker Compose 共用 `data/...`、`config/...`、`.cache/...` 形式，不要写 `/app/CodeRadar`、Windows 盘符或用户目录；
- `MINIRAG_PROFILE=formal` 已选择 `config/mini_rag.formal.yaml`。只有临时使用自定义 YAML 时才设置 `MINIRAG_CONFIG_PATH`，且它的优先级高于 profile；
- `CODERADAR_DATABASE_URL=sqlite:///data/runtime/coderadar.db` 会稳定解析到项目的 `data/runtime/`。

> **重要**：`CODERADAR_AGENT_MODE=llm` 是**严格模式**，DeepSeek API 不可用时任务直接失败，不回退到规则。如果希望在线分析但允许失败时回退，可以用 `hybrid` 模式。

### 2.2 启动 Elasticsearch

```powershell
docker compose up -d elasticsearch
```

等待 ES 就绪：

```powershell
curl -s http://localhost:9200/_cluster/health?wait_for_status=yellow
```

### 2.3 构建 BGE-M3 索引

首次运行时会自动下载 BGE-M3 模型（~2.2 GB）和 Cross-Encoder（~1.5 GB），耗时取决于网络：

```powershell
conda activate CodeRadar

# 使用正式配置全量构建索引
# 第一次会下载模型，请保持网络通畅
python -m scripts.build_index --config config\mini_rag.formal.yaml
```

预期输出：

```
embedding        | loading model BAAI/bge-m3...         # 首次下载 2.2 GB
embedding        | model loaded into cache: .cache\models
index manager    | created index coderadar_chunks_v20260720_...
index builder    | indexed 2633 chunks
index manager    | switched read alias coderadar_chunks_current → coderadar_chunks_v20260720_...
```

> **注意**：BGE-M3 和 hash 模式的**向量空间不同**。使用 BGE-M3 索引后，查询端也必须使用 `mini_rag.formal.yaml` 配置。`/ready` 接口会校验查询端与索引端模型一致性，不一致返回 503 degraded。

### 2.4 初始化数据库

```powershell
python -m alembic upgrade head
python -m scripts.init_database --seed-competitors --import-artifacts artifacts\week3 --seed-codemate
```

> `init_database` 是幂等的，重复运行安全。

### 2.5 启动后端 API

```powershell
conda activate CodeRadar

# llm 模式：必须有 DEEPSEEK_API_KEY，否则启动时 agent 构造报错
$env:CODERADAR_AGENT_MODE = 'llm'

python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

API 启动时校验链：
1. `check_connection()` — SQLite 可达
2. `check_migration()` — alembic 版本 `0003_api_closure`
3. `auth_enabled` + `api_key` — 认证开启时必须有 key

### 2.6 启动异步 Worker

**新开终端**：

```powershell
conda activate CodeRadar
$env:CODERADAR_AGENT_MODE = 'llm'
python -m scripts.run_workflow_worker
```

Worker 同样校验 DB，然后进入轮询等待任务。

### 2.7 前端配置

创建或编辑 `frontend/.env`：

```powershell
cd frontend
copy .env.example .env
```

确保内容：

```ini
VITE_API_TARGET=http://127.0.0.1:8000
```

启动前端：

```powershell
npm run dev
```

打开浏览器 `http://127.0.0.1:5173`。

---

## 3. 方式二：docker-compose 启动

> compose 启动前需在宿主机完成数据库初始化和索引构建。

### 3.1 宿主机初始化

```powershell
conda activate CodeRadar

# 数据库
python -m alembic upgrade head
python -m scripts.init_database --seed-competitors --import-artifacts artifacts\week3

# BGE-M3 索引（需下载模型，耗时较长）
python -m scripts.build_index --config config\mini_rag.formal.yaml
```

### 3.2 修改 `.env` 供 compose 读取

确保 `.env` 包含 compose 使用的变量：

```ini
CODERADAR_AUTH_ENABLED=true
CODERADAR_API_KEY=coderadar-phase3-local-demo
CODERADAR_AGENT_MODE=llm
CODERADAR_LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-你的真实Key
DEEPSEEK_MODEL=deepseek-chat

MINIRAG_PROFILE=formal
MINIRAG_CONFIG_PATH=
MINIRAG_INSTALL_ML=true          # ← 容器内安装 torch + sentence-transformers
```

### 3.3 构建并启动

```powershell
# 使用 INSTALL_ML=true 构建包含 ML 依赖的镜像
$env:MINIRAG_INSTALL_ML = 'true'
docker compose build --build-arg INSTALL_ML=true
docker compose up -d

# 验证
docker compose ps
```

三个服务都应 `Up (healthy)`。

> **注意**：容器内首次启动 API 时不会自动构建索引，因为索引已在宿主机建好。容器通过绑定挂载 `./data` 卷共享 `data/runtime/` 和 `data/raw/` 等目录。

### 3.4 启动前端

```powershell
cd frontend
npm run dev
```

compose 中 API 端口默认映射到 **8001**，前端 `VITE_API_TARGET` 应指向 `http://127.0.0.1:8001`。

---

## 4. 验证正式版功能

### 4.1 验证 RAG 检索使用 BGE-M3

```powershell
curl http://localhost:8000/ready
```

响应中的 `index` 字段应显示 BGE-M3 索引名称：

```json
{
  "status": "ok",
  "backend": {"status": "green"},
  "index": "coderadar_chunks_v20260720_...",
  "indexed_chunks": 2633,
  "embedding_model": "BAAI/bge-m3",
  "reranker": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
}
```

### 4.2 验证 Ask 问答（使用 BGE-M3 + DeepSeek）

```powershell
curl -X POST http://localhost:8000/api/ask `
  -H "Content-Type: application/json" `
  -H "X-API-Key: coderadar-phase3-local-demo" `
  -d '{"question":"Cursor 和 GitHub Copilot 的 Agent 能力有哪些关键差异","top_k":8}'
```

成功时应返回：
- `answer_mode`: `"hybrid"`（Ask 接口的 LLM 模式）
- `answer`: 带 `[n]` 引用的中文回答
- `references`: 证据列表（每条含 chunk_id/quote/URL/分数）

### 4.3 验证工作流（使用 DeepSeek llm 模式）

```powershell
curl -X POST http://localhost:8000/api/workflows `
  -H "Content-Type: application/json" `
  -H "X-API-Key: coderadar-phase3-local-demo" `
  -d '{"competitor":"cursor","analysis_mode":"llm","branches":["product","price","risk"],"include_snapshot":true,"include_briefing":true}'
```

返回 202：

```json
{"workflow_id":"workflow_...","status":"queued","status_url":"/api/workflows/workflow_..."}
```

轮询直到状态为 `success`：

```powershell
curl -H "X-API-Key: coderadar-phase3-local-demo" `
  http://localhost:8000/api/workflows/workflow_...
```

终态响应应包含：
- `branch_results` 中三分支均完成
- `card_ids` 数组（生成的卡片 ID）
- `snapshot_id`（能力快照 ID）
- `briefing_id`（简报 ID）
- `analysis_mode`: `"llm"`

### 4.4 验证前端页面

| 页面 | URL | 验证内容 |
|---|---|---|
| 首页 | `http://localhost:5173/` | 系统状态在线、BGE-M3 索引切片数 |
| AI 随问 | `http://localhost:5173/ask` | 输入问题后获得带引用回答 |
| 深度报告 | `http://localhost:5173/analysis` | 选择 llm 模式 → 提交 → 轮询结果 → 查看卡片/雷达图/简报 |

---

## 5. 关键配置差异对比

| 维度 | 离线基线（hash + rules） | 正式版（BGE-M3 + llm） |
|---|---|---|
| **配置** | `config/mini_rag.yaml` | `config/mini_rag.formal.yaml` |
| **嵌入模型** | `hash`（无依赖，确定性） | `sentence_transformers` + BAAI/bge-m3 |
| **重排序** | `lexical`（词项重叠） | `cross_encoder`（mmarco-mMiniLMv2） |
| **检索质量** | 低（仅词法匹配） | 高（语义 + 多语言 + 跨编码器） |
| **模型下载** | 不需要 | 首次需下载 ~3.7 GB |
| **Agent 模式** | `rules`（离线确定性） | `llm`（严格在线） |
| **API Key 要求** | 无 | 必须 `DEEPSEEK_API_KEY` |
| **LLM 容错** | N/A（纯规则） | 无容错，API 失败 = 任务失败 |
| **可复现性** | 完全确定 | 模型输出有随机性 |
| **适用场景** | 开发调试 / 单元测试 | 正式分析 / 报告生成 |

### 向量空间重要提醒

```
hash 向量空间（1024 维） ≠ BGE-M3 向量空间（1024 维）
```

- 两个索引的向量维度相同（均为 1024），但**向量值不同**，属于不同的向量空间
- 切换配置必须**全量重建索引**，不能混用
- 查询服务的配置必须与索引使用的配置一致，否则 `/ready` 返回 503 degraded

### 三种 Agent 模式对比

| 模式 | 行为 | 密钥要求 | 失败时 |
|---|---|---|---|
| `rules`（默认） | 完全离线/确定性 | 不需要 | N/A（纯规则） |
| `hybrid` | 规则底稿 + DeepSeek 润色 | 需要 `DEEPSEEK_API_KEY` | 自动回退规则，记 warning |
| `llm` | 与 hybrid 同链但 strict=True | 需要 `DEEPSEEK_API_KEY` | 直接报错，不回退 |

---

## 6. 常见问题

### 6.1 索引构建失败：模型下载超时

```
requests.exceptions.ConnectionError: HTTPSConnectionPool ... timeout
```

**解决**：设置 HuggingFace 镜像：

```powershell
$env:HF_ENDPOINT = 'https://hf-mirror.com'
python -m scripts.build_index --config config\mini_rag.formal.yaml
```

### 6.2 API 启动时报缺少 DEEPSEEK_API_KEY

```
ValueError: DEEPSEEK_API_KEY is required for mode 'llm'
```

**原因**：`llm` 模式下 `LangChainLLMClient.from_env()` 会检查 API Key，缺失则报错。  
**解决**：在 `.env` 中配置 `DEEPSEEK_API_KEY`，或先用 `hybrid` 模式（有回退）。

### 6.3 /ready 返回 503 degraded

```json
{"status":"degraded","detail":"query embedding model mismatch with index"}
```

**原因**：查询端配置的 embedding provider 与索引构建时使用的 provider 不一致。  
**解决**：确保查询时使用与索引相同的配置文件（`mini_rag.formal.yaml`）。

### 6.4 工作流提交后一直是 pending

**排查**：
1. Worker 是否在运行 → `docker compose ps worker` 或检查 Worker 终端
2. Worker 日志是否有 `claim_next` 输出
3. 工作流状态是否写入 DB → 用 `curl` 轮询 `/api/workflows/{id}`

### 6.5 工作流最终状态为 failed（llm 模式）

**原因**：DeepSeek API 调用失败（超时/认证/限流），`llm` 严格模式下不回退。  
**解决**：
1. 检查 `DEEPSEEK_API_KEY` 是否有效且有余额
2. 检查网络是否能访问 `api.deepseek.com`
3. 考虑改用 `hybrid` 模式（API 失败时自动回退规则）

### 6.6 前端提示 401 Unauthorized

**解决**：浏览器端使用登录 Cookie，不需要配置 API Key；脚本调用仍需检查 `CODERADAR_API_KEY`。

### 6.7 Docker 构建时 INSTALL_ML 导致镜像过大

BGE-M3 需要 torch + sentence-transformers，镜像体积约 2-3 GB。这是正常的。如果不需要 ML，保持 `INSTALL_ML=false`（默认）并使用 hash 模式。

---

## 附录：完整启动流程速查

```powershell
# 终端 1：ES
docker compose up -d elasticsearch

# 终端 1（等 ES 就绪）：
conda activate CodeRadar
python -m scripts.build_index --config config\mini_rag.formal.yaml
python -m alembic upgrade head
python -m scripts.init_database --seed-competitors --import-artifacts artifacts\week3 --seed-codemate
$env:CODERADAR_AGENT_MODE = 'llm'
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 终端 2：
conda activate CodeRadar
$env:CODERADAR_AGENT_MODE = 'llm'
python -m scripts.run_workflow_worker

# 终端 3：
cd frontend
$env:VITE_API_TARGET = 'http://127.0.0.1:8000'
npm run dev
```

打开浏览器：`http://127.0.0.1:5173`
