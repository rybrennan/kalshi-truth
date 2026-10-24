"""Fill-record field extraction — the traps, encoded.

Kalshi's fills API is the ground truth for what you actually traded, but its
field conventions are hostile to naive readers:

  * count lives under FOUR different keys: count, quantity, filled_count, count_fp
  * prices come per-side ({side}_price_dollars / {side}_price / price) and may
    be cents or dollars (see money.to_dollars)
  * the `side` on a fill is the side of the CONTRACT LEG, not "what you meant":
    entering a NO/UNDER position is recorded as action=sell side=no; closing a
    YES position is recorded as action=sell side=no with the NO price near 1¢.
    Reading `action` as buy=open/sell=close, or pricing a fill by your
    position's side instead of the fill's own side, silently mis-books trades.

These extractors are ported from two production systems that learned each rule
the expensive way.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .money import to_dollars


def _price_keys_for_side(side: Optional[str]) -> tuple:
    side_norm = str(side or "").strip().upper()
    if side_norm == "NO":
        return ("no_price_dollars", "no_price", "price")
    return ("yes_price_dollars", "yes_price", "price")


def fill_count(fill: Dict[str, Any]) -> float:
    """Contract count, whatever key it hides under."""
    for key in ("count", "quantity", "filled_count", "count_fp"):
        v = fill.get(key)
        if v is None:
            continue
        try:
            return max(0.0, float(v))
        except (TypeError, ValueError):
            continue
    return 0.0


def fill_side(fill: Dict[str, Any]) -> Optional[str]:
    """'yes' | 'no' | None — the side of the contract leg this fill traded.

    Kalshi deprecated `side` (and `action`) in May 2026 in favour of
    `outcome_side`, "the outcome the user profits from". On every fill shape
    this tool books, that is the same side `side` carried, so it is the
    fallback for the day the legacy field disappears.
    """
    for key in ("side", "outcome_side"):
        s = str(fill.get(key) or "").strip().lower()
        if s in ("yes", "no"):
            return s
    return None


def fill_price(fill: Dict[str, Any], side: Optional[str] = None) -> float:
    """Price IN DOLLARS for the given side (default: the fill's own side).

    Preference: native-side keys, then — last resort only — the complement
    of the opposite side. Never a direct cross-side read.
    """
    side = side or fill_side(fill)
    for key in _price_keys_for_side(side):
        v = fill.get(key)
        if v is None:
            continue
        px = to_dollars(v)
        if px > 0.0:
            return px

    side_norm = str(side or "").strip().upper()
    if side_norm == "NO":
        complement_keys = ("yes_price_dollars", "yes_price")
    elif side_norm == "YES":
        complement_keys = ("no_price_dollars", "no_price")
    else:
        return 0.0
    for key in complement_keys:
        v = fill.get(key)
        if v is None:
            continue
        comp = to_dollars(v)
        if 0.0 < comp < 1.0:
            return 1.0 - comp
    return 0.0


def fill_fee(fill: Dict[str, Any]) -> float:
    try:
        return float(fill.get("fee_cost") or 0.0)
    except (TypeError, ValueError):
        return 0.0
