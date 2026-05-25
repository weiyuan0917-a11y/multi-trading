import atexit
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

from api.auto_trader import AutoTraderService, auto_trader_config_path_for_owner, make_feishu_sender
from api.schemas_backtest import BacktestKline
from api.longport_history_gate import (
    PRIORITY_HIGH,
    acquire_history_slot,
    longport_history_priority,
    release_history_slot,
)
from api.auto_trader_research import run_research_snapshot
from api.brokers import service_layer as broker_service
from config.live_settings import live_settings
from longbridge.openapi import (
    AdjustType,
    Config,
    OrderSide,
    OrderType,
    Period,
    QuoteContext,
    TimeInForceType,
    TradeContext,
    TradeSessions,
)
from mcp_server.backtest_engine import Bar, coerce_bar_datetime
from mcp_server.risk_manager import get_manager, trade_value
from api.perf_metrics import emit_metric

PID_FILE = os.path.join(ROOT, ".auto_trader_worker.pid")
RUNTIME_FILE = os.path.join(ROOT, ".auto_trader_worker.runtime.json")
SCAN_TRIGGER_FILE = os.path.join(ROOT, ".auto_trader_worker.trigger_scan")
CONFIRM_QUEUE_FILE = os.path.join(ROOT, ".auto_trader_worker.confirm_signals.json")
quote_ctx: Optional[QuoteContext] = None
trade_ctx: Optional[TradeContext] = None
_ctx_lock = threading.RLock()
_stop_event = threading.Event()
_ET = ZoneInfo("America/New_York")
_QUOTE_TS_SOURCE_TZ = ZoneInfo(os.getenv("QUOTE_TS_SOURCE_TZ", "Asia/Shanghai"))
_LAST_RUNTIME_WRITE_TS = 0.0
_LAST_RUNTIME_DIGEST: Optional[str] = None
_QUOTE_LAST_GOOD_CACHE: dict[str, dict[str, Any]] = {}
_QUOTE_FAILURE_STATE: dict[str, dict[str, Any]] = {}
_QUOTE_RETRY_TIMES = max(1, int(os.getenv("AUTO_TRADER_QUOTE_RETRY_TIMES", "3")))
_QUOTE_RETRY_BACKOFF_MS = max(20, int(os.getenv("AUTO_TRADER_QUOTE_RETRY_BACKOFF_MS", "120")))
_QUOTE_BREAKER_THRESHOLD = max(1, int(os.getenv("AUTO_TRADER_QUOTE_BREAKER_THRESHOLD", "5")))
_QUOTE_BREAKER_COOLDOWN_SECONDS = max(5, int(os.getenv("AUTO_TRADER_QUOTE_BREAKER_COOLDOWN_SECONDS", "20")))


def _bootstrap_auto_trader_worker_env() -> None:
    """与 launcher/Supervisor 一致：合并 data/user_env/davies，避免 Worker 仅用代码默认 :8000 拉 K 失败。"""
    try:
        from pathlib import Path

        from config.user_env_store import combined_env_for_cli

        for k, v in combined_env_for_cli(Path(ROOT)).items():
            os.environ[k] = str(v)
    except Exception:
        pass


def _api_base_url() -> str:
    return str(os.getenv("AUTO_TRADER_API_BASE_URL", "http://127.0.0.1:8010")).strip().rstrip("/")


def _use_api_proxy() -> bool:
    return str(os.getenv("AUTO_TRADER_WORKER_USE_API_PROXY", "true")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _api_proxy_timeout_seconds() -> float:
    return max(1.0, float(os.getenv("AUTO_TRADER_API_PROXY_TIMEOUT_SECONDS", "8")))


def _api_path_for_trade_auth(url_or_path: str) -> str:
    s = str(url_or_path or "")
    if "://" in s:
        try:
            return urllib.parse.urlparse(s).path or ""
        except Exception:
            return ""
    return s.split("?", 1)[0]


def _read_trade_proxy_credentials_from_config(path: str) -> tuple[str, str]:
    if not path:
        return "", ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return "", ""
        k = str(data.get("api_key") or "").strip()
        b = str(data.get("api_bearer_token") or "").strip()
        return k, b
    except Exception:
        return "", ""


def _api_key_owner_matches(raw_key: str, owner_id: str) -> bool:
    key = str(raw_key or "").strip()
    owner = str(owner_id or "").strip().lower()
    if not key or not owner:
        return False
    try:
        from api.services.user_auth_service import get_user_auth_service

        return str(get_user_auth_service().verify_api_key(key) or "").strip().lower() == owner
    except Exception:
        return False


def _maybe_migrate_trade_proxy_key_to_owner_config(raw_key: str, owner_id: str) -> None:
    key = str(raw_key or "").strip()
    owner = str(owner_id or "").strip().lower()
    if not key or not owner:
        return
    path = str(os.getenv("AUTO_TRADER_CONFIG_PATH") or "").strip() or auto_trader_config_path_for_owner(owner, root=ROOT)
    legacy_path = os.path.join(ROOT, "api", "auto_trader_config.json")
    if os.path.abspath(path) == os.path.abspath(legacy_path):
        return
    try:
        data: dict[str, Any] = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                data = raw
        if str(data.get("api_key") or "").strip() or str(data.get("api_bearer_token") or "").strip():
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data["api_key"] = key
        data["api_key_migrated_from_legacy_config_at"] = datetime.now(timezone.utc).isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
    except Exception:
        return


def _trade_proxy_from_auto_trader_config_file() -> tuple[str, str]:
    owner = str(
        globals().get("_API_LOCAL_OWNER")
        or os.getenv("AUTO_TRADER_OWNER_ID")
        or os.getenv("X_MT_LOCAL_OWNER")
        or ""
    ).strip().lower()
    path = str(os.getenv("AUTO_TRADER_CONFIG_PATH") or "").strip() or auto_trader_config_path_for_owner(
        owner,
        root=ROOT,
    )
    k, b = _read_trade_proxy_credentials_from_config(path)
    if k or b:
        return k, b
    legacy_path = os.path.join(ROOT, "api", "auto_trader_config.json")
    legacy_key, _legacy_bearer = _read_trade_proxy_credentials_from_config(legacy_path)
    if legacy_key and _api_key_owner_matches(legacy_key, owner):
        _maybe_migrate_trade_proxy_key_to_owner_config(legacy_key, owner)
        return legacy_key, ""
    return "", ""


def _api_trade_proxy_credentials() -> tuple[str, str]:
    """(x_api_key, bearer_session)；优先环境变量，其次 api/auto_trader_config.json（与 Setup 一键写入一致）。"""
    k = str(os.getenv("AUTO_TRADER_API_KEY") or "").strip()
    b = str(os.getenv("AUTO_TRADER_API_BEARER_TOKEN") or "").strip()
    owner = str(globals().get("_API_LOCAL_OWNER") or "").strip().lower()
    if k and owner and not _api_key_owner_matches(k, owner):
        k = ""
    if k or b:
        return k, b
    return _trade_proxy_from_auto_trader_config_file()


_API_LOCAL_OWNER = str(
    os.getenv("AUTO_TRADER_OWNER_ID") or os.getenv("X_MT_LOCAL_OWNER") or ""
).strip().lower()
_API_ACCOUNT_ID = str(os.getenv("AUTO_TRADER_ACCOUNT_ID") or "").strip()
_API_BROKER_PROVIDER = str(os.getenv("AUTO_TRADER_BROKER_PROVIDER") or "").strip().lower()


def _account_query_path(path: str) -> str:
    if not _API_ACCOUNT_ID:
        return path
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}{urllib.parse.urlencode({'account_id': _API_ACCOUNT_ID})}"


