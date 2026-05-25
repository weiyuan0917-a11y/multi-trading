import unittest
from types import SimpleNamespace

from api.brokers.base import StockOrderRequest
from api.brokers.fosun_adapter import FosunAdapter, FosunContexts


class _FakeMarket:
    def quote(self, codes, fields=None):
        return {
            "code": 0,
            "data": {
                codes[0]: {
                    "rawSymbol": codes[0],
                    "price": 1885000,
                    "pClose": 1800000,
                    "power": 4,
                    "vol": 123,
                    "bidPrice": 1884000,
                    "askPrice": 1886000,
                }
            },
        }


class _FakePortfolio:
    def get_assets_summary(self, **_kwargs):
        return {
            "code": 0,
            "data": {
                "breakdown": [{"currency": "USD", "cashPurchasingPower": "50000", "maxPurchasingPower": "100000"}],
                "summary": {},
            },
        }

    def get_holdings(self, **kwargs):
        if kwargs.get("product_types") == [15]:
            return {"code": 0, "data": {"list": []}}
        return {
            "code": 0,
            "data": {
                "list": [
                    {
                        "marketCode": "us",
                        "stockCode": "AAPL",
                        "quantity": "3",
                        "avgCost": "180.5",
                        "productType": 6,
                    }
                ]
            },
        }


class _FakeTrade:
    def __init__(self):
        self.last_order = None

    def list_orders(self, **_kwargs):
        return {"code": 0, "data": {"list": []}}

    def create_order(self, **kwargs):
        self.last_order = kwargs
        return {"code": 0, "data": {"orderId": "FOSUN-1"}}

    def cancel_order(self, **_kwargs):
        return {"code": 0, "data": {"orderId": "FOSUN-1"}}


class _FakeClient:
    def __init__(self):
        self.market = _FakeMarket()
        self.optmarket = _FakeMarket()
        self.portfolio = _FakePortfolio()
        self.trade = _FakeTrade()


class TestFosunAdapter(unittest.TestCase):
    def test_maps_quote_account_position_and_stock_order(self):
        adapter = FosunAdapter()
        client = _FakeClient()
        ctx = FosunContexts(client=client, sub_account_id="SUB-1")

        quotes = adapter.get_quotes(ctx, ["AAPL.US"])
        self.assertEqual("AAPL.US", quotes[0].symbol)
        self.assertEqual(188.5, quotes[0].last_done)
        self.assertEqual(188.4, quotes[0].bid_price)

        balance = adapter.get_account_balance(ctx)[0]
        self.assertEqual(100000.0, balance.net_assets)
        self.assertEqual(50000.0, balance.buy_power)

        positions = adapter.get_stock_positions(ctx)
        self.assertEqual("AAPL.US", positions.channels[0].positions[0].symbol)
        self.assertEqual(3.0, positions.channels[0].positions[0].quantity)

        resp = adapter.submit_stock_order(
            ctx,
            StockOrderRequest(
                symbol="AAPL.US",
                side="buy",
                order_type="limit",
                submitted_quantity=2,
                submitted_price="188.50",
            ),
        )
        self.assertEqual("FOSUN-1", resp.order_id)
        self.assertEqual("SUB-1", client.trade.last_order["sub_account_id"])
        self.assertEqual("AAPL", client.trade.last_order["stock_code"])
        self.assertEqual("us", client.trade.last_order["market_code"])
        self.assertEqual(1, client.trade.last_order["direction"])
        self.assertEqual(3, client.trade.last_order["order_type"])
        self.assertEqual(6, client.trade.last_order["product_type"])

    def test_maps_occ_option_order_to_fosun_fields(self):
        adapter = FosunAdapter()
        client = _FakeClient()
        ctx = FosunContexts(client=client, sub_account_id="SUB-1", option_apply_account_id="OPT-1")

        resp = adapter.submit_stock_order(
            ctx,
            StockOrderRequest(
                symbol="QQQ260522C704000.US",
                side="sell",
                order_type="limit",
                submitted_quantity=1,
                submitted_price="3.20",
            ),
        )

        self.assertEqual("FOSUN-1", resp.order_id)
        self.assertEqual("OPT-1", client.trade.last_order["apply_account_id"])
        self.assertEqual("QQQ", client.trade.last_order["stock_code"])
        self.assertEqual("20260522", client.trade.last_order["expiry"])
        self.assertEqual("704", client.trade.last_order["strike"])
        self.assertEqual("CALL", client.trade.last_order["right"])
        self.assertEqual(15, client.trade.last_order["product_type"])
        self.assertEqual(2, client.trade.last_order["direction"])


if __name__ == "__main__":
    unittest.main()
