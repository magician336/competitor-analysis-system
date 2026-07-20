"""Generate the offline week-three cross-product comparison artifacts.

The command consumes serialized capability snapshots and delegates all matrix
calculation to :class:`agents.compare_agent.CompareAgent`.  When the CodeMate
Campus snapshot is absent, it creates an explicit zero-coverage baseline so a
matrix can still be inspected without inventing a product score or an official
ranking.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agents.compare_agent import CompareAgent
from schemas.capability_snapshot import CapabilitySnapshot, CapabilitySnapshotSet


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOTS = PROJECT_ROOT / "artifacts" / "week3" / "capability_snapshots.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts" / "week3" / "comparison"
DEFAULT_BASELINE_PRODUCT = "CodeMate Campus"


def _project_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _write_json(path: Path, payload: Any) -> None:
    _atomic_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _invalidate_commit_marker(path: Path) -> None:
    if path.is_symlink() or path.is_dir():
        raise RuntimeError(f"refusing unsafe comparison provenance target: {path}")
    path.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"snapshot input does not exist: {path}") from exc
    return "sha256:" + digest.hexdigest()


def _read_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}: {exc}") from exc


def _snapshot_list(path: Path, *, label: str) -> list[CapabilitySnapshot]:
    payload = _read_json(path, label=label)
    if not isinstance(payload, list):
        raise ValueError(f"{label} must contain a JSON array of snapshots")
    try:
        snapshots = CapabilitySnapshotSet.model_validate(
            {"snapshots": payload}
        ).snapshots
    except Exception as exc:
        raise ValueError(f"{label} contains invalid capability snapshots: {exc}") from exc
    return snapshots


def _single_snapshot(path: Path, *, label: str) -> CapabilitySnapshot:
    payload = _read_json(path, label=label)
    if isinstance(payload, list):
        if len(payload) != 1:
            raise ValueError(f"{label} must contain exactly one snapshot")
        payload = payload[0]
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain one JSON snapshot object")
    try:
        return CapabilitySnapshot.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"{label} contains an invalid capability snapshot: {exc}") from exc


def _unique_products(
    snapshots: Iterable[CapabilitySnapshot],
    *,
    label: str,
) -> dict[str, CapabilitySnapshot]:
    by_product: dict[str, CapabilitySnapshot] = {}
    for snapshot in snapshots:
        key = snapshot.competitor.strip().casefold()
        if key in by_product:
            raise ValueError(f"{label} must contain one snapshot per product")
        by_product[key] = snapshot
    return by_product


def _cohort_signature(snapshot: CapabilitySnapshot) -> tuple[Any, ...]:
    return snapshot.snapshot_date, snapshot.window_start, snapshot.window_end


def _require_aligned_cohort(
    snapshots: Iterable[CapabilitySnapshot],
    *,
    label: str,
) -> tuple[Any, ...]:
    materialized = list(snapshots)
    signatures = {_cohort_signature(snapshot) for snapshot in materialized}
    if len(signatures) != 1:
        raise ValueError(
            f"{label} must use one snapshot_date/window_start/window_end cohort"
        )
    return next(iter(signatures))


def _insufficient_baseline(
    *,
    baseline_product: str,
    reference: CapabilitySnapshot,
) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        competitor=baseline_product,
        snapshot_date=reference.snapshot_date,
        product_version="unknown",
        scoring_version=reference.scoring_version,
        window_start=reference.window_start,
        window_end=reference.window_end,
        scores={},
        confidence={},
        evidence_count={},
        details=[],
        total_score=0.0,
        overall_confidence=0.0,
        coverage_ratio=0.0,
        input_card_ids=[],
        benchmark_run_ids=[],
        deltas={},
    )


def _input_provenance(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    return {"path": str(path), "sha256": _sha256_file(path)}


def generate_comparison(
    *,
    snapshots_path: str | Path = DEFAULT_SNAPSHOTS,
    output_dir: str | Path = DEFAULT_OUTPUT,
    baseline_product: str = DEFAULT_BASELINE_PRODUCT,
    baseline_snapshot_path: str | Path | None = None,
    previous_snapshots_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a cohort and atomically write the three comparison artifacts."""

    baseline_product = baseline_product.strip()
    if not baseline_product:
        raise ValueError("baseline_product must not be blank")
    snapshots_file = _project_path(snapshots_path)
    output = _project_path(output_dir)
    baseline_file = (
        _project_path(baseline_snapshot_path)
        if baseline_snapshot_path is not None
        else None
    )
    previous_file = (
        _project_path(previous_snapshots_path)
        if previous_snapshots_path is not None
        else None
    )

    input_snapshots = _snapshot_list(
        snapshots_file,
        label="capability_snapshots",
    )
    if not input_snapshots:
        raise ValueError("capability_snapshots must contain at least one snapshot")
    current_by_product = _unique_products(
        input_snapshots,
        label="capability_snapshots",
    )
    baseline_key = baseline_product.casefold()
    external_snapshots = [
        snapshot
        for key, snapshot in current_by_product.items()
        if key != baseline_key
    ]
    if not external_snapshots:
        raise ValueError("at least one external product snapshot is required")

    scoring_versions = {snapshot.scoring_version for snapshot in input_snapshots}
    if len(scoring_versions) != 1:
        raise ValueError("all current snapshots must use the same scoring_version")
    scoring_version = next(iter(scoring_versions))
    cohort_signature = _require_aligned_cohort(
        external_snapshots,
        label="external snapshots",
    )

    embedded_baseline = current_by_product.get(baseline_key)
    explicit_baseline = (
        _single_snapshot(baseline_file, label="baseline_snapshot")
        if baseline_file is not None
        else None
    )
    if explicit_baseline is not None and explicit_baseline.competitor.casefold() != baseline_key:
        raise ValueError(
            "baseline_snapshot competitor must match baseline_product: "
            f"{baseline_product}"
        )
    if embedded_baseline is not None and explicit_baseline is not None:
        if embedded_baseline.model_dump(mode="json") != explicit_baseline.model_dump(
            mode="json"
        ):
            raise ValueError(
                "conflicting baseline snapshots were supplied in the cohort and "
                "--baseline-snapshot"
            )
        baseline = embedded_baseline
        baseline_source = "embedded_and_explicit"
    elif explicit_baseline is not None:
        baseline = explicit_baseline
        baseline_source = "explicit"
    elif embedded_baseline is not None:
        baseline = embedded_baseline
        baseline_source = "embedded"
    else:
        baseline = _insufficient_baseline(
            baseline_product=baseline_product,
            reference=external_snapshots[0],
        )
        baseline_source = "synthesized_insufficient_evidence"

    if baseline.scoring_version != scoring_version:
        raise ValueError("baseline snapshot must use the cohort scoring_version")
    if _cohort_signature(baseline) != cohort_signature:
        raise ValueError(
            "baseline snapshot must use the cohort snapshot_date and analysis window"
        )

    current = [baseline, *external_snapshots]
    current_ids = [snapshot.snapshot_id for snapshot in current]
    if len(current_ids) != len(set(current_ids)):
        raise ValueError("current cohort snapshot_id values must be unique")
    previous = (
        _snapshot_list(previous_file, label="previous_snapshots")
        if previous_file is not None
        else []
    )
    _unique_products(previous, label="previous_snapshots")

    # This is the sole matrix calculation path. Validation, ranking eligibility,
    # D1--D7 rows and trend rules remain owned by the real CompareAgent.
    matrix = CompareAgent().compare_snapshots(
        current,
        baseline_product=baseline_product,
        previous_snapshots=previous,
    )
    baseline_summary = next(
        item
        for item in matrix.products
        if item.product.casefold() == baseline_key
    )
    synthesized = baseline_source == "synthesized_insufficient_evidence"
    threshold = matrix.rules.minimum_rank_coverage
    official_ranking_ready = bool(
        not synthesized
        and baseline_summary.coverage_ratio >= threshold
        and baseline_summary.rank_eligible
    )
    warnings: list[str] = []
    if synthesized:
        warnings.append(
            f"{baseline_product} snapshot was absent; a zero-coverage, "
            "insufficient-evidence baseline was created without synthetic scores."
        )
        warnings.append(
            "Official cross-product ranking is disabled until the baseline reaches "
            f"minimum_rank_coverage={threshold:.2f}."
        )
    elif not official_ranking_ready:
        warnings.append(
            f"Baseline coverage {baseline_summary.coverage_ratio:.3f} is below "
            f"minimum_rank_coverage={threshold:.3f}; official ranking is disabled."
        )

    output_files = [
        "comparison_matrix.json",
        "codemate_baseline_snapshot.json",
        "comparison_provenance.json",
    ]
    provenance = {
        "artifact_version": "week3-comparison-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "network_used": False,
        "comparison_engine": "CompareAgent.compare_snapshots",
        "baseline_product": baseline.competitor,
        "baseline_source": baseline_source,
        "baseline_snapshot_id": baseline.snapshot_id,
        "baseline_coverage_ratio": baseline_summary.coverage_ratio,
        "baseline_scored_dimension_count": sum(
            item.status.value == "scored" for item in baseline.details
        ),
        "minimum_rank_coverage": threshold,
        "official_ranking_ready": official_ranking_ready,
        "scoring_version": matrix.scoring_version,
        "snapshot_date": baseline.snapshot_date.isoformat(),
        "analysis_window": {
            "start_time": (
                baseline.window_start.isoformat() if baseline.window_start else None
            ),
            "end_time": (
                baseline.window_end.isoformat() if baseline.window_end else None
            ),
        },
        "external_products": sorted(
            (snapshot.competitor for snapshot in external_snapshots),
            key=str.casefold,
        ),
        "current_snapshot_ids": matrix.current_snapshot_ids,
        "previous_snapshot_ids": matrix.previous_snapshot_ids,
        "inputs": {
            "capability_snapshots": _input_provenance(snapshots_file),
            "baseline_snapshot": _input_provenance(baseline_file),
            "previous_snapshots": _input_provenance(previous_file),
        },
        "files": output_files,
        "warnings": warnings,
    }

    provenance_path = output / "comparison_provenance.json"
    # The provenance file certifies the three-file set. Invalidate a previous
    # marker before replacing any member so an interrupted run is detectable.
    _invalidate_commit_marker(provenance_path)
    _write_json(
        output / "comparison_matrix.json",
        matrix.model_dump(mode="json"),
    )
    _write_json(
        output / "codemate_baseline_snapshot.json",
        baseline.model_dump(mode="json"),
    )
    # Provenance is written last and acts as the commit marker for the set.
    _write_json(provenance_path, provenance)
    return provenance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the offline week-three capability comparison artifacts."
    )
    parser.add_argument(
        "--snapshots",
        type=Path,
        default=DEFAULT_SNAPSHOTS,
        help="Current capability_snapshots.json input.",
    )
    parser.add_argument(
        "--baseline-snapshot",
        type=Path,
        default=None,
        help="Optional real baseline snapshot JSON object.",
    )
    parser.add_argument(
        "--previous-snapshots",
        type=Path,
        default=None,
        help="Optional previous snapshot JSON array for trend comparison.",
    )
    parser.add_argument(
        "--baseline-product",
        default=DEFAULT_BASELINE_PRODUCT,
        help="Baseline product name (default: CodeMate Campus).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output directory for the three comparison artifacts.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    provenance = generate_comparison(
        snapshots_path=args.snapshots,
        output_dir=args.output,
        baseline_product=args.baseline_product,
        baseline_snapshot_path=args.baseline_snapshot,
        previous_snapshots_path=args.previous_snapshots,
    )
    print(
        json.dumps(
            {
                "output": str(_project_path(args.output)),
                "baseline_source": provenance["baseline_source"],
                "official_ranking_ready": provenance["official_ranking_ready"],
                "external_product_count": len(provenance["external_products"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
