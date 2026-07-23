from __future__ import annotations

from datetime import datetime, timezone

from agents.compare_agent import CompareAgent
from schemas.capability_snapshot import CapabilitySnapshot


def test_snapshot_never_serializes_an_ambiguous_null_product_version() -> None:
    snapshot = CapabilitySnapshot(competitor="Cursor", product_version=None)

    assert snapshot.product_version == "unknown"
    assert snapshot.model_dump(mode="json")["product_version"] == "unknown"


def test_compare_uses_window_end_as_reproducible_snapshot_date() -> None:
    end = datetime(2026, 7, 20, 23, 59, tzinfo=timezone.utc)

    snapshot = CompareAgent().build_snapshot(
        "Cursor",
        [],
        window_start=datetime(2026, 4, 22, tzinfo=timezone.utc),
        window_end=end,
    )

    assert snapshot.snapshot_date.isoformat() == "2026-07-20"
    assert snapshot.window_start is not None
    assert snapshot.window_end == end
    assert snapshot.product_version == "unknown"
