"""实盘：从期权链解析 0DTE（或指定到期）对应的 OPRA 代码与报价，与回测中的 strike/right 对齐。"""
from __future__ import annotations

from typing import Any, Literal

OptionRight = Literal["call", "put"]


def normalize_option_right(s: str) -> OptionRight:
    u = (s or "").strip().lower()
    if u in ("c", "call"):
        return "call"
    if u in ("p", "put"):
        return "put"
    raise ValueError(f"不识别的期权方向: {s!r}（支持 call/put/C/P）")


def _nearest_strike_row(options: list[dict[str, Any]], strike: float) -> tuple[dict[str, Any] | None, float]:
    best: dict[str, Any] | None = None
    best_d = 1e18
    for row in options or []:
        sp = row.get("strike_price")
        if sp is None:
            continue
        d = abs(float(sp) - float(strike))
        if d < best_d:
            best_d = d
            best = row
    return best, best_d


def _extract_quote_price(q: dict[str, Any] | None) -> float:
    if not q:
        return 0.0
    ld = q.get("last_done")
    if ld is None:
        return 0.0
    try:
        v = float(ld)
        return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def resolve_from_chain_payload(
    chain: dict[str, Any],
    strike: float,
    right: str,
    *,
    max_strike_diff: float = 1.5,
    quote_ctx: Any | None = None,
    use_bid_for_sell_limit: bool = False,
    use_ask_for_buy_limit: bool = False,
) -> dict[str, Any]:
    """
    从 ``fetch_option_chain`` 返回的 dict 中解析与目标行权价最近的 OPRA 与报价。
    use_bid_for_sell_limit / use_ask_for_buy_limit 需传 quote_ctx；二者勿同时为 true。
    """
    sym = str(chain.get("symbol") or "").strip().upper()
    exp = chain.get("expiry_date")
    options = chain.get("options") or []
    norm = normalize_option_right(right)
    row, diff = _nearest_strike_row(options, strike)
    if row is None:
        return {
            "ok": False,
            "error": "链中无有效行权价行",
            "underlying": sym,
            "expiry_date": exp,
        }
    if diff > float(max_strike_diff):
        return {
            "ok": False,
            "error": f"最近行权价与请求相差 {diff:.4f}，超过阈值 {max_strike_diff}",
            "underlying": sym,
            "expiry_date": exp,
            "nearest_strike": row.get("strike_price"),
            "strike_requested": float(strike),
        }
    sp = row.get("strike_price")
    if norm == "call":
        op = row.get("call_symbol")
        quote = row.get("call_quote")
    else:
        op = row.get("put_symbol")
        quote = row.get("put_quote")
    op_s = str(op).strip() if op else ""
    if not op_s:
        return {
            "ok": False,
            "error": f"该行权价缺少 {'call' if norm == 'call' else 'put'} 合约代码",
            "underlying": sym,
            "expiry_date": exp,
            "strike_price": sp,
            "right": norm,
        }
    qd = quote if isinstance(quote, dict) else None
    px = _extract_quote_price(qd)
    px_src = "chain_last_done"
    if use_bid_for_sell_limit and quote_ctx is not None and op_s:
        from mcp_server.options_service import fetch_option_best_bid

        bid_px, tag = fetch_option_best_bid(quote_ctx, op_s)
        if bid_px is not None and bid_px > 0:
            px = float(bid_px)
            px_src = tag
    elif use_ask_for_buy_limit and quote_ctx is not None and op_s:
        from mcp_server.options_service import fetch_option_best_ask

        ask_px, tag = fetch_option_best_ask(quote_ctx, op_s)
        if ask_px is not None and ask_px > 0:
            px = float(ask_px)
            px_src = tag
    return {
        "ok": True,
        "underlying": sym,
        "expiry_date": exp,
        "strike_requested": float(strike),
        "strike_price": float(sp) if sp is not None else None,
        "right": norm,
        "symbol": op_s.upper(),
        "quote": quote,
        "suggested_limit_price_per_share": px,
        "suggested_limit_price_source": px_src,
    }


def fetch_and_resolve_0dte_leg(
    quote_ctx: Any,
    underlying: str,
    strike: float,
    right: str,
    *,
    expiry_date: str | None = None,
    strike_window: float = 5.0,
    standard_only: bool = False,
    max_strike_diff: float = 1.5,
    use_bid_for_sell_limit: bool = False,
    use_ask_for_buy_limit: bool = False,
) -> dict[str, Any]:
    """拉取窄窗口期权链并解析单腿；需已登录当前券商 QuoteContext。"""
    from mcp_server.options_service import fetch_option_chain

    u = str(underlying or "").strip().upper()
    k = float(strike)
    w = max(0.5, float(strike_window))
    chain = fetch_option_chain(
        quote_ctx,
        u,
        expiry_date=expiry_date,
        min_strike=k - w,
        max_strike=k + w,
        standard_only=standard_only,
        limit=200,
        offset=0,
    )
    if not chain.get("options"):
        return {
            "ok": False,
            "error": "期权链为空（检查标的是否支持期权、到期日是否正确）",
            "underlying": u,
            "expiry_date": chain.get("expiry_date"),
            "expiries_available": chain.get("expiries") or [],
        }
    out = resolve_from_chain_payload(
        chain,
        k,
        right,
        max_strike_diff=max_strike_diff,
        quote_ctx=quote_ctx,
        use_bid_for_sell_limit=use_bid_for_sell_limit,
        use_ask_for_buy_limit=use_ask_for_buy_limit,
    )
    if out.get("ok"):
        out["chain_pagination"] = chain.get("pagination")
    return out
