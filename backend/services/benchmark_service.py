"""Service adapter for benchmark tasks and manual result imports."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from agents.benchmark_agent import BenchmarkAgent
from schemas.benchmark import (
    BenchmarkComparisonRow,
    BenchmarkImportSummary,
    BenchmarkRun,
    BenchmarkTask,
)


class BenchmarkService:
    def __init__(self, agent: BenchmarkAgent | None = None) -> None:
        self.agent = agent or BenchmarkAgent()
        self._manual_runs: list[BenchmarkRun] = []
        self._lock = threading.RLock()

    def list_tasks(self) -> list[BenchmarkTask]:
        return self.agent.load_tasks()

    def list_runs(self) -> list[BenchmarkRun]:
        with self._lock:
            runs = list(self._manual_runs)
        return [*self.agent.load_runs(), *runs]

    def import_rows(self, rows: list[dict[str, Any]]) -> BenchmarkImportSummary:
        # Week-three manual imports are durable and idempotent. The Agent uses
        # an atomic file replacement, so a process restart cannot lose results.
        return self.agent.import_runs(rows, persist=True, idempotent=True)

    def import_csv(self, csv_path: str | Path) -> BenchmarkImportSummary:
        requested = Path(csv_path)
        candidate = (
            requested.resolve()
            if requested.is_absolute()
            else (Path.cwd() / requested).resolve()
        )
        allowed_root = self.agent.results_path.parent.resolve()
        if not candidate.is_relative_to(allowed_root):
            raise ValueError(
                "csv_path must stay inside the configured benchmarks/results directory"
            )
        return self.agent.import_csv(candidate, persist=True, idempotent=True)

    def compare(self) -> list[BenchmarkComparisonRow]:
        return self.agent.compare(self.list_runs())


_lock = threading.RLock()
_service: BenchmarkService | None = None


def get_benchmark_service() -> BenchmarkService:
    global _service
    with _lock:
        if _service is None:
            _service = BenchmarkService()
        return _service


def set_benchmark_service(service: BenchmarkService | None) -> None:
    global _service
    with _lock:
        _service = service


__all__ = ["BenchmarkService", "get_benchmark_service", "set_benchmark_service"]
