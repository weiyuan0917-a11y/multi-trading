from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Iterator

from api.brokers.base import BrokerAdapter, BrokerContexts, BrokerCredentials, StockOrderRequest


@dataclass(frozen=True)
class FosunContexts:
    client: Any
    sub_account_id: str
    client_id: int | None = None
    apply_account_id: str = ""
    option_apply_account_id: str = ""


class FosunAdapter(BrokerAdapter):
    broker_id = "fosun"

    _CONNECT_ERROR_KEYS = (
        "fsopenapi",
        "fosun",
        "session",
        "authentication",
        "permission",
        "signature",
        "decrypt",
        "encrypt",
        "timeout",
        "connection",
        "network",
        "unauthorized",
        "forbidden",
        "FSOPENAPI_SERVER_PUBLIC_KEY",
        "FSOPENAPI_CLIENT_PRIVATE_KEY",
    )

    def create_contexts(self, credentials: BrokerCredentials) -> BrokerContexts:
        extras = dict(credentials.extras or {})
        try:
            from fsopenapi import SDKClient
        except Exception as exc:
            raise RuntimeError(
                "Fosun OpenAPI SDK is not installed. Install it with: "
                "pip install -e <path-to-openapi-python-sdk>"
            ) from exc

        base_url = str(extras.get("base_url") or extras.get("fsopenapi_base_url") or os.getenv("FSOPENAPI_BASE_URL") or "").strip()
        if not base_url:
            raise ValueError("missing_fosun_base_url")

        server_public_key = str(extras.get("server_public_key") or os.getenv("FSOPENAPI_SERVER_PUBLIC_KEY") or "").strip()
        client_private_key = str(extras.get("client_private_key") or os.getenv("FSOPENAPI_CLIENT_PRIVATE_KEY") or "").strip()
        sdk_type = str(extras.get("sdk_type") or os.getenv("SDK_TYPE") or "").strip()
        if not server_public_key or not client_private_key:
            raise ValueError("missing_fosun_sdk_keys: need server_public_key and client_private_key PEM")

        with self._sdk_env(server_public_key=server_public_key, client_private_key=client_private_key, sdk_type=sdk_type):
            client = SDKClient(
                base_url=base_url,
                api_key=credentials.app_key,
                logging_enable=str(extras.get("logging_enable") or "").lower() in {"1", "true", "yes"},
                log_body=str(extras.get("log_body") or "").lower() in {"1", "true", "yes"},
            )
        sub_account_id = str(extras.get("sub_account_id") or credentials.access_token or "").strip()
        if not sub_account_id:
            try:
                accounts = self._data(client.account.list_accounts())
                rows = accounts.get("subAccounts") if isinstance(accounts, dict) else None
                if isinstance(rows, list) and rows:
                    sub_account_id = str(self._get(rows[0], "subAccountId", "sub_account_id", "id", default="") or "").strip()
            except Exception:
                sub_account_id = ""
        if not sub_account_id:
            raise ValueError("missing_fosun_sub_account_id")

        client_id = self._optional_int(extras.get("client_id"))
        apply_account_id = str(extras.get("apply_account_id") or "").strip()
        option_apply_account_id = str(extras.get("option_apply_account_id") or "").strip()
        ctx = FosunContexts(
            client=client,
            sub_account_id=sub_account_id,
            client_id=client_id,
            apply_account_id=apply_account_id,
            option_apply_account_id=option_apply_account_id,
        )
        return BrokerContexts(quote=ctx, trade=ctx)

    @contextmanager
    def _sdk_env(self, *, server_public_key: str, client_private_key: str, sdk_type: str) -> Iterator[None]:
        keys = {
            "FSOPENAPI_SERVER_PUBLIC_KEY": server_public_key,
            "FSOPENAPI_CLIENT_PRIVATE_KEY": client_private_key,
            "SDK_TYPE": sdk_type,
        }
        old = {k: os.environ.get(k) for k in keys}
        try:
            for key, value in keys.items():
                if value:
                    os.environ[key] = value
            yield
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def is_connect_error(self, err: Exception | str) -> bool:
        text = str(err or "").lower()
        return any(key.lower() in text for key in self._CONNECT_ERROR_KEYS)

    def get_quotes(self, quote_ctx: Any, symbols: list[str]) -> list[Any]:
        ctx = self._ctx(quote_ctx)
        stock_codes: list[str] = []
        option_codes: list[str] = []
        for symbol in symbols:
            code = self._to_fosun_code(symbol)
            if self._is_us_option_symbol(symbol):
                option_codes.append(code)
            else:
                stock_codes.append(code)
        rows: list[Any] = []
        if stock_codes:
            data = self._data(ctx.client.market.quote(stock_codes))
            quote_map = data if isinstance(data, dict) else {}
            rows.extend(self._quote_row(code, row, original_symbols=symbols) for code, row in quote_map.items())
        if option_codes:
            data = self._data(ctx.client.optmarket.quote(option_codes))
            quote_map = data if isinstance(data, dict) else {}
            rows.extend(self._quote_row(code, row, original_symbols=symbols) for code, row in quote_map.items())
        return rows

    def get_static_info(self, quote_ctx: Any, symbols: list[str]) -> list[Any]:
        return [SimpleNamespace(symbol=str(symbol or "").strip().upper(), lot_size=1, raw={}) for symbol in symbols]

    def get_account_balance(self, trade_ctx: Any) -> list[Any]:
        ctx = self._ctx(trade_ctx)
        data = self._data(
            ctx.client.portfolio.get_assets_summary(
                sub_account_id=ctx.sub_account_id,
                client_id=ctx.client_id,
                apply_account_id=ctx.apply_account_id or None,
            )
        )
        summary = self._get(data, "summary", default={}) if isinstance(data, dict) else {}
        breakdown = self._get(data, "breakdown", default=[]) if isinstance(data, dict) else []
        usd = None
        if isinstance(breakdown, list):
            usd = next((x for x in breakdown if str(self._get(x, "currency", default="")).upper() == "USD"), None)
        row = usd or summary or {}
        net_assets = self._to_float(self._get(row, "maxPurchasingPower", "cashPurchasingPower", "ledgerBalance", default=0))
        buy_power = self._to_float(self._get(row, "cashPurchasingPower", "maxPurchasingPower", "marginPurchasingPower", default=0))
        currency = str(self._get(row, "currency", default="USD") or "USD")
        return [SimpleNamespace(net_assets=net_assets, buy_power=buy_power, currency=currency, raw=data)]

    def get_stock_positions(self, trade_ctx: Any) -> Any:
        ctx = self._ctx(trade_ctx)
        rows: list[Any] = []
        for product_types, sub_account_class, apply_account_id in (
            ([5, 6, 7], None, ctx.apply_account_id or None),
            ([15], 9, ctx.option_apply_account_id or ctx.apply_account_id or None),
        ):
            try:
                data = self._data(
                    ctx.client.portfolio.get_holdings(
                        sub_account_id=ctx.sub_account_id,
                        client_id=ctx.client_id,
                        apply_account_id=apply_account_id,
                        sub_account_class=sub_account_class,
                        product_types=product_types,
                        start=0,
                        count=999,
                        use_us_pre=True,
                        use_us_post=True,
                        use_us_night=True,
                    )
                )
                items = data.get("list") if isinstance(data, dict) else []
                if isinstance(items, list):
                    rows.extend(self._position_row(row) for row in items)
            except Exception:
                if product_types != [15]:
                    raise
        return SimpleNamespace(channels=[SimpleNamespace(positions=rows)])

    def get_today_orders(self, trade_ctx: Any) -> list[Any]:
        ctx = self._ctx(trade_ctx)
        out: list[Any] = []
        for show_type, apply_account_id in ((1, ctx.apply_account_id or None), (2, ctx.option_apply_account_id or ctx.apply_account_id or None)):
            try:
                data = self._data(
                    ctx.client.trade.list_orders(
                        sub_account_id=ctx.sub_account_id,
                        client_id=ctx.client_id,
                        apply_account_id=apply_account_id,
                        start=0,
                        count=100,
                        sort="desc",
                        show_type=show_type,
                    )
                )
                items = data.get("list") if isinstance(data, dict) else []
                if isinstance(items, list):
                    out.extend(self._order_row(row) for row in items)
            except Exception:
                if show_type != 2:
                    raise
        return out

    def submit_stock_order(self, trade_ctx: Any, request: StockOrderRequest) -> Any:
        ctx = self._ctx(trade_ctx)
        meta = self._parse_symbol_for_order(request.symbol)
        product_type = 15 if meta.get("is_option") else self._product_type_for_market(meta["market_code"])
        resp = self._data(
            ctx.client.trade.create_order(
                sub_account_id=ctx.sub_account_id,
                client_id=ctx.client_id,
                apply_account_id=(ctx.option_apply_account_id or ctx.apply_account_id or None) if product_type == 15 else (ctx.apply_account_id or None),
                stock_code=meta["stock_code"],
                market_code=meta["market_code"],
                currency="USD" if meta["market_code"] == "us" else "HKD",
                direction=1 if request.side == "buy" else 2,
                order_type=9 if request.order_type == "market" else 3,
                quantity=str(int(request.submitted_quantity)),
                price=None if request.submitted_price is None else str(request.submitted_price),
                product_type=product_type,
                expiry=meta.get("expiry"),
                strike=meta.get("strike"),
                right=meta.get("right"),
                time_in_force=0,
                exp_type=1,
                short_sell_type="N",
            )
        )
        order_id = self._get(resp, "orderId", "order_id", default=resp)
        return SimpleNamespace(order_id=str(order_id or ""), raw=resp)

    def cancel_order(self, trade_ctx: Any, order_id: str) -> None:
        ctx = self._ctx(trade_ctx)
        ctx.client.trade.cancel_order(order_id=order_id, sub_account_id=ctx.sub_account_id, client_id=ctx.client_id)

    def get_option_chain_expiry_dates(self, quote_ctx: Any, symbol: str) -> list[Any]:
        raise NotImplementedError("Fosun option chain expiry list is not mapped yet")

    def get_option_chain_by_date(self, quote_ctx: Any, symbol: str, expiry_date: Any) -> list[Any]:
        raise NotImplementedError("Fosun option chain by date is not mapped yet")

    def get_depth(self, quote_ctx: Any, symbol: str) -> Any:
        ctx = self._ctx(quote_ctx)
        code = self._to_fosun_code(symbol)
        if self._is_us_option_symbol(symbol):
            data = self._data(ctx.client.optmarket.orderbook(code))
        else:
            data = self._data(ctx.client.market.orderbook(code))
        power = int(self._get(data, "power", default=4) or 4) if isinstance(data, dict) else 4
        return SimpleNamespace(
            bid=[self._book_row(x, power) for x in (self._get(data, "buyOrders", default=[]) if isinstance(data, dict) else [])],
            ask=[self._book_row(x, power) for x in (self._get(data, "sellOrders", default=[]) if isinstance(data, dict) else [])],
            raw=data,
        )

    def get_option_quotes(self, quote_ctx: Any, symbols: list[str]) -> list[Any]:
        ctx = self._ctx(quote_ctx)
        codes = [self._to_fosun_code(symbol) for symbol in symbols]
        data = self._data(ctx.client.optmarket.quote(codes))
        quote_map = data if isinstance(data, dict) else {}
        return [self._quote_row(code, row, original_symbols=symbols) for code, row in quote_map.items()]

    def get_order_detail(self, trade_ctx: Any, order_id: str) -> Any:
        for row in self.get_today_orders(trade_ctx):
            if str(getattr(row, "order_id", "") or "") == str(order_id):
                return row
        return SimpleNamespace(order_id=str(order_id), raw={})

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
        ctx = self._ctx(quote_ctx)
        code = self._to_fosun_code(symbol)
        ktype = self._to_ktype(period)
        start_time = self._date_to_int(start)
        end_time = self._date_to_int(end)
        api = ctx.client.optmarket if self._is_us_option_symbol(symbol) else ctx.client.market
        data = self._data(api.kline(code=code, ktype=ktype, start_time=start_time, end_time=end_time, num=1000))
        rows = data.get("data") if isinstance(data, dict) else []
        power = int(data.get("power") or 4) if isinstance(data, dict) else 4
        return [self._kline_row(row, power) for row in (rows or [])]

    def get_calc_indexes(self, quote_ctx: Any, symbols: list[str], indexes: list[Any]) -> list[Any]:
        raise NotImplementedError("Fosun calculated indexes are not mapped yet")

    def get_intraday(self, quote_ctx: Any, symbol: str) -> list[Any]:
        ctx = self._ctx(quote_ctx)
        data = self._data(ctx.client.market.min(self._to_fosun_code(symbol), count=500))
        min_rows = data.get("min", []) if isinstance(data, dict) else []
        if isinstance(min_rows, dict):
            min_rows = min_rows.get("data", [])
        return list(min_rows or [])

    def get_watchlist(self, quote_ctx: Any) -> list[Any]:
        return []

    @staticmethod
    def _ctx(ctx: Any) -> FosunContexts:
        if isinstance(ctx, FosunContexts):
            return ctx
        raise TypeError("invalid_fosun_context")

    @staticmethod
    def _data(resp: Any) -> Any:
        if isinstance(resp, dict) and "data" in resp and "code" in resp:
            return resp.get("data")
        return resp

    @staticmethod
    def _get(row: Any, *names: str, default: Any = None) -> Any:
        for name in names:
            if isinstance(row, dict) and name in row:
                return row.get(name)
            if hasattr(row, name):
                return getattr(row, name)
        return default

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            if value in (None, ""):
                return None
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            if value in (None, ""):
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _scaled(value: Any, power: Any, default: float = 0.0) -> float:
        try:
            if value in (None, ""):
                return default
            return float(value) / (10 ** int(power or 0))
        except Exception:
            return default

    @staticmethod
    def _to_fosun_code(symbol: Any) -> str:
        s = str(symbol or "").strip().upper()
        if not s:
            return ""
        if s.endswith(".US"):
            core = s[:-3]
            if FosunAdapter._is_us_option_symbol(s):
                return "us" + core
            return "us" + core
        if s.endswith(".HK"):
            return "hk" + s[:-3].zfill(5)
        if s.endswith(".SH"):
            return "sh" + s[:-3]
        if s.endswith(".SZ"):
            return "sz" + s[:-3]
        return s

    @staticmethod
    def _from_fosun_code(code: Any) -> str:
        raw = str(code or "").strip()
        lower = raw.lower()
        if lower.startswith("us"):
            core = raw[2:].upper()
            return f"{core}.US"
        if lower.startswith("hk"):
            return f"{raw[2:].zfill(5)}.HK"
        if lower.startswith("sh"):
            return f"{raw[2:]}.SH"
        if lower.startswith("sz"):
            return f"{raw[2:]}.SZ"
        return raw.upper()

    @staticmethod
    def _is_us_option_symbol(symbol: Any) -> bool:
        s = str(symbol or "").strip().upper()
        core = s[:-3] if s.endswith(".US") else s
        import re

        return bool(re.match(r"^[A-Z0-9]+?\d{6,8}[CP]\d+$", core))

    @staticmethod
    def _parse_symbol_for_order(symbol: str) -> dict[str, Any]:
        s = str(symbol or "").strip().upper()
        if FosunAdapter._is_us_option_symbol(s):
            core = s[:-3] if s.endswith(".US") else s
            import re

            m = re.match(r"^([A-Z0-9]+?)(\d{6}|\d{8})([CP])(\d+)$", core)
            if not m:
                raise ValueError(f"invalid_us_option_symbol: {symbol}")
            underlying, expiry, cp, strike_raw = m.groups()
            if len(expiry) == 6:
                expiry = f"20{expiry}"
            return {
                "is_option": True,
                "market_code": "us",
                "stock_code": underlying,
                "expiry": expiry,
                "strike": str(Decimal(str(int(strike_raw))) / Decimal("1000")),
                "right": "CALL" if cp == "C" else "PUT",
            }
        if s.endswith(".US"):
            return {"is_option": False, "market_code": "us", "stock_code": s[:-3]}
        if s.endswith(".HK"):
            return {"is_option": False, "market_code": "hk", "stock_code": s[:-3].zfill(5)}
        if s.endswith(".SH"):
            return {"is_option": False, "market_code": "sh", "stock_code": s[:-3]}
        if s.endswith(".SZ"):
            return {"is_option": False, "market_code": "sz", "stock_code": s[:-3]}
        return {"is_option": False, "market_code": "us", "stock_code": s}

    @staticmethod
    def _product_type_for_market(market_code: str) -> int:
        market = str(market_code or "").lower()
        if market == "hk":
            return 5
        if market == "us":
            return 6
        return 7

    def _quote_row(self, code: Any, row: Any, *, original_symbols: list[str] | None = None) -> Any:
        power = int(self._get(row, "power", default=4) or 4)
        raw_symbol = self._get(row, "rawSymbol", "raw_symbol", default=code)
        symbol = self._from_fosun_code(raw_symbol or code)
        return SimpleNamespace(
            symbol=symbol,
            last_done=self._scaled(self._get(row, "price", "latestClosePrice", default=0), power),
            prev_close=self._scaled(self._get(row, "pClose", default=0), power),
            open=self._scaled(self._get(row, "open", default=0), power),
            high=self._scaled(self._get(row, "high", default=0), power),
            low=self._scaled(self._get(row, "low", default=0), power),
            volume=int(self._get(row, "vol", default=0) or 0),
            bid_price=self._scaled(self._get(row, "bidPrice", default=0), power),
            ask_price=self._scaled(self._get(row, "askPrice", default=0), power),
            timestamp=str(self._get(row, "qtDate", default="")) + str(self._get(row, "qtTime", default="")),
            raw=row,
        )

    def _position_row(self, row: Any) -> Any:
        product_type = int(self._get(row, "productType", default=0) or 0)
        if product_type == 15:
            symbol = self._from_fosun_code(self._get(row, "mktStockCode", "symbol", "stockCode", default=""))
        else:
            market = str(self._get(row, "marketCode", default="") or "").lower()
            stock_code = str(self._get(row, "stockCode", "symbol", default="") or "")
            symbol = self._from_fosun_code(f"{market}{stock_code}" if market else stock_code)
        return SimpleNamespace(
            symbol=symbol,
            quantity=self._to_float(self._get(row, "quantity", default=0)),
            cost_price=self._to_float(self._get(row, "avgCost", "dilutedCost", default=0)),
            raw=row,
        )

    def _order_row(self, row: Any) -> Any:
        market = str(self._get(row, "marketCode", default="") or "").lower()
        stock_code = str(self._get(row, "stockCode", default="") or "")
        product_type = int(self._get(row, "productType", default=0) or 0)
        if product_type == 15:
            expiry = str(self._get(row, "expiry", default="") or "")
            right = "C" if str(self._get(row, "right", default="")).upper().startswith("C") else "P"
            strike_raw = self._get(row, "strike", default="0")
            try:
                strike = int(Decimal(str(strike_raw)) * Decimal("1000"))
            except Exception:
                strike = 0
            symbol = f"{stock_code.upper()}{expiry[2:] if len(expiry) == 8 else expiry}{right}{strike:08d}.US"
        else:
            symbol = self._from_fosun_code(f"{market}{stock_code}" if market else stock_code)
        direction = int(self._get(row, "direction", default=0) or 0)
        return SimpleNamespace(
            order_id=str(self._get(row, "orderId", default="") or ""),
            symbol=symbol,
            side="buy" if direction == 1 else "sell" if direction == 2 else "",
            quantity=self._to_float(self._get(row, "quantity", default=0)),
            price=self._get(row, "price", "filledPrice", default=None),
            status=str(self._get(row, "orderStatus", default="") or ""),
            raw=row,
        )

    def _book_row(self, row: Any, power: int) -> Any:
        return SimpleNamespace(
            price=self._scaled(self._get(row, "p", "price", default=0), power),
            volume=int(self._get(row, "v", "volume", default=0) or 0),
            raw=row,
        )

    def _kline_row(self, row: Any, power: int) -> Any:
        return SimpleNamespace(
            open=self._scaled(self._get(row, "open", default=0), power),
            close=self._scaled(self._get(row, "close", default=0), power),
            high=self._scaled(self._get(row, "high", default=0), power),
            low=self._scaled(self._get(row, "low", default=0), power),
            volume=int(self._get(row, "vol", default=0) or 0),
            timestamp=self._get(row, "time", default=None),
            raw=row,
        )

    @staticmethod
    def _to_ktype(period: Any) -> str:
        text = str(getattr(period, "value", period) or "").lower()
        if "min_1" in text or text in {"1m", "min1"}:
            return "min1"
        if "min_5" in text or text in {"5m", "min5"}:
            return "min5"
        if "day" in text or text in {"1d", "d"}:
            return "day"
        return "day"

    @staticmethod
    def _date_to_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return int(value.strftime("%Y%m%d"))
        if isinstance(value, date):
            return int(value.strftime("%Y%m%d"))
        s = str(value)
        digits = "".join(ch for ch in s[:10] if ch.isdigit())
        try:
            return int(digits) if digits else None
        except Exception:
            return None
