from __future__ import annotations

import json
from pathlib import Path

import pytest

from crawler.storage import RawWriter
from scripts import data_pipeline
from schemas.document import StructuredDocument


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _prepare_doctor_test(monkeypatch) -> None:
    monkeypatch.setattr(data_pipeline.sys, "version_info", (3, 11, 9))
    monkeypatch.setattr(
        data_pipeline.importlib,
        "import_module",
        lambda _module_name: object(),
    )


def test_doctor_passes_in_project_venv_and_never_prints_token(
    monkeypatch,
    capsys,
) -> None:
    _prepare_doctor_test(monkeypatch)
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.setattr(data_pipeline.sys, "prefix", str(PROJECT_ROOT / ".venv"))
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-that-must-not-appear")

    exit_code = data_pipeline.main(["doctor", "--log-level", "ERROR"])
    output = capsys.readouterr()

    assert exit_code == 0
    assert "[OK] Python: 3.11.9" in output.out
    assert "[OK] 运行环境: 项目 .venv" in output.out
    assert "[OK] 运行依赖: 可导入" in output.out
    assert f"[OK] 依赖清单: {PROJECT_ROOT / 'requirement.txt'}" in output.out
    assert "[OK] 竞品配置" in output.out
    assert "[OK] 标签配置" in output.out
    assert "GitHub Token: 已配置（值未显示）" in output.out
    assert "secret-token-that-must-not-appear" not in output.out + output.err


@pytest.mark.parametrize(
    ("conda_default_env", "conda_prefix", "python_prefix"),
    [
        ("CodeRadar", None, "D:/Python311"),
        (None, "D:/Miniconda/envs/CodeRadar", "D:/Python311"),
        (None, None, "D:/Miniconda/envs/CodeRadar"),
    ],
)
def test_doctor_accepts_all_coderadar_conda_signals(
    monkeypatch,
    capsys,
    conda_default_env,
    conda_prefix,
    python_prefix,
) -> None:
    _prepare_doctor_test(monkeypatch)
    if conda_default_env is None:
        monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    else:
        monkeypatch.setenv("CONDA_DEFAULT_ENV", conda_default_env)
    if conda_prefix is None:
        monkeypatch.delenv("CONDA_PREFIX", raising=False)
    else:
        monkeypatch.setenv("CONDA_PREFIX", conda_prefix)
    monkeypatch.setattr(data_pipeline.sys, "prefix", python_prefix)

    exit_code = data_pipeline.main(["doctor", "--log-level", "ERROR"])
    output = capsys.readouterr()

    assert exit_code == 0
    assert "[OK] 运行环境: Conda CodeRadar" in output.out
    assert "[OK] 运行依赖: 可导入" in output.out


def test_doctor_reports_missing_langchain_dependencies_without_secrets(
    monkeypatch,
    capsys,
) -> None:
    _prepare_doctor_test(monkeypatch)
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "CodeRadar")
    monkeypatch.setattr(data_pipeline.sys, "prefix", "D:/Miniconda/envs/CodeRadar")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key-that-must-not-appear")

    missing_modules = {"langchain", "langchain_core", "langchain_deepseek"}

    def import_dependency(module_name):
        if module_name in missing_modules:
            raise ImportError(module_name)
        return object()

    monkeypatch.setattr(data_pipeline.importlib, "import_module", import_dependency)

    exit_code = data_pipeline.main(["doctor", "--log-level", "ERROR"])
    output = capsys.readouterr()

    assert exit_code == 1
    assert (
        "缺少运行依赖：LangChain, LangChain Core, LangChain DeepSeek"
        in output.out
    )
    assert "secret-key-that-must-not-appear" not in output.out + output.err


def test_doctor_requirement_path_prefers_current_and_accepts_legacy(
    monkeypatch,
    tmp_path,
) -> None:
    project_root = tmp_path / "CodeRadar"
    project_root.mkdir()
    monkeypatch.setattr(data_pipeline, "PROJECT_ROOT", project_root)

    current = project_root / "requirement.txt"
    legacy_root = project_root / "requirements.txt"
    legacy_docs = tmp_path / "docs" / "requirement.txt"
    current.touch()
    legacy_root.touch()

    assert data_pipeline._doctor_requirement_path() == current

    current.unlink()
    assert data_pipeline._doctor_requirement_path() == legacy_root

    legacy_root.unlink()
    legacy_docs.parent.mkdir()
    legacy_docs.touch()
    assert data_pipeline._doctor_requirement_path() == legacy_docs


