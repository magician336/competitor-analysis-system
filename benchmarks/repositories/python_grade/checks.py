import math

import pytest

from grading import grade


@pytest.mark.parametrize(
    ("score", "expected"),
    [(100, "A"), (95.5, "A"), (89, "B"), (75, "C"), (65, "D"), (0, "F")],
)
def test_representative_scores(score, expected):
    assert grade(score) == expected


@pytest.mark.parametrize(
    ("score", "expected"),
    [(90, "A"), (80, "B"), (70, "C"), (60, "D"), (59.999, "F")],
)
def test_exact_boundaries(score, expected):
    assert grade(score) == expected


@pytest.mark.parametrize(
    "invalid",
    [-1, 100.001, "90", None, True, False, math.nan, math.inf, -math.inf],
)
def test_invalid_input_raises_value_error(invalid):
    with pytest.raises(ValueError):
        grade(invalid)
