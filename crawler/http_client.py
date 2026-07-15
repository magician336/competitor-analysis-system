"""Shared synchronous HTTP client for all collectors."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


LOGGER = logging.getLogger(__name__)


class RobotsDeniedError(PermissionError):
    """Raised when a URL is disallowed by the origin's robots.txt."""


@dataclass(frozen=True, slots=True)
class HttpClientConfig:
    timeout_seconds: float = 15.0
    retries: int = 3
    backoff_factor: float = 0.5
    requests_per_second: float = 1.0
    user_agent: str = "CodeRadarBot/1.0"
    trust_environment: bool = True
    respect_robots_txt: bool = True
    robots_cache_seconds: float = 3600.0
    robots_fail_open: bool = True

    @property
    def minimum_interval_seconds(self) -> float:
        if self.requests_per_second <= 0:
            return 0.0
        return 1.0 / self.requests_per_second


def canonical_request_url(url: str) -> str:
    """Normalize a URL for conditional-request cache lookup."""

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)), doseq=True)
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, query, ""))


class ConditionalRequestStore:
    """Small persistent ETag/Last-Modified cache with atomic updates."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._entries = {
                    str(key): dict(value)
                    for key, value in raw.items()
                    if isinstance(value, Mapping)
                }
        except (OSError, ValueError, TypeError):
            LOGGER.warning("Ignoring invalid conditional request cache: %s", self.path)

    def get_headers(self, url: str) -> dict[str, str]:
        with self._lock:
            entry = self._entries.get(canonical_request_url(url), {})
            headers: dict[str, str] = {}
            if entry.get("etag"):
                headers["If-None-Match"] = entry["etag"]
            if entry.get("last_modified"):
                headers["If-Modified-Since"] = entry["last_modified"]
            return headers

    def update(self, url: str, headers: Mapping[str, str]) -> None:
        etag = headers.get("ETag") or headers.get("etag")
        last_modified = headers.get("Last-Modified") or headers.get("last-modified")
        if not etag and not last_modified:
            return
        with self._lock:
            self._entries[canonical_request_url(url)] = {
                "etag": etag or "",
                "last_modified": last_modified or "",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._flush()

    def _flush(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._entries, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)


class DomainRateLimiter:
    """Thread-safe, per-origin minimum interval limiter."""

    def __init__(
        self,
        minimum_interval_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.minimum_interval_seconds = max(0.0, minimum_interval_seconds)
        self._clock = clock
        self._sleeper = sleeper
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        if self.minimum_interval_seconds == 0:
            return
        parts = urlsplit(url)
        origin = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
        with self._lock:
            now = self._clock()
            previous = self._last_request.get(origin)
            if previous is not None:
                delay = self.minimum_interval_seconds - (now - previous)
                if delay > 0:
                    self._sleeper(delay)
                    now = self._clock()
            self._last_request[origin] = now


class HttpClient:
    """Requests-based client with retry, throttling and robots enforcement."""

    def __init__(
        self,
        config: HttpClientConfig | None = None,
        *,
        session: requests.Session | None = None,
        condition_store: ConditionalRequestStore | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or HttpClientConfig()
        self.session = session or requests.Session()
        self.session.trust_env = self.config.trust_environment
        self.session.headers.update(
            {
                "User-Agent": self.config.user_agent,
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self._configure_retries()
        self.condition_store = condition_store or ConditionalRequestStore()
        self.rate_limiter = DomainRateLimiter(
            self.config.minimum_interval_seconds,
            clock=clock,
            sleeper=sleeper,
        )
        self._robots: dict[str, tuple[float, RobotFileParser]] = {}
        self._robots_lock = threading.RLock()
        self._clock = clock

    def _configure_retries(self) -> None:
        retry = Retry(
            total=self.config.retries,
            connect=self.config.retries,
            read=self.config.retries,
            status=self.config.retries,
            allowed_methods=frozenset({"GET", "HEAD"}),
            status_forcelist=frozenset({429, 500, 502, 503, 504}),
            backoff_factor=self.config.backoff_factor,
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _prepared_url(
        self, url: str, params: Mapping[str, Any] | None
    ) -> str:
        return requests.Request("GET", url, params=params).prepare().url or url

    def _robots_parser(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
        with self._robots_lock:
            cached = self._robots.get(origin)
            if cached and (self._clock() - cached[0]) < self.config.robots_cache_seconds:
                return cached[1]

            robots_url = f"{origin}/robots.txt"
            parser = RobotFileParser(robots_url)
            try:
                self.rate_limiter.wait(robots_url)
                response = self.session.get(
                    robots_url,
                    timeout=self.config.timeout_seconds,
                    allow_redirects=True,
                    headers={"Accept": "text/plain,*/*;q=0.1"},
                )
                if response.status_code in (401, 403):
                    parser.disallow_all = True
                elif 200 <= response.status_code < 300:
                    parser.parse(response.text.splitlines())
                else:
                    parser.allow_all = self.config.robots_fail_open
            except requests.RequestException as exc:
                LOGGER.warning("robots.txt request failed for %s: %s", origin, exc)
                parser.allow_all = self.config.robots_fail_open
            self._robots[origin] = (self._clock(), parser)
            return parser

    def can_fetch(self, url: str) -> bool:
        if not self.config.respect_robots_txt:
            return True
        return self._robots_parser(url).can_fetch(self.config.user_agent, url)

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        conditional: bool = True,
        force: bool = False,
        check_robots: bool = True,
    ) -> requests.Response:
        request_url = self._prepared_url(url, params)
        if check_robots and not self.can_fetch(request_url):
            raise RobotsDeniedError(f"robots.txt disallows {request_url}")

        request_headers = dict(headers or {})
        if conditional and not force:
            for key, value in self.condition_store.get_headers(request_url).items():
                request_headers.setdefault(key, value)

        self.rate_limiter.wait(request_url)
        response = self.session.get(
            url,
            params=params,
            headers=request_headers,
            timeout=timeout or self.config.timeout_seconds,
            allow_redirects=True,
        )
        if response.status_code == 200:
            self.condition_store.update(request_url, response.headers)
        return response

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
