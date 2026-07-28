from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.generate_week3_baseline as baseline
from schemas.orchestration import (
    BranchExecutionStatus,
    SpecialistBranch,
    WorkflowExecutionStatus,
)


class _Dumpable:
    def model_dump(self, *, mode: str):
        assert mode == "json"
        return dict(self.payload)


class _Card(_Dumpable):
    def __init__(
        self,
        card_id: str,
        *,
        analysis_mode: str,
        evidence_backed: bool,
    ) -> None:
        self.card_id = card_id
        self.analysis_mode = analysis_mode
        self.evidence = [SimpleNamespace(chunk_id=f"chunk-{card_id}")] if evidence_backed else []
        self.review_required = not evidence_backed
        self.payload = {
            "card_id": card_id,
            "analysis_mode": analysis_mode,
            "evidence": ([{"chunk_id": f"chunk-{card_id}"}] if evidence_backed else []),
            "review_required": self.review_required,
        }


class _Snapshot(_Dumpable):
    snapshot_id = "snapshot-fixture"
    competitor = "Cursor"
    scoring_version = "week3-evidence-v2"
    total_score = 64.5
    coverage_ratio = 0.5
    payload = {
        "snapshot_id": snapshot_id,
        "competitor": competitor,
        "scoring_version": scoring_version,
    }


class _BenchmarkAgent:
    def load_tasks(self):
        return [SimpleNamespace(task_id="bench_001"), SimpleNamespace(task_id="bench_002")]

    def load_runs(self):
        return [SimpleNamespace(run_id="run-001")]


class _Workflow:
    def __init__(self) -> None:
        self.benchmark_agent = _BenchmarkAgent()
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        backed = _Card("card-backed", analysis_mode="hybrid", evidence_backed=True)
        degraded = _Card("card-degraded", analysis_mode="rules", evidence_backed=False)
        trace = SimpleNamespace(
            trace_id="trace-price",
            agent_kind=SimpleNamespace(value="price"),
            duration_ms=12.5,
            events=[
                SimpleNamespace(
                    stage="rag_retrieval",
                    event="end",
                    at=datetime(2026, 7, 20, 12, tzinfo=timezone.utc),
                    detail="prompt=must never be persisted api_key=supersecret",
                )
            ],
            llm_used=True,
            fallback_used=False,
            model_name="fixture-model",
        )
        price_result = SimpleNamespace(
            trace=trace,
            rag_query_id="query-price",
            cards=[backed],
            warnings=["provider token=supersecret"],
        )
        product_result = SimpleNamespace(
            trace=None,
            rag_query_id="query-product",
            cards=[degraded],
            warnings=["no evidence returned"],
        )
        error = SimpleNamespace(
            error_type="RuntimeError",
            message="api_key=supersecret prompt=full private prompt",
            stage="analysis",
            retryable=False,
        )
        outcomes = {
            SpecialistBranch.PRICE: SimpleNamespace(
                status=BranchExecutionStatus.SUCCESS,
                duration_ms=20.0,
                result=price_result,
                error=None,
            ),
            SpecialistBranch.PRODUCT: SimpleNamespace(
                status=BranchExecutionStatus.SUCCESS,
                duration_ms=10.0,
                result=product_result,
                error=None,
            ),
            SpecialistBranch.RISK: SimpleNamespace(
                status=BranchExecutionStatus.FAILED,
                duration_ms=5.0,
                result=None,
                error=error,
            ),
        }
        return SimpleNamespace(
            workflow_id="workflow_fixture",
            request=request,
            status=WorkflowExecutionStatus.PARTIAL_FAILURE,
            branch_outcomes=outcomes,
            cards=[backed, degraded],
            snapshot=_Snapshot(),
            briefing="# Cursor briefing\n\nFixture output.",
            warnings=["risk failed with api_key=supersecret"],
        )


class _RagService:
    def health(self):
        return {
            "status": "ready",
            "index": "fixture-index",
            "physical_index": "fixture-index-v1",
            "indexed_chunks": 2,
            "embedding_model": "fixture-embedding",
            "embedding_dimension": 3,
            "embedding_compatible": True,
        }


def _configure_inputs(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    paths = {
        "tasks": tmp_path / "inputs" / "tasks.jsonl",
        "scoring": tmp_path / "inputs" / "scoring.yaml",
        "cleaned_data": tmp_path / "inputs" / "documents.jsonl",
    }
    payloads = {
        "tasks": b'{"task_id":"bench_fixture"}\n',
        "scoring": b"version: fixture\n",
        "cleaned_data": b'{"document_id":"doc_fixture"}\n',
    }
    for name, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payloads[name])
    monkeypatch.setattr(baseline, "TASKS_INPUT", paths["tasks"])
    monkeypatch.setattr(baseline, "SCORING_INPUT", paths["scoring"])
    monkeypatch.setattr(baseline, "CLEANED_DATA_INPUT", paths["cleaned_data"])
    return paths


