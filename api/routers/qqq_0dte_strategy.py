from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Header, Query

from api import runtime_bridge as rt
from api.routers.local_owner import require_entitlement

router = APIRouter(tags=["qqq-0dte-strategy"])


@router.post("/strategy/qqq-0dte/backtest")
def qqq_0dte_backtest(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """QQQ 0DTE 模块化策略：标的 K 线 + BS 合成期权价回测。"""
    return rt.qqq_0dte_backtest(body)


@router.post("/strategy/qqq-0dte/matrix")
def qqq_0dte_matrix(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """同一批 K 线下对 grid 做笛卡尔积回测，返回按收益排序的 TOP N 组参数。"""
    return rt.qqq_0dte_matrix(body)


@router.post("/strategy/qqq-0dte/resolve-contract")
def qqq_0dte_resolve_contract(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    """按行权价与方向从 LongPort 期权链解析 OPRA 与参考价，供与回测 strike 对齐后下单。"""
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return rt.qqq_0dte_resolve_contract(body, owner_id=identity.owner_id)


@router.get("/strategy/qqq-0dte/strategy-recommendation")
def qqq_0dte_strategy_recommendation_get_route() -> dict[str, Any]:
    """系统推荐策略（Worker 约每 10 分钟扫描写入；只读展示，不参与下单）。"""
    return rt.qqq_0dte_strategy_recommendation_get()


@router.get("/strategy/qqq-0dte/live-worker-config")
def qqq_0dte_live_worker_config_get_route(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    """读取 `data/qqq_0dte/live_worker_config.json`（与实盘 Worker 共用），缺省字段由后端补齐。"""
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return {"config": rt.qqq_0dte_live_worker_config_get(owner_id=identity.owner_id)}


@router.put("/strategy/qqq-0dte/live-worker-config")
def qqq_0dte_live_worker_config_put_route(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    """写入实盘 Worker 配置文件；启动 Worker 前应先保存，以便子进程读到最新参数。"""
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return rt.qqq_0dte_live_worker_config_put(body if isinstance(body, dict) else {}, owner_id=identity.owner_id)


@router.post("/strategy/qqq-0dte/live-worker-manual-review/clear")
def qqq_0dte_live_worker_manual_review_clear_route(
    body: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return rt.qqq_0dte_live_worker_manual_review_lock_clear(
        body if isinstance(body, dict) else {},
        owner_id=identity.owner_id,
    )


@router.get("/strategy/qqq-0dte/live-worker-decision-tail")
def qqq_0dte_live_worker_decision_tail_route(
    limit: int = Query(20, ge=1, le=100),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    """实盘 Worker 每根 K 线决策摘要（JSONL 尾部）；用于排查未下单原因。"""
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return rt.qqq_0dte_live_worker_decision_tail_get(limit, owner_id=identity.owner_id)


@router.get("/strategy/qqq-1dte/strategy-recommendation")
def qqq_1dte_strategy_recommendation_get_route() -> dict[str, Any]:
    return rt.qqq_1dte_strategy_recommendation_get()


@router.get("/strategy/qqq-1dte/live-worker-config")
def qqq_1dte_live_worker_config_get_route(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return {"config": rt.qqq_1dte_live_worker_config_get(owner_id=identity.owner_id)}


@router.put("/strategy/qqq-1dte/live-worker-config")
def qqq_1dte_live_worker_config_put_route(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return rt.qqq_1dte_live_worker_config_put(body if isinstance(body, dict) else {}, owner_id=identity.owner_id)


@router.post("/strategy/qqq-1dte/live-worker-manual-review/clear")
def qqq_1dte_live_worker_manual_review_clear_route(
    body: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return rt.qqq_1dte_live_worker_manual_review_lock_clear(
        body if isinstance(body, dict) else {},
        owner_id=identity.owner_id,
    )


@router.get("/strategy/qqq-1dte/live-worker-decision-tail")
def qqq_1dte_live_worker_decision_tail_route(
    limit: int = Query(20, ge=1, le=100),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return rt.qqq_1dte_live_worker_decision_tail_get(limit, owner_id=identity.owner_id)


@router.get("/strategy/stock-options-swing/live-worker-config")
def stock_options_swing_config_get_route(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return {"config": rt.stock_options_swing_config_get(owner_id=identity.owner_id)}


@router.put("/strategy/stock-options-swing/live-worker-config")
def stock_options_swing_config_put_route(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return rt.stock_options_swing_config_put(body if isinstance(body, dict) else {}, owner_id=identity.owner_id)


@router.get("/strategy/stock-options-swing/live-worker-decision-tail")
def stock_options_swing_decision_tail_route(
    limit: int = Query(20, ge=1, le=100),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return rt.stock_options_swing_decision_tail_get(limit, owner_id=identity.owner_id)


@router.post("/strategy/stock-options-swing/position-action")
def stock_options_swing_position_action_route(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return rt.stock_options_swing_position_action(body if isinstance(body, dict) else {}, owner_id=identity.owner_id)


@router.post("/strategy/stock-options-swing/refresh-position-runtime")
def stock_options_swing_refresh_position_runtime_route(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_entitlement(authorization, x_local_owner, "option_auto_trading", x_api_key)
    return rt.stock_options_swing_refresh_positions_runtime(owner_id=identity.owner_id)


@router.get("/strategy/qqq-0dte/snapshots/top")
def qqq_0dte_snapshots_top(
    top: int = Query(5, ge=1, le=50, description="取前 N 条"),
    sort: Literal["realized_pnl", "return_pct"] = Query(
        "realized_pnl",
        description="realized_pnl=按已实现盈亏；return_pct=按盈亏率 return_pct（分母为累计开仓权利金；旧快照无该字段时排后）",
    ),
) -> dict[str, Any]:
    from mcp_server.strategy_qqq_0dte.snapshot_store import top_snapshots

    return top_snapshots(top_n=top, sort=sort)


@router.get("/strategy/qqq-0dte/snapshots/recent")
def qqq_0dte_snapshots_recent(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    from mcp_server.strategy_qqq_0dte.snapshot_store import list_snapshots

    return list_snapshots(limit=limit)
