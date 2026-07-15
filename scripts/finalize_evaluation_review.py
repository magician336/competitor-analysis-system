"""Promote a completely human-reviewed candidate set to the canonical gold CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from mini_rag.config import load_settings
from mini_rag.evaluation import load_evaluation_cases, validate_evaluation_cases
from mini_rag.indexing import ElasticsearchClient
from scripts.prepare_evaluation_set import _write_cases


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW = PROJECT_ROOT / "data" / "samples" / "评测集人工复核.csv"
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "samples" / "评测集说明.json"


def _truth(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "是", "通过"}


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _parse_additional(value: str) -> dict[str, float]:
    if not str(value or "").strip():
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("additional_relevance_grades must be a JSON object")
    result = {str(key): float(grade) for key, grade in parsed.items()}
    if any(grade < 1 or grade > 3 for grade in result.values()):
        raise ValueError("additional relevance grades must be between 1 and 3")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", help="Completed human review CSV path")
    parser.add_argument("--dataset", help="Candidate/canonical evaluation CSV path")
    parser.add_argument("--config", help="Mini-RAG YAML path")
    parser.add_argument("--manifest", help="Evaluation manifest JSON path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(PROJECT_ROOT, args.config)
    review_path = Path(args.review).resolve() if args.review else DEFAULT_REVIEW
    dataset_path = Path(args.dataset).resolve() if args.dataset else settings.evaluation_path
    manifest_path = Path(args.manifest).resolve() if args.manifest else DEFAULT_MANIFEST
    cases = load_evaluation_cases(dataset_path)
    by_id = {case.case_id: case for case in cases}
    rows_by_case: dict[str, list[dict[str, str]]] = defaultdict(list)
    with review_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            rows_by_case[str(row.get("case_id") or "")].append(dict(row))
    if set(rows_by_case) != set(by_id):
        missing = sorted(set(by_id) - set(rows_by_case))
        extra = sorted(set(rows_by_case) - set(by_id))
        raise ValueError(f"review case mismatch; missing={missing}; extra={extra}")

    backend = ElasticsearchClient(
        base_url=settings.elasticsearch.url,
        request_timeout=settings.elasticsearch.request_timeout_seconds,
        verify_certs=settings.elasticsearch.verify_certs,
    )
    alias = settings.elasticsearch.read_alias
    reviewers: set[str] = set()
    review_times: list[str] = []
    final_cases = []
    for case_id, case in by_id.items():
        grades: dict[str, float] = {}
        document_ids: list[str] = []
        for row in rows_by_case[case_id]:
            if not _truth(row.get("question_valid")) or not _truth(row.get("quote_valid")):
                raise ValueError(f"{case_id}: question_valid and quote_valid must pass")
            reviewer = str(row.get("reviewer") or "").strip()
            reviewed_at = str(row.get("reviewed_at") or "").strip()
            if not reviewer or not reviewed_at:
                raise ValueError(f"{case_id}: reviewer and reviewed_at are required")
            reviewers.add(reviewer)
            review_times.append(reviewed_at)
            try:
                grade = float(row.get("human_grade") or "")
            except ValueError as exc:
                raise ValueError(f"{case_id}: human_grade must be 0, 1, 2, or 3") from exc
            if grade not in {0.0, 1.0, 2.0, 3.0}:
                raise ValueError(f"{case_id}: human_grade must be 0, 1, 2, or 3")
            if grade > 0:
                grades[str(row["chunk_id"])] = grade
            grades.update(_parse_additional(row.get("additional_relevance_grades", "")))
        if not grades:
            raise ValueError(f"{case_id}: at least one relevant chunk is required")
        for chunk_id in grades:
            chunk = backend.get_document(alias, chunk_id)
            if not chunk:
                raise ValueError(f"{case_id}: reviewed chunk is absent from active index: {chunk_id}")
            document_id = str(chunk.get("document_id") or "")
            if document_id and document_id not in document_ids:
                document_ids.append(document_id)
        final_cases.append(
            case.model_copy(
                update={
                    "relevant_document_ids": document_ids,
                    "relevant_chunk_ids": list(grades),
                    "relevance_grades": grades,
                }
            )
        )
    validate_evaluation_cases(final_cases)
    _write_cases(dataset_path, final_cases)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "human_review_status": "complete",
            "label_origin": "human-reviewed gold labels",
            "reviewers": sorted(reviewers),
            "review_completed_at": max(review_times),
            "dataset_sha256": _sha256(dataset_path),
            "finalized_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
