"""Multiply acceptance tests (seed・red 期待・maker が src/multiply.py 実装)."""

import pytest

from multiply import multiply


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (0, 0, 0),
        (1, 1, 1),
        (2, 3, 6),
        (5, 7, 35),
        (-3, 4, -12),
        (-2, -5, 10),
        (100, 100, 10000),
    ],
)
def test_happy(a, b, expected):
    assert multiply(a, b) == expected


@pytest.mark.parametrize(
    "a,b",
    [
        (1.5, 2),
        (2, "3"),
        (None, 5),
        ("a", "b"),
        (True, 3),
        (1, False),
    ],
)
def test_type_error(a, b):
    with pytest.raises(TypeError):
        multiply(a, b)
