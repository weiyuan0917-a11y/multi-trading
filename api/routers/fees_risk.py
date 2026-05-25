from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Path, Query

from api import runtime_bridge as rt

router = APIRouter(tags=["fees-risk"])


@router.get("/fees/schedule")
def fees_schedule(broker_id: str | None = Query(None, description="查看指定券商的费用表；缺省为当前试算默认券商")) -> dict[str, Any]:
    return rt.fees_schedule(broker_id=broker_id)


@router.get("/fees/schedule/default")
def fees_schedule_default() -> dict[str, Any]:
    return rt.fees_schedule_default()


@router.post("/fees/schedule")
def fees_schedule_save(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.fees_schedule_save(body)


@router.get("/fees/brokers")
def fees_brokers_list() -> dict[str, Any]:
    return rt.fees_brokers_list()


@router.post("/fees/brokers")
def fees_brokers_create(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.fees_brokers_create(body)


@router.post("/fees/brokers/active")
def fees_brokers_set_active(
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """设置「未连接默认账户」时使用的费用模板（manual_fee_broker_id）；连接后仍以账户券商为准。"""
    return rt.fees_brokers_set_active(body)


@router.patch("/fees/brokers/{broker_id}")
def fees_brokers_patch_display_name(
    broker_id: str = Path(..., description="券商 ID"),
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    return rt.fees_brokers_patch_display_name(broker_id, body)


@router.delete("/fees/brokers/{broker_id}")
def fees_brokers_delete(broker_id: str = Path(..., description="要删除的券商 ID")) -> dict[str, Any]:
    return rt.fees_brokers_delete(broker_id)


@router.get("/fees/estimate")
def fees_estimate(
    asset_class: Literal["stock", "us_option"] = "stock",
    market: Literal["HK", "US", "CN", "OTHER"] = "US",
    side: Literal["buy", "sell"] = "buy",
    quantity: int = 100,
    price: float = 1.0,
) -> dict[str, Any]:
    return rt.fees_estimate(
        asset_class=asset_class,
        market=market,
        side=side,
        quantity=quantity,
        price=price,
    )


@router.get("/risk/config")
def risk_config() -> dict[str, Any]:
    return rt.risk_config()

