import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP_SERVER_DIR = os.path.join(_ROOT, "mcp_server")
if _MCP_SERVER_DIR not in sys.path:
    sys.path.insert(0, _MCP_SERVER_DIR)

from api import runtime_bridge as rt


class _FakeMain:
    def __init__(self):
        self.HTTPException = HTTPException
        self._RUNTIME_STATE = SimpleNamespace()
        self.ACCOUNT_REGISTRY = SimpleNamespace(mark_broker_connect_error=self._mark_broker_connect_error)
        self.mark_calls = []
        self.reset_calls = []
        self.throttle_calls = 0
        self.ensure_calls = 0

    def _mark_broker_connect_error(self, err, account_id=None, owner_id=None):
        self.mark_calls.append((str(err), account_id, owner_id))

    def _gateway_get_json(self, _path, _params=None):
        return None

    def _is_longport_connect_error(self, err):
        return "client error (connect)" in str(err).lower()

    def throttled_reset_contexts(self, reset_fn, _state):
        self.throttle_calls += 1
        reset_fn()
        return True

    def reset_contexts(self, account_id=None, owner_id=None):
        self.reset_calls.append((account_id, owner_id))

    def ensure_contexts(self, account_id=None, owner_id=None):
        self.ensure_calls += 1
        return None, object()

    def broker_get_account_balance(self, _tctx):
        raise RuntimeError("OpenApiException: client error (connect)")


class TestTradeRuntimeBridge(unittest.TestCase):
    def test_trade_account_raises_structured_broker_connect_error(self):
        fake_main = _FakeMain()
        with patch("api.runtime_bridge._m", return_value=fake_main):
            with self.assertRaises(Exception) as ctx:
                rt.trade_account(account_id="default", owner_id="alice")

        exc = ctx.exception
        self.assertEqual(503, getattr(exc, "status_code", None))
        detail = getattr(exc, "detail", None)
        self.assertIsInstance(detail, dict)
        self.assertEqual("broker_connect_error", detail.get("error"))
        self.assertEqual(2, fake_main.ensure_calls)
        self.assertEqual(2, len(fake_main.mark_calls))
        self.assertEqual(1, fake_main.throttle_calls)
        self.assertEqual([("default", "alice")], fake_main.reset_calls)


if __name__ == "__main__":
    unittest.main()
