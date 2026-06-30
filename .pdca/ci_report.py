"""Collect deterministic-gate results into .pdca/ci_result.json.

Called by ci.yml. exit code based detection: bash array → string variable 経由 を
完全廃止し、 直接 exit code file + pytest.json を読む (bash IFS の subshell empty array
bug "1001"/"20" garbage 問題への恒久対策・2026-06-30 fizzbuzz/multiply で踏破)。

Usage (new):
    # ci.yml で各 gate の exit code を .pdca/*_exit.txt に書いてから call
    python .pdca/ci_report.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PDCA_DIR = Path(".pdca")
RUFF_EXIT = PDCA_DIR / "ruff_exit.txt"
PYTEST_EXIT = PDCA_DIR / "pytest_exit.txt"
PYTEST_JSON = PDCA_DIR / "pytest.json"


def _read_exit(path: Path) -> int:
    """exit code file を読む・存在なし or parse fail は 0 扱い (= gate skip 想定)."""
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return 0


def build() -> dict:
    """exit code file + pytest.json から CI 結果を構築 (group 名引き渡し不要)."""
    ruff_exit = _read_exit(RUFF_EXIT)
    pytest_exit = _read_exit(PYTEST_EXIT)

    failing_groups: list[str] = []
    if ruff_exit != 0:
        failing_groups.append("lint:ruff")
    if pytest_exit != 0:
        failing_groups.append("tests")

    failing: list[str] = []
    # Per-test granularity for the failure signature, if pytest-json-report ran.
    if PYTEST_JSON.exists():
        try:
            report = json.loads(PYTEST_JSON.read_text(encoding="utf-8"))
            failing += [
                t["nodeid"] for t in report.get("tests", []) if t.get("outcome") == "failed"
            ]
        except json.JSONDecodeError:
            pass

    # Non-test gate groups (lint, type, ...) contribute their group id directly.
    failing += [g for g in failing_groups if g != "tests"]

    outcome = "fail" if failing_groups else "pass"
    return {
        "outcome": outcome,
        "failing_checks": failing,
        "error_kinds": failing_groups,
        "summary": (
            f"{len(failing_groups)} gate group(s) red" if failing_groups else "all gates green"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    # argv は無視 (backwards-compat で旧 scaffold が "group1,group2" 渡しても drop)
    result = build()
    PDCA_DIR.mkdir(exist_ok=True)
    (PDCA_DIR / "ci_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(result["outcome"])
    return 0 if result["outcome"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
