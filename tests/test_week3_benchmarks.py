from __future__ import annotations

import csv
import hashlib
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache

import pytest
from pydantic import ValidationError

import agents.benchmark_agent as benchmark_module
from agents.benchmark_agent import BenchmarkAgent
from schemas.benchmark import BenchmarkRun, BenchmarkTask, BenchmarkTaskType


RUN_AT = "2026-07-20T08:00:00Z"


@lru_cache(maxsize=None)
def _current_provenance(task_id: str) -> dict[str, str]:
    agent = BenchmarkAgent()
    tasks = {task.task_id: task for task in agent.load_tasks()}
    report = agent.audit_assets()
    records = {record.task_id: record for record in report.records}
    selected_id = task_id if task_id in tasks else "bench_001"
    task = tasks[selected_id]
    record = records[selected_id]
    return {
        "task_revision": task.task_revision,
        "task_fingerprint": task.task_fingerprint,
        "validator_sha256": record.validator_sha256,
        "protocol_sha256": record.protocol_sha256,
        "starter_sha256": record.starter_sha256,
    }


def _run_row(**overrides: object) -> dict[str, object]:
    task_id = str(overrides.get("task_id", "bench_001"))
    run_id = str(overrides.get("run_id", "run_test_001"))
    provenance = _current_provenance(task_id)
    row: dict[str, object] = {
        "run_id": run_id,
        "competitor": "Cursor",
        "task_id": task_id,
        **provenance,
        "candidate_sha256": hashlib.sha256(run_id.encode("utf-8")).hexdigest(),
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
        assert task.protocol_version == "week3-frozen-v2"
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


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_revision", ""),
        ("task_fingerprint", "sha256:" + "A" * 64),
        ("validator_sha256", "0" * 63),
        ("protocol_sha256", "G" * 64),
        ("starter_sha256", "sha256:" + "0" * 64),
        ("candidate_sha256", ""),
    ],
)
def test_run_schema_requires_strict_reproducibility_metadata(field, value) -> None:
    payload = _run_row()
    payload[field] = value

    with pytest.raises(ValidationError):
        BenchmarkRun.model_validate(payload)


def test_auto_run_id_includes_candidate_and_protocol_provenance() -> None:
    first_payload = _run_row(run_id="")
    second_payload = dict(first_payload)
    second_payload["candidate_sha256"] = "f" * 64

    first = BenchmarkRun.model_validate(first_payload)
    second = BenchmarkRun.model_validate(second_payload)

    assert first.run_id != second.run_id


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


def test_sample_csv_uses_current_audited_contract_and_real_starter_hashes() -> None:
    summary = BenchmarkAgent().import_csv(
        "benchmarks/results/sample_runs.csv",
    )

    assert summary.imported_count == 3
    assert summary.errors == []
    assert all(
        run.candidate_sha256 == run.starter_sha256 for run in summary.runs
    )


def test_empty_legacy_csv_header_is_rejected_with_migration_guidance(tmp_path) -> None:
    results_path = tmp_path / "legacy.csv"
    results_path.write_text(
        "run_id,competitor,task_id,product_version,model,task_success,"
        "compile_success,test_pass_rate,edit_rounds,latency_ms,"
        "manual_intervention,estimated_cost,harmful_action,notes,run_at\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Legacy CSV files are rejected"):
        BenchmarkAgent(results_path=results_path).load_runs()


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("task_revision", "0.0.0"),
        ("task_fingerprint", "sha256:" + "0" * 64),
        ("validator_sha256", "0" * 64),
        ("protocol_sha256", "0" * 64),
        ("starter_sha256", "0" * 64),
    ],
)
def test_import_rejects_results_from_noncurrent_contract(field, bad_value) -> None:
    summary = BenchmarkAgent().import_runs([_run_row(**{field: bad_value})])

    assert summary.imported_count == 0
    assert summary.skipped_count == 1
    assert field in summary.errors[0]


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


def test_persistence_rejects_reused_run_id_with_different_candidate(tmp_path) -> None:
    results_path = tmp_path / "manual_runs.csv"
    agent = BenchmarkAgent(results_path=results_path)
    agent.import_runs([_run_row()], persist=True)

    conflicting = _run_row(candidate_sha256="f" * 64)
    summary = agent.import_runs([conflicting], persist=True)

    assert summary.imported_count == 0
    assert summary.skipped_count == 1
    assert summary.duplicate_count == 1
    assert "conflicting run_id" in summary.errors[0]
    assert agent.load_runs()[0].candidate_sha256 != "f" * 64


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


def test_compare_rechecks_direct_runs_against_current_protocol() -> None:
    valid = BenchmarkRun.model_validate(_run_row())
    stale = valid.model_copy(
        update={
            "run_id": "run_stale_protocol",
            "protocol_sha256": "0" * 64,
        }
    )

    with pytest.raises(ValueError, match="protocol_sha256"):
        BenchmarkAgent().compare([valid, stale])


def test_compare_rejects_conflicting_duplicate_run_identity() -> None:
    valid = BenchmarkRun.model_validate(_run_row())
    conflicting = valid.model_copy(update={"notes": "different payload"})

    with pytest.raises(ValueError, match="conflicting duplicate run_id"):
        BenchmarkAgent().compare([valid, conflicting])
