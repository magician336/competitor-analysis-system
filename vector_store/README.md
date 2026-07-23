# Vector Store

第二周规划中的 `vector_store/` 在当前实现中由 Elasticsearch 物理索引和读别名承担。

- Mini-RAG 会通过 `scripts/build_index.py` 构建索引。
- 索引元数据、别名、Embedding 维度和检索配置由 `config/mini_rag.yaml` 管理。
- 本目录作为交付清单兼容说明保留，实际索引数据不进入版本库。
