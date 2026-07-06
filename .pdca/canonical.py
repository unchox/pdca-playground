"""Canonical JSON bytes + hash — the single definition used by admit, runner, cache.

Reproducibility lives in hash(canonical_input, ...) memoization (spec §0-2), so
every hasher must agree on one canonical form: sorted keys, minimal separators,
UTF-8, no ASCII escaping.

stdlib only — act.py imports this and pdca-act.yml installs no packages.
"""

from __future__ import annotations

import hashlib
import json


def canonical_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def canonical_hash(obj) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()
