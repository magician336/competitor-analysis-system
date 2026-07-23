"""Run the week-three E1--E3 / D1--D7 DimensionTaggingAgent.

Examples::

    python -m scripts.tag_dimensions single --mode rules \
        --source-type official_changelog --title "Agent update" \
        --content "Repository context is now supported."

    python -m scripts.tag_dimensions batch --mode rules \
        --input requests.jsonl --output tagging_results.jsonl

The command is deliberately independent from the second-week processing
pipeline.  Batch output replaces its destination only after every input line
has parsed, validated and completed successfully.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents.dimension_tagging_agent import DimensionTaggingAgent
from schemas.document import SourceType
from schemas.tagging import (
    DimensionTaggingRequest,
    DimensionTaggingResult,
    TagMergeStrategy,
)


AGENT_MODES = ("rules", "hybrid", "llm")


class DimensionCLIError(RuntimeError):
    """Expected input, configuration or Agent execution failure."""


def _add_mode_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mode",
        choices=AGENT_MODES,
        default="rules",
        help=(
            "rules is fully offline; hybrid and llm use the configured "
            "LangChain model while retaining rule/Agent audit fields"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    single = commands.add_parser(
        "single",
        help="Classify one title/content pair and print one JSON object.",
    )
    _add_mode_argument(single)
    single.add_argument(
        "--source-type",
        required=True,
        choices=[item.value for item in SourceType],
    )
    single.add_argument("--title", required=True)
    single.add_argument("--content", required=True)
    single.add_argument(
        "--strategy",
        choices=[item.value for item in TagMergeStrategy],
        default=TagMergeStrategy.UNION.value,
    )
    single.add_argument("--correlation-id")

    batch = commands.add_parser(
        "batch",
        help="Classify JSONL requests and atomically replace one result JSONL.",
    )
    _add_mode_argument(batch)
    batch.add_argument("--input", type=Path, required=True, help="Input JSONL path")
    batch.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output JSONL path; replaced only after the full batch succeeds",
    )
    return parser


def _build_agent(mode: str) -> DimensionTaggingAgent:
    os.environ["CODERADAR_AGENT_MODE"] = mode
    if mode == "rules":
        return DimensionTaggingAgent()
    agent = DimensionTaggingAgent.from_env()
    if agent.llm_client is None:
        raise DimensionCLIError(
            f"--mode {mode} requires a non-empty DEEPSEEK_API_KEY and a valid "
            "LangChain model configuration"
        )
    return agent


def _strict_result(value: Any) -> DimensionTaggingResult:
    return (
        value
        if isinstance(value, DimensionTaggingResult)
        else DimensionTaggingResult.model_validate(value)
    )


def _run_one(
    agent: DimensionTaggingAgent,
    request: DimensionTaggingRequest,
    *,
    context: str,
) -> DimensionTaggingResult:
    try:
        return _strict_result(agent.run(request))
    except Exception as exc:
        raise DimensionCLIError(
            f"{context}: DimensionTaggingAgent failed: {type(exc).__name__}: {exc}"
        ) from exc


def _load_jsonl(path: Path) -> list[tuple[int, DimensionTaggingRequest]]:
    if not path.is_file():
        raise DimensionCLIError(f"input JSONL does not exist or is not a file: {path}")
    records: list[tuple[int, DimensionTaggingRequest]] = []
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, raw_line in enumerate(handle, 1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DimensionCLIError(
                        f"input line {line_number}: invalid JSON: {exc.msg}"
                    ) from exc
                if not isinstance(payload, dict):
                    raise DimensionCLIError(
                        f"input line {line_number}: expected one JSON object"
                    )
                try:
                    request = DimensionTaggingRequest.model_validate(payload)
                except ValidationError as exc:
                    raise DimensionCLIError(
                        f"input line {line_number}: invalid tagging request: {exc}"
                    ) from exc
                records.append((line_number, request))
    except UnicodeError as exc:
        raise DimensionCLIError(f"input JSONL must be UTF-8: {path}") from exc
    except OSError as exc:
        raise DimensionCLIError(f"cannot read input JSONL {path}: {exc}") from exc
    if not records:
        raise DimensionCLIError(f"input JSONL contains no request objects: {path}")
    return records


def _render_jsonl(results: Iterable[DimensionTaggingResult]) -> str:
    return "".join(
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for result in results
    )


def _atomic_replace_text(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DimensionCLIError(f"cannot create output directory {path.parent}: {exc}") from exc
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
    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise DimensionCLIError(f"cannot atomically replace output JSONL {path}: {exc}") from exc


def _single(args: argparse.Namespace, agent: DimensionTaggingAgent) -> int:
    request = DimensionTaggingRequest(
        source_type=args.source_type,
        title=args.title,
        content=args.content,
        strategy=args.strategy,
        correlation_id=args.correlation_id,
    )
    result = _run_one(agent, request, context="single request")
    print(result.model_dump_json(indent=2))
    return 0


def _batch(args: argparse.Namespace, agent: DimensionTaggingAgent) -> int:
    records = _load_jsonl(args.input)
    results = [
        _run_one(agent, request, context=f"input line {line_number}")
        for line_number, request in records
    ]
    _atomic_replace_text(args.output, _render_jsonl(results))
    print(
        json.dumps(
            {
                "mode": args.mode,
                "processed": len(results),
                "output": str(args.output),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _safe_error(exc: Exception) -> str:
    message = " ".join(str(exc).split()) or "unknown error"
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if api_key:
        message = message.replace(api_key, "<redacted>")
    return message


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        agent = _build_agent(args.mode)
        if args.command == "single":
            return _single(args, agent)
        if args.command == "batch":
            return _batch(args, agent)
        raise DimensionCLIError(f"unsupported command: {args.command}")
    except (DimensionCLIError, ValidationError, ValueError, OSError) as exc:
        print(f"tag_dimensions error: {_safe_error(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AGENT_MODES",
    "DimensionCLIError",
    "build_parser",
    "main",
]
