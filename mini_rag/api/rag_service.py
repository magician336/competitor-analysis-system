"""Unified Python service for indexing, retrieval, evidence and evaluation."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any, Iterable, Sequence

from mini_rag.config import MiniRAGSettings, load_settings
from mini_rag.embedding import EmbeddingService, create_embedding_provider
from mini_rag.ingestion import DocumentLoader
from mini_rag.models import (
    Chunk,
    Evidence,
    RAGQuery,
    RAGResponse,
    RetrievalTrace,
    SearchCandidate,
)
from mini_rag.ranking import (
    CrossEncoderReranker,
    EvidenceRanker,
    RankingPipeline,
    RRFFusion,
    TermOverlapReranker,
    TemporalVersionRanker,
)

from .trace_store import TraceStore


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000.0, 3)


def _serialise(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if is_dataclass(value):
        return asdict(value)
    return value


class MiniRAGService:
    """Coordinate the complete retrieval pipeline behind one stable contract."""

    def __init__(
        self,
        *,
        settings: MiniRAGSettings,
        backend: Any,
        embedding_service: EmbeddingService,
        bm25_retriever: Any,
        dense_retriever: Any,
        query_parser: Any,
        ranking_pipeline: RankingPipeline,
        citation_builder: Any,
        conflict_detector: Any,
        citation_validator: Any,
        trace_store: TraceStore,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.embedding_service = embedding_service
        self.bm25_retriever = bm25_retriever
        self.dense_retriever = dense_retriever
        self.query_parser = query_parser
        self.ranking_pipeline = ranking_pipeline
        self.citation_builder = citation_builder
        self.conflict_detector = conflict_detector
        self.citation_validator = citation_validator
        self.trace_store = trace_store
        self.rrf = RRFFusion(k=settings.retrieval.rrf_k)
        self.index_name = settings.elasticsearch.read_alias
        self._index_lock = RLock()

    def query(self, query: RAGQuery | str, **kwargs: Any) -> RAGResponse:
        return self._run_query(query, kwargs=kwargs, ablation=None)

    def query_with_ablation(
        self,
        query: RAGQuery | str,
        ablation: Any,
        **kwargs: Any,
    ) -> RAGResponse:
        """Run one fixed ablation specification through the public response contract."""

        return self._run_query(query, kwargs=kwargs, ablation=ablation)

    @staticmethod
    def _ablation_flag(ablation: Any, name: str, default: bool = True) -> bool:
        if ablation is None:
            return default
        if isinstance(ablation, dict):
            return bool(ablation.get(name, default))
        return bool(getattr(ablation, name, default))

    def _run_query(
        self,
        query: RAGQuery | str,
        *,
        kwargs: dict[str, Any],
        ablation: Any,
    ) -> RAGResponse:
        supplied = query if isinstance(query, RAGQuery) else RAGQuery(question=query, **kwargs)
        explicit_names = set(supplied.model_fields_set) - {"question", "top_k"}
        requested_top_k = (
            supplied.top_k
            if "top_k" in supplied.model_fields_set
            else self.settings.retrieval.default_top_k
        )
        if requested_top_k > self.settings.retrieval.maximum_top_k:
            raise ValueError(
                f"top_k cannot exceed configured maximum "
                f"{self.settings.retrieval.maximum_top_k}"
            )
        explicit = supplied.model_dump(
            mode="python",
            include=explicit_names,
        )
        request = self.query_parser.parse(
            supplied.question,
            explicit_filters=explicit,
        )
        request.top_k = requested_top_k
        overall_started = perf_counter()
        stage_latency: dict[str, float] = {}
        filters = request.filters()

        use_bm25 = self._ablation_flag(ablation, "use_bm25")
        use_dense = self._ablation_flag(ablation, "use_dense")
        use_rrf = self._ablation_flag(ablation, "use_rrf")
        use_reranker = self._ablation_flag(ablation, "use_reranker")
        use_temporal_version = self._ablation_flag(ablation, "use_temporal_version")
        use_evidence_ranking = self._ablation_flag(ablation, "use_evidence_ranking")
        bm25_limit = max(self.settings.retrieval.bm25_candidates, request.top_k)
        dense_limit = max(self.settings.retrieval.dense_candidates, request.top_k)
        fused_limit = max(self.settings.retrieval.fused_candidates, request.top_k)
        rerank_limit = max(self.settings.retrieval.rerank_candidates, request.top_k)

        started = perf_counter()
        bm25 = (
            self.bm25_retriever.search(
                request.question,
                filters=filters,
                top_k=bm25_limit,
            )
            if use_bm25
            else []
        )
        stage_latency["bm25"] = _elapsed_ms(started)

        started = perf_counter()
        dense = (
            self.dense_retriever.search(
                request.question,
                filters=filters,
                top_k=dense_limit,
            )
            if use_dense
            else []
        )
        stage_latency["dense"] = _elapsed_ms(started)

        started = perf_counter()
        if use_rrf:
            fused = self.rrf.fuse(
                {"bm25": bm25, "dense": dense},
                top_k=fused_limit,
            )
        else:
            fused = []
            seen_chunk_ids: set[str] = set()
            for candidate in [*bm25, *dense]:
                if candidate.chunk_id in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(candidate.chunk_id)
                fused.append(candidate)
                if len(fused) >= fused_limit:
                    break
        stage_latency["fusion"] = _elapsed_ms(started)

        started = perf_counter()
        ranked = self.ranking_pipeline.rank(
            request,
            fused[:rerank_limit],
            top_k=None,
            use_reranker=use_reranker,
            use_temporal_version=use_temporal_version,
            use_evidence_ranking=use_evidence_ranking,
        )
        stage_latency["ranking"] = _elapsed_ms(started)
        reranker_backend = getattr(self.ranking_pipeline.reranker, "last_backend", None)
        reranker_error = getattr(self.ranking_pipeline.reranker, "last_error", None)
        warnings: list[str] = []
        configured_lexical = self.settings.reranker.provider.casefold() in {
            "lexical",
            "term_overlap",
        }
        if use_reranker and not configured_lexical and reranker_backend != "cross_encoder":
            warnings.append(
                f"configured Cross-Encoder was not used; backend={reranker_backend}; "
                f"error={reranker_error}"
            )

        started = perf_counter()
        evidence: list[Evidence] = self.citation_builder.build(
            ranked,
            question=request.question,
            top_k=request.top_k,
        )
        conflicts = self.conflict_detector.detect(ranked)
        stage_latency["evidence"] = _elapsed_ms(started)

        try:
            physical_index = self.backend.resolve_alias(self.index_name)
        except Exception:
            physical_index = None

        response = RAGResponse(
            query=request.question,
            parsed_filters=filters,
            evidence=evidence,
            conflicts=conflicts,
            retrieval_trace=RetrievalTrace(
                bm25_candidates=len(bm25),
                dense_candidates=len(dense),
                fused_candidates=len(fused),
                reranked_candidates=len(ranked),
                returned_candidates=len(evidence),
                latency_ms=_elapsed_ms(overall_started),
                stage_latency_ms=stage_latency,
                warnings=warnings,
                retrieval_config={
                    "index": self.index_name,
                    "physical_index": physical_index,
                    "embedding_model": self.embedding_service.model_name,
                    "embedding_dimension": self.embedding_service.dimension,
                    "rrf_k": self.settings.retrieval.rrf_k,
                    "reranker_provider": self.settings.reranker.provider,
                    "reranker_model": self.settings.reranker.model,
                    "reranker_backend": reranker_backend,
                    "reranker_error": reranker_error,
                    "ranking_weights": self.settings.ranking.model_dump(mode="json"),
                    "effective_candidate_windows": {
                        "bm25": bm25_limit,
                        "dense": dense_limit,
                        "fused": fused_limit,
                        "rerank": rerank_limit,
                    },
                    "ablation": _serialise(ablation) if ablation is not None else None,
                },
            ),
        )
        self.trace_store.put(response)
        return response

    def get_trace(self, query_id: str) -> dict[str, Any] | None:
        return self.trace_store.get(query_id)

    def get_evidence(self, chunk_id: str) -> Evidence | None:
        raw = self.backend.get_document(self.index_name, chunk_id)
        if raw is None:
            return None
        source = raw.get("_source", raw) if isinstance(raw, dict) else raw
        if isinstance(source, dict):
            source = dict(source)
            source.pop("indexed_at", None)
        chunk = source if isinstance(source, Chunk) else Chunk.model_validate(source)
        return Evidence.from_candidate(SearchCandidate(chunk=chunk))

    def validate_citations(
        self,
        citations: Sequence[Any],
        *,
        query_id: str,
    ) -> Any:
        trace = self.get_trace(query_id)
        if trace is None:
            raise KeyError(f"unknown query_id: {query_id}")
        evidence = trace.get("evidence", [])
        allowed = [item["chunk_id"] for item in evidence]
        return self.citation_validator.validate(
            citations,
            evidence,
            allowed_chunk_ids=allowed,
            conflicts=trace.get("conflicts", []),
        )

    def health(self) -> dict[str, Any]:
        index_embedding_model: str | None = None
        index_embedding_dimension: int | None = None
        embedding_compatible = False
        try:
            from mini_rag.indexing import embedding_metadata

            backend_health = self.backend.health()
            physical_index = self.backend.resolve_alias(self.index_name)
            count = self.backend.count(self.index_name)
            index_embedding_model, index_embedding_dimension = embedding_metadata(
                self.backend.get_mapping(physical_index)
            )
            embedding_compatible = (
                index_embedding_dimension == self.embedding_service.dimension
                and (
                    not index_embedding_model
                    or index_embedding_model == self.embedding_service.model_name
                )
            )
            status = "ready" if embedding_compatible else "degraded"
            if not embedding_compatible:
                backend_health = {
                    **dict(backend_health),
                    "embedding_compatibility_error": (
                        "query provider "
                        f"{self.embedding_service.model_name!r}/"
                        f"{self.embedding_service.dimension} does not match index "
                        f"{index_embedding_model!r}/{index_embedding_dimension}"
                    ),
                }
        except Exception as exc:
            backend_health = {"error": f"{type(exc).__name__}: {exc}"}
            physical_index = None
            count = None
            status = "degraded"
        return {
            "status": status,
            "index": self.index_name,
            "physical_index": physical_index,
            "indexed_chunks": count,
            "embedding_model": self.embedding_service.model_name,
            "embedding_dimension": self.embedding_service.dimension,
            "index_embedding_model": index_embedding_model,
            "index_embedding_dimension": index_embedding_dimension,
            "embedding_compatible": embedding_compatible,
            "backend": _serialise(backend_health),
        }

    def build_index(
        self,
        documents_path: str | Path | None = None,
        *,
        rebuild: bool = True,
        delete_missing: bool = False,
    ) -> dict[str, Any]:
        with self._index_lock:
            return self._build_index(
                documents_path,
                rebuild=rebuild,
                delete_missing=delete_missing,
            )

    def _build_index(
        self,
        documents_path: str | Path | None = None,
        *,
        rebuild: bool = True,
        delete_missing: bool = False,
    ) -> dict[str, Any]:
        from mini_rag.chunking import ChunkingDispatcher
        from mini_rag.indexing import IndexBuilder, IndexManager, SearchBackendError

        path = Path(documents_path) if documents_path is not None else self.settings.documents_path
        documents = DocumentLoader(path).load()
        chunks = ChunkingDispatcher(self.settings.chunking).chunk_documents(documents)
        manager = IndexManager(
            self.backend,
            base_name=self.settings.elasticsearch.index_prefix,
            alias=self.settings.elasticsearch.read_alias,
            shards=self.settings.elasticsearch.number_of_shards,
            replicas=self.settings.elasticsearch.number_of_replicas,
        )
        if rebuild:
            report = manager.rebuild(chunks, self.embedding_service)
        else:
            physical_index = manager.current_index()
            if physical_index is None:
                if self.settings.service.fail_on_missing_index:
                    raise SearchBackendError(
                        f"read alias {self.index_name!r} does not exist; "
                        "run a full index rebuild first"
                    )
                report = manager.rebuild(chunks, self.embedding_service)
                return {
                    "documents": len(documents),
                    "chunks": len(chunks),
                    "report": _serialise(report),
                }
            builder = IndexBuilder(
                self.backend,
                self.embedding_service,
                index_name=physical_index,
            )
            report = builder.build(
                chunks,
                recreate=False,
                incremental=True,
                delete_missing=delete_missing,
            )
        return {
            "documents": len(documents),
            "chunks": len(chunks),
            "report": _serialise(report),
        }

    def evaluate(self, cases: Iterable[Any]) -> Any:
        from mini_rag.evaluation import RetrievalEvaluator

        evaluator = RetrievalEvaluator(lambda request: self.query(request))
        try:
            physical_index = self.backend.resolve_alias(self.index_name)
        except Exception:
            physical_index = None
        result = evaluator.evaluate(
            list(cases),
            config={
                "index_alias": self.index_name,
                "index_version": physical_index,
                "embedding_model": self.embedding_service.model_name,
                "embedding_dimension": self.embedding_service.dimension,
                "bm25_candidates": self.settings.retrieval.bm25_candidates,
                "dense_candidates": self.settings.retrieval.dense_candidates,
                "fused_candidates": self.settings.retrieval.fused_candidates,
                "rerank_candidates": self.settings.retrieval.rerank_candidates,
                "rrf_k": self.settings.retrieval.rrf_k,
                "reranker_provider": self.settings.reranker.provider,
                "reranker_model": self.settings.reranker.model,
                "ranking_weights": self.settings.ranking.model_dump(mode="json"),
            },
        )
        result.config["actual_reranker_backend"] = getattr(
            self.ranking_pipeline.reranker, "last_backend", None
        )
        result.config["reranker_error"] = getattr(
            self.ranking_pipeline.reranker, "last_error", None
        )
        return result

    def evaluate_ablations(self, cases: Iterable[Any]) -> dict[str, Any]:
        """Run the six fixed A-F retrieval ablations on one materialized case set."""

        from mini_rag.evaluation import ABLATION_SPECS, AblationRunner, RetrievalEvaluator

        if self.settings.reranker.provider.casefold() in {"lexical", "term_overlap"}:
            raise ValueError(
                "A-F ablations D-F require a configured Cross-Encoder reranker; "
                "select a model-backed reranker provider"
            )
        materialized = list(cases)
        runner = AblationRunner(
            lambda spec: RetrievalEvaluator(
                lambda request: self.query_with_ablation(request, spec)
            )
        )
        full_config = self.evaluate([]).config
        reranker = self.ranking_pipeline.reranker
        previous_strict = getattr(reranker, "strict", False)
        reranker.strict = True
        try:
            if materialized:
                from mini_rag.models import EvaluationCase, RAGQuery

                first_case = (
                    materialized[0]
                    if isinstance(materialized[0], EvaluationCase)
                    else EvaluationCase.model_validate(materialized[0])
                )
                query_fields = set(RAGQuery.model_fields) - {"question"}
                warmup_filters = {
                    name: value
                    for name, value in first_case.query_filters.items()
                    if name in query_fields
                }
                warmup_filters["top_k"] = max(
                    10,
                    int(warmup_filters.get("top_k", 0) or 0),
                )
                warmup_request = RAGQuery(
                    question=first_case.question,
                    **warmup_filters,
                )
                for spec in ABLATION_SPECS:
                    self.query_with_ablation(warmup_request, spec)
            results = runner.run(materialized, shared_config=full_config)
        finally:
            reranker.strict = previous_strict
        actual_backend = getattr(reranker, "last_backend", None)
        if actual_backend != "cross_encoder":
            raise RuntimeError(
                "Cross-Encoder ablation could not run with the configured model: "
                f"{getattr(reranker, 'last_error', None)}"
            )
        for result in results.values():
            result.config["actual_reranker_backend"] = actual_backend
            result.config["warmup_queries_per_group"] = 1
            result.config["latency_scope"] = "steady_state_after_group_warmup"
        return results


def create_service(
    settings: MiniRAGSettings | None = None,
    *,
    backend: Any | None = None,
) -> MiniRAGService:
    """Build a lazily-model-loading Elasticsearch-backed service."""

    from mini_rag.evidence import CitationBuilder, CitationValidator, ConflictDetector
    from mini_rag.indexing import ElasticsearchClient
    from mini_rag.retrieval import BM25Retriever, DenseRetriever, QueryParser, QueryRewriter

    resolved = settings or load_settings()
    provider = create_embedding_provider(
        resolved.embedding.provider,
        model_name=resolved.embedding.model,
        dimension=resolved.embedding.dimension,
        cache_folder=str(resolved.resolve_path(resolved.embedding.cache_dir)),
        normalize_embeddings=resolved.embedding.normalize,
    )
    embeddings = EmbeddingService(provider, batch_size=resolved.embedding.batch_size)
    search_backend = backend or ElasticsearchClient(
        base_url=resolved.elasticsearch.url,
        request_timeout=resolved.elasticsearch.request_timeout_seconds,
        verify_certs=resolved.elasticsearch.verify_certs,
    )
    rewriter = QueryRewriter()
    bm25 = BM25Retriever(
        search_backend,
        resolved.elasticsearch.read_alias,
        query_rewriter=rewriter,
    )
    dense = DenseRetriever(
        search_backend,
        resolved.elasticsearch.read_alias,
        embedding_service=embeddings,
        query_rewriter=rewriter,
    )
    if resolved.reranker.provider.casefold() in {"lexical", "term_overlap"}:
        reranker = TermOverlapReranker()
    else:
        reranker = CrossEncoderReranker(
            model_name=resolved.reranker.model,
            batch_size=resolved.reranker.batch_size,
            strict=resolved.reranker.strict,
        )
    ranking = RankingPipeline(
        reranker=reranker,
        temporal_ranker=TemporalVersionRanker(
            half_life_days=resolved.ranking.temporal_half_life_days,
            temporal_weight=resolved.ranking.temporal_weight,
            version_weight=resolved.ranking.version_weight,
        ),
        evidence_ranker=EvidenceRanker(evidence_weight=resolved.ranking.evidence_weight),
        semantic_weight=resolved.ranking.semantic_weight,
        temporal_weight=resolved.ranking.temporal_weight,
        version_weight=resolved.ranking.version_weight,
        evidence_weight=resolved.ranking.evidence_weight,
    )
    return MiniRAGService(
        settings=resolved,
        backend=search_backend,
        embedding_service=embeddings,
        bm25_retriever=bm25,
        dense_retriever=dense,
        query_parser=QueryParser(
            current_only_for_current_intent=(
                resolved.service.current_only_for_current_intent
            )
        ),
        ranking_pipeline=ranking,
        citation_builder=CitationBuilder(),
        conflict_detector=ConflictDetector(),
        citation_validator=CitationValidator(),
        trace_store=TraceStore(resolved.trace_path, resolved.service.trace_retention),
    )


def query_rag(question: str, **kwargs: Any) -> dict[str, Any]:
    """Public Python contract matching ``POST /api/rag/query``."""

    return create_service().query(RAGQuery(question=question, **kwargs)).model_dump(mode="json")


__all__ = ["MiniRAGService", "create_service", "query_rag"]
