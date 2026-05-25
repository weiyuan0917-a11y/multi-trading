from unittest.mock import patch

from api import qqq_0dte_live_worker as worker
from mcp_server.strategy_qqq_0dte.config import Qqq0dteConfig
from mcp_server.strategy_qqq_0dte.runner_live import Qqq0dteLiveSession


def test_intraday_quote_quality_blocks_wide_spread() -> None:
    ok, detail = worker._validate_intraday_option_quote_quality(
        raw={"stock_options_mode": True, "intraday_safety": {"max_bid_ask_spread_pct": 0.1}},
        resolve_response={
            "ok": True,
            "symbol": "QQQ260619C00700000.US",
            "quote": {"bid": 0.5, "ask": 0.8},
        },
        leg={"symbol": "QQQ260619C00700000.US", "side": "buy", "contracts": 1, "price": 0.8},
    )

    assert ok is False
    assert "bid_ask_spread_too_wide" in detail["blocks"]


def test_intraday_quote_quality_blocks_missing_limit_price() -> None:
    ok, detail = worker._validate_intraday_option_quote_quality(
        raw={"stock_options_mode": True},
        resolve_response={"ok": True, "symbol": "QQQ260619P00690000.US", "quote": {"bid": 0.3, "ask": 0.32}},
        leg={"symbol": "QQQ260619P00690000.US", "side": "buy", "contracts": 1, "price": 0},
    )

    assert ok is False
    assert "limit_price_missing" in detail["blocks"]


def test_intraday_unmanaged_position_guard_blocks_same_underlying_manual_option() -> None:
    cfg = Qqq0dteConfig.from_dict({"symbol": "QQQ.US"})
    session = Qqq0dteLiveSession(cfg)
    positions = [{"symbol": "QQQ260619C00700000.US", "quantity": 1}]

    with (
        patch.object(worker, "_api_get_option_positions_checked", return_value=(positions, None)),
        patch.object(worker, "_worker_unclosed_lots_from_ledger", return_value={}),
    ):
        detail = worker._intraday_unmanaged_position_entry_guard(
            session=session,
            open_live=None,
            raw={"stock_options_mode": True},
            cfg=cfg,
            symbol="QQQ.US",
        )

    assert detail is not None
    assert detail["reason"] == "unmanaged_option_positions_detected"
    assert detail["count"] == 1


def test_intraday_post_entry_protection_cancels_active_submitted_order() -> None:
    raw = {"stock_options_mode": True, "intraday_safety": {"post_entry_check_delay_seconds": 0}}
    with (
        patch.object(
            worker,
            "_api_get_option_orders",
            return_value=[{"order_id": "oid-1", "symbol": "QQQ260619C00700000.US", "status": "New"}],
        ),
        patch.object(worker, "_api_cancel_order", return_value=(True, {"ok": True})) as cancel,
    ):
        ok, detail = worker._intraday_post_entry_order_protection(
            raw=raw,
            order_response={
                "mode": "single_leg",
                "order": {"order_id": "oid-1", "symbol": "QQQ260619C00700000.US", "side": "buy", "contracts": 1, "price": 0.5},
            },
            expected_legs=[{"symbol": "QQQ260619C00700000.US", "side": "buy", "contracts": 1, "price": 0.5}],
            dry_run=False,
        )

    assert ok is False
    assert detail["error"] == "entry_order_not_fully_filled"
    cancel.assert_called_once()


def test_intraday_post_entry_protection_writes_manual_review_lock(tmp_path) -> None:
    lock_path = tmp_path / "manual_review_lock.json"
    raw = {"stock_options_mode": True, "account_id": "acct-1", "intraday_safety": {"post_entry_check_delay_seconds": 0}}
    with (
        patch.object(worker, "MANUAL_REVIEW_LOCK_FILE", str(lock_path)),
        patch.object(
            worker,
            "_api_get_option_orders",
            return_value=[{"order_id": "oid-1", "symbol": "QQQ260619C00700000.US", "status": "New"}],
        ),
        patch.object(worker, "_api_cancel_order", return_value=(True, {"ok": True})),
    ):
        ok, detail = worker._intraday_post_entry_order_protection(
            raw=raw,
            order_response={
                "mode": "single_leg",
                "order": {"order_id": "oid-1", "symbol": "QQQ260619C00700000.US", "side": "buy", "contracts": 1, "price": 0.5},
            },
            expected_legs=[{"symbol": "QQQ260619C00700000.US", "side": "buy", "contracts": 1, "price": 0.5}],
            dry_run=False,
        )
        lock = worker._load_manual_review_lock()

    assert ok is False
    assert detail["manual_review_required"] is True
    assert lock is not None
    assert lock["reason"] == "entry_order_not_fully_filled"
    assert lock["order_ids"] == ["oid-1"]
    assert lock["account_id"] == "acct-1"


