# AGENTS.md

Notes for AI coding agents working in this repo.

## What this is
A read-only tool that rebuilds a Kalshi account's real P&L from fills, settlements and the archive, then checks it against cash. Background: [docs/how-it-works.md](docs/how-it-works.md).

## Commands
```bash
pip install ".[dev]"
pytest                                                        # all tests, under a second
kalshi-truth report --from-file examples/demo_account.json    # demo, no key needed
kalshi-truth check  --from-file examples/demo_account.json    # must print OK
```

## Layout
- `kalshi_truth/client.py`: signed, GET-only Kalshi client (live + `/historical` endpoints)
- `kalshi_truth/ledger.py`: per-market reconciliation (settlement, archive, fills cross-check)
- `kalshi_truth/audit.py`: the cash identity behind `check`
- `kalshi_truth/fills.py`, `money.py`: field parsing (the traps live here)
- `examples/make_demo.py`: generates the synthetic demo account

## Rules
- **Never add a non-GET request.** Read-only is the product's core promise.
- **Never commit real account data.** Fixtures, demo data and docs use synthetic tickers and amounts. No API keys, `.env` or `.pem` files.
- **Don't trust a P&L change until `check` still ties out** on the demo, and on a real account if you have one.
- If you change the demo or report output, run `python examples/make_demo.py` and update the output blocks in `README.md` and `docs/how-it-works.md` to match exactly.
- Python 3.10+. Dependencies: `httpx`, `cryptography`. Keep it that small.
