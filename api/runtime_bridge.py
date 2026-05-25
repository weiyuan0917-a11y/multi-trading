from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from datetime import date, datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config.live_settings import live_settings
from mcp_server.risk_manager import load_config, save_config
from runtime_process_utils import is_pid_alive, read_pid_file

from api.auto_trader_research import get_research_status
from api.auto_trader import AutoTraderService, archive_legacy_unscoped_signals, summarize_legacy_unscoped_signals
from api.brokers import BrokerCredentials
from api.schemas_auto_trader import (
    AutoTraderConfirmBody,
    AutoTraderConfigBody,
    AutoTraderImportBody,
    AutoTraderImportConfigBody,
    AutoTraderMlMatrixApplyBody,
    AutoTraderMlMatrixRunBody,
    AutoTraderResearchRunBody,
    AutoTraderRollbackBody,
    AutoTraderStrategyMatrixRunBody,
    AutoTraderTemplateApplyBody,
)
from api.schemas_backtest import BacktestCompareBody, BacktestKline, BacktestKlineCacheFetchBody
from api.schemas_fees_risk import (
    FeeBrokerActiveBody,
    FeeBrokerCreateBody,
    FeeBrokerDisplayNameBody,
    FeeScheduleBody,
)
from api.schemas_options_trade import OptionBacktestBody, OptionOrderBody, SubmitOrderBody, SyntheticOptionPathBody
from api.schemas_qqq_0dte import Qqq0dteBacktestBody, Qqq0dteMatrixBody, Qqq0dteResolveContractBody
from api.schemas_setup import (
    SetupAccountRegisterBody,
    SetupCnMarketDataInstallBody,
    SetupConfigBody,
    SetupRiskConfigBody,
    SetupStartBody,
    SetupStopAllBody,
    SetupStopBody,
)
from api.services import (
    apply_agent_policy_update,
    apply_auto_trader_config_update,
    apply_template_with_sync,
    apply_setup_env_updates,
    build_fee_schedule_response,
    build_risk_config_response,
    build_auto_trader_config_policy,
    build_auto_trader_status_response,
    redact_auto_trader_secrets_for_client,
    build_broker_diagnostics_response,
    build_longport_diagnostics_response,
    build_setup_config_response,
    build_setup_services_status,
    collect_broker_context_snapshot,
    collect_longport_context_snapshot,
    estimate_fees,
    import_config_with_rollback,
    build_option_legs_or_400,
    build_option_submit_response,
    preview_rollback_safe,
    preview_template_safe,
    rollback_config_with_sync,
    start_services,
    stop_all_services,
    stop_services,
)
from api.services.option_short_guard import is_opening_short_options_allowed, validate_option_sell_covered
from api.services.backtest_task_service import get_backtest_events, get_backtest_task, list_backtest_tasks, run_sync_backtest_task
from api.services.cn_market_data_service import get_cn_market_data_service
from api.services.convex_dev_control_service import (
    convex_dev_status as build_convex_dev_status,
    restart_convex_dev,
    start_convex_dev,
    stop_convex_dev,
)
from api.services.public_market_data_service import get_public_market_data_service

_SETUP_SERVICES_STATUS_CACHE_TTL_SECONDS = 2.0
_setup_services_status_cache_lock = threading.Lock()
_setup_services_status_cache_by_owner: dict[str, tuple[float, dict[str, Any]]] = {}
_DASHBOARD_MARKET_CACHE_LOCK = threading.Lock()
_DASHBOARD_MARKET_CACHE: dict[str, list[dict[str, Any]]] = {"cn_hk": [], "us": []}


def _env_bool(key: str, default: str = "0") -> bool:
    return str(os.getenv(key, default)).strip().lower() in {"1", "true", "yes", "on"}


def _m():
    # 延迟导入，避免在模块加载阶段触发循环依赖。
    from api import main as m

    return m


