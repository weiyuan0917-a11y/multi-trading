from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException

from api import runtime_bridge as rt
from api.services.user_auth_service import get_user_auth_service
from api.routers.local_owner import require_entitlement, require_local_identity

router = APIRouter(tags=["options-trade"])


def _extract_bearer(authorization: str | None) -> str:
    raw = str(authorization or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def _require_user(authorization: str | None, x_api_key: str | None, x_local_owner: str | None = None) -> str:
    """
    浏览器：Authorization: Bearer <session token>
    本机 Worker / 脚本：X-Api-Key: <api_key>（控制台「API Key」页生成）
    """
    return require_local_identity(authorization, x_local_owner, x_api_key).owner_id


def _require_feature(
    feature: str,
    authorization: str | None,
    x_api_key: str | None,
    x_local_owner: str | None = None,
) -> str:
    return require_entitlement(authorization, x_local_owner, feature, x_api_key).owner_id


@router.get("/trade/account")
def trade_account(
    account_id: str | None = None,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner_id = _require_user(authorization, x_api_key, x_local_owner)
    return rt.trade_account(account_id=account_id, owner_id=owner_id)


@router.get("/options/expiries")
def options_expiries(
    symbol: str,
    account_id: str | None = None,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner_id = _require_feature("option_trading", authorization, x_api_key, x_local_owner)
    return rt.options_expiries(symbol=symbol, account_id=account_id, owner_id=owner_id)


@router.get("/options/chain")
def options_chain(
    symbol: str,
    account_id: str | None = None,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
    expiry_date: str | None = None,
    min_strike: float | None = None,
    max_strike: float | None = None,
    standard_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    owner_id = _require_feature("option_trading", authorization, x_api_key, x_local_owner)
    return rt.options_chain(
        symbol=symbol,
        account_id=account_id,
        owner_id=owner_id,
        expiry_date=expiry_date,
        min_strike=min_strike,
        max_strike=max_strike,
        standard_only=standard_only,
        limit=limit,
        offset=offset,
    )


@router.post("/options/fee-estimate")
def options_fee_estimate(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    _require_feature("option_trading", authorization, x_api_key, x_local_owner)
    return rt.options_fee_estimate(body)


@router.post("/options/order")
def options_order(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner_id = _require_feature("option_trading", authorization, x_api_key, x_local_owner)
    return rt.options_order(body, owner_id=owner_id)


@router.post("/options/order/{order_id}/cancel")
def options_cancel_order(
    order_id: str,
    account_id: str | None = None,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner_id = _require_feature("option_trading", authorization, x_api_key, x_local_owner)
    return rt.trade_cancel_order(order_id=order_id, account_id=account_id, owner_id=owner_id)


@router.get("/options/orders")
def options_orders(
    status: str = "all",
    account_id: str | None = None,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner_id = _require_feature("option_trading", authorization, x_api_key, x_local_owner)
    return rt.options_orders(status=status, account_id=account_id, owner_id=owner_id)


@router.get("/options/positions")
def options_positions(
    account_id: str | None = None,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner_id = _require_feature("option_trading", authorization, x_api_key, x_local_owner)
    return rt.options_positions(account_id=account_id, owner_id=owner_id)


@router.get("/options/pnl-calendar")
def options_pnl_calendar(
    from_date: str,
    to_date: str,
    tz: str = "America/New_York",
    symbol: str | None = None,
    summary_only: bool = False,
    account_id: str | None = None,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner_id = _require_feature("option_trading", authorization, x_api_key, x_local_owner)
    return rt.options_pnl_calendar(
        from_date=from_date,
        to_date=to_date,
        tz=tz,
        symbol=symbol,
        summary_only=summary_only,
        account_id=account_id,
        owner_id=owner_id,
    )


@router.post("/options/backtest")
def options_backtest(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.options_backtest(body)


@router.post("/options/synthetic-path")
def options_synthetic_path(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """标的 K 线 + 滚动历史波动率 + BS 合成单腿或垂直价差理论价序列（无期权历史 K 线时使用）。"""
    return rt.options_synthetic_path(body)


@router.get("/trade/positions")
def trade_positions(
    account_id: str | None = None,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner_id = _require_user(authorization, x_api_key, x_local_owner)
    return rt.trade_positions(account_id=account_id, owner_id=owner_id)


@router.get("/trade/orders")
def trade_orders(
    status: str = "all",
    account_id: str | None = None,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner_id = _require_user(authorization, x_api_key, x_local_owner)
    return rt.trade_orders(status=status, account_id=account_id, owner_id=owner_id)


@router.get("/trade/order/{order_id}")
def trade_order_detail(
    order_id: str,
    account_id: str | None = None,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner_id = _require_user(authorization, x_api_key, x_local_owner)
    return rt.trade_order_detail(order_id=order_id, account_id=account_id, owner_id=owner_id)


@router.post("/trade/order")
def trade_submit_order(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner_id = _require_feature("stock_trading", authorization, x_api_key, x_local_owner)
    return rt.trade_submit_order(body, owner_id=owner_id)


@router.post("/trade/order/{order_id}/cancel")
def trade_cancel_order(
    order_id: str,
    account_id: str | None = None,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner_id = _require_feature("stock_trading", authorization, x_api_key, x_local_owner)
    return rt.trade_cancel_order(order_id=order_id, account_id=account_id, owner_id=owner_id)
