"""Evidence construction, validation, and structured conflict detection."""

from .citation_builder import CitationBuilder, build_evidence, select_quote
from .citation_validator import CitationValidator, validate_citations
from .conflict_detector import ConflictDetector, detect_conflicts

__all__ = [
    "CitationBuilder",
    "CitationValidator",
    "ConflictDetector",
    "build_evidence",
    "detect_conflicts",
    "select_quote",
    "validate_citations",
]

