from __future__ import annotations

import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SUPPORT = ROOT / "validator_support"
MUTANTS = ROOT / "validator_assets"
JAVA_NOISE = re.compile(
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/',
    re.DOTALL,
)
COMPLETION_PREFIX = "CODERADAR_CHILD_COMPLETE task=bench_015 phase=enrollment"


def _without_java_noise(source: str) -> str:
    return JAVA_NOISE.sub(lambda match: " " * len(match.group(0)), source)


def _declared_test_count(source: str) -> int:
    return len(re.findall(r"(?<![\w.])@Test\b", _without_java_noise(source)))


def _compile_and_run(mutant: Path | None, expected_tests: int) -> tuple[str, str]:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        return "environment_error", "javac and java are required"

    production = mutant or (ROOT / "EnrollmentService.java")
    sources = [
        str(production),
        str(ROOT / "EnrollmentException.java"),
        str(ROOT / "EnrollmentServiceTest.java"),
        *(str(path) for path in sorted(SUPPORT.rglob("*.java"))),
    ]
    with tempfile.TemporaryDirectory(prefix="bench_015_") as build:
        compiled = subprocess.run(
            [javac, "-Xlint:all", "-Werror", "-d", build, *sources],
            cwd=ROOT,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
        if compiled.returncode:
            return "compile_error", (compiled.stdout + compiled.stderr).strip()
        nonce = secrets.token_hex(16)
        executed = subprocess.run(
            [
                java,
                "-ea",
                "-cp",
                build,
                "MiniJupiterRunner",
                "EnrollmentServiceTest",
                nonce,
                str(expected_tests),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=20,
        )
        output = (executed.stdout + executed.stderr).strip()
        completion_pattern = re.compile(
            rf"^{re.escape(COMPLETION_PREFIX)} nonce={nonce} "
            rf"tests={expected_tests} failed=(\d+)$"
        )
        completions = [
            match
            for line in output.splitlines()
            if (match := completion_pattern.fullmatch(line.strip()))
        ]
        if len(completions) != 1:
            return "protocol_error", output
        failed = int(completions[0].group(1))
        if executed.returncode == 0 and failed == 0:
            return "pass", output
        if executed.returncode == 1 and failed > 0:
            return "tests_failed", output
        return "protocol_error", output


def main() -> int:
    test_source = (ROOT / "EnrollmentServiceTest.java").read_text(encoding="utf-8")
    clean_test_source = _without_java_noise(test_source)
    expected_tests = _declared_test_count(test_source)
    structural_failures: list[str] = []
    if "org.junit.jupiter.api.Test" not in clean_test_source:
        structural_failures.append("tests must use the JUnit 5 @Test API")
    if expected_tests < 4:
        structural_failures.append("at least four independent @Test methods are required")
    if not re.search(
        r"ExecutorService|CountDownLatch|Thread\s*\(", clean_test_source
    ):
        structural_failures.append("a real concurrent duplicate-submission test is required")

    for repeat in range(1, 4):
        status, output = _compile_and_run(None, expected_tests)
        if status != "pass":
            print(f"bench_015 validator: FAIL - reference run {repeat}/3: {status}")
            print(output)
            return 1 if status != "environment_error" else 2

    killed: list[str] = []
    survived: list[str] = []
    infrastructure_errors: list[str] = []
    mutant_files = sorted(MUTANTS.glob("*/EnrollmentService.java"))
    for mutant in mutant_files:
        status, output = _compile_and_run(mutant, expected_tests)
        name = mutant.parent.name
        if status == "tests_failed":
            killed.append(name)
        elif status == "pass":
            survived.append(name)
        else:
            infrastructure_errors.append(f"{name}: {status}: {output}")

    if infrastructure_errors:
        print("bench_015 validator error: invalid mutation infrastructure")
        for error in infrastructure_errors:
            print(f"- {error}")
        return 2
    score = len(killed) / len(mutant_files) if mutant_files else 0.0
    print(f"bench_015 mutation score: {len(killed)}/{len(mutant_files)} = {score:.0%}")
    print("killed: " + (", ".join(killed) or "none"))
    print("survived: " + (", ".join(survived) or "none"))
    if structural_failures:
        print("bench_015 validator: FAIL - required test structure is incomplete")
        for failure in structural_failures:
            print(f"- {failure}")
        return 1
    if score < 0.75:
        print("bench_015 validator: FAIL - mutation score must be at least 75%")
        return 1
    print("bench_015 validator: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
