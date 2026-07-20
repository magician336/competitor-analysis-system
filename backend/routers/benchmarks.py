"""HTTP endpoints for benchmark tasks and manual result imports."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from backend.services.benchmark_service import BenchmarkService, get_benchmark_service
from schemas.benchmark import (
    BenchmarkComparisonRow,
    BenchmarkImportSummary,
    BenchmarkRun,
    BenchmarkTask,
)


router = APIRouter(prefix="/api/benchmarks", tags=["Benchmarks"])


class BenchmarkImportRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)
    csv_path: str | None = None

    @model_validator(mode="after")
    def require_source(self) -> "BenchmarkImportRequest":
        if not self.rows and not self.csv_path:
            raise ValueError("rows or csv_path is required")
        return self


def _service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (FileNotFoundError, ValueError)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Benchmark service unavailable: {type(exc).__name__}: {exc}",
    )


@router.get("/tasks", response_model=list[BenchmarkTask])
def list_tasks(
    service: BenchmarkService = Depends(get_benchmark_service),
) -> list[BenchmarkTask]:
    return service.list_tasks()


@router.get("/results", response_model=list[BenchmarkRun])
def list_results(
    service: BenchmarkService = Depends(get_benchmark_service),
) -> list[BenchmarkRun]:
    return service.list_runs()


@router.post("/runs/import", response_model=BenchmarkImportSummary)
def import_runs(
    request: BenchmarkImportRequest,
    service: BenchmarkService = Depends(get_benchmark_service),
) -> BenchmarkImportSummary:
    try:
        if request.csv_path:
            return service.import_csv(request.csv_path)
        return service.import_rows(request.rows)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.get("/compare", response_model=list[BenchmarkComparisonRow])
def compare_results(
    service: BenchmarkService = Depends(get_benchmark_service),
) -> list[BenchmarkComparisonRow]:
    return service.compare()
