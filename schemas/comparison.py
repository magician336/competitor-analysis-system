"""Strict cross-product capability comparison contracts for week three.

The comparison layer consumes already-audited :class:`CapabilitySnapshot`
objects.  It never invents a score for an insufficient dimension and it keeps
the rule inputs that produced every ranking or trend conclusion in the output.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .capability_snapshot import CapabilityScoreStatus, CapabilitySnapshot
from .document import DimensionTag


class GapTrendStatus(str, Enum):
    WIDENING = "widening"
    NARROWING = "narrowing"
    STABLE = "stable"


class TableStakesStatus(str, Enum):
    TABLE_STAKES = "table_stakes"
    NOT_TABLE_STAKES = "not_table_stakes"
    INSUFFICIENT_DATA = "insufficient_data"


class ComparisonRules(BaseModel):
    """Configurable, serialized rules used by matrix-derived conclusions."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    minimum_rank_coverage: float = Field(default=0.80, gt=0.0, le=1.0)
    minimum_trend_coverage: float = Field(default=0.80, gt=0.0, le=1.0)
    minimum_competitive_products: int = Field(default=2, ge=2)
    table_stakes_score_threshold: int = Field(default=70, ge=0, le=100)
    table_stakes_min_valid_products: int = Field(default=3, ge=2)
    gap_stable_tolerance: float = Field(default=0.5, ge=0.0, le=100.0)


