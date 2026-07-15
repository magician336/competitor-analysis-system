from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from processing.versioning import VersionStore
from schemas.document import IndexStatus, StructuredDocument


def _document(
    *,
    content: str,
    crawl_time: str,
    raw_record_id: str,
) -> StructuredDocument:
    return StructuredDocument(
        raw_record_id=raw_record_id,
        raw_path=f"data/raw/cursor/pricing/{raw_record_id}.html",
        competitor="Cursor",
        title="Plans and pricing",
        content=content,
        source_type="pricing",
        evidence_level="A",
        url="https://cursor.com/pricing",
        crawl_time=crawl_time,
        event_type="E1",
        dimension_tags=["D5"],
        label_confidence=0.8,
    )


def _issue_document(
    *,
    content: str,
    crawl_time: str,
    raw_record_id: str,
) -> StructuredDocument:
    return StructuredDocument(
        raw_record_id=raw_record_id,
        raw_path=f"data/raw/copilot/github_issue/{raw_record_id}.json",
        competitor="GitHub Copilot",
        title="Agent stalls in large repository",
        content=content,
        source_type="github_issue",
        evidence_level="C",
        url="https://github.com/example/copilot/issues/42",
        publish_time="2026-07-01T08:00:00Z",
        crawl_time=crawl_time,
        event_type="E3",
        dimension_tags=["D2", "D5"],
        label_confidence=0.8,
    )


def test_repeated_merge_is_idempotent_and_updates_last_seen_metadata(tmp_path) -> None:
    store = VersionStore(tmp_path / "documents.jsonl")
    first = _document(
        content="Pro costs $20 per month.",
        crawl_time="2026-07-01T08:00:00Z",
        raw_record_id="raw_first",
    )
    repeated = _document(
        content="Pro costs $20 per month.",
        crawl_time="2026-07-02T08:00:00Z",
        raw_record_id="raw_second",
    )

    initial = store.merge([first])
    result = store.merge([repeated])

    assert initial.added == 1
    assert result.added == 0
    assert result.unchanged == 1
    assert len(result.documents) == 1
    [saved] = store.load()
    assert saved.version_id == first.version_id
    assert saved.raw_record_id == "raw_second"
    assert saved.crawl_time == datetime(2026, 7, 2, 8, tzinfo=timezone.utc)
    assert saved.source_metadata["raw_record_ids"] == ["raw_first", "raw_second"]
    assert saved.is_current is True


def test_content_change_adds_version_and_closes_previous_interval(tmp_path) -> None:
    store = VersionStore(tmp_path / "documents.jsonl")
    old = _document(
        content="Pro costs $20 per month.",
        crawl_time="2026-07-01T08:00:00Z",
        raw_record_id="raw_old",
    )
    new = _document(
        content="Pro costs $25 per month.",
        crawl_time="2026-07-10T08:00:00Z",
        raw_record_id="raw_new",
    )

    store.merge([old])
    result = store.merge([new])

    assert result.added == 1
    assert result.superseded == 1
    assert len(result.documents) == 2
    previous, current = result.documents
    assert previous.document_id == current.document_id
    assert previous.version_id != current.version_id
    assert previous.is_current is False
    assert previous.index_status is IndexStatus.STALE
    assert previous.valid_to == current.valid_from
    assert current.is_current is True
    assert current.valid_to is None
    assert current.index_status is IndexStatus.PENDING


def test_content_recurrence_creates_a_new_non_contiguous_interval(tmp_path) -> None:
    store = VersionStore(tmp_path / "documents.jsonl")
    first_a = _document(
        content="Pro costs $20 per month.",
        crawl_time="2026-07-01T08:00:00Z",
        raw_record_id="raw_a_first",
    )
    middle_b = _document(
        content="Pro costs $25 per month.",
        crawl_time="2026-07-10T08:00:00Z",
        raw_record_id="raw_b",
    )
    returned_a = _document(
        content="Pro costs $20 per month.",
        crawl_time="2026-07-20T08:00:00Z",
        raw_record_id="raw_a_returned",
    )

    store.merge([first_a])
    store.merge([middle_b])
    result = store.merge([returned_a])

    assert result.added == 1
    assert result.unchanged == 0
    assert result.superseded == 1
    assert len(result.documents) == 3
    initial, middle, current = result.documents
    assert [item.content for item in result.documents] == [
        "Pro costs $20 per month.",
        "Pro costs $25 per month.",
        "Pro costs $20 per month.",
    ]
    assert initial.content_hash == current.content_hash
    assert initial.version_id != current.version_id
    assert initial.valid_to == middle.valid_from
    assert middle.valid_to == current.valid_from
    assert initial.is_current is middle.is_current is False
    assert current.is_current is True
    assert current.valid_to is None
    assert current.source_metadata["base_version_id"] == initial.version_id


