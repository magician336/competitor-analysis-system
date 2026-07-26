from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from scripts.data_pipeline import _doctor_competitors


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_competitor_configuration_contains_the_five_planned_products() -> None:
    config = yaml.safe_load(
        (PROJECT_ROOT / "config" / "competitors.yaml").read_text(encoding="utf-8")
    )

    competitors = {item["id"]: item for item in config["competitors"]}
    assert set(competitors) == {
        "cursor",
        "github_copilot",
        "trae",
        "tongyi_lingma",
        "codegeex",
    }
    assert competitors["cursor"]["sources"]["github"]["enabled"] is False
    assert competitors["tongyi_lingma"]["sources"]["github"]["enabled"] is False
    assert competitors["codegeex"]["sources"]["pricing"]["enabled"] is True
    assert "Lingma" in competitors["tongyi_lingma"]["aliases"]
    assert "Qoder CN" in competitors["tongyi_lingma"]["aliases"]
    assert competitors["trae"]["sources"]["official"]["browser_fallback"][
        "enabled"
    ] is True
    assert competitors["codegeex"]["sources"]["official"]["browser_fallback"][
        "enabled"
    ] is True
    assert competitors["trae"]["sources"]["changelog"][
        "empty_result_markers"
    ] == ["No update record yet."]
    assert competitors["trae"]["sources"]["github"]["collect_releases"] is False
    assert competitors["codegeex"]["sources"]["github"][
        "collect_releases"
    ] is False
    assert competitors["trae"]["sources"]["rss"]["enabled"] is True
    assert competitors["trae"]["sources"]["rss"]["format"] == "rss"
    assert competitors["trae"]["sources"]["rss"]["evidence_level"] == "A"
    assert competitors["cursor"]["sources"]["product_docs"]["sitemap_discovery"][
        "enabled"
    ] is True
    assert competitors["github_copilot"]["sources"]["product_docs"][
        "link_discovery"
    ]["max_urls"] == 500
    assert all(
        competitor["sources"]["review"]["enabled"]
        for competitor in competitors.values()
    )
    assert all(
        competitor["sources"]["review"]["urls"]
        for competitor in competitors.values()
    )
    assert competitors["trae"]["sources"]["community"]["enabled"] is True
    assert competitors["tongyi_lingma"]["sources"]["community"]["enabled"] is True
    assert competitors["codegeex"]["sources"]["community"]["enabled"] is True
    assert competitors["codegeex"]["sources"]["security_privacy"]["enabled"] is True
    assert competitors["codegeex"]["sources"]["security_privacy"]["urls"] == [
        "https://github.com/CodeGeeX/codegeex-vscode-extension#privacy"
    ]


def test_every_enabled_web_source_has_an_https_url() -> None:
    config = yaml.safe_load(
        (PROJECT_ROOT / "config" / "competitors.yaml").read_text(encoding="utf-8")
    )

    for competitor in config["competitors"]:
        for source_name in (
            "official",
            "changelog",
            "pricing",
            "community",
            "review",
            "rss",
        ):
            source = competitor["sources"][source_name]
            if source["enabled"]:
                assert source["urls"], f"{competitor['id']}.{source_name} has no URL"
                assert all(url.startswith("https://") for url in source["urls"])


def test_doctor_rejects_unbounded_link_discovery() -> None:
    config = yaml.safe_load(
        (PROJECT_ROOT / "config" / "competitors.yaml").read_text(encoding="utf-8")
    )
    invalid = deepcopy(config)
    invalid["competitors"][0]["sources"]["product_docs"]["link_discovery"] = {
        "enabled": True,
        "max_urls": 1001,
    }

    errors, _ = _doctor_competitors(invalid)

    assert any(
        "link_discovery" in error and "allowed_path_prefixes" in error
        for error in errors
    )


def test_dimension_configuration_has_stable_d1_to_d7_mapping() -> None:
    config = yaml.safe_load(
        (PROJECT_ROOT / "config" / "dimensions.yaml").read_text(encoding="utf-8")
    )

    dimensions = config["dimensions"]
    assert {item["code"] for item in dimensions.values()} == {
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
        "D6",
        "D7",
    }
    assert all(item["keywords"] for item in dimensions.values())
    assert 0 < config["review_threshold"] < 1
