"""Export a deterministic stratified sample of documents needing label review."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Sequence

from mini_rag.config import load_settings
from mini_rag.ingestion import DocumentLoader


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "samples" / "标签人工复核.csv"


def stratified_sample(documents: Sequence, limit: int) -> list:
    groups: dict[tuple[str, str], deque] = defaultdict(deque)
    for document in sorted(
        (item for item in documents if item.needs_review),
        key=lambda item: (item.competitor, item.source_type.value, item.document_id),
    ):
        groups[(document.competitor, document.source_type.value)].append(document)
    selected = []
    active = sorted(groups)
    while active and len(selected) < limit:
        remaining = []
        for key in active:
            if len(selected) >= limit:
                break
            selected.append(groups[key].popleft())
            if groups[key]:
                remaining.append(key)
        active = remaining
    return selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Mini-RAG YAML path")
    parser.add_argument("--output", help="Review CSV path")
    parser.add_argument("--limit", type=int, default=40, help="Maximum review rows")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 1:
        raise ValueError("limit must be positive")
    settings = load_settings(PROJECT_ROOT, args.config)
    documents = DocumentLoader(settings.documents_path).load()
    selected = stratified_sample(documents, args.limit)
    output = Path(args.output).resolve() if args.output else DEFAULT_OUTPUT
    fields = (
        "document_id", "competitor", "source_type", "title", "event_type",
        "current_dimension_tags", "label_confidence", "label_reasons",
        "content_excerpt", "url", "raw_path", "human_event_type",
        "human_dimension_tags", "review_decision", "reviewer", "reviewed_at", "notes",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for document in selected:
            writer.writerow(
                {
                    "document_id": document.document_id,
                    "competitor": document.competitor,
                    "source_type": document.source_type.value,
                    "title": document.title,
                    "event_type": document.event_type.value if document.event_type else "",
                    "current_dimension_tags": json.dumps(
                        [item.value for item in document.dimension_tags], ensure_ascii=False
                    ),
                    "label_confidence": document.label_confidence,
                    "label_reasons": json.dumps(document.label_reasons, ensure_ascii=False),
                    "content_excerpt": document.content[:1200],
                    "url": document.url,
                    "raw_path": document.raw_path,
                    "human_event_type": "",
                    "human_dimension_tags": "",
                    "review_decision": "",
                    "reviewer": "",
                    "reviewed_at": "",
                    "notes": "",
                }
            )
    print(
        json.dumps(
            {
                "needs_review_total": sum(item.needs_review for item in documents),
                "sample_count": len(selected),
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
