# CodeRadar Docker 使用指南

## 目录

1. [概述](#1-概述)
2. [环境准备](#2-环境准备)
3. [项目结构](#3-项目结构)
4. [一键部署（推荐）](#4-一键部署推荐)
5. [手动分步部署](#5-手动分步部署)
6. [配置详解](#6-配置详解)
7. [日常运维](#7-日常运维)
8. [常见问题](#8-常见问题)

---

## 1. 概述

CodeRadar 使用 **Docker Compose** 将全部服务编排在四个容器中：

| 服务 | 容器名 | 功能 | 对外端口 |
|------|--------|------|---------|
| **frontend** | nginx | 提供 Vue 3 前端页面 + 反向代理 API | `8080`（可配置） |
| **api** | FastAPI | Mini-RAG 检索、Agent 分析、认证与工作流管理 | `8001`（调试用） |
| **elasticsearch** | ES 9.4.1 | Chunk 文本、元数据和向量存储 | `9200` |
| **worker** | 后台异步消费者 | 单例租约领取 SQLite 任务，调用 Agent 并写回进度 | 无 |

架构示意：

```
浏览器 ──► :8080 ──► nginx (frontend)
                          │
                          ├── /api/* ──► api:8000 ──► elasticsearch:9200
                          │                              │
                     dist/（静态文件）                SQLite 数据库
                                                      ▲
                                              worker（后台消费）
```

---

## 2. 环境准备

### 2.1 安装 Docker

**Windows**

1. 下载 [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/)
2. 安装时确保勾选 **"Use WSL 2 instead of Hyper-V"**（推荐）
3. 安装完成后重启，Docker Desktop 自动启动
4. 打开 PowerShell 验证：

```powershell
docker --version
docker compose version
```

> **系统要求**：Windows 10（21H2+）或 Windows 11，启用 WSL 2。

**macOS**

1. 下载 [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/)
2. 安装后启动 Docker Desktop
3. 终端验证：

```bash
docker --version
docker compose version
```

**Linux（Ubuntu/Debian）**

```bash
# 安装依赖
sudo apt update
sudo apt install ca-certificates curl

# 添加 Docker 官方源
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 将当前用户加入 docker 组（避免每次 sudo）
sudo usermod -aG docker $USER
# 退出重新登录或执行 newgrp docker
```

### 2.2 系统要求

- **内存**：至少 4GB 可用（推荐 8GB），Elasticsearch 容器占用约 2GB
- **磁盘**：至少 10GB 空闲空间
- **Docker**：支持 `docker compose` 子命令的版本（≥ v2.0）

### 2.3 获取项目代码

```powershell
# 进入项目目录
cd Proj/competitor-analysis-system

# 确认关键文件存在
ls docker-compose.yml, .env.example, deploy.ps1
```

---

## 3. 项目结构

以下是与 Docker 部署直接相关的文件：

```
Proj/competitor-analysis-system/
├── docker-compose.yml       # 服务编排（4 个服务）
├── Dockerfile               # API + Worker 镜像构建
├── .env.example             # 环境变量模板（复制为 .env 使用）
├── .env                     # 实际配置（已 gitignore，不提交）
├── .dockerignore            # Docker 构建排除规则
│
├── frontend/
│   ├── Dockerfile           # 前端多阶段构建（Node build → nginx）
│   └── nginx.conf           # nginx 配置（SPA + API 反向代理）
│
├── deploy.ps1               # ★ 一键部署脚本
│
├── backend/                 # FastAPI 后端代码
├── config/                  # RAG / 竞品 / 维度配置
│   ├── mini_rag.yaml        # 默认 RAG 配置（hash + lexical）
│   └── mini_rag.formal.yaml # 正式评测 RAG 配置（BGE-M3 + Cross-Encoder）
├── data/                    # 数据目录（已 gitignore）
│   ├── cleaned/documents.jsonl
│   └── runtime/             # SQLite 数据库 + 运行时数据
└── scripts/                 # 运维脚本（init_database, build_index 等）
```

---

## 4. 一键部署（推荐）

CodeRadar 提供了 `deploy.ps1` PowerShell 脚本，一条命令完成全部部署流程。

### 4.1 首次部署

```powershell
# 1. 进入项目根目录
cd Proj/competitor-analysis-system

# 2. 运行部署脚本
.\deploy.ps1
```

脚本会自动执行以下 9 个步骤：

| 步骤 | 说明 |
|------|------|
| ① **检查前置条件** | 验证 Docker 和 Docker Compose 是否可用，检查端口占用 |
| ② **初始化 .env** | 如果 `.env` 不存在，从 `.env.example` 复制，并提示填写必填项 |
| ③ **构建镜像** | `docker compose build`，构建所有镜像 |
| ④ **启动 Elasticsearch** | 先启动 ES，轮询 `/_cluster/health` 直到就绪（最多 2 分钟） |
| ⑤ **数据库迁移** | `docker compose run --rm api python -m alembic upgrade head`，在 API 启动前建表 |
| ⑥ **种子数据** | `init_database`，导入竞品定义和基线产物 |
| ⑦ **启动剩余服务** | 启动 `api`、`worker`、`frontend`，等待 API 的 `/health` 就绪 |
| ⑧ **构建 ES 索引** | `build_index`，将文档分块写入 Elasticsearch |
| ⑨ **输出信息** | 显示访问地址和当前配置摘要 |

> **首次运行**时，脚本会在步骤 ② 暂停，提示你编辑 `.env` 填入必填的密钥。按 Enter 继续。

### 4.2 自定义参数

```powershell
# 自定义端口
.\deploy.ps1 -Port 3000 -ApiPort 9001

# 仅启动服务，跳过数据库和索引初始化
.\deploy.ps1 -SkipInit

# 只跳过 ES 索引构建
.\deploy.ps1 -SkipIndex

# 覆盖已存在的 .env 重新开始
.\deploy.ps1 -ForceEnv

# 跳过所有初始化步骤
.\deploy.ps1 -SkipDB -SkipSeed -SkipIndex
```

### 4.3 部署后访问

部署成功后，脚本会输出类似以下信息：

```
╔══════════════════════════════════════════════════════════╗
║        CodeRadar 部署成功！                              ║
╠══════════════════════════════════════════════════════════╣
║                                                          ║
  🌐  前端 UI：         http://localhost:8080
  🔗  API（直接）：     http://localhost:8001/health
  🔍  Elasticsearch：  http://localhost:9200

  当前配置：
    Agent 模式：    rules
    认证已启用：    是
    RAG Profile：   formal
    ML 依赖：       已安装
...
```

打开浏览器访问 `http://localhost:8080` 即可看到 CodeRadar 前端界面。

---

## 5. 手动分步部署

如果不使用一键脚本，也可以手动执行每一步，便于理解底层流程。

### 5.1 准备环境配置

```powershell
# 从模板创建 .env
Copy-Item .env.example .env

# 编辑 .env，填写必填项（用记事本或 VS Code）
notepad .env
```

**.env 必填项：**

```ini
GITHUB_TOKEN=ghp_你的GitHubToken         # 用于采集数据
DEEPSEEK_API_KEY=sk-你的DeepSeekKey      # Agent 在线模式
CODERADAR_API_KEY=你的API密钥至少32字符   # 后端认证密钥
```

### 5.2 构建镜像

```powershell
docker compose build
```

构建所有 4 个镜像（elasticsearch 从 Docker Hub 拉取，其余本地构建）。

### 5.3 启动 Elasticsearch

```powershell
docker compose up -d elasticsearch

# 等待 ES 就绪
$i = 0; while ($i -lt 120) {
  try { $r = Invoke-WebRequest -Uri http://localhost:9200/_cluster/health -UseBasicParsing -TimeoutSec 3 2>$null; if ($r.StatusCode -eq 200) { "ES 就绪"; break } } catch {}
  $i++; Start-Sleep -Seconds 1
}
```

Elasticsearch 先单独启动，因为它没有数据库依赖。

### 5.4 初始化数据库

```powershell
# 数据库迁移（在 API 启动前执行）
docker compose run --rm api python -m alembic upgrade head

# 导入种子数据
docker compose run --rm api python -m scripts.init_database --seed-competitors --seed-codemate --import-artifacts artifacts/week3
```

> `docker compose run --rm` 创建一个临时容器执行命令，不经过健康检查。执行完自动删除。

### 5.5 启动其余服务

```powershell
docker compose up -d api worker frontend
```

参数说明：
- `-d`：后台运行（detached）

### 5.6 等待 API 就绪

```powershell
$i = 0; while ($i -lt 120) {
  try { $r = Invoke-WebRequest -Uri http://localhost:8001/health -UseBasicParsing; if ($r.StatusCode -eq 200) { "API 就绪"; break } } catch {}
  $i++; Start-Sleep -Seconds 1
}
```

### 5.7 构建 ES 索引

```powershell
# 全量构建索引
docker compose exec api python -m scripts.build_index

# 如果之后有增量文档更新，可执行增量同步
docker compose exec api python -m scripts.build_index --incremental
```

索引构建完成后，读取别名 `coderadar_chunks_current` 会原子切换到新物理索引。

### 5.8 验证部署

```powershell
# 就绪检查（含 ES 连接、索引兼容性）
Invoke-RestMethod http://localhost:8001/ready

# 带认证的 RAG 查询
$headers = @{ "X-API-Key" = "你的API密钥" }
$body = @{
  question = "Cursor 最近有哪些 Agent 能力更新"
  competitor = "Cursor"
  top_k = 8
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://localhost:8001/api/rag/query `
  -Headers $headers -ContentType 'application/json' -Body $body
```

### 5.9 访问前端

在浏览器中打开 `http://localhost:8080`。

---

## 6. 配置详解

所有配置集中在一个文件：**`.env`**。修改后需要 `docker compose up -d --build` 重新构建容器生效。

### 6.1 基础配置

```ini
# ── 端口 ────────────────────────────────────────────────────
CODERADAR_API_PORT=8001       # API 直接访问端口（调试用）
CODERADAR_UI_PORT=8080        # 前端 nginx 端口（主要访问入口）

# ── API 认证 ────────────────────────────────────────────────
CODERADAR_AUTH_ENABLED=true   # 是否启用 API-Key 认证
CODERADAR_API_KEY=            # API 密钥（至少 32 字符随机字符串）
CODERADAR_READ_RATE_PER_MINUTE=120
CODERADAR_WRITE_RATE_PER_MINUTE=30
```

### 6.2 RAG 检索配置（Profile 一键切换）

```ini
# ── Mini-RAG Profile ──────────────────────────────────────
# ★ 一键切换整套配置集，无需分别设置 Embedding/Reranker。
#
# default → config/mini_rag.yaml
#   - 嵌入: hash（离线确定性，无外部依赖）
#   - 重排序: lexical（词法重叠）
#   适合开发、测试、无模型依赖的环境。
#
# formal  → config/mini_rag.formal.yaml
#   - 嵌入: sentence_transformers（BGE-M3 多语言嵌入）
#   - 重排序: cross_encoder（Cross-Encoder 模型）
#   适合正式检索与评测。
MINIRAG_PROFILE=default

# ── ML 依赖安装（构建时参数）────────────────────────────
# 设为 true 时在构建镜像时安装 torch + sentence-transformers。
# 使用 profile=formal 时必须开启。
MINIRAG_INSTALL_ML=false
```

**典型配置组合：**

| 场景 | `MINIRAG_PROFILE` | `MINIRAG_INSTALL_ML` | `CODERADAR_AGENT_MODE` |
|------|-------------------|---------------------|-----------------------|
| 快速体验（无需 GPU） | `default` | `false` | `rules` |
| 在线分析体验 | `default` | `false` | `hybrid`（需配置 DeepSeek） |
| 正式检索（推荐） | `formal` | `true` | `hybrid` |
| 全面评测 | `formal` | `true` | `hybrid` |

> **切换 profile 后必须重建索引**：`docker compose exec api python -m scripts.build_index`
> 不同 Embedding 模型的向量空间不兼容，不重建会导致检索结果异常。

**高级微调**：如果默认的 formal/default 配置不能满足需求，可以单独覆盖个别参数。
在 `.env` 中取消注释以下变量即可覆盖 YAML 文件中的对应值：

```ini
# MINIRAG_CONFIG_PATH=config/mini_rag.yaml
# MINIRAG_EMBEDDING_PROVIDER=hash
# MINIRAG_EMBEDDING_MODEL=BAAI/bge-m3
# MINIRAG_EMBEDDING_DIMENSION=1024
# MINIRAG_RERANKER_PROVIDER=lexical
# MINIRAG_RERANKER_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
# MINIRAG_RERANKER_STRICT=false
```

### 6.3 Agent 分析配置

```ini
# ── Agent 模式 ─────────────────────────────────────────────
# rules   = 离线确定性（默认，推荐测试/快照）
# llm     = 使用 DeepSeek 模型；失败时回退到 rules
# hybrid  = 证据优先链 + LLM 独立润色（推荐在线模式）
CODERADAR_AGENT_MODE=rules

# ── DeepSeek（仅 llm/hybrid 模式需配置）───────────────────
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
CODERADAR_LLM_TIMEOUT_SECONDS=45
CODERADAR_LLM_MAX_TOKENS=4096
```

### 6.4 典型配置组合

| 场景 | `MINIRAG_PROFILE` | `MINIRAG_INSTALL_ML` | `CODERADAR_AGENT_MODE` |
|------|------------------|---------------------|-----------------------|
| 快速体验（无 GPU） | `default` | `false` | `rules` |
| 在线分析体验 | `default` | `false` | `hybrid`（需配置 DeepSeek） |
| 正式检索（推荐） | `formal` | `true` | `hybrid` |
| 全面评测 | `formal` | `true` | `hybrid` |

> **切换 profile 后必须全量重建索引**，因为不同 Embedding 模型的向量空间不兼容。

---

## 7. 日常运维

### 7.1 查看日志

```powershell
# 查看所有服务的日志
docker compose logs

# 查看特定服务的最新日志
docker compose logs --tail 100 api
docker compose logs --tail 50 frontend
docker compose logs -f api          # 持续跟踪（Ctrl+C 退出）
```

### 7.2 启动 / 停止 / 重启

```powershell
# 停止所有服务（保留数据）
docker compose stop

# 重新启动
docker compose start

# 重启单个服务
docker compose restart api
```

### 7.3 重新构建

修改代码或配置后：

```powershell
# 重新构建并启动变更的服务
docker compose up -d --build

# 强制重新构建某个服务
docker compose build --no-cache api

# 重建全部
docker compose build --no-cache
```

### 7.4 数据管理

```powershell
# 查看持久化卷
docker volume ls

# 停止并删除容器（保留数据卷）
docker compose down

# 停止并删除容器 + 卷（⚠ 清除所有数据）
docker compose down -v
```

> **数据目录说明**：
> - `coderadar_es_data`：Elasticsearch 索引数据
> - `coderadar_model_cache`：HuggingFace 模型缓存
> - `coderadar_runtime_data`：SQLite 数据库 + 运行时数据
> - `./data` 绑定挂载：结构化文档、评测数据

### 7.5 执行一次性命令

```powershell
# 在 API 容器内执行脚本
docker compose exec api python -m scripts.audit_documents

# 增量更新索引
docker compose exec api python -m scripts.build_index --incremental

# 进入容器的交互式 shell
docker compose exec api bash

# 进入 worker 容器的交互式 shell
docker compose exec worker bash
```

### 7.6 健康检查

```powershell
# API 进程存活检查
Invoke-RestMethod http://localhost:8001/health

# 完整就绪检查（含 ES 连接、索引兼容性）
Invoke-RestMethod http://localhost:8001/ready

# 索引状态
Invoke-RestMethod http://localhost:8001/api/rag/index/status
```

### 7.7 升级流程

当代码更新后：

```powershell
# 1. 拉取最新代码（git pull）

# 2. 重新构建并启动
docker compose up -d --build

# 3. 执行数据库迁移（如有新增迁移）
docker compose exec api python -m alembic upgrade head

# 4. 增量更新索引（如果文档未变）
docker compose exec api python -m scripts.build_index --incremental
```

---

## 8. 常见问题

### 8.1 端口被占用

```
错误信息：port is already allocated
```

**解决方法**：修改 `.env` 中的端口配置

```ini
CODERADAR_UI_PORT=3000       # 改前端端口
CODERADAR_API_PORT=9001      # 改 API 端口
```

或者停止占用端口的程序：

```powershell
# 查找占用 8080 端口的进程
netstat -ano | findstr ":8080"
# 在任务管理器中结束对应 PID 的进程
```

### 8.2 Elasticsearch 启动失败（内存不足）

```
错误信息：max virtual memory areas vm.max_map_count [65530] is too low
```

**Linux 解决方法**：

```bash
sudo sysctl -w vm.max_map_count=262144
# 永久生效
echo 'vm.max_map_count=262144' | sudo tee -a /etc/sysctl.conf
```

**Windows / macOS**：在 Docker Desktop 设置中增加内存分配（Settings → Resources → Memory），建议设为 4GB 以上。

### 8.3 API 返回 401 Unauthorized

```
HTTP 401：缺少 X-API-Key 请求头
```

**解决方法**：所有 API 请求需要带上认证头

```powershell
$headers = @{ "X-API-Key" = "你的.env中的CODERADAR_API_KEY" }
Invoke-RestMethod -Uri http://localhost:8001/api/rag/status -Headers $headers
```

或在 `.env` 中临时关闭认证用于调试：

```ini
CODERADAR_AUTH_ENABLED=false
```

然后重新构建启动：`docker compose up -d --build api`

### 8.4 /ready 返回 degraded

```
status: "degraded"
```

**排查步骤**：

```powershell
# 1. 检查 ES 是否健康
Invoke-RestMethod http://localhost:9200/_cluster/health

# 2. 检查索引别名是否存在
docker compose exec api python -c "
from mini_rag.indexing import IndexManager
from mini_rag.config import load_config
cfg = load_config()
mgr = IndexManager(cfg)
print('别名指向:', mgr.get_active_index())
"

# 3. 如别名不存在，执行全量构建
docker compose exec api python -m scripts.build_index
```

### 8.5 Worker 不处理任务

```powershell
# 查看 worker 日志
docker compose logs --tail 50 worker
```

常见原因：
- worker 容器未启动：`docker compose ps` 确认状态
- SQLite 文件权限问题：检查 `data/runtime/` 目录权限
- 已有其他 worker 持有租约：CodeRadar 只支持单 worker 实例

### 8.6 前端页面空白 / API 请求失败

**排查**：

```powershell
# 1. 确认前端容器运行
docker compose ps frontend

# 2. 查看 nginx 日志
docker compose logs frontend

# 3. 确认 API 是否可直连
Invoke-RestMethod http://localhost:8001/health

# 4. 检查 .env 中 CORS 配置
# CODERADAR_CORS_ORIGINS 必须包含前端实际访问地址
# 例如通过 localhost:8080 访问，则设为 http://localhost:8080
```

### 8.7 修改 .env 后不生效

需要重新构建容器：

```powershell
docker compose up -d --build
```

如果只改了运行时环境变量（非构建参数），可以只重建受影响的服务：

```powershell
docker compose up -d --build api worker
```

### 8.8 Windows 路径挂载问题

在 Windows 上，`./data:/app/CodeRadar/data` 挂载可能因路径格式导致问题。

**确保**：
- 项目目录不在 `C:\Windows\` 等系统保护目录下
- Docker Desktop 已授权访问你的驱动器（Settings → Resources → File Sharing）
- 使用 PowerShell（而非 CMD）执行 Docker 命令

---

> **进一步参考**：
> - [部署文档](./部署文档.md) — 更详细的配置参数与安全说明
> - [项目运行全流程](./项目运行全流程-Week2-4输入输出.md) — 端到端输入输出详解
> - `.env.example` — 所有可配置项的完整清单

