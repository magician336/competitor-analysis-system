from __future__ import annotations

from types import SimpleNamespace

from scripts.prepare_label_review import stratified_sample


def _document(identifier: str, competitor: str, source: str):
    return SimpleNamespace(
        document_id=identifier,
        competitor=competitor,
        source_type=SimpleNamespace(value=source),
        needs_review=True,
    )


def test_stratified_label_sample_round_robins_competitor_and_source() -> None:
    documents = [
        _document("a1", "A", "issue"),
        _document("a2", "A", "issue"),
        _document("b1", "B", "release"),
        _document("b2", "B", "release"),
    ]

    selected = stratified_sample(documents, 3)

    assert [item.document_id for item in selected] == ["a1", "b1", "a2"]
