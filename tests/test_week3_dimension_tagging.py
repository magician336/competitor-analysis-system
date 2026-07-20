from __future__ import annotations

from typing import Any

import pytest
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from pydantic import ValidationError

from agents.dimension_tagging_agent import DimensionTaggingAgent
from schemas.document import DimensionTag, EventType
from schemas.tagging import DimensionTaggingDraft, DimensionTaggingRequest


class FakeTaggingClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def tag_dimensions(self, *, prompt: str, request: DimensionTaggingRequest) -> Any:
        self.calls.append({"prompt": prompt, "request": request})
        return self.response


def test_hybrid_agreement_retains_both_audit_results() -> None:
    client = FakeTaggingClient(
        {
            "event_type": "product_release",
            "dimension_tags": ["agent_context"],
            "confidence_score": 0.91,
            "label_reasons": ["Agent 与 codebase 表明项目级上下文能力"],
            "needs_review": False,
        }
    )
    agent = DimensionTaggingAgent(llm_client=client)

    result = agent.run(
        {
            "source_type": "official_changelog",
            "title": "Agent mode update",
            "content": "The Agent now understands the codebase and repository context.",
            "strategy": "consensus",
        }
    )

    assert isinstance(agent.prompt_template, ChatPromptTemplate)
    assert isinstance(agent.pipeline, Runnable)
    assert result.analysis_mode == "hybrid"
    assert result.rule_event == EventType.PRODUCT_RELEASE
    assert result.rule_tags == [DimensionTag.AGENT_CONTEXT]
    assert result.rule_reasons
    assert result.agent_event == EventType.PRODUCT_RELEASE
    assert result.agent_tags == [DimensionTag.AGENT_CONTEXT]
    assert result.agent_reasons == ["Agent 与 codebase 表明项目级上下文能力"]
    assert result.agreement is True
    assert result.final_event == EventType.PRODUCT_RELEASE
    assert result.final_tags == [DimensionTag.AGENT_CONTEXT]
    assert result.needs_review is False
    assert "不可信" in client.calls[0]["prompt"]


def test_hybrid_disagreement_uses_union_and_forces_review() -> None:
    client = FakeTaggingClient(
        DimensionTaggingDraft(
            event_type=EventType.RISK_EXPERIENCE,
            dimension_tags=[DimensionTag.SECURITY_COMPLIANCE],
            confidence_score=0.82,
            label_reasons=["资料提到隐私风险"],
        )
    )
    agent = DimensionTaggingAgent(llm_client=client)

    result = agent.run(
        DimensionTaggingRequest(
            source_type="pricing",
            title="Student pricing and quota update",
            content="The student price and request quota changed this month.",
            strategy="union",
        )
    )

    # The rule baseline stays intact even though the injected Agent disagrees.
    assert result.rule_event == EventType.PRICING_CHANGE
    assert set(result.rule_tags) == {
        DimensionTag.PERFORMANCE_COST,
        DimensionTag.EDUCATION_FIT,
    }
    assert result.agent_event == EventType.RISK_EXPERIENCE
    assert result.agent_tags == [DimensionTag.SECURITY_COMPLIANCE]
    assert set(result.final_tags) == {
        DimensionTag.PERFORMANCE_COST,
        DimensionTag.EDUCATION_FIT,
        DimensionTag.SECURITY_COMPLIANCE,
    }
    assert result.consensus_tags == []
    assert result.final_event is None
    assert result.agreement is False
    assert result.disagreements
    assert result.needs_review is True


def test_no_llm_is_explicit_rules_mode() -> None:
    agent = DimensionTaggingAgent()

    result = agent.run(
        {
            "source_type": "github_issue",
            "title": "Privacy vulnerability report",
            "content": "A security vulnerability may expose private user data.",
        }
    )

    assert result.analysis_mode == "rules"
    assert result.rule_event == EventType.RISK_EXPERIENCE
    assert result.rule_tags == [DimensionTag.SECURITY_COMPLIANCE]
    assert result.agent_event is None
    assert result.agent_tags == []
    assert result.agent_confidence is None
    assert result.final_event == result.rule_event
    assert result.final_tags == result.rule_tags
    assert all(not reason.startswith("agent:") for reason in result.rule_reasons)


def test_invalid_llm_output_falls_back_without_overwriting_rules() -> None:
    client = FakeTaggingClient(
        {
            "event_type": "not_an_event",
            "dimension_tags": ["not_a_dimension"],
            "confidence_score": 1.5,
            "label_reasons": [],
            "needs_review": False,
            "unexpected": "must be rejected",
        }
    )
    agent = DimensionTaggingAgent(llm_client=client)

    result = agent.run(
        {
            "source_type": "github_issue",
            "title": "Agent security issue",
            "content": "The Agent executed a dangerous command and exposed private data.",
        }
    )

    assert result.analysis_mode == "hybrid_fallback"
    assert result.rule_event == EventType.RISK_EXPERIENCE
    assert set(result.rule_tags) == {
        DimensionTag.AGENT_CONTEXT,
        DimensionTag.SECURITY_COMPLIANCE,
    }
    assert result.agent_event is None
    assert result.agent_tags == []
    assert result.final_event == result.rule_event
    assert result.final_tags == result.rule_tags
    assert result.needs_review is True
    assert result.warnings == [
        "invalid LLM tagging output; retained rule baseline: ValidationError"
    ]


def test_tagging_contracts_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DimensionTaggingRequest.model_validate(
            {
                "source_type": "rss",
                "title": "Release",
                "content": "New model released.",
                "extra": "not allowed",
            }
        )

    with pytest.raises(ValidationError):
        DimensionTaggingDraft.model_validate(
            {
                "event_type": "product_release",
                "dimension_tags": ["model_extensibility"],
                "confidence_score": 0.8,
                "label_reasons": ["new model"],
                "needs_review": False,
                "extra": "not allowed",
            }
        )