class CapabilityComparisonRequest(BaseModel):
    """Validated current and optional historical snapshot cohort."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    snapshots: list[CapabilitySnapshot] = Field(min_length=2)
    baseline_product: str = Field(default="CodeMate Campus", min_length=1)
    previous_snapshots: list[CapabilitySnapshot] = Field(default_factory=list)
    rules: ComparisonRules = Field(default_factory=ComparisonRules)

    @field_validator("baseline_product")
    @classmethod
    def strip_baseline(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("baseline_product must not be blank")
        return stripped

    @model_validator(mode="after")
    def validate_cohort(self) -> "CapabilityComparisonRequest":
        current_by_name: dict[str, CapabilitySnapshot] = {}
        for snapshot in self.snapshots:
            key = snapshot.competitor.strip().casefold()
            if key in current_by_name:
                raise ValueError(
                    "current snapshots must contain one snapshot per product"
                )
            current_by_name[key] = snapshot

        baseline_key = self.baseline_product.casefold()
        if baseline_key not in current_by_name:
            raise ValueError(
                f"baseline_product must exist in snapshots: {self.baseline_product}"
            )

        versions = {snapshot.scoring_version for snapshot in self.snapshots}
        if len(versions) != 1:
            raise ValueError("all current snapshots must use the same scoring_version")
        scoring_version = next(iter(versions))

        previous_by_name: dict[str, CapabilitySnapshot] = {}
        for snapshot in self.previous_snapshots:
            key = snapshot.competitor.strip().casefold()
            if key in previous_by_name:
                raise ValueError(
                    "previous_snapshots must contain one snapshot per product"
                )
            if key not in current_by_name:
                raise ValueError(
                    "previous_snapshots cannot contain products absent from snapshots"
                )
            if snapshot.scoring_version != scoring_version:
                raise ValueError(
                    "current and previous snapshots must use the same scoring_version"
                )
            if snapshot.snapshot_date > current_by_name[key].snapshot_date:
                raise ValueError(
                    "previous snapshot date must not be later than current snapshot date"
                )
            previous_by_name[key] = snapshot
        return self


class CapabilityMatrixCell(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    product: str = Field(min_length=1)
    dimension: DimensionTag
    status: CapabilityScoreStatus
    score: int | None = Field(default=None, ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_count: int = Field(ge=0)
    gap_to_baseline: int | None = Field(default=None, ge=-100, le=100)
    delta_from_previous: int | None = Field(default=None, ge=-100, le=100)

    @model_validator(mode="after")
    def align_status(self) -> "CapabilityMatrixCell":
        if self.status == CapabilityScoreStatus.SCORED and self.score is None:
            raise ValueError("scored matrix cell requires score")
        if self.status == CapabilityScoreStatus.INSUFFICIENT_EVIDENCE:
            if self.score is not None or self.gap_to_baseline is not None:
                raise ValueError("insufficient matrix cell must use N/A score and gap")
        return self


class CapabilityMatrixRow(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    dimension: DimensionTag
    cells: list[CapabilityMatrixCell] = Field(min_length=2)
    valid_product_count: int = Field(ge=0)
    mean_score: float | None = Field(default=None, ge=0.0, le=100.0)
    score_spread: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_cells(self) -> "CapabilityMatrixRow":
        products = [cell.product.casefold() for cell in self.cells]
        if len(products) != len(set(products)):
            raise ValueError("matrix row must contain one cell per product")
        if any(cell.dimension != self.dimension for cell in self.cells):
            raise ValueError("matrix cell dimension must match row dimension")
        valid = [cell for cell in self.cells if cell.status == CapabilityScoreStatus.SCORED]
        if self.valid_product_count != len(valid):
            raise ValueError("valid_product_count does not match scored cells")
        if not valid and (self.mean_score is not None or self.score_spread is not None):
            raise ValueError("empty dimension must not report score statistics")
        return self


class ProductComparisonSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    product: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    weighted_total_score: float | None = Field(default=None, ge=0.0, le=100.0)
    weighted_confidence: float = Field(ge=0.0, le=1.0)
    rank_eligible: bool
    rank: int | None = Field(default=None, ge=1)
    gap_to_baseline_total: float | None = Field(default=None, ge=-100.0, le=100.0)
    comparable_trend_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    weighted_delta: float | None = Field(default=None, ge=-100.0, le=100.0)
    trend_eligible: bool = False
    dimension_deltas: dict[DimensionTag, int] = Field(default_factory=dict)
    ranking_reason: str = Field(min_length=1)
    trend_reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_eligibility(self) -> "ProductComparisonSummary":
        if self.rank_eligible and self.weighted_total_score is None:
            raise ValueError("rank-eligible product requires weighted_total_score")
        if not self.rank_eligible and self.rank is not None:
            raise ValueError("coverage-ineligible product cannot have a rank")
        if self.trend_eligible and self.weighted_delta is None:
            raise ValueError("trend-eligible product requires weighted_delta")
        if not self.trend_eligible and self.weighted_delta is not None:
            raise ValueError("trend-ineligible product cannot report weighted_delta")
        return self


class FastestGrowthInsight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    product: str = Field(min_length=1)
    weighted_delta: float = Field(gt=0.0, le=100.0)
    comparable_coverage: float = Field(gt=0.0, le=1.0)
    dimensions: list[DimensionTag] = Field(min_length=1)
    current_snapshot_id: str = Field(min_length=1)
    previous_snapshot_id: str = Field(min_length=1)
    basis: str = Field(min_length=1)


class CompetitiveDimensionInsight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    dimension: DimensionTag
    valid_product_count: int = Field(ge=2)
    mean_score: float = Field(ge=0.0, le=100.0)
    score_spread: int = Field(ge=0, le=100)
    population_stddev: float = Field(ge=0.0, le=50.0)
    intensity_score: float = Field(ge=0.0, le=100.0)
    product_scores: dict[str, int] = Field(min_length=2)
    basis: str = Field(min_length=1)


class GapTrend(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    product: str = Field(min_length=1)
    dimension: DimensionTag
    previous_gap_to_baseline: int = Field(ge=-100, le=100)
    current_gap_to_baseline: int = Field(ge=-100, le=100)
    absolute_gap_change: float = Field(ge=-100.0, le=100.0)
    status: GapTrendStatus
    current_product_snapshot_id: str = Field(min_length=1)
    previous_product_snapshot_id: str = Field(min_length=1)
    current_baseline_snapshot_id: str = Field(min_length=1)
    previous_baseline_snapshot_id: str = Field(min_length=1)
    basis: str = Field(min_length=1)


class TableStakesInference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    dimension: DimensionTag
    status: TableStakesStatus
    score_threshold: int = Field(ge=0, le=100)
    minimum_valid_products: int = Field(ge=2)
    valid_product_count: int = Field(ge=0)
    qualifying_product_count: int = Field(ge=0)
    evaluated_scores: dict[str, int] = Field(default_factory=dict)
    qualifying_products: list[str] = Field(default_factory=list)
    basis: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence_counts(self) -> "TableStakesInference":
        if self.valid_product_count != len(self.evaluated_scores):
            raise ValueError("valid_product_count must match evaluated_scores")
        if self.qualifying_product_count != len(self.qualifying_products):
            raise ValueError("qualifying_product_count must match qualifying_products")
        if any(
            product not in self.evaluated_scores
            or self.evaluated_scores[product] < self.score_threshold
            for product in self.qualifying_products
        ):
            raise ValueError("qualifying_products must be supported by evaluated_scores")
        if (
            self.status == TableStakesStatus.INSUFFICIENT_DATA
            and self.valid_product_count >= self.minimum_valid_products
        ):
            raise ValueError("insufficient_data requires fewer than minimum valid products")
        if self.status == TableStakesStatus.TABLE_STAKES and (
            self.valid_product_count < self.minimum_valid_products
            or self.qualifying_product_count < self.minimum_valid_products
        ):
            raise ValueError("table_stakes status does not satisfy its explicit rule")
        return self


class CapabilityComparisonMatrix(BaseModel):
    """Complete D1--D7 comparison, ranking and explainable trend result."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    baseline_product: str = Field(min_length=1)
    scoring_version: str = Field(min_length=1)
    comparison_date: date
    rules: ComparisonRules
    current_snapshot_ids: dict[str, str] = Field(min_length=2)
    previous_snapshot_ids: dict[str, str] = Field(default_factory=dict)
    rows: list[CapabilityMatrixRow] = Field(min_length=7, max_length=7)
    products: list[ProductComparisonSummary] = Field(min_length=2)
    fastest_growth: FastestGrowthInsight | None = None
    most_competitive_dimension: CompetitiveDimensionInsight | None = None
    gap_trends: list[GapTrend] = Field(default_factory=list)
    table_stakes: list[TableStakesInference] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def validate_matrix(self) -> "CapabilityComparisonMatrix":
        row_dimensions = [row.dimension for row in self.rows]
        stakes_dimensions = [item.dimension for item in self.table_stakes]
        expected = set(DimensionTag)
        if len(set(row_dimensions)) != 7 or set(row_dimensions) != expected:
            raise ValueError("rows must contain D1--D7 exactly once")
        if len(set(stakes_dimensions)) != 7 or set(stakes_dimensions) != expected:
            raise ValueError("table_stakes must contain D1--D7 exactly once")

        product_names = [item.product for item in self.products]
        product_keys = {name.casefold() for name in product_names}
        if len(product_keys) != len(product_names):
            raise ValueError("products must contain unique product names")
        if self.baseline_product.casefold() not in product_keys:
            raise ValueError("baseline_product must exist in products")
        if {name.casefold() for name in self.current_snapshot_ids} != product_keys:
            raise ValueError("current_snapshot_ids must match products")
        for row in self.rows:
            if {cell.product.casefold() for cell in row.cells} != product_keys:
                raise ValueError("every row must contain every product exactly once")
        return self


__all__ = [
    "CapabilityComparisonMatrix",
    "CapabilityComparisonRequest",
    "CapabilityMatrixCell",
    "CapabilityMatrixRow",
    "ComparisonRules",
    "CompetitiveDimensionInsight",
    "FastestGrowthInsight",
    "GapTrend",
    "GapTrendStatus",
    "ProductComparisonSummary",
    "TableStakesInference",
    "TableStakesStatus",
]
