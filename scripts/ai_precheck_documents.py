"""Run the reusable full-corpus AI pre-check and persist inspectable evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from mini_rag.config import load_settings
from processing.audit import AUDIT_SCHEMA_VERSION, REVIEW_AGENT, audit_corpus
from processing.labeling import RuleLabeler


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "data" / "runtime" / "ai_precheck"
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _write_review_queue(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    fields = (
        "version_id",
        "document_id",
        "competitor",
        "source_type",
        "overall_decision",
        "reason_codes",
        "raw_path",
        "url",
        "review_agent",
        "reviewed_at",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            if row["overall_decision"] == "PASS":
                continue
            writer.writerow(
                {
                    "version_id": row["version_id"],
                    "document_id": row["document_id"],
                    "competitor": row["competitor"],
                    "source_type": row["source_type"],
                    "overall_decision": row["overall_decision"],
                    "reason_codes": json.dumps(row["reason_codes"], ensure_ascii=False),
                    "raw_path": row["raw_path"],
                    "url": row["url"],
                    "review_agent": row["review_agent"],
                    "reviewed_at": row["reviewed_at"],
                }
            )


def _default_run_id(now: datetime, documents_sha256: str) -> str:
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{documents_sha256.removeprefix('sha256:')[:8]}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", help="Structured documents JSONL path")
    parser.add_argument("--config", help="Mini-RAG YAML path used to locate documents")
    parser.add_argument("--dimensions", help="D1-D7 rule YAML path")
    parser.add_argument("--output-root", help="Parent directory for audit-run folders")
    parser.add_argument("--audit-run-id", help="Stable output folder name")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(PROJECT_ROOT, args.config)
    documents_path = (
        Path(args.documents).resolve() if args.documents else settings.documents_path.resolve()
    )
    dimensions_path = (
        Path(args.dimensions).resolve()
        if args.dimensions
        else PROJECT_ROOT / "config" / "dimensions.yaml"
    )
    labeler = RuleLabeler.from_yaml(dimensions_path) if dimensions_path.is_file() else RuleLabeler()
    now = datetime.now(timezone.utc)
    result = audit_corpus(
        documents_path,
        project_root=PROJECT_ROOT,
        labeler=labeler,
        now=now,
    )
    run_id = args.audit_run_id or _default_run_id(now, result.documents_sha256)
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError("audit_run_id may contain only letters, digits, dot, underscore, and dash")
    output_root = Path(args.output_root).resolve() if args.output_root else DEFAULT_RUNTIME_ROOT
    output_dir = output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    document_rows = [item.to_dict() for item in result.audits]
    documents_output = output_dir / "documents.audit.jsonl"
    queue_output = output_dir / "review-queue.csv"
    summary_output = output_dir / "summary.json"
    manifest_output = output_dir / "manifest.json"
    _write_jsonl(documents_output, document_rows)
    _write_review_queue(queue_output, document_rows)
    summary = result.summary()
    summary.update({"audit_run_id": run_id, "output_directory": str(output_dir)})
    _write_json(summary_output, summary)
    manifest = {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "audit_run_id": run_id,
        "generated_at": result.generated_at,
        "review_agent": REVIEW_AGENT,
        "acceptance_boundary": "AI pre-check only; independent human acceptance remains required.",
        "documents_path": str(result.documents_path),
        "documents_sha256": result.documents_sha256,
        "dimensions_path": str(dimensions_path),
        "dimensions_sha256": _sha256(dimensions_path) if dimensions_path.is_file() else None,
        "input_line_count": result.input_line_count,
        "valid_document_count": result.valid_document_count,
        "audit_record_count": len(result.audits),
        "outputs": {
            "documents_audit": documents_output.name,
            "review_queue": queue_output.name,
            "summary": summary_output.name,
        },
        "output_sha256": {
            "documents_audit": _sha256(documents_output),
            "review_queue": _sha256(queue_output),
            "summary": _sha256(summary_output),
        },
        "limitations": [
            "This is an AI pre-check and does not replace human acceptance.",
            "Sensitive-data flags are pattern candidates and require contextual review.",
            "Keyword label evidence does not prove semantic completeness.",
        ],
    }
    _write_json(manifest_output, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 1 if summary["decision_counts"]["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
