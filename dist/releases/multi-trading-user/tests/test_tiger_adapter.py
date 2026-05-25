import unittest
from types import SimpleNamespace

from api.brokers.base import StockOrderRequest
from api.brokers.tiger_adapter import TigerAdapter, TigerContexts


class _FakeQuoteClient:
    def get_stock_briefs(self, symbols):
        return [{"symbol": symbols[0], "market": "US", "latest_price": 188.5, "timestamp": "2026-05-14T09:30:00Z"}]


class _FakeTradeClient:
    def __init__(self):
        self.last_contract = None
        self.last_order = None

    def get_assets(self):
        return {"net_liquidation": 100000, "buying_power": 50000, "currency": "USD"}

    def get_positions(self):
        return [{"symbol": "AAPL", "market": "US", "quantity": 3, "average_cost": 180.0}]

    def place_order(self, contract, order):
        self.last_contract = contract
        self.last_order = order
        return {"id": "TIGER-1"}


class TestTigerAdapter(unittest.TestCase):
    def test_maps_quote_account_position_and_order_shapes(self):
        adapter = TigerAdapter()
        trade_client = _FakeTradeClient()
        ctx = TigerContexts(quote_client=_FakeQuoteClient(), trade_client=trade_client, config=SimpleNamespace())

        quotes = adapter.get_quotes(ctx, ["AAPL"])
        self.assertEqual("AAPL.US", quotes[0].symbol)
        self.assertEqual(188.5, quotes[0].last_done)

        balance = adapter.get_account_balance(ctx)[0]
        self.assertEqual(100000.0, balance.net_assets)
        self.assertEqual(50000.0, balance.buy_power)

        positions = adapter.get_stock_positions(ctx)
        self.assertEqual("AAPL.US", positions.channels[0].positions[0].symbol)

        resp = adapter.submit_stock_order(
            ctx,
            StockOrderRequest(
                symbol="AAPL.US",
                side="buy",
                order_type="limit",
                submitted_quantity=2,
                submitted_price=188.5,
            ),
        )
        self.assertEqual("TIGER-1", resp.order_id)
        self.assertEqual("AAPL", trade_client.last_contract.symbol)
        self.assertEqual("BUY", trade_client.last_order.action)
        self.assertEqual("LMT", trade_client.last_order.order_type)


if __name__ == "__main__":
    unittest.main()
