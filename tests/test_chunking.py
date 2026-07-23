from __future__ import annotations

from datetime import datetime, timezone

from mini_rag.chunking import (
    ChangelogChunker,
    ChunkingConfig,
    ChunkingDispatcher,
    GitHubChunker,
    OfficialPageChunker,
    PricingChunker,
    chunk_document,
)
from schemas.document import EvidenceLevel, SourceType, StructuredDocument


def _document(
    content: str,
    *,
    source_type: SourceType = SourceType.OFFICIAL_PAGE,
    title: str = "Product",
    source_metadata: dict | None = None,
    product_version: str | None = None,
) -> StructuredDocument:
    return StructuredDocument(
        raw_record_id="raw_test",
        raw_path="data/raw/test/source.txt",
        competitor="Example",
        title=title,
        content=content,
        source_type=source_type,
        evidence_level=EvidenceLevel.A,
        url="https://example.test/source",
        product_version=product_version,
        publish_time="2026-07-01T00:00:00Z",
        crawl_time="2026-07-14T00:00:00Z",
        source_metadata=source_metadata or {},
    )


def _assert_exact_source_locations(
    document: StructuredDocument, chunks: list
) -> None:
    assert chunks
    for chunk in chunks:
        assert document.content[chunk.char_start : chunk.char_end] == chunk.content
        assert chunk.char_end - chunk.char_start == len(chunk.content)
        assert chunk.source_locator.endswith(
            f"#chars={chunk.char_start}-{chunk.char_end}"
        )


def test_official_chunker_preserves_heading_path_limits_and_stable_ids() -> None:
    long_body = " ".join(f"repository-context-{index}" for index in range(45))
    document = _document(
        "# Platform\n\nOverview.\n\n## Agent Context\n\n"
        + long_body
        + "\n\n## Security\n\nEnterprise controls."
    )
    chunker = OfficialPageChunker(
        ChunkingConfig(
            target_characters=120,
            maximum_characters=160,
            overlap_characters=20,
            minimum_characters=20,
        )
    )

    first = chunker.chunk(document)
    second = chunker.chunk(document)

    _assert_exact_source_locations(document, first)
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert all(len(chunk.content) <= 160 for chunk in first)
    context_chunks = [
        chunk for chunk in first if "Agent Context" in chunk.heading_path
    ]
    assert len(context_chunks) > 1
    assert all(
        chunk.heading_path == ["Platform", "Agent Context"]
        for chunk in context_chunks
    )
    assert any(chunk.heading_path == ["Platform", "Security"] for chunk in first)


def test_changelog_chunker_keeps_versions_and_categories_separate() -> None:
    document = _document(
        "# Version 2.0.0\n\n## New features\n\nAdds workspace agents.\n\n"
        "## Known issues\n\nRemote sessions can time out.\n\n"
        "# Version 1.5.0\n\n## Fixes\n\nFixed completion latency.",
        source_type=SourceType.OFFICIAL_CHANGELOG,
        title="Release history",
    )

    chunks = ChangelogChunker().chunk(document)

    _assert_exact_source_locations(document, chunks)
    feature = next(chunk for chunk in chunks if "Adds workspace agents" in chunk.content)
    known_issue = next(
        chunk for chunk in chunks if "Remote sessions can time out" in chunk.content
    )
    fix = next(chunk for chunk in chunks if "Fixed completion latency" in chunk.content)
    assert feature.product_version == "2.0.0"
    assert feature.change_category == "features"
    assert known_issue.product_version == "2.0.0"
    assert known_issue.change_category == "known_issues"
    assert fix.product_version == "1.5.0"
    assert fix.change_category == "fixes"
    assert not any("2.0.0" in chunk.content and "1.5.0" in chunk.content for chunk in chunks)


def test_changelog_plain_version_lines_are_stable_boundaries() -> None:
    document = _document(
        "Release history\n\nVersion 2.1.0\nNew agent mode.\n\n"
        "Version 2.0.0\nPrevious completion mode.",
        source_type=SourceType.OFFICIAL_CHANGELOG,
        title="Release history",
    )

    chunks = ChangelogChunker().chunk(document)

    current = next(chunk for chunk in chunks if "New agent mode" in chunk.content)
    previous = next(
        chunk for chunk in chunks if "Previous completion mode" in chunk.content
    )
    assert current.product_version == "2.1.0"
    assert previous.product_version == "2.0.0"
    assert current.chunk_id != previous.chunk_id
    assert not any(
        "New agent mode" in chunk.content
        and "Previous completion mode" in chunk.content
        for chunk in chunks
    )


def test_chunker_omits_heading_only_parent_sections() -> None:
    document = _document(
        "# Product\n\n## Enterprise\n\n### Security\n\nPolicy controls are available.",
        source_type=SourceType.OFFICIAL_CHANGELOG,
        title="Product update",
    )

    chunks = ChangelogChunker().chunk(document)

    _assert_exact_source_locations(document, chunks)
    assert [chunk.content for chunk in chunks] == [
        "### Security\n\nPolicy controls are available."
    ]
    assert chunks[0].heading_path == ["Product", "Enterprise", "Security"]


