from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, Query

from api import runtime_bridge as rt
from api.routers.local_owner import require_entitlement, require_local_identity

router = APIRouter(prefix="/auto-trading", tags=["auto-trading"])


def _module_feature(module_id: str) -> str:
    return "stock_auto_trading" if str(module_id or "").strip().lower() == "stocks" else "option_auto_trading"


@router.get("/modules")
def auto_trading_modules() -> dict[str, Any]:
    return rt.auto_trading_modules()


@router.get("/status")
def auto_trading_status() -> dict[str, Any]:
    return rt.auto_trading_status()


@router.get("/{module_id}/status")
def auto_trading_module_status(module_id: str) -> dict[str, Any]:
    return rt.auto_trading_module_status(module_id)


@router.post("/{module_id}/start")
def auto_trading_module_start(
    module_id: str,
    body: dict[str, Any] | None = Body(None),
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
) -> dict[str, Any]:
    identity = require_entitlement(authorization, x_local_owner, _module_feature(module_id), x_api_key)
    return rt.auto_trading_module_start(module_id, body=body, owner_id=identity.owner_id)


@router.post("/{module_id}/stop")
def auto_trading_module_stop(
    module_id: str,
    body: dict[str, Any] | None = Body(None),
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
) -> dict[str, Any]:
    require_local_identity(authorization, x_local_owner, x_api_key)
    return rt.auto_trading_module_stop(module_id, body=body)


@router.post("/{module_id}/restart")
def auto_trading_module_restart(
    module_id: str,
    body: dict[str, Any] | None = Body(None),
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
) -> dict[str, Any]:
    identity = require_entitlement(authorization, x_local_owner, _module_feature(module_id), x_api_key)
    return rt.auto_trading_module_restart(module_id, body=body, owner_id=identity.owner_id)


@router.get("/{module_id}/risk-summary")
def auto_trading_module_risk_summary(module_id: str) -> dict[str, Any]:
    return rt.auto_trading_module_risk_summary(module_id)


@router.get("/{module_id}/events")
def auto_trading_module_events(module_id: str, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return rt.auto_trading_module_events(module_id, limit=limit)


@router.post("/{module_id}/confirm")
def auto_trading_module_confirm(
    module_id: str,
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
) -> dict[str, Any]:
    require_entitlement(authorization, x_local_owner, _module_feature(module_id), x_api_key)
    return rt.auto_trading_module_confirm(module_id, body=body)
