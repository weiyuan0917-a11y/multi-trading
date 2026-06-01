from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote

from fastapi import HTTPException


_LEVEL_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
_L3_KEYS = {
    "OPENCLAW_MCP_MAX_LEVEL",
    "OPENCLAW_MCP_ALLOW_L3",
    "OPENCLAW_MCP_L3_CONFIRMATION_TOKEN",
}


def normalize_level(raw: str | None, default: str = "L2") -> str:
    v = str(raw or default).strip().upper()
    return v if v in _LEVEL_RANK else default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return value_bool(raw, default=default)


def value_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return bool(default)
    val = str(raw).strip().lower()
    if not val:
        return bool(default)
    return val in {"1", "true", "yes", "on"}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _project_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root).resolve()
    try:
        from config.user_env_store import project_root

        return project_root()
    except Exception:
        return Path(os.getenv("MULTITRADING_ROOT") or Path.cwd()).resolve()


def _load_user_env(username: str, root: str | Path | None = None) -> dict[str, str]:
    owner = _clean(username).lower()
    if not owner:
        return {}
    try:
        from config.user_env_store import load_user_env

        return load_user_env(owner, _project_root(root))
    except Exception:
        return {}


def _iter_saved_user_envs(root: str | Path | None = None) -> list[tuple[str, dict[str, str]]]:
    try:
        from config.user_env_store import user_env_dir, load_user_env

        env_dir = user_env_dir(_project_root(root))
        if not env_dir.exists():
            return []
        out: list[tuple[str, dict[str, str]]] = []
        for path in sorted(env_dir.glob("*.env")):
            owner = unquote(path.stem).strip().lower()
            if not owner:
                continue
            data = load_user_env(owner, _project_root(root))
            if any(_clean(data.get(k)) for k in _L3_KEYS):
                out.append((owner, data))
        return out
    except Exception:
        return []


def _effective_l3_env(user_env: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: _clean(os.getenv(k, "")) for k in _L3_KEYS}
    for key, value in dict(user_env or {}).items():
        if key in _L3_KEYS and _clean(value):
            env[key] = _clean(value)
    return env


def _l3_settings(user_env: dict[str, str] | None = None) -> dict[str, object]:
    env = _effective_l3_env(user_env)
    max_level = normalize_level(env.get("OPENCLAW_MCP_MAX_LEVEL"), default="L2")
    allow_l3 = value_bool(env.get("OPENCLAW_MCP_ALLOW_L3"), default=False)
    expected = _clean(env.get("OPENCLAW_MCP_L3_CONFIRMATION_TOKEN"))
    return {
        "max_level": max_level,
        "allow_l3": allow_l3,
        "expected_token": expected,
        "token_configured": bool(expected),
    }


def l3_confirmation_status(
    *,
    owner_id: str | None = None,
    root: str | Path | None = None,
    config_token: str | None = None,
) -> dict[str, object]:
    owner = _clean(owner_id).lower()
    user_env = _load_user_env(owner, root) if owner else {}
    settings = _l3_settings(user_env)
    module_token_configured = bool(_clean(config_token))
    ready = (
        _LEVEL_RANK.get(str(settings["max_level"]), 2) >= 3
        and bool(settings["allow_l3"])
        and (bool(settings["token_configured"]) or module_token_configured)
    )
    return {
        "max_level": settings["max_level"],
        "allow_l3": settings["allow_l3"],
        "env_token_configured": bool(settings["token_configured"]),
        "user_token_configured": bool(owner and settings["token_configured"]),
        "module_token_configured": module_token_configured,
        "required_for_live_order": True,
        "ready": bool(ready),
    }


def _validate_settings(settings: dict[str, object], submitted: str) -> str | None:
    max_level = str(settings.get("max_level") or "L2")
    allow_l3 = bool(settings.get("allow_l3"))
    expected = _clean(settings.get("expected_token"))
    if _LEVEL_RANK.get(max_level, 2) < 3:
        return "level"
    if not allow_l3:
        return "allow"
    if expected and submitted != expected:
        return "token"
    return None


def _raise_l3_error(reason: str) -> None:
    if reason == "level":
        raise HTTPException(status_code=403, detail="当前权限不足：需要 L3")
    if reason == "allow":
        raise HTTPException(status_code=403, detail="L3敏感交易已禁用")
    raise HTTPException(status_code=403, detail="confirmation_token 无效或缺失")


def ensure_l3_confirmation(
    token: str | None,
    *,
    owner_id: str | None = None,
    root: str | Path | None = None,
) -> None:
    submitted = _clean(token)
    owner = _clean(owner_id).lower()
    if owner:
        reason = _validate_settings(_l3_settings(_load_user_env(owner, root)), submitted)
        if reason is None:
            return
        _raise_l3_error(reason)

    candidates = [("process", _l3_settings({}))]
    candidates.extend((owner_name, _l3_settings(data)) for owner_name, data in _iter_saved_user_envs(root))

    reasons: list[str] = []
    for _name, settings in candidates:
        reason = _validate_settings(settings, submitted)
        if reason is None:
            return
        reasons.append(reason)

    if "token" in reasons:
        _raise_l3_error("token")
    if "allow" in reasons:
        _raise_l3_error("allow")
    _raise_l3_error("level")
