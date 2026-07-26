from __future__ import annotations

import gzip

import pytest
import responses

import crawler.sitemap_loader as sitemap_module
from crawler.base import ConfiguredPageCollector
from crawler.http_client import HttpClient, HttpClientConfig
from crawler.models import CollectorTask, SourceType
from crawler.sitemap_loader import SitemapDiscoverySettings, parse_sitemap
from crawler.storage import RawWriter
from processing.pipeline import ProcessingPipeline
from schemas.document import StructuredDocument


def _client() -> HttpClient:
    return HttpClient(
        HttpClientConfig(
            retries=0,
            backoff_factor=0,
            requests_per_second=0,
            trust_environment=False,
            respect_robots_txt=False,
        )
    )


def _page(title: str) -> str:
    return (
        f"<html><head><title>{title}</title></head><body><main><h1>{title}</h1>"
        "<p>Configuration-driven sitemap discovery collects bounded official "
        "documentation pages for evidence processing and audit.</p></main></body></html>"
    )


def _task(
    *,
    seed: str,
    sitemap_urls: list[str],
    **settings: object,
) -> CollectorTask:
    return CollectorTask(
        "cursor",
        "product_docs",
        urls=(seed,),
        metadata={
            "evidence_level": "A",
            "sitemap_discovery": {
                "enabled": True,
                "urls": sitemap_urls,
                **settings,
            },
        },
    )


