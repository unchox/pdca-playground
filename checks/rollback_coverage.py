"""Generic traceability check: every element under --path has a non-empty rollback.

Contract: run(artifact, argv) -> (passed: bool, gap: str). Fails list the
uncovered step indices so the maker's fix hint is actionable (spec §5.2).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def run(artifact: dict, argv: list[str]) -> tuple[bool, str]:
    ap = argparse.ArgumentParser(prog="rollback_coverage", add_help=False)
    ap.add_argument("--path", required=True)
    args = ap.parse_args(argv)

    node = artifact
    for part in args.path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, f"path '{args.path}' is missing"
        node = node[part]

    if not isinstance(node, list):
        return False, f"'{args.path}' is not a list"

    uncovered = [
        i
        for i, step in enumerate(node)
        if not (isinstance(step, dict) and isinstance(step.get("rollback"), str)
                and step["rollback"].strip())
    ]
    if uncovered:
        return False, f"steps without rollback at indices: {uncovered}"
    return True, ""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    artifact_path = Path(argv.pop(0))
    import yaml

    artifact = yaml.safe_load(artifact_path.read_text(encoding="utf-8"))
    passed, gap = run(artifact, argv)
    print("pass" if passed else f"fail: {gap}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
