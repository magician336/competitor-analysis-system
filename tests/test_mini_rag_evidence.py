from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mini_rag.evidence import CitationBuilder, CitationValidator, ConflictDetector
from mini_rag.models import Chunk, SearchCandidate


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def make_candidate(
    chunk_id: str,
    content: str,
    *,
    url: str | None = None,
    evidence_level: str = "A",
    current: bool = True,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    plan_name: str | None = None,
    price_value: float | None = None,
    currency: str | None = None,
    metadata: dict | None = None,
) -> SearchCandidate:
    chunk = Chunk(
        chunk_id=chunk_id,
        document_id=f"doc-{chunk_id}",
        version_id=f"ver-{chunk_id}",
        raw_record_id=f"raw-{chunk_id}",
        raw_path=f"raw/{chunk_id}.html",
        chunk_index=0,
        title=f"Title {chunk_id}",
        content=content,
        char_start=0,
        char_end=len(content),
        competitor="Cursor",
        source_type="pricing" if plan_name else "official_page",
        evidence_level=evidence_level,
        url=url or f"https://example.com/{chunk_id}",
        product_version="1.2.0",
        publish_time=valid_from or NOW,
        crawl_time=NOW,
        valid_from=valid_from or NOW,
        valid_to=valid_to,
        is_current=current,
        plan_name=plan_name,
        price_value=price_value,
        currency=currency,
        billing_period="month" if plan_name else None,
        source_metadata=metadata or {},
    )
    return SearchCandidate(chunk=chunk, bm25_rank=1, dense_rank=2, rrf_score=0.03, final_score=0.9)


def test_builder_keeps_exact_quote_locator_scores_and_structured_metadata() -> None:
    content = "第一段介绍价格。\n\nAgent context window expanded to 200k tokens."
    candidate = make_candidate("one", content, plan_name="Pro", price_value=20, currency="USD")

    evidence = CitationBuilder(max_quote_characters=30).build_one(
        candidate,
        question="context window",
    )

    assert evidence.quote in evidence.content
    assert len(evidence.quote) <= 30
    assert evidence.char_end - evidence.char_start == len(content)
    assert evidence.rrf_score == 0.03
    assert evidence.metadata["plan_name"] == "Pro"
    assert evidence.metadata["price_value"] == 20


def test_validator_accepts_exact_and_ellipsised_quotes() -> None:
    evidence = CitationBuilder().build_one(make_candidate("one", "Agent context supports 200k tokens."))
    citations = [
        {
            "chunk_id": "one",
            "url": "https://example.com/one#section",
            "quote": "Agent context ... 200k tokens.",
            "title": "Title one",
            "product_version": "1.2.0",
        }
    ]

    result = CitationValidator().validate(citations, [evidence], require_quote=True)

    assert result.valid
    assert result.validated_chunk_ids == ["one"]


def test_validator_rejects_wrong_url_quote_and_query_scope() -> None:
    evidence = CitationBuilder().build_one(make_candidate("one", "Authoritative source text."))
    result = CitationValidator().validate(
        [
            {"chunk_id": "one", "url": "https://attacker.example/one", "quote": "fabricated"},
            {"chunk_id": "outside", "url": "https://example.com/outside", "quote": "text"},
        ],
        [evidence],
        allowed_chunk_ids=["one"],
        require_quote=True,
    )

    assert not result.valid
    assert any("URL does not match" in error for error in result.errors)
    assert any("cannot be located" in error for error in result.errors)
    assert any("outside the current query scope" in error for error in result.errors)
    assert result.validated_chunk_ids == []


def test_validator_rejects_version_and_source_range_mismatch() -> None:
    evidence = CitationBuilder().build_one(make_candidate("one", "Authoritative source text."))
    result = CitationValidator().validate(
        [
            {
                "chunk_id": "one",
                "url": evidence.url,
                "quote": evidence.quote,
                "version_id": "ver-wrong",
                "char_start": 4,
                "char_end": evidence.char_end,
            }
        ],
        [evidence],
    )

    assert not result.valid
    assert any("version_id does not match" in error for error in result.errors)
    assert any("char_start does not match" in error for error in result.errors)


def test_validator_marks_low_level_evidence_for_uncertainty_context() -> None:
    evidence = CitationBuilder().build_one(
        make_candidate("lead", "Community report", evidence_level="C")
    )

    result = CitationValidator().validate([evidence], [evidence], require_quote=True)

    assert result.valid
    assert any("level C" in warning for warning in result.warnings)


def test_pricing_conflict_is_structured_and_prefers_current_authority() -> None:
    older = make_candidate(
        "old",
        "Pro costs 18 USD",
        plan_name="Pro",
        price_value=18,
        currency="USD",
        evidence_level="B",
        current=False,
        valid_from=NOW - timedelta(days=100),
        valid_to=NOW + timedelta(days=1),
    )
    current = make_candidate(
        "new",
        "Pro costs 20 USD",
        plan_name="Pro",
        price_value=20,
        currency="USD",
        evidence_level="A",
        current=True,
        valid_from=NOW - timedelta(days=1),
    )

    conflicts = ConflictDetector().detect([older, current])

    price_conflict = next(conflict for conflict in conflicts if conflict.field == "price_value")
    assert set(price_conflict.values) == {"18", "20"}
    assert set(price_conflict.chunk_ids) == {"old", "new"}
    assert price_conflict.preferred_chunk_id == "new"


def test_non_overlapping_historical_prices_are_not_conflicts() -> None:
    historical = make_candidate(
        "historical",
        "old",
        plan_name="Pro",
        price_value=18,
        valid_from=NOW - timedelta(days=100),
        valid_to=NOW - timedelta(days=50),
        current=False,
    )
    current = make_candidate(
        "current",
        "new",
        plan_name="Pro",
        price_value=20,
        valid_from=NOW - timedelta(days=10),
        current=True,
    )

    assert ConflictDetector().detect([historical, current]) == []


def test_adjacent_half_open_version_intervals_are_not_conflicts() -> None:
    boundary = NOW - timedelta(days=10)
    historical = make_candidate(
        "historical-boundary",
        "old",
        plan_name="Pro",
        price_value=18,
        valid_from=NOW - timedelta(days=100),
        valid_to=boundary,
        current=False,
    )
    current = make_candidate(
        "current-boundary",
        "new",
        plan_name="Pro",
        price_value=20,
        valid_from=boundary,
        current=True,
    )

    assert ConflictDetector().detect([historical, current]) == []


def test_explicit_structured_facts_detect_status_conflict() -> None:
    left = make_candidate(
        "left",
        "feature enabled",
        metadata={
            "structured_facts": [{"field": "status", "value": "enabled", "entity": "agent_mode"}]
        },
    )
    right = make_candidate(
        "right",
        "feature beta",
        metadata={
            "structured_facts": [{"field": "status", "value": "beta", "entity": "agent_mode"}]
        },
    )

    conflicts = ConflictDetector().detect([left, right])

    assert len(conflicts) == 1
    assert conflicts[0].field == "status"
