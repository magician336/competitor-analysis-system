"""Evidence- and benchmark-aware D1--D7 snapshot generation."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.benchmark import BenchmarkRun, BenchmarkTask
from schemas.capability_snapshot import (
    BenchmarkContribution,
    CapabilityScore,
    CapabilityScoreStatus,
    CapabilitySnapshot,
    EvidenceContribution,
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

    @property
    def uses_langchain(self) -> bool:
        return self.pipeline.__class__.__module__.startswith("langchain_core")

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

    def _build_from_payload(self, payload: dict[str, Any]) -> CapabilitySnapshot:
        competitor = str(payload["competitor"]).strip()
        if not competitor:
            raise ValueError("competitor must not be blank")
        window_start = self._aware(payload.get("window_start"))
        window_end = self._aware(payload.get("window_end"))
        if window_start and window_end and window_end < window_start:
            raise ValueError("window_end must be greater than or equal to window_start")

        cards = self._scoped_cards(competitor, payload["cards"])
        runs = self._scoped_runs(
            competitor,
            payload["benchmark_runs"],
            window_start,
            window_end,
        )
        tasks: dict[str, BenchmarkTask] = {
            task.task_id: task for task in payload["benchmark_tasks"]
        }
        previous: CapabilitySnapshot | None = payload.get("previous_snapshot")
        details = [
            self._score_dimension(
                dimension,
                cards,
                runs,
                tasks,
                product_version=payload.get("product_version"),
                previous=previous,
                window_start=window_start,
                window_end=window_end,
            )
            for dimension in DimensionTag
        ]
        return CapabilitySnapshot(
            competitor=competitor,
            snapshot_date=payload.get("snapshot_date") or date.today(),
            product_version=payload.get("product_version"),
            scoring_version=self.scoring.version,
            window_start=window_start,
            window_end=window_end,
            details=details,
            previous_snapshot_id=previous.snapshot_id if previous else None,
        )

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
            run = raw if isinstance(raw, BenchmarkRun) else BenchmarkRun.model_validate(raw)
            if run.competitor.casefold() != competitor.casefold():
                continue
            run_at = run.run_at if run.run_at.tzinfo else run.run_at.replace(tzinfo=timezone.utc)
            if window_start and run_at < window_start:
                continue
            if window_end and run_at > window_end:
                continue
            unique[run.run_id] = run
        return list(unique.values())

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
