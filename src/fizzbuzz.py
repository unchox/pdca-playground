"""FizzBuzz module (seed stub・maker が full 実装する責務).

PDCA loop: 初回 push で red になる前提・maker が この stub を 置き換えて
tests/test_fizzbuzz.py の全 case を green にするまで loop。
"""


def fizzbuzz(n):
    """1〜100 の整数 n に対し古典 FizzBuzz を返す (maker 実装).

    Rules:
    - n が 15 の倍数 → "FizzBuzz"
    - n が 3 の倍数 (15 除く) → "Fizz"
    - n が 5 の倍数 (15 除く) → "Buzz"
    - それ以外 (1〜100 の整数) → str(n)
    - 範囲外 / 非整数 → raise ValueError

    See .pdca/plan.yaml T1 for full contract.
    """
    raise NotImplementedError("maker must implement fizzbuzz()")
