"""Plan-aware chunking and price metadata extraction."""

from __future__ import annotations

import re
from typing import Any

from schemas.document import StructuredDocument

from .base_chunker import BaseChunker, TextSection


_KNOWN_PLAN_NAMES = {
    "free",
    "hobby",
    "individual",
    "personal",
    "pro",
    "pro+",
    "plus",
    "ultra",
    "business",
    "team",
    "teams",
    "enterprise",
    "education",
    "student",
}

_PRICE_RE = re.compile(
    r"(?i)(?:(?P<symbol>[$€£¥])\s*(?P<symbol_value>\d+(?:[.,]\d+)?)|"
    r"(?P<code>USD|EUR|GBP|CNY|RMB|JPY)\s*(?P<code_value>\d+(?:[.,]\d+)?))"
)

_CURRENCY_BY_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "CNY"}


class PricingChunker(BaseChunker):
    """Use one plan as the strongest boundary and isolate common billing rules."""

    section_type = "pricing_common"

    def _sections(self, document: StructuredDocument) -> list[TextSection]:
        sections = super()._sections(document)
        plan_names: list[str] = []
        for section in sections:
            excerpt = document.content[section.start : section.end]
            plan_name = self._plan_name(section, excerpt)
            if plan_name and plan_name not in plan_names:
                plan_names.append(plan_name)
            section.metadata["detected_plan_name"] = plan_name
        for section in sections:
            section.metadata["all_plan_names"] = list(plan_names)
        return sections

    def _section_metadata(
        self,
        document: StructuredDocument,
        section: TextSection,
        content: str,
        start: int,
        end: int,
    ) -> dict[str, Any]:
        metadata = super()._section_metadata(document, section, content, start, end)
        plan_name = metadata.pop("detected_plan_name", None)
        all_plans = metadata.pop("all_plan_names", [])
        price_value, currency = self._price(content)
        billing_period = self._billing_period(content)
        metadata.update(
            {
                "section_type": "pricing_plan" if plan_name else "pricing_common",
                "plan_name": plan_name,
                "price_value": price_value,
                "currency": currency,
                "billing_period": billing_period,
                "applicable_plans": [plan_name] if plan_name else all_plans,
            }
        )
        return metadata

    @staticmethod
    def _plan_name(section: TextSection, content: str) -> str | None:
        if not section.heading_path:
            return None
        heading = section.heading_path[-1].strip().strip("*_")
        folded = heading.casefold()
        path = " ".join(section.heading_path[:-1]).casefold()
        if any(term in path for term in ("question", "faq", "frequently asked")):
            return None
        if folded.startswith(("what ", "how ", "can ", "is ", "are ", "where ")):
            return None
        normalised = re.sub(r"\s+plan$", "", folded).strip()
        if normalised in _KNOWN_PLAN_NAMES:
            return heading
        first_lines = "\n".join(content.splitlines()[1:5])
        if _PRICE_RE.search(first_lines) or re.search(
            r"(?im)^\s*(?:free|custom|contact sales)\s*$", first_lines
        ):
            return heading
        return None

    @staticmethod
    def _price(content: str) -> tuple[float | None, str | None]:
        match = _PRICE_RE.search(content)
        if match:
            raw_value = match.group("symbol_value") or match.group("code_value")
            value = float(raw_value.replace(",", ""))
            currency = match.group("code")
            if currency:
                currency = "CNY" if currency.upper() == "RMB" else currency.upper()
            else:
                currency = _CURRENCY_BY_SYMBOL[match.group("symbol")]
            return value, currency
        if re.search(r"(?im)^\s*free\s*$", content):
            return 0.0, None
        return None, None

    @staticmethod
    def _billing_period(content: str) -> str | None:
        if re.search(r"(?i)(?:/|per\s+)\s*(?:user\s*/\s*)?(?:mo\.?|month(?:ly)?)\b", content):
            return "month"
        if re.search(r"(?i)(?:/|per\s+)\s*(?:user\s*/\s*)?(?:yr\.?|year(?:ly)?)\b", content):
            return "year"
        return None


__all__ = ["PricingChunker"]
