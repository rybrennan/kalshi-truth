"""The reconciliation engine — what you ACTUALLY made, per market.

THE MODEL (the whole trick, verified against real accounts):

    Every fill is a PURCHASE of `fill.side` at that side's price,
    regardless of what `action` says.

Kalshi runs a single YES orderbook. "Buying NO" is an ask on the YES book, and
the fills API records legs from shifting perspectives — entering a NO/UNDER
position shows up as action=sell side=no; closing a YES position shows up as
action=sell side=no at a ~1¢ NO price. Reading action as open/close is how
trackers flip sides, book 77×-wrong cost bases, and hide losses. Treating
every leg as a purchase of its own side sidesteps the perspective problem
entirely, because the exchange mechanics guarantee:

    * holding YES + NO of the same market is a BOX → nets to $1.00 × count
      (Kalshi returns the cash immediately; the settlement row then shows the
      flattened pair with revenue 0)
    * at settlement, each remaining winning-side contract pays $1.00

And the second discovery (probed live, 2026-07-10): **the settlement row is
fully self-contained** — it carries BOTH sides' counts and cost bases, the
revenue, and the fee, even for positions closed early (the close-leg appears
as the opposite side's count/cost). So for every SETTLED market:

    box      = min(yes_count, no_count) × $1.00      # boxes cash-settle at $1
    realized = revenue + box − yes_cost − no_cost − fee

That is the authoritative number. The fills-based twin —

    realized = box(fills counts) + revenue − Σ(count × side_price) − Σ fees

— must agree when the fills API surfaced every leg, so it runs as a built-in
CROSS-CHECK; divergence beyond rounding means the fills pull was incomplete
(common) or something new is wrong (flagged loudly). Validated to the cent
against held wins, held losses, cashed-out YES, cashed-out NO (the hard case),
and locked boxes from a real account.

ARCHIVED MARKETS. Kalshi moves settled positions older than its archive
cutoff out of /portfolio/settlements entirely; they survive only as
/historical/positions rows (realized P&L before fees, fees paid, traded
notional) — there is no historical settlements endpoint. For those markets
the archive row is the authority:

    realized = realized_pnl − fees_paid
    cost     = total_traded + fees_paid

and the same fills twin, priced with the market's settlement value, runs as
the cross-check. A tracker that only reads /portfolio/settlements silently
loses everything before the cutoff.

Omitting the box term is THE classic error: a naive settlement read scores a
cashed-out winner as a total loss of both legs (revenue 0, two costs) — on an
active account this line item alone can add up to thousands of dollars.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .fills import fill_count, fill_fee, fill_price, fill_side
from .money import dollar_field

_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def series_of(ticker: str) -> str:
    """Series prefix, e.g. KXMLBKS-26JUL09... → KXMLBKS."""
    return (ticker or "").split("-", 1)[0] or "?"


def event_date_of(ticker: str) -> Optional[str]:
    """YYYY-MM-DD embedded in most Kalshi event tickers ({YY}{MON}{DD}) —
    the EVENT date, unlike settled_time which is UTC and rolls past midnight."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})(?=\d{4}|[A-Z]|$)", ticker or "")
    if not m:
        return None
    yy, mon, dd = m.groups()
    month = _MONTHS.get(mon)
    if not month:
        return None
    return f"20{yy}-{month:02d}-{int(dd):02d}"


@dataclass
class MarketTruth:
    ticker: str
    series: str
    event_date: Optional[str]
    yes_bought: float = 0.0
    no_bought: float = 0.0
    cost: float = 0.0            # incl. fees
    fees: float = 0.0
    box_cash: float = 0.0
    settle_revenue: float = 0.0
    settle_result: Optional[str] = None
    settled_time: Optional[str] = None
    method: str = "fills"        # settlement | archived | fills | open
    realized: float = 0.0
    settled: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def held_side(self) -> Optional[str]:
        """Net directional side (None when flat/boxed)."""
        if self.yes_bought > self.no_bought:
            return "YES"
        if self.no_bought > self.yes_bought:
            return "NO"
        return None

    @property
    def won(self) -> bool:
        return self.realized > 0

    @property
    def day(self) -> Optional[str]:
        return self.event_date or (self.settled_time or "")[:10] or None


