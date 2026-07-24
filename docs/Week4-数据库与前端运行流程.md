# Week4：数据库持久化 + 前端运行流程

> 本文分析 CodeRadar 第四周的数据库初始化、后端启动校验、前端启动与 API 代理、以及 docker-compose 三服务编排——回答「Week4 的数据库和前端是怎么跑起来的」。

---

## 一、总体启动顺序

```mermaid
flowchart LR
    subgraph 手动初始化（仅首次）
        A1["alembic upgrade head<br/>建 14 表 + 3 次迁移"] --> A2
        A2["init_database --seed-competitors<br/>写入竞品配置"] --> A3
        A3["init_database --import-artifacts<br/>导入 Week3 基线产物"]
    end

    subgraph 每次启动
        B1["docker compose up -d<br/>或单独 uvicorn + worker"] --> B2["check_connection()<br/>SELECT 1"]
        B2 --> B3["check_migration()<br/>必须 = 0003_api_closure"]
        B3 --> B4["API / Worker 就绪"]
    end

    subgraph 前端独立
        C1["npm run dev<br/>127.0.0.1:5173"] --> C2["Vite proxy<br/>/api/* → 8001"]
        C2 --> C3["正式 API 面<br/>formal_api + ask + workflows"]
    end

    手动初始化 --> 每次启动
```

| 阶段 | 命令 | 职责 |
|---|---|---|
| 数据库建表 | `python -m alembic upgrade head` | 3 次迁移建 14 张业务表 |
| 初始化数据 | `python -m scripts.init_database --seed-competitors --import-artifacts artifacts\week3` | 写入竞品 + 导入基线产物 |
| 后端服务 | `uvicorn backend.main:app` 或 compose | 启动时校验 DB 连接 + 迁移版本 |
| Worker 服务 | `python -m scripts.run_workflow_worker` | 同上校验 + 轮询队列 |
| 前端开发 | `cd frontend && npm run dev` | Vite + 代理到后端 |

---

## 二、数据库：建表、迁移与初始化

### 2.1 表结构定义

全部模型在 `backend/models.py`（第 29–428 行），`Base = DeclarativeBase`。共 14 张实体表 + 1 张 `alembic_version` 迁移表：

| ORM 类 | 表名 | 核心字段 | 用途 |
|---|---|---|---|
| `CompetitorRecord` | `competitors` | id, name, display_name, source_types, config_version | 竞品配置快照 |
| `IntelligenceCardRecord` | `intelligence_cards` | id, competitor, event_type, priority, confidence, payload JSON | 情报卡片 |
| `CardEvidenceRecord` | `card_evidence` | card_id, chunk_id, quote, url, evidence_level | 卡片证据链接 |
| `CapabilitySnapshotRecord` | `capability_snapshots` | id, competitor, scores JSON, coverage_ratio | D1–D7 快照 |
| `SnapshotCardRecord` | `snapshot_cards` | snapshot_id, card_id | 快照-卡片关联 |
| `BriefingRecord` | `briefings` | id, competitor, content_hash, markdown_content | Markdown 简报 |
| `WorkflowRecord` | `workflows` | id, status, request JSON | 工作流队列 |
| `WorkflowBranchRecord` | `workflow_branches` | workflow_id, branch, status, result JSON | 分支状态 |
| `WorkflowAttemptRecord` | `workflow_attempts` | workflow_id, attempt_number, status | 重试 attempt |
| `WorkflowBranchAttemptRecord` | `workflow_branch_attempts` | attempt_id, branch, status | 分支级重试 |
| `WorkflowWorkerLeaseRecord` | `workflow_worker_leases` | worker_id, heartbeat_at | 单例租约 |
| `AgentTraceRecord` | `agent_traces` | workflow_id, trace JSON | Agent 执行 trace |
| `ComparisonMatrixRecord` | `comparison_matrices` | id, competitors, matrix JSON | 对比矩阵 |
| `ComparisonSnapshotRecord` | `comparison_snapshots` | matrix_id, competitor, snapshot JSON | 矩阵输入快照 |
| `ApiAuditEventRecord` | `api_audit_events` | id, api_key_hash, action, outcome | 审计事件 |
| `ArtifactImportRecord` | `artifact_imports` | id, artifact_id, checksum_sha256 | 基线导入去重 |

关键设计决策：**载荷以 JSON 列原样保存完整 Schema**（`backend/repositories.py:88-105`），同时把常用过滤字段（竞品/事件/预警/置信度/时间）提升为独立列用于查询。

### 2.2 Alembic 迁移（三次）