def test_intraday_manual_review_lock_blocks_new_entry(tmp_path) -> None:
    lock_path = tmp_path / "manual_review_lock.json"
    raw = {"stock_options_mode": True, "account_id": "acct-1"}
    with patch.object(worker, "MANUAL_REVIEW_LOCK_FILE", str(lock_path)):
        worker._write_manual_review_lock(
            raw=raw,
            reason="partial_multi_leg_submit",
            expected_legs=[{"symbol": "QQQ260619C00700000.US", "side": "buy", "contracts": 1, "price": 0.5}],
            order_response={"order": {"order_id": "oid-1"}},
        )
        guard = worker._intraday_manual_review_entry_guard(raw)

    assert guard is not None
    assert guard["blocked"] is True
    assert guard["reason"] == "manual_review_lock_active"
    assert guard["manual_review_lock"]["reason"] == "partial_multi_leg_submit"


def test_intraday_post_entry_reconciliation_uses_filled_order_rows(tmp_path) -> None:
    lock_path = tmp_path / "manual_review_lock.json"
    lifecycle_path = tmp_path / "order_lifecycle.jsonl"
    raw = {"stock_options_mode": True, "account_id": "acct-1", "intraday_safety": {"post_entry_check_delay_seconds": 0}}
    order_row = {
        "order_id": "oid-1",
        "symbol": "QQQ260619C00700000.US",
        "side": "buy",
        "quantity": 1,
        "filled_quantity": 1,
        "avg_fill_price": 0.52,
        "status": "Filled",
    }
    with (
        patch.object(worker, "MANUAL_REVIEW_LOCK_FILE", str(lock_path)),
        patch.object(worker, "ORDER_LIFECYCLE_FILE", str(lifecycle_path)),
        patch.object(worker, "_api_get_option_orders", side_effect=lambda _raw, status="all": [order_row] if status in {"all", "filled"} else []),
        patch.object(worker, "_api_get_option_positions_checked", return_value=([{"symbol": "QQQ260619C00700000.US", "quantity": 1}], None)),
    ):
        ok, detail = worker._intraday_post_entry_order_protection(
            raw=raw,
            order_response={
                "mode": "single_leg",
                "order": {"order_id": "oid-1", "symbol": "QQQ260619C00700000.US", "side": "buy", "contracts": 1, "price": 0.5},
            },
            expected_legs=[{"symbol": "QQQ260619C00700000.US", "side": "buy", "contracts": 1, "price": 0.5}],
            dry_run=False,
        )
        events = worker._recent_order_lifecycle_events()
        summary = worker._order_lifecycle_summary()

    assert ok is True
    rec = detail["reconciliation"]
    assert rec["all_expected_filled"] is True
    assert rec["uncertain"] is False
    assert rec["filled_ledger_rows"][0]["price"] == 0.52
    assert not lock_path.exists()
    assert [x["state"] for x in events] == ["submitted", "filled"]
    assert events[-1]["filled_contracts"] == 1
    assert summary["needs_attention"] is False
    assert summary["severity"] == "good"
    assert summary["pending_order_ids"] == []


def test_intraday_account_level_risk_blocks_live_when_account_unavailable() -> None:
    raw = {"stock_options_mode": True, "account_id": "acct-1", "dry_run": False, "account_risk": {"enabled": True}}

    with patch.object(worker, "_api_get_trade_account", return_value=None):
        gate = worker._account_level_risk_gate(
            raw,
            legs=[{"symbol": "QQQ260619C00700000.US", "side": "buy", "contracts": 1, "price": 1.2}],
            dry_run=False,
        )

    assert gate["blocked"] is True
    assert "account_unavailable" in gate["blocks"]
    assert gate["order_premium"] == 120.0