def _fills_view(fills: List[Dict[str, Any]]) -> Dict[str, float]:
    """Aggregate the fills legs: every fill = a purchase of its own side."""
    yes = no = cost = fees = 0.0
    for f in fills:
        side = fill_side(f)
        c = fill_count(f)
        if side is None or c <= 0:
            continue
        px = fill_price(f, side)
        fee = fill_fee(f)
        cost += c * px + fee
        fees += fee
        if side == "yes":
            yes += c
        else:
            no += c
    return {"yes": yes, "no": no, "cost": cost, "fees": fees,
            "box": min(yes, no) * 1.0}


def reconcile_market(
    ticker: str,
    fills: List[Dict[str, Any]],
    settlement: Optional[Dict[str, Any]],
) -> MarketTruth:
    """One market's truth. Settlement row = authority; fills = cross-check."""
    mt = MarketTruth(ticker=ticker, series=series_of(ticker),
                     event_date=event_date_of(ticker))
    fv = _fills_view(fills)

    if settlement:
        mt.settled = True
        mt.settle_result = settlement.get("market_result")
        mt.settled_time = settlement.get("settled_time")
        mt.settle_revenue = (float(settlement.get("revenue") or 0)) / 100.0
        s_yes = float(settlement.get("yes_count_fp") or 0)
        s_no = float(settlement.get("no_count_fp") or 0)

        if s_yes + s_no > 0:
            # AUTHORITATIVE: the settlement row carries both sides' counts,
            # costs, revenue, and fee — including early-close legs.
            s_cost = ((dollar_field(settlement.get("yes_total_cost_dollars")) or 0.0)
                      + (dollar_field(settlement.get("no_total_cost_dollars")) or 0.0))
            s_fee = float(settlement.get("fee_cost") or 0.0)
            mt.method = "settlement"
            mt.yes_bought, mt.no_bought = s_yes, s_no
            mt.box_cash = min(s_yes, s_no) * 1.0
            mt.cost = s_cost + s_fee
            mt.fees = s_fee
            mt.realized = round(
                mt.settle_revenue + mt.box_cash - s_cost - s_fee, 4)

            # Cross-check via the fills twin when the fills pull looks complete.
            if fills and fv["yes"] >= s_yes and fv["no"] >= s_no:
                twin = round(fv["box"] + mt.settle_revenue - fv["cost"], 4)
                if abs(twin - mt.realized) > 0.05:
                    mt.notes.append(
                        f"fills/settlement divergence: fills say {twin:+.4f}")
            elif fills:
                mt.notes.append("fills incomplete (settlement is authoritative)")
        elif fills:
            # degenerate settlement row (no counts) — fall back to fills math
            mt.method = "fills"
            mt.yes_bought, mt.no_bought = fv["yes"], fv["no"]
            mt.box_cash = fv["box"]
            mt.cost, mt.fees = fv["cost"], fv["fees"]
            mt.realized = round(fv["box"] + mt.settle_revenue - fv["cost"], 4)
        else:
            mt.method = "settlement"
            mt.realized = round(mt.settle_revenue, 4)
        return mt

    # No settlement: open position (or a pre-settlement box, already cashed)
    mt.method = "open"
    mt.yes_bought, mt.no_bought = fv["yes"], fv["no"]
    mt.box_cash = fv["box"]
    mt.cost, mt.fees = fv["cost"], fv["fees"]
    if fv["box"] > 0 and fv["yes"] == fv["no"]:
        # fully boxed pre-settlement — outcome already locked in cash
        mt.realized = round(fv["box"] - fv["cost"], 4)
    return mt


def yes_value_of(market: Optional[Dict[str, Any]]) -> Optional[float]:
    """What one YES contract paid at settlement ($1, $0, or a scalar value).
    None when the market dict doesn't say."""
    if not market:
        return None
    result = market.get("result")
    if result == "yes":
        return 1.0
    if result == "no":
        return 0.0
    return dollar_field(market.get("settlement_value_dollars"))


