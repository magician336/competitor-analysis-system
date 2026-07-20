from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

from validator_assets.mutants import MUTANTS


ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "validator_assets" / "run_suite.py"
COMPLETION_PREFIX = "CODERADAR_BENCH_008_SUITE_COMPLETION "
COMPLETION_KEYS = {
    "schema",
    "token",
    "variant",
    "exit_code",
    "collected",
    "executed",
    "passed",
    "failed",
    "skipped",
}


def _run(variant: str) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None, str]:
    token = secrets.token_hex(32)
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "-s", str(RUNNER), variant, token],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    lines = [
        line[len(COMPLETION_PREFIX) :]
        for line in result.stdout.splitlines()
        if line.startswith(COMPLETION_PREFIX)
    ]
    if len(lines) != 1:
        return result, None, "missing or duplicate protected suite completion record"
    try:
        record = json.loads(lines[0])
    except json.JSONDecodeError:
        return result, None, "invalid protected suite completion JSON"
    if not isinstance(record, dict) or set(record) != COMPLETION_KEYS:
        return result, None, "invalid protected suite completion fields"
    if (
        record["schema"] != "coderadar.bench_008.suite_completion.v1"
        or record["token"] != token
        or record["variant"] != variant
        or record["exit_code"] != result.returncode
    ):
        return result, None, "protected suite completion identity mismatch"
    counts = [record[name] for name in ("collected", "executed", "passed", "failed", "skipped")]
    if any(type(value) is not int or value < 0 for value in counts):
        return result, None, "invalid protected suite completion counts"
    if (
        record["collected"] == 0
        or record["executed"] != record["collected"]
        or record["passed"] + record["failed"] + record["skipped"] != record["executed"]
        or record["skipped"] != 0
    ):
        return result, None, "suite did not execute every collected test without skips"
    return result, record, ""


def main() -> int:
    baseline, baseline_record, baseline_error = _run("baseline")
    if baseline_error or baseline_record is None or baseline.returncode != 0:
        print("bench_008 validator: FAIL - generated tests fail on the reference parser")
        if baseline_error:
            print(baseline_error)
        print((baseline.stdout + baseline.stderr).strip())
        return 1

    killed: list[str] = []
    survived: list[str] = []
    for mutant in MUTANTS:
        result, record, error = _run(mutant)
        if error or record is None:
            print(f"bench_008 validator: FAIL - {mutant}: {error}")
            print((result.stdout + result.stderr).strip())
            return 1
        if record["collected"] != baseline_record["collected"]:
            print(
                "bench_008 validator: FAIL - collected test count changed between "
                f"baseline and {mutant}"
            )
            return 1
        if result.returncode == 0:
            if record["failed"] != 0 or record["passed"] != record["collected"]:
                print(f"bench_008 validator: FAIL - inconsistent passing record for {mutant}")
                return 1
            survived.append(mutant)
        elif result.returncode == 1 and record["failed"] > 0:
            killed.append(mutant)
        else:
            print(
                f"bench_008 validator: FAIL - {mutant} ended abnormally "
                f"(exit {result.returncode})"
            )
            return 1

    score = len(killed) / len(MUTANTS)
    print(f"bench_008 mutation score: {len(killed)}/{len(MUTANTS)} = {score:.0%}")
    print("killed: " + (", ".join(killed) or "none"))
    print("survived: " + (", ".join(survived) or "none"))
    if score < 0.80:
        print("bench_008 validator: FAIL - mutation score must be at least 80%")
        return 1
    print("bench_008 validator: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
