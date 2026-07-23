"""Benchmark task loading, manual run import and summary comparison."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable

from schemas.benchmark import (
    BenchmarkComparisonRow,
    BenchmarkImportSummary,
    BenchmarkRun,
    BenchmarkTask,
    BenchmarkTaskType,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_PERSIST_LOCK = threading.RLock()
_RUN_PROVENANCE_FIELDS = (
    "task_revision",
    "task_fingerprint",
    "validator_sha256",
    "protocol_sha256",
    "starter_sha256",
    "candidate_sha256",
)


def _repository_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = _REPOSITORY_ROOT / candidate
    return candidate.resolve()


class BenchmarkAgent:
    def __init__(
        self,
        *,
        tasks_path: str | Path = "benchmarks/tasks/tasks.jsonl",
        results_path: str | Path = "benchmarks/results/manual_runs.csv",
    ) -> None:
        # Defaults and caller-provided relative paths are repository-relative so
        # CLI, tests and API workers behave identically regardless of cwd.
        self.tasks_path = _repository_path(tasks_path)
        self.results_path = _repository_path(results_path)

    def load_tasks(self) -> list[BenchmarkTask]:
        if not self.tasks_path.exists():
            return []
        tasks: list[BenchmarkTask] = []
        task_ids: set[str] = set()
        for line_number, line in enumerate(self.tasks_path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                task = BenchmarkTask.model_validate_json(stripped)
            except Exception as exc:
                raise ValueError(f"invalid benchmark task line {line_number}: {exc}") from exc
            if task.task_id in task_ids:
                raise ValueError(
                    f"duplicate benchmark task_id at line {line_number}: {task.task_id}"
                )
            task_ids.add(task.task_id)
            tasks.append(task)
        return tasks

    def load_runs(self) -> list[BenchmarkRun]:
        if not self.results_path.exists():
            return []
        with self.results_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            self._validate_csv_header(reader.fieldnames)
            persisted_rows = [
                (row_number, row)
                for row_number, row in enumerate(reader, 2)
                if any(value not in (None, "") for value in row.values())
            ]
        if not persisted_rows:
            return []

        tasks = self.load_tasks()
        task_by_id, contract_by_id = self._current_protocol_contracts(tasks)
        runs: list[BenchmarkRun] = []
        for row_number, row in persisted_rows:
            try:
                run = self._row_to_run(row)
                self._validate_current_protocol(
                    run,
                    task_by_id=task_by_id,
                    contract_by_id=contract_by_id,
                )
                runs.append(run)
            except Exception as exc:
                raise ValueError(
                    f"invalid persisted benchmark row {row_number}: {exc}"
                ) from exc
        return runs

    def import_runs(
        self,
        rows: Iterable[dict[str, object]],
        *,
        persist: bool = False,
        idempotent: bool = True,
    ) -> BenchmarkImportSummary:
        """Validate rows independently and optionally commit them atomically.

        The original ``import_runs(rows)`` call remains unchanged. When
        ``persist=True``, valid new rows are appended via an atomic replacement;
        replayed run IDs are skipped when ``idempotent=True``.
        """

        tasks = self.load_tasks()
        task_by_id, contract_by_id = self._current_protocol_contracts(tasks)
        candidates: list[tuple[int, BenchmarkRun]] = []
        errors: list[str] = []
        seen_batch: set[str] = set()
        skipped_count = 0
        duplicate_count = 0
        for index, row in enumerate(rows, 1):
            try:
                run = self._row_to_run(row)
                self._validate_current_protocol(
                    run,
                    task_by_id=task_by_id,
                    contract_by_id=contract_by_id,
                )
                if run.run_id in seen_batch:
                    duplicate_count += 1
                    skipped_count += 1
                    errors.append(f"row {index}: duplicate run_id {run.run_id!r} in batch")
                    continue
                seen_batch.add(run.run_id)
                candidates.append((index, run))
            except Exception as exc:
                errors.append(f"row {index}: {exc}")
                skipped_count += 1

        persisted_count = 0
        if persist and candidates:
            with _PERSIST_LOCK:
                existing = self.load_runs()
                existing_by_id = {run.run_id: run for run in existing}
                accepted: list[tuple[int, BenchmarkRun]] = []
                for index, run in candidates:
                    existing_run = existing_by_id.get(run.run_id)
                    if existing_run is not None:
                        duplicate_count += 1
                        skipped_count += 1
                        if existing_run != run:
                            errors.append(
                                f"row {index}: conflicting run_id {run.run_id!r} "
                                "already identifies different result or provenance data"
                            )
                        elif not idempotent:
                            errors.append(
                                f"row {index}: run_id {run.run_id!r} already exists"
                            )
                        continue
                    existing_by_id[run.run_id] = run
                    accepted.append((index, run))
                candidates = accepted
                if candidates:
                    # Existing legacy duplicates are collapsed during the atomic
                    # rewrite, preventing them from distorting future summaries.
                    combined = list(existing_by_id.values())
                    self._write_runs_atomic(combined)
                    persisted_count = len(candidates)

        imported = [run for _, run in candidates]
        return BenchmarkImportSummary(
            imported_count=len(imported),
            skipped_count=skipped_count,
            duplicate_count=duplicate_count,
            persisted_count=persisted_count,
            errors=errors,
            runs=imported,
        )

    def import_csv(
        self,
        csv_path: str | Path,
        *,
        persist: bool = False,
        idempotent: bool = True,
    ) -> BenchmarkImportSummary:
        path = Path(csv_path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            self._validate_csv_header(reader.fieldnames)
            return self.import_runs(
                reader,
                persist=persist,
                idempotent=idempotent,
            )

    def compare(self, runs: Iterable[BenchmarkRun] | None = None) -> list[BenchmarkComparisonRow]:
        materialized = (
            [
                BenchmarkRun.model_validate(run.model_dump(mode="python"))
                if isinstance(run, BenchmarkRun)
                else BenchmarkRun.model_validate(run)
                for run in runs
            ]
            if runs is not None
            else self.load_runs()
        )
        tasks = self.load_tasks()
        if not materialized:
            return []
        task_by_id, contract_by_id = self._current_protocol_contracts(tasks)
        for run in materialized:
            self._validate_current_protocol(
                run,
                task_by_id=task_by_id,
                contract_by_id=contract_by_id,
            )
        total_tasks = len(task_by_id)
        total_task_types = len(BenchmarkTaskType)

        unique_runs: list[BenchmarkRun] = []
        seen_run_ids: dict[str, BenchmarkRun] = {}
        duplicate_counts: dict[str, int] = {}
        for run in materialized:
            canonical = seen_run_ids.get(run.run_id)
            if canonical is not None:
                if canonical != run:
                    raise ValueError(
                        f"conflicting duplicate run_id {run.run_id!r}; "
                        "a run ID cannot identify different result or provenance data"
                    )
                duplicate_counts[canonical.competitor] = (
                    duplicate_counts.get(canonical.competitor, 0) + 1
                )
                continue
            seen_run_ids[run.run_id] = run
            unique_runs.append(run)

        grouped: dict[str, list[BenchmarkRun]] = {}
        for run in unique_runs:
            grouped.setdefault(run.competitor, []).append(run)
        rows: list[BenchmarkComparisonRow] = []
        for competitor, competitor_runs in sorted(
            grouped.items(), key=lambda item: item[0].casefold()
        ):
            count = len(competitor_runs)
            covered_task_ids = sorted(
                {run.task_id for run in competitor_runs if run.task_id in task_by_id}
            )
            covered_task_types = {
                task_by_id[task_id].task_type for task_id in covered_task_ids
            }
            compile_results = [
                run.compile_success
                for run in competitor_runs
                if run.compile_success is not None
            ]
            harmful_count = sum(run.harmful_action for run in competitor_runs)
            total_interventions = sum(
                run.manual_intervention for run in competitor_runs
            )
            total_cost = sum(run.estimated_cost for run in competitor_runs)
            rows.append(
                BenchmarkComparisonRow(
                    competitor=competitor,
                    run_count=count,
                    task_success_rate=sum(run.task_success for run in competitor_runs) / count,
                    average_test_pass_rate=sum(run.test_pass_rate for run in competitor_runs) / count,
                    average_edit_rounds=sum(run.edit_rounds for run in competitor_runs) / count,
                    harmful_action_count=harmful_count,
                    unique_task_count=len(covered_task_ids),
                    total_task_count=total_tasks,
                    task_coverage_rate=(
                        len(covered_task_ids) / total_tasks if total_tasks else 0.0
                    ),
                    covered_task_type_count=len(covered_task_types),
                    total_task_type_count=total_task_types,
                    task_type_coverage_rate=(
                        len(covered_task_types) / total_task_types
                        if total_task_types
                        else 0.0
                    ),
                    covered_task_ids=covered_task_ids,
                    compile_evaluated_count=len(compile_results),
                    compile_success_rate=(
                        sum(compile_results) / len(compile_results)
                        if compile_results
                        else None
                    ),
                    average_latency_ms=(
                        sum(run.latency_ms for run in competitor_runs) / count
                    ),
                    total_manual_intervention=total_interventions,
                    average_manual_intervention=total_interventions / count,
                    total_estimated_cost=total_cost,
                    average_estimated_cost=total_cost / count,
                    harmful_action_rate=harmful_count / count,
                    safe_run_rate=(count - harmful_count) / count,
                    duplicate_run_count=duplicate_counts.get(competitor, 0),
                )
            )
        return rows

    @staticmethod
    def _row_to_run(row: dict[str, object]) -> BenchmarkRun:
        if None in row:
            raise ValueError("row contains values beyond the declared CSV columns")
        cleaned: dict[str, Any] = {}
        for key, value in row.items():
            if not key:
                raise ValueError("row contains a blank column name")
            normalized_key = str(key).strip()
            if not normalized_key:
                raise ValueError("row contains a blank column name")
            if normalized_key in cleaned:
                raise ValueError(f"duplicate column after normalization: {normalized_key}")
            cleaned[normalized_key] = value

        missing_provenance = [
            name
            for name in _RUN_PROVENANCE_FIELDS
            if cleaned.get(name) in (None, "")
        ]
        if missing_provenance:
            raise ValueError(
                "legacy benchmark row rejected; missing reproducibility fields "
                f"{missing_provenance}. Re-run the frozen validator and copy its "
                "task, validator, protocol, starter and candidate hashes; these "
                "values cannot be inferred safely from an old result."
            )

        for optional_name in ("product_version", "model"):
            if cleaned.get(optional_name) in (None, ""):
                cleaned.pop(optional_name, None)
        for bool_name in ("task_success", "compile_success", "harmful_action"):
            if bool_name in cleaned:
                cleaned[bool_name] = _to_bool(cleaned[bool_name])
        for int_name in ("edit_rounds", "latency_ms", "manual_intervention"):
            if cleaned.get(int_name) in (None, ""):
                cleaned.pop(int_name, None)
            elif int_name in cleaned:
                cleaned[int_name] = int(cleaned[int_name])
        for float_name in ("test_pass_rate", "estimated_cost"):
            if cleaned.get(float_name) in (None, ""):
                cleaned.pop(float_name, None)
            elif float_name in cleaned:
                cleaned[float_name] = float(cleaned[float_name])
        return BenchmarkRun.model_validate(cleaned)

    @staticmethod
    def _validate_csv_header(fieldnames: list[str] | None) -> None:
        if not fieldnames:
            raise ValueError("benchmark CSV must contain a header")
        normalized = [str(name).strip() for name in fieldnames]
        if any(not name for name in normalized):
            raise ValueError("benchmark CSV contains a blank header")
        if len(normalized) != len(set(normalized)):
            raise ValueError("benchmark CSV contains duplicate headers")
        expected = list(BenchmarkRun.model_fields)
        if normalized != expected:
            missing = [name for name in expected if name not in normalized]
            unknown = [name for name in normalized if name not in expected]
            raise ValueError(
                "benchmark CSV header must exactly match the current BenchmarkRun "
                f"contract; missing={missing}, unknown={unknown}, "
                f"expected_order={expected}. Legacy CSV files are rejected because "
                "protocol and content hashes cannot be reconstructed safely; re-run "
                "the frozen validator and create a new CSV."
            )

    @staticmethod
    def _current_protocol_contracts(
        tasks: Iterable[BenchmarkTask],
    ) -> tuple[dict[str, BenchmarkTask], dict[str, Any]]:
        materialized = list(tasks)
        task_by_id = {task.task_id: task for task in materialized}
        if len(task_by_id) != len(materialized):
            raise ValueError("benchmark task catalog contains duplicate task IDs")

        from benchmarks.validators.run_task import audit_assets

        report = audit_assets(materialized)
        if report.status != "ready" or report.errors:
            details = "; ".join(report.errors) or report.status
            raise ValueError(f"benchmark asset audit is not ready: {details}")
        contract_by_id = {record.task_id: record for record in report.records}
        if set(contract_by_id) != set(task_by_id):
            missing = sorted(set(task_by_id) - set(contract_by_id))
            unknown = sorted(set(contract_by_id) - set(task_by_id))
            raise ValueError(
                "benchmark asset audit does not match the task catalog: "
                f"missing={missing}, unknown={unknown}"
            )
        return task_by_id, contract_by_id

    def _validate_current_protocol(
        self,
        run: BenchmarkRun,
        *,
        task_by_id: dict[str, BenchmarkTask],
        contract_by_id: dict[str, Any],
    ) -> None:
        task = task_by_id.get(run.task_id)
        if task is None:
            raise ValueError(
                f"unknown task_id {run.task_id!r}; it is not in {self.tasks_path}"
            )
        contract = contract_by_id[run.task_id]
        expected = {
            "task_revision": task.task_revision,
            "task_fingerprint": task.task_fingerprint,
            "validator_sha256": contract.validator_sha256,
            "protocol_sha256": contract.protocol_sha256,
            "starter_sha256": contract.starter_sha256,
        }
        mismatches = [
            name for name, value in expected.items() if getattr(run, name) != value
        ]
        if mismatches:
            raise ValueError(
                f"benchmark run {run.run_id!r} provenance does not match the current "
                f"frozen contract for {run.task_id}: {mismatches}. Do not mix results "
                "from different task revisions or validator protocols; re-run this "
                "task against the current starter."
            )

    def _write_runs_atomic(self, runs: Iterable[BenchmarkRun]) -> None:
        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = list(BenchmarkRun.model_fields)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                prefix=f".{self.results_path.name}.",
                suffix=".tmp",
                dir=self.results_path.parent,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for run in runs:
                    writer.writerow(run.model_dump(mode="json"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.results_path)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    def tasks_as_json(self) -> str:
        return json.dumps(
            [task.model_dump(mode="json") for task in self.load_tasks()],
            ensure_ascii=False,
            indent=2,
        )

    def audit_assets(self):
        """Audit the frozen task repositories without executing their starters."""

        from benchmarks.validators.run_task import audit_assets

        return audit_assets(self.load_tasks())


def _to_bool(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "y", "是", "成功"}:
        return True
    if normalized in {"0", "false", "no", "n", "否", "失败"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


__all__ = ["BenchmarkAgent"]
