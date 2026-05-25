import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, tzinfo
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)


class _TestZoneInfo(tzinfo):
    def __init__(self, key: str):
        self.key = key

    def utcoffset(self, _dt):
        return timedelta(0)

    def dst(self, _dt):
        return timedelta(0)

    def tzname(self, _dt):
        return self.key


import zoneinfo

zoneinfo.ZoneInfo = _TestZoneInfo

from api.auto_trader import AutoTraderService
from api.schemas_auto_trader import AutoTraderConfirmBody


def _make_service(execute_trade):
    cfg_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    cfg_file.close()
    os.unlink(cfg_file.name)
    service = AutoTraderService(
        fetch_bars=lambda *_args, **_kwargs: [],
        quote_last=lambda *_args, **_kwargs: {"last": 10.0},
        send_feishu=lambda *_args, **_kwargs: True,
        execute_trade=execute_trade,
        get_positions=lambda: {"positions": []},
        get_account=lambda: {"net_assets": 100000.0, "buy_power": 100000.0},
        config_path=cfg_file.name,
    )
    return service


class TestAutoTraderL3Confirmation(unittest.TestCase):
    def setUp(self):
        import api.auto_trader as auto_trader_mod

        self._td = tempfile.TemporaryDirectory()
        self._patches = [
            patch.object(
                auto_trader_mod,
                "AUTO_TRADER_SIGNALS_PERSIST_FILE",
                os.path.join(self._td.name, "signals.json"),
            ),
            patch.object(
                auto_trader_mod,
                "AUTO_TRADER_SCAN_COUNTER_FILE",
                os.path.join(self._td.name, "scan_counter.json"),
            ),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._td.cleanup()

    def test_confirm_body_accepts_confirmation_token(self):
        body = AutoTraderConfirmBody.model_validate({"confirmation_token": "token-123"})
        self.assertEqual("token-123", body.confirmation_token)

    def test_manual_confirm_passes_token_to_execute_trade(self):
        calls = []

        def execute_trade(*args, **kwargs):
            calls.append((args, kwargs))
            return {"success": True, "order_id": "OID-1"}

        service = _make_service(execute_trade)
        signal_id = "AT-TEST-L3"
        service._signals[signal_id] = {
            "signal_id": signal_id,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(minutes=5)).isoformat(),
            "symbol": "AAPL.US",
            "action": "buy",
            "quantity": 1,
            "strategy": "unit",
            "strategy_score": 1.0,
        }

        result = service.confirm_and_execute(signal_id, confirmation_token="token-123")

        self.assertTrue(result.get("success"))
        self.assertEqual("token-123", calls[0][1].get("confirmation_token"))

    def test_worker_confirm_queue_supports_tokens_and_legacy_ids(self):
        import importlib

        longbridge_mod = types.ModuleType("longbridge")
        openapi_mod = types.ModuleType("longbridge.openapi")
        dummy = type("_LongbridgeDummy", (), {})
        for name in [
            "AdjustType",
            "Config",
            "OrderSide",
            "OrderType",
            "Period",
            "QuoteContext",
            "TimeInForceType",
            "TradeContext",
            "TradeSessions",
        ]:
            setattr(openapi_mod, name, dummy)
        sys.modules.pop("api.auto_trader_worker", None)
        with patch.dict(sys.modules, {"longbridge": longbridge_mod, "longbridge.openapi": openapi_mod}):
            worker = importlib.import_module("api.auto_trader_worker")

        with tempfile.TemporaryDirectory() as td:
            queue_file = os.path.join(td, "confirm.json")
            with open(queue_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "confirmations": [
                            {"signal_id": "AT-1", "confirmation_token": "tok-1"},
                            "AT-LEGACY",
                        ]
                    },
                    f,
                )

            with patch.object(worker, "CONFIRM_QUEUE_FILE", queue_file):
                rows = worker._consume_confirm_queue()

        self.assertEqual(
            [
                {"signal_id": "AT-1", "confirmation_token": "tok-1"},
                {"signal_id": "AT-LEGACY", "confirmation_token": ""},
            ],
            rows,
        )


if __name__ == "__main__":
    unittest.main()
