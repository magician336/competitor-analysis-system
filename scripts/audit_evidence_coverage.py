"""Audit Mini-RAG evidence coverage for competitor and event-type pairs."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from mini_rag.config import load_settings
from mini_rag.ingestion import DocumentLoader


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPETITORS = ("Cursor", "GitHub Copilot")
DEFAULT_EVENT_TYPES = ("pricing_change", "risk_experience")


def _value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _parse_as_of(value: str) -> datetime:
    raw = value.strip()
    if not raw:
        raise argparse.ArgumentTypeError("--as-of must not be blank")
    if len(raw) == 10:
        try:
            return datetime.combine(date.fromisoformat(raw), time.max, tzinfo=timezone.utc)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid --as-of date: {value}") from exc
    normalized = raw[:-1] + "+00:00" if raw.upper().endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid --as-of timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--as-of timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _positive_days(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--window-days must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("--window-days must be positive")
    return parsed


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _resolve_competitors(requested: Iterable[str], available: Iterable[str]) -> list[str]:
    names = list(dict.fromkeys(item for item in available if item))
    aliases = {
        name.casefold(): name
        for name in names
    }
    aliases.update(
        {
            name.casefold().replace(" ", "_"): name
            for name in names
        }
    )
    selected: list[str] = []
    unknown: list[str] = []
    for item in requested:
        canonical = aliases.get(item.casefold())
        if canonical is None:
            unknown.append(item)
        elif canonical not in selected:
            selected.append(canonical)
    if unknown:
        raise ValueError("unknown competitors: " + ", ".join(unknown))
    return selected


def _effective_time(document: Any) -> tuple[datetime | None, str | None]:
    publish_time = getattr(document, "publish_time", None)
    if publish_time is not None:
        return publish_time, "publish_time"
    valid_from = getattr(document, "valid_from", None)
    if valid_from is not None:
        return valid_from, "valid_from"
    return None, None


def build_coverage(
    documents: Sequence[Any],
    *,
    competitors: Sequence[str],
    event_types: Sequence[str],
    start_time: datetime,
    end_time: datetime,
    current_only: bool = True,
    sample_limit: int = 3,
) -> dict[str, Any]:
    coverage: list[dict[str, Any]] = []
    for competitor in competitors:
        for event_type in event_types:
            matching = [
                document
                for document in documents
                if _value(getattr(document, "competitor", None)) == competitor
                and _value(getattr(document, "event_type", None)) == event_type
                and (not current_only or bool(getattr(document, "is_current", False)))
            ]
            in_window: list[tuple[Any, datetime, str]] = []
            for document in matching:
                effective_time, time_basis = _effective_time(document)
                if (
                    effective_time is not None
                    and time_basis is not None
                    and start_time <= effective_time <= end_time
                ):
                    in_window.append((document, effective_time, time_basis))
            evidence_levels = Counter(
                _value(getattr(document, "evidence_level", None))
                for document, _, _ in in_window
            )
            source_types = Counter(
                _value(getattr(document, "source_type", None))
                for document, _, _ in in_window
            )
            samples = [
                {
                    "title": _value(getattr(document, "title", None)),
                    "url": _value(getattr(document, "url", None)),
                    "source_type": _value(getattr(document, "source_type", None)),
                    "evidence_level": _value(getattr(document, "evidence_level", None)),
                    "effective_time": effective_time.isoformat(),
                    "time_basis": time_basis,
                }
                for document, effective_time, time_basis in sorted(
                    in_window,
                    key=lambda item: item[1],
                    reverse=True,
                )[:sample_limit]
            ]
            coverage.append(
                {
                    "competitor": competitor,
                    "event_type": event_type,
                    "status": "covered" if in_window else "gap",
                    "matching_documents": len(matching),
                    "in_window_documents": len(in_window),
                    "publish_time_documents": sum(
                        getattr(document, "publish_time", None) is not None
                        for document, _, _ in in_window
                    ),
                    "valid_from_fallback_documents": sum(
                        basis == "valid_from" for _, _, basis in in_window
                    ),
                    "evidence_levels": dict(sorted(evidence_levels.items())),
                    "source_types": dict(sorted(source_types.items())),
                    "samples": samples,
                }
            )
    gap_count = sum(item["status"] == "gap" for item in coverage)
    return {
        "window": {
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        },
        "current_only": current_only,
        "coverage": coverage,
        "covered_count": len(coverage) - gap_count,
        "gap_count": gap_count,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path)
    parser.add_argument(
        "--competitors",
        default=",".join(DEFAULT_COMPETITORS),
        help="Comma-separated competitor names or IDs.",
    )
    parser.add_argument(
        "--event-types",
        default=",".join(DEFAULT_EVENT_TYPES),
        help="Comma-separated event types.",
    )
    parser.add_argument(
        "--as-of",
        type=_parse_as_of,
        default=None,
        help="UTC cutoff as YYYY-MM-DD or an ISO timestamp.",
    )
    parser.add_argument(
        "--window-days",
        type=_positive_days,
        default=90,
        help="Lookback window in days (default: 90).",
    )
    parser.add_argument(
        "--include-history",
        action="store_true",
        help="Include superseded document versions.",
    )
    parser.add_argument("--sample-limit", type=int, choices=range(0, 11), default=3)
    parser.add_argument("--config")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(PROJECT_ROOT, args.config)
    path = args.documents or settings.documents_path
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    documents = DocumentLoader(path).load()
    end_time = args.as_of or datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=args.window_days)
    available_competitors = [
        _value(getattr(document, "competitor", None))
        for document in documents
    ]
    requested = _csv(args.competitors)
    if len(requested) == 1 and requested[0].casefold() == "all":
        competitors = sorted(set(available_competitors))
    else:
        competitors = _resolve_competitors(requested, available_competitors)
    report = build_coverage(
        documents,
        competitors=competitors,
        event_types=_csv(args.event_types),
        start_time=start_time,
        end_time=end_time,
        current_only=not args.include_history,
        sample_limit=args.sample_limit,
    )
    report["documents"] = str(path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["gap_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
