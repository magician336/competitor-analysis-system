"""Evaluator-owned pytest harness for the frozen week-three benchmarks.

This module intentionally lives outside candidate repositories and is invoked
with Python isolated mode.  It emits one machine-readable completion record
only after ``pytest.main`` returns, including exact collection and execution
counts.  The outer runner validates the per-run token and every field.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import pytest


COMPLETION_PREFIX = "CODERADAR_PYTEST_COMPLETION "
TOKEN_NAME = "CODERADAR_EVALUATOR_COMPLETION_TOKEN"


class AccountingPlugin:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--expected-tests", type=int, required=True)
    parser.add_argument("--pytest-config", type=Path, required=True)
    parser.add_argument("--noconftest", action="store_true", required=True)
    parser.add_argument("test_file", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    token = os.environ.get(TOKEN_NAME, "")
    if re.fullmatch(r"[0-9a-f]{64}", token) is None:
        print("missing evaluator completion token")
        return 2
    if args.expected_tests < 1 or not args.pytest_config.is_file():
        print("invalid evaluator pytest contract")
        return 2

    accounting = AccountingPlugin()
    exit_code = int(
        pytest.main(
            [
                "-q",
                "--noconftest",
                "-c",
                str(args.pytest_config),
                "-p",
                "no:cacheprovider",
                str(args.test_file),
            ],
            plugins=[accounting],
        )
    )
    print(
        COMPLETION_PREFIX
        + json.dumps(
            {
                "schema": "coderadar.pytest_completion.v1",
                "token": token,
                "expected": args.expected_tests,
                "collected": accounting.collected,
                "executed": accounting.executed,
                "passed": accounting.passed,
                "failed": accounting.failed,
                "skipped": accounting.skipped,
                "exit_code": exit_code,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
