from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agents.benchmark_agent import BenchmarkAgent
from benchmarks.validators.run_task import REPOSITORIES_ROOT, ValidatorSpec, run_task


def test_all_sixteen_benchmark_assets_are_auditable() -> None:
    report = BenchmarkAgent().audit_assets()

    assert report.status == "ready", report.errors
    assert report.task_count == 16
    assert len(report.records) == 16
    assert set(report.task_type_counts.values()) == {2}
    assert all(len(record.starter_sha256) == 64 for record in report.records)


def test_validator_contract_rejects_shell_operators_and_unknown_placeholders() -> None:
    base = {
        "task_id": "bench_001",
        "version": "1.1.0",
        "timeout_seconds": 30,
        "protected_paths": ["validator.json"],
    }
    with pytest.raises(ValidationError, match="shell operators"):
        ValidatorSpec.model_validate({**base, "command": ["{python}", "&&"]})
    with pytest.raises(ValidationError, match="placeholder"):
        ValidatorSpec.model_validate({**base, "command": ["{python}", "{repo}"]})


@pytest.mark.parametrize(
    "protected_paths",
    [
        [],
        ["../checks.py"],
        ["/etc/passwd"],
        ["C:/Windows/system.ini"],
        ["checks.py", "CHECKS.py"],
    ],
)
def test_validator_contract_rejects_invalid_protected_paths(protected_paths) -> None:
    with pytest.raises(ValidationError, match="protected_paths"):
        ValidatorSpec.model_validate(
            {
                "task_id": "bench_001",
                "version": "1.1.0",
                "timeout_seconds": 30,
                "command": ["{python}", "checks.py"],
                "protected_paths": protected_paths,
            }
        )


def test_starter_run_returns_structured_expected_failure() -> None:
    result = run_task("bench_001")

    assert result.status == "failed"
    assert result.passed is False
    assert result.return_code not in (None, 0)
    assert result.timed_out is False
    assert result.task_fingerprint.startswith("sha256:")


def test_runner_cli_emits_json_and_never_requires_a_shell() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.validators.run_task",
            "--task-id",
            "bench_001",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["status"] == "failed"
    assert payload["command"][0] == sys.executable


def _copy_candidate(tmp_path: Path, repository_name: str) -> Path:
    candidate = tmp_path / repository_name
    shutil.copytree(REPOSITORIES_ROOT / repository_name, candidate)
    return candidate


def test_modified_checks_are_rejected_before_subprocess(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidate = _copy_candidate(tmp_path, "python_grade")
    (candidate / "checks.py").write_text(
        "def test_bypass(): assert True\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("tampered candidate must never execute")
        ),
    )

    with pytest.raises(ValueError, match="protected asset was modified: checks.py"):
        run_task("bench_001", candidate)


def test_modified_mutation_asset_is_rejected_before_subprocess(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidate = _copy_candidate(tmp_path, "python_parser")
    (candidate / "validator_assets" / "mutants.py").write_text(
        "MUTANTS = []\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("tampered candidate must never execute")
        ),
    )

    with pytest.raises(
        ValueError,
        match="protected asset was modified: validator_assets/mutants.py",
    ):
        run_task("bench_008", candidate)


def test_protected_path_replaced_by_directory_is_rejected(tmp_path: Path) -> None:
    candidate = _copy_candidate(tmp_path, "python_grade")
    (candidate / "checks.py").unlink()
    (candidate / "checks.py").mkdir()

    with pytest.raises(ValueError, match="must be a file, not a directory"):
        run_task("bench_001", candidate)


def test_untouched_candidate_copy_is_executed(tmp_path: Path) -> None:
    candidate = _copy_candidate(tmp_path, "python_grade")

    result = run_task("bench_001", candidate)

    assert result.status == "failed"
    assert result.return_code not in (None, 0)
    assert str(candidate.resolve()) == result.candidate_path
    assert result.candidate_sha256 == result.starter_sha256
    assert len(result.validator_sha256) == 64
    assert len(result.protocol_sha256) == 64


def test_candidate_environment_excludes_credentials_and_uses_temporary_homes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidate = _copy_candidate(tmp_path, "python_grade")
    observed: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        observed.update(kwargs["env"])
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-test-token")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_task("bench_001", candidate)

    assert result.status == "failed"
    assert "DEEPSEEK_API_KEY" not in observed
    assert "GITHUB_TOKEN" not in observed
    assert observed["HOME"] == observed["USERPROFILE"]
    assert observed["TEMP"] == observed["TMP"]
    assert observed["HOME"] != os.path.expanduser("~")
    assert "coderadar-bench_001-" in observed["HOME"]


