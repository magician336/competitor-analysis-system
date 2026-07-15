"""Structured fact conflict detection across source and document versions."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mini_rag.models import Conflict, Evidence
from mini_rag.ranking._common import as_datetime, chunk_of, get_value, metadata_of, score_of


_LEVEL_PRIORITY = {"A": 4, "B": 3, "C": 2, "D": 1}


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _normalise_scalar(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _display_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float):
        return f"{value:g}"
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


@dataclass(frozen=True)
class _Fact:
    field: str
    value: Any
    entity: str
    competitor: str
    chunk_id: str
    product_version: str | None
    publish_time: datetime | None
    valid_from: datetime | None
    valid_to: datetime | None
    is_current: bool
    evidence_level: str
    final_score: float

    @property
    def group_key(self) -> tuple[str, str, str]:
        return self.competitor.casefold(), self.entity.casefold(), self.field.casefold()

    @property
    def normalised_value(self) -> str:
        return _normalise_scalar(self.value)


def _item_metadata(item: Any) -> dict[str, Any]:
    if isinstance(item, Evidence):
        return dict(item.metadata)
    metadata = metadata_of(item)
    candidate_metadata = get_value(item, "metadata", {}) or {}
    if isinstance(candidate_metadata, Mapping):
        metadata.update(candidate_metadata)
    return metadata


def _base_fact(item: Any, field: str, value: Any, entity: str) -> _Fact | None:
    if value in (None, "", []):
        return None
    source = chunk_of(item)
    chunk_id = str(get_value(source, "chunk_id", "") or get_value(item, "chunk_id", ""))
    if not chunk_id:
        return None
    return _Fact(
        field=str(field),
        value=value,
        entity=entity or "default",
        competitor=str(get_value(source, "competitor", "") or get_value(item, "competitor", "")),
        chunk_id=chunk_id,
        product_version=get_value(source, "product_version", None) or get_value(item, "product_version", None),
        publish_time=as_datetime(get_value(source, "publish_time", None) or get_value(item, "publish_time", None)),
        valid_from=as_datetime(get_value(source, "valid_from", None) or get_value(item, "valid_from", None)),
        valid_to=as_datetime(get_value(source, "valid_to", None) or get_value(item, "valid_to", None)),
        is_current=bool(get_value(source, "is_current", get_value(item, "is_current", True))),
        evidence_level=_enum_value(
            get_value(source, "evidence_level", None) or get_value(item, "evidence_level", "D")
        ).upper(),
        final_score=score_of(item),
    )


def _facts_from_item(item: Any) -> list[_Fact]:
    source = chunk_of(item)
    metadata = _item_metadata(item)
    entity = str(
        metadata.get("fact_entity")
        or metadata.get("entity")
        or get_value(source, "plan_name", None)
        or metadata.get("plan_name")
        or "default"
    )
    facts: list[_Fact] = []

    structured = metadata.get("structured_facts", metadata.get("facts"))
    if isinstance(structured, Mapping):
        structured = [{"field": field, "value": value} for field, value in structured.items()]
    if isinstance(structured, Sequence) and not isinstance(structured, (str, bytes)):
        for entry in structured:
            if not isinstance(entry, Mapping):
                continue
            field = entry.get("field") or entry.get("key") or entry.get("name")
            if not field or "value" not in entry:
                continue
            fact = _base_fact(item, str(field), entry.get("value"), str(entry.get("entity") or entity))
            if fact:
                facts.append(fact)

    explicit_field = metadata.get("fact_field")
    if explicit_field and "fact_value" in metadata:
        fact = _base_fact(item, str(explicit_field), metadata.get("fact_value"), entity)
        if fact:
            facts.append(fact)

    source_type = _enum_value(get_value(source, "source_type", get_value(item, "source_type", "")))
    plan_name = get_value(source, "plan_name", None) or metadata.get("plan_name")
    if source_type == "pricing" and plan_name:
        pricing_entity = str(plan_name)
        for field in ("price_value", "currency", "billing_period"):
            value = get_value(source, field, None)
            if value is None:
                value = metadata.get(field)
            fact = _base_fact(item, field, value, pricing_entity)
            if fact:
                facts.append(fact)

    repository = get_value(source, "repository", None) or metadata.get("repository")
    issue_number = get_value(source, "github_number", None) or metadata.get("github_number")
    state = get_value(source, "github_state", None) or metadata.get("github_state")
    if repository and issue_number is not None and state:
        fact = _base_fact(item, "github_state", state, f"{repository}#{issue_number}")
        if fact:
            facts.append(fact)

    unique: dict[tuple[str, str, str], _Fact] = {}
    for fact in facts:
        unique[(fact.field.casefold(), fact.entity.casefold(), fact.normalised_value)] = fact
    return list(unique.values())


def _intervals_overlap(left: _Fact, right: _Fact) -> bool:
    earliest_end = min(
        left.valid_to or datetime.max.replace(tzinfo=timezone.utc),
        right.valid_to or datetime.max.replace(tzinfo=timezone.utc),
    )
    latest_start = max(
        left.valid_from or datetime.min.replace(tzinfo=timezone.utc),
        right.valid_from or datetime.min.replace(tzinfo=timezone.utc),
    )
    # Adjacent document versions share a boundary but are never valid together.
    return latest_start < earliest_end


def _preferred(facts: Sequence[_Fact]) -> _Fact:
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return max(
        facts,
        key=lambda fact: (
            int(fact.is_current),
            _LEVEL_PRIORITY.get(fact.evidence_level, 0),
            fact.publish_time or epoch,
            fact.final_score,
            fact.chunk_id,
        ),
    )


class ConflictDetector:
    """Find distinct active values for the same structured fact entity."""

    def __init__(self, fact_extractor: Callable[[Any], Sequence[Mapping[str, Any]]] | None = None) -> None:
        self.fact_extractor = fact_extractor

    def _extract(self, item: Any) -> list[_Fact]:
        if self.fact_extractor is None:
            return _facts_from_item(item)
        result: list[_Fact] = []
        for entry in self.fact_extractor(item):
            field = entry.get("field")
            if field and "value" in entry:
                fact = _base_fact(item, str(field), entry["value"], str(entry.get("entity") or "default"))
                if fact:
                    result.append(fact)
        return result

    def detect(self, items: Sequence[Any]) -> list[Conflict]:
        groups: dict[tuple[str, str, str], list[_Fact]] = defaultdict(list)
        for item in items:
            for fact in self._extract(item):
                groups[fact.group_key].append(fact)

        conflicts: list[Conflict] = []
        for (_, entity, field), facts in sorted(groups.items()):
            participants: set[str] = set()
            for index, left in enumerate(facts):
                for right in facts[index + 1 :]:
                    if (
                        left.chunk_id != right.chunk_id
                        and left.normalised_value != right.normalised_value
                        and _intervals_overlap(left, right)
                    ):
                        participants.update((left.chunk_id, right.chunk_id))
            active = [fact for fact in facts if fact.chunk_id in participants]
            distinct_values = list(dict.fromkeys(fact.normalised_value for fact in active))
            if len(participants) < 2 or len(distinct_values) < 2:
                continue
            # A chunk may repeat the same fact; keep the strongest record once.
            by_chunk: dict[str, _Fact] = {}
            for fact in active:
                existing = by_chunk.get(fact.chunk_id)
                if existing is None or _preferred((existing, fact)) is fact:
                    by_chunk[fact.chunk_id] = fact
            ordered = sorted(by_chunk.values(), key=lambda fact: fact.chunk_id)
            preferred = _preferred(ordered)
            values = list(dict.fromkeys(_display_value(fact.value) for fact in ordered))
            conflicts.append(
                Conflict(
                    field=field,
                    competitor=ordered[0].competitor or None,
                    values=values,
                    chunk_ids=[fact.chunk_id for fact in ordered],
                    product_versions=[fact.product_version for fact in ordered],
                    publish_times=[fact.publish_time for fact in ordered],
                    preferred_chunk_id=preferred.chunk_id,
                    reason=(
                        f"entity={entity}; preferred current evidence with the highest authority, "
                        "then the newest publication time and retrieval score"
                    ),
                )
            )
        return conflicts


def detect_conflicts(items: Sequence[Any]) -> list[Conflict]:
    return ConflictDetector().detect(items)
