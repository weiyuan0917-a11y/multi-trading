import unittest
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MCP = ROOT / "mcp_server"
if str(MCP) not in sys.path:
    sys.path.insert(0, str(MCP))

from api import runtime_bridge as rt


class DashboardMarketPublicFallbackTests(unittest.TestCase):
    def test_normalize_market_snap_row_computes_missing_change_pct(self) -> None:
        row = rt._normalize_market_snap_row(
            {
                "symbol": "000001.SH",
                "last": 10.9,
                "prev_close": 10.99,
                "change_pct": None,
                "source": "mootdx",
            },
            "000001.SH",
            "上证综指",
            "public",
        )

        self.assertIsNotNone(row)
        self.assertEqual(row["symbol"], "000001.SH")
        self.assertEqual(row["name"], "上证综指")
        self.assertEqual(row["change_pct"], -0.82)
        self.assertEqual(row["source_label"], "mootdx / Tongdaxin public")

    def test_market_snap_uses_public_rows_without_broker(self) -> None:
        symbols = [("000001.SH", "上证综指"), ("399001.SZ", "深证成指")]
        public_rows = [
            {"symbol": "000001.SH", "last": 10.9, "prev_close": 10.99, "source": "mootdx"},
            {"symbol": "399001.SZ", "last": 15557.57, "prev_close": 15561.37, "change_pct": -0.02, "source": "eastmoney"},
        ]

        with patch("api.runtime_bridge._longbridge_market_data_ready", return_value=False), patch(
            "api.runtime_bridge._public_market_snap_resilient", return_value=public_rows
        ):
            rows = rt._market_snap_with_public_fallback(m=object(), symbols=symbols, owner_id=None)

        self.assertEqual([row["symbol"] for row in rows], ["000001.SH", "399001.SZ"])
        self.assertEqual(rows[0]["change_pct"], -0.82)
        self.assertEqual(rows[1]["change_pct"], -0.02)

    def test_market_snap_group_status_reports_public_fallback(self) -> None:
        rows = [{"symbol": "000001.SH", "source": "mootdx", "source_label": "mootdx / Tongdaxin public"}]
        status = rt._market_snap_group_status(rows, [("000001.SH", "上证综指"), ("399001.SZ", "深证成指")])
        self.assertEqual(status["available"], 1)
        self.assertEqual(status["missing_symbols"], ["399001.SZ"])
        self.assertTrue(status["public_fallback_used"])
        self.assertFalse(status["broker_required"])

    def test_resilient_public_snap_uses_us_last_good_cache_after_timeout(self) -> None:
        class FakeService:
            def quote(self, symbols, source="auto"):
                return {"ok": False, "items": []}

            def _us_cache_quote(self, symbol):
                return {
                    "symbol": symbol,
                    "last": 501.2,
                    "prev_close": 500.0,
                    "source": "us_local_cache",
                    "source_label": "Local US last-good cache",
                }

        with patch("api.runtime_bridge.get_public_market_data_service", return_value=FakeService()):
            rows = rt._public_market_snap_resilient([("SPY.US", "S&P 500 ETF")], overall_timeout=1.0)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "SPY.US")
        self.assertEqual(rows[0]["source"], "us_local_cache")
        self.assertEqual(rows[0]["change_pct"], 0.24)

    def test_resilient_public_snap_uses_hk_last_good_cache_after_timeout(self) -> None:
        class FakeService:
            def quote(self, symbols, source="auto"):
                return {"ok": False, "items": []}

            def _hk_cache_quote(self, symbol):
                return {
                    "symbol": symbol,
                    "last": 25000.0,
                    "prev_close": 24900.0,
                    "source": "hk_local_cache",
                    "source_label": "Local HK last-good cache",
                }

        with patch("api.runtime_bridge.get_public_market_data_service", return_value=FakeService()):
            rows = rt._public_market_snap_resilient([("HSI.HK", "恒生指数")], overall_timeout=1.0)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "HSI.HK")
        self.assertEqual(rows[0]["source"], "hk_local_cache")
        self.assertEqual(rows[0]["change_pct"], 0.4)

    def test_dashboard_market_cache_prevents_empty_refresh_from_dropping_rows(self) -> None:
        symbols = [("SPY.US", "标普500"), ("QQQ.US", "纳指100")]
        with rt._DASHBOARD_MARKET_CACHE_LOCK:
            rt._DASHBOARD_MARKET_CACHE["us"] = []

        first = rt._dashboard_market_cache_update(
            "us",
            [{"symbol": "SPY.US", "last": 501.2, "change_pct": 0.2, "source": "yahoo"}],
            symbols,
        )
        second = rt._dashboard_market_cache_update("us", [], symbols)

        self.assertEqual([row["symbol"] for row in first], ["SPY.US"])
        self.assertEqual([row["symbol"] for row in second], ["SPY.US"])
        self.assertTrue(second[0]["stale"])


if __name__ == "__main__":
    unittest.main()
