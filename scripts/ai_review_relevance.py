"""Create an auditable AI pre-review of retrieval relevance labels.

The command deliberately keeps AI judgements separate from human review.  It
never calls ``finalize_evaluation_review`` and never marks a dataset as human
reviewed.  Each case is pooled from BM25, dense, and hybrid retrieval, with the
prepared seed chunks added even when retrieval misses them.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from mini_rag.api import create_service
from mini_rag.config import load_settings
from mini_rag.evaluation import (
    ABLATION_SPECS,
    load_evaluation_cases,
    validate_evaluation_cases,
)
from mini_rag.models import EvaluationCase, RAGQuery
from mini_rag.ranking import CrossEncoderReranker
from mini_rag.ranking.reranker import lexical_relevance
from scripts.prepare_evaluation_set import (
    AI_REVIEW_FIELDS,
    HUMAN_REVIEW_FIELDS,
    REVIEW_FIELDS,
    _write_cases,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "mini_rag.formal.yaml"
DEFAULT_REVIEW = PROJECT_ROOT / "data" / "samples" / "评测集人工复核.csv"
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "samples" / "评测集说明.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "runtime" / "ai_relevance"
POOL_SPECS = tuple(spec for spec in ABLATION_SPECS if spec.group in {"A", "B", "C"})
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class RelevanceScorer(Protocol):
    model_name: str

    @property
    def backend(self) -> str: ...

    def score(self, question: str, texts: Sequence[str]) -> Sequence[float]: ...


class LocalCrossEncoderScorer:
    """Expose normalized scores from the configured local Cross-Encoder."""

    def __init__(self, reranker: CrossEncoderReranker) -> None:
        self.reranker = reranker
        self.model_name = str(reranker.model_name or "")

    @property
    def backend(self) -> str:
        return str(self.reranker.last_backend)

    def score(self, question: str, texts: Sequence[str]) -> list[float]:
        predictions = self.reranker._predict(question, texts)
        scores = self.reranker._normalise_predictions(predictions)
        if self.reranker.last_backend != "cross_encoder":
            raise RuntimeError(
                "AI relevance review requires the configured local Cross-Encoder; "
                f"actual backend={self.reranker.last_backend!r}"
            )
        return scores


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalise(text: Any) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(text or "")).casefold().split()
    )


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _json_value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(text, encoding=encoding)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_cases(path: Path, cases: Sequence[EvaluationCase]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".csv", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        _write_cases(temporary, cases)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_review(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    if not fieldnames or not rows:
        raise ValueError("review CSV must contain a header and at least one row")
    for field_name in REVIEW_FIELDS:
        if field_name not in fieldnames:
            fieldnames.append(field_name)
    return fieldnames, rows


def _atomic_write_review(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(fieldnames), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    _atomic_write_text(path, buffer.getvalue(), encoding="utf-8-sig")


def _evidence_record(evidence: Any) -> dict[str, Any]:
    required = ("chunk_id", "document_id", "content", "title", "url")
    missing = [name for name in required if not _value(evidence, name)]
    if missing:
        raise ValueError(f"retrieval evidence is missing required fields: {missing}")
    return {
        "chunk_id": str(_value(evidence, "chunk_id")),
        "document_id": str(_value(evidence, "document_id")),
        "version_id": str(_value(evidence, "version_id", "") or ""),
        "title": str(_value(evidence, "title", "") or ""),
        "content": str(_value(evidence, "content", "") or ""),
        "quote": str(_value(evidence, "quote", "") or ""),
        "url": str(_value(evidence, "url", "") or ""),
        "competitor": str(_value(evidence, "competitor", "") or ""),
        "source_type": _json_value(_value(evidence, "source_type")),
        "event_type": _json_value(_value(evidence, "event_type")),
        "dimension_tags": _json_value(_value(evidence, "dimension_tags", [])),
        "product_version": _value(evidence, "product_version"),
        "is_current": bool(_value(evidence, "is_current", True)),
        "retrievals": {},
        "seed_relevant": False,
    }


def _retrieval_entry(evidence: Any, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "bm25_rank": _value(evidence, "bm25_rank"),
        "dense_rank": _value(evidence, "dense_rank"),
        "rrf_score": _value(evidence, "rrf_score"),
        "final_score": _value(evidence, "final_score"),
    }


def collect_candidate_pool(
    service: Any,
    case: EvaluationCase,
    *,
    seed_chunk_ids: Sequence[str],
    top_n: int,
) -> list[dict[str, Any]]:
    """Collect the A/B/C Top-N union and force all prepared seed chunks into it."""

    query_fields = set(RAGQuery.model_fields) - {"question", "top_k"}
    filters = {
        name: value
        for name, value in case.query_filters.items()
        if name in query_fields
    }
    request = RAGQuery(question=case.question, top_k=top_n, **filters)
    by_chunk: dict[str, dict[str, Any]] = {}
    for spec in POOL_SPECS:
        response = service.query_with_ablation(request, spec)
        for rank, evidence in enumerate(_value(response, "evidence", []) or [], start=1):
            chunk_id = str(_value(evidence, "chunk_id", "") or "")
            if not chunk_id:
                raise ValueError(f"{case.case_id}: retrieval returned an empty chunk_id")
            record = by_chunk.setdefault(chunk_id, _evidence_record(evidence))
            record["retrievals"][spec.group] = _retrieval_entry(evidence, rank)

    for chunk_id in dict.fromkeys(str(item) for item in seed_chunk_ids if item):
        record = by_chunk.get(chunk_id)
        if record is None:
            evidence = service.get_evidence(chunk_id)
            if evidence is None:
                raise ValueError(
                    f"{case.case_id}: seed chunk is absent from the active index: {chunk_id}"
                )
            record = _evidence_record(evidence)
            by_chunk[chunk_id] = record
        record["seed_relevant"] = True

    if not by_chunk:
        raise ValueError(f"{case.case_id}: retrieval and seed pooling returned no candidates")

    def order(record: Mapping[str, Any]) -> tuple[Any, ...]:
        ranks = [item["rank"] for item in record["retrievals"].values()]
        return (
            not bool(record["seed_relevant"]),
            min(ranks) if ranks else top_n + 1,
            str(record["chunk_id"]),
        )

    return sorted(by_chunk.values(), key=order)


def _grade(
    *,
    cross_encoder_score: float,
    lexical_score: float,
    quote_match: bool,
    seed_relevant: bool,
    retrieval_agreement: int,
) -> tuple[int, float, float]:
    agreement = min(1.0, retrieval_agreement / len(POOL_SPECS))
    combined = min(
        1.0,
        0.60 * cross_encoder_score
        + 0.25 * lexical_score
        + 0.10 * float(quote_match)
        + 0.05 * agreement,
    )
    if quote_match:
        grade = 3
    elif combined >= 0.72 and cross_encoder_score >= 0.65:
        grade = 3
    elif combined >= 0.52 or (seed_relevant and combined >= 0.38):
        grade = 2
    elif combined >= 0.30 or (
        retrieval_agreement >= 2
        and lexical_score >= 0.08
        and cross_encoder_score >= 0.20
    ):
        grade = 1
    else:
        grade = 0

    distance = min(abs(combined - boundary) for boundary in (0.30, 0.52, 0.72))
    confidence = (
        0.55
        + min(0.20, distance)
        + 0.10 * float(quote_match)
        + 0.05 * float(seed_relevant)
        + 0.06 * agreement
    )
    return grade, round(min(0.99, max(0.50, confidence)), 4), round(combined, 6)


def score_candidate_pool(
    case: EvaluationCase,
    candidates: Sequence[dict[str, Any]],
    scorer: RelevanceScorer,
) -> list[dict[str, Any]]:
    texts = [
        "\n".join(item for item in (record["title"], record["content"]) if item)
        for record in candidates
    ]
    cross_scores = [float(value) for value in scorer.score(case.question, texts)]
    if len(cross_scores) != len(candidates):
        raise ValueError(
            f"{case.case_id}: Cross-Encoder returned {len(cross_scores)} scores "
            f"for {len(candidates)} candidates"
        )
    expected_quote = _normalise(case.expected_evidence_quote)
    reviewed: list[dict[str, Any]] = []
    for record, raw_score, text in zip(candidates, cross_scores, texts):
        if not math.isfinite(raw_score):
            raise ValueError(f"{case.case_id}: Cross-Encoder returned a non-finite score")
        cross_score = min(1.0, max(0.0, raw_score))
        term_score = lexical_relevance(case.question, text)
        quote_match = bool(expected_quote) and expected_quote in _normalise(text)
        agreement = len(record["retrievals"])
        grade, confidence, combined = _grade(
            cross_encoder_score=cross_score,
            lexical_score=term_score,
            quote_match=quote_match,
            seed_relevant=bool(record["seed_relevant"]),
            retrieval_agreement=agreement,
        )
        groups = sorted(record["retrievals"])
        rationale = (
            f"Cross-Encoder={cross_score:.3f}; 词项证据={term_score:.3f}; "
            f"预期引文命中={'是' if quote_match else '否'}; "
            f"种子Chunk={'是' if record['seed_relevant'] else '否'}; "
            f"检索组={','.join(groups) if groups else '仅种子'}; "
            f"组合分={combined:.3f}。"
        )
        reviewed.append(
            {
                **record,
                "cross_encoder_score": round(cross_score, 6),
                "lexical_score": round(term_score, 6),
                "expected_quote_match": quote_match,
                "retrieval_agreement": agreement,
                "combined_score": combined,
                "ai_grade": grade,
                "ai_confidence": confidence,
                "ai_rationale": rationale,
            }
        )
    return reviewed


def _validate_manifest_lineage(
    manifest: Mapping[str, Any],
    *,
    dataset_path: Path,
    documents_path: Path,
    case_count: int,
) -> None:
    if str(manifest.get("human_review_status", "pending")).casefold() == "complete":
        raise ValueError(
            "AI pre-review refuses to overwrite a completed human review; "
            "regenerate a pending candidate set first"
        )
    expected_dataset = str(manifest.get("dataset_sha256") or "")
    if expected_dataset and expected_dataset != _sha256(dataset_path):
        raise ValueError(
            "evaluation dataset hash does not match its manifest; rerun "
            "scripts.prepare_evaluation_set"
        )
    expected_documents = str(manifest.get("documents_sha256") or "")
    if expected_documents and expected_documents != _sha256(documents_path):
        raise ValueError(
            "documents.jsonl hash does not match the evaluation manifest; rerun "
            "scripts.prepare_evaluation_set after processing"
        )
    recorded_count = manifest.get("case_count")
    if recorded_count is not None and int(recorded_count) != case_count:
        raise ValueError("evaluation case count does not match its manifest")


def _index_metadata(service: Any) -> dict[str, Any]:
    alias = str(getattr(service, "index_name", "") or "")
    backend = getattr(service, "backend", None)
    physical_index = None
    indexed_chunk_count = None
    if backend is not None and alias:
        try:
            physical_index = backend.resolve_alias(alias)
        except Exception:
            physical_index = None
        try:
            indexed_chunk_count = backend.count(alias)
        except Exception:
            indexed_chunk_count = None
    return {
        "index_alias": alias or None,
        "physical_index": physical_index,
        "indexed_chunk_count": indexed_chunk_count,
    }


def run_ai_review(
    *,
    service: Any,
    scorer: RelevanceScorer,
    dataset_path: Path,
    review_path: Path,
    manifest_path: Path,
    documents_path: Path,
    output_root: Path,
    run_id: str,
    top_n: int,
) -> dict[str, Any]:
    """Execute one complete, non-human AI relevance review run."""

    if not RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must contain only letters, digits, dot, dash, or underscore")
    if top_n < 1:
        raise ValueError("top_n must be positive")
    for required in (dataset_path, review_path, manifest_path, documents_path):
        if not required.is_file():
            raise FileNotFoundError(required)

    cases = load_evaluation_cases(dataset_path)
    validate_evaluation_cases(cases)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("evaluation manifest must contain a JSON object")
    _validate_manifest_lineage(
        manifest,
        dataset_path=dataset_path,
        documents_path=documents_path,
        case_count=len(cases),
    )

    fieldnames, review_rows = _read_review(review_path)
    rows_by_case: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in review_rows:
        rows_by_case[str(row.get("case_id") or "")].append(row)
    case_ids = {case.case_id for case in cases}
    if set(rows_by_case) != case_ids:
        raise ValueError(
            "review CSV case IDs do not match the evaluation dataset; rerun "
            "scripts.prepare_evaluation_set"
        )

    run_dir = output_root / run_id
    if run_dir.exists():
        raise FileExistsError(f"AI review run already exists: {run_dir}")
    started = _utc_now()
    reviewed_at = started.isoformat()
    all_audit_rows: list[dict[str, Any]] = []
    updated_cases: list[EvaluationCase] = []
    grade_counts: Counter[int] = Counter()

    for case in cases:
        seed_ids = list(
            dict.fromkeys(
                str(row.get("chunk_id") or "")
                for row in rows_by_case[case.case_id]
                if str(row.get("chunk_id") or "")
            )
        )
        if not seed_ids:
            raise ValueError(f"{case.case_id}: review CSV contains no prepared seed chunk")
        pool = collect_candidate_pool(
            service,
            case,
            seed_chunk_ids=seed_ids,
            top_n=top_n,
        )
        reviewed = score_candidate_pool(case, pool, scorer)
        if scorer.backend != "cross_encoder":
            raise RuntimeError(
                "AI relevance review requires a Cross-Encoder backend; "
                f"actual backend={scorer.backend!r}"
            )
        positives = sorted(
            (record for record in reviewed if int(record["ai_grade"]) > 0),
            key=lambda record: (
                -int(record["ai_grade"]),
                -float(record["combined_score"]),
                str(record["chunk_id"]),
            ),
        )
        if not positives:
            raise ValueError(f"{case.case_id}: AI pre-review produced no relevant chunks")
        relevant_chunk_ids = [str(record["chunk_id"]) for record in positives]
        relevance_grades = {
            str(record["chunk_id"]): float(record["ai_grade"])
            for record in positives
        }
        relevant_document_ids = list(
            dict.fromkeys(str(record["document_id"]) for record in positives)
        )
        updated_cases.append(
            case.model_copy(
                update={
                    "relevant_chunk_ids": relevant_chunk_ids,
                    "relevance_grades": relevance_grades,
                    "relevant_document_ids": relevant_document_ids,
                }
            )
        )

        by_chunk = {str(record["chunk_id"]): record for record in reviewed}
        additional = {
            str(record["chunk_id"]): int(record["ai_grade"])
            for record in positives
            if str(record["chunk_id"]) not in seed_ids
        }
        for index, row in enumerate(rows_by_case[case.case_id]):
            for field_name in HUMAN_REVIEW_FIELDS:
                row[field_name] = ""
            chunk_id = str(row.get("chunk_id") or "")
            candidate = by_chunk.get(chunk_id)
            if candidate is None:
                raise ValueError(
                    f"{case.case_id}: review seed was not included in candidate audit: {chunk_id}"
                )
            row.update(
                {
                    "suggested_grade": str(candidate["ai_grade"]),
                    "ai_grade": str(candidate["ai_grade"]),
                    "ai_confidence": f"{float(candidate['ai_confidence']):.4f}",
                    "ai_question_valid": "true",
                    "ai_quote_valid": str(
                        bool(candidate["expected_quote_match"])
                    ).lower(),
                    "ai_additional_relevance_grades": (
                        json.dumps(additional, ensure_ascii=False, separators=(",", ":"))
                        if index == 0 and additional
                        else ""
                    ),
                    "ai_rationale": str(candidate["ai_rationale"]),
                    "ai_model": scorer.model_name,
                    "ai_reviewed_at": reviewed_at,
                    "ai_review_run_id": run_id,
                }
            )
        for record in reviewed:
            grade_counts[int(record["ai_grade"])] += 1
            all_audit_rows.append(
                {
                    "run_id": run_id,
                    "reviewed_at": reviewed_at,
                    "case_id": case.case_id,
                    "question": case.question,
                    "expected_evidence_quote": case.expected_evidence_quote,
                    "query_filters": case.query_filters,
                    "ai_model": scorer.model_name,
                    "ai_backend": scorer.backend,
                    **record,
                }
            )

    validate_evaluation_cases(updated_cases)
    run_dir.mkdir(parents=True, exist_ok=False)
    candidates_path = run_dir / "candidates.jsonl"
    summary_path = run_dir / "summary.json"
    candidate_text = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
        for row in all_audit_rows
    )
    _atomic_write_text(candidates_path, candidate_text)

    dataset_sha_before = _sha256(dataset_path)
    _atomic_write_cases(dataset_path, updated_cases)
    _atomic_write_review(review_path, fieldnames, review_rows)
    completed = _utc_now()
    manifest.update(
        {
            "ai_review_status": "complete",
            "ai_review_run_id": run_id,
            "ai_reviewed_at": completed.isoformat(),
            "ai_model": scorer.model_name,
            "ai_backend": scorer.backend,
            "ai_scoring_method": (
                "local Cross-Encoder + lexical relevance + expected quote evidence + "
                "A/B/C retrieval agreement"
            ),
            "ai_candidate_pool": {
                "groups": [spec.group for spec in POOL_SPECS],
                "top_n_per_group": top_n,
                "candidate_count": len(all_audit_rows),
            },
            "ai_candidates_path": str(candidates_path.resolve()),
            "ai_candidates_sha256": _sha256(candidates_path),
            "human_review_status": "pending",
            "label_origin": "AI-pre-reviewed candidate labels",
            "dataset_sha256": _sha256(dataset_path),
            "review_sha256": _sha256(review_path),
        }
    )
    _atomic_write_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )

    summary = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat(),
        "case_count": len(updated_cases),
        "candidate_count": len(all_audit_rows),
        "positive_candidate_count": sum(
            count for grade, count in grade_counts.items() if grade > 0
        ),
        "grade_distribution": {
            str(grade): grade_counts.get(grade, 0) for grade in range(4)
        },
        "top_n_per_group": top_n,
        "retrieval_groups": [spec.group for spec in POOL_SPECS],
        "ai_model": scorer.model_name,
        "ai_backend": scorer.backend,
        "human_review_status": "pending",
        "label_origin": "AI-pre-reviewed candidate labels",
        "documents_path": str(documents_path.resolve()),
        "documents_sha256": _sha256(documents_path),
        "dataset_path": str(dataset_path.resolve()),
        "dataset_sha256_before": dataset_sha_before,
        "dataset_sha256_after": _sha256(dataset_path),
        "review_path": str(review_path.resolve()),
        "review_sha256": _sha256(review_path),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": _sha256(manifest_path),
        "candidates_path": str(candidates_path.resolve()),
        "candidates_sha256": _sha256(candidates_path),
        **_index_metadata(service),
        "limitations": [
            "AI relevance labels are candidate labels, not human-reviewed gold labels.",
            "Human review remains pending and must use finalize_evaluation_review separately.",
        ],
    }
    _atomic_write_text(
        summary_path,
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Formal Mini-RAG YAML")
    parser.add_argument("--dataset", help="Candidate evaluation CSV")
    parser.add_argument("--review", help="Evaluation review CSV")
    parser.add_argument("--manifest", help="Evaluation manifest JSON")
    parser.add_argument("--documents", help="Structured documents JSONL")
    parser.add_argument("--output-root", help="AI relevance audit root")
    parser.add_argument("--run-id", help="Stable run directory name")
    parser.add_argument("--top-n", type=int, default=20, help="Pool depth per A/B/C group")
    parser.add_argument(
        "--allow-model-download",
        action="store_true",
        help="Allow Hugging Face downloads when a configured model is absent locally",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.allow_model_download:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        settings = load_settings(PROJECT_ROOT, args.config)
        if settings.reranker.provider.casefold() != "cross_encoder":
            raise ValueError(
                "AI relevance review requires reranker.provider=cross_encoder; "
                "use config/mini_rag.formal.yaml"
            )
        if args.top_n > settings.retrieval.maximum_top_k:
            raise ValueError(
                f"top_n cannot exceed configured maximum_top_k "
                f"{settings.retrieval.maximum_top_k}"
            )
        service = create_service(settings)
        scorer = LocalCrossEncoderScorer(
            CrossEncoderReranker(
                model_name=settings.reranker.model,
                batch_size=settings.reranker.batch_size,
                strict=True,
            )
        )
        run_id = args.run_id or _utc_now().strftime("%Y%m%dT%H%M%S%fZ")
        summary = run_ai_review(
            service=service,
            scorer=scorer,
            dataset_path=(
                Path(args.dataset).resolve() if args.dataset else settings.evaluation_path
            ),
            review_path=(Path(args.review).resolve() if args.review else DEFAULT_REVIEW),
            manifest_path=(
                Path(args.manifest).resolve() if args.manifest else DEFAULT_MANIFEST
            ),
            documents_path=(
                Path(args.documents).resolve() if args.documents else settings.documents_path
            ),
            output_root=(
                Path(args.output_root).resolve()
                if args.output_root
                else DEFAULT_OUTPUT_ROOT
            ),
            run_id=run_id,
            top_n=args.top_n,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
