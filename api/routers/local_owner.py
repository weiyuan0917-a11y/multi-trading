from __future__ import annotations

import os
import re
import contextvars
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from api.services.user_auth_service import get_user_auth_service
from api.services.local_license_service import stronger_plan, valid_license_identity


Plan = str


PLAN_RANK: dict[Plan, int] = {
    "free": 0,
    "pro": 1,
    "premium": 2,
}

FEATURE_REQUIRED_PLAN: dict[str, Plan] = {
    "research": "free",
    "backtest": "free",
    "tradingagents": "free",
    "openbb": "free",
    "stock_trading": "pro",
    "stock_auto_trading": "pro",
    "option_trading": "premium",
    "option_auto_trading": "premium",
    "multi_broker": "premium",
    "multi_account": "premium",
}

_OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,39}$")
_RESERVED_OWNERS = {"admin", "root", "system", "__system__", "null", "undefined"}
_IDENTITY_HEADER_CONTEXT: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "mt_identity_header_context",
    default={},
)


@dataclass(frozen=True)
class LocalIdentity:
    owner_id: str
    plan: Plan
    role: str
    is_admin: bool
    source: str

    def can_use(self, feature: str) -> bool:
        if self.is_admin or self.role in {"admin", "owner"}:
            return True
        required = FEATURE_REQUIRED_PLAN.get(feature, "free")
        return PLAN_RANK.get(self.plan, 0) >= PLAN_RANK.get(required, 0)


