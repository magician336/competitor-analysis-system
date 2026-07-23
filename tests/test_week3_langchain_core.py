from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from langchain_core.runnables import Runnable, RunnableLambda

from agents import PriceAgent, ProductAgent, RiskAgent
from agents.llm import (
    LLMCapabilityImpactDraft,
    LLMCardDraft,
    LLMFindingDraft,
    LLMSettings,
    LangChainLLMClient,
)
from mini_rag.models import Conflict, Evidence, RAGQuery, RAGResponse, RetrievalTrace
from schemas.document import DimensionTag, EventType, EvidenceLevel, SourceType
from schemas.intelligence_card import (
    AgentAnalysisRequest,
    AlertLevel,
    FindingType,
    ImpactDirection,
    RiskLevel,
)


class StaticRAGService:
    """Completely offline RAG double that still exercises the LCEL tool."""

    def __init__(
        self,
        evidence: list[Evidence] | None = None,
        conflicts: list[Conflict] | None = None,
    ) -> None:
        self.evidence = evidence or []
        self.conflicts = conflicts or []
        self.queries: list[RAGQuery] = []

    def query(self, request: RAGQuery) -> RAGResponse:
        self.queries.append(request)
        return RAGResponse(
            query=request.question,
            parsed_filters=request.filters(),
            evidence=[item.model_copy(deep=True) for item in self.evidence],
            conflicts=[item.model_copy(deep=True) for item in self.conflicts],
            retrieval_trace=RetrievalTrace(
                returned_candidates=len(self.evidence),
                latency_ms=0.1,
            ),
        )


def _price_evidence(
    chunk_id: str = "chunk_price_primary",
    *,
    content: str = "The student plan costs 20 USD and includes 500 premium requests.",
    title: str = "Student pricing update",
) -> Evidence:
    return Evidence(
        chunk_id=chunk_id,
        document_id=f"doc_{chunk_id}",
        version_id=f"version_{chunk_id}",
        content=content,
        quote=content,
        title=title,
        url=f"https://example.test/pricing/{chunk_id}",
        char_start=0,
        char_end=len(content),
        competitor="Cursor",
        source_type=SourceType.PRICING,
        evidence_level=EvidenceLevel.A,
        event_type=EventType.PRICING_CHANGE,
        dimension_tags=[DimensionTag.PERFORMANCE_COST],
        publish_time=datetime.now(timezone.utc),
        final_score=0.95,
    )


def _valid_draft(chunk_id: str = "chunk_price_primary") -> LLMCardDraft:
    return LLMCardDraft(
        event_title="Structured pricing intelligence",
        summary="The cited student plan changes the cost baseline.",
        change_before="The previous student allowance was unknown.",
        change_after="The plan now documents a 20 USD price and request allowance.",
        impact_analysis="The cited price changes the unit-cost comparison.",
        relevance_to_our_product="CodeMate Campus should compare its campus quota.",
        threat_level=RiskLevel.CRITICAL,
        opportunity="Publish a transparent education quota comparison.",
        threat="A lower effective competitor price can weaken differentiation.",
        recommended_action="Verify the plan monthly and retain the source snapshot.",
        confidence_score=0.99,
        findings=[
            LLMFindingDraft(
                finding_type=FindingType.FACT,
                title="Documented student price",
                summary="The official evidence states the student price and allowance.",
                evidence_chunk_ids=[chunk_id],
                confidence_score=0.99,
            )
        ],
        impact_details=[
            LLMCapabilityImpactDraft(
                dimension=DimensionTag.PERFORMANCE_COST,
                direction=ImpactDirection.NEGATIVE,
                magnitude=10,
                confidence_score=0.99,
                rationale="The cited plan directly affects comparative cost.",
                evidence_chunk_ids=[chunk_id],
            )
        ],
    )


def _offline_llm_client(
    runnable: Runnable,
    *,
    max_retries: int = 0,
) -> LangChainLLMClient:
    return LangChainLLMClient(
        settings=LLMSettings(
            mode="llm",
            model="offline-runnable-test",
            max_retries=max_retries,
        ),
        structured_card_runnable=runnable,
    )


@pytest.mark.parametrize("agent_type", [PriceAgent, ProductAgent, RiskAgent])
def test_specialist_pipeline_is_real_langchain_core_runnable(agent_type) -> None:
    agent = agent_type(StaticRAGService(), auto_configure_llm=False)

    assert isinstance(agent.pipeline, Runnable)
    assert isinstance(agent._rag_tool, Runnable)
    assert agent.uses_langchain is True
    assert agent.pipeline.__class__.__module__.startswith("langchain_core")