def test_candidate_startup_hooks_and_pytest_configuration_cannot_fake_pass(
    tmp_path: Path,
) -> None:
    candidate = _copy_candidate(tmp_path, "python_grade")
    markers = {
        name: tmp_path / f"executed-{name}.txt"
        for name in ("conftest", "sitecustomize", "pytest_module", "pytest_package")
    }
    (candidate / "conftest.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(markers['conftest'])!r}).write_text('executed')\n"
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    session.exitstatus = 0\n",
        encoding="utf-8",
    )
    for filename, marker_name in (
        ("sitecustomize.py", "sitecustomize"),
        ("pytest.py", "pytest_module"),
    ):
        (candidate / filename).write_text(
            "import os\nfrom pathlib import Path\n"
            f"Path({str(markers[marker_name])!r}).write_text('executed')\n"
            "os._exit(0)\n",
            encoding="utf-8",
        )
    pytest_package = candidate / "pytest"
    pytest_package.mkdir()
    (pytest_package / "__init__.py").write_text("", encoding="utf-8")
    (pytest_package / "__main__.py").write_text(
        "import os\nfrom pathlib import Path\n"
        f"Path({str(markers['pytest_package'])!r}).write_text('executed')\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    (candidate / "pytest.ini").write_text(
        "[pytest]\naddopts = --collect-only\n",
        encoding="utf-8",
    )
    (candidate / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = '--collect-only'\n",
        encoding="utf-8",
    )
    (candidate / "setup.cfg").write_text(
        "[tool:pytest]\naddopts = --collect-only\n",
        encoding="utf-8",
    )
    (candidate / "tox.ini").write_text(
        "[pytest]\naddopts = --collect-only\n",
        encoding="utf-8",
    )

    result = run_task("bench_001", candidate)

    assert result.status == "failed"
    assert result.passed is False
    assert result.return_code not in (None, 0)
    assert not any(marker.exists() for marker in markers.values())
    assert "-I" in result.command
    assert "--noconftest" in result.command


def test_zero_exit_without_protected_completion_is_not_a_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidate = _copy_candidate(tmp_path, "python_grade")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    result = run_task("bench_001", candidate)

    assert result.status == "failed"
    assert result.passed is False
    assert result.return_code == 0
    assert "without protected completion attestation" in result.stderr


def test_printed_pytest_summary_then_early_exit_cannot_fake_completion(
    tmp_path: Path,
) -> None:
    candidate = _copy_candidate(tmp_path, "python_grade")
    (candidate / "grading.py").write_text(
        "import os\nprint('20 passed', flush=True)\nos._exit(0)\n",
        encoding="utf-8",
    )

    result = run_task("bench_001", candidate)

    assert result.return_code == 0
    assert result.status == "failed"
    assert result.passed is False
    assert "without protected completion attestation" in result.stderr


def test_pytest_harness_accepts_exact_complete_passing_run(tmp_path: Path) -> None:
    candidate = _copy_candidate(tmp_path, "python_grade")
    (candidate / "grading.py").write_text(
        "import math\n"
        "def grade(score):\n"
        "    if isinstance(score, bool) or not isinstance(score, (int, float)):\n"
        "        raise ValueError('score must be a number')\n"
        "    if not math.isfinite(score) or not 0 <= score <= 100:\n"
        "        raise ValueError('score out of range')\n"
        "    if score >= 90: return 'A'\n"
        "    if score >= 80: return 'B'\n"
        "    if score >= 70: return 'C'\n"
        "    if score >= 60: return 'D'\n"
        "    return 'F'\n",
        encoding="utf-8",
    )

    result = run_task("bench_001", candidate)

    assert result.return_code == 0
    assert result.status == "passed"
    assert result.passed is True
    assert "\"collected\":20" in result.stdout
    assert result.candidate_sha256 != result.starter_sha256


def test_parser_child_early_exit_is_not_counted_as_mutation_success(
    tmp_path: Path,
) -> None:
    candidate = _copy_candidate(tmp_path, "python_parser")
    (candidate / "test_generated.py").write_text(
        "import os\nos._exit(0)\n",
        encoding="utf-8",
    )

    result = run_task("bench_008", candidate)

    assert result.status == "failed"
    assert result.passed is False
    assert "missing or duplicate protected suite completion record" in result.stdout


def test_manual_review_task_cannot_become_final_pass_from_automatic_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidate = _copy_candidate(tmp_path, "explanation_lower_bound")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="bench_007 validator: PASS automatic gates; manual review remains\n",
            stderr="",
        ),
    )

    result = run_task("bench_007", candidate)

    assert result.status == "pending_manual_review"
    assert result.passed is False
