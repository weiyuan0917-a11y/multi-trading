import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta
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
_OWNER_ENV_APPLIED_AT: dict[str, float] = {}
_OWNER_ENV_APPLY_TTL_SECONDS = 10.0


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
            now = time.monotonic()
            key = str(owner).strip().lower()
            if now - float(_OWNER_ENV_APPLIED_AT.get(key) or 0.0) >= _OWNER_ENV_APPLY_TTL_SECONDS:
                apply_light_session_env_for_user(owner, Path(ROOT))
                _OWNER_ENV_APPLIED_AT[key] = now
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


def _is_scheduled_report_trading_day(now: datetime) -> bool:
    return now.weekday() < 5


def _is_scheduled_report_window(now: datetime) -> bool:
    return not (6 <= now.hour <= 7)


def _format_local_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def _next_scheduled_report_candidate(now: datetime) -> datetime:
    candidate = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    for _ in range(24 * 10):
        if _is_scheduled_report_trading_day(candidate) and _is_scheduled_report_window(candidate):
            return candidate
        candidate += timedelta(hours=1)
    return candidate


def _scheduled_market_report_status(
    status: dict[str, Any],
    prefs: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    cur = now or datetime.now()
    pref = prefs.get("scheduled_market_report") if isinstance(prefs, dict) else {}
    enabled = bool(pref.get("enabled", True)) if isinstance(pref, dict) else True
    app_configured = bool(status.get("feishu_app_configured"))
    chat_configured = bool(status.get("scheduled_chat_id_configured"))
    trading_day = _is_scheduled_report_trading_day(cur)
    in_window = _is_scheduled_report_window(cur)
    next_candidate = _next_scheduled_report_candidate(cur)

    reason = "ready"
    message = "当前符合定时报告发送条件；飞书机器人运行时会在下一个整点尝试发送。"
    if not app_configured:
        reason = "missing_feishu_app"
        message = "飞书应用 App ID/App Secret 未配置，定时报告无法发送。"
    elif not chat_configured:
        reason = "missing_scheduled_chat_id"
        message = "未配置 scheduled_chat_id，飞书机器人不会启动定时报告线程。"
    elif not enabled:
        reason = "disabled"
        message = "通知中心已关闭「定时市场分析报告」，整点会跳过。"
    elif not trading_day:
        reason = "non_trading_day"
        message = "今天不是周一到周五，定时市场分析报告会跳过。"
    elif not in_window:
        reason = "outside_window"
        message = "当前处于 06:00-07:59 跳过时段，定时报告暂不发送。"

    return {
        "enabled": enabled,
        "scheduled_chat_id_configured": chat_configured,
        "feishu_app_configured": app_configured,
        "trading_day": trading_day,
        "in_trading_window": in_window,
        "should_send_now": reason == "ready",
        "reason": reason,
        "message": message,
        "now": _format_local_dt(cur),
        "next_candidate_at": _format_local_dt(next_candidate),
        "rule": "按本机时间：周一至周五 00:00-05:59、08:00-23:59 的整点尝试发送；06:00-07:59 跳过。",
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


def _response_log_id(resp: requests.Response) -> str:
    headers = getattr(resp, "headers", {}) or {}
    for header in ("X-Tt-Logid", "X-Request-Id", "X-Lark-Request-Id"):
        value = str(headers.get(header) or "").strip()
        if value:
            return value
    return ""


def _feishu_failure_hint(kind: str, stage: str | None = None) -> str:
    if kind == "webhook":
        return "请检查 Webhook 地址、签名密钥，以及机器人是否仍在目标群。"
    if stage == "tenant_access_token":
        return "请检查 App ID/App Secret 是否正确，飞书应用是否已启用或发布。"
    if stage == "message":
        return "请检查 scheduled_chat_id 是否为 oc_ 开头的群 ID，应用机器人是否已加入该群，并已开通/发布发送消息权限。"
    return "请检查飞书应用凭证、目标群 ID 和机器人权限。"


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
        log_id = _response_log_id(resp)
        return {
            "kind": "webhook",
            "index": index,
            "ok": ok,
            "status_code": resp.status_code,
            "code": code,
            "message": data.get("msg") or data.get("message") or "",
            **({"log_id": log_id} if log_id else {}),
            **({} if ok else {"hint": _feishu_failure_hint("webhook")}),
        }
    except Exception as exc:
        return {
            "kind": "webhook",
            "index": index,
            "ok": False,
            "error": str(exc)[:300],
            "hint": _feishu_failure_hint("webhook"),
        }


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
            log_id = _response_log_id(token_resp)
            return {
                "kind": "app_chat",
                "ok": False,
                "stage": "tenant_access_token",
                "status_code": token_resp.status_code,
                "code": token_data.get("code"),
                "message": token_data.get("msg") or token_data.get("message") or "",
                **({"log_id": log_id} if log_id else {}),
                "hint": _feishu_failure_hint("app_chat", "tenant_access_token"),
            }
        msg_resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
            json={"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
            timeout=FEISHU_HTTP_TIMEOUT_SECONDS,
        )
        msg_data = _response_json(msg_resp)
        ok = bool(msg_resp.ok and _feishu_code_ok(msg_data.get("code")))
        log_id = _response_log_id(msg_resp)
        return {
            "kind": "app_chat",
            "ok": ok,
            "stage": "message",
            "status_code": msg_resp.status_code,
            "code": msg_data.get("code"),
            "message": msg_data.get("msg") or msg_data.get("message") or "",
            **({"log_id": log_id} if log_id else {}),
            **({} if ok else {"hint": _feishu_failure_hint("app_chat", "message")}),
        }
    except Exception as exc:
        return {
            "kind": "app_chat",
            "ok": False,
            "error": str(exc)[:300],
            "hint": _feishu_failure_hint("app_chat"),
        }


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
        "scheduled_market_report_status": _scheduled_market_report_status(status, prefs),
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
    if not ok:
        logger.warning("Feishu test failed: %s", json.dumps(targets, ensure_ascii=False, default=str))
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

