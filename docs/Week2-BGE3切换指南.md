# 切换到 BGE-M3 正式评测配置

## .env.example 中 Week2 配置逐项说明

`.env.example` 中与 Week2（Mini-RAG）相关的环境变量共 **14 个**，前三行之后每行都附了注释，下面是逐一说明它们映射到代码中的什么位置、起什么作用。

### 一、数据与存储（3 个）

```ini
MINIRAG_DOCUMENTS_PATH=data/cleaned/documents.jsonl
```
- **映射**：`config/mini_rag.yaml` → `data.documents_path`
- **作用**：指定 `DocumentLoader` 读取的输入文档路径
- **默认值**：`data/cleaned/documents.jsonl`
- **典型场景**：你有另一份处理后的文档要测试时，改为新路径

```ini
MINIRAG_ELASTICSEARCH_URL=http://localhost:9200
```
- **映射**：`config/mini_rag.yaml` → `elasticsearch.url`
- **作用**：`ElasticsearchClient` 连接的 ES 地址
- **默认值**：`http://localhost:9200`
- **典型场景**：ES 不在本机（docker-compose 中 api/worker 服务设为 `http://elasticsearch:9200`）

```ini
MINIRAG_INDEX_PREFIX=coderadar_chunks
```
- **映射**：`config/mini_rag.yaml` → `elasticsearch.index_prefix`
- **作用**：物理索引名前缀，全量重建时生成 `coderadar_chunks_v<UTC时间戳>`
- **默认值**：`coderadar_chunks`
- **典型场景**：一般不改，除非你的 ES 集群上有多套 CodeRadar 实例需要区分

### 二、读别名（1 个）

```ini
MINIRAG_READ_ALIAS=coderadar_chunks_current
```
- **映射**：`config/mini_rag.yaml` → `elasticsearch.read_alias`
- **作用**：API 查询时透明引用的读别名。全量重建成功后原子切换此别名指向新索引
- **默认值**：`coderadar_chunks_current`
- **典型场景**：一般不改；如果需要并行维护多套索引，可以改为不同别名

### 三、嵌入层（5 个）

这是切换到 BGE-M3 **最关键的一组变量**：

```ini
MINIRAG_INSTALL_ML=false
```
- **⚠️ 注意**：这个变量**不**是 `mini_rag/config.py` 读的运行时环境变量，而是 **Docker 构建参数**（`Dockerfile:3` `ARG INSTALL_ML=false`）
- **作用**：Docker 构建时是否安装 `torch` + `sentence-transformers`。设 `true` 时，Dockerfile 会 `pip install "torch==2.10.0"` 和 `sentence-transformers==5.6.0`
- **本地开发不经过 Docker 时，这个变量没有效果**——你直接在 conda 环境里 `pip install sentence-transformers==5.6.0` 即可

---

以下四个才是 Mini-RAG 运行时真正读的嵌入配置：

```ini
MINIRAG_EMBEDDING_PROVIDER=hash
```
- **映射**：`config/mini_rag.yaml` → `embedding.provider`
- **作用**：选择嵌入实现
  - `hash` → `HashEmbeddingProvider`（离线确定性，零依赖）
  - `sentence_transformers` → `SentenceTransformerProvider`（BGE-M3，需要安装 sentence-transformers 包）
- **这就是切换到 BGE-M3 要改的核心变量**：改为 `sentence_transformers`

```ini
MINIRAG_EMBEDDING_MODEL=BAAI/bge-m3
```
- **映射**：`config/mini_rag.yaml` → `embedding.model`
- **作用**：指定 HuggingFace 模型名称。provider=`hash` 时此字段仅记录 metadata；provider=`sentence_transformers` 时实际加载此模型
- **默认值**：`BAAI/bge-m3`
- **典型场景**：一般不改；如果想换别的嵌入模型（例如 `BAAI/bge-multilingual-gemma2`），改这里

```ini
MINIRAG_EMBEDDING_DIMENSION=1024
```
- **映射**：`config/mini_rag.yaml` → `embedding.dimension`
- **作用**：向量维度。用于 ES mapping 定义 `dense_vector` 字段的 dims，以及 `/ready` 的一致性校验
- **默认值**：1024
- **典型场景**：BGE-M3 输出 1024 维，和 hash 的 1024 维仅是维度相同但**向量空间完全不同**。如果换模型要同步改此处

