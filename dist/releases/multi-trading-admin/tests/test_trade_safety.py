import os
import tempfile
import unittest
from unittest.mock import patch

import api.services.trade_safety as trade_safety
from api.services.trade_safety import TradeSafetyBlocked


class TestTradeSafety(unittest.TestCase):
    def setUp(self):
        trade_safety._RECENT_ORDER_KEYS.clear()
        self._old_env = dict(os.environ)
        self._tmp = tempfile.NamedTemporaryFile(delete=False)
        self._tmp.close()
        os.environ["TRADE_AUDIT_LOG_FILE"] = self._tmp.name

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)
        trade_safety._RECENT_ORDER_KEYS.clear()
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def test_kill_switch_blocks_submit(self):
        os.environ["TRADE_KILL_SWITCH"] = "1"
        with self.assertRaises(TradeSafetyBlocked) as ctx:
            trade_safety.guard_before_submit_order(
                symbol="AAPL.US",
                side="buy",
                submitted_quantity=1,
                order_type="MO",
                time_in_force="Day",
            )
        self.assertEqual("live_trading_kill_switch_enabled", ctx.exception.reason)

    def test_dry_run_returns_order_like_response(self):
        os.environ["TRADE_DRY_RUN"] = "1"
        _key, resp = trade_safety.guard_before_submit_order(
            symbol="AAPL.US",
            side="buy",
            submitted_quantity=1,
            order_type="MO",
            time_in_force="Day",
        )
        self.assertIsNotNone(resp)
        self.assertTrue(resp.dry_run)
        self.assertTrue(resp.order_id.startswith("DRYRUN-"))

    @patch.dict(os.environ, {"TRADE_IDEMPOTENCY_WINDOW_SECONDS": "30"}, clear=False)
    def test_duplicate_window_blocks_same_order(self):
        kwargs = {
            "symbol": "AAPL.US",
            "side": "buy",
            "submitted_quantity": 1,
            "order_type": "MO",
            "time_in_force": "Day",
        }
        trade_safety.guard_before_submit_order(**kwargs)
        with self.assertRaises(TradeSafetyBlocked) as ctx:
            trade_safety.guard_before_submit_order(**kwargs)
        self.assertEqual("duplicate_order_window", ctx.exception.reason)


if __name__ == "__main__":
    unittest.main()
