"""Pure exact-deduplication helper for in-memory callers."""

from collections.abc import Iterable

from schemas.document import StructuredDocument


def deduplicate_documents(documents: Iterable[StructuredDocument]) -> list[StructuredDocument]:
    """Keep the latest observation of every stable content version."""

    unique: dict[str, StructuredDocument] = {}
    for document in documents:
        previous = unique.get(document.version_id)
        if previous is None or document.crawl_time >= previous.crawl_time:
            unique[document.version_id] = document
    return list(unique.values())