def reconcile_archived(
    ticker: str,
    fills: List[Dict[str, Any]],
    position: Dict[str, Any],
    yes_value: Optional[float] = None,
) -> MarketTruth:
    """One archived market. The /historical/positions row is the authority;
    the fills twin (priced with the settlement value) is the cross-check."""
    mt = MarketTruth(ticker=ticker, series=series_of(ticker),
                     event_date=event_date_of(ticker))
    fv = _fills_view(fills)
    kalshi_pnl = dollar_field(position.get("realized_pnl_dollars")) or 0.0
    fees = dollar_field(position.get("fees_paid_dollars")) or 0.0
    traded = dollar_field(position.get("total_traded_dollars")) or 0.0

    mt.method = "archived"
    mt.settled = True
    mt.settled_time = position.get("last_updated_ts")
    mt.yes_bought, mt.no_bought = fv["yes"], fv["no"]
    mt.box_cash = fv["box"]
    mt.fees = fees
    mt.cost = traded + fees
    mt.realized = round(kalshi_pnl - fees, 4)
    if yes_value is not None:
        mt.settle_result = ("yes" if yes_value == 1.0 else
                            "no" if yes_value == 0.0 else "scalar")

    if not fills:
        mt.notes.append("no fills surfaced (archive row is authoritative)")
    elif yes_value is not None:
        held_yes = max(fv["yes"] - fv["no"], 0.0)
        held_no = max(fv["no"] - fv["yes"], 0.0)
        payout = held_yes * yes_value + held_no * (1.0 - yes_value)
        twin = round(fv["box"] + payout - fv["cost"], 4)
        if abs(twin - mt.realized) > 0.05:
            mt.notes.append(f"fills/archive divergence: fills say {twin:+.4f}")
    return mt


def _is_empty_position(p: Dict[str, Any]) -> bool:
    """Archive rows for orders that never filled: all-zero, no trade at all."""
    return not any(dollar_field(p.get(k)) for k in
                   ("total_traded_dollars", "realized_pnl_dollars", "fees_paid_dollars"))


def reconcile_account(
    fills: List[Dict[str, Any]],
    settlements: List[Dict[str, Any]],
    archived_positions: Optional[List[Dict[str, Any]]] = None,
    markets: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[MarketTruth]:
    """Every market the account ever touched, reconciled.

    `archived_positions` are /historical/positions market rows; `markets` maps
    ticker → market dict (for the settlement value used by the archive
    cross-check). A live settlement row wins over an archive row for the same
    ticker."""
    fills_by: Dict[str, List[Dict[str, Any]]] = {}
    for f in fills:
        tk = f.get("ticker") or ""
        if tk:
            fills_by.setdefault(tk, []).append(f)

    sett_by: Dict[str, Dict[str, Any]] = {}
    for s in settlements:
        tk = s.get("ticker") or ""
        if not tk:
            continue
        if tk in sett_by:
            # multiple settlement rows for one ticker: merge revenue/costs
            prev = sett_by[tk]
            prev["revenue"] = (prev.get("revenue") or 0) + (s.get("revenue") or 0)
            for k in ("yes_total_cost_dollars", "no_total_cost_dollars", "fee_cost"):
                prev[k] = float(prev.get(k) or 0) + float(s.get(k) or 0)
        else:
            sett_by[tk] = dict(s)

    arch_by: Dict[str, Dict[str, Any]] = {}
    for p in archived_positions or []:
        tk = p.get("ticker") or ""
        if tk and tk not in sett_by and not _is_empty_position(p):
            arch_by[tk] = p

    markets = markets or {}
    out: List[MarketTruth] = []
    for tk in sorted(set(fills_by) | set(sett_by) | set(arch_by)):
        if tk in arch_by:
            out.append(reconcile_archived(tk, fills_by.get(tk, []), arch_by[tk],
                                          yes_value_of(markets.get(tk))))
        else:
            out.append(reconcile_market(tk, fills_by.get(tk, []), sett_by.get(tk)))
    return out
