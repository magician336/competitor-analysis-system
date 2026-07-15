"""Chunk indexing backends and lifecycle management."""

from .elasticsearch_client import (
    BackendHit,
    BulkResult,
    ElasticsearchBackend,
    ElasticsearchClient,
    InMemorySearchBackend,
    MemorySearchBackend,
    SearchBackend,
    SearchBackendError,
    build_filter_clauses,
    normalize_filters,
    tokenize_for_search,
)
from .incremental_indexer import IncrementalIndexer
from .index_builder import IndexBuilder, IndexingReport, chunk_to_document
from .index_manager import IndexManager
from .index_mapping import (
    INDEX_MAPPING_VERSION,
    INDEX_SCHEMA_VERSION,
    build_index_mapping,
    create_index_mapping,
    embedding_metadata,
)

__all__ = [
    "BackendHit",
    "BulkResult",
    "ElasticsearchBackend",
    "ElasticsearchClient",
    "INDEX_MAPPING_VERSION",
    "INDEX_SCHEMA_VERSION",
    "InMemorySearchBackend",
    "IncrementalIndexer",
    "IndexBuilder",
    "IndexManager",
    "IndexingReport",
    "MemorySearchBackend",
    "SearchBackend",
    "SearchBackendError",
    "build_filter_clauses",
    "build_index_mapping",
    "chunk_to_document",
    "create_index_mapping",
    "embedding_metadata",
    "normalize_filters",
    "tokenize_for_search",
]
