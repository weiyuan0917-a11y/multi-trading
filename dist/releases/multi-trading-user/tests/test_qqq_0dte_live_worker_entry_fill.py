"""qqq_0dte_live_worker：开仓回包解析与重启恢复回归测试。"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

from api.qqq_0dte_live_worker import (
    _append_execution_ledger_from_detail,
    _entry_fill_prices_from_detail,
    _is_force_close_reason,
    _load_execution_ledger_rows,
    _restore_open_state_from_snapshot,
    _restore_open_state_from_broker,
    _sync_open_state_snapshot,
    _strangle_exit_fill_prices_from_detail,
)
from mcp_server.strategy_qqq_0dte.config import Qqq0dteConfig
from mcp_server.strategy_qqq_0dte.runner_live import Qqq0dteLiveSession
from mcp_server.strategy_qqq_0dte.state import OpenPosition


def test_entry_fill_single_leg_reaction_zone_shape() -> None:
    """反应区 / Gamma / 早盘方向单：与 options_trade_service.build_option_submit_response 一致。"""
    detail = {
        "step": "entry",
        "symbol": "QQQ260422C653000.US",
        "order": {
            "mode": "single_leg",
            "order": {"order_id": "1", "symbol": "QQQ260422C653000.US", "side": "buy", "contracts": 1, "price": 0.6},
            "risk": {},
        },
    }
    assert _entry_fill_prices_from_detail(detail) == {"single": 0.6}


def test_entry_fill_single_leg_string_price() -> None:
    detail = {
        "order": {
            "mode": "single_leg",
            "order": {"side": "buy", "price": "0.55"},
        }
    }
    assert _entry_fill_prices_from_detail(detail) == {"single": 0.55}


def test_entry_fill_single_leg_omit_side_treated_as_buy() -> None:
    detail = {"order": {"mode": "single_leg", "order": {"price": 0.4}}}
    assert _entry_fill_prices_from_detail(detail) == {"single": 0.4}


def test_entry_fill_single_leg_sell_not_used_for_entry_px() -> None:
    detail = {"order": {"mode": "single_leg", "order": {"side": "sell", "price": 0.6}}}
    assert _entry_fill_prices_from_detail(detail) == {}


def test_entry_fill_multi_leg_strangle() -> None:
    detail = {
        "resolved": [
            {"symbol": "QQQX.CALL", "right": "call"},
            {"symbol": "QQQX.PUT", "right": "put"},
        ],
        "order": {
            "mode": "multi_leg",
            "result": {
                "ok": True,
                "legs_submitted": [
                    {"symbol": "QQQX.CALL", "side": "buy", "contracts": 1, "price": 1.5},
                    {"symbol": "QQQX.PUT", "side": "buy", "contracts": 1, "price": 0.9},
                ],
            },
        },
    }
    assert _entry_fill_prices_from_detail(detail) == {"call": 1.5, "put": 0.9}


def test_strangle_exit_fill_single_leg() -> None:
    detail = {
        "resolved": [{"symbol": "QQQX.PUT", "right": "put"}],
        "order": {
            "mode": "single_leg",
            "order": {"symbol": "QQQX.PUT", "side": "sell", "contracts": 1, "price": 1.3},
        },
    }
    assert _strangle_exit_fill_prices_from_detail(detail) == {"put": 1.3}


def test_apply_strangle_leg_closed_records_realized_exit() -> None:
    session = Qqq0dteLiveSession(Qqq0dteConfig())
    session._ctl._pos = OpenPosition(
        side="strangle",
        strike=0.0,
        entry_bar_index=1,
        entry_time=datetime(2025, 6, 3, 10, 0, 0),
        entry_px=2.0,
        contracts=1,
        call_strike=500.0,
        put_strike=498.0,
        call_entry_px=1.0,
        put_entry_px=1.0,
        strangle_original_entry_px=2.0,
    )

    session.apply_strangle_leg_closed("put", 1.3)
    pos = session.open_position()

    assert pos is not None
    assert pos.strangle_realized_exit_px == 1.3
    assert pos.entry_px == 1.0
    assert pos.strangle_put_active is False


def test_force_close_reason_detection_only_matches_clock_exits() -> None:
    assert _is_force_close_reason("time_exit:strangle_force_close_et=11:30")
    assert _is_force_close_reason("time_exit:directional_force_close_et=11:30")
    assert _is_force_close_reason("time_exit:gamma_force_close_et=14:00")
    assert _is_force_close_reason("time_exit:gamma_pro_force_close_et=15:45")

    assert not _is_force_close_reason("time_exit:held_min=60.0>=60")
    assert not _is_force_close_reason("take_profit:strangle_R=0.6>=0.6")
    assert not _is_force_close_reason("stop_loss:pnl_pct=-0.35<=-0.35")


def test_restore_open_position_after_skipped_live_close_signal() -> None:
    session = Qqq0dteLiveSession(Qqq0dteConfig())
    session._ctl._pos = OpenPosition(
        side="long_call",
        strike=500.0,
        entry_bar_index=1,
        entry_time=datetime(2025, 6, 3, 10, 0, 0),
        entry_px=1.0,
        contracts=1,
    )
    snapshot = copy.deepcopy(session.open_position())

    session.clear_open_position()
    assert session.open_position() is None

    session.restore_open_position(snapshot)
    pos = session.open_position()
    assert pos is not None
    assert pos.side == "long_call"
    assert pos.strike == 500.0


def test_restore_open_state_from_broker_single_leg(monkeypatch, tmp_path: Path) -> None:
    session = Qqq0dteLiveSession(Qqq0dteConfig(symbol="QQQ.US"))
    monkeypatch.setattr("api.qqq_0dte_live_worker.DECISION_TAIL_FILE", str(tmp_path / "live_worker_decision_tail.jsonl"))
    ledger = tmp_path / "live_worker_execution_ledger.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "at": "2026-05-18T14:41:24+00:00",
                "order_id": "1",
                "symbol": "QQQ260518C709000.US",
                "side": "buy",
                "contracts": 1,
                "price": 0.86,
                "ts": 1778856084,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "api.qqq_0dte_live_worker._api_get_option_positions",
        lambda raw: [{"symbol": "QQQ260518C709000.US", "quantity": 1, "cost_price": 0.86}],
    )
    open_live, meta = _restore_open_state_from_broker(
        session=session,
        raw={"expiry_date": "2026-05-18"},
        cfg=Qqq0dteConfig(symbol="QQQ.US"),
        symbol="QQQ.US",
    )
    pos = session.open_position()
    assert pos is not None
    assert pos.side == "long_call"
    assert pos.strike == 709.0
    assert pos.entry_px == 0.86
    assert open_live == {"mode": "single", "symbol": "QQQ260518C709000.US"}
    assert meta and meta["restored"] is True


def test_restore_open_state_from_broker_strangle(monkeypatch, tmp_path: Path) -> None:
    session = Qqq0dteLiveSession(Qqq0dteConfig(symbol="QQQ.US"))
    monkeypatch.setattr("api.qqq_0dte_live_worker.DECISION_TAIL_FILE", str(tmp_path / "live_worker_decision_tail.jsonl"))
    ledger = tmp_path / "live_worker_execution_ledger.jsonl"
    ledger.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "at": "2026-05-18T14:20:00+00:00",
                        "order_id": "1",
                        "symbol": "QQQ260519C710000.US",
                        "side": "buy",
                        "contracts": 1,
                        "price": 3.11,
                        "ts": 1778854800,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "at": "2026-05-18T14:20:00+00:00",
                        "order_id": "2",
                        "symbol": "QQQ260519P706000.US",
                        "side": "buy",
                        "contracts": 1,
                        "price": 3.24,
                        "ts": 1778854800,
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "api.qqq_0dte_live_worker._api_get_option_positions",
        lambda raw: [
            {"symbol": "QQQ260519C710000.US", "quantity": 1, "cost_price": 3.09},
            {"symbol": "QQQ260519P706000.US", "quantity": 1, "cost_price": 3.24},
        ],
    )
    cfg = Qqq0dteConfig(symbol="QQQ.US")
    monkeypatch.setattr("api.qqq_0dte_live_worker._expiry_for_resolve", lambda raw, cfg, underlying: "2026-05-19")
    open_live, meta = _restore_open_state_from_broker(session=session, raw={}, cfg=cfg, symbol="QQQ.US")
    pos = session.open_position()
    assert pos is not None
    assert pos.side == "strangle"
    assert pos.call_strike == 710.0
    assert pos.put_strike == 706.0
    assert round(pos.call_entry_px, 2) == 3.11
    assert round(pos.put_entry_px, 2) == 3.24
    assert open_live == {"mode": "strangle", "call_symbol": "QQQ260519C710000.US", "put_symbol": "QQQ260519P706000.US"}
    assert meta and meta["restored"] is True


def test_restore_open_state_from_broker_skips_manual_position_without_ledger(monkeypatch, tmp_path: Path) -> None:
    session = Qqq0dteLiveSession(Qqq0dteConfig(symbol="QQQ.US"))
    monkeypatch.setattr("api.qqq_0dte_live_worker.DECISION_TAIL_FILE", str(tmp_path / "live_worker_decision_tail.jsonl"))
    monkeypatch.setattr(
        "api.qqq_0dte_live_worker._api_get_option_positions",
        lambda raw: [{"symbol": "QQQ260518C709000.US", "quantity": 1, "cost_price": 0.86}],
    )
    open_live, meta = _restore_open_state_from_broker(session=session, raw={}, cfg=Qqq0dteConfig(symbol="QQQ.US"), symbol="QQQ.US")
    assert open_live is None
    assert meta is None
    assert session.open_position() is None


def test_restore_open_state_from_snapshot_round_trip(monkeypatch, tmp_path: Path) -> None:
    session = Qqq0dteLiveSession(Qqq0dteConfig(symbol="QQQ.US"))
    monkeypatch.setattr("api.qqq_0dte_live_worker.OPEN_STATE_FILE", str(tmp_path / "live_worker_open_state.json"))
    session.restore_open_position(
        OpenPosition(
            side="strangle",
            strike=0.0,
            call_strike=710.0,
            put_strike=706.0,
            entry_bar_index=0,
            entry_time=datetime(2026, 5, 18, 10, 20, 0),
            entry_px=6.35,
            call_entry_px=3.11,
            put_entry_px=3.24,
            strangle_original_entry_px=6.35,
            contracts=1,
        )
    )
    open_live = {"mode": "strangle", "call_symbol": "QQQ260519C710000.US", "put_symbol": "QQQ260519P706000.US"}
    _sync_open_state_snapshot(session=session, open_live=open_live, symbol="QQQ.US", session_date="2026-05-18")

    restored = Qqq0dteLiveSession(Qqq0dteConfig(symbol="QQQ.US"))
    restored_open_live, meta = _restore_open_state_from_snapshot(
        session=restored,
        raw={},
        symbol="QQQ.US",
        positions=[
            {"symbol": "QQQ260519C710000.US", "quantity": 1, "cost_price": 3.09},
            {"symbol": "QQQ260519P706000.US", "quantity": 1, "cost_price": 3.24},
        ],
    )
    pos = restored.open_position()
    assert restored_open_live == open_live
    assert meta and meta["source"] == "snapshot"
    assert pos is not None
    assert pos.side == "strangle"
    assert pos.call_strike == 710.0
    assert pos.put_strike == 706.0


def test_load_execution_ledger_rows_backfills_from_decision_tail(monkeypatch, tmp_path: Path) -> None:
    decision_tail = tmp_path / "live_worker_decision_tail.jsonl"
    decision_tail.write_text(
        json.dumps(
            {
                "at": "2026-05-18T14:34:05+00:00",
                "action": {
                    "action": "entry",
                    "ok": True,
                    "detail": {
                        "step": "entry_strangle",
                        "order": {
                            "mode": "multi_leg",
                            "result": {
                                "ok": True,
                                "legs_submitted": [
                                    {"order_id": "1", "symbol": "QQQ260518C708000.US", "side": "buy", "contracts": 1, "price": 1.11},
                                    {"order_id": "2", "symbol": "QQQ260518P700000.US", "side": "buy", "contracts": 1, "price": 1.21},
                                ],
                            },
                        },
                    },
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("api.qqq_0dte_live_worker.DECISION_TAIL_FILE", str(decision_tail))
    rows = _load_execution_ledger_rows()
    assert len(rows) == 2
    assert {x["symbol"] for x in rows} == {"QQQ260518C708000.US", "QQQ260518P700000.US"}
    ledger = tmp_path / "live_worker_execution_ledger.jsonl"
    assert ledger.exists()


def test_append_execution_ledger_from_detail_writes_rows(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("api.qqq_0dte_live_worker.DECISION_TAIL_FILE", str(tmp_path / "live_worker_decision_tail.jsonl"))
    _append_execution_ledger_from_detail(
        {
            "order": {
                "mode": "multi_leg",
                "result": {
                    "legs_submitted": [
                        {"order_id": "1", "symbol": "QQQ260519C710000.US", "side": "buy", "contracts": 1, "price": 3.11},
                        {"order_id": "2", "symbol": "QQQ260519P706000.US", "side": "buy", "contracts": 1, "price": 3.24},
                    ]
                },
            }
        }
    )
    ledger = tmp_path / "live_worker_execution_ledger.jsonl"
    rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 2
    assert rows[0]["symbol"] == "QQQ260519C710000.US"
