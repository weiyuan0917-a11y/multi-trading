from __future__ import annotations

import os

from fastapi import HTTPException


_LEVEL_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}


def normalize_level(raw: str | None, default: str = "L2") -> str:
    v = str(raw or default).strip().upper()
    return v if v in _LEVEL_RANK else default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def ensure_l3_confirmation(token: str | None) -> None:
    max_level = normalize_level(os.getenv("OPENCLAW_MCP_MAX_LEVEL", "L2"), default="L2")
    allow_l3 = env_bool("OPENCLAW_MCP_ALLOW_L3", default=False)
    expected = str(os.getenv("OPENCLAW_MCP_L3_CONFIRMATION_TOKEN", "")).strip()
    if _LEVEL_RANK.get(max_level, 2) < 3:
        raise HTTPException(status_code=403, detail="当前权限不足：需要 L3")
    if not allow_l3:
        raise HTTPException(status_code=403, detail="L3敏感交易已禁用")
    if expected and str(token or "").strip() != expected:
        raise HTTPException(status_code=403, detail="confirmation_token 无效或缺失")
