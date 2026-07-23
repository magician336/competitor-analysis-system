from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent


COMPLETION = "CODERADAR_BENCH_010_CHECKS_COMPLETE tests=5 failed=0"


def run(command: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode:
        print(result.stdout + result.stderr, file=sys.stderr)
    return result


def main() -> int:
    cmake, ctest = shutil.which("cmake"), shutil.which("ctest")
    if not cmake or not ctest:
        print("validator error: cmake and ctest are required", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="coderadar-bench010-") as temporary:
        build = Path(temporary) / "build"
        if run([cmake, "-S", str(ROOT), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release"]).returncode:
            return 1
        if run([cmake, "--build", str(build), "--config", "Release"]).returncode:
            return 1
        result = run(
            [
                ctest,
                "--test-dir",
                str(build),
                "-C",
                "Release",
                "-V",
                "--output-on-failure",
            ]
        )
        output = result.stdout + result.stderr
        if result.returncode != 0:
            return result.returncode
        if output.count(COMPLETION) != 1:
            print(
                "bench_010 validator: FAIL - protected C++ completion record missing",
                file=sys.stderr,
            )
            return 1
        print("bench_010 validator: PASS")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
