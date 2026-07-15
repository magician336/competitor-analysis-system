"""Dimension tagging compatibility API."""

from schemas.document import DimensionTag, SourceType

from .labeling import LabelResult, RuleLabeler


def tag_dimensions(title: str, content: str) -> list[DimensionTag]:
    """Return only D1--D7 tags for callers that do not need audit metadata."""

    return RuleLabeler().label(SourceType.OFFICIAL_PAGE, title, content).dimension_tags


__all__ = ["LabelResult", "RuleLabeler", "tag_dimensions"]
