"""Multiply module (seed stub・maker が full 実装する責務)."""


def multiply(a, b):
    """2 つの整数を 掛けて返す (maker 実装).

    Rules:
    - a, b ともに int (bool 除く)
    - それ以外 → raise TypeError

    See .pdca/plan.yaml (multiply-v3 contract) for spec.
    """
    if not isinstance(a, int) or not isinstance(b, int) or isinstance(a, bool) or isinstance(b, bool):
        raise TypeError(f"multiply requires int arguments, got {type(a)} and {type(b)}")
    return a * b
