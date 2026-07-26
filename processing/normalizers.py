"""Deterministic normalisation helpers used before hashing and labelling."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import unicodedata
from datetime import date, datetime, time, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from dateutil import parser as date_parser


TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref_src",
}


def normalize_url(url: str) -> str:
    """Return a canonical HTTP(S) URL suitable for identity generation."""

    value = unicodedata.normalize("NFKC", (url or "").strip())
    if not value:
        return ""
    split = urlsplit(value)
    if not split.scheme and split.path.startswith("//"):
        split = urlsplit(f"https:{value}")
    elif not split.scheme and split.path:
        # Relative and opaque identifiers are preserved instead of being
        # silently interpreted as hosts.
        if not re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/|$)", split.path):
            return value.split("#", 1)[0]
        split = urlsplit(f"https://{value}")

    scheme = split.scheme.lower()
    hostname = (split.hostname or "").lower()
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    port = split.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname
    if split.username:
        credentials = split.username
        if split.password:
            credentials += f":{split.password}"
        netloc = f"{credentials}@{netloc}"

    raw_path = re.sub(r"/{2,}", "/", split.path or "/")
    normal_path = posixpath.normpath(raw_path)
    if raw_path.endswith("/") and normal_path != "/":
        normal_path += "/"
    if normal_path != "/":
        normal_path = normal_path.rstrip("/")
    path = quote(normal_path, safe="/%:@!$&'()*+,;=-._~")

    query_items = []
    for key, item_value in parse_qsl(split.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in TRACKING_QUERY_KEYS:
            continue
        query_items.append((key, item_value))
    query = urlencode(sorted(query_items), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_text(text: Any, *, preserve_lines: bool = True) -> str:
    """Apply Unicode NFKC and stable whitespace normalisation."""

    if text is None:
        return ""
    value = unicodedata.normalize("NFKC", str(text))
    preserved_controls = {"\n", "\r", "\t", "\f", "\v"}
    value = "".join(
        " "
        if character not in preserved_controls
        and unicodedata.category(character) in {"Cc", "Cf", "Co", "Cs"}
        else character
        for character in value
    )
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    value = re.sub(r"[\t\f\v ]+", " ", value)
    if not preserve_lines:
        return re.sub(r"\s+", " ", value).strip()
    lines = [line.strip() for line in value.split("\n")]
    value = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def normalize_datetime(value: Any, *, default_timezone: timezone = timezone.utc) -> datetime | None:
    """Parse a date-like value and return an aware UTC datetime."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, (int, float)) or (
        isinstance(value, str)
        and re.fullmatch(r"-?\d+(?:\.\d+)?", value.strip())
    ):
        # GitHub and RSS timestamps are seconds; accepting milliseconds makes
        # fixtures and exported APIs safer to process.
        timestamp = float(value)
        if abs(timestamp) > 10_000_000_000:
            timestamp /= 1000
        parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    else:
        raw = normalize_text(value, preserve_lines=False)
        try:
            parsed = date_parser.parse(raw)
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = parsedate_to_datetime(raw)
            except (TypeError, ValueError, OverflowError):
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_timezone)
    return parsed.astimezone(timezone.utc)


def normalize_version(value: Any) -> str | None:
    """Convert a version expression to a comparable SemVer-like value."""

    raw = normalize_text(value, preserve_lines=False)
    if not raw:
        return None
    match = re.search(
        r"(?i)(?:^|\b(?:version|ver|release)\s*)v?"
        r"(\d+)(?:\.(\d+))?(?:\.(\d+))?"
        r"(?:([-+_ ])([0-9A-Za-z][0-9A-Za-z.-]*))?",
        raw,
    )
    if not match:
        return None
    major, minor, patch, separator, suffix = match.groups()
    result = f"{int(major)}.{int(minor or 0)}.{int(patch or 0)}"
    if suffix:
        clean_suffix = re.sub(r"[^0-9A-Za-z.-]+", ".", suffix).strip(".").lower()
        if separator == " " and not re.match(
            r"^(?:a(?:lpha)?|b(?:eta)?|rc|pre(?:view)?|dev|nightly)\d*(?:[.-].*)?$",
            clean_suffix,
        ):
            clean_suffix = ""
        if clean_suffix:
            result += f"-{clean_suffix}"
    return result


def extract_version(*values: Any) -> tuple[str | None, str | None]:
    """Return the first explicit raw version expression and its normal form."""

    suffix = r"(?:[-+_][0-9A-Za-z.-]+| (?:alpha|beta|rc|preview|pre|dev|nightly)\d*(?:[.-][0-9A-Za-z.-]+)?)?"
    pattern = re.compile(
        rf"(?i)\b(?:version|ver|release)\s*v?\d+(?:\.\d+){{0,3}}{suffix}"
        rf"|(?<![A-Za-z0-9])v\d+(?:\.\d+){{1,3}}(?:[-+_][0-9A-Za-z.-]+)?"
    )
    for value in values:
        text = normalize_text(value, preserve_lines=False)
        match = pattern.search(text)
        if match:
            raw = match.group(0).strip()
            return raw, normalize_version(raw)
    return None, None


def sha256_bytes(payload: bytes) -> str:
    """Return a namespaced SHA-256 digest."""

    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def sha256_text(text: str) -> str:
    """Hash normalised text so equivalent whitespace has one identity."""

    return sha256_bytes(normalize_text(text).encode("utf-8"))


def stable_id(prefix: str, *parts: Any, length: int = 24) -> str:
    """Generate a deterministic opaque identifier from serialisable parts."""

    normalised_parts = []
    for part in parts:
        if isinstance(part, str):
            normalised_parts.append(normalize_text(part, preserve_lines=False))
        else:
            normalised_parts.append(part)
    serialised = json.dumps(
        normalised_parts,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(serialised.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def detect_language(text: str) -> str:
    """Return a lightweight language hint without an external model."""

    content = normalize_text(text, preserve_lines=False)
    if not content:
        return "und"
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", content))
    latin_count = len(re.findall(r"[A-Za-z]", content))
    if cjk_count and cjk_count >= latin_count * 0.15:
        return "zh"
    if latin_count:
        return "en"
    return "und"
