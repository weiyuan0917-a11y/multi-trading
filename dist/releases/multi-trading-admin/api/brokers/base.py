from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class BrokerCredentials:
    app_key: str
    app_secret: str
    access_token: str
    extras: dict[str, Any] | None = None


@dataclass(frozen=True)
class BrokerContexts:
    quote: Any
    trade: Any


BrokerOrderSide = Literal["buy", "sell"]
BrokerOrderType = Literal["market", "limit"]
BrokerTimeInForce = Literal["day", "gtc", "ioc", "fok"]


@dataclass(frozen=True)
class StockOrderRequest:
    symbol: str
    side: BrokerOrderSide
    order_type: BrokerOrderType
    submitted_quantity: int
    time_in_force: BrokerTimeInForce = "day"
    submitted_price: Any = None


@runtime_checkable
class BrokerAdapter(Protocol):
    broker_id: str

    def create_contexts(self, credentials: BrokerCredentials) -> BrokerContexts:
        """Create quote/trade contexts for the broker."""

    def is_connect_error(self, err: Exception | str) -> bool:
        """Check whether an error is a broker connectivity failure."""

    def get_quotes(self, quote_ctx: Any, symbols: list[str]) -> list[Any]:
        """Fetch real-time quotes for symbols."""

    def get_static_info(self, quote_ctx: Any, symbols: list[str]) -> list[Any]:
        """Fetch static info for symbols (lot size, etc.)."""

    def get_account_balance(self, trade_ctx: Any) -> list[Any]:
        """Fetch account balance snapshot."""

    def get_stock_positions(self, trade_ctx: Any) -> Any:
        """Fetch stock positions."""

    def get_today_orders(self, trade_ctx: Any) -> list[Any]:
        """Fetch today's orders."""

    def submit_stock_order(
        self,
        trade_ctx: Any,
        request: StockOrderRequest,
    ) -> Any:
        """Submit a stock order."""

    def cancel_order(self, trade_ctx: Any, order_id: str) -> None:
        """Cancel an existing order."""

    def get_option_chain_expiry_dates(self, quote_ctx: Any, symbol: str) -> list[Any]:
        """Fetch option expiries for underlying symbol."""

    def get_option_chain_by_date(self, quote_ctx: Any, symbol: str, expiry_date: Any) -> list[Any]:
        """Fetch option chain rows for one expiry."""

    def get_depth(self, quote_ctx: Any, symbol: str) -> Any:
        """Fetch level-2 market depth for symbol."""

    def get_option_quotes(self, quote_ctx: Any, symbols: list[str]) -> list[Any]:
        """Fetch option quote rows when broker supports dedicated option quote API."""

    def get_order_detail(self, trade_ctx: Any, order_id: str) -> Any:
        """Fetch one order detail object."""

    def get_history_candlesticks_by_date(
        self,
        quote_ctx: Any,
        *,
        symbol: str,
        period: Any,
        adjust_type: Any,
        start: Any,
        end: Any,
        trade_sessions: Any,
    ) -> list[Any]:
        """Fetch historical candlesticks by date range."""

    def get_calc_indexes(self, quote_ctx: Any, symbols: list[str], indexes: list[Any]) -> list[Any]:
        """Fetch calculated index values for symbols."""

    def get_intraday(self, quote_ctx: Any, symbol: str) -> list[Any]:
        """Fetch intraday line data."""

    def get_watchlist(self, quote_ctx: Any) -> list[Any]:
        """Fetch watchlist groups."""
