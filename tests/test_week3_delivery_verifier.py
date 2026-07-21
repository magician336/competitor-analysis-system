from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from agents.compare_agent import CompareAgent
from schemas.benchmark import BenchmarkRun
from schemas.capability_snapshot import CapabilityScore, CapabilitySnapshot
from schemas.document import DimensionTag, EventType, EvidenceLevel, SourceType
from schemas.intelligence_card import (
    AgentExecutionTrace,
    AgentKind,
    AgentTraceEvent,
    CapabilityImpact,
    EvidenceReference,
    ImpactDirection,
    IntelligenceCard,
    StructuredFinding,
)
from scripts import verify_week3_delivery as verifier


COMPETITORS = [
    "Cursor",
    "GitHub Copilot",
    "Trae",
    "通义灵码",
    "CodeGeeX",
]
BRANCHES = [
    ("price", AgentKind.PRICE, EventType.PRICING_CHANGE),
    ("product", AgentKind.PRODUCT, EventType.PRODUCT_RELEASE),
    ("risk", AgentKind.RISK, EventType.RISK_EXPERIENCE),
]


class FakeBenchmarkAgent:
    def __init__(self, results_path: Path, runs=None) -> None:
        self.results_path = results_path
        self._runs = list(runs or [])

    def audit_assets(self):
        return SimpleNamespace(
            status="ready",
            task_count=16,
            task_type_counts={"completion": 2},
            records=[object() for _ in range(16)],
            errors=[],
        )

    def load_runs(self):
        return list(self._runs)


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _card(
    competitor: str,
    branch: str,
    kind: AgentKind,
    event_type: EventType,
) -> IntelligenceCard:
    chunk_id = f"chunk_{competitor.casefold().replace(' ', '_')}_{branch}"
    dimension = {
        "price": DimensionTag.PERFORMANCE_COST,
        "product": DimensionTag.AGENT_CONTEXT,
        "risk": DimensionTag.SECURITY_COMPLIANCE,
    }[branch]
    evidence = EvidenceReference(
        chunk_id=chunk_id,
        document_id=f"doc_{chunk_id}",
        version_id=f"version_{chunk_id}",
        title=f"{competitor} {branch} evidence",
        url=f"https://example.test/{branch}/{chunk_id}",
        competitor=competitor,
        source_type=SourceType.OFFICIAL_CHANGELOG,
        evidence_level=EvidenceLevel.A,
        event_type=event_type,
        dimension_tags=[dimension],
        product_version="2026.07",
        publish_time=datetime(2026, 7, 20, tzinfo=timezone.utc),
        quote="Audited week-three fixture evidence.",
    )
    direction = (
        ImpactDirection.NEGATIVE
        if branch == "risk"
        else ImpactDirection.POSITIVE
    )
    return IntelligenceCard(
        agent_kind=kind,
        competitor=competitor,
        event_type=event_type,
        dimension_tags=[dimension],
        event_title=f"{competitor} {branch} update",
        summary="Evidence-backed fixture summary.",
        change_before="before",
        change_after="after",
        impact_analysis="The cited change affects the product comparison.",
        relevance_to_our_product="high",
        opportunity="Use the finding to refine CodeMate Campus.",
        threat="The competitor may narrow a capability gap.",
        recommended_action="Review the cited source and update the roadmap.",
        confidence_score=0.85,
        priority_score=70,
        evidence=[evidence],
        findings=[
            StructuredFinding(
                title="Audited finding",
                summary="The finding is tied to one allowed chunk.",
                evidence_chunk_ids=[chunk_id],
                confidence_score=0.85,
            )
        ],
        impact_details=[
            CapabilityImpact(
                dimension=dimension,
                direction=direction,
                magnitude=7,
                confidence_score=0.85,
                rationale="The allowed evidence supports this impact.",
                evidence_chunk_ids=[chunk_id],
            )
        ],
        analysis_mode="rules",
        rag_query_id=f"query_{chunk_id}",
        prompt_name=f"{branch}_prompt.md",
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )


