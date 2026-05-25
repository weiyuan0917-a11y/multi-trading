import os
import sys
import tempfile
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


class TestAutoTraderRestartRecovery(unittest.TestCase):
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
            patch.object(
                auto_trader_mod,
                "AUTO_TRADER_OPEN_STATE_FILE",
                os.path.join(self._td.name, "open_state.json"),
            ),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._td.cleanup()

    def _make_service(self, positions_rows, execute_trade):
        cfg_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        cfg_file.close()
        os.unlink(cfg_file.name)
        return AutoTraderService(
            fetch_bars=lambda *_args, **_kwargs: [],
            quote_last=lambda *_args, **_kwargs: {"last": 10.0},
            send_feishu=lambda *_args, **_kwargs: True,
            execute_trade=execute_trade,
            get_positions=lambda: {"positions": list(positions_rows)},
            get_account=lambda: {"net_assets": 100000.0, "buy_power": 100000.0},
            config_path=cfg_file.name,
        )

    def test_auto_execute_persists_signal_and_open_state(self):
        positions = [{"symbol": "AAPL.US", "quantity": 1, "current_price": 10.5, "cost_price": 10.0}]

        def execute_trade(*_args, **_kwargs):
            return {"success": True, "order_id": "OID-1"}

        service = self._make_service(positions, execute_trade)
        signal = service._create_and_execute_signal(
            "buy",
            "AAPL.US",
            {"strategy": "unit", "strategy_label": "unit", "composite_score": 1.0},
            1,
        )

        self.assertEqual("executed", signal["status"])
        rows = service.list_signals(status="executed")
        self.assertTrue(any(str(r.get("symbol")) == "AAPL.US" for r in rows))
        status = service.get_status()
        restored = status.get("restored_open_positions") or []
        self.assertEqual("AAPL.US", restored[0]["symbol"])
        self.assertEqual(1, restored[0]["quantity"])
        self.assertTrue(os.path.exists(os.path.join(self._td.name, "open_state.json")))

    def test_restore_open_positions_on_boot_only_accepts_worker_managed_positions(self):
        positions = [
            {"symbol": "AAPL.US", "quantity": 2, "current_price": 11.0, "cost_price": 10.0},
            {"symbol": "MSFT.US", "quantity": 3, "current_price": 21.0, "cost_price": 20.0},
        ]

        def execute_trade(*_args, **_kwargs):
            return {"success": True, "order_id": "OID-1"}

        service1 = self._make_service(positions, execute_trade)
        service1._create_and_execute_signal(
            "buy",
            "AAPL.US",
            {"strategy": "unit", "strategy_label": "unit", "composite_score": 1.0},
            2,
        )
        status1 = service1.get_status()
        restored1 = status1.get("restored_open_positions") or []
        self.assertEqual(["AAPL.US"], [r["symbol"] for r in restored1])

        service2 = self._make_service(positions, execute_trade)
        status2 = service2.get_status()
        restored2 = status2.get("restored_open_positions") or []
        meta2 = status2.get("restored_open_positions_meta") or {}
        self.assertEqual(["AAPL.US"], [r["symbol"] for r in restored2])
        self.assertEqual("snapshot", meta2.get("source"))


if __name__ == "__main__":
    unittest.main()
