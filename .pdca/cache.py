"""Decision / verdict store (Phase 2, L4) — file-backed, audit-grade.

get/put over store/{decisions,verdicts}/<key>.json. A corrupt record raises
CacheCorruption instead of being treated as a miss: silent recomputation would
hide a broken audit trail (spec §6.1).

stdlib only — act.py imports this and pdca-act.yml installs no packages.
store/ is committed to the task branch (2026-07-06 uchino decision, §6.5
option (a)): the existing continue step's `git add -A` persists it with zero
workflow change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

KINDS = ("decisions", "verdicts")
STORE = Path(os.environ.get("PDCA_STORE", "store"))


class CacheCorruption(Exception):
    """A stored record failed to parse — the audit chain is broken; stop."""


def _record_path(kind: str, key: str) -> Path:
    if kind not in KINDS:
        raise ValueError(f"unknown cache kind '{kind}' (expected one of {KINDS})")
    if not key or "/" in key or key.startswith("."):
        raise ValueError(f"unsafe cache key '{key}'")
    return STORE / kind / f"{key}.json"


def get(kind: str, key: str) -> dict | None:
    path = _record_path(kind, key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise CacheCorruption(f"corrupt cache record {path}: {exc}") from exc


def put(kind: str, key: str, value: dict) -> None:
    path = _record_path(kind, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
