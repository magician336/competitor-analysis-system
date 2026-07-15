"""Six fixed ablation groups for the Mini-RAG retrieval pipeline."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from mini_rag.models import EvaluationCase, EvaluationResult

from .evaluator import RetrievalEvaluator


@dataclass(frozen=True)
class AblationSpec:
    group: str
    description: str
    use_bm25: bool
    use_dense: bool
    use_rrf: bool
    use_reranker: bool
    use_temporal_version: bool
    use_evidence_ranking: bool

    def as_config(self) -> dict[str, Any]:
        return asdict(self)


ABLATION_SPECS: tuple[AblationSpec, ...] = (
    AblationSpec("A", "BM25 only", True, False, False, False, False, False),
    AblationSpec("B", "Dense Vector only", False, True, False, False, False, False),
    AblationSpec("C", "BM25 + Dense Vector + RRF", True, True, True, False, False, False),
    AblationSpec("D", "C + Cross-Encoder Reranker", True, True, True, True, False, False),
    AblationSpec("E", "D + temporal and version ranking", True, True, True, True, True, False),
    AblationSpec("F", "E + evidence-level ranking", True, True, True, True, True, True),
)


class AblationRunner:
    """Run identical labelled cases against evaluator instances for groups A-F."""

    def __init__(
        self,
        evaluator_factory: Callable[[AblationSpec], RetrievalEvaluator],
        *,
        specs: Sequence[AblationSpec] = ABLATION_SPECS,
    ) -> None:
        groups = [spec.group for spec in specs]
        if len(groups) != len(set(groups)):
            raise ValueError("ablation groups must be unique")
        self.evaluator_factory = evaluator_factory
        self.specs = tuple(specs)

    def run(
        self,
        cases: Sequence[EvaluationCase | Mapping[str, Any]],
        *,
        shared_config: Mapping[str, Any] | None = None,
    ) -> dict[str, EvaluationResult]:
        results: dict[str, EvaluationResult] = {}
        for spec in self.specs:
            evaluator = self.evaluator_factory(spec)
            results[spec.group] = evaluator.evaluate(
                cases,
                config={**dict(shared_config or {}), "ablation": spec.as_config()},
            )
        return results


def run_six_ablations(
    evaluator_factory: Callable[[AblationSpec], RetrievalEvaluator],
    cases: Sequence[EvaluationCase | Mapping[str, Any]],
    *,
    shared_config: Mapping[str, Any] | None = None,
) -> dict[str, EvaluationResult]:
    return AblationRunner(evaluator_factory).run(cases, shared_config=shared_config)


def ablation_metric_table(results: Mapping[str, EvaluationResult]) -> list[dict[str, Any]]:
    """Flatten results into table-ready records ordered by ablation group."""

    return [
        {"group": group, **result.metrics}
        for group, result in sorted(results.items(), key=lambda item: item[0])
    ]


SIX_ABLATIONS = ABLATION_SPECS

