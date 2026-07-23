from __future__ import annotations

from collections.abc import Sequence


def lower_bound(values: Sequence[int], target: int) -> int:
    """Return the first index whose value is greater than or equal to target."""

    lo, hi = 0, len(values)
    while lo < hi:
        mid = lo + (hi - lo) // 2
        if values[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo

