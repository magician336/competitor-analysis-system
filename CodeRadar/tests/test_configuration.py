from __future__ import annotations

from pathlib import Path

import yaml


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
    assert competitors["codegeex"]["sources"]["pricing"]["enabled"] is False
    assert "Lingma" in competitors["tongyi_lingma"]["aliases"]
    assert "Qoder CN" in competitors["tongyi_lingma"]["aliases"]


def test_every_enabled_web_source_has_an_https_url() -> None:
    config = yaml.safe_load(
        (PROJECT_ROOT / "config" / "competitors.yaml").read_text(encoding="utf-8")
    )

    for competitor in config["competitors"]:
        for source_name in ("official", "changelog", "pricing"):
            source = competitor["sources"][source_name]
            if source["enabled"]:
                assert source["urls"], f"{competitor['id']}.{source_name} has no URL"
                assert all(url.startswith("https://") for url in source["urls"])


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
