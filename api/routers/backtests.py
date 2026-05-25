from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from api import runtime_bridge as rt

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.get("")
def list_backtest_tasks(limit: int = Query(50, ge=1, le=200), kind: str | None = None) -> dict[str, Any]:
    return rt.backtests_list(limit=limit, kind=kind)


@router.post("")
def create_backtest_task(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.backtests_create(body)


@router.get("/{task_id}")
def get_backtest_task(task_id: str) -> dict[str, Any]:
    out = rt.backtests_get(task_id)
    if not out:
        raise HTTPException(status_code=404, detail="backtest_task_not_found")
    return out


@router.get("/{task_id}/events")
def get_backtest_task_events(task_id: str) -> dict[str, Any]:
    return rt.backtests_events(task_id)


@router.post("/{task_id}/cancel")
def cancel_backtest_task(task_id: str) -> dict[str, Any]:
    return rt.backtests_cancel(task_id)