def _build_complete_delivery(root: Path) -> tuple[Path, FakeBenchmarkAgent]:
    root.mkdir(parents=True, exist_ok=True)
    inputs = root / "inputs"
    inputs.mkdir()
    input_files = {
        "tasks": inputs / "tasks.jsonl",
        "scoring": inputs / "scoring.yaml",
        "cleaned_data": inputs / "documents.jsonl",
    }
    for name, path in input_files.items():
        path.write_text(f"fixture-{name}\n", encoding="utf-8")

    cards: list[IntelligenceCard] = []
    snapshots: list[CapabilitySnapshot] = []
    workflows: list[dict[str, object]] = []
    traces: list[dict[str, object]] = []
    start_time = datetime(2026, 4, 21, tzinfo=timezone.utc)
    end_time = datetime(2026, 7, 20, tzinfo=timezone.utc)

    for product_index, competitor in enumerate(COMPETITORS, 1):
        product_cards = [
            _card(competitor, branch, kind, event_type)
            for branch, kind, event_type in BRANCHES
        ]
        cards.extend(product_cards)
        source_card = product_cards[1]
        source_chunk = source_card.evidence[0].chunk_id
        snapshot = CapabilitySnapshot(
            competitor=competitor,
            snapshot_date=date(2026, 7, 20),
            product_version="2026.07",
            scoring_version="week3-evidence-v2",
            window_start=start_time,
            window_end=end_time,
            details=[
                CapabilityScore(
                    dimension=dimension,
                    score=70 + product_index,
                    confidence=0.8,
                    evidence_count=1,
                    evidence_chunk_ids=[source_chunk],
                    source_card_ids=[source_card.card_id],
                    evidence_score=float(70 + product_index),
                    rationale="Audited fixture evidence score.",
                )
                for dimension in DimensionTag
            ],
        )
        snapshots.append(snapshot)
        workflow_id = f"workflow_{product_index:024x}"
        workflows.append(
            {
                "competitor": competitor,
                "workflow_id": workflow_id,
                "status": "success",
                "card_count": 3,
                "evidence_count": 3,
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_score": snapshot.total_score,
                "snapshot_coverage": snapshot.coverage_ratio,
                "warnings": [],
                "branches": {branch: "success" for branch, _, _ in BRANCHES},
            }
        )
        for branch, kind, _ in BRANCHES:
            card = next(item for item in product_cards if item.agent_kind == kind)
            trace = AgentExecutionTrace(
                trace_id=f"trace_{product_index}_{branch}",
                agent_kind=kind,
                duration_ms=1.0,
                events=[
                    AgentTraceEvent(
                        stage=f"{branch}_prepare",
                        event="start",
                        at=start_time,
                    ),
                    AgentTraceEvent(
                        stage=f"{branch}_guard_and_structure",
                        event="end",
                        at=end_time,
                    ),
                ],
            )
            traces.append(
                {
                    "workflow_id": workflow_id,
                    "branch": branch,
                    "status": "success",
                    "duration_ms": 1.0,
                    "error": None,
                    "trace": trace.model_dump(mode="json"),
                    "rag_query_id": card.rag_query_id,
                    "card_ids": [card.card_id],
                    "warnings": [],
                }
            )

    briefing_files: list[str] = []
    for index, competitor in enumerate(COMPETITORS, 1):
        relative = f"briefings/product_{index}.md"
        briefing_files.append(relative)
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# {competitor} 竞争态势简报\n\n"
            "## 执行摘要\n\n已完成。\n\n"
            "## 证据索引\n\n- chunk fixture\n\n"
            "## 复核项\n\n- 发布前抽查。\n",
            encoding="utf-8",
        )

    _write_json(
        root / "intelligence_cards.json",
        [item.model_dump(mode="json") for item in cards],
    )
    _write_json(
        root / "capability_snapshots.json",
        [item.model_dump(mode="json") for item in snapshots],
    )
    _write_json(root / "workflow_summary.json", workflows)
    _write_json(root / "trace_summaries.json", traces)

    baseline = CapabilitySnapshot(
        competitor="CodeMate Campus",
        snapshot_date=date(2026, 7, 20),
        product_version="unknown",
        scoring_version="week3-evidence-v2",
        window_start=start_time,
        window_end=end_time,
        details=[],
    )
    matrix = CompareAgent().compare_snapshots(
        [baseline, *snapshots],
        baseline_product="CodeMate Campus",
    )
    comparison_root = root / "comparison"
    _write_json(
        comparison_root / "comparison_matrix.json",
        matrix.model_dump(mode="json"),
    )
    _write_json(
        comparison_root / "codemate_baseline_snapshot.json",
        baseline.model_dump(mode="json"),
    )
    _write_json(
        comparison_root / "comparison_provenance.json",
        {
            "artifact_version": "week3-comparison-v1",
            "generated_at": end_time.isoformat(),
            "network_used": False,
            "comparison_engine": "CompareAgent.compare_snapshots",
            "baseline_product": baseline.competitor,
            "baseline_source": "synthesized_insufficient_evidence",
            "baseline_snapshot_id": baseline.snapshot_id,
            "baseline_coverage_ratio": 0.0,
            "baseline_scored_dimension_count": 0,
            "minimum_rank_coverage": matrix.rules.minimum_rank_coverage,
            "official_ranking_ready": False,
            "scoring_version": matrix.scoring_version,
            "snapshot_date": baseline.snapshot_date.isoformat(),
            "analysis_window": {
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
            },
            "external_products": sorted(COMPETITORS, key=str.casefold),
            "current_snapshot_ids": matrix.current_snapshot_ids,
            "previous_snapshot_ids": {},
            "inputs": {
                "capability_snapshots": {
                    "path": str((root / "capability_snapshots.json").resolve()),
                    "sha256": _sha256(root / "capability_snapshots.json"),
                },
                "baseline_snapshot": None,
                "previous_snapshots": None,
            },
            "files": [
                "comparison_matrix.json",
                "codemate_baseline_snapshot.json",
                "comparison_provenance.json",
            ],
            "warnings": [
                "Synthetic zero-coverage baseline; official ranking is disabled."
            ],
        },
    )

    all_files = [
        "manifest.json",
        "intelligence_cards.json",
        "capability_snapshots.json",
        "workflow_summary.json",
        "trace_summaries.json",
        *briefing_files,
    ]
    manifest = {
        "artifact_version": "week3-baseline-v2",
        "generated_at": end_time.isoformat(),
        "analysis_mode": "rules",
        "requested_analysis_mode": "rules",
        "effective_analysis_mode_counts": {
            branch: {"hybrid": 0, "llm": 0, "rules": 5}
            for branch, _, _ in BRANCHES
        },
        "scoring_version": "week3-evidence-v2",
        "competitors": COMPETITORS,
        "competitor_count": 5,
        "intelligence_card_count": 15,
        "evidence_backed_card_count": 15,
        "degraded_card_count": 0,
        "snapshot_count": 5,
        "trace_count": 15,
        "top_k": 8,
        "analysis_window": {
            "as_of": end_time.isoformat(),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "window_days": 90,
        },
        "benchmark_task_count": 16,
        "benchmark_run_count": 0,
        "input_sha256": {
            name: {"path": str(path.resolve()), "sha256": _sha256(path)}
            for name, path in input_files.items()
        },
        "mini_rag": {
            "index": "fixture-index",
            "physical_index": "fixture-physical-index",
            "indexed_chunks": 15,
            "embedding_model": "fixture-embedding",
            "embedding_dimension": 8,
            "embedding_compatible": True,
        },
        "files": {
            "cards": "intelligence_cards.json",
            "snapshots": "capability_snapshots.json",
            "workflows": "workflow_summary.json",
            "traces": "trace_summaries.json",
            "briefings": "briefings/",
            "briefing_files": briefing_files,
            "all": all_files,
        },
    }
    _write_json(root / "manifest.json", manifest)

    results_path = root / "formal_manual_runs.csv"
    results_path.write_text(
        ",".join(BenchmarkRun.model_fields) + "\n",
        encoding="utf-8",
    )
    return root, FakeBenchmarkAgent(results_path)


