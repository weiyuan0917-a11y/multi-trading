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


class _FakeAccountRegistry:
    def __init__(self):
        self.default_account_id = "old-account"
        self.accounts = {"old-account": True, "new-account": True}

    def get_default_account_id(self, owner_id=None):
        return self.default_account_id

    def connect_account(self, account_id, owner_id=None):
        self.default_account_id = account_id
        rec = SimpleNamespace(
            account_id=account_id,
            broker_provider="longbridge",
            is_default=True,
            quote_ctx=None,
            trade_ctx=None,
            status="ready",
            last_error=None,
            last_init_at="2026-05-24T00:00:00",
            manual_disconnected=False,
        )
        return object(), object(), rec

    def disconnect_account(self, account_id, owner_id=None):
        return SimpleNamespace(
            account_id=account_id,
            quote_ctx=None,
            trade_ctx=None,
            status="disconnected",
            last_error=None,
            manual_disconnected=True,
        )

    def has_connected_account(self, owner_id=None):
        return False

    def get_account_record(self, account_id=None, owner_id=None):
        aid = account_id or self.default_account_id
        if aid not in self.accounts:
            raise ValueError(f"account_not_found: {aid}")
        return SimpleNamespace(
            account_id=aid,
            broker_provider="longbridge",
            quote_ctx=object(),
            trade_ctx=object(),
            status="ready",
            manual_disconnected=False,
        )

    def delete_account(self, account_id, owner_id=None):
        if account_id not in self.accounts:
            raise ValueError(f"account_not_found: {account_id}")
        del self.accounts[account_id]
        if self.default_account_id == account_id:
            self.default_account_id = next(iter(self.accounts), "")
        return SimpleNamespace(
            account_id=account_id,
            broker_provider="longbridge",
            quote_ctx=None,
            trade_ctx=None,
            status="deleted",
            manual_disconnected=True,
        )

    def list_accounts(self, owner_id=None):
        return [
            {
                "account_id": aid,
                "broker_provider": "longbridge",
                "is_default": aid == self.default_account_id,
                "status": "registered",
            }
            for aid in self.accounts
        ]


class _FakeMainForAccountSwitch:
    def __init__(self):
        self.HTTPException = HTTPException
        self.ACCOUNT_REGISTRY = _FakeAccountRegistry()
        self.stop_calls = []
        self._managed_processes = {}
        self.ROOT = _ROOT
        self.MCP_DIR = _MCP_SERVER_DIR

    def _auto_trader_service_for_owner(self, owner_id=None):
        return SimpleNamespace()

    def _start_auto_trader_worker(self, owner_id=None):
        return "started"

    def _start_qqq_0dte_live_worker(self, owner_id=None):
        return "started"

    def _start_qqq_1dte_live_worker(self, owner_id=None):
        return "started"

    def _start_stock_options_swing_worker(self, owner_id=None):
        return "started"

    def _win_subprocess_silent_kwargs(self):
        return {}

    def _auto_trader_runtime_status(self):
        return {"worker_running": False, "supervisor_running": False}

    def _stop_auto_trader_worker(self, timeout_seconds=5.0):
        self.stop_calls.append("auto_trader")
        return "stopped"

    def _stop_qqq_0dte_live_worker(self, timeout_seconds=5.0):
        self.stop_calls.append("qqq_0dte")
        return "stopped"

    def _stop_qqq_1dte_live_worker(self, timeout_seconds=5.0):
        self.stop_calls.append("qqq_1dte")
        return "stopped"

    def _stop_stock_options_swing_worker(self, timeout_seconds=5.0):
        self.stop_calls.append("stock_options_swing")
        return "stopped"


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

    def test_account_switch_stops_all_trading_workers_and_syncs_context(self):
        fake_main = _FakeMainForAccountSwitch()
        with (
            patch("api.runtime_bridge._m", return_value=fake_main),
            patch(
                "api.runtime_bridge._sync_trading_worker_configs_to_account",
                return_value=[{"module": "stock_options_swing", "status": "synced"}],
            ) as sync_configs,
        ):
            out = rt.setup_account_connect(account_id="new-account", owner_id="davies")

        self.assertTrue(out["account_switched"])
        self.assertEqual("old-account", out["previous_default_account_id"])
        self.assertEqual("new-account", out["default_account_id"])
        self.assertEqual(
            {"auto_trader", "qqq_0dte", "qqq_1dte", "stock_options_swing"},
            set(fake_main.stop_calls),
        )
        sync_configs.assert_called_once_with(owner_id="davies", account_id="new-account")
        self.assertEqual([{"module": "stock_options_swing", "status": "synced"}], out["synced_worker_configs"])

    def test_disconnect_last_account_stops_stock_options_swing_too(self):
        fake_main = _FakeMainForAccountSwitch()
        with patch("api.runtime_bridge._m", return_value=fake_main):
            out = rt.setup_account_disconnect(account_id="new-account", owner_id="davies")

        self.assertTrue(out["all_accounts_disconnected"])
        self.assertIn("stock_options_swing", set(fake_main.stop_calls))

    def test_delete_connected_account_removes_record_and_stops_workers_when_last_connected(self):
        fake_main = _FakeMainForAccountSwitch()
        fake_main.ACCOUNT_REGISTRY.accounts = {"new-account": True}
        fake_main.ACCOUNT_REGISTRY.default_account_id = "new-account"
        with patch("api.runtime_bridge._m", return_value=fake_main):
            out = rt.setup_account_delete(account_id="new-account", owner_id="davies")

        self.assertTrue(out["deleted"])
        self.assertTrue(out["was_connected"])
        self.assertEqual([], out["remaining_accounts"])
        self.assertIsNone(out["default_account_id"])
        self.assertIn("stock_options_swing", set(fake_main.stop_calls))

    def test_setup_accounts_does_not_recreate_default_when_empty(self):
        fake_main = _FakeMainForAccountSwitch()
        fake_main.ACCOUNT_REGISTRY.accounts = {}
        fake_main.ACCOUNT_REGISTRY.default_account_id = ""
        with patch("api.runtime_bridge._m", return_value=fake_main):
            out = rt.setup_accounts(owner_id="davies")

        self.assertTrue(out["ok"])
        self.assertEqual([], out["accounts"])
        self.assertIsNone(out["default_account_id"])

    def test_start_worker_syncs_config_to_current_default_account_first(self):
        fake_main = _FakeMainForAccountSwitch()
        fake_main.ACCOUNT_REGISTRY.default_account_id = "new-account"
        fake_main.ACCOUNT_REGISTRY.get_account_record = lambda account_id=None, owner_id=None: SimpleNamespace(
            account_id=account_id or "new-account",
            broker_provider="longbridge",
            status="ready",
            manual_disconnected=False,
        )
        with (
            patch("api.runtime_bridge._m", return_value=fake_main),
            patch("api.runtime_bridge._sync_trading_worker_configs_to_account") as sync_configs,
            patch("api.runtime_bridge.stock_options_swing_config_get", return_value={"account_id": "new-account"}),
            patch("api.runtime_bridge.start_services", return_value={"ok": True, "started": {"stock_options_swing": "started"}}),
        ):
            out = rt.setup_start_services({"enable_stock_options_swing": True}, owner_id="davies")

        self.assertTrue(out["ok"])
        sync_configs.assert_called_once_with(owner_id="davies", account_id="new-account")


if __name__ == "__main__":
    unittest.main()
