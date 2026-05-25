import os
import sys
import unittest
from unittest.mock import patch
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

from api import runtime_bridge as rt
from api.services.backtest_task_service import normalize_backtest_result, run_sync_backtest_task


class TestUnifiedBacktests(unittest.TestCase):
    def test_normalize_backtest_result_schema(self):
        out = normalize_backtest_result(
            task_id="bt_test",
            kind="options_combo",
            source_module="options",
            request={"symbol": "AAPL.US"},
            raw_result={
                "stats": {"total_trades": 2, "win_rate_pct": 50.0, "total_net_pnl": 12.3},
                "trades": [{"id": 1}],
            },
        )

        self.assertEqual("unified_backtest_result.v1", out["schema"])
        self.assertEqual("bt_test", out["task_id"])
        self.assertEqual("options", out["source_module"])
        self.assertEqual(2, out["metrics"]["total_trades"])
        self.assertEqual([{"id": 1}], out["trades"])

    def test_run_sync_backtest_task_stores_completed_task(self):
        with tempfile.TemporaryDirectory() as td:
            import api.services.backtest_task_service as svc

            with patch.object(svc, "_STORE_PATH", os.path.join(td, "unified_backtests.json")):
                svc._TASKS.clear()
                created = run_sync_backtest_task(
                    kind="options_combo",
                    source_module="options",
                    request={"symbol": "AAPL.US"},
                    runner=lambda _req: {"stats": {"total_trades": 1}},
                )

                task_id = created["result"]["task_id"]
                fetched = rt.backtests_get(task_id)
                events = rt.backtests_events(task_id)
                listed = rt.backtests_list()

                self.assertEqual("completed", fetched["status"])
                self.assertEqual("completed", fetched["result"]["status"])
                self.assertTrue(events["events"])
                self.assertEqual(task_id, listed["items"][0]["task_id"])

    @patch("api.runtime_bridge.options_backtest", return_value={"stats": {"total_trades": 3}})
    def test_create_options_combo_backtest(self, mock_runner):
        out = rt.backtests_create({"kind": "options_combo", "request": {"symbol": "AAPL.US"}})

        self.assertTrue(out["ok"])
        self.assertEqual("options_combo", out["result"]["kind"])
        mock_runner.assert_called_once_with({"symbol": "AAPL.US"})

    @patch("api.runtime_bridge.qqq_0dte_backtest", return_value={"stats": {"closed_trades": 4}, "realized_pnl": 10})
    def test_create_qqq_strategy_backtest(self, mock_runner):
        out = rt.backtests_create({"kind": "qqq_0dte_strategy", "request": {"symbol": "QQQ.US"}})

        self.assertTrue(out["ok"])
        self.assertEqual("auto-trading/options-0dte", out["result"]["source_module"])
        self.assertEqual(4, out["result"]["metrics"]["closed_trades"])
        mock_runner.assert_called_once_with({"symbol": "QQQ.US"})


if __name__ == "__main__":
    unittest.main()
