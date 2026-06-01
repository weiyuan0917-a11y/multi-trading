import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from mcp_server import options_service as svc


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp())


def test_worker_log_reader_includes_stock_options_swing_ledger(tmp_path: Path, monkeypatch) -> None:
    mcp_dir = tmp_path / "mcp_server"
    data_dir = tmp_path / "data" / "stock_options_swing"
    mcp_dir.mkdir()
    data_dir.mkdir(parents=True)
    ledger = data_dir / "live_worker_execution_ledger.jsonl"
    rows = [
        {
            "event": "entry_submitted",
            "at": "2026-06-01T14:30:00+00:00",
            "response": {
                "mode": "single_leg",
                "order": {
                    "order_id": "swing-entry",
                    "symbol": "AAPL260619C200000.US",
                    "side": "buy",
                    "contracts": 1,
                    "price": 2.0,
                },
            },
        },
        {
            "event": "exit_submitted",
            "at": "2026-06-01T16:30:00+00:00",
            "response": {
                "mode": "single_leg",
                "order": {
                    "order_id": "swing-exit",
                    "symbol": "AAPL260619C200000.US",
                    "side": "sell",
                    "contracts": 1,
                    "price": 3.0,
                },
            },
        },
        {
            "event": "exit_dry_run",
            "at": "2026-06-01T17:30:00+00:00",
            "order_preview": {"symbol": "AAPL260619C200000.US", "side": "sell", "contracts": 1, "price": 4.0},
        },
    ]
    ledger.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    monkeypatch.setattr(svc, "__file__", str(mcp_dir / "options_service.py"))

    got = svc._iter_option_executions_from_worker_logs(
        from_date=date(2026, 6, 1),
        to_date=date(2026, 6, 1),
        tz_name="UTC",
    )

    assert [(row["order_id"], row["side"], row["price"]) for row in got] == [
        ("swing-entry", "buy", 2.0),
        ("swing-exit", "sell", 3.0),
    ]


def test_pnl_calendar_order_detail_supplements_when_worker_logs_exist(monkeypatch) -> None:
    symbol = "AAPL260619C200000.US"
    buy_ts = _ts("2026-06-01T14:30:00")
    sell_ts = _ts("2026-06-01T16:30:00")

    monkeypatch.setattr(
        svc,
        "_iter_option_executions_from_worker_logs",
        lambda **_: [
            {
                "order_id": "worker-existing",
                "symbol": "QQQ260601C500000.US",
                "side": "buy",
                "qty": 1,
                "price": 1.0,
                "ts": buy_ts,
            }
        ],
    )
    monkeypatch.setattr(svc, "_iter_option_executions_for_range", lambda *_, **__: [])
    monkeypatch.setattr(
        svc.broker_service,
        "get_today_orders",
        lambda _ctx: [
            SimpleNamespace(order_id="detail-buy", symbol=symbol),
            SimpleNamespace(order_id="detail-sell", symbol=symbol),
        ],
    )

    details = {
        "detail-buy": SimpleNamespace(
            symbol=symbol,
            side="buy",
            executed_quantity=1,
            executed_price=2.0,
            updated_at=buy_ts,
            charge_detail=SimpleNamespace(total_amount=0),
            history=[],
        ),
        "detail-sell": SimpleNamespace(
            symbol=symbol,
            side="sell",
            executed_quantity=1,
            executed_price=3.0,
            updated_at=sell_ts,
            charge_detail=SimpleNamespace(total_amount=0),
            history=[],
        ),
    }
    monkeypatch.setattr(svc.broker_service, "get_order_detail", lambda _ctx, oid: details[oid])

    result = svc.get_option_pnl_calendar(
        SimpleNamespace(),
        from_date="2026-06-01",
        to_date="2026-06-01",
        tz_name="UTC",
    )

    day = result["days"][0]
    assert day["realized_pnl"] == 100.0
    assert day["closed_contracts"] == 1
    assert result["debug"]["execution_source"] == "worker_logs"
    assert result["debug"]["order_detail_executions_added"] == 2
