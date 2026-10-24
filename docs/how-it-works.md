# How kalshi-truth works

The full version of the [README](../README.md): every trap in detail, the math, and how the answer gets checked.

## The seven traps

1. **`action` doesn't mean open or close.** A NO entry can be recorded as `action=sell, side=no`. Closing a YES is *also* `sell, side=no`, at a NO price of about 1¢. Closing a NO shows up as `sell, side=yes`. Treat `sell` as cash in and a $7.00 NO entry becomes income. Treat `buy` as the entry and a 2¢ closing leg becomes your cost basis, so a winning NO shows up as "YES, cost $0.20, +1336%".

2. **A cashed-out winner looks like a double loss.** Close a winner early and the settlement row lists both legs' costs with `revenue: 0`, because your YES + NO pair already paid out $1 each when you closed. Skip that and every early exit gets scored as a loss on both sides. On an active account that adds up to thousands of dollars.

3. **Cents and dollars share fields.** The same price field can hold `0.74` or `74` depending on the response shape. Values above 1.0 are cents.

4. **Contract count hides under four names:** `count`, `quantity`, `filled_count`, `count_fp`, depending on the endpoint and when the row was written.

5. **Exit fills are labeled from the other side.** Price a fill by the side you held instead of the side on the fill and you get the complement (1¢ instead of 99¢).

6. **Pagination truncates.** A fixed page budget silently drops old history, and per-ticker fills sometimes don't show up at all. kalshi-truth pages to the end, raises an error instead of truncating, retries missing tickers one by one, and falls back to the settlement row, which has everything it needs.

7. **Old history moves to a different API.** Kalshi archives data past a rolling cutoff (`GET /historical/cutoff`). Old fills move from `/portfolio/fills` to `/historical/fills`. Old settlements disappear from `/portfolio/settlements` and survive only as `/historical/positions` rows, with different fields and P&L *before* fees. A tracker built on the live endpoints won't error. It'll just quietly shrink to the last couple of months. kalshi-truth reads both, drops the overlap by `fill_id`, and reconciles archived markets too.

## The math

**Every fill is a purchase of `fill.side` at that side's price, whatever `action` says.** Kalshi runs a single YES order book, so holding YES and NO in the same market is a pair worth exactly $1.00. Per market:

```
pairs    = min(yes_count, no_count) × $1.00
realized = settlement_revenue + pairs − yes_cost − no_cost − fees
```

The settlement row has every number on the right, even for positions closed early, so it's the authority. Once a market is archived, the archive row takes over:

```
realized = realized_pnl − fees_paid       # Kalshi's archived P&L is before fees
cost     = total_traded + fees_paid
```

Either way, the same answer is also computed from the fills alone, priced with the market's settlement value. If the two disagree by more than 5¢, the market gets flagged in the report.

## Checking the answer

Every dollar in an account arrived as a deposit, left as a withdrawal, or went through a market. So the total has to satisfy:

```
Σ realized  =  cash + open cost basis − net deposits
```

`kalshi-truth check` pulls your balance, deposits (net of deposit fees) and withdrawals, and does the math:

```
$ kalshi-truth check --from-file examples/demo_account.json
cash identity:  Σ realized  =  cash + open cost − net deposits
  Σ realized (settled)      $       +2.03
  cash                      $      140.88
  open cost basis           $        9.15
  net deposits              $      148.00
  residual                  $       +0.00
  OK — ties out. A small residual is expected: cash credits that are not trades (e.g. interest) are not in the API.
```

On a real account, with thousands of markets back to its first trade and most of them archived, the residual is under $1, and the fills cross-check disagrees with Kalshi's own records on zero markets. Expect a small residual rather than exactly zero, because things like interest land in your cash but aren't in the API. The withdrawal math follows Kalshi's docs but hasn't yet been checked against an account that has withdrawn.

## All commands

| Command | What it does |
| --- | --- |
| `report` | Lifetime P&L grouped by series |
| `report --by day` | Grouped by event date |
| `report --by ticker --json` | Every market, machine-readable |
| `report --since 2026-07-01 --until 2026-07-07` | A date window |
| `check` | Ties the total to your cash balance and deposits |
| `balance` | Current cash |
| `--from-file <path>` | Run `report` or `check` on a saved JSON account instead of the API |

Dates are event dates parsed from the ticker, not settlement timestamps. A 10pm game that settles after midnight still counts on the day it was played.

## Tests

```bash
pip install ".[dev]" && pytest
```

The tests use the hard cases from the real-account audit (sell-side entries, double-loss cash-outs, missing fills, archived markets, live/archive overlap) with the exact API row shapes and made-up tickers and amounts. The demo account's cash is built from raw money flows by [`examples/make_demo.py`](../examples/make_demo.py), not from the engine, so running `check` on it is a real test.

## Found an eighth trap?

Open an issue with the raw rows (scrub your tickers and amounts first) and what you expected to see.
