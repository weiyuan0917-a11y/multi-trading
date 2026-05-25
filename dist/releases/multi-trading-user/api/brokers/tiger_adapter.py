from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from api.brokers.base import BrokerAdapter, BrokerContexts, BrokerCredentials, StockOrderRequest


@dataclass(frozen=True)
class TigerContexts:
    quote_client: Any
    trade_client: Any
    config: Any


class TigerAdapter(BrokerAdapter):
    broker_id = "tiger"

    _CONNECT_ERROR_KEYS = (
        "tiger",
        "connection",
        "connect",
        "timeout",
        "timed out",
        "network",
        "request limit",
        "rate limit",
        "unauthorized",
        "forbidden",
        "permission",
        "signature",
        "license",
        "private key",
        "token",
    )

    def create_contexts(self, credentials: BrokerCredentials) -> BrokerContexts:
        extras = dict(credentials.extras or {})
        config = self._create_client_config(credentials, extras)
        quote_client, trade_client = self._create_clients(config)
        ctx = TigerContexts(quote_client=quote_client, trade_client=trade_client, config=config)
        return BrokerContexts(quote=ctx, trade=ctx)

    def is_connect_error(self, err: Exception | str) -> bool:
        text = str(err or "").lower()
        return any(key in text for key in self._CONNECT_ERROR_KEYS)

    def get_quotes(self, quote_ctx: Any, symbols: list[str]) -> list[Any]:
        client = self._quote_client(quote_ctx)
        method = self._first_method(client, ("get_stock_briefs", "get_briefs", "get_quote", "get_quotes"))
        raw = method(symbols)
        return [self._quote_row(row) for row in self._as_list(raw)]

    def get_static_info(self, quote_ctx: Any, symbols: list[str]) -> list[Any]:
        client = self._quote_client(quote_ctx)
        method = self._first_method(client, ("get_stock_details", "get_stock_detail", "get_symbol_names"))
        raw = method(symbols)
        return [self._static_row(row, symbol=symbols[i] if i < len(symbols) else "") for i, row in enumerate(self._as_list(raw))]

    def get_account_balance(self, trade_ctx: Any) -> list[Any]:
        client = self._trade_client(trade_ctx)
        for name in ("get_prime_assets", "get_assets", "get_account"):
            method = getattr(client, name, None)
            if callable(method):
                raw = method()
                return [self._account_row(raw)]
        raise NotImplementedError("Tiger account balance API is not mapped yet")

    def get_stock_positions(self, trade_ctx: Any) -> Any:
        client = self._trade_client(trade_ctx)
        method = self._first_method(client, ("get_positions", "get_prime_positions"))
        raw = method()
        positions = [self._position_row(row) for row in self._as_list(raw)]
        return SimpleNamespace(channels=[SimpleNamespace(positions=positions)])

    def get_today_orders(self, trade_ctx: Any) -> list[Any]:
        client = self._trade_client(trade_ctx)
        method = self._first_method(client, ("get_orders", "get_active_orders", "get_filled_orders"))
        raw = method()
        return [self._order_row(row) for row in self._as_list(raw)]

    def submit_stock_order(self, trade_ctx: Any, request: StockOrderRequest) -> Any:
        client = self._trade_client(trade_ctx)
        contract = self._build_stock_contract(request.symbol)
        order = self._build_stock_order(request)
        for name in ("place_order", "submit_order"):
            method = getattr(client, name, None)
            if callable(method):
                try:
                    raw = method(contract, order)
                except TypeError:
                    raw = method(order)
                return self._submit_response(raw)
        raise NotImplementedError("Tiger submit order API is not mapped yet")

    def cancel_order(self, trade_ctx: Any, order_id: str) -> None:
        client = self._trade_client(trade_ctx)
        method = self._first_method(client, ("cancel_order",))
        method(order_id)

    def get_option_chain_expiry_dates(self, quote_ctx: Any, symbol: str) -> list[Any]:
        client = self._quote_client(quote_ctx)
        method = self._first_method(client, ("get_option_expirations", "get_option_expiry_dates"))
        return self._as_list(method(symbol))

    def get_option_chain_by_date(self, quote_ctx: Any, symbol: str, expiry_date: Any) -> list[Any]:
        client = self._quote_client(quote_ctx)
        method = self._first_method(client, ("get_option_chain", "get_option_briefs"))
        return self._as_list(method(symbol, expiry_date))

    def get_depth(self, quote_ctx: Any, symbol: str) -> Any:
        client = self._quote_client(quote_ctx)
        method = self._first_method(client, ("get_depth_quote", "get_depth"))
        return method(symbol)

    def get_option_quotes(self, quote_ctx: Any, symbols: list[str]) -> list[Any]:
        client = self._quote_client(quote_ctx)
        method = self._first_method(client, ("get_option_briefs", "get_option_quotes"))
        return self._as_list(method(symbols))

    def get_order_detail(self, trade_ctx: Any, order_id: str) -> Any:
        client = self._trade_client(trade_ctx)
        method = self._first_method(client, ("get_order", "get_order_detail"))
        return method(order_id)

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
        client = self._quote_client(quote_ctx)
        method = self._first_method(client, ("get_bars", "get_history_bars", "get_kline"))
        return self._as_list(method(symbol, period=period, begin_time=start, end_time=end))

    def get_calc_indexes(self, quote_ctx: Any, symbols: list[str], indexes: list[Any]) -> list[Any]:
        raise NotImplementedError("Tiger calculated indexes are not mapped yet")

    def get_intraday(self, quote_ctx: Any, symbol: str) -> list[Any]:
        client = self._quote_client(quote_ctx)
        method = self._first_method(client, ("get_timeline", "get_intraday"))
        return self._as_list(method(symbol))

    def get_watchlist(self, quote_ctx: Any) -> list[Any]:
        raise NotImplementedError("Tiger watchlist is not mapped yet")

    def _create_client_config(self, credentials: BrokerCredentials, extras: dict[str, Any]) -> Any:
        try:
            from tigeropen.tiger_open_config import TigerOpenClientConfig
        except Exception as exc:
            raise RuntimeError("Tiger SDK is not installed. Install tigeropen before connecting a tiger account.") from exc

        config = TigerOpenClientConfig()
        self._set_first_existing(config, ("tiger_id", "tigerId"), credentials.app_key)
        self._set_first_existing(config, ("account", "account_id"), credentials.access_token)
        self._set_first_existing(config, ("license",), credentials.app_secret)
        for attr in ("env", "private_key", "private_key_path", "props_path", "secret_key", "token_path"):
            value = extras.get(attr)
            if value not in (None, ""):
                setattr(config, attr, value)
        return config

    @staticmethod
    def _create_clients(config: Any) -> tuple[Any, Any]:
        try:
            from tigeropen.quote.quote_client import QuoteClient
            from tigeropen.trade.trade_client import TradeClient
        except Exception as exc:
            raise RuntimeError("Tiger SDK clients are unavailable. Check tigeropen installation.") from exc
        return QuoteClient(config), TradeClient(config)

    @staticmethod
    def _set_first_existing(target: Any, names: tuple[str, ...], value: Any) -> None:
        for name in names:
            try:
                setattr(target, name, value)
                return
            except Exception:
                continue

    @staticmethod
    def _quote_client(ctx: Any) -> Any:
        return getattr(ctx, "quote_client", ctx)

    @staticmethod
    def _trade_client(ctx: Any) -> Any:
        return getattr(ctx, "trade_client", ctx)

    @staticmethod
    def _first_method(obj: Any, names: tuple[str, ...]):
        for name in names:
            fn = getattr(obj, name, None)
            if callable(fn):
                return fn
        raise NotImplementedError(f"Tiger SDK method not mapped. Tried: {', '.join(names)}")

    @staticmethod
    def _as_list(raw: Any) -> list[Any]:
        if raw is None:
            return []
        data = getattr(raw, "data", raw)
        if isinstance(data, list):
            return data
        if isinstance(data, tuple):
            return list(data)
        return [data]

    @staticmethod
    def _get(row: Any, *names: str, default: Any = None) -> Any:
        for name in names:
            if isinstance(row, dict) and name in row:
                return row.get(name)
            if hasattr(row, name):
                return getattr(row, name)
        return default

    @staticmethod
    def _symbol_to_tiger(symbol: str) -> tuple[str, str]:
        sym = str(symbol or "").strip().upper()
        if sym.endswith(".US"):
            return sym[:-3], "US"
        if sym.endswith(".HK"):
            return sym[:-3], "HK"
        return sym, ""

    @staticmethod
    def _symbol_from_tiger(symbol: Any, market: Any = "") -> str:
        sym = str(symbol or "").strip().upper()
        mk = str(market or "").strip().upper()
        if not sym:
            return ""
        if "." in sym:
            return sym
        if mk in {"US", "HK", "SG"}:
            return f"{sym}.{mk}"
        return sym

    def _quote_row(self, row: Any) -> Any:
        market = self._get(row, "market", "sec_type", default="")
        symbol = self._symbol_from_tiger(self._get(row, "symbol", "identifier", default=""), market)
        return SimpleNamespace(
            symbol=symbol,
            last_done=self._get(row, "latest_price", "last_price", "price", "last_done", default=0),
            timestamp=self._get(row, "timestamp", "time", "latest_time", default=None),
            raw=row,
        )

    def _static_row(self, row: Any, *, symbol: str = "") -> Any:
        market = self._get(row, "market", default="")
        return SimpleNamespace(
            symbol=self._symbol_from_tiger(self._get(row, "symbol", default=symbol), market),
            lot_size=int(self._get(row, "lot_size", "lotSize", "min_tick", default=1) or 1),
            raw=row,
        )

    def _account_row(self, row: Any) -> Any:
        return SimpleNamespace(
            net_assets=float(self._get(row, "net_liquidation", "net_assets", "equity", "total_assets", default=0) or 0),
            buy_power=float(self._get(row, "buying_power", "buy_power", "available_cash", "cash_available_for_trade", default=0) or 0),
            currency=str(self._get(row, "currency", "base_currency", default="USD") or "USD"),
            raw=row,
        )

    def _position_row(self, row: Any) -> Any:
        market = self._get(row, "market", default="")
        return SimpleNamespace(
            symbol=self._symbol_from_tiger(self._get(row, "symbol", default=""), market),
            quantity=float(self._get(row, "quantity", "position", "qty", default=0) or 0),
            cost_price=float(self._get(row, "average_cost", "avg_cost", "cost_price", default=0) or 0),
            raw=row,
        )

    def _order_row(self, row: Any) -> Any:
        market = self._get(row, "market", default="")
        return SimpleNamespace(
            order_id=str(self._get(row, "id", "order_id", "orderId", default="") or ""),
            symbol=self._symbol_from_tiger(self._get(row, "symbol", default=""), market),
            side=str(self._get(row, "action", "side", default="") or ""),
            quantity=float(self._get(row, "quantity", "total_quantity", "qty", default=0) or 0),
            price=self._get(row, "limit_price", "price", default=None),
            status=str(self._get(row, "status", default="") or ""),
            raw=row,
        )

    def _build_stock_contract(self, symbol: str) -> Any:
        tiger_symbol, market = self._symbol_to_tiger(symbol)
        try:
            from tigeropen.common.util.contract_utils import stock_contract

            if market:
                return stock_contract(symbol=tiger_symbol, currency="USD" if market == "US" else None)
            return stock_contract(symbol=tiger_symbol)
        except Exception:
            return SimpleNamespace(symbol=tiger_symbol, market=market)

    def _build_stock_order(self, request: StockOrderRequest) -> Any:
        try:
            from tigeropen.common.util.order_utils import limit_order, market_order

            action = self._to_tiger_action(request.side)
            if request.order_type == "limit":
                return limit_order(action, int(request.submitted_quantity), request.submitted_price)
            return market_order(action, int(request.submitted_quantity))
        except Exception:
            return SimpleNamespace(
                action=self._to_tiger_action(request.side),
                order_type=self._to_tiger_order_type(request.order_type),
                quantity=int(request.submitted_quantity),
                limit_price=request.submitted_price,
                time_in_force=request.time_in_force,
            )

    @staticmethod
    def _to_tiger_action(side: str) -> str:
        value = str(side or "").strip().lower()
        if value == "buy":
            return "BUY"
        if value == "sell":
            return "SELL"
        raise ValueError(f"Unsupported Tiger order side: {side}")

    @staticmethod
    def _to_tiger_order_type(order_type: str) -> str:
        value = str(order_type or "").strip().lower()
        if value == "limit":
            return "LMT"
        if value == "market":
            return "MKT"
        raise ValueError(f"Unsupported Tiger order type: {order_type}")

    @staticmethod
    def _submit_response(raw: Any) -> Any:
        order_id = TigerAdapter._get(raw, "id", "order_id", "orderId", default=raw)
        return SimpleNamespace(order_id=str(order_id or ""), raw=raw)
