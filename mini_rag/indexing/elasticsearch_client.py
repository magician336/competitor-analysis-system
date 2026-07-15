"""Search backend abstraction with Elasticsearch and offline implementations."""

from __future__ import annotations

import json
import math
import re
import threading
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote


class SearchBackendError(RuntimeError):
    """Backend request failed or returned an invalid response."""


@dataclass(frozen=True)
class BackendHit:
    """One backend-neutral hit."""

    source: dict[str, Any]
    score: float
    document_id: str | None = None


@dataclass
class BulkResult:
    """Observable result from a bulk write or delete operation."""

    attempted: int = 0
    indexed: int = 0
    deleted: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return self.indexed + self.deleted

    @property
    def ok(self) -> bool:
        return self.failed == 0


@runtime_checkable
class SearchBackend(Protocol):
    """Operations required by index builders and retrievers."""

    def index_exists(self, index: str) -> bool: ...

    def create_index(self, index: str, mapping: Mapping[str, Any]) -> None: ...

    def delete_index(self, index: str, *, ignore_missing: bool = True) -> bool: ...

    def get_mapping(self, index: str) -> dict[str, Any]: ...

    def bulk_index(
        self,
        index: str,
        documents: Iterable[Mapping[str, Any]],
        *,
        id_field: str = "chunk_id",
        refresh: bool = False,
    ) -> BulkResult: ...

    def delete_documents(
        self, index: str, document_ids: Iterable[str], *, refresh: bool = False
    ) -> BulkResult: ...

    def get_document(self, index: str, document_id: str) -> dict[str, Any] | None: ...

    def iter_documents(self, index: str) -> Iterator[dict[str, Any]]: ...

    def search_bm25(
        self, index: str, query: str, filters: Any = None, *, top_k: int = 20
    ) -> list[BackendHit]: ...

    def search_dense(
        self,
        index: str,
        vector: Sequence[float],
        filters: Any = None,
        *,
        top_k: int = 20,
    ) -> list[BackendHit]: ...

    def swap_alias(self, alias: str, index: str) -> None: ...

    def resolve_alias(self, alias: str) -> str | None: ...

    def count(self, index: str) -> int: ...

    def refresh(self, index: str) -> None: ...


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return value


def normalize_filters(filters: Any) -> dict[str, Any]:
    """Convert RAGQuery, Pydantic models, or mappings to plain filter values."""

    if filters is None:
        return {}
    if hasattr(filters, "filters") and callable(filters.filters):
        return _json_value(filters.filters(exclude_empty=True))
    if hasattr(filters, "model_dump"):
        raw = filters.model_dump(mode="json", exclude_none=True)
        raw.pop("question", None)
        raw.pop("top_k", None)
        return {key: value for key, value in raw.items() if value not in (None, [], "", False)}
    if isinstance(filters, Mapping):
        return {
            str(key): _json_value(value)
            for key, value in filters.items()
            if value not in (None, [], "", False)
        }
    raise TypeError("filters must be a mapping or RAGQuery-compatible object")


def build_filter_clauses(filters: Any) -> list[dict[str, Any]]:
    """Translate public filters into Elasticsearch boolean filter clauses."""

    values = normalize_filters(filters)
    clauses: list[dict[str, Any]] = []
    scalar_fields = {
        "competitor": "competitor",
        "is_current": "is_current",
        "current_only": "is_current",
        "product_version": "product_version",
        "source_type": "source_type",
        "event_type": "event_type",
        "evidence_level": "evidence_level",
    }
    list_fields = {
        "event_types": "event_type",
        "dimension_tags": "dimension_tags",
        "product_versions": "product_version",
        "evidence_levels": "evidence_level",
        "source_types": "source_type",
        "competitors": "competitor",
    }
    for public_name, index_name in scalar_fields.items():
        if public_name in values:
            value = True if public_name == "current_only" else values[public_name]
            clauses.append({"term": {index_name: value}})
    for public_name, index_name in list_fields.items():
        selected = values.get(public_name)
        if selected:
            clauses.append({"terms": {index_name: list(selected)}})
    if values.get("start_time") is not None or values.get("end_time") is not None:
        bounds: dict[str, Any] = {}
        if values.get("start_time") is not None:
            bounds["gte"] = values["start_time"]
        if values.get("end_time") is not None:
            bounds["lte"] = values["end_time"]
        clauses.append({"range": {"publish_time": bounds}})
    return clauses


