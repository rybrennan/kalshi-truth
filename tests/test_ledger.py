"""The engine, pinned on the hard cases.

Each fixture reproduces a case found while auditing a live Kalshi account
against its settlement rows, fill legs, and cash balance. Field shapes match
what the API returns; tickers, prices, and counts are synthetic. If one of
these breaks, the engine is wrong — not the fixture.
"""

from kalshi_truth.ledger import event_date_of, reconcile_account, reconcile_market, series_of


def _fill(action, side, count, yes_p, no_p, fee="0"):
    return {"action": action, "side": side, "count_fp": str(count),
            "yes_price_dollars": str(yes_p), "no_price_dollars": str(no_p),
            "fee_cost": fee}


class TestHardCases:
    def test_cashed_out_no_position(self):
        """THE side-flip case. Entering NO is recorded as action=sell; the
        close is a 2¢ YES buy. Naive trackers read this as 'YES bet costing
        $0.20, +1336%'. Truth: NO position, ~$7.33 all-in cost, +$2.67."""
        tk = "KXMLBKS-26JUL091410ALPBRV-ALPPITCHER18-6"
        fills = [
            _fill("sell", "no", 10, "0.3000", "0.7000", fee="0.120000"),
            _fill("buy", "yes", 10, "0.0200", "0.9800", fee="0.007000"),
        ]
        sett = {"market_result": "no", "revenue": 0,
                "yes_count_fp": "10.00", "yes_total_cost_dollars": "0.200000",
                "no_count_fp": "10.00", "no_total_cost_dollars": "7.000000",
                "fee_cost": "0.127000",
                "settled_time": "2026-07-10T02:00:00Z"}
        m = reconcile_market(tk, fills, sett)
        assert m.realized == 2.673           # exact, to the cent
        assert m.won is True
        assert m.method == "settlement"
        assert m.box_cash == 10.0            # closed via box, not settlement
        assert m.event_date == "2026-07-09"
        assert not m.notes                   # fills agree — no divergence flag

    def test_held_no_loss(self):
        """Held-to-settlement NO loss — a lone action=sell entry fill.
        The class of bet that loss-hiding trackers drop entirely."""
        tk = "KXMLBKS-26JUL012010CHRDLT-DLTPITCHER7-6"
        fills = [_fill("sell", "no", 10, "0.4000", "0.6000", fee="0.170000")]
        sett = {"market_result": "yes", "revenue": 0,
                "yes_count_fp": "0.00", "yes_total_cost_dollars": "0",
                "no_count_fp": "10.00", "no_total_cost_dollars": "6.000000",
                "fee_cost": "0.170000",
                "settled_time": "2026-07-02T03:36:56Z"}
        m = reconcile_market(tk, fills, sett)
        assert m.realized == -6.17           # cost 6.00 + fee 0.17, revenue 0
        assert m.won is False
        assert m.held_side == "NO"

    def test_cashed_out_yes_win_settlement_row(self):
        """A cashed-out YES winner, in the exact settlement-row shape the API
        returns. It reads like a two-legged loss (revenue 0, costs on both
        sides) unless you credit the box: min(5,5) × $1 = $5.00."""
        tk = "KXMLBHIT-26MAY291915ECHFXT-ECHBATTER3-1"
        sett = {"event_ticker": "KXMLBHIT-26MAY291915ECHFXT",
                "fee_cost": "0.080000", "market_result": "yes",
                "no_count_fp": "5.00", "no_total_cost_dollars": "0.050000",
                "revenue": 0, "settled_time": "2026-05-30T02:02:10.714312Z",
                "ticker": tk, "value": 100,
                "yes_count_fp": "5.00", "yes_total_cost_dollars": "3.100000"}
        m = reconcile_market(tk, [], sett)
        assert m.realized == 1.77            # 5.00 − 3.10 − 0.05 − 0.08
        assert m.won is True                 # NOT a loss, despite revenue 0

    def test_held_yes_win_pays_settlement_revenue(self):
        m = reconcile_market(
            "KXMLBKS-26JUL011840GLFHTL-X-6",
            [_fill("buy", "yes", 3, "0.50", "0.50", fee="0.03")],
            {"market_result": "yes", "revenue": 300,
             "yes_count_fp": "3.00", "yes_total_cost_dollars": "1.50",
             "no_count_fp": "0.00", "no_total_cost_dollars": "0",
             "fee_cost": "0.03"},
        )
        assert m.realized == 1.47            # 3.00 − 1.50 − 0.03
        assert m.held_side == "YES"

    def test_settled_market_with_no_fills_surfaced(self):
        """Settled ticker whose fills never surfaced: the settlement row is
        self-contained, so the answer is still exact."""
        m = reconcile_market(
            "KXMLBF5TOTAL-26MAY271307BRVECH-3",
            [],
            {"market_result": "no", "revenue": 600,
             "no_count_fp": "6.00", "no_total_cost_dollars": "1.800000",
             "yes_count_fp": "0.00", "yes_total_cost_dollars": "0",
             "fee_cost": "0.04"},
        )
        assert m.method == "settlement"
        assert m.realized == 4.16
        assert m.won is True

    def test_incomplete_fills_flagged_but_settlement_wins(self):
        """Only the entry leg surfaced from the fills API — the naive fills
        read would call this a −$7.12 loss. Settlement authority prevents it."""
        tk = "KXMLBKS-26JUL091410ALPBRV-ALPPITCHER18-6"
        fills = [_fill("sell", "no", 10, "0.3000", "0.7000", fee="0.120000")]
        sett = {"market_result": "no", "revenue": 0,
                "yes_count_fp": "10.00", "yes_total_cost_dollars": "0.200000",
                "no_count_fp": "10.00", "no_total_cost_dollars": "7.000000",
                "fee_cost": "0.127000"}
        m = reconcile_market(tk, fills, sett)
        assert m.realized == 2.673
        assert any("incomplete" in n for n in m.notes)

    def test_open_position_not_finalized(self):
        m = reconcile_market(
            "KXMLBKS-26JUL111910ALPHTL-X-6",
            [_fill("buy", "yes", 5, "0.40", "0.60", fee="0.05")],
            None,
        )
        assert m.method == "open"
        assert m.settled is False

    def test_open_but_fully_boxed_is_locked_cash(self):
        """Closed pre-settlement, settlement row not out yet — the box has
        already cash-settled, so realized is known now."""
        m = reconcile_market(
            "KXMLBKS-26JUL111910ALPHTL-X-6",
            [_fill("buy", "yes", 5, "0.40", "0.60", fee="0.05"),
             _fill("sell", "no", 5, "0.90", "0.10", fee="0.01")],
            None,
        )
        assert m.method == "open"
        assert m.realized == round(5.0 - (2.0 + 0.05 + 0.5 + 0.01), 4)  # +2.44


