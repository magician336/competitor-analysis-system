"""Build or incrementally update the Mini-RAG Elasticsearch index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from mini_rag.api import create_service
from mini_rag.config import load_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", help="Path to documents.jsonl")
    parser.add_argument("--config", help="Path to Mini-RAG YAML config")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Update the current index instead of rebuilding and switching the alias",
    )
    parser.add_argument(
        "--delete-missing",
        action="store_true",
        help="Remove indexed chunks absent from this input during an incremental update",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(PROJECT_ROOT, args.config)
    service = create_service(settings)
    result = service.build_index(
        args.documents,
        rebuild=not args.incremental,
        delete_missing=args.delete_missing,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["report"].get("failed_count", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
