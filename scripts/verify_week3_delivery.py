"""Verify that the complete third-week delivery is safe to hand off.

The default path is offline.  ``--live-llm`` is the only switch that permits a
minimal remote structured-output request; API keys are never written to the
report or terminal output.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents import (
    BenchmarkAgent,
    BriefingAgent,
    CompareAgent,
    DimensionTaggingAgent,
    MultiAgentOrchestrator,
    PriceAgent,
    ProductAgent,
    RiskAgent,
)
from schemas.capability_snapshot import CapabilitySnapshot
from schemas.comparison import CapabilityComparisonMatrix
from schemas.intelligence_card import (
    AgentExecutionTrace,
    AgentKind,
    IntelligenceCard,
)
from schemas.orchestration import (
    BranchError,
    BranchExecutionStatus,
    SpecialistBranch,
    WorkflowExecutionStatus,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMPETITOR_COUNT = 5
PROMPTS = (
    "prompts/price_prompt.md",
    "prompts/product_prompt.md",
    "prompts/risk_prompt.md",
    "prompts/dimension_tagging_prompt.md",
)
CORE_AGENT_FILES = (
    "agents/price_agent.py",
    "agents/product_agent.py",
    "agents/risk_agent.py",
    "agents/dimension_tagging_agent.py",
    "agents/benchmark_agent.py",
    "agents/compare_agent.py",
    "agents/briefing_agent.py",
    "agents/orchestrator.py",
)


class _DigestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class _AnalysisWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    start_time: datetime
    end_time: datetime
    window_days: int = Field(ge=1, le=3_650)

    @model_validator(mode="after")
    def validate_window(self) -> "_AnalysisWindow":
        if self.end_time <= self.start_time:
            raise ValueError("analysis window end_time must be after start_time")
        if self.as_of != self.end_time:
            raise ValueError("analysis window as_of must equal end_time")
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (self.as_of, self.start_time, self.end_time)
        ):
            raise ValueError("analysis window datetimes must be timezone-aware")
        return self


class _ArtifactFiles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cards: str = Field(min_length=1)
    snapshots: str = Field(min_length=1)
    workflows: str = Field(min_length=1)
    traces: str = Field(min_length=1)
    briefings: str = Field(min_length=1)
    briefing_files: list[str] = Field(min_length=1)
    all: list[str] = Field(min_length=1)


class _Week3Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_version: Literal["week3-baseline-v2"]
    generated_at: datetime
    analysis_mode: Literal["rules", "llm", "hybrid"]
    requested_analysis_mode: Literal["rules", "llm", "hybrid"]
    effective_analysis_mode_counts: dict[str, dict[str, int]]
    scoring_version: str = Field(min_length=1)
    competitors: list[str] = Field(min_length=EXPECTED_COMPETITOR_COUNT)
    competitor_count: int = Field(ge=EXPECTED_COMPETITOR_COUNT)
    intelligence_card_count: int = Field(ge=1)
    evidence_backed_card_count: int = Field(ge=0)
    degraded_card_count: int = Field(ge=0)
    snapshot_count: int = Field(ge=1)
    trace_count: int = Field(ge=1)
    top_k: int = Field(ge=1, le=30)
    analysis_window: _AnalysisWindow
    benchmark_task_count: int = Field(ge=1)
    benchmark_run_count: int = Field(ge=0)
    input_sha256: dict[str, _DigestEntry]
    mini_rag: dict[str, Any]
    files: _ArtifactFiles

    @model_validator(mode="after")
    def validate_counts(self) -> "_Week3Manifest":
        if self.analysis_mode != self.requested_analysis_mode:
            raise ValueError("analysis_mode must equal requested_analysis_mode")
        if len(self.competitors) != len({item.casefold() for item in self.competitors}):
            raise ValueError("manifest competitors must be unique")
        if self.competitor_count != len(self.competitors):
            raise ValueError("competitor_count must match competitors")
        if self.evidence_backed_card_count + self.degraded_card_count != (
            self.intelligence_card_count
        ):
            raise ValueError("evidence-backed and degraded card counts must sum to total")
        required_hashes = {"tasks", "scoring", "cleaned_data"}
        if not required_hashes.issubset(self.input_sha256):
            raise ValueError(
                "input_sha256 must include tasks, scoring and cleaned_data"
            )
        return self


class _WorkflowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    competitor: str = Field(min_length=1)
    workflow_id: str = Field(pattern=r"^workflow_[0-9a-f]{24}$")
    status: WorkflowExecutionStatus
    card_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    snapshot_id: str = Field(min_length=1)
    snapshot_score: float = Field(ge=0.0, le=100.0)
    snapshot_coverage: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    branches: dict[SpecialistBranch, BranchExecutionStatus]


class _TraceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(pattern=r"^workflow_[0-9a-f]{24}$")
    branch: SpecialistBranch
    status: BranchExecutionStatus
    duration_ms: float = Field(ge=0.0)
    error: BranchError | None = None
    trace: AgentExecutionTrace | None = None
    rag_query_id: str | None = None
    card_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class _ComparisonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class _ComparisonInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_snapshots: _ComparisonInput
    baseline_snapshot: _ComparisonInput | None = None
    previous_snapshots: _ComparisonInput | None = None


class _ComparisonWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: datetime | None = None
    end_time: datetime | None = None

    @model_validator(mode="after")
    def validate_window(self) -> "_ComparisonWindow":
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("comparison window must provide both start_time and end_time")
        if self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValueError("comparison window end_time must not precede start_time")
        return self


class _ComparisonProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_version: Literal["week3-comparison-v1"]
    generated_at: datetime
    network_used: Literal[False]
    comparison_engine: Literal["CompareAgent.compare_snapshots"]
    baseline_product: str = Field(min_length=1)
    baseline_source: Literal[
        "synthesized_insufficient_evidence",
        "explicit",
        "embedded",
        "embedded_and_explicit",
    ]
    baseline_snapshot_id: str = Field(min_length=1)
    baseline_coverage_ratio: float = Field(ge=0.0, le=1.0)
    baseline_scored_dimension_count: int = Field(ge=0, le=7)
    minimum_rank_coverage: float = Field(gt=0.0, le=1.0)
    official_ranking_ready: bool
    scoring_version: str = Field(min_length=1)
    snapshot_date: date
    analysis_window: _ComparisonWindow
    external_products: list[str] = Field(min_length=1)
    current_snapshot_ids: dict[str, str] = Field(min_length=2)
    previous_snapshot_ids: dict[str, str] = Field(default_factory=dict)
    inputs: _ComparisonInputs
    files: list[str] = Field(min_length=3, max_length=3)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identifiers(self) -> "_ComparisonProvenance":
        if len(self.external_products) != len(
            {item.casefold() for item in self.external_products}
        ):
            raise ValueError("comparison external_products must be unique")
        if len(self.current_snapshot_ids) != len(
            {item.casefold() for item in self.current_snapshot_ids}
        ):
            raise ValueError("comparison current_snapshot_ids keys must be unique")
        if len(set(self.current_snapshot_ids.values())) != len(
            self.current_snapshot_ids
        ):
            raise ValueError("comparison current snapshot IDs must be unique")
        expected_files = {
            "comparison_matrix.json",
            "codemate_baseline_snapshot.json",
            "comparison_provenance.json",
        }
        if set(self.files) != expected_files:
            raise ValueError("comparison provenance files must list the three artifacts")
        return self


class AcceptanceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(min_length=1)
    status: Literal["pass", "fail", "warning"]
    message: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class AcceptanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_version: Literal["week3-acceptance-v1"] = "week3-acceptance-v1"
    status: Literal["accepted", "rejected"]
    generated_at: datetime
    offline: bool
    live_llm_requested: bool
    project_root: str
    artifact_root: str
    environment: dict[str, Any]
    passed_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    checks: list[AcceptanceCheck]


class _ToolProbeInput(BaseModel):
    value: str


class _LiveStructuredProbe(BaseModel):
    ok: bool
    marker: Literal["coderadar-week3"]


class _NoopRAG:
    def query(self, request: Any) -> Any:  # pragma: no cover - construction only.
        raise AssertionError("offline construction probe must not query Mini-RAG")


def _redact(value: str) -> str:
    text = " ".join(str(value).split())
    for name in ("DEEPSEEK_API_KEY", "GITHUB_TOKEN"):
        secret = os.getenv(name, "")
        if secret:
            text = text.replace(secret, "<redacted>")
    return text[:2_000] or "unknown error"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
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
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"required artifact is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON artifact {path}: {exc}") from exc


def _list_models(path: Path, model: type[BaseModel]) -> list[BaseModel]:
    payload = _read_json(path)
    if not isinstance(payload, list):
        raise ValueError(f"artifact must contain a JSON array: {path}")
    return [model.model_validate(item) for item in payload]


def _safe_artifact_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"artifact path escapes artifact root: {relative}") from exc
    return candidate


def _safe_input_path(project_root: Path, artifact_root: Path, value: str) -> Path:
    raw = Path(value)
    candidate = raw.resolve() if raw.is_absolute() else (project_root / raw).resolve()
    roots = (project_root.resolve(), artifact_root.resolve())
    if not any(_is_relative_to(candidate, root) for root in roots):
        raise ValueError(f"manifest input path is outside approved roots: {value}")
    return candidate


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _environment() -> tuple[dict[str, Any], list[str]]:
    conda_name = os.getenv("CONDA_DEFAULT_ENV")
    warnings: list[str] = []
    if sys.version_info[:2] != (3, 11):
        warnings.append(
            f"project targets Python 3.11; current is {sys.version_info.major}.{sys.version_info.minor}"
        )
    if conda_name and conda_name.casefold() != "coderadar":
        warnings.append(f"active Conda environment is {conda_name!r}, not 'CodeRadar'")
    if not conda_name:
        warnings.append("CONDA_DEFAULT_ENV is not set; environment name was not enforced")
    return (
        {
            "python_version": sys.version.split()[0],
            "python_executable": sys.executable,
            "conda_environment": conda_name,
            "conda_prefix": os.getenv("CONDA_PREFIX"),
        },
        warnings,
    )


def _check_dependencies_and_agents(project_root: Path) -> dict[str, Any]:
    from langchain_core.runnables import Runnable, RunnableParallel
    from langchain_core.tools import StructuredTool
    from langchain_deepseek import ChatDeepSeek

    versions = {
        package: importlib.metadata.version(package)
        for package in ("langchain", "langchain-core", "langchain-deepseek", "pydantic")
    }
    tool = StructuredTool.from_function(
        func=lambda value: value,
        name="week3_delivery_probe",
        description="Offline construction-only structured tool probe.",
        args_schema=_ToolProbeInput,
    )
    if not isinstance(tool, StructuredTool):
        raise ValueError("LangChain StructuredTool construction failed")
    model = ChatDeepSeek(
        model="deepseek-chat",
        api_key="offline-construction-only-not-a-secret",
        temperature=0,
        max_retries=0,
    )

    for relative in (*PROMPTS, *CORE_AGENT_FILES):
        path = project_root / relative
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            raise ValueError(f"missing or empty third-week asset: {relative}")

    rag = _NoopRAG()
    specialists = [
        PriceAgent(rag, auto_configure_llm=False),
        ProductAgent(rag, auto_configure_llm=False),
        RiskAgent(rag, auto_configure_llm=False),
    ]
    dimension = DimensionTaggingAgent()
    compare = CompareAgent(project_root / "config" / "scoring.yaml")
    briefing = BriefingAgent()
    orchestrator = MultiAgentOrchestrator(
        rag,
        auto_configure_llm=False,
    )
    runnables = [
        *(agent.pipeline for agent in specialists),
        dimension.pipeline,
        compare.pipeline,
        compare.comparison_pipeline,
        briefing.pipeline,
        orchestrator.pipeline,
    ]
    if not all(isinstance(item, Runnable) for item in runnables):
        raise ValueError("one or more core Agent pipelines are not LangChain Runnables")
    if not isinstance(orchestrator.pipeline, RunnableParallel):
        raise ValueError("MultiAgentOrchestrator is not backed by RunnableParallel")
    return {
        "versions": versions,
        "runnable_count": len(runnables),
        "structured_tool": type(tool).__name__,
        "chat_model": type(model).__name__,
        "chat_model_name": model.model_name,
        "prompt_count": len(PROMPTS),
        "core_agent_file_count": len(CORE_AGENT_FILES),
    }


def _check_benchmarks(agent: BenchmarkAgent) -> dict[str, Any]:
    report = agent.audit_assets()
    if report.status != "ready" or report.errors:
        raise ValueError("benchmark asset audit failed: " + "; ".join(report.errors))
    if report.task_count != 16 or len(report.records) != 16:
        raise ValueError(
            f"benchmark delivery must contain 16 audited tasks, found {report.task_count}"
        )
    return {
        "status": report.status,
        "task_count": report.task_count,
        "record_count": len(report.records),
        "task_type_counts": dict(report.task_type_counts),
    }


def _check_manual_runs(agent: BenchmarkAgent) -> dict[str, Any]:
    if not agent.results_path.is_file():
        raise ValueError(f"formal manual run CSV is missing: {agent.results_path}")
    runs = agent.load_runs()
    sample_ids = [run.run_id for run in runs if "sample_run" in run.run_id.casefold()]
    if sample_ids:
        raise ValueError(
            "formal manual_runs.csv contains sample_run IDs: " + ", ".join(sample_ids)
        )
    # Empty formal results are valid; the header must still be the strict
    # BenchmarkRun contract, which load_runs() validates.
    return {
        "path": str(agent.results_path),
        "run_count": len(runs),
        "sample_run_count": 0,
    }


def _validate_briefing(path: Path) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read briefing {path}: {exc}") from exc
    required = ("# ", "## 执行摘要", "## 证据索引", "## 复核项")
    missing = [marker for marker in required if marker not in content]
    if missing:
        raise ValueError(f"briefing {path.name} is missing sections: {missing}")


def _check_effective_modes(
    manifest: _Week3Manifest,
    cards: list[IntelligenceCard],
) -> None:
    expected: dict[str, dict[str, int]] = {}
    for kind in (AgentKind.PRICE, AgentKind.PRODUCT, AgentKind.RISK):
        counts = Counter(
            card.analysis_mode for card in cards if card.agent_kind == kind
        )
        expected[kind.value] = {
            mode: counts.get(mode, 0) for mode in sorted(("rules", "llm", "hybrid"))
        }
    if manifest.effective_analysis_mode_counts != expected:
        raise ValueError(
            "effective_analysis_mode_counts does not match intelligence cards"
        )


def _check_evidence_links(
    cards: list[IntelligenceCard],
    snapshots: list[CapabilitySnapshot],
    workflows: list[_WorkflowSummary],
    traces: list[_TraceSummary],
) -> None:
    cards_by_id = {card.card_id: card for card in cards}
    if len(cards_by_id) != len(cards):
        raise ValueError("intelligence_cards.json contains duplicate card_id values")
    snapshots_by_id = {snapshot.snapshot_id: snapshot for snapshot in snapshots}
    if len(snapshots_by_id) != len(snapshots):
        raise ValueError("capability_snapshots.json contains duplicate snapshot_id values")

    for snapshot in snapshots:
        unknown_cards = set(snapshot.input_card_ids) - set(cards_by_id)
        if unknown_cards:
            raise ValueError(
                f"snapshot {snapshot.snapshot_id} references unknown cards: "
                + ", ".join(sorted(unknown_cards))
            )
        snapshot_cards = [cards_by_id[card_id] for card_id in snapshot.input_card_ids]
        if any(
            card.competitor.casefold() != snapshot.competitor.casefold()
            for card in snapshot_cards
        ):
            raise ValueError(
                f"snapshot {snapshot.snapshot_id} references another product's card"
            )
        snapshot_evidence_ids = {
            evidence.chunk_id
            for card in snapshot_cards
            for evidence in card.evidence
        }
        for detail in snapshot.details:
            unknown_detail_cards = set(detail.source_card_ids) - set(snapshot.input_card_ids)
            if unknown_detail_cards:
                raise ValueError(
                    f"snapshot detail references cards outside snapshot input: "
                    + ", ".join(sorted(unknown_detail_cards))
                )
            unknown_detail_evidence = (
                set(detail.evidence_chunk_ids) - snapshot_evidence_ids
            )
            if unknown_detail_evidence:
                raise ValueError(
                    f"snapshot detail references unknown evidence chunks: "
                    + ", ".join(sorted(unknown_detail_evidence))
                )
            for contribution in detail.evidence_contributions:
                card = cards_by_id.get(contribution.card_id)
                if card is None:
                    raise ValueError(
                        f"snapshot contribution references unknown card {contribution.card_id}"
                    )
                if contribution.chunk_id not in {
                    evidence.chunk_id for evidence in card.evidence
                }:
                    raise ValueError(
                        "snapshot contribution references a chunk absent from its card: "
                        f"{contribution.chunk_id}"
                    )

    workflows_by_id = {item.workflow_id: item for item in workflows}
    if len(workflows_by_id) != len(workflows):
        raise ValueError("workflow_summary.json contains duplicate workflow_id values")
    for workflow in workflows:
        if workflow.snapshot_id not in snapshots_by_id:
            raise ValueError(
                f"workflow {workflow.workflow_id} references unknown snapshot"
            )
    seen_trace_branches: set[tuple[str, SpecialistBranch]] = set()
    for item in traces:
        workflow = workflows_by_id.get(item.workflow_id)
        if workflow is None:
            raise ValueError(f"trace references unknown workflow {item.workflow_id}")
        key = (item.workflow_id, item.branch)
        if key in seen_trace_branches:
            raise ValueError("trace_summaries.json contains duplicate workflow branch")
        seen_trace_branches.add(key)
        if workflow.branches.get(item.branch) != item.status:
            raise ValueError("trace branch status does not match workflow summary")
        unknown_cards = set(item.card_ids) - set(cards_by_id)
        if unknown_cards:
            raise ValueError("trace references unknown card IDs: " + ", ".join(unknown_cards))
        if item.trace is None or not item.trace.events:
            raise ValueError("every delivered specialist branch requires a non-empty trace")
        expected_kind = AgentKind(item.branch.value)
        if item.trace.agent_kind != expected_kind:
            raise ValueError("trace agent_kind does not match specialist branch")


def _check_comparison_artifacts(
    project_root: Path,
    artifact_root: Path,
    snapshots_path: Path,
    main_snapshots: list[CapabilitySnapshot],
) -> dict[str, Any]:
    comparison_root = _safe_artifact_path(artifact_root, "comparison")
    matrix_path = comparison_root / "comparison_matrix.json"
    baseline_path = comparison_root / "codemate_baseline_snapshot.json"
    provenance_path = comparison_root / "comparison_provenance.json"
    matrix = CapabilityComparisonMatrix.model_validate(_read_json(matrix_path))
    baseline = CapabilitySnapshot.model_validate(_read_json(baseline_path))
    provenance = _ComparisonProvenance.model_validate(_read_json(provenance_path))

    baseline_key = matrix.baseline_product.casefold()
    if baseline.competitor.casefold() != baseline_key:
        raise ValueError("comparison baseline snapshot product does not match matrix")
    if provenance.baseline_product.casefold() != baseline_key:
        raise ValueError("comparison provenance baseline_product does not match matrix")
    if matrix.current_snapshot_ids.get(baseline.competitor) != baseline.snapshot_id:
        # Fall back to case-insensitive lookup before rejecting display-name
        # differences; the ID itself must still match exactly.
        matrix_baseline_id = next(
            (
                snapshot_id
                for product, snapshot_id in matrix.current_snapshot_ids.items()
                if product.casefold() == baseline_key
            ),
            None,
        )
        if matrix_baseline_id != baseline.snapshot_id:
            raise ValueError("matrix baseline snapshot ID does not match baseline artifact")
    if provenance.baseline_snapshot_id != baseline.snapshot_id:
        raise ValueError("comparison provenance baseline_snapshot_id is inconsistent")
    if provenance.current_snapshot_ids != matrix.current_snapshot_ids:
        raise ValueError("comparison provenance current_snapshot_ids do not match matrix")
    if provenance.previous_snapshot_ids != matrix.previous_snapshot_ids:
        raise ValueError("comparison provenance previous_snapshot_ids do not match matrix")
    for summary in matrix.products:
        matrix_id = next(
            (
                snapshot_id
                for product, snapshot_id in matrix.current_snapshot_ids.items()
                if product.casefold() == summary.product.casefold()
            ),
            None,
        )
        if matrix_id != summary.snapshot_id:
            raise ValueError("matrix product summary snapshot ID is inconsistent")

    main_by_product = {
        snapshot.competitor.casefold(): snapshot for snapshot in main_snapshots
    }
    matrix_external = {
        product.casefold(): snapshot_id
        for product, snapshot_id in matrix.current_snapshot_ids.items()
        if product.casefold() != baseline_key
    }
    main_external = {
        key: snapshot.snapshot_id
        for key, snapshot in main_by_product.items()
        if key != baseline_key
    }
    if matrix_external != main_external:
        raise ValueError(
            "comparison external snapshot IDs do not match capability_snapshots.json"
        )
    if {item.casefold() for item in provenance.external_products} != set(
        main_external
    ):
        raise ValueError("comparison provenance external_products do not match snapshots")
    embedded_baseline = main_by_product.get(baseline_key)
    if embedded_baseline and embedded_baseline.snapshot_id != baseline.snapshot_id:
        raise ValueError("embedded baseline ID does not match baseline artifact")

    if not (
        matrix.scoring_version
        == baseline.scoring_version
        == provenance.scoring_version
    ):
        raise ValueError("comparison scoring_version is inconsistent")
    if not (
        matrix.comparison_date
        == baseline.snapshot_date
        == provenance.snapshot_date
    ):
        raise ValueError("comparison snapshot date is inconsistent")
    if (
        provenance.analysis_window.start_time != baseline.window_start
        or provenance.analysis_window.end_time != baseline.window_end
    ):
        raise ValueError("comparison baseline window does not match provenance")
    for key in main_external:
        snapshot = main_by_product[key]
        if (
            snapshot.snapshot_date != baseline.snapshot_date
            or snapshot.window_start != baseline.window_start
            or snapshot.window_end != baseline.window_end
            or snapshot.scoring_version != baseline.scoring_version
        ):
            raise ValueError("comparison external snapshot cohort is not aligned")

    if provenance.minimum_rank_coverage != matrix.rules.minimum_rank_coverage:
        raise ValueError("comparison rank threshold differs between matrix and provenance")
    baseline_summary = next(
        item for item in matrix.products if item.product.casefold() == baseline_key
    )
    scored_dimension_count = sum(
        detail.status.value == "scored" for detail in baseline.details
    )
    if provenance.baseline_coverage_ratio != baseline_summary.coverage_ratio:
        raise ValueError("provenance baseline coverage does not match matrix")
    if baseline.coverage_ratio != baseline_summary.coverage_ratio:
        raise ValueError("baseline snapshot coverage does not match matrix summary")
    if provenance.baseline_scored_dimension_count != scored_dimension_count:
        raise ValueError("provenance baseline scored dimension count is inconsistent")

    input_entries = {
        "capability_snapshots": provenance.inputs.capability_snapshots,
        "baseline_snapshot": provenance.inputs.baseline_snapshot,
        "previous_snapshots": provenance.inputs.previous_snapshots,
    }
    resolved_inputs: dict[str, Path] = {}
    for label, entry in input_entries.items():
        if entry is None:
            continue
        path = _safe_input_path(project_root, artifact_root, entry.path)
        if not path.is_file():
            raise ValueError(f"comparison provenance input is missing ({label}): {path}")
        if _sha256(path) != entry.sha256:
            raise ValueError(f"comparison provenance input hash mismatch: {label}")
        resolved_inputs[label] = path
    if resolved_inputs.get("capability_snapshots") != snapshots_path.resolve():
        raise ValueError(
            "comparison capability_snapshots input must reference the main snapshot artifact"
        )

    baseline_source = provenance.baseline_source
    explicit_sources = {"explicit", "embedded_and_explicit"}
    embedded_sources = {"embedded", "embedded_and_explicit"}
    if baseline_source in embedded_sources and embedded_baseline is None:
        raise ValueError("embedded baseline_source requires baseline in main snapshots")
    if baseline_source == "synthesized_insufficient_evidence" and embedded_baseline:
        raise ValueError("synthesized baseline_source contradicts embedded baseline")
    if baseline_source in explicit_sources:
        explicit_path = resolved_inputs.get("baseline_snapshot")
        if explicit_path is None:
            raise ValueError("explicit comparison baseline is missing input provenance")
        explicit_payload = _read_json(explicit_path)
        if isinstance(explicit_payload, list):
            if len(explicit_payload) != 1:
                raise ValueError("explicit baseline input must contain one snapshot")
            explicit_payload = explicit_payload[0]
        explicit = CapabilitySnapshot.model_validate(explicit_payload)
        if explicit.snapshot_id != baseline.snapshot_id:
            raise ValueError("explicit baseline input does not match baseline artifact")
    elif provenance.inputs.baseline_snapshot is not None:
        raise ValueError("baseline input provenance contradicts baseline_source")

    previous_input = resolved_inputs.get("previous_snapshots")
    if previous_input is None:
        if matrix.previous_snapshot_ids:
            raise ValueError("matrix previous snapshot IDs lack input provenance")
    else:
        previous = [
            item
            for item in _list_models(previous_input, CapabilitySnapshot)
            if isinstance(item, CapabilitySnapshot)
        ]
        previous_ids = {item.competitor: item.snapshot_id for item in previous}
        if previous_ids != matrix.previous_snapshot_ids:
            raise ValueError("previous snapshot input IDs do not match matrix")

    synthesized = baseline_source == "synthesized_insufficient_evidence"
    if synthesized:
        if provenance.official_ranking_ready:
            raise ValueError("synthesized insufficient baseline cannot enable ranking")
        if (
            baseline.coverage_ratio != 0
            or baseline.total_score != 0
            or baseline.overall_confidence != 0
            or baseline.details
            or baseline.scores
            or baseline.confidence
            or baseline.evidence_count
            or baseline.input_card_ids
            or baseline.benchmark_run_ids
            or scored_dimension_count != 0
        ):
            raise ValueError(
                "synthesized baseline must remain zero-coverage and score-free"
            )
        if (
            baseline_summary.rank_eligible
            or baseline_summary.rank is not None
            or baseline_summary.weighted_total_score is not None
            or baseline_summary.gap_to_baseline_total is not None
        ):
            raise ValueError("synthesized baseline matrix summary contains a ranking score")
        if any(
            cell.gap_to_baseline is not None
            for row in matrix.rows
            for cell in row.cells
        ) or any(
            product.gap_to_baseline_total is not None for product in matrix.products
        ):
            raise ValueError(
                "relative-to-baseline gaps must be N/A for a synthesized baseline"
            )
        if matrix.gap_trends:
            raise ValueError("gap trends require a real comparable baseline")
    else:
        expected_official = bool(
            baseline_summary.coverage_ratio >= matrix.rules.minimum_rank_coverage
            and baseline_summary.rank_eligible
        )
        if provenance.official_ranking_ready != expected_official:
            raise ValueError(
                "official_ranking_ready contradicts real baseline coverage and rules"
            )
        if provenance.official_ranking_ready and baseline.coverage_ratio < (
            matrix.rules.minimum_rank_coverage
        ):
            raise ValueError("official ranking requires sufficient real baseline coverage")

    comparison_files = {
        path.name for path in comparison_root.iterdir() if path.is_file()
    }
    if set(provenance.files) != comparison_files:
        raise ValueError("comparison directory files do not match provenance")
    return {
        "artifact_version": provenance.artifact_version,
        "baseline_product": baseline.competitor,
        "baseline_source": baseline_source,
        "baseline_snapshot_id": baseline.snapshot_id,
        "baseline_coverage_ratio": baseline.coverage_ratio,
        "official_ranking_ready": provenance.official_ranking_ready,
        "external_product_count": len(matrix_external),
        "matrix_product_count": len(matrix.products),
        "matrix_row_count": len(matrix.rows),
        "scoring_version": matrix.scoring_version,
    }


def _check_artifacts(
    project_root: Path,
    artifact_root: Path,
    benchmark_agent: BenchmarkAgent,
) -> dict[str, Any]:
    manifest_path = artifact_root / "manifest.json"
    manifest = _Week3Manifest.model_validate(_read_json(manifest_path))
    if manifest.competitor_count != EXPECTED_COMPETITOR_COUNT:
        raise ValueError(
            f"delivery must contain exactly {EXPECTED_COMPETITOR_COUNT} competitors"
        )
    if manifest.benchmark_task_count != 16:
        raise ValueError("manifest benchmark_task_count must be 16")
    formal_run_count = len(benchmark_agent.load_runs())
    if manifest.benchmark_run_count != formal_run_count:
        raise ValueError("manifest benchmark_run_count does not match formal CSV")

    for name, digest_entry in manifest.input_sha256.items():
        source = _safe_input_path(
            project_root,
            artifact_root,
            digest_entry.path,
        )
        if not source.is_file():
            raise ValueError(f"manifest hash input is missing ({name}): {source}")
        actual = _sha256(source)
        if actual != digest_entry.sha256:
            raise ValueError(
                f"manifest input hash mismatch for {name}: expected "
                f"{digest_entry.sha256}, got {actual}"
            )

    cards_path = _safe_artifact_path(artifact_root, manifest.files.cards)
    snapshots_path = _safe_artifact_path(artifact_root, manifest.files.snapshots)
    workflows_path = _safe_artifact_path(artifact_root, manifest.files.workflows)
    traces_path = _safe_artifact_path(artifact_root, manifest.files.traces)
    cards = [
        item for item in _list_models(cards_path, IntelligenceCard) if isinstance(item, IntelligenceCard)
    ]
    snapshots = [
        item for item in _list_models(snapshots_path, CapabilitySnapshot) if isinstance(item, CapabilitySnapshot)
    ]
    workflows = [
        item for item in _list_models(workflows_path, _WorkflowSummary) if isinstance(item, _WorkflowSummary)
    ]
    traces = [
        item for item in _list_models(traces_path, _TraceSummary) if isinstance(item, _TraceSummary)
    ]

    if len(cards) != manifest.intelligence_card_count:
        raise ValueError("manifest intelligence_card_count does not match card artifact")
    if len(snapshots) != manifest.snapshot_count:
        raise ValueError("manifest snapshot_count does not match snapshot artifact")
    if len(workflows) != manifest.competitor_count:
        raise ValueError("workflow count must equal competitor_count")
    non_null_traces = sum(item.trace is not None for item in traces)
    if non_null_traces != manifest.trace_count:
        raise ValueError("manifest trace_count does not match trace artifact")
    if len(traces) != manifest.competitor_count * len(SpecialistBranch):
        raise ValueError("trace artifact must contain three specialist branches per product")

    expected_products = {item.casefold() for item in manifest.competitors}
    cohorts = {
        "cards": {item.competitor.casefold() for item in cards},
        "snapshots": {item.competitor.casefold() for item in snapshots},
        "workflows": {item.competitor.casefold() for item in workflows},
    }
    for label, products in cohorts.items():
        if products != expected_products:
            raise ValueError(f"{label} competitor set does not match manifest")
    if len(snapshots) != len({item.competitor.casefold() for item in snapshots}):
        raise ValueError("delivery requires exactly one snapshot per competitor")

    expected_branches = set(SpecialistBranch)
    for workflow in workflows:
        if workflow.status != WorkflowExecutionStatus.SUCCESS:
            raise ValueError(f"workflow is not successful: {workflow.workflow_id}")
        if set(workflow.branches) != expected_branches or any(
            value != BranchExecutionStatus.SUCCESS
            for value in workflow.branches.values()
        ):
            raise ValueError("every workflow must contain three successful branches")

    # ``unknown`` is an explicit, auditable scope when upstream evidence has
    # no release identifier.  Null/blank/placeholder spellings remain invalid.
    placeholders = {"", "n/a", "none", "null", "unspecified"}
    for snapshot in snapshots:
        if snapshot.window_start is None or snapshot.window_end is None:
            raise ValueError(f"snapshot {snapshot.snapshot_id} is missing analysis window")
        if snapshot.product_version.strip().casefold() in placeholders:
            raise ValueError(f"snapshot {snapshot.snapshot_id} has no product_version")
        if snapshot.scoring_version != manifest.scoring_version:
            raise ValueError("snapshot scoring_version does not match manifest")
        if (
            snapshot.window_start != manifest.analysis_window.start_time
            or snapshot.window_end != manifest.analysis_window.end_time
        ):
            raise ValueError("snapshot analysis window does not match manifest")

    actual_evidence_backed = sum(
        bool(card.evidence) and not card.review_required for card in cards
    )
    if actual_evidence_backed != manifest.evidence_backed_card_count:
        raise ValueError("manifest evidence_backed_card_count does not match cards")
    if len(cards) - actual_evidence_backed != manifest.degraded_card_count:
        raise ValueError("manifest degraded_card_count does not match cards")
    _check_effective_modes(manifest, cards)
    _check_evidence_links(cards, snapshots, workflows, traces)

    briefing_files = set(manifest.files.briefing_files)
    if len(briefing_files) != manifest.competitor_count:
        raise ValueError("briefing file count must equal competitor_count")
    for relative in briefing_files:
        _validate_briefing(_safe_artifact_path(artifact_root, relative))
    briefing_root = _safe_artifact_path(artifact_root, manifest.files.briefings)
    actual_briefings = {
        path.relative_to(artifact_root).as_posix()
        for path in briefing_root.glob("*.md")
        if path.is_file()
    }
    if actual_briefings != briefing_files:
        raise ValueError("briefing directory does not match manifest briefing_files")

    required_outputs = {
        "manifest.json",
        manifest.files.cards,
        manifest.files.snapshots,
        manifest.files.workflows,
        manifest.files.traces,
        *manifest.files.briefing_files,
    }
    if set(manifest.files.all) != required_outputs:
        raise ValueError("manifest files.all does not exactly match delivered outputs")
    for relative in manifest.files.all:
        if not _safe_artifact_path(artifact_root, relative).is_file():
            raise ValueError(f"manifest-listed output is missing: {relative}")

    comparison = _check_comparison_artifacts(
        project_root,
        artifact_root,
        snapshots_path,
        snapshots,
    )

    return {
        "artifact_version": manifest.artifact_version,
        "competitor_count": manifest.competitor_count,
        "card_count": len(cards),
        "snapshot_count": len(snapshots),
        "workflow_count": len(workflows),
        "trace_count": non_null_traces,
        "briefing_count": len(briefing_files),
        "scoring_version": manifest.scoring_version,
        "analysis_window": manifest.analysis_window.model_dump(mode="json"),
        "unknown_product_version_count": sum(
            item.product_version.casefold() == "unknown" for item in snapshots
        ),
        "comparison": comparison,
    }


def _check_live_llm() -> dict[str, Any]:
    from agents.llm import LLMSettings
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_deepseek import ChatDeepSeek

    settings = LLMSettings.from_env()
    if not settings.api_key:
        raise ValueError("--live-llm requires DEEPSEEK_API_KEY")
    model = ChatDeepSeek(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=0,
        timeout=settings.timeout_seconds,
        max_tokens=128,
        max_retries=0,
    )
    chain = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "Return one JSON object matching the schema. This is a minimal "
                "CodeRadar delivery probe.",
            ),
            (
                "human",
                # ChatPromptTemplate treats single braces as variables.
                'Return {{"ok": true, "marker": "coderadar-week3"}}.',
            ),
        ]
    ) | model.with_structured_output(_LiveStructuredProbe, method="json_mode")
    raw = chain.invoke({})
    result = (
        raw
        if isinstance(raw, _LiveStructuredProbe)
        else _LiveStructuredProbe.model_validate(raw)
    )
    if not result.ok:
        raise ValueError("live structured-output probe returned ok=false")
    return {
        "provider": "deepseek",
        "model": settings.model,
        "structured_output": True,
        "marker": result.marker,
    }


def _execute_check(
    check_id: str,
    message: str,
    function: Callable[[], dict[str, Any]],
) -> AcceptanceCheck:
    try:
        details = function()
        return AcceptanceCheck(
            check_id=check_id,
            status="pass",
            message=message,
            details=details,
        )
    except Exception as exc:
        return AcceptanceCheck(
            check_id=check_id,
            status="fail",
            message=_redact(f"{type(exc).__name__}: {exc}"),
        )


def verify_delivery(
    *,
    project_root: Path = PROJECT_ROOT,
    artifact_root: Path | None = None,
    output_path: Path | None = None,
    live_llm: bool = False,
    benchmark_agent: BenchmarkAgent | None = None,
) -> AcceptanceReport:
    project_root = project_root.resolve()
    artifact_root = (artifact_root or project_root / "artifacts" / "week3").resolve()
    output_path = (output_path or artifact_root / "acceptance_report.json").resolve()
    benchmark_agent = benchmark_agent or BenchmarkAgent(
        tasks_path=project_root / "benchmarks" / "tasks" / "tasks.jsonl",
        results_path=project_root / "benchmarks" / "results" / "manual_runs.csv",
    )
    environment, environment_warnings = _environment()
    checks: list[AcceptanceCheck] = [
        AcceptanceCheck(
            check_id="environment",
            status="warning" if environment_warnings else "pass",
            message=(
                "; ".join(environment_warnings)
                if environment_warnings
                else "Python and Conda environment metadata recorded"
            ),
            details=environment,
        ),
        _execute_check(
            "langchain_and_agents",
            "LangChain dependencies and core Agent constructions are ready",
            lambda: _check_dependencies_and_agents(project_root),
        ),
        _execute_check(
            "benchmark_assets",
            "All 16 benchmark task assets passed the formal audit",
            lambda: _check_benchmarks(benchmark_agent),
        ),
        _execute_check(
            "formal_manual_runs",
            "Formal manual run CSV is strict and contains no sample_run IDs",
            lambda: _check_manual_runs(benchmark_agent),
        ),
        _execute_check(
            "week3_artifacts",
            "Manifest, cards, snapshots, workflows, traces and briefings are consistent",
            lambda: _check_artifacts(project_root, artifact_root, benchmark_agent),
        ),
    ]
    if live_llm:
        checks.append(
            _execute_check(
                "live_llm",
                "Explicit live structured-output probe succeeded",
                _check_live_llm,
            )
        )
    failed_count = sum(item.status == "fail" for item in checks)
    report = AcceptanceReport(
        status="rejected" if failed_count else "accepted",
        generated_at=datetime.now(timezone.utc),
        offline=not live_llm,
        live_llm_requested=live_llm,
        project_root=str(project_root),
        artifact_root=str(artifact_root),
        environment=environment,
        passed_count=sum(item.status == "pass" for item in checks),
        warning_count=sum(item.status == "warning" for item in checks),
        failed_count=failed_count,
        checks=checks,
    )
    _atomic_json(output_path, report.model_dump(mode="json"))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--artifacts", type=Path, help="Week-three artifact directory")
    parser.add_argument("--output", type=Path, help="Acceptance report JSON path")
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="Explicitly permit one minimal DeepSeek structured-output call",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = args.project_root.resolve()
    artifact_root = (
        args.artifacts.resolve()
        if args.artifacts
        else project_root / "artifacts" / "week3"
    )
    output_path = (
        args.output.resolve()
        if args.output
        else artifact_root / "acceptance_report.json"
    )
    try:
        report = verify_delivery(
            project_root=project_root,
            artifact_root=artifact_root,
            output_path=output_path,
            live_llm=args.live_llm,
        )
    except Exception as exc:
        print(f"verify_week3_delivery error: {_redact(str(exc))}", file=sys.stderr)
        return 2
    print(report.model_dump_json(indent=2))
    return 0 if report.status == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AcceptanceCheck",
    "AcceptanceReport",
    "build_parser",
    "main",
    "verify_delivery",
]