def _api_apply_trade_proxy_auth(req: urllib.request.Request, path_or_url: str) -> None:
    """对内部行情与 /trade/* 统一透传 owner；/trade/* 额外附加鉴权。"""
    p = _api_path_for_trade_auth(path_or_url)
    if _API_LOCAL_OWNER and (
        p.startswith("/trade/")
        or p.startswith("/internal/longport/")
        or p.startswith("/auto-trader/")
    ):
        req.add_header("X-MT-Local-Owner", _API_LOCAL_OWNER)
    if not p.startswith("/trade/"):
        return
    ak, bt = _api_trade_proxy_credentials()
    if ak:
        req.add_header("X-Api-Key", ak)
    elif bt:
        req.add_header("Authorization", f"Bearer {bt}")


def _direct_fallback_enabled() -> bool:
    return str(os.getenv("LONGPORT_DIRECT_FALLBACK", "0")).strip().lower() in {"1", "true", "yes", "on"}


_bootstrap_auto_trader_worker_env()


def _allow_direct_longport() -> bool:
    # API代理模式默认禁止直连，避免额外占用连接；仅在故障开关开启时允许回退直连。
    return (not _use_api_proxy()) or _direct_fallback_enabled()


def _api_get_json(path: str, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
    url = f"{_api_base_url()}{path}"
    try:
        req = urllib.request.Request(url)
        _api_apply_trade_proxy_auth(req, path)
        with urllib.request.urlopen(req, timeout=float(timeout or _api_proxy_timeout_seconds())) as resp:
            if int(getattr(resp, "status", 200) or 200) != 200:
                return None
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _api_post_json(path: str, payload: dict[str, Any], timeout: Optional[float] = None) -> tuple[bool, dict[str, Any]]:
    url = f"{_api_base_url()}{path}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    _api_apply_trade_proxy_auth(req, path)
    try:
        with urllib.request.urlopen(req, timeout=float(timeout or _api_proxy_timeout_seconds())) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw) if raw else {}
            return True, data if isinstance(data, dict) else {}
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", errors="ignore")
            data = json.loads(raw) if raw else {}
            if isinstance(data, dict):
                return False, data
        except Exception:
            pass
        return False, {"error": f"http_{int(getattr(e, 'code', 500) or 500)}"}
    except Exception as e:
        return False, {"error": str(e)}