def test_repeated_observation_merges_into_current_recurrence(tmp_path) -> None:
    store = VersionStore(tmp_path / "documents.jsonl")
    store.merge(
        [
            _document(
                content="Pro costs $20 per month.",
                crawl_time="2026-07-01T08:00:00Z",
                raw_record_id="raw_a_first",
            ),
            _document(
                content="Pro costs $25 per month.",
                crawl_time="2026-07-10T08:00:00Z",
                raw_record_id="raw_b",
            ),
            _document(
                content="Pro costs $20 per month.",
                crawl_time="2026-07-20T08:00:00Z",
                raw_record_id="raw_a_returned",
            ),
        ]
    )

    result = store.merge(
        [
            _document(
                content="Pro costs $20 per month.",
                crawl_time="2026-07-21T08:00:00Z",
                raw_record_id="raw_a_repeated",
            )
        ]
    )

    assert result.added == 0
    assert result.unchanged == 1
    assert len(result.documents) == 3
    assert result.documents[-1].raw_record_id == "raw_a_repeated"
    assert result.documents[-1].source_metadata["raw_record_ids"] == [
        "raw_a_returned",
        "raw_a_repeated",
    ]
    assert result.documents[-1].is_current is True


def test_rebuild_orders_observations_before_detecting_content_recurrence(tmp_path) -> None:
    store = VersionStore(tmp_path / "documents.jsonl")
    returned_a = _document(
        content="Pro costs $20 per month.",
        crawl_time="2026-07-20T08:00:00Z",
        raw_record_id="raw_a_returned",
    )
    first_a = _document(
        content="Pro costs $20 per month.",
        crawl_time="2026-07-01T08:00:00Z",
        raw_record_id="raw_a_first",
    )
    middle_b = _document(
        content="Pro costs $25 per month.",
        crawl_time="2026-07-10T08:00:00Z",
        raw_record_id="raw_b",
    )

    result = store.merge(
        [returned_a, middle_b, first_a],
        include_existing=False,
    )

    assert result.added == 3
    assert len(result.documents) == 3
    assert [item.content for item in result.documents] == [
        "Pro costs $20 per month.",
        "Pro costs $25 per month.",
        "Pro costs $20 per month.",
    ]
    assert [item.is_current for item in result.documents] == [False, False, True]


def test_mutable_issue_versions_use_first_observation_for_changed_content(
    tmp_path,
) -> None:
    store = VersionStore(tmp_path / "documents.jsonl")
    first = _issue_document(
        content="The agent stalls in large repositories.",
        crawl_time="2026-07-02T08:00:00Z",
        raw_record_id="raw_first",
    )
    repeated = _issue_document(
        content="The agent stalls in large repositories.",
        crawl_time="2026-07-04T08:00:00Z",
        raw_record_id="raw_repeat",
    )
    changed = _issue_document(
        content="The issue is resolved in the latest extension.",
        crawl_time="2026-07-05T08:00:00Z",
        raw_record_id="raw_changed",
    )

    store.merge([first])
    store.merge([repeated])
    result = store.merge([changed])

    previous, current = result.documents
    assert previous.document_id == current.document_id
    assert previous.publish_time == current.publish_time == datetime(
        2026, 7, 1, 8, tzinfo=timezone.utc
    )
    assert previous.valid_from == datetime(2026, 7, 1, 8, tzinfo=timezone.utc)
    assert previous.valid_to == datetime(2026, 7, 5, 8, tzinfo=timezone.utc)
    assert current.valid_from == datetime(2026, 7, 5, 8, tzinfo=timezone.utc)
    assert previous.source_metadata["first_seen_at"].startswith(
        "2026-07-02T08:00:00"
    )
    assert previous.source_metadata["last_seen_at"].startswith(
        "2026-07-04T08:00:00"
    )


def test_merge_without_write_leaves_store_unchanged(tmp_path) -> None:
    output = tmp_path / "documents.jsonl"
    store = VersionStore(output)

    result = store.merge(
        [
            _document(
                content="Pro costs $20 per month.",
                crawl_time="2026-07-01T08:00:00Z",
                raw_record_id="raw_first",
            )
        ],
        write=False,
    )

    assert result.added == 1
    assert not output.exists()


def test_rebuild_ignores_existing_derived_versions(tmp_path) -> None:
    output = tmp_path / "documents.jsonl"
    store = VersionStore(output)
    store.merge(
        [
            _document(
                content="Polluted derived content.",
                crawl_time="2026-07-01T08:00:00Z",
                raw_record_id="raw_first",
            )
        ]
    )

    result = store.merge(
        [
            _document(
                content="Clean replayed content.",
                crawl_time="2026-07-01T08:00:00Z",
                raw_record_id="raw_first",
            )
        ],
        include_existing=False,
    )

    persisted = store.load()
    assert result.added == 1
    assert result.unchanged == 0
    assert len(persisted) == 1
    assert persisted[0].content == "Clean replayed content."


def test_store_writes_one_valid_json_object_per_line(tmp_path) -> None:
    output = tmp_path / "documents.jsonl"
    store = VersionStore(output)
    documents = [
        _document(
            content="Pro costs $20 per month.",
            crawl_time="2026-07-01T08:00:00Z",
            raw_record_id="raw_first",
        ),
        _document(
            content="Pro costs $25 per month.",
            crawl_time="2026-07-10T08:00:00Z",
            raw_record_id="raw_second",
        ),
    ]

    store.merge(documents)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    assert len(rows) == 2
    assert all(row["schema_version"] == "1.0" for row in rows)
    assert {row["is_current"] for row in rows} == {False, True}
    assert not list(tmp_path.glob(".documents.jsonl.*.tmp"))


def test_store_reports_invalid_jsonl_line_number(tmp_path) -> None:
    output = tmp_path / "documents.jsonl"
    output.write_text("{}\nnot-json\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"documents\.jsonl:1"):
        VersionStore(output).load()
