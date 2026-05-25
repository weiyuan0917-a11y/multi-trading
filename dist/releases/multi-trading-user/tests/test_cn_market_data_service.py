import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

from api import runtime_bridge as rt
from api.services import cn_market_data_service as svc
from api.services.cn_market_data_service import CnMarketDataService, normalize_cn_symbol
from api.services.setup_service import build_setup_config_response


class TestCnMarketDataService(unittest.TestCase):
    def test_normalize_cn_symbol(self):
        self.assertEqual("600519.SH", normalize_cn_symbol("600519"))
        self.assertEqual("300750.SZ", normalize_cn_symbol("300750"))
        self.assertEqual("600519.SH", normalize_cn_symbol("sh.600519"))
        self.assertEqual("000001.SZ", normalize_cn_symbol("1.sz"))

    def test_local_cache_klines_and_quote(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "600519_SH__1d__p2.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "meta": {"symbol": "600519.SH"},
                        "items": [
                            {"date": "2026-01-01T00:00:00", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
                            {"date": "2026-01-02T00:00:00", "open": 10, "high": 12, "low": 10, "close": 11, "volume": 200},
                        ],
                    },
                    f,
                )
            with patch.object(svc, "KLINE_CACHE_DIR", td):
                out = CnMarketDataService().klines("600519.SH", source="local_cache", limit=2)
                quote = CnMarketDataService().quote("600519.SH", source="local_cache")

        self.assertTrue(out["ok"])
        self.assertEqual("local_cache", out["source"])
        self.assertEqual(2, out["bar_count"])
        self.assertEqual(11.0, quote["items"][0]["last"])
        self.assertEqual(10.0, quote["items"][0]["change_pct"])

    def test_runtime_bridge_provider_status(self):
        body = rt.cn_market_data_provider_status()
        self.assertTrue(body["ok"])
        ids = {x["id"] for x in body["providers"]}
        self.assertIn("tencent_index", ids)
        self.assertIn("local_cache", ids)
        self.assertIn("akshare", ids)
        self.assertIn("tushare", ids)
        self.assertIn("baostock", ids)

    def test_tencent_index_quote_parses_shanghai_index(self):
        class FakeResp:
            content = (
                'v_sh000001="1~上证指数~000001~4126.35~4135.39~4120.14~409750565~0~0~0.00'
                '~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00'
                '~0~~20260518120000~-9.04~-0.22~4145.66~4110.83~4126.35/409750565/874851287854'
                '~409750565~87485129";'
            ).encode("gbk")

            def raise_for_status(self):
                return None

        def fake_get(url, **kwargs):
            return FakeResp()

        with patch.object(svc.requests, "get", fake_get):
            item = CnMarketDataService()._tencent_index_quote("000001.SH")

        self.assertIsNotNone(item)
        self.assertEqual("tencent_index", item["source"])
        self.assertEqual(4126.35, item["last"])
        self.assertEqual(-0.22, item["change_pct"])

    def test_universe_reads_cache_symbols(self):
        with tempfile.TemporaryDirectory() as td:
            for name in ("600519_SH__1d__p2.json", "300750_SZ__1d__p2.json"):
                with open(os.path.join(td, name), "w", encoding="utf-8") as f:
                    json.dump({"items": []}, f)
            with patch.object(svc, "KLINE_CACHE_DIR", td):
                body = CnMarketDataService().universe()

        symbols = {x["symbol"] for x in body["items"]}
        self.assertIn("600519.SH", symbols)
        self.assertIn("300750.SZ", symbols)

    def test_setup_config_exposes_cn_market_data_fields(self):
        body = build_setup_config_response(
            env_data={
                "CN_MARKET_DATA_PROVIDER_ORDER": "akshare,local_cache",
                "CN_MARKET_AKSHARE_ENABLED": "true",
                "CN_MARKET_TUSHARE_ENABLED": "false",
                "CN_MARKET_BAOSTOCK_ENABLED": "true",
                "TUSHARE_TOKEN": "secret-token",
            },
            feishu_cfg={},
            mask_secret=lambda v: "***" if v else "",
        )

        self.assertTrue(body["configured"]["cn_market_data"])
        values = body["values"]
        self.assertEqual("akshare,local_cache", values["cn_market_data_provider_order"])
        self.assertEqual("true", values["cn_market_akshare_enabled"])
        self.assertEqual("false", values["cn_market_tushare_enabled"])
        self.assertEqual("***", values["tushare_token"])

    def test_install_cn_provider_rejects_unknown_package(self):
        with self.assertRaises(Exception):
            rt.setup_install_cn_market_data_provider({"provider": "not-allowed"})


if __name__ == "__main__":
    unittest.main()
