from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from scripts.audit_evidence_coverage import build_coverage


NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def _document(
    *,
    event_type: str,
    publish_time: datetime | None,
    valid_from: datetime | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        competitor="Cursor",
        event_type=event_type,
        is_current=True,
        publish_time=publish_time,
        valid_from=valid_from,
        evidence_level="A",
        source_type="pricing" if event_type == "pricing_change" else "status_page",
        title="Fixture",
        url="https://example.test/evidence",
    )


def test_coverage_uses_observation_time_only_for_missing_publish_time() -> None:
    documents = [
        _document(
            event_type="pricing_change",
            publish_time=None,
            valid_from=NOW - timedelta(days=2),
        ),
        _document(
            event_type="pricing_change",
            publish_time=NOW - timedelta(days=120),
            valid_from=NOW - timedelta(days=1),
        ),
    ]

    report = build_coverage(
        documents,
        competitors=["Cursor"],
        event_types=["pricing_change", "risk_experience"],
        start_time=NOW - timedelta(days=90),
        end_time=NOW,
    )

    pricing, risk = report["coverage"]
    assert pricing["status"] == "covered"
    assert pricing["in_window_documents"] == 1
    assert pricing["valid_from_fallback_documents"] == 1
    assert risk["status"] == "gap"
    assert report["gap_count"] == 1