**Alembic 配置**：
- `alembic.ini` — `sqlalchemy.url = sqlite:///data/runtime/coderadar.db`
- `alembic/env.py`（第 17–18 行）— 运行时用 `CODERADAR_DATABASE_URL` 环境变量覆盖 URL，通过 `Base.metadata` 自动读取模型元数据

**三次迁移迭代**：

| 迁移文件 | 新加表 | 备注 |
|---|---|---|
| `0001_sqlite_persistence.py` | competitors, intelligence_cards, card_evidence, capability_snapshots, snapshot_cards, workflows, workflow_branches, agent_traces, briefings, artifact_imports | 初始建表 |
| `0002_async_workflows.py` | workflow_attempts, workflow_branch_attempts, workflow_worker_leases | 异步队列支持 |
| `0003_api_closure.py` | comparison_matrices, comparison_snapshots, api_audit_events | API 收口 + 审计 |

执行：`python -m alembic upgrade head`（幂等，只跑未跑过的迁移）。

### 2.3 基线产物导入

`scripts/init_database.py` 是统一初始化入口（第 1–80 行），执行前先调 `_require_current_schema()`（第 39–52 行）检查 `alembic_version` 的 `version_num` 是否等于 `"0003_api_closure"`。

三个参数：

```powershell
python -m scripts.init_database --seed-competitors                    # 从 config/competitors.yaml 写入竞品
python -m scripts.init_database --import-artifacts artifacts\week3    # 导入基线产物
python -m scripts.init_database --seed-codemate                       # 导入 CodeMate 基准（可选）
```

**`--import-artifacts` 核心逻辑**（`backend/artifact_import.py:240-306` 的 `initialize_database()`）：
1. 解析 `artifacts/week3/manifest.json`（含 tasks/scoring/cleaned-data 三个输入 SHA-256）
2. 按 `artifact_id` 查 `artifact_imports` 表，已存在则跳过（幂等）
3. 单事务批量插入 35 张情报卡片 + 5 份能力快照 + 5 份简报 + 工作流/trace 记录
4. `--import-artifacts` 隐式等价于同时指定 `--seed-competitors`

---

## 三、后端启动校验（每次服务启动）

### 3.1 lifespan 校验链

`backend/main.py:42-53` 的 `create_app()` → `lifespan` 上下文管理器：

```python
database = get_database()
database.check_connection()   # 确认 DB 文件可达
database.check_migration()    # 确认迁移版本正确
```

**`check_connection()`**（`backend/database.py:50-52`）：
```python
def check_connection(self) -> None:
    with self.engine.connect() as conn:
        conn.execute(text("SELECT 1"))
```
SQLite 文件打不开或路径不存在 → `OperationalError` → API 崩溃退出。

**`check_migration()`**（`backend/database.py:54-70`）：
```python
ALEMBIC_HEAD_REVISION = "0003_api_closure"
def check_migration(self) -> None:
    with self.engine.connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if row != self.ALEMBIC_HEAD_REVISION:
            raise RuntimeError(...)
```
- `alembic_version` 表不存在 → `NoResultFound` → fail-fast
- 版本号不对 → `RuntimeError` → fail-fast

**Worker 进程同样校验**（`scripts/run_workflow_worker.py:15-17`）：
```python
database = get_database()
database.check_connection()
database.check_migration()
```

**关键约束**：compose 启动前必须先手动执行 `alembic upgrade head + init_database`，否则 API 和 Worker 一启动就崩溃。

---

## 四、前端：启动、代理与页面结构

### 4.1 启动命令

```powershell
cd frontend
npm install          # 首次：Vue 3.5 + Element Plus + d3 + axios 等
npm run dev          # vite --host 127.0.0.1，端口 5173
npm run test         # Vitest 组件单测（10 个 spec）
npm run build        # vue-tsc typecheck + vite build → dist/
```

生产部署时可 `npm run build` 后用 `npm run preview` 预览。

### 4.2 API 代理配置

`frontend/vite.config.ts:8-16`：

```typescript
server: {
  host: '127.0.0.1',
  proxy: {
    '/api':   { target: 'http://127.0.0.1:8001' },
    '/health': { target: 'http://127.0.0.1:8001' },
    '/ready':  { target: 'http://127.0.0.1:8001' },
  }
}
```

`frontend/.env.example`：`VITE_API_TARGET=http://127.0.0.1:8001`

开发时前端只调 **Week4 正式 API 面**（`formal_api.py` + `workflows.py` + `ask.py`），不直接调 Week3 的 `/api/agent/*`。

### 4.3 Axios 客户端

