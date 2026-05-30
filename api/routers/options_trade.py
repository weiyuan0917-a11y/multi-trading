from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(tags=["trading-interfaces"])

REMOVED_REASON = "trading_execution_removed_from_customer_public_source"


def _disabled(path: str) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail={
            "ok": False,
            "disabled": True,
            "reason": REMOVED_REASON,
            "path": path,
            "message": "The customer public source keeps the API interface only; trading execution is not included.",
        },
    )


@router.api_route("/trade/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def trade_interface_removed(path: str, request: Request) -> dict[str, Any]:
    return _disabled(str(request.url.path))


@router.api_route("/options/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def options_interface_removed(path: str, request: Request) -> dict[str, Any]:
    return _disabled(str(request.url.path))