@responses.activate
def test_urlset_is_saved_and_discovered_pages_are_canonical_and_deduplicated(
    tmp_path,
) -> None:
    seed = "https://example.test/docs"
    sitemap = "https://example.test/sitemap.xml"
    page_a = "https://example.test/docs/a?b=2&z=1"
    page_b = "https://example.test/docs/b"
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.test/docs#top</loc></url>
      <url><loc>https://example.test/docs/a?z=1&amp;b=2#section</loc></url>
      <url><loc>https://example.test/docs/a?b=2&amp;z=1#duplicate</loc></url>
      <url><loc>https://example.test/docs/b</loc></url>
      <url><loc>https://outside.test/docs/c</loc></url>
    </urlset>"""
    responses.add(responses.GET, seed, body=_page("Seed"), status=200)
    responses.add(
        responses.GET,
        sitemap,
        body=sitemap_xml,
        status=200,
        content_type="application/xml",
    )
    responses.add(responses.GET, page_a, body=_page("Page A"), status=200)
    responses.add(responses.GET, page_b, body=_page("Page B"), status=200)
    writer = RawWriter(tmp_path / "data" / "raw", "run-urlset")
    collector = ConfiguredPageCollector(
        _client(),
        writer,
        SourceType.PRODUCT_DOCS,
    )

    result = collector.collect(
        _task(
            seed=seed,
            sitemap_urls=[sitemap],
            allowed_path_prefixes=["/docs"],
            max_urls=10,
            max_sitemaps=2,
        )
    )

    assert result.failure_count == 0
    sitemap_records = [
        record
        for record in result.records
        if record.source_metadata.get("record_role") == "discovery_sitemap"
    ]
    assert len(sitemap_records) == 1
    assert sitemap_records[0].source_metadata["sitemap_kind"] == "urlset"
    assert (writer.raw_root / sitemap_records[0].payload_path).read_bytes() == (
        sitemap_xml.encode()
    )
    page_records = [
        record
        for record in result.records
        if record.source_metadata.get("record_role") != "discovery_sitemap"
    ]
    assert [record.requested_url for record in page_records] == [seed, page_a, page_b]
    assert "discovery" not in page_records[0].source_metadata
    assert all(
        record.source_metadata["discovery"]["method"] == "sitemap"
        for record in page_records[1:]
    )


@responses.activate
def test_nested_gzip_sitemap_obeys_strict_sitemap_limit(tmp_path) -> None:
    seed = "https://example.test/docs"
    root = "https://example.test/sitemap-index.xml"
    nested_gzip = "https://example.test/sitemaps/one.xml.gz"
    not_fetched = "https://example.test/sitemaps/two.xml"
    discovered = "https://example.test/docs/from-gzip"
    root_xml = f"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>{nested_gzip}</loc></sitemap>
      <sitemap><loc>{not_fetched}</loc></sitemap>
    </sitemapindex>"""
    nested_xml = f"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>{discovered}#release</loc></url>
    </urlset>"""
    responses.add(responses.GET, seed, body=_page("Seed"), status=200)
    responses.add(
        responses.GET,
        root,
        body=root_xml,
        status=200,
        content_type="application/xml",
    )
    responses.add(
        responses.GET,
        nested_gzip,
        body=gzip.compress(nested_xml.encode()),
        status=200,
        content_type="application/gzip",
    )
    responses.add(responses.GET, discovered, body=_page("Nested"), status=200)
    writer = RawWriter(tmp_path / "data" / "raw", "run-index")

    result = ConfiguredPageCollector(
        _client(),
        writer,
        SourceType.PRODUCT_DOCS,
    ).collect(
        _task(
            seed=seed,
            sitemap_urls=[root],
            max_urls=10,
            max_sitemaps=2,
        )
    )

    assert result.failure_count == 0
    sitemap_records = [
        record
        for record in result.records
        if record.source_metadata.get("record_role") == "discovery_sitemap"
    ]
    assert [record.requested_url for record in sitemap_records] == [root, nested_gzip]
    assert sitemap_records[0].source_metadata["sitemap_kind"] == "sitemapindex"
    assert sitemap_records[1].source_metadata["sitemap_gzip"] is True
    assert any(record.requested_url == discovered for record in result.records)
    assert not any(call.request.url == not_fetched for call in responses.calls)


@responses.activate
def test_sitemap_path_filters_and_url_limit_are_strict(tmp_path) -> None:
    seed = "https://example.test/docs"
    sitemap = "https://example.test/sitemap.xml"
    accepted = (
        "https://example.test/docs/one",
        "https://example.test/docs/two",
    )
    sitemap_xml = """<urlset>
      <url><loc>https://example.test/docs/one</loc></url>
      <url><loc>https://example.test/docs/private/hidden</loc></url>
      <url><loc>https://example.test/blog/outside-prefix</loc></url>
      <url><loc>https://example.test/docs/two</loc></url>
      <url><loc>https://example.test/docs/three</loc></url>
    </urlset>"""
    responses.add(responses.GET, seed, body=_page("Seed"), status=200)
    responses.add(
        responses.GET,
        sitemap,
        body=sitemap_xml,
        status=200,
        content_type="text/xml",
    )
    for url in accepted:
        responses.add(responses.GET, url, body=_page(url.rsplit("/", 1)[-1]), status=200)

    result = ConfiguredPageCollector(
        _client(),
        RawWriter(tmp_path / "data" / "raw", "run-filter"),
        SourceType.PRODUCT_DOCS,
    ).collect(
        _task(
            seed=seed,
            sitemap_urls=[sitemap],
            allowed_path_prefixes=["docs"],
            excluded_path_prefixes=["/docs/private"],
            max_urls=2,
            max_sitemaps=1,
        )
    )

    requested_pages = [
        record.requested_url
        for record in result.records
        if record.source_metadata.get("discovery", {}).get("method") == "sitemap"
        and record.source_metadata.get("record_role") != "discovery_sitemap"
    ]
    assert result.failure_count == 0
    assert requested_pages == list(accepted)


@responses.activate
def test_sitemap_failure_is_auditable_and_does_not_block_seed(tmp_path) -> None:
    seed = "https://example.test/docs"
    sitemap = "https://example.test/sitemap.xml"
    responses.add(responses.GET, seed, body=_page("Seed"), status=200)
    responses.add(
        responses.GET,
        sitemap,
        body="temporarily unavailable",
        status=503,
        content_type="text/plain",
    )
    writer = RawWriter(tmp_path / "data" / "raw", "run-failure")

    result = ConfiguredPageCollector(
        _client(),
        writer,
        SourceType.PRODUCT_DOCS,
    ).collect(_task(seed=seed, sitemap_urls=[sitemap]))

    seed_record = next(record for record in result.records if record.requested_url == seed)
    error_record = next(record for record in result.records if record.error)
    assert seed_record.http_status == 200
    assert result.success_count == 1
    assert result.failure_count == 1
    assert error_record.http_status == 503
    assert error_record.payload_path is not None
    assert error_record.source_metadata["record_role"] == "discovery_sitemap"


def test_processing_treats_sitemap_payload_as_expected_skip(tmp_path) -> None:
    raw_root = tmp_path / "data" / "raw"
    record = RawWriter(raw_root, "run-processing").write_bytes(
        competitor="cursor",
        source_type="product_docs",
        requested_url="https://example.test/sitemap.xml",
        canonical_url="https://example.test/sitemap.xml",
        payload=b"<urlset><url><loc>https://example.test/docs/a</loc></url></urlset>",
        content_type="application/xml",
        source_metadata={"record_role": "discovery_sitemap"},
    )
    meta_path = next(raw_root.rglob(f"{record.raw_record_id}.meta.json"))

    result = ProcessingPipeline(project_root=tmp_path).process([meta_path])

    assert result.succeeded is True
    assert result.scanned == 1
    assert result.skipped == 1
    assert result.generated_documents == 0
    assert result.failures == []


@responses.activate
def test_request_override_source_metadata_selects_json_items_per_url(
    tmp_path,
) -> None:
    first_public = "https://example.test/plugins/first"
    second_public = "https://example.test/plugins/second"
    first_api = "https://api.example.test/first"
    second_api = "https://api.example.test/second"
    responses.add(
        responses.GET,
        first_api,
        json={"items": [{"id": 1, "headline": "First release", "body": "Agent one"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        second_api,
        json={
            "data": {
                "rows": [
                    {"key": "two", "version": "2.0.0", "notes": "Agent two"}
                ]
            }
        },
        status=200,
    )
    writer = RawWriter(tmp_path / "data" / "raw", "run-overrides")
    task = CollectorTask(
        "codegeex",
        "plugin_marketplace",
        urls=(first_public, second_public),
        metadata={
            "evidence_level": "B",
            "json_items": {
                "result_path": "unused",
                "title_field": "unused",
                "content_fields": ["unused"],
            },
            "request_overrides": {
                first_public: {
                    "url": first_api,
                    "source_metadata": {
                        "json_items": {
                            "result_path": "items",
                            "title_field": "headline",
                            "content_fields": ["body"],
                            "identity_field": "id",
                        }
                    },
                },
                second_public: {
                    "url": second_api,
                    "source_metadata": {
                        "json_items": {
                            "result_path": "data.rows",
                            "title_field": "version",
                            "content_fields": ["notes"],
                            "identity_field": "key",
                        }
                    },
                },
            },
        },
    )

    collection = ConfiguredPageCollector(
        _client(),
        writer,
        SourceType.PLUGIN_MARKETPLACE,
    ).collect(task)

    assert collection.failure_count == 0
    assert collection.records[0].source_metadata["json_items"]["result_path"] == "items"
    assert (
        collection.records[1].source_metadata["json_items"]["result_path"]
        == "data.rows"
    )
    processing = ProcessingPipeline(project_root=tmp_path).process()
    documents = [
        StructuredDocument.model_validate_json(line)
        for line in processing.output_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert processing.succeeded is True
    assert {document.title for document in documents} == {"First release", "2.0.0"}


@responses.activate
def test_external_sitemap_redirect_is_rejected_before_external_request(
    tmp_path,
) -> None:
    seed = "https://example.test/docs"
    sitemap = "https://example.test/sitemap.xml"
    external = "https://outside.test/sitemap.xml"
    responses.add(responses.GET, seed, body=_page("Seed"), status=200)
    responses.add(
        responses.GET,
        sitemap,
        status=302,
        headers={"Location": external},
    )

    result = ConfiguredPageCollector(
        _client(),
        RawWriter(tmp_path / "data" / "raw", "run-redirect"),
        SourceType.PRODUCT_DOCS,
    ).collect(_task(seed=seed, sitemap_urls=[sitemap]))

    assert result.success_count == 1
    assert result.failure_count == 1
    assert not any(call.request.url == external for call in responses.calls)
    error_record = next(record for record in result.records if record.error)
    assert "outside configured seed origins" in error_record.error
    assert error_record.source_metadata["redirect_rejected"] is True
    assert error_record.payload_path is None


@responses.activate
def test_path_prefix_uses_segment_boundary_and_rejects_encoded_traversal(
    tmp_path,
) -> None:
    seed = "https://example.test/seed"
    sitemap = "https://example.test/sitemap.xml"
    allowed = (
        "https://example.test/docs",
        "https://example.test/docs/guide",
        "https://example.test/docs/safe",
    )
    sitemap_xml = """<urlset>
      <url><loc>https://example.test/docs</loc></url>
      <url><loc>https://example.test/docs/guide</loc></url>
      <url><loc>https://example.test/docs-evil</loc></url>
      <url><loc>https://example.test/docs/%2e%2e/admin</loc></url>
      <url><loc>https://example.test/docs/%252e%252e/admin</loc></url>
      <url><loc>https://example.test/docs/%255cadmin</loc></url>
      <url><loc>https://example.test/docs/%25255cadmin</loc></url>
      <url><loc>https://example.test/docs/safe</loc></url>
    </urlset>"""
    responses.add(responses.GET, seed, body=_page("Seed"), status=200)
    responses.add(
        responses.GET,
        sitemap,
        body=sitemap_xml,
        status=200,
        content_type="application/xml",
    )
    for url in allowed:
        responses.add(responses.GET, url, body=_page(url), status=200)

    result = ConfiguredPageCollector(
        _client(),
        RawWriter(tmp_path / "data" / "raw", "run-path-security"),
        SourceType.PRODUCT_DOCS,
    ).collect(
        _task(
            seed=seed,
            sitemap_urls=[sitemap],
            allowed_path_prefixes=["/docs/"],
        )
    )

    discovered = [
        record.requested_url
        for record in result.records
        if record.source_metadata.get("discovery", {}).get("method") == "sitemap"
        and record.source_metadata.get("record_role") != "discovery_sitemap"
    ]
    assert result.failure_count == 0
    assert discovered == list(allowed)


def test_sitemap_parser_enforces_response_and_decompression_limits(
    monkeypatch,
) -> None:
    source_url = "https://example.test/sitemap.xml"
    plain = b"<urlset><url><loc>https://example.test/docs</loc></url></urlset>"
    monkeypatch.setattr(sitemap_module, "MAX_SITEMAP_RESPONSE_BYTES", 32)
    with pytest.raises(ValueError, match="response exceeds hard byte limit"):
        parse_sitemap(plain, source_url=source_url)

    monkeypatch.setattr(sitemap_module, "MAX_SITEMAP_RESPONSE_BYTES", 1_024)
    monkeypatch.setattr(sitemap_module, "MAX_SITEMAP_DECOMPRESSED_BYTES", 32)
    compressed = gzip.compress(plain)
    with pytest.raises(ValueError, match="decompressed sitemap exceeds"):
        parse_sitemap(compressed, source_url=source_url)


def test_sitemap_parser_enforces_loc_and_element_limits(monkeypatch) -> None:
    source_url = "https://example.test/sitemap.xml"
    two_locations = b"""<urlset>
      <url><loc>https://example.test/docs/one</loc></url>
      <url><loc>https://example.test/docs/two</loc></url>
    </urlset>"""
    monkeypatch.setattr(sitemap_module, "MAX_SITEMAP_LOCATIONS", 1)
    with pytest.raises(ValueError, match="hard loc limit"):
        parse_sitemap(two_locations, source_url=source_url)

    monkeypatch.setattr(sitemap_module, "MAX_SITEMAP_LOCATIONS", 10)
    monkeypatch.setattr(sitemap_module, "MAX_SITEMAP_LOC_LENGTH", 10)
    with pytest.raises(ValueError, match="hard character limit"):
        parse_sitemap(two_locations, source_url=source_url)

    monkeypatch.setattr(sitemap_module, "MAX_SITEMAP_LOC_LENGTH", 8_192)
    monkeypatch.setattr(sitemap_module, "MAX_SITEMAP_XML_ELEMENTS", 2)
    with pytest.raises(ValueError, match="hard element limit"):
        parse_sitemap(two_locations, source_url=source_url)


def test_sitemap_configuration_rejects_values_above_global_caps() -> None:
    base = {
        "enabled": True,
        "urls": ["https://example.test/sitemap.xml"],
    }
    with pytest.raises(ValueError, match="max_urls exceeds global maximum"):
        SitemapDiscoverySettings.from_mapping(
            {
                **base,
                "max_urls": sitemap_module.MAX_CONFIGURED_URLS + 1,
            }
        )
    with pytest.raises(ValueError, match="max_sitemaps exceeds global maximum"):
        SitemapDiscoverySettings.from_mapping(
            {
                **base,
                "max_sitemaps": sitemap_module.MAX_CONFIGURED_SITEMAPS + 1,
            }
        )


@responses.activate
def test_declared_oversize_sitemap_is_rejected_without_persisting_payload(
    tmp_path,
    monkeypatch,
) -> None:
    seed = "https://example.test/docs"
    sitemap = "https://example.test/sitemap.xml"
    monkeypatch.setattr(sitemap_module, "MAX_SITEMAP_RESPONSE_BYTES", 32)
    responses.add(responses.GET, seed, body=_page("Seed"), status=200)
    responses.add(
        responses.GET,
        sitemap,
        body="<urlset></urlset>",
        status=200,
        headers={"Content-Length": "1024"},
        content_type="application/xml",
    )

    result = ConfiguredPageCollector(
        _client(),
        RawWriter(tmp_path / "data" / "raw", "run-size-limit"),
        SourceType.PRODUCT_DOCS,
    ).collect(_task(seed=seed, sitemap_urls=[sitemap]))

    assert result.failure_count == 1
    error_record = next(record for record in result.records if record.error)
    assert "hard byte limit" in error_record.error
    assert error_record.payload_path is None
    assert error_record.source_metadata["resource_limit_rejected"] is True
