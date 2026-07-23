"""Deterministic CodeMate Campus product-definition snapshot seed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from backend.config import PROJECT_ROOT
from backend.repositories import AnalysisRepository
from schemas.capability_snapshot import CapabilityScore, CapabilitySnapshot
from schemas.document import DimensionTag


DEFAULT_CODEMATE_CONFIG = PROJECT_ROOT / "config" / "codemate_baseline.yaml"


def load_codemate_snapshot(path: str | Path = DEFAULT_CODEMATE_CONFIG) -> tuple[CapabilitySnapshot, dict[str, Any]]:
    source = Path(path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    dimensions = payload.pop("dimensions")
    source_kind = payload.pop("source_kind")
    provenance = {
        "source_kind": source_kind,
        "planning_only": bool(payload.pop("planning_only")),
        "source_references": list(payload.pop("source_references")),
        "verified_product_capability": False,
        "official_ranking_ready": False,
    }
    details: list[CapabilityScore] = []
    for dimension in DimensionTag:
        item = dimensions[dimension.value]
        supported = "score" in item
        details.append(
            CapabilityScore(
                dimension=dimension,
                score=int(item.get("score", 0)),
                confidence=float(item.get("confidence", 0.0)),
                evidence_count=0,
                evidence_score=(float(item["score"]) if supported else None),
                rationale=str(item["rationale"]),
            )
        )
    snapshot = CapabilitySnapshot.model_validate({**payload, "details": details})
    return snapshot, provenance


def seed_codemate_snapshot(repository: AnalysisRepository) -> CapabilitySnapshot:
    snapshot, provenance = load_codemate_snapshot()
    return repository.save_snapshot(
        snapshot,
        source_kind="product_definition",
        provenance=provenance,
    )


__all__ = ["DEFAULT_CODEMATE_CONFIG", "load_codemate_snapshot", "seed_codemate_snapshot"]
