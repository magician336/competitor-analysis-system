from __future__ import annotations

import yaml

from mini_rag.config import load_settings


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
    assert settings.documents_path == (tmp_path / "fixtures" / "documents.jsonl").resolve()

