"""Event classification compatibility API."""

from schemas.document import EventType, SourceType

from .labeling import RuleLabeler


def classify_event(source_type: SourceType | str) -> EventType | None:
    """Map a source category to its E1--E3 event label."""

    return RuleLabeler.event_for_source(source_type)
