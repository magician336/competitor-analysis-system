from __future__ import annotations

import shutil
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXPECTED_TESTS = 9


def main() -> int:
    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        print("validator error: javac and java are required", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="coderadar-bench004-") as temporary:
        build = Path(temporary)
        compiled = subprocess.run(
            [javac, "-Xlint:all", "-Werror", "-d", str(build), "Stack.java", "StackTest.java"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
        if compiled.returncode:
            print(compiled.stdout + compiled.stderr, file=sys.stderr)
            return 1
        nonce = secrets.token_hex(16)
        run = subprocess.run(
            [java, "-ea", "-cp", str(build), "StackTest", nonce],
            cwd=ROOT,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=20,
        )
        output = (run.stdout + run.stderr).strip()
        if run.returncode:
            print(output, file=sys.stderr)
            return 1
        completion = (
            "CODERADAR_CHILD_COMPLETE task=bench_004 phase=stack "
            f"nonce={nonce} tests={EXPECTED_TESTS} failed=0"
        )
        if output.splitlines().count(completion) != 1:
            print(
                "bench_004 validator: FAIL - child completion protocol missing "
                "or malformed",
                file=sys.stderr,
            )
            if output:
                print(output, file=sys.stderr)
            return 1
        print("bench_004 validator: PASS")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