```ini
MINIRAG_MODEL_CACHE_DIR=.cache/models
```
- **映射**：`config/mini_rag.yaml` → `embedding.cache_dir`
- **作用**：HuggingFace 模型下载的缓存目录。BGE-M3 和 Cross-Encoder 模型都缓存在这里
- **默认值**：`.cache/models`
- **典型场景**：可以改为一个持久化路径以避免重复下载，例如 `D:\models\cache`

```ini
MINIRAG_RERANKER_PROVIDER=lexical
```
- **映射**：`config/mini_rag.yaml` → `reranker.provider`
- **作用**：选择排序阶段的重排序实现
  - `lexical`（或 `term_overlap`）→ `TermOverlapReranker`，计算词项重叠分数，纯 CPU 零模型
  - `cross_encoder` → `CrossEncoderReranker`，加载 Cross-Encoder 模型对 (问题, Chunk) 对打分，需要 `sentence-transformers` 包
- **切换到 BGE-M3 后建议同步改为 `cross_encoder`**，否则嵌入用了语义向量、排序却只用词项重叠，会浪费一半效果

```ini
MINIRAG_RERANKER_MODEL=BAAI/bge-reranker-v2-m3
```
- **映射**：`config/mini_rag.yaml` → `reranker.model`
- **⚠️ 注意**：`.env.example` 中的这个默认值是 **BAAI/bge-reranker-v2-m3**，但 `config/mini_rag.formal.yaml:42` 实际用的是 **cross-encoder/mmarco-mMiniLMv2-L12-H384-v1**
- **建议**：以 `mini_rag.formal.yaml` 为准，设为 `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`。这个模型专门做重排序，对检索任务效果更好

```ini
MINIRAG_RERANKER_STRICT=false
```
- **映射**：`config/mini_rag.yaml` → `reranker.strict`
- **作用**：
  - `false`（默认）：Cross-Encoder 模型加载失败时**静默回退**到词项重叠 reranker，`retrieval_trace.warnings` 留痕，查询正常返回
  - `true`：Cross-Encoder 加载失败直接**报错**，查询失败（HTTP 503）
- **切换 BGE-M3 后建议设为 `true`**：避免无 Cross-Encoder 时静默降级导致你以为排序效果对了其实没有

### 四、服务与轨迹（2 个）

```ini
MINIRAG_CONFIG_PATH=config/mini_rag.yaml
```
- **⚠️ 注意**：这个变量也不在 `_ENV_OVERRIDES` 中，而是 `mini_rag/config.py:163` 直接用 `os.getenv("MINIRAG_CONFIG_PATH", "config/mini_rag.yaml")` 读取
- **作用**：覆盖默认的配置文件路径。设 `config/mini_rag.formal.yaml` 时 YAML 配置本身就是 BGE-M3 + Cross-Encoder
- **优先级低于 CLI 的 `--config` 参数**：`python -m scripts.build_index --config config\mini_rag.formal.yaml` 会覆盖此值

```ini
MINIRAG_TRACE_PATH=data/runtime/retrieval_traces.jsonl
```
- **映射**：`config/mini_rag.yaml` → `data.trace_path`
- **作用**：每次查询的完整检索轨迹持久化到该文件（JSON Lines 格式，保留最近 1000 条）
- **默认值**：`data/runtime/retrieval_traces.jsonl`

---

### 快速参考表

| 环境变量 | 对应 YAML 路径 | 切换 BGE-M3 的推荐值 |
|----------|---------------|-------------------|
| `MINIRAG_EMBEDDING_PROVIDER` | `embedding.provider` | `sentence_transformers` |
| `MINIRAG_EMBEDDING_MODEL` | `embedding.model` | `BAAI/bge-m3`（不改变） |
| `MINIRAG_EMBEDDING_DIMENSION` | `embedding.dimension` | `1024`（不改变） |
| `MINIRAG_RERANKER_PROVIDER` | `reranker.provider` | `cross_encoder` |
| `MINIRAG_RERANKER_MODEL` | `reranker.model` | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` |
| `MINIRAG_RERANKER_STRICT` | `reranker.strict` | `true` |
| `MINIRAG_MODEL_CACHE_DIR` | `embedding.cache_dir` | `.cache/models`（不改变） |
| `MINIRAG_CONFIG_PATH` | 加载哪个 YAML | `config/mini_rag.formal.yaml`（或直接改 YAML） |

---

## 切换到 BGE-M3 的完整指南

### 方法 A：改 .env + 覆盖配置变量（推荐）

一步到位，修改 `.env`：

```ini
# 嵌入层
MINIRAG_EMBEDDING_PROVIDER=sentence_transformers
MINIRAG_EMBEDDING_MODEL=BAAI/bge-m3
MINIRAG_EMBEDDING_DIMENSION=1024
MINIRAG_MODEL_CACHE_DIR=.cache/models

