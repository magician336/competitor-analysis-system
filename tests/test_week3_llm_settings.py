from __future__ import annotations

import pytest
from langchain_core.runnables import Runnable

from agents.llm import LLMSettings, LangChainLLMClient


def test_rules_mode_does_not_construct_remote_client(monkeypatch) -> None:
    monkeypatch.setenv("CODERADAR_AGENT_MODE", "rules")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    assert LangChainLLMClient.from_env() is None


@pytest.mark.parametrize("mode", ["llm", "hybrid"])
def test_online_modes_require_explicit_key(monkeypatch, mode: str) -> None:
    monkeypatch.setenv("CODERADAR_AGENT_MODE", mode)
    # An explicit empty value prevents the developer's local .env from being
    # loaded into this isolated contract test.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        LangChainLLMClient.from_env()


def test_production_chatdeepseek_chains_construct_without_network(monkeypatch) -> None:
    monkeypatch.setenv("CODERADAR_AGENT_MODE", "hybrid")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-construction-only")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    client = LangChainLLMClient.from_env()

    assert client is not None
    assert client.model_name == "deepseek-chat"
    assert client.supports_react is True
    assert isinstance(client._card_chain, Runnable)
    assert isinstance(client._tagging_chain, Runnable)


def test_settings_reject_blank_model(monkeypatch) -> None:
    monkeypatch.setenv("CODERADAR_AGENT_MODE", "rules")
    monkeypatch.setenv("DEEPSEEK_MODEL", "")

    with pytest.raises(ValueError):
        LLMSettings.from_env()


def test_request_mode_override_does_not_mutate_process_environment(monkeypatch) -> None:
    monkeypatch.setenv("CODERADAR_AGENT_MODE", "rules")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "configured-for-construction")

    settings = LLMSettings.from_env(mode="hybrid")

    assert settings.mode == "hybrid"
    assert LLMSettings.from_env().mode == "rules"
