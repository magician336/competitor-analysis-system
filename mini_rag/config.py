"""Validated Mini-RAG configuration with environment overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DataSettings(BaseModel):
    documents_path: str = "data/cleaned/documents.jsonl"
    evaluation_path: str = "data/samples/测试数据集.csv"
    trace_path: str = "data/runtime/retrieval_traces.jsonl"


class ChunkingSettings(BaseModel):
    target_characters: int = Field(default=1200, ge=100)
    maximum_characters: int = Field(default=1800, ge=100)
    overlap_characters: int = Field(default=160, ge=0)
    minimum_characters: int = Field(default=80, ge=1)

    @model_validator(mode="after")
    def validate_limits(self) -> "ChunkingSettings":
        if self.target_characters > self.maximum_characters:
            raise ValueError("target_characters cannot exceed maximum_characters")
        if self.overlap_characters >= self.maximum_characters:
            raise ValueError("overlap_characters must be smaller than maximum_characters")
        return self


class EmbeddingSettings(BaseModel):
    provider: str = "hash"
    model: str = "BAAI/bge-m3"
    dimension: int = Field(default=1024, gt=0)
    normalize: bool = True
    batch_size: int = Field(default=16, gt=0)
    cache_dir: str = ".cache/models"


class ElasticsearchSettings(BaseModel):
    url: str = "http://localhost:9200"
    index_prefix: str = "coderadar_chunks"
    read_alias: str = "coderadar_chunks_current"
    request_timeout_seconds: int = Field(default=30, gt=0)
    verify_certs: bool = False
    number_of_shards: int = Field(default=1, gt=0)
    number_of_replicas: int = Field(default=0, ge=0)


class RetrievalSettings(BaseModel):
    bm25_candidates: int = Field(default=20, gt=0)
    dense_candidates: int = Field(default=20, gt=0)
    fused_candidates: int = Field(default=30, gt=0)
    rerank_candidates: int = Field(default=20, gt=0)
    default_top_k: int = Field(default=8, gt=0)
    maximum_top_k: int = Field(default=50, gt=0)
    rrf_k: int = Field(default=60, ge=1)

    @model_validator(mode="after")
    def validate_windows(self) -> "RetrievalSettings":
        if self.default_top_k > self.maximum_top_k:
            raise ValueError("default_top_k cannot exceed maximum_top_k")
        return self


class RerankerSettings(BaseModel):
    provider: str = "lexical"
    model: str = "BAAI/bge-reranker-v2-m3"
    batch_size: int = Field(default=8, gt=0)
    strict: bool = False


class RankingSettings(BaseModel):
    semantic_weight: float = Field(default=0.70, ge=0.0)
    temporal_weight: float = Field(default=0.12, ge=0.0)
    version_weight: float = Field(default=0.08, ge=0.0)
    evidence_weight: float = Field(default=0.10, ge=0.0)
    temporal_half_life_days: int = Field(default=180, gt=0)

    @model_validator(mode="after")
    def validate_weights(self) -> "RankingSettings":
        total = self.semantic_weight + self.temporal_weight + self.version_weight + self.evidence_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError("ranking weights must sum to 1.0")
        return self


class ServiceSettings(BaseModel):
    trace_retention: int = Field(default=1000, gt=0)
    fail_on_missing_index: bool = True
    current_only_for_current_intent: bool = True


class MiniRAGSettings(BaseModel):
    """Complete validated settings used by indexing, retrieval and the API."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    project_root: Path = Field(default_factory=lambda: Path.cwd())
    data: DataSettings = Field(default_factory=DataSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    elasticsearch: ElasticsearchSettings = Field(default_factory=ElasticsearchSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    ranking: RankingSettings = Field(default_factory=RankingSettings)
    service: ServiceSettings = Field(default_factory=ServiceSettings)

    def resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (self.project_root / path).resolve()

    @property
    def documents_path(self) -> Path:
        return self.resolve_path(self.data.documents_path)

    @property
    def evaluation_path(self) -> Path:
        return self.resolve_path(self.data.evaluation_path)

    @property
    def trace_path(self) -> Path:
        return self.resolve_path(self.data.trace_path)


_ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    "MINIRAG_DOCUMENTS_PATH": ("data", "documents_path"),
    "MINIRAG_EVALUATION_PATH": ("data", "evaluation_path"),
    "MINIRAG_TRACE_PATH": ("data", "trace_path"),
    "MINIRAG_ELASTICSEARCH_URL": ("elasticsearch", "url"),
    "MINIRAG_INDEX_PREFIX": ("elasticsearch", "index_prefix"),
    "MINIRAG_READ_ALIAS": ("elasticsearch", "read_alias"),
    "MINIRAG_EMBEDDING_PROVIDER": ("embedding", "provider"),
    "MINIRAG_EMBEDDING_MODEL": ("embedding", "model"),
    "MINIRAG_EMBEDDING_DIMENSION": ("embedding", "dimension"),
    "MINIRAG_MODEL_CACHE_DIR": ("embedding", "cache_dir"),
    "MINIRAG_RERANKER_PROVIDER": ("reranker", "provider"),
    "MINIRAG_RERANKER_MODEL": ("reranker", "model"),
    "MINIRAG_RERANKER_STRICT": ("reranker", "strict"),
}


def _set_nested(mapping: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = mapping
    for segment in path[:-1]:
        current = current.setdefault(segment, {})
    current[path[-1]] = value


def load_settings(
    project_root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> MiniRAGSettings:
    """Load YAML configuration and apply documented environment overrides.

    ``MINIRAG_PROFILE`` provides a one-shot shortcut for switching between
    pre‑defined config bundles without setting individual overrides:

    * ``default`` (or unset) → ``config/mini_rag.yaml`` (hash + lexical baseline)
    * ``formal`` → ``config/mini_rag.formal.yaml`` (BGE‑M3 + Cross‑Encoder)

    An explicit ``config_path`` argument or ``MINIRAG_CONFIG_PATH`` env var
    takes precedence over the profile — set either one to bypass profile logic.
    Individual ``MINIRAG_*`` environment variables (embedding provider, model,
    reranker, …) still override the loaded YAML values regardless of profile.
    """

    root = Path(project_root or Path.cwd()).resolve()
    load_dotenv(root / ".env", override=False)

    # ── Profile shortcut ────────────────────────────────────────────────
    # Only applies when neither caller arg nor MINIRAG_CONFIG_PATH is set.
    configured = config_path or os.getenv("MINIRAG_CONFIG_PATH")
    if configured is None:
        profile = os.getenv("MINIRAG_PROFILE", "").strip().lower()
        if profile == "formal":
            configured = "config/mini_rag.formal.yaml"
        else:
            configured = "config/mini_rag.yaml"
    # (if configured was set explicitly, use it as-is)
    path = Path(configured)
    if not path.is_absolute():
        path = root / path
    payload: dict[str, Any] = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Mini-RAG config root must be an object: {path}")
        payload.update(loaded)
    payload["project_root"] = root
    for variable, target in _ENV_OVERRIDES.items():
        value = os.getenv(variable)
        if value is not None and value != "":
            _set_nested(payload, target, value)
    return MiniRAGSettings.model_validate(payload)
