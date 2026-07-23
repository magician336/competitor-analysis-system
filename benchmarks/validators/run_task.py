"""Audit and execute the frozen week-three benchmark protocol.

Validator commands are JSON argument arrays and are always executed with
``shell=False``.  This keeps command interpretation identical on Windows and
Unix and prevents task metadata from becoming a shell-injection surface.

These controls protect benchmark protocol files only. They are not an
operating-system sandbox and do not claim to restrict candidate network or
filesystem access. Run untrusted candidates in a disposable, least-privilege
container or virtual machine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.benchmark import BenchmarkTask, BenchmarkTaskType


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPOSITORIES_ROOT = (PROJECT_ROOT / "benchmarks" / "repositories").resolve()
TASKS_PATH = PROJECT_ROOT / "benchmarks" / "tasks" / "tasks.jsonl"
PYTEST_HARNESS_PATH = Path(__file__).with_name("pytest_harness.py").resolve()
PYTEST_COMPLETION_PREFIX = "CODERADAR_PYTEST_COMPLETION "
SHELL_TOKENS = {"&&", "||", ";", "|", ">", ">>", "<", "2>", "2>&1"}
ENVIRONMENT_ALLOWLIST = {
    "PATH",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "JAVA_HOME",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "OS",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
}
SENSITIVE_ENVIRONMENT_NAME = re.compile(
    r"(?:KEY|TOKEN|SECRET|AUTH|PASSWORD|PASSWD|CREDENTIAL|COOKIE)",
    re.IGNORECASE,
)
RESERVED_CANDIDATE_HOOK_NAMES = {
    "conftest.py",
    "pytest.ini",
    "pyproject.toml",
    "tox.ini",
    "setup.cfg",
    "sitecustomize.py",
    "usercustomize.py",
    "pytest.py",
}
TREE_EXCLUDED_DIRECTORIES = {".git", ".pytest_cache", "__pycache__"}
TREE_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ValidatorSpec(BaseModel):
    """Frozen, portable command contract stored beside every task."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    task_id: str = Field(pattern=r"^bench_[0-9]{3}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    timeout_seconds: int = Field(ge=1, le=300)
    command: list[str] = Field(min_length=2, max_length=16)
    protected_paths: list[str] = Field(min_length=1, max_length=256)
    expected_starter_status: Literal["fail", "pass"] = "fail"
    manual_review_required: bool = False

    @field_validator("command")
    @classmethod
    def validate_command(cls, command: list[str]) -> list[str]:
        if command[0] != "{python}":
            raise ValueError("validator command must start with {python}")
        for argument in command:
            if not argument or "\x00" in argument or "\n" in argument or "\r" in argument:
                raise ValueError("validator command contains an invalid argument")
            if argument in SHELL_TOKENS:
                raise ValueError("shell operators are forbidden in validator commands")
            if "{" in argument or "}" in argument:
                if argument not in {
                    "{python}",
                    "{pytest_config}",
                    "{pytest_harness}",
                }:
                    raise ValueError(
                        "only {python}, {pytest_config}, and {pytest_harness} "
                        "placeholders are supported"
                    )
        return command

    @field_validator("protected_paths")
    @classmethod
    def validate_protected_paths(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_value in values:
            value = raw_value.replace("\\", "/").strip()
            parts = value.split("/")
            if (
                not value
                or "\x00" in value
                or "\n" in value
                or "\r" in value
                or value.startswith("/")
                or re.match(r"^[A-Za-z]:", value)
                or any(part in {"", ".", ".."} for part in parts)
            ):
                raise ValueError(
                    "protected_paths entries must be normalized repository-relative paths"
                )
            portable = PurePosixPath(value).as_posix()
            key = portable.casefold()
            if key in seen:
                raise ValueError("protected_paths must not contain duplicates")
            seen.add(key)
            normalized.append(portable)
        return normalized


class AssetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    task_revision: str
    task_fingerprint: str
    repository_path: str
    validator_sha256: str = Field(pattern=SHA256_PATTERN)
    protected_assets_sha256: dict[str, str]
    protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    starter_sha256: str = Field(pattern=SHA256_PATTERN)
    manual_review_required: bool
    required_assets: list[str]


class AssetAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    task_count: int
    expected_task_count_range: list[int]
    task_type_counts: dict[str, int]
    records: list[AssetRecord]
    errors: list[str]


class ValidatorRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    task_revision: str
    task_fingerprint: str
    validator_version: str
    validator_sha256: str = Field(pattern=SHA256_PATTERN)
    protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    starter_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    status: str
    passed: bool
    return_code: int | None
    duration_ms: int
    timed_out: bool
    candidate_path: str
    command: list[str]
    stdout: str
    stderr: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_sha256(repository: Path, *, task_id: str) -> str:
    """Hash the portable, reviewable file tree before evaluator staging.

    The digest is the SHA-256 of canonical JSON mapping sorted POSIX-relative
    paths to their individual SHA-256 values. Ephemeral VCS/test/bytecode files
    are excluded so the same submission has one identity across environments.
    """

    files: dict[str, str] = {}
    for path in repository.rglob("*"):
        relative = path.relative_to(repository)
        if any(part in TREE_EXCLUDED_DIRECTORIES for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(
                f"{task_id}: candidate symbolic links are not supported: "
                f"{relative.as_posix()}"
            )
        if not path.is_file() or path.suffix.casefold() in TREE_EXCLUDED_SUFFIXES:
            continue
        files[relative.as_posix()] = _sha256(path)
    canonical = json.dumps(
        files,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _official_repository(task: BenchmarkTask) -> Path:
    if task.repository_path is None:
        raise ValueError(f"{task.task_id}: repository_path is required")
    repository = (PROJECT_ROOT / task.repository_path).resolve()
    if not _inside(repository, REPOSITORIES_ROOT):
        raise ValueError(f"{task.task_id}: repository_path escapes benchmark repositories")
    if not repository.is_dir():
        raise ValueError(f"{task.task_id}: repository does not exist: {task.repository_path}")
    return repository


def _load_catalog(path: Path = TASKS_PATH) -> list[BenchmarkTask]:
    tasks: list[BenchmarkTask] = []
    task_ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            task = BenchmarkTask.model_validate_json(line)
        except Exception as exc:
            raise ValueError(f"invalid benchmark task line {line_number}: {exc}") from exc
        if task.task_id in task_ids:
            raise ValueError(f"duplicate benchmark task_id: {task.task_id}")
        task_ids.add(task.task_id)
        tasks.append(task)
    return tasks


def _load_spec(repository: Path, task: BenchmarkTask) -> tuple[ValidatorSpec, Path]:
    path = repository / "validator.json"
    if path.is_symlink():
        raise ValueError(f"{task.task_id}: validator.json must not be a symbolic link")
    if not path.is_file():
        raise ValueError(f"{task.task_id}: missing validator.json")
    try:
        spec = ValidatorSpec.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"{task.task_id}: invalid validator.json: {exc}") from exc
    if spec.task_id != task.task_id:
        raise ValueError(
            f"{task.task_id}: validator task_id mismatch ({spec.task_id})"
        )
    if spec.version != task.task_revision:
        raise ValueError(
            f"{task.task_id}: validator version {spec.version} does not match "
            f"task_revision {task.task_revision}"
        )
    if "validator.json" not in spec.protected_paths:
        raise ValueError(
            f"{task.task_id}: protected_paths must include validator.json"
        )
    if "{pytest_harness}" in spec.command:
        try:
            expected_index = spec.command.index("--expected-tests") + 1
            expected_tests = int(spec.command[expected_index])
        except (ValueError, IndexError) as exc:
            raise ValueError(
                f"{task.task_id}: pytest harness requires --expected-tests N"
            ) from exc
        if (
            spec.command.count("{pytest_harness}") != 1
            or spec.command.count("{pytest_config}") != 1
            or expected_tests < 1
            or "--noconftest" not in spec.command
        ):
            raise ValueError(f"{task.task_id}: invalid pytest harness contract")
    elif "{pytest_config}" in spec.command:
        raise ValueError(
            f"{task.task_id}: pytest_config is only valid with pytest_harness"
        )
    return spec, path


def _protected_file(repository: Path, relative_path: str, *, task_id: str) -> Path:
    """Resolve one declared file without following any symlink component."""

    current = repository
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(
                f"{task_id}: protected asset must not be a symbolic link: "
                f"{relative_path}"
            )
    if not current.exists():
        raise ValueError(f"{task_id}: protected asset is missing: {relative_path}")
    if not current.is_file():
        raise ValueError(
            f"{task_id}: protected asset must be a file, not a directory: "
            f"{relative_path}"
        )
    resolved = current.resolve()
    if not _inside(resolved, repository.resolve()):
        raise ValueError(
            f"{task_id}: protected asset escapes the repository: {relative_path}"
        )
    return resolved


def _protected_assets(
    repository: Path,
    spec: ValidatorSpec,
    *,
    task_id: str,
) -> dict[str, Path]:
    return {
        relative_path: _protected_file(
            repository,
            relative_path,
            task_id=task_id,
        )
        for relative_path in spec.protected_paths
    }


def _verify_candidate_protected_assets(
    official: Path,
    candidate: Path,
    spec: ValidatorSpec,
    *,
    task_id: str,
) -> None:
    official_assets = _protected_assets(official, spec, task_id=task_id)
    candidate_assets = _protected_assets(candidate, spec, task_id=task_id)
    for relative_path, official_path in official_assets.items():
        candidate_path = candidate_assets[relative_path]
        if _sha256(candidate_path) != _sha256(official_path):
            raise ValueError(
                f"{task_id}: protected asset was modified: {relative_path}"
            )


def _protocol_sha256(
    task: BenchmarkTask,
    spec: ValidatorSpec,
    protected_hashes: dict[str, str],
) -> str:
    canonical = json.dumps(
        {
            "task_fingerprint": task.task_fingerprint,
            "validator_version": spec.version,
            "command": spec.command,
            "manual_review_required": spec.manual_review_required,
            "protected_assets_sha256": protected_hashes,
            "evaluator_runner_sha256": _sha256(Path(__file__)),
            "pytest_harness_sha256": _sha256(PYTEST_HARNESS_PATH),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _successful_completion_attested(
    task: BenchmarkTask,
    spec: ValidatorSpec,
    stdout: str,
    stderr: str,
    completion_token: str,
) -> bool:
    """Require evaluator-owned output evidence in addition to exit code zero."""

    output = f"{stdout}\n{stderr}"
    command = spec.command
    if "{pytest_harness}" in command:
        records = [
            line[len(PYTEST_COMPLETION_PREFIX) :]
            for line in stdout.splitlines()
            if line.startswith(PYTEST_COMPLETION_PREFIX)
        ]
        if len(records) != 1:
            return False
        try:
            record = json.loads(records[0])
            expected_index = command.index("--expected-tests") + 1
            expected = int(command[expected_index])
        except (ValueError, IndexError, TypeError, json.JSONDecodeError):
            return False
        if not isinstance(record, dict) or set(record) != {
            "schema",
            "token",
            "expected",
            "collected",
            "executed",
            "passed",
            "failed",
            "skipped",
            "exit_code",
        }:
            return False
        count_names = ("expected", "collected", "executed", "passed", "failed", "skipped")
        if any(type(record[name]) is not int for name in count_names):
            return False
        return (
            record["schema"] == "coderadar.pytest_completion.v1"
            and record["token"] == completion_token
            and record["exit_code"] == 0
            and expected > 0
            and record["expected"] == expected
            and record["collected"] == expected
            and record["executed"] == expected
            and record["passed"] == expected
            and record["failed"] == 0
            and record["skipped"] == 0
        )
    return f"{task.task_id} validator: PASS" in output


def _candidate_environment(
    task: BenchmarkTask,
    run_root: Path,
    completion_token: str,
) -> dict[str, str]:
    environment: dict[str, str] = {}
    source_by_casefold = {name.casefold(): (name, value) for name, value in os.environ.items()}
    for allowed_name in ENVIRONMENT_ALLOWLIST:
        source = source_by_casefold.get(allowed_name.casefold())
        if source is None or SENSITIVE_ENVIRONMENT_NAME.search(source[0]):
            continue
        environment[allowed_name] = source[1]

    home = run_root / "home"
    temporary = run_root / "tmp"
    config = run_root / "config"
    cache = run_root / "cache"
    data = run_root / "data"
    for directory in (home, temporary, config, cache, data):
        directory.mkdir(parents=True, exist_ok=True)
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "TMPDIR": str(temporary),
            "APPDATA": str(config),
            "LOCALAPPDATA": str(data),
            "XDG_CONFIG_HOME": str(config),
            "XDG_CACHE_HOME": str(cache),
            "XDG_DATA_HOME": str(data),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
            "CODERADAR_BENCHMARK_TASK_ID": task.task_id,
            "CODERADAR_BENCHMARK_PROTOCOL": task.protocol_version,
            "CODERADAR_EVALUATOR_COMPLETION_TOKEN": completion_token,
        }
    )
    environment.setdefault("CONDA_PREFIX", sys.prefix)
    return environment


def _reject_candidate_symlinks(candidate: Path, *, task_id: str) -> None:
    for root, directories, files in os.walk(candidate, followlinks=False):
        root_path = Path(root)
        for name in [*directories, *files]:
            path = root_path / name
            if path.is_symlink():
                relative = path.relative_to(candidate).as_posix()
                raise ValueError(
                    f"{task_id}: candidate symbolic links are not supported: {relative}"
                )


def _stage_candidate(
    official: Path,
    candidate: Path,
    spec: ValidatorSpec,
    run_root: Path,
    *,
    task_id: str,
) -> Path:
    """Create an evaluator-owned candidate copy with official protocol files."""

    _reject_candidate_symlinks(candidate, task_id=task_id)
    staged = run_root / "candidate"
    shutil.copytree(
        candidate,
        staged,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", "*.pyo"),
    )
    _reject_candidate_symlinks(staged, task_id=task_id)

    official_assets = _protected_assets(official, spec, task_id=task_id)
    for relative_path, official_path in official_assets.items():
        destination = staged.joinpath(*PurePosixPath(relative_path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.is_dir():
            shutil.rmtree(destination)
        shutil.copy2(official_path, destination)

    # Candidate-owned pytest/Python startup hooks are not part of any task's
    # answer surface. Remove them from the disposable execution copy only.
    hook_paths = sorted(
        (
            path
            for path in staged.rglob("*")
            if path.name.casefold() in RESERVED_CANDIDATE_HOOK_NAMES
        ),
        key=lambda item: len(item.parts),
        reverse=True,
    )
    for path in hook_paths:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    return staged


def audit_assets(
    tasks: Iterable[BenchmarkTask] | None = None,
) -> AssetAuditReport:
    """Validate all task repositories without executing deliberately bad starters."""

    materialized = list(tasks) if tasks is not None else _load_catalog()
    errors: list[str] = []
    records: list[AssetRecord] = []
    ids = [task.task_id for task in materialized]
    if not 12 <= len(materialized) <= 20:
        errors.append(f"catalog must contain 12-20 tasks, found {len(materialized)}")
    if len(ids) != len(set(ids)):
        errors.append("catalog contains duplicate task IDs")

    type_counts = {kind.value: 0 for kind in BenchmarkTaskType}
    for task in materialized:
        type_counts[task.task_type.value] += 1
        try:
            repository = _official_repository(task)
            task_file = repository / "TASK.md"
            if not task_file.is_file() or not task_file.read_text(encoding="utf-8").strip():
                raise ValueError(f"{task.task_id}: missing or empty TASK.md")
            spec, spec_path = _load_spec(repository, task)
            protected_assets = _protected_assets(
                repository,
                spec,
                task_id=task.task_id,
            )
            protected_hashes = {
                relative_path: _sha256(path)
                for relative_path, path in protected_assets.items()
            }
            expected_command = (
                f"{sys.executable} -m benchmarks.validators.run_task "
                f"--task-id {task.task_id}"
            )
            if task.validation_command is None or task.task_id not in task.validation_command:
                raise ValueError(
                    f"{task.task_id}: validation_command must reference the frozen runner; "
                    f"example: {expected_command}"
                )
            required = list(protected_assets)
            if task.task_type == BenchmarkTaskType.EXPLANATION:
                for name in (
                    "rubric.json",
                    "reviewer_a.template.json",
                    "reviewer_b.template.json",
                    "merge_rule.json",
                    "RESPONSE.md",
                ):
                    asset = repository / name
                    if not asset.is_file() or not asset.read_text(encoding="utf-8").strip():
                        raise ValueError(f"{task.task_id}: missing or empty {name}")
                    if name not in required:
                        required.append(name)
            records.append(
                AssetRecord(
                    task_id=task.task_id,
                    task_revision=task.task_revision,
                    task_fingerprint=task.task_fingerprint,
                    repository_path=repository.relative_to(PROJECT_ROOT).as_posix(),
                    validator_sha256=_sha256(spec_path),
                    protected_assets_sha256=protected_hashes,
                    protocol_sha256=_protocol_sha256(task, spec, protected_hashes),
                    starter_sha256=_tree_sha256(
                        repository,
                        task_id=task.task_id,
                    ),
                    manual_review_required=spec.manual_review_required,
                    required_assets=required,
                )
            )
        except Exception as exc:
            errors.append(str(exc))

    return AssetAuditReport(
        status="ready" if not errors else "invalid",
        task_count=len(materialized),
        expected_task_count_range=[12, 20],
        task_type_counts=type_counts,
        records=records,
        errors=errors,
    )


def run_task(task_id: str, candidate_path: Path | None = None) -> ValidatorRunResult:
    """Run one validator after protocol checks, not as an OS security sandbox."""

    tasks = {task.task_id: task for task in _load_catalog()}
    try:
        task = tasks[task_id]
    except KeyError as exc:
        raise ValueError(f"unknown benchmark task_id: {task_id}") from exc
    official = _official_repository(task)
    spec, spec_path = _load_spec(official, task)
    candidate_input = (candidate_path or official).expanduser()
    if candidate_input.is_symlink():
        raise ValueError("candidate repository must not be a symbolic link")
    candidate = candidate_input.resolve()
    if not candidate.is_dir():
        raise ValueError(f"candidate repository does not exist: {candidate}")
    _verify_candidate_protected_assets(
        official,
        candidate,
        spec,
        task_id=task.task_id,
    )
    _reject_candidate_symlinks(candidate, task_id=task.task_id)
    protected_hashes = {
        relative_path: _sha256(path)
        for relative_path, path in _protected_assets(
            official,
            spec,
            task_id=task.task_id,
        ).items()
    }
    validator_sha256 = _sha256(spec_path)
    protocol_sha256 = _protocol_sha256(task, spec, protected_hashes)
    starter_sha256 = _tree_sha256(official, task_id=task.task_id)
    candidate_sha256 = _tree_sha256(candidate, task_id=task.task_id)

    started = time.perf_counter()
    timed_out = False
    return_code: int | None = None
    stdout = ""
    stderr = ""
    status = "failed"
    with tempfile.TemporaryDirectory(prefix=f"coderadar-{task.task_id}-") as run_dir:
        run_root = Path(run_dir)
        completion_token = secrets.token_hex(32)
        pytest_config = run_root / "evaluator_pytest.ini"
        pytest_config.write_text(
            "[pytest]\naddopts =\n",
            encoding="utf-8",
        )
        staged = _stage_candidate(
            official,
            candidate,
            spec,
            run_root,
            task_id=task.task_id,
        )
        command = [
            sys.executable
            if item == "{python}"
            else str(PYTEST_HARNESS_PATH)
            if item == "{pytest_harness}"
            else str(pytest_config)
            if item == "{pytest_config}"
            else item
            for item in spec.command
        ]
        environment = _candidate_environment(task, run_root, completion_token)
        try:
            completed = subprocess.run(
                command,
                cwd=staged,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=spec.timeout_seconds,
                shell=False,
                check=False,
            )
            return_code = completed.returncode
            stdout = completed.stdout[-20_000:]
            stderr = completed.stderr[-20_000:]
            attested = completed.returncode == 0 and _successful_completion_attested(
                task,
                spec,
                completed.stdout,
                completed.stderr,
                completion_token,
            )
            if completed.returncode == 0 and not attested:
                status = "failed"
                attestation_error = (
                    "runner error: validator exited 0 without protected completion "
                    "attestation"
                )
                stderr = f"{stderr}\n{attestation_error}".strip()
            elif attested and spec.manual_review_required:
                status = "pending_manual_review"
            else:
                status = "passed" if attested else "failed"
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            status = "timeout"
            stdout = (exc.stdout or "")[-20_000:]
            stderr = (exc.stderr or "")[-20_000:]
        except OSError as exc:
            status = "tool_error"
            stderr = str(exc)
    duration_ms = round((time.perf_counter() - started) * 1000)
    return ValidatorRunResult(
        task_id=task.task_id,
        task_revision=task.task_revision,
        task_fingerprint=task.task_fingerprint,
        validator_version=spec.version,
        validator_sha256=validator_sha256,
        protocol_sha256=protocol_sha256,
        starter_sha256=starter_sha256,
        candidate_sha256=candidate_sha256,
        status=status,
        passed=status == "passed",
        return_code=return_code,
        duration_ms=duration_ms,
        timed_out=timed_out,
        candidate_path=str(candidate),
        command=command,
        stdout=stdout,
        stderr=stderr,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit or run the frozen third-week benchmark protocol.",
        epilog=(
            "shell=False and protected_paths preserve protocol integrity; they do "
            "not restrict candidate network/filesystem access. Run untrusted "
            "candidates in a disposable restricted container or VM."
        ),
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--audit", action="store_true", help="audit all task assets")
    action.add_argument("--task-id", help="run one bench_NNN task")
    parser.add_argument(
        "--candidate",
        type=Path,
        help="candidate repository; defaults to the deliberately failing starter",
    )
    parser.add_argument("--output", type=Path, help="also write the JSON result")
    return parser


def _write_output(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: AssetAuditReport | ValidatorRunResult
        if args.audit:
            if args.candidate is not None:
                raise ValueError("--candidate can only be used with --task-id")
            result = audit_assets()
            exit_code = 0 if result.status == "ready" else 2
        else:
            result = run_task(str(args.task_id), args.candidate)
            exit_code = 0 if result.passed else 1
        payload = result.model_dump(mode="json")
    except Exception as exc:
        payload = {"status": "configuration_error", "error": str(exc)}
        exit_code = 2
    if args.output:
        _write_output(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
