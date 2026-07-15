"""Rule-based parsing of common competitor, version, and time filters."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from mini_rag.models import RAGQuery


DEFAULT_COMPETITOR_ALIASES: dict[str, tuple[str, ...]] = {
    "Cursor": ("cursor", "cursor ai"),
    "GitHub Copilot": ("github copilot", "copilot"),
    "Trae": ("trae",),
    "通义灵码": ("通义灵码", "灵码", "tongyi lingma", "lingma", "qoder"),
    "CodeGeeX": ("codegeex", "code geex"),
}

_EVENT_KEYWORDS = {
    "pricing_change": ("价格", "定价", "套餐", "费用", "price", "pricing", "plan", "cost"),
    "product_release": ("发布", "更新", "版本", "release", "changelog", "update"),
    "risk_experience": ("风险", "故障", "问题", "体验", "risk", "issue", "bug", "outage"),
}

_DIMENSION_KEYWORDS = {
    "code_intelligence": ("代码补全", "代码智能", "completion", "code intelligence"),
    "agent_context": ("智能体", "上下文", "agent", "context"),
    "ide_ecosystem": ("ide", "编辑器", "插件", "plugin", "vscode", "jetbrains"),
    "model_extensibility": ("模型", "扩展", "model", "mcp", "extension"),
    "performance_cost": ("性能", "成本", "延迟", "performance", "latency", "cost"),
    "security_compliance": ("安全", "合规", "隐私", "security", "compliance", "privacy"),
    "education_fit": ("教育", "学生", "教学", "education", "student", "teaching"),
}

_VERSION_RE = re.compile(r"(?<![\w$])v?(\d+(?:\.\d+){1,3})(?!\w)", re.IGNORECASE)
_RELATIVE_TIME_RE = re.compile(
    r"(?:最近|近|last|past)\s*(\d+)\s*(天|日|周|个月|月|年|days?|weeks?|months?|years?)",
    re.IGNORECASE,
)


class QueryParser:
    """Parse high-confidence filters while retaining the original question."""

    def __init__(
        self,
        competitor_aliases: Mapping[str, tuple[str, ...] | list[str]] | None = None,
        *,
        current_only_for_current_intent: bool = True,
    ) -> None:
        aliases = competitor_aliases or DEFAULT_COMPETITOR_ALIASES
        self.competitor_aliases = {
            canonical: tuple(alias.casefold() for alias in values)
            for canonical, values in aliases.items()
        }
        self.current_only_for_current_intent = bool(current_only_for_current_intent)

    @staticmethod
    def _contains(text: str, keyword: str) -> bool:
        if re.fullmatch(r"[a-z0-9 _-]+", keyword):
            return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text) is not None
        return keyword in text

    def _competitor(self, text: str) -> str | None:
        matches: list[tuple[int, str]] = []
        for canonical, aliases in self.competitor_aliases.items():
            for alias in aliases:
                if self._contains(text, alias):
                    matches.append((len(alias), canonical))
        return max(matches)[1] if matches else None

    @staticmethod
    def _relative_start(text: str, now: datetime) -> datetime | None:
        match = _RELATIVE_TIME_RE.search(text)
        if not match:
            if any(phrase in text for phrase in ("最近三个月", "近三个月", "last three months")):
                return now - timedelta(days=90)
            return None
        amount = int(match.group(1))
        unit = match.group(2).casefold()
        if unit in {"天", "日", "day", "days"}:
            days = amount
        elif unit in {"周", "week", "weeks"}:
            days = 7 * amount
        elif unit in {"个月", "月", "month", "months"}:
            days = 30 * amount
        else:
            days = 365 * amount
        return now - timedelta(days=days)

    def parse(
        self,
        question: str,
        explicit_filters: Mapping[str, Any] | None = None,
        *,
        now: datetime | None = None,
        **filters: Any,
    ) -> RAGQuery:
        clean = question.strip()
        if not clean:
            raise ValueError("question must not be blank")
        text = clean.casefold()
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        parsed: dict[str, Any] = {}
        competitor = self._competitor(text)
        if competitor:
            parsed["competitor"] = competitor

        event_types = [
            event
            for event, keywords in _EVENT_KEYWORDS.items()
            if any(self._contains(text, keyword) for keyword in keywords)
        ]
        if event_types:
            parsed["event_types"] = event_types

        dimension_tags = [
            dimension
            for dimension, keywords in _DIMENSION_KEYWORDS.items()
            if any(self._contains(text, keyword) for keyword in keywords)
        ]
        if dimension_tags:
            parsed["dimension_tags"] = dimension_tags

        versions = list(dict.fromkeys(match.group(1) for match in _VERSION_RE.finditer(text)))
        if versions:
            parsed["product_versions"] = versions

        start_time = self._relative_start(text, current_time)
        if start_time:
            parsed["start_time"] = start_time
            parsed["end_time"] = current_time

        historical = any(
            phrase in text
            for phrase in ("历史", "过去版本", "曾经", "history", "historical", "previous version")
        )
        current = any(
            phrase in text
            for phrase in ("当前", "目前", "现在", "最新", "current", "latest", "now")
        )
        pricing_query = "pricing_change" in event_types
        if self.current_only_for_current_intent and (current or (pricing_query and not historical)):
            parsed["current_only"] = True

        # Explicit API values always take precedence over inferred values.
        explicit = dict(explicit_filters or {})
        explicit.update(filters)
        parsed.update({key: value for key, value in explicit.items() if value is not None})
        return RAGQuery(question=clean, **parsed)

    def __call__(self, question: str, **filters: Any) -> RAGQuery:
        return self.parse(question, **filters)


def parse_query(question: str, **filters: Any) -> RAGQuery:
    return QueryParser().parse(question, **filters)


__all__ = ["DEFAULT_COMPETITOR_ALIASES", "QueryParser", "parse_query"]
