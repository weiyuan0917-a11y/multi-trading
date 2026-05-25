import tempfile
import unittest
from unittest.mock import patch

from api.services import public_market_data_service as svc_mod
from api.services.public_market_data_service import PublicMarketDataService


class PublicMarketDataUsProviderTests(unittest.TestCase):
    def test_provider_order_contains_non_broker_us_sources(self) -> None:
        svc = PublicMarketDataService()
        order = svc._provider_order()
        self.assertLess(order.index("polygon"), order.index("eastmoney"))
        self.assertLess(order.index("twelvedata"), order.index("eastmoney"))
        self.assertLess(order.index("tencent_hk"), order.index("eastmoney"))
        self.assertLess(order.index("tencent_index"), order.index("mootdx"))
        self.assertIn("us_local_cache", order)
        self.assertIn("hk_local_cache", order)

    def test_unconfigured_api_key_provider_is_disabled(self) -> None:
        with patch.dict("os.environ", {"POLYGON_API_KEY": "", "TWELVE_DATA_API_KEY": ""}, clear=False):
            self.assertFalse(PublicMarketDataService._provider_enabled("polygon"))
            self.assertFalse(PublicMarketDataService._provider_enabled("twelvedata"))

    def test_last_good_cache_roundtrip_for_us_quote(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(svc_mod, "LAST_GOOD_CACHE_DIR", td):
            svc = PublicMarketDataService()
            svc._save_last_good_quote(
                {
                    "symbol": "SPY.US",
                    "last": 500.0,
                    "prev_close": 495.0,
                    "change_pct": 1.01,
                    "source": "eastmoney",
                    "source_label": "EastMoney public",
                }
            )
            item = svc._us_cache_quote("SPY.US")

        self.assertIsNotNone(item)
        self.assertEqual(item["symbol"], "SPY.US")
        self.assertEqual(item["last"], 500.0)
        self.assertEqual(item["source"], "us_local_cache")
        self.assertTrue(item["cache"])
        self.assertTrue(item["stale"])

    def test_last_good_cache_roundtrip_for_hk_quote(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(svc_mod, "LAST_GOOD_CACHE_DIR", td):
            svc = PublicMarketDataService()
            svc._save_last_good_quote(
                {
                    "symbol": "HSI.HK",
                    "last": 25000.0,
                    "prev_close": 24900.0,
                    "change_pct": 0.4,
                    "source": "eastmoney",
                    "source_label": "EastMoney public",
                }
            )
            item = svc._hk_cache_quote("HSI.HK")

        self.assertIsNotNone(item)
        self.assertEqual(item["symbol"], "HSI.HK")
        self.assertEqual(item["last"], 25000.0)
        self.assertEqual(item["source"], "hk_local_cache")
        self.assertTrue(item["cache"])
        self.assertTrue(item["stale"])

    def test_polygon_quote_parses_trade_and_previous_close(self) -> None:
        class FakeResp:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            if "/last/trade/" in url:
                return FakeResp({"results": {"p": 501.25, "t": 1715600000000000000}})
            return FakeResp({"results": [{"c": 500.0}]})

        with patch.dict("os.environ", {"POLYGON_API_KEY": "test-key"}, clear=False), patch.object(svc_mod.requests, "get", fake_get):
            item = PublicMarketDataService()._polygon_quote("SPY.US")

        self.assertIsNotNone(item)
        self.assertEqual(item["source"], "polygon")
        self.assertEqual(item["last"], 501.25)
        self.assertEqual(item["prev_close"], 500.0)
        self.assertEqual(item["change_pct"], 0.25)
        self.assertEqual(len(calls), 2)

    def test_tencent_hk_quote_parses_index_payload(self) -> None:
        class FakeResp:
            content = (
                'v_hkHSI="100~恒生指数~HSI~25550.720~25962.730~25838.960~15019892.8028~0~0~25550.720'
                '~0~0~0~0~0~0~0~0~0~25550.720~0~0~0~0~0~0~0~0~0~0.0~2026/05/18 11:31:33'
                '~-412.010~-1.59~25838.960~25505.710~25550.720~15019892.8028~15019892.803";'
            ).encode("gbk")

            def raise_for_status(self):
                return None

        def fake_get(url, **kwargs):
            return FakeResp()

        with patch.object(svc_mod.requests, "get", fake_get):
            item = PublicMarketDataService()._tencent_hk_quote("HSI.HK")

        self.assertIsNotNone(item)
        self.assertEqual(item["source"], "tencent_hk")
        self.assertEqual(item["last"], 25550.72)
        self.assertEqual(item["prev_close"], 25962.73)
        self.assertEqual(item["change_pct"], -1.59)


if __name__ == "__main__":
    unittest.main()