def test_crawl_dry_run_uses_real_configuration_without_network(
    monkeypatch,
    capsys,
) -> None:
    from crawler.http_client import HttpClient

    def forbid_network(*args, **kwargs):
        raise AssertionError("crawl --dry-run attempted a network request")

    monkeypatch.setattr(HttpClient, "get", forbid_network)
    before = sorted((PROJECT_ROOT / "data" / "raw").rglob("*.meta.json"))

    exit_code = data_pipeline.main(
        [
            "crawl",
            "--competitors",
            "cursor,github_copilot",
            "--sources",
            "official,github",
            "--since-days",
            "90",
            "--max-issues",
            "10",
            "--max-comments",
            "2",
            "--dry-run",
            "--log-level",
            "ERROR",
        ]
    )
    output = capsys.readouterr()
    after = sorted((PROJECT_ROOT / "data" / "raw").rglob("*.meta.json"))

    assert exit_code == 0
    assert "dry-run: 3 个采集任务" in output.out
    assert "cursor/official" in output.out
    assert "github_copilot/official" in output.out
    assert "github_copilot/github" in output.out
    assert before == after


def test_process_cli_builds_jsonl_and_is_idempotent(
    tmp_path,
    fixture_text,
    monkeypatch,
    capsys,
) -> None:
    raw_root = tmp_path / "data" / "raw"
    record = RawWriter(raw_root, "run-001").write_bytes(
        competitor="cursor",
        source_type="official",
        requested_url="https://cursor.com/",
        canonical_url="https://cursor.com/",
        payload=fixture_text("pages/official.html").encode(),
        content_type="text/html",
        source_metadata={"competitor_name": "Cursor", "evidence_level": "A"},
    )
    monkeypatch.setattr(data_pipeline, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(data_pipeline, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(
        data_pipeline,
        "DIMENSION_CONFIG",
        PROJECT_ROOT / "config" / "dimensions.yaml",
    )

    first_exit = data_pipeline.main(["process", "--log-level", "ERROR"])
    first_output = capsys.readouterr().out
    second_exit = data_pipeline.main(
        ["process", "--force", "--log-level", "ERROR"]
    )
    second_output = capsys.readouterr().out
    rebuild_exit = data_pipeline.main(
        ["process", "--rebuild", "--log-level", "ERROR"]
    )
    rebuild_output = capsys.readouterr().out

    output_path = tmp_path / "data" / "cleaned" / "documents.jsonl"
    rows = [
        StructuredDocument.model_validate(json.loads(line))
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert first_exit == second_exit == rebuild_exit == 0
    assert "scanned=1 processed=1 generated=1" in first_output
    assert "added=1 unchanged=0" in first_output
    assert "added=0 unchanged=1" in second_output
    assert "added=1 unchanged=0" in rebuild_output
    assert len(rows) == 1
    assert rows[0].raw_record_id == record.raw_record_id
    assert rows[0].competitor == "Cursor"


def test_process_cli_returns_runtime_error_when_no_record_can_be_processed(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    RawWriter(tmp_path / "data" / "raw", "run-001").write_bytes(
        competitor="cursor",
        source_type="github_issue",
        requested_url="https://api.github.com/repos/example/project/issues/1",
        canonical_url="https://github.com/example/project/issues/1",
        payload=b'{"kind":"github_issue","item":',
        content_type="application/json",
    )
    monkeypatch.setattr(data_pipeline, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(data_pipeline, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(
        data_pipeline,
        "DIMENSION_CONFIG",
        PROJECT_ROOT / "config" / "dimensions.yaml",
    )

    exit_code = data_pipeline.main(["process", "--log-level", "ERROR"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "processed=0" in output
    assert "raw_record_id=" in output


def test_parser_accepts_all_documented_runtime_options() -> None:
    args = data_pipeline.build_parser().parse_args(
        [
            "all",
            "--competitors",
            "all",
            "--sources",
            "official,github_issue",
            "--since-days",
            "30",
            "--max-issues",
            "5",
            "--max-comments",
            "1",
            "--dry-run",
            "--force",
            "--rebuild",
            "--log-level",
            "WARNING",
        ]
    )

    assert args.command == "all"
    assert args.competitors == "all"
    assert args.sources == "official,github_issue"
    assert args.since_days == 30
    assert args.max_issues == 5
    assert args.max_comments == 1
    assert args.dry_run is args.force is args.rebuild is True
    assert args.log_level == "WARNING"
