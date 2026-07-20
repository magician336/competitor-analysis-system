"""Evidence- and benchmark-aware D1--D7 snapshot generation."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Iterable

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.benchmark import BenchmarkRun, BenchmarkTask
from schemas.capability_snapshot import (
    BenchmarkContribution,
    CAPABILITY_WEIGHTS,
    CapabilityScore,
    CapabilityScoreStatus,
    CapabilitySnapshot,
    EvidenceContribution,
)
from schemas.comparison import (
    CapabilityComparisonMatrix,
    CapabilityComparisonRequest,
    CapabilityMatrixCell,
    CapabilityMatrixRow,
    ComparisonRules,
    CompetitiveDimensionInsight,
    FastestGrowthInsight,
    GapTrend,
    GapTrendStatus,
    ProductComparisonSummary,
    TableStakesInference,
    TableStakesStatus,
)
from schemas.document import DimensionTag, EvidenceLevel
from schemas.intelligence_card import (
    AgentKind,
    CapabilityImpact,
    ImpactDirection,
    IntelligenceCard,
)

from .lcel import runnable_lambda


class FreshnessBand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_days: int | None = Field(default=None, ge=0)
    weight: float = Field(ge=0.0, le=1.0)


class BenchmarkScoring(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_success_weight: float = Field(ge=0.0, le=1.0)
    test_pass_weight: float = Field(ge=0.0, le=1.0)
    compile_success_weight: float = Field(ge=0.0, le=1.0)
    harmful_action_penalty: float = Field(ge=0.0, le=100.0)
    manual_intervention_penalty_per_edit: float = Field(ge=0.0, le=100.0)
    max_manual_intervention_penalty: float = Field(ge=0.0, le=100.0)


class SnapshotScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    neutral_baseline: float = Field(ge=0.0, le=100.0)
    direction_points_per_magnitude: float = Field(gt=0.0, le=10.0)
    evidence_share: float = Field(ge=0.0, le=1.0)
    benchmark_share: float = Field(ge=0.0, le=1.0)
    evidence_level_weights: dict[EvidenceLevel, float]
    freshness: list[FreshnessBand] = Field(min_length=1)
    benchmark: BenchmarkScoring

    @model_validator(mode="after")
    def validate_weights(self) -> "SnapshotScoringConfig":
        if abs(self.evidence_share + self.benchmark_share - 1.0) > 1e-9:
            raise ValueError("evidence_share and benchmark_share must sum to 1")
        if set(self.evidence_level_weights) != set(EvidenceLevel):
            raise ValueError("evidence_level_weights must define A, B, C and D")
        if self.freshness[-1].max_days is not None:
            raise ValueError("last freshness band must be an open-ended fallback")
        return self


_DIRECTION_SIGN = {
    ImpactDirection.POSITIVE: 1.0,
    ImpactDirection.NEGATIVE: -1.0,
    ImpactDirection.MIXED: 0.0,
    ImpactDirection.NEUTRAL: 0.0,
    ImpactDirection.UNKNOWN: 0.0,
}


class CompareAgent:
    """Build reproducible snapshots without hard-coding competitor rankings."""

    def __init__(self, scoring_path: str | Path | None = None) -> None:
        root = Path(__file__).resolve().parents[1]
        path = Path(scoring_path) if scoring_path else root / "config" / "scoring.yaml"
        if not path.is_absolute():
            path = root / path
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            self.scoring = SnapshotScoringConfig.model_validate(payload)
        except Exception as exc:
            raise ValueError(f"invalid snapshot scoring config {path}: {exc}") from exc
        self.pipeline = runnable_lambda(
            self._build_from_payload,
            name="compare_evidence_and_benchmarks",
        ).with_config(
            {
                "tags": ["week3", "compare", "capability-snapshot"],
                "metadata": {"scoring_version": self.scoring.version},
            }
        )
        self.comparison_pipeline = runnable_lambda(
            self._build_comparison_matrix,
            name="compare_cross_product_capability_matrix",
        ).with_config(
            {
                "tags": ["week3", "compare", "cross-product-matrix"],
                "metadata": {"comparison_contract": "D1-D7"},
            }
        )

    @property
    def uses_langchain(self) -> bool:
        return self.pipeline.__class__.__module__.startswith(
            "langchain_core"
        ) and self.comparison_pipeline.__class__.__module__.startswith(
            "langchain_core"
        )

    def build_snapshot(
        self,
        competitor: str,
        cards: Iterable[IntelligenceCard],
        *,
        product_version: str | None = None,
        benchmark_runs: Iterable[BenchmarkRun] | None = None,
        benchmark_tasks: Iterable[BenchmarkTask] | None = None,
        previous_snapshot: CapabilitySnapshot | None = None,
        snapshot_date: date | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> CapabilitySnapshot:
        return self.pipeline.invoke(
            {
                "competitor": competitor,
                "cards": list(cards),
                "product_version": product_version,
                "benchmark_runs": list(benchmark_runs or []),
                "benchmark_tasks": list(benchmark_tasks or []),
                "previous_snapshot": previous_snapshot,
                "snapshot_date": snapshot_date,
                "window_start": window_start,
                "window_end": window_end,
            }
        )

    def compare_snapshots(
        self,
        snapshots: Iterable[CapabilitySnapshot],
        *,
        baseline_product: str = "CodeMate Campus",
        previous_snapshots: Iterable[CapabilitySnapshot] | None = None,
        rules: ComparisonRules | dict[str, Any] | None = None,
    ) -> CapabilityComparisonMatrix:
        """Build a validated D1--D7 matrix and evidence-backed trends.

        A baseline is never synthesized: ``baseline_product`` must be present
        in the current cohort.  Ranking, growth and table-stakes conclusions
        are derived exclusively from the serialized rule object.
        """

        request = CapabilityComparisonRequest(
            snapshots=list(snapshots),
            baseline_product=baseline_product,
            previous_snapshots=list(previous_snapshots or []),
            rules=rules or ComparisonRules(),
        )
        return self.comparison_pipeline.invoke(request)

    @staticmethod
    def _detail_map(snapshot: CapabilitySnapshot) -> dict[DimensionTag, CapabilityScore]:
        return {detail.dimension: detail for detail in snapshot.details}

    @staticmethod
    def _scored(detail: CapabilityScore | None) -> bool:
        return bool(detail and detail.status == CapabilityScoreStatus.SCORED)

    @staticmethod
    def _validated_summary(
        summary: ProductComparisonSummary,
        **updates: Any,
    ) -> ProductComparisonSummary:
        return ProductComparisonSummary.model_validate(
            {**summary.model_dump(mode="python"), **updates}
        )

    def _build_comparison_matrix(
        self,
        raw_request: CapabilityComparisonRequest | dict[str, Any],
    ) -> CapabilityComparisonMatrix:
        request = (
            raw_request
            if isinstance(raw_request, CapabilityComparisonRequest)
            else CapabilityComparisonRequest.model_validate(raw_request)
        )
        baseline_key = request.baseline_product.casefold()
        current_by_key = {
            snapshot.competitor.casefold(): snapshot for snapshot in request.snapshots
        }
        previous_by_key = {
            snapshot.competitor.casefold(): snapshot
            for snapshot in request.previous_snapshots
        }
        baseline = current_by_key[baseline_key]
        ordered = [baseline] + sorted(
            (
                snapshot
                for key, snapshot in current_by_key.items()
                if key != baseline_key
            ),
            key=lambda item: item.competitor.casefold(),
        )
        current_details = {
            snapshot.competitor.casefold(): self._detail_map(snapshot)
            for snapshot in ordered
        }
        previous_details = {
            key: self._detail_map(snapshot)
            for key, snapshot in previous_by_key.items()
        }

        rows = self._comparison_rows(
            ordered,
            current_details=current_details,
            previous_details=previous_details,
            baseline_key=baseline_key,
        )
        products = self._product_summaries(
            ordered,
            current_details=current_details,
            previous_details=previous_details,
            baseline_key=baseline_key,
            rules=request.rules,
        )
        fastest_growth = self._fastest_growth(
            products,
            current_by_key=current_by_key,
            previous_by_key=previous_by_key,
        )
        most_competitive = self._most_competitive_dimension(rows, request.rules)
        gap_trends = self._gap_trends(
            ordered,
            current_details=current_details,
            previous_details=previous_details,
            current_by_key=current_by_key,
            previous_by_key=previous_by_key,
            baseline_key=baseline_key,
            rules=request.rules,
        )
        table_stakes = self._table_stakes(rows, request.rules)

        return CapabilityComparisonMatrix(
            baseline_product=baseline.competitor,
            scoring_version=baseline.scoring_version,
            comparison_date=max(snapshot.snapshot_date for snapshot in ordered),
            rules=request.rules,
            current_snapshot_ids={
                snapshot.competitor: snapshot.snapshot_id for snapshot in ordered
            },
            previous_snapshot_ids={
                snapshot.competitor: snapshot.snapshot_id
                for snapshot in request.previous_snapshots
            },
            rows=rows,
            products=products,
            fastest_growth=fastest_growth,
            most_competitive_dimension=most_competitive,
            gap_trends=gap_trends,
            table_stakes=table_stakes,
        )

    def _comparison_rows(
        self,
        snapshots: list[CapabilitySnapshot],
        *,
        current_details: dict[str, dict[DimensionTag, CapabilityScore]],
        previous_details: dict[str, dict[DimensionTag, CapabilityScore]],
        baseline_key: str,
    ) -> list[CapabilityMatrixRow]:
        rows: list[CapabilityMatrixRow] = []
        for dimension in DimensionTag:
            baseline_detail = current_details[baseline_key].get(dimension)
            baseline_score = (
                baseline_detail.score if self._scored(baseline_detail) else None
            )
            cells: list[CapabilityMatrixCell] = []
            for snapshot in snapshots:
                key = snapshot.competitor.casefold()
                detail = current_details[key].get(dimension)
                is_scored = self._scored(detail)
                score = detail.score if is_scored and detail else None
                previous = previous_details.get(key, {}).get(dimension)
                delta = (
                    score - previous.score
                    if score is not None and self._scored(previous)
                    else None
                )
                gap = (
                    score - baseline_score
                    if score is not None and baseline_score is not None
                    else None
                )
                cells.append(
                    CapabilityMatrixCell(
                        product=snapshot.competitor,
                        dimension=dimension,
                        status=(
                            CapabilityScoreStatus.SCORED
                            if is_scored
                            else CapabilityScoreStatus.INSUFFICIENT_EVIDENCE
                        ),
                        score=score,
                        confidence=detail.confidence if detail else 0.0,
                        evidence_count=detail.evidence_count if detail else 0,
                        gap_to_baseline=gap,
                        delta_from_previous=delta,
                    )
                )
            valid_scores = [cell.score for cell in cells if cell.score is not None]
            rows.append(
                CapabilityMatrixRow(
                    dimension=dimension,
                    cells=cells,
                    valid_product_count=len(valid_scores),
                    mean_score=(round(fmean(valid_scores), 2) if valid_scores else None),
                    score_spread=(
                        max(valid_scores) - min(valid_scores) if valid_scores else None
                    ),
                )
            )
        return rows

    def _product_summaries(
        self,
        snapshots: list[CapabilitySnapshot],
        *,
        current_details: dict[str, dict[DimensionTag, CapabilityScore]],
        previous_details: dict[str, dict[DimensionTag, CapabilityScore]],
        baseline_key: str,
        rules: ComparisonRules,
    ) -> list[ProductComparisonSummary]:
        summaries: list[ProductComparisonSummary] = []
        for snapshot in snapshots:
            key = snapshot.competitor.casefold()
            details = current_details[key]
            scored = {
                dimension: detail
                for dimension, detail in details.items()
                if self._scored(detail)
            }
            coverage = sum(CAPABILITY_WEIGHTS[dimension] for dimension in scored)
            weighted_total = (
                sum(
                    detail.score * CAPABILITY_WEIGHTS[dimension]
                    for dimension, detail in scored.items()
                )
                / coverage
                if coverage
                else None
            )
            weighted_confidence = (
                sum(
                    detail.confidence * CAPABILITY_WEIGHTS[dimension]
                    for dimension, detail in scored.items()
                )
                / coverage
                if coverage
                else 0.0
            )

            previous = previous_details.get(key, {})
            dimension_deltas = {
                dimension: detail.score - previous[dimension].score
                for dimension, detail in scored.items()
                if dimension in previous and self._scored(previous[dimension])
            }
            trend_coverage = sum(
                CAPABILITY_WEIGHTS[dimension] for dimension in dimension_deltas
            )
            trend_eligible = (
                key in previous_details
                and trend_coverage >= rules.minimum_trend_coverage
            )
            weighted_delta = (
                sum(
                    delta * CAPABILITY_WEIGHTS[dimension]
                    for dimension, delta in dimension_deltas.items()
                )
                / trend_coverage
                if trend_eligible and trend_coverage
                else None
            )
            rank_eligible = coverage >= rules.minimum_rank_coverage
            summaries.append(
                ProductComparisonSummary(
                    product=snapshot.competitor,
                    snapshot_id=snapshot.snapshot_id,
                    coverage_ratio=round(coverage, 3),
                    weighted_total_score=(
                        round(weighted_total, 2) if weighted_total is not None else None
                    ),
                    weighted_confidence=round(weighted_confidence, 3),
                    rank_eligible=rank_eligible,
                    comparable_trend_coverage=round(trend_coverage, 3),
                    weighted_delta=(
                        round(weighted_delta, 2) if weighted_delta is not None else None
                    ),
                    trend_eligible=trend_eligible,
                    dimension_deltas=dimension_deltas,
                    ranking_reason=(
                        "weighted scored-dimension coverage satisfies "
                        f"minimum_rank_coverage={rules.minimum_rank_coverage:.2f}"
                        if rank_eligible
                        else "unranked because weighted scored-dimension coverage "
                        f"{coverage:.3f} is below {rules.minimum_rank_coverage:.3f}"
                    ),
                    trend_reason=(
                        "current/previous comparable weighted coverage satisfies "
                        f"minimum_trend_coverage={rules.minimum_trend_coverage:.2f}"
                        if trend_eligible
                        else "trend unavailable because comparable current/previous "
                        f"coverage {trend_coverage:.3f} is below "
                        f"{rules.minimum_trend_coverage:.3f}"
                    ),
                )
            )

        eligible = sorted(
            (item for item in summaries if item.rank_eligible),
            key=lambda item: (
                -(item.weighted_total_score or 0.0),
                -item.coverage_ratio,
                item.product.casefold(),
            ),
        )
        ranks: dict[str, int] = {}
        previous_score: float | None = None
        previous_rank = 0
        for position, item in enumerate(eligible, 1):
            if previous_score is None or item.weighted_total_score != previous_score:
                previous_rank = position
                previous_score = item.weighted_total_score
            ranks[item.product.casefold()] = previous_rank

        baseline_summary = next(
            item for item in summaries if item.product.casefold() == baseline_key
        )
        return [
            self._validated_summary(
                item,
                rank=ranks.get(item.product.casefold()),
                gap_to_baseline_total=(
                    round(
                        (item.weighted_total_score or 0.0)
                        - (baseline_summary.weighted_total_score or 0.0),
                        2,
                    )
                    if item.rank_eligible and baseline_summary.rank_eligible
                    else None
                ),
            )
            for item in summaries
        ]

    @staticmethod
    def _fastest_growth(
        products: list[ProductComparisonSummary],
        *,
        current_by_key: dict[str, CapabilitySnapshot],
        previous_by_key: dict[str, CapabilitySnapshot],
    ) -> FastestGrowthInsight | None:
        eligible = [
            item
            for item in products
            if item.trend_eligible
            and item.weighted_delta is not None
            and item.weighted_delta > 0
        ]
        if not eligible:
            return None
        winner = sorted(
            eligible,
            key=lambda item: (-item.weighted_delta, item.product.casefold()),
        )[0]
        key = winner.product.casefold()
        return FastestGrowthInsight(
            product=winner.product,
            weighted_delta=winner.weighted_delta,
            comparable_coverage=winner.comparable_trend_coverage,
            dimensions=list(winner.dimension_deltas),
            current_snapshot_id=current_by_key[key].snapshot_id,
            previous_snapshot_id=previous_by_key[key].snapshot_id,
            basis=(
                "largest positive capability-weighted score delta among products "
                "meeting minimum_trend_coverage"
            ),
        )

    @staticmethod
    def _most_competitive_dimension(
        rows: list[CapabilityMatrixRow],
        rules: ComparisonRules,
    ) -> CompetitiveDimensionInsight | None:
        candidates: list[CompetitiveDimensionInsight] = []
        for row in rows:
            scores = {
                cell.product: cell.score
                for cell in row.cells
                if cell.score is not None
            }
            if len(scores) < rules.minimum_competitive_products:
                continue
            values = list(scores.values())
            deviation = pstdev(values)
            mean = fmean(values)
            candidates.append(
                CompetitiveDimensionInsight(
                    dimension=row.dimension,
                    valid_product_count=len(scores),
                    mean_score=round(mean, 2),
                    score_spread=max(values) - min(values),
                    population_stddev=round(deviation, 3),
                    intensity_score=round(max(0.0, min(100.0, mean - deviation)), 3),
                    product_scores=scores,
                    basis=(
                        "intensity_score = mean_score - population_stddev; "
                        "higher means broadly strong and closely contested"
                    ),
                )
            )
        if not candidates:
            return None
        dimension_order = {dimension: index for index, dimension in enumerate(DimensionTag)}
        return max(
            candidates,
            key=lambda item: (
                item.intensity_score,
                item.valid_product_count,
                -item.score_spread,
                -dimension_order[item.dimension],
            ),
        )

    def _gap_trends(
        self,
        snapshots: list[CapabilitySnapshot],
        *,
        current_details: dict[str, dict[DimensionTag, CapabilityScore]],
        previous_details: dict[str, dict[DimensionTag, CapabilityScore]],
        current_by_key: dict[str, CapabilitySnapshot],
        previous_by_key: dict[str, CapabilitySnapshot],
        baseline_key: str,
        rules: ComparisonRules,
    ) -> list[GapTrend]:
        if baseline_key not in previous_details:
            return []
        output: list[GapTrend] = []
        for snapshot in snapshots:
            key = snapshot.competitor.casefold()
            if key == baseline_key or key not in previous_details:
                continue
            for dimension in DimensionTag:
                current_product = current_details[key].get(dimension)
                current_baseline = current_details[baseline_key].get(dimension)
                previous_product = previous_details[key].get(dimension)
                previous_baseline = previous_details[baseline_key].get(dimension)
                if not all(
                    self._scored(detail)
                    for detail in (
                        current_product,
                        current_baseline,
                        previous_product,
                        previous_baseline,
                    )
                ):
                    continue
                assert current_product and current_baseline
                assert previous_product and previous_baseline
                current_gap = current_product.score - current_baseline.score
                previous_gap = previous_product.score - previous_baseline.score
                absolute_change = abs(current_gap) - abs(previous_gap)
                if absolute_change > rules.gap_stable_tolerance:
                    status = GapTrendStatus.WIDENING
                elif absolute_change < -rules.gap_stable_tolerance:
                    status = GapTrendStatus.NARROWING
                else:
                    status = GapTrendStatus.STABLE
                output.append(
                    GapTrend(
                        product=snapshot.competitor,
                        dimension=dimension,
                        previous_gap_to_baseline=previous_gap,
                        current_gap_to_baseline=current_gap,
                        absolute_gap_change=round(absolute_change, 2),
                        status=status,
                        current_product_snapshot_id=current_by_key[key].snapshot_id,
                        previous_product_snapshot_id=previous_by_key[key].snapshot_id,
                        current_baseline_snapshot_id=current_by_key[
                            baseline_key
                        ].snapshot_id,
                        previous_baseline_snapshot_id=previous_by_key[
                            baseline_key
                        ].snapshot_id,
                        basis=(
                            "compare absolute current and previous signed score gaps; "
                            f"stable tolerance={rules.gap_stable_tolerance:.2f}"
                        ),
                    )
                )
        return output

    @staticmethod
    def _table_stakes(
        rows: list[CapabilityMatrixRow],
        rules: ComparisonRules,
    ) -> list[TableStakesInference]:
        results: list[TableStakesInference] = []
        for row in rows:
            evaluated = {
                cell.product: cell.score
                for cell in row.cells
                if cell.score is not None
            }
            qualifying = [
                product
                for product, score in evaluated.items()
                if score >= rules.table_stakes_score_threshold
            ]
            if len(evaluated) < rules.table_stakes_min_valid_products:
                status = TableStakesStatus.INSUFFICIENT_DATA
            elif len(qualifying) >= rules.table_stakes_min_valid_products:
                status = TableStakesStatus.TABLE_STAKES
            else:
                status = TableStakesStatus.NOT_TABLE_STAKES
            results.append(
                TableStakesInference(
                    dimension=row.dimension,
                    status=status,
                    score_threshold=rules.table_stakes_score_threshold,
                    minimum_valid_products=rules.table_stakes_min_valid_products,
                    valid_product_count=len(evaluated),
                    qualifying_product_count=len(qualifying),
                    evaluated_scores=evaluated,
                    qualifying_products=qualifying,
                    basis=(
                        "table_stakes requires at least "
                        f"{rules.table_stakes_min_valid_products} scored products at or "
                        f"above {rules.table_stakes_score_threshold}"
                    ),
                )
            )
        return results

    def _build_from_payload(self, payload: dict[str, Any]) -> CapabilitySnapshot:
        competitor = str(payload["competitor"]).strip()
        if not competitor:
            raise ValueError("competitor must not be blank")
        window_start = self._aware(payload.get("window_start"))
        window_end = self._aware(payload.get("window_end"))
        if window_start and window_end and window_end < window_start:
            raise ValueError("window_end must be greater than or equal to window_start")

        cards = self._scoped_cards(competitor, payload["cards"])
        requested_product_version = payload.get("product_version")
        snapshot_product_version = self._snapshot_product_version(
            requested_product_version,
            cards,
            payload["benchmark_runs"],
        )
        runs = self._scoped_runs(
            competitor,
            payload["benchmark_runs"],
            window_start,
            window_end,
        )
        tasks: dict[str, BenchmarkTask] = {
            task.task_id: task for task in payload["benchmark_tasks"]
        }
        self._validate_benchmark_cohort(runs, tasks)
        previous: CapabilitySnapshot | None = payload.get("previous_snapshot")
        details = [
            self._score_dimension(
                dimension,
                cards,
                runs,
                tasks,
                # Only an explicitly requested version scopes evidence.  The
                # derived snapshot label below must not accidentally filter a
                # mixed corpus after retrieval has already happened.
                product_version=requested_product_version,
                previous=previous,
                window_start=window_start,
                window_end=window_end,
            )
            for dimension in DimensionTag
        ]
        return CapabilitySnapshot(
            competitor=competitor,
            snapshot_date=(
                payload.get("snapshot_date")
                or (window_end.date() if window_end is not None else date.today())
            ),
            product_version=snapshot_product_version,
            scoring_version=self.scoring.version,
            window_start=window_start,
            window_end=window_end,
            details=details,
            previous_snapshot_id=previous.snapshot_id if previous else None,
        )

    @staticmethod
    def _snapshot_product_version(
        requested: str | None,
        cards: Iterable[IntelligenceCard],
        benchmark_runs: Iterable[BenchmarkRun],
    ) -> str:
        if requested is not None and requested.strip():
            return requested.strip()
        versions = {
            version.strip()
            for card in cards
            for reference in card.evidence
            for version in [reference.product_version]
            if version is not None and version.strip()
        }
        versions.update(
            run.product_version.strip()
            for run in benchmark_runs
            if run.product_version is not None and run.product_version.strip()
        )
        if not versions:
            return "unknown"
        if len(versions) == 1:
            return next(iter(versions))
        return "mixed"

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _scoped_cards(
        competitor: str,
        cards: Iterable[IntelligenceCard],
    ) -> list[IntelligenceCard]:
        unique: dict[str, IntelligenceCard] = {}
        for raw in cards:
            card = raw if isinstance(raw, IntelligenceCard) else IntelligenceCard.model_validate(raw)
            if card.competitor.casefold() == competitor.casefold():
                unique[card.card_id] = card
        return list(unique.values())

    @staticmethod
    def _scoped_runs(
        competitor: str,
        runs: Iterable[BenchmarkRun],
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> list[BenchmarkRun]:
        unique: dict[str, BenchmarkRun] = {}
        for raw in runs:
            run = (
                BenchmarkRun.model_validate(raw.model_dump(mode="python"))
                if isinstance(raw, BenchmarkRun)
                else BenchmarkRun.model_validate(raw)
            )
            if run.competitor.casefold() != competitor.casefold():
                continue
            run_at = run.run_at if run.run_at.tzinfo else run.run_at.replace(tzinfo=timezone.utc)
            if window_start and run_at < window_start:
                continue
            if window_end and run_at > window_end:
                continue
            existing = unique.get(run.run_id)
            if existing is not None and existing != run:
                raise ValueError(
                    f"conflicting duplicate benchmark run_id {run.run_id!r}"
                )
            unique[run.run_id] = run
        return list(unique.values())

    @staticmethod
    def _validate_benchmark_cohort(
        runs: Iterable[BenchmarkRun],
        tasks: dict[str, BenchmarkTask],
    ) -> None:
        """Reject untraceable or mixed-protocol benchmark evidence.

        The catalog-level :class:`BenchmarkAgent` verifies hashes against the
        live frozen assets.  Snapshot construction can also receive explicit
        in-memory tasks, so it enforces the strongest check available here:
        task identity must match and every run of the same task must name one
        identical validator/protocol/starter contract.
        """

        protocol_by_task: dict[str, tuple[str, str, str]] = {}
        for run in runs:
            task = tasks.get(run.task_id)
            if task is None:
                raise ValueError(
                    f"benchmark run {run.run_id!r} references task {run.task_id!r} "
                    "which is absent from benchmark_tasks"
                )
            mismatches: list[str] = []
            if run.task_revision != task.task_revision:
                mismatches.append("task_revision")
            if run.task_fingerprint != task.task_fingerprint:
                mismatches.append("task_fingerprint")
            if mismatches:
                raise ValueError(
                    f"benchmark run {run.run_id!r} does not match task "
                    f"{run.task_id!r}: {mismatches}"
                )

            protocol = (
                run.validator_sha256,
                run.protocol_sha256,
                run.starter_sha256,
            )
            prior = protocol_by_task.setdefault(run.task_id, protocol)
            if prior != protocol:
                raise ValueError(
                    f"benchmark cohort mixes validator/protocol/starter hashes "
                    f"for task {run.task_id!r}"
                )

    def _score_dimension(
        self,
        dimension: DimensionTag,
        cards: list[IntelligenceCard],
        runs: list[BenchmarkRun],
        tasks: dict[str, BenchmarkTask],
        *,
        product_version: str | None,
        previous: CapabilitySnapshot | None,
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> CapabilityScore:
        evidence = self._evidence_contributions(
            dimension,
            cards,
            product_version=product_version,
            window_start=window_start,
            window_end=window_end,
        )
        benchmark = self._benchmark_contributions(dimension, runs, tasks)
        evidence_score, evidence_confidence = self._evidence_score(evidence)
        benchmark_score, benchmark_confidence = self._benchmark_score(
            dimension,
            benchmark,
            tasks,
        )

        active: list[tuple[float, float, float]] = []
        if evidence_score is not None:
            active.append((evidence_score, evidence_confidence, self.scoring.evidence_share))
        if benchmark_score is not None:
            active.append((benchmark_score, benchmark_confidence, self.scoring.benchmark_share))
        if active:
            denominator = sum(weight for _, _, weight in active)
            final_score = round(sum(score * weight for score, _, weight in active) / denominator)
            confidence = round(
                min(0.98, sum(conf * weight for _, conf, weight in active) / denominator),
                3,
            )
            status = CapabilityScoreStatus.SCORED
        else:
            final_score = 0
            confidence = 0.0
            status = CapabilityScoreStatus.INSUFFICIENT_EVIDENCE

        previous_detail = None
        if previous:
            previous_detail = next(
                (item for item in previous.details if item.dimension == dimension),
                None,
            )
        delta = (
            final_score - previous_detail.score
            if previous_detail
            and previous_detail.status == CapabilityScoreStatus.SCORED
            and status == CapabilityScoreStatus.SCORED
            else None
        )
        levels = Counter(item.evidence_level for item in evidence)
        evidence_ids = list(dict.fromkeys(item.chunk_id for item in evidence))
        card_ids = list(dict.fromkeys(item.card_id for item in evidence))
        run_ids = list(dict.fromkeys(item.run_id for item in benchmark))
        sources = {(item.source_type, item.url) for item in evidence}
        if status == CapabilityScoreStatus.INSUFFICIENT_EVIDENCE:
            rationale = f"{dimension.value} 没有可用证据或对应基准结果，未生成推测性基线分。"
        else:
            parts = []
            if evidence_score is not None:
                parts.append(
                    f"证据分 {evidence_score:.1f}（{len(evidence_ids)} 条、{len(sources)} 个独立来源）"
                )
            if benchmark_score is not None:
                parts.append(f"基准分 {benchmark_score:.1f}（{len(run_ids)} 次运行）")
            rationale = f"{dimension.value}: " + "；".join(parts) + "。"
        return CapabilityScore(
            dimension=dimension,
            score=final_score,
            status=status,
            confidence=confidence,
            evidence_count=len(evidence_ids),
            independent_source_count=len(sources),
            evidence_levels=dict(levels),
            evidence_chunk_ids=evidence_ids,
            source_card_ids=card_ids,
            benchmark_run_ids=run_ids,
            evidence_score=evidence_score,
            benchmark_score=benchmark_score,
            delta=delta,
            evidence_contributions=evidence,
            benchmark_contributions=benchmark,
            rationale=rationale,
        )

    def _evidence_contributions(
        self,
        dimension: DimensionTag,
        cards: list[IntelligenceCard],
        *,
        product_version: str | None,
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> list[EvidenceContribution]:
        deduplicated: dict[str, EvidenceContribution] = {}
        for card in cards:
            impacts = [item for item in card.impact_details if item.dimension == dimension]
            if not impacts:
                impacts = self._legacy_impacts(card, dimension)
            references = {item.chunk_id: item for item in card.evidence}
            for impact in impacts:
                for chunk_id in impact.evidence_chunk_ids:
                    reference = references.get(chunk_id)
                    if reference is None:
                        continue
                    if product_version and reference.product_version not in {None, product_version}:
                        continue
                    published = self._aware(reference.publish_time)
                    if window_start and published and published < window_start:
                        continue
                    if window_end and published and published > window_end:
                        continue
                    contribution = EvidenceContribution(
                        chunk_id=chunk_id,
                        card_id=card.card_id,
                        url=reference.url,
                        source_type=reference.source_type,
                        evidence_level=reference.evidence_level,
                        direction=impact.direction,
                        magnitude=impact.magnitude,
                        authority_weight=self.scoring.evidence_level_weights[
                            reference.evidence_level
                        ],
                        freshness_weight=self._freshness_weight(published),
                        confidence_weight=min(card.confidence_score, impact.confidence_score),
                        publish_time=published,
                    )
                    existing = deduplicated.get(chunk_id)
                    # A source chunk contributes at most once per dimension.
                    # Rank equal-authority analyses deterministically so the
                    # result cannot change merely because cards arrived in a
                    # different parallel-branch order.
                    if existing is None or self._contribution_rank(
                        contribution
                    ) > self._contribution_rank(existing):
                        deduplicated[chunk_id] = contribution
        return list(deduplicated.values())

    @staticmethod
    def _legacy_impacts(
        card: IntelligenceCard,
        dimension: DimensionTag,
    ) -> list[CapabilityImpact]:
        relevant = [
            item.chunk_id for item in card.evidence if dimension in item.dimension_tags
        ]
        if not relevant or dimension not in card.dimension_tags:
            return []
        direction = {
            AgentKind.PRODUCT: ImpactDirection.POSITIVE,
            AgentKind.RISK: ImpactDirection.NEGATIVE,
            AgentKind.PRICE: ImpactDirection.MIXED,
        }.get(card.agent_kind, ImpactDirection.UNKNOWN)
        return [
            CapabilityImpact(
                dimension=dimension,
                direction=direction,
                magnitude=max(1, min(10, round(card.priority_score / 10))),
                confidence_score=card.confidence_score,
                rationale="legacy card compatibility mapping",
                evidence_chunk_ids=relevant,
            )
        ]

    def _freshness_weight(self, published: datetime | None) -> float:
        if published is None:
            return self.scoring.freshness[-1].weight
        days = max(0, (datetime.now(timezone.utc) - published).days)
        for band in self.scoring.freshness:
            if band.max_days is None or days <= band.max_days:
                return band.weight
        return self.scoring.freshness[-1].weight

    @staticmethod
    def _contribution_weight(item: EvidenceContribution) -> float:
        return item.authority_weight * item.freshness_weight * item.confidence_weight

    @classmethod
    def _contribution_rank(
        cls,
        item: EvidenceContribution,
    ) -> tuple[float, int, str]:
        return (cls._contribution_weight(item), item.magnitude, item.card_id)

    def _evidence_score(
        self,
        contributions: list[EvidenceContribution],
    ) -> tuple[float | None, float]:
        if not contributions:
            return None, 0.0
        weights = [self._contribution_weight(item) for item in contributions]
        denominator = sum(weights)
        signed = (
            sum(
                _DIRECTION_SIGN[item.direction] * item.magnitude * weight
                for item, weight in zip(contributions, weights)
            )
            / denominator
            if denominator
            else 0.0
        )
        score = max(
            0.0,
            min(
                100.0,
                self.scoring.neutral_baseline
                + signed * self.scoring.direction_points_per_magnitude,
            ),
        )
        volume = min(1.0, 0.45 + 0.12 * len(contributions))
        source_diversity = min(
            1.0,
            0.75
            + 0.08
            * len({(item.source_type, item.url) for item in contributions}),
        )
        confidence = min(
            0.96,
            (sum(weights) / len(weights)) * volume * source_diversity,
        )
        return round(score, 2), round(confidence, 3)

    def _benchmark_contributions(
        self,
        dimension: DimensionTag,
        runs: list[BenchmarkRun],
        tasks: dict[str, BenchmarkTask],
    ) -> list[BenchmarkContribution]:
        contributions: list[BenchmarkContribution] = []
        for run in runs:
            task = tasks.get(run.task_id)
            if task is None or dimension not in task.primary_dimensions:
                continue
            contributions.append(
                BenchmarkContribution(
                    run_id=run.run_id,
                    task_id=run.task_id,
                    score=self._benchmark_run_score(run),
                    task_success=run.task_success,
                    test_pass_rate=run.test_pass_rate,
                    harmful_action=run.harmful_action,
                )
            )
        return contributions

    def _benchmark_run_score(self, run: BenchmarkRun) -> float:
        config = self.scoring.benchmark
        components = [
            (1.0 if run.task_success else 0.0, config.task_success_weight),
            (run.test_pass_rate, config.test_pass_weight),
        ]
        if run.compile_success is not None:
            components.append(
                (1.0 if run.compile_success else 0.0, config.compile_success_weight)
            )
        total_weight = sum(weight for _, weight in components)
        raw = 100 * sum(value * weight for value, weight in components) / total_weight
        raw -= min(
            config.max_manual_intervention_penalty,
            run.manual_intervention * config.manual_intervention_penalty_per_edit,
        )
        if run.harmful_action:
            raw -= config.harmful_action_penalty
        return round(max(0.0, min(100.0, raw)), 2)

    def _benchmark_score(
        self,
        dimension: DimensionTag,
        contributions: list[BenchmarkContribution],
        tasks: dict[str, BenchmarkTask],
    ) -> tuple[float | None, float]:
        if not contributions:
            return None, 0.0
        score = sum(item.score for item in contributions) / len(contributions)
        relevant_tasks = {
            task.task_id
            for task in tasks.values()
            if dimension in task.primary_dimensions
        }
        covered = {item.task_id for item in contributions}
        coverage = len(covered) / len(relevant_tasks) if relevant_tasks else 0.5
        repeat_factor = min(1.0, 0.65 + 0.08 * len(contributions))
        return round(score, 2), round(min(0.98, coverage * repeat_factor), 3)


__all__ = ["CompareAgent", "SnapshotScoringConfig"]
