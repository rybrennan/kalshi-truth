"""Build examples/demo_account.json — a SYNTHETIC Kalshi account.

Nothing here is a real trade. Tickers use made-up team codes; prices, counts
and outcomes come from a seeded RNG. The rows use the same field shapes the
real fills/settlements/archive APIs return, and every trap from the README
appears: NO entries recorded as action=sell, cashed-out winners whose
settlement row shows revenue 0, settled markets whose fills never surfaced,
markets old enough to live only in Kalshi's archive, and open positions.

The cash balance is built independently from the raw money flows (deposits,
every leg's cost and fee, every payout), never from the engine's output, so
`kalshi-truth check --from-file examples/demo_account.json` is a real test.

    python examples/make_demo.py      # rewrites examples/demo_account.json
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

TEAMS = ["ALP", "BRV", "CHR", "DLT", "ECH", "FXT", "GLF", "HTL"]
rng = random.Random(163)


def fee(count: int, price: float) -> float:
    """Kalshi-style taker fee: 0.07 × C × P × (1 − P), rounded up to the cent."""
    return math.ceil(0.07 * count * price * (1 - price) * 100) / 100


def fill(ticker, action, side, count, yes_price, fee_cost):
    return {"ticker": ticker, "action": action, "side": side,
            "count_fp": f"{count:.2f}",
            "yes_price_dollars": f"{yes_price:.4f}",
            "no_price_dollars": f"{1 - yes_price:.4f}",
            "fee_cost": f"{fee_cost:.6f}"}


def settlement(ticker, result, revenue_cents, yes_n, yes_cost, no_n, no_cost,
               fee_cost, day):
    return {"ticker": ticker, "market_result": result, "revenue": revenue_cents,
            "yes_count_fp": f"{yes_n:.2f}", "yes_total_cost_dollars": f"{yes_cost:.6f}",
            "no_count_fp": f"{no_n:.2f}", "no_total_cost_dollars": f"{no_cost:.6f}",
            "fee_cost": f"{fee_cost:.6f}",
            "settled_time": f"2026-10-{day + 1:02d}T03:30:00Z"}


def ticker_for(series: str, day: int, i: int) -> str:
    a, b = rng.sample(TEAMS, 2)
    if series == "KXMLBKS":
        return f"KXMLBKS-26OCT{day:02d}1910{a}{b}-{a}PITCHER{i}-6"
    if series == "KXNBATOTAL":
        return f"KXNBATOTAL-26OCT{day:02d}{a}{b}-{rng.choice([214, 221, 228])}"
    return f"KXNHLGAME-26OCT{day:02d}{a}{b}-{a}"


def market(series: str, day: int, i: int, kind: str):
    """→ (visible fills, settlement row or None, true cash flow, notional, fees, yes_value)."""
    tk = ticker_for(series, day, i)
    c = rng.choice([3, 5, 6, 8, 10, 12])
    fills, sett = [], None
    paid = 0.0      # every leg actually traded, visible or not
    fee_total = 0.0
    payout = 0.0
    yes_value = None

    if kind == "held_yes":
        p = round(rng.uniform(0.30, 0.70), 2)
        f = fee(c, p)
        result = "yes" if rng.random() < p + rng.uniform(-0.12, 0.08) else "no"
        fills.append(fill(tk, "buy", "yes", c, p, f))
        sett = settlement(tk, result, 100 * c if result == "yes" else 0,
                          c, c * p, 0, 0, f, day)
        paid, fee_total, payout = c * p, f, (c if result == "yes" else 0)
        yes_value = 1.0 if result == "yes" else 0.0

    elif kind == "held_no":  # trap 1: a NO entry is recorded as action=sell
        q = round(rng.uniform(0.30, 0.70), 2)
        f = fee(c, q)
        result = "no" if rng.random() < q + rng.uniform(-0.12, 0.08) else "yes"
        fills.append(fill(tk, "sell", "no", c, 1 - q, f))
        sett = settlement(tk, result, 100 * c if result == "no" else 0,
                          0, 0, c, c * q, f, day)
        paid, fee_total, payout = c * q, f, (c if result == "no" else 0)
        yes_value = 1.0 if result == "yes" else 0.0

    elif kind in ("cashout_no", "cashout_no_partial_fills"):
        # trap 2: entered NO, closed early by buying YES cheap → box.
        # Settlement row shows both legs and revenue 0.
        q = round(rng.uniform(0.45, 0.75), 2)
        r = round(rng.uniform(0.01, 0.06), 2)
        f1, f2 = fee(c, q), fee(c, r)
        fills.append(fill(tk, "sell", "no", c, 1 - q, f1))
        if kind == "cashout_no":
            fills.append(fill(tk, "buy", "yes", c, r, f2))
        sett = settlement(tk, "no", 0, c, c * r, c, c * q, f1 + f2, day)
        paid, fee_total, payout, yes_value = c * (q + r), f1 + f2, c, 0.0  # box pays $1 × c

    elif kind == "cashout_yes":
        # closing a YES is recorded as action=sell side=no at a small NO price
        p = round(rng.uniform(0.35, 0.60), 2)
        r_no = round(rng.uniform(0.04, 0.15), 2)
        f1, f2 = fee(c, p), fee(c, r_no)
        fills.append(fill(tk, "buy", "yes", c, p, f1))
        fills.append(fill(tk, "sell", "no", c, 1 - r_no, f2))
        sett = settlement(tk, "yes", 0, c, c * p, c, c * r_no, f1 + f2, day)
        paid, fee_total, payout, yes_value = c * (p + r_no), f1 + f2, c, 1.0

    elif kind == "settlement_only":  # fills never surfaced from the API
        p = round(rng.uniform(0.30, 0.65), 2)
        f = fee(c, p)
        result = rng.choice(["yes", "no"])
        sett = settlement(tk, result, 100 * c if result == "yes" else 0,
                          c, c * p, 0, 0, f, day)
        paid, fee_total, payout = c * p, f, (c if result == "yes" else 0)
        yes_value = 1.0 if result == "yes" else 0.0

    elif kind == "open":
        p = round(rng.uniform(0.30, 0.70), 2)
        f = fee(c, p)
        fills.append(fill(tk, "buy", "yes", c, p, f))
        paid, fee_total = c * p, f

    return fills, sett, payout - paid - fee_total, paid, fee_total, yes_value


ARCHIVE_BEFORE_DAY = 16   # markets on days 13-15 have aged into the archive


def main() -> None:
    kinds = (["held_yes"] * 9 + ["held_no"] * 9 + ["cashout_no"] * 3
             + ["cashout_yes"] * 3 + ["settlement_only"] * 2
             + ["cashout_no_partial_fills"] * 2 + ["open"] * 2)
    all_fills, live_setts, archived, results = [], [], [], {}
    flows = 0.0
    for i, kind in enumerate(kinds):
        series = ["KXMLBKS", "KXNBATOTAL", "KXNHLGAME"][i % 3]
        day = 13 + (i % 9)
        f, s, flow, notional, fees_, yes_value = market(series, day, i, kind)
        all_fills += f
        flows += flow
        if not s:
            continue
        archive = day < ARCHIVE_BEFORE_DAY and kind != "cashout_no_partial_fills"
        if archive:
            # Kalshi archives the settlement into a position row: realized P&L
            # BEFORE fees, fees paid, traded notional. No settlement row survives.
            archived.append({"ticker": s["ticker"],
                             "realized_pnl_dollars": f"{flow + fees_:.6f}",
                             "fees_paid_dollars": f"{fees_:.6f}",
                             "total_traded_dollars": f"{notional:.6f}",
                             "position_fp": "0.00", "market_exposure_dollars": "0.000000",
                             "last_updated_ts": s["settled_time"], "exchange_index": 0})
            results[s["ticker"]] = {"ticker": s["ticker"],
                                    "result": "yes" if yes_value == 1.0 else "no"}
        else:
            live_setts.append(s)
    # An order that never filled leaves an all-zero archive row.
    archived.append({"ticker": "KXNHLGAME-26OCT13ALPBRV-ALP", "realized_pnl_dollars": "0.000000",
                     "fees_paid_dollars": "0.000000", "total_traded_dollars": "0.000000",
                     "position_fp": "0.00", "market_exposure_dollars": "0.000000",
                     "last_updated_ts": "2026-10-14T03:30:00Z", "exchange_index": 0})
    deposits = [{"id": "dep-1", "type": "apm", "status": "applied",
                 "amount_cents": 10000, "fee_cents": 200},
                {"id": "dep-2", "type": "debit", "status": "applied",
                 "amount_cents": 5000, "fee_cents": 0}]
    net_dep = sum(d["amount_cents"] - d["fee_cents"] for d in deposits) / 100
    cash = net_dep + flows
    out = Path(__file__).with_name("demo_account.json")
    out.write_text(json.dumps({
        "_note": "SYNTHETIC demo data — not a real account. Generated by examples/make_demo.py.",
        "cutoff": {"market_positions_last_updated_ts": f"2026-10-{ARCHIVE_BEFORE_DAY:02d}T00:00:00Z"},
        "fills": all_fills, "settlements": live_setts,
        "archived_positions": archived, "markets": results,
        "balance": {"balance": round(cash * 100), "balance_dollars": f"{cash:.4f}"},
        "deposits": deposits, "withdrawals": []}, indent=1) + "\n")
    print(f"wrote {out} — {len(all_fills)} fills, {len(live_setts)} live settlements, "
          f"{len(archived)} archive rows")


if __name__ == "__main__":
    main()
