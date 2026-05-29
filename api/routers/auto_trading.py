from __future__ import annotations

from fastapi import APIRouter, HTTPException


router = APIRouter(prefix="/auto-trading", tags=["auto-trading"])


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def auto_trading_removed(path: str) -> dict[str, str]:
    raise HTTPException(status_code=410, detail={"reason": "auto_trading_removed", "path": path})
