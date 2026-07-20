from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXPECTED = "max=9 min=-2 average=3.60"


def main() -> int:
    compiler = shutil.which("gcc")
    if compiler is None:
        print("validator error: gcc is not installed", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="coderadar-bench002-") as temporary:
        executable = Path(temporary) / (
            "array_stats.exe" if sys.platform == "win32" else "array_stats"
        )
        compile_result = subprocess.run(
            [compiler, "main.c", "-std=c11", "-Wall", "-Wextra", "-Werror", "-o", str(executable)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if compile_result.returncode:
            print(compile_result.stdout + compile_result.stderr, file=sys.stderr)
            return 1
        run = subprocess.run(
            [str(executable)], cwd=ROOT, capture_output=True, text=True, timeout=10
        )
        actual = run.stdout.strip()
        if run.returncode or actual != EXPECTED:
            print(f"expected: {EXPECTED!r}\nactual:   {actual!r}", file=sys.stderr)
            return 1
    source = (ROOT / "main.c").read_text(encoding="utf-8")
    if "<= count" in source or "<=count" in source.replace(" ", ""):
        print("loop bound still permits index == count", file=sys.stderr)
        return 1
    print("bench_002 validator: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
