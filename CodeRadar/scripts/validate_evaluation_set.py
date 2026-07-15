"""Validate evaluation labels against the active Elasticsearch chunk index."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from mini_rag.config import load_settings
from mini_rag.evaluation import (
    load_evaluation_cases,
    metadata_filter_accuracy,
    validate_evaluation_cases,
)
from mini_rag.indexing import ElasticsearchClient, embedding_metadata


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "runtime" / "evaluation_set_validation.json"
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "samples" / "评测集说明.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _normalise(value: str) -> str:
    return " ".join(value.casefold().split())


def validate_indexed_cases(
    cases: Sequence[Any],
    *,
    get_chunk: Any,
) -> dict[str, Any]:
    errors: list[str] = []
    checked_chunks: set[str] = set()
    for case in cases:
        chunks: list[dict[str, Any]] = []
        for chunk_id in case.relevant_chunk_ids:
            chunk = get_chunk(chunk_id)
            if not chunk:
                errors.append(f"{case.case_id}: missing chunk {chunk_id}")
                continue
            checked_chunks.add(chunk_id)
            chunks.append(chunk)
            if chunk.get("document_id") not in case.relevant_document_ids:
                errors.append(
                    f"{case.case_id}: chunk {chunk_id} has unlabelled document_id "
                    f"{chunk.get('document_id')}"
                )
        if chunks and metadata_filter_accuracy(chunks, case.query_filters) != 1.0:
            errors.append(f"{case.case_id}: relevant chunks do not satisfy query_filters")
        if case.expected_evidence_quote:
            quote = _normalise(case.expected_evidence_quote)
            if not any(quote in _normalise(str(chunk.get("content", ""))) for chunk in chunks):
                errors.append(
                    f"{case.case_id}: expected_evidence_quote is absent from relevant chunks"
                )
    return {
        "valid": not errors,
        "case_count": len(cases),
        "checked_chunk_count": len(checked_chunks),
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", help="Evaluation CSV path")
    parser.add_argument("--config", help="Mini-RAG YAML path")
    parser.add_argument("--output", help="Validation result JSON path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(PROJECT_ROOT, args.config)
    dataset = Path(args.dataset).resolve() if args.dataset else settings.evaluation_path
    output = Path(args.output).resolve() if args.output else DEFAULT_OUTPUT
    cases = load_evaluation_cases(dataset)
    validate_evaluation_cases(cases)
    backend = ElasticsearchClient(
        base_url=settings.elasticsearch.url,
        request_timeout=settings.elasticsearch.request_timeout_seconds,
        verify_certs=settings.elasticsearch.verify_certs,
    )
    alias = settings.elasticsearch.read_alias
    physical_index = backend.resolve_alias(alias)
    result = validate_indexed_cases(
        cases,
        get_chunk=lambda chunk_id: backend.get_document(alias, chunk_id),
    )
    index_model, index_dimension = embedding_metadata(backend.get_mapping(physical_index))
    manifest = (
        json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
        if DEFAULT_MANIFEST.is_file()
        else {}
    )
    result.update(
        {
            "dataset": str(dataset),
            "dataset_sha256": _sha256(dataset),
            "human_review_status": manifest.get("human_review_status", "unknown"),
            "index_alias": alias,
            "physical_index": physical_index,
            "indexed_chunk_count": backend.count(alias),
            "index_embedding_model": index_model,
            "index_embedding_dimension": index_dimension,
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
