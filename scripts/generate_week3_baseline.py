"""Generate the auditable week-three baseline from the configured Mini-RAG index.

This command deliberately uses the same LangChain Multi-Agent workflow as the
service layer.  It writes only derived third-week artifacts; it does not crawl,
rebuild the index, call a frontend, or perform any fourth-week deployment work.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
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

# generate_comparison is called at the end of generate_baseline() so the
# comparison matrix always matches the freshly-produced capability snapshots.
from .generate_week3_comparison import generate_comparison



PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts" / "week3"
TASKS_INPUT = PROJECT_ROOT / "benchmarks" / "tasks" / "tasks.jsonl"
SCORING_INPUT = PROJECT_ROOT / "config" / "scoring.yaml"
CLEANED_DATA_INPUT = PROJECT_ROOT / "data" / "cleaned" / "documents.jsonl"
ALLOWED_AGENT_MODES = {"rules", "llm", "hybrid"}
KNOWN_COMPETITOR_SLUGS = {
    "cursor": "cursor",
    "github copilot": "github_copilot",
    "trae": "trae",
    "通义灵码": "tongyi_lingma",
    "codegeex": "codegeex",
}


def _parse_as_of(value: str) -> datetime:
    """Parse a UTC ISO timestamp or an inclusive UTC calendar date."""

    raw = value.strip()
    if not raw:
        raise argparse.ArgumentTypeError("--as-of must not be blank")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            parsed_date = date.fromisoformat(raw)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid --as-of date: {value}") from exc
        return datetime.combine(parsed_date, time.max, tzinfo=timezone.utc)

    normalized = raw[:-1] + "+00:00" if raw.upper().endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--as-of must be an ISO timestamp or YYYY-MM-DD"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "--as-of timestamps must include UTC (Z or +00:00)"
        )
    if parsed.utcoffset() != timedelta(0):
        raise argparse.ArgumentTypeError("--as-of must use UTC, not a local offset")
    return parsed.astimezone(timezone.utc)


def _positive_days(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("window days must be an integer") from exc
    if parsed < 1 or parsed > 3_650:
        raise argparse.ArgumentTypeError("window days must be between 1 and 3650")
    return parsed


def _analysis_window(
    as_of: datetime | date | str | None,
    window_days: int,
) -> tuple[datetime, datetime]:
    if window_days < 1 or window_days > 3_650:
        raise ValueError("window_days must be between 1 and 3650")
    if as_of is None:
        end_time = datetime.now(timezone.utc)
    elif isinstance(as_of, datetime):
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of datetime must be timezone-aware UTC")
        if as_of.utcoffset() != timedelta(0):
            raise ValueError("as_of datetime must use UTC")
        end_time = as_of.astimezone(timezone.utc)
    elif isinstance(as_of, date):
        end_time = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
    else:
        try:
            end_time = _parse_as_of(as_of)
        except argparse.ArgumentTypeError as exc:
            raise ValueError(str(exc)) from exc
    return end_time - timedelta(days=window_days), end_time


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"required baseline input is missing: {path}") from exc
    return "sha256:" + digest.hexdigest()


def _manifest_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


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
    known = KNOWN_COMPETITOR_SLUGS.get(value.strip().casefold())
    if known is not None:
        return known
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


def _invalidate_commit_marker(path: Path) -> None:
    """Remove a prior manifest before replacing any member of its artifact set."""

    if path.is_symlink() or path.is_dir():
        raise RuntimeError(f"refusing unsafe baseline manifest target: {path}")
    path.unlink(missing_ok=True)


def _model_list(values: Iterable[Any]) -> list[dict[str, Any]]:
    return [value.model_dump(mode="json") for value in values]


def _redact_text(value: Any) -> str:
    """Keep diagnostics useful without persisting credentials or prompt text."""

    text = " ".join(str(value).split())
    configured_secret = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if configured_secret:
        text = text.replace(configured_secret, "[REDACTED]")
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", text)
    text = re.sub(
        r"(?i)\b(api[_ -]?key|authorization|bearer|token|secret)\b"
        r"\s*[:=]?\s*[^\s,;]+",
        r"\1=[REDACTED]",
        text,
    )
    prompt_match = re.search(r"(?i)\bprompt\b\s*[:=]", text)
    if prompt_match:
        text = text[: prompt_match.start()] + "prompt=[REDACTED]"
    return text[:1_000]


def _safe_error(error: Any | None) -> dict[str, Any] | None:
    if error is None:
        return None
    return {
        "error_type": str(error.error_type),
        "message": _redact_text(error.message),
        "stage": str(error.stage),
        "retryable": bool(error.retryable),
    }


def _safe_trace(trace: Any | None) -> dict[str, Any] | None:
    if trace is None:
        return None
    # Event detail is intentionally excluded: provider callbacks may attach
    # model input fragments. The structural timing trace is sufficient here.
    return {
        "trace_id": str(trace.trace_id),
        "agent_kind": getattr(trace.agent_kind, "value", str(trace.agent_kind)),
        "duration_ms": float(trace.duration_ms),
        "events": [
            {
                "stage": str(event.stage),
                "event": str(event.event),
                "at": event.at.isoformat(),
            }
            for event in trace.events
        ],
        "llm_used": bool(trace.llm_used),
        "fallback_used": bool(trace.fallback_used),
        "model_name": trace.model_name,
    }


def _trace_summaries(
    results: Iterable[MultiAgentAnalysisResult],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for workflow_result in results:
        for branch, outcome in workflow_result.branch_outcomes.items():
            branch_result = outcome.result
            summaries.append(
                {
                    "workflow_id": workflow_result.workflow_id,
                    "branch": branch.value,
                    "status": outcome.status.value,
                    "duration_ms": outcome.duration_ms,
                    "error": _safe_error(outcome.error),
                    "trace": _safe_trace(
                        branch_result.trace if branch_result is not None else None
                    ),
                    "rag_query_id": (
                        branch_result.rag_query_id
                        if branch_result is not None
                        else None
                    ),
                    "card_ids": (
                        [card.card_id for card in branch_result.cards]
                        if branch_result is not None
                        else []
                    ),
                    "warnings": (
                        [_redact_text(item) for item in branch_result.warnings]
                        if branch_result is not None
                        else []
                    ),
                }
            )
    return summaries


def _effective_mode_counts(
    results: Iterable[MultiAgentAnalysisResult],
) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = {}
    for workflow_result in results:
        for branch, outcome in workflow_result.branch_outcomes.items():
            branch_counts = counts.setdefault(branch.value, Counter())
            if outcome.result is None:
                continue
            for card in outcome.result.cards:
                branch_counts[str(card.analysis_mode)] += 1
    return {
        branch: {mode: branch_counts.get(mode, 0) for mode in sorted(ALLOWED_AGENT_MODES)}
        for branch, branch_counts in sorted(counts.items())
    }


def _controlled_briefing_paths(
    output_dir: Path,
    competitors: Iterable[str],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    briefing_dir = output_dir / "briefings"
    if briefing_dir.is_symlink():
        raise RuntimeError("briefings directory must not be a symbolic link")
    briefing_dir.mkdir(parents=True, exist_ok=True)
    resolved_output = output_dir.resolve()
    resolved_briefings = briefing_dir.resolve()
    if resolved_briefings.parent != resolved_output:
        raise RuntimeError("briefings directory escapes the requested output directory")

    paths: dict[str, Path] = {}
    used_names: set[str] = set()
    for competitor in competitors:
        filename = f"{_safe_filename(competitor)}.md"
        if filename in used_names:
            raise ValueError(f"competitor briefing filename collision: {filename}")
        used_names.add(filename)
        target = briefing_dir / filename
        if target.parent.resolve() != resolved_briefings:
            raise RuntimeError("briefing path escapes the controlled directory")
        paths[competitor] = target
    return paths


def _clean_controlled_briefings(paths: Iterable[Path]) -> None:
    for path in paths:
        if path.is_dir():
            raise RuntimeError(f"refusing to replace briefing directory: {path}")
        if path.exists() or path.is_symlink():
            path.unlink()


def generate_baseline(
    *,
    competitors: list[str],
    output_dir: Path,
    mode: str,
    top_k: int,
    allow_partial: bool,
    as_of: datetime | date | str | None = None,
    window_days: int = 90,
) -> dict[str, Any]:
    if mode not in ALLOWED_AGENT_MODES:
        raise ValueError(f"unsupported Agent mode: {mode}")
    if top_k < 1 or top_k > 30:
        raise ValueError("top_k must be between 1 and 30")
    start_time, end_time = _analysis_window(as_of, window_days)
    input_sha256 = {
        "tasks": {
            "path": _manifest_path(TASKS_INPUT),
            "sha256": _sha256_file(TASKS_INPUT),
        },
        "scoring": {
            "path": _manifest_path(SCORING_INPUT),
            "sha256": _sha256_file(SCORING_INPUT),
        },
        "cleaned_data": {
            "path": _manifest_path(CLEANED_DATA_INPUT),
            "sha256": _sha256_file(CLEANED_DATA_INPUT),
        },
    }
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
                start_time=start_time,
                end_time=end_time,
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
    trace_summaries = _trace_summaries(results)
    effective_mode_counts = _effective_mode_counts(results)
    evidence_backed_count = sum(
        bool(card.evidence) and not card.review_required for card in cards
    )
    degraded_card_count = len(cards) - evidence_backed_count
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
            "warnings": [_redact_text(item) for item in result.warnings],
            "branches": {
                branch.value: outcome.status.value
                for branch, outcome in result.branch_outcomes.items()
            },
        }
        for result in results
    ]
    briefing_paths = _controlled_briefing_paths(output_dir, competitors)
    briefing_files = [
        path.relative_to(output_dir).as_posix()
        for path in briefing_paths.values()
    ]
    all_output_files = [
        "manifest.json",
        "intelligence_cards.json",
        "capability_snapshots.json",
        "workflow_summary.json",
        "trace_summaries.json",
        *briefing_files,
    ]
    manifest = {
        "artifact_version": "week3-baseline-v2",
        "generated_at": generated_at.isoformat(),
        # Keep the old field for existing consumers and make request/effective
        # semantics explicit for v2 readers.
        "analysis_mode": mode,
        "requested_analysis_mode": mode,
        "effective_analysis_mode_counts": effective_mode_counts,
        "scoring_version": snapshots[0].scoring_version if snapshots else None,
        "competitors": competitors,
        "competitor_count": len(competitors),
        "intelligence_card_count": len(cards),
        "evidence_backed_card_count": evidence_backed_count,
        "degraded_card_count": degraded_card_count,
        "snapshot_count": len(snapshots),
        "trace_count": sum(item["trace"] is not None for item in trace_summaries),
        "top_k": top_k,
        "analysis_window": {
            "as_of": end_time.isoformat(),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "window_days": window_days,
        },
        "benchmark_task_count": len(workflow.benchmark_agent.load_tasks()),
        "benchmark_run_count": len(workflow.benchmark_agent.load_runs()),
        "input_sha256": input_sha256,
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
            "traces": "trace_summaries.json",
            "briefings": "briefings/",
            "briefing_files": briefing_files,
            "all": all_output_files,
        },
    }

    manifest_path = output_dir / "manifest.json"
    # A failed regeneration must not leave the previous manifest falsely
    # certifying a partially replaced artifact set.
    _invalidate_commit_marker(manifest_path)
    _write_json(output_dir / "intelligence_cards.json", _model_list(cards))
    _write_json(output_dir / "capability_snapshots.json", _model_list(snapshots))
    _write_json(output_dir / "workflow_summary.json", summary)
    _write_json(output_dir / "trace_summaries.json", trace_summaries)
    _clean_controlled_briefings(briefing_paths.values())
    for result in results:
        assert result.briefing is not None
        _atomic_text(
            briefing_paths[result.request.competitor],
            result.briefing.rstrip() + "\n",
        )
    # The manifest is the commit marker for the artifact set and is written
    # only after every listed output exists.
    #
    # Regenerate the cross-product comparison matrix so it always reflects the
    # freshly-produced capability snapshots.  This avoids the common workflow
    # mistake of re-running generate_week3_baseline without re-running
    # generate_week3_comparison, which would leave stale snapshot IDs in the
    # comparison directory and cause verify_week3_delivery to reject the set.
    # The manifest does not track comparison files — those are verified directly
    # from the comparison/ directory by verify_week3_delivery.
    comparison_dir = output_dir / "comparison"
    comparison_dir.mkdir(parents=True, exist_ok=True)
    try:
        generate_comparison(
            snapshots_path=output_dir / "capability_snapshots.json",
            output_dir=comparison_dir,
            baseline_product="CodeMate Campus",
        )
    except Exception as exc:
        raise RuntimeError(
            f"comparison regeneration failed after baseline write: {exc}"
        ) from exc

    _write_json(manifest_path, manifest)
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
        "--as-of",
        type=_parse_as_of,
        default=None,
        help=(
            "UTC cutoff as YYYY-MM-DD (inclusive through end of day) or an "
            "ISO timestamp ending in Z/+00:00; default is current UTC time."
        ),
    )
    parser.add_argument(
        "--window-days",
        type=_positive_days,
        default=90,
        help="Analysis lookback window in days (default: 90).",
    )
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
        as_of=args.as_of,
        window_days=args.window_days,
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
