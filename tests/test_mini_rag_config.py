from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mini_rag.config import DEFAULT_PROJECT_ROOT, load_settings


def test_load_settings_resolves_paths_and_environment(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "mini_rag.yaml").write_text(
        yaml.safe_dump(
            {
                "embedding": {"dimension": 64},
                "retrieval": {"default_top_k": 4, "maximum_top_k": 10},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINIRAG_DOCUMENTS_PATH", "fixtures/documents.jsonl")
    monkeypatch.setenv("MINIRAG_EMBEDDING_DIMENSION", "128")

    settings = load_settings(project_root=tmp_path)

    assert settings.embedding.dimension == 128
    assert settings.retrieval.default_top_k == 4
    assert settings.documents_path == (
        tmp_path / "fixtures" / "documents.jsonl"
    ).resolve()


def test_load_settings_default_root_does_not_depend_on_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "MINIRAG_CONFIG_PATH",
        str(DEFAULT_PROJECT_ROOT / "config" / "mini_rag.yaml"),
    )
    monkeypatch.setenv("MINIRAG_DOCUMENTS_PATH", "data/cleaned/documents.jsonl")

    settings = load_settings()

    assert settings.project_root == DEFAULT_PROJECT_ROOT
    assert settings.documents_path == (
        DEFAULT_PROJECT_ROOT / "data" / "cleaned" / "documents.jsonl"
    ).resolve()


def test_blank_config_path_uses_selected_profile(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "mini_rag.formal.yaml").write_text(
        yaml.safe_dump({"embedding": {"provider": "sentence_transformers"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINIRAG_CONFIG_PATH", "  ")
    monkeypatch.setenv("MINIRAG_PROFILE", "formal")

    settings = load_settings(project_root=tmp_path)

    assert settings.embedding.provider == "sentence_transformers"


def test_explicit_missing_config_fails_fast(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINIRAG_CONFIG_PATH", "config/missing.yaml")

    with pytest.raises(FileNotFoundError, match="config.missing.yaml"):
        load_settings(project_root=tmp_path)

