from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException
from api import runtime_bridge as rt
from api.services.user_auth_service import get_user_auth_service
from api.routers.local_owner import require_identity_entitlement, require_local_identity, require_local_owner

router = APIRouter(tags=["setup"])


def _extract_bearer(authorization: str | None) -> str:
    raw = str(authorization or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def _require_user(authorization: str | None) -> str:
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        resp = get_user_auth_service().me(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="unauthorized")
    user = resp.get("user") if isinstance(resp, dict) else None
    username = str((user or {}).get("username", "")).strip().lower() if isinstance(user, dict) else ""
    if not username:
        raise HTTPException(status_code=401, detail="unauthorized")
    return username


def _require_owner(authorization: str | None, x_local_owner: str | None = None) -> str:
    return require_local_owner(authorization, x_local_owner)


def _normalize_broker(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return "longbridge" if raw in {"", "longport"} else raw


def _require_account_register_permissions(identity: Any, body: dict[str, Any]) -> None:
    accounts = rt.setup_accounts(owner_id=identity.owner_id).get("accounts", [])
    account_id = str((body or {}).get("account_id") or "").strip()
    broker_provider = _normalize_broker((body or {}).get("broker_provider") or "longbridge")
    existing_ids = {
        str(row.get("account_id") or "").strip()
        for row in accounts
        if isinstance(row, dict) and str(row.get("account_id") or "").strip()
    }
    creates_additional_account = bool(account_id and account_id not in existing_ids and existing_ids)
    if creates_additional_account:
        require_identity_entitlement(identity, "multi_account")
    if broker_provider != "longbridge":
        require_identity_entitlement(identity, "multi_broker")

@router.get("/setup/config")
def setup_config(authorization: str | None = Header(default=None), x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner")) -> dict[str, Any]:
    user = _require_owner(authorization, x_local_owner)
    return rt.setup_config(owner_id=user)


@router.post("/setup/config")
def setup_save_config(body: dict[str, Any] = Body(...), authorization: str | None = Header(default=None), x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner")) -> dict[str, Any]:
    user = _require_owner(authorization, x_local_owner)
    return rt.setup_save_config(body, owner_id=user)


@router.post("/setup/cn-market-data/install")
def setup_install_cn_market_data_provider(body: dict[str, Any] = Body(...), authorization: str | None = Header(default=None), x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner")) -> dict[str, Any]:
    _require_owner(authorization, x_local_owner)
    return rt.setup_install_cn_market_data_provider(body)


@router.post("/setup/risk-config")
def setup_risk_config(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.setup_risk_config(body)


@router.get("/setup/services/status")
def setup_services_status(authorization: str | None = Header(default=None), x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner")) -> dict[str, Any]:
    owner = ""
    try:
        owner = require_local_owner(authorization, x_local_owner)
    except HTTPException:
        owner = ""
    return rt.setup_services_status(owner_id=owner)


@router.get("/setup/public-ip")
def setup_public_ip(
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    _require_owner(authorization, x_local_owner)
    return rt.setup_public_ip()


@router.get("/setup/convex-dev/status")
def setup_convex_dev_status(
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    _require_owner(authorization, x_local_owner)
    return rt.setup_convex_dev_status()


@router.post("/setup/convex-dev/start")
def setup_convex_dev_start(
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    _require_owner(authorization, x_local_owner)
    return rt.setup_convex_dev_start()


@router.post("/setup/convex-dev/stop")
def setup_convex_dev_stop(
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    _require_owner(authorization, x_local_owner)
    return rt.setup_convex_dev_stop()


@router.post("/setup/convex-dev/restart")
def setup_convex_dev_restart(
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    _require_owner(authorization, x_local_owner)
    return rt.setup_convex_dev_restart()


@router.get("/setup/accounts")
def setup_accounts(authorization: str | None = Header(default=None), x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner")) -> dict[str, Any]:
    user = _require_owner(authorization, x_local_owner)
    return rt.setup_accounts(owner_id=user)


@router.post("/setup/accounts/register")
def setup_account_register(body: dict[str, Any] = Body(...), authorization: str | None = Header(default=None), x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner")) -> dict[str, Any]:
    identity = require_local_identity(authorization, x_local_owner)
    _require_account_register_permissions(identity, body)
    return rt.setup_account_register(body, owner_id=identity.owner_id)


@router.post("/setup/accounts/{account_id}/connect")
def setup_account_connect(account_id: str, authorization: str | None = Header(default=None), x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner")) -> dict[str, Any]:
    user = _require_owner(authorization, x_local_owner)
    return rt.setup_account_connect(account_id=account_id, owner_id=user)


@router.post("/setup/accounts/{account_id}/disconnect")
def setup_account_disconnect(account_id: str, authorization: str | None = Header(default=None), x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner")) -> dict[str, Any]:
    user = _require_owner(authorization, x_local_owner)
    return rt.setup_account_disconnect(account_id=account_id, owner_id=user)


@router.get("/setup/longport/diagnostics")
def setup_longport_diagnostics(
    probe: bool = False,
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    user = _require_owner(authorization, x_local_owner)
    return rt.setup_longport_diagnostics(probe=probe, owner_id=user)


@router.post("/setup/services/start")
def setup_start_services(body: dict[str, Any] = Body(...), authorization: str | None = Header(default=None), x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner")) -> dict[str, Any]:
    identity = require_local_identity(authorization, x_local_owner)
    if bool((body or {}).get("enable_auto_trader")):
        require_identity_entitlement(identity, "stock_auto_trading")
    if (
        bool((body or {}).get("enable_qqq_0dte_live"))
        or bool((body or {}).get("enable_qqq_1dte_live"))
        or bool((body or {}).get("enable_stock_options_swing"))
    ):
        require_identity_entitlement(identity, "option_auto_trading")
    return rt.setup_start_services(body, owner_id=identity.owner_id)


@router.post("/setup/services/stop")
def setup_stop_services(body: dict[str, Any] = Body(...), authorization: str | None = Header(default=None), x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner")) -> dict[str, Any]:
    user = _require_owner(authorization, x_local_owner)
    return rt.setup_stop_services(body, owner_id=user)


@router.post("/setup/services/stop-all")
def setup_stop_all_services(body: dict[str, Any] = Body(...), authorization: str | None = Header(default=None), x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner")) -> dict[str, Any]:
    user = _require_owner(authorization, x_local_owner)
    return rt.setup_stop_all_services(body, owner_id=user)