def _check(report, check_id: str):
    return next(item for item in report.checks if item.check_id == check_id)


def test_complete_offline_fixture_is_accepted_and_report_is_atomic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_root, benchmark = _build_complete_delivery(tmp_path / "week3")
    output = artifact_root / "acceptance_report.json"
    output.write_text("old report\n", encoding="utf-8")
    monkeypatch.setattr(
        verifier,
        "_check_live_llm",
        lambda: (_ for _ in ()).throw(AssertionError("offline verifier called LLM")),
    )

    report = verifier.verify_delivery(
        artifact_root=artifact_root,
        output_path=output,
        benchmark_agent=benchmark,
    )

    persisted = verifier.AcceptanceReport.model_validate_json(
        output.read_text(encoding="utf-8")
    )
    assert report.status == "accepted"
    assert persisted.status == "accepted"
    assert report.offline is True
    assert report.live_llm_requested is False
    assert report.failed_count == 0
    assert _check(report, "week3_artifacts").details["competitor_count"] == 5
    assert _check(report, "week3_artifacts").details["trace_count"] == 15
    comparison = _check(report, "week3_artifacts").details["comparison"]
    assert comparison["baseline_source"] == "synthesized_insufficient_evidence"
    assert comparison["baseline_coverage_ratio"] == 0.0
    assert comparison["official_ranking_ready"] is False
    assert comparison["external_product_count"] == 5
    assert comparison["matrix_row_count"] == 7
    assert _check(report, "benchmark_assets").details["task_count"] == 16
    assert all(item.check_id != "live_llm" for item in report.checks)
    assert not list(artifact_root.glob(f".{output.name}.*.tmp"))


