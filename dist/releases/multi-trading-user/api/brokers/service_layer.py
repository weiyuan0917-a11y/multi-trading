from __future__ import annotations

import threading
from typing import Any

from config.live_settings import live_settings

from api.brokers.base import BrokerOrderSide, BrokerOrderType, BrokerTimeInForce, StockOrderRequest
from api.brokers.registry import get_broker_adapter
from api.services.trade_safety import guard_before_submit_order, record_submit_error, record_submit_result

_CONTEXT_ADAPTER_LOCK = threading.RLock()
_CONTEXT_BROKER_IDS: dict[int, str] = {}


def bind_contexts_to_broker(quote_ctx: Any, trade_ctx: Any, broker_provider: str) -> None:
    broker_id = str(broker_provider or "").strip().lower()
    if not broker_id:
        return
    with _CONTEXT_ADAPTER_LOCK:
        if quote_ctx is not None:
            _CONTEXT_BROKER_IDS[id(quote_ctx)] = broker_id
        if trade_ctx is not None:
            _CONTEXT_BROKER_IDS[id(trade_ctx)] = broker_id


def unbind_contexts(quote_ctx: Any, trade_ctx: Any) -> None:
    with _CONTEXT_ADAPTER_LOCK:
        if quote_ctx is not None:
            _CONTEXT_BROKER_IDS.pop(id(quote_ctx), None)
        if trade_ctx is not None:
            _CONTEXT_BROKER_IDS.pop(id(trade_ctx), None)


def _adapter_for_context(ctx: Any):
    broker_id = ""
    with _CONTEXT_ADAPTER_LOCK:
        if ctx is not None:
            broker_id = _CONTEXT_BROKER_IDS.get(id(ctx), "")
    return get_broker_adapter(broker_id or live_settings.active_broker())


def _enum_text(value: Any) -> str:
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    text = str(raw or "").strip().lower()
    if not text or text.startswith("<"):
        text = str(getattr(value, "name", "") or "").strip().lower()
    return text


def _normalize_order_side(value: Any) -> BrokerOrderSide:
    text = _enum_text(value)
    if "sell" in text:
        return "sell"
    if "buy" in text:
        return "buy"
    raise ValueError(f"Unsupported broker order side: {value}")


def _normalize_order_type(value: Any) -> BrokerOrderType:
    text = _enum_text(value)
    if text in {"lo", "limit"} or text.endswith(".lo") or "limit" in text:
        return "limit"
    if text in {"mo", "market"} or text.endswith(".mo") or "market" in text:
        return "market"
    raise ValueError(f"Unsupported broker order type: {value}")


def _normalize_time_in_force(value: Any) -> BrokerTimeInForce:
    text = _enum_text(value) or "day"
    if text in {"day", "dayonly"} or text.endswith(".day") or "day" in text:
        return "day"
    if text in {"gtc", "goodtilcanceled"} or "goodtilcanceled" in text:
        return "gtc"
    if text in {"ioc", "immediateorcancel"} or "immediateorcancel" in text:
        return "ioc"
    if text in {"fok", "fillorkill"} or "fillorkill" in text:
        return "fok"
    raise ValueError(f"Unsupported broker time in force: {value}")


def get_quotes(quote_ctx: Any, symbols: list[str]) -> list[Any]:
    return _adapter_for_context(quote_ctx).get_quotes(quote_ctx, symbols)


def get_static_info(quote_ctx: Any, symbols: list[str]) -> list[Any]:
    return _adapter_for_context(quote_ctx).get_static_info(quote_ctx, symbols)


def get_account_balance(trade_ctx: Any) -> list[Any]:
    return _adapter_for_context(trade_ctx).get_account_balance(trade_ctx)


def get_stock_positions(trade_ctx: Any) -> Any:
    return _adapter_for_context(trade_ctx).get_stock_positions(trade_ctx)


def get_today_orders(trade_ctx: Any) -> list[Any]:
    return _adapter_for_context(trade_ctx).get_today_orders(trade_ctx)


def submit_order(
    trade_ctx: Any,
    *,
    symbol: str,
    order_type: Any,
    side: Any,
    submitted_quantity: int,
    time_in_force: Any,
    submitted_price: Any = None,
) -> Any:
    request = StockOrderRequest(
        symbol=str(symbol or "").strip().upper(),
        order_type=_normalize_order_type(order_type),
        side=_normalize_order_side(side),
        submitted_quantity=int(submitted_quantity),
        time_in_force=_normalize_time_in_force(time_in_force),
        submitted_price=submitted_price,
    )
    order_key, dry_run_response = guard_before_submit_order(
        symbol=request.symbol,
        order_type=request.order_type,
        side=request.side,
        submitted_quantity=int(request.submitted_quantity),
        time_in_force=request.time_in_force,
        submitted_price=submitted_price,
    )
    if dry_run_response is not None:
        record_submit_result(order_key, dry_run_response)
        return dry_run_response
    try:
        resp = _adapter_for_context(trade_ctx).submit_stock_order(
            trade_ctx,
            request=request,
        )
    except Exception as e:
        record_submit_error(order_key, e)
        raise
    record_submit_result(order_key, resp)
    return resp


def cancel_order(trade_ctx: Any, order_id: str) -> None:
    _adapter_for_context(trade_ctx).cancel_order(trade_ctx, order_id)


def get_option_chain_expiry_dates(quote_ctx: Any, symbol: str) -> list[Any]:
    return _adapter_for_context(quote_ctx).get_option_chain_expiry_dates(quote_ctx, symbol)


def get_option_chain_by_date(quote_ctx: Any, symbol: str, expiry_date: Any) -> list[Any]:
    return _adapter_for_context(quote_ctx).get_option_chain_by_date(quote_ctx, symbol, expiry_date)


def get_depth(quote_ctx: Any, symbol: str) -> Any:
    fn = getattr(quote_ctx, "depth", None)
    if callable(fn):
        return fn(symbol)
    return _adapter_for_context(quote_ctx).get_depth(quote_ctx, symbol)


def get_option_quotes(quote_ctx: Any, symbols: list[str]) -> list[Any]:
    fn = getattr(quote_ctx, "option_quote", None)
    if callable(fn):
        return fn(symbols)
    return _adapter_for_context(quote_ctx).get_option_quotes(quote_ctx, symbols)


def get_order_detail(trade_ctx: Any, order_id: str) -> Any:
    return _adapter_for_context(trade_ctx).get_order_detail(trade_ctx, order_id)


def get_history_candlesticks_by_date(
    quote_ctx: Any,
    *,
    symbol: str,
    period: Any,
    adjust_type: Any,
    start: Any,
    end: Any,
    trade_sessions: Any,
) -> list[Any]:
    return _adapter_for_context(quote_ctx).get_history_candlesticks_by_date(
        quote_ctx,
        symbol=symbol,
        period=period,
        adjust_type=adjust_type,
        start=start,
        end=end,
        trade_sessions=trade_sessions,
    )


def get_calc_indexes(quote_ctx: Any, symbols: list[str], indexes: list[Any]) -> list[Any]:
    return _adapter_for_context(quote_ctx).get_calc_indexes(quote_ctx, symbols, indexes)


def get_intraday(quote_ctx: Any, symbol: str) -> list[Any]:
    return _adapter_for_context(quote_ctx).get_intraday(quote_ctx, symbol)


def get_watchlist(quote_ctx: Any) -> list[Any]:
    return _adapter_for_context(quote_ctx).get_watchlist(quote_ctx)
