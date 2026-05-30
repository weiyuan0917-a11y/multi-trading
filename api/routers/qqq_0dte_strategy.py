from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(tags=["strategy-interfaces"])

REMOVED_REASON = "auto_trading_strategy_removed_from_customer_public_source"


@router.api_route("/strategy/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def strategy_interface_removed(path: str, request: Request) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail={
            "ok": False,
            "disabled": True,
            "reason": REMOVED_REASON,
            "path": str(request.url.path),
            "message": "The customer public source keeps the strategy API interface only; live strategy execution is not included.",
        },
    )
