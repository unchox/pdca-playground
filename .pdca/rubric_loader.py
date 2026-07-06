"""Rubric loading, schema validation, and version resolution (Phase 2, L1).

Fail-closed by design (spec §0-4): schema violations, references to
nonexistent check scripts, and duplicate criterion ids all raise RubricError
with a message precise enough for the human-gated issue body. Never guess
past a broken rubric.

Runs only where the dev extra is installed (ci.yml / local dev) — act.py must
never import this module (pdca-act.yml installs no Python packages).
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import jsonschema
import yaml

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "rubric" / "rubric.schema.json"


class RubricError(Exception):
    """Any rubric contract violation. The loop must stop, not guess."""


def _load_schema() -> dict:
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - repo corruption
        raise RubricError(f"rubric schema unreadable at {SCHEMA_PATH}: {exc}") from exc


def load_rubric(path: str | Path, checks_dir: str | Path | None = "checks") -> dict:
    """Load + validate a rubric YAML. Returns the rubric dict or raises RubricError.

    checks_dir=None skips check-script existence verification — the admit gate
    only needs (domain, version) identity; script existence is the Check
    stage's (runner's) concern.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RubricError(f"rubric not readable: {path}: {exc}") from exc

    try:
        rubric = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RubricError(f"rubric is not valid YAML: {path}: {exc}") from exc

    try:
        jsonschema.validate(rubric, _load_schema())
    except jsonschema.ValidationError as exc:
        raise RubricError(
            f"rubric schema violation in {path}: {exc.message} (at {list(exc.absolute_path)})"
        ) from exc

    seen: set[str] = set()
    for crit in rubric["criteria"]:
        cid = crit["id"]
        if cid in seen:
            raise RubricError(f"duplicate criterion id '{cid}' in {path}")
        seen.add(cid)

    if checks_dir is None:
        return rubric

    checks_dir = Path(checks_dir)
    for crit in rubric["criteria"]:
        if crit["class"] != "A":
            continue
        tokens = shlex.split(crit["check"])
        if not tokens:
            raise RubricError(f"criterion '{crit['id']}' has an empty check string")
        script = checks_dir / f"{tokens[0]}.py"
        if not script.is_file():
            raise RubricError(
                f"criterion '{crit['id']}' references unknown check '{tokens[0]}' "
                f"(expected {script})"
            )

    return rubric


def rubric_id(rubric: dict) -> tuple[str, int]:
    """(domain, version) — the identity used in cache keys and run metadata."""
    return rubric["domain"], rubric["version"]
