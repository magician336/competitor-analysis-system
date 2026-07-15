"""Command-line and Python entry point for the Mini-RAG evidence service."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from mini_rag.api import create_service
from mini_rag.config import load_settings
from mini_rag.models import RAGQuery


PROJECT_ROOT = Path(__file__).resolve().parent


def _csv_values(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def query_rag(question: str, **kwargs: Any) -> dict[str, Any]:
    settings = load_settings(PROJECT_ROOT)
    service = create_service(settings)
    return service.query(RAGQuery(question=question, **kwargs)).model_dump(mode="json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--config")
    parser.add_argument("--competitor")
    parser.add_argument("--event-types")
    parser.add_argument("--dimension-tags")
    parser.add_argument("--product-versions")
    parser.add_argument("--evidence-levels")
    parser.add_argument("--source-types")
    parser.add_argument("--start-time")
    parser.add_argument("--end-time")
    parser.add_argument(
        "--current-only",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--top-k", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(PROJECT_ROOT, args.config)
    service = create_service(settings)
    values: dict[str, Any] = {}
    for name in ("competitor", "start_time", "end_time", "top_k", "current_only"):
        value = getattr(args, name)
        if value is not None:
            values[name] = value
    for name in (
        "event_types",
        "dimension_tags",
        "product_versions",
        "evidence_levels",
        "source_types",
    ):
        raw_value = getattr(args, name)
        if raw_value is not None:
            values[name] = _csv_values(raw_value)
    request = RAGQuery(question=args.question, **values)
    response = service.query(request)
    print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
