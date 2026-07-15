"""Bounded, query-id-addressable retrieval trace storage."""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel


def _as_json_object(value: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return json.loads(json.dumps(dict(value), ensure_ascii=False, default=str))


class TraceStore:
    """Keep recent query responses in memory and optionally persist JSONL."""

    def __init__(self, path: str | Path | None = None, retention: int = 1000) -> None:
        if retention <= 0:
            raise ValueError("retention must be positive")
        self.path = Path(path) if path is not None else None
        self.retention = retention
        self._records: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self._load_existing()

    @staticmethod
    def _query_id(record: Mapping[str, Any]) -> str:
        value = record.get("query_id")
        if not value:
            raise ValueError("trace record must contain query_id")
        return str(value)

    def _load_existing(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        with self.path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                    query_id = self._query_id(record)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                self._records[query_id] = record
                self._records.move_to_end(query_id)
        self._trim()

    def _trim(self) -> None:
        while len(self._records) > self.retention:
            self._records.popitem(last=False)

    def put(self, value: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
        record = _as_json_object(value)
        query_id = self._query_id(record)
        with self._lock:
            self._records[query_id] = record
            self._records.move_to_end(query_id)
            self._trim()
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                    handle.write("\n")
        return record

    def get(self, query_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(query_id)
            return dict(record) if record is not None else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