# 重排序
MINIRAG_RERANKER_PROVIDER=cross_encoder
MINIRAG_RERANKER_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
MINIRAG_RERANKER_STRICT=true
```

然后执行（全程约 3-5 分钟，首次下载模型）：

```powershell
conda activate CodeRadar

# Step 1：安装 sentence-transformers（如需）
python -m pip install sentence-transformers==5.6.0
python -m pip check

# Step 2：全量重建索引（环境变量被 .env 自动加载）
python -m scripts.build_index

# Step 3：启动 API
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# Step 4：验证
curl http://localhost:8000/ready
# {"status":"ready","embedding_model":"BAAI/bge-m3","embedding_compatible":true}
```

### 方法 B：直接指定 formal 配置文件（效果等价）

不改 `.env`，运行时通过 `--config` 指定：

```powershell
# 建索引
python -m scripts.build_index --config config\mini_rag.formal.yaml

# 查询
python rag_query.py "Cursor Agent 能力更新" --competitor Cursor --top-k 5 --config config\mini_rag.formal.yaml

# 启动 API（需要在 .env 中设 MINIRAG_CONFIG_PATH 或改 main.py）
set MINIRAG_CONFIG_PATH=config\mini_rag.formal.yaml
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### 方法 C：直接改 YAML（最直接）

编辑 `config/mini_rag.yaml`，把 4 个值改掉：

```yaml
embedding:
  provider: sentence_transformers   # 原来: hash
  model: BAAI/bge-m3
  dimension: 1024

reranker:
  provider: cross_encoder           # 原来: lexical
  model: cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
  strict: true                      # 原来: false
```

然后正常执行所有命令，**不再需要传 `--config`**。

---

### 模型下载说明

切换后首次执行会下载两个模型：

| 模型 | 大小 | 下载时机 |
|------|------|---------|
| `BAAI/bge-m3` | ~2.2 GB | **建索引时**（`python -m scripts.build_index` 执行 chunk embedding 时需要） |
| `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | ~500 MB | **首次查询时** lazy 加载（第一次 `rag_query.py` 或 API 查询时自动下载） |

下载进度条由 HuggingFace `huggingface_hub` 库自动打印，无需额外操作。下载完成后模型文件缓存在 `.cache/models/` 目录，之后离线可用。

---

### 验证是否切换成功

```powershell
# 1. 查索引状态
curl http://localhost:8000/api/rag/index/status
# embedding_model 应为 "BAAI/bge-m3"
# embedding_compatible 应为 true

# 2. 比对效果（同一查询在 hash vs BGE-M3 下的差异）
python rag_query.py "Cursor 的项目级 Agent 上下文能力" --top-k 3
# BGE-M3 模式下，"Agent 上下文"语义相关的 Chunk 会排到前面
# hash 模式下，返回排序基本≈BM25 排序，语义无关
```

### 恢复默认 hash 配置

```powershell
# 改回 .env 或 YAML
MINIRAG_EMBEDDING_PROVIDER=hash
MINIRAG_RERANKER_PROVIDER=lexical
MINIRAG_RERANKER_STRICT=false

# 必须全量重建！
python -m scripts.build_index
```

### Docker 容器中的 BGE-M3

如果你用 `docker compose up -d` 部署，需要在 `.env` 中加上：

```ini
MINIRAG_INSTALL_ML=true        # Dockerfile 构建参数
MINIRAG_EMBEDDING_PROVIDER=sentence_transformers
MINIRAG_RERANKER_PROVIDER=cross_encoder
MINIRAG_RERANKER_STRICT=true
```

然后重建镜像：

```powershell
docker compose build --build-arg INSTALL_ML=true
docker compose up -d
```

这样容器内会安装 torch + sentence-transformers，运行时读环境变量自动走 BGE-M3。
