from __future__ import annotations

from datetime import datetime, timedelta, timezone

from processing.normalizers import (
    detect_language,
    extract_version,
    normalize_datetime,
    normalize_text,
    normalize_url,
    normalize_version,
    sha256_text,
    stable_id,
)


def test_normalize_url_removes_tracking_fragment_and_default_port() -> None:
    url = (
        "HTTPS://Example.COM:443//docs/../pricing/?utm_source=mail&b=2&a=1"
        "#enterprise"
    )

    assert normalize_url(url) == "https://example.com/pricing?a=1&b=2"


def test_normalize_url_handles_bare_host_and_preserves_relative_path() -> None:
    assert normalize_url("example.com/pricing/") == "https://example.com/pricing"
    assert normalize_url("/pricing/?plan=pro#details") == "/pricing/?plan=pro"


def test_normalize_text_applies_nfkc_and_stable_whitespace() -> None:
    source = "  ＡＩ\u00a0 coding  \r\n\r\n\r\n  Agent\tmode  "

    assert normalize_text(source) == "AI coding\n\nAgent mode"
    assert normalize_text(source, preserve_lines=False) == "AI coding Agent mode"


def test_normalize_text_removes_private_use_icon_glyphs() -> None:
    assert normalize_text("Agent\uea75\x00\u200b task", preserve_lines=False) == "Agent task"


def test_normalize_datetime_returns_utc_for_offset_naive_and_epoch_values() -> None:
    assert normalize_datetime("2026-07-13T16:00:00+08:00") == datetime(
        2026, 7, 13, 8, tzinfo=timezone.utc
    )
    assert normalize_datetime(
        "2026-07-13 08:00:00",
        default_timezone=timezone(timedelta(hours=8)),
    ) == datetime(2026, 7, 13, 0, tzinfo=timezone.utc)
    assert normalize_datetime(1_782_850_600_000) == normalize_datetime(1_782_850_600)
    assert normalize_datetime("1782850600000") == normalize_datetime(1_782_850_600)
    assert normalize_datetime("not a date") is None


def test_version_normalization_and_extraction() -> None:
    assert normalize_version("Version v1.2") == "1.2.0"
    assert normalize_version("release 2.0.1 beta") == "2.0.1-beta"
    assert normalize_version("no explicit version") is None
    assert extract_version("Agent update", "Released in v3.4.1") == (
        "v3.4.1",
        "3.4.1",
    )


def test_hash_and_stable_id_are_deterministic_after_text_normalization() -> None:
    assert sha256_text("Agent   mode") == sha256_text("Agent mode")
    assert stable_id("doc", " Cursor ", "official") == stable_id(
        "doc", "Cursor", "official"
    )
    assert stable_id("doc", "Cursor", "official") != stable_id(
        "doc", "Cursor", "pricing"
    )


def test_language_detection_handles_chinese_english_and_empty_text() -> None:
    assert detect_language("这是一个代码智能助手。") == "zh"
    assert detect_language("An AI coding assistant") == "en"
    assert detect_language("") == "und"
