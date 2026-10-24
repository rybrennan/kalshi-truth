"""The cash identity — the one oracle that can't lie.

Every dollar in a Kalshi account either came in as a deposit, left as a
withdrawal, or moved through a market. So, at any moment:

    Σ realized (settled markets)  =  cash + open cost basis − net deposits

If the per-market engine is right, the residual is near zero. Cash credits
that are not trades (interest, promotions) are not exposed by the API, so a
small residual is expected; a large one means a market is missing or misread.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .ledger import MarketTruth
from .money import dollar_field

# Transfers in these states never moved money.
_NOT_MOVED = {"failed", "canceled", "cancelled", "rejected", "returned", "reversed"}


def _cents(row: Dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0) / 100.0
    except (TypeError, ValueError):
        return 0.0


def net_deposits(deposits: List[Dict[str, Any]],
                 withdrawals: List[Dict[str, Any]]) -> float:
    """Deposits credit amount minus the deposit fee (verified on a real
    account: card deposits carry a 2% fee_cents). Pending deposits are not
    yet credited. Withdrawal rows use the same amount_cents/fee_cents shape
    per the API docs; this path has not been checked against an account that
    has withdrawn."""
    total = 0.0
    for d in deposits:
        status = str(d.get("status") or "").lower()
        if status in _NOT_MOVED or status == "pending":
            continue
        total += _cents(d, "amount_cents") - _cents(d, "fee_cents")
    for w in withdrawals:
        if str(w.get("status") or "").lower() in _NOT_MOVED:
            continue
        total -= _cents(w, "amount_cents") + _cents(w, "fee_cents")
    return total


def cash_of(balance: Dict[str, Any]) -> float:
    """Prefer the sub-cent `balance_dollars`; fall back to integer cents."""
    v = dollar_field(balance.get("balance_dollars"))
    return v if v is not None else _cents(balance, "balance")


def cash_identity(markets: List[MarketTruth], balance: Dict[str, Any],
                  deposits: List[Dict[str, Any]],
                  withdrawals: List[Dict[str, Any]]) -> Dict[str, float]:
    settled = [m for m in markets if m.method != "open"]
    open_ = [m for m in markets if m.method == "open"]
    realized = sum(m.realized for m in settled)
    open_cost = sum(m.cost - m.box_cash for m in open_)
    cash = cash_of(balance)
    nd = net_deposits(deposits, withdrawals)
    return {"realized": round(realized, 4), "cash": round(cash, 4),
            "open_cost": round(open_cost, 4), "net_deposits": round(nd, 4),
            "residual": round(realized - (cash + open_cost - nd), 4) or 0.0}  # no "-0.00"