def test_hash_mismatch_rejects_delivery_and_writes_machine_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_root, benchmark = _build_complete_delivery(tmp_path / "week3")
    manifest = json.loads((artifact_root / "manifest.json").read_text(encoding="utf-8"))
    manifest["input_sha256"]["scoring"]["sha256"] = "sha256:" + "0" * 64
    _write_json(artifact_root / "manifest.json", manifest)
    monkeypatch.setattr(
        verifier,
        "_check_dependencies_and_agents",
        lambda _root: {"offline_test": True},
    )
    output = artifact_root / "acceptance_report.json"

    report = verifier.verify_delivery(
        artifact_root=artifact_root,
        output_path=output,
        benchmark_agent=benchmark,
    )

    assert report.status == "rejected"
    assert report.failed_count == 1
    artifact_check = _check(report, "week3_artifacts")
    assert artifact_check.status == "fail"
    assert "hash mismatch" in artifact_check.message
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted["status"] == "rejected"


def test_formal_sample_run_is_rejected(tmp_path: Path, monkeypatch) -> None:
    artifact_root, _ = _build_complete_delivery(tmp_path / "week3")
    sample = SimpleNamespace(run_id="sample_run_cursor")
    benchmark = FakeBenchmarkAgent(
        artifact_root / "formal_manual_runs.csv",
        runs=[sample],
    )
    manifest = json.loads((artifact_root / "manifest.json").read_text(encoding="utf-8"))
    manifest["benchmark_run_count"] = 1
    _write_json(artifact_root / "manifest.json", manifest)
    monkeypatch.setattr(
        verifier,
        "_check_dependencies_and_agents",
        lambda _root: {"offline_test": True},
    )

    report = verifier.verify_delivery(
        artifact_root=artifact_root,
        output_path=artifact_root / "acceptance_report.json",
        benchmark_agent=benchmark,
    )

    assert report.status == "rejected"
    assert _check(report, "formal_manual_runs").status == "fail"
    assert "sample_run" in _check(report, "formal_manual_runs").message


def test_synthesized_baseline_with_forged_score_is_never_accepted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_root, benchmark = _build_complete_delivery(tmp_path / "week3")
    baseline_path = artifact_root / "comparison" / "codemate_baseline_snapshot.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline["scores"] = {"code_intelligence": 99}
    _write_json(baseline_path, baseline)
    monkeypatch.setattr(
        verifier,
        "_check_dependencies_and_agents",
        lambda _root: {"offline_test": True},
    )

    report = verifier.verify_delivery(
        artifact_root=artifact_root,
        output_path=artifact_root / "acceptance_report.json",
        benchmark_agent=benchmark,
    )

    assert report.status == "rejected"
    artifact_check = _check(report, "week3_artifacts")
    assert artifact_check.status == "fail"
    assert "score-free" in artifact_check.message


