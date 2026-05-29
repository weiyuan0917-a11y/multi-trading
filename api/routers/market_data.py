from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Query, HTTPException

from api import runtime_bridge as rt
from api.routers.local_owner import require_local_owner

router = APIRouter(prefix="/market-data", tags=["market-data"])


@router.get("/providers/status")
def market_data_provider_status(
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner = ""
    try:
        owner = require_local_owner(authorization, x_local_owner)
    except HTTPException:
        owner = ""
    return rt.market_data_provider_status(owner_id=owner)


@router.get("/public/providers/status")
def public_market_data_provider_status(
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner = ""
    try:
        owner = require_local_owner(authorization, x_local_owner)
    except HTTPException:
        owner = ""
    return rt.public_market_data_provider_status(owner_id=owner)


@router.get("/quote")
def public_market_quote(
    symbols: str = Query(..., description="Comma separated symbols, e.g. SPY.US,QQQ.US,HSI.HK,000001.SH"),
    source: str = "auto",
) -> dict[str, Any]:
    return rt.public_market_data_quote(symbols=symbols, source=source)


@router.get("/klines")
def public_market_klines(
    symbol: str = Query(...),
    period: str = "1d",
    days: int = 180,
    limit: int = 0,
    source: str = "auto",
) -> dict[str, Any]:
    return rt.public_market_data_klines(symbol=symbol, period=period, days=days, limit=limit, source=source)


@router.get("/cn/quote")
def cn_market_quote(
    symbols: str = Query(..., description="Comma separated symbols, e.g. 600519.SH,300750.SZ"),
    source: str = "auto",
) -> dict[str, Any]:
    return rt.cn_market_data_quote(symbols=symbols, source=source)


@router.get("/cn/klines")
def cn_market_klines(
    symbol: str = Query(...),
    period: str = "1d",
    adjust: str = "qfq",
    days: int = 180,
    limit: int = 0,
    source: str = "auto",
) -> dict[str, Any]:
    return rt.cn_market_data_klines(
        symbol=symbol,
        period=period,
        adjust=adjust,
        days=days,
        limit=limit,
        source=source,
    )


@router.get("/cn/valuation")
def cn_market_valuation(
    symbol: str = Query(...),
    source: str = "auto",
) -> dict[str, Any]:
    return rt.cn_market_data_valuation(symbol=symbol, source=source)


@router.get("/cn/universe")
def cn_market_universe(market: str = "cn") -> dict[str, Any]:
    return rt.cn_market_data_universe(market=market)
