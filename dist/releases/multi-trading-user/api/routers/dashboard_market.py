from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header

from api import runtime_bridge as rt
from api.routers.local_owner import require_local_identity

router = APIRouter(tags=["dashboard-market"])


def _optional_owner_id(authorization: str | None, x_local_owner: str | None) -> str | None:
    if not authorization and not x_local_owner:
        return None
    return require_local_identity(authorization, x_local_owner).owner_id


@router.get("/dashboard/summary")
def dashboard_summary(
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    return rt.dashboard_summary(owner_id=_optional_owner_id(authorization, x_local_owner))


@router.get("/market/analysis")
def market_analysis(
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    return rt.market_analysis(owner_id=_optional_owner_id(authorization, x_local_owner))


@router.get("/market/sectors")
def market_sectors(
    days: int = 5,
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    return rt.market_sectors(days=days, owner_id=_optional_owner_id(authorization, x_local_owner))


@router.get("/signals")
def signals(symbol: str = "RXRX.US") -> dict[str, Any]:
    return rt.signals(symbol=symbol)

