"""VLAN id parsing helper."""

from __future__ import annotations

import re

_VLAN_RE = re.compile(r"^(?:vlan)?(\d+)$", re.IGNORECASE)
_MIN_VLAN_ID = 1
_MAX_VLAN_ID = 4094


def parse_vlan(text: str) -> int:
    """Parse a VLAN id from text like "vlan101", "VLAN200", or "300".

    Raises ValueError if the text isn't a recognized VLAN identifier or the
    numeric id falls outside the valid range (1..4094).
    """
    match = _VLAN_RE.match(text)
    if not match:
        raise ValueError(f"not a valid VLAN identifier: {text!r}")
    vlan_id = int(match.group(1))
    if not (_MIN_VLAN_ID <= vlan_id <= _MAX_VLAN_ID):
        raise ValueError(
            f"VLAN id out of range (1..{_MAX_VLAN_ID}): {vlan_id}"
        )
    return vlan_id
