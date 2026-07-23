from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _load_json(name: str) -> dict[str, object]:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def main() -> int:
    answer_path = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "RESPONSE.md")
    if not answer_path.is_file():
        print(f"bench_007 validator: FAIL - missing answer {answer_path.name}")
        return 2

    rubric = _load_json("rubric.json")
    merge = _load_json("merge_rule.json")
    reviewer_a = _load_json("reviewer_a.template.json")
    reviewer_b = _load_json("reviewer_b.template.json")
    metadata_errors: list[str] = []
    if rubric.get("task_id") != "bench_007" or rubric.get("maximum_score") != 10:
        metadata_errors.append("rubric metadata is invalid")
    if merge.get("required_reviewers") != 2:
        metadata_errors.append("merge rule must require two reviewers")
    if reviewer_a.get("reviewer_slot") != "A" or reviewer_b.get("reviewer_slot") != "B":
        metadata_errors.append("reviewer templates must define A and B slots")
    if metadata_errors:
        for error in metadata_errors:
            print(f"bench_007 validator error: {error}")
        return 2

    text = answer_path.read_text(encoding="utf-8")
    compact = re.sub(r"\s+", "", text).casefold()
    boundary_terms = ("空数组", "重复元素", "全部元素", "小于所有", "大于所有")
    checks = {
        "loop invariant and half-open interval": (
            "循环不变式" in text
            and ("[lo,hi)" in compact or "左闭右开" in text)
        ),
        "both bound updates": "lo=mid+1" in compact and "hi=mid" in compact,
        "termination at lo == hi": "lo==hi" in compact and "终止" in text,
        "O(log n) time": bool(re.search(r"o\s*\(\s*log\s*n\s*\)", text, re.I)),
        "O(1) extra space": bool(re.search(r"o\s*\(\s*1\s*\)", text, re.I)),
        "at least two boundary categories": sum(term in text for term in boundary_terms) >= 2,
        "common error discussed": "常见错误" in text or "错误写法" in text,
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        print("bench_007 validator: FAIL - automatic explanation gates not met")
        for failure in failures:
            print(f"- {failure}")
        print("Manual blind review is required after the automatic gates pass.")
        return 1
    print("bench_007 validator: PASS automatic gates; two-person blind review remains")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