def test_pricing_chunker_never_combines_plans_and_extracts_price_fields() -> None:
    document = _document(
        "# Pricing\n\n### Hobby\n\nFree\n\nLimited requests.\n\n"
        "### Pro\n\n$20 / mo.\n\nUnlimited completions.\n\n"
        "### Teams\n\n$40 / user / mo.\n\nCentral billing.\n\n"
        "## Questions & Answers\n\n### Are taxes included?\n\nPrices exclude tax.",
        source_type=SourceType.PRICING,
        title="Pricing",
    )

    chunks = PricingChunker().chunk(document)

    _assert_exact_source_locations(document, chunks)
    hobby = next(chunk for chunk in chunks if chunk.plan_name == "Hobby")
    pro = next(chunk for chunk in chunks if chunk.plan_name == "Pro")
    teams = next(chunk for chunk in chunks if chunk.plan_name == "Teams")
    assert hobby.price_value == 0.0
    assert pro.price_value == 20.0
    assert pro.currency == "USD"
    assert pro.billing_period == "month"
    assert teams.price_value == 40.0
    assert teams.billing_period == "month"
    assert "Teams" not in pro.content
    assert "Pro" not in teams.content
    question = next(chunk for chunk in chunks if "Prices exclude tax" in chunk.content)
    assert question.section_type == "pricing_common"
    assert question.plan_name is None
    assert question.applicable_plans == ["Hobby", "Pro", "Teams"]


def test_github_chunker_separates_issue_body_and_comment_citations() -> None:
    document = _document(
        "Agent stalls\n\nThe agent slows on large repositories.\n\n"
        "Issue state: open\n\nLabels: bug\n\n"
        "Comment by maintainer at 2026-07-03T09:00:00Z\n\n"
        "Remove generated files from the workspace.\n\n"
        "Comment by alice at 2026-07-04T10:00:00Z\n\n"
        "The workaround succeeds.",
        source_type=SourceType.GITHUB_ISSUE,
        title="Agent stalls",
        source_metadata={
            "github_kind": "github_issue",
            "repository": "example/copilot",
            "issue_number": 42,
            "issue_state": "open",
            "labels": ["bug"],
            "comments": [
                {
                    "html_url": "https://github.com/example/copilot/issues/42#issuecomment-501",
                    "created_at": "2026-07-03T09:00:00Z",
                    "author_association": "MEMBER",
                },
                {
                    "html_url": "https://github.com/example/copilot/issues/42#issuecomment-502",
                    "created_at": "2026-07-04T10:00:00Z",
                    "author_association": "NONE",
                },
            ],
        },
    )

    chunks = GitHubChunker().chunk(document)

    _assert_exact_source_locations(document, chunks)
    assert [chunk.section_type for chunk in chunks] == [
        "github_issue",
        "github_comment",
        "github_comment",
    ]
    assert all(chunk.repository == "example/copilot" for chunk in chunks)
    assert all(chunk.github_number == 42 for chunk in chunks)
    assert all(chunk.github_labels == ["bug"] for chunk in chunks)
    first_comment = chunks[1]
    assert first_comment.author_type == "maintainer"
    assert first_comment.comment_url.endswith("#issuecomment-501")
    assert first_comment.comment_time == datetime(
        2026, 7, 3, 9, tzinfo=timezone.utc
    )
    assert chunks[2].author_type == "community"
    assert chunks[1].chunk_id != chunks[2].chunk_id


def test_github_release_and_dispatcher_use_source_specific_chunkers() -> None:
    release = _document(
        "# Agent 1.2\n\nAdds repository context.",
        source_type=SourceType.GITHUB_RELEASE,
        title="Agent 1.2",
        product_version="1.2.0",
        source_metadata={
            "github_kind": "github_release",
            "repository": "example/copilot",
            "release_id": 901,
        },
    )
    dispatcher = ChunkingDispatcher()

    chunks = dispatcher.chunk(release)

    _assert_exact_source_locations(release, chunks)
    assert isinstance(dispatcher.get_chunker("official"), OfficialPageChunker)
    assert isinstance(dispatcher.get_chunker("changelog"), ChangelogChunker)
    assert isinstance(dispatcher.get_chunker("pricing"), PricingChunker)
    assert isinstance(dispatcher.get_chunker("issue"), GitHubChunker)
    assert isinstance(dispatcher.get_chunker("docs"), OfficialPageChunker)
    assert isinstance(dispatcher.get_chunker("status"), OfficialPageChunker)
    assert isinstance(dispatcher.get_chunker("marketplace"), OfficialPageChunker)
    assert isinstance(dispatcher.get_chunker("forum"), OfficialPageChunker)
    assert isinstance(dispatcher.get_chunker("reviews"), OfficialPageChunker)
    assert isinstance(dispatcher.get_chunker("privacy"), OfficialPageChunker)
    assert isinstance(dispatcher.get_chunker("benchmarks"), OfficialPageChunker)
    assert all(chunk.section_type == "github_release" for chunk in chunks)
    assert all(chunk.github_kind == "github_release" for chunk in chunks)
    assert all(chunk.repository == "example/copilot" for chunk in chunks)
    assert [chunk.chunk_id for chunk in chunks] == [
        chunk.chunk_id for chunk in chunk_document(release)
    ]