def _normalize_owner(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_plan(value: Any) -> Plan:
    raw = str(value or "").strip().lower()
    if raw == "premium":
        return "premium"
    if raw == "pro":
        return "pro"
    return "free"


def _normalize_role(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"admin", "owner"}:
        return raw
    return "user"


def _normalize_bool(value: Any) -> bool:
    raw = str(value or "").strip().lower()
    return raw in {"1", "true", "yes", "y", "on", "admin", "owner"}


def set_local_identity_header_context(headers: dict[str, Any]) -> contextvars.Token[dict[str, str]]:
    clean = {
        str(k): str(v or "").strip()
        for k, v in dict(headers or {}).items()
        if str(k or "").strip()
    }
    return _IDENTITY_HEADER_CONTEXT.set(clean)


def reset_local_identity_header_context(token: contextvars.Token[dict[str, str]]) -> None:
    _IDENTITY_HEADER_CONTEXT.reset(token)


def _identity_header_value(key: str) -> str:
    try:
        return str((_IDENTITY_HEADER_CONTEXT.get() or {}).get(key, "") or "").strip()
    except Exception:
        return ""


def allowed_local_owners() -> set[str]:
    raw = os.environ.get("LOCAL_AGENT_ALLOWED_OWNERS") or os.environ.get("LOCAL_AGENT_OWNER_ID") or ""
    owners = {_normalize_owner(x) for x in str(raw).split(",")}
    return {x for x in owners if x}


def allow_user_local_owners() -> bool:
    raw = os.environ.get("LOCAL_AGENT_ALLOW_USER_OWNERS", "true")
    return str(raw or "").strip().lower() not in {"0", "false", "no", "off"}


def is_valid_owner_id(owner_id: str) -> bool:
    owner = _normalize_owner(owner_id)
    return bool(_OWNER_RE.match(owner) and owner not in _RESERVED_OWNERS)


def extract_bearer(authorization: str | None) -> str:
    raw = str(authorization or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def _identity_from_public_user(user: dict[str, Any], owner_id: str, source: str) -> LocalIdentity:
    role = _normalize_role(user.get("role"))
    is_admin = bool(user.get("is_admin")) or role in {"admin", "owner"}
    identity = LocalIdentity(
        owner_id=_normalize_owner(user.get("username") or owner_id),
        plan="premium" if is_admin else _normalize_plan(user.get("plan")),
        role="admin" if is_admin and role == "user" else role,
        is_admin=is_admin,
        source=source,
    )
    return _apply_license_override(identity)


def _identity_from_authorization(authorization: str | None) -> LocalIdentity | None:
    token = extract_bearer(authorization)
    if not token:
        return None
    try:
        resp = get_user_auth_service().me(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="unauthorized")
    user = resp.get("user") if isinstance(resp, dict) else None
    username = _normalize_owner((user or {}).get("username", "") if isinstance(user, dict) else "")
    if not username:
        raise HTTPException(status_code=401, detail="unauthorized")
    return _identity_from_public_user(user if isinstance(user, dict) else {}, username, "local_session")


def _identity_from_env_owner(owner_id: str) -> LocalIdentity:
    role = _normalize_role(os.environ.get("LOCAL_AGENT_OWNER_ROLE", "user"))
    is_admin = (
        _normalize_bool(os.environ.get("LOCAL_AGENT_OWNER_IS_ADMIN"))
        or role in {"admin", "owner"}
    )
    identity = LocalIdentity(
        owner_id=owner_id,
        plan="premium" if is_admin else _normalize_plan(os.environ.get("LOCAL_AGENT_OWNER_PLAN", "free")),
        role="admin" if is_admin and role == "user" else role,
        is_admin=is_admin,
        source="local_owner_header",
    )
    return _apply_license_override(identity)


def _apply_license_override(identity: LocalIdentity) -> LocalIdentity:
    license_identity = valid_license_identity(identity.owner_id)
    if not license_identity:
        return identity
    role = _normalize_role(license_identity.get("role") or identity.role)
    is_admin = identity.is_admin or bool(license_identity.get("is_admin")) or role in {"admin", "owner"}
    return LocalIdentity(
        owner_id=identity.owner_id,
        plan="premium" if is_admin else stronger_plan(identity.plan, str(license_identity.get("plan") or "")),
        role="admin" if is_admin and role == "user" else role,
        is_admin=is_admin,
        source=f"{identity.source}+local_license",
    )


def require_local_identity(
    authorization: str | None,
    x_local_owner: str | None = None,
    x_api_key: str | None = None,
) -> LocalIdentity:
    raw_key = str(x_api_key or "").strip()
    if raw_key:
        try:
            username = get_user_auth_service().verify_api_key(raw_key)
            user = get_user_auth_service().public_user(username)
        except ValueError:
            raise HTTPException(status_code=401, detail="unauthorized")
        return _identity_from_public_user(user, username, "api_key")

    requested_owner = _normalize_owner(x_local_owner)
    session_identity = _identity_from_authorization(authorization)
    if requested_owner:
        if session_identity is not None:
            if session_identity.owner_id != requested_owner:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "local_owner_session_mismatch",
                        "session_owner": session_identity.owner_id,
                        "requested_owner": requested_owner,
                    },
                )
            return session_identity
        allowed = allowed_local_owners()
        if "*" in allowed or requested_owner in allowed or (allow_user_local_owners() and is_valid_owner_id(requested_owner)):
            return _identity_from_env_owner(requested_owner)
        raise HTTPException(status_code=403, detail="local_owner_not_allowed")

    if session_identity is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return session_identity


def require_local_owner(authorization: str | None, x_local_owner: str | None = None) -> str:
    return require_local_identity(authorization, x_local_owner).owner_id


def required_plan_for(feature: str) -> Plan:
    return FEATURE_REQUIRED_PLAN.get(feature, "free")


def require_identity_entitlement(identity: LocalIdentity, feature: str) -> None:
    if identity.can_use(feature):
        return
    raise HTTPException(
        status_code=403,
        detail={
            "error": "plan_required",
            "feature": feature,
            "required_plan": required_plan_for(feature),
            "current_plan": identity.plan,
        },
    )


def require_entitlement(
    authorization: str | None,
    x_local_owner: str | None,
    feature: str,
    x_api_key: str | None = None,
) -> LocalIdentity:
    identity = require_local_identity(authorization, x_local_owner, x_api_key)
    require_identity_entitlement(identity, feature)
    return identity
