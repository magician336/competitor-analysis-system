"""Starter implementation for the grade conversion task."""


def grade(score: object) -> str:
    """Return the letter grade for *score*.

    The starter deliberately implements only the happy path. Validation and
    exact boundary behavior are part of the benchmark task.
    """

    if score > 90:  # type: ignore[operator]
        return "A"
    if score > 80:  # type: ignore[operator]
        return "B"
    if score > 70:  # type: ignore[operator]
        return "C"
    if score > 60:  # type: ignore[operator]
        return "D"
    return "F"