`frontend/src/api/client.ts:1-45`：
- 创建 axios 实例，`baseURL = ''`（走 Vite proxy）
- 自动从 `sessionStorage` 读 `apiKey`，注入 `X-API-Key` 请求头
- 响应拦截器：429 状态码翻译为「请求过于频繁，请稍后重试」中文提示

`frontend/src/api/services.ts:1-87` 封装 8 组 API：

| 函数 | 端点 | 用途 |
|---|---|---|
| `checkHealth()` | `GET /health` | 存活检测 |
| `checkReady()` | `GET /ready` | 就绪检测（含 ES + 索引） |
| `askApi.submit()` | `POST /api/ask` | 一次性问答 |
| `competitorApi.list()` | `GET /api/competitors` | 竞品列表 |
| `cardApi.list()` | `GET /api/cards` | 情报卡片（分页+过滤） |
| `cardApi.detail()` | `GET /api/cards/{id}` | 卡片详情+证据 |
| `evidenceApi.get()` | `GET /api/evidence/{chunk_id}` | 证据原文 |
| `snapshotApi.list()` | `GET /api/snapshots` | 能力快照列表 |
| `comparisonApi.latest()` | `GET /api/comparisons/latest` | 最新对比矩阵 |
| `workflowApi.submit()` | `POST /api/workflows` | 提交分析工作流 |
| `workflowApi.detail()` | `GET /api/workflows/{id}` | 轮询工作流结果 |
| `workflowApi.cancel()` | `POST /api/workflows/{id}/cancel` | 取消工作流 |
| `briefingApi.list()` | `GET /api/briefings` | 简报列表 |
| `briefingApi.detail()` | `GET /api/briefings/{id}` | 简报内容 |
| `briefingApi.download()` | `GET /api/briefings/{id}/download` | Markdown 下载 |

### 4.4 路由与页面

`frontend/src/router/index.ts:5-18`：

| 路径 | 视图组件 | 页面名 | 说明 |
|---|---|---|---|
| `/` | `HomeView.vue` | 趋势观测 | 系统状态 + 信号场 + 对比矩阵概览 |
| `/ask` | `AskView.vue` | AI 随问 | 一次性证据问答 |
| `/analysis` | `AnalysisView.vue` | 深度报告 | 提交并管理分析工作流 |
| `/analysis/:workflowId` | `AnalysisResultView.vue` | 分析结果 | 轮询结果，展示卡片/雷达图 |
| `/analysis/:workflowId/report` | `AnalysisReportView.vue` | 分析报告 | Markdown 简报渲染 |

### 4.5 状态管理与关键交互

**`stores/app.ts:6-44`**（Pinia store）：
- `agentMode`：存 `sessionStorage`，随工作流提交下发
- 预置演示 API Key → `sessionStorage`

**工作流轮询**（`composables/workflowPolling.ts:7-42`）：
```typescript
// 每 1.5s 轮询 GET /api/workflows/{id}
// status ∈ { pending, running, success, partial_failure, failed, cancelled } 时停止
// 终态后调 onSuccess / onError 回调
```

**首页仪表盘**：加载 `/api/cards?limit=5` + `/api/snapshots?limit=1` + `/api/comparisons/latest`，展示情报摘要 + 雷达图 + 对比矩阵。

### 4.6 图表组件

| 组件 | 位置 | 用途 |
|---|---|---|
| `RadarChart.vue` | `components/charts/` | D1–D7 能力雷达图 |
| `CapabilityMatrix.vue` | `components/charts/` | 跨竞品能力矩阵（热力图风格） |
| `GapChart.vue` | `components/charts/` | 差距分析图 |
| `TrendChart.vue` | `components/charts/` | 历史趋势（d3 折线图） |
| `EventStream.vue` | `components/charts/` | 事件流时间线 |
| `CapabilityScoreTable.vue` | `components/` | D1–D7 评分明细表 |
| `ReportArtifacts.vue` | `components/` | 简报产物展示 |

所有组件正确处理 N/A、`review_required`、冲突、`partial_failure` 等降级语义。

---

## 五、docker-compose 三服务编排

### 5.1 服务定义

`docker-compose.yml:1-124`：

```yaml
services:
  elasticsearch:    # docker.elastic.co/elasticsearch/elasticsearch:9.4.1
                    # 1g 堆，健康检查 /_cluster/health 等 yellow
  api:              # Dockerfile 构建，CMD uvicorn backend.main:app
                    # depends_on elasticsearch (healthy)
                    # 宿主 ${CODERADAR_API_PORT:-8001}:容器 8000
  worker:           # 同一镜像，CMD python -m scripts.run_workflow_worker
                    # depends_on elasticsearch (healthy)
```

