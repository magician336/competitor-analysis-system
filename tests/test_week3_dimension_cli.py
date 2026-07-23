from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from agents.dimension_tagging_agent import DimensionTaggingAgent
from schemas.document import DimensionTag, EventType
from schemas.tagging import DimensionTaggingDraft, DimensionTaggingResult
from scripts.tag_dimensions import main


class OfflineTaggingClient:
    def tag_dimensions(self, *, prompt: str, request: Any) -> DimensionTaggingDraft:
        return DimensionTaggingDraft(
            event_type=EventType.PRODUCT_RELEASE,
            dimension_tags=[DimensionTag.AGENT_CONTEXT],
            confidence_score=0.9,
            label_reasons=["Agent and repository context are explicit"],
        )


def _request_payload(title: str, content: str) -> dict[str, object]:
    return {
        "source_type": "official_changelog",
        "title": title,
        "content": content,
        "strategy": "union",
    }


def test_single_rules_command_prints_one_strict_json_object(capsys) -> None:
    code = main(
        [
            "single",
            "--mode",
            "rules",
            "--source-type",
            "official_changelog",
            "--title",
            "Agent mode update",
            "--content",
            "The Agent now understands repository and codebase context.",
            "--strategy",
            "consensus",
            "--correlation-id",
            "cli-single-001",
        ]
    )

    captured = capsys.readouterr()
    result = DimensionTaggingResult.model_validate(json.loads(captured.out))
    assert code == 0
    assert captured.err == ""
    assert result.analysis_mode == "rules"
    assert result.final_event == EventType.PRODUCT_RELEASE
    assert result.final_tags == [DimensionTag.AGENT_CONTEXT]
    assert result.request.correlation_id == "cli-single-001"


def test_batch_writes_validated_jsonl_with_atomic_replacement(
    tmp_path: Path,
    capsys,
) -> None:
    input_path = tmp_path / "requests.jsonl"
    output_path = tmp_path / "nested" / "results.jsonl"
    input_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        **_request_payload(
                            "Student price update",
                            "The student price and request quota changed.",
                        ),
                        "source_type": "pricing",
                    },
                    ensure_ascii=False,
                ),
                "",
                json.dumps(
                    {
                        **_request_payload(
                            "Privacy incident",
                            "A vulnerability may expose private user data.",
                        ),
                        "source_type": "github_issue",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    code = main(
        [
            "batch",
            "--mode",
            "rules",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    results = [
        DimensionTaggingResult.model_validate_json(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert code == 0
    assert captured.err == ""
    assert summary == {
        "mode": "rules",
        "output": str(output_path),
        "processed": 2,
    }
    assert len(results) == 2
    assert results[0].final_event == EventType.PRICING_CHANGE
    assert results[1].final_event == EventType.RISK_EXPERIENCE
    assert not list(output_path.parent.glob(f".{output_path.name}.*.tmp"))


def test_invalid_batch_returns_nonzero_and_preserves_existing_output(
    tmp_path: Path,
    capsys,
) -> None:
    input_path = tmp_path / "invalid.jsonl"
    output_path = tmp_path / "results.jsonl"
    input_path.write_text(
        json.dumps(_request_payload("Valid", "Agent release information."))
        + "\n{not-json}\n",
        encoding="utf-8",
    )
    output_path.write_text("existing-delivery\n", encoding="utf-8")

    code = main(
        [
            "batch",
            "--mode",
            "rules",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    assert code != 0
    assert captured.out == ""
    assert "tag_dimensions error" in captured.err
    assert "input line 2" in captured.err
    assert "invalid JSON" in captured.err
    assert output_path.read_text(encoding="utf-8") == "existing-delivery\n"
    assert not list(tmp_path.glob(f".{output_path.name}.*.tmp"))


@pytest.mark.parametrize("mode", ["hybrid", "llm"])
def test_online_modes_use_existing_from_env_constructor_without_network(
    mode: str,
    monkeypatch,
    capsys,
) -> None:
    observed_modes: list[str | None] = []

    def fake_from_env(cls, **kwargs):
        observed_modes.append(os.getenv("CODERADAR_AGENT_MODE"))
        return cls(llm_client=OfflineTaggingClient(), **kwargs)

    monkeypatch.setattr(
        DimensionTaggingAgent,
        "from_env",
        classmethod(fake_from_env),
    )

    code = main(
        [
            "single",
            "--mode",
            mode,
            "--source-type",
            "official_changelog",
            "--title",
            "Agent context update",
            "--content",
            "The Agent understands repository context and codebase structure.",
        ]
    )

    captured = capsys.readouterr()
    result = DimensionTaggingResult.model_validate_json(captured.out)
    assert code == 0
    assert captured.err == ""
    assert observed_modes == [mode]
    assert result.analysis_mode == "hybrid"
    assert result.agent_event == EventType.PRODUCT_RELEASE
    assert result.agent_tags == [DimensionTag.AGENT_CONTEXT]


def test_online_mode_without_constructed_client_fails_clearly(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        DimensionTaggingAgent,
        "from_env",
        classmethod(lambda cls, **kwargs: cls(**kwargs)),
    )

    code = main(
        [
            "single",
            "--mode",
            "hybrid",
            "--source-type",
            "official_page",
            "--title",
            "Agent feature",
            "--content",
            "Agent context capability announcement.",
        ]
    )

    captured = capsys.readouterr()
    assert code != 0
    assert captured.out == ""
    assert "--mode hybrid requires" in captured.err
    assert "DEEPSEEK_API_KEY" in captured.err
