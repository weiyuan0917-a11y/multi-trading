import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcp_server.strategy_qqq_0dte.snapshot_store import (  # noqa: E402
    append_backtest_snapshot,
    top_snapshots,
)


class TestQqq0dteSnapshots(unittest.TestCase):
    def test_top_by_pnl_and_return(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            append_backtest_snapshot(
                request_meta={"symbol": "QQQ.US", "kline": "1m"},
                strategy_config={"max_trades_per_day": 1},
                metrics={"realized_pnl": 10.0, "return_pct": None},
                store_path=path,
            )
            append_backtest_snapshot(
                request_meta={"symbol": "QQQ.US", "kline": "1m"},
                strategy_config={"max_trades_per_day": 2},
                metrics={"realized_pnl": 99.0, "return_pct": 1.5},
                store_path=path,
            )
            append_backtest_snapshot(
                request_meta={"symbol": "QQQ.US", "kline": "1m"},
                strategy_config={"max_trades_per_day": 3},
                metrics={"realized_pnl": 50.0, "return_pct": 5.0},
                store_path=path,
            )
            top_pnl = top_snapshots(top_n=2, sort="realized_pnl", store_path=path)
            self.assertEqual(len(top_pnl["runs"]), 2)
            self.assertEqual(float(top_pnl["runs"][0]["metrics"]["realized_pnl"]), 99.0)

            top_ret = top_snapshots(top_n=2, sort="return_pct", store_path=path)
            self.assertEqual(float(top_ret["runs"][0]["metrics"]["return_pct"]), 5.0)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
