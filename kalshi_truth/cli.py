"""kalshi-truth CLI — your account, reconciled to the cent.

    kalshi-truth report                       # everything, grouped by series
    kalshi-truth report --since 2026-07-01 --until 2026-07-03
    kalshi-truth report --by day
    kalshi-truth report --by ticker --json
    kalshi-truth check                        # the cash identity: does it all tie out?
    kalshi-truth report --from-file examples/demo_account.json   # offline, no keys
    kalshi-truth balance

Credentials: KALSHI_API_KEY + KALSHI_PRIVATE_KEY_PATH (env or ./.env).
--from-file reads a JSON object with any of: fills, settlements,
archived_positions, markets, balance, deposits, withdrawals.
Read-only by construction — this tool cannot place or cancel orders.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from .audit import cash_identity
from .client import KalshiAPIError, KalshiReadOnlyClient, load_env_file
from .ledger import MarketTruth, reconcile_account


async def _pull(client: KalshiReadOnlyClient, with_cash: bool) -> Dict[str, Any]:
    """Everything needed for a lifetime answer: live AND archived history."""
    acct: Dict[str, Any] = {}
    if with_cash:
        acct["balance"] = await client.get_balance()
    acct["cutoff"] = await client.historical_cutoff()
    acct["settlements"] = await client.all_settlements()
    acct["archived_positions"] = await client.archived_positions()
    fills = await client.all_fills()  # live + /historical/fills, de-duplicated

    # Gap-close: settled tickers whose fills didn't surface in the account-wide
    # walk get one targeted per-ticker retry (the API sometimes omits them).
    have = {f.get("ticker") for f in fills}
    settled = {s.get("ticker") for s in acct["settlements"]} | {
        p.get("ticker") for p in acct["archived_positions"]
        if float(p.get("total_traded_dollars") or 0)}
    for tk in sorted(t for t in settled if t and t not in have):
        fills.extend(await client.fills_for_ticker(tk))
    acct["fills"] = fills

    # Settlement values for archived markets, used by the fills cross-check.
    arch_tickers = sorted({p["ticker"] for p in acct["archived_positions"]
                           if p.get("ticker") and p["ticker"] in have})
    acct["markets"] = {m["ticker"]: m for m in
                       await client.historical_markets(arch_tickers)}
    if with_cash:
        acct["deposits"] = await client.deposits()
        acct["withdrawals"] = await client.withdrawals()
        after = await client.get_balance()
        if after.get("balance") != acct["balance"].get("balance"):
            print("kalshi-truth: balance moved during the pull (a trade landed?) — "
                  "re-run when the account is quiet", file=sys.stderr)
        acct["balance"] = after
    return acct


def _reconcile(acct: Dict[str, Any]) -> List[MarketTruth]:
    return reconcile_account(acct.get("fills") or [],
                             acct.get("settlements") or [],
                             acct.get("archived_positions") or [],
                             acct.get("markets") or {})


def _window(markets: List[MarketTruth], since: str, until: str) -> List[MarketTruth]:
    out = markets
    if since:
        out = [m for m in out if (m.day or "") >= since]
    if until:
        out = [m for m in out if (m.day or "") <= until]
    return out


def _fmt_row(label: str, n: int, cost: float, realized: float,
             wins: int, losses: int) -> str:
    roi = (100 * realized / cost) if cost else 0.0
    return (f"{label:<16} {n:>5} {wins:>4}-{losses:<4} "
            f"${cost:>9.2f} ${realized:>+9.2f} {roi:>+7.1f}%")


def _print_report(markets: List[MarketTruth], by: str, cutoff: Dict[str, Any]) -> None:
    settled = [m for m in markets if m.method != "open"]
    open_ = [m for m in markets if m.method == "open"]

    groups: Dict[str, List[MarketTruth]] = defaultdict(list)
    for m in settled:
        key = {"series": m.series, "day": m.day or "?", "ticker": m.ticker}[by]
        groups[key].append(m)

    print("=" * 72)
    print(f"{'group':<16} {'mkts':>5} {'W-L':>9} {'cost':>10} {'realized':>10} {'ROI':>8}")
    print("-" * 72)
    for key in sorted(groups):
        ms = groups[key]
        wins = sum(1 for m in ms if m.won)
        print(_fmt_row(key, len(ms), sum(m.cost for m in ms),
                       sum(m.realized for m in ms), wins, len(ms) - wins))
    print("-" * 72)
    wins = sum(1 for m in settled if m.won)
    print(_fmt_row("TOTAL", len(settled), sum(m.cost for m in settled),
                   sum(m.realized for m in settled), wins, len(settled) - wins))
    n_arch = sum(1 for m in settled if m.method == "archived")
    if n_arch:
        cut = (cutoff.get("market_positions_last_updated_ts") or "")[:10]
        print(f"  {n_arch} markets from Kalshi's archive"
              + (f" (settled before {cut})" if cut else ""))
    flagged = [m for m in markets if any("divergence" in n for n in m.notes)]
    if flagged:
        print(f"  ! {len(flagged)} markets where fills disagree with the exchange record "
              "(run with --by ticker --json to see them)")
    if open_:
        exposure = sum(m.cost - m.box_cash for m in open_)
        print(f"  open positions: {len(open_)} markets, ~${exposure:.2f} at risk (not in totals)")


def _print_check(acct: Dict[str, Any], markets: List[MarketTruth]) -> int:
    if "balance" not in acct or "deposits" not in acct:
        print("kalshi-truth: check needs balance and deposits", file=sys.stderr)
        return 2
    r = cash_identity(markets, acct["balance"], acct.get("deposits") or [],
                      acct.get("withdrawals") or [])
    print("cash identity:  Σ realized  =  cash + open cost − net deposits")
    print(f"  Σ realized (settled)      ${r['realized']:>+12.2f}")
    print(f"  cash                      ${r['cash']:>12.2f}")
    print(f"  open cost basis           ${r['open_cost']:>12.2f}")
    print(f"  net deposits              ${r['net_deposits']:>12.2f}")
    print(f"  residual                  ${r['residual']:>+12.2f}")
    if abs(r["residual"]) <= 1.0:
        print("  OK — ties out. A small residual is expected: cash credits that are not "
              "trades (e.g. interest) are not in the API.")
        return 0
    print("  MISMATCH — something is missing or mis-read. Check open positions and "
          "the archive cutoff, and re-run when no trades are in flight.")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="kalshi-truth",
                                 description="Reconcile your Kalshi P&L from raw fills + settlements.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    rep = sub.add_parser("report", help="reconciled P&L report")
    rep.add_argument("--since", default="", help="YYYY-MM-DD (event date)")
    rep.add_argument("--until", default="", help="YYYY-MM-DD (event date)")
    rep.add_argument("--by", choices=("series", "day", "ticker"), default="series")
    rep.add_argument("--json", action="store_true", help="machine-readable output")
    rep.add_argument("--from-file", default="", help="offline: read a JSON account dump")

    chk = sub.add_parser("check", help="verify the totals against your cash balance")
    chk.add_argument("--from-file", default="", help="offline: read a JSON account dump")

    sub.add_parser("balance", help="cash balance")

    args = ap.parse_args()

    def show(acct: Dict[str, Any]) -> int:
        markets = _reconcile(acct)
        if args.cmd == "check":
            return _print_check(acct, markets)
        markets = _window(markets, args.since, args.until)
        if args.json:
            print(json.dumps([m.__dict__ | {"won": m.won, "day": m.day,
                                            "held_side": m.held_side}
                              for m in markets], indent=2, default=str))
        else:
            _print_report(markets, args.by, acct.get("cutoff") or {})
        return 0

    if args.cmd in ("report", "check") and args.from_file:
        return show(json.loads(Path(args.from_file).read_text()))

    load_env_file(Path.cwd() / ".env")

    async def run() -> int:
        async with KalshiReadOnlyClient() as client:
            if args.cmd == "balance":
                b = await client.get_balance()
                print(f"cash: ${(b.get('balance') or 0) / 100.0:.2f}")
                return 0
            return show(await _pull(client, with_cash=args.cmd == "check"))

    try:
        return asyncio.run(run())
    except KalshiAPIError as e:
        print(f"kalshi-truth: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