def test_intraday_post_entry_reconciliation_preserves_position_group_id(tmp_path) -> None:
    lock_path = tmp_path / "manual_review_lock.json"
    lifecycle_path = tmp_path / "order_lifecycle.jsonl"
    raw = {"stock_options_mode": True, "account_id": "acct-1", "intraday_safety": {"post_entry_check_delay_seconds": 0}}
    order_row = {
        "order_id": "oid-1",
        "symbol": "QQQ260619C00700000.US",
        "side": "buy",
        "quantity": 1,
        "filled_quantity": 1,
        "avg_fill_price": 0.52,
        "status": "Filled",
    }
    with (
        patch.object(worker, "MANUAL_REVIEW_LOCK_FILE", str(lock_path)),
        patch.object(worker, "ORDER_LIFECYCLE_FILE", str(lifecycle_path)),
        patch.object(worker, "_api_get_option_orders", side_effect=lambda _raw, status="all": [order_row] if status in {"all", "filled"} else []),
        patch.object(worker, "_api_get_option_positions_checked", return_value=([{"symbol": "QQQ260619C00700000.US", "quantity": 1}], None)),
    ):
        ok, detail = worker._intraday_post_entry_order_protection(
            raw=raw,
            order_response={
                "mode": "single_leg",
                "order": {"order_id": "oid-1", "symbol": "QQQ260619C00700000.US", "side": "buy", "contracts": 1, "price": 0.5},
            },
            expected_legs=[
                {
                    "symbol": "QQQ260619C00700000.US",
                    "side": "buy",
                    "contracts": 1,
                    "price": 0.5,
                    "position_group_id": "combo-1",
                    "combo_mode": "test_combo",
                }
            ],
            dry_run=False,
        )

    assert ok is True
    row = detail["reconciliation"]["filled_ledger_rows"][0]
    assert row["position_group_id"] == "combo-1"
    assert row["combo_mode"] == "test_combo"


def test_intraday_post_entry_reconciliation_locks_when_order_missing(tmp_path) -> None:
    lock_path = tmp_path / "manual_review_lock.json"
    lifecycle_path = tmp_path / "order_lifecycle.jsonl"
    raw = {"stock_options_mode": True, "account_id": "acct-1", "intraday_safety": {"post_entry_check_delay_seconds": 0}}
    with (
        patch.object(worker, "MANUAL_REVIEW_LOCK_FILE", str(lock_path)),
        patch.object(worker, "ORDER_LIFECYCLE_FILE", str(lifecycle_path)),
        patch.object(worker, "_api_get_option_orders", return_value=[]),
        patch.object(worker, "_api_get_option_positions_checked", return_value=([], None)),
    ):
        ok, detail = worker._intraday_post_entry_order_protection(
            raw=raw,
            order_response={
                "mode": "single_leg",
                "order": {"order_id": "oid-1", "symbol": "QQQ260619C00700000.US", "side": "buy", "contracts": 1, "price": 0.5},
            },
            expected_legs=[{"symbol": "QQQ260619C00700000.US", "side": "buy", "contracts": 1, "price": 0.5}],
            dry_run=False,
        )
        lock = worker._load_manual_review_lock()
        events = worker._recent_order_lifecycle_events()
        summary = worker._order_lifecycle_summary()

    assert ok is False
    assert detail["error"] == "entry_order_reconciliation_uncertain"
    assert detail["manual_review_required"] is True
    assert lock is not None
    assert lock["reason"] == "entry_order_reconciliation_uncertain"
    assert [x["state"] for x in events] == ["submitted", "uncertain", "manual_review"]
    assert events[-1]["reason"] == "entry_order_reconciliation_uncertain"
    assert summary["needs_attention"] is True
    assert summary["severity"] == "bad"
    assert summary["pending_order_ids"] == ["oid-1"]
