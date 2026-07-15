"""Conservative bilingual query expansion for dual retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field

from mini_rag.models import RAGQuery


DEFAULT_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "价格": ("定价", "费用", "pricing", "price"),
    "定价": ("价格", "费用", "pricing"),
    "pricing": ("price", "cost", "价格", "定价"),
    "套餐": ("plan", "subscription", "订阅"),
    "智能体": ("agent", "agentic"),
    "agent": ("智能体", "automation"),
    "代码补全": ("code completion", "autocomplete"),
    "安全": ("security", "privacy", "compliance"),
    "发布": ("release", "changelog", "update"),
}


@dataclass(frozen=True)
class RewrittenQuery:
    original: str
    keyword_query: str
    semantic_query: str
    expanded_terms: list[str] = field(default_factory=list)


class QueryRewriter:
    """Add a small controlled synonym set to the BM25 expression."""

    def __init__(self, expansions: dict[str, tuple[str, ...]] | None = None) -> None:
        self.expansions = expansions or DEFAULT_EXPANSIONS

    def rewrite(self, query: str | RAGQuery) -> RewrittenQuery:
        original = query.question if isinstance(query, RAGQuery) else str(query).strip()
        folded = original.casefold()
        additions: list[str] = []
        for phrase, related in self.expansions.items():
            if phrase.casefold() in folded:
                additions.extend(related)
        additions = list(dict.fromkeys(term for term in additions if term.casefold() not in folded))
        keyword_query = " ".join([original, *additions]).strip()
        return RewrittenQuery(
            original=original,
            keyword_query=keyword_query,
            semantic_query=original,
            expanded_terms=additions,
        )

    def __call__(self, query: str | RAGQuery) -> RewrittenQuery:
        return self.rewrite(query)


__all__ = ["DEFAULT_EXPANSIONS", "QueryRewriter", "RewrittenQuery"]
