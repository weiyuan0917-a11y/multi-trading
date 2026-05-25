import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

from api import auto_trader_research as research


class _FakeTrader:
    def get_config(self):
        return {
            "strategies": ["momentum"],
            "pair_pool": {},
            "ml_model_type": "logreg",
        }


class _FakeProvider:
    def __init__(self, _primary):
        self.tradingagents = type("FakeTradingAgents", (), {"status": lambda _self: {"enabled": True}})()

    def strong_stocks(self, market, top_n, kline):
        return [{"symbol": "AAPL.US", "strength_score": 1.0, "price_type": "fake"}]

    def score_symbol(self, symbol, strategies, backtest_days, kline):
        return [{"strategy": "momentum", "composite_score": 1.0}]

    def external_market_regime(self, market):
        raise AssertionError("OpenBB market regime should be skipped")

    def external_symbol_factors(self, symbols, market, kline, limit=8):
        raise AssertionError("OpenBB symbol factors should be skipped")

    def external_tradingagents_insights(self, symbols, market, kline, limit=8):
        raise AssertionError("TradingAgents should be skipped")

    def pair_backtest(self, market, backtest_days, kline):
        raise AssertionError("pair backtest should be skipped")

    def provider_status(self):
        raise AssertionError("OpenBB provider status should be skipped")


class TestAutoTraderResearchOptions(unittest.TestCase):
    def test_research_options_skip_heavy_optional_providers(self):
        with patch.object(research, "ResearchProviderRouter", _FakeProvider), patch.object(
            research, "_build_ml_diagnostics", side_effect=AssertionError("ML diagnostics should be skipped")
        ):
            snap = research.run_research_snapshot(
                trader=_FakeTrader(),
                market="us",
                kline="1d",
                top_n=1,
                backtest_days=120,
                run_openbb=False,
                run_tradingagents=False,
                run_pair_backtest=False,
                run_ml_diagnostics=False,
            )

        self.assertEqual(
            {
                "openbb": False,
                "tradingagents": False,
                "pair_backtest": False,
                "ml_diagnostics": False,
            },
            snap["research_options"],
        )
        self.assertTrue(snap["external_research"]["market_regime"]["skipped"])
        self.assertEqual([], snap["external_research"]["symbol_factors"])
        self.assertEqual([], snap["external_research"]["tradingagents_insights"])
        self.assertTrue(snap["factor_gating"]["skipped"])
        self.assertTrue(snap["agent_gating"]["skipped"])
        self.assertTrue(snap["pair_backtest"]["skipped"])
        self.assertTrue(snap["ml_diagnostics"]["skipped"])
        self.assertTrue(snap["data_providers"]["openbb_skipped"])


if __name__ == "__main__":
    unittest.main()
