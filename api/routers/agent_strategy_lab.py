from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/agent-strategy-lab", tags=["agent-strategy-lab"])

REMOVED_REASON = "auto_trading_strategy_lab_removed_from_customer_public_source"


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def agent_strategy_lab_interface_removed(path: str, request: Request) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail={
            "ok": False,
            "disabled": True,
            "reason": REMOVED_REASON,
            "path": str(request.url.path),
            "message": "The customer public source keeps the Strategy Lab API interface only; candidate generation and auto-trading approval logic are not included.",
        },
    )
