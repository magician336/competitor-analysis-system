from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from mini_rag.evaluation import load_evaluation_cases
from mini_rag.models import Evidence, EvaluationCase, RAGResponse
from scripts.ai_review_relevance import _grade, _sha256, run_ai_review
from scripts.prepare_evaluation_set import REVIEW_FIELDS, _write_cases


def _evidence(chunk_id: str, content: str) -> Evidence:
    return Evidence(
        chunk_id=chunk_id,
        document_id=f"doc-{chunk_id}",
        version_id=f"ver-{chunk_id}",
        content=content,
        title=f"Title {chunk_id}",
        url=f"https://example.com/{chunk_id}",
        char_start=0,
        char_end=len(content),
        competitor="Cursor",
        source_type="official_page",
        evidence_level="A",
    )


class _Backend:
    def resolve_alias(self, alias: str) -> str:
        assert alias == "chunks_current"
        return "chunks_v1"

    def count(self, alias: str) -> int:
        assert alias == "chunks_current"
        return 3


class _Service:
    index_name = "chunks_current"
    backend = _Backend()

    def __init__(self) -> None:
        self.seed = _evidence(
            "seed",
            "The agent uses repository context directly before editing.",
        )
        self.extra = _evidence(
            "extra",
            "The agent reads repository files and project context before acting.",
        )
        self.irrelevant = _evidence("irrelevant", "The monthly price is twenty dollars.")

    def query_with_ablation(self, request, spec):
        assert request.top_k == 5
        evidence = {
            "A": [self.extra],
            "B": [self.irrelevant, self.extra],
            "C": [self.extra, self.irrelevant],
        }[spec.group]
        return RAGResponse(query=request.question, evidence=evidence)

    def get_evidence(self, chunk_id: str):
        return self.seed if chunk_id == "seed" else None


class _Scorer:
    model_name = "fixture/cross-encoder"
    backend = "cross_encoder"

    def score(self, _question: str, texts):
        values = []
        for text in texts:
            if "repository context directly" in text:
                values.append(0.95)
            elif "project context" in text:
                values.append(0.86)
            else:
                values.append(0.05)
        return values


def _fixture_files(root: Path, *, human_status: str = "pending"):
    dataset = root / "测试数据集.csv"
    review = root / "评测集人工复核.csv"
    manifest = root / "评测集说明.json"
    documents = root / "documents.jsonl"
    case = EvaluationCase(
        case_id="cursor-agent",
        question="How does the agent use repository context?",
        relevant_document_ids=["doc-seed"],
        relevant_chunk_ids=["seed"],
        relevance_grades={"seed": 3},
        expected_competitor="Cursor",
        expected_evidence_quote="repository context directly",
        query_filters={
            "competitor": "Cursor",
            "source_types": ["official_page"],
            "current_only": True,
        },
    )
    _write_cases(dataset, [case])
    row = {field_name: "" for field_name in REVIEW_FIELDS}
    row.update(
        {
            "case_id": case.case_id,
            "question": case.question,
            "chunk_id": "seed",
            "document_id": "doc-seed",
            "competitor": "Cursor",
            "source_type": "official_page",
            "title": "Title seed",
            "expected_quote": case.expected_evidence_quote,
            "content": "The agent uses repository context directly before editing.",
            "suggested_grade": "3",
            "human_grade": "3",
        }
    )
    with review.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerow(row)
    documents.write_text('{"document_id":"doc-seed"}\n', encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "case_count": 1,
                "dataset_sha256": _sha256(dataset),
                "documents_sha256": _sha256(documents),
                "human_review_status": human_status,
                "ai_review_status": "pending",
            }
        ),
        encoding="utf-8",
    )
    return dataset, review, manifest, documents


def test_grade_uses_quote_as_direct_evidence_and_rejects_weak_candidate() -> None:
    direct = _grade(
        cross_encoder_score=0.4,
        lexical_score=0.1,
        quote_match=True,
        seed_relevant=True,
        retrieval_agreement=1,
    )
    weak = _grade(
        cross_encoder_score=0.05,
        lexical_score=0.0,
        quote_match=False,
        seed_relevant=False,
        retrieval_agreement=1,
    )

    assert direct[0] == 3
    assert weak[0] == 0
    assert 0.5 <= direct[1] <= 0.99


def test_ai_review_pools_candidates_updates_ai_fields_and_keeps_human_pending(
    tmp_path: Path,
) -> None:
    dataset, review, manifest, documents = _fixture_files(tmp_path)
    output_root = tmp_path / "runtime"

    summary = run_ai_review(
        service=_Service(),
        scorer=_Scorer(),
        dataset_path=dataset,
        review_path=review,
        manifest_path=manifest,
        documents_path=documents,
        output_root=output_root,
        run_id="fixture-run",
        top_n=5,
    )

    assert summary["human_review_status"] == "pending"
    assert summary["ai_backend"] == "cross_encoder"
    assert summary["candidate_count"] == 3
    assert summary["grade_distribution"]["0"] == 1
    cases = load_evaluation_cases(dataset)
    assert cases[0].relevant_chunk_ids == ["seed", "extra"]
    assert cases[0].relevance_grades == {"seed": 3.0, "extra": 2.0}

    with review.open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["human_grade"] == ""
    assert row["question_valid"] == ""
    assert row["reviewer"] == ""
    assert row["ai_grade"] == "3"
    assert row["ai_question_valid"] == "true"
    assert row["ai_quote_valid"] == "true"
    assert json.loads(row["ai_additional_relevance_grades"]) == {"extra": 2}

    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    assert metadata["ai_review_status"] == "complete"
    assert metadata["human_review_status"] == "pending"
    assert metadata["label_origin"] == "AI-pre-reviewed candidate labels"
    candidates = [
        json.loads(line)
        for line in (output_root / "fixture-run" / "candidates.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {item["chunk_id"] for item in candidates} == {
        "seed",
        "extra",
        "irrelevant",
    }
    assert all("ai_rationale" in item for item in candidates)
    assert (output_root / "fixture-run" / "summary.json").is_file()


def test_ai_review_refuses_to_overwrite_completed_human_gold(tmp_path: Path) -> None:
    dataset, review, manifest, documents = _fixture_files(
        tmp_path, human_status="complete"
    )

    with pytest.raises(ValueError, match="completed human review"):
        run_ai_review(
            service=_Service(),
            scorer=_Scorer(),
            dataset_path=dataset,
            review_path=review,
            manifest_path=manifest,
            documents_path=documents,
            output_root=tmp_path / "runtime",
            run_id="must-fail",
            top_n=5,
        )

    assert not (tmp_path / "runtime").exists()
