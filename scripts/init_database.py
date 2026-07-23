"""Explicit database seed and approved artifact import command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import text

from backend.artifact_import import initialize_database
from backend.config import PROJECT_ROOT
from backend.codemate_seed import seed_codemate_snapshot
from backend.database import ALEMBIC_HEAD_REVISION, get_database
from backend.repositories import AnalysisRepository


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed-codemate",
        action="store_true",
        help="idempotently seed the product-definition CodeMate snapshot",
    )
    parser.add_argument(
        "--seed-competitors",
        action="store_true",
        help="idempotently seed config/competitors.yaml",
    )
    parser.add_argument(
        "--import-artifacts",
        type=Path,
        metavar="DIRECTORY",
        help="import only the non-Benchmark files declared by manifest.json",
    )
    return parser.parse_args()


def _require_current_schema() -> None:
    database = get_database()
    try:
        with database.engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception as exc:
        raise SystemExit(
            "database schema is missing; run `python -m alembic upgrade head` first"
        ) from exc
    if revision != ALEMBIC_HEAD_REVISION:
        raise SystemExit(
            f"database revision is {revision!r}; expected {ALEMBIC_HEAD_REVISION!r}"
        )


def main() -> int:
    args = _parse_args()
    if not args.seed_competitors and not args.seed_codemate and args.import_artifacts is None:
        raise SystemExit(
            "select --seed-competitors, --seed-codemate and/or --import-artifacts"
        )
    _require_current_schema()
    repository = AnalysisRepository()
    result = initialize_database(
        repository,
        competitor_config=(
            PROJECT_ROOT / "config" / "competitors.yaml"
            if args.seed_competitors
            else None
        ),
        artifact_root=args.import_artifacts,
    )
    payload = result.model_dump(mode="json")
    if args.seed_codemate:
        snapshot = seed_codemate_snapshot(repository)
        payload["codemate_snapshot_id"] = snapshot.snapshot_id
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
