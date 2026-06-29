"""Collect deterministic-gate results into .pdca/ci_result.json.

Called by ci.yml. Keeping this out of the workflow YAML avoids fragile inline
heredocs and lets the report logic be unit-tested if desired.

Usage:
    python .pdca/ci_report.py <comma-separated failing gate groups>
e.g.
    python .pdca/ci_report.py lint:ruff,tests
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def build(failing_groups: list[str]) -> dict:
    failing: list[str] = []

    # Per-test granularity for the failure signature, if pytest-json-report ran.
    pytest_json = Path(".pdca/pytest.json")
    if pytest_json.exists():
        report = json.loads(pytest_json.read_text(encoding="utf-8"))
        failing += [
            t["nodeid"] for t in report.get("tests", []) if t.get("outcome") == "failed"
        ]

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


def main(argv: list[str]) -> int:
    raw = argv[1] if len(argv) > 1 else ""
    groups = [g for g in raw.split(",") if g]
    result = build(groups)
    Path(".pdca").mkdir(exist_ok=True)
    Path(".pdca/ci_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(result["outcome"])
    return 0 if result["outcome"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
