from __future__ import annotations

from pathlib import Path

from backend.services import benchmark_service
from backend.services.benchmark_service import BenchmarkService


class _BenchmarkAgentStub:
    def __init__(self, project_root: Path) -> None:
        self.results_path = (
            project_root / "benchmarks" / "results" / "manual_runs.csv"
        )
        self.imported_path: Path | None = None

    def import_csv(
        self,
        path: Path,
        *,
        persist: bool,
        idempotent: bool,
    ) -> object:
        assert persist is True
        assert idempotent is True
        self.imported_path = path
        return object()


def test_relative_import_path_is_rooted_at_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    unrelated_cwd = tmp_path / "unrelated"
    unrelated_cwd.mkdir()
    agent = _BenchmarkAgentStub(tmp_path)
    service = BenchmarkService(agent=agent)  # type: ignore[arg-type]
    monkeypatch.setattr(benchmark_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(unrelated_cwd)

    service.import_csv("benchmarks/results/import.csv")

    assert agent.imported_path == (
        tmp_path / "benchmarks" / "results" / "import.csv"
    ).resolve()