def test_cli_parses_utc_timestamp_date_and_default_window() -> None:
    parser = baseline.build_parser()

    dated = parser.parse_args(["--as-of", "2026-07-20"])
    assert dated.as_of == datetime.combine(
        datetime(2026, 7, 20).date(), time.max, tzinfo=timezone.utc
    )
    assert dated.window_days == 90

    timestamped = parser.parse_args(
        ["--as-of", "2026-07-20T12:30:45Z", "--window-days", "30"]
    )
    assert timestamped.as_of == datetime(
        2026, 7, 20, 12, 30, 45, tzinfo=timezone.utc
    )
    assert timestamped.window_days == 30

    with pytest.raises(SystemExit):
        parser.parse_args(["--as-of", "2026-07-20T12:30:45+08:00"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--window-days", "0"])


def test_manifest_v2_trace_stats_hashes_and_controlled_briefing_cleanup(
    monkeypatch,
    tmp_path: Path,
) -> None:
    input_paths = _configure_inputs(monkeypatch, tmp_path)
    workflow = _Workflow()
    monkeypatch.setattr(baseline, "create_service", lambda: _RagService())
    monkeypatch.setattr(
        baseline,
        "MultiAgentOrchestrator",
        lambda *_args, **_kwargs: workflow,
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "supersecret")

    output = tmp_path / "delivery"
    briefing_dir = output / "briefings"
    briefing_dir.mkdir(parents=True)
    (briefing_dir / "cursor.md").write_text("stale cursor", encoding="utf-8")
    (briefing_dir / "github_copilot.md").write_text(
        "must remain", encoding="utf-8"
    )
    (briefing_dir / "notes.md").write_text("must also remain", encoding="utf-8")

    as_of = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    manifest = baseline.generate_baseline(
        competitors=["Cursor"],
        output_dir=output,
        mode="hybrid",
        top_k=5,
        allow_partial=True,
        as_of=as_of,
        window_days=30,
    )

    assert len(workflow.requests) == 1
    request = workflow.requests[0]
    assert request.start_time == as_of - timedelta(days=30)
    assert request.end_time == as_of
    assert request.top_k == 5

    assert manifest["artifact_version"] == "week3-baseline-v2"
    assert manifest["requested_analysis_mode"] == "hybrid"
    assert manifest["effective_analysis_mode_counts"] == {
        "price": {"hybrid": 1, "llm": 0, "rules": 0},
        "product": {"hybrid": 0, "llm": 0, "rules": 1},
        "risk": {"hybrid": 0, "llm": 0, "rules": 0},
    }
    assert manifest["evidence_backed_card_count"] == 1
    assert manifest["degraded_card_count"] == 1
    assert manifest["trace_count"] == 1
    assert manifest["top_k"] == 5
    assert manifest["analysis_window"] == {
        "as_of": as_of.isoformat(),
        "start_time": (as_of - timedelta(days=30)).isoformat(),
        "end_time": as_of.isoformat(),
        "window_days": 30,
    }

    for name, path in input_paths.items():
        expected = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert manifest["input_sha256"][name]["sha256"] == expected

    listed = manifest["files"]["all"]
    assert listed == [
        "manifest.json",
        "intelligence_cards.json",
        "capability_snapshots.json",
        "workflow_summary.json",
        "trace_summaries.json",
        "briefings/cursor.md",
    ]
    assert all((output / relative).is_file() for relative in listed)
    assert json.loads((output / "manifest.json").read_text(encoding="utf-8")) == manifest

    traces = json.loads((output / "trace_summaries.json").read_text(encoding="utf-8"))
    assert len(traces) == 3
    assert all(
        set(item)
        == {
            "workflow_id",
            "branch",
            "status",
            "duration_ms",
            "error",
            "trace",
            "rag_query_id",
            "card_ids",
            "warnings",
        }
        for item in traces
    )
    trace_text = json.dumps(traces, ensure_ascii=False).casefold()
    assert "supersecret" not in trace_text
    assert "full private prompt" not in trace_text
    price_trace = next(item for item in traces if item["branch"] == "price")["trace"]
    assert price_trace is not None
    assert price_trace["events"] == [
        {
            "stage": "rag_retrieval",
            "event": "end",
            "at": "2026-07-20T12:00:00+00:00",
        }
    ]

    assert (briefing_dir / "cursor.md").read_text(encoding="utf-8").startswith(
        "# Cursor briefing"
    )
    assert (briefing_dir / "github_copilot.md").read_text(encoding="utf-8") == "must remain"
    assert (briefing_dir / "notes.md").read_text(encoding="utf-8") == "must also remain"


def test_online_configuration_error_is_not_downgraded_or_swallowed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure_inputs(monkeypatch, tmp_path)
    monkeypatch.setattr(baseline, "create_service", lambda: _RagService())

    def fail_configuration(*_args, **_kwargs):
        raise ValueError("DEEPSEEK_API_KEY is required")

    monkeypatch.setattr(baseline, "MultiAgentOrchestrator", fail_configuration)

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY is required"):
        baseline.generate_baseline(
            competitors=["Cursor"],
            output_dir=tmp_path / "delivery",
            mode="llm",
            top_k=8,
            allow_partial=False,
            as_of="2026-07-20",
        )
