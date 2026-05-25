import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.brokers import register_broker_adapter
from api.brokers.base import BrokerContexts
from api.brokers import service_layer


class _FakeBrokerAdapter:
    def __init__(self, broker_id: str = "fakebroker") -> None:
        self.broker_id = broker_id
        self.last_request = None

    @property
    def order_id(self) -> str:
        return f"{self.broker_id}-order"

    def create_contexts(self, _credentials):
        return BrokerContexts(quote=object(), trade=object())

    def is_connect_error(self, _err):
        return False

    def get_quotes(self, _quote_ctx, symbols):
        return [{"symbol": s, "source": self.broker_id} for s in symbols]

    def get_static_info(self, _quote_ctx, symbols):
        return []

    def get_account_balance(self, _trade_ctx):
        return []

    def get_stock_positions(self, _trade_ctx):
        return SimpleNamespace(channels=[])

    def get_today_orders(self, _trade_ctx):
        return []

    def submit_stock_order(self, _trade_ctx, request):
        self.last_request = request
        return SimpleNamespace(order_id=self.order_id)

    def cancel_order(self, _trade_ctx, _order_id):
        return None

    def get_option_chain_expiry_dates(self, _quote_ctx, _symbol):
        return []

    def get_option_chain_by_date(self, _quote_ctx, _symbol, _expiry_date):
        return []

    def get_depth(self, _quote_ctx, _symbol):
        return None

    def get_option_quotes(self, _quote_ctx, _symbols):
        return []

    def get_order_detail(self, _trade_ctx, _order_id):
        return None

    def get_history_candlesticks_by_date(self, _quote_ctx, **_kwargs):
        return []

    def get_calc_indexes(self, _quote_ctx, _symbols, _indexes):
        return []

    def get_intraday(self, _quote_ctx, _symbol):
        return []

    def get_watchlist(self, _quote_ctx):
        return []


class TestBrokerServiceLayer(unittest.TestCase):
    def test_bound_context_uses_context_broker_adapter_for_orders(self):
        adapter = _FakeBrokerAdapter()
        register_broker_adapter(adapter.broker_id, adapter)
        quote_ctx = object()
        trade_ctx = object()
        service_layer.bind_contexts_to_broker(quote_ctx, trade_ctx, adapter.broker_id)
        try:
            with patch("api.brokers.service_layer.guard_before_submit_order", return_value=("k", None)):
                resp = service_layer.submit_order(
                    trade_ctx,
                    symbol="aapl.us",
                    order_type="limit",
                    side="buy",
                    submitted_quantity=3,
                    time_in_force="day",
                    submitted_price="188.50",
                )
        finally:
            service_layer.unbind_contexts(quote_ctx, trade_ctx)

        self.assertEqual("fakebroker-order", resp.order_id)
        self.assertIsNotNone(adapter.last_request)
        self.assertEqual("AAPL.US", adapter.last_request.symbol)
        self.assertEqual("limit", adapter.last_request.order_type)
        self.assertEqual("buy", adapter.last_request.side)
        self.assertEqual("day", adapter.last_request.time_in_force)

    def test_broker_adapter_binding_isolated_between_supported_brokers(self):
        longbridge = _FakeBrokerAdapter("longbridge_iso")
        tiger = _FakeBrokerAdapter("tiger_iso")
        fosun = _FakeBrokerAdapter("fosun_iso")
        register_broker_adapter("longbridge_iso", longbridge)
        register_broker_adapter("tiger_iso", tiger)
        register_broker_adapter("fosun_iso", fosun)
        quote_longbridge = object()
        trade_longbridge = object()
        quote_tiger = object()
        trade_tiger = object()
        service_layer.bind_contexts_to_broker(quote_longbridge, trade_longbridge, "longbridge_iso")
        service_layer.bind_contexts_to_broker(quote_tiger, trade_tiger, "tiger_iso")
        try:
            with patch("api.brokers.service_layer.guard_before_submit_order", return_value=("k", None)):
                longbridge_resp = service_layer.submit_order(
                    trade_longbridge,
                    symbol="aapl.us",
                    order_type="limit",
                    side="buy",
                    submitted_quantity=1,
                    time_in_force="day",
                    submitted_price="100",
                )
                tiger_resp = service_layer.submit_order(
                    trade_tiger,
                    symbol="msft.us",
                    order_type="limit",
                    side="sell",
                    submitted_quantity=2,
                    time_in_force="day",
                    submitted_price="200",
                )
        finally:
            service_layer.unbind_contexts(quote_longbridge, trade_longbridge)
            service_layer.unbind_contexts(quote_tiger, trade_tiger)

        self.assertEqual("longbridge_iso-order", longbridge_resp.order_id)
        self.assertEqual("tiger_iso-order", tiger_resp.order_id)
        self.assertIsNone(fosun.last_request)
        self.assertEqual("AAPL.US", longbridge.last_request.symbol)
        self.assertEqual("MSFT.US", tiger.last_request.symbol)


if __name__ == "__main__":
    unittest.main()
