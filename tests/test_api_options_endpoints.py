import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app


class _FakeBalance:
    buy_power = 100000
    net_assets = 200000
    currency = "USD"


class _FakeTradeContext:
    positions = []

    def account_balance(self):
        return [_FakeBalance()]

    def today_orders(self):
        return []

    def stock_positions(self):
        class _P:
            channels = []

        positions = list(getattr(self, "positions", []) or [])
        if not positions:
            return _P()
        channel = type("_Channel", (), {})()
        channel.positions = positions
        p = _P()
        p.channels = [channel]
        return p

    def submit_order(self, **_kwargs):
        class _Resp:
            order_id = "ORD-TEST-1"

        return _Resp()


class _FakeQuoteContext:
    def option_chain_expiry_date_list(self, _symbol):
        from datetime import date

        return [date(2026, 6, 19)]

    def option_chain_info_by_date(self, _symbol, _target):
        class _Item:
            price = 200
            call_symbol = "AAPL260619C00200000"
            put_symbol = "AAPL260619P00200000"
            standard = True

        return [_Item()]

    def quote(self, _symbols):
        return []


class TestApiOptionsEndpoints(unittest.TestCase):
    def setUp(self):
        self._old_env = dict(os.environ)
        os.environ["LOCAL_AGENT_OWNER_PLAN"] = "premium"
        os.environ["LOCAL_AGENT_ALLOW_USER_OWNERS"] = "true"
        self.headers = {"X-MT-Local-Owner": "alice"}
        self.client = TestClient(app)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)

    @patch("api.main.ensure_contexts", return_value=(_FakeQuoteContext(), _FakeTradeContext()))
    def test_options_chain(self, _mock_ctx):
        r = self.client.get("/options/chain", params={"symbol": "AAPL.US"}, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("options", body)
        self.assertGreaterEqual(len(body["options"]), 1)

    @patch("api.main.ensure_contexts", return_value=(_FakeQuoteContext(), _FakeTradeContext()))
    @patch("api.main._ensure_l3_confirmation", return_value=None)
    def test_options_order(self, _mock_auth, _mock_ctx):
        r = self.client.post(
            "/options/order",
            json={
                "legs": [
                    {"symbol": "AAPL260619C00200000", "side": "buy", "contracts": 1, "price": 1.2},
                    {"symbol": "AAPL260619C00210000", "side": "sell", "contracts": 1, "price": 0.7},
                ],
                "confirmation_token": "ok",
            },
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("risk", r.json())

    @patch("api.main.ensure_contexts", return_value=(_FakeQuoteContext(), _FakeTradeContext()))
    @patch("api.main._ensure_l3_confirmation", return_value=None)
    def test_options_order_blocks_uncovered_single_sell(self, _mock_auth, _mock_ctx):
        r = self.client.post(
            "/options/order",
            json={
                "legs": [{"symbol": "AAPL260619C00200000", "side": "sell", "contracts": 1, "price": 1.2}],
                "confirmation_token": "ok",
            },
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 400)
        detail = r.json()["detail"]
        self.assertEqual("option_sell_uncovered", detail["error"])

    @patch("api.main._ensure_l3_confirmation", return_value=None)
    def test_options_order_allows_sell_to_close_with_broker_position(self, _mock_auth):
        pos = type("_Pos", (), {})()
        pos.symbol = "AAPL260619C00200000"
        pos.quantity = 1
        pos.cost_price = 1.0
        trade_ctx = _FakeTradeContext()
        trade_ctx.positions = [pos]
        with patch("api.main.ensure_contexts", return_value=(_FakeQuoteContext(), trade_ctx)):
            r = self.client.post(
                "/options/order",
                json={
                    "legs": [{"symbol": "AAPL260619C00200000", "side": "sell", "contracts": 1, "price": 1.2}],
                    "confirmation_token": "ok",
                },
                headers=self.headers,
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual("single_leg", r.json()["mode"])

    @patch("api.main._gateway_get_json", return_value=None)
    @patch("api.main.ensure_contexts", side_effect=RuntimeError("broker_connect_breaker_open"))
    def test_internal_longport_quote_returns_unavailable_on_broker_breaker(self, _mock_ctx, _mock_gateway):
        r = self.client.get("/internal/longport/quote", params={"symbol": "QQQ.US"}, headers=self.headers)

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual("QQQ.US", body["symbol"])
        self.assertFalse(body["available"])
        self.assertEqual("broker_connect_unavailable", body["reason"])


if __name__ == "__main__":
    unittest.main()
