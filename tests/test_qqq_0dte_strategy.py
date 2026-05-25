import os
import sys
import unittest
from collections import Counter
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcp_server.backtest_engine import Bar
from mcp_server.strategy_qqq_0dte.config import Qqq0dteConfig
from mcp_server.strategy_qqq_0dte.contract_select import select_strike
from mcp_server.strategy_qqq_0dte.backtest import run_qqq_0dte_backtest
from mcp_server.strategy_qqq_0dte.regime import classify_gap, GapRegime
from mcp_server.strategy_qqq_0dte.session_us import is_past_new_trade_cutoff
from mcp_server.strategy_qqq_0dte.zones import build_zones_from_levels, find_active_zone
from mcp_server.strategy_qqq_0dte.decision_log_summary import summarize_decision_messages
from mcp_server.strategy_qqq_0dte.exit_rules import (
    evaluate_double_strangle_exit,
    evaluate_exit,
    evaluate_morning_directional_exit,
    evaluate_strangle_exit,
)
from mcp_server.strategy_qqq_0dte.live_contract import normalize_option_right, resolve_from_chain_payload
from mcp_server.strategy_qqq_0dte.state import OpenPosition


class TestQqq0dteStrategy(unittest.TestCase):
    def test_config_roundtrip(self) -> None:
        c = Qqq0dteConfig(symbol="QQQ.US", max_trades_per_day=3)
        c2 = Qqq0dteConfig.from_dict(c.to_dict())
        self.assertEqual(c2.symbol, "QQQ.US")
        self.assertEqual(c2.max_trades_per_day, 3)

    def test_select_strike(self) -> None:
        self.assertEqual(select_strike(501.2, "call", strike_step=1.0, otm_steps=0), 501.0)
        self.assertEqual(select_strike(501.2, "put", strike_step=1.0, otm_steps=1), 500.0)

    def test_classify_gap(self) -> None:
        cfg = Qqq0dteConfig(gap_threshold_pct=0.001)
        self.assertEqual(classify_gap(101.0, 100.0, cfg), GapRegime.GAP_UP)

    def test_zones(self) -> None:
        zs = build_zones_from_levels([100.0, 100.5], 0.001)
        self.assertIsNotNone(find_active_zone(100.0, zs))

    def test_decision_summary_counts(self) -> None:
        c = Counter({"no_reaction_zone": 10, "no_volume_spike": 5, "enter_call": 1})
        s = summarize_decision_messages(c, bar_count=20, log_decisions_enabled=True, preview_tail=[])
        self.assertEqual(s["total_log_lines"], 16)
        self.assertEqual(s["by_message"][0]["message"], "no_reaction_zone")
        self.assertTrue(s["by_message"][0]["is_entry_blocker"])

    def test_backtest_empty(self) -> None:
        r = run_qqq_0dte_backtest([], Qqq0dteConfig())
        self.assertEqual(r["bar_count"], 0)
        self.assertIn("decision_summary", r)
        self.assertEqual(r["decision_summary"]["total_log_lines"], 0)
        self.assertEqual(r.get("open_premium_debit_usd"), 0.0)
        self.assertIsNone(r.get("return_pct"))

    def test_new_trade_cutoff_et(self) -> None:
        cfg = Qqq0dteConfig(
            no_new_trades_after_enabled=True,
            no_new_trades_after_hour_et=12,
            no_new_trades_after_minute_et=0,
        )
        tz = "America/New_York"
        self.assertFalse(is_past_new_trade_cutoff(datetime(2025, 6, 2, 11, 59, 0), cfg, tz))
        self.assertTrue(is_past_new_trade_cutoff(datetime(2025, 6, 2, 12, 0, 0), cfg, tz))
        self.assertFalse(is_past_new_trade_cutoff(datetime(2025, 6, 2, 12, 0, 0), Qqq0dteConfig(), tz))

    def test_normalize_option_right(self) -> None:
        self.assertEqual(normalize_option_right("C"), "call")
        self.assertEqual(normalize_option_right("put"), "put")

    def test_resolve_from_chain_payload(self) -> None:
        chain = {
            "symbol": "QQQ.US",
            "expiry_date": "2025-06-20",
            "options": [
                {
                    "strike_price": 500.0,
                    "call_symbol": "QQQ250620C500000.US",
                    "put_symbol": "QQQ250620P500000.US",
                    "call_quote": {"last_done": 1.2},
                    "put_quote": {"last_done": 0.8},
                },
            ],
        }
        r = resolve_from_chain_payload(chain, 500.0, "call")
        self.assertTrue(r["ok"])
        self.assertEqual(r["symbol"], "QQQ250620C500000.US")
        self.assertAlmostEqual(float(r["suggested_limit_price_per_share"]), 1.2)

    def test_resolve_from_chain_payload_bid_for_sell_uses_depth(self) -> None:
        from types import SimpleNamespace

        chain = {
            "symbol": "QQQ.US",
            "expiry_date": "2025-06-20",
            "options": [
                {
                    "strike_price": 500.0,
                    "call_symbol": "QQQ250620C500000.US",
                    "put_symbol": "QQQ250620P500000.US",
                    "call_quote": {"last_done": 9.99},
                    "put_quote": {"last_done": 0.8},
                },
            ],
        }

        class Ctx:
            def depth(self, s: str):
                return SimpleNamespace(bid=[SimpleNamespace(price="0.42")])

        r = resolve_from_chain_payload(chain, 500.0, "call", quote_ctx=Ctx(), use_bid_for_sell_limit=True)
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(float(r["suggested_limit_price_per_share"]), 0.42)
        self.assertEqual(r["suggested_limit_price_source"], "depth_bid")

    def test_resolve_from_chain_payload_ask_for_buy_uses_depth(self) -> None:
        from types import SimpleNamespace

        chain = {
            "symbol": "QQQ.US",
            "expiry_date": "2025-06-20",
            "options": [
                {
                    "strike_price": 500.0,
                    "call_symbol": "QQQ250620C500000.US",
                    "put_symbol": "QQQ250620P500000.US",
                    "call_quote": {"last_done": 0.05},
                    "put_quote": {"last_done": 0.8},
                },
            ],
        }

        class Ctx:
            def depth(self, s: str):
                return SimpleNamespace(ask=[SimpleNamespace(price="1.11")])

        r = resolve_from_chain_payload(chain, 500.0, "call", quote_ctx=Ctx(), use_ask_for_buy_limit=True)
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(float(r["suggested_limit_price_per_share"]), 1.11)
        self.assertEqual(r["suggested_limit_price_source"], "depth_ask")

    def test_backtest_runs_on_synthetic_bars(self) -> None:
        """两日美东 RTH 内 1m K（naive 时间按 NY 解释），确保回放不抛错。"""
        cfg = Qqq0dteConfig(
            assume_bars_timezone="America/New_York",
            no_trade_first_minutes=0,
            restricted_opening_minutes=0,
            volume_spike_multiplier=0.25,
            max_trades_per_day=5,
        )
        bars: list[Bar] = []
        base = datetime(2025, 6, 2, 9, 30, 0)
        for day in (0, 1):
            d0 = base + timedelta(days=day)
            for i in range(120):
                t = d0 + timedelta(minutes=i)
                px = 450.0 + day * 0.5 + i * 0.01
                vol = 1_000_000.0 if i == 60 else 100_000.0
                bars.append(
                    Bar(
                        date=t,
                        open=px,
                        high=px + 0.2,
                        low=px - 0.2,
                        close=px,
                        volume=vol,
                    )
                )
        r = run_qqq_0dte_backtest(bars, cfg)
        self.assertGreaterEqual(r["bar_count"], 200)
        self.assertIn("stats", r)
        self.assertIn("trades", r)

    def test_morning_strangle_opens_once(self) -> None:
        """两日 K：前收 500；次日 9:35 美东 bar 用 low 近似 bid，涨跌幅在阈值内 → 产生宽跨开仓。"""
        cfg = Qqq0dteConfig(
            assume_bars_timezone="America/New_York",
            strategy_variant="morning_strangle",
            max_trades_per_day=1,
            strangle_range_pct=0.01,
            strangle_entry_start_hhmm_et="09:35",
            strangle_entry_end_hhmm_et="10:00",
            strangle_underlying_field="low",
            log_decisions=False,
        )
        bars: list[Bar] = []
        bars.append(
            Bar(
                date=datetime(2025, 6, 2, 15, 0, 0),
                open=500.0,
                high=500.2,
                low=499.8,
                close=500.0,
                volume=1e6,
            )
        )
        bars.append(
            Bar(
                date=datetime(2025, 6, 3, 9, 35, 0),
                open=500.0,
                high=500.1,
                low=499.95,
                close=500.05,
                volume=1e6,
            )
        )
        r = run_qqq_0dte_backtest(bars, cfg)
        opens = [t for t in r["trades"] if t.get("event") == "open"]
        self.assertEqual(len(opens), 1)
        self.assertEqual(str(opens[0].get("side")), "strangle")
        self.assertIn("call_strike", opens[0])
        self.assertIn("put_strike", opens[0])

    def test_morning_double_strangle_opens_four_legs(self) -> None:
        cfg = Qqq0dteConfig(
            assume_bars_timezone="America/New_York",
            strategy_variant="morning_double_strangle",
            max_trades_per_day=1,
            strangle_range_pct=0.01,
            strangle_entry_start_hhmm_et="09:35",
            strangle_entry_end_hhmm_et="10:00",
            strangle_underlying_field="low",
            double_strangle_call_long_strikes_otm=2,
            double_strangle_call_short_strikes_otm=1,
            double_strangle_put_long_strikes_otm=2,
            double_strangle_put_short_strikes_otm=1,
            log_decisions=False,
        )
        bars: list[Bar] = [
            Bar(
                date=datetime(2025, 6, 2, 15, 0, 0),
                open=500.0,
                high=500.2,
                low=499.8,
                close=500.0,
                volume=1e6,
            ),
            Bar(
                date=datetime(2025, 6, 3, 9, 35, 0),
                open=500.0,
                high=500.1,
                low=499.95,
                close=500.05,
                volume=1e6,
            ),
        ]

        r = run_qqq_0dte_backtest(bars, cfg)
        opens = [t for t in r["trades"] if t.get("event") == "open"]

        self.assertEqual(len(opens), 1)
        self.assertEqual(str(opens[0].get("side")), "double_strangle")
        legs = opens[0].get("double_strangle_legs")
        self.assertIsInstance(legs, dict)
        assert isinstance(legs, dict)
        self.assertEqual(set(legs), {"call_long", "call_short", "put_long", "put_short"})
        self.assertGreater(float(legs["call_long"]["strike"]), float(legs["call_short"]["strike"]))
        self.assertLess(float(legs["put_long"]["strike"]), float(legs["put_short"]["strike"]))
        self.assertGreater(float(opens[0].get("entry_px") or 0.0), 0.0)

    def test_morning_directional_call_on_drawdown(self) -> None:
        """前收 500，9:35 bar low 使 chg ≤ -1% → 买入 Call。"""
        cfg = Qqq0dteConfig(
            assume_bars_timezone="America/New_York",
            strategy_variant="morning_directional",
            max_trades_per_day=1,
            directional_down_pct=0.01,
            directional_up_pct=0.01,
            strangle_underlying_field="low",
            log_decisions=False,
        )
        bars: list[Bar] = []
        bars.append(
            Bar(
                date=datetime(2025, 6, 2, 15, 0, 0),
                open=500.0,
                high=500.2,
                low=499.8,
                close=500.0,
                volume=1e6,
            )
        )
        bars.append(
            Bar(
                date=datetime(2025, 6, 3, 9, 35, 0),
                open=495.0,
                high=496.0,
                low=494.0,
                close=495.5,
                volume=1e6,
            )
        )
        r = run_qqq_0dte_backtest(bars, cfg)
        opens = [t for t in r["trades"] if t.get("event") == "open"]
        self.assertEqual(len(opens), 1)
        self.assertEqual(str(opens[0].get("side")), "long_call")

    def test_morning_directional_put_on_rally(self) -> None:
        """前收 500，9:35 bar low 使 chg ≥ +1% → 买入 Put。"""
        cfg = Qqq0dteConfig(
            assume_bars_timezone="America/New_York",
            strategy_variant="morning_directional",
            max_trades_per_day=1,
            directional_down_pct=0.01,
            directional_up_pct=0.01,
            strangle_underlying_field="low",
            log_decisions=False,
        )
        bars: list[Bar] = []
        bars.append(
            Bar(
                date=datetime(2025, 6, 2, 15, 0, 0),
                open=500.0,
                high=500.2,
                low=499.8,
                close=500.0,
                volume=1e6,
            )
        )
        bars.append(
            Bar(
                date=datetime(2025, 6, 3, 9, 35, 0),
                open=506.0,
                high=507.0,
                low=505.5,
                close=506.5,
                volume=1e6,
            )
        )
        r = run_qqq_0dte_backtest(bars, cfg)
        opens = [t for t in r["trades"] if t.get("event") == "open"]
        self.assertEqual(len(opens), 1)
        self.assertEqual(str(opens[0].get("side")), "long_put")

    def test_gamma_pro_breakout_entry(self) -> None:
        cfg = Qqq0dteConfig(
            assume_bars_timezone="America/New_York",
            strategy_variant="gamma_pro",
            log_decisions=False,
            max_trades_per_day=1,
            volume_lookback_bars=3,
            volume_spike_multiplier=1.2,
            gamma_pro_require_leader_confirmation=False,
        )
        bars: list[Bar] = [
            Bar(
                date=datetime(2025, 6, 2, 15, 30, 0),
                open=499.0,
                high=500.0,
                low=498.5,
                close=499.5,
                volume=200_000.0,
            ),
            Bar(
                date=datetime(2025, 6, 3, 9, 45, 0),
                open=499.4,
                high=499.6,
                low=499.2,
                close=499.5,
                volume=120_000.0,
            ),
            Bar(
                date=datetime(2025, 6, 3, 9, 50, 0),
                open=499.5,
                high=499.7,
                low=499.3,
                close=499.6,
                volume=130_000.0,
            ),
            Bar(
                date=datetime(2025, 6, 3, 10, 5, 0),
                open=500.2,
                high=501.4,
                low=500.1,
                close=501.2,
                volume=500_000.0,
            ),
        ]
        r = run_qqq_0dte_backtest(bars, cfg)
        opens = [t for t in r["trades"] if t.get("event") == "open"]
        self.assertEqual(len(opens), 1)
        self.assertEqual(str(opens[0].get("side")), "long_call")

    def test_strangle_exit_tp_bid_sl_last_split(self) -> None:
        """止盈用 bid、止损用 last：bid 很高但 last 已深亏时应先触发单腿止损。"""
        cfg = Qqq0dteConfig(
            strategy_variant="morning_strangle",
            strangle_leg_take_profit_pct=0.30,
            strangle_leg_stop_loss_pct=0.40,
            strangle_take_profit_return=9.0,
        )
        now = datetime(2025, 6, 3, 10, 0, 0)
        pos = OpenPosition(
            side="strangle",
            strike=0.0,
            entry_bar_index=1,
            entry_time=now,
            entry_px=2.0,
            contracts=1,
            call_strike=500.0,
            put_strike=498.0,
            call_entry_px=1.0,
            put_entry_px=1.0,
        )
        # bid 侧 call 仍贵（无 leg TP），但 last 侧 call 已腰斩 → 应先 leg SL call
        reason, detail, leg = evaluate_strangle_exit(pos, 2.0, 1.0, 0.45, 1.0, now, cfg, "America/New_York")
        self.assertEqual(reason, "stop_loss")
        self.assertEqual(leg, "call")
        self.assertIn("strangle_call_leg_sl", detail)

    def test_strangle_combo_tp_includes_realized_leg_after_partial_exit(self) -> None:
        cfg = Qqq0dteConfig(
            strategy_variant="morning_strangle",
            strangle_leg_take_profit_pct=0.0,
            strangle_leg_stop_loss_pct=0.0,
            strangle_take_profit_return=0.60,
        )
        now = datetime(2025, 6, 3, 10, 0, 0)
        pos = OpenPosition(
            side="strangle",
            strike=0.0,
            entry_bar_index=1,
            entry_time=now,
            entry_px=1.0,
            contracts=1,
            call_strike=500.0,
            put_strike=498.0,
            call_entry_px=1.0,
            put_entry_px=0.0,
            strangle_put_active=False,
            strangle_original_entry_px=2.0,
            strangle_realized_exit_px=1.3,
        )

        reason, detail, leg = evaluate_strangle_exit(pos, 1.9, 0.0, 1.9, 0.0, now, cfg, "America/New_York")

        self.assertEqual(reason, "take_profit")
        self.assertEqual(leg, "none")
        self.assertIn("strangle_R=0.6000", detail)

    def test_strangle_partial_leg_tp_precedes_combo_tp_then_combo_closes_remainder(self) -> None:
        cfg = Qqq0dteConfig(
            strategy_variant="morning_strangle",
            strangle_leg_take_profit_pct=1.0,
            strangle_leg_stop_loss_pct=0.0,
            strangle_take_profit_return=0.60,
        )
        now = datetime(2025, 6, 3, 10, 0, 0)
        pos = OpenPosition(
            side="strangle",
            strike=0.0,
            entry_bar_index=1,
            entry_time=now,
            entry_px=2.0,
            contracts=1,
            call_strike=500.0,
            put_strike=498.0,
            call_entry_px=1.0,
            put_entry_px=1.0,
            strangle_original_entry_px=2.0,
        )

        reason, detail, leg = evaluate_strangle_exit(pos, 1.0, 2.1, 1.0, 2.1, now, cfg, "America/New_York")
        self.assertEqual(reason, "take_profit")
        self.assertEqual(leg, "put")
        self.assertIn("strangle_put_leg_tp", detail)

        pos.strangle_realized_exit_px = 2.1
        pos.strangle_put_active = False
        pos.put_entry_px = 0.0
        pos.entry_px = 1.0
        reason2, detail2, leg2 = evaluate_strangle_exit(pos, 1.1, 0.0, 1.1, 0.0, now, cfg, "America/New_York")
        self.assertEqual(reason2, "take_profit")
        self.assertEqual(leg2, "none")
        self.assertIn("strangle_R=0.6000", detail2)

    def test_strangle_leg_tp_uses_long_short_otm_thresholds(self) -> None:
        cfg = Qqq0dteConfig(
            strategy_variant="morning_strangle",
            strangle_leg_take_profit_pct=0.0,
            strangle_long_leg_take_profit_pct=1.50,
            strangle_short_leg_take_profit_pct=0.50,
            strangle_leg_stop_loss_pct=0.0,
            strangle_take_profit_return=9.0,
        )
        now = datetime(2025, 6, 3, 10, 0, 0)
        pos = OpenPosition(
            side="strangle",
            strike=0.0,
            entry_bar_index=1,
            entry_time=now,
            entry_px=2.0,
            contracts=1,
            call_strike=502.0,
            put_strike=499.0,
            call_entry_px=1.0,
            put_entry_px=1.0,
            strangle_original_entry_px=2.0,
            call_strikes_otm=2,
            put_strikes_otm=1,
        )

        reason, detail, leg = evaluate_strangle_exit(pos, 2.6, 1.4, 2.6, 1.4, now, cfg, "America/New_York")
        self.assertEqual(reason, "take_profit")
        self.assertEqual(leg, "call")
        self.assertIn("strangle_call_leg_tp=1.6000>=1.5", detail)

        reason2, detail2, leg2 = evaluate_strangle_exit(pos, 1.4, 1.6, 1.4, 1.6, now, cfg, "America/New_York")
        self.assertEqual(reason2, "take_profit")
        self.assertEqual(leg2, "put")
        self.assertIn("strangle_put_leg_tp=0.6000>=0.5", detail2)

    def test_strangle_leg_tp_equal_otm_uses_short_threshold_for_both_legs(self) -> None:
        cfg = Qqq0dteConfig(
            strategy_variant="morning_strangle",
            strangle_long_leg_take_profit_pct=1.50,
            strangle_short_leg_take_profit_pct=0.50,
            strangle_take_profit_return=9.0,
        )
        now = datetime(2025, 6, 3, 10, 0, 0)
        pos = OpenPosition(
            side="strangle",
            strike=0.0,
            entry_bar_index=1,
            entry_time=now,
            entry_px=2.0,
            contracts=1,
            call_strike=501.0,
            put_strike=499.0,
            call_entry_px=1.0,
            put_entry_px=1.0,
            strangle_original_entry_px=2.0,
            call_strikes_otm=1,
            put_strikes_otm=1,
        )

        reason, detail, leg = evaluate_strangle_exit(pos, 1.6, 1.0, 1.6, 1.0, now, cfg, "America/New_York")
        self.assertEqual(reason, "take_profit")
        self.assertEqual(leg, "call")
        self.assertIn("strangle_call_leg_tp=0.6000>=0.5", detail)

    def test_strangle_leg_tp_legacy_field_remains_compatible(self) -> None:
        cfg = Qqq0dteConfig(
            strategy_variant="morning_strangle",
            strangle_leg_take_profit_pct=0.80,
            strangle_long_leg_take_profit_pct=0.0,
            strangle_short_leg_take_profit_pct=0.0,
            strangle_take_profit_return=9.0,
        )
        now = datetime(2025, 6, 3, 10, 0, 0)
        pos = OpenPosition(
            side="strangle",
            strike=0.0,
            entry_bar_index=1,
            entry_time=now,
            entry_px=2.0,
            contracts=1,
            call_strike=502.0,
            put_strike=499.0,
            call_entry_px=1.0,
            put_entry_px=1.0,
            strangle_original_entry_px=2.0,
            call_strikes_otm=2,
            put_strikes_otm=1,
        )

        reason, detail, leg = evaluate_strangle_exit(pos, 1.9, 1.0, 1.9, 1.0, now, cfg, "America/New_York")
        self.assertEqual(reason, "take_profit")
        self.assertEqual(leg, "call")
        self.assertIn("strangle_call_leg_tp=0.9000>=0.8", detail)

    def test_double_strangle_uses_per_leg_take_profit_thresholds(self) -> None:
        cfg = Qqq0dteConfig(
            strategy_variant="morning_double_strangle",
            double_strangle_call_long_leg_take_profit_pct=1.5,
            double_strangle_call_short_leg_take_profit_pct=0.3,
            double_strangle_put_long_leg_take_profit_pct=1.4,
            double_strangle_put_short_leg_take_profit_pct=0.25,
            double_strangle_single_leg_stop_loss_pct=0.0,
            double_strangle_combo_take_profit_pct=9.0,
            double_strangle_combo_stop_loss_pct=0.0,
            strangle_force_close_hhmm_et="15:00",
        )
        now = datetime(2025, 6, 3, 10, 0, 0)
        pos = OpenPosition(
            side="double_strangle",
            strike=0.0,
            entry_bar_index=1,
            entry_time=now,
            entry_px=4.0,
            strangle_original_entry_px=4.0,
            contracts=1,
            double_strangle_legs={
                "call_long": {"entry_px": 1.0, "active": True},
                "call_short": {"entry_px": 1.0, "active": True},
                "put_long": {"entry_px": 1.0, "active": True},
                "put_short": {"entry_px": 1.0, "active": True},
            },
        )

        reason, detail, leg = evaluate_double_strangle_exit(
            pos,
            {"call_long": 2.0, "call_short": 1.35, "put_long": 1.0, "put_short": 1.0},
            {"call_long": 1.0, "call_short": 1.0, "put_long": 1.0, "put_short": 1.0},
            now,
            cfg,
            "America/New_York",
        )

        self.assertEqual(reason, "take_profit")
        self.assertEqual(leg, "call_short")
        self.assertIn("double_strangle_call_short_tp=0.3500>=0.3", detail)

    def test_double_strangle_combo_tp_includes_realized_partial_exit(self) -> None:
        cfg = Qqq0dteConfig(
            strategy_variant="morning_double_strangle",
            double_strangle_call_long_leg_take_profit_pct=0.0,
            double_strangle_call_short_leg_take_profit_pct=0.0,
            double_strangle_put_long_leg_take_profit_pct=0.0,
            double_strangle_put_short_leg_take_profit_pct=0.0,
            double_strangle_single_leg_stop_loss_pct=0.0,
            double_strangle_combo_take_profit_pct=0.5,
            double_strangle_combo_stop_loss_pct=0.0,
            strangle_force_close_hhmm_et="15:00",
        )
        now = datetime(2025, 6, 3, 10, 0, 0)
        pos = OpenPosition(
            side="double_strangle",
            strike=0.0,
            entry_bar_index=1,
            entry_time=now,
            entry_px=3.0,
            strangle_original_entry_px=4.0,
            strangle_realized_exit_px=1.5,
            contracts=1,
            double_strangle_legs={
                "call_long": {"entry_px": 1.0, "active": False, "realized_exit_px": 1.5},
                "call_short": {"entry_px": 1.0, "active": True},
                "put_long": {"entry_px": 1.0, "active": True},
                "put_short": {"entry_px": 1.0, "active": True},
            },
        )

        reason, detail, leg = evaluate_double_strangle_exit(
            pos,
            {"call_short": 1.5, "put_long": 1.5, "put_short": 1.5},
            {"call_short": 1.0, "put_long": 1.0, "put_short": 1.0},
            now,
            cfg,
            "America/New_York",
        )

        self.assertEqual(reason, "take_profit")
        self.assertEqual(leg, "none")
        self.assertIn("double_strangle_R=0.5000", detail)

    def test_morning_directional_stop_loss(self) -> None:
        cfg = Qqq0dteConfig(
            strategy_variant="morning_directional",
            directional_stop_loss_pct=0.35,
            directional_take_profit_return=9.0,
        )
        now = datetime(2025, 6, 3, 10, 0, 0)
        pos = OpenPosition(
            side="long_call",
            strike=500.0,
            entry_bar_index=1,
            entry_time=now,
            entry_px=1.0,
            contracts=1,
        )
        # bid 仍高，但 last 已深亏 → 应止损
        r, d = evaluate_morning_directional_exit(pos, 1.2, now, cfg, "America/New_York", mark_sl=0.6)
        self.assertEqual(r, "stop_loss")
        self.assertIn("directional_sl", d)

    def test_evaluate_exit_tp_sl_split(self) -> None:
        cfg = Qqq0dteConfig(take_profit_pct=0.50, stop_loss_pct=0.30)
        now = datetime(2025, 6, 3, 10, 0, 0)
        pos = OpenPosition(
            side="long_call",
            strike=500.0,
            entry_bar_index=1,
            entry_time=now,
            entry_px=1.0,
            contracts=1,
        )
        # 止盈用 bid：1.2 未达 50% TP；止损用 last：0.65 为 -35% 触发 SL（先于 TP 分支仍先判 TP，故 bid 不能已达标）
        r_tp, _ = evaluate_exit(pos, 1.2, now, cfg, "America/New_York", mark_sl=0.65)
        self.assertEqual(r_tp, "stop_loss")


if __name__ == "__main__":
    unittest.main()
