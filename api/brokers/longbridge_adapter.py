from __future__ import annotations

import base64
import json
import time

from longbridge.openapi import Config, OrderSide, OrderType, QuoteContext, TimeInForceType, TradeContext

from api.brokers.base import BrokerAdapter, BrokerContexts, BrokerCredentials, StockOrderRequest


class LongBridgeAdapter(BrokerAdapter):
    broker_id = "longbridge"

    _CONNECT_ERROR_KEYS = (
        "openapiexception",
        "client error (connect)",
        "error sending request for url",
        "/v1/socket/token",
        "connection reset",
        "name or service not known",
        "timed out",
        "connection refused",
        "breaker_open",
    )

    @staticmethod
    def _token_exp(access_token: str) -> int | None:
        parts = str(access_token or "").strip().split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        try:
            data = json.loads(base64.urlsafe_b64decode(f"{payload}{padding}").decode("utf-8"))
        except Exception:
            return None
        try:
            return int(data.get("exp"))
        except Exception:
            return None

    @classmethod
    def _raise_if_token_expired(cls, access_token: str) -> None:
        exp = cls._token_exp(access_token)
        if exp is not None and exp <= int(time.time()):
            raise ValueError(f"longbridge_access_token_expired: exp={exp}")

    @staticmethod
    def _raise_if_credentials_missing(credentials: BrokerCredentials) -> None:
        missing = []
        if not str(credentials.app_key or "").strip():
            missing.append("app_key")
        if not str(credentials.app_secret or "").strip():
            missing.append("app_secret")
        if not str(credentials.access_token or "").strip():
            missing.append("access_token")
        if missing:
            raise ValueError(f"longbridge_credentials_required: {','.join(missing)}")

    def create_contexts(self, credentials: BrokerCredentials) -> BrokerContexts:
        self._raise_if_credentials_missing(credentials)
        self._raise_if_token_expired(credentials.access_token)
        cfg = Config.from_apikey(
            credentials.app_key,
            credentials.app_secret,
            credentials.access_token,
            enable_overnight=True,
            enable_print_quote_packages=False,
        )
        return BrokerContexts(quote=QuoteContext(cfg), trade=TradeContext(cfg))

    def is_connect_error(self, err: Exception | str) -> bool:
        text = str(err or "").lower()
        return any(key in text for key in self._CONNECT_ERROR_KEYS)

    def get_quotes(self, quote_ctx, symbols: list[str]) -> list:
        return quote_ctx.quote(symbols)

    def get_static_info(self, quote_ctx, symbols: list[str]) -> list:
        return quote_ctx.static_info(symbols)

    def get_account_balance(self, trade_ctx) -> list:
        return trade_ctx.account_balance()

    def get_stock_positions(self, trade_ctx):
        return trade_ctx.stock_positions()

    def get_today_orders(self, trade_ctx) -> list:
        return trade_ctx.today_orders()

    def submit_stock_order(
        self,
        trade_ctx,
        request: StockOrderRequest,
    ):
        kwargs = {}
        if request.submitted_price is not None:
            kwargs["submitted_price"] = request.submitted_price
        return trade_ctx.submit_order(
            symbol=request.symbol,
            order_type=self._to_order_type(request.order_type),
            side=self._to_order_side(request.side),
            submitted_quantity=int(request.submitted_quantity),
            time_in_force=self._to_time_in_force(request.time_in_force),
            **kwargs,
        )

    @staticmethod
    def _to_order_side(value: str):
        side = str(value or "").strip().lower()
        if side == "buy":
            return OrderSide.Buy
        if side == "sell":
            return OrderSide.Sell
        raise ValueError(f"Unsupported order side for LongBridge: {value}")

    @staticmethod
    def _to_order_type(value: str):
        order_type = str(value or "").strip().lower()
        if order_type == "limit":
            return OrderType.LO
        if order_type == "market":
            return OrderType.MO
        raise ValueError(f"Unsupported order type for LongBridge: {value}")

    @staticmethod
    def _to_time_in_force(value: str):
        tif = str(value or "").strip().lower()
        if tif == "day":
            return TimeInForceType.Day
        if tif == "gtc":
            return TimeInForceType.GoodTilCanceled
        if tif == "ioc":
            raise ValueError("LongBridge does not support ioc time in force through this adapter")
        if tif == "fok":
            raise ValueError("LongBridge does not support fok time in force through this adapter")
        raise ValueError(f"Unsupported time in force for LongBridge: {value}")

    def cancel_order(self, trade_ctx, order_id: str) -> None:
        trade_ctx.cancel_order(order_id)

    def get_option_chain_expiry_dates(self, quote_ctx, symbol: str) -> list:
        return quote_ctx.option_chain_expiry_date_list(symbol)

    def get_option_chain_by_date(self, quote_ctx, symbol: str, expiry_date) -> list:
        return quote_ctx.option_chain_info_by_date(symbol, expiry_date)

    def get_depth(self, quote_ctx, symbol: str):
        return quote_ctx.depth(symbol)

    def get_option_quotes(self, quote_ctx, symbols: list[str]) -> list:
        fn = getattr(quote_ctx, "option_quote", None)
        if not callable(fn):
            raise AttributeError("option_quote is not supported by current quote context")
        return fn(symbols)

    def get_order_detail(self, trade_ctx, order_id: str):
        return trade_ctx.order_detail(order_id=order_id)

    def get_history_candlesticks_by_date(
        self,
        quote_ctx,
        *,
        symbol: str,
        period,
        adjust_type,
        start,
        end,
        trade_sessions,
    ) -> list:
        return quote_ctx.history_candlesticks_by_date(
            symbol=symbol,
            period=period,
            adjust_type=adjust_type,
            start=start,
            end=end,
            trade_sessions=trade_sessions,
        )

    def get_calc_indexes(self, quote_ctx, symbols: list[str], indexes: list) -> list:
        return quote_ctx.calc_indexes(symbols, indexes)

    def get_intraday(self, quote_ctx, symbol: str) -> list:
        return quote_ctx.intraday(symbol)

    def get_watchlist(self, quote_ctx) -> list:
        return quote_ctx.watchlist()