def test_comparison_external_snapshot_id_must_match_main_snapshot_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_root, benchmark = _build_complete_delivery(tmp_path / "week3")
    comparison_root = artifact_root / "comparison"
    matrix_path = comparison_root / "comparison_matrix.json"
    provenance_path = comparison_root / "comparison_provenance.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    forged_id = "snap_forged_external_id"
    matrix["current_snapshot_ids"]["Cursor"] = forged_id
    next(
        item for item in matrix["products"] if item["product"] == "Cursor"
    )["snapshot_id"] = forged_id
    provenance["current_snapshot_ids"]["Cursor"] = forged_id
    _write_json(matrix_path, matrix)
    _write_json(provenance_path, provenance)
    monkeypatch.setattr(
        verifier,
        "_check_dependencies_and_agents",
        lambda _root: {"offline_test": True},
    )

    report = verifier.verify_delivery(
        artifact_root=artifact_root,
        output_path=artifact_root / "acceptance_report.json",
        benchmark_agent=benchmark,
    )

    assert report.status == "rejected"
    assert "external snapshot IDs" in _check(
        report,
        "week3_artifacts",
    ).message


def test_comparison_input_snapshot_hash_must_match(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_root, benchmark = _build_complete_delivery(tmp_path / "week3")
    provenance_path = artifact_root / "comparison" / "comparison_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["inputs"]["capability_snapshots"]["sha256"] = (
        "sha256:" + "0" * 64
    )
    _write_json(provenance_path, provenance)
    monkeypatch.setattr(
        verifier,
        "_check_dependencies_and_agents",
        lambda _root: {"offline_test": True},
    )

    report = verifier.verify_delivery(
        artifact_root=artifact_root,
        output_path=artifact_root / "acceptance_report.json",
        benchmark_agent=benchmark,
    )

    assert report.status == "rejected"
    assert "comparison provenance input hash mismatch" in _check(
        report,
        "week3_artifacts",
    ).message


def test_official_ranking_cannot_use_real_but_undercovered_baseline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_root, benchmark = _build_complete_delivery(tmp_path / "week3")
    comparison_root = artifact_root / "comparison"
    baseline_path = comparison_root / "codemate_baseline_snapshot.json"
    explicit_input = artifact_root / "explicit_baseline_input.json"
    explicit_input.write_bytes(baseline_path.read_bytes())
    provenance_path = comparison_root / "comparison_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["baseline_source"] = "explicit"
    provenance["official_ranking_ready"] = True
    provenance["inputs"]["baseline_snapshot"] = {
        "path": str(explicit_input.resolve()),
        "sha256": _sha256(explicit_input),
    }
    _write_json(provenance_path, provenance)
    monkeypatch.setattr(
        verifier,
        "_check_dependencies_and_agents",
        lambda _root: {"offline_test": True},
    )

    report = verifier.verify_delivery(
        artifact_root=artifact_root,
        output_path=artifact_root / "acceptance_report.json",
        benchmark_agent=benchmark,
    )

    assert report.status == "rejected"
    assert "official_ranking_ready contradicts" in _check(
        report,
        "week3_artifacts",
    ).message


def test_live_llm_check_runs_only_when_explicitly_requested(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_root, benchmark = _build_complete_delivery(tmp_path / "week3")
    calls: list[bool] = []
    monkeypatch.setattr(
        verifier,
        "_check_dependencies_and_agents",
        lambda _root: {"offline_test": True},
    )
    monkeypatch.setattr(
        verifier,
        "_check_live_llm",
        lambda: calls.append(True) or {
            "provider": "offline-test-double",
            "structured_output": True,
        },
    )

    report = verifier.verify_delivery(
        artifact_root=artifact_root,
        output_path=artifact_root / "acceptance_report.json",
        live_llm=True,
        benchmark_agent=benchmark,
    )

    assert report.status == "accepted"
    assert report.offline is False
    assert report.live_llm_requested is True
    assert calls == [True]
    assert _check(report, "live_llm").status == "pass"


def test_main_returns_nonzero_for_rejected_report(monkeypatch, capsys) -> None:
    rejected = verifier.AcceptanceReport(
        status="rejected",
        generated_at=datetime.now(timezone.utc),
        offline=True,
        live_llm_requested=False,
        project_root="project",
        artifact_root="artifacts",
        environment={},
        passed_count=0,
        warning_count=0,
        failed_count=1,
        checks=[
            verifier.AcceptanceCheck(
                check_id="fixture",
                status="fail",
                message="fixture failure",
            )
        ],
    )
    monkeypatch.setattr(verifier, "verify_delivery", lambda **_kwargs: rejected)

    code = verifier.main([])

    captured = capsys.readouterr()
    assert code == 1
    assert json.loads(captured.out)["status"] == "rejected"
    assert captured.err == ""
