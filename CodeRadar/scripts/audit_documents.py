"""Validate and summarize the structured-document hand-off."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from mini_rag.config import load_settings
from mini_rag.ingestion import DocumentLoader


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents")
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    settings = load_settings(PROJECT_ROOT, args.config)
    path = Path(args.documents) if args.documents else settings.documents_path
    documents = DocumentLoader(path).load()
    statistics = DocumentLoader.statistics(documents)
    print(json.dumps(asdict(statistics), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
