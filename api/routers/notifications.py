import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from fastapi import APIRouter, Body, Header, HTTPException

from config.notification_settings import resolve_feishu_app_config
from api.notification_preferences import (
    DEFAULT_NOTIFICATION_PREFERENCES,
    load_notification_preferences,
    merge_patch_notification_preferences,
)

router = APIRouter(tags=["notifications"])
logger = logging.getLogger(__name__)

ROOT = os.path.abspath(
    os.getenv("MULTITRADING_ROOT")
    or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
MCP_DIR = os.path.join(ROOT, "mcp_server")
FEISHU_HTTP_TIMEOUT_SECONDS = 10


def _load_notification_config(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Failed to load notification config %s: %s", path, exc)
        return {}


def _apply_owner_env(authorization: str | None, x_local_owner: str | None) -> str:
    try:
        from api.routers.local_owner import require_local_owner
        from config.user_env_store import apply_light_session_env_for_user

        owner = require_local_owner(authorization, x_local_owner)
        if owner:
            apply_light_session_env_for_user(owner, Path(ROOT))
        return owner
    except Exception:
        return ""


def _feishu_webhook_bots(data: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    raw = data.get("feishu_bots")
    if not isinstance(raw, list):
        return []
    out: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        webhook_url = str(item.get("webhook_url") or "").strip()
        if webhook_url:
            out.append((index, item))
    return out


def _status_payload(data: dict[str, Any], feishu_cfg: dict[str, str]) -> dict[str, Any]:
    webhook_count = len(_feishu_webhook_bots(data))
    app_configured = bool(feishu_cfg.get("app_id") and feishu_cfg.get("app_secret"))
    scheduled_chat_id = str(feishu_cfg.get("scheduled_chat_id") or "").strip()
    scheduled_configured = bool(scheduled_chat_id)
    app_count = 1 if app_configured else 0
    return {
        "feishu_app_configured": app_configured,
        "scheduled_chat_id": scheduled_chat_id,
        "scheduled_chat_id_configured": scheduled_configured,
        "feishu_webhook_bots_count": webhook_count,
        "feishu_app_bots_count": app_count,
        "feishu_bots_count": webhook_count + app_count,
        "feishu_push_targets_count": webhook_count + (1 if app_configured and scheduled_configured else 0),
    }


def _feishu_sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _response_json(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"raw": data}
    except Exception:
        return {"raw": (resp.text or "")[:300]}


def _feishu_code_ok(value: Any) -> bool:
    return str(value).strip() == "0"


def _send_feishu_webhook_text(index: int, bot_config: dict[str, Any], text: str) -> dict[str, Any]:
    webhook_url = str(bot_config.get("webhook_url") or "").strip()
    secret = str(bot_config.get("secret") or "").strip()
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        timestamp = int(time.time())
        payload["timestamp"] = str(timestamp)
        payload["sign"] = _feishu_sign(secret, timestamp)
    try:
        resp = requests.post(webhook_url, json=payload, timeout=FEISHU_HTTP_TIMEOUT_SECONDS)
        data = _response_json(resp)
        code = data.get("code")
        ok = bool(resp.ok and _feishu_code_ok(code))
        return {
            "kind": "webhook",
            "index": index,
            "ok": ok,
            "status_code": resp.status_code,
            "code": code,
            "message": data.get("msg") or data.get("message") or "",
        }
    except Exception as exc:
        return {"kind": "webhook", "index": index, "ok": False, "error": str(exc)[:300]}


def _send_feishu_app_chat_text(feishu_cfg: dict[str, str], text: str) -> dict[str, Any]:
    app_id = str(feishu_cfg.get("app_id") or "").strip()
    app_secret = str(feishu_cfg.get("app_secret") or "").strip()
    chat_id = str(feishu_cfg.get("scheduled_chat_id") or "").strip()
    if not (app_id and app_secret and chat_id):
        return {"kind": "app_chat", "ok": False, "error": "missing_app_or_scheduled_chat_id"}
    try:
        token_resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=FEISHU_HTTP_TIMEOUT_SECONDS,
        )
        token_data = _response_json(token_resp)
        token = str(token_data.get("tenant_access_token") or "").strip()
        if not (token_resp.ok and _feishu_code_ok(token_data.get("code")) and token):
            return {
                "kind": "app_chat",
                "ok": False,
                "stage": "tenant_access_token",
                "status_code": token_resp.status_code,
                "code": token_data.get("code"),
                "message": token_data.get("msg") or token_data.get("message") or "",
            }
        msg_resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
            json={"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
            timeout=FEISHU_HTTP_TIMEOUT_SECONDS,
        )
        msg_data = _response_json(msg_resp)
        return {
            "kind": "app_chat",
            "ok": bool(msg_resp.ok and _feishu_code_ok(msg_data.get("code"))),
            "stage": "message",
            "status_code": msg_resp.status_code,
            "code": msg_data.get("code"),
            "message": msg_data.get("msg") or msg_data.get("message") or "",
        }
    except Exception as exc:
        return {"kind": "app_chat", "ok": False, "error": str(exc)[:300]}


@router.get("/notifications/status")
def notifications_status(
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    _apply_owner_env(authorization, x_local_owner)
    cfg_path = os.path.join(MCP_DIR, "notification_config.json")
    data = _load_notification_config(cfg_path)
    feishu_cfg = resolve_feishu_app_config(cfg_path)
    env_override = any(
        bool(os.getenv(k))
        for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_SCHEDULED_CHAT_ID")
    )
    prefs = load_notification_preferences()
    status = _status_payload(data, feishu_cfg)
    return {
        **status,
        "feishu_config_source": "env" if env_override else "file",
        "preferences_summary": {
            "scheduled_market_report": bool((prefs.get("scheduled_market_report") or {}).get("enabled", True)),
            "semi_auto_pending_signal": bool((prefs.get("semi_auto_pending_signal") or {}).get("enabled", True)),
            "full_auto_execution": bool((prefs.get("full_auto_execution") or {}).get("enabled", True)),
            "observer_mode_digest": bool((prefs.get("observer_mode_digest") or {}).get("enabled", True)),
            "bottom_reversal_watch": bool((prefs.get("bottom_reversal_watch") or {}).get("enabled", False)),
            "feishu_builtin_reversal": bool((prefs.get("feishu_builtin_reversal_monitor") or {}).get("enabled", False)),
        },
    }


@router.post("/notifications/test/feishu")
def notifications_test_feishu(
    body: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    _apply_owner_env(authorization, x_local_owner)
    cfg_path = os.path.join(MCP_DIR, "notification_config.json")
    data = _load_notification_config(cfg_path)
    feishu_cfg = resolve_feishu_app_config(cfg_path)
    status = _status_payload(data, feishu_cfg)
    raw_message = body.get("message") if isinstance(body, dict) else None
    message = str(raw_message or "").strip() or (
        "MultiTrading 飞书连通性测试\n"
        f"发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    targets: list[dict[str, Any]] = []
    for index, bot_config in _feishu_webhook_bots(data):
        targets.append(_send_feishu_webhook_text(index, bot_config, message))
    if status["feishu_app_configured"] and status["scheduled_chat_id_configured"]:
        targets.append(_send_feishu_app_chat_text(feishu_cfg, message))

    if not targets:
        if status["feishu_app_configured"] and not status["scheduled_chat_id_configured"]:
            msg = "飞书应用已配置，但未配置 scheduled_chat_id；无法发送测试消息。"
        else:
            msg = "未找到可测试的飞书 Webhook 或飞书应用推送目标。"
        return {"ok": False, "message": msg, "targets": [], **status}

    ok = any(bool(item.get("ok")) for item in targets)
    return {
        "ok": ok,
        "message": "飞书测试消息已发送，请检查目标群。" if ok else "飞书测试发送失败，请检查应用凭证、群 ID 或 Webhook。",
        "targets": targets,
        **status,
    }


@router.get("/notifications/preferences")
def notifications_preferences_get() -> dict[str, Any]:
    return {
        "ok": True,
        "preferences": load_notification_preferences(),
        "defaults": DEFAULT_NOTIFICATION_PREFERENCES,
        "signals_page_storage_key": "signals_page_symbols_v1",
    }


@router.put("/notifications/preferences")
def notifications_preferences_put(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    patch = body.get("preferences") if isinstance(body.get("preferences"), dict) else body
    if not isinstance(patch, dict):
        raise HTTPException(status_code=400, detail="invalid_body_expect_preferences_object")
    merged = merge_patch_notification_preferences(patch)
    return {"ok": True, "preferences": merged}

