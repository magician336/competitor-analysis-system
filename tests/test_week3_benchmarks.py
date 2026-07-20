from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import agents.benchmark_agent as benchmark_module
from agents.benchmark_agent import BenchmarkAgent
from schemas.benchmark import BenchmarkRun, BenchmarkTask, BenchmarkTaskType


RUN_AT = "2026-07-20T08:00:00Z"


def _run_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "run_id": "run_test_001",
        "competitor": "Cursor",
        "task_id": "bench_001",
        "product_version": "2026.07",
        "model": "test-model",
        "task_success": True,
        "compile_success": True,
        "test_pass_rate": 1.0,
        "edit_rounds": 1,
        "latency_ms": 1_000,
        "manual_intervention": 0,
        "estimated_cost": 0.1,
        "harmful_action": False,
        "notes": "offline fixture",
        "run_at": RUN_AT,
    }
    row.update(overrides)
    return row


def test_catalog_has_sixteen_reproducible_balanced_tasks() -> None:
    tasks = BenchmarkAgent().load_tasks()

    assert len(tasks) == 16
    assert Counter(task.task_type for task in tasks) == {
        task_type: 2 for task_type in BenchmarkTaskType
    }
    assert len({task.task_id for task in tasks}) == 16
    assert len({task.task_fingerprint for task in tasks}) == 16
    for task in tasks:
        assert task.success_criteria
        assert task.validation_method.strip()
        assert len(task.fairness_constraints) >= 2
        assert task.protocol_version == "week3-manual-v1"
        assert task.task_fingerprint.startswith("sha256:")


def test_default_catalog_path_is_independent_of_current_directory(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    agent = BenchmarkAgent()

    assert agent.tasks_path.is_absolute()
    assert agent.tasks_path.name == "tasks.jsonl"
    assert len(agent.load_tasks()) == 16


def test_task_fingerprint_detects_metadata_drift() -> None:
    task = BenchmarkAgent().load_tasks()[0]
    payload = task.model_dump(mode="json")
    payload["prompt"] = "tampered prompt"

    with pytest.raises(ValidationError, match="task_fingerprint"):
        BenchmarkTask.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        _run_row(run_id="bad id"),
        _run_row(competitor="   "),
        _run_row(task_id="bad task id"),
        _run_row(product_version="   "),
        _run_row(run_at=datetime(2026, 7, 20, 8, 0, 0)),
    ],
)
def test_run_schema_rejects_invalid_identifiers_blank_text_and_naive_time(
    payload,
) -> None:
    with pytest.raises(ValidationError):
        BenchmarkRun.model_validate(payload)


def test_import_isolates_row_errors_unknown_tasks_and_batch_duplicates() -> None:
    agent = BenchmarkAgent()
    valid = _run_row()
    rows = [
        valid,
        _run_row(run_id="run_unknown", task_id="bench_999"),
        dict(valid),
        _run_row(run_id="run_blank", competitor="   "),
        _run_row(run_id="run_bool", task_success="maybe"),
    ]

    summary = agent.import_runs(rows)

    assert summary.imported_count == 1
    assert summary.skipped_count == 4
    assert summary.duplicate_count == 1
    assert summary.persisted_count == 0
    assert [run.run_id for run in summary.runs] == ["run_test_001"]
    assert len(summary.errors) == 4
    assert any("unknown task_id" in error for error in summary.errors)
    assert any("duplicate run_id" in error for error in summary.errors)
    assert any("invalid boolean value" in error for error in summary.errors)


def test_csv_import_isolates_invalid_rows(tmp_path) -> None:
    csv_path = tmp_path / "runs.csv"
    valid = _run_row()
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(valid))
        writer.writeheader()
        writer.writerow(valid)
        writer.writerow(_run_row(run_id="run_unknown_csv", task_id="bench_999"))

    summary = BenchmarkAgent().import_csv(csv_path)

    assert summary.imported_count == 1
    assert summary.skipped_count == 1
    assert len(summary.errors) == 1
    assert "unknown task_id" in summary.errors[0]


def test_atomic_persistence_is_idempotent(tmp_path) -> None:
    results_path = tmp_path / "manual_runs.csv"
    agent = BenchmarkAgent(results_path=results_path)

    first = agent.import_runs([_run_row()], persist=True)
    replay = agent.import_runs([_run_row()], persist=True)

    assert first.imported_count == 1
    assert first.persisted_count == 1
    assert replay.imported_count == 0
    assert replay.persisted_count == 0
    assert replay.skipped_count == 1
    assert replay.duplicate_count == 1
    persisted = agent.load_runs()
    assert [run.run_id for run in persisted] == ["run_test_001"]


def test_atomic_persistence_preserves_original_on_replace_failure(
    tmp_path, monkeypatch
) -> None:
    results_path = tmp_path / "manual_runs.csv"
    agent = BenchmarkAgent(results_path=results_path)
    agent.import_runs([_run_row()], persist=True)
    original = results_path.read_bytes()

    def fail_replace(source, destination) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(benchmark_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        agent.import_runs(
            [_run_row(run_id="run_test_002", task_id="bench_002")],
            persist=True,
        )

    assert results_path.read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))


def test_compare_reports_full_metrics_and_deduplicates_run_ids() -> None:
    first = BenchmarkRun.model_validate(_run_row(manual_intervention=1))
    second = BenchmarkRun.model_validate(
        _run_row(
            run_id="run_test_002",
            task_id="bench_002",
            task_success=False,
            compile_success=False,
            test_pass_rate=0.5,
            edit_rounds=2,
            latency_ms=2_000,
            manual_intervention=2,
            estimated_cost=0.2,
            harmful_action=True,
        )
    )

    rows = BenchmarkAgent().compare([first, first.model_copy(deep=True), second])

    assert len(rows) == 1
    row = rows[0]
    assert row.run_count == 2
    assert row.duplicate_run_count == 1
    assert row.unique_task_count == 2
    assert row.total_task_count == 16
    assert row.task_coverage_rate == pytest.approx(2 / 16)
    assert row.covered_task_type_count == 2
    assert row.task_type_coverage_rate == pytest.approx(2 / 8)
    assert row.task_success_rate == pytest.approx(0.5)
    assert row.compile_evaluated_count == 2
    assert row.compile_success_rate == pytest.approx(0.5)
    assert row.average_test_pass_rate == pytest.approx(0.75)
    assert row.average_latency_ms == pytest.approx(1_500)
    assert row.total_manual_intervention == 3
    assert row.average_manual_intervention == pytest.approx(1.5)
    assert row.total_estimated_cost == pytest.approx(0.3)
    assert row.average_estimated_cost == pytest.approx(0.15)
    assert row.harmful_action_count == 1
    assert row.harmful_action_rate == pytest.approx(0.5)
    assert row.safe_run_rate == pytest.approx(0.5)
