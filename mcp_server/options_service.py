from __future__ import annotations

import re
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from collections import defaultdict, deque
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from api.brokers import service_layer as broker_service
try:
    from .fee_model import estimate_us_option_multi_leg_fee, estimate_us_option_order_fee
except ImportError:
    from fee_model import estimate_us_option_multi_leg_fee, estimate_us_option_order_fee


OptionSide = Literal["buy", "sell"]


@dataclass
class OptionLeg:
    symbol: str
    side: OptionSide
    contracts: int
    price: float = 0.0


def _is_option_symbol(symbol: str) -> bool:
    s = str(symbol or "").upper()
    if ".US" in s and (" C " in s or " P " in s):
        return True
    return bool(re.search(r"\d{6,8}[CP]\d+", s))


def normalize_legs(legs: list[dict[str, Any]]) -> list[OptionLeg]:
    out: list[OptionLeg] = []
    for idx, leg in enumerate(legs or []):
        if not isinstance(leg, dict):
            raise ValueError(f"legs[{idx}] 必须是对象")
        symbol = str(leg.get("symbol", "")).strip().upper()
        side = str(leg.get("side", "")).strip().lower()
        contracts = int(leg.get("contracts", 0))
        price = float(leg.get("price", 0.0) or 0.0)
        if not symbol:
            raise ValueError(f"legs[{idx}].symbol 不能为空")
        if side not in {"buy", "sell"}:
            raise ValueError(f"legs[{idx}].side 仅支持 buy/sell")
        if contracts <= 0:
            raise ValueError(f"legs[{idx}].contracts 必须 > 0")
        if price < 0:
            raise ValueError(f"legs[{idx}].price 不能为负")
        out.append(OptionLeg(symbol=symbol, side=side, contracts=contracts, price=price))
    if not out:
        raise ValueError("legs 不能为空")
    return out


def legs_to_fee_payload(legs: list[OptionLeg]) -> list[dict[str, Any]]:
    return [{"symbol": x.symbol, "side": x.side, "contracts": x.contracts, "price": x.price} for x in legs]


def build_order_legs(
    *,
    legs: list[dict[str, Any]] | None = None,
    symbol: str | None = None,
    side: OptionSide | None = None,
    contracts: int | None = None,
    price: float | None = None,
) -> list[OptionLeg]:
    """Build normalized option legs from either multi-leg payload or single-leg fields."""
    if legs:
        return normalize_legs(legs)
    if not symbol or not side or not contracts:
        raise ValueError("单腿模式需提供 symbol/side/contracts")
    return normalize_legs(
        [
            {
                "symbol": symbol,
                "side": side,
                "contracts": contracts,
                "price": price or 0.0,
            }
        ]
    )


def estimate_option_fee_for_legs(legs: list[OptionLeg]) -> dict[str, Any]:
    return estimate_us_option_multi_leg_fee(legs_to_fee_payload(legs))


def evaluate_option_risk(
    legs: list[OptionLeg],
    available_cash: float,
    max_loss_threshold: float | None = None,
    max_capital_usage: float | None = None,
) -> dict[str, Any]:
    fee = estimate_option_fee_for_legs(legs)
    max_loss_est = float(fee.get("max_loss_estimate", 0.0))
    capital_usage = max(0.0, -float(fee.get("net_premium", 0.0))) + float(fee.get("total_fee", 0.0))

    blocks: list[dict[str, Any]] = []
    if max_loss_threshold is not None and max_loss_est > float(max_loss_threshold):
        blocks.append(
            {
                "rule": "max_loss_threshold",
                "reason": f"策略最大损失估算 {max_loss_est:.2f} 超过阈值 {float(max_loss_threshold):.2f}",
            }
        )
    if max_capital_usage is not None and capital_usage > float(max_capital_usage):
        blocks.append(
            {
                "rule": "capital_usage_limit",
                "reason": f"策略资金占用估算 {capital_usage:.2f} 超过限制 {float(max_capital_usage):.2f}",
            }
        )
    if available_cash >= 0 and capital_usage > available_cash:
        blocks.append(
            {
                "rule": "available_cash",
                "reason": f"可用资金 {available_cash:.2f} 不足以覆盖估算占用 {capital_usage:.2f}",
            }
        )
    return {
        "passed": len(blocks) == 0,
        "blocks": blocks,
        "max_loss_estimate": round(max_loss_est, 6),
        "capital_usage_estimate": round(capital_usage, 6),
        "fee_breakdown": fee.get("fee_breakdown", {}),
    }


def submit_option_order_with_risk(
    trade_ctx: Any,
    legs: list[OptionLeg],
    available_cash: float,
    max_loss_threshold: float | None = None,
    max_capital_usage: float | None = None,
) -> dict[str, Any]:
    risk = evaluate_option_risk(
        legs=legs,
        available_cash=available_cash,
        max_loss_threshold=max_loss_threshold,
        max_capital_usage=max_capital_usage,
    )
    if not risk.get("passed"):
        return {"ok": False, "blocked": True, "risk": risk}

    if len(legs) == 1:
        leg = legs[0]
        order = submit_option_single_leg(trade_ctx, leg.symbol, leg.side, leg.contracts, leg.price or None)
        return {"ok": True, "blocked": False, "mode": "single_leg", "order": order, "risk": risk}

    result = submit_option_multi_leg(trade_ctx, legs)
    if not result.get("ok"):
        return {"ok": False, "blocked": False, "mode": "multi_leg", "result": result, "risk": risk}
    return {"ok": True, "blocked": False, "mode": "multi_leg", "result": result, "risk": risk}


