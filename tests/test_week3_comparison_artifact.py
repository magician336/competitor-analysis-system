from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from schemas.capability_snapshot import CapabilityScore, CapabilitySnapshot
from schemas.document import DimensionTag
from scripts.generate_week3_comparison import generate_comparison, main


SNAPSHOT_DATE = date(2026, 7, 20)
WINDOW_END = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
WINDOW_START = WINDOW_END - timedelta(days=90)


def _snapshot(
    competitor: str,
    *,
    score: int = 70,
    scoring_version: str = "week3-evidence-v2",
    snapshot_date: date = SNAPSHOT_DATE,
    window_start: datetime = WINDOW_START,
    window_end: datetime = WINDOW_END,
    dimensions: list[DimensionTag] | None = None,
) -> CapabilitySnapshot:
    selected = list(DimensionTag) if dimensions is None else dimensions
    details = [
        CapabilityScore(
            dimension=dimension,
            score=score,
            confidence=0.9,
            evidence_count=1,
            evidence_chunk_ids=[f"chunk-{competitor}-{dimension.value}"],
            source_card_ids=[f"card-{competitor}-{dimension.value}"],
            evidence_score=float(score),
            rationale="Frozen benchmark evidence supports this score.",
        )
        for dimension in selected
    ]
    return CapabilitySnapshot(
        competitor=competitor,
        snapshot_date=snapshot_date,
        product_version="2026.07",
        scoring_version=scoring_version,
        window_start=window_start,
        window_end=window_end,
        details=details,
    )