class ElasticsearchClient:
    """Minimal Elasticsearch HTTP client.

    The implementation uses the REST API already exposed by Elasticsearch and
    therefore does not require the optional official Python client package.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:9200",
        *,
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        verify: bool = True,
        timeout: float = 30.0,
        session: Any | None = None,
        request_timeout: float | None = None,
        verify_certs: bool | None = None,
    ) -> None:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - declared base dependency
            raise RuntimeError("requests is required for Elasticsearch HTTP access") from exc
        self.base_url = base_url.rstrip("/")
        self.timeout = float(request_timeout if request_timeout is not None else timeout)
        self.verify = verify if verify_certs is None else bool(verify_certs)
        self.session = session or requests.Session()
        if username is not None:
            self.session.auth = (username, password or "")
        self._headers = {"Accept": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"ApiKey {api_key}"

    @staticmethod
    def _path(*parts: str) -> str:
        return "/" + "/".join(quote(str(part), safe="*,-_") for part in parts)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        data: str | None = None,
        headers: Mapping[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        merged_headers = {**self._headers, **dict(headers or {})}
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                json=json_body,
                data=data,
                headers=merged_headers,
                params=params,
                timeout=self.timeout,
                verify=self.verify,
            )
        except Exception as exc:
            raise SearchBackendError(f"Elasticsearch request failed: {method} {path}: {exc}") from exc
        if response.status_code not in expected:
            detail = response.text[:1_000]
            raise SearchBackendError(
                f"Elasticsearch returned HTTP {response.status_code} for {method} {path}: {detail}"
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise SearchBackendError(f"Elasticsearch returned non-JSON data for {path}") from exc

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/_cluster/health")

    def ping(self) -> bool:
        try:
            self._request("GET", "/", expected=(200,))
        except SearchBackendError:
            return False
        return True

    def index_exists(self, index: str) -> bool:
        try:
            response = self.session.request(
                "HEAD",
                f"{self.base_url}{self._path(index)}",
                headers=self._headers,
                timeout=self.timeout,
                verify=self.verify,
            )
        except Exception as exc:
            raise SearchBackendError(f"failed to check index {index}: {exc}") from exc
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise SearchBackendError(
                f"Elasticsearch returned HTTP {response.status_code} while checking {index}"
            )
        return True

    def create_index(self, index: str, mapping: Mapping[str, Any]) -> None:
        self._request("PUT", self._path(index), json_body=_json_value(mapping), expected=(200, 201))

    def delete_index(self, index: str, *, ignore_missing: bool = True) -> bool:
        expected = (200, 404) if ignore_missing else (200,)
        result = self._request("DELETE", self._path(index), expected=expected)
        return bool(result and result.get("acknowledged"))

    def get_mapping(self, index: str) -> dict[str, Any]:
        return self._request("GET", self._path(index, "_mapping"))

    def bulk_index(
        self,
        index: str,
        documents: Iterable[Mapping[str, Any]],
        *,
        id_field: str = "chunk_id",
        refresh: bool = False,
    ) -> BulkResult:
        materialized = [_json_value(dict(document)) for document in documents]
        report = BulkResult(attempted=len(materialized))
        if not materialized:
            return report
        lines: list[str] = []
        for document in materialized:
            identifier = document.get(id_field)
            if not identifier:
                raise ValueError(f"bulk document is missing {id_field}")
            lines.append(json.dumps({"index": {"_index": index, "_id": identifier}}))
            lines.append(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
        payload = "\n".join(lines) + "\n"
        result = self._request(
            "POST",
            "/_bulk",
            data=payload,
            headers={"Content-Type": "application/x-ndjson"},
            params={"refresh": "wait_for" if refresh else "false"},
        )
        for item in result.get("items", []):
            operation = item.get("index", {})
            status = int(operation.get("status", 500))
            if 200 <= status < 300:
                report.indexed += 1
            else:
                report.failed += 1
                report.errors.append(
                    f"{operation.get('_id', '<unknown>')}: {operation.get('error', status)}"
                )
        # A malformed response must not look successful.
        unaccounted = report.attempted - report.indexed - report.failed
        if unaccounted > 0:
            report.failed += unaccounted
            report.errors.append(f"bulk response omitted {unaccounted} operations")
        return report

    def delete_documents(
        self, index: str, document_ids: Iterable[str], *, refresh: bool = False
    ) -> BulkResult:
        identifiers = list(dict.fromkeys(str(value) for value in document_ids))
        report = BulkResult(attempted=len(identifiers))
        if not identifiers:
            return report
        payload = "\n".join(
            json.dumps({"delete": {"_index": index, "_id": identifier}})
            for identifier in identifiers
        ) + "\n"
        result = self._request(
            "POST",
            "/_bulk",
            data=payload,
            headers={"Content-Type": "application/x-ndjson"},
            params={"refresh": "wait_for" if refresh else "false"},
        )
        for item in result.get("items", []):
            operation = item.get("delete", {})
            status = int(operation.get("status", 500))
            if status in (200, 202, 404):
                report.deleted += 1
            else:
                report.failed += 1
                report.errors.append(
                    f"{operation.get('_id', '<unknown>')}: {operation.get('error', status)}"
                )
        return report

    def get_document(self, index: str, document_id: str) -> dict[str, Any] | None:
        path = self._path(index, "_doc", document_id)
        result = self._request("GET", path, expected=(200, 404))
        if not result or not result.get("found", True):
            return None
        return result.get("_source")

    def iter_documents(self, index: str) -> Iterator[dict[str, Any]]:
        # Scroll is intentionally bounded per page but streams all matching
        # documents, which supports stale-ID reconciliation on large indexes.
        page = self._request(
            "POST",
            self._path(index, "_search"),
            json_body={"query": {"match_all": {}}, "size": 1_000, "sort": ["_doc"]},
            params={"scroll": "1m"},
        )
        scroll_id = page.get("_scroll_id")
        try:
            while True:
                hits = page.get("hits", {}).get("hits", [])
                if not hits:
                    break
                for hit in hits:
                    yield hit.get("_source", {})
                page = self._request(
                    "POST",
                    "/_search/scroll",
                    json_body={"scroll": "1m", "scroll_id": scroll_id},
                )
                scroll_id = page.get("_scroll_id", scroll_id)
        finally:
            if scroll_id:
                try:
                    self._request(
                        "DELETE",
                        "/_search/scroll",
                        json_body={"scroll_id": [scroll_id]},
                        expected=(200, 404),
                    )
                except SearchBackendError:
                    pass

    def search_bm25(
        self, index: str, query: str, filters: Any = None, *, top_k: int = 20
    ) -> list[BackendHit]:
        if top_k < 1:
            return []
        text_query: dict[str, Any]
        if query.strip():
            text_query = {
                "multi_match": {
                    "query": query,
                    "fields": ["title^3", "heading_path^2", "content"],
                    "type": "best_fields",
                }
            }
        else:
            text_query = {"match_all": {}}
        body = {
            "size": top_k,
            "_source": {"excludes": ["embedding"]},
            "query": {
                "bool": {
                    "must": [text_query],
                    "filter": build_filter_clauses(filters),
                }
            },
        }
        result = self._request("POST", self._path(index, "_search"), json_body=body)
        return [
            BackendHit(
                source=hit.get("_source", {}),
                score=float(hit.get("_score") or 0.0),
                document_id=hit.get("_id"),
            )
            for hit in result.get("hits", {}).get("hits", [])
        ]

    def search_dense(
        self,
        index: str,
        vector: Sequence[float],
        filters: Any = None,
        *,
        top_k: int = 20,
    ) -> list[BackendHit]:
        if top_k < 1 or not vector or not any(float(value) for value in vector):
            return []
        clauses = build_filter_clauses(filters)
        num_candidates = min(10_000, max(top_k + 1, top_k * 4))
        knn: dict[str, Any] = {
            "field": "embedding",
            "query_vector": [float(value) for value in vector],
            "k": top_k,
            "num_candidates": num_candidates,
        }
        if clauses:
            knn["filter"] = {"bool": {"filter": clauses}}
        body = {
            "size": top_k,
            "_source": {"excludes": ["embedding"]},
            "knn": knn,
        }
        result = self._request("POST", self._path(index, "_search"), json_body=body)
        return [
            BackendHit(
                source=hit.get("_source", {}),
                score=float(hit.get("_score") or 0.0),
                document_id=hit.get("_id"),
            )
            for hit in result.get("hits", {}).get("hits", [])
        ]

    def swap_alias(self, alias: str, index: str) -> None:
        if not self.index_exists(index):
            raise SearchBackendError(f"cannot point alias {alias} at missing index {index}")
        # Elasticsearch applies this action list atomically. Wildcard removal
        # ensures concurrent rebuilds cannot leave the alias on two indices;
        # the last complete swap wins.
        actions = [
            {
                "remove": {
                    "index": "*",
                    "alias": alias,
                    "must_exist": False,
                }
            },
            {
                "add": {
                    "index": index,
                    "alias": alias,
                    "is_write_index": True,
                }
            },
        ]
        self._request("POST", "/_aliases", json_body={"actions": actions})

    def resolve_alias(self, alias: str) -> str | None:
        result = self._request("GET", self._path("_alias", alias), expected=(200, 404))
        if not result or (result.get("status") == 404 and "error" in result):
            return None
        names = sorted(result)
        if len(names) > 1:
            raise SearchBackendError(
                f"alias {alias} resolves to multiple indices: {', '.join(names)}"
            )
        return names[0] if names else None

    def count(self, index: str) -> int:
        result = self._request("GET", self._path(index, "_count"))
        return int(result.get("count", 0))

    def refresh(self, index: str) -> None:
        self._request("POST", self._path(index, "_refresh"))


_WORD_RE = re.compile(r"[a-z0-9]+(?:[._+#/-][a-z0-9]+)*", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def tokenize_for_search(text: str) -> list[str]:
    """Dependency-free bilingual tokens used by the memory BM25 backend."""

    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    tokens = _WORD_RE.findall(normalized)
    for run in _CJK_RE.findall(normalized):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


class InMemorySearchBackend:
    """Thread-safe backend implementing BM25, cosine search, and aliases."""

    def __init__(self) -> None:
        self._indices: dict[str, dict[str, dict[str, Any]]] = {}
        self._mappings: dict[str, dict[str, Any]] = {}
        self._aliases: dict[str, str] = {}
        self._lock = threading.RLock()

    def _resolve(self, name: str) -> str:
        resolved = self._aliases.get(name, name)
        if resolved not in self._indices:
            raise SearchBackendError(f"index or alias does not exist: {name}")
        return resolved

    def health(self) -> dict[str, Any]:
        return {
            "status": "green",
            "backend": "memory",
            "indices": len(self._indices),
            "documents": sum(len(index) for index in self._indices.values()),
        }

    def ping(self) -> bool:
        return True

    def index_exists(self, index: str) -> bool:
        with self._lock:
            return index in self._indices or index in self._aliases

    def create_index(self, index: str, mapping: Mapping[str, Any]) -> None:
        with self._lock:
            if index in self._indices or index in self._aliases:
                raise SearchBackendError(f"index already exists: {index}")
            self._indices[index] = {}
            self._mappings[index] = deepcopy(dict(mapping))

    def delete_index(self, index: str, *, ignore_missing: bool = True) -> bool:
        with self._lock:
            resolved = self._aliases.get(index, index)
            if resolved not in self._indices:
                if ignore_missing:
                    return False
                raise SearchBackendError(f"index does not exist: {index}")
            del self._indices[resolved]
            self._mappings.pop(resolved, None)
            self._aliases = {
                alias: target for alias, target in self._aliases.items() if target != resolved
            }
            return True

    def get_mapping(self, index: str) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._mappings[self._resolve(index)])

    def _embedding_dimension(self, index: str) -> int | None:
        mapping = self._mappings[index]
        return (
            mapping.get("mappings", {})
            .get("properties", {})
            .get("embedding", {})
            .get("dims")
        )

    def bulk_index(
        self,
        index: str,
        documents: Iterable[Mapping[str, Any]],
        *,
        id_field: str = "chunk_id",
        refresh: bool = False,
    ) -> BulkResult:
        materialized = [_json_value(dict(document)) for document in documents]
        result = BulkResult(attempted=len(materialized))
        with self._lock:
            resolved = self._resolve(index)
            dimension = self._embedding_dimension(resolved)
            for document in materialized:
                identifier = document.get(id_field)
                if not identifier:
                    result.failed += 1
                    result.errors.append(f"document missing {id_field}")
                    continue
                embedding = document.get("embedding")
                if embedding is not None and dimension is not None and len(embedding) != dimension:
                    result.failed += 1
                    result.errors.append(
                        f"{identifier}: embedding dimension {len(embedding)} != {dimension}"
                    )
                    continue
                self._indices[resolved][str(identifier)] = deepcopy(document)
                result.indexed += 1
        return result

    def delete_documents(
        self, index: str, document_ids: Iterable[str], *, refresh: bool = False
    ) -> BulkResult:
        identifiers = list(dict.fromkeys(str(value) for value in document_ids))
        result = BulkResult(attempted=len(identifiers))
        with self._lock:
            documents = self._indices[self._resolve(index)]
            for identifier in identifiers:
                documents.pop(identifier, None)
                result.deleted += 1
        return result

    def get_document(self, index: str, document_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._indices[self._resolve(index)].get(str(document_id))
            return deepcopy(value) if value is not None else None

    def iter_documents(self, index: str) -> Iterator[dict[str, Any]]:
        with self._lock:
            snapshot = list(self._indices[self._resolve(index)].values())
        for document in snapshot:
            yield deepcopy(document)

    @staticmethod
    def _date_value(value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value).replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _matches_filters(cls, document: Mapping[str, Any], filters: Any) -> bool:
        values = normalize_filters(filters)
        scalar = {
            "competitor": "competitor",
            "product_version": "product_version",
            "source_type": "source_type",
            "event_type": "event_type",
            "evidence_level": "evidence_level",
        }
        lists = {
            "event_types": "event_type",
            "dimension_tags": "dimension_tags",
            "product_versions": "product_version",
            "evidence_levels": "evidence_level",
            "source_types": "source_type",
            "competitors": "competitor",
        }
        for public_name, field_name in scalar.items():
            if public_name not in values:
                continue
            expected = str(values[public_name]).casefold()
            if str(document.get(field_name, "")).casefold() != expected:
                return False
        if values.get("current_only") and document.get("is_current") is not True:
            return False
        if "is_current" in values and bool(document.get("is_current")) != bool(values["is_current"]):
            return False
        for public_name, field_name in lists.items():
            selected = {str(item).casefold() for item in values.get(public_name, [])}
            if not selected:
                continue
            actual_value = document.get(field_name)
            actual_items = actual_value if isinstance(actual_value, list) else [actual_value]
            actual = {str(item).casefold() for item in actual_items if item is not None}
            if not actual.intersection(selected):
                return False
        published = cls._date_value(document.get("publish_time"))
        start = cls._date_value(values.get("start_time"))
        end = cls._date_value(values.get("end_time"))
        # Match Elasticsearch range semantics: a missing date does not satisfy
        # an explicit range.
        if (start or end) and published is None:
            return False
        if start and published and published < start:
            return False
        if end and published and published > end:
            return False
        return True

    def _filtered(self, index: str, filters: Any) -> list[dict[str, Any]]:
        with self._lock:
            documents = list(self._indices[self._resolve(index)].values())
        return [document for document in documents if self._matches_filters(document, filters)]

    def search_bm25(
        self, index: str, query: str, filters: Any = None, *, top_k: int = 20
    ) -> list[BackendHit]:
        if top_k < 1:
            return []
        documents = self._filtered(index, filters)
        query_terms = tokenize_for_search(query)
        if not query_terms:
            return [
                BackendHit(source=deepcopy(document), score=0.0, document_id=document.get("chunk_id"))
                for document in documents[:top_k]
            ]

        weighted_terms: list[list[str]] = []
        lengths: list[int] = []
        for document in documents:
            content = tokenize_for_search(str(document.get("content", "")))
            title = tokenize_for_search(str(document.get("title", "")))
            headings = tokenize_for_search(" ".join(document.get("heading_path") or []))
            terms = content + title * 3 + headings * 2
            weighted_terms.append(terms)
            lengths.append(len(terms))
        if not documents:
            return []
        average_length = sum(lengths) / len(lengths) or 1.0
        frequencies = [Counter(terms) for terms in weighted_terms]
        document_frequency = {
            term: sum(1 for counts in frequencies if counts.get(term, 0) > 0)
            for term in set(query_terms)
        }
        k1, b = 1.2, 0.75
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for document, counts, length in zip(documents, frequencies, lengths, strict=True):
            score = 0.0
            for term in query_terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                df = document_frequency[term]
                inverse_df = math.log(1.0 + (len(documents) - df + 0.5) / (df + 0.5))
                denominator = frequency + k1 * (1.0 - b + b * length / average_length)
                score += inverse_df * frequency * (k1 + 1.0) / denominator
            if score > 0.0:
                scored.append((score, str(document.get("chunk_id", "")), document))
        scored.sort(key=lambda value: (-value[0], value[1]))
        return [
            BackendHit(source=deepcopy(document), score=score, document_id=identifier)
            for score, identifier, document in scored[:top_k]
        ]

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right) or not left:
            return float("-inf")
        dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
        right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
        if left_norm == 0.0 or right_norm == 0.0:
            return float("-inf")
        return dot / (left_norm * right_norm)

    def search_dense(
        self,
        index: str,
        vector: Sequence[float],
        filters: Any = None,
        *,
        top_k: int = 20,
    ) -> list[BackendHit]:
        if top_k < 1 or not vector or not any(float(value) for value in vector):
            return []
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for document in self._filtered(index, filters):
            embedding = document.get("embedding")
            if not isinstance(embedding, list):
                continue
            score = self._cosine(vector, embedding)
            if math.isfinite(score):
                scored.append((score, str(document.get("chunk_id", "")), document))
        scored.sort(key=lambda value: (-value[0], value[1]))
        return [
            BackendHit(source=deepcopy(document), score=score, document_id=identifier)
            for score, identifier, document in scored[:top_k]
        ]

    def swap_alias(self, alias: str, index: str) -> None:
        with self._lock:
            if index not in self._indices:
                raise SearchBackendError(f"cannot point alias {alias} at missing index {index}")
            if alias in self._indices:
                raise SearchBackendError(f"alias conflicts with physical index: {alias}")
            self._aliases[alias] = index

    def resolve_alias(self, alias: str) -> str | None:
        with self._lock:
            return self._aliases.get(alias)

    def count(self, index: str) -> int:
        with self._lock:
            return len(self._indices[self._resolve(index)])

    def refresh(self, index: str) -> None:
        self._resolve(index)


# Friendly aliases used in configuration and tests.
MemorySearchBackend = InMemorySearchBackend
ElasticsearchBackend = ElasticsearchClient


__all__ = [
    "BackendHit",
    "BulkResult",
    "ElasticsearchBackend",
    "ElasticsearchClient",
    "InMemorySearchBackend",
    "MemorySearchBackend",
    "SearchBackend",
    "SearchBackendError",
    "build_filter_clauses",
    "normalize_filters",
    "tokenize_for_search",
]
