from __future__ import annotations

from schemas.document import StructuredDocument
from mini_rag.ingestion.document_loader import DocumentLoadError, DocumentLoader


def _document(content: str, *, current: bool = True) -> StructuredDocument:
    return StructuredDocument(
        raw_record_id=f"raw_{content}",
        raw_path=f"data/raw/{content}.html",
        competitor="Cursor",
        title="Pricing",
        content=content,
        source_type="pricing",
        evidence_level="A",
        url="https://cursor.com/pricing",
        crawl_time="2026-07-14T00:00:00Z",
        is_current=current,
    )


def test_loader_validates_and_summarizes_jsonl(tmp_path) -> None:
    old = _document("$20", current=False)
    current = _document("$25")
    path = tmp_path / "documents.jsonl"
    path.write_text(
        "\n".join(item.model_dump_json() for item in (old, current)) + "\n",
        encoding="utf-8",
    )

    documents = DocumentLoader(path).load()
    statistics = DocumentLoader.statistics(documents)

    assert statistics.total == 2
    assert statistics.current == 1
    assert statistics.historical == 1
    assert statistics.sources == {"pricing": 2}


def test_loader_rejects_duplicate_version_id(tmp_path) -> None:
    document = _document("same")
    path = tmp_path / "documents.jsonl"
    path.write_text(
        document.model_dump_json() + "\n" + document.model_dump_json() + "\n",
        encoding="utf-8",
    )

    try:
        DocumentLoader(path).load()
    except DocumentLoadError as exc:
        assert "Duplicate version_id" in str(exc)
    else:
        raise AssertionError("duplicate version_id was accepted")