def test_structured_draft_composes_citations_without_mutating_priority_or_alert() -> None:
    service = StaticRAGService([_price_evidence()])
    rules_card = PriceAgent(service, auto_configure_llm=False).run(
        AgentAnalysisRequest(competitor="Cursor")
    ).cards[0]
    client = _offline_llm_client(RunnableLambda(lambda _prompt: _valid_draft()))

    result = PriceAgent(
        service,
        llm_client=client,
        auto_configure_llm=False,
    ).run(AgentAnalysisRequest(competitor="Cursor"))
    card = result.cards[0]

    assert card.analysis_mode == "hybrid"
    assert card.model_name == "offline-runnable-test"
    assert card.event_title == "Structured pricing intelligence"
    assert card.priority_breakdown == rules_card.priority_breakdown
    assert card.priority_score == rules_card.priority_score
    assert card.alert_level == rules_card.alert_level
    assert card.threat_level == RiskLevel.CRITICAL
    assert card.findings[0].evidence_chunk_ids == ["chunk_price_primary"]
    assert card.impact_details[0].evidence_chunk_ids == ["chunk_price_primary"]
    assert card.capability_impact[DimensionTag.PERFORMANCE_COST] == 10
    assert not any("fallback" in warning.casefold() for warning in result.warnings)


def test_unknown_model_chunk_id_triggers_rules_fallback_warning() -> None:
    service = StaticRAGService([_price_evidence()])
    client = _offline_llm_client(
        RunnableLambda(lambda _prompt: _valid_draft("chunk_not_retrieved"))
    )

    result = PriceAgent(
        service,
        llm_client=client,
        auto_configure_llm=False,
    ).run(AgentAnalysisRequest(competitor="Cursor"))
    card = result.cards[0]

    assert card.analysis_mode == "rules"
    assert card.findings[0].evidence_chunk_ids == ["chunk_price_primary"]
    assert any(
        "fallback" in warning.casefold() and "chunk_not_retrieved" in warning
        for warning in result.warnings
    )
    assert result.trace is not None
    assert result.trace.fallback_used is True


def test_lcel_structured_output_retries_once_then_succeeds() -> None:
    attempts = 0

    def flaky_structured_output(_prompt: Any) -> LLMCardDraft:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("transient offline failure")
        return _valid_draft()

    client = _offline_llm_client(
        RunnableLambda(flaky_structured_output),
        max_retries=1,
    )
    result = PriceAgent(
        StaticRAGService([_price_evidence()]),
        llm_client=client,
        auto_configure_llm=False,
    ).run(AgentAnalysisRequest(competitor="Cursor"))

    assert attempts == 2
    assert result.cards[0].analysis_mode == "hybrid"
    assert not any("fallback" in warning.casefold() for warning in result.warnings)


def test_no_evidence_is_degraded_and_requires_review() -> None:
    result = PriceAgent(
        StaticRAGService(),
        auto_configure_llm=False,
    ).run(AgentAnalysisRequest(competitor="Cursor"))
    card = result.cards[0]

    assert result.evidence_count == 0
    assert result.degraded is True
    assert card.review_required is True
    assert card.confidence_score <= 0.3
    assert card.priority_score <= 39
    assert card.alert_level == AlertLevel.BLUE
    assert card.threat_level == RiskLevel.UNKNOWN
    assert card.assumptions
    assert any("no RAG evidence" in warning for warning in result.warnings)


def test_rag_conflict_is_preserved_as_structured_review_note() -> None:
    first = _price_evidence(
        "chunk_price_20",
        content="The student plan costs 20 USD per month.",
        title="Price page snapshot A",
    )
    second = _price_evidence(
        "chunk_price_30",
        content="The student plan costs 30 USD per month.",
        title="Price page snapshot B",
    )
    conflict = Conflict(
        field="monthly_price",
        competitor="Cursor",
        values=["20 USD", "30 USD"],
        chunk_ids=[first.chunk_id, second.chunk_id],
        preferred_chunk_id=second.chunk_id,
        reason="Two current official snapshots disagree.",
    )

    result = PriceAgent(
        StaticRAGService([first, second], [conflict]),
        auto_configure_llm=False,
    ).run(AgentAnalysisRequest(competitor="Cursor"))
    card = result.cards[0]

    assert card.review_required is True
    assert result.degraded is True
    assert len(card.conflict_notes) == 1
    assert "monthly_price" in card.conflict_notes[0]
    assert first.chunk_id in card.conflict_notes[0]
    assert second.chunk_id in card.conflict_notes[0]
    assert card.conflict_notes[0] in result.warnings


def test_trace_contains_chain_and_tool_lifecycle_events() -> None:
    result = PriceAgent(
        StaticRAGService([_price_evidence()]),
        auto_configure_llm=False,
    ).run(AgentAnalysisRequest(competitor="Cursor", correlation_id="trace-test"))

    assert result.trace is not None
    start_stages = {
        event.stage for event in result.trace.events if event.event == "start"
    }
    assert "RunnableSequence" in start_stages
    assert "price_prepare" in start_stages
    assert "price_rag_tool" in start_stages
    assert "retrieve_price_evidence" in start_stages
    assert "price_guard_and_structure" in start_stages
    assert any(event.event == "end" for event in result.trace.events)
