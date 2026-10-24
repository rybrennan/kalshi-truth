# kalshi-truth

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Read-only](https://img.shields.io/badge/trading-read--only-brightgreen.svg)

**Find out what you actually made on Kalshi.**

Kalshi's records are right. Trackers built on its API often aren't. The API records trades in ways that are easy to misread, and older history quietly moves to a second API. kalshi-truth reads all of it, works out your real profit and loss, and checks the answer against your cash balance.

```
$ kalshi-truth report
========================================================================
group             mkts       W-L       cost   realized      ROI
------------------------------------------------------------------------
KXMLBKS             10    5-5    $    38.19 $    -1.19    -3.1%
KXNBATOTAL           9    4-5    $    40.50 $    -9.50   -23.5%
KXNHLGAME            9    6-3    $    34.28 $   +12.72   +37.1%
------------------------------------------------------------------------
TOTAL               28   15-13   $   112.97 $    +2.03    +1.8%
  9 markets from Kalshi's archive (settled before 2026-10-16)
  open positions: 2 markets, ~$9.15 at risk (not in totals)
```
<sub>Demo account. The numbers are made up.</sub>

## Get started

Try it with no API key:

```bash
git clone https://github.com/rybrennan/kalshi-truth && cd kalshi-truth
pip install .
kalshi-truth report --from-file examples/demo_account.json
```

Then point it at your account:

```bash
export KALSHI_API_KEY=your-key-id
export KALSHI_PRIVATE_KEY_PATH=~/kalshi_key.pem

kalshi-truth report    # what you made, market by market
kalshi-truth check     # proof: ties every dollar back to your cash
```

## Or hand it to your AI agent

Paste this into Claude Code, Cursor, or any coding agent:

```text
Clone https://github.com/rybrennan/kalshi-truth, install it, and run the demo
with `kalshi-truth report --from-file examples/demo_account.json`.
Then help me create a Kalshi RSA API key, run `kalshi-truth report` and
`kalshi-truth check` on my account, and explain anything that gets flagged.
Don't ask me to paste my private key into the chat; reference the file path.
```

## The seven traps it catches

Each one fooled a real trading system's books. One left a lifetime P&L off by more than 10×.

1. **NO bets recorded as sells.** `action=sell` can mean you just *opened* a position.
2. **Cash-outs that look like double losses.** Close a winner early and the settlement shows two costs and zero revenue.
3. **Cents and dollars in the same field.** `0.74` and `74` can mean the same price.
4. **Contract counts under four different names.** `count`, `quantity`, `filled_count`, `count_fp`.
5. **Exit fills labeled from the other side.** Read the wrong side and a 99¢ fill becomes 1¢.
6. **History that silently truncates.** Stop paginating early and old trades just vanish.
7. **The archive.** After about two months, fills and settlements move to separate `/historical` endpoints. Trackers built on the live ones quietly shrink.

These aren't theoretical. Kalshi has since [deprecated the `action` and `side` fields](https://docs.kalshi.com/changelog) in favour of `outcome_side`, and open-source Kalshi libraries have [filed the same bugs](https://github.com/pmxt-dev/pmxt/issues/1188). Trap seven was found by this tool: the README listed six when it went to publish, and a full-history run before release came back with only two months of data. That is the check doing its job.

## How it works

Every fill is treated as a *purchase* of its own side, whatever the `action` field says. That one rule takes care of the first five traps. Each market's result comes from Kalshi's own settlement or archive record, and is checked against a second calculation from the raw fills. Then `check` ties the grand total to your cash:

```
your P&L  =  cash  +  money in open positions  −  what you deposited
```

The full walkthrough, with the formulas and every trap explained, is in [how it works](docs/how-it-works.md).

## Why you can trust it

- **It can't trade.** The client only reads. It has no way to place, change or cancel an order.
- **It checks itself.** `kalshi-truth check` adds up your deposits, your cash and every market, and shows whether they agree.
- **It's been run on a real account.** Thousands of markets, back to the first trade, came out within $1 of the cash balance.

## Good to know

- Needs Python 3.10+ and an RSA API key. Ed25519 keys aren't supported yet.
- Bookkeeping only: no picks, no signals, no strategy.
- Not affiliated with Kalshi.

---

Built by [Anchor 163](https://anchor163.com). Want this wired into your own trading system? [That's what we do](https://anchor163.com/kalshi-api-integration/). MIT licensed.