class TestAccountRollup:
    def test_groups_by_ticker_and_merges_multirow_settlements(self):
        fills = [
            {**_fill("buy", "yes", 2, "0.50", "0.50"), "ticker": "S-26JUL01X-1"},
            {**_fill("sell", "no", 3, "0.45", "0.55"), "ticker": "S-26JUL01X-2"},
        ]
        setts = [  # degenerate rows (no counts) → engine falls back to fills math
            {"ticker": "S-26JUL01X-1", "market_result": "yes", "revenue": 100},
            {"ticker": "S-26JUL01X-1", "market_result": "yes", "revenue": 100},
            {"ticker": "S-26JUL01X-2", "market_result": "yes", "revenue": 0},
        ]
        out = {m.ticker: m for m in reconcile_account(fills, setts)}
        assert out["S-26JUL01X-1"].settle_revenue == 2.0     # merged rows
        assert out["S-26JUL01X-1"].realized == 1.0           # 2.00 − 1.00
        assert out["S-26JUL01X-2"].realized == -1.65


class TestTickerParsing:
    def test_series(self):
        assert series_of("KXMLBKS-26JUL091410ALPBRV-ALPPITCHER18-6") == "KXMLBKS"
        assert series_of("") == "?"

    def test_event_date_mlb_and_nba_encodings(self):
        assert event_date_of("KXMLBF5TOTAL-26MAY272210CHRGLF-4") == "2026-05-27"
        assert event_date_of("KXNBAPTS-26MAY17DLTFXT-DLTGUARD8-20") == "2026-05-17"
        assert event_date_of("garbage") is None
