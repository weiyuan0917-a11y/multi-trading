from __future__ import annotations

from typing import Any, Callable


REMOVED_REASON = "auto_trading_removed"


def _disabled(**extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "disabled": True, "reason": REMOVED_REASON}
    out.update(extra)
    return out


def redact_auto_trader_secrets_for_client(config: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(config or {})
    for key in ("confirmation_token", "feishu_webhook", "api_key", "secret_key"):
        if key in out and out[key]:
            out[key] = "***"
    out["enabled"] = False
    out["removed"] = True
    return out


def build_auto_trader_status_response(
    *,
    status: dict[str, Any] | None,
    runtime: dict[str, Any] | None,
    research: dict[str, Any] | None,
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "status": status or _disabled(running=False),
        "runtime": runtime or {},
        "research": research or _disabled(),
        "config": redact_auto_trader_secrets_for_client(config or {}),
        "disabled": True,
        "reason": REMOVED_REASON,
    }


def apply_auto_trader_config_update(
    *,
    payload: dict[str, Any],
    update_config: Callable[[dict[str, Any]], dict[str, Any]],
    sync_worker: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    next_payload = dict(payload or {})
    next_payload["enabled"] = False
    cfg = update_config(next_payload)
    sync = sync_worker(cfg) if sync_worker else {}
    return {"config": redact_auto_trader_secrets_for_client(cfg), "sync": sync, "reason": REMOVED_REASON}


def apply_agent_policy_update(
    *,
    raw_payload: dict[str, Any],
    current_config: dict[str, Any],
    validate_update: Callable[..., Any] | None = None,
    locked_fields: Any = None,
    allowed_field_rules: Any = None,
    update_config: Callable[[dict[str, Any]], dict[str, Any]],
    sync_worker: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return apply_auto_trader_config_update(
        payload=raw_payload,
        update_config=update_config,
        sync_worker=sync_worker,
    )


def apply_template_with_sync(
    *,
    template_name: str | None,
    apply_template: Callable[[str | None], dict[str, Any]],
    sync_worker: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cfg = apply_template(template_name)
    sync = sync_worker(cfg) if sync_worker else {}
    return {"config": redact_auto_trader_secrets_for_client(cfg), "sync": sync, "reason": REMOVED_REASON}


def build_auto_trader_config_policy(*, locked_fields: Any = None, field_rules: Any = None) -> dict[str, Any]:
    return {
        "locked_fields": locked_fields or {},
        "field_rules": field_rules or {},
        "disabled": True,
        "reason": REMOVED_REASON,
    }


def import_config_with_rollback(
    *,
    config_obj: dict[str, Any],
    current_config: dict[str, Any],
    validate_import_config: Callable[[dict[str, Any]], dict[str, Any]] | None,
    update_config: Callable[[dict[str, Any]], dict[str, Any]],
    sync_worker: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = dict(config_obj or {})
    if validate_import_config is not None:
        payload = validate_import_config(payload)
    return apply_auto_trader_config_update(payload=payload, update_config=update_config, sync_worker=sync_worker)


def rollback_config_with_sync(
    *,
    backup_id: str | None,
    rollback_config: Callable[[str | None], dict[str, Any]],
    sync_worker: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cfg = rollback_config(backup_id)
    sync = sync_worker(cfg) if sync_worker else {}
    return {"config": redact_auto_trader_secrets_for_client(cfg), "sync": sync, "reason": REMOVED_REASON}


def preview_template_safe(*, template_name: str | None, preview_template: Callable[[str | None], dict[str, Any]]) -> dict[str, Any]:
    return preview_template(template_name)


def preview_rollback_safe(*, backup_id: str | None, preview_rollback: Callable[[str | None], dict[str, Any]]) -> dict[str, Any]:
    return preview_rollback(backup_id)
