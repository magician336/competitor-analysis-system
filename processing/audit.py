"""Deep, traceable quality checks for the structured-document hand-off.

The fast :mod:`scripts.audit_documents` command validates the public Pydantic
contract and prints corpus statistics.  This module performs the more
expensive checks that connect each structured version to its immutable raw
payload.  Results are advisory AI pre-check evidence and deliberately contain
no ``human_*`` fields.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

from pydantic import ValidationError

from schemas.document import RawRecord, StructuredDocument

from .labeling import RuleLabeler
from .normalizers import normalize_text, normalize_url, sha256_bytes, sha256_text


AUDIT_SCHEMA_VERSION = "1.0"
REVIEW_AGENT = "codex-ai-precheck"
DECISIONS = {"PASS", "NEEDS_REVIEW", "FAIL"}

_FAIL = "fail"
_REVIEW = "review"

_MIN_CONTENT_LENGTH: dict[str, int] = {
    "official_page": 200,
    "official_changelog": 80,
    "pricing": 200,
    "product_docs": 120,
    "status_page": 80,
    "github_release": 80,
    "github_issue": 80,
    "plugin_marketplace": 120,
    "community": 80,
    "review": 80,
    "security_privacy": 120,
    "benchmark": 120,
    "rss": 80,
}

_HTML_ARTIFACT_RE = re.compile(
    r"<\s*/?\s*(?:script|style|nav|footer|header|button|input|select|option)\b",
    flags=re.IGNORECASE,
)
_COOKIE_BANNER_RE = re.compile(
    r"\b(?:accept all cookies|manage cookie preferences|cookie settings)\b"
    r"|(?:接受所有|管理|设置)\s*cookie",
    flags=re.IGNORECASE,
)
_NAVIGATION_NOISE_RE = re.compile(
    r"(?:^|\n)\s*(?:skip to content|back to top|返回顶部|跳到主要内容)\s*(?:\n|$)",
    flags=re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I)
# Restrict phone candidates to recognizable layouts.  A permissive
# ``digit + separators`` pattern treats ISO dates, release timestamps, model
# versions, and benchmark identifiers as phone numbers across most records.
_PHONE_RE = re.compile(
    r"(?<![\w+])(?:\+?86[ -]?)?1[3-9]\d{9}(?!\d)"
    r"|(?<!\w)\+\d(?:[ ()-]*\d){7,14}(?!\d)"
    r"|(?<!\d)(?:\(\d{2,4}\)|\d{2,4})[ -]\d{3,4}[ -]\d{3,4}(?!\d)"
)
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{12,}(?!\d)")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
_TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})\b"
)
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:Users|Documents and Settings)\\[^\s\\]+", re.I)


@dataclass(frozen=True, slots=True)
class AuditFinding:
    """One machine-checkable reason attached to an audited document."""

    check_id: str
    severity: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass(slots=True)
class DocumentAudit:
    """Pre-check output for one structured version or one invalid input row."""

    record_key: str
    line_number: int
    version_id: str
    document_id: str = ""
    competitor: str = ""
    source_type: str = ""
    url: str = ""
    raw_record_id: str = ""
    raw_path: str = ""
    current_event_type: str | None = None
    suggested_event_type: str | None = None
    current_dimension_tags: list[str] = field(default_factory=list)
    suggested_dimension_tags: list[str] = field(default_factory=list)
    dimension_evidence: dict[str, list[str]] = field(default_factory=dict)
    sensitive_data_flags: list[str] = field(default_factory=list)
    noise_flags: list[str] = field(default_factory=list)
    findings: list[AuditFinding] = field(default_factory=list)
    review_agent: str = REVIEW_AGENT
    reviewed_at: str = ""

    def add(self, check_id: str, severity: str, message: str) -> None:
        finding = AuditFinding(check_id=check_id, severity=severity, message=message)
        if finding not in self.findings:
            self.findings.append(finding)

    @property
    def overall_decision(self) -> str:
        if any(item.severity == _FAIL for item in self.findings):
            return "FAIL"
        if self.findings:
            return "NEEDS_REVIEW"
        return "PASS"

    @property
    def reason_codes(self) -> list[str]:
        return list(dict.fromkeys(item.check_id for item in self.findings))

    def to_dict(self) -> dict[str, Any]:
        decision = self.overall_decision
        if decision not in DECISIONS:  # defensive contract guard
            raise ValueError(f"Unsupported audit decision: {decision}")
        return {
            "audit_schema_version": AUDIT_SCHEMA_VERSION,
            "record_key": self.record_key,
            "line_number": self.line_number,
            "version_id": self.version_id,
            "document_id": self.document_id,
            "competitor": self.competitor,
            "source_type": self.source_type,
            "url": self.url,
            "raw_record_id": self.raw_record_id,
            "raw_path": self.raw_path,
            "overall_decision": decision,
            "current_event_type": self.current_event_type,
            "suggested_event_type": self.suggested_event_type,
            "current_dimension_tags": self.current_dimension_tags,
            "suggested_dimension_tags": self.suggested_dimension_tags,
            "dimension_evidence": self.dimension_evidence,
            "sensitive_data_flags": self.sensitive_data_flags,
            "noise_flags": self.noise_flags,
            "reason_codes": self.reason_codes,
            "findings": [item.to_dict() for item in self.findings],
            "review_agent": self.review_agent,
            "reviewed_at": self.reviewed_at,
        }


@dataclass(frozen=True, slots=True)
class CorpusAuditResult:
    """Complete in-memory evidence returned by :func:`audit_corpus`."""

    documents_path: Path
    documents_sha256: str
    generated_at: str
    input_line_count: int
    valid_document_count: int
    audits: list[DocumentAudit]

    def summary(self) -> dict[str, Any]:
        decisions = Counter(item.overall_decision for item in self.audits)
        findings = Counter(code for item in self.audits for code in item.reason_codes)
        competitors = Counter(item.competitor for item in self.audits if item.competitor)
        sources = Counter(item.source_type for item in self.audits if item.source_type)
        return {
            "audit_schema_version": AUDIT_SCHEMA_VERSION,
            "review_agent": REVIEW_AGENT,
            "generated_at": self.generated_at,
            "documents_path": str(self.documents_path),
            "documents_sha256": self.documents_sha256,
            "input_line_count": self.input_line_count,
            "audit_record_count": len(self.audits),
            "valid_document_count": self.valid_document_count,
            "decision_counts": {key: decisions.get(key, 0) for key in sorted(DECISIONS)},
            "finding_counts": dict(sorted(findings.items())),
            "competitors": dict(sorted(competitors.items())),
            "sources": dict(sorted(sources.items())),
        }


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _digest_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _evidence_from_reasons(
    document: StructuredDocument,
    rules: Mapping[Any, Sequence[str]],
) -> dict[str, list[str]]:
    """Return exact matched terms, without copying arbitrary document text."""

    haystack = normalize_text(f"{document.title}\n{document.content}", preserve_lines=False).casefold()
    evidence: dict[str, list[str]] = {}
    for tag in document.dimension_tags:
        matches = [
            keyword
            for keyword in rules.get(tag, ())
            if RuleLabeler._matches_keyword(haystack, keyword)
        ]
        if matches:
            evidence[tag.value] = matches[:5]
    return evidence


def _sensitive_flags(content: str) -> list[str]:
    checks = (
        ("EMAIL", _EMAIL_RE),
        ("PHONE", _PHONE_RE),
        ("LONG_NUMBER", _LONG_NUMBER_RE),
        ("PRIVATE_KEY", _PRIVATE_KEY_RE),
        ("TOKEN_CANDIDATE", _TOKEN_RE),
        ("LOCAL_USER_PATH", _WINDOWS_PATH_RE),
    )
    return [name for name, pattern in checks if pattern.search(content)]


def _noise_flags(content: str) -> list[str]:
    result: list[str] = []
    if _HTML_ARTIFACT_RE.search(content):
        result.append("HTML_ARTIFACT")
    if _COOKIE_BANNER_RE.search(content):
        result.append("COOKIE_BANNER")
    if _NAVIGATION_NOISE_RE.search(content):
        result.append("NAVIGATION_TEXT")
    lines = [normalize_text(line, preserve_lines=False).casefold() for line in content.splitlines()]
    repeated = Counter(line for line in lines if len(line) >= 20)
    if any(count >= 3 for count in repeated.values()):
        result.append("REPEATED_LINE")
    return result


def _meta_path(raw_path: Path) -> Path:
    return raw_path.with_name(f"{raw_path.stem}.meta.json")


def _audit_raw_trace(
    audit: DocumentAudit,
    document: StructuredDocument,
    project_root: Path,
) -> None:
    raw_root = (project_root / "data" / "raw").resolve()
    candidate = Path(document.raw_path)
    raw_path = candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
    if not _inside(raw_path, project_root):
        audit.add("RAW_PATH_ESCAPE", _FAIL, "raw_path resolves outside the project root")
        return
    if not _inside(raw_path, raw_root):
        audit.add("RAW_PATH_OUTSIDE_RAW_ROOT", _FAIL, "raw_path is not under data/raw")
    if not raw_path.is_file():
        audit.add("RAW_PAYLOAD_MISSING", _FAIL, "raw payload does not exist")
        return
    if raw_path.stem != document.raw_record_id:
        audit.add("RAW_FILENAME_ID_MISMATCH", _FAIL, "raw payload filename does not match raw_record_id")

    meta_path = _meta_path(raw_path)
    if not meta_path.is_file():
        audit.add("RAW_META_MISSING", _FAIL, "raw sidecar metadata does not exist")
        return
    try:
        record = RawRecord.model_validate_json(meta_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValidationError, ValueError) as exc:
        audit.add("RAW_META_INVALID", _FAIL, f"raw metadata is invalid: {type(exc).__name__}")
        return

    if record.raw_record_id != document.raw_record_id:
        audit.add("RAW_RECORD_ID_MISMATCH", _FAIL, "document and metadata raw_record_id differ")
    if record.source_type != document.source_type:
        audit.add("RAW_SOURCE_TYPE_MISMATCH", _FAIL, "document and metadata source_type differ")
    competitor_id = str(document.source_metadata.get("competitor_id") or "").strip().casefold()
    if competitor_id and competitor_id != record.competitor.strip().casefold():
        audit.add("RAW_COMPETITOR_MISMATCH", _FAIL, "document competitor_id and metadata competitor differ")
    if record.error:
        audit.add("RAW_ACQUISITION_ERROR", _FAIL, "structured document points to an acquisition error")
    if record.http_status is None or not 200 <= record.http_status < 300:
        audit.add("RAW_HTTP_STATUS_INVALID", _FAIL, "structured document does not point to a 2xx response")

    actual_hash = sha256_bytes(raw_path.read_bytes())
    if record.payload_hash != actual_hash:
        audit.add("PAYLOAD_HASH_MISMATCH", _FAIL, "raw payload SHA-256 does not match metadata")
    documented_hash = document.source_metadata.get("payload_hash")
    if documented_hash and documented_hash != record.payload_hash:
        audit.add("DOCUMENT_PAYLOAD_HASH_MISMATCH", _FAIL, "document and metadata payload hashes differ")

    if not record.payload_path:
        audit.add("META_PAYLOAD_PATH_MISSING", _FAIL, "metadata has no payload_path")
    else:
        recorded_path = Path(record.payload_path)
        resolved = (
            recorded_path.resolve()
            if recorded_path.is_absolute()
            else (raw_root / recorded_path).resolve()
        )
        if resolved != raw_path:
            audit.add("META_PAYLOAD_PATH_MISMATCH", _FAIL, "metadata payload_path does not resolve to raw_path")


def _audit_document(
    document: StructuredDocument,
    *,
    line_number: int,
    project_root: Path,
    labeler: RuleLabeler,
    now: datetime,
) -> DocumentAudit:
    current_event = document.event_type.value if document.event_type else None
    recomputed = labeler.label(document.source_type, document.title, document.content)
    suggested_event = recomputed.event_type.value if recomputed.event_type else None
    audit = DocumentAudit(
        record_key=document.version_id,
        line_number=line_number,
        version_id=document.version_id,
        document_id=document.document_id,
        competitor=document.competitor,
        source_type=document.source_type.value,
        url=document.url,
        raw_record_id=document.raw_record_id,
        raw_path=document.raw_path,
        current_event_type=current_event,
        suggested_event_type=suggested_event,
        current_dimension_tags=[item.value for item in document.dimension_tags],
        suggested_dimension_tags=[item.value for item in recomputed.dimension_tags],
        dimension_evidence=_evidence_from_reasons(document, labeler.rules),
        reviewed_at=now.isoformat().replace("+00:00", "Z"),
    )

    if sha256_text(document.content) != document.content_hash:
        audit.add("CONTENT_HASH_MISMATCH", _FAIL, "content SHA-256 does not match content_hash")
    split = urlsplit(document.url)
    if split.scheme not in {"http", "https"} or not split.netloc:
        audit.add("URL_INVALID", _FAIL, "url must be an absolute HTTP(S) URL")
    elif normalize_url(document.url) != document.url:
        audit.add("URL_NOT_CANONICAL", _FAIL, "url is not in canonical form")

    if current_event != suggested_event:
        audit.add("EVENT_TYPE_MISMATCH", _FAIL, "event_type does not match the source mapping")
    if document.dimension_tags != recomputed.dimension_tags:
        audit.add("DIMENSION_LABEL_MISMATCH", _REVIEW, "dimension tags differ from current configured rules")
    if document.label_confidence != recomputed.label_confidence:
        audit.add("LABEL_CONFIDENCE_MISMATCH", _REVIEW, "label confidence differs from current configured rules")
    expected_reasons = list(recomputed.label_reasons)
    if document.source_metadata.get("needs_browser"):
        expected_reasons.append("source:needs_browser")
    if document.label_reasons != expected_reasons:
        audit.add("LABEL_REASONS_MISMATCH", _REVIEW, "label reasons differ from current configured evidence")
    expected_review = recomputed.needs_review or bool(document.source_metadata.get("needs_browser"))
    if document.needs_review != expected_review:
        audit.add("NEEDS_REVIEW_FLAG_MISMATCH", _FAIL, "needs_review is inconsistent with labels and source state")
    if document.dimension_tags and set(audit.dimension_evidence) != set(audit.current_dimension_tags):
        audit.add("LABEL_EVIDENCE_MISSING", _REVIEW, "one or more dimension tags have no configured keyword evidence")
    if not document.dimension_tags:
        audit.add("NO_DIMENSION_LABEL", _REVIEW, "document has no D1-D7 capability label")
    elif recomputed.needs_review:
        audit.add("LABEL_LOW_CONFIDENCE", _REVIEW, "configured label confidence requires semantic review")
    if document.source_metadata.get("needs_browser"):
        audit.add("NEEDS_BROWSER", _REVIEW, "source requires browser-based extraction for fuller evidence")

    minimum = _MIN_CONTENT_LENGTH.get(document.source_type.value, 80)
    if len(document.content) < minimum:
        audit.add(
            "CONTENT_TOO_SHORT",
            _REVIEW,
            f"content length {len(document.content)} is below the {minimum}-character source threshold",
        )
    if document.title == "Untitled document":
        audit.add("UNTITLED_DOCUMENT", _REVIEW, "cleaner supplied the fallback title")

    audit.noise_flags = _noise_flags(document.content)
    for flag in audit.noise_flags:
        audit.add(f"NOISE_{flag}", _REVIEW, f"content contains the {flag.lower()} pattern")
    audit.sensitive_data_flags = _sensitive_flags(f"{document.title}\n{document.content}")
    for flag in audit.sensitive_data_flags:
        audit.add(f"SENSITIVE_{flag}", _REVIEW, f"content contains a {flag.lower()} candidate")

    future_limit = now + timedelta(hours=24)
    for field_name in ("publish_time", "crawl_time", "valid_from", "valid_to"):
        value = getattr(document, field_name)
        if value is not None and _utc(value) > future_limit:
            audit.add("FUTURE_TIMESTAMP", _REVIEW, f"{field_name} is more than 24 hours in the future")
    if document.valid_from and document.valid_to and document.valid_to < document.valid_from:
        audit.add("INVALID_VALIDITY_INTERVAL", _FAIL, "valid_to precedes valid_from")
    if document.is_current and document.valid_to is not None:
        audit.add("CURRENT_VERSION_HAS_VALID_TO", _FAIL, "current version must have null valid_to")
    if not document.is_current and document.valid_to is None:
        audit.add("HISTORICAL_VERSION_MISSING_VALID_TO", _FAIL, "historical version must have valid_to")

    _audit_raw_trace(audit, document, project_root)
    return audit


def _apply_group_checks(
    documents: Sequence[StructuredDocument],
    audits_by_version: Mapping[str, DocumentAudit],
    duplicate_versions: Iterable[str],
) -> None:
    for version_id in duplicate_versions:
        audits_by_version[version_id].add(
            "DUPLICATE_VERSION_ID", _FAIL, "version_id occurs more than once in the JSONL input"
        )

    grouped: dict[str, list[StructuredDocument]] = defaultdict(list)
    for document in documents:
        grouped[document.document_id].append(document)
    for versions in grouped.values():
        current_count = sum(item.is_current for item in versions)
        if current_count != 1:
            code = "MISSING_CURRENT_VERSION" if current_count == 0 else "MULTIPLE_CURRENT_VERSIONS"
            for item in versions:
                audits_by_version[item.version_id].add(
                    code,
                    _FAIL,
                    f"document_id has {current_count} current versions; expected exactly one",
                )
        ordered = sorted(
            versions,
            key=lambda item: (
                item.valid_from or datetime.min.replace(tzinfo=timezone.utc),
                item.version_id,
            ),
        )
        for previous, current in zip(ordered, ordered[1:]):
            if previous.valid_to is None:
                audits_by_version[previous.version_id].add(
                    "VERSION_INTERVAL_OVERLAP", _FAIL, "non-final version has an open-ended validity interval"
                )
                continue
            if current.valid_from and previous.valid_to > current.valid_from:
                for item in (previous, current):
                    audits_by_version[item.version_id].add(
                        "VERSION_INTERVAL_OVERLAP", _FAIL, "validity intervals overlap"
                    )


def audit_corpus(
    documents_path: str | Path,
    *,
    project_root: str | Path,
    labeler: RuleLabeler | None = None,
    now: datetime | None = None,
) -> CorpusAuditResult:
    """Audit every input row and return exactly one result per valid version ID."""

    path = Path(documents_path).resolve()
    root = Path(project_root).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Structured document file does not exist: {path}")
    active_labeler = labeler or RuleLabeler()
    generated_at = _utc(now)
    audits_by_version: dict[str, DocumentAudit] = {}
    invalid_audits: list[DocumentAudit] = []
    documents_by_version: dict[str, StructuredDocument] = {}
    duplicate_versions: set[str] = set()
    input_line_count = 0

    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            input_line_count += 1
            raw_version_id = ""
            try:
                payload = json.loads(line)
                if isinstance(payload, Mapping):
                    raw_version_id = str(payload.get("version_id") or "")
                document = StructuredDocument.model_validate(payload)
            except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
                record_key = f"invalid_line_{line_number:08d}"
                audit = DocumentAudit(
                    record_key=record_key,
                    line_number=line_number,
                    version_id=raw_version_id,
                    reviewed_at=generated_at.isoformat().replace("+00:00", "Z"),
                )
                audit.add("SCHEMA_INVALID", _FAIL, f"input row violates StructuredDocument: {type(exc).__name__}")
                invalid_audits.append(audit)
                continue

            if document.version_id in audits_by_version:
                duplicate_versions.add(document.version_id)
                continue
            audit = _audit_document(
                document,
                line_number=line_number,
                project_root=root,
                labeler=active_labeler,
                now=generated_at,
            )
            audits_by_version[document.version_id] = audit
            documents_by_version[document.version_id] = document

    _apply_group_checks(
        list(documents_by_version.values()),
        audits_by_version,
        duplicate_versions,
    )
    audits = sorted(
        [*audits_by_version.values(), *invalid_audits],
        key=lambda item: (item.line_number, item.record_key),
    )
    return CorpusAuditResult(
        documents_path=path,
        documents_sha256=_digest_file(path),
        generated_at=generated_at.isoformat().replace("+00:00", "Z"),
        input_line_count=input_line_count,
        valid_document_count=len(documents_by_version),
        audits=audits,
    )


__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "DECISIONS",
    "REVIEW_AGENT",
    "AuditFinding",
    "CorpusAuditResult",
    "DocumentAudit",
    "audit_corpus",
]
