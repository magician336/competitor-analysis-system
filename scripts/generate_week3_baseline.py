"""Generate the auditable week-three baseline from the configured Mini-RAG index.

This command deliberately uses the same LangChain Multi-Agent workflow as the
service layer.  It writes only derived third-week artifacts; it does not crawl,
rebuild the index, call a frontend, or perform any fourth-week deployment work.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from agents.orchestrator import MultiAgentOrchestrator
from mini_rag.api import create_service
from schemas.orchestration import (
    MultiAgentAnalysisRequest,
    MultiAgentAnalysisResult,
    WorkflowExecutionStatus,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts" / "week3"
ALLOWED_AGENT_MODES = {"rules", "llm", "hybrid"}


def _load_competitors(path: Path) -> list[str]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    competitors = payload.get("competitors", [])
    names = [str(item.get("name", "")).strip() for item in competitors]
    names = list(dict.fromkeys(name for name in names if name))
    if not names:
        raise ValueError(f"no competitors configured in {path}")
    return names


def _select_competitors(configured: list[str], requested: str) -> list[str]:
    if requested.strip().casefold() == "all":
        return configured
    aliases = {name.casefold(): name for name in configured}
    selected: list[str] = []
    unknown: list[str] = []
    for raw in requested.split(","):
        value = raw.strip()
        if not value:
            continue
        canonical = aliases.get(value.casefold())
        if canonical is None:
            unknown.append(value)
        elif canonical not in selected:
            selected.append(canonical)
    if unknown:
        raise ValueError("unknown competitors: " + ", ".join(unknown))
    if not selected:
        raise ValueError("at least one competitor must be selected")
    return selected


def _safe_filename(value: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_.")
    if ascii_name:
        return ascii_name.casefold()
    digest = value.encode("utf-8").hex()[:24]
    return f"competitor_{digest}"


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _write_json(path: Path, value: Any) -> None:
    _atomic_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _model_list(values: Iterable[Any]) -> list[dict[str, Any]]:
    return [value.model_dump(mode="json") for value in values]


def generate_baseline(
    *,
    competitors: list[str],
    output_dir: Path,
    mode: str,
    top_k: int,
    allow_partial: bool,
) -> dict[str, Any]:
    if mode not in ALLOWED_AGENT_MODES:
        raise ValueError(f"unsupported Agent mode: {mode}")
    os.environ["CODERADAR_AGENT_MODE"] = mode

    rag_service = create_service()
    health = rag_service.health()
    if health.get("status") != "ready":
        raise RuntimeError(
            "Mini-RAG is not ready; build a compatible index before generating "
            f"the baseline: {health}"
        )

    workflow = MultiAgentOrchestrator(
        rag_service,
        auto_configure_llm=mode in {"llm", "hybrid"},
    )
    results: list[MultiAgentAnalysisResult] = []
    for competitor in competitors:
        result = workflow.run(
            MultiAgentAnalysisRequest(
                competitor=competitor,
                top_k=top_k,
                correlation_id=f"week3-baseline-{_safe_filename(competitor)}",
                include_snapshot=True,
                include_briefing=True,
                include_benchmark_data=True,
                use_cache=False,
            )
        )
        if result.status == WorkflowExecutionStatus.FAILED:
            raise RuntimeError(f"all specialist branches failed for {competitor}")
        if (
            result.status == WorkflowExecutionStatus.PARTIAL_FAILURE
            and not allow_partial
        ):
            raise RuntimeError(
                f"partial failure for {competitor}: " + "; ".join(result.warnings)
            )
        if result.snapshot is None or result.briefing is None:
            raise RuntimeError(f"missing snapshot or briefing for {competitor}")
        results.append(result)

    cards = [card for result in results for card in result.cards]
    snapshots = [result.snapshot for result in results if result.snapshot is not None]
    generated_at = datetime.now(timezone.utc)
    summary = [
        {
            "competitor": result.request.competitor,
            "workflow_id": result.workflow_id,
            "status": result.status.value,
            "card_count": len(result.cards),
            "evidence_count": sum(len(card.evidence) for card in result.cards),
            "snapshot_id": result.snapshot.snapshot_id if result.snapshot else None,
            "snapshot_score": result.snapshot.total_score if result.snapshot else None,
            "snapshot_coverage": (
                result.snapshot.coverage_ratio if result.snapshot else None
            ),
            "warnings": result.warnings,
            "branches": {
                branch.value: outcome.status.value
                for branch, outcome in result.branch_outcomes.items()
            },
        }
        for result in results
    ]
    manifest = {
        "artifact_version": "week3-baseline-v1",
        "generated_at": generated_at.isoformat(),
        "analysis_mode": mode,
        "scoring_version": snapshots[0].scoring_version if snapshots else None,
        "competitors": competitors,
        "competitor_count": len(competitors),
        "intelligence_card_count": len(cards),
        "snapshot_count": len(snapshots),
        "benchmark_task_count": len(workflow.benchmark_agent.load_tasks()),
        "benchmark_run_count": len(workflow.benchmark_agent.load_runs()),
        "mini_rag": {
            "index": health.get("index"),
            "physical_index": health.get("physical_index"),
            "indexed_chunks": health.get("indexed_chunks"),
            "embedding_model": health.get("embedding_model"),
            "embedding_dimension": health.get("embedding_dimension"),
            "embedding_compatible": health.get("embedding_compatible"),
        },
        "files": {
            "cards": "intelligence_cards.json",
            "snapshots": "capability_snapshots.json",
            "workflows": "workflow_summary.json",
            "briefings": "briefings/",
        },
    }

    _write_json(output_dir / "manifest.json", manifest)
    _write_json(output_dir / "intelligence_cards.json", _model_list(cards))
    _write_json(output_dir / "capability_snapshots.json", _model_list(snapshots))
    _write_json(output_dir / "workflow_summary.json", summary)
    for result in results:
        assert result.briefing is not None
        _atomic_text(
            output_dir / "briefings" / f"{_safe_filename(result.request.competitor)}.md",
            result.briefing.rstrip() + "\n",
        )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate five-competitor week-three Agent baseline artifacts."
    )
    parser.add_argument(
        "--competitors",
        default="all",
        help="Comma-separated configured competitor names, or 'all'.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Artifact directory (default: artifacts/week3).",
    )
    parser.add_argument(
        "--mode",
        choices=sorted(ALLOWED_AGENT_MODES),
        default="rules",
        help="Agent analysis mode; rules is reproducible and offline.",
    )
    parser.add_argument("--top-k", type=int, choices=range(1, 31), default=8)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Write artifacts when at least one specialist branch succeeds.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configured = _load_competitors(PROJECT_ROOT / "config" / "competitors.yaml")
    competitors = _select_competitors(configured, args.competitors)
    output = args.output
    if not output.is_absolute():
        output = (PROJECT_ROOT / output).resolve()
    manifest = generate_baseline(
        competitors=competitors,
        output_dir=output,
        mode=args.mode,
        top_k=args.top_k,
        allow_partial=args.allow_partial,
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "competitor_count": manifest["competitor_count"],
                "intelligence_card_count": manifest["intelligence_card_count"],
                "snapshot_count": manifest["snapshot_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

