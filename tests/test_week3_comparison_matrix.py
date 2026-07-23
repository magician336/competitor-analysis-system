from __future__ import annotations

from datetime import date

import pytest
from langchain_core.runnables import Runnable
from pydantic import ValidationError

from agents.compare_agent import CompareAgent
from schemas.capability_snapshot import (
    CapabilityScore,
    CapabilityScoreStatus,
    CapabilitySnapshot,
)
from schemas.comparison import (
    ComparisonRules,
    GapTrendStatus,
    TableStakesStatus,
)
from schemas.document import DimensionTag


def _scored(dimension: DimensionTag, score: int) -> CapabilityScore:
    return CapabilityScore(
        dimension=dimension,
        score=score,
        confidence=0.85,
        evidence_count=1,
        evidence_chunk_ids=[f"chunk_{dimension.value}_{score}"],
        evidence_score=float(score),
        rationale="fixture score backed by an explicit evidence contribution",
    )


def _insufficient(dimension: DimensionTag) -> CapabilityScore:
    return CapabilityScore(
        dimension=dimension,
        score=0,
        confidence=0.0,
        evidence_count=0,
        rationale="fixture intentionally has no comparable evidence",
    )


def _snapshot(
    product: str,
    scores: dict[DimensionTag, int],
    *,
    snapshot_date: date = date(2026, 7, 20),
    scoring_version: str = "week3-evidence-v2",
) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        competitor=product,
        snapshot_date=snapshot_date,
        scoring_version=scoring_version,
        details=[
            _scored(dimension, scores[dimension])
            if dimension in scores
            else _insufficient(dimension)
            for dimension in DimensionTag
        ],
    )


def _uniform(product: str, score: int, **kwargs) -> CapabilitySnapshot:
    return _snapshot(
        product,
        {dimension: score for dimension in DimensionTag},
        **kwargs,
    )


def _cell(result, dimension: DimensionTag, product: str):
    row = next(item for item in result.rows if item.dimension == dimension)
    return next(item for item in row.cells if item.product == product)


def test_matrix_uses_real_lcel_and_keeps_insufficient_cells_na() -> None:
    baseline = _uniform("CodeMate Campus", 70)
    competitor = _uniform("Alpha", 80)
    sparse = _snapshot(
        "Sparse",
        {
            DimensionTag.CODE_INTELLIGENCE: 95,
            DimensionTag.AGENT_CONTEXT: 90,
        },
    )
    agent = CompareAgent()

    result = agent.compare_snapshots([sparse, competitor, baseline])

    assert isinstance(agent.comparison_pipeline, Runnable)
    assert agent.uses_langchain is True
    assert result.baseline_product == "CodeMate Campus"
    assert len(result.rows) == 7
    assert all(len(row.cells) == 3 for row in result.rows)
    assert _cell(
        result,
        DimensionTag.IDE_ECOSYSTEM,
        "Sparse",
    ).status == CapabilityScoreStatus.INSUFFICIENT_EVIDENCE
    assert _cell(result, DimensionTag.IDE_ECOSYSTEM, "Sparse").score is None
    assert _cell(result, DimensionTag.IDE_ECOSYSTEM, "Sparse").gap_to_baseline is None
    assert _cell(result, DimensionTag.CODE_INTELLIGENCE, "Alpha").gap_to_baseline == 10
    assert _cell(
        result,
        DimensionTag.CODE_INTELLIGENCE,
        "CodeMate Campus",
    ).gap_to_baseline == 0

    products = {item.product: item for item in result.products}
    assert products["Alpha"].rank_eligible is True
    assert products["Alpha"].rank == 1
    assert products["CodeMate Campus"].rank == 2
    assert products["Sparse"].rank_eligible is False
    assert products["Sparse"].rank is None
    assert products["Sparse"].weighted_total_score is not None
    assert "unranked" in products["Sparse"].ranking_reason

    d3_stakes = next(
        item
        for item in result.table_stakes
        if item.dimension == DimensionTag.IDE_ECOSYSTEM
    )
    assert d3_stakes.status == TableStakesStatus.INSUFFICIENT_DATA
    assert d3_stakes.valid_product_count == 2
    assert d3_stakes.evaluated_scores == {
        "CodeMate Campus": 70,
        "Alpha": 80,
    }


def test_missing_baseline_and_mixed_scoring_versions_are_rejected() -> None:
    agent = CompareAgent()
    alpha = _uniform("Alpha", 70)
    beta = _uniform("Beta", 75)

    with pytest.raises(ValidationError, match="baseline_product must exist"):
        agent.compare_snapshots([alpha, beta])

    mismatched = _uniform("Beta", 75, scoring_version="another-formula")
    with pytest.raises(ValidationError, match="same scoring_version"):
        agent.compare_snapshots(
            [_uniform("CodeMate Campus", 70), alpha, mismatched]
        )

    previous_mismatch = _uniform(
        "Alpha",
        60,
        snapshot_date=date(2026, 7, 13),
        scoring_version="old-formula",
    )
    with pytest.raises(ValidationError, match="same scoring_version"):
        agent.compare_snapshots(
            [_uniform("CodeMate Campus", 70), alpha],
            previous_snapshots=[previous_mismatch],
        )


