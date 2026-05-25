import unittest
import os
import sys
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

from api import runtime_bridge as rt


class TestAutoTradingUnifiedApi(unittest.TestCase):
    def test_modules_lists_auto_trading_modules(self):
        body = rt.auto_trading_modules()
        ids = {x["id"] for x in body["items"]}
        self.assertEqual({"stocks", "options-0dte", "options-1dte", "options-swing"}, ids)

    @patch("api.runtime_bridge.auto_trader_status")
    @patch("api.runtime_bridge._auto_trading_option_config", return_value={"dry_run": True})
    @patch("api.runtime_bridge.setup_services_status")
    def test_status_normalizes_runtime_for_all_modules(self, mock_services, _mock_option_cfg, mock_auto_status):
        mock_services.return_value = {
            "auto_trader_scheduler_running": True,
            "auto_trader_supervisor_running": True,
            "auto_trader_worker_pid": 111,
            "auto_trader_runtime": {"worker_running": True, "updated_at": "t1"},
            "qqq_0dte_live_running": False,
            "qqq_0dte_live_pid": None,
            "qqq_0dte_live_runtime": {"worker_running": False},
            "qqq_1dte_live_running": True,
            "qqq_1dte_live_pid": 333,
            "qqq_1dte_live_runtime": {"runtime": {"updated_at": "t3"}},
        }
        mock_auto_status.return_value = {
            "config": {"dry_run_mode": True},
            "daily_trade_count": 2,
            "pending_signals": 1,
            "executed_signals": 1,
        }

        body = rt.auto_trading_status()

        self.assertTrue(body["any_running"])
        by_id = {x["id"]: x for x in body["modules"]}
        self.assertTrue(by_id["stocks"]["running"])
        self.assertEqual(111, by_id["stocks"]["pid"])
        self.assertFalse(by_id["options-0dte"]["running"])
        self.assertTrue(by_id["options-1dte"]["running"])
        self.assertEqual(333, by_id["options-1dte"]["pid"])

    @patch("api.runtime_bridge.auto_trader_status")
    def test_stock_risk_summary_uses_auto_trader_config_and_status(self, mock_auto_status):
        mock_auto_status.return_value = {
            "config": {
                "auto_execute": False,
                "dry_run_mode": True,
                "max_daily_trades": 5,
                "max_position_value": 10000,
                "max_total_exposure": 0.3,
            },
            "daily_trade_count": 2,
            "daily_loss_pct": 0.01,
            "daily_loss_circuit_triggered": False,
        }

        body = rt.auto_trading_module_risk_summary("stocks")

        risk = body["risk"]
        self.assertTrue(risk["dry_run_mode"])
        self.assertEqual(5, risk["max_daily_trades"])
        self.assertEqual(2, risk["daily_trade_count"])

    @patch("api.runtime_bridge.auto_trader_metrics_recent")
    def test_stock_events_proxy_metrics(self, mock_metrics):
        mock_metrics.return_value = {"items": [{"event": "one"}, {"event": "two"}]}

        body = rt.auto_trading_module_events("stocks", limit=2)

        self.assertEqual([{"event": "one"}, {"event": "two"}], body["items"])
        mock_metrics.assert_called_once_with(limit=2, event=None)

    @patch("api.runtime_bridge.auto_trader_confirm")
    def test_stock_confirm_forwards_signal_and_l3_token(self, mock_confirm):
        mock_confirm.return_value = {"success": True}

        body = rt.auto_trading_module_confirm("stocks", {"signal_id": "AT-1", "confirmation_token": "tok"})

        self.assertTrue(body["ok"])
        mock_confirm.assert_called_once_with("AT-1", {"confirmation_token": "tok"}, owner_id=None)

    @patch("api.runtime_bridge._m")
    @patch("api.runtime_bridge._auto_trading_option_config", return_value={"confirmation_token": "configured"})
    def test_option_confirm_uses_unified_l3_check(self, _mock_cfg, mock_m):
        class _FakeMain:
            @staticmethod
            def _ensure_l3_confirmation(token):
                if token != "ok":
                    raise AssertionError("unexpected token")

        mock_m.return_value = _FakeMain

        body = rt.auto_trading_module_confirm("options-0dte", {"confirmation_token": "ok"})

        self.assertTrue(body["confirmed"])


if __name__ == "__main__":
    unittest.main()
