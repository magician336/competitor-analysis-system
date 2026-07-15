"""Elasticsearch mapping for chunk-level hybrid search."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


INDEX_SCHEMA_VERSION = "1.0"


def build_index_mapping(
    embedding_dimension: int,
    *,
    embedding_model: str | None = None,
    shards: int = 1,
    replicas: int = 0,
) -> dict[str, Any]:
    """Build a deployable mapping with one fixed dense-vector dimension."""

    if embedding_dimension < 1:
        raise ValueError("embedding_dimension must be positive")
    if shards < 1 or replicas < 0:
        raise ValueError("invalid shard or replica count")

    return {
        "settings": {
            "number_of_shards": shards,
            "number_of_replicas": replicas,
            # Incremental synchronization compares embedding model/dimension
            # against the stored vector. CodeRadar also requires full source
            # fidelity for evidence audits and deterministic reindexing.
            "index.mapping.exclude_source_vectors": False,
            "analysis": {
                "analyzer": {
                    # The standard tokenizer handles Latin terms, version
                    # punctuation, and CJK text without an external IK plugin.
                    "coderadar_text": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": ["lowercase", "asciifolding"],
                    }
                }
            },
        },
        "mappings": {
            "dynamic": False,
            "_meta": {
                "schema_version": INDEX_SCHEMA_VERSION,
                "embedding_model": embedding_model,
                "embedding_dimension": embedding_dimension,
            },
            "properties": {
                "schema_version": {"type": "keyword"},
                "chunk_id": {"type": "keyword"},
                "document_id": {"type": "keyword"},
                "version_id": {"type": "keyword"},
                "raw_record_id": {"type": "keyword"},
                "raw_path": {"type": "keyword", "index": False},
                "chunk_index": {"type": "integer"},
                "title": {
                    "type": "text",
                    "analyzer": "coderadar_text",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 1024}},
                },
                "content": {"type": "text", "analyzer": "coderadar_text"},
                "heading_path": {
                    "type": "text",
                    "analyzer": "coderadar_text",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 1024}},
                },
                "char_start": {"type": "integer"},
                "char_end": {"type": "integer"},
                "section_type": {"type": "keyword"},
                "competitor": {"type": "keyword"},
                "source_type": {"type": "keyword"},
                "evidence_level": {"type": "keyword"},
                "event_type": {"type": "keyword"},
                "dimension_tags": {"type": "keyword"},
                "url": {"type": "keyword", "index": False},
                "raw_version": {"type": "keyword"},
                "product_version": {"type": "keyword"},
                "publish_time": {"type": "date"},
                "crawl_time": {"type": "date"},
                "valid_from": {"type": "date"},
                "valid_to": {"type": "date"},
                "is_current": {"type": "boolean"},
                "language": {"type": "keyword"},
                "author": {"type": "keyword"},
                "content_hash": {"type": "keyword"},
                "chunk_hash": {"type": "keyword"},
                "source_metadata": {"type": "flattened"},
                "change_category": {"type": "keyword"},
                "plan_name": {"type": "keyword"},
                "price_value": {"type": "double"},
                "currency": {"type": "keyword"},
                "billing_period": {"type": "keyword"},
                "applicable_plans": {"type": "keyword"},
                "repository": {"type": "keyword"},
                "github_kind": {"type": "keyword"},
                "github_number": {"type": "long"},
                "github_labels": {"type": "keyword"},
                "github_state": {"type": "keyword"},
                "author_type": {"type": "keyword"},
                "comment_url": {"type": "keyword", "index": False},
                "comment_time": {"type": "date"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": embedding_dimension,
                    "index": True,
                    "similarity": "cosine",
                },
                "embedding_model": {"type": "keyword"},
                "embedding_dimension": {"type": "integer"},
                "indexed_at": {"type": "date"},
            },
        },
    }


def embedding_metadata(mapping: dict[str, Any]) -> tuple[str | None, int | None]:
    """Extract model and vector dimension from a mapping response or body."""

    body = mapping
    # Elasticsearch GET /index/_mapping wraps mappings in the index name.
    if "mappings" not in body and len(body) == 1:
        body = next(iter(body.values()))
    mappings = body.get("mappings", body)
    meta = mappings.get("_meta", {})
    properties = mappings.get("properties", {})
    dimension = meta.get("embedding_dimension")
    if dimension is None:
        dimension = properties.get("embedding", {}).get("dims")
    return meta.get("embedding_model"), int(dimension) if dimension is not None else None


def clone_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy suitable for backend storage or mutation."""

    return deepcopy(mapping)


# Compatibility names used by scripts and tests.
create_index_mapping = build_index_mapping
INDEX_MAPPING_VERSION = INDEX_SCHEMA_VERSION


__all__ = [
    "INDEX_MAPPING_VERSION",
    "INDEX_SCHEMA_VERSION",
    "build_index_mapping",
    "clone_mapping",
    "create_index_mapping",
    "embedding_metadata",
]