def fetch_option_expiries(quote_ctx: Any, symbol: str) -> dict[str, Any]:
    dates = broker_service.get_option_chain_expiry_dates(quote_ctx, symbol)
    return {"symbol": symbol, "expiries": [d.isoformat() for d in dates]}


def _float_from_quote_field(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _quote_timestamp_iso(v: Any) -> str | None:
    if v is None:
        return None
    try:
        if hasattr(v, "isoformat"):
            return v.isoformat()
    except Exception:
        pass
    return str(v) if v else None


def _fetch_quote_map(quote_ctx: Any, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Batch real-time quotes (LongPort max 500 symbols per request)."""
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        s = str(raw or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        ordered.append(s)
    out: dict[str, dict[str, Any]] = {}
    if not ordered:
        return out
    chunk = 500
    for i in range(0, len(ordered), chunk):
        batch = ordered[i : i + chunk]
        try:
            qs = broker_service.get_quotes(quote_ctx, batch)
        except Exception:
            continue
        if not qs:
            continue
        for q in qs:
            sym = str(getattr(q, "symbol", "") or "")
            if not sym:
                continue
            out[sym] = {
                "last_done": _float_from_quote_field(getattr(q, "last_done", None)),
                "prev_close": _float_from_quote_field(getattr(q, "prev_close", None)),
                "open": _float_from_quote_field(getattr(q, "open", None)),
                "high": _float_from_quote_field(getattr(q, "high", None)),
                "low": _float_from_quote_field(getattr(q, "low", None)),
                "volume": int(getattr(q, "volume", 0) or 0),
                "timestamp": _quote_timestamp_iso(getattr(q, "timestamp", None)),
            }
    return out


def _depth_first_bid_price(quote_ctx: Any, symbol: str) -> float | None:
    """LongPort `depth`：取 bid 档位第一档价格（买一）。"""
    try:
        dep = broker_service.get_depth(quote_ctx, str(symbol).strip())
    except Exception:
        return None
    if dep is None:
        return None
    bids = getattr(dep, "bid", None) or getattr(dep, "bids", None) or []
    if not bids:
        return None
    b0 = bids[0]
    raw = getattr(b0, "price", None)
    if raw is None and isinstance(b0, dict):
        raw = b0.get("price")
    if raw is None:
        return None
    v = _float_from_quote_field(raw)
    return float(v) if v is not None and v > 0 else None


def _quote_object_first_positive_bid(q0: Any) -> float | None:
    for attr in ("bid_price", "best_bid", "bid"):
        if hasattr(q0, attr):
            v = _float_from_quote_field(getattr(q0, attr))
            if v is not None and v > 0:
                return float(v)
    return None


def _depth_first_ask_price(quote_ctx: Any, symbol: str) -> float | None:
    """LongPort `depth`：取 ask 档位第一档价格（卖一）。"""
    try:
        dep = broker_service.get_depth(quote_ctx, str(symbol).strip())
    except Exception:
        return None
    if dep is None:
        return None
    asks = getattr(dep, "ask", None) or getattr(dep, "asks", None) or []
    if not asks:
        return None
    a0 = asks[0]
    raw = getattr(a0, "price", None)
    if raw is None and isinstance(a0, dict):
        raw = a0.get("price")
    if raw is None:
        return None
    v = _float_from_quote_field(raw)
    return float(v) if v is not None and v > 0 else None


def _quote_object_first_positive_ask(q0: Any) -> float | None:
    for attr in ("ask_price", "best_ask", "ask"):
        if hasattr(q0, attr):
            v = _float_from_quote_field(getattr(q0, attr))
            if v is not None and v > 0:
                return float(v)
    return None


def fetch_option_best_bid(quote_ctx: Any, symbol: str) -> tuple[float | None, str]:
    """
    实盘平仓用：优先取期权实时买一（depth 第一档 bid），其次 quote/option_quote 对象上的 bid 字段，
    最后回退到通用 quote 的 last_done（需已开通期权行情权限）。
    返回 (price_or_none, source_tag)。
    """
    sym = str(symbol or "").strip().upper()
    if not sym or quote_ctx is None:
        return None, "none"

    b = _depth_first_bid_price(quote_ctx, sym)
    if b is not None:
        return b, "depth_bid"

    try:
        rows = broker_service.get_option_quotes(quote_ctx, [sym])
        if rows:
            v = _quote_object_first_positive_bid(rows[0])
            if v is not None:
                return v, "option_quote_bid"
    except Exception:
        pass

    try:
        qs = broker_service.get_quotes(quote_ctx, [sym])
        if qs:
            v = _quote_object_first_positive_bid(qs[0])
            if v is not None:
                return v, "quote_bid"
    except Exception:
        pass

    qmap = _fetch_quote_map(quote_ctx, [sym])
    row = qmap.get(sym) or {}
    ld = row.get("last_done")
    if ld is not None and float(ld) > 0:
        return float(ld), "last_done_fallback"
    return None, "none"


def fetch_option_best_ask(quote_ctx: Any, symbol: str) -> tuple[float | None, str]:
    """
    实盘开仓（买）用：优先取期权实时卖一（depth 第一档 ask），其次 option_quote / quote 上的 ask 字段，
    最后回退 last_done。
    """
    sym = str(symbol or "").strip().upper()
    if not sym or quote_ctx is None:
        return None, "none"

    a = _depth_first_ask_price(quote_ctx, sym)
    if a is not None:
        return a, "depth_ask"

    try:
        rows = broker_service.get_option_quotes(quote_ctx, [sym])
        if rows:
            v = _quote_object_first_positive_ask(rows[0])
            if v is not None:
                return v, "option_quote_ask"
    except Exception:
        pass

    try:
        qs = broker_service.get_quotes(quote_ctx, [sym])
        if qs:
            v = _quote_object_first_positive_ask(qs[0])
            if v is not None:
                return v, "quote_ask"
    except Exception:
        pass

    qmap = _fetch_quote_map(quote_ctx, [sym])
    row = qmap.get(sym) or {}
    ld = row.get("last_done")
    if ld is not None and float(ld) > 0:
        return float(ld), "last_done_fallback"
    return None, "none"


def _attach_option_chain_quotes(rows: list[dict[str, Any]], quote_ctx: Any) -> None:
    syms: list[str] = []
    for row in rows:
        cs = row.get("call_symbol")
        ps = row.get("put_symbol")
        if cs:
            syms.append(str(cs))
        if ps:
            syms.append(str(ps))
    qmap = _fetch_quote_map(quote_ctx, syms)
    for row in rows:
        cs = row.get("call_symbol")
        ps = row.get("put_symbol")
        row["call_quote"] = qmap.get(str(cs)) if cs else None
        row["put_quote"] = qmap.get(str(ps)) if ps else None


def fetch_option_chain(
    quote_ctx: Any,
    symbol: str,
    expiry_date: str | None = None,
    min_strike: float | None = None,
    max_strike: float | None = None,
    standard_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    expiries = broker_service.get_option_chain_expiry_dates(quote_ctx, symbol)
    if not expiries:
        return {"symbol": symbol, "expiries": [], "options": []}
    if expiry_date:
        target = date.fromisoformat(expiry_date)
    else:
        target = min(expiries)
    rows: list[dict[str, Any]] = []
    for item in broker_service.get_option_chain_by_date(quote_ctx, symbol, target):
        strike = float(item.price) if getattr(item, "price", None) is not None else None
        standard = bool(getattr(item, "standard", False))
        if standard_only and not standard:
            continue
        if min_strike is not None and strike is not None and strike < float(min_strike):
            continue
        if max_strike is not None and strike is not None and strike > float(max_strike):
            continue
        rows.append(
            {
                "expiry_date": target.isoformat(),
                "strike_price": strike,
                "call_symbol": getattr(item, "call_symbol", None),
                "put_symbol": getattr(item, "put_symbol", None),
                "standard": standard,
            }
        )
    rows.sort(key=lambda x: (x["strike_price"] is None, x["strike_price"]))
    total = len(rows)
    lim = max(1, min(int(limit), 500))
    off = max(0, int(offset))
    data = rows[off : off + lim]
    _attach_option_chain_quotes(data, quote_ctx)
    return {
        "symbol": symbol,
        "expiry_date": target.isoformat(),
        "expiries": [d.isoformat() for d in expiries],
        "pagination": {"offset": off, "limit": lim, "total": total, "has_more": (off + lim) < total},
        "options": data,
    }


def submit_option_single_leg(
    trade_ctx: Any,
    symbol: str,
    side: OptionSide,
    contracts: int,
    price: float | None = None,
) -> dict[str, Any]:
    from longbridge.openapi import OrderSide, OrderType, TimeInForceType

    if contracts <= 0:
        raise ValueError("contracts 必须 > 0")
    resp = broker_service.submit_order(
        trade_ctx,
        symbol=symbol,
        order_type=OrderType.LO if price else OrderType.MO,
        side=OrderSide.Buy if side == "buy" else OrderSide.Sell,
        submitted_quantity=contracts,
        time_in_force=TimeInForceType.Day,
        submitted_price=(None if not price else Decimal(str(price))),
    )
    return {"order_id": resp.order_id, "symbol": symbol, "side": side, "contracts": contracts, "price": price}


def submit_option_multi_leg(
    trade_ctx: Any,
    legs: list[OptionLeg],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for leg in legs:
        try:
            results.append(submit_option_single_leg(trade_ctx, leg.symbol, leg.side, leg.contracts, leg.price or None))
        except Exception as e:
            errors.append({"symbol": leg.symbol, "error": str(e)})
            break
    return {"ok": len(errors) == 0, "legs_submitted": results, "errors": errors}


def get_option_positions(trade_ctx: Any, quote_ctx: Any) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for ch in broker_service.get_stock_positions(trade_ctx).channels:
        for pos in ch.positions:
            symbol = str(pos.symbol)
            if not _is_option_symbol(symbol):
                continue
            cur = 0.0
            try:
                q = broker_service.get_quotes(quote_ctx, [symbol])
                if q:
                    cur = float(q[0].last_done)
            except Exception:
                cur = 0.0
            qty = float(pos.quantity)
            cost = float(pos.cost_price)
            pnl = qty * (cur - cost)
            items.append(
                {
                    "symbol": symbol,
                    "quantity": qty,
                    "cost_price": cost,
                    "current_price": cur,
                    "pnl": round(pnl, 2),
                }
            )
    return {"positions": items, "count": len(items)}


def _order_float(row: Any, *names: str) -> float:
    for name in names:
        val = getattr(row, name, None)
        if val not in (None, ""):
            return _to_float(val, 0.0)
    raw = getattr(row, "raw", None)
    if isinstance(raw, dict):
        for name in names:
            val = raw.get(name)
            if val not in (None, ""):
                return _to_float(val, 0.0)
    return 0.0


def get_option_orders(trade_ctx: Any, status: str = "all") -> dict[str, Any]:
    allowed = {"active": {"New", "PartialFilled"}, "filled": {"Filled"}, "cancelled": {"Canceled"}}.get(status)
    orders: list[dict[str, Any]] = []
    for o in broker_service.get_today_orders(trade_ctx):
        symbol = str(o.symbol)
        if not _is_option_symbol(symbol):
            continue
        s = str(o.status)
        if allowed and s not in allowed:
            continue
        orders.append(
            {
                "order_id": o.order_id,
                "symbol": symbol,
                "side": str(o.side),
                "quantity": float(o.quantity),
                "price": float(o.price) if o.price else None,
                "status": s,
                "filled_quantity": _order_float(
                    o,
                    "filled_quantity",
                    "filled_qty",
                    "executed_quantity",
                    "dealt_quantity",
                    "filledQuantity",
                    "filledQty",
                ),
                "avg_fill_price": _order_float(
                    o,
                    "avg_fill_price",
                    "average_fill_price",
                    "executed_price",
                    "dealt_avg_price",
                    "filledPrice",
                    "avgFilledPrice",
                ),
            }
        )
    return {"orders": orders, "count": len(orders)}


def _normalize_side(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if "buy" in s:
        return "buy"
    if "sell" in s:
        return "sell"
    return s


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _to_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except Exception:
        return default


def _to_date_key(ts: Any, tz_name: str) -> str:
    sec = _to_int(ts, 0)
    if sec <= 0:
        return ""
    try:
        tz = ZoneInfo(tz_name or "America/New_York")
    except Exception:
        tz = timezone.utc
    dt = datetime.fromtimestamp(sec, tz)
    return dt.date().isoformat()


def _extract_charge_total_amount(detail_obj: Any) -> float:
    cd = getattr(detail_obj, "charge_detail", None)
    if cd is None and isinstance(detail_obj, dict):
        cd = detail_obj.get("charge_detail")
    if cd is None:
        return 0.0
    if isinstance(cd, dict):
        return max(0.0, _to_float(cd.get("total_amount"), 0.0))
    return max(0.0, _to_float(getattr(cd, "total_amount", 0.0), 0.0))


def _iter_option_orders_for_range(trade_ctx: Any) -> list[Any]:
    """
    尽可能获取可用期权订单集合：
    1) today_orders（稳定）
    2) history_orders（若 SDK 版本支持）
    """
    out: list[Any] = []
    seen: set[str] = set()

    def _add(rows: Any) -> None:
        if not rows:
            return
        for o in rows:
            oid = str(getattr(o, "order_id", "") or "")
            sym = str(getattr(o, "symbol", "") or "")
            if not oid or oid in seen or not _is_option_symbol(sym):
                continue
            seen.add(oid)
            out.append(o)

    try:
        _add(broker_service.get_today_orders(trade_ctx))
    except Exception:
        pass

    fn = getattr(trade_ctx, "history_orders", None)
    if callable(fn):
        for call in (
            lambda: fn(),
            lambda: fn(status="Filled"),
        ):
            try:
                _add(call())
                break
            except Exception:
                continue
    return out


def _iter_option_executions_for_range(
    trade_ctx: Any,
    *,
    from_date: date,
    to_date: date,
) -> list[dict[str, Any]]:
    """
    直接读取成交明细（history/today），用于订单接口不可用时的兜底。
    """
    out: list[dict[str, Any]] = []
    hist_fn = getattr(trade_ctx, "history_executions", None)
    today_fn = getattr(trade_ctx, "today_executions", None)

    def _norm_row(x: Any) -> dict[str, Any] | None:
        sym = str(getattr(x, "symbol", "") or (x.get("symbol") if isinstance(x, dict) else "")).strip().upper()
        if not _is_option_symbol(sym):
            return None
        side = _normalize_side(getattr(x, "side", "") or (x.get("side") if isinstance(x, dict) else ""))
        qty = max(0, _to_int(getattr(x, "quantity", None), _to_int((x or {}).get("quantity"), 0)))
        price = max(0.0, _to_float(getattr(x, "price", None), _to_float((x or {}).get("price"), 0.0)))
        ts = _to_int(getattr(x, "time", None), _to_int((x or {}).get("time"), 0))
        if ts <= 0:
            ts = _to_int(getattr(x, "created_at", None), _to_int((x or {}).get("created_at"), 0))
        if not sym or side not in {"buy", "sell"} or qty <= 0 or price <= 0 or ts <= 0:
            return None
        return {"symbol": sym, "side": side, "qty": qty, "price": price, "ts": ts}

    if callable(hist_fn):
        start_dt = datetime.combine(from_date, datetime.min.time())
        end_dt = datetime.combine(to_date + timedelta(days=1), datetime.min.time()) - timedelta(seconds=1)
        for call in (
            lambda: hist_fn(start_at=start_dt, end_at=end_dt),
            lambda: hist_fn(start_at=int(start_dt.replace(tzinfo=timezone.utc).timestamp()), end_at=int(end_dt.replace(tzinfo=timezone.utc).timestamp())),
            lambda: hist_fn(),
        ):
            try:
                rows = call() or []
                for r in rows:
                    n = _norm_row(r)
                    if n:
                        out.append(n)
                if out:
                    break
            except Exception:
                continue

    if callable(today_fn):
        try:
            rows = today_fn() or []
            for r in rows:
                n = _norm_row(r)
                if n:
                    out.append(n)
        except Exception:
            pass
    return out


def _iter_option_executions_from_worker_logs(
    *,
    from_date: date,
    to_date: date,
    tz_name: str,
    symbol_query: str | None = None,
) -> list[dict[str, Any]]:
    """
    从 0DTE / 1DTE worker 决策尾日志提取期权成交（提交价口径）。
    仅在 API 不可用时兜底。
    """
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    files = [
        (
            os.path.join(root, "data", "qqq_0dte", "live_worker_decision_tail.jsonl"),
            os.path.join(root, "data", "qqq_0dte", "live_worker_execution_ledger.jsonl"),
        ),
        (
            os.path.join(root, "data", "qqq_1dte", "live_worker_decision_tail.jsonl"),
            os.path.join(root, "data", "qqq_1dte", "live_worker_execution_ledger.jsonl"),
        ),
    ]
    sym_q = str(symbol_query or "").strip().upper()
    seen_order_ids: set[str] = set()
    out: list[dict[str, Any]] = []
    pending_ledger_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def _parse_iso_ts(v: Any) -> int:
        s = str(v or "").strip()
        if not s:
            return 0
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except Exception:
            return 0

    def _append_leg(leg: dict[str, Any], ts: int, ledger_path: str, *, from_ledger: bool = False) -> None:
        oid = str(leg.get("order_id") or "").strip()
        sym = str(leg.get("symbol") or "").strip().upper()
        side = _normalize_side(leg.get("side"))
        qty = max(0, _to_int(leg.get("contracts"), 0))
        price = max(0.0, _to_float(leg.get("price"), 0.0))
        if not oid or oid in seen_order_ids:
            return
        if not sym or not _is_option_symbol(sym):
            return
        if sym_q and sym_q not in sym:
            return
        if side not in {"buy", "sell"} or qty <= 0 or price <= 0 or ts <= 0:
            return
        day_key = _to_date_key(ts, tz_name)
        if not day_key or not (from_date.isoformat() <= day_key <= to_date.isoformat()):
            return
        seen_order_ids.add(oid)
        out.append({"symbol": sym, "side": side, "qty": qty, "price": price, "ts": ts})
        if not from_ledger:
            pending_ledger_rows[ledger_path].append(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "order_id": oid,
                    "symbol": sym,
                    "side": side,
                    "contracts": qty,
                    "price": price,
                    "ts": ts,
                }
            )

    def _parse_ledger(ledger_fp: str) -> None:
        if not os.path.isfile(ledger_fp):
            return
        try:
            with open(ledger_fp, "r", encoding="utf-8") as f:
                for line in f:
                    ln = line.strip()
                    if not ln:
                        continue
                    try:
                        row = json.loads(ln)
                    except Exception:
                        continue
                    if not isinstance(row, dict):
                        continue
                    ts = _to_int(row.get("ts"), 0)
                    if ts <= 0:
                        ts = _parse_iso_ts(row.get("at"))
                    _append_leg(
                        {
                            "order_id": row.get("order_id"),
                            "symbol": row.get("symbol"),
                            "side": row.get("side"),
                            "contracts": row.get("contracts"),
                            "price": row.get("price"),
                        },
                        ts,
                        ledger_fp,
                        from_ledger=True,
                    )
        except Exception:
            return

    for _, ledger_fp in files:
        _parse_ledger(ledger_fp)

    for fp, ledger_fp in files:
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    ln = line.strip()
                    if not ln:
                        continue
                    try:
                        row = json.loads(ln)
                    except Exception:
                        continue
                    if not isinstance(row, dict):
                        continue
                    action = row.get("action") if isinstance(row.get("action"), dict) else {}
                    if not action or not bool(action.get("ok", False)):
                        continue
                    detail = action.get("detail") if isinstance(action.get("detail"), dict) else {}
                    order = detail.get("order") if isinstance(detail.get("order"), dict) else {}
                    ts = _parse_iso_ts(row.get("at"))
                    mode = str(order.get("mode") or "").strip().lower()
                    if mode == "single_leg":
                        single = order.get("order") if isinstance(order.get("order"), dict) else {}
                        _append_leg(single, ts, ledger_fp)
                    elif mode == "multi_leg":
                        result = order.get("result") if isinstance(order.get("result"), dict) else {}
                        legs = result.get("legs_submitted") if isinstance(result.get("legs_submitted"), list) else []
                        for leg in legs:
                            if isinstance(leg, dict):
                                _append_leg(leg, ts, ledger_fp)
        except Exception:
            continue

    for ledger_fp, rows in pending_ledger_rows.items():
        if not rows:
            continue
        try:
            parent = os.path.dirname(ledger_fp)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(ledger_fp, "a", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False, default=str))
                    f.write("\n")
        except Exception:
            continue
    return out


def _estimate_fee_per_contract(side: str, contracts: int) -> float:
    qty = max(1, int(contracts))
    try:
        est = estimate_us_option_order_fee(side=("sell" if str(side).lower() == "sell" else "buy"), contracts=qty)
        total_fee = float((est or {}).get("total_fee") or 0.0)
        return max(0.0, total_fee / qty)
    except Exception:
        return 0.0


def get_option_pnl_calendar(
    trade_ctx: Any,
    *,
    from_date: str,
    to_date: str,
    tz_name: str = "America/New_York",
    symbol_query: str | None = None,
) -> dict[str, Any]:
    """
    按日汇总期权已实现收益（FIFO）。
    数据源优先级：worker 日志（最快最可靠）> history_executions API > order_detail API。
    全程 try/except 保护，绝不向外抛出 500。
    """
    try:
        d0 = date.fromisoformat(from_date)
        d1 = date.fromisoformat(to_date)
    except Exception:
        d0 = date.today().replace(day=1)
        d1 = date.today()
    if d1 < d0:
        d0, d1 = d1, d0

    sym_q = str(symbol_query or "").strip().upper()

    # ── 第 1 层：worker 日志（最优先，不依赖任何 API）─────────────────────────
    executions: list[dict[str, Any]] = []
    execution_source = "-"
    log_exec_count = 0
    try:
        log_exec_rows = _iter_option_executions_from_worker_logs(
            from_date=d0,
            to_date=d1,
            tz_name=tz_name,
            symbol_query=sym_q or None,
        )
        for r in log_exec_rows:
            day_key = _to_date_key(r.get("ts"), tz_name)
            if not day_key:
                continue
            _side = str(r["side"])
            _qty = int(r["qty"])
            executions.append(
                {
                    "day": day_key,
                    "symbol": str(r["symbol"]),
                    "side": _side,
                    "qty": _qty,
                    "price": float(r["price"]),
                    "fee_per_contract": _estimate_fee_per_contract(_side, _qty),
                    "ts": int(r["ts"]),
                }
            )
        if executions:
            execution_source = "worker_logs"
            log_exec_count = len(executions)
    except Exception:
        pass

    seen_exec_keys: set[tuple[str, str, int, int, int]] = set()
    for e in executions:
        seen_exec_keys.add(
            (
                str(e.get("symbol", "")),
                str(e.get("side", "")),
                int(e.get("qty", 0)),
                int(round(float(e.get("price", 0.0)) * 10000)),
                int(e.get("ts", 0)),
            )
        )

    # ── 第 2 层：history_executions API（做增量补全）────────────────────────────
    api_exec_count = 0
    try:
        direct_exec_rows = _iter_option_executions_for_range(trade_ctx, from_date=d0, to_date=d1)
        for r in direct_exec_rows:
            day_key = _to_date_key(r.get("ts"), tz_name)
            if not day_key or not (d0.isoformat() <= day_key <= d1.isoformat()):
                continue
            key = (
                str(r["symbol"]),
                str(r["side"]),
                int(r["qty"]),
                int(round(float(r["price"]) * 10000)),
                int(r["ts"]),
            )
            if key in seen_exec_keys:
                continue
            seen_exec_keys.add(key)
            executions.append(
                {
                    "day": day_key,
                    "symbol": str(r["symbol"]),
                    "side": str(r["side"]),
                    "qty": int(r["qty"]),
                    "price": float(r["price"]),
                    "fee_per_contract": _estimate_fee_per_contract(str(r["side"]), int(r["qty"])),
                    "ts": int(r["ts"]),
                }
            )
            api_exec_count += 1
        if execution_source == "-" and executions:
            execution_source = "history_executions"
    except Exception:
        pass

    # ── 第 3 层：order_detail API（逐单拉取，最慢）────────────────────────────
    orders_scanned = 0
    details_loaded = 0
    fallback_executions = 0
    if not executions:
        try:
            all_orders = _iter_option_orders_for_range(trade_ctx)
            orders_scanned = len(all_orders)
            for o in all_orders:
                oid = str(getattr(o, "order_id", "") or "")
                if not oid:
                    continue
                try:
                    detail = broker_service.get_order_detail(trade_ctx, oid)
                except Exception:
                    continue
                try:
                    sym = str(getattr(detail, "symbol", "") or getattr(o, "symbol", "") or "").strip().upper()
                    if sym_q and sym_q not in sym:
                        continue
                    _side = _normalize_side(getattr(detail, "side", "") or getattr(o, "side", ""))
                    qty_total = max(0, _to_int(getattr(detail, "executed_quantity", 0), 0))
                    if qty_total <= 0 or not sym or _side not in {"buy", "sell"}:
                        continue
                    charge = _extract_charge_total_amount(detail)
                    fee_pc = charge / qty_total if qty_total > 0 else 0.0
                    details_loaded += 1
                    matched = 0
                    for h in list(getattr(detail, "history", None) or []):
                        try:
                            hs = str(getattr(h, "status", "") or (h.get("status") if isinstance(h, dict) else "")).lower()
                            if hs and ("filled" not in hs and "execut" not in hs and "partial" not in hs):
                                continue
                            q = max(0, _to_int(getattr(h, "quantity", None), _to_int((h or {}).get("quantity"), 0)))
                            p = _to_float(getattr(h, "price", None), _to_float((h or {}).get("price"), 0.0))
                            t = getattr(h, "time", None) or (h.get("time") if isinstance(h, dict) else None)
                            if q <= 0 or p <= 0:
                                continue
                            day_key = _to_date_key(t, tz_name)
                            if not day_key:
                                continue
                            executions.append({"day": day_key, "symbol": sym, "side": _side,
                                               "qty": q, "price": p, "fee_per_contract": fee_pc, "ts": _to_int(t, 0)})
                            matched += 1
                        except Exception:
                            continue
                    if matched == 0:
                        p2 = _to_float(getattr(detail, "executed_price", 0), 0.0)
                        t2 = _to_int(getattr(detail, "updated_at", 0), 0) or _to_int(getattr(detail, "submitted_at", 0), 0)
                        dk2 = _to_date_key(t2, tz_name)
                        if p2 > 0 and dk2:
                            executions.append({"day": dk2, "symbol": sym, "side": _side,
                                               "qty": qty_total, "price": p2, "fee_per_contract": fee_pc, "ts": t2})
                            fallback_executions += 1
                except Exception:
                    continue
            if executions:
                execution_source = "order_detail"
        except Exception:
            pass

    executions.sort(key=lambda x: (x["ts"], x["symbol"]))
    long_pos: dict[str, deque[dict[str, float]]] = defaultdict(deque)
    short_pos: dict[str, deque[dict[str, float]]] = defaultdict(deque)
    daily = defaultdict(lambda: {"realized_pnl": 0.0, "realized_cost": 0.0, "closed_contracts": 0, "trades": 0})
    daily_details: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for e in executions:
        sym = str(e["symbol"])
        side = str(e["side"])
        qty = int(e["qty"])
        px = float(e["price"])
        fee_pc = float(e["fee_per_contract"])
        day = str(e["day"])
        if not (d0.isoformat() <= day <= d1.isoformat()):
            continue
        if side == "buy":
            remain = qty
            while remain > 0 and short_pos[sym]:
                lot = short_pos[sym][0]
                m = min(remain, int(lot["qty"]))
                pnl = (float(lot["price"]) - px) * m * 100.0 - (float(lot["fee_pc"]) + fee_pc) * m
                cost = float(lot["price"]) * m * 100.0 + float(lot["fee_pc"]) * m
                daily[day]["realized_pnl"] += pnl
                daily[day]["realized_cost"] += max(0.0, cost)
                daily[day]["closed_contracts"] += m
                daily[day]["trades"] += 1
                daily_details[day].append(
                    {
                        "symbol": sym,
                        "close_side": "buy",
                        "contracts": int(m),
                        "entry_price": round(float(lot["price"]), 6),
                        "exit_price": round(px, 6),
                        "entry_fee_per_contract": round(float(lot["fee_pc"]), 6),
                        "exit_fee_per_contract": round(fee_pc, 6),
                        "realized_pnl": round(pnl, 6),
                    }
                )
                lot["qty"] -= m
                remain -= m
                if lot["qty"] <= 0:
                    short_pos[sym].popleft()
            if remain > 0:
                long_pos[sym].append({"qty": float(remain), "price": px, "fee_pc": fee_pc})
        else:  # sell
            remain = qty
            while remain > 0 and long_pos[sym]:
                lot = long_pos[sym][0]
                m = min(remain, int(lot["qty"]))
                pnl = (px - float(lot["price"])) * m * 100.0 - (float(lot["fee_pc"]) + fee_pc) * m
                cost = float(lot["price"]) * m * 100.0 + float(lot["fee_pc"]) * m
                daily[day]["realized_pnl"] += pnl
                daily[day]["realized_cost"] += max(0.0, cost)
                daily[day]["closed_contracts"] += m
                daily[day]["trades"] += 1
                daily_details[day].append(
                    {
                        "symbol": sym,
                        "close_side": "sell",
                        "contracts": int(m),
                        "entry_price": round(float(lot["price"]), 6),
                        "exit_price": round(px, 6),
                        "entry_fee_per_contract": round(float(lot["fee_pc"]), 6),
                        "exit_fee_per_contract": round(fee_pc, 6),
                        "realized_pnl": round(pnl, 6),
                    }
                )
                lot["qty"] -= m
                remain -= m
                if lot["qty"] <= 0:
                    long_pos[sym].popleft()
            if remain > 0:
                short_pos[sym].append({"qty": float(remain), "price": px, "fee_pc": fee_pc})

    rows: list[dict[str, Any]] = []
    cursor = d0
    total_pnl = 0.0
    total_cost = 0.0
    while cursor <= d1:
        k = cursor.isoformat()
        item = daily.get(k) or {"realized_pnl": 0.0, "realized_cost": 0.0, "closed_contracts": 0, "trades": 0}
        pnl = float(item["realized_pnl"])
        cost = float(item["realized_cost"])
        ret = (pnl / cost * 100.0) if cost > 0 else None
        rows.append(
            {
                "date": k,
                "realized_pnl": round(pnl, 4),
                "realized_return_pct": round(ret, 4) if ret is not None else None,
                "closed_contracts": int(item["closed_contracts"]),
                "trades": int(item["trades"]),
            }
        )
        total_pnl += pnl
        total_cost += cost
        cursor += timedelta(days=1)
    total_ret = (total_pnl / total_cost * 100.0) if total_cost > 0 else None
    return {
        "from_date": d0.isoformat(),
        "to_date": d1.isoformat(),
        "tz": tz_name,
        "symbol_query": sym_q or None,
        "days": rows,
        "details_by_date": {k: v for k, v in daily_details.items()},
        "debug": {
            "orders_scanned": int(orders_scanned),
            "order_details_loaded": int(details_loaded),
            "executions_parsed": len(executions),
            "api_executions_added": int(api_exec_count),
            "fallback_executions": int(fallback_executions),
            "execution_source": execution_source,
            "log_executions": int(log_exec_count),
        },
        "summary": {
            "total_realized_pnl": round(total_pnl, 4),
            "total_realized_return_pct": round(total_ret, 4) if total_ret is not None else None,
            "total_closed_contracts": int(sum(int(x["closed_contracts"]) for x in rows)),
            "total_trades": int(sum(int(x["trades"]) for x in rows)),
        },
        "note": "按订单详情成交历史(FIFO)估算已实现收益；若券商接口仅返回当日订单，则历史日期可能为空。",
    }


def run_option_backtest(
    symbol: str,
    template: str,
    holding_bars: int = 20,
    contracts: int = 1,
    width_pct: float = 0.05,
    *,
    bars: list[Any] | None = None,
    fetch_bars_fn: Callable[[str, int], list[Any]] | None = None,
    days: int = 180,
    kline: str | None = None,
    periods: int | None = None,
) -> dict[str, Any]:
    if bars is not None:
        _bars = bars
    else:
        if fetch_bars_fn is None:
            raise ValueError("期权回测需要提供 K 线 bars 或 fetch_bars_fn")
        _bars = fetch_bars_fn(symbol, days)
    step = max(2, int(holding_bars))
    min_len = max(step + 2, 20)
    if len(_bars) < min_len:
        raise ValueError(f"历史数据不足（需要至少 {min_len} 根 K 线，当前 {len(_bars)}），无法进行期权回测")
    closes = [float(b.close) for b in _bars]
    dates = [str(b.date) for b in _bars]

    template_key = str(template or "").strip().lower()
    supported = {"bull_call_spread", "bear_put_spread", "straddle", "strangle"}
    if template_key not in supported:
        raise ValueError(f"不支持模板 {template}，可选: {', '.join(sorted(supported))}")

    trade_rows: list[dict[str, Any]] = []
    fee_total = 0.0
    fee_breakdown: dict[str, float] = {}
    qty = max(1, int(contracts))
    width = max(0.01, float(width_pct))

    def _add_fee(parts: dict[str, Any]) -> float:
        fee = float(parts.get("total_fee", 0.0))
        for k, v in (parts.get("fee_breakdown", {}) or {}).items():
            fee_breakdown[k] = fee_breakdown.get(k, 0.0) + float(v)
        return fee

    for i in range(0, len(closes) - step, step):
        s0 = closes[i]
        s1 = closes[i + step]
        d0 = dates[i]
        d1 = dates[i + step]
        gross_per_share = 0.0
        premium_per_share = 0.0
        legs_count = 2

        if template_key == "bull_call_spread":
            k1 = s0
            k2 = s0 * (1 + width)
            premium_per_share = s0 * 0.04
            gross_per_share = max(0.0, s1 - k1) - max(0.0, s1 - k2) - premium_per_share
        elif template_key == "bear_put_spread":
            k1 = s0
            k2 = s0 * (1 - width)
            premium_per_share = s0 * 0.04
            gross_per_share = max(0.0, k1 - s1) - max(0.0, k2 - s1) - premium_per_share
        elif template_key == "straddle":
            premium_per_share = s0 * 0.08
            gross_per_share = abs(s1 - s0) - premium_per_share
        else:  # strangle
            kc = s0 * (1 + width / 2.0)
            kp = s0 * (1 - width / 2.0)
            premium_per_share = s0 * 0.06
            gross_per_share = max(0.0, s1 - kc) + max(0.0, kp - s1) - premium_per_share

        gross = gross_per_share * 100.0 * qty
        fee = _add_fee(
            estimate_us_option_multi_leg_fee(
                [{"side": "buy", "contracts": qty, "price": 0.0} for _ in range(legs_count * 2)]
            )
        )
        fee_total += fee
        net = gross - fee
        trade_rows.append(
            {
                "entry_date": d0,
                "exit_date": d1,
                "entry_spot": round(s0, 4),
                "exit_spot": round(s1, 4),
                "gross_pnl": round(gross, 4),
                "fee": round(fee, 4),
                "net_pnl": round(net, 4),
            }
        )

    total_net = sum(x["net_pnl"] for x in trade_rows)
    wins = [x for x in trade_rows if x["net_pnl"] > 0]
    losses = [x for x in trade_rows if x["net_pnl"] <= 0]
    initial_capital = 100000.0
    total_return_pct = (total_net / initial_capital) * 100.0
    return {
        "symbol": symbol,
        "template": template_key,
        "days": days,
        "periods": int(periods or 0),
        "kline": str(kline or "1d"),
        "holding_bars": step,
        "holding_days": step,
        "contracts": qty,
        "trades": trade_rows,
        "stats": {
            "total_trades": len(trade_rows),
            "win_rate_pct": round((len(wins) / len(trade_rows) * 100.0) if trade_rows else 0.0, 2),
            "total_net_pnl": round(total_net, 4),
            "total_return_pct": round(total_return_pct, 4),
            "total_fee": round(fee_total, 4),
            "fee_breakdown": {k: round(v, 4) for k, v in fee_breakdown.items()},
            "avg_win": round(sum(x["net_pnl"] for x in wins) / len(wins), 4) if wins else 0.0,
            "avg_loss": round(sum(x["net_pnl"] for x in losses) / len(losses), 4) if losses else 0.0,
        },
    }
