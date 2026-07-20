from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import parser as parser_module  # noqa: E402
from validator_assets.mutants import MUTANTS  # noqa: E402


COMPLETION_PREFIX = "CODERADAR_BENCH_008_SUITE_COMPLETION "


class _AccountingPlugin:
    def __init__(self) -> None:
        self.collected = 0
        self.executed = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when != "call":
            return
        self.executed += 1
        if report.passed:
            self.passed += 1
        elif report.failed:
            self.failed += 1
        elif report.skipped:
            self.skipped += 1


def main() -> int:
    variant = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    token = sys.argv[2] if len(sys.argv) > 2 else ""
    if not token or len(token) != 64:
        print("missing evaluator completion token", file=sys.stderr)
        return 2
    if variant != "baseline":
        try:
            parser_module.parse_kv_line = MUTANTS[variant]
        except KeyError:
            print(f"unknown mutant: {variant}", file=sys.stderr)
            return 2
    accounting = _AccountingPlugin()
    exit_code = int(
        pytest.main(
            [
                "-q",
                "-p",
                "no:cacheprovider",
                "--noconftest",
                "-o",
                "addopts=",
                str(ROOT / "test_generated.py"),
            ],
            plugins=[accounting],
        )
    )
    print(
        COMPLETION_PREFIX
        + json.dumps(
            {
                "schema": "coderadar.bench_008.suite_completion.v1",
                "token": token,
                "variant": variant,
                "exit_code": exit_code,
                "collected": accounting.collected,
                "executed": accounting.executed,
                "passed": accounting.passed,
                "failed": accounting.failed,
                "skipped": accounting.skipped,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
