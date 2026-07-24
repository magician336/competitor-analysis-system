# CodeRadar API 接口文档

## 1. 服务范围

CodeRadar 主体服务使用 FastAPI（基于 Python 的 Web 应用程序接口框架）提供 Mini-RAG（轻量检索增强生成，Mini Retrieval-Augmented Generation）接口。当前应用版本为 `0.2.0`，只注册系统接口和 `/api/rag` 路由。`backend/routers` 中的其他路由文件未在主体应用中注册。

服务默认监听 `http://127.0.0.1:8000`。JSON（JavaScript Object Notation，JavaScript 对象表示法）响应声明 UTF-8 字符集。

## 2. 接口总览

下表列出当前注册的全部路由。错误响应采用 FastAPI 的 `{"detail": ...}` 结构。

| 方法 | 路径 | 作用 | 主要状态码 |
| --- | --- | --- | --- |
| GET | `/health` | 进程存活检查 | 200 |
| GET | `/ready` | Elasticsearch、别名和向量兼容性检查 | 200、503 |
| POST | `/api/rag/query` | 混合检索并返回证据与轨迹 | 200、400、503 |
| GET | `/api/rag/evidence/{chunk_id}` | 按 Chunk 读取证据 | 200、404、503 |
| GET | `/api/rag/trace/{query_id}` | 读取保存的查询轨迹 | 200、404 |
| GET | `/api/rag/index/status` | 读取索引状态 | 200 |
| POST | `/api/rag/rebuild` | 全量构建物理索引并切换别名 | 200、422、503 |
| POST | `/api/rag/index/incremental` | 增量同步当前物理索引 | 200、422、503 |
| POST | `/api/rag/evaluate` | 执行检索评测或 A—F 消融 | 200、400、503 |
| POST | `/api/rag/citations/validate` | 校验同次查询中的引用 | 200、400、503 |

路由表说明了服务边界：主体 API 提供检索证据服务，第三周 Agent 接口位于 `update` 工作区的 `0.3.0` 应用中。

## 3. 查询接口

### 3.1 `POST /api/rag/query`

请求体遵循 `RAGQuery`。`question` 为必填字段；其余字段用于元数据过滤。

```json
{
  "question": "Cursor 最近有哪些 Agent 能力更新",
  "competitor": "Cursor",
  "event_types": ["product_release"],
  "dimension_tags": ["agent_context"],
  "product_versions": [],
  "start_time": "2026-04-25T00:00:00Z",
  "end_time": "2026-07-24T23:59:59Z",
  "evidence_levels": ["A", "B"],
  "source_types": ["official_changelog", "github_release"],
  "current_only": true,
  "top_k": 8
}
```

`event_types` 接受 E1—E3 枚举值，`dimension_tags` 接受 D1—D7 枚举值。`top_k` 的接口模型上限为 100，服务配置的当前实际最大值为 50；超过配置上限返回 400。

响应遵循 `RAGResponse`：

```json
{
  "query_id": "qry_...",
  "query": "Cursor 最近有哪些 Agent 能力更新",
  "parsed_filters": {"competitor": "Cursor", "current_only": true},
  "evidence": [],
  "conflicts": [],
  "retrieval_trace": {
    "bm25_candidates": 0,
    "dense_candidates": 0,
    "fused_candidates": 0,
    "reranked_candidates": 0,
    "returned_candidates": 0,
    "latency_ms": 0.0,
    "stage_latency_ms": {},
    "warnings": [],
    "retrieval_config": {}
  }
}
```

证据对象包含 `chunk_id`、`document_id`、`version_id`、原文、引文、标题、URL（Uniform Resource Locator，统一资源定位符）、字符范围、来源、证据等级、时间、版本、事件与能力标签，以及各检索阶段分数。

## 4. 证据与轨迹

### 4.1 `GET /api/rag/evidence/{chunk_id}`

接口从读取别名中查找指定 Chunk。找到时返回 `Evidence`，不存在时返回 404。该接口读取索引中的完整 Chunk，不重新运行检索。

### 4.2 `GET /api/rag/trace/{query_id}`

接口读取 `TraceStore` 保存的查询响应。轨迹默认写入 `data/runtime/retrieval_traces.jsonl`，内存保留上限为 1,000 条。未知 `query_id` 返回 404。

## 5. 索引管理

### 5.1 状态接口

`GET /ready` 和 `GET /api/rag/index/status` 返回相同的索引健康结构。`/ready` 在状态不是 `ready` 时使用 503；索引状态接口保持 200 并由响应字段表达状态。

```json
{
  "status": "ready",
  "index": "coderadar_chunks_current",
  "physical_index": "coderadar_chunks_v...",
  "indexed_chunks": 2688,
  "embedding_model": "hash-embedding/coderadar-v1",
  "embedding_dimension": 1024,
  "index_embedding_model": "hash-embedding/coderadar-v1",
  "index_embedding_dimension": 1024,
  "embedding_compatible": true,
  "backend": {}
}
```

当前本机读取别名返回 2,688 个 Chunk。另一个未被该别名引用的物理索引包含 3,337 个 Chunk。`ready` 只说明当前别名可查询且向量兼容，不证明索引与最新结构化数据同步。

### 5.2 全量构建

`POST /api/rag/rebuild` 请求体：

```json
{"documents_path": null, "delete_missing": false}
```

服务加载结构化文档、执行来源感知切分、生成向量、创建版本化物理索引，并在成功后切换读取别名。`documents_path` 为空时使用配置中的 `data/cleaned/documents.jsonl`。

### 5.3 增量同步

`POST /api/rag/index/incremental` 使用同一请求结构。`delete_missing=true` 时删除输入中已经不存在的 Chunk。读取别名缺失且 `fail_on_missing_index=true` 时，服务要求先执行全量构建。

## 6. 评测接口

`POST /api/rag/evaluate` 接受一个或多个 `EvaluationCase`：

```json
{
  "cases": [
    {
      "case_id": "case_demo",
      "question": "GitHub Copilot 的价格证据",
      "relevant_document_ids": ["doc_..."],
      "relevant_chunk_ids": ["chunk_..."],
      "query_filters": {"competitor": "GitHub Copilot"}
    }
  ],
  "run_ablations": false
}
```

`run_ablations=true` 会执行 A—F 六组固定消融。D—F 要求配置真实 Cross-Encoder（交叉编码器）重排序器；当前默认 `lexical` 配置会拒绝该消融请求。

## 7. 引用校验

`POST /api/rag/citations/validate` 将引用限制在指定 `query_id` 对应证据白名单中。

```json
{
  "query_id": "qry_...",
  "citations": [
    {
      "chunk_id": "chunk_...",
      "quote": "原文片段",
      "url": "https://example.com/source"
    }
  ]
}
```

校验结果包含 `valid`、`errors`、`warnings`、已验证 Chunk 标识和冲突列表。未知查询标识、白名单外 Chunk、引文不一致和定位信息错误都会阻止引用通过。

## 8. 错误语义

| 条件 | 状态码 |
| --- | ---: |
| 请求字段或业务参数无效 | 400 或 FastAPI 模型校验状态码 422 |
| 结构化文档路径不存在 | 422 |
| 证据或查询轨迹不存在 | 404 |
| Elasticsearch、索引或服务依赖不可用 | 503 |

接口调用方应记录 `query_id`、读取别名、物理索引、嵌入模型和维度。这些字段共同构成检索结果的运行来源。
