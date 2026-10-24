"""Kalshi dollar/price parsing.

Kalshi mixes cents and dollars in fill payloads — the same field can carry
`0.74` (dollars) in one response shape and `74` (cents) in another. Battle-
tested heuristic from two production trading systems: numeric > 1.0 is cents.
"""

from __future__ import annotations

from typing import Any, Optional


def dollar_field(val: Any) -> Optional[float]:
    """Parse an optional dollar-denominated API field (string or number).
    No cents heuristic — use for fields documented as dollars."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_dollars(value: Any) -> float:
    """Normalize a price that may be cents or dollars. Values > 1.0 are cents."""
    if value is None:
        return 0.0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric > 1.0:
        return numeric / 100.0
    return numeric
