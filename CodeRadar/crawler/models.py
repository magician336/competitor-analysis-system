"""Core data contracts shared by the crawler components.

The crawler deliberately keeps these models independent from the application's
Pydantic document schemas.  Raw records are the boundary between acquisition
and processing; the processing package can therefore evolve without coupling
network code to a particular structured-document version.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


class SourceType(str, Enum):
    """Supported acquisition sources.

    ``GITHUB`` is a task-level selector.  Persisted GitHub records use either
    ``GITHUB_RELEASE`` or ``GITHUB_ISSUE``.
    """

    OFFICIAL = "official"
    CHANGELOG = "changelog"
    PRICING = "pricing"
    GITHUB = "github"
    GITHUB_RELEASE = "github_release"
    GITHUB_ISSUE = "github_issue"

    @classmethod
    def parse(cls, value: "SourceType | str") -> "SourceType":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower().replace("-", "_")
        aliases = {
            "website": cls.OFFICIAL,
            "homepage": cls.OFFICIAL,
            "release": cls.GITHUB_RELEASE,
            "releases": cls.GITHUB_RELEASE,
            "issue": cls.GITHUB_ISSUE,
            "issues": cls.GITHUB_ISSUE,
        }
        if normalized in aliases:
            return aliases[normalized]
        return cls(normalized)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class CollectorTask:
    """A normalized unit of collector work."""

    competitor: str
    source_type: SourceType
    urls: tuple[str, ...] = ()
    repositories: tuple[str, ...] = ()
    enabled: bool = True
    format: str = "html"
    since: datetime | None = None
    max_issues: int = 100
    max_comments: int = 20
    force: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_type", SourceType.parse(self.source_type))
        object.__setattr__(self, "urls", tuple(url for url in self.urls if url))
        object.__setattr__(
            self,
            "repositories",
            tuple(repo.strip().strip("/") for repo in self.repositories if repo),
        )
        if self.max_issues < 0 or self.max_comments < 0:
            raise ValueError("max_issues and max_comments must be non-negative")

    @classmethod
    def from_mapping(
        cls,
        competitor: str,
        source_type: SourceType | str,
        value: Mapping[str, Any] | str | Sequence[str],
        **overrides: Any,
    ) -> "CollectorTask":
        """Build a task from common YAML source shapes."""

        if isinstance(value, str):
            data: dict[str, Any] = {"url": value}
        elif isinstance(value, Mapping):
            data = dict(value)
        else:
            data = {"urls": list(value)}

        raw_urls = data.pop("urls", None)
        if raw_urls is None:
            raw_url = data.pop("url", None)
            raw_urls = [raw_url] if raw_url else []
        elif isinstance(raw_urls, str):
            raw_urls = [raw_urls]

        raw_repositories = data.pop("repositories", data.pop("repos", []))
        if isinstance(raw_repositories, str):
            raw_repositories = [raw_repositories]
        repository = data.pop("repository", data.pop("repo", None))
        if repository:
            raw_repositories = [*raw_repositories, repository]

        known = {
            "enabled": data.pop("enabled", True),
            "format": data.pop("format", data.pop("type", "html")),
            "max_issues": data.pop("max_issues", 100),
            "max_comments": data.pop("max_comments", 20),
            "force": data.pop("force", False),
        }
        known.update(overrides)
        return cls(
            competitor=competitor,
            source_type=SourceType.parse(source_type),
            urls=tuple(raw_urls),
            repositories=tuple(raw_repositories),
            metadata=data,
            **known,
        )


@dataclass(slots=True)
class RawRecord:
    """Metadata describing one persisted raw payload or acquisition event."""

    raw_record_id: str
    crawl_run_id: str
    competitor: str
    source_type: SourceType
    requested_url: str
    canonical_url: str
    final_url: str | None
    fetched_at: datetime
    published_at: datetime | None = None
    http_status: int | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    payload_path: str | None = None
    payload_hash: str | None = None
    error: str | None = None
    needs_browser: bool = False
    source_metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source_type"] = self.source_type.value
        result["fetched_at"] = isoformat_utc(self.fetched_at)
        result["published_at"] = isoformat_utc(self.published_at)
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RawRecord":
        values = dict(data)
        values["source_type"] = SourceType.parse(values["source_type"])
        for field_name in ("fetched_at", "published_at"):
            value = values.get(field_name)
            if value:
                values[field_name] = datetime.fromisoformat(
                    str(value).replace("Z", "+00:00")
                )
        return cls(**values)


@dataclass(slots=True)
class CollectorResult:
    """Outcome of one collector task; errors do not abort sibling tasks."""

    competitor: str
    source_type: SourceType
    records: list[RawRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(
            1
            for record in self.records
            if record.error is None and record.http_status != 304
        )

    @property
    def unchanged_count(self) -> int:
        return sum(1 for record in self.records if record.http_status == 304)

    @property
    def failure_count(self) -> int:
        # ``add_error`` retains both a human-readable message and a traceable
        # metadata record for the same event. Count that pair once.
        error_record_count = sum(1 for record in self.records if record.error)
        return max(len(self.errors), error_record_count)

    def add_error(self, message: str, record: RawRecord | None = None) -> None:
        self.errors.append(message)
        if record is not None:
            self.records.append(record)


@dataclass(slots=True)
class CrawlSummary:
    """Aggregate result returned by :class:`CrawlOrchestrator`."""

    crawl_run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    results: list[CollectorResult] = field(default_factory=list)
    planned_tasks: list[CollectorTask] = field(default_factory=list)
    configuration_errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def success_count(self) -> int:
        return sum(result.success_count for result in self.results)

    @property
    def unchanged_count(self) -> int:
        return sum(result.unchanged_count for result in self.results)

    @property
    def failure_count(self) -> int:
        return len(self.configuration_errors) + sum(
            result.failure_count for result in self.results
        )

    @property
    def exit_code(self) -> int:
        if self.configuration_errors:
            return 1
        if self.failure_count:
            completed_source = any(
                result.failure_count == 0
                or result.success_count > 0
                or result.unchanged_count > 0
                for result in self.results
            )
            return 2 if completed_source else 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "crawl_run_id": self.crawl_run_id,
            "started_at": isoformat_utc(self.started_at),
            "finished_at": isoformat_utc(self.finished_at),
            "dry_run": self.dry_run,
            "success_count": self.success_count,
            "unchanged_count": self.unchanged_count,
            "failure_count": self.failure_count,
            "configuration_errors": list(self.configuration_errors),
            "results": [
                {
                    "competitor": result.competitor,
                    "source_type": result.source_type.value,
                    "success_count": result.success_count,
                    "unchanged_count": result.unchanged_count,
                    "failure_count": result.failure_count,
                    "skipped": list(result.skipped),
                    "errors": list(result.errors),
                }
                for result in self.results
            ],
        }


def coerce_path(value: str | Path) -> Path:
    return value if isinstance(value, Path) else Path(value)
