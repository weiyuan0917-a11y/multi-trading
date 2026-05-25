from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query

from api.routers.local_owner import require_entitlement
from api.services.agent_strategy_lab_service import (
    AgentStrategyLabError,
    approve_candidate,
    build_data_quality_report,
    create_lab_task,
    create_lab_run,
    get_lab_task,
    get_lab_run,
    lab_status,
    list_approvals,
    list_lab_tasks,
    list_lab_runs,
    normalize_instance,
    preview_candidate_diff,
    rollback_approval,
)

router = APIRouter(prefix="/agent-strategy-lab", tags=["agent-strategy-lab"])


def _http_error(exc: AgentStrategyLabError) -> HTTPException:
    detail = str(exc) or "agent_strategy_lab_error"
    if detail in {"run_not_found", "candidate_not_found", "task_not_found", "approval_not_found"}:
        return HTTPException(status_code=404, detail=detail)
    if detail in {"unsupported_instance", "candidate_validation_not_passed"}:
        return HTTPException(status_code=400, detail=detail)
    return HTTPException(status_code=500, detail=detail)


@router.get("/status")
def agent_strategy_lab_status(instance: str = Query("0dte")) -> dict[str, Any]:
    try:
        return lab_status(instance=normalize_instance(instance))
    except AgentStrategyLabError as exc:
        raise _http_error(exc) from exc


@router.get("/data-quality")
def agent_strategy_lab_data_quality(instance: str = Query("0dte")) -> dict[str, Any]:
    try:
        return build_data_quality_report(instance=normalize_instance(instance))
    except AgentStrategyLabError as exc:
        raise _http_error(exc) from exc


@router.get("/runs")
def agent_strategy_lab_runs(
    instance: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    try:
        return list_lab_runs(instance=normalize_instance(instance) if instance else None, limit=limit)
    except AgentStrategyLabError as exc:
        raise _http_error(exc) from exc


@router.post("/runs")
def agent_strategy_lab_create_run(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        return create_lab_run(body if isinstance(body, dict) else {})
    except AgentStrategyLabError as exc:
        raise _http_error(exc) from exc


@router.get("/tasks")
def agent_strategy_lab_tasks(
    instance: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    try:
        return list_lab_tasks(instance=normalize_instance(instance) if instance else None, limit=limit)
    except AgentStrategyLabError as exc:
        raise _http_error(exc) from exc


@router.post("/tasks")
def agent_strategy_lab_create_task(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        return create_lab_task(body if isinstance(body, dict) else {})
    except AgentStrategyLabError as exc:
        raise _http_error(exc) from exc


@router.get("/tasks/{task_id}")
def agent_strategy_lab_get_task(task_id: str) -> dict[str, Any]:
    try:
        return get_lab_task(task_id)
    except AgentStrategyLabError as exc:
        raise _http_error(exc) from exc


@router.get("/runs/{run_id}")
def agent_strategy_lab_get_run(run_id: str) -> dict[str, Any]:
    try:
        return get_lab_run(run_id)
    except AgentStrategyLabError as exc:
        raise _http_error(exc) from exc


@router.get("/runs/{run_id}/candidates/{candidate_id}/diff")
def agent_strategy_lab_candidate_diff(run_id: str, candidate_id: str) -> dict[str, Any]:
    try:
        return preview_candidate_diff(run_id, candidate_id)
    except AgentStrategyLabError as exc:
        raise _http_error(exc) from exc


@router.get("/approvals")
def agent_strategy_lab_approvals(
    instance: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    try:
        return list_approvals(instance=normalize_instance(instance) if instance else None, limit=limit)
    except AgentStrategyLabError as exc:
        raise _http_error(exc) from exc


@router.post("/approvals/rollback")
def agent_strategy_lab_rollback(
    body: dict[str, Any] = Body(default_factory=dict),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    payload = body if isinstance(body, dict) else {}
    try:
        return rollback_approval(
            str(payload.get("approval_id") or "") or None,
            instance=normalize_instance(payload.get("instance")),
        )
    except AgentStrategyLabError as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/approve")
def agent_strategy_lab_approve(
    run_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    payload = body if isinstance(body, dict) else {}
    try:
        return approve_candidate(
            run_id,
            str(payload.get("candidate_id") or ""),
            force=bool(payload.get("force")),
            approved_by=identity.owner_id,
        )
    except AgentStrategyLabError as exc:
        raise _http_error(exc) from exc
