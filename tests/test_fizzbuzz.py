"""FizzBuzz acceptance tests (seed・契約 = .pdca/plan.yaml T1 の test_spec を実装).

PDCA loop: 初回 push で red になる前提・maker が src/fizzbuzz.py を実装して green まで loop。
このファイルは maker が 触らない (test = 受け入れ基準 = 不変・relax 禁止).
"""

import pytest

from fizzbuzz import fizzbuzz


# ----------------------------- happy path -----------------------------
@pytest.mark.parametrize(
    "n,expected",
    [
        (1, "1"),
        (2, "2"),
        (3, "Fizz"),
        (5, "Buzz"),
        (6, "Fizz"),
        (9, "Fizz"),
        (10, "Buzz"),
        (15, "FizzBuzz"),
        (30, "FizzBuzz"),
        (45, "FizzBuzz"),
        (99, "Fizz"),
    ],
)
def test_happy(n, expected):
    assert fizzbuzz(n) == expected


# ----------------------------- boundary -----------------------------
def test_lower_bound():
    """n = 1 (lower bound・3 でも 5 でもない)"""
    assert fizzbuzz(1) == "1"


def test_upper_bound():
    """n = 100 (upper bound・5 の倍数なので "Buzz")"""
    assert fizzbuzz(100) == "Buzz"


# ----------------------------- error cases -----------------------------
@pytest.mark.parametrize(
    "invalid",
    [
        0,        # 下限未満
        -1,       # 負数
        101,      # 上限超え
        3.0,      # float
        "3",      # str
        None,     # None
    ],
)
def test_error_raises_value_error(invalid):
    """範囲外 or 非整数 は ValueError を raise する"""
    with pytest.raises(ValueError):
        fizzbuzz(invalid)