def _write_snapshots(path: Path, snapshots: list[CapabilitySnapshot]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [snapshot.model_dump(mode="json") for snapshot in snapshots],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_snapshot(path: Path, snapshot: CapabilitySnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )


def test_cli_synthesizes_zero_coverage_baseline_and_supports_previous_snapshots(
    tmp_path: Path,
    capsys,
) -> None:
    current_file = tmp_path / "capability_snapshots.json"
    previous_file = tmp_path / "previous_snapshots.json"
    output = tmp_path / "comparison"
    cursor = _snapshot("Cursor", score=78)
    previous_cursor = _snapshot(
        "Cursor",
        score=70,
        snapshot_date=date(2026, 6, 20),
        window_start=WINDOW_START - timedelta(days=30),
        window_end=WINDOW_END - timedelta(days=30),
    )
    _write_snapshots(current_file, [cursor])
    _write_snapshots(previous_file, [previous_cursor])

    exit_code = main(
        [
            "--snapshots",
            str(current_file),
            "--previous-snapshots",
            str(previous_file),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    cli_output = json.loads(capsys.readouterr().out)
    assert cli_output["baseline_source"] == "synthesized_insufficient_evidence"
    assert cli_output["official_ranking_ready"] is False

    matrix = json.loads((output / "comparison_matrix.json").read_text(encoding="utf-8"))
    baseline = json.loads(
        (output / "codemate_baseline_snapshot.json").read_text(encoding="utf-8")
    )
    provenance = json.loads(
        (output / "comparison_provenance.json").read_text(encoding="utf-8")
    )

    assert "artifact_version" not in matrix
    assert matrix["baseline_product"] == "CodeMate Campus"
    assert matrix["previous_snapshot_ids"] == {"Cursor": previous_cursor.snapshot_id}
    assert baseline["competitor"] == "CodeMate Campus"
    assert baseline["product_version"] == "unknown"
    assert baseline["scoring_version"] == cursor.scoring_version
    assert baseline["snapshot_date"] == cursor.snapshot_date.isoformat()
    assert datetime.fromisoformat(baseline["window_start"].replace("Z", "+00:00")) == (
        cursor.window_start
    )
    assert datetime.fromisoformat(baseline["window_end"].replace("Z", "+00:00")) == (
        cursor.window_end
    )
    assert baseline["coverage_ratio"] == 0.0
    assert baseline["total_score"] == 0.0
    assert baseline["scores"] == {}
    assert baseline["details"] == []

    assert provenance["official_ranking_ready"] is False
    assert provenance["baseline_source"] == "synthesized_insufficient_evidence"
    assert provenance["baseline_coverage_ratio"] == 0.0
    assert provenance["baseline_scored_dimension_count"] == 0
    assert len(provenance["warnings"]) == 2
    assert provenance["network_used"] is False
    assert provenance["comparison_engine"] == "CompareAgent.compare_snapshots"
    assert provenance["files"] == [
        "comparison_matrix.json",
        "codemate_baseline_snapshot.json",
        "comparison_provenance.json",
    ]
    assert provenance["inputs"]["capability_snapshots"]["sha256"] == (
        "sha256:" + hashlib.sha256(current_file.read_bytes()).hexdigest()
    )
    assert provenance["inputs"]["previous_snapshots"]["sha256"] == (
        "sha256:" + hashlib.sha256(previous_file.read_bytes()).hexdigest()
    )


def test_explicit_real_baseline_at_threshold_enables_official_ranking(
    tmp_path: Path,
) -> None:
    current_file = tmp_path / "capability_snapshots.json"
    baseline_file = tmp_path / "codemate.json"
    output = tmp_path / "comparison"
    _write_snapshots(current_file, [_snapshot("Cursor", score=80)])
    real_baseline = _snapshot("CodeMate Campus", score=74)
    _write_snapshot(baseline_file, real_baseline)

    provenance = generate_comparison(
        snapshots_path=current_file,
        baseline_snapshot_path=baseline_file,
        output_dir=output,
    )

    assert provenance["baseline_source"] == "explicit"
    assert provenance["official_ranking_ready"] is True
    assert provenance["baseline_coverage_ratio"] == 1.0
    assert provenance["minimum_rank_coverage"] == 0.8
    assert provenance["warnings"] == []
    delivered = json.loads(
        (output / "codemate_baseline_snapshot.json").read_text(encoding="utf-8")
    )
    assert delivered["snapshot_id"] == real_baseline.snapshot_id
    assert provenance["inputs"]["baseline_snapshot"]["sha256"] == (
        "sha256:" + hashlib.sha256(baseline_file.read_bytes()).hexdigest()
    )


def test_embedded_real_baseline_is_used_without_replacement(tmp_path: Path) -> None:
    current_file = tmp_path / "capability_snapshots.json"
    output = tmp_path / "comparison"
    real_baseline = _snapshot("CodeMate Campus", score=72)
    _write_snapshots(current_file, [_snapshot("Cursor", score=80), real_baseline])

    provenance = generate_comparison(
        snapshots_path=current_file,
        output_dir=output,
    )

    assert provenance["baseline_source"] == "embedded"
    assert provenance["baseline_snapshot_id"] == real_baseline.snapshot_id
    assert provenance["official_ranking_ready"] is True


def test_real_baseline_below_rule_threshold_is_not_official(tmp_path: Path) -> None:
    current_file = tmp_path / "capability_snapshots.json"
    baseline_file = tmp_path / "codemate.json"
    output = tmp_path / "comparison"
    _write_snapshots(current_file, [_snapshot("Cursor")])
    partial_baseline = _snapshot(
        "CodeMate Campus",
        dimensions=[DimensionTag.CODE_INTELLIGENCE],
    )
    _write_snapshot(baseline_file, partial_baseline)

    provenance = generate_comparison(
        snapshots_path=current_file,
        baseline_snapshot_path=baseline_file,
        output_dir=output,
    )

    assert provenance["baseline_source"] == "explicit"
    assert provenance["baseline_coverage_ratio"] == 0.2
    assert provenance["official_ranking_ready"] is False
    assert "below minimum_rank_coverage" in provenance["warnings"][0]


@pytest.mark.parametrize(
    "snapshots, expected_message",
    [
        (
            [_snapshot("Cursor"), _snapshot("Trae", scoring_version="other-v1")],
            "same scoring_version",
        ),
        ([_snapshot("CodeMate Campus")], "external product snapshot"),
        (
            [
                _snapshot("Cursor"),
                _snapshot("Trae", snapshot_date=date(2026, 7, 19)),
            ],
            "snapshot_date/window_start/window_end cohort",
        ),
    ],
)
def test_invalid_current_cohorts_fail_before_writing(
    tmp_path: Path,
    snapshots: list[CapabilitySnapshot],
    expected_message: str,
) -> None:
    current_file = tmp_path / "capability_snapshots.json"
    output = tmp_path / "comparison"
    _write_snapshots(current_file, snapshots)

    with pytest.raises(ValueError, match=expected_message):
        generate_comparison(snapshots_path=current_file, output_dir=output)

    assert not output.exists()