### 5.2 共用 Dockerfile

`Dockerfile:1-28`：
```dockerfile
FROM python:3.11.9-slim
pip install -r requirements-api.txt    # FastAPI + SQLAlchemy + Alembic + uvicorn ...
ARG INSTALL_ML=true                    # 条件安装 torch + sentence-transformers
COPY . /app/CodeRadar/
CMD ["python", "-m", "uvicorn", "backend.main:app", ...]
```

### 5.3 启动链

```
elasticsearch 启动
  → healthcheck: GET /_cluster/health?wait_for_status=yellow（每 10s，30 次）
  → 标记 healthy
    ↓
api 启动（depends_on elasticsearch: service_healthy）
worker 启动（depends_on elasticsearch: service_healthy）  # 无 depends_on api，与 api 并行
    ↓
api lifespan: check_connection() → check_migration()
worker init:    check_connection() → check_migration()
    ↓
API 开放 /health → 容器 healthcheck（每 30s，start_period 10s）
Worker 进入 WorkflowWorker.run_forever() 轮询队列
    ↓
前端（宿主机）: npm run dev → Vite proxy → 127.0.0.1:8001
```

**三个命名卷**：`coderadar_es_data`（ES 数据）、`coderadar_model_cache`（嵌入模型缓存）、`coderadar_runtime_data`（SQLite + 轨迹文件）。

### 5.4 前端不在 compose 中

前端需在宿主机独立运行：

```powershell
cd frontend
npm install
npm run dev    # 127.0.0.1:5173，通过 Vite proxy -> 127.0.0.1:8001
```

或生产模式 `npm run build` 后通过 nginx 或 `npm run preview` 托管静态文件。

---

## 六、关键约束速查

| 约束 | 原因 | 后果 |
|---|---|---|
| compose 前须手动 `alembic upgrade head + init_database` | API/Worker 启动时 fail-fast 校验迁移版本 | 未初始化 → 容器崩溃重启循环 |
| 前端单独运行，不在 compose 内 | 无 nginx 容器，前端 dev 依赖 Vite proxy | 生产部署需额外 nginx 或 `npm run preview` |
| 卡片/快照/简报全部落 SQLite，不再仅存内存 | Week4 核心变化 | 重启不丢数据；前端从 DB 读而非从 Agent 重跑 |
| 正式 API 面只调 `formal_api.py + workflows.py + ask.py` | 前端接口契约 | 不直接调 `/api/agent/*` |
| `backend/routers/{competitors,dashboard}.py` 仍是 0B 占位 | 功能已由 `formal_api.py` 承担 | 忽略这些空文件即可 |

---

## 七、相关文件索引

| 职责 | 文件路径 | 关键行号 |
|---|---|---|
| DB 模型定义 | `backend/models.py` | 29–428 |
| 连接/校验 | `backend/database.py` | 50–70 |
| 后端配置 | `backend/config.py` | 1–70 |
| 后端入口 (lifespan) | `backend/main.py` | 42–53 |
| 初始化脚本 | `scripts/init_database.py` | 1–80 |
| 基线导入 | `backend/artifact_import.py` | 240–306 |
| 仓库层（DB 读写） | `backend/repositories.py` | 全文件 |
| 正式 API 面 | `backend/routers/formal_api.py` | 全文件 |
| 工作流 API | `backend/routers/workflows.py` | 全文件 |
| Ask 问答 | `backend/routers/ask.py` | 全文件 |
| Worker 入口 | `scripts/run_workflow_worker.py` | 全文件 |
| 安全中间件 | `backend/api_security.py` | 全文件 |
| Dockerfile | `Dockerfile` | 1–28 |
| docker-compose.yml | `docker-compose.yml` | 1–124 |
| 前端入口 | `frontend/src/main.ts` | 1–10 |
| 前端路由 | `frontend/src/router/index.ts` | 5–18 |
| API 代理 | `frontend/vite.config.ts` | 8–16 |
| Axios 客户端 | `frontend/src/api/client.ts` | 1–45 |
| API 服务封装 | `frontend/src/api/services.ts` | 1–87 |
| 状态管理 | `frontend/src/stores/app.ts` | 6–44 |
| 工作流轮询 | `frontend/src/composables/workflowPolling.ts` | 7–42 |
| 迁移 0001 | `alembic/versions/0001_sqlite_persistence.py` | 20–208 |
| 迁移 0002 | `alembic/versions/0002_async_workflows.py` | 20–144 |
| 迁移 0003 | `alembic/versions/0003_api_closure.py` | 20–134 |
