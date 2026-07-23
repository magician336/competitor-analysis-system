"""Validated, idempotent import of the approved week-three artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import select

from backend.models import ArtifactImportRecord, CompetitorRecord
from backend.repositories import AnalysisRepository, AnalysisStore
from schemas.capability_snapshot import CapabilitySnapshot
from schemas.intelligence_card import AgentExecutionTrace, IntelligenceCard
from schemas.orchestration import (
    BranchExecutionStatus,
    SpecialistBranch,
    WorkflowExecutionStatus,
)


class ArtifactFiles(BaseModel):
    model_config = ConfigDict(extra="allow")

    all: list[str] = Field(min_length=1)
    briefing_files: list[str] = Field(min_length=1)
    cards: str
    snapshots: str
    traces: str
    workflows: str


class ArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    artifact_version: str = Field(min_length=1)
    generated_at: datetime
    competitor_count: int = Field(ge=0)
    intelligence_card_count: int = Field(ge=0)
    snapshot_count: int = Field(ge=0)
    trace_count: int = Field(ge=0)
    competitors: list[str]
    files: ArtifactFiles


class WorkflowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(pattern=r"^workflow_[0-9a-f]{24}$")
    competitor: str = Field(min_length=1)
    status: WorkflowExecutionStatus
    branches: dict[SpecialistBranch, BranchExecutionStatus]
    card_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    snapshot_coverage: float = Field(ge=0.0, le=1.0)
    snapshot_id: str = Field(min_length=1)
    snapshot_score: float = Field(ge=0.0, le=100.0)
    warnings: list[str] = Field(default_factory=list)


class TraceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(pattern=r"^workflow_[0-9a-f]{24}$")
    branch: SpecialistBranch
    status: BranchExecutionStatus
    duration_ms: float = Field(ge=0.0)
    error: dict[str, Any] | None = None
    rag_query_id: str | None = None
    card_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trace: AgentExecutionTrace


class ArtifactBundle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_root: Path
    source_sha256: str
    manifest: ArtifactManifest
    manifest_payload: dict[str, Any]
    cards: list[IntelligenceCard]
    snapshots: list[CapabilitySnapshot]
    workflows: list[WorkflowSummary]
    traces: list[TraceSummary]
    briefings: dict[str, str]


class InitializationResult(BaseModel):
    seeded_competitors: int = 0
    artifact_status: Literal["not_requested", "imported", "already_imported"]
    counts: dict[str, int] = Field(default_factory=dict)
    source_sha256: str | None = None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON artifact: {path}") from exc


def _safe_artifact_path(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    lowered_parts = {part.casefold() for part in relative.parts}
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"artifact path escapes source directory: {relative_path}")
    if "comparison" in lowered_parts or any(
        "benchmark" in part or "codemate" in part for part in lowered_parts
    ):
        raise ValueError(f"excluded artifact appeared in manifest: {relative_path}")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"artifact path escapes source directory: {relative_path}"
        ) from exc
    if not path.is_file():
        raise ValueError(f"manifest artifact does not exist: {relative_path}")
    return path


def _bundle_digest(root: Path, paths: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        path = _safe_artifact_path(root, relative)
        digest.update(relative.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_artifact_bundle(source_root: str | Path) -> ArtifactBundle:
    root = Path(source_root).expanduser().resolve()
    manifest_path = root / "manifest.json"
    manifest_payload = _read_json(manifest_path)
    manifest = ArtifactManifest.model_validate(manifest_payload)

    declared = set(manifest.files.all)
    required = {
        "manifest.json",
        manifest.files.cards,
        manifest.files.snapshots,
        manifest.files.workflows,
        manifest.files.traces,
        *manifest.files.briefing_files,
    }
    if declared != required:
        raise ValueError("manifest files.all must exactly match the importable artifacts")

    cards = TypeAdapter(list[IntelligenceCard]).validate_python(
        _read_json(_safe_artifact_path(root, manifest.files.cards))
    )
    snapshots = TypeAdapter(list[CapabilitySnapshot]).validate_python(
        _read_json(_safe_artifact_path(root, manifest.files.snapshots))
    )
    workflows = TypeAdapter(list[WorkflowSummary]).validate_python(
        _read_json(_safe_artifact_path(root, manifest.files.workflows))
    )
    traces = TypeAdapter(list[TraceSummary]).validate_python(
        _read_json(_safe_artifact_path(root, manifest.files.traces))
    )
    briefings = {
        Path(relative).stem: _safe_artifact_path(root, relative).read_text(
            encoding="utf-8-sig"
        )
        for relative in manifest.files.briefing_files
    }

    actual_counts = {
        "competitors": len(manifest.competitors),
        "cards": len(cards),
        "snapshots": len(snapshots),
        "workflows": len(workflows),
        "traces": len(traces),
        "briefings": len(briefings),
    }
    expected_counts = {
        "competitors": manifest.competitor_count,
        "cards": manifest.intelligence_card_count,
        "snapshots": manifest.snapshot_count,
        "workflows": manifest.competitor_count,
        "traces": manifest.trace_count,
        "briefings": manifest.competitor_count,
    }
    if actual_counts != expected_counts:
        raise ValueError(
            f"artifact counts do not match manifest: {actual_counts} != {expected_counts}"
        )

    return ArtifactBundle(
        source_root=root,
        source_sha256=_bundle_digest(root, manifest.files.all),
        manifest=manifest,
        manifest_payload=manifest_payload,
        cards=cards,
        snapshots=snapshots,
        workflows=workflows,
        traces=traces,
        briefings=briefings,
    )


def load_competitor_seed(config_path: str | Path) -> tuple[int, list[dict[str, Any]]]:
    path = Path(config_path).expanduser().resolve()
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read competitor seed: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("competitor seed must be a mapping")
    version = payload.get("version")
    competitors = payload.get("competitors")
    if not isinstance(version, int) or version < 1:
        raise ValueError("competitor seed version must be a positive integer")
    if not isinstance(competitors, list) or not competitors:
        raise ValueError("competitor seed must contain competitors")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in competitors:
        if not isinstance(raw, Mapping):
            raise ValueError("each competitor seed entry must be a mapping")
        item = dict(raw)
        competitor_id = str(item.get("id", "")).strip()
        name = str(item.get("name", "")).strip()
        if not competitor_id or not name or competitor_id in seen:
            raise ValueError("competitor seed IDs and names must be unique and non-blank")
        seen.add(competitor_id)
        normalized.append(item)
    return version, normalized


def initialize_database(
    repository: AnalysisRepository,
    *,
    competitor_config: str | Path | None = None,
    artifact_root: str | Path | None = None,
) -> InitializationResult:
    """Seed/import in one transaction after all requested inputs validate."""

    seed = load_competitor_seed(competitor_config) if competitor_config else None
    bundle = load_artifact_bundle(artifact_root) if artifact_root else None
    counts: dict[str, int] = {}
    artifact_status: Literal[
        "not_requested", "imported", "already_imported"
    ] = "not_requested"

    with repository.transaction() as store:
        if seed is not None:
            version, competitors = seed
            counts["competitors"] = store.upsert_competitors(
                competitors,
                config_version=version,
            )

        if bundle is not None:
            existing = store.session.scalar(
                select(ArtifactImportRecord.import_id).where(
                    ArtifactImportRecord.source_sha256 == bundle.source_sha256
                )
            )
            if existing is not None:
                artifact_status = "already_imported"
            else:
                artifact_status = "imported"
                store.upsert_cards(bundle.cards)
                for snapshot in bundle.snapshots:
                    store.upsert_snapshot(snapshot)
                for workflow in bundle.workflows:
                    store.upsert_workflow_summary(workflow.model_dump(mode="json"))
                # The session disables autoflush deliberately; trace summaries
                # resolve their branch through a database lookup.
                store.session.flush()
                for trace in bundle.traces:
                    store.upsert_trace_summary(trace.model_dump(mode="json"))
                _import_briefings(store, bundle)
                counts.update(
                    {
                        "cards": len(bundle.cards),
                        "snapshots": len(bundle.snapshots),
                        "workflows": len(bundle.workflows),
                        "traces": len(bundle.traces),
                        "briefings": len(bundle.briefings),
                    }
                )
                store.add_artifact_import(
                    source_root=str(bundle.source_root),
                    source_sha256=bundle.source_sha256,
                    artifact_version=bundle.manifest.artifact_version,
                    counts=counts,
                    manifest=bundle.manifest_payload,
                )

    return InitializationResult(
        seeded_competitors=counts.get("competitors", 0),
        artifact_status=artifact_status,
        counts=counts,
        source_sha256=bundle.source_sha256 if bundle is not None else None,
    )


def _import_briefings(store: AnalysisStore, bundle: ArtifactBundle) -> None:
    competitors = list(store.session.scalars(select(CompetitorRecord)))
    by_id = {item.competitor_id: item.name for item in competitors}
    snapshots = {item.competitor: item for item in bundle.snapshots}
    workflows = {item.competitor: item for item in bundle.workflows}
    for competitor_id, markdown in bundle.briefings.items():
        competitor = by_id.get(competitor_id)
        if competitor is None:
            raise ValueError(
                f"briefing filename references unseeded competitor: {competitor_id}"
            )
        snapshot = snapshots.get(competitor)
        workflow = workflows.get(competitor)
        if snapshot is None or workflow is None:
            raise ValueError(f"briefing has no matching snapshot/workflow: {competitor}")
        store.upsert_briefing(
            competitor=competitor,
            markdown=markdown,
            snapshot_id=snapshot.snapshot_id,
            workflow_id=workflow.workflow_id,
            created_at=bundle.manifest.generated_at,
            payload={
                "artifact_version": bundle.manifest.artifact_version,
                "source_file": f"briefings/{competitor_id}.md",
            },
        )


__all__ = [
    "ArtifactBundle",
    "ArtifactManifest",
    "InitializationResult",
    "TraceSummary",
    "WorkflowSummary",
    "initialize_database",
    "load_artifact_bundle",
    "load_competitor_seed",
]
