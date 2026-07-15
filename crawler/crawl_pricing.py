"""Official pricing-page collector."""

from .base import PageCollector
from .models import SourceType


class PricingCollector(PageCollector):
    """Persist current snapshots of official pricing pages."""

    source_type = SourceType.PRICING


PricingCrawler = PricingCollector


__all__ = ["PricingCollector", "PricingCrawler"]
