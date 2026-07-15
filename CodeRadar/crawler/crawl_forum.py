"""Compatibility exports for the GitHub Issue acquisition source."""

from .crawl_github import GitHubCollector


class ForumCollector(GitHubCollector):
    """Alias retained for the original project layout.

    The current evidence source is the official GitHub Issue API, so forum
    acquisition uses the same implementation and emits ``github_issue`` raw
    records.
    """


__all__ = ["ForumCollector"]
