"""Archived history: markets Kalshi has moved out of /portfolio/settlements.

Field shapes match /historical/positions, /historical/fills and
/historical/markets; tickers and amounts are synthetic.
"""

import asyncio

from kalshi_truth.audit import cash_identity, net_deposits
from kalshi_truth.client import KalshiReadOnlyClient
from kalshi_truth.ledger import reconcile_account, reconcile_archived, yes_value_of


def _fill(ticker, action, side, count, yes_p, fee="0", fill_id=None):
    return {"ticker": ticker, "action": action, "side": side,
            "count_fp": f"{count:.2f}", "yes_price_dollars": f"{yes_p:.4f}",
            "no_price_dollars": f"{1 - yes_p:.4f}", "fee_cost": fee,
            "fill_id": fill_id or f"{ticker}-{action}-{side}-{count}-{yes_p}"}


def _pos(ticker, pnl, fees, traded):
    return {"ticker": ticker, "realized_pnl_dollars": f"{pnl:.6f}",
            "fees_paid_dollars": f"{fees:.6f}", "total_traded_dollars": f"{traded:.6f}",
            "position_fp": "0.00", "market_exposure_dollars": "0.000000",
            "last_updated_ts": "2026-06-02T04:00:00Z", "exchange_index": 0}


TK = "KXMLBKS-26JUN011910ALPBRV-ALPPITCHER5-6"


class TestArchivedMarket:
    def test_archive_row_is_authority_and_fees_come_off(self):
        """Kalshi's archived realized P&L is BEFORE fees."""
        fills = [_fill(TK, "buy", "yes", 10, 0.40, fee="0.170000")]
        m = reconcile_archived(TK, fills, _pos(TK, 6.00, 0.17, 4.00), yes_value=1.0)
        assert m.method == "archived"
        assert m.realized == 5.83            # 10 × $1 − $4.00 − $0.17
        assert m.cost == 4.17                # notional + fees
        assert m.settled and m.won
        assert m.settle_result == "yes"
        assert not m.notes                   # fills twin agrees

    def test_cashed_out_no_in_archive(self):
        """The side-flip trap survives archival: NO entry as action=sell,
        close as a cheap YES buy, flattened into a $1 box."""
        fills = [_fill(TK, "sell", "no", 10, 0.30, fee="0.120000"),
                 _fill(TK, "buy", "yes", 10, 0.02, fee="0.007000")]
        m = reconcile_archived(TK, fills, _pos(TK, 2.80, 0.127, 7.20), yes_value=0.0)
        assert m.realized == 2.673
        assert m.box_cash == 10.0
        assert not m.notes

    def test_scalar_settlement_value(self):
        fills = [_fill(TK, "buy", "yes", 10, 0.40)]
        m = reconcile_archived(TK, fills, _pos(TK, 2.50, 0.0, 4.00),
                               yes_value=yes_value_of({"result": "scalar",
                                                       "settlement_value_dollars": "0.6500"}))
        assert m.realized == 2.50            # 10 × $0.65 − $4.00
        assert m.settle_result == "scalar"
        assert not m.notes

    def test_divergence_flagged(self):
        fills = [_fill(TK, "buy", "yes", 10, 0.40)]
        m = reconcile_archived(TK, fills, _pos(TK, -4.00, 0.0, 4.00), yes_value=1.0)
        assert m.realized == -4.0            # the archive row still wins
        assert any("divergence" in n for n in m.notes)

    def test_no_fills_still_exact(self):
        m = reconcile_archived(TK, [], _pos(TK, 1.25, 0.05, 3.00))
        assert m.realized == 1.20
        assert any("no fills" in n for n in m.notes)


class TestAccountWithArchive:
    def test_live_settlement_wins_and_empty_rows_skipped(self):
        live_tk = "KXNBATOTAL-26OCT21ALPBRV-221"
        empty_tk = "KXBTC15M-26JUN011500-00"
        fills = [_fill(TK, "buy", "yes", 10, 0.40, fee="0.17"),
                 _fill(live_tk, "buy", "yes", 4, 0.50)]
        sett = [{"ticker": live_tk, "market_result": "no", "revenue": 0,
                 "yes_count_fp": "4.00", "yes_total_cost_dollars": "2.00",
                 "no_count_fp": "0.00", "no_total_cost_dollars": "0", "fee_cost": "0"}]
        arch = [_pos(TK, 6.00, 0.17, 4.00),
                _pos(live_tk, 99.0, 0.0, 2.00),       # stale duplicate: ignored
                _pos(empty_tk, 0.0, 0.0, 0.0)]        # order never filled: skipped
        out = {m.ticker: m for m in reconcile_account(
            fills, sett, arch, {TK: {"result": "yes"}})}
        assert set(out) == {TK, live_tk}
        assert out[live_tk].method == "settlement" and out[live_tk].realized == -2.0
        assert out[TK].method == "archived" and out[TK].realized == 5.83


class TestLiveThenHistoricalFills:
    def test_overlap_deduplicated_by_fill_id(self):
        a = _fill(TK, "buy", "yes", 1, 0.5, fill_id="f1")
        b = _fill(TK, "buy", "yes", 2, 0.5, fill_id="f2")
        c = _fill(TK, "buy", "yes", 3, 0.5, fill_id="f3")
        pages = {"/trade-api/v2/portfolio/fills": [b, c],
                 "/trade-api/v2/historical/fills": [a, b]}   # f2 is in both

        client = KalshiReadOnlyClient.__new__(KalshiReadOnlyClient)

        async def fake_paginate(endpoint, key, params=None, max_pages=500):
            for row in pages[endpoint]:
                yield row
        client._paginate = fake_paginate

        got = asyncio.run(client.all_fills())
        assert sorted(f["fill_id"] for f in got) == ["f1", "f2", "f3"]


class TestCashIdentity:
    def test_deposit_fee_comes_off_and_pending_is_ignored(self):
        deps = [{"amount_cents": 10000, "fee_cents": 200, "status": "applied"},
                {"amount_cents": 5000, "fee_cents": 0, "status": "pending"}]
        assert net_deposits(deps, []) == 98.0

    def test_identity_ties_out(self):
        """Deposit $98 net; win $5.83 on one market; $2 still open."""
        fills = [_fill(TK, "buy", "yes", 10, 0.40, fee="0.17"),
                 _fill("KXNHLGAME-26OCT30ALPBRV-ALP", "buy", "yes", 4, 0.50)]
        markets = reconcile_account(fills, [], [_pos(TK, 6.00, 0.17, 4.00)],
                                    {TK: {"result": "yes"}})
        cash = 98.0 + 5.83 - 2.00
        r = cash_identity(markets, {"balance_dollars": f"{cash:.4f}"},
                          [{"amount_cents": 10000, "fee_cents": 200, "status": "applied"}], [])
        assert r["residual"] == 0.0
        assert r["open_cost"] == 2.0
