from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query

from api.schemas_auth import AuthApiKeyCreateBody, AuthLoginBody, AuthRegisterBody
from api.services.user_auth_service import get_user_auth_service
from api.routers.local_owner import require_local_identity
from config.user_env_store import apply_light_session_env_for_user

router = APIRouter(tags=["auth"])


def _project_root() -> Path:
    from api import main as m

    return Path(m.ROOT)


def _sync_env_light(username: str) -> None:
    apply_light_session_env_for_user(str(username or "").strip().lower(), _project_root())


def _extract_bearer(authorization: str | None) -> str:
    raw = str(authorization or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


@router.post("/auth/register")
def auth_register(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    parsed = AuthRegisterBody.model_validate(body if isinstance(body, dict) else {})
    svc = get_user_auth_service()
    try:
        out = svc.register(parsed.username, parsed.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    user = out.get("user") if isinstance(out, dict) else None
    un = str((user or {}).get("username", "")).strip().lower() if isinstance(user, dict) else ""
    if un:
        _sync_env_light(un)
    return out


@router.post("/auth/login")
def auth_login(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    parsed = AuthLoginBody.model_validate(body if isinstance(body, dict) else {})
    svc = get_user_auth_service()
    try:
        out = svc.login(parsed.username, parsed.password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    user = out.get("user") if isinstance(out, dict) else None
    un = str((user or {}).get("username", "")).strip().lower() if isinstance(user, dict) else ""
    if un:
        _sync_env_light(un)
    return out


@router.get("/auth/me")
def auth_me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    svc = get_user_auth_service()
    token = _extract_bearer(authorization)
    try:
        out = svc.me(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="unauthorized")
    user = out.get("user") if isinstance(out, dict) else None
    un = str((user or {}).get("username", "")).strip().lower() if isinstance(user, dict) else ""
    if un:
        _sync_env_light(un)
    return out


@router.post("/auth/logout")
def auth_logout(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    svc = get_user_auth_service()
    token = _extract_bearer(authorization)
    svc.logout(token)
    return {"ok": True}


def _require_session_username(authorization: str | None) -> str:
    svc = get_user_auth_service()
    token = _extract_bearer(authorization)
    try:
        out = svc.me(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="unauthorized")
    user = out.get("user") if isinstance(out, dict) else None
    un = str((user or {}).get("username", "")).strip().lower() if isinstance(user, dict) else ""
    if not un:
        raise HTTPException(status_code=401, detail="unauthorized")
    return un


def _require_api_key_owner(authorization: str | None, x_local_owner: str | None) -> str:
    if str(x_local_owner or "").strip():
        return require_local_identity(authorization, x_local_owner).owner_id
    return _require_session_username(authorization)


@router.post("/auth/api-keys")
def auth_api_keys_create(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    """创建个人 API Key（明文仅返回一次）；本机 Worker 请用请求头 X-Api-Key。"""
    parsed = AuthApiKeyCreateBody.model_validate(body if isinstance(body, dict) else {})
    username = _require_api_key_owner(authorization, x_local_owner)
    svc = get_user_auth_service()
    try:
        return svc.create_api_key(username, parsed.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/auth/api-keys")
def auth_api_keys_list(
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    username = _require_api_key_owner(authorization, x_local_owner)
    svc = get_user_auth_service()
    return {"ok": True, "items": svc.list_api_keys(username)}


@router.delete("/auth/api-keys/{key_id}")
def auth_api_keys_revoke(
    key_id: str,
    purge: bool = Query(False, description="为 true 时永久删除已吊销记录（不可恢复）"),
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    username = _require_api_key_owner(authorization, x_local_owner)
    svc = get_user_auth_service()
    if purge:
        code = svc.purge_revoked_api_key(username, key_id)
        if code == "deleted":
            return {"ok": True, "purged": True}
        if code == "not_revoked":
            raise HTTPException(status_code=400, detail="api_key_not_revoked")
        raise HTTPException(status_code=404, detail="api_key_not_found")
    if not svc.revoke_api_key(username, key_id):
        raise HTTPException(status_code=404, detail="api_key_not_found")
    return {"ok": True}