def _normalize_broker_provider(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return "longbridge" if raw in {"longport"} else raw


def _normalize_owner_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return "".join(ch for ch in raw if ch.isalnum() or ch in {"_", "-"})[:80]


def _owner_data_dir(owner_id: str | None, *parts: str) -> str:
    m = _m()
    owner = _normalize_owner_id(owner_id)
    if not owner:
        return os.path.join(m.ROOT, "data", *parts)
    return os.path.join(m.ROOT, "data", "owners", owner, *parts)


def _read_json_path(path: str) -> dict[str, Any]:
    try:
        if not os.path.isfile(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json_path(path: str, payload: dict[str, Any]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _stamp_config_owner_context(cfg: dict[str, Any], owner_id: str | None) -> dict[str, Any]:
    owner = _normalize_owner_id(owner_id)
    if not owner:
        return cfg
    out = dict(cfg)
    out["owner_id"] = owner
    try:
        rec = _m().ACCOUNT_REGISTRY.get_account_record(
            account_id=(str(out.get("account_id") or "").strip() or None),
            owner_id=owner,
        )
        out["account_id"] = str(getattr(rec, "account_id", "") or "").strip() or out.get("account_id")
        out["broker_provider"] = str(getattr(rec, "broker_provider", "") or "").strip().lower() or out.get("broker_provider")
    except Exception:
        out.setdefault("account_id", None)
        out.setdefault("broker_provider", None)
    return out


def _legacy_config_matches_owner_context(disk: dict[str, Any] | None, owner_id: str | None) -> bool:
    owner = _normalize_owner_id(owner_id)
    if not owner or not isinstance(disk, dict):
        return False
    legacy_owner = _normalize_owner_id(disk.get("owner_id"))
    if legacy_owner:
        return legacy_owner == owner
    account_id = str(disk.get("account_id") or "").strip()
    if not account_id:
        return False
    try:
        rec = _m().ACCOUNT_REGISTRY.get_account_record(account_id=account_id, owner_id=owner)
    except Exception:
        return False
    rec_account = str(getattr(rec, "account_id", "") or "").strip()
    if rec_account != account_id:
        return False
    expected_broker = _normalize_broker_provider(disk.get("broker_provider"))
    if expected_broker:
        rec_broker = _normalize_broker_provider(getattr(rec, "broker_provider", ""))
        if rec_broker and rec_broker != expected_broker:
            return False
    return True


def _stock_auto_trader_safety_status(owner_id: str | None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    m = _m()
    cfg = dict(config or {})
    owner = str(owner_id or "").strip().lower()
    checks: list[dict[str, Any]] = []

    def add_check(check_id: str, ok: bool, severity: str, message: str, **extra: Any) -> None:
        row: dict[str, Any] = {"id": check_id, "ok": bool(ok), "severity": severity, "message": message}
        if extra:
            row.update(extra)
        checks.append(row)

    add_check("explicit_owner", bool(owner), "danger", "启动股票自动交易必须携带 owner。")

    rec = None
    account_error = None
    if owner:
        try:
            rec = m.ACCOUNT_REGISTRY.get_account_record(owner_id=owner)
        except Exception as e:
            account_error = str(e)
    account_id = str(getattr(rec, "account_id", "") or "").strip() if rec is not None else ""
    broker_provider = _normalize_broker_provider(getattr(rec, "broker_provider", "") if rec is not None else "")
    account_connected = bool(
        rec is not None
        and not bool(getattr(rec, "manual_disconnected", False))
        and str(getattr(rec, "status", "") or "").strip().lower() != "disconnected"
    )
    add_check(
        "default_account_ready",
        bool(rec is not None and account_connected and account_id and broker_provider),
        "danger",
        "默认账户必须存在且处于连接状态。",
        account_id=account_id or None,
        broker_provider=broker_provider or None,
        error=account_error,
    )

    legacy = summarize_legacy_unscoped_signals()
    legacy_count = int(legacy.get("count") or 0)
    add_check(
        "legacy_unscoped_signals_archived",
        legacy_count == 0,
        "danger",
        "主信号文件里不能存在缺少 owner/account/broker 的旧实盘信号。",
        count=legacy_count,
        symbols=legacy.get("symbols") or [],
        archive_path=legacy.get("archive_path"),
    )

    autostart_on_boot = _env_bool("AUTO_TRADER_AUTOSTART_ON_API_BOOT", "false")
    add_check(
        "api_boot_autostart_disabled",
        not autostart_on_boot,
        "danger",
        "API 启动时禁止隐式自启股票自动交易 worker。",
    )

    auto_execute = bool(cfg.get("auto_execute", True))
    auto_sell_enabled = bool(cfg.get("auto_sell_enabled", True))
    dry_run_mode = bool(cfg.get("dry_run_mode", False))
    add_check(
        "managed_position_sell_only",
        True,
        "info",
        "自动卖出已限制为本 worker 买入并带账户归属的持仓信号。",
        auto_sell_enabled=auto_sell_enabled,
    )

    danger_failed = [c for c in checks if not c.get("ok") and c.get("severity") == "danger"]
    warn_failed = [c for c in checks if not c.get("ok") and c.get("severity") == "warn"]
    can_start_worker = not danger_failed
    return {
        "ok": can_start_worker and not warn_failed,
        "can_start_worker": can_start_worker,
        "can_manual_scan": can_start_worker,
        "level": "danger" if danger_failed else ("warn" if warn_failed else "ok"),
        "checks": checks,
        "account": {
            "owner_id": owner or None,
            "account_id": account_id or None,
            "broker_provider": broker_provider or None,
            "account_connected": account_connected,
            "quote_ready": bool(getattr(rec, "quote_ctx", None) is not None) if rec is not None else False,
            "trade_ready": bool(getattr(rec, "trade_ctx", None) is not None) if rec is not None else False,
            "status": getattr(rec, "status", None) if rec is not None else None,
            "manual_disconnected": bool(getattr(rec, "manual_disconnected", False)) if rec is not None else False,
            "last_error": getattr(rec, "last_error", None) if rec is not None else account_error,
        },
        "legacy_unscoped_signals": legacy,
        "autostart_on_api_boot": autostart_on_boot,
        "auto_execute": auto_execute,
        "auto_sell_enabled": auto_sell_enabled,
        "dry_run_mode": dry_run_mode,
    }


def _assert_stock_auto_trader_safety(owner_id: str | None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    safety = _stock_auto_trader_safety_status(owner_id=owner_id, config=config)
    if not bool(safety.get("can_start_worker")):
        raise _m().HTTPException(
            status_code=409,
            detail={
                "error": "auto_trader_safety_blocked",
                "message": "股票自动交易安全检查未通过，已拒绝启动/扫描。",
                "safety": safety,
            },
        )
    return safety


def _broker_credentials_from_setup_body(m: Any, parsed: SetupAccountRegisterBody, broker_provider: str):
    raw_credentials = dict(parsed.credentials or {})
    provider = str(broker_provider or "").strip().lower()
    if provider == "itiger":
        provider = "tiger"

    if provider == "tiger":
        tiger_id = str(raw_credentials.get("tiger_id") or parsed.tiger_id or "").strip()
        account = str(
            raw_credentials.get("account")
            or raw_credentials.get("tiger_account")
            or parsed.tiger_account
            or ""
        ).strip()
        license_value = str(raw_credentials.get("license") or parsed.tiger_license or "").strip()
        extras = {
            "env": raw_credentials.get("env") or parsed.tiger_env or "PAPER",
            "private_key": raw_credentials.get("private_key") or parsed.tiger_private_key or "",
            "private_key_path": raw_credentials.get("private_key_path") or parsed.tiger_private_key_path or "",
            "props_path": raw_credentials.get("props_path") or parsed.tiger_props_path or "",
            "secret_key": raw_credentials.get("secret_key") or parsed.tiger_secret_key or "",
            "token_path": raw_credentials.get("token_path") or parsed.tiger_token_path or "",
        }
        extras.update(
            {
                str(k): v
                for k, v in raw_credentials.items()
                if str(k)
                not in {
                    "tiger_id",
                    "account",
                    "tiger_account",
                    "license",
                    "env",
                    "private_key",
                    "private_key_path",
                    "props_path",
                    "secret_key",
                    "token_path",
                }
            }
        )
        if not tiger_id or not account or not license_value:
            raise m.HTTPException(
                status_code=400,
                detail="missing_broker_credentials (need tiger_id/account/license for tiger)",
            )
        if not extras.get("private_key") and not extras.get("private_key_path") and not extras.get("props_path"):
            raise m.HTTPException(
                status_code=400,
                detail="missing_broker_credentials (need private_key, private_key_path, or props_path for tiger)",
            )
        return BrokerCredentials(app_key=tiger_id, app_secret=license_value, access_token=account, extras=extras)

    if provider in {"fosun", "fosunwealth"}:
        api_key = str(raw_credentials.get("api_key") or raw_credentials.get("fosun_api_key") or parsed.fosun_api_key or "").strip()
        base_url = str(raw_credentials.get("base_url") or raw_credentials.get("fosun_base_url") or parsed.fosun_base_url or "").strip()
        sub_account_id = str(
            raw_credentials.get("sub_account_id")
            or raw_credentials.get("fosun_sub_account_id")
            or parsed.fosun_sub_account_id
            or ""
        ).strip()
        server_public_key = str(
            raw_credentials.get("server_public_key")
            or raw_credentials.get("fosun_server_public_key")
            or parsed.fosun_server_public_key
            or ""
        ).strip()
        client_private_key = str(
            raw_credentials.get("client_private_key")
            or raw_credentials.get("fosun_client_private_key")
            or parsed.fosun_client_private_key
            or ""
        ).strip()
        extras = {
            "base_url": base_url,
            "sub_account_id": sub_account_id,
            "client_id": raw_credentials.get("client_id") or raw_credentials.get("fosun_client_id") or parsed.fosun_client_id or "",
            "server_public_key": server_public_key,
            "client_private_key": client_private_key,
            "sdk_type": raw_credentials.get("sdk_type") or raw_credentials.get("fosun_sdk_type") or parsed.fosun_sdk_type or "",
            "apply_account_id": raw_credentials.get("apply_account_id") or raw_credentials.get("fosun_apply_account_id") or parsed.fosun_apply_account_id or "",
            "option_apply_account_id": raw_credentials.get("option_apply_account_id")
            or raw_credentials.get("fosun_option_apply_account_id")
            or parsed.fosun_option_apply_account_id
            or "",
        }
        extras.update(
            {
                str(k): v
                for k, v in raw_credentials.items()
                if str(k)
                not in {
                    "api_key",
                    "fosun_api_key",
                    "base_url",
                    "fosun_base_url",
                    "sub_account_id",
                    "fosun_sub_account_id",
                    "client_id",
                    "fosun_client_id",
                    "server_public_key",
                    "fosun_server_public_key",
                    "client_private_key",
                    "fosun_client_private_key",
                    "sdk_type",
                    "fosun_sdk_type",
                    "apply_account_id",
                    "fosun_apply_account_id",
                    "option_apply_account_id",
                    "fosun_option_apply_account_id",
                }
            }
        )
        if not api_key or not base_url or not sub_account_id:
            raise m.HTTPException(
                status_code=400,
                detail="missing_broker_credentials (need api_key/base_url/sub_account_id for fosun)",
            )
        if not server_public_key or not client_private_key:
            raise m.HTTPException(
                status_code=400,
                detail="missing_broker_credentials (need server_public_key/client_private_key PEM for fosun SDK)",
            )
        return BrokerCredentials(app_key=api_key, app_secret="", access_token=sub_account_id, extras=extras)

    key = str(
        raw_credentials.get("app_key")
        or raw_credentials.get("longport_app_key")
        or parsed.longport_app_key
        or m.live_settings.LONGPORT_APP_KEY
        or ""
    ).strip()
    secret = str(
        raw_credentials.get("app_secret")
        or raw_credentials.get("longport_app_secret")
        or parsed.longport_app_secret
        or m.live_settings.LONGPORT_APP_SECRET
        or ""
    ).strip()
    token = str(
        raw_credentials.get("access_token")
        or raw_credentials.get("longport_access_token")
        or parsed.longport_access_token
        or m.live_settings.LONGPORT_ACCESS_TOKEN
        or ""
    ).strip()
    if not key or not secret or not token:
        raise m.HTTPException(
            status_code=400,
            detail="missing_broker_credentials (need app_key/app_secret/access_token or configured LongPort env)",
        )
    extras = {
        str(k): v
        for k, v in raw_credentials.items()
        if str(k) not in {"app_key", "app_secret", "access_token", "longport_app_key", "longport_app_secret", "longport_access_token"}
    }
    return BrokerCredentials(app_key=key, app_secret=secret, access_token=token, extras=extras)


def _longbridge_market_data_ready(m: Any, owner_id: str | None = None) -> bool:
    if _env_bool("PUBLIC_MARKET_DATA_ONLY", "0"):
        return False
    owner = str(owner_id or "").strip()
    if owner:
        try:
            rec = m.ACCOUNT_REGISTRY.get_account_record(owner_id=owner)
            active = str(getattr(rec, "broker_provider", "") or "").strip().lower()
            if active == "longport":
                active = "longbridge"
            if active != "longbridge":
                return False
            creds = getattr(rec, "credentials", None)
            if getattr(rec, "manual_disconnected", False):
                return False
            return bool(
                str(getattr(creds, "app_key", "") or "").strip()
                and str(getattr(creds, "app_secret", "") or "").strip()
                and str(getattr(creds, "access_token", "") or "").strip()
            )
        except Exception:
            return False
    try:
        active = str(getattr(m, "ACTIVE_BROKER_ID", "") or m.live_settings.active_broker()).strip().lower()
    except Exception:
        active = "longbridge"
    if active == "longport":
        active = "longbridge"
    if active != "longbridge":
        return False
    try:
        rec = m.ACCOUNT_REGISTRY.get_account_record(getattr(m, "DEFAULT_ACCOUNT_ID", None))
        creds = getattr(rec, "credentials", None)
        if getattr(rec, "manual_disconnected", False):
            return False
        return bool(
            str(getattr(creds, "app_key", "") or "").strip()
            and str(getattr(creds, "app_secret", "") or "").strip()
            and str(getattr(creds, "access_token", "") or "").strip()
        )
    except Exception:
        try:
            return not bool(m.live_settings.missing_longport_fields())
        except Exception:
            key = str(os.getenv("LONGPORT_APP_KEY", "")).strip()
            secret = str(os.getenv("LONGPORT_APP_SECRET", "")).strip()
            token = str(os.getenv("LONGPORT_ACCESS_TOKEN", "")).strip()
            return bool(key and secret and token)


def _owner_quote_context(m: Any, owner_id: str | None) -> Any | None:
    owner = str(owner_id or "").strip()
    if not owner:
        return None
    try:
        account_id = m.ACCOUNT_REGISTRY.get_default_account_id(owner_id=owner)
        qctx, _ = m.ensure_contexts(account_id, owner_id=owner)
        return qctx
    except Exception:
        return None


_MARKET_SOURCE_LABELS = {
    "longbridge": "Longbridge",
    "longport": "Longbridge",
    "polygon": "Polygon.io market data",
    "twelvedata": "Twelve Data market data",
    "tencent_hk": "Tencent HK public",
    "tencent_index": "Tencent A-share index",
    "mootdx": "mootdx / Tongdaxin public",
    "eastmoney": "EastMoney public",
    "yahoo": "Yahoo Finance public",
    "akshare": "AkShare public",
    "stooq": "Stooq public",
    "cn_local_cache": "Local CN cache",
    "hk_local_cache": "Local HK last-good cache",
    "us_local_cache": "Local US last-good cache",
}


def _market_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("%", "").strip()
            if not v or v in {"-", "--", "nan", "None"}:
                return None
        out = float(v)
        if out == out and abs(out) != float("inf"):
            return out
    except Exception:
        pass
    return None


def _normalize_market_snap_row(row: Any, symbol: str, name: str, default_source: str) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    sym = str(symbol or row.get("symbol") or "").strip().upper()
    if not sym:
        return None
    last = _market_float(row.get("last"))
    if last is None:
        last = _market_float(row.get("price") or row.get("close") or row.get("last_done"))
    if last is None:
        return None
    prev = _market_float(row.get("prev_close") or row.get("previous_close"))
    change_pct = _market_float(row.get("change_pct"))
    if change_pct is None and prev and prev > 0:
        change_pct = round((last - prev) / prev * 100.0, 2)
    if change_pct is None:
        change_pct = 0.0

    source = str(row.get("source") or default_source or "public").strip().lower()
    source_label = str(row.get("source_label") or _MARKET_SOURCE_LABELS.get(source, source or "public")).strip()
    out = dict(row)
    out.update(
        {
            "symbol": sym,
            "name": str(name or row.get("name") or sym),
            "last": last,
            "change_pct": round(float(change_pct), 2),
            "source": source,
            "source_label": source_label,
            "price_type": str(row.get("price_type") or source_label or "public snapshot"),
        }
    )
    if prev is not None:
        out["prev_close"] = prev
    return out


def _public_market_snap_resilient(symbols: list[tuple[str, str]], overall_timeout: float = 7.5) -> list[dict[str, Any]]:
    """Fetch public quotes per symbol so one slow provider cannot empty the whole market panel."""
    if not symbols:
        return []
    svc = get_public_market_data_service()
    ordered_symbols = [(str(sym).strip().upper(), name) for sym, name in symbols if str(sym or "").strip()]

    def _fallback_one(sym: str, name: str) -> dict[str, Any] | None:
        # Last-good cache is local and fast. It keeps dashboard/market usable when public HTTP sources stall.
        if sym.endswith(".US"):
            try:
                item = getattr(svc, "_us_cache_quote")(sym)
            except Exception:
                item = None
            row = _normalize_market_snap_row(item, sym, name, "us_local_cache")
            if row:
                return row
        if sym.endswith(".HK"):
            try:
                item = getattr(svc, "_hk_cache_quote")(sym)
            except Exception:
                item = None
            row = _normalize_market_snap_row(item, sym, name, "hk_local_cache")
            if row:
                return row
        return None

    def _fetch_one(sym: str, name: str) -> dict[str, Any] | None:
        resp = svc.quote([sym], source="auto")
        items = resp.get("items") if isinstance(resp, dict) else None
        if not isinstance(items, list) or not items:
            return None
        return _normalize_market_snap_row(items[0], sym, name, "public")

    rows_by_symbol: dict[str, dict[str, Any]] = {}
    pool = ThreadPoolExecutor(max_workers=max(1, min(6, len(symbols))))
    futures = {pool.submit(_fetch_one, sym, name): (sym, name) for sym, name in ordered_symbols}
    try:
        for future in as_completed(futures, timeout=max(1.0, float(overall_timeout))):
            sym, _name = futures[future]
            try:
                row = future.result(timeout=0.1)
            except Exception:
                row = None
            if row:
                rows_by_symbol[sym] = row
    except FuturesTimeoutError:
        pass
    finally:
        for future in futures:
            if not future.done():
                future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)

    for sym, name in ordered_symbols:
        if sym in rows_by_symbol:
            continue
        row = _fallback_one(sym, name)
        if row:
            rows_by_symbol[sym] = row
    return [rows_by_symbol[sym] for sym, _ in ordered_symbols if sym in rows_by_symbol]


def _market_snap_group_status(rows: list[dict[str, Any]], symbols: list[tuple[str, str]]) -> dict[str, Any]:
    requested = [str(sym).strip().upper() for sym, _ in symbols]
    available = {str(row.get("symbol") or "").strip().upper() for row in rows if isinstance(row, dict)}
    source_counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source_label") or row.get("source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
    return {
        "requested": len(requested),
        "available": len(available),
        "missing_symbols": [sym for sym in requested if sym not in available],
        "sources": source_counts,
        "public_fallback_used": any(str(row.get("source") or "").lower() not in {"longbridge", "longport"} for row in rows if isinstance(row, dict)),
        "broker_required": False,
    }


def _merge_market_rows_by_symbol(
    preferred: list[dict[str, Any]],
    fallback: list[dict[str, Any]],
    symbols: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    preferred_by_symbol = {
        str(row.get("symbol") or "").strip().upper(): dict(row)
        for row in preferred
        if isinstance(row, dict) and str(row.get("symbol") or "").strip()
    }
    fallback_by_symbol = {
        str(row.get("symbol") or "").strip().upper(): dict(row)
        for row in fallback
        if isinstance(row, dict) and str(row.get("symbol") or "").strip()
    }
    out: list[dict[str, Any]] = []
    for sym, _name in symbols:
        key = str(sym or "").strip().upper()
        if key in preferred_by_symbol:
            out.append(preferred_by_symbol[key])
        elif key in fallback_by_symbol:
            cached = dict(fallback_by_symbol[key])
            cached["stale"] = True
            cached["price_type"] = str(cached.get("price_type") or cached.get("source_label") or "cached snapshot")
            out.append(cached)
    return out


def _dashboard_market_cache_update(
    group: str,
    rows: list[dict[str, Any]],
    symbols: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    valid_rows = [dict(row) for row in rows if isinstance(row, dict) and _market_float(row.get("last")) is not None]
    with _DASHBOARD_MARKET_CACHE_LOCK:
        cached = [dict(row) for row in _DASHBOARD_MARKET_CACHE.get(group, []) if isinstance(row, dict)]
        merged = _merge_market_rows_by_symbol(valid_rows, cached, symbols)
        if merged:
            _DASHBOARD_MARKET_CACHE[group] = [dict(row) for row in merged]
        return merged


def _market_snap_with_public_fallback(
    m: Any,
    symbols: list[tuple[str, str]],
    owner_id: str | None = None,
) -> list[dict[str, Any]]:
    names_by_symbol = {str(sym).strip().upper(): name for sym, name in symbols}
    rows_by_symbol: dict[str, dict[str, Any]] = {}
    if _longbridge_market_data_ready(m, owner_id=owner_id):
        try:
            for row in m._market_snap(symbols, owner_id=owner_id) or []:
                if not isinstance(row, dict):
                    continue
                sym = str(row.get("symbol") or "").strip().upper()
                norm = _normalize_market_snap_row(row, sym, names_by_symbol.get(sym, ""), "longbridge")
                if norm:
                    rows_by_symbol[sym] = norm
            if len(rows_by_symbol) >= len(symbols):
                return [rows_by_symbol[str(sym).strip().upper()] for sym, _ in symbols if str(sym).strip().upper() in rows_by_symbol]
        except Exception:
            rows_by_symbol = {}

    missing = [(sym, name) for sym, name in symbols if str(sym or "").strip().upper() not in rows_by_symbol]
    if missing:
        for row in _public_market_snap_resilient(missing):
            sym = str(row.get("symbol") or "").strip().upper()
            norm = _normalize_market_snap_row(row, sym, names_by_symbol.get(sym, ""), "public")
            if sym and norm:
                rows_by_symbol[sym] = norm
    return [rows_by_symbol[str(sym).strip().upper()] for sym, _ in symbols if str(sym).strip().upper() in rows_by_symbol]


def _get_zone(name: str):
    key = name or "America/New_York"
    try:
        return ZoneInfo(key)
    except ZoneInfoNotFoundError:
        return timezone.utc


def _is_rth_bar(dt: datetime, *, tz_name: str, open_hour: int, open_minute: int, close_hour: int, close_minute: int) -> bool:
    ny = _get_zone("America/New_York")
    base = dt
    if base.tzinfo is None:
        base = base.replace(tzinfo=_get_zone(tz_name))
    et = base.astimezone(ny)
    t = et.timetz().replace(tzinfo=None)
    start = datetime(et.year, et.month, et.day, max(0, min(23, int(open_hour))), max(0, min(59, int(open_minute)))).time()
    end = datetime(et.year, et.month, et.day, max(0, min(23, int(close_hour))), max(0, min(59, int(close_minute)))).time()
    return start <= t < end


def _filter_rth_bars_for_qqq_0dte(bars: list[Any], cfg: Any) -> tuple[list[Any], int, int]:
    tz_name = str(getattr(cfg, "assume_bars_timezone", "America/New_York") or "America/New_York")
    open_hour = int(getattr(cfg, "rth_open_hour", 9))
    open_minute = int(getattr(cfg, "rth_open_minute", 30))
    close_hour = int(getattr(cfg, "rth_close_hour", 16))
    close_minute = int(getattr(cfg, "rth_close_minute", 0))
    out: list[Any] = []
    non_rth = 0
    for b in bars:
        dt = getattr(b, "date", None)
        if not isinstance(dt, datetime):
            non_rth += 1
            continue
        if _is_rth_bar(
            dt,
            tz_name=tz_name,
            open_hour=open_hour,
            open_minute=open_minute,
            close_hour=close_hour,
            close_minute=close_minute,
        ):
            out.append(b)
        else:
            non_rth += 1
    return out, len(out), non_rth


def _normalize_quantity_by_lot_size(qctx: Any, symbol: str, quantity: int) -> tuple[int, int]:
    """
    将下单数量修正为最小交易单位（lot_size）的整数倍。
    返回 (normalized_quantity, lot_size)。
    """
    qty = max(1, int(quantity))
    sym = str(symbol).strip().upper()
    # 用户要求：美股不做自动手数修正，保持原始数量。
    if sym.endswith(".US"):
        return qty, 1
    lot_size = 1
    try:
        m = _m()
        st = m.broker_get_static_info(qctx, [sym])
        if st:
            lot_size = max(1, int(getattr(st[0], "lot_size", 1) or 1))
    except Exception:
        lot_size = 1
    if lot_size <= 1:
        return qty, 1
    if qty % lot_size == 0:
        return qty, lot_size
    # 向上取整，避免因不足一手导致持续下单失败（例如港股 100 -> 200）。
    normalized = ((qty + lot_size - 1) // lot_size) * lot_size
    return max(lot_size, normalized), lot_size


def setup_config(owner_id: str) -> dict[str, Any]:
    m = _m()
    from pathlib import Path

    from config.user_env_store import apply_light_session_env_for_user, resolve_user_env_with_defaults

    root = Path(m.ROOT)
    apply_light_session_env_for_user(owner_id, root)
    env_data = resolve_user_env_with_defaults(owner_id, root)
    return build_setup_config_response(env_data=env_data, feishu_cfg={}, mask_secret=m._mask_secret)


def setup_save_config(body: dict[str, Any], owner_id: str) -> dict[str, Any]:
    m = _m()
    from pathlib import Path

    from config.user_env_store import apply_full_session_env_for_user, resolve_user_env_with_defaults, save_user_env

    root = Path(m.ROOT)
    parsed = SetupConfigBody.model_validate(body if isinstance(body, dict) else {})
    payload = parsed.model_dump()
    env_data = dict(resolve_user_env_with_defaults(owner_id, root))
    changed = apply_setup_env_updates(payload=payload, env_data=env_data, env_var_map=m.ENV_VAR_MAP)
    llm_api_key_raw = str(payload.get("llm_api_key") or "").strip()
    if llm_api_key_raw:
        provider = str(payload.get("tradingagents_llm_provider") or env_data.get("TRADINGAGENTS_LLM_PROVIDER") or "openai").strip().lower()
        provider_key_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
            "xai": "XAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "qwen": "DASHSCOPE_API_KEY",
            "glm": "ZHIPUAI_API_KEY",
            "azure": "AZURE_OPENAI_API_KEY",
        }
        target_env_key = provider_key_map.get(provider)
        if target_env_key:
            env_data[target_env_key] = llm_api_key_raw
            os.environ[target_env_key] = llm_api_key_raw
            changed.append(target_env_key)
    save_user_env(owner_id, env_data, root)
    apply_full_session_env_for_user(owner_id, root)
    return {"ok": True, "changed": changed, "restart_recommended": bool(changed)}


def setup_risk_config(body: dict[str, Any]) -> dict[str, Any]:
    parsed = SetupRiskConfigBody.model_validate(body if isinstance(body, dict) else {})
    cfg = load_config()
    updates = parsed.model_dump(exclude_none=True)
    for k, v in updates.items():
        setattr(cfg, k, v)
    save_config(cfg)
    return {"ok": True, "risk_config": cfg.to_dict()}


def setup_services_status(owner_id: str | None = None) -> dict[str, Any]:
    now_mono = time.monotonic()
    cache_key = str(owner_id or "").strip().lower()
    with _setup_services_status_cache_lock:
        cached = _setup_services_status_cache_by_owner.get(cache_key)
        if cached and now_mono < cached[0]:
            return cached[1]

    m = _m()
    auto_runtime_raw = m._auto_trader_runtime_status()
    auto_context = _auto_trader_status_context(owner_id)
    auto_runtime = (
        auto_runtime_raw
        if (not auto_context or _runtime_matches_account_context(auto_runtime_raw, auto_context))
        else _context_mismatch_runtime(auto_runtime_raw, auto_context)
    )
    try:
        out = build_setup_services_status(
            managed_processes=m._managed_processes,
            feishu_pid_file=m.FEISHU_PID_FILE,
            auto_trader_supervisor_pid_file=m.AUTO_TRADER_SUPERVISOR_PID_FILE,
            auto_runtime=auto_runtime,
            qqq_0dte_live_worker_pid_file=m.QQQ_0DTE_LIVE_WORKER_PID_FILE,
            qqq_0dte_live_runtime=m._qqq_0dte_live_runtime_status(owner_id=owner_id),
            qqq_1dte_live_worker_pid_file=m.QQQ_1DTE_LIVE_WORKER_PID_FILE,
            qqq_1dte_live_runtime=m._qqq_1dte_live_runtime_status(owner_id=owner_id),
            stock_options_swing_worker_pid_file=m.STOCK_OPTIONS_SWING_WORKER_PID_FILE,
            stock_options_swing_runtime=m._stock_options_swing_runtime_status(owner_id=owner_id),
        )
        with _setup_services_status_cache_lock:
            _setup_services_status_cache_by_owner[cache_key] = (
                time.monotonic() + _SETUP_SERVICES_STATUS_CACHE_TTL_SECONDS,
                out,
            )
        return out
    except Exception:
        with _setup_services_status_cache_lock:
            cached = _setup_services_status_cache_by_owner.get(cache_key)
            if cached:
                return cached[1]
        raise


def setup_public_ip() -> dict[str, Any]:
    providers = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
    ]
    errors: list[str] = []
    for url in providers:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MultiTrading/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                text = resp.read(128).decode("utf-8", errors="ignore").strip()
            if text:
                return {"ok": True, "ip": text, "source": url}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            errors.append(f"{url}: {e}")
    return {"ok": False, "ip": "", "source": "", "error": "; ".join(errors[-2:])}


def _frontend_dir(m: Any) -> str:
    return os.path.join(m.ROOT, "frontend")


def setup_convex_dev_status() -> dict[str, Any]:
    m = _m()
    return build_convex_dev_status(
        root=m.ROOT,
        frontend_dir=_frontend_dir(m),
        managed_processes=m._managed_processes,
        win_subprocess_silent_kwargs=m._win_subprocess_silent_kwargs,
    )


def setup_convex_dev_start() -> dict[str, Any]:
    m = _m()
    return start_convex_dev(
        root=m.ROOT,
        frontend_dir=_frontend_dir(m),
        managed_processes=m._managed_processes,
        win_subprocess_silent_kwargs=m._win_subprocess_silent_kwargs,
    )


def setup_convex_dev_stop() -> dict[str, Any]:
    m = _m()
    return stop_convex_dev(
        root=m.ROOT,
        frontend_dir=_frontend_dir(m),
        managed_processes=m._managed_processes,
        win_subprocess_silent_kwargs=m._win_subprocess_silent_kwargs,
    )


def setup_convex_dev_restart() -> dict[str, Any]:
    m = _m()
    return restart_convex_dev(
        root=m.ROOT,
        frontend_dir=_frontend_dir(m),
        managed_processes=m._managed_processes,
        win_subprocess_silent_kwargs=m._win_subprocess_silent_kwargs,
    )


def cn_market_data_provider_status() -> dict[str, Any]:
    return get_cn_market_data_service().provider_status()


def public_market_data_provider_status() -> dict[str, Any]:
    return get_public_market_data_service().provider_status()


def market_data_provider_status() -> dict[str, Any]:
    cn = get_cn_market_data_service().provider_status()
    public = get_public_market_data_service().provider_status()
    out = dict(cn)
    out["public_market"] = public
    return out


def cn_market_data_quote(*, symbols: str | list[str], source: str = "auto") -> dict[str, Any]:
    return get_cn_market_data_service().quote(symbols=symbols, source=source)


def public_market_data_quote(*, symbols: str | list[str], source: str = "auto") -> dict[str, Any]:
    return get_public_market_data_service().quote(symbols=symbols, source=source)


def cn_market_data_klines(
    *,
    symbol: str,
    period: str = "1d",
    adjust: str = "qfq",
    days: int = 180,
    limit: int = 0,
    source: str = "auto",
) -> dict[str, Any]:
    return get_cn_market_data_service().klines(
        symbol=symbol,
        period=period,
        adjust=adjust,
        days=days,
        limit=limit,
        source=source,
    )


def cn_market_data_valuation(*, symbol: str, source: str = "auto") -> dict[str, Any]:
    return get_cn_market_data_service().valuation(symbol=symbol, source=source)


def public_market_data_klines(
    *,
    symbol: str,
    period: str = "1d",
    days: int = 180,
    limit: int = 0,
    source: str = "auto",
) -> dict[str, Any]:
    return get_public_market_data_service().klines(
        symbol=symbol,
        period=period,
        days=days,
        limit=limit,
        source=source,
    )


def cn_market_data_universe(*, market: str = "cn") -> dict[str, Any]:
    return get_cn_market_data_service().universe(market=market)


def setup_install_cn_market_data_provider(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = SetupCnMarketDataInstallBody.model_validate(body if isinstance(body, dict) else {})
    provider_map = {
        "mootdx": ["tdxpy", "prettytable", "mootdx"],
        "akshare": ["akshare"],
        "baostock": ["baostock"],
        "tushare": ["tushare"],
        "all": ["tdxpy", "prettytable", "mootdx", "akshare", "baostock", "tushare"],
    }
    package_flags = {
        "mootdx": ["--no-deps"],
    }
    allowed = {"mootdx", "tdxpy", "prettytable", "akshare", "baostock", "tushare"}
    requested: list[str] = []
    provider = str(parsed.provider or "").strip().lower()
    if provider:
        requested.extend(provider_map.get(provider, []))
    if parsed.packages:
        requested.extend(str(x or "").strip().lower() for x in parsed.packages)
    packages: list[str] = []
    for pkg in requested:
        if pkg in allowed and pkg not in packages:
            packages.append(pkg)
    if not packages:
        raise m.HTTPException(status_code=400, detail="no_allowed_packages_requested")

    installed: list[dict[str, Any]] = []
    try:
        for pkg in packages:
            cmd = [m.sys.executable, "-m", "pip", "install", *(package_flags.get(pkg) or []), pkg]
            proc = m.subprocess.run(
                cmd,
                cwd=m.ROOT,
                capture_output=True,
                text=True,
                timeout=600,
            )
            installed.append(
                {
                    "package": pkg,
                    "cmd": " ".join(cmd),
                    "returncode": int(proc.returncode),
                    "stdout_tail": str(proc.stdout or "")[-3000:],
                    "stderr_tail": str(proc.stderr or "")[-3000:],
                }
            )
            if int(proc.returncode) != 0:
                break
    except Exception as exc:
        return {
            "ok": False,
            "packages": packages,
            "installed": installed,
            "error": str(exc),
            "hint": "请确认后端 Python 环境可访问 pip 和网络，或在终端手动安装。",
        }
    ok = bool(installed) and all(int(x.get("returncode") or 0) == 0 for x in installed)
    return {
        "ok": ok,
        "packages": packages,
        "installed": installed,
        "returncode": 0 if ok else int((installed[-1] if installed else {}).get("returncode") or 1),
        "stdout_tail": "\n".join(str(x.get("stdout_tail") or "") for x in installed)[-6000:],
        "stderr_tail": "\n".join(str(x.get("stderr_tail") or "") for x in installed)[-6000:],
        "restart_recommended": ok,
        "provider_status": get_cn_market_data_service().provider_status(),
    }


def setup_longport_diagnostics(probe: bool = False, owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    owner = str(owner_id or "").strip()
    probe_ok = None
    probe_error = None
    accounts: list[dict[str, Any]] = []
    default_account_id: str | None = None
    broker_provider = str(getattr(m, "ACTIVE_BROKER_ID", "longbridge") or "longbridge")

    if owner:
        try:
            accounts = m.ACCOUNT_REGISTRY.list_accounts(owner_id=owner) if hasattr(m, "ACCOUNT_REGISTRY") else []
        except Exception:
            accounts = []
        if accounts:
            try:
                default_account_id = m.ACCOUNT_REGISTRY.get_default_account_id(owner_id=owner)
            except Exception:
                default_account_id = str(accounts[0].get("account_id") or "").strip() or None
            default_row = next(
                (
                    row
                    for row in accounts
                    if isinstance(row, dict)
                    and str(row.get("account_id") or "").strip() == str(default_account_id or "").strip()
                ),
                accounts[0],
            )
            broker_provider = str(default_row.get("broker_provider") or broker_provider)
        if probe:
            if not default_account_id:
                probe_ok = False
                probe_error = "account_not_registered"
            else:
                try:
                    m.ACCOUNT_REGISTRY.ensure_contexts(default_account_id, owner_id=owner)
                    probe_ok = True
                except Exception as e:
                    probe_ok = False
                    probe_error = str(e)
            try:
                accounts = m.ACCOUNT_REGISTRY.list_accounts(owner_id=owner) if hasattr(m, "ACCOUNT_REGISTRY") else []
            except Exception:
                accounts = []
    else:
        if probe:
            try:
                m.ensure_contexts()
                probe_ok = True
            except Exception as e:
                probe_ok = False
                probe_error = str(e)
        runtime_ctx = m._collect_longport_runtime_state()
        accounts = runtime_ctx.get("accounts") if isinstance(runtime_ctx.get("accounts"), list) else []
        default_account_id = runtime_ctx.get("default_account_id")
        broker_provider = str(runtime_ctx.get("broker_provider") or broker_provider)

    active_connections = sum(
        int(bool(row.get("quote_ready"))) + int(bool(row.get("trade_ready")))
        for row in accounts
        if isinstance(row, dict)
    )
    usage_pct = round(active_connections / max(1, int(m.LONGPORT_CONNECTION_LIMIT)) * 100, 2)
    quote_ready = any(bool(row.get("quote_ready")) for row in accounts if isinstance(row, dict))
    trade_ready = any(bool(row.get("trade_ready")) for row in accounts if isinstance(row, dict))
    default_row = next(
        (
            row
            for row in accounts
            if isinstance(row, dict) and str(row.get("account_id") or "").strip() == str(default_account_id or "").strip()
        ),
        accounts[0] if accounts else {},
    )
    last_error = default_row.get("last_error") if isinstance(default_row, dict) else None
    last_init_at = default_row.get("last_init_at") if isinstance(default_row, dict) else None

    show_aux_processes = bool(accounts) or not owner
    mcp_pid = read_pid_file(m.MCP_PID_FILE) if show_aux_processes else None
    feishu_pid = read_pid_file(m.FEISHU_PID_FILE) if show_aux_processes else None
    auto_trader_pid = read_pid_file(m.AUTO_TRADER_PID_FILE) if show_aux_processes else None
    auto_trader_supervisor_pid = read_pid_file(m.AUTO_TRADER_SUPERVISOR_PID_FILE) if show_aux_processes else None
    out = build_broker_diagnostics_response(
        connection_limit=m.LONGPORT_CONNECTION_LIMIT,
        active_connections=active_connections,
        usage_pct=usage_pct,
        quote_ready=quote_ready,
        trade_ready=trade_ready,
        last_error=last_error,
        last_init_at=last_init_at,
        probe_requested=probe,
        probe_ok=probe_ok,
        probe_error=probe_error,
        mcp_pid=mcp_pid,
        feishu_pid=feishu_pid,
        auto_trader_pid=auto_trader_pid,
        auto_trader_supervisor_pid=auto_trader_supervisor_pid,
        mcp_running=is_pid_alive(mcp_pid),
        feishu_running=is_pid_alive(feishu_pid),
        auto_trader_running=is_pid_alive(auto_trader_pid),
        auto_trader_supervisor_running=is_pid_alive(auto_trader_supervisor_pid),
        mcp_pid_file=m.MCP_PID_FILE,
        feishu_pid_file=m.FEISHU_PID_FILE,
        auto_trader_pid_file=m.AUTO_TRADER_PID_FILE,
        auto_trader_supervisor_pid_file=m.AUTO_TRADER_SUPERVISOR_PID_FILE,
        auto_trader_supervisor_status_file=m.AUTO_TRADER_SUPERVISOR_STATUS_FILE,
        auto_trader_worker_runtime_file=m.AUTO_TRADER_WORKER_RUNTIME_FILE,
        gateway_enabled=m._gateway_enabled(),
        broker_provider=broker_provider,
    )
    if owner and not accounts:
        out["alert_level"] = "notice"
        out["recommendations"] = ["当前 owner 尚未注册券商账户，请先在“账户与券商”中配置 Broker API。"]
        out["note"] = "诊断已按当前 owner 隔离；未注册券商账户时不会复用其他 owner 的 API 连接。"
    out["owner_id"] = owner or None
    out["owner_scoped"] = bool(owner)
    out["account_registered"] = bool(accounts)
    out["default_account_id"] = default_account_id
    out["accounts"] = accounts
    return out


def setup_accounts(owner_id: str) -> dict[str, Any]:
    m = _m()
    items = m.ACCOUNT_REGISTRY.list_accounts(owner_id=owner_id) if hasattr(m, "ACCOUNT_REGISTRY") else []
    try:
        default_account_id = (
            m.ACCOUNT_REGISTRY.get_default_account_id(owner_id=owner_id) if hasattr(m, "ACCOUNT_REGISTRY") else None
        )
    except Exception:
        default_account_id = None
    return {"ok": True, "default_account_id": default_account_id, "accounts": items}


def _sync_fee_runtime_after_account_change() -> None:
    try:
        from api.services import fee_broker_profiles as fbp

        fbp.sync_runtime_fee_from_accounts(persist_effective_mirror=False)
    except Exception:
        pass


def _clear_setup_services_status_cache(owner_id: str | None = None) -> None:
    owner = str(owner_id or "").strip().lower()
    with _setup_services_status_cache_lock:
        if owner:
            _setup_services_status_cache_by_owner.pop(owner, None)
        else:
            _setup_services_status_cache_by_owner.clear()


def _sync_trading_worker_configs_to_account(owner_id: str | None, account_id: str | None) -> list[dict[str, Any]]:
    owner = _normalize_owner_id(owner_id)
    aid = str(account_id or "").strip()
    if not owner or not aid:
        return []

    m = _m()
    try:
        rec = m.ACCOUNT_REGISTRY.get_account_record(account_id=aid, owner_id=owner)
    except Exception as e:
        return [{"module": "all", "status": f"error:{e}"}]

    ctx = {
        "owner_id": owner,
        "account_id": str(getattr(rec, "account_id", "") or aid).strip() or aid,
        "broker_provider": _normalize_broker_provider(getattr(rec, "broker_provider", "")),
    }
    results: list[dict[str, Any]] = []

    for module, write_one in (
        ("qqq_0dte", lambda: _write_json_path(_qqq_live_worker_config_path("0dte", owner), {**qqq_0dte_live_worker_config_get(owner_id=owner), **ctx})),
        ("qqq_1dte", lambda: _write_json_path(_qqq_live_worker_config_path("1dte", owner), {**qqq_1dte_live_worker_config_get(owner_id=owner), **ctx})),
        ("stock_options_swing", lambda: _write_json_path(_stock_options_swing_config_path(owner), {**stock_options_swing_config_get(owner_id=owner), **ctx})),
    ):
        try:
            write_one()
            results.append({"module": module, "status": "synced", **ctx})
        except Exception as e:
            results.append({"module": module, "status": f"error:{e}", **ctx})
    return results


def setup_account_register(body: dict[str, Any], owner_id: str) -> dict[str, Any]:
    m = _m()
    parsed = SetupAccountRegisterBody.model_validate(body if isinstance(body, dict) else {})
    account_id = str(parsed.account_id or "").strip()
    if not account_id:
        raise m.HTTPException(status_code=400, detail="account_id_required")
    broker_provider = str(parsed.broker_provider or "longbridge").strip().lower() or "longbridge"
    credentials = _broker_credentials_from_setup_body(m, parsed, broker_provider)
    try:
        rec = m.ACCOUNT_REGISTRY.register_account(
            owner_id=owner_id,
            account_id=account_id,
            broker_provider=broker_provider,
            credentials=credentials,
            is_default=bool(parsed.is_default),
            overwrite=bool(parsed.overwrite),
        )
    except ValueError as e:
        raise m.HTTPException(status_code=400, detail=str(e))
    if owner_id == "__system__" and hasattr(m, "_sync_runtime_state_from_default_account"):
        m._sync_runtime_state_from_default_account()
    _sync_fee_runtime_after_account_change()
    return {
        "ok": True,
        "account": {
            "account_id": rec.account_id,
            "broker_provider": rec.broker_provider,
            "is_default": rec.is_default,
            "status": rec.status,
        },
        "default_account_id": m.ACCOUNT_REGISTRY.get_default_account_id(owner_id=owner_id),
    }


def setup_account_connect(account_id: str, owner_id: str) -> dict[str, Any]:
    m = _m()
    aid = str(account_id or "").strip()
    if not aid:
        raise m.HTTPException(status_code=400, detail="account_id_required")
    try:
        previous_default_account_id = m.ACCOUNT_REGISTRY.get_default_account_id(owner_id=owner_id)
    except Exception:
        previous_default_account_id = None
    account_switched = bool(previous_default_account_id) and str(previous_default_account_id) != aid
    pre_connect_stop_results = (
        _stop_auto_workers_after_account_connect(m, account_switched=True)
        if account_switched
        else []
    )
    try:
        qctx, tctx, rec = m.ACCOUNT_REGISTRY.connect_account(aid, owner_id=owner_id)
    except ValueError as e:
        _clear_setup_services_status_cache(owner_id)
        raise m.HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _clear_setup_services_status_cache(owner_id)
        raise m.HTTPException(status_code=400, detail=str(e))
    post_connect_stop_results = _stop_auto_workers_after_account_connect(m, account_switched=False)
    auto_stop_results = [*pre_connect_stop_results, *post_connect_stop_results]
    synced_worker_configs = _sync_trading_worker_configs_to_account(owner_id=owner_id, account_id=rec.account_id)
    try:
        default_account_id = m.ACCOUNT_REGISTRY.get_default_account_id(owner_id=owner_id)
    except Exception:
        default_account_id = rec.account_id
    out = {
        "ok": True,
        "account_id": aid,
        "default_account_id": default_account_id,
        "is_default": bool(rec.is_default),
        "quote_ready": qctx is not None,
        "trade_ready": tctx is not None,
        "status": rec.status,
        "last_error": rec.last_error,
        "last_init_at": rec.last_init_at,
        "manual_disconnected": bool(rec.manual_disconnected),
        "account_switched": account_switched,
        "previous_default_account_id": previous_default_account_id,
        "auto_stopped_processes": auto_stop_results,
        "auto_stopped_workers": auto_stop_results,
        "synced_worker_configs": synced_worker_configs,
    }
    _sync_fee_runtime_after_account_change()
    _clear_setup_services_status_cache(owner_id)
    return out


def _stop_auto_workers_after_account_connect(m: Any, *, account_switched: bool) -> list[dict[str, Any]]:
    runtime = {}
    try:
        runtime = m._auto_trader_runtime_status()
    except Exception:
        runtime = {}
    worker = runtime.get("worker") if isinstance(runtime, dict) else {}
    worker_status = runtime.get("worker_status") if isinstance(runtime, dict) else {}
    worker_has_account = bool(
        str((worker if isinstance(worker, dict) else {}).get("account_id") or "").strip()
        or str((worker_status if isinstance(worker_status, dict) else {}).get("account_id") or "").strip()
    )
    auto_trader_running = bool((runtime if isinstance(runtime, dict) else {}).get("worker_running")) or bool(
        (runtime if isinstance(runtime, dict) else {}).get("supervisor_running")
    )
    stop_auto_trader_for_missing_context = bool(auto_trader_running and not worker_has_account)
    if not account_switched and not stop_auto_trader_for_missing_context:
        return []

    process_stops: list[tuple[str, Any]] = [
        ("auto_trader", getattr(m, "_stop_auto_trader_worker", None)),
        ("qqq_0dte", getattr(m, "_stop_qqq_0dte_live_worker", None)),
        ("qqq_1dte", getattr(m, "_stop_qqq_1dte_live_worker", None)),
        ("stock_options_swing", getattr(m, "_stop_stock_options_swing_worker", None)),
    ]
    if not account_switched:
        process_stops = [process_stops[0]]
    stop_targets = [(process_name, stop_fn) for process_name, stop_fn in process_stops if callable(stop_fn)]
    if not stop_targets:
        return []

    auto_stop_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(stop_targets)) as pool:
        futures = {
            pool.submit(stop_fn, timeout_seconds=5.0): process_name
            for process_name, stop_fn in stop_targets
        }
        for fut in as_completed(futures):
            process_name = futures[fut]
            try:
                stop_status = str(fut.result())
            except Exception as e:
                stop_status = f"error:{e}"
            reason = "account_switched" if account_switched else "missing_account_context"
            auto_stop_results.append(
                {
                    "process": process_name,
                    "stop_status": stop_status,
                    "reason": reason,
                }
            )
    return auto_stop_results


def setup_account_disconnect(account_id: str, owner_id: str) -> dict[str, Any]:
    m = _m()
    aid = str(account_id or "").strip()
    if not aid:
        raise m.HTTPException(status_code=400, detail="account_id_required")
    try:
        rec = m.ACCOUNT_REGISTRY.disconnect_account(aid, owner_id=owner_id)
    except ValueError as e:
        raise m.HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise m.HTTPException(status_code=400, detail=str(e))
    auto_stop_results: list[dict[str, Any]] = []
    all_accounts_disconnected = not bool(m.ACCOUNT_REGISTRY.has_connected_account(owner_id=owner_id))
    if all_accounts_disconnected:
        process_stops: list[tuple[str, Any]] = [
            ("auto_trader", getattr(m, "_stop_auto_trader_worker", None)),
            ("qqq_0dte", getattr(m, "_stop_qqq_0dte_live_worker", None)),
            ("qqq_1dte", getattr(m, "_stop_qqq_1dte_live_worker", None)),
            ("stock_options_swing", getattr(m, "_stop_stock_options_swing_worker", None)),
        ]
        stop_targets = [(process_name, stop_fn) for process_name, stop_fn in process_stops if callable(stop_fn)]

        # 仅在“全部账户都断连”时并行停止所有自动交易进程。
        if stop_targets:
            with ThreadPoolExecutor(max_workers=len(stop_targets)) as pool:
                futures = {
                    pool.submit(stop_fn, timeout_seconds=5.0): process_name
                    for process_name, stop_fn in stop_targets
                }
                for fut in as_completed(futures):
                    process_name = futures[fut]
                    try:
                        stop_status = str(fut.result())
                    except Exception as e:
                        stop_status = f"error:{e}"
                    auto_stop_results.append(
                        {
                            "process": process_name,
                            "stop_status": stop_status,
                        }
                    )
    out = {
        "ok": True,
        "account_id": aid,
        "quote_ready": rec.quote_ctx is not None,
        "trade_ready": rec.trade_ctx is not None,
        "status": rec.status,
        "last_error": rec.last_error,
        "manual_disconnected": bool(rec.manual_disconnected),
        "all_accounts_disconnected": all_accounts_disconnected,
        "auto_stopped_processes": auto_stop_results,
        # 兼容历史前端字段，后续可移除。
        "auto_stopped_workers": auto_stop_results,
    }
    _sync_fee_runtime_after_account_change()
    _clear_setup_services_status_cache(owner_id)
    return out


def setup_start_services(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    parsed = SetupStartBody.model_validate(body if isinstance(body, dict) else {})
    def _assert_account_connected_for_start(name: str, account_id: str | None = None) -> None:
        try:
            rec = m.ACCOUNT_REGISTRY.get_account_record(account_id=account_id, owner_id=owner_id)
        except ValueError as e:
            raise m.HTTPException(status_code=400, detail=f"{name}_account_not_ready: {e}")
        if bool(rec.manual_disconnected) or str(rec.status or "").strip().lower() == "disconnected":
            raise m.HTTPException(
                status_code=400,
                detail=f"{name}_account_disconnected_manual_connect_required: {rec.account_id}",
            )

    needs_auto_trading_account = bool(
        parsed.enable_auto_trader
        or parsed.enable_qqq_0dte_live
        or parsed.enable_qqq_1dte_live
        or parsed.enable_stock_options_swing
    )
    if needs_auto_trading_account:
        if not str(owner_id or "").strip():
            raise m.HTTPException(status_code=401, detail="unauthorized")
        try:
            default_account_id = m.ACCOUNT_REGISTRY.get_default_account_id(owner_id=owner_id)
        except Exception:
            default_account_id = None
        if default_account_id:
            _sync_trading_worker_configs_to_account(owner_id=owner_id, account_id=default_account_id)
            _clear_setup_services_status_cache(owner_id)
        if bool(parsed.enable_auto_trader):
            _assert_account_connected_for_start("auto_trader", None)
            _assert_stock_auto_trader_safety(
                owner_id=owner_id,
                config=m._auto_trader_service_for_owner(owner_id).get_config(),
            )
        if bool(parsed.enable_qqq_0dte_live):
            cfg0 = qqq_0dte_live_worker_config_get(owner_id=owner_id)
            cfg0_account_id = str((cfg0 or {}).get("account_id") or "").strip() or None
            _assert_account_connected_for_start("qqq_0dte", cfg0_account_id)
        if bool(parsed.enable_qqq_1dte_live):
            cfg1 = qqq_1dte_live_worker_config_get(owner_id=owner_id)
            cfg1_account_id = str((cfg1 or {}).get("account_id") or "").strip() or None
            _assert_account_connected_for_start("qqq_1dte", cfg1_account_id)
        if bool(parsed.enable_stock_options_swing):
            cfgs = stock_options_swing_config_get(owner_id=owner_id)
            cfgs_account_id = str((cfgs or {}).get("account_id") or "").strip() or None
            _assert_account_connected_for_start("stock_options_swing", cfgs_account_id)
    return start_services(
        start_feishu_bot=bool(parsed.start_feishu_bot),
        enable_auto_trader=bool(parsed.enable_auto_trader),
        enable_qqq_0dte_live=bool(parsed.enable_qqq_0dte_live),
        enable_qqq_1dte_live=bool(parsed.enable_qqq_1dte_live),
        enable_stock_options_swing=bool(parsed.enable_stock_options_swing),
        auto_trader=m._auto_trader_service_for_owner(owner_id),
        start_auto_trader_worker=m._start_auto_trader_worker,
        start_qqq_0dte_live_worker=m._start_qqq_0dte_live_worker,
        start_qqq_1dte_live_worker=m._start_qqq_1dte_live_worker,
        start_stock_options_swing_worker=m._start_stock_options_swing_worker,
        managed_processes=m._managed_processes,
        root=m.ROOT,
        mcp_dir=m.MCP_DIR,
        win_subprocess_silent_kwargs=m._win_subprocess_silent_kwargs,
        owner_id=owner_id,
    )


def setup_stop_services(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    parsed = SetupStopBody.model_validate(body if isinstance(body, dict) else {})
    requested = {
        "stocks": bool(parsed.stop_auto_trader),
        "options-0dte": bool(parsed.stop_qqq_0dte_live),
        "options-1dte": bool(parsed.stop_qqq_1dte_live),
        "options-swing": bool(parsed.stop_stock_options_swing),
    }
    # Stop is a defensive operation. If a global worker is still alive after an
    # account switch, allow the current owner to stop it even when the runtime
    # snapshot belongs to the previous account context.
    return stop_services(
        stop_auto_trader=bool(parsed.stop_auto_trader),
        stop_qqq_0dte_live=bool(parsed.stop_qqq_0dte_live),
        stop_qqq_1dte_live=bool(parsed.stop_qqq_1dte_live),
        stop_stock_options_swing=bool(parsed.stop_stock_options_swing),
        stop_feishu_bot=bool(parsed.stop_feishu_bot),
        auto_trader=m._auto_trader_service_for_owner(owner_id),
        stop_auto_trader_worker=m._stop_auto_trader_worker,
        stop_qqq_0dte_live_worker=m._stop_qqq_0dte_live_worker,
        stop_qqq_1dte_live_worker=m._stop_qqq_1dte_live_worker,
        stop_stock_options_swing_worker=m._stop_stock_options_swing_worker,
        stop_feishu_bot_managed_or_pidfile=m._stop_feishu_bot_managed_or_pidfile,
        wait_auto_trader_stopped=m._wait_auto_trader_processes_stopped,
        stop_confirm_timeout_seconds=8.0,
    )


def setup_stop_all_services(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    parsed = SetupStopAllBody.model_validate(body if isinstance(body, dict) else {})
    return stop_all_services(
        stop_backend=bool(parsed.stop_backend),
        stop_frontend=bool(parsed.stop_frontend),
        stop_feishu_bot=bool(parsed.stop_feishu_bot),
        stop_auto_trader=bool(parsed.stop_auto_trader),
        stop_qqq_0dte_live=bool(parsed.stop_qqq_0dte_live),
        stop_qqq_1dte_live=bool(parsed.stop_qqq_1dte_live),
        stop_stock_options_swing=bool(parsed.stop_stock_options_swing),
        auto_trader=m._auto_trader_service_for_owner(owner_id),
        stop_auto_trader_worker=m._stop_auto_trader_worker,
        stop_qqq_0dte_live_worker=m._stop_qqq_0dte_live_worker,
        stop_qqq_1dte_live_worker=m._stop_qqq_1dte_live_worker,
        stop_stock_options_swing_worker=m._stop_stock_options_swing_worker,
        stop_feishu_bot_managed_or_pidfile=m._stop_feishu_bot_managed_or_pidfile,
        watchdog_pause_file=m.WATCHDOG_PAUSE_FILE,
        watchdog_pid_file=m.WATCHDOG_PID_FILE,
        read_pid_file=read_pid_file,
        is_pid_alive=is_pid_alive,
        win_subprocess_silent_kwargs=m._win_subprocess_silent_kwargs,
    )


AUTO_TRADING_MODULES: dict[str, dict[str, Any]] = {
    "stocks": {
        "id": "stocks",
        "kind": "stock",
        "label": "股票自动交易",
        "legacy_status_path": "/auto-trader/status",
        "legacy_start_flag": "enable_auto_trader",
        "legacy_stop_flag": "stop_auto_trader",
    },
    "options-0dte": {
        "id": "options-0dte",
        "kind": "option",
        "label": "期权 0DTE",
        "legacy_status_path": "/strategy/qqq-0dte/live-worker-config",
        "legacy_start_flag": "enable_qqq_0dte_live",
        "legacy_stop_flag": "stop_qqq_0dte_live",
        "instance": "0dte",
    },
    "options-1dte": {
        "id": "options-1dte",
        "kind": "option",
        "label": "股票期权日内交易",
        "legacy_status_path": "/strategy/qqq-1dte/live-worker-config",
        "legacy_start_flag": "enable_qqq_1dte_live",
        "legacy_stop_flag": "stop_qqq_1dte_live",
        "instance": "1dte",
    },
    "options-swing": {
        "id": "options-swing",
        "kind": "option",
        "label": "股票期权中长线交易",
        "legacy_status_path": "/strategy/stock-options-swing/live-worker-config",
        "legacy_start_flag": "enable_stock_options_swing",
        "legacy_stop_flag": "stop_stock_options_swing",
        "instance": "stock_options_swing",
    },
}


def _auto_trading_module(module_id: str) -> dict[str, Any]:
    mid = str(module_id or "").strip().lower()
    meta = AUTO_TRADING_MODULES.get(mid)
    if not meta:
        raise _m().HTTPException(status_code=404, detail={"error": "unknown_auto_trading_module", "module_id": module_id})
    return meta


def _runtime_inner(runtime: Any) -> dict[str, Any]:
    if isinstance(runtime, dict) and isinstance(runtime.get("runtime"), dict):
        return runtime.get("runtime") or {}
    return runtime if isinstance(runtime, dict) else {}


def _runtime_matches_account_context(runtime: dict[str, Any] | None, context: dict[str, str] | None) -> bool:
    if not isinstance(runtime, dict):
        return False
    ctx = context if isinstance(context, dict) else {}
    inner = _runtime_inner(runtime)
    worker = runtime.get("worker") if isinstance(runtime.get("worker"), dict) else {}
    status = runtime.get("status") if isinstance(runtime.get("status"), dict) else {}
    for key in ("owner_id", "account_id", "broker_provider"):
        current = str(ctx.get(key) or "").strip().lower()
        got = str(
            runtime.get(key)
            or inner.get(key)
            or worker.get(key)
            or status.get(key)
            or ""
        ).strip().lower()
        if current and (not got or got != current):
            return False
    return True


def _auto_trader_service(owner_id: str | None = None) -> AutoTraderService:
    m = _m()
    factory = getattr(m, "_auto_trader_service_for_owner", None)
    if callable(factory):
        return factory(owner_id)
    return m.auto_trader


def _auto_trader_status_context(owner_id: str | None = None) -> dict[str, str]:
    m = _m()
    owner = str(owner_id or "").strip().lower()
    if not owner:
        return {}
    try:
        return m._auto_trader_signal_account_context(owner)
    except Exception:
        return {"owner_id": owner}


def _context_mismatch_runtime(raw: dict[str, Any] | None, context: dict[str, str]) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "status": "context_mismatch",
        "context_mismatch": True,
        "updated_at": raw.get("updated_at"),
        "worker_running": False,
        "worker_pid": None,
        "context": context,
    }


def _auto_trading_l3_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    max_level = str(os.getenv("OPENCLAW_MCP_MAX_LEVEL", "L2") or "L2").strip().upper()
    allow_l3 = str(os.getenv("OPENCLAW_MCP_ALLOW_L3", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
    env_token_configured = bool(str(os.getenv("OPENCLAW_MCP_L3_CONFIRMATION_TOKEN", "") or "").strip())
    cfg_token_configured = bool(str((config or {}).get("confirmation_token") or "").strip())
    return {
        "max_level": max_level,
        "allow_l3": allow_l3,
        "env_token_configured": env_token_configured,
        "module_token_configured": cfg_token_configured,
        "required_for_live_order": True,
        "ready": bool(max_level == "L3" and allow_l3 and (env_token_configured or cfg_token_configured)),
    }


def _auto_trading_option_config(module_id: str, owner_id: str | None = None) -> dict[str, Any]:
    if module_id == "options-swing":
        return stock_options_swing_config_get(owner_id=owner_id)
    meta = _auto_trading_module(module_id)
    inst = str(meta.get("instance") or "")
    return qqq_live_worker_config_get(inst, owner_id=owner_id)


def _auto_trading_option_runtime(module_id: str, services: dict[str, Any]) -> dict[str, Any]:
    if module_id == "options-swing":
        return services.get("stock_options_swing_runtime") if isinstance(services.get("stock_options_swing_runtime"), dict) else {}
    if module_id == "options-1dte":
        return services.get("qqq_1dte_live_runtime") if isinstance(services.get("qqq_1dte_live_runtime"), dict) else {}
    return services.get("qqq_0dte_live_runtime") if isinstance(services.get("qqq_0dte_live_runtime"), dict) else {}


def _auto_trading_module_running(module_id: str, services: dict[str, Any]) -> bool:
    if module_id == "stocks":
        return bool(services.get("auto_trader_scheduler_running") or services.get("auto_trader_supervisor_running"))
    if module_id == "options-swing":
        return bool(services.get("stock_options_swing_running"))
    if module_id == "options-1dte":
        return bool(services.get("qqq_1dte_live_running"))
    return bool(services.get("qqq_0dte_live_running"))


def _auto_trading_module_pid(module_id: str, services: dict[str, Any]) -> int | None:
    key = {
        "stocks": "auto_trader_worker_pid",
        "options-0dte": "qqq_0dte_live_pid",
        "options-1dte": "qqq_1dte_live_pid",
        "options-swing": "stock_options_swing_pid",
    }.get(module_id)
    raw = services.get(key or "")
    try:
        return int(raw) if raw is not None and str(raw).strip().isdigit() else None
    except Exception:
        return None


def _auto_trading_module_status_from_services(
    module_id: str,
    services: dict[str, Any] | None = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    meta = _auto_trading_module(module_id)
    services = services if isinstance(services, dict) else setup_services_status(owner_id=owner_id)
    running = _auto_trading_module_running(module_id, services)
    runtime = services.get("auto_trader_runtime") if module_id == "stocks" else _auto_trading_option_runtime(module_id, services)
    runtime = runtime if isinstance(runtime, dict) else {}
    cfg: dict[str, Any] = {}
    status_extra: dict[str, Any] = {}
    if module_id == "stocks":
        try:
            st = auto_trader_status(owner_id=owner_id)
            cfg = st.get("config") if isinstance(st.get("config"), dict) else {}
            status_extra = {
                "daily_trade_count": st.get("daily_trade_count"),
                "pending_signals": st.get("pending_signals"),
                "executed_signals": st.get("executed_signals"),
                "last_scan_at": st.get("last_scan_at"),
                "last_scan_summary": st.get("last_scan_summary"),
            }
        except Exception:
            cfg = {}
    else:
            cfg = _auto_trading_option_config(module_id, owner_id=owner_id)
    inner = _runtime_inner(runtime)
    return {
        "id": meta["id"],
        "kind": meta["kind"],
        "label": meta["label"],
        "running": running,
        "pid": _auto_trading_module_pid(module_id, services),
        "runtime": runtime,
        "last_error": runtime.get("last_error") or inner.get("last_error"),
        "updated_at": runtime.get("updated_at") or inner.get("updated_at") or inner.get("loop_started"),
        "l3": _auto_trading_l3_status(cfg),
        **status_extra,
    }


def auto_trading_modules() -> dict[str, Any]:
    return {"ok": True, "items": [dict(v) for v in AUTO_TRADING_MODULES.values()]}


def auto_trading_status(owner_id: str | None = None) -> dict[str, Any]:
    services = setup_services_status(owner_id=owner_id)
    modules = [_auto_trading_module_status_from_services(mid, services, owner_id=owner_id) for mid in AUTO_TRADING_MODULES]
    return {
        "ok": True,
        "modules": modules,
        "running_count": sum(1 for m in modules if m.get("running")),
        "any_running": any(bool(m.get("running")) for m in modules),
        "source": "auto_trading_unified",
    }


def auto_trading_module_status(module_id: str, owner_id: str | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "module": _auto_trading_module_status_from_services(_auto_trading_module(module_id)["id"], owner_id=owner_id),
    }


def _auto_trading_setup_start_body(module_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = _auto_trading_module(module_id)
    out = {
        "start_feishu_bot": False,
        "enable_auto_trader": False,
        "enable_qqq_0dte_live": False,
        "enable_qqq_1dte_live": False,
        "enable_stock_options_swing": False,
    }
    out[str(meta["legacy_start_flag"])] = True
    if isinstance(body, dict) and "start_feishu_bot" in body:
        out["start_feishu_bot"] = bool(body.get("start_feishu_bot"))
    return out


def _auto_trading_setup_stop_body(module_id: str) -> dict[str, Any]:
    meta = _auto_trading_module(module_id)
    out = {
        "stop_feishu_bot": False,
        "stop_auto_trader": False,
        "stop_qqq_0dte_live": False,
        "stop_qqq_1dte_live": False,
        "stop_stock_options_swing": False,
    }
    out[str(meta["legacy_stop_flag"])] = True
    return out


def auto_trading_module_start(module_id: str, body: dict[str, Any] | None = None, owner_id: str | None = None) -> dict[str, Any]:
    mid = _auto_trading_module(module_id)["id"]
    result = setup_start_services(_auto_trading_setup_start_body(mid, body), owner_id=owner_id)
    return {
        "ok": True,
        "module_id": mid,
        "action": "start",
        "result": result,
        "module": _auto_trading_module_status_from_services(mid, owner_id=owner_id),
    }


def auto_trading_module_stop(module_id: str, body: dict[str, Any] | None = None, owner_id: str | None = None) -> dict[str, Any]:
    mid = _auto_trading_module(module_id)["id"]
    result = setup_stop_services(_auto_trading_setup_stop_body(mid), owner_id=owner_id)
    return {
        "ok": True,
        "module_id": mid,
        "action": "stop",
        "result": result,
        "module": _auto_trading_module_status_from_services(mid, owner_id=owner_id),
    }


def auto_trading_module_restart(module_id: str, body: dict[str, Any] | None = None, owner_id: str | None = None) -> dict[str, Any]:
    mid = _auto_trading_module(module_id)["id"]
    stopped = setup_stop_services(_auto_trading_setup_stop_body(mid), owner_id=owner_id)
    started = setup_start_services(_auto_trading_setup_start_body(mid, body), owner_id=owner_id)
    return {
        "ok": True,
        "module_id": mid,
        "action": "restart",
        "stopped": stopped,
        "started": started,
        "module": _auto_trading_module_status_from_services(mid, owner_id=owner_id),
    }


def auto_trading_module_risk_summary(module_id: str, owner_id: str | None = None) -> dict[str, Any]:
    mid = _auto_trading_module(module_id)["id"]
    if mid == "stocks":
        st = auto_trader_status(owner_id=owner_id)
        cfg = st.get("config") if isinstance(st.get("config"), dict) else {}
        summary = {
            "auto_execute": bool(cfg.get("auto_execute")),
            "dry_run_mode": bool(cfg.get("dry_run_mode")),
            "max_daily_trades": cfg.get("max_daily_trades"),
            "daily_trade_count": st.get("daily_trade_count"),
            "max_position_value": cfg.get("max_position_value"),
            "max_total_exposure": cfg.get("max_total_exposure"),
            "daily_loss_limit_pct": st.get("daily_loss_limit_pct") or cfg.get("daily_loss_limit_pct"),
            "daily_loss_pct": st.get("daily_loss_pct"),
            "daily_loss_circuit_triggered": bool(st.get("daily_loss_circuit_triggered")),
            "consecutive_loss_stop_triggered": bool(st.get("consecutive_loss_stop_triggered")),
        }
    else:
        cfg = _auto_trading_option_config(mid, owner_id=owner_id)
        runtime = _runtime_inner(_auto_trading_option_runtime(mid, setup_services_status(owner_id=owner_id)))
        if mid == "options-swing":
            strat = cfg.get("strategy") if isinstance(cfg.get("strategy"), dict) else {}
            summary = {
                "dry_run": bool(cfg.get("dry_run")),
                "auto_submit_orders": bool(cfg.get("auto_submit_orders")),
                "managed_positions_only": bool(cfg.get("managed_positions_only")),
                "skip_existing_broker_positions": bool(cfg.get("skip_existing_broker_positions")),
                "stock_pool": cfg.get("stock_pool") if isinstance(cfg.get("stock_pool"), list) else [],
                "account_id": cfg.get("account_id"),
                "poll_seconds": cfg.get("poll_seconds"),
                "history_days": cfg.get("history_days"),
                "kline": cfg.get("kline"),
                "strategy": strat,
                "candidate_count": runtime.get("candidate_count"),
                "managed_positions": runtime.get("managed_positions"),
                "unmanaged_positions": runtime.get("unmanaged_positions"),
                "warnings": runtime.get("warnings"),
                "scheduler": runtime.get("scheduler"),
            }
        else:
            strat = cfg.get("strategy_config") if isinstance(cfg.get("strategy_config"), dict) else {}
            summary = {
                "dry_run": bool(cfg.get("dry_run")),
                "symbol": cfg.get("symbol"),
                "stock_options_mode": bool(cfg.get("stock_options_mode")),
                "stock_pool": cfg.get("stock_pool") if isinstance(cfg.get("stock_pool"), list) else [],
                "account_id": cfg.get("account_id"),
                "poll_seconds": cfg.get("poll_seconds"),
                "trade_bar_freshness_seconds": cfg.get("trade_bar_freshness_seconds"),
                "expiry_offset_days": cfg.get("expiry_offset_days"),
                "strategy_config": strat,
                "last_position": runtime.get("position") or runtime.get("open_position"),
                "last_decision": runtime.get("decision") or runtime.get("last_decision"),
            }
    return {"ok": True, "module_id": mid, "risk": summary, "l3": _auto_trading_l3_status(cfg if isinstance(cfg, dict) else {})}


def auto_trading_module_events(module_id: str, limit: int = 50, owner_id: str | None = None) -> dict[str, Any]:
    mid = _auto_trading_module(module_id)["id"]
    lim = max(1, min(200, int(limit)))
    if mid == "stocks":
        metrics = auto_trader_metrics_recent(limit=lim, event=None)
        items = metrics.get("items") if isinstance(metrics.get("items"), list) else metrics.get("metrics")
        if not isinstance(items, list):
            items = []
        return {"ok": True, "module_id": mid, "items": items[:lim], "source": "/auto-trader/metrics/recent"}
    if mid == "options-swing":
        tail = stock_options_swing_decision_tail_get(limit=lim, owner_id=owner_id)
        source = "/strategy/stock-options-swing/live-worker-decision-tail"
    elif mid == "options-1dte":
        tail = qqq_1dte_live_worker_decision_tail_get(limit=lim, owner_id=owner_id)
        source = "/strategy/qqq-1dte/live-worker-decision-tail"
    else:
        tail = qqq_0dte_live_worker_decision_tail_get(limit=lim, owner_id=owner_id)
        source = "/strategy/qqq-0dte/live-worker-decision-tail"
    items = tail.get("items") if isinstance(tail.get("items"), list) else []
    return {"ok": bool(tail.get("ok", True)), "module_id": mid, "items": items, "source": source, "path": tail.get("path")}


def auto_trading_module_confirm(module_id: str, body: dict[str, Any]) -> dict[str, Any]:
    mid = _auto_trading_module(module_id)["id"]
    payload = body if isinstance(body, dict) else {}
    token = str(payload.get("confirmation_token") or "").strip()
    if mid == "stocks":
        signal_id = str(payload.get("signal_id") or "").strip()
        if not signal_id:
            raise _m().HTTPException(status_code=400, detail={"error": "signal_id_required"})
        return {
            "ok": True,
            "module_id": mid,
            "result": auto_trader_confirm(signal_id, {"confirmation_token": token}, owner_id=payload.get("owner_id")),
        }
    _m()._ensure_l3_confirmation(token)
    return {
        "ok": True,
        "module_id": mid,
        "confirmed": True,
        "message": "L3 confirmation accepted for this module. Option live orders continue to use the module worker config token.",
        "l3": _auto_trading_l3_status(_auto_trading_option_config(mid, owner_id=payload.get("owner_id"))),
    }


def fees_schedule(broker_id: str | None = None) -> dict[str, Any]:
    from fastapi import HTTPException

    from api.services import fee_broker_profiles as fbp

    listed = fbp.list_broker_profiles()
    bid = (broker_id or listed["active_broker_id"]).strip()
    try:
        sched = fbp.get_schedule_for_broker(bid)
    except HTTPException:
        raise
    out = build_fee_schedule_response(sched)
    out["broker_id"] = bid
    out["active_broker_id"] = listed["active_broker_id"]
    out["effective_broker_id"] = listed.get("effective_broker_id", listed["active_broker_id"])
    out["fee_source"] = listed.get("fee_source")
    out["manual_fee_broker_id"] = listed.get("manual_fee_broker_id")
    return out


def fees_schedule_default() -> dict[str, Any]:
    m = _m()
    return build_fee_schedule_response(m.get_default_fee_schedule())


def fees_schedule_save(body: dict[str, Any]) -> dict[str, Any]:
    from fastapi import HTTPException

    from api.services import fee_broker_profiles as fbp

    parsed = FeeScheduleBody.model_validate(body if isinstance(body, dict) else {})
    try:
        updated = fbp.save_schedule_for_broker(parsed.broker_id, parsed.schedule)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"费用配置格式错误: {e}") from e
    listed = fbp.list_broker_profiles()
    res_bid = (parsed.broker_id or listed.get("effective_broker_id") or listed["active_broker_id"]).strip()
    return {
        "ok": True,
        "version": str(updated.get("version", "1.0")),
        "schedule": updated,
        "broker_id": res_bid,
        "active_broker_id": listed["active_broker_id"],
        "effective_broker_id": listed.get("effective_broker_id", listed["active_broker_id"]),
        "manual_fee_broker_id": listed.get("manual_fee_broker_id"),
        "fee_source": listed.get("fee_source"),
    }


def fees_brokers_list() -> dict[str, Any]:
    from api.services import fee_broker_profiles as fbp

    return fbp.list_broker_profiles()


def fees_brokers_create(body: dict[str, Any]) -> dict[str, Any]:
    from fastapi import HTTPException

    from api.services import fee_broker_profiles as fbp

    parsed = FeeBrokerCreateBody.model_validate(body if isinstance(body, dict) else {})
    try:
        return fbp.add_broker_profile(parsed.broker_id, parsed.display_name, parsed.copy_from)
    except HTTPException:
        raise


def fees_brokers_set_active(body: dict[str, Any]) -> dict[str, Any]:
    from fastapi import HTTPException

    from api.services import fee_broker_profiles as fbp

    parsed = FeeBrokerActiveBody.model_validate(body if isinstance(body, dict) else {})
    try:
        return fbp.set_active_broker(parsed.broker_id)
    except HTTPException:
        raise


def fees_brokers_patch_display_name(broker_id: str, body: dict[str, Any]) -> dict[str, Any]:
    from fastapi import HTTPException

    from api.services import fee_broker_profiles as fbp

    parsed = FeeBrokerDisplayNameBody.model_validate(body if isinstance(body, dict) else {})
    try:
        return fbp.update_broker_display_name(broker_id.strip(), parsed.display_name)
    except HTTPException:
        raise


def fees_brokers_delete(broker_id: str) -> dict[str, Any]:
    from fastapi import HTTPException

    from api.services import fee_broker_profiles as fbp

    try:
        return fbp.delete_broker_profile(broker_id.strip())
    except HTTPException:
        raise


def fees_estimate(
    *,
    asset_class: Literal["stock", "us_option"] = "stock",
    market: Literal["HK", "US", "CN", "OTHER"] = "US",
    side: Literal["buy", "sell"] = "buy",
    quantity: int = 100,
    price: float = 1.0,
) -> dict[str, Any]:
    m = _m()
    return estimate_fees(
        asset_class=asset_class,
        market=market,
        side=side,
        quantity=quantity,
        price=price,
        estimate_stock_order_fee=m.estimate_stock_order_fee,
        estimate_us_option_order_fee=m.estimate_us_option_order_fee,
    )


def risk_config() -> dict[str, Any]:
    return build_risk_config_response(load_config=load_config)


def dashboard_summary(owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    qctx = _owner_quote_context(m, owner_id)
    cn_hk_symbols = [
        ("000001.SH", "上证综指"),
        ("399001.SZ", "深证成指"),
        ("HSI.HK", "恒生指数"),
        ("HSTECH.HK", "恒生科技"),
    ]
    us_symbols = [
        ("SPY.US", "标普500"),
        ("QQQ.US", "纳指100"),
        ("DIA.US", "道指"),
    ]
    pool = m.ThreadPoolExecutor(max_workers=4)
    try:
        fut_analysis = pool.submit(m.get_comprehensive_analysis, qctx)
        fut_sectors = pool.submit(m.get_sector_rotation, 5, qctx)
        fut_cn_hk = pool.submit(
            _market_snap_with_public_fallback,
            m,
            cn_hk_symbols,
            owner_id,
        )
        fut_us = pool.submit(
            _market_snap_with_public_fallback,
            m,
            us_symbols,
            owner_id,
        )
        try:
            # get_comprehensive_analysis 内并发拉 CBOE/LongPort 等，冷启动常 >3s；过短会整段退化为空 indicators
            analysis = fut_analysis.result(timeout=15.0)
        except Exception:
            analysis = {
                "market_environment": "数据刷新中",
                "strategy_recommendation": "建议稍后重试",
                "score": 0,
                "indicators": {},
                "analysis_time": m.datetime.now().isoformat(),
                "data_source": "fallback",
            }
        try:
            sectors = fut_sectors.result(timeout=10.0)
        except Exception:
            sectors = {
                "data_source": "fallback",
                "data_source_label": "兜底",
                "top_performers": [],
                "bottom_performers": [],
            }
        try:
            cn_hk = fut_cn_hk.result(timeout=6.0)
        except Exception:
            cn_hk = []
        cn_hk = _dashboard_market_cache_update("cn_hk", cn_hk, cn_hk_symbols)
        try:
            us = fut_us.result(timeout=6.0)
        except Exception:
            us = []
        us = _dashboard_market_cache_update("us", us, us_symbols)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return {
        "markets": {"cn_hk": cn_hk, "us": us},
        "market_data_status": {
            "cn_hk": _market_snap_group_status(cn_hk, cn_hk_symbols),
            "us": _market_snap_group_status(us, us_symbols),
        },
        "analysis": analysis,
        "sector_data_source": sectors.get("data_source", "unknown"),
        "sector_data_source_label": sectors.get("data_source_label", "未知"),
        "sector_age_seconds": sectors.get("age_seconds"),
        "sector_last_refresh_ts": sectors.get("last_refresh_ts"),
        "sector_top3": sectors.get("top_performers", [])[:3],
        "sector_bottom3": sectors.get("bottom_performers", [])[:3],
    }


def market_analysis(owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    return m.get_comprehensive_analysis(_owner_quote_context(m, owner_id))


def market_sectors(days: int = 5, owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    return m.get_sector_rotation(days, _owner_quote_context(m, owner_id))


def signals(symbol: str = "RXRX.US") -> dict[str, Any]:
    m = _m()
    from api.signal_center_signals import analyze_signal_center_from_closes

    bars = m._fetch_bars_calendar_days(symbol, 90)
    data_status = "fresh"
    if len(bars) < 25:
        fallback = m._fetch_public_market_bars(symbol, 240, "1d", limit=180, source="auto")
        if len(fallback) > len(bars):
            bars = fallback
            data_status = "public_fallback_or_stale"
    if len(bars) < 25:
        raise m.HTTPException(status_code=400, detail="历史数据不足")
    closes = [float(b.close) for b in bars]
    snap = analyze_signal_center_from_closes(closes)
    if not snap:
        raise m.HTTPException(status_code=400, detail="历史数据不足")
    signal_flags = snap["signals"]
    rt = m._quote_last(symbol, allow_public=True)
    latest_price = closes[-1]
    latest_price_type = "K线收盘"
    latest_price_source = "kline_close"
    if rt and float(rt.get("last", 0) or 0) > 0:
        latest_price = float(rt["last"])
        latest_price_type = str(rt.get("price_type", "盘中"))
        latest_price_source = "realtime_quote"
    return {
        "symbol": symbol,
        "latest_close": closes[-1],
        "latest_price": round(float(latest_price), 4),
        "latest_price_type": latest_price_type,
        "latest_price_source": latest_price_source,
        "bar_count": len(bars),
        "bar_as_of": bars[-1].date.isoformat() if hasattr(bars[-1].date, "isoformat") else str(bars[-1].date),
        "data_status": data_status,
        "rsi14": snap["rsi14"],
        "ma5": snap["ma5"],
        "ma20": snap["ma20"],
        "signals": signal_flags,
    }


def backtest_strategies_catalog() -> dict[str, Any]:
    m = _m()
    return {"items": m.list_strategy_metadata()}


def backtest_compare(
    *,
    symbol: str = "RXRX.US",
    days: int = 180,
    periods: int = 0,
    kline: BacktestKline = "1d",
    initial_capital: float = 100000.0,
    execution_mode: Literal["next_open", "bar_close"] = "next_open",
    slippage_bps: float = 3.0,
    commission_bps: float | None = None,
    stamp_duty_bps: float | None = None,
    walk_forward_windows: int = 1,
    ml_filter_enabled: bool = False,
    ml_model_type: Literal["logreg", "random_forest", "gbdt"] = "logreg",
    ml_threshold: float = 0.55,
    ml_horizon_days: int = 5,
    ml_train_ratio: float = 0.7,
    include_trades: bool = False,
    trade_limit: int = 50,
    trade_offset: int = 0,
    strategy_key: str | None = None,
    include_best_kline: bool = False,
    use_server_kline_cache: bool = False,
    market_data_source: str = "auto",
) -> dict[str, Any]:
    m = _m()
    sym = str(symbol or "").strip().upper()
    bars = m._resolve_bars_for_backtest_compare(
        sym,
        periods,
        days,
        kline,
        None,
        use_server_kline_cache=bool(use_server_kline_cache),
        market_data_source=market_data_source,
    )
    return m._backtest_compare_core(
        sym,
        bars,
        periods=periods,
        days=days,
        kline=kline,
        initial_capital=initial_capital,
        execution_mode=execution_mode,
        slippage_bps=slippage_bps,
        commission_bps=commission_bps,
        stamp_duty_bps=stamp_duty_bps,
        walk_forward_windows=walk_forward_windows,
        ml_filter_enabled=ml_filter_enabled,
        ml_model_type=ml_model_type,
        ml_threshold=ml_threshold,
        ml_horizon_days=ml_horizon_days,
        ml_train_ratio=ml_train_ratio,
        include_trades=include_trades,
        trade_limit=trade_limit,
        trade_offset=trade_offset,
        strategy_key=strategy_key,
        include_best_kline=include_best_kline,
        strategy_params_map=None,
        include_bars_in_response=False,
    )


def backtest_compare_post(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = BacktestCompareBody.model_validate(body if isinstance(body, dict) else {})
    sym = str(parsed.symbol or "").strip().upper()
    client_list = m._parse_client_bars_for_backtest(parsed.bars) if parsed.bars else []
    client_bars = client_list if client_list else None
    bars = m._resolve_bars_for_backtest_compare(
        sym,
        parsed.periods,
        parsed.days,
        parsed.kline,
        client_bars,
        use_server_kline_cache=bool(parsed.use_server_kline_cache),
        market_data_source=parsed.market_data_source,
    )
    sp_map = parsed.strategy_params if isinstance(parsed.strategy_params, dict) else None
    return m._backtest_compare_core(
        sym,
        bars,
        periods=parsed.periods,
        days=parsed.days,
        kline=parsed.kline,
        initial_capital=parsed.initial_capital,
        execution_mode=parsed.execution_mode,
        slippage_bps=parsed.slippage_bps,
        commission_bps=parsed.commission_bps,
        stamp_duty_bps=parsed.stamp_duty_bps,
        walk_forward_windows=parsed.walk_forward_windows,
        ml_filter_enabled=parsed.ml_filter_enabled,
        ml_model_type=parsed.ml_model_type,
        ml_threshold=parsed.ml_threshold,
        ml_horizon_days=parsed.ml_horizon_days,
        ml_train_ratio=parsed.ml_train_ratio,
        include_trades=parsed.include_trades,
        trade_limit=parsed.trade_limit,
        trade_offset=parsed.trade_offset,
        strategy_key=parsed.strategy_key,
        include_best_kline=parsed.include_best_kline,
        strategy_params_map=sp_map,
        include_bars_in_response=bool(parsed.include_bars_in_response),
    )


def backtest_kline_cache_fetch(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = BacktestKlineCacheFetchBody.model_validate(body if isinstance(body, dict) else {})
    sym = str(parsed.symbol or "").strip().upper()
    if not sym:
        raise m.HTTPException(status_code=400, detail="symbol_required")
    periods = max(0, int(parsed.periods or 0))
    days = max(1, min(3650, int(parsed.days or 180)))
    kline = parsed.kline
    source = str(parsed.source or "auto").strip().lower() or "auto"
    path = m._kline_server_cache_path(sym, kline, periods, days)
    if not parsed.force_refresh and m.os.path.isfile(path):
        bars0, meta0 = m._read_server_kline_cache_file(path)
        if bars0 and (periods <= 0 or len(bars0) >= periods):
            use = bars0[-periods:] if periods > 0 and len(bars0) > periods else bars0
            return {
                "ok": True,
                "cached": True,
                "symbol": sym,
                "kline": str(kline),
                "periods": periods,
                "days": days,
                "bar_count": len(use),
                "cache_path": path,
                "source": source,
                "meta": meta0,
            }
    if source not in {"auto", "longbridge", "longport"}:
        fetch_days = days if periods <= 0 else max(days, periods * 2)
        bars = m._fetch_public_market_bars(sym, fetch_days, kline, limit=periods, source=source)
        bars = bars[-periods:] if periods > 0 and len(bars) > periods else bars
    elif periods > 0:
        bars = m._fetch_bars_by_periods(sym, periods, kline)
        bars = bars[-periods:] if len(bars) > periods else bars
    else:
        bars = m._fetch_bars_calendar_days(sym, days, kline)
    if not bars:
        raise m.HTTPException(status_code=400, detail="无法获取历史数据")
    m._write_server_kline_cache_file(path, symbol=sym, kline=str(kline), periods=periods, days=days, bars=bars)
    return {
        "ok": True,
        "cached": False,
        "symbol": sym,
        "kline": str(kline),
        "periods": periods,
        "days": days,
        "bar_count": len(bars),
        "cache_path": path,
        "source": source,
        "meta": {"saved_at": m.datetime.now(m.timezone.utc).isoformat(), "bar_count": len(bars), "source": source},
    }


def backtest_kline_cache_status(
    *,
    symbol: str,
    kline: BacktestKline = "1d",
    periods: int = 0,
    days: int = 180,
) -> dict[str, Any]:
    m = _m()
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise m.HTTPException(status_code=400, detail="symbol_required")
    periods = max(0, int(periods))
    days = max(1, min(3650, int(days)))
    path = m._kline_server_cache_path(sym, kline, periods, days)
    bars, meta = m._read_server_kline_cache_file(path)
    ok = bool(bars) and (periods <= 0 or len(bars) >= periods)
    return {
        "exists": bool(bars),
        "ready": ok,
        "symbol": sym,
        "kline": str(kline),
        "periods": periods,
        "days": days,
        "bar_count": len(bars) if bars else 0,
        "cache_path": path,
        "meta": meta,
    }


def backtest_kline_cache_delete(
    *,
    symbol: str,
    kline: BacktestKline = "1d",
    periods: int = 0,
    days: int = 180,
) -> dict[str, Any]:
    m = _m()
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise m.HTTPException(status_code=400, detail="symbol_required")
    periods = max(0, int(periods))
    days = max(1, min(3650, int(days)))
    path = m._kline_server_cache_path(sym, kline, periods, days)
    base = m.os.path.abspath(m.KLINE_SERVER_CACHE_DIR)
    abspath = m.os.path.abspath(path)
    if not abspath.startswith(base + m.os.sep) and abspath != base:
        raise m.HTTPException(status_code=400, detail="invalid_cache_path")
    removed = False
    try:
        if m.os.path.isfile(path):
            m.os.remove(path)
            removed = True
    except OSError:
        pass
    return {"ok": True, "removed": removed, "cache_path": path}


def _is_trade_gateway_success(payload: Any, *, required_keys: tuple[str, ...] = ()) -> bool:
    if not isinstance(payload, dict):
        return False
    if required_keys and not all(k in payload for k in required_keys):
        return False
    if payload.get("ok") is False:
        return False
    return True


def _raise_broker_connect_http_error(m: Any, err: Exception | str) -> None:
    raise m.HTTPException(
        status_code=503,
        detail={
            "error": "broker_connect_error",
            "message": "券商连接失败，系统已自动重置连接并将继续重试。请检查网络、Longbridge 凭证和账户连接状态。",
            "detail": str(err),
        },
    )


def _with_trade_context_retry(
    operation_name: str,
    *,
    account_id: str | None,
    owner_id: str | None,
    fn: Any,
) -> Any:
    m = _m()
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if not m._is_longport_connect_error(e):
                raise
            try:
                m.ACCOUNT_REGISTRY.mark_broker_connect_error(e, account_id=account_id, owner_id=owner_id)
            except Exception:
                pass
            if attempt >= 1:
                break
            try:
                m.throttled_reset_contexts(lambda: m.reset_contexts(account_id=account_id, owner_id=owner_id), m._RUNTIME_STATE)
            except Exception:
                pass
    _raise_broker_connect_http_error(m, last_err or RuntimeError(f"{operation_name}_failed"))


def trade_account(account_id: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    gw = m._gateway_get_json("/trade/account", {"account_id": account_id} if account_id else None)
    if _is_trade_gateway_success(gw, required_keys=("net_assets", "buy_power", "currency")):
        return gw
    def _load() -> dict[str, Any]:
        _, tctx = m.ensure_contexts(account_id, owner_id=owner_id)
        bl = m.broker_get_account_balance(tctx)
        if not bl:
            raise m.HTTPException(status_code=400, detail="账户信息为空")
        b = bl[0]
        return {"net_assets": float(b.net_assets), "buy_power": float(b.buy_power), "currency": str(b.currency)}

    return _with_trade_context_retry("trade_account", account_id=account_id, owner_id=owner_id, fn=_load)
    _, tctx = m.ensure_contexts(account_id, owner_id=owner_id)
    bl = m.broker_get_account_balance(tctx)
    if not bl:
        raise m.HTTPException(status_code=400, detail="账户信息为空")
    b = bl[0]
    return {"net_assets": float(b.net_assets), "buy_power": float(b.buy_power), "currency": str(b.currency)}


def options_expiries(symbol: str, account_id: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    qctx, _ = m.ensure_contexts(account_id, owner_id=owner_id)
    return m.fetch_option_expiries(qctx, symbol)


def options_chain(
    *,
    symbol: str,
    account_id: str | None = None,
    owner_id: str | None = None,
    expiry_date: str | None = None,
    min_strike: float | None = None,
    max_strike: float | None = None,
    standard_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    m = _m()
    qctx, _ = m.ensure_contexts(account_id, owner_id=owner_id)
    return m.fetch_option_chain(
        quote_ctx=qctx,
        symbol=symbol,
        expiry_date=expiry_date,
        min_strike=min_strike,
        max_strike=max_strike,
        standard_only=standard_only,
        limit=limit,
        offset=offset,
    )


def options_fee_estimate(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = OptionOrderBody.model_validate(body if isinstance(body, dict) else {})
    legs = build_option_legs_or_400(body=parsed, build_order_legs=m.build_order_legs)
    estimate = m.estimate_option_fee_for_legs(legs)
    return {"estimate": estimate}


def options_order(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    parsed = OptionOrderBody.model_validate(body if isinstance(body, dict) else {})
    m._ensure_l3_confirmation(parsed.confirmation_token)
    qctx, tctx = m.ensure_contexts(parsed.account_id, owner_id=owner_id)
    bl = m.broker_get_account_balance(tctx)
    b = bl[0] if bl else None
    available_cash = float(b.buy_power) if b else 0.0
    legs = build_option_legs_or_400(body=parsed, build_order_legs=m.build_order_legs)
    positions_result = m.svc_get_option_positions(tctx, qctx)
    positions = positions_result.get("positions") if isinstance(positions_result, dict) else []
    sell_guard = validate_option_sell_covered(
        legs=legs,
        positions=[x for x in (positions or []) if isinstance(x, dict)] if isinstance(positions, list) else [],
        allow_opening_short_options=is_opening_short_options_allowed(body if isinstance(body, dict) else {}),
    )
    if sell_guard.get("blocked"):
        raise m.HTTPException(
            status_code=400,
            detail={
                "error": "option_sell_uncovered",
                "message": "Option sell order blocked because broker position is insufficient; refusing to open short options.",
                "guard": sell_guard,
            },
        )
    submit_result = m.submit_option_order_with_risk(
        trade_ctx=tctx,
        legs=legs,
        available_cash=available_cash,
        max_loss_threshold=parsed.max_loss_threshold,
        max_capital_usage=parsed.max_capital_usage,
    )
    return build_option_submit_response(submit_result)


def options_orders(status: str = "all", account_id: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    _, tctx = m.ensure_contexts(account_id, owner_id=owner_id)
    return m.svc_get_option_orders(tctx, status=status)


def options_positions(account_id: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    qctx, tctx = m.ensure_contexts(account_id, owner_id=owner_id)
    return m.svc_get_option_positions(tctx, qctx)


def options_pnl_calendar(
    *,
    from_date: str,
    to_date: str,
    tz: str = "America/New_York",
    symbol: str | None = None,
    account_id: str | None = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    m = _m()
    _, tctx = m.ensure_contexts(account_id, owner_id=owner_id)
    return m.svc_get_option_pnl_calendar(
        tctx,
        from_date=str(from_date).strip(),
        to_date=str(to_date).strip(),
        tz_name=str(tz or "America/New_York").strip() or "America/New_York",
        symbol_query=str(symbol or "").strip() or None,
    )


def _parse_option_expiry_iso(expiry_raw: str) -> datetime:
    s = str(expiry_raw or "").strip()
    if not s:
        raise ValueError("expiry 不能为空")
    if s.endswith("Z"):
        s = s[:-1]
    if "T" in s or (len(s) > 10 and s[10] in " T"):
        dt = datetime.fromisoformat(s.replace(" ", "T"))
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    return datetime.combine(date.fromisoformat(s[:10]), datetime.min.time())


def options_synthetic_path(body: dict[str, Any]) -> dict[str, Any]:
    from mcp_server.synthetic_option_pricing import (
        build_synthetic_option_path,
        periods_per_year_for_kline,
        synthetic_path_to_dict_rows,
        synthetic_vertical_spread_path,
    )

    m = _m()
    parsed = SyntheticOptionPathBody.model_validate(body if isinstance(body, dict) else {})
    sym = str(parsed.symbol or "").strip().upper()
    bars = m._resolve_bars_for_backtest_compare(
        sym,
        parsed.periods,
        parsed.days,
        parsed.kline,
        None,
        use_server_kline_cache=bool(parsed.use_server_kline_cache),
    )
    exp = _parse_option_expiry_iso(parsed.expiry)
    ppy = periods_per_year_for_kline(parsed.kline)
    common: dict[str, Any] = {
        "expiry": exp,
        "right": parsed.right,
        "rate": parsed.rate,
        "div_yield": parsed.div_yield,
        "vol_window": parsed.vol_window,
        "periods_per_year": ppy,
        "kline": parsed.kline,
        "min_sigma": parsed.min_sigma,
        "spot_source": parsed.spot_source,
    }
    if parsed.structure == "single":
        path = build_synthetic_option_path(bars, strike=float(parsed.strike or 0), **common)
        rows: list[dict[str, Any]] = synthetic_path_to_dict_rows(path)
    else:
        rows = synthetic_vertical_spread_path(
            bars,
            long_strike=float(parsed.long_strike or 0),
            short_strike=float(parsed.short_strike or 0),
            **common,
        )
    total = len(rows)
    cap = int(parsed.max_rows)
    truncated = total > cap
    if truncated:
        rows = rows[-cap:]
    return {
        "symbol": sym,
        "kline": parsed.kline,
        "periods": parsed.periods,
        "days": parsed.days,
        "structure": parsed.structure,
        "bar_count_total": total,
        "rows_returned": len(rows),
        "truncated": truncated,
        "model": "black_scholes_european_rolling_hv",
        "disclaimer": "理论价路径，非期权历史成交；未建模 IV 曲面、价差与滑点。",
        "rows": rows,
    }


def qqq_0dte_backtest(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    from mcp_server.strategy_qqq_0dte.backtest import run_qqq_0dte_backtest
    from mcp_server.strategy_qqq_0dte.config import Qqq0dteConfig

    parsed = Qqq0dteBacktestBody.model_validate(body if isinstance(body, dict) else {})
    sym = str(parsed.symbol or "").strip().upper()
    bars_raw = m._resolve_bars_for_backtest_compare(
        sym,
        parsed.periods,
        parsed.days,
        parsed.kline,
        None,
        use_server_kline_cache=bool(parsed.use_server_kline_cache),
    )
    cfg = Qqq0dteConfig.from_dict(parsed.strategy_config if isinstance(parsed.strategy_config, dict) else {})
    cfg.symbol = sym
    bar_count_total = len(bars_raw)
    if bool(parsed.rth_only):
        bars, bar_count_rth, bar_count_non_rth = _filter_rth_bars_for_qqq_0dte(list(bars_raw), cfg)
    else:
        bars = list(bars_raw)
        bar_count_rth = sum(
            1
            for b in bars
            if isinstance(getattr(b, "date", None), datetime)
            and _is_rth_bar(
                b.date,
                tz_name=cfg.assume_bars_timezone,
                open_hour=cfg.rth_open_hour,
                open_minute=cfg.rth_open_minute,
                close_hour=cfg.rth_close_hour,
                close_minute=cfg.rth_close_minute,
            )
        )
        bar_count_non_rth = len(bars) - bar_count_rth
    result = run_qqq_0dte_backtest(bars, cfg)
    result["rth_only"] = bool(parsed.rth_only)
    result["bar_count_total"] = int(bar_count_total)
    result["bar_count_rth"] = int(bar_count_rth)
    result["bar_count_non_rth"] = int(bar_count_non_rth)

    result["snapshot"] = {"saved": False}
    if bool(parsed.save_snapshot):
        from mcp_server.strategy_qqq_0dte.snapshot_store import append_backtest_snapshot

        st = result.get("stats") if isinstance(result.get("stats"), dict) else {}
        metrics = {
            "realized_pnl": result.get("realized_pnl"),
            "total_fee": result.get("total_fee"),
            "bar_count": result.get("bar_count"),
            "open_events": result.get("open_events"),
            "close_events": result.get("close_events"),
            "closed_trades": st.get("closed_trades"),
            "wins": st.get("wins"),
            "losses": st.get("losses"),
            "win_rate_pct": st.get("win_rate_pct"),
            "return_pct": result.get("return_pct"),
            "open_premium_debit_usd": result.get("open_premium_debit_usd"),
        }
        snap = append_backtest_snapshot(
            request_meta={
                "symbol": sym,
                "days": int(parsed.days),
                "periods": int(parsed.periods),
                "kline": str(parsed.kline),
                "use_server_kline_cache": bool(parsed.use_server_kline_cache),
                "rth_only": bool(parsed.rth_only),
            },
            strategy_config=result.get("config") if isinstance(result.get("config"), dict) else {},
            metrics=metrics,
        )
        result["snapshot"] = {
            "saved": True,
            "id": snap.get("id"),
            "created_at": snap.get("created_at"),
        }

    return result


def qqq_0dte_matrix(body: dict[str, Any]) -> dict[str, Any]:
    from fastapi import HTTPException

    m = _m()
    from mcp_server.strategy_qqq_0dte.matrix_runner import grid_combination_count, run_parameter_matrix

    parsed = Qqq0dteMatrixBody.model_validate(body if isinstance(body, dict) else {})
    grid = parsed.grid
    for k, vals in grid.items():
        if not isinstance(vals, list) or len(vals) == 0:
            raise HTTPException(status_code=400, detail=f"grid[{k!r}] 必须为非空列表")

    ncomb = grid_combination_count(grid)
    if ncomb == 0:
        raise HTTPException(status_code=400, detail="网格组合数为 0")
    max_c = int(parsed.max_combinations)
    if ncomb > max_c:
        raise HTTPException(
            status_code=400,
            detail=f"组合数 {ncomb} 超过上限 {max_c}，请缩小 grid 或提高 max_combinations（≤10000）",
        )

    sym = str(parsed.symbol or "").strip().upper()
    bars_raw = m._resolve_bars_for_backtest_compare(
        sym,
        parsed.periods,
        parsed.days,
        parsed.kline,
        None,
        use_server_kline_cache=bool(parsed.use_server_kline_cache),
    )
    base = dict(parsed.strategy_config) if isinstance(parsed.strategy_config, dict) else {}
    from mcp_server.strategy_qqq_0dte.config import Qqq0dteConfig

    cfg_base = Qqq0dteConfig.from_dict(base)
    cfg_base.symbol = sym
    bar_count_total = len(bars_raw)
    if bool(parsed.rth_only):
        bars, bar_count_rth, bar_count_non_rth = _filter_rth_bars_for_qqq_0dte(list(bars_raw), cfg_base)
    else:
        bars = list(bars_raw)
        bar_count_rth = sum(
            1
            for b in bars
            if isinstance(getattr(b, "date", None), datetime)
            and _is_rth_bar(
                b.date,
                tz_name=cfg_base.assume_bars_timezone,
                open_hour=cfg_base.rth_open_hour,
                open_minute=cfg_base.rth_open_minute,
                close_hour=cfg_base.rth_close_hour,
                close_minute=cfg_base.rth_close_minute,
            )
        )
        bar_count_non_rth = len(bars) - bar_count_rth
    grid_norm = {str(k): list(vals) for k, vals in grid.items()}
    rows = run_parameter_matrix(
        list(bars),
        base_strategy_config=base,
        grid=grid_norm,
        symbol=sym,
        suppress_logs=bool(parsed.suppress_logs),
    )

    sort_key = str(parsed.sort_by)
    if sort_key == "return_pct":

        def _rk(r: dict[str, Any]) -> tuple[bool, float, float]:
            rp = r.get("return_pct")
            pnl = float(r.get("realized_pnl") or 0.0)
            if rp is None:
                return (False, -1e18, pnl)
            return (True, float(rp), pnl)

        rows.sort(key=_rk, reverse=True)
    else:
        rows.sort(key=lambda r: float(r.get("realized_pnl") or 0.0), reverse=True)

    top_n = int(parsed.top_n)
    return {
        "symbol": sym,
        "days": int(parsed.days),
        "periods": int(parsed.periods),
        "kline": str(parsed.kline),
        "rth_only": bool(parsed.rth_only),
        "bar_count_total": int(bar_count_total),
        "bar_count_rth": int(bar_count_rth),
        "bar_count_non_rth": int(bar_count_non_rth),
        "bar_count_first": int(rows[0]["bar_count"]) if rows else 0,
        "combinations_run": ncomb,
        "sort_by": sort_key,
        "top": rows[:top_n],
        "disclaimer": "样本内网格易过拟合；回测为合成期权价，排名仅供参考。",
    }


def _qqq_live_worker_data_subdir(instance: str) -> str:
    return "qqq_1dte" if instance == "1dte" else "qqq_0dte"


def qqq_live_worker_default_config(instance: str = "0dte") -> dict[str, Any]:
    """与 api/qqq_0dte_live_worker 读取的 JSON 结构一致；用于缺省与 GET 归并。1DTE 默认 expiry_offset_days=1。"""
    out: dict[str, Any] = {
        "api_base_url": "http://127.0.0.1:8010",
        "account_id": None,
        "symbol": "QQQ.US",
        "stock_options_mode": bool(instance == "1dte"),
        "stock_pool": ["QQQ.US"] if instance == "1dte" else [],
        "history_days": 2,
        "kline": "1m",
        "poll_seconds": 30,
        "trade_bar_freshness_seconds": 90,
        "skip_historical_bars_on_startup": True,
        "restore_open_positions_on_startup": True,
        "dry_run": True,
        "confirmation_token": None,
        "expiry_date": None,
        "expiry_offset_days": 1 if instance == "1dte" else 0,
        "kline_wall_clock_timezone": "Asia/Shanghai",
        "resolve": {"strike_window": 5.0, "standard_only": False, "max_strike_diff": 1.5},
        "strategy_config": {},
    }
    if instance == "1dte":
        out["intraday_safety"] = {
            "enabled": True,
            "max_bid_ask_spread_pct": 0.18,
            "min_bid": 0.01,
            "min_ask": 0.01,
            "min_mid": 0.03,
            "min_volume": 0,
            "min_open_interest": 0,
            "require_bid": True,
            "latest_entry_hhmm_et": "15:00",
            "block_entry_when_unmanaged_positions": True,
            "block_market_order": True,
            "post_entry_check_delay_seconds": 1.0,
            "cancel_open_entry_orders_after_submit": True,
            "cancel_active_orders_at_force_close": True,
        }
    return out


def qqq_0dte_live_worker_default_config() -> dict[str, Any]:
    return qqq_live_worker_default_config("0dte")


def _qqq_live_worker_config_path(instance: str, owner_id: str | None = None) -> str:
    m = _m()
    sub = _qqq_live_worker_data_subdir(instance)
    owner = _normalize_owner_id(owner_id)
    if owner:
        return _owner_data_dir(owner, sub, "live_worker_config.json")
    return os.path.join(m.ROOT, "data", sub, "live_worker_config.json")


def qqq_live_worker_config_get(instance: str, owner_id: str | None = None) -> dict[str, Any]:
    sub = _qqq_live_worker_data_subdir(instance)
    path = _qqq_live_worker_config_path(instance, owner_id)
    legacy_path = os.path.join(_m().ROOT, "data", sub, "live_worker_config.json")
    base = qqq_live_worker_default_config(instance)
    if _normalize_owner_id(owner_id):
        base = _stamp_config_owner_context(base, owner_id)
    if _normalize_owner_id(owner_id) and not os.path.isfile(path) and os.path.isfile(legacy_path):
        disk = _read_json_path(legacy_path)
        if _legacy_config_matches_owner_context(disk, owner_id):
            disk = _stamp_config_owner_context(disk, owner_id)
            _write_json_path(path, disk)
    if not os.path.isfile(path):
        return dict(base)
    try:
        with open(path, "r", encoding="utf-8") as f:
            disk = json.load(f)
        if not isinstance(disk, dict):
            return dict(base)
        merged = dict(base)
        merged.update({k: v for k, v in disk.items() if k not in {"resolve", "strategy_config"}})
        if isinstance(disk.get("resolve"), dict):
            r0 = dict(base["resolve"])
            r0.update(disk["resolve"])
            merged["resolve"] = r0
        if isinstance(disk.get("strategy_config"), dict):
            merged["strategy_config"] = disk["strategy_config"]
        else:
            merged["strategy_config"] = {}
        return _stamp_config_owner_context(merged, owner_id) if _normalize_owner_id(owner_id) else merged
    except Exception:
        return dict(base)


def qqq_0dte_live_worker_config_get(owner_id: str | None = None) -> dict[str, Any]:
    return qqq_live_worker_config_get("0dte", owner_id=owner_id)


def qqq_1dte_live_worker_config_get(owner_id: str | None = None) -> dict[str, Any]:
    return qqq_live_worker_config_get("1dte", owner_id=owner_id)


def qqq_live_worker_config_put(instance: str, body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    path = _qqq_live_worker_config_path(instance, owner_id)
    cur = qqq_live_worker_config_get(instance, owner_id=owner_id)
    if not isinstance(body, dict):
        body = {}
    for k, v in body.items():
        if k == "strategy_config":
            if isinstance(v, dict):
                cur["strategy_config"] = v
            continue
        if k == "resolve":
            if isinstance(v, dict):
                r = dict(cur.get("resolve") or {})
                r.update(v)
                cur["resolve"] = r
            continue
        if k == "intraday_safety":
            if isinstance(v, dict):
                r = dict(cur.get("intraday_safety") or {})
                r.update(v)
                cur["intraday_safety"] = r
            continue
        if k == "expiry_offset_days":
            try:
                cur["expiry_offset_days"] = int(v)
            except Exception:
                cur["expiry_offset_days"] = int(qqq_live_worker_default_config(instance)["expiry_offset_days"])
            continue
        if k in {"confirmation_token", "expiry_date"} and (v == "" or v is None):
            cur[k] = None
            continue
        cur[k] = v
    if not isinstance(cur.get("strategy_config"), dict):
        cur["strategy_config"] = {}
    if not isinstance(cur.get("resolve"), dict):
        cur["resolve"] = dict(qqq_live_worker_default_config(instance)["resolve"])
    if instance == "1dte" and not isinstance(cur.get("intraday_safety"), dict):
        cur["intraday_safety"] = dict(qqq_live_worker_default_config(instance).get("intraday_safety") or {})
    cur = _stamp_config_owner_context(cur, owner_id) if _normalize_owner_id(owner_id) else cur
    _write_json_path(path, cur)
    return {"ok": True, "config": cur}


def qqq_0dte_live_worker_config_put(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    return qqq_live_worker_config_put("0dte", body if isinstance(body, dict) else {}, owner_id=owner_id)


def qqq_1dte_live_worker_config_put(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    return qqq_live_worker_config_put("1dte", body if isinstance(body, dict) else {}, owner_id=owner_id)


def qqq_live_worker_manual_review_lock_clear(
    instance: str,
    body: dict[str, Any] | None = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    m = _m()
    sub = _qqq_live_worker_data_subdir(instance)
    path = os.path.join(m.ROOT, "data", sub, "live_worker_manual_review_lock.json")
    context: dict[str, str] = {}
    if str(owner_id or "").strip():
        try:
            context = m._qqq_live_status_account_context(instance, owner_id)
        except Exception:
            context = {"owner_id": str(owner_id or "").strip().lower()}
    previous: dict[str, Any] | None = None
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            previous = loaded if isinstance(loaded, dict) else None
        except Exception:
            previous = None
    cleared = False
    if previous and context:
        try:
            matches_context = bool(m._manual_review_lock_matches_context(previous, context))
        except Exception:
            matches_context = False
        if not matches_context:
            return {
                "ok": False,
                "cleared": False,
                "error": "manual_review_lock_context_mismatch",
                "previous": previous,
                "context": context,
                "path": path,
            }
    try:
        if os.path.isfile(path):
            os.remove(path)
            cleared = True
    except Exception as e:
        raise _m().HTTPException(status_code=500, detail={"error": "manual_review_lock_clear_failed", "message": str(e)})
    note = ""
    if isinstance(body, dict):
        note = str(body.get("note") or "").strip()
    return {
        "ok": True,
        "cleared": cleared,
        "previous": previous,
        "context": context or None,
        "note": note or None,
        "cleared_at": datetime.now(timezone.utc).isoformat(),
        "path": path,
    }


def qqq_0dte_live_worker_manual_review_lock_clear(
    body: dict[str, Any] | None = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    return qqq_live_worker_manual_review_lock_clear("0dte", body, owner_id=owner_id)


def qqq_1dte_live_worker_manual_review_lock_clear(
    body: dict[str, Any] | None = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    return qqq_live_worker_manual_review_lock_clear("1dte", body, owner_id=owner_id)


def stock_options_swing_default_config() -> dict[str, Any]:
    return {
        "api_base_url": "http://127.0.0.1:8010",
        "account_id": None,
        "symbol": "QQQ.US",
        "stock_pool": ["QQQ.US", "NVDA.US", "AAPL.US", "MSFT.US", "TSLA.US"],
        "history_days": 260,
        "kline": "1d",
        "poll_seconds": 3600,
        "scan_time_hhmm_et": "10:00",
        "second_scan_time_hhmm_et": "15:30",
        "dry_run": True,
        "auto_submit_orders": False,
        "live_submit_confirmed_at": None,
        "live_submit_confirmed_by": None,
        "confirmation_token": None,
        "contracts": 1,
        "strategy": {
            "strategy_variant": "swing_trend_call",
            "mode": "long_call",
            "trend_fast_ma": 20,
            "trend_slow_ma": 50,
            "long_ma": 200,
            "min_trend_score": 3,
            "min_price_above_slow_ma_pct": 0.0,
            "max_price_above_fast_ma_pct": 0.12,
            "min_dte": 45,
            "target_dte": 90,
            "max_dte": 180,
            "target_delta_min": 0.35,
            "target_delta_max": 0.7,
            "fallback_otm_pct": 0.03,
            "spread_width_pct": 0.05,
            "max_spread_debit": 600.0,
            "min_open_interest": 50,
            "min_option_volume": 1,
            "max_bid_ask_spread_pct": 0.18,
            "take_profit_pct": 0.8,
            "stop_loss_pct": 0.45,
            "dte_exit_days": 21,
            "earnings_blackout_days": 7,
            "trend_exit_below_ma": 50,
            "trend_exit_confirm_bars": 2,
        },
        "managed_positions_only": True,
        "strict_account_ledger_match": True,
        "allow_import_existing_positions": False,
        "skip_existing_broker_positions": True,
        "symbol_blacklist": [],
        "event_blackouts": [],
        "risk": {
            "max_contracts_per_order": 1,
            "max_open_contracts": 10,
            "max_premium_per_order": 800.0,
            "max_premium_per_symbol": 1500.0,
            "max_total_option_premium": 4000.0,
            "max_new_premium_per_day": 1500.0,
        },
    }


def _stock_options_swing_config_path(owner_id: str | None = None) -> str:
    m = _m()
    owner = _normalize_owner_id(owner_id)
    if owner:
        return _owner_data_dir(owner, "stock_options_swing", "live_worker_config.json")
    return os.path.join(m.ROOT, "data", "stock_options_swing", "live_worker_config.json")


def stock_options_swing_config_get(owner_id: str | None = None) -> dict[str, Any]:
    path = _stock_options_swing_config_path(owner_id)
    legacy_path = os.path.join(_m().ROOT, "data", "stock_options_swing", "live_worker_config.json")
    base = stock_options_swing_default_config()
    if _normalize_owner_id(owner_id):
        base = _stamp_config_owner_context(base, owner_id)
    if _normalize_owner_id(owner_id) and not os.path.isfile(path) and os.path.isfile(legacy_path):
        disk = _read_json_path(legacy_path)
        if _legacy_config_matches_owner_context(disk, owner_id):
            disk = _stamp_config_owner_context(disk, owner_id)
            _write_json_path(path, disk)
    if not os.path.isfile(path):
        return dict(base)
    try:
        with open(path, "r", encoding="utf-8") as f:
            disk = json.load(f)
        if not isinstance(disk, dict):
            return dict(base)
        merged = dict(base)
        merged.update({k: v for k, v in disk.items() if k not in {"strategy", "risk"}})
        if isinstance(disk.get("strategy"), dict):
            strat = dict(base["strategy"])
            strat.update(disk["strategy"])
            merged["strategy"] = strat
        if isinstance(disk.get("risk"), dict):
            risk = dict(base["risk"])
            risk.update(disk["risk"])
            merged["risk"] = risk
        return _stamp_config_owner_context(merged, owner_id) if _normalize_owner_id(owner_id) else merged
    except Exception:
        return dict(base)


def stock_options_swing_config_put(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    from fastapi import HTTPException

    path = _stock_options_swing_config_path(owner_id)
    cur = stock_options_swing_config_get(owner_id=owner_id)
    payload = body if isinstance(body, dict) else {}
    before_live_submit = (not bool(cur.get("dry_run", True))) and bool(cur.get("auto_submit_orders", False))
    after_dry_run = bool(payload.get("dry_run", cur.get("dry_run", True)))
    after_auto_submit = bool(payload.get("auto_submit_orders", cur.get("auto_submit_orders", False)))
    after_live_submit = (not after_dry_run) and after_auto_submit
    if after_live_submit and not str(payload.get("confirmation_token") or cur.get("confirmation_token") or "").strip():
        raise HTTPException(status_code=400, detail="confirmation_token_required_for_live_auto_submit")
    if after_live_submit and not before_live_submit:
        phrase = str(payload.get("live_submit_confirm") or "").strip()
        if phrase != "CONFIRM_LIVE_AUTO_SUBMIT":
            raise HTTPException(status_code=400, detail="live_auto_submit_second_confirmation_required")
    for k, v in payload.items():
        if k == "live_submit_confirm":
            continue
        if k == "strategy":
            if isinstance(v, dict):
                strat = dict(cur.get("strategy") or {})
                strat.update(v)
                cur["strategy"] = strat
            continue
        if k == "risk":
            if isinstance(v, dict):
                risk = dict(cur.get("risk") or {})
                risk.update(v)
                cur["risk"] = risk
            continue
        if k == "confirmation_token" and (v == "" or v is None):
            cur[k] = None
            continue
        cur[k] = v
    cur_live_submit = (not bool(cur.get("dry_run", True))) and bool(cur.get("auto_submit_orders", False))
    if cur_live_submit:
        cur["live_submit_confirmed_at"] = datetime.now(timezone.utc).isoformat()
        cur["live_submit_confirmed_by"] = "local_ui"
    else:
        cur["live_submit_confirmed_at"] = None
        cur["live_submit_confirmed_by"] = None
    if not isinstance(cur.get("strategy"), dict):
        cur["strategy"] = dict(stock_options_swing_default_config()["strategy"])
    cur = _stamp_config_owner_context(cur, owner_id) if _normalize_owner_id(owner_id) else cur
    _write_json_path(path, cur)
    return {"ok": True, "config": cur}


def _stock_options_swing_append_ledger(event: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    path = os.path.join(m.ROOT, "data", "stock_options_swing", "live_worker_execution_ledger.jsonl")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = dict(event)
    payload.setdefault("at", datetime.now(timezone.utc).isoformat())
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str))
        f.write("\n")
    return {"ok": True, "path": path, "event": payload}


def stock_options_swing_refresh_positions_runtime(owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    cfg = stock_options_swing_config_get(owner_id=owner_id)
    path = m.STOCK_OPTIONS_SWING_WORKER_RUNTIME_FILE
    try:
        from api import stock_options_swing_worker as sw

        sw._API_BASE_URL = str(cfg.get("api_base_url") or sw._API_BASE_URL).strip().rstrip("/")
        sw._API_BEARER_TOKEN = sw._resolve_api_bearer(cfg)
        sw._API_KEY = sw._resolve_api_key(cfg)
        sw._API_LOCAL_OWNER = str(owner_id or cfg.get("owner_id") or sw._API_LOCAL_OWNER or "").strip().lower()
        sw._API_ACCOUNT_ID = str(cfg.get("account_id") or sw._API_ACCOUNT_ID or "").strip()
        sw._API_BROKER_PROVIDER = str(cfg.get("broker_provider") or sw._API_BROKER_PROVIDER or "").strip().lower()
        snapshot = sw.build_position_management_snapshot(cfg)
    except Exception as e:
        return {"ok": False, "error": str(e), "path": path}

    existing: dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                disk = json.load(f)
            if isinstance(disk, dict):
                existing = disk
        except Exception:
            existing = {}
    merged = dict(existing)
    merged.update(snapshot)
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    merged["position_snapshot_refreshed_by"] = "manual_position_action"
    if "worker_running" not in merged:
        pid = read_pid_file(m.STOCK_OPTIONS_SWING_WORKER_PID_FILE)
        merged["worker_running"] = bool(pid and is_pid_alive(pid))
    if "pid" not in merged:
        pid = read_pid_file(m.STOCK_OPTIONS_SWING_WORKER_PID_FILE)
        if pid:
            merged["pid"] = pid
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
        os.replace(tmp, path)
    except Exception as e:
        return {"ok": False, "error": str(e), "path": path, "snapshot": snapshot}
    return {"ok": True, "path": path, "runtime": merged}


def stock_options_swing_position_action(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    from fastapi import HTTPException

    payload = body if isinstance(body, dict) else {}
    action = str(payload.get("action") or "").strip().lower()
    option_symbol = str(payload.get("symbol") or payload.get("option_symbol") or "").strip().upper()
    underlying = str(payload.get("underlying") or "").strip().upper()
    if underlying and "." not in underlying:
        underlying = f"{underlying}.US"
    if not option_symbol and action in {"import", "unmanage"}:
        raise HTTPException(status_code=400, detail="option_symbol_required")
    cfg = stock_options_swing_config_get(owner_id=owner_id)
    if action == "import":
        result = _stock_options_swing_append_ledger(
            {
                "event": "imported_existing_position",
                "option_symbol": option_symbol,
                "symbol": option_symbol,
                "underlying": underlying,
                "quantity": payload.get("quantity"),
                "cost_price": payload.get("cost_price"),
                "current_price": payload.get("current_price"),
                "source": "manual_ui_import",
                "owner_id": owner_id,
                "account_id": cfg.get("account_id"),
                "broker_provider": cfg.get("broker_provider"),
            }
        )
        result["refresh"] = stock_options_swing_refresh_positions_runtime(owner_id=owner_id)
        return result
    if action == "unmanage":
        result = _stock_options_swing_append_ledger(
            {
                "event": "closed",
                "option_symbol": option_symbol,
                "symbol": option_symbol,
                "underlying": underlying,
                "source": "manual_ui_unmanage",
                "owner_id": owner_id,
                "account_id": cfg.get("account_id"),
                "broker_provider": cfg.get("broker_provider"),
            }
        )
        result["refresh"] = stock_options_swing_refresh_positions_runtime(owner_id=owner_id)
        return result
    if action in {"blacklist", "unblacklist"}:
        target = underlying
        if not target and option_symbol:
            import re

            m = re.match(r"^([A-Z0-9]+?)(\d{6}|\d{8})[CP]\d+\.US$", option_symbol)
            if m:
                root = re.sub(r"\d+$", "", m.group(1)) or m.group(1)
                target = f"{root}.US"
        if not target:
            raise HTTPException(status_code=400, detail="underlying_required")
        cur = stock_options_swing_config_get(owner_id=owner_id)
        rows = cur.get("symbol_blacklist") if isinstance(cur.get("symbol_blacklist"), list) else []
        values = {str(x or "").strip().upper() for x in rows if str(x or "").strip()}
        if action == "blacklist":
            values.add(target)
        else:
            values.discard(target)
        result = stock_options_swing_config_put({"symbol_blacklist": sorted(values)}, owner_id=owner_id)
        result["refresh"] = stock_options_swing_refresh_positions_runtime(owner_id=owner_id)
        return result
    raise HTTPException(status_code=400, detail="unsupported_action")


def _row_matches_runtime_account_context(row: dict[str, Any] | None, context: dict[str, str] | None) -> bool:
    if not isinstance(row, dict):
        return False
    ctx = context if isinstance(context, dict) else {}
    for key in ("owner_id", "account_id", "broker_provider"):
        current = str(ctx.get(key) or "").strip().lower()
        got = str(row.get(key) or "").strip().lower()
        if current and (not got or got != current):
            return False
    return True


def _qqq_live_status_context(instance: str, owner_id: str | None = None) -> dict[str, str]:
    m = _m()
    owner = str(owner_id or "").strip().lower()
    if not owner:
        return {}
    try:
        return m._qqq_live_status_account_context(instance, owner)
    except Exception:
        return {"owner_id": owner}


def _stock_options_swing_status_context(owner_id: str | None = None) -> dict[str, str]:
    m = _m()
    owner = str(owner_id or "").strip().lower()
    if not owner:
        return {}
    try:
        return m._stock_options_swing_status_account_context(owner)
    except Exception:
        return {"owner_id": owner}


def qqq_live_worker_decision_tail_get(instance: str, limit: int = 20, owner_id: str | None = None) -> dict[str, Any]:
    """读取指定实例 Worker 的决策尾 JSONL。"""
    m = _m()
    sub = _qqq_live_worker_data_subdir(instance)
    path = os.path.join(m.ROOT, "data", sub, "live_worker_decision_tail.jsonl")
    lim = max(1, min(100, int(limit)))
    context = _qqq_live_status_context(instance, owner_id)
    if not os.path.isfile(path):
        return {"ok": True, "items": [], "path": path, "returned": 0, "account_context": context or None}
    try:
        chunk = 512 * 1024
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            sz = f.tell()
            if sz <= chunk:
                f.seek(0)
                raw = f.read().decode("utf-8", errors="ignore")
            else:
                f.seek(max(0, sz - chunk))
                raw = f.read().decode("utf-8", errors="ignore")
                nl = raw.find("\n")
                if nl >= 0:
                    raw = raw[nl + 1 :]
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        tail = lines[-max(lim, 100):]
        items: list[Any] = []
        for ln in tail:
            try:
                row = json.loads(ln)
                if isinstance(row, dict) and _row_matches_runtime_account_context(row, context):
                    items.append(row)
            except Exception:
                continue
        items = items[-lim:]
        return {"ok": True, "items": items, "path": path, "returned": len(items), "account_context": context or None}
    except Exception as e:
        return {"ok": False, "error": str(e), "items": [], "path": path, "returned": 0}


def qqq_0dte_live_worker_decision_tail_get(limit: int = 20, owner_id: str | None = None) -> dict[str, Any]:
    return qqq_live_worker_decision_tail_get("0dte", limit, owner_id=owner_id)


def qqq_1dte_live_worker_decision_tail_get(limit: int = 20, owner_id: str | None = None) -> dict[str, Any]:
    return qqq_live_worker_decision_tail_get("1dte", limit, owner_id=owner_id)


def stock_options_swing_decision_tail_get(limit: int = 20, owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    path = os.path.join(m.ROOT, "data", "stock_options_swing", "live_worker_decision_tail.jsonl")
    lim = max(1, min(100, int(limit)))
    context = _stock_options_swing_status_context(owner_id)
    if not os.path.isfile(path):
        return {"ok": True, "items": [], "path": path, "returned": 0, "account_context": context or None}
    try:
        chunk = 512 * 1024
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            sz = f.tell()
            if sz <= chunk:
                f.seek(0)
                raw = f.read().decode("utf-8", errors="ignore")
            else:
                f.seek(max(0, sz - chunk))
                raw = f.read().decode("utf-8", errors="ignore")
                nl = raw.find("\n")
                if nl >= 0:
                    raw = raw[nl + 1 :]
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        items: list[Any] = []
        for ln in lines[-max(lim, 100):]:
            try:
                obj = json.loads(ln)
            except Exception:
                obj = {"raw": ln}
            if isinstance(obj, dict) and _row_matches_runtime_account_context(obj, context):
                items.append(obj)
        items = items[-lim:]
        return {"ok": True, "items": items, "path": path, "returned": len(items), "account_context": context or None}
    except Exception as e:
        return {"ok": False, "error": str(e), "items": [], "path": path, "returned": 0}


def _qqq_live_strategy_recommendation_compute_live(instance: str) -> dict[str, Any] | None:
    """
    与 Worker 相同启发式：用 live_worker_config 的标的/时区/K 线 + 当前行情即时计算。
    在 Worker 未写入 JSON 时由 GET 接口回退调用，避免「必须开 Worker 才能看到推荐」。
    """
    try:
        m = _m()
        from mcp_server.strategy_qqq_0dte.config import Qqq0dteConfig
        from mcp_server.strategy_qqq_0dte.session_us import ny_date
        from mcp_server.strategy_qqq_0dte.strategy_recommendation import compute_strategy_recommendation

        raw = qqq_live_worker_config_get(instance)
        sym = str(raw.get("symbol") or "QQQ.US").strip().upper()
        days = max(1, int(raw.get("history_days", 2)))
        kline = str(raw.get("kline", "1m"))
        strat = raw.get("strategy_config") if isinstance(raw.get("strategy_config"), dict) else {}
        cfg = Qqq0dteConfig.from_dict(strat)
        kwtz = raw.get("kline_wall_clock_timezone")
        if isinstance(kwtz, str) and kwtz.strip():
            cfg.assume_bars_timezone = kwtz.strip()

        bars = m._fetch_bars(sym, days, kline)  # type: ignore[arg-type]
        today_d = ny_date(datetime.now(timezone.utc), cfg.assume_bars_timezone)

        gw = m._gateway_get_json("/internal/longport/quote", {"symbol": sym}) or {}
        if not isinstance(gw, dict):
            gw = {}
        last = gw.get("last")
        prev = gw.get("prev_close")
        chg_pct = None
        try:
            if prev is not None and float(prev) > 0 and last is not None:
                chg_pct = round((float(last) - float(prev)) / float(prev) * 100.0, 4)
        except Exception:
            chg_pct = gw.get("change_pct")

        rt_fields: dict[str, Any] = {
            "realtime_quote": {
                "available": bool(gw.get("available", False)),
                "last": last,
                "prev_close": prev,
                "change_pct": chg_pct,
                "timestamp": gw.get("timestamp"),
            }
        }

        vix_sym = str(getattr(cfg, "gamma_vix_symbol", "VIX.US") or "VIX.US").strip().upper()
        vgw = m._gateway_get_json("/internal/longport/quote", {"symbol": vix_sym}) or {}
        vix_chg = 0.0
        if isinstance(vgw, dict) and vgw.get("available"):
            try:
                pv = float(vgw.get("prev_close") or 0.0)
                lv = vgw.get("last")
                if pv > 0 and lv is not None:
                    vix_chg = (float(lv) - pv) / pv * 100.0
            except Exception:
                try:
                    vix_chg = float(vgw.get("change_pct") or 0.0)
                except Exception:
                    vix_chg = 0.0

        payload = compute_strategy_recommendation(
            symbol=sym,
            cfg=cfg,
            bars=bars,
            today_d=today_d,
            rt_fields=rt_fields,
            vix_change_pct=vix_chg,
        )
        payload["source"] = "api_on_demand"
        payload["note"] = (
            "由后端在缺少 Worker 生成文件时即时拉取行情与 K 线计算，规则与 Worker 一致；不参与下单。"
        )
        return payload
    except Exception:
        return None


def qqq_live_strategy_recommendation_get(instance: str) -> dict[str, Any]:
    """优先读 Worker 写入的 JSON；若无文件则尝试与 Worker 等价的即时计算。"""
    m = _m()
    sub = _qqq_live_worker_data_subdir(instance)
    path = os.path.join(m.ROOT, "data", sub, "strategy_recommendation.json")
    disclaimer = (
        "本推荐根据 QQQ（及配置标的）行情快照与 K 线统计生成，仅供参考，不构成投资建议，且不触发任何实盘下单。"
    )
    err_hint = f"data/{sub}/strategy_recommendation_error.json"
    fallback: dict[str, Any] = {
        "ok": False,
        "error": "unavailable",
        "message": f"暂无系统推荐：请确认 LongPort 已连接且可拉取行情；若已启动 Worker，可查看 {err_hint}。",
        "disclaimer": disclaimer,
        "scan_interval_seconds": 600,
    }
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                out = dict(data)
                out["ok"] = bool(data.get("ok", True))
                out["source"] = "worker_file"
                return out
        except Exception as e:
            fallback = {**fallback, "error": "read_failed", "message": str(e)}

    computed = _qqq_live_strategy_recommendation_compute_live(instance)
    if computed:
        out = dict(computed)
        out["ok"] = True
        return out

    return fallback


def qqq_0dte_strategy_recommendation_get() -> dict[str, Any]:
    return qqq_live_strategy_recommendation_get("0dte")


def qqq_1dte_strategy_recommendation_get() -> dict[str, Any]:
    return qqq_live_strategy_recommendation_get("1dte")


def qqq_0dte_resolve_contract(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    from mcp_server.strategy_qqq_0dte.live_contract import fetch_and_resolve_0dte_leg

    parsed = Qqq0dteResolveContractBody.model_validate(body if isinstance(body, dict) else {})
    qctx, _ = m.ensure_contexts(owner_id=owner_id)
    return fetch_and_resolve_0dte_leg(
        qctx,
        str(parsed.symbol).strip(),
        float(parsed.strike),
        str(parsed.right),
        expiry_date=parsed.expiry_date,
        strike_window=float(parsed.strike_window),
        standard_only=bool(parsed.standard_only),
        max_strike_diff=float(parsed.max_strike_diff),
        use_bid_for_sell_limit=bool(parsed.use_bid_for_sell_limit),
        use_ask_for_buy_limit=bool(parsed.use_ask_for_buy_limit),
    )


def options_backtest(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = OptionBacktestBody.model_validate(body if isinstance(body, dict) else {})
    sym = str(parsed.symbol or "").strip().upper()
    bars = m._resolve_bars_for_backtest_compare(
        sym,
        parsed.periods,
        parsed.days,
        parsed.kline,
        None,
        use_server_kline_cache=bool(parsed.use_server_kline_cache),
    )
    return m.svc_run_option_backtest(
        sym,
        parsed.template,
        holding_bars=parsed.holding_days,
        contracts=parsed.contracts,
        width_pct=parsed.width_pct,
        bars=bars,
        days=parsed.days,
        kline=parsed.kline,
        periods=parsed.periods,
    )


def backtests_create(body: dict[str, Any]) -> dict[str, Any]:
    from fastapi import HTTPException

    payload = body if isinstance(body, dict) else {}
    kind = str(payload.get("kind") or payload.get("type") or "").strip().lower()
    request = payload.get("request") if isinstance(payload.get("request"), dict) else payload.get("payload")
    if not isinstance(request, dict):
        request = {k: v for k, v in payload.items() if k not in {"kind", "type", "request", "payload"}}
    if kind in {"options_combo", "option_combo", "options"}:
        return run_sync_backtest_task(
            kind="options_combo",
            source_module="options",
            request=dict(request),
            runner=options_backtest,
        )
    if kind in {"qqq_0dte_strategy", "qqq-0dte-strategy", "options-0dte"}:
        return run_sync_backtest_task(
            kind="qqq_0dte_strategy",
            source_module="auto-trading/options-0dte",
            request=dict(request),
            runner=qqq_0dte_backtest,
        )
    raise HTTPException(
        status_code=400,
        detail={
            "error": "unsupported_backtest_kind",
            "supported": ["options_combo", "qqq_0dte_strategy"],
        },
    )


def backtests_list(limit: int = 50, kind: str | None = None) -> dict[str, Any]:
    return {"ok": True, "items": list_backtest_tasks(limit=limit, kind=kind), "limit": max(1, min(200, int(limit)))}


def backtests_get(task_id: str) -> dict[str, Any]:
    return get_backtest_task(task_id)


def backtests_events(task_id: str) -> dict[str, Any]:
    task = get_backtest_task(task_id)
    return {"ok": bool(task), "task_id": task_id, "events": get_backtest_events(task_id)}


def backtests_cancel(task_id: str) -> dict[str, Any]:
    task = get_backtest_task(task_id)
    if not task:
        return {"ok": False, "task_id": task_id, "status": "not_found"}
    if task.get("status") in {"completed", "failed", "cancelled"}:
        return {"ok": True, "task_id": task_id, "status": task.get("status"), "cancelled": False}
    task["status"] = "cancelled"
    return {"ok": True, "task_id": task_id, "status": "cancelled", "cancelled": True}


def trade_positions(account_id: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    gw = m._gateway_get_json("/trade/positions", {"account_id": account_id} if account_id else None)
    if _is_trade_gateway_success(gw) and isinstance(gw.get("positions"), list):
        return gw
    def _load() -> dict[str, Any]:
        qctx, tctx = m.ensure_contexts(account_id, owner_id=owner_id)
        pos = m.broker_get_stock_positions(tctx)
        rows: list[dict[str, Any]] = []
        for ch in pos.channels:
            for p in ch.positions:
                cur = 0.0
                price_type = "-"
                try:
                    q = m.broker_get_quotes(qctx, [p.symbol])
                    if q:
                        cur, price_type = m._get_realtime_price(q[0])
                except Exception:
                    pass
                qty = float(p.quantity)
                cost = float(p.cost_price)
                value = qty * cur
                pnl = value - qty * cost
                rows.append(
                    {
                        "symbol": p.symbol,
                        "quantity": qty,
                        "cost_price": cost,
                        "current_price": cur,
                        "pnl": round(pnl, 2),
                        "price_type": price_type,
                    }
                )
        return {"positions": rows}

    return _with_trade_context_retry("trade_positions", account_id=account_id, owner_id=owner_id, fn=_load)
    qctx, tctx = m.ensure_contexts(account_id, owner_id=owner_id)
    pos = m.broker_get_stock_positions(tctx)
    rows: list[dict[str, Any]] = []
    for ch in pos.channels:
        for p in ch.positions:
            cur = 0.0
            price_type = "-"
            try:
                q = m.broker_get_quotes(qctx, [p.symbol])
                if q:
                    cur, price_type = m._get_realtime_price(q[0])
            except Exception:
                pass
            qty = float(p.quantity)
            cost = float(p.cost_price)
            value = qty * cur
            pnl = value - qty * cost
            rows.append(
                {
                    "symbol": p.symbol,
                    "quantity": qty,
                    "cost_price": cost,
                    "current_price": cur,
                    "pnl": round(pnl, 2),
                    "price_type": price_type,
                }
            )
    return {"positions": rows}


def _order_raw_value(row: Any, *names: str) -> Any:
    for name in names:
        val = getattr(row, name, None)
        if val not in (None, ""):
            return val
    raw = getattr(row, "raw", None)
    if isinstance(raw, dict):
        for name in names:
            val = raw.get(name)
            if val not in (None, ""):
                return val
    return None


def _order_float_value(row: Any, *names: str) -> float:
    val = _order_raw_value(row, *names)
    try:
        return float(val) if val not in (None, "") else 0.0
    except Exception:
        return 0.0


def _order_filled_quantity(row: Any) -> float:
    return _order_float_value(
        row,
        "filled_quantity",
        "filled_qty",
        "executed_quantity",
        "dealt_quantity",
        "filledQuantity",
        "filledQty",
        "filled_quantity",
        "dealQuantity",
    )


def _order_avg_fill_price(row: Any) -> float:
    return _order_float_value(
        row,
        "avg_fill_price",
        "average_fill_price",
        "executed_price",
        "dealt_avg_price",
        "filledPrice",
        "avgFilledPrice",
        "dealAvgPrice",
    )


def trade_orders(status: str = "all", account_id: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    query = {"status": status}
    if account_id:
        query["account_id"] = account_id
    gw = m._gateway_get_json("/trade/orders", query)
    if _is_trade_gateway_success(gw) and isinstance(gw.get("orders"), list):
        return gw
    def _load() -> dict[str, Any]:
        _, tctx = m.ensure_contexts(account_id, owner_id=owner_id)
        allowed = {"active": {"New", "PartialFilled"}, "filled": {"Filled"}, "cancelled": {"Canceled"}}.get(status)
        orders = []
        for o in m.broker_get_today_orders(tctx):
            s = str(o.status)
            if allowed and s not in allowed:
                continue
            orders.append(
                {
                    "order_id": o.order_id,
                    "symbol": o.symbol,
                    "side": str(o.side),
                    "quantity": float(o.quantity),
                    "price": float(o.price) if o.price else None,
                    "status": s,
                    "filled_quantity": _order_filled_quantity(o),
                    "avg_fill_price": _order_avg_fill_price(o),
                }
            )
        return {"orders": orders}

    return _with_trade_context_retry("trade_orders", account_id=account_id, owner_id=owner_id, fn=_load)
    _, tctx = m.ensure_contexts(account_id, owner_id=owner_id)
    allowed = {"active": {"New", "PartialFilled"}, "filled": {"Filled"}, "cancelled": {"Canceled"}}.get(status)
    orders = []
    for o in m.broker_get_today_orders(tctx):
        s = str(o.status)
        if allowed and s not in allowed:
            continue
        orders.append(
            {
                "order_id": o.order_id,
                "symbol": o.symbol,
                "side": str(o.side),
                "quantity": float(o.quantity),
                "price": float(o.price) if o.price else None,
                "status": s,
            }
        )
    return {"orders": orders}


def trade_submit_order(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    parsed = SubmitOrderBody.model_validate(body if isinstance(body, dict) else {})
    m._ensure_l3_confirmation(parsed.confirmation_token)
    qctx, tctx = m.ensure_contexts(parsed.account_id, owner_id=owner_id)
    normalized_qty, lot_size = _normalize_quantity_by_lot_size(qctx, parsed.symbol, parsed.quantity)
    qty_adjusted = int(normalized_qty) != int(parsed.quantity)
    payload = parsed.model_dump(exclude_none=True)
    payload["quantity"] = int(normalized_qty)
    ok, gw = m._gateway_post_json(
        "/trade/order",
        payload,
        timeout=max(m.LONGPORT_GATEWAY_TIMEOUT_SECONDS, 12.0),
    )
    if ok and isinstance(gw, dict) and gw.get("order_id"):
        if qty_adjusted:
            gw["requested_quantity"] = int(parsed.quantity)
            gw["submitted_quantity"] = int(normalized_qty)
            gw["lot_size"] = int(lot_size)
            gw["quantity_adjusted"] = True
        return gw
    m._assert_us_order_session_allowed(parsed.symbol)
    cp = parsed.price or 0.0
    if not cp and parsed.action == "buy":
        qs = m.broker_get_quotes(qctx, [parsed.symbol])
        cp = m._get_realtime_price(qs[0])[0] if qs else 0.0
    if parsed.action == "buy" and cp > 0:
        bl = m.broker_get_account_balance(tctx)
        b = bl[0] if bl else None
        ta = float(b.net_assets) if b else 0.0
        ac = float(b.buy_power) if b else 0.0
        ev = 0.0
        for ch in m.broker_get_stock_positions(tctx).channels:
            for p in ch.positions:
                if p.symbol == parsed.symbol:
                    ev = m.trade_value(parsed.symbol, float(p.quantity), float(p.cost_price))
        rr = m.get_manager().full_check_before_order(
            symbol=parsed.symbol,
            action=parsed.action,
            quantity=int(normalized_qty),
            price=cp,
            total_assets=ta,
            available_cash=ac,
            existing_position_value=ev,
        )
        if not rr["passed"]:
            raise m.HTTPException(status_code=400, detail={"risk_blocks": rr["blocks"]})
    resp = m.broker_submit_stock_order(
        tctx,
        symbol=parsed.symbol,
        order_type="limit" if parsed.price else "market",
        side=parsed.action,
        submitted_quantity=int(normalized_qty),
        time_in_force="day",
        submitted_price=(None if not parsed.price else m.Decimal(str(parsed.price))),
    )
    out: dict[str, Any] = {"order_id": resp.order_id}
    if qty_adjusted:
        out.update(
            {
                "requested_quantity": int(parsed.quantity),
                "submitted_quantity": int(normalized_qty),
                "lot_size": int(lot_size),
                "quantity_adjusted": True,
            }
        )
    return out


def trade_cancel_order(order_id: str, account_id: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    payload: dict[str, Any] = {}
    if account_id:
        payload["account_id"] = account_id
    ok, gw = m._gateway_post_json(
        f"/trade/order/{order_id}/cancel",
        payload,
        timeout=max(m.LONGPORT_GATEWAY_TIMEOUT_SECONDS, 10.0),
    )
    if ok and isinstance(gw, dict) and bool(gw.get("ok")):
        return gw
    _, tctx = m.ensure_contexts(account_id, owner_id=owner_id)
    m.broker_cancel_order(tctx, order_id)
    return {"ok": True, "order_id": order_id, "account_id": account_id}


def trade_account(account_id: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    gw = m._gateway_get_json("/trade/account", {"account_id": account_id} if account_id else None)
    if _is_trade_gateway_success(gw, required_keys=("net_assets", "buy_power", "currency")):
        return gw

    def _load() -> dict[str, Any]:
        _, tctx = m.ensure_contexts(account_id, owner_id=owner_id)
        bl = m.broker_get_account_balance(tctx)
        if not bl:
            raise m.HTTPException(status_code=400, detail="account_balance_empty")
        b = bl[0]
        return {"net_assets": float(b.net_assets), "buy_power": float(b.buy_power), "currency": str(b.currency)}

    return _with_trade_context_retry("trade_account", account_id=account_id, owner_id=owner_id, fn=_load)


def trade_positions(account_id: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    gw = m._gateway_get_json("/trade/positions", {"account_id": account_id} if account_id else None)
    if _is_trade_gateway_success(gw) and isinstance(gw.get("positions"), list):
        return gw

    def _load() -> dict[str, Any]:
        qctx, tctx = m.ensure_contexts(account_id, owner_id=owner_id)
        pos = m.broker_get_stock_positions(tctx)
        rows: list[dict[str, Any]] = []
        for ch in pos.channels:
            for p in ch.positions:
                cur = 0.0
                price_type = "-"
                try:
                    q = m.broker_get_quotes(qctx, [p.symbol])
                    if q:
                        cur, price_type = m._get_realtime_price(q[0])
                except Exception:
                    pass
                qty = float(p.quantity)
                cost = float(p.cost_price)
                value = qty * cur
                pnl = value - qty * cost
                rows.append(
                    {
                        "symbol": p.symbol,
                        "quantity": qty,
                        "cost_price": cost,
                        "current_price": cur,
                        "pnl": round(pnl, 2),
                        "price_type": price_type,
                    }
                )
        return {"positions": rows}

    return _with_trade_context_retry("trade_positions", account_id=account_id, owner_id=owner_id, fn=_load)


def trade_orders(status: str = "all", account_id: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    query = {"status": status}
    if account_id:
        query["account_id"] = account_id
    gw = m._gateway_get_json("/trade/orders", query)
    if _is_trade_gateway_success(gw) and isinstance(gw.get("orders"), list):
        return gw

    def _load() -> dict[str, Any]:
        _, tctx = m.ensure_contexts(account_id, owner_id=owner_id)
        allowed = {"active": {"New", "PartialFilled"}, "filled": {"Filled"}, "cancelled": {"Canceled"}}.get(status)
        orders = []
        for o in m.broker_get_today_orders(tctx):
            s = str(o.status)
            if allowed and s not in allowed:
                continue
            orders.append(
                {
                    "order_id": o.order_id,
                    "symbol": o.symbol,
                    "side": str(o.side),
                    "quantity": float(o.quantity),
                    "price": float(o.price) if o.price else None,
                    "status": s,
                    "filled_quantity": _order_filled_quantity(o),
                    "avg_fill_price": _order_avg_fill_price(o),
                }
            )
        return {"orders": orders}

    return _with_trade_context_retry("trade_orders", account_id=account_id, owner_id=owner_id, fn=_load)


def auto_trader_status(owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    trader = _auto_trader_service(owner_id)
    cfg = trader.get_config()
    runtime_raw = m._auto_trader_runtime_status()
    context = _auto_trader_status_context(owner_id)
    runtime = (
        runtime_raw
        if (not context or _runtime_matches_account_context(runtime_raw, context))
        else _context_mismatch_runtime(runtime_raw, context)
    )
    out = build_auto_trader_status_response(
        status=trader.get_status(),
        runtime=runtime,
        research=get_research_status(),
        config=cfg,
    )
    out["safety"] = _stock_auto_trader_safety_status(owner_id=owner_id, config=cfg)
    return out


def auto_trader_config(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    trader = _auto_trader_service(owner_id)
    parsed = AutoTraderConfigBody.model_validate(body if isinstance(body, dict) else {})
    payload = {k: v for k, v in parsed.model_dump().items() if v is not None}
    return apply_auto_trader_config_update(
        payload=payload,
        update_config=trader.update_config,
        sync_worker=lambda cfg: m._sync_auto_trader_worker_with_config(cfg, owner_id=owner_id),
    )


def auto_trader_templates() -> dict[str, Any]:
    m = _m()
    return {"items": m.auto_trader.list_templates()}


def auto_trader_config_policy() -> dict[str, Any]:
    m = _m()
    return build_auto_trader_config_policy(locked_fields=m.AGENT_POLICY_LOCKED_FIELDS, field_rules=m.AGENT_POLICY_FIELD_RULES)


def auto_trader_config_agent_update(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    trader = _auto_trader_service(owner_id)
    parsed = AutoTraderConfigBody.model_validate(body if isinstance(body, dict) else {})
    raw_payload = {k: v for k, v in parsed.model_dump().items() if v is not None}
    return apply_agent_policy_update(
        raw_payload=raw_payload,
        current_config=trader.get_config(),
        validate_update=m._validate_agent_policy_update,
        locked_fields=m.AGENT_POLICY_LOCKED_FIELDS,
        allowed_field_rules=m.AGENT_POLICY_FIELD_RULES,
        update_config=trader.update_config,
        sync_worker=lambda cfg: m._sync_auto_trader_worker_with_config(cfg, owner_id=owner_id),
    )


def auto_trader_template_apply(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    trader = _auto_trader_service(owner_id)
    parsed = AutoTraderTemplateApplyBody.model_validate(body if isinstance(body, dict) else {})
    return apply_template_with_sync(
        template_name=parsed.name,
        apply_template=trader.apply_template,
        sync_worker=lambda cfg: m._sync_auto_trader_worker_with_config(cfg, owner_id=owner_id),
    )


def auto_trader_template_preview(
    name: Literal["trend", "mean_reversion", "defensive"],
    owner_id: str | None = None,
) -> dict[str, Any]:
    trader = _auto_trader_service(owner_id)
    return preview_template_safe(template_name=name, preview_template=trader.preview_template)


def auto_trader_export_config(owner_id: str | None = None) -> dict[str, Any]:
    trader = _auto_trader_service(owner_id)
    return {"config": redact_auto_trader_secrets_for_client(trader.get_config())}


def auto_trader_import_config(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    trader = _auto_trader_service(owner_id)
    parsed = AutoTraderImportBody.model_validate(body if isinstance(body, dict) else {})
    return import_config_with_rollback(
        config_obj=dict(parsed.config or {}),
        current_config=trader.get_config(),
        validate_import_config=lambda cfg: AutoTraderImportConfigBody.model_validate(cfg).model_dump(exclude_none=True),
        update_config=trader.update_config,
        sync_worker=lambda cfg: m._sync_auto_trader_worker_with_config(cfg, owner_id=owner_id),
    )


def auto_trader_config_backups(owner_id: str | None = None) -> dict[str, Any]:
    trader = _auto_trader_service(owner_id)
    return {"items": trader.list_config_backups()}


def auto_trader_config_rollback(body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    trader = _auto_trader_service(owner_id)
    parsed = AutoTraderRollbackBody.model_validate(body if isinstance(body, dict) else {})
    return rollback_config_with_sync(
        backup_id=parsed.backup_id,
        rollback_config=trader.rollback_config,
        sync_worker=lambda cfg: m._sync_auto_trader_worker_with_config(cfg, owner_id=owner_id),
    )


def auto_trader_config_rollback_preview(backup_id: str, owner_id: str | None = None) -> dict[str, Any]:
    trader = _auto_trader_service(owner_id)
    return preview_rollback_safe(backup_id=backup_id, preview_rollback=trader.preview_rollback)


def auto_trader_strong_stocks(
    market: Literal["us", "hk", "cn"] = "us",
    limit: int = 8,
    kline: BacktestKline = "1d",
) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_strong_stocks(market=market, limit=limit, kline=kline)


def auto_trader_strategy_score(
    symbol: str,
    days: int = 120,
    kline: BacktestKline = "1d",
) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_strategy_score(symbol=symbol, days=days, kline=kline)


def auto_trader_strategies() -> dict[str, Any]:
    m = _m()
    return m.auto_trader_strategies()


def auto_trader_pair_backtest(
    market: Literal["us", "hk", "cn"] = "us",
    days: int = 180,
    kline: BacktestKline = "1d",
    initial_capital: float = 100000.0,
) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_pair_backtest(market=market, days=days, kline=kline, initial_capital=initial_capital)


def auto_trader_scan_run(owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    trader = _auto_trader_service(owner_id)
    _assert_stock_auto_trader_safety(owner_id=owner_id, config=trader.get_config())
    return m.auto_trader_scan_run(owner_id=owner_id)


def auto_trader_signals(status: str = "all", owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_signals(status=status, owner_id=owner_id)


def auto_trader_archive_legacy_unscoped_signals(reason: str = "manual") -> dict[str, Any]:
    m = _m()
    result = archive_legacy_unscoped_signals(reason=reason)
    removed = 0
    try:
        removed = m.auto_trader.drop_signals(list(result.get("archived_signal_ids") or []))
    except Exception:
        removed = 0
    result["memory_removed_count"] = removed
    return result


def auto_trader_confirm(signal_id: str, body: dict[str, Any], owner_id: str | None = None) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderConfirmBody.model_validate(body if isinstance(body, dict) else {})
    return m.auto_trader_confirm(signal_id=signal_id, body=parsed, owner_id=owner_id)


def auto_trader_metrics_recent(limit: int = 200, event: str | None = None) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_metrics_recent(limit=limit, event=event)


def auto_trader_metrics_sla(window_minutes: int = 5, limit: int = 2000) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_metrics_sla(window_minutes=window_minutes, limit=limit)


def auto_trader_research_status() -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_status()


def auto_trader_research_snapshot() -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_snapshot()


def auto_trader_research_snapshot_history_list(history_type: str, market: Literal["us", "hk", "cn"] = "us") -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_snapshot_history_list(history_type=history_type, market=market)


def auto_trader_research_snapshot_history_get(
    history_type: str,
    snapshot_id: str,
    market: Literal["us", "hk", "cn"] = "us",
) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_snapshot_history_get(
        history_type=history_type,
        snapshot_id=snapshot_id,
        market=market,
    )


def auto_trader_research_run(body: dict[str, Any] | None = None) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderResearchRunBody.model_validate(body if isinstance(body, dict) else {}) if body is not None else None
    return m.auto_trader_research_run(body=parsed)


def auto_trader_research_task_status(task_id: str) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_task_status(task_id=task_id)


def auto_trader_research_task_cancel(task_id: str) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_task_cancel(task_id=task_id)


def auto_trader_research_model_compare(top: int = 10) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_model_compare(top=top)


def auto_trader_research_strategy_matrix_run(body: dict[str, Any] | None = None) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderStrategyMatrixRunBody.model_validate(body if isinstance(body, dict) else {}) if body is not None else None
    return m.auto_trader_research_strategy_matrix_run(body=parsed)


def auto_trader_research_strategy_matrix_result(market: str | None = None) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_strategy_matrix_result(market=market)


def auto_trader_research_ml_matrix_run(body: dict[str, Any] | None = None) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderMlMatrixRunBody.model_validate(body if isinstance(body, dict) else {}) if body is not None else None
    return m.auto_trader_research_ml_matrix_run(body=parsed)


def auto_trader_research_ml_matrix_result(market: str | None = None) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_ml_matrix_result(market=market)


def auto_trader_research_ml_matrix_apply_to_config(body: dict[str, Any] | None = None) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderMlMatrixApplyBody.model_validate(body if isinstance(body, dict) else {}) if body is not None else None
    return m.auto_trader_research_ml_matrix_apply_to_config(body=parsed)


def auto_trader_research_ab_report() -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_ab_report()


def auto_trader_research_ab_report_markdown() -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_ab_report_markdown()
