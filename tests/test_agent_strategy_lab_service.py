from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.services.agent_strategy_lab_service import (
    AgentStrategyLabError,
    approve_candidate,
    build_data_quality_report,
    create_lab_run,
    create_lab_task,
    generate_stock_options_swing_candidates,
    get_lab_task,
    list_approvals,
    preview_candidate_diff,
    rollback_approval,
    validate_candidate,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")


def _seed_lab_root(tmp_path: Path) -> None:
    sub = tmp_path / "data" / "qqq_0dte"
    _write_json(
        sub / "live_worker_config.json",
        {
            "symbol": "QQQ.US",
            "kline": "1m",
            "strategy_config": {
                "strategy_variant": "morning_strangle",
                "symbol": "QQQ.US",
                "assume_bars_timezone": "Asia/Shanghai",
                "max_trades_per_day": 1,
                "strangle_entry_start_hhmm_et": "09:35",
                "strangle_entry_end_hhmm_et": "10:30",
                "strangle_force_close_hhmm_et": "12:00",
                "strangle_range_pct": 0.003,
                "strangle_take_profit_return": 0.5,
                "strangle_long_leg_take_profit_pct": 1.0,
                "strangle_short_leg_take_profit_pct": 0.3,
                "call_strikes_otm": 2,
                "put_strikes_otm": 2,
            },
        },
    )
    _write_json(
        sub / "strategy_recommendation.json",
        {
            "ok": True,
            "recommended_variant": "morning_strangle",
            "features": {
                "symbol": "QQQ.US",
                "spot": 700.0,
                "change_pct_from_prev_close": -0.25,
                "volume_ratio_today_vs_recent_days": 1.3,
            },
        },
    )
    _append_jsonl(
        sub / "live_worker_decision_tail.jsonl",
        [
            {
                "at": "2026-05-19T14:48:24+00:00",
                "owner_id": "davies",
                "account_id": "aisura",
                "bar_utc": "2026-05-19T14:48:00+00:00",
                "action": {"action": "hold", "bar_utc": "2026-05-19T14:48:00+00:00"},
            },
            {
                "at": "2026-05-19T14:49:24+00:00",
                "owner_id": "davies",
                "account_id": "aisura",
                "bar_utc": "2026-05-19T14:49:00+00:00",
                "action": {"action": "hold", "bar_utc": "2026-05-19T14:49:00+00:00"},
            },
        ],
    )
    _append_jsonl(
        sub / "live_worker_execution_ledger.jsonl",
        [
            {"at": "2026-05-19T13:32:19+00:00", "side": "buy", "symbol": "QQQ260519C704000.US"},
            {"at": "2026-05-19T14:14:08+00:00", "side": "sell", "symbol": "QQQ260519C704000.US"},
        ],
    )


def _seed_daily_kline_cache(tmp_path: Path, symbol: str = "QQQ.US", bars: int = 260) -> None:
    from datetime import date, timedelta

    items = []
    d = date(2025, 1, 2)
    close = 100.0
    for i in range(bars):
        close += 0.28 + (0.04 if i % 5 in {1, 2} else -0.01)
        items.append(
            {
                "date": (d + timedelta(days=i)).isoformat(),
                "open": round(close - 0.12, 4),
                "high": round(close + 0.35, 4),
                "low": round(close - 0.42, 4),
                "close": round(close, 4),
                "volume": 1000000 + i * 1000,
            }
        )
    stem = symbol.upper().replace(".", "_").replace("-", "_")
    _write_json(tmp_path / "data" / "klines" / f"{stem}__1d__d60.json", {"items": items})


def _passing_backtest(body: dict) -> dict:
    return {
        "bar_count": 390,
        "open_events": 3,
        "close_events": 3,
        "realized_pnl": 120.0,
        "return_pct": 12.5,
        "total_fee": 8.0,
        "stats": {"closed_trades": 3, "wins": 2, "losses": 1, "win_rate_pct": 66.67},
        "trades": [
            {"event": "close", "bar_time_et": "2026-05-01T10:30:00-04:00", "net_pnl": 60.0},
            {"event": "close", "bar_time_et": "2026-05-02T10:30:00-04:00", "net_pnl": -20.0},
            {"event": "close", "bar_time_et": "2026-05-03T10:30:00-04:00", "net_pnl": 80.0},
        ],
        "config": body.get("strategy_config", {}),
    }


def _failing_backtest(body: dict) -> dict:
    return {
        "bar_count": 390,
        "open_events": 1,
        "close_events": 1,
        "realized_pnl": -50.0,
        "return_pct": -5.0,
        "total_fee": 3.0,
        "stats": {"closed_trades": 1, "wins": 0, "losses": 1, "win_rate_pct": 0.0},
        "trades": [{"event": "close", "bar_time_et": "2026-05-01T10:30:00-04:00", "net_pnl": -50.0}],
        "config": body.get("strategy_config", {}),
    }


def _wait_task(task_id: str, timeout_seconds: float = 5.0) -> dict:
    import time

    deadline = time.time() + timeout_seconds
    latest = {}
    while time.time() < deadline:
        latest = get_lab_task(task_id)["task"]
        if latest.get("status") in {"completed", "failed"}:
            return latest
        time.sleep(0.05)
    return latest


def test_data_quality_reads_config_logs_and_ledger(tmp_path: Path) -> None:
    _seed_lab_root(tmp_path)

    report = build_data_quality_report(tmp_path, "0dte")

    assert report["instance"] == "0dte"
    assert report["ok"] is True
    assert report["summary"]["action_counts"]["hold"] == 2
    assert report["current_config"]["strategy_config"]["strategy_variant"] == "morning_strangle"


def test_create_lab_run_generates_candidates_and_validates(tmp_path: Path) -> None:
    _seed_lab_root(tmp_path)

    out = create_lab_run(
        {
            "instance": "0dte",
            "validation_windows_days": [60],
            "max_candidates": 2,
            "use_server_kline_cache": True,
            "rth_only": True,
        },
        root=tmp_path,
        backtest_runner=_passing_backtest,
    )

    run = out["run"]
    assert run["pipeline"][-2]["status"] == "waiting_for_human"
    assert run["pipeline"][-1]["status"] == "not_touched"
    assert len(run["candidates"]) == 2
    assert all(c["validation"]["passed"] for c in run["candidates"])
    assert all(c["safety_note"] for c in run["candidates"])
    patches = [c["strategy_config_patch"] for c in run["candidates"]]
    assert any("call_strikes_otm" in patch for patch in patches)
    assert any("put_strikes_otm" in patch for patch in patches)
    assert any("strangle_take_profit_return" in patch for patch in patches)
    assert any("strangle_long_leg_take_profit_pct" in patch for patch in patches)
    assert any("strangle_short_leg_take_profit_pct" in patch for patch in patches)


def test_morning_strangle_candidates_cover_risk_and_otm_steps(tmp_path: Path) -> None:
    _seed_lab_root(tmp_path)

    out = create_lab_run(
        {
            "instance": "0dte",
            "strategy_variant": "morning_strangle",
            "validation_windows_days": [60],
            "max_candidates": 3,
            "use_server_kline_cache": True,
            "rth_only": True,
        },
        root=tmp_path,
        backtest_runner=_passing_backtest,
    )

    candidates = out["run"]["candidates"]
    assert [c["candidate_id"] for c in candidates] == [
        "baseline_guarded",
        "take_profit_sensitive_strangle",
        "stop_loss_guarded_strangle",
    ]
    baseline_patch = candidates[0]["strategy_config_patch"]
    tp_patch = candidates[1]["strategy_config_patch"]
    sl_patch = candidates[2]["strategy_config_patch"]
    assert baseline_patch["call_strikes_otm"] == 2
    assert baseline_patch["put_strikes_otm"] == 2
    assert tp_patch["call_strikes_otm"] == 1
    assert tp_patch["put_strikes_otm"] == 1
    assert tp_patch["strangle_take_profit_return"] < baseline_patch["strangle_take_profit_return"]
    assert tp_patch["strangle_short_leg_take_profit_pct"] < baseline_patch["strangle_short_leg_take_profit_pct"]
    assert sl_patch["call_strikes_otm"] == 3
    assert sl_patch["put_strikes_otm"] == 3
    assert sl_patch["strangle_stop_loss_return"] > 0
    assert sl_patch["strangle_leg_stop_loss_pct"] > 0
    assert sl_patch["strangle_stop_loss_cooldown_minutes"] >= 8


def test_morning_strangle_time_window_dimension_only_changes_timing(tmp_path: Path) -> None:
    _seed_lab_root(tmp_path)

    out = create_lab_run(
        {
            "instance": "0dte",
            "strategy_variant": "morning_strangle",
            "research_dimension": "time_window",
            "validation_windows_days": [60],
            "max_candidates": 3,
        },
        root=tmp_path,
        backtest_runner=_passing_backtest,
    )

    candidates = out["run"]["candidates"]
    assert out["run"]["request"]["research_dimension"] == "time_window"
    assert [c["candidate_id"] for c in candidates] == [
        "baseline_guarded",
        "time_window_early_strangle",
        "time_window_late_strangle",
    ]
    for candidate in candidates[1:]:
        patch_keys = set(candidate["strategy_config_patch"])
        assert {"strangle_entry_start_hhmm_et", "strangle_entry_end_hhmm_et", "strangle_force_close_hhmm_et"} <= patch_keys
        assert "call_strikes_otm" not in patch_keys
        assert "put_strikes_otm" not in patch_keys
        assert "strangle_take_profit_return" not in patch_keys
        assert "strangle_stop_loss_return" not in patch_keys
        assert "strangle_leg_stop_loss_pct" not in patch_keys


def test_morning_strangle_combined_dimension_changes_timing_risk_and_steps(tmp_path: Path) -> None:
    _seed_lab_root(tmp_path)

    out = create_lab_run(
        {
            "instance": "0dte",
            "strategy_variant": "morning_strangle",
            "research_dimension": "combined",
            "validation_windows_days": [60],
            "max_candidates": 3,
        },
        root=tmp_path,
        backtest_runner=_passing_backtest,
    )

    candidates = out["run"]["candidates"]
    assert out["run"]["request"]["research_dimension"] == "combined"
    assert [c["candidate_id"] for c in candidates] == [
        "baseline_guarded",
        "combined_near_tp_strangle",
        "combined_wide_sl_strangle",
    ]
    near_patch = candidates[1]["strategy_config_patch"]
    wide_patch = candidates[2]["strategy_config_patch"]
    assert "strangle_entry_start_hhmm_et" in near_patch
    assert "strangle_force_close_hhmm_et" in near_patch
    assert "call_strikes_otm" in near_patch
    assert "put_strikes_otm" in near_patch
    assert "strangle_take_profit_return" in near_patch
    assert "strangle_stop_loss_return" in wide_patch
    assert "strangle_leg_stop_loss_pct" in wide_patch


def test_create_lab_run_supports_morning_directional(tmp_path: Path) -> None:
    _seed_lab_root(tmp_path)

    out = create_lab_run(
        {
            "instance": "0dte",
            "strategy_variant": "morning_directional",
            "validation_windows_days": [60],
            "max_candidates": 2,
            "use_server_kline_cache": True,
            "rth_only": True,
        },
        root=tmp_path,
        backtest_runner=_passing_backtest,
    )

    run = out["run"]
    assert run["request"]["strategy_variant"] == "morning_directional"
    assert len(run["candidates"]) == 2
    assert all(c["strategy_config"]["strategy_variant"] == "morning_directional" for c in run["candidates"])
    assert any("directional_take_profit_return" in c["strategy_config_patch"] for c in run["candidates"])
    assert any("directional_stop_loss_pct" in c["strategy_config_patch"] for c in run["candidates"])
    assert any("call_strikes_otm" in c["strategy_config_patch"] for c in run["candidates"])
    assert any("put_strikes_otm" in c["strategy_config_patch"] for c in run["candidates"])


def test_create_lab_run_supports_morning_double_strangle(tmp_path: Path) -> None:
    _seed_lab_root(tmp_path)

    out = create_lab_run(
        {
            "instance": "0dte",
            "strategy_variant": "morning_double_strangle",
            "validation_windows_days": [60],
            "max_candidates": 3,
            "use_server_kline_cache": True,
            "rth_only": True,
        },
        root=tmp_path,
        backtest_runner=_passing_backtest,
    )

    candidates = out["run"]["candidates"]
    assert out["run"]["request"]["strategy_variant"] == "morning_double_strangle"
    assert [c["candidate_id"] for c in candidates] == [
        "double_strangle_baseline_guarded",
        "double_strangle_gap_2_fast_tp",
        "double_strangle_gap_3_guarded",
    ]
    assert all(c["strategy_config"]["strategy_variant"] == "morning_double_strangle" for c in candidates)
    patches = [c["strategy_config_patch"] for c in candidates]
    assert any("double_strangle_call_long_strikes_otm" in patch for patch in patches)
    assert any("double_strangle_call_short_strikes_otm" in patch for patch in patches)
    assert any("double_strangle_put_long_strikes_otm" in patch for patch in patches)
    assert any("double_strangle_put_short_strikes_otm" in patch for patch in patches)
    assert any("double_strangle_call_long_leg_take_profit_pct" in patch for patch in patches)
    assert any("double_strangle_call_short_leg_take_profit_pct" in patch for patch in patches)
    assert any("double_strangle_combo_take_profit_pct" in patch for patch in patches)
    assert any("double_strangle_combo_stop_loss_pct" in patch for patch in patches)
    assert candidates[1]["strategy_config_patch"]["double_strangle_call_long_strikes_otm"] - candidates[1]["strategy_config_patch"]["double_strangle_call_short_strikes_otm"] == 2
    assert candidates[2]["strategy_config_patch"]["double_strangle_call_long_strikes_otm"] - candidates[2]["strategy_config_patch"]["double_strangle_call_short_strikes_otm"] == 3


def test_morning_double_strangle_leg_gap_dimension_generates_gap_sweep(tmp_path: Path) -> None:
    _seed_lab_root(tmp_path)

    out = create_lab_run(
        {
            "instance": "0dte",
            "strategy_variant": "morning_double_strangle",
            "research_dimension": "leg_gap",
            "validation_windows_days": [60],
            "max_candidates": 4,
        },
        root=tmp_path,
        backtest_runner=_passing_backtest,
    )

    candidates = out["run"]["candidates"]
    assert out["run"]["request"]["research_dimension"] == "leg_gap"
    assert [c["candidate_id"] for c in candidates] == [
        "double_strangle_baseline_guarded",
        "double_strangle_gap_1",
        "double_strangle_gap_2",
        "double_strangle_gap_3",
    ]
    gaps = [
        c["strategy_config_patch"]["double_strangle_call_long_strikes_otm"]
        - c["strategy_config_patch"]["double_strangle_call_short_strikes_otm"]
        for c in candidates[1:]
    ]
    assert gaps == [1, 2, 3]
    assert all(
        c["strategy_config_patch"]["double_strangle_put_long_strikes_otm"]
        - c["strategy_config_patch"]["double_strangle_put_short_strikes_otm"]
        == gap
        for c, gap in zip(candidates[1:], gaps)
    )


def test_create_lab_run_supports_tradingagents_candidate_generator(tmp_path: Path) -> None:
    _seed_lab_root(tmp_path)

    out = create_lab_run(
        {
            "instance": "0dte",
            "candidate_generator": "tradingagents",
            "validation_windows_days": [60],
            "max_candidates": 1,
        },
        root=tmp_path,
        backtest_runner=_passing_backtest,
    )

    run = out["run"]
    candidate = run["candidates"][0]
    assert run["request"]["candidate_generator"] == "tradingagents"
    assert run["pipeline"][1]["mode"] == "tradingagents_adapter"
    assert candidate["generator"] == "tradingagents"
    assert any("TradingAgents" in line for line in candidate["reasoning"])


def test_approval_requires_passing_validation_unless_forced(tmp_path: Path) -> None:
    _seed_lab_root(tmp_path)
    run = create_lab_run(
        {"instance": "0dte", "validation_windows_days": [60], "max_candidates": 1},
        root=tmp_path,
        backtest_runner=_failing_backtest,
    )["run"]
    cid = run["candidates"][0]["candidate_id"]

    with pytest.raises(AgentStrategyLabError, match="candidate_validation_not_passed"):
        approve_candidate(run["run_id"], cid, root=tmp_path)

    approved = approve_candidate(run["run_id"], cid, root=tmp_path, force=True, approved_by="tester")

    assert approved["approval"]["worker_started"] is False
    assert approved["approval"]["orders_sent"] is False
    cfg = json.loads((tmp_path / "data" / "qqq_0dte" / "live_worker_config.json").read_text(encoding="utf-8"))
    assert cfg["strategy_config"]["strategy_variant"] == "morning_strangle"
    assert (tmp_path / "data" / "qqq_0dte" / "agent_strategy_lab_approved_draft.json").is_file()


def test_approval_writes_morning_directional_strategy(tmp_path: Path) -> None:
    _seed_lab_root(tmp_path)
    run = create_lab_run(
        {
            "instance": "0dte",
            "strategy_variant": "morning_directional",
            "validation_windows_days": [60],
            "max_candidates": 1,
        },
        root=tmp_path,
        backtest_runner=_passing_backtest,
    )["run"]
    cid = run["candidates"][0]["candidate_id"]

    approve_candidate(run["run_id"], cid, root=tmp_path, approved_by="tester")

    cfg = json.loads((tmp_path / "data" / "qqq_0dte" / "live_worker_config.json").read_text(encoding="utf-8"))
    assert cfg["strategy_config"]["strategy_variant"] == "morning_directional"


def test_approval_writes_morning_double_strangle_patch_only(tmp_path: Path) -> None:
    _seed_lab_root(tmp_path)
    run = create_lab_run(
        {
            "instance": "0dte",
            "strategy_variant": "morning_double_strangle",
            "validation_windows_days": [60],
            "max_candidates": 1,
        },
        root=tmp_path,
        backtest_runner=_passing_backtest,
    )["run"]
    cid = run["candidates"][0]["candidate_id"]

    preview = preview_candidate_diff(run["run_id"], cid, root=tmp_path)
    patch = preview["strategy_config_patch"]
    assert patch["strategy_variant"] == "morning_double_strangle"
    assert "double_strangle_call_long_strikes_otm" in patch
    assert "double_strangle_combo_take_profit_pct" in patch
    assert "directional_down_pct" not in patch
    assert not any(str(key).startswith("gamma_") for key in patch)

    approve_candidate(run["run_id"], cid, root=tmp_path, approved_by="tester")

    cfg = json.loads((tmp_path / "data" / "qqq_0dte" / "live_worker_config.json").read_text(encoding="utf-8"))
    sc = cfg["strategy_config"]
    assert sc["strategy_variant"] == "morning_double_strangle"
    assert sc["double_strangle_call_long_strikes_otm"] > sc["double_strangle_call_short_strikes_otm"]
    assert sc["double_strangle_put_long_strikes_otm"] > sc["double_strangle_put_short_strikes_otm"]
    assert "directional_down_pct" not in sc
    assert "gamma_entry_start_hhmm_et" not in sc


def test_approval_diff_history_and_rollback(tmp_path: Path) -> None:
    _seed_lab_root(tmp_path)
    run = create_lab_run(
        {"instance": "0dte", "validation_windows_days": [60], "max_candidates": 2},
        root=tmp_path,
        backtest_runner=_passing_backtest,
    )["run"]
    cid = run["candidates"][1]["candidate_id"]

    preview = preview_candidate_diff(run["run_id"], cid, root=tmp_path)
    assert preview["candidate_id"] == cid
    assert any(row["field"] == "call_strikes_otm" for row in preview["diff"])
    assert any(row["field"] == "put_strikes_otm" for row in preview["diff"])
    assert any(row["field"] == "strangle_take_profit_return" for row in preview["diff"])
    assert all(not row["field"].startswith("gamma_") for row in preview["diff"])

    approved = approve_candidate(run["run_id"], cid, root=tmp_path, approved_by="tester")
    approvals = list_approvals(tmp_path, instance="0dte")
    assert approvals["items"][0]["approval_id"] == approved["approval"]["approval_id"]
    assert approvals["items"][0]["diff"]
    cfg = json.loads((tmp_path / "data" / "qqq_0dte" / "live_worker_config.json").read_text(encoding="utf-8"))
    assert "gamma_entry_start_hhmm_et" not in cfg["strategy_config"]
    assert "directional_down_pct" not in cfg["strategy_config"]
    assert cfg["strategy_config"]["call_strikes_otm"] == 1
    assert cfg["strategy_config"]["put_strikes_otm"] == 1

    rollback = rollback_approval(approved["approval"]["approval_id"], root=tmp_path, instance="0dte")
    assert rollback["rollback"]["worker_started"] is False
    assert rollback["rollback"]["orders_sent"] is False
    assert rollback["config"]["strategy_config"]["strangle_entry_end_hhmm_et"] == "10:30"


def test_create_lab_task_returns_immediately_and_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_lab_root(tmp_path)

    import api.services.agent_strategy_lab_service as svc

    monkeypatch.setattr(svc, "_default_backtest_runner", _passing_backtest)
    created = create_lab_task(
        {"instance": "0dte", "validation_windows_days": [60], "max_candidates": 1},
        root=tmp_path,
    )

    task = created["task"]
    assert created["async_run"] is True
    assert task["status"] == "queued"

    done = _wait_task(task["task_id"])
    assert done["status"] == "completed"
    assert done["progress_pct"] == 100
    assert done["run"]["candidates"][0]["validation"]["passed"] is True


def test_stock_options_swing_generates_debit_spread_candidate() -> None:
    candidates = generate_stock_options_swing_candidates(
        {"ok": True, "current_config": {"strategy": {"strategy_variant": "swing_trend_call"}, "risk": {}}},
        max_candidates=3,
        research_dimension="risk_controls",
        strategy_variant="swing_trend_call",
    )
    spread = [c for c in candidates if c["strategy_config_patch"]["strategy"].get("mode") == "call_debit_spread"]
    assert spread
    assert spread[0]["strategy_config_patch"]["strategy"]["spread_width_pct"] > 0
    assert spread[0]["strategy_config_patch"]["strategy"]["max_spread_debit"] > 0
    assert 0 < spread[0]["strategy_config_patch"]["strategy"]["max_spread_debit_to_width_pct"] <= 0.45
    assert spread[0]["strategy_config_patch"]["strategy"]["spread_min_hold_days_before_stop"] >= 3
    assert spread[0]["strategy_config_patch"]["strategy"]["sim_spread_slippage_pct"] > 0


def test_stock_options_swing_approx_backtest_supports_debit_spread(tmp_path: Path) -> None:
    _seed_daily_kline_cache(tmp_path)
    candidate = {
        "candidate_id": "spread",
        "strategy_config_patch": {
            "strategy": {
                "strategy_variant": "swing_trend_call",
                "mode": "call_debit_spread",
                "min_trend_score": 3,
                "target_dte": 75,
                "fallback_otm_pct": 0.02,
                "spread_width_pct": 0.05,
                "max_spread_debit": 800,
                "max_spread_debit_to_width_pct": 0.8,
                "spread_min_hold_days_before_stop": 3,
                "take_profit_pct": 0.4,
                "stop_loss_pct": 0.35,
                "dte_exit_days": 21,
            },
            "risk": {"max_premium_per_order": 1000},
        },
    }
    result = validate_candidate(
        candidate,
        instance="stock_options_swing",
        windows_days=[60],
        kline="1d",
        use_server_kline_cache=True,
        rth_only=True,
        root=tmp_path,
        data_quality={
            "current_config": {
                "symbol": "QQQ.US",
                "stock_pool": ["QQQ.US"],
                "strategy": {"strategy_variant": "swing_trend_call"},
                "risk": {"max_premium_per_order": 1000},
            }
        },
    )
    assert result["instance"] == "stock_options_swing"
    assert result["rows"][0]["metrics"]["model"] == "daily_trend_signal_black_scholes_proxy_with_spread_slippage"
    assert result["rows"][0]["metrics"]["historical_underlying_data"] == "real_daily_kline_cache"
    assert result["rows"][0]["metrics"]["option_price_history"] == "not_available_proxy_pricing"
    assert result["summary"]["historical_validation"]["underlying_history"] == "real_daily_kline_cache"
    assert result["summary"]["historical_validation"]["option_price_history"] == "not_available"
    assert result["gate"]["validation_confidence"] in {"low", "medium", "medium_high"}
    assert result["gate"]["not_real_option_history"] is True
    assert result["rows"][0]["metrics"]["trade_details"]
    first_trade = result["rows"][0]["metrics"]["trade_details"][0]
    assert first_trade["structure"] == "call_debit_spread"
    assert first_trade["debit_to_width_pct"] is not None
    assert first_trade["min_stop_hold_days"] == 3
    assert first_trade["spread_entry_mid"] is not None


def test_stock_options_swing_debit_spread_blocks_expensive_net_debit(tmp_path: Path) -> None:
    _seed_daily_kline_cache(tmp_path)
    candidate = {
        "candidate_id": "spread_too_expensive",
        "strategy_config_patch": {
            "strategy": {
                "strategy_variant": "swing_trend_call",
                "mode": "call_debit_spread",
                "min_trend_score": 3,
                "target_dte": 75,
                "fallback_otm_pct": 0.02,
                "spread_width_pct": 0.02,
                "max_spread_debit": 1000,
                "max_spread_debit_to_width_pct": 0.01,
                "take_profit_pct": 0.4,
                "stop_loss_pct": 0.35,
                "dte_exit_days": 21,
            },
            "risk": {"max_premium_per_order": 1000},
        },
    }
    result = validate_candidate(
        candidate,
        instance="stock_options_swing",
        windows_days=[60],
        kline="1d",
        use_server_kline_cache=True,
        rth_only=True,
        root=tmp_path,
        data_quality={
            "current_config": {
                "symbol": "QQQ.US",
                "stock_pool": ["QQQ.US"],
                "strategy": {"strategy_variant": "swing_trend_call"},
                "risk": {"max_premium_per_order": 1000},
            }
        },
    )
    metrics = result["rows"][0]["metrics"]
    assert metrics["closed_trades"] == 0
    assert metrics["primary_no_trade_reason"] == "spread_debit_too_high_vs_width"
    assert metrics["symbols_no_trade"][0]["last_budget_block"]["reason"] == "spread_debit_too_high_vs_width"
