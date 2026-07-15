"""Run the labelled Mini-RAG retrieval evaluation set."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Sequence

from mini_rag.api import create_service
from mini_rag.config import load_settings
from mini_rag.evaluation import load_evaluation_cases, validate_evaluation_cases


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _provenance(dataset: Path, config_argument: str | None) -> dict[str, object]:
    dataset = dataset.resolve()
    configured = config_argument or os.getenv("MINIRAG_CONFIG_PATH", "config/mini_rag.yaml")
    config_path = Path(configured)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    config_path = config_path.resolve()
    manifest_path = dataset.with_name("评测集说明.json")
    manifest: dict[str, object] = {}
    if manifest_path.is_file():
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            manifest = loaded
    return {
        "dataset_path": str(dataset),
        "dataset_sha256": _sha256(dataset),
        "human_review_status": manifest.get("human_review_status", "unknown"),
        "label_origin": manifest.get("label_origin", "unknown"),
        "config_path": str(config_path),
        "config_sha256": _sha256(config_path) if config_path.is_file() else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", help="Evaluation CSV path")
    parser.add_argument("--output", help="Result JSON path")
    parser.add_argument("--config", help="Mini-RAG YAML path")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write the result file without printing the full JSON payload",
    )
    parser.add_argument(
        "--ablations",
        action="store_true",
        help="Run all six fixed A-F retrieval ablations",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(PROJECT_ROOT, args.config)
    dataset = Path(args.dataset) if args.dataset else settings.evaluation_path
    cases = load_evaluation_cases(dataset)
    validate_evaluation_cases(cases)
    service = create_service(settings)
    result = service.evaluate_ablations(cases) if args.ablations else service.evaluate(cases)
    provenance = _provenance(dataset, args.config)
    if isinstance(result, dict):
        for value in result.values():
            if hasattr(value, "config"):
                value.config.update(provenance)
        payload = {
            key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            for key, value in result.items()
        }
    else:
        if hasattr(result, "config"):
            result.config.update(provenance)
        payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            output = PROJECT_ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    if not args.quiet:
        print(rendered)
    if isinstance(payload, dict) and args.ablations:
        success_rates = [
            float(item.get("metrics", {}).get("query_success_rate", 0.0))
            for item in payload.values()
        ]
        return 0 if success_rates and all(rate == 1.0 for rate in success_rates) else 1
    success_rate = float(payload.get("metrics", {}).get("query_success_rate", 0.0))
    return 0 if success_rate == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
