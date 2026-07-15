"""CodeRadar multi-source acquisition package."""

from .base import BaseCollector, PageCollector
from .crawl_changelog import ChangelogCollector
from .crawl_github import GitHubCollector
from .crawl_official import OfficialCollector
from .crawl_pricing import PricingCollector
from .http_client import (
    ConditionalRequestStore,
    HttpClient,
    HttpClientConfig,
    RobotsDeniedError,
)
from .models import (
    CollectorResult,
    CollectorTask,
    CrawlSummary,
    RawRecord,
    SourceType,
)
from .orchestrator import CrawlOrchestrator
from .storage import RawWriter


__all__ = [
    "BaseCollector",
    "ChangelogCollector",
    "CollectorResult",
    "CollectorTask",
    "ConditionalRequestStore",
    "CrawlOrchestrator",
    "CrawlSummary",
    "GitHubCollector",
    "HttpClient",
    "HttpClientConfig",
    "OfficialCollector",
    "PageCollector",
    "PricingCollector",
    "RawRecord",
    "RawWriter",
    "RobotsDeniedError",
    "SourceType",
]
