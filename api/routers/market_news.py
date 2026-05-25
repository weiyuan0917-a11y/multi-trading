from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Query

from api.routers.local_owner import require_local_identity
from api.services.market_news_service import get_market_news_feed

router = APIRouter(prefix="/market", tags=["market-news"])


def _optional_owner_id(authorization: str | None, x_local_owner: str | None) -> str | None:
    if not authorization and not x_local_owner:
        return None
    return require_local_identity(authorization, x_local_owner).owner_id


def _split_symbols(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in str(raw).split(",") if x.strip()]


@router.get("/news-feed")
def market_news_feed(
    account_id: str | None = None,
    symbols: str | None = Query(default=None, description="Comma separated symbols. Empty means current stock positions."),
    region: str = Query(default="all", description="all/global/china/us"),
    limit: int = Query(default=80, ge=10, le=120),
    refresh: bool = False,
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    owner_id = _optional_owner_id(authorization, x_local_owner)
    return get_market_news_feed(
        account_id=account_id,
        owner_id=owner_id,
        symbols=_split_symbols(symbols),
        region=region,
        limit=limit,
        refresh=refresh,
    )
