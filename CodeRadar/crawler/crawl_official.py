"""Official product-page collector."""

from .base import PageCollector
from .models import SourceType


class OfficialCollector(PageCollector):
    """Persist current snapshots of official product pages."""

    source_type = SourceType.OFFICIAL


# Compatibility name for existing imports in early project scaffolding.
OfficialCrawler = OfficialCollector


__all__ = ["OfficialCollector", "OfficialCrawler"]