def test_trends_growth_competition_gap_and_table_stakes_keep_rule_evidence() -> None:
    dimensions = list(DimensionTag)
    current_baseline_scores = {
        dimensions[0]: 75,
        dimensions[1]: 70,
        dimensions[2]: 60,
        dimensions[3]: 65,
        dimensions[4]: 65,
        dimensions[5]: 65,
        dimensions[6]: 65,
    }
    current_alpha_scores = {
        dimensions[0]: 82,
        dimensions[1]: 72,
        dimensions[2]: 60,
        dimensions[3]: 76,
        dimensions[4]: 76,
        dimensions[5]: 76,
        dimensions[6]: 76,
    }
    current_beta_scores = {
        dimensions[0]: 80,
        dimensions[1]: 71,
        dimensions[2]: 60,
        dimensions[3]: 66,
        dimensions[4]: 66,
        dimensions[5]: 66,
        dimensions[6]: 66,
    }
    previous_baseline_scores = {
        **current_baseline_scores,
        dimensions[0]: 73,
        dimensions[1]: 68,
    }
    previous_alpha_scores = {
        dimension: score - 8 for dimension, score in current_alpha_scores.items()
    }
    previous_alpha_scores[dimensions[0]] = 74
    previous_alpha_scores[dimensions[1]] = 75
    previous_beta_scores = {
        dimension: score - 1 for dimension, score in current_beta_scores.items()
    }

    current = [
        _snapshot("CodeMate Campus", current_baseline_scores),
        _snapshot("Alpha", current_alpha_scores),
        _snapshot("Beta", current_beta_scores),
    ]
    previous = [
        _snapshot(
            "CodeMate Campus",
            previous_baseline_scores,
            snapshot_date=date(2026, 7, 13),
        ),
        _snapshot(
            "Alpha",
            previous_alpha_scores,
            snapshot_date=date(2026, 7, 13),
        ),
        _snapshot(
            "Beta",
            previous_beta_scores,
            snapshot_date=date(2026, 7, 13),
        ),
    ]
    rules = ComparisonRules(
        minimum_rank_coverage=1.0,
        minimum_trend_coverage=1.0,
        minimum_competitive_products=3,
        table_stakes_score_threshold=70,
        table_stakes_min_valid_products=3,
        gap_stable_tolerance=0.5,
    )

    result = CompareAgent().compare_snapshots(
        current,
        previous_snapshots=previous,
        rules=rules,
    )

    assert result.fastest_growth is not None
    assert result.fastest_growth.product == "Alpha"
    assert result.fastest_growth.comparable_coverage == 1.0
    assert len(result.fastest_growth.dimensions) == 7
    assert "minimum_trend_coverage" in result.fastest_growth.basis

    assert result.most_competitive_dimension is not None
    assert result.most_competitive_dimension.dimension == dimensions[0]
    assert result.most_competitive_dimension.product_scores == {
        "CodeMate Campus": 75,
        "Alpha": 82,
        "Beta": 80,
    }
    assert "mean_score - population_stddev" in (
        result.most_competitive_dimension.basis
    )

    widening = next(
        item
        for item in result.gap_trends
        if item.product == "Alpha" and item.dimension == dimensions[0]
    )
    narrowing = next(
        item
        for item in result.gap_trends
        if item.product == "Alpha" and item.dimension == dimensions[1]
    )
    assert widening.previous_gap_to_baseline == 1
    assert widening.current_gap_to_baseline == 7
    assert widening.status == GapTrendStatus.WIDENING
    assert narrowing.previous_gap_to_baseline == 7
    assert narrowing.current_gap_to_baseline == 2
    assert narrowing.status == GapTrendStatus.NARROWING
    assert widening.current_baseline_snapshot_id == current[0].snapshot_id
    assert widening.previous_baseline_snapshot_id == previous[0].snapshot_id

    d1_stakes = next(
        item for item in result.table_stakes if item.dimension == dimensions[0]
    )
    d3_stakes = next(
        item for item in result.table_stakes if item.dimension == dimensions[2]
    )
    assert d1_stakes.status == TableStakesStatus.TABLE_STAKES
    assert d1_stakes.qualifying_product_count == 3
    assert d1_stakes.score_threshold == 70
    assert d1_stakes.minimum_valid_products == 3
    assert d3_stakes.status == TableStakesStatus.NOT_TABLE_STAKES
    assert d3_stakes.evaluated_scores == {
        "CodeMate Campus": 60,
        "Alpha": 60,
        "Beta": 60,
    }


def test_growth_is_withheld_when_comparable_coverage_is_too_low() -> None:
    current = [
        _uniform("CodeMate Campus", 70),
        _uniform("Alpha", 80),
    ]
    previous_alpha = _snapshot(
        "Alpha",
        {DimensionTag.CODE_INTELLIGENCE: 40},
        snapshot_date=date(2026, 7, 13),
    )

    result = CompareAgent().compare_snapshots(
        current,
        previous_snapshots=[previous_alpha],
        rules=ComparisonRules(minimum_trend_coverage=0.8),
    )

    alpha = next(item for item in result.products if item.product == "Alpha")
    assert alpha.comparable_trend_coverage == 0.2
    assert alpha.trend_eligible is False
    assert alpha.weighted_delta is None
    assert result.fastest_growth is None
