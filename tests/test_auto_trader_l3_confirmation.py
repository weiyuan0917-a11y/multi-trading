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

        with patch("api.auto_trader._auto_trader_owner_id", lambda: "davies"), patch(
            "api.auto_trader._auto_trader_account_id", lambda: "aisura"
        ), patch("api.auto_trader._auto_trader_broker_provider", lambda: "longbridge"):
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

        self.assertEqual("AT-1", rows[0].get("signal_id"))
        self.assertEqual("tok-1", rows[0].get("confirmation_token"))
        self.assertEqual("AT-LEGACY", rows[1].get("signal_id"))
        self.assertEqual("", rows[1].get("confirmation_token"))

    def test_worker_trade_proxy_key_falls_back_to_matching_legacy_owner_key(self):
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
        with patch.dict(
            os.environ,
            {
                "AUTO_TRADER_OWNER_ID": "davies",
                "X_MT_LOCAL_OWNER": "davies",
                "AUTO_TRADER_CONFIG_PATH": "",
                "AUTO_TRADER_API_KEY": "",
                "AUTO_TRADER_API_BEARER_TOKEN": "",
            },
            clear=False,
        ), patch.dict(sys.modules, {"longbridge": longbridge_mod, "longbridge.openapi": openapi_mod}):
            worker = importlib.import_module("api.auto_trader_worker")

        with tempfile.TemporaryDirectory() as td:
            owner_cfg = os.path.join(td, "data", "auto_trader", "davies", "auto_trader_config.json")
            legacy_cfg = os.path.join(td, "api", "auto_trader_config.json")
            os.makedirs(os.path.dirname(owner_cfg), exist_ok=True)
            os.makedirs(os.path.dirname(legacy_cfg), exist_ok=True)
            with open(owner_cfg, "w", encoding="utf-8") as f:
                json.dump({"enabled": True}, f)
            with open(legacy_cfg, "w", encoding="utf-8") as f:
                json.dump({"api_key": "legacy-key"}, f)

            with patch.object(worker, "ROOT", td), patch.object(
                worker,
                "auto_trader_config_path_for_owner",
                lambda owner, root=None: os.path.join(root or td, "data", "auto_trader", owner, "auto_trader_config.json"),
            ), patch.object(worker, "_API_LOCAL_OWNER", "davies"), patch.object(
                worker, "_api_key_owner_matches", lambda key, owner: key == "legacy-key" and owner == "davies"
            ):
                self.assertEqual(("legacy-key", ""), worker._trade_proxy_from_auto_trader_config_file())
                with open(owner_cfg, "r", encoding="utf-8") as f:
                    migrated = json.load(f)
                self.assertEqual("legacy-key", migrated.get("api_key"))

    def test_worker_trade_proxy_key_does_not_fallback_to_other_owner_key(self):
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
        with patch.dict(
            os.environ,
            {
                "AUTO_TRADER_OWNER_ID": "davies",
                "X_MT_LOCAL_OWNER": "davies",
                "AUTO_TRADER_CONFIG_PATH": "",
                "AUTO_TRADER_API_KEY": "",
                "AUTO_TRADER_API_BEARER_TOKEN": "",
            },
            clear=False,
        ), patch.dict(sys.modules, {"longbridge": longbridge_mod, "longbridge.openapi": openapi_mod}):
            worker = importlib.import_module("api.auto_trader_worker")

        with tempfile.TemporaryDirectory() as td:
            owner_cfg = os.path.join(td, "data", "auto_trader", "davies", "auto_trader_config.json")
            legacy_cfg = os.path.join(td, "api", "auto_trader_config.json")
            os.makedirs(os.path.dirname(owner_cfg), exist_ok=True)
            os.makedirs(os.path.dirname(legacy_cfg), exist_ok=True)
            with open(owner_cfg, "w", encoding="utf-8") as f:
                json.dump({"enabled": True}, f)
            with open(legacy_cfg, "w", encoding="utf-8") as f:
                json.dump({"api_key": "other-owner-key"}, f)

            with patch.object(worker, "ROOT", td), patch.object(
                worker,
                "auto_trader_config_path_for_owner",
                lambda owner, root=None: os.path.join(root or td, "data", "auto_trader", owner, "auto_trader_config.json"),
            ), patch.object(worker, "_API_LOCAL_OWNER", "davies"), patch.object(worker, "_api_key_owner_matches", lambda _key, _owner: False):
                self.assertEqual(("", ""), worker._trade_proxy_from_auto_trader_config_file())
                with open(owner_cfg, "r", encoding="utf-8") as f:
                    migrated = json.load(f)
                self.assertNotIn("api_key", migrated)

    def test_worker_trade_proxy_env_key_must_match_owner(self):
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
        with patch.dict(
            os.environ,
            {
                "AUTO_TRADER_OWNER_ID": "davies",
                "X_MT_LOCAL_OWNER": "davies",
                "AUTO_TRADER_CONFIG_PATH": "",
                "AUTO_TRADER_API_KEY": "other-owner-key",
                "AUTO_TRADER_API_BEARER_TOKEN": "",
            },
            clear=False,
        ), patch.dict(sys.modules, {"longbridge": longbridge_mod, "longbridge.openapi": openapi_mod}):
            worker = importlib.import_module("api.auto_trader_worker")

        with patch.object(worker, "_API_LOCAL_OWNER", "davies"), patch.object(
            worker, "_api_key_owner_matches", lambda _key, _owner: False
        ), patch.object(worker, "_trade_proxy_from_auto_trader_config_file", lambda: ("", "")):
            self.assertEqual(("", ""), worker._api_trade_proxy_credentials())


if __name__ == "__main__":
    unittest.main()