def _write_pid_file() -> None:
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _remove_pid_file() -> None:
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def _write_runtime(data: dict[str, Any], force: bool = False) -> None:
    global _LAST_RUNTIME_WRITE_TS, _LAST_RUNTIME_DIGEST
    try:
        digest = json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)
        now = time.monotonic()
        if not force and digest == _LAST_RUNTIME_DIGEST and (now - _LAST_RUNTIME_WRITE_TS) < 5.0:
            return
        with open(RUNTIME_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
        _LAST_RUNTIME_DIGEST = digest
        _LAST_RUNTIME_WRITE_TS = now
    except Exception:
        pass


def _write_startup_guard_runtime(reason: str) -> None:
    now = datetime.now().isoformat()
    _write_runtime(
        {
            "pid": os.getpid(),
            "started_at": now,
            "updated_at": now,
            "worker_running": False,
            "startup_rejected": True,
            "startup_rejected_reason": reason,
            "owner_id": _API_LOCAL_OWNER or None,
            "account_id": _API_ACCOUNT_ID or None,
            "broker_provider": _API_BROKER_PROVIDER or None,
            "status": {
                "enabled": False,
                "running": False,
                "startup_rejected": True,
                "startup_rejected_reason": reason,
                "owner_id": _API_LOCAL_OWNER or None,
                "account_id": _API_ACCOUNT_ID or None,
                "broker_provider": _API_BROKER_PROVIDER or None,
            },
        },
        force=True,
    )


def _assert_explicit_account_context() -> None:
    missing = []
    if not _API_LOCAL_OWNER:
        missing.append("AUTO_TRADER_OWNER_ID")
    if not _API_ACCOUNT_ID:
        missing.append("AUTO_TRADER_ACCOUNT_ID")
    if not _API_BROKER_PROVIDER:
        missing.append("AUTO_TRADER_BROKER_PROVIDER")
    if not missing:
        return
    reason = "missing_explicit_account_context:" + ",".join(missing)
    _write_startup_guard_runtime(reason)
    raise SystemExit(reason)


def _iso_min(*values: object) -> Optional[str]:
    candidates = [str(v) for v in values if v]
    return min(candidates) if candidates else None


def _iso_max(*values: object) -> Optional[str]:
    candidates = [str(v) for v in values if v]
    return max(candidates) if candidates else None


def _normalized_scheduler_state(data: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    scheduler = status.get("scheduler")
    if not isinstance(scheduler, dict):
        scheduler = data.get("scheduler")
    if not isinstance(scheduler, dict):
        scheduler = {}
    return {
        "scan_in_progress": bool(scheduler.get("scan_in_progress")),
        "scan_started_at": scheduler.get("scan_started_at"),
        "scan_finished_at": scheduler.get("scan_finished_at"),
        "last_error": scheduler.get("last_error"),
    }


def _with_consistent_scan_state(
    data: dict[str, Any],
    *,
    manual_scan_in_progress: bool,
    manual_scan_started_at: Optional[str],
    manual_scan_finished_at: Optional[str],
    last_manual_scan_error: Optional[str],
) -> dict[str, Any]:
    """Keep runtime scan lifecycle fields in sync across legacy and nested keys."""
    out = dict(data)
    raw_status = out.get("status")
    status = dict(raw_status) if isinstance(raw_status, dict) else {}
    scheduler = _normalized_scheduler_state(out, status)
    status["scheduler"] = scheduler

    manual_scan = {
        "scan_in_progress": bool(manual_scan_in_progress),
        "scan_started_at": manual_scan_started_at,
        "scan_finished_at": None if manual_scan_in_progress else manual_scan_finished_at,
        "last_error": last_manual_scan_error,
    }

    out["last_manual_scan_error"] = last_manual_scan_error
    scheduler_active = bool(scheduler.get("scan_in_progress"))
    manual_active = bool(manual_scan["scan_in_progress"])
    if manual_active and scheduler_active:
        scan_source: Optional[str] = "manual+scheduler"
        scan_started_at = _iso_min(manual_scan_started_at, scheduler.get("scan_started_at"))
    elif manual_active:
        scan_source = "manual"
        scan_started_at = manual_scan_started_at
    elif scheduler_active:
        scan_source = "scheduler"
        scan_started_at = scheduler.get("scan_started_at")
    else:
        scan_source = None
        scan_started_at = None

    out["status"] = status
    out["scheduler"] = scheduler
    out["manual_scan"] = manual_scan
    # Legacy top-level scan fields now mean "any worker scan is active".
    out["scan_in_progress"] = bool(manual_active or scheduler_active)
    out["scan_started_at"] = scan_started_at
    out["scan_finished_at"] = (
        None
        if out["scan_in_progress"]
        else _iso_max(manual_scan_finished_at, scheduler.get("scan_finished_at"))
    )
    out["scan_source"] = scan_source
    return out


def _write_worker_runtime(
    data: dict[str, Any],
    *,
    force: bool = False,
    manual_scan_in_progress: bool,
    manual_scan_started_at: Optional[str],
    manual_scan_finished_at: Optional[str],
    last_manual_scan_error: Optional[str],
) -> None:
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    restored_meta = status.get("restored_open_positions_meta") if isinstance(status.get("restored_open_positions_meta"), dict) else {}
    _write_runtime(
        _with_consistent_scan_state(
            {
                **data,
                "owner_id": _API_LOCAL_OWNER or None,
                "account_id": _API_ACCOUNT_ID or None,
                "broker_provider": _API_BROKER_PROVIDER or None,
                "restored_open_positions": status.get("restored_open_positions"),
                "restored_open_positions_meta": restored_meta,
                "open_state_snapshot_path": status.get("open_state_snapshot_path"),
            },
            manual_scan_in_progress=manual_scan_in_progress,
            manual_scan_started_at=manual_scan_started_at,
            manual_scan_finished_at=manual_scan_finished_at,
            last_manual_scan_error=last_manual_scan_error,
        ),
        force=force,
    )


def _remove_runtime_file() -> None:
    try:
        if os.path.exists(RUNTIME_FILE):
            os.remove(RUNTIME_FILE)
    except Exception:
        pass


def _consume_manual_scan_trigger() -> bool:
    try:
        if not os.path.exists(SCAN_TRIGGER_FILE):
            return False
        os.remove(SCAN_TRIGGER_FILE)
        return True
    except Exception:
        return False


def _consume_confirm_queue() -> list[dict[str, str]]:
    """
    消费 API 侧投递的确认信号队列（文件队列）。
    队列文件包含 signal_ids 列表。
    """
    try:
        if not os.path.exists(CONFIRM_QUEUE_FILE):
            return []
        raw = open(CONFIRM_QUEUE_FILE, "r", encoding="utf-8").read()
        try:
            os.remove(CONFIRM_QUEUE_FILE)
        except Exception:
            pass
        if not raw.strip():
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            sigs = data.get("confirmations") or data.get("signal_ids") or data.get("signals") or []
        else:
            sigs = data
        if not isinstance(sigs, list):
            return []
        out: list[dict[str, str]] = []
        for x in sigs:
            if isinstance(x, dict):
                sid = str(x.get("signal_id") or x.get("id") or "").strip()
                token = str(x.get("confirmation_token") or "").strip()
                owner = str(x.get("owner_id") or "").strip().lower()
                account_id = str(x.get("account_id") or "").strip()
                broker_provider = str(x.get("broker_provider") or "").strip().lower()
            else:
                sid = str(x or "").strip()
                token = ""
                owner = ""
                account_id = ""
                broker_provider = ""
            if sid:
                if _API_LOCAL_OWNER and owner != _API_LOCAL_OWNER:
                    continue
                if _API_ACCOUNT_ID and account_id != _API_ACCOUNT_ID:
                    continue
                if _API_BROKER_PROVIDER and broker_provider != _API_BROKER_PROVIDER:
                    continue
                out.append(
                    {
                        "signal_id": sid,
                        "confirmation_token": token,
                        "owner_id": owner,
                        "account_id": account_id,
                        "broker_provider": broker_provider,
                    }
                )
        return out
    except Exception:
        return []


def _close_context(ctx: Any) -> None:
    if ctx is None:
        return
    close_fn = getattr(ctx, "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except Exception:
            pass


def reset_contexts() -> None:
    global quote_ctx, trade_ctx
    with _ctx_lock:
        broker_service.unbind_contexts(quote_ctx, trade_ctx)
        _close_context(quote_ctx)
        _close_context(trade_ctx)
        quote_ctx = None
        trade_ctx = None


def create_contexts() -> tuple[QuoteContext, TradeContext]:
    live_settings.assert_longport_configured()
    cfg = Config.from_apikey(
        live_settings.LONGPORT_APP_KEY,
        live_settings.LONGPORT_APP_SECRET,
        live_settings.LONGPORT_ACCESS_TOKEN,
        enable_overnight=True,
        enable_print_quote_packages=False,
    )
    return QuoteContext(cfg), TradeContext(cfg)


def ensure_contexts(account_id: str | None = None, owner_id: str | None = None) -> tuple[QuoteContext, TradeContext]:
    global quote_ctx, trade_ctx
    aid = str(account_id or "").strip()
    owner = str(owner_id or "").strip().lower()
    if aid or owner:
        try:
            from api import main as api_main

            return api_main.ensure_contexts(aid or None, owner_id=owner or None)
        except Exception:
            if aid or owner:
                raise
    with _ctx_lock:
        if quote_ctx is None or trade_ctx is None:
            quote_ctx, trade_ctx = create_contexts()
            broker_service.bind_contexts_to_broker(quote_ctx, trade_ctx, "longbridge")
        return quote_ctx, trade_ctx


def _resolve_period(kline: BacktestKline):
    candidates = {
        "1m": ["Min_1", "Min1", "OneMin"],
        "5m": ["Min_5", "Min5", "FiveMin"],
        "10m": ["Min_10", "Min10", "TenMin"],
        "30m": ["Min_30", "Min30", "ThirtyMin"],
        "1h": ["Min_60", "Hour", "H1", "Min60"],
        "2h": ["Min_120", "Min120", "TwoHour"],
        "4h": ["Min_240", "Min240", "FourHour"],
        "1d": ["Day", "D1"],
    }[kline]
    for name in candidates:
        p = getattr(Period, name, None)
        if p is not None:
            return p
    raise RuntimeError(f"unsupported kline: {kline}")


def _as_et_datetime(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    dt: Optional[datetime] = None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except Exception:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_QUOTE_TS_SOURCE_TZ)
    return dt.astimezone(_ET)


def _extract_quote_timestamp(quote_obj: Any) -> Optional[datetime]:
    for attr in ("timestamp", "trade_timestamp", "updated_at", "time"):
        if hasattr(quote_obj, attr):
            ts = _as_et_datetime(getattr(quote_obj, attr))
            if ts is not None:
                return ts
    return None


def _session_kind_et(now_et: datetime) -> str:
    t = now_et.timetz().replace(tzinfo=None)
    if dt_time(4, 0) <= t < dt_time(9, 30):
        return "盘前"
    if dt_time(9, 30) <= t < dt_time(16, 0):
        return "盘中"
    if dt_time(16, 0) <= t < dt_time(20, 0):
        return "盘后"
    return "夜盘"


def _is_fresh_for_session(kind: str, quote_ts_et: Optional[datetime], now_et: datetime) -> bool:
    if quote_ts_et is None:
        return False
    today = now_et.date()
    t = quote_ts_et.timetz().replace(tzinfo=None)
    if kind == "盘前":
        return quote_ts_et.date() == today and dt_time(4, 0) <= t < dt_time(9, 30)
    if kind == "盘中":
        return quote_ts_et.date() == today and dt_time(9, 30) <= t < dt_time(16, 0)
    if kind == "盘后":
        return quote_ts_et.date() == today and dt_time(16, 0) <= t < dt_time(20, 0)
    if kind == "夜盘":
        now_t = now_et.timetz().replace(tzinfo=None)
        if now_t < dt_time(4, 0):
            start = datetime.combine(today - timedelta(days=1), dt_time(20, 0), tzinfo=_ET)
            end = datetime.combine(today, dt_time(4, 0), tzinfo=_ET)
        else:
            start = datetime.combine(today, dt_time(20, 0), tzinfo=_ET)
            end = datetime.combine(today + timedelta(days=1), dt_time(4, 0), tzinfo=_ET)
        return start <= quote_ts_et < end
    return False


def _get_realtime_price(q: Any) -> tuple[float, str]:
    now_et = datetime.now(timezone.utc).astimezone(_ET)
    session = _session_kind_et(now_et)
    candidates: dict[str, Any] = {
        "盘前": getattr(q, "pre_market_quote", None),
        "盘后": getattr(q, "post_market_quote", None),
        "夜盘": getattr(q, "overnight_quote", None),
        "盘中": q,
    }
    preferred_order = {
        "盘前": ["盘前", "盘中", "夜盘", "盘后"],
        "盘中": ["盘中", "盘前", "盘后", "夜盘"],
        "盘后": ["盘后", "盘中", "夜盘", "盘前"],
        "夜盘": ["夜盘", "盘后", "盘中", "盘前"],
    }[session]
    for kind in preferred_order:
        obj = candidates.get(kind)
        if not obj or not getattr(obj, "last_done", None):
            continue
        if kind == "盘中":
            return float(obj.last_done), kind
        ts = _extract_quote_timestamp(obj)
        if _is_fresh_for_session(kind, ts, now_et):
            return float(obj.last_done), kind
    for kind in preferred_order:
        obj = candidates.get(kind)
        if obj and getattr(obj, "last_done", None):
            return float(obj.last_done), kind
    return float(getattr(q, "last_done", 0.0) or 0.0), "盘中"


def _fetch_bars(symbol: str, days: int, kline: BacktestKline = "1d") -> list[Bar]:
    if _use_api_proxy():
        sym = str(symbol or "").strip().upper()
        q = urllib.parse.urlencode(
            {"symbol": sym, "days": int(days), "kline": str(kline), "priority": "high"}
        )
        data = _api_get_json(f"/internal/longport/history-bars?{q}", timeout=max(_api_proxy_timeout_seconds(), 20.0))
        items = data.get("items") if isinstance(data, dict) else None
        if isinstance(items, list) and items:
            out: list[Bar] = []
            for x in items:
                if not isinstance(x, dict):
                    continue
                try:
                    out.append(
                        Bar(
                            date=coerce_bar_datetime(x.get("date")),
                            open=float(x.get("open", 0.0) or 0.0),
                            high=float(x.get("high", 0.0) or 0.0),
                            low=float(x.get("low", 0.0) or 0.0),
                            close=float(x.get("close", 0.0) or 0.0),
                            volume=float(x.get("volume", 0.0) or 0.0),
                        )
                    )
                except Exception:
                    continue
            if out:
                return out
        if not _allow_direct_longport():
            return []
    with longport_history_priority(PRIORITY_HIGH):
        if not acquire_history_slot(timeout=25.0):
            return []
        try:
            qctx, _ = ensure_contexts(_API_ACCOUNT_ID or None, owner_id=_API_LOCAL_OWNER or None)
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            period = _resolve_period(kline)
            candles = broker_service.get_history_candlesticks_by_date(
                qctx,
                symbol=symbol,
                period=period,
                adjust_type=AdjustType.ForwardAdjust,
                start=start_date,
                end=end_date,
                trade_sessions=TradeSessions.All,
            )
        finally:
            release_history_slot()
    return [
        Bar(
            date=coerce_bar_datetime(c.timestamp),
            open=float(c.open),
            high=float(c.high),
            low=float(c.low),
            close=float(c.close),
            volume=float(c.volume),
        )
        for c in candles
    ]


def _quote_last(symbol: str) -> Optional[dict[str, float]]:
    if _use_api_proxy():
        sym = str(symbol or "").strip().upper()
        if not sym:
            return None
        q = urllib.parse.urlencode({"symbol": sym})
        data = _api_get_json(f"/internal/longport/quote?{q}", timeout=max(_api_proxy_timeout_seconds(), 8.0))
        if isinstance(data, dict) and bool(data.get("available")):
            return {
                "last": float(data.get("last", 0.0) or 0.0),
                "change_pct": float(data.get("change_pct", 0.0) or 0.0),
                "price_type": str(data.get("price_type", "")),
                "prev_close": float(data.get("prev_close", 0.0) or 0.0),
            }
        if not _allow_direct_longport():
            return None
    qctx, _ = ensure_contexts(_API_ACCOUNT_ID or None, owner_id=_API_LOCAL_OWNER or None)
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    start = time.perf_counter()
    now = time.time()
    state = _QUOTE_FAILURE_STATE.get(sym, {})
    open_until = float(state.get("open_until", 0.0) or 0.0)
    if now < open_until:
        cached = _QUOTE_LAST_GOOD_CACHE.get(sym)
        if cached:
            emit_metric(
                event="worker.quote_last",
                ok=True,
                elapsed_ms=(time.perf_counter() - start) * 1000.0,
                tags={"symbol": sym, "source": "circuit_breaker_cache"},
            )
            return dict(cached)
    last_err: Optional[str] = None
    for i in range(_QUOTE_RETRY_TIMES):
        try:
            qs = broker_service.get_quotes(qctx, [sym])
            if not qs:
                raise RuntimeError("quote_empty")
            q = qs[0]
            last, price_type = _get_realtime_price(q)
            prev = float(getattr(q, "prev_close", 0.0) or 0.0)
            chg = ((last - prev) / prev * 100) if prev else 0.0
            out = {"last": last, "change_pct": round(chg, 2), "price_type": price_type, "prev_close": prev}
            _QUOTE_LAST_GOOD_CACHE[sym] = out
            _QUOTE_FAILURE_STATE[sym] = {"fails": 0, "open_until": 0.0}
            emit_metric(
                event="worker.quote_last",
                ok=True,
                elapsed_ms=(time.perf_counter() - start) * 1000.0,
                tags={"symbol": sym, "source": "live", "attempt": i + 1},
            )
            return out
        except Exception as e:
            last_err = str(e)
            if i < _QUOTE_RETRY_TIMES - 1:
                time.sleep((_QUOTE_RETRY_BACKOFF_MS * (i + 1)) / 1000.0)
    fail_state = _QUOTE_FAILURE_STATE.get(sym, {"fails": 0, "open_until": 0.0})
    fails = int(fail_state.get("fails", 0) or 0) + 1
    open_ts = 0.0
    if fails >= _QUOTE_BREAKER_THRESHOLD:
        open_ts = now + _QUOTE_BREAKER_COOLDOWN_SECONDS
    _QUOTE_FAILURE_STATE[sym] = {"fails": fails, "open_until": open_ts}
    cached = _QUOTE_LAST_GOOD_CACHE.get(sym)
    emit_metric(
        event="worker.quote_last",
        ok=bool(cached),
        elapsed_ms=(time.perf_counter() - start) * 1000.0,
        tags={"symbol": sym, "source": "cache" if cached else "none"},
        extra={"error": last_err, "fails": fails, "breaker_open_until": open_ts},
    )
    if cached:
        return dict(cached)
    return None


def _get_positions() -> dict[str, Any]:
    if _use_api_proxy():
        data = _api_get_json(_account_query_path("/trade/positions"), timeout=max(_api_proxy_timeout_seconds(), 8.0))
        if isinstance(data, dict) and isinstance(data.get("positions"), list):
            return {
                **data,
                "available": True,
                "source": data.get("source") or "api_proxy",
                "account_id": _API_ACCOUNT_ID or data.get("account_id"),
                "broker_provider": _API_BROKER_PROVIDER or data.get("broker_provider"),
            }
        if not _allow_direct_longport():
            return {
                "positions": [],
                "available": False,
                "error": "proxy_positions_unavailable",
                "source": "api_proxy",
                "account_id": _API_ACCOUNT_ID or None,
                "broker_provider": _API_BROKER_PROVIDER or None,
            }
    try:
        qctx, tctx = ensure_contexts(_API_ACCOUNT_ID or None, owner_id=_API_LOCAL_OWNER or None)
        pos = broker_service.get_stock_positions(tctx)
    except Exception as e:
        return {
            "positions": [],
            "available": False,
            "error": str(e),
            "source": "longport_direct",
            "account_id": _API_ACCOUNT_ID or None,
            "broker_provider": _API_BROKER_PROVIDER or None,
        }
    rows: list[dict[str, Any]] = []
    for ch in pos.channels:
        for p in ch.positions:
            cur = 0.0
            try:
                q = broker_service.get_quotes(qctx, [p.symbol])
                if q:
                    cur, _ = _get_realtime_price(q[0])
            except Exception:
                pass
            rows.append(
                {
                    "symbol": p.symbol,
                    "quantity": float(p.quantity),
                    "cost_price": float(p.cost_price),
                    "current_price": cur,
                }
            )
    return {"positions": rows, "available": True, "source": "longport_direct", "account_id": _API_ACCOUNT_ID or None, "broker_provider": _API_BROKER_PROVIDER or None}


def _get_account() -> dict[str, Any]:
    if _use_api_proxy():
        data = _api_get_json(_account_query_path("/trade/account"), timeout=max(_api_proxy_timeout_seconds(), 8.0))
        if isinstance(data, dict):
            bp = float(data.get("buy_power", 0.0) or 0.0)
            na = float(data.get("net_assets", 0.0) or 0.0)
            return {
                "net_assets": na,
                "total_assets": na,
                "buy_power": bp,
                "cash": bp,
                "currency": str(data.get("currency", "") or ""),
                "account_id": _API_ACCOUNT_ID or data.get("account_id"),
                "broker_provider": _API_BROKER_PROVIDER or data.get("broker_provider"),
            }
        if not _allow_direct_longport():
            return {
                "net_assets": 0.0,
                "total_assets": 0.0,
                "buy_power": 0.0,
                "cash": 0.0,
                "currency": "",
                "account_id": _API_ACCOUNT_ID or None,
                "broker_provider": _API_BROKER_PROVIDER or None,
            }
    _, tctx = ensure_contexts(_API_ACCOUNT_ID or None, owner_id=_API_LOCAL_OWNER or None)
    bl = broker_service.get_account_balance(tctx)
    if not bl:
        return {"net_assets": 0.0, "buy_power": 0.0, "currency": ""}
    b = bl[0]
    return {"net_assets": float(b.net_assets), "buy_power": float(b.buy_power), "currency": str(b.currency)}


def _execute_trade(
    action: str,
    symbol: str,
    quantity: int,
    price: float,
    confirmation_token: str | None = None,
) -> dict[str, Any]:
    if _use_api_proxy():
        payload: dict[str, Any] = {
            "action": str(action).lower(),
            "symbol": str(symbol).strip().upper(),
            "quantity": int(quantity),
        }
        if _API_ACCOUNT_ID:
            payload["account_id"] = _API_ACCOUNT_ID
        if float(price or 0.0) > 0:
            payload["price"] = float(price)
        token = str(confirmation_token or "").strip() or str(os.getenv("OPENCLAW_MCP_L3_CONFIRMATION_TOKEN", "")).strip()
        if token:
            payload["confirmation_token"] = token
        ok, data = _api_post_json("/trade/order", payload, timeout=max(_api_proxy_timeout_seconds(), 12.0))
        if ok:
            return {"success": True, "order_id": str(data.get("order_id", ""))}
        detail = data.get("detail") if isinstance(data, dict) else None
        if not _allow_direct_longport():
            return {"success": False, "error": str(detail or data.get("error") or "proxy_trade_submit_failed")}
    try:
        qctx, tctx = ensure_contexts(_API_ACCOUNT_ID or None, owner_id=_API_LOCAL_OWNER or None)
        cp = float(price or 0.0)
        if cp <= 0 and str(action).lower() == "buy":
            qs = broker_service.get_quotes(qctx, [symbol])
            if qs:
                cp, _ = _get_realtime_price(qs[0])

        if str(action).lower() == "buy" and cp > 0:
            bl = broker_service.get_account_balance(tctx)
            b = bl[0] if bl else None
            total_assets = float(b.net_assets) if b else 0.0
            available_cash = float(b.buy_power) if b else 0.0
            existing_value = 0.0
            for ch in broker_service.get_stock_positions(tctx).channels:
                for p in ch.positions:
                    if p.symbol == symbol:
                        existing_value = trade_value(symbol, float(p.quantity), float(p.cost_price))
            rr = get_manager().full_check_before_order(
                symbol=symbol,
                action=action,
                quantity=int(quantity),
                price=float(cp),
                total_assets=total_assets,
                available_cash=available_cash,
                existing_position_value=existing_value,
            )
            if not rr.get("passed"):
                return {"success": False, "error": f"risk_blocked: {rr.get('blocks', [])}"}

        side = OrderSide.Buy if str(action).lower() != "sell" else OrderSide.Sell
        resp = broker_service.submit_order(
            tctx,
            symbol=symbol,
            order_type=OrderType.LO if cp > 0 else OrderType.MO,
            side=side,
            submitted_quantity=int(quantity),
            time_in_force=TimeInForceType.Day,
            submitted_price=(None if cp <= 0 else Decimal(str(cp))),
        )
        return {"success": True, "order_id": str(resp.order_id)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _handle_signal(_signum: int, _frame: Any) -> None:
    _stop_event.set()


def main() -> None:
    _assert_explicit_account_context()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    _bootstrap_auto_trader_worker_env()
    if _use_api_proxy():
        _ak, _bt = _api_trade_proxy_credentials()
        if not _ak and not _bt:
            print(
                "[auto_trader_worker] 警告：AUTO_TRADER_WORKER_USE_API_PROXY=true，"
                "但未设置 AUTO_TRADER_API_KEY / AUTO_TRADER_API_BEARER_TOKEN（环境变量），"
                "且 api/auto_trader_config.json 中亦无 api_key / api_bearer_token；"
                "HTTP 调用 /trade/* 将返回 401。可在 Setup「个人 API Key」一键写入股票自动交易配置。",
                file=sys.stderr,
            )
    _write_pid_file()
    atexit.register(_remove_pid_file)
    atexit.register(reset_contexts)
    atexit.register(_remove_runtime_file)

    trader = AutoTraderService(
        fetch_bars=lambda symbol, days, kline: _fetch_bars(symbol, days, kline),  # type: ignore[arg-type]
        quote_last=_quote_last,
        send_feishu=make_feishu_sender(os.path.join(MCP_DIR, "notification_config.json")),
        execute_trade=_execute_trade,
        get_positions=_get_positions,
        get_account=_get_account,
        config_path=str(os.getenv("AUTO_TRADER_CONFIG_PATH") or "").strip()
        or auto_trader_config_path_for_owner(_API_LOCAL_OWNER, root=ROOT),
    )

    cfg = trader.get_config()
    if not cfg.get("enabled"):
        trader.update_config({"enabled": True})
    trader.start_scheduler()
    last_manual_scan_error: Optional[str] = None
    last_research_version: Optional[str] = None
    scan_in_progress = False
    scan_started_at: Optional[str] = None
    scan_finished_at: Optional[str] = None
    last_manual_scan_at: Optional[str] = None
    _st0 = trader.get_status(for_runtime_export=True)
    _write_worker_runtime(
        {
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(),
            "worker_running": True,
            "status": _st0,
            "scheduler": _st0.get("scheduler"),
            "last_scan_summary": None,
            "research_allocation_last": None,
        },
        force=True,
        manual_scan_in_progress=scan_in_progress,
        manual_scan_started_at=scan_started_at,
        manual_scan_finished_at=scan_finished_at,
        last_manual_scan_error=last_manual_scan_error,
    )

    while not _stop_event.is_set():
        if _consume_manual_scan_trigger():
            try:
                scan_in_progress = True
                scan_started_at = datetime.now().isoformat()
                scan_finished_at = None
                last_manual_scan_error = None
                _st_m = trader.get_status(for_runtime_export=True)
                _write_worker_runtime(
                    {
                        "pid": os.getpid(),
                        "updated_at": datetime.now().isoformat(),
                        "worker_running": bool(_st_m.get("running")),
                        "status": _st_m,
                        "scheduler": _st_m.get("scheduler"),
                        "last_scan_summary": getattr(trader, "_last_scan_summary", None),
                        "last_manual_scan_at": last_manual_scan_at,
                        "research_snapshot_version": last_research_version,
                        "research_allocation_last": getattr(trader, "_last_research_allocation_ctx", None),
                    },
                    force=True,
                    manual_scan_in_progress=scan_in_progress,
                    manual_scan_started_at=scan_started_at,
                    manual_scan_finished_at=scan_finished_at,
                    last_manual_scan_error=last_manual_scan_error,
                )
                scan_started = time.perf_counter()
                summary = trader.run_scan_once()
                emit_metric(
                    event="worker.manual_scan_once",
                    ok=True,
                    elapsed_ms=(time.perf_counter() - scan_started) * 1000.0,
                    tags={"market": str(trader.get_config().get("market", "us"))},
                )
                scan_in_progress = False
                scan_finished_at = datetime.now().isoformat()
                last_manual_scan_at = scan_finished_at
                last_manual_scan_error = None
                _st_ok = trader.get_status(for_runtime_export=True)
                _write_worker_runtime(
                    {
                        "pid": os.getpid(),
                        "updated_at": datetime.now().isoformat(),
                        "worker_running": bool(_st_ok.get("running")),
                        "status": _st_ok,
                        "scheduler": _st_ok.get("scheduler"),
                        "last_scan_summary": summary,
                        "last_manual_scan_at": last_manual_scan_at,
                        "research_snapshot_version": last_research_version,
                        "research_allocation_last": getattr(trader, "_last_research_allocation_ctx", None),
                    },
                    force=True,
                    manual_scan_in_progress=scan_in_progress,
                    manual_scan_started_at=scan_started_at,
                    manual_scan_finished_at=scan_finished_at,
                    last_manual_scan_error=last_manual_scan_error,
                )
                try:
                    cfg_now = trader.get_config()
                    rs = run_research_snapshot(
                        trader=trader,
                        market=str(cfg_now.get("market", "us") or "us"),
                        kline=str(cfg_now.get("kline", "1d") or "1d"),
                        top_n=int(cfg_now.get("top_n", 8) or 8),
                        backtest_days=int(cfg_now.get("backtest_days", 180) or 180),
                    )
                    last_research_version = str(rs.get("version") or "")
                except Exception:
                    pass
            except Exception as e:
                last_manual_scan_error = str(e)
                scan_in_progress = False
                scan_finished_at = datetime.now().isoformat()
                last_manual_scan_at = scan_finished_at
                emit_metric(
                    event="worker.manual_scan_once",
                    ok=False,
                    tags={"market": str(trader.get_config().get("market", "us"))},
                    extra={"error": last_manual_scan_error},
                )
                _st_err = trader.get_status(for_runtime_export=True)
                _write_worker_runtime(
                    {
                        "pid": os.getpid(),
                        "updated_at": datetime.now().isoformat(),
                        "worker_running": bool(_st_err.get("running")),
                        "status": _st_err,
                        "scheduler": _st_err.get("scheduler"),
                        "last_scan_summary": getattr(trader, "_last_scan_summary", None),
                        "last_manual_scan_at": last_manual_scan_at,
                        "research_snapshot_version": last_research_version,
                        "research_allocation_last": getattr(trader, "_last_research_allocation_ctx", None),
                    },
                    force=True,
                    manual_scan_in_progress=scan_in_progress,
                    manual_scan_started_at=scan_started_at,
                    manual_scan_finished_at=scan_finished_at,
                    last_manual_scan_error=last_manual_scan_error,
                )
        # 处理 API 侧投递的“确认执行”请求（半自动模式待确认信号）
        confirmations = _consume_confirm_queue()
        if confirmations:
            for item in confirmations:
                try:
                    trader.confirm_and_execute(
                        str(item.get("signal_id") or ""),
                        confirmation_token=str(item.get("confirmation_token") or "").strip() or None,
                    )
                except Exception:
                    # 静默吞掉，避免阻塞主循环；信号状态由持久化文件体现
                    pass
        _st = trader.get_status(for_runtime_export=True)
        if not _st.get("running"):
            trader.start_scheduler()
            _st = trader.get_status(for_runtime_export=True)
        _write_worker_runtime(
            {
                "pid": os.getpid(),
                "updated_at": datetime.now().isoformat(),
                "worker_running": bool(_st.get("running")),
                "status": _st,
                "scheduler": _st.get("scheduler"),
                "last_scan_summary": getattr(trader, "_last_scan_summary", None),
                "last_manual_scan_at": last_manual_scan_at,
                "research_snapshot_version": last_research_version,
                "research_allocation_last": getattr(trader, "_last_research_allocation_ctx", None),
            },
            manual_scan_in_progress=scan_in_progress,
            manual_scan_started_at=scan_started_at,
            manual_scan_finished_at=scan_finished_at,
            last_manual_scan_error=last_manual_scan_error,
        )
        time.sleep(2)

    trader.stop_scheduler()
    _st_x = trader.get_status(for_runtime_export=True)
    _write_worker_runtime(
        {
            "pid": os.getpid(),
            "updated_at": datetime.now().isoformat(),
            "worker_running": False,
            "status": _st_x,
            "scheduler": _st_x.get("scheduler"),
            "last_scan_summary": getattr(trader, "_last_scan_summary", None),
            "last_manual_scan_at": last_manual_scan_at,
        },
        force=True,
        manual_scan_in_progress=False,
        manual_scan_started_at=scan_started_at,
        manual_scan_finished_at=scan_finished_at,
        last_manual_scan_error=last_manual_scan_error,
    )


if __name__ == "__main__":
    main()
