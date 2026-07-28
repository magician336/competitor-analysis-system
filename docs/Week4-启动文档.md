# Week4 启动文档：数据库 + 后端 + Worker + 前端

> 本文档面向首次部署 CodeRadar Week4 的开发者。涵盖手动分步启动和 docker-compose 一键启动两种方式。

**项目路径**：所有命令从 `Proj/competitor-analysis-system/` 目录执行，除非另有说明。  
**Python 环境**：conda 环境 `CodeRadar`（Python 3.11.9），见下方激活命令。  
**Node 环境**：Node 24+，前端独立运行。

---

## 目录

1. [前置条件](#1-前置条件)
2. [方式一：手动分步启动（推荐开发调试）](#2-方式一手动分步启动推荐开发调试)
   - [2.1 环境准备](#21-环境准备)
   - [2.2 启动 Elasticsearch](#22-启动-elasticsearch)
   - [2.3 构建 RAG 索引](#23-构建-rag-索引)
   - [2.4 初始化数据库](#24-初始化数据库)
   - [2.5 启动后端 API](#25-启动后端-api)
   - [2.6 启动异步 Worker](#26-启动异步-worker)
   - [2.7 启动前端](#27-启动前端)
3. [方式二：docker-compose 一键启动](#3-方式二docker-compose-一键启动)
4. [验证启动是否成功](#4-验证启动是否成功)
5. [常见问题](#5-常见问题)
6. [服务端口速查](#6-服务端口速查)

---

## 1. 前置条件

| 条件 | 要求 | 验证命令 |
|---|---|---|
| Python 3.11.9 | conda 环境 `CodeRadar` | `conda activate CodeRadar && python --version` |
| Node 24+ | 前端构建需要 | `node --version` |
| Docker | 运行 ES 或 compose 需要 | `docker --version` |
| Week2 RAG 数据 | `data/cleaned/documents.jsonl` 存在 | `ls data/cleaned/documents.jsonl` |
| Week3 基线产物（可选） | `artifacts/week3/manifest.json` 存在 | `ls artifacts/week3/manifest.json` |

**安装依赖**（首次或依赖更新时）：

```powershell
conda activate CodeRadar
python -m pip install -r requirements.txt
python -m pip check
```

**前端依赖**：

```powershell
cd frontend
npm install
cd ..
```

---

## 2. 方式一：手动分步启动（推荐开发调试）

### 2.1 环境变量

从 `.env.example` 复制并编辑 `.env`：

```powershell
copy .env.example .env
```

至少配置以下关键变量（`．env` 已 gitignore，不会泄露）：

```ini
# 必须：Mini-RAG 检索后端
MINIRAG_ELASTICSEARCH_URL=http://localhost:9200
MINIRAG_EMBEDDING_PROVIDER=hash          # 离线确定性模式，免模型下载
MINIRAG_CONFIG_PATH=config/mini_rag.yaml

# Agent 模式（默认 rules = 离线确定性，不需要 API Key）
CODERADAR_AGENT_MODE=rules

# 可选：DeepSeek 在线模型（仅 hybrid/llm 模式需要）
# DEEPSEEK_API_KEY=sk-your-key-here

# API 认证（默认开启）
CODERADAR_AUTH_ENABLED=true
CODERADAR_API_KEY=coderadar-phase3-local-demo
```

> **重要**：前端使用登录 Cookie，不再读取 `VITE_API_KEY`；`CODERADAR_API_KEY` 只供脚本和外部 API 调用。

### 2.2 启动 Elasticsearch

```powershell
docker compose up -d elasticsearch
```

等待 ES 就绪（约 10-30 秒）：

```powershell
# 方式一：等待 healthcheck
docker compose ps elasticsearch    # 看是否 healthy

# 方式二：直接 curl
curl -s http://localhost:9200/_cluster/health?wait_for_status=yellow
```

### 2.3 构建 RAG 索引

```powershell
conda activate CodeRadar

# 先校验文档数据
python -m scripts.audit_documents

# 全量构建索引（首次必须，后续可用 --incremental）
python -m scripts.build_index
```

成功后输出类似：

```
index builder  | building index from 0 ids
index manager  | created index coderadar_chunks_v20260720_123456
index builder  | indexed 2633 chunks
index manager  | switched read alias coderadar_chunks_current → coderadar_chunks_v20260720_123456
```

> **切换 BGE-M3 向量模型**：`python -m scripts.build_index --config config\mini_rag.formal.yaml`（需要 GPU 或 CPU torch，并且会下载模型）。注意切换后 `/ready` 会校验查询端与索引端模型一致性。

### 2.4 初始化数据库

**第 1 步：运行 Alembic 迁移建表**

```powershell
conda activate CodeRadar
python -m alembic upgrade head
```

输出：
```
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_sqlite_persistence, ...
INFO  [alembic.runtime.migration] Running upgrade 0001_sqlite_persistence -> 0002_async_workflows, ...
INFO  [alembic.runtime.migration] Running upgrade 0002_async_workflows -> 0003_api_closure, ...
```

验证表已创建：

```powershell
python -c "from backend.database import get_database; db=get_database(); print([t.name for t in db.engine.table_names()])"
```

应该能看到 15 张表（14 张业务表 + `alembic_version`）。

**第 2 步：导入竞品配置和 Week3 基线产物**

```powershell
# 仅导入竞品配置（最少启动所需）
python -m scripts.init_database --seed-competitors

# 或者同时导入 Week3 基线产物（推荐，这样首页/卡片/快照有历史数据）
python -m scripts.init_database --seed-competitors --import-artifacts artifacts\week3

# 或者全量（包括 CodeMate 基准）
python -m scripts.init_database --seed-competitors --import-artifacts artifacts\week3 --seed-codemate
```

输出示例：
```json
{
  "imported_artifacts": ["manifest.json"],
  "competitors": 5,
  "cards": 35,
  "snapshots": 5,
  "briefings": 5
}
```

> `init_database` 是幂等的，重复运行安全。

### 2.5 启动后端 API

```powershell
conda activate CodeRadar

# 设置 Agent 模式（rules 离线确定性，无需 API Key）
$env:CODERADAR_AGENT_MODE = 'rules'

# 启动 API（开发模式，热重载）
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

API 启动时自动校验：
1. `check_connection()` — SQLite 数据库可达
2. `check_migration()` — alembic 版本是 `0003_api_closure`
3. `auth_enabled` + `api_key` — 认证开启时必须有 key

成功输出：
```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

测试 API 是否正常：

```powershell
# 健康检查（不需要 API Key）
curl http://127.0.0.1:8000/health

# 就绪检查
curl http://127.0.0.1:8000/ready

# 卡片列表（需要 X-API-Key 头）
curl -H "X-API-Key: coderadar-phase3-local-demo" http://127.0.0.1:8000/api/cards
```

### 2.6 启动异步 Worker

**新开一个终端**：

```powershell
conda activate CodeRadar
$env:CODERADAR_AGENT_MODE = 'rules'
python -m scripts.run_workflow_worker
```

Worker 也做同样的 DB 校验，然后进入轮询：

```
workflow worker | database connection OK
workflow worker | migration is up to date (0003_api_closure)
workflow worker | worker started, polling every 1s
```

Worker 会：
- 每 1 秒检查 `workflows` 表有无 `QUEUED` 状态的任务
- 领取任务后每 10 秒心跳续租
- 执行完成后更新状态为 `success` / `partial_failure` / `failed`

### 2.7 启动前端

**新开一个终端**：

```powershell
cd frontend

# 配置 API 目标（默认已指向 127.0.0.1:8001，这里手动跑 API 在 8000）
# 修改 .env 或直接设置环境变量
$env:VITE_API_TARGET = 'http://127.0.0.1:8000'

npm run dev
```

成功输出：
```
VITE v7.x.x  ready in 342ms
  ➜  Local:   http://127.0.0.1:5173/
  ➜  Network: use --host to expose
```

打开浏览器访问 `http://127.0.0.1:5173`。

---

## 3. 方式二：docker-compose 一键启动

> **注意**：compose 启动前必须先执行 **手动初始化** 第 2.4 步（`alembic upgrade head` + `init_database`），因为 API 和 Worker 容器启动时会 fail-fast 校验数据库迁移版本。

```powershell
# 1. 先手动初始化数据库
conda activate CodeRadar
python -m alembic upgrade head
python -m scripts.init_database --seed-competitors --import-artifacts artifacts\week3

# 2. 构建 RAG 索引
python -m scripts.build_index
```

**修改 `.env`**，确保 compose 使用的配置正确：

```ini
# compose 会读取这些变量
CODERADAR_AUTH_ENABLED=true
CODERADAR_API_KEY=coderadar-phase3-local-demo
CODERADAR_AGENT_MODE=rules
MINIRAG_EMBEDDING_PROVIDER=hash
```

**启动所有服务**：

```powershell
docker compose up -d --build
```

等待所有服务就绪（约 30-60 秒）：

```powershell
docker compose ps
```

期望状态：

```
NAME                   IMAGE                        STATUS
competitor-analysis-system-elasticsearch-1   ...:9.4.1              Up (healthy)
competitor-analysis-system-api-1             ...                    Up (healthy)
competitor-analysis-system-worker-1          ...                    Up (healthy)
```

**启动前端**（前端不在 compose 内，需要宿主机独立运行）：

```powershell
cd frontend
npm run dev
```

> compose 中 API 端口默认映射到 **8001**（`docker-compose.yml:53`），所以前端 `VITE_API_TARGET` 应指向 `http://127.0.0.1:8001`（`frontend/.env.example` 的默认值）。

---

## 4. 验证启动是否成功

### 4.1 API 健康检查

```powershell
curl http://localhost:8000/health
# → {"status":"ok"}
```

### 4.2 就绪状态（含 ES + 索引校验）

```powershell
curl http://localhost:8000/ready
# → {"status":"ok","backend":{"status":"green"},"index":"coderadar_chunks_v20260720_...","indexed_chunks":2633}
```

### 4.3 前端页面

| 页面 | URL | 验证内容 |
|---|---|---|
| 首页 | `http://localhost:5173/` | 系统状态卡片、趋势事件流、能力星图 |
| AI 随问 | `http://localhost:5173/ask` | 可输入问题并获取带引用的回答 |
| 深度报告 | `http://localhost:5173/analysis` | 可选竞品、分析模式、分支，提交工作流 |

### 4.4 提交一个测试工作流

```powershell
curl -X POST http://localhost:8000/api/workflows `
  -H "Content-Type: application/json" `
  -H "X-API-Key: coderadar-phase3-local-demo" `
  -d '{"competitor":"cursor","analysis_mode":"rules","branches":["product"],"question":"总结Cursor近期产品能力变化"}'
```

正常返回 202：

```json
{"workflow_id":"workflow_abc123...","status":"queued","status_url":"/api/workflows/workflow_abc123..."}
```

轮询状态：

```powershell
curl -H "X-API-Key: coderadar-phase3-local-demo" `
  http://localhost:8000/api/workflows/workflow_abc123...
```

### 4.5 Ask 问答测试

```powershell
curl -X POST http://localhost:8000/api/ask `
  -H "Content-Type: application/json" `
  -H "X-API-Key: coderadar-phase3-local-demo" `
  -d '{"question":"Cursor 最近有哪些 Agent 能力更新","top_k":5}'
```

返回应包含 `answer`（带 `[n]` 引用标记）和 `references`（证据列表）。

---

## 5. 常见问题

### 5.1 API 启动报错：数据库未初始化

```
RuntimeError: database schema is missing; run `python -m alembic upgrade head` first
```

**解决**：运行 `python -m alembic upgrade head`

### 5.2 API 启动报错：Alembic 版本不匹配

```
RuntimeError: database revision is '0002_async_workflows'; expected '0003_api_closure'
```

**解决**：运行 `python -m alembic upgrade head` 升级到最新迁移

### 5.3 API 启动报错：缺少 API Key

```
RuntimeError: CODERADAR_API_KEY is required when CODERADAR_AUTH_ENABLED is true
```

**解决**：在 `.env` 中设置 `CODERADAR_API_KEY`，或设置 `CODERADAR_AUTH_ENABLED=false`

### 5.4 Worker 启动后不执行任务

Worker 使用单例租约，多个 Worker 不会冲突。检查：
- 是否已提交工作流（`POST /api/workflows`）
- Worker 日志是否有 `claim_next` 输出
- 工作流状态是否为 `queued`

### 5.5 前端提示 401 Unauthorized

**原因**：API Key 不匹配  
**解决**：
- 浏览器请求请先登录；脚本请求则检查后端 `CODERADAR_API_KEY`
- 或者在浏览器 sessionStorage 中设置 `apiKey`

### 5.6 前端提示 429 Too Many Requests

**原因**：触发了限流（读 120 次/分钟，写 30 次/分钟）  
**解决**：等待 1 分钟后自动恢复，或调大 `CODERADAR_READ_RATE_PER_MINUTE` / `CODERADAR_WRITE_RATE_PER_MINUTE`

### 5.7 compose 中 API/Worker 启动后崩溃重启循环

**原因**：数据库未初始化（容器内没有 alembic 迁移）  
**解决**：退出 compose，在宿主机执行 `python -m alembic upgrade head && python -m scripts.init_database --seed-competitors --import-artifacts artifacts\week3`，再重启 compose

### 5.8 索引构建失败：ES 未就绪

```
elasticsearch.exceptions.ConnectionError: Connection refused
```

**解决**：等 ES 就绪后再重试 `python -m scripts.build_index`

### 5.9 `frontend/.env` 不存在

```powershell
copy frontend\.env.example frontend\.env
```

---

## 6. 服务端口速查

| 服务 | 手动模式 | docker-compose 模式 | 说明 |
|---|---|---|---|
| Elasticsearch | `127.0.0.1:9200` | `127.0.0.1:9200` | compose 映射相同端口 |
| FastAPI 后端 | `127.0.0.1:8000` | `127.0.0.1:8001` | compose 默认用 8001 |
| 前端 Vite | `127.0.0.1:5173` | `127.0.0.1:5173` | 前端始终在宿主机独立运行 |
| Worker | 独立终端进程 | 容器内自动启动 | 无对外端口 |

---

## 附录：完整启动流程速查（手动模式）

```powershell
# 终端 1：ES
docker compose up -d elasticsearch

# 终端 1（等 ES 就绪后）：
conda activate CodeRadar
python -m scripts.build_index
python -m alembic upgrade head
python -m scripts.init_database --seed-competitors --import-artifacts artifacts\week3
$env:CODERADAR_AGENT_MODE = 'rules'
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 终端 2：
conda activate CodeRadar
$env:CODERADAR_AGENT_MODE = 'rules'
python -m scripts.run_workflow_worker

# 终端 3：
cd frontend
$env:VITE_API_TARGET = 'http://127.0.0.1:8000'
npm run dev
```

打开浏览器：`http://127.0.0.1:5173`
