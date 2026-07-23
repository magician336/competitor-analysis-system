from __future__ import annotations

from pathlib import Path

import pytest

from processing.labeling import RuleLabeler
from schemas.document import DimensionTag, EventType, SourceType


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("source_type", "expected"),
    [
        (SourceType.PRICING, EventType.PRICING_CHANGE),
        (SourceType.OFFICIAL_CHANGELOG, EventType.PRODUCT_RELEASE),
        (SourceType.GITHUB_RELEASE, EventType.PRODUCT_RELEASE),
        (SourceType.RSS, EventType.PRODUCT_RELEASE),
        (SourceType.GITHUB_ISSUE, EventType.RISK_EXPERIENCE),
        (SourceType.STATUS_PAGE, EventType.RISK_EXPERIENCE),
        (SourceType.PLUGIN_MARKETPLACE, EventType.RISK_EXPERIENCE),
        (SourceType.COMMUNITY, EventType.RISK_EXPERIENCE),
        (SourceType.REVIEW, EventType.RISK_EXPERIENCE),
        (SourceType.PRODUCT_DOCS, None),
        (SourceType.SECURITY_PRIVACY, None),
        (SourceType.BENCHMARK, None),
        (SourceType.OFFICIAL_PAGE, None),
    ],
)
def test_event_type_is_determined_by_source(source_type, expected) -> None:
    assert RuleLabeler.event_for_source(source_type) is expected


def test_default_rules_return_multi_labels_with_auditable_reasons() -> None:
    result = RuleLabeler().label(
        SourceType.OFFICIAL_CHANGELOG,
        "Agent and IDE update",
        "The agent understands repository context and ships a VS Code extension.",
    )

    assert result.event_type is EventType.PRODUCT_RELEASE
    assert result.dimension_tags == [
        DimensionTag.AGENT_CONTEXT,
        DimensionTag.IDE_ECOSYSTEM,
    ]
    assert result.label_confidence >= 0.55
    assert result.needs_review is False
    assert "source:official_changelog->product_release" in result.label_reasons
    assert any(reason.startswith("agent_context:keywords=") for reason in result.label_reasons)


def test_pricing_source_gets_e1_and_cost_dimension() -> None:
    result = RuleLabeler().label(
        SourceType.PRICING,
        "Plans and pricing",
        "The Pro plan costs $20 per month with a request limit.",
    )

    assert result.event_type is EventType.PRICING_CHANGE
    assert DimensionTag.PERFORMANCE_COST in result.dimension_tags
    assert result.needs_review is False


def test_unmatched_content_is_marked_for_review() -> None:
    result = RuleLabeler().label(
        SourceType.OFFICIAL_PAGE,
        "About",
        "General product information.",
    )

    assert result.event_type is None
    assert result.dimension_tags == []
    assert result.label_confidence == 0
    assert result.needs_review is True


def test_custom_rules_accept_dimension_codes_and_unicode_keywords() -> None:
    labeler = RuleLabeler({"D7": ["学生", "campus"]})

    result = labeler.label(
        SourceType.OFFICIAL_PAGE,
        "Campus",
        "为学生提供编程学习支持。",
    )

    assert result.dimension_tags == [DimensionTag.EDUCATION_FIT]
    assert any("学生" in reason for reason in result.label_reasons)


def test_short_latin_keywords_do_not_match_inside_unrelated_words() -> None:
    labeler = RuleLabeler(
        {
            "D2": ["agent"],
            "D3": ["ide", "git"],
        }
    )

    unrelated = labeler.label(
        SourceType.OFFICIAL_PAGE,
        "Provider update",
        "The digital sidebar provides general guidance.",
    )
    plural = labeler.label(
        SourceType.OFFICIAL_PAGE,
        "Agents and IDEs",
        "Plugins support multiple agents and IDEs.",
    )

    assert unrelated.dimension_tags == []
    assert plural.dimension_tags == [
        DimensionTag.AGENT_CONTEXT,
        DimensionTag.IDE_ECOSYSTEM,
    ]


def test_rules_load_from_project_yaml_with_configured_threshold() -> None:
    labeler = RuleLabeler.from_yaml(PROJECT_ROOT / "config" / "dimensions.yaml")

    result = labeler.label(
        SourceType.OFFICIAL_PAGE,
        "Campus security update",
        "Students can use the IDE plugin with a permission audit.",
    )

    assert labeler.review_threshold == 0.55
    assert result.dimension_tags == [
        DimensionTag.IDE_ECOSYSTEM,
        DimensionTag.SECURITY_COMPLIANCE,
        DimensionTag.EDUCATION_FIT,
    ]
    assert result.needs_review is False


@pytest.mark.parametrize("threshold", [-0.01, 1.01])
def test_review_threshold_must_be_probability(threshold) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        RuleLabeler(review_threshold=threshold)
