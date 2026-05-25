from unittest.mock import patch

from api import stock_options_swing_worker as worker


def test_swing_account_level_risk_blocks_live_when_account_unavailable() -> None:
    raw = {"dry_run": False, "auto_submit_orders": True, "account_id": "acct-1", "account_risk": {"enabled": True}}

    with patch.object(worker, "_fetch_trade_account", return_value=None):
        gate = worker._account_level_risk_gate(raw, positions=[], order_premium=250.0, dry_run=False)

    assert gate["blocked"] is True
    assert "account_unavailable" in gate["blocks"]
    assert gate["order_premium"] == 250.0


def test_swing_account_level_risk_blocks_order_over_account_pct() -> None:
    raw = {
        "dry_run": False,
        "auto_submit_orders": True,
        "account_id": "acct-1",
        "account_risk": {"enabled": True, "max_order_premium_pct": 0.05},
    }

    with patch.object(worker, "_fetch_trade_account", return_value={"net_assets": 1000.0, "buy_power": 1000.0}):
        gate = worker._account_level_risk_gate(raw, positions=[], order_premium=80.0, dry_run=False)

    assert gate["blocked"] is True
    assert "order_premium_pct_exceeded" in gate["blocks"]
    assert gate["max_order_premium"] == 50.0


def test_swing_submit_entry_rechecks_account_risk_before_live_order(tmp_path) -> None:
    raw = {
        "dry_run": False,
        "auto_submit_orders": True,
        "confirmation_token": "tok",
        "live_submit_confirmed_at": "2026-05-23T00:00:00+00:00",
        "contracts": 1,
        "account_risk": {"enabled": True, "max_order_premium_pct": 0.05},
    }
    contract_result = {
        "structure": "long_call",
        "contract": {"symbol": "QQQ260619C00700000.US", "ask": 1.2, "last": 1.1},
    }

    with (
        patch.object(worker, "LEDGER_FILE", str(tmp_path / "ledger.jsonl")),
        patch.object(worker, "_fetch_option_positions", return_value=([], None, "test")),
        patch.object(worker, "_fetch_trade_account", return_value={"net_assets": 1000.0, "buy_power": 1000.0}),
        patch.object(worker, "_api_post_json") as post,
    ):
        result = worker._submit_entry_order("QQQ.US", contract_result, raw, {"action": "candidate_long_call"})

    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["error"].startswith("account_risk:")
    assert "order_premium_pct_exceeded" in result["account_risk_gate"]["blocks"]
    post.assert_not_called()
