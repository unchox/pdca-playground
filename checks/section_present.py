"""Generic structural check: required sections / keys / minimums / role coverage.

Contract: run(artifact, argv) -> (passed: bool, gap: str). The artifact is a
parsed YAML/JSON mapping. Supported argv (spec §5.2):
  --path a.b.c              node that must exist
  --require-keys k1,k2      dotted keys (relative to node) that must be present and non-null
  --min k=v                 numeric floor on a (dotted) key relative to node
  --require-roles r1,r2     node is a list; each role must appear as an entry's `role`
  --distinct field          the `field` values of the required-role entries must differ
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_MISSING = object()


def _resolve(node, dotted: str):
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def run(artifact: dict, argv: list[str]) -> tuple[bool, str]:
    ap = argparse.ArgumentParser(prog="section_present", add_help=False)
    ap.add_argument("--path", required=True)
    ap.add_argument("--require-keys", default="")
    ap.add_argument("--min", default="")
    ap.add_argument("--require-roles", default="")
    ap.add_argument("--distinct", default="")
    args = ap.parse_args(argv)

    gaps: list[str] = []
    node = _resolve(artifact, args.path)
    if node is _MISSING:
        return False, f"path '{args.path}' is missing"

    for key in filter(None, args.require_keys.split(",")):
        value = _resolve(node, key) if isinstance(node, dict) else _MISSING
        if value is _MISSING or value is None:
            gaps.append(f"required key '{args.path}.{key}' is missing or null")

    if args.min:
        key, _, floor = args.min.partition("=")
        value = _resolve(node, key) if isinstance(node, dict) else _MISSING
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            gaps.append(f"'{args.path}.{key}' is not a number (min {floor} required)")
        elif value < float(floor):
            gaps.append(f"'{args.path}.{key}' = {value} is below the minimum {floor}")

    roles = [r for r in args.require_roles.split(",") if r]
    if roles:
        entries = node if isinstance(node, list) else []
        by_role = {}
        for entry in entries:
            if isinstance(entry, dict) and "role" in entry:
                by_role.setdefault(entry["role"], []).append(entry)
        for role in roles:
            if role not in by_role:
                gaps.append(f"'{args.path}' has no entry with role '{role}'")
        if args.distinct and all(r in by_role for r in roles):
            values = [by_role[r][0].get(args.distinct) for r in roles]
            if None in values or len(set(map(json.dumps, values))) != len(values):
                gaps.append(
                    f"role entries in '{args.path}' do not have distinct '{args.distinct}' values"
                )

    return (not gaps), "; ".join(gaps)


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
