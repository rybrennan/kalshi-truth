"""The shipped demo account reconciles cleanly, archive included.

The demo's cash balance is built from raw money flows by examples/make_demo.py,
independently of the engine, so the identity below is a real check.
"""

import json
from pathlib import Path

from kalshi_truth.audit import cash_identity
from kalshi_truth.ledger import reconcile_account

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_account.json"


def _demo():
    data = json.loads(DEMO.read_text())
    markets = reconcile_account(data["fills"], data["settlements"],
                                data["archived_positions"], data["markets"])
    return data, markets


def test_demo_account_reconciles_with_only_expected_flags():
    _, markets = _demo()
    assert len(markets) == 30                       # the empty archive row is skipped
    assert sum(m.method == "open" for m in markets) == 2
    assert sum(m.method == "archived" for m in markets) == 9
    # The only notes allowed: the two markets built with a missing close leg.
    flagged = [n for m in markets for n in m.notes]
    assert flagged == ["fills incomplete (settlement is authoritative)"] * 2
    assert not any("divergence" in n for n in flagged)


def test_demo_cash_identity_ties_to_the_cent():
    data, markets = _demo()
    r = cash_identity(markets, data["balance"], data["deposits"], data["withdrawals"])
    assert abs(r["residual"]) < 0.0001
