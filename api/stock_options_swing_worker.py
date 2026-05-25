"""
Stock options swing / long-term worker.

This worker is intentionally independent from the 0DTE / intraday stock options
worker.  It uses its own config, pid/runtime files and ledger directory, and it
does not manage broker positions unless they were opened by this worker's own
ledger.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import time
import hashlib
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

from mcp_server.backtest_engine import Bar, coerce_bar_datetime

INSTANCE = "stock_options_swing"
DATA_DIR = os.path.join(ROOT, "data", INSTANCE)
CONFIG_FILE = os.path.join(DATA_DIR, "live_worker_config.json")
DECISION_TAIL_FILE = os.path.join(DATA_DIR, "live_worker_decision_tail.jsonl")
LEDGER_FILE = os.path.join(DATA_DIR, "live_worker_execution_ledger.jsonl")
PID_FILE = os.path.join(ROOT, ".stock_options_swing_worker.pid")
STOP_FILE = os.path.join(ROOT, ".stock_options_swing_worker.stop")
RUNTIME_FILE = os.path.join(ROOT, ".stock_options_swing_worker.runtime.json")
SINGLETON_LOCK_FILE = os.path.join(ROOT, ".stock_options_swing_worker.singleton.lock")

DECISION_TAIL_MAX_BYTES = int(os.getenv("STOCK_OPTIONS_SWING_DECISION_TAIL_MAX_BYTES", str(2 * 1024 * 1024)))
DECISION_TAIL_KEEP_BYTES = int(os.getenv("STOCK_OPTIONS_SWING_DECISION_TAIL_KEEP_BYTES", str(1 * 1024 * 1024)))
API_TIMEOUT = max(1.0, float(os.getenv("STOCK_OPTIONS_SWING_API_TIMEOUT_SECONDS", "25")))
_API_BASE_URL = str(os.getenv("STOCK_OPTIONS_SWING_API_BASE_URL", "http://127.0.0.1:8010")).strip().rstrip("/")
_API_BEARER_TOKEN = str(os.getenv("STOCK_OPTIONS_SWING_API_BEARER_TOKEN") or os.getenv("QQQ_LIVE_API_BEARER_TOKEN") or "").strip()
_API_KEY = str(os.getenv("STOCK_OPTIONS_SWING_API_KEY") or os.getenv("QQQ_LIVE_API_KEY") or "").strip()
_API_LOCAL_OWNER = str(os.getenv("STOCK_OPTIONS_SWING_OWNER_ID") or os.getenv("X_MT_LOCAL_OWNER") or "").strip().lower()
_API_ACCOUNT_ID = str(os.getenv("STOCK_OPTIONS_SWING_ACCOUNT_ID") or "").strip()
_API_BROKER_PROVIDER = str(os.getenv("STOCK_OPTIONS_SWING_BROKER_PROVIDER") or "").strip().lower()

_stop = threading.Event()
_SINGLETON_WIN_MUTEX_HANDLE: Any = None
_SINGLETON_POSIX_LOCK_FD: Any = None
_LAST_RUNTIME_DIGEST = ""
_LAST_RUNTIME_TS = 0.0

DEFAULT_CONFIG: dict[str, Any] = {
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
    "confirmation_token": None,
    "contracts": 1,
    "account_risk": {
        "enabled": True,
        "fail_closed_for_live": True,
        "min_buy_power": 0.0,
        "min_buy_power_pct": 0.0,
        "max_order_premium_pct": 0.05,
        "max_total_option_premium_pct": 0.35,
    },
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


def _truthy(v: Any) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "on"}


def _write_pid() -> None:
    parent = os.path.dirname(PID_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def _remove_pid() -> None:
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def _remove_runtime() -> None:
    try:
        if os.path.exists(RUNTIME_FILE):
            os.remove(RUNTIME_FILE)
    except Exception:
        pass


def _acquire_process_singleton_or_exit() -> None:
    global _SINGLETON_WIN_MUTEX_HANDLE, _SINGLETON_POSIX_LOCK_FD
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            mutex_name = "Local\\MultiTradingStockOptionsSwingWorker"
            handle = kernel32.CreateMutexW(None, False, mutex_name)
            if not handle:
                return
            wait = kernel32.WaitForSingleObject(handle, 0)
            if wait != 0:
                print("[stock_options_swing_worker] mutex busy, exit 0.", file=sys.stderr)
                sys.exit(0)
            _SINGLETON_WIN_MUTEX_HANDLE = handle
            return
        except Exception:
            return
    try:
        import fcntl

        _SINGLETON_POSIX_LOCK_FD = os.open(SINGLETON_LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(_SINGLETON_POSIX_LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[stock_options_swing_worker] lock busy, exit 0.", file=sys.stderr)
        sys.exit(0)
    except Exception:
        return


def _release_process_singleton() -> None:
    global _SINGLETON_WIN_MUTEX_HANDLE, _SINGLETON_POSIX_LOCK_FD
    if os.name == "nt" and _SINGLETON_WIN_MUTEX_HANDLE:
        try:
            import ctypes

            ctypes.windll.kernel32.ReleaseMutex(_SINGLETON_WIN_MUTEX_HANDLE)
            ctypes.windll.kernel32.CloseHandle(_SINGLETON_WIN_MUTEX_HANDLE)
        except Exception:
            pass
        _SINGLETON_WIN_MUTEX_HANDLE = None
    if _SINGLETON_POSIX_LOCK_FD is not None:
        try:
            import fcntl

            fcntl.flock(_SINGLETON_POSIX_LOCK_FD, fcntl.LOCK_UN)
            os.close(_SINGLETON_POSIX_LOCK_FD)
        except Exception:
            pass
        _SINGLETON_POSIX_LOCK_FD = None


def _should_stop() -> bool:
    return _stop.is_set() or os.path.exists(STOP_FILE)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_config(path: str = CONFIG_FILE) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return _deep_merge(DEFAULT_CONFIG, data)
    except Exception:
        pass
    return dict(DEFAULT_CONFIG)


def _normalize_stock_pool(raw: dict[str, Any]) -> list[str]:
    values: list[str] = []
    src = raw.get("stock_pool")
    if isinstance(src, str):
        values.extend(re.split(r"[\s,;，；]+", src))
    elif isinstance(src, list):
        values.extend(str(x) for x in src)
    primary = str(raw.get("symbol") or "QQQ.US").strip()
    if primary:
        values.insert(0, primary)
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        sym = str(v or "").strip().upper()
        if not sym:
            continue
        if "." not in sym:
            sym = f"{sym}.US"
        if sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out or ["QQQ.US"]


def _resolve_api_bearer(raw: dict[str, Any]) -> str:
    env = str(os.getenv("STOCK_OPTIONS_SWING_API_BEARER_TOKEN") or os.getenv("QQQ_LIVE_API_BEARER_TOKEN") or "").strip()
    if env:
        return env
    return str(raw.get("api_bearer_token") or "").strip()


def _read_api_auth_from_config(path: str) -> tuple[str, str]:
    if not path:
        return "", ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return "", ""
        return str(data.get("api_key") or "").strip(), str(data.get("api_bearer_token") or "").strip()
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


def _api_key_allowed_for_owner(raw_key: str, owner_id: str) -> bool:
    owner = str(owner_id or "").strip().lower()
    if not str(raw_key or "").strip():
        return False
    return not owner or _api_key_owner_matches(raw_key, owner)


def _worker_auth_legacy_config_paths(config_path: str = "") -> list[str]:
    current = os.path.abspath(config_path) if config_path else ""
    candidates = [
        os.path.join(ROOT, "data", "stock_options_swing", "live_worker_config.json"),
        os.path.join(ROOT, "data", "qqq_1dte", "live_worker_config.json"),
        os.path.join(ROOT, "data", "qqq_0dte", "live_worker_config.json"),
        os.path.join(ROOT, "api", "auto_trader_config.json"),
    ]
    out: list[str] = []
    seen: set[str] = set()
    for p in candidates:
        ap = os.path.abspath(p)
        if current and ap == current:
            continue
        if ap in seen:
            continue
        seen.add(ap)
        out.append(p)
    return out


def _maybe_migrate_api_key_to_config(config_path: str, raw_key: str, source_path: str) -> None:
    key = str(raw_key or "").strip()
    if not config_path or not key:
        return
    try:
        if source_path and os.path.abspath(config_path) == os.path.abspath(source_path):
            return
        data: dict[str, Any] = {}
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                data = raw
        if str(data.get("api_key") or "").strip() or str(data.get("api_bearer_token") or "").strip():
            return
        parent = os.path.dirname(config_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        data["api_key"] = key
        data["api_key_migrated_from_legacy_config_at"] = datetime.now(timezone.utc).isoformat()
        if source_path:
            data["api_key_migrated_from_legacy_config"] = os.path.relpath(source_path, ROOT)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
    except Exception:
        return


def _resolve_api_key(raw: dict[str, Any], config_path: str = "") -> str:
    env = str(os.getenv("STOCK_OPTIONS_SWING_API_KEY") or os.getenv("QQQ_LIVE_API_KEY") or "").strip()
    owner = str(globals().get("_API_LOCAL_OWNER") or "").strip().lower()
    if env and _api_key_allowed_for_owner(env, owner):
        return env
    key = str(raw.get("api_key") or "").strip() if isinstance(raw, dict) else ""
    if key and _api_key_allowed_for_owner(key, owner):
        return key
    for path in _worker_auth_legacy_config_paths(config_path):
        legacy_key, _legacy_bearer = _read_api_auth_from_config(path)
        if legacy_key and _api_key_allowed_for_owner(legacy_key, owner):
            _maybe_migrate_api_key_to_config(config_path, legacy_key, path)
            return legacy_key
    return ""


def _effective_account_id(raw: dict[str, Any] | None = None) -> str:
    if isinstance(raw, dict) and str(raw.get("account_id") or "").strip():
        return str(raw.get("account_id") or "").strip()
    return _API_ACCOUNT_ID


def _effective_broker_provider(raw: dict[str, Any] | None = None) -> str:
    if isinstance(raw, dict) and str(raw.get("broker_provider") or "").strip():
        return str(raw.get("broker_provider") or "").strip().lower()
    return _API_BROKER_PROVIDER


def _worker_context(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "owner_id": _API_LOCAL_OWNER or None,
        "account_id": _effective_account_id(raw) or None,
        "broker_provider": _effective_broker_provider(raw) or None,
    }


def _config_matches_worker_context(raw: dict[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(raw, dict):
        return False, "config_not_dict"
    if _API_LOCAL_OWNER:
        owner = str(raw.get("owner_id") or "").strip().lower()
        if owner and owner != _API_LOCAL_OWNER:
            return False, "owner_mismatch"
    if _API_ACCOUNT_ID:
        account_id = str(raw.get("account_id") or "").strip()
        if not account_id:
            return False, "account_missing"
        if account_id != _API_ACCOUNT_ID:
            return False, "account_mismatch"
    if _API_BROKER_PROVIDER:
        provider = str(raw.get("broker_provider") or "").strip().lower()
        if provider and provider != _API_BROKER_PROVIDER:
            return False, "broker_mismatch"
    return True, ""


def _api_apply_auth(req: urllib.request.Request) -> None:
    if _API_KEY:
        req.add_header("X-Api-Key", _API_KEY)
    elif _API_BEARER_TOKEN:
        req.add_header("Authorization", f"Bearer {_API_BEARER_TOKEN}")
    if _API_LOCAL_OWNER:
        req.add_header("X-MT-Local-Owner", _API_LOCAL_OWNER)


def _api_get_json(path: str, timeout: float | None = None) -> dict[str, Any] | None:
    url = f"{_API_BASE_URL}{path}"
    try:
        req = urllib.request.Request(url)
        _api_apply_auth(req)
        with urllib.request.urlopen(req, timeout=float(timeout or API_TIMEOUT)) as resp:
            if int(getattr(resp, "status", 200) or 200) != 200:
                return None
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw) if raw else {}
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _api_post_json(path: str, payload: dict[str, Any], timeout: float | None = None) -> tuple[bool, dict[str, Any]]:
    url = f"{_API_BASE_URL}{path}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    _api_apply_auth(req)
    try:
        with urllib.request.urlopen(req, timeout=float(timeout or API_TIMEOUT)) as resp:
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


def _account_risk_config(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    src = raw.get("account_risk") if isinstance(raw, dict) and isinstance(raw.get("account_risk"), dict) else {}
    out = {
        "enabled": True,
        "fail_closed_for_live": True,
        "min_buy_power": 0.0,
        "min_buy_power_pct": 0.0,
        "max_order_premium_pct": 0.05,
        "max_total_option_premium_pct": 0.35,
    }
    out.update(src)
    out["enabled"] = bool(out.get("enabled", True))
    out["fail_closed_for_live"] = bool(out.get("fail_closed_for_live", True))
    return out


def _fetch_trade_account(raw: dict[str, Any]) -> dict[str, Any] | None:
    account_id = _effective_account_id(raw)
    q = urllib.parse.urlencode({"account_id": account_id}) if account_id else ""
    return _api_get_json("/trade/account" + (f"?{q}" if q else ""), timeout=min(API_TIMEOUT, 12.0))


def _account_level_risk_gate(
    raw: dict[str, Any],
    *,
    positions: list[dict[str, Any]],
    order_premium: float = 0.0,
    dry_run: bool | None = None,
) -> dict[str, Any]:
    cfg = _account_risk_config(raw)
    dry_like = (not _is_live_auto_submit(raw)) if dry_run is None else bool(dry_run)
    detail: dict[str, Any] = {
        "enabled": bool(cfg.get("enabled", True)),
        "dry_run": dry_like,
        "order_premium": round(max(0.0, float(order_premium or 0.0)), 2),
        "blocked": False,
        "blocks": [],
        **_worker_context(raw),
    }
    if not bool(cfg.get("enabled", True)):
        return detail
    account = _fetch_trade_account(raw)
    if not isinstance(account, dict):
        detail["account_available"] = False
        detail["blocks"].append("account_unavailable")
        detail["blocked"] = bool((not dry_like) and cfg.get("fail_closed_for_live", True))
        return detail
    net_assets = _num(account.get("net_assets") or account.get("total_assets") or account.get("equity"), 0.0)
    buy_power = _num(account.get("buy_power") or account.get("buying_power") or account.get("available_cash"), 0.0)
    detail.update(
        {
            "account_available": True,
            "net_assets": round(net_assets, 2) if net_assets > 0 else None,
            "buy_power": round(buy_power, 2) if buy_power > 0 else None,
            "currency": account.get("currency"),
        }
    )
    min_buy_power = _num(cfg.get("min_buy_power"), 0.0)
    min_buy_power_pct = _num(cfg.get("min_buy_power_pct"), 0.0)
    required_bp = max(min_buy_power, net_assets * min_buy_power_pct if net_assets > 0 else 0.0)
    if required_bp > 0 and buy_power < required_bp:
        detail["blocks"].append("buy_power_below_min")
        detail["required_buy_power"] = round(required_bp, 2)
    if order_premium > 0 and buy_power > 0 and buy_power < order_premium:
        detail["blocks"].append("buy_power_below_order_premium")
    max_order_pct = _num(cfg.get("max_order_premium_pct"), 0.0)
    if net_assets > 0 and max_order_pct > 0 and order_premium > net_assets * max_order_pct:
        detail["blocks"].append("order_premium_pct_exceeded")
        detail["max_order_premium"] = round(net_assets * max_order_pct, 2)
    total_premium = sum(_position_premium(x) for x in positions if isinstance(x, dict))
    detail["current_option_premium"] = round(total_premium, 2)
    max_total_pct = _num(cfg.get("max_total_option_premium_pct"), 0.0)
    if net_assets > 0 and max_total_pct > 0 and total_premium + order_premium > net_assets * max_total_pct:
        detail["blocks"].append("total_option_premium_pct_exceeded")
        detail["max_total_option_premium"] = round(net_assets * max_total_pct, 2)
    detail["blocks"] = sorted(set(str(x) for x in detail["blocks"] if str(x)))
    detail["blocked"] = bool(detail["blocks"] and not dry_like)
    return detail


def _append_jsonl(path: str, payload: dict[str, Any], *, max_bytes: int = 0, keep_bytes: int = 0) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if max_bytes > 0 and os.path.isfile(path):
        try:
            sz = int(os.path.getsize(path) or 0)
            if sz > max(1024, max_bytes):
                keep = max(512, min(sz, keep_bytes or max_bytes // 2))
                with open(path, "rb") as f:
                    if keep < sz:
                        f.seek(sz - keep)
                    tail = f.read()
                cut = tail.find(b"\n")
                if cut >= 0 and cut + 1 < len(tail):
                    tail = tail[cut + 1 :]
                with open(path, "wb") as f:
                    f.write(tail)
                    if tail and not tail.endswith(b"\n"):
                        f.write(b"\n")
        except Exception:
            pass
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str))
        f.write("\n")


def _append_decision(symbol: str, action: dict[str, Any], logs: list[dict[str, Any]] | None = None, raw: dict[str, Any] | None = None) -> None:
    _append_jsonl(
        DECISION_TAIL_FILE,
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "symbol": str(symbol or "").strip().upper(),
            **_worker_context(raw),
            "action": action,
            "logs": logs or [],
        },
        max_bytes=DECISION_TAIL_MAX_BYTES,
        keep_bytes=DECISION_TAIL_KEEP_BYTES,
    )


def _write_runtime(payload: dict[str, Any], *, force: bool = False) -> None:
    global _LAST_RUNTIME_DIGEST, _LAST_RUNTIME_TS
    doc = {
        "worker_running": True,
        "pid": os.getpid(),
        "instance": INSTANCE,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    digest = json.dumps(doc, sort_keys=True, ensure_ascii=False, default=str)
    now = time.monotonic()
    if not force and digest == _LAST_RUNTIME_DIGEST and now - _LAST_RUNTIME_TS < 10:
        return
    parent = os.path.dirname(RUNTIME_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = RUNTIME_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, default=str)
        f.write("\n")
    os.replace(tmp, RUNTIME_FILE)
    _LAST_RUNTIME_DIGEST = digest
    _LAST_RUNTIME_TS = now


def _fetch_bars(symbol: str, days: int, kline: str) -> tuple[list[Bar], str]:
    q = urllib.parse.urlencode({"symbol": symbol.upper(), "days": int(days), "kline": str(kline), "priority": "normal"})
    data = _api_get_json(f"/internal/longport/history-bars?{q}", timeout=max(API_TIMEOUT, 30.0))
    items = data.get("items") if isinstance(data, dict) else None
    source = str(data.get("source") or "internal_longport") if isinstance(data, dict) else "internal_longport"
    out: list[Bar] = []
    if isinstance(items, list):
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
    out.sort(key=lambda b: b.date)
    return out, source


def _fetch_quote(symbol: str) -> tuple[dict[str, Any] | None, str]:
    q = urllib.parse.urlencode({"symbol": symbol.upper()})
    data = _api_get_json(f"/internal/longport/quote?{q}", timeout=min(API_TIMEOUT, 8.0))
    if isinstance(data, dict) and bool(data.get("available")):
        return data, str(data.get("source") or "internal_longport")
    return None, str(data.get("source") or "internal_longport") if isinstance(data, dict) else "internal_longport"


def _fetch_option_positions(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None, str]:
    account_id = _effective_account_id(raw)
    q = urllib.parse.urlencode({"account_id": account_id}) if account_id else ""
    data = _api_get_json("/options/positions" + (f"?{q}" if q else ""), timeout=min(API_TIMEOUT, 12.0))
    primary_err: str | None = None
    if isinstance(data, dict) and isinstance(data.get("positions"), list):
        return [x for x in data.get("positions", []) if isinstance(x, dict)], None, "options_positions"
    if isinstance(data, dict):
        primary_err = str(data.get("error") or data.get("detail") or "options_positions_bad_response")
    else:
        primary_err = "options_positions_unavailable"

    # The trading panel reads /trade/positions, and some broker adapters expose
    # OCC option positions there even when /options/positions is unavailable.
    fallback = _api_get_json("/trade/positions" + (f"?{q}" if q else ""), timeout=min(API_TIMEOUT, 12.0))
    if isinstance(fallback, dict) and isinstance(fallback.get("positions"), list):
        option_rows = []
        for row in fallback.get("positions", []):
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").strip().upper()
            if _option_symbol_underlying(sym):
                option_rows.append(row)
        return option_rows, None, "trade_positions_fallback"
    if isinstance(fallback, dict):
        fallback_err = str(fallback.get("error") or fallback.get("detail") or "trade_positions_bad_response")
    else:
        fallback_err = "trade_positions_unavailable"
    return [], f"{primary_err};fallback:{fallback_err}", "unavailable"


def _fetch_option_expiries(symbol: str, raw: dict[str, Any]) -> list[date]:
    account_id = _effective_account_id(raw)
    params = {"symbol": symbol.upper()}
    if account_id:
        params["account_id"] = account_id
    data = _api_get_json(f"/options/expiries?{urllib.parse.urlencode(params)}", timeout=min(API_TIMEOUT, 12.0))
    xs = data.get("expiries") if isinstance(data, dict) else None
    out: list[date] = []
    if isinstance(xs, list):
        for x in xs:
            try:
                out.append(date.fromisoformat(str(x)[:10]))
            except Exception:
                continue
    return sorted(set(out))


def _fetch_option_chain(symbol: str, expiry: date, spot: float, raw: dict[str, Any]) -> list[dict[str, Any]]:
    account_id = _effective_account_id(raw)
    lo = max(0.01, spot * 0.6)
    hi = spot * 1.4
    params: dict[str, Any] = {
        "symbol": symbol.upper(),
        "expiry_date": expiry.isoformat(),
        "min_strike": round(lo, 2),
        "max_strike": round(hi, 2),
        "standard_only": "true",
        "limit": 500,
    }
    if account_id:
        params["account_id"] = account_id
    data = _api_get_json(f"/options/chain?{urllib.parse.urlencode(params)}", timeout=max(API_TIMEOUT, 20.0))
    rows = data.get("options") if isinstance(data, dict) else None
    return [x for x in (rows or []) if isinstance(x, dict)] if isinstance(rows, list) else []


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        out = float(v)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _sma(values: list[float], n: int) -> float | None:
    n = max(1, int(n))
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _rsi(values: list[float], n: int = 14) -> float | None:
    if len(values) <= n:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(len(values) - n, len(values)):
        chg = values[i] - values[i - 1]
        if chg >= 0:
            gains += chg
        else:
            losses += -chg
    if losses <= 0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def _trend_signal(symbol: str, bars: list[Bar], cfg: dict[str, Any], quote: dict[str, Any] | None) -> dict[str, Any]:
    strat = cfg.get("strategy") if isinstance(cfg.get("strategy"), dict) else {}
    closes = [float(b.close) for b in bars if float(b.close) > 0]
    if len(closes) < max(60, int(strat.get("trend_slow_ma") or 50)):
        return {"action": "skip", "reason": "insufficient_daily_bars", "bars": len(closes)}
    last = _num(quote.get("last") if isinstance(quote, dict) else None, closes[-1])
    prev = closes[-2] if len(closes) >= 2 else closes[-1]
    fast_n = int(strat.get("trend_fast_ma") or 20)
    slow_n = int(strat.get("trend_slow_ma") or 50)
    long_n = int(strat.get("long_ma") or 200)
    fast = _sma(closes, fast_n)
    slow = _sma(closes, slow_n)
    long_ma = _sma(closes, long_n)
    rsi = _rsi(closes, 14)
    score = 0
    reasons: list[str] = []
    if fast is not None and last > fast:
        score += 1
        reasons.append(f"price_above_ma{fast_n}")
    if fast is not None and slow is not None and fast > slow:
        score += 1
        reasons.append(f"ma{fast_n}_above_ma{slow_n}")
    if long_ma is not None and last > long_ma:
        score += 1
        reasons.append(f"price_above_ma{long_n}")
    if last > prev:
        score += 1
        reasons.append("daily_momentum_positive")
    if rsi is not None and 45 <= rsi <= 72:
        score += 1
        reasons.append("rsi_not_extreme")
    fast_gap = (last - fast) / fast if fast else 0.0
    slow_gap = (last - slow) / slow if slow else 0.0
    min_score = int(strat.get("min_trend_score") or 3)
    if slow_gap < _num(strat.get("min_price_above_slow_ma_pct"), 0.0):
        return {
            "action": "skip",
            "reason": "below_slow_ma_threshold",
            "score": score,
            "last": last,
            "ma_fast": fast,
            "ma_slow": slow,
            "ma_long": long_ma,
            "rsi14": rsi,
            "slow_gap": slow_gap,
        }
    if fast_gap > _num(strat.get("max_price_above_fast_ma_pct"), 0.12):
        return {
            "action": "watch",
            "reason": "extended_above_fast_ma",
            "score": score,
            "last": last,
            "ma_fast": fast,
            "ma_slow": slow,
            "ma_long": long_ma,
            "rsi14": rsi,
            "fast_gap": fast_gap,
        }
    action = "candidate_long_call" if score >= min_score else "watch"
    return {
        "action": action,
        "reason": "trend_candidate" if action == "candidate_long_call" else "trend_score_low",
        "score": score,
        "reasons": reasons,
        "last": last,
        "prev_close": prev,
        "change_pct": ((last - prev) / prev * 100.0) if prev > 0 else None,
        "ma_fast": fast,
        "ma_slow": slow,
        "ma_long": long_ma,
        "rsi14": rsi,
        "fast_gap": fast_gap,
        "slow_gap": slow_gap,
    }


def _choose_expiry(expiries: list[date], strat: dict[str, Any], today: date) -> date | None:
    min_dte = int(strat.get("min_dte") or 45)
    target_dte = int(strat.get("target_dte") or 90)
    max_dte = int(strat.get("max_dte") or 180)
    candidates = [d for d in expiries if min_dte <= (d - today).days <= max_dte]
    if not candidates:
        return None
    return min(candidates, key=lambda d: abs((d - today).days - target_dte))


def _quote_price(row: dict[str, Any], right: str) -> dict[str, Any]:
    q = row.get("call_quote") if right == "call" else row.get("put_quote")
    return q if isinstance(q, dict) else {}


def _option_quote_snapshot(row: dict[str, Any], right: str) -> dict[str, Any]:
    q = _quote_price(row, right)
    last = _num(q.get("last_done"), 0.0)
    bid = _num(q.get("bid") or q.get("best_bid") or q.get("bid_price"), 0.0)
    ask = _num(q.get("ask") or q.get("best_ask") or q.get("ask_price"), 0.0)
    volume = int(_num(q.get("volume"), 0.0))
    mid = ((bid + ask) / 2.0) if bid > 0 and ask > 0 else last
    spread_pct = ((ask - bid) / max(mid, 1e-9)) if ask > 0 and bid > 0 and mid > 0 else None
    return {
        "bid": bid or None,
        "ask": ask or None,
        "last": last or None,
        "mid": mid or None,
        "spread_pct": spread_pct,
        "volume": volume,
        "quote": q,
    }


def _choose_option_contract(symbol: str, spot: float, raw: dict[str, Any]) -> dict[str, Any]:
    strat = raw.get("strategy") if isinstance(raw.get("strategy"), dict) else {}
    mode = str(strat.get("mode") or "long_call").strip().lower()
    expiries = _fetch_option_expiries(symbol, raw)
    today = datetime.now(ZoneInfo("America/New_York")).date()
    expiry = _choose_expiry(expiries, strat, today)
    if expiry is None:
        return {"ok": False, "reason": "no_expiry_in_dte_window", "expiries": [d.isoformat() for d in expiries[:12]]}
    rows = _fetch_option_chain(symbol, expiry, spot, raw)
    if not rows:
        return {"ok": False, "reason": "option_chain_empty", "expiry": expiry.isoformat()}
    fallback_otm = _num(strat.get("fallback_otm_pct"), 0.03)
    target_strike = spot * (1.0 + max(0.0, fallback_otm))
    calls = [
        x
        for x in rows
        if str(x.get("call_symbol") or "").strip()
        and _num(x.get("strike_price"), 0.0) >= max(0.01, spot * 0.85)
    ]
    if not calls:
        return {"ok": False, "reason": "no_call_rows", "expiry": expiry.isoformat()}
    calls.sort(key=lambda x: abs(_num(x.get("strike_price"), 0.0) - target_strike))
    max_spread = _num(strat.get("max_bid_ask_spread_pct"), 0.18)
    min_volume = int(strat.get("min_option_volume") or 0)
    chosen: dict[str, Any] | None = None
    checked: list[dict[str, Any]] = []
    for row in calls[:20]:
        snap = _option_quote_snapshot(row, "call")
        candidate = {
            "symbol": str(row.get("call_symbol") or "").strip().upper(),
            "expiry_date": expiry.isoformat(),
            "strike": _num(row.get("strike_price"), 0.0),
            "right": "call",
            **snap,
        }
        checked.append({k: candidate.get(k) for k in ("symbol", "strike", "bid", "ask", "last", "spread_pct", "volume")})
        if min_volume > 0 and int(candidate.get("volume") or 0) < min_volume:
            continue
        if candidate.get("spread_pct") is not None and float(candidate["spread_pct"]) > max_spread:
            continue
        if candidate["ask"] or candidate["last"]:
            chosen = candidate
            break
    if chosen is None:
        row = calls[0]
        q = _quote_price(row, "call")
        chosen = {
            "symbol": str(row.get("call_symbol") or "").strip().upper(),
            "expiry_date": expiry.isoformat(),
            "strike": _num(row.get("strike_price"), 0.0),
            "right": "call",
            "bid": _num(q.get("bid") or q.get("best_bid") or q.get("bid_price"), 0.0) or None,
            "ask": _num(q.get("ask") or q.get("best_ask") or q.get("ask_price"), 0.0) or None,
            "last": _num(q.get("last_done"), 0.0) or None,
            "spread_pct": None,
            "volume": int(_num(q.get("volume"), 0.0)),
            "quote": q,
            "warning": "fallback_contract_without_full_liquidity_pass",
        }
    if mode != "call_debit_spread":
        return {"ok": True, "structure": "long_call", "contract": chosen, "checked": checked[:8]}

    long_leg = chosen
    width_pct = max(0.01, _num(strat.get("spread_width_pct"), 0.05))
    target_short = max(float(long_leg.get("strike") or 0.0) + 0.01, spot * (1.0 + max(0.0, fallback_otm) + width_pct))
    short_rows = [x for x in calls if _num(x.get("strike_price"), 0.0) > float(long_leg.get("strike") or 0.0)]
    if not short_rows:
        return {"ok": False, "reason": "no_short_call_row_for_debit_spread", "expiry": expiry.isoformat(), "long_leg": long_leg}
    short_rows.sort(key=lambda x: abs(_num(x.get("strike_price"), 0.0) - target_short))
    short_leg: dict[str, Any] | None = None
    for row in short_rows[:20]:
        snap = _option_quote_snapshot(row, "call")
        candidate = {
            "symbol": str(row.get("call_symbol") or "").strip().upper(),
            "expiry_date": expiry.isoformat(),
            "strike": _num(row.get("strike_price"), 0.0),
            "right": "call",
            **snap,
        }
        if min_volume > 0 and int(candidate.get("volume") or 0) < min_volume:
            continue
        if candidate.get("spread_pct") is not None and float(candidate["spread_pct"]) > max_spread:
            continue
        if candidate.get("bid") or candidate.get("last"):
            short_leg = candidate
            break
    if short_leg is None:
        row = short_rows[0]
        short_leg = {
            "symbol": str(row.get("call_symbol") or "").strip().upper(),
            "expiry_date": expiry.isoformat(),
            "strike": _num(row.get("strike_price"), 0.0),
            "right": "call",
            **_option_quote_snapshot(row, "call"),
            "warning": "fallback_short_leg_without_full_liquidity_pass",
        }
    long_px = _num(long_leg.get("ask") or long_leg.get("last"), 0.0)
    short_px = _num(short_leg.get("bid") or short_leg.get("last"), 0.0)
    net_debit = max(0.0, long_px - short_px)
    if net_debit <= 0:
        return {"ok": False, "reason": "invalid_spread_net_debit", "long_leg": long_leg, "short_leg": short_leg}
    max_debit = _num(strat.get("max_spread_debit"), 0.0)
    if max_debit > 0 and net_debit * 100.0 > max_debit:
        return {
            "ok": False,
            "reason": f"spread_debit_over_limit:{round(net_debit * 100.0, 2)}>{max_debit}",
            "long_leg": long_leg,
            "short_leg": short_leg,
        }
    width = max(0.0, float(short_leg.get("strike") or 0.0) - float(long_leg.get("strike") or 0.0))
    return {
        "ok": True,
        "structure": "call_debit_spread",
        "contract": long_leg,
        "long_leg": long_leg,
        "short_leg": short_leg,
        "legs": [
            {"symbol": long_leg["symbol"], "side": "buy", "contracts": 1, "price": long_px},
            {"symbol": short_leg["symbol"], "side": "sell", "contracts": 1, "price": short_px},
        ],
        "net_debit": round(net_debit, 4),
        "width": round(width, 4),
        "max_loss": round(net_debit * 100.0, 2),
        "max_profit": round(max(0.0, width - net_debit) * 100.0, 2),
        "checked": checked[:8],
    }


def _option_symbol_underlying(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    m = re.match(r"^([A-Z0-9]+?)(\d{6}|\d{8})[CP]\d+\.US$", s)
    if m:
        root = re.sub(r"\d+$", "", m.group(1)) or m.group(1)
        return f"{root}.US"
    if " " in s:
        return s.split(" ", 1)[0].strip().upper()
    return ""


def _parse_occ_option_symbol(symbol: str) -> dict[str, Any]:
    s = str(symbol or "").strip().upper()
    m = re.match(r"^([A-Z0-9]+?)(\d{6}|\d{8})([CP])(\d+)\.US$", s)
    if not m:
        return {"ok": False, "symbol": s, "underlying": _option_symbol_underlying(s)}
    root_raw, expiry_raw, right, strike_raw = m.groups()
    und = re.sub(r"\d+$", "", root_raw) or root_raw
    expiry_text = expiry_raw
    if len(expiry_text) == 6:
        expiry_text = f"20{expiry_text}"
    expiry_date = None
    try:
        expiry_date = date(int(expiry_text[:4]), int(expiry_text[4:6]), int(expiry_text[6:8]))
    except Exception:
        expiry_date = None
    strike = _num(strike_raw, 0.0) / 1000.0 if strike_raw else 0.0
    dte = (expiry_date - datetime.now(ZoneInfo("America/New_York")).date()).days if expiry_date else None
    return {
        "ok": True,
        "symbol": s,
        "underlying": f"{und}.US",
        "expiry_date": expiry_date.isoformat() if expiry_date else None,
        "dte": dte,
        "right": "call" if right == "C" else "put",
        "strike": strike,
    }


def _ledger_row_matches_context(row: dict[str, Any], raw: dict[str, Any] | None = None) -> bool:
    if not isinstance(row, dict):
        return False
    cfg = raw if isinstance(raw, dict) else {}
    if not bool(cfg.get("strict_account_ledger_match", True)):
        return True
    want_account = _effective_account_id(cfg)
    got_account = str(row.get("account_id") or "").strip()
    if want_account and got_account != want_account:
        return False
    want_owner = _API_LOCAL_OWNER
    got_owner = str(row.get("owner_id") or "").strip().lower()
    if want_owner and got_owner != want_owner:
        return False
    want_broker = _effective_broker_provider(cfg)
    got_broker = str(row.get("broker_provider") or "").strip().lower()
    if want_broker and got_broker != want_broker:
        return False
    return True


def _ledger_option_symbols(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    sym = str(row.get("option_symbol") or row.get("symbol") or "").strip().upper()
    if sym:
        out.append(sym)
    legs = row.get("legs")
    if isinstance(legs, list):
        for leg in legs:
            if isinstance(leg, dict):
                leg_sym = str(leg.get("symbol") or "").strip().upper()
                if leg_sym:
                    out.append(leg_sym)
    seen: set[str] = set()
    clean: list[str] = []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            clean.append(item)
    return clean


def _spread_group_id_from_row(row: dict[str, Any], symbols: list[str]) -> str:
    existing = str(row.get("position_group_id") or row.get("group_id") or "").strip()
    if existing:
        return existing
    raw = "|".join([str(row.get("at") or ""), str(row.get("underlying") or ""), *symbols])
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"spread-{digest}"


def _managed_spread_groups(raw: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    missing_by_group: dict[str, set[str]] = {}
    for row in _ledger_events(raw):
        event = str(row.get("event") or "")
        symbols = _ledger_option_symbols(row)
        gid = _spread_group_id_from_row(row, symbols)
        if event == "entry_partial_submitted" and str(row.get("structure") or "") == "call_debit_spread":
            gid = str(row.get("position_group_id") or gid)
            groups[gid] = {
                "position_group_id": gid,
                "structure": "call_debit_spread",
                "underlying": str(row.get("underlying") or _option_symbol_underlying(symbols[0] if symbols else "") or "").strip().upper(),
                "opened_at": row.get("at"),
                "option_symbol": symbols[0] if symbols else "",
                "legs": [],
                "contracts": max(1, int(abs(_num(row.get("contracts"), 1.0)) or 1)),
                "entry_net_debit": 0.0,
                "entry_event": row,
                "partial_entry": True,
            }
            continue
        if event in {"entry_submitted", "entry_dry_run"} and str(row.get("structure") or "") == "call_debit_spread":
            legs = row.get("legs")
            if not isinstance(legs, list) or len(legs) < 2:
                continue
            clean_legs: list[dict[str, Any]] = []
            for leg in legs:
                if not isinstance(leg, dict):
                    continue
                sym = str(leg.get("symbol") or "").strip().upper()
                side = str(leg.get("side") or "").strip().lower()
                if not sym or side not in {"buy", "sell"}:
                    continue
                clean_legs.append(
                    {
                        "symbol": sym,
                        "side": side,
                        "contracts": max(1, int(abs(_num(leg.get("contracts"), row.get("contracts") or 1)) or 1)),
                        "price": _num(leg.get("price"), 0.0),
                    }
                )
            if len(clean_legs) < 2:
                continue
            groups[gid] = {
                "position_group_id": gid,
                "structure": "call_debit_spread",
                "underlying": str(row.get("underlying") or _option_symbol_underlying(clean_legs[0]["symbol"]) or "").strip().upper(),
                "opened_at": row.get("at"),
                "option_symbol": clean_legs[0]["symbol"],
                "legs": clean_legs,
                "contracts": max(1, int(abs(_num(row.get("contracts"), clean_legs[0].get("contracts") or 1)) or 1)),
                "entry_net_debit": _num(row.get("net_debit"), 0.0) or _event_debit_premium(row) / 100.0,
                "entry_event": row,
            }
        elif event in {"exit_submitted", "closed", "spread_closed"}:
            if gid in groups:
                groups.pop(gid, None)
        elif event == "broker_position_missing_marked_closed":
            for existing_gid, group in list(groups.items()):
                group_symbols = {str(leg.get("symbol") or "").strip().upper() for leg in group.get("legs", []) if isinstance(leg, dict)}
                if symbols and any(sym in group_symbols for sym in symbols):
                    missing = missing_by_group.setdefault(existing_gid, set())
                    missing.update(sym for sym in symbols if sym in group_symbols)
                    if group_symbols and group_symbols.issubset(missing):
                        groups.pop(existing_gid, None)
    return groups


def _managed_position_symbols(raw: dict[str, Any] | None = None) -> set[str]:
    out: set[str] = set()
    if not os.path.isfile(LEDGER_FILE):
        return out
    try:
        with open(LEDGER_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                if not _ledger_row_matches_context(row, raw):
                    continue
                symbols = _ledger_option_symbols(row)
                event = str(row.get("event") or "")
                if event in {"entry_submitted", "entry_partial_submitted", "imported_existing_position"}:
                    out.update(symbols)
                if event in {"exit_submitted", "closed", "broker_position_missing_marked_closed"}:
                    for sym in symbols:
                        out.discard(sym)
    except Exception:
        return out
    return out


def _reconcile_missing_managed_positions(positions: list[dict[str, Any]], raw: dict[str, Any]) -> list[dict[str, Any]]:
    managed = _managed_position_symbols(raw)
    if not managed:
        return []
    broker_symbols = {str(p.get("symbol") or "").strip().upper() for p in positions if isinstance(p, dict)}
    missing = sorted(sym for sym in managed if sym and sym not in broker_symbols)
    rows: list[dict[str, Any]] = []
    for sym in missing:
        row = {
            "event": "broker_position_missing_marked_closed",
            "at": datetime.now(timezone.utc).isoformat(),
            "option_symbol": sym,
            "symbol": sym,
            "underlying": _option_symbol_underlying(sym),
            "reason": "broker_position_missing_after_positions_reconcile",
            **_worker_context(raw),
        }
        _append_jsonl(LEDGER_FILE, row)
        rows.append(row)
    return rows


def _classify_positions(positions: list[dict[str, Any]], raw: dict[str, Any] | None = None) -> dict[str, Any]:
    managed = _managed_position_symbols(raw)
    spread_symbols: set[str] = set()
    for group in _managed_spread_groups(raw).values():
        for leg in group.get("legs", []):
            if isinstance(leg, dict):
                sym = str(leg.get("symbol") or "").strip().upper()
                if sym:
                    spread_symbols.add(sym)
    managed_rows = []
    unmanaged_rows = []
    for p in positions:
        sym = str(p.get("symbol") or "").strip().upper()
        if not sym:
            continue
        row = dict(p)
        row.update(_parse_occ_option_symbol(sym))
        row["quantity"] = _option_position_qty(row)
        if sym in managed or sym in spread_symbols:
            managed_rows.append(row)
        else:
            unmanaged_rows.append(row)
    return {"managed": managed_rows, "unmanaged": unmanaged_rows}


def _option_position_qty(row: dict[str, Any]) -> float:
    return _num(row.get("quantity"), 0.0)


def _position_premium(row: dict[str, Any]) -> float:
    qty = abs(_option_position_qty(row))
    cost = _num(row.get("cost_price") or row.get("avg_cost") or row.get("price"), 0.0)
    return qty * cost * 100.0


def _position_key(row: dict[str, Any]) -> str:
    return str(row.get("symbol") or "").strip().upper()


def _is_live_auto_submit(raw: dict[str, Any]) -> bool:
    return (not bool(raw.get("dry_run", True))) and bool(raw.get("auto_submit_orders", False))


def _live_submit_guard(raw: dict[str, Any], *, structure: str = "long_call") -> tuple[bool, str]:
    if not _is_live_auto_submit(raw):
        return True, ""
    if not str(raw.get("confirmation_token") or "").strip():
        return False, "confirmation_token_missing"
    if not str(raw.get("live_submit_confirmed_at") or "").strip():
        return False, "live_submit_not_confirmed"
    return True, ""


def _ledger_events(raw: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not os.path.isfile(LEDGER_FILE):
        return []
    out: list[dict[str, Any]] = []
    try:
        with open(LEDGER_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and _ledger_row_matches_context(row, raw):
                    out.append(row)
    except Exception:
        return []
    return out


def _event_date_et(row: dict[str, Any]) -> date | None:
    raw = str(row.get("at") or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("America/New_York")).date()
    except Exception:
        return None


def _event_debit_premium(row: dict[str, Any]) -> float:
    if isinstance(row.get("net_debit"), (int, float)):
        return max(0.0, float(row.get("net_debit") or 0.0) * 100.0 * abs(_num(row.get("contracts"), 1.0)))
    legs = row.get("legs")
    if isinstance(legs, list):
        total = 0.0
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            sign = 1.0 if str(leg.get("side") or "").lower() == "buy" else -1.0
            total += sign * _num(leg.get("price"), 0.0) * abs(_num(leg.get("contracts"), 0.0)) * 100.0
        return max(0.0, total)
    return max(0.0, _num(row.get("price"), 0.0) * abs(_num(row.get("contracts"), 0.0)) * 100.0)


def _new_premium_today(raw: dict[str, Any]) -> float:
    today = datetime.now(ZoneInfo("America/New_York")).date()
    total = 0.0
    for row in _ledger_events(raw):
        if str(row.get("event") or "") not in {"entry_submitted", "entry_dry_run"}:
            continue
        if _event_date_et(row) != today:
            continue
        total += _event_debit_premium(row)
    return round(total, 2)


def _blacklisted_underlyings(raw: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    src = raw.get("symbol_blacklist")
    if isinstance(src, str):
        values = re.split(r"[\s,;锛岋紱]+", src)
    elif isinstance(src, list):
        values = [str(x) for x in src]
    else:
        values = []
    for item in values:
        sym = str(item or "").strip().upper()
        if not sym:
            continue
        if "." not in sym:
            sym = f"{sym}.US"
        out.add(sym)
    return out


def _active_event_blackout(symbol: str, raw: dict[str, Any]) -> dict[str, Any] | None:
    rows = raw.get("event_blackouts")
    if not isinstance(rows, list):
        return None
    today = datetime.now(ZoneInfo("America/New_York")).date()
    sym = str(symbol or "").strip().upper()
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_symbol = str(row.get("symbol") or "").strip().upper()
        if raw_symbol and "." not in raw_symbol:
            raw_symbol = f"{raw_symbol}.US"
        if raw_symbol and raw_symbol != sym:
            continue
        try:
            start = date.fromisoformat(str(row.get("start") or row.get("from") or "")[:10])
            end = date.fromisoformat(str(row.get("end") or row.get("to") or "")[:10])
        except Exception:
            continue
        if start <= today <= end:
            return {
                "symbol": raw_symbol or sym,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "reason": str(row.get("reason") or "event_blackout"),
            }
    return None


def _fetch_underlying_trend_exit(underlying: str, raw: dict[str, Any]) -> dict[str, Any] | None:
    strat = raw.get("strategy") if isinstance(raw.get("strategy"), dict) else {}
    ma = int(strat.get("trend_exit_below_ma") or 0)
    confirm = max(1, int(strat.get("trend_exit_confirm_bars") or 1))
    if ma <= 0:
        return None
    bars, source = _fetch_bars(underlying, max(ma + confirm + 5, 80), "1d")
    if len(bars) < ma + confirm:
        return {"checked": False, "reason": "insufficient_bars", "bars": len(bars), "source": source}
    closes = [b.close for b in bars if b.close > 0]
    if len(closes) < ma + confirm:
        return {"checked": False, "reason": "insufficient_closes", "bars": len(closes), "source": source}
    broken = True
    snapshots: list[dict[str, Any]] = []
    for offset in range(confirm):
        idx = len(closes) - 1 - offset
        window = closes[idx - ma + 1 : idx + 1]
        avg = sum(window) / len(window)
        close = closes[idx]
        snapshots.append({"close": round(close, 4), "ma": round(avg, 4), "below": close < avg})
        if close >= avg:
            broken = False
    return {
        "checked": True,
        "exit": broken,
        "ma": ma,
        "confirm_bars": confirm,
        "source": source,
        "snapshots": list(reversed(snapshots)),
    }


def _evaluate_managed_position(row: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    strat = raw.get("strategy") if isinstance(raw.get("strategy"), dict) else {}
    sym = str(row.get("symbol") or "").strip().upper()
    cost = _num(row.get("cost_price"), 0.0)
    cur = _num(row.get("current_price"), 0.0)
    qty = _option_position_qty(row)
    parsed = _parse_occ_option_symbol(sym)
    ret = ((cur - cost) / cost) if cost > 0 and cur > 0 else None
    reasons: list[str] = []
    exit_signal = False
    if qty <= 0:
        return {
            "symbol": sym,
            **parsed,
            "quantity": qty,
            "cost_price": cost,
            "current_price": cur,
            "premium": round(_position_premium(row), 2),
            "return_pct": ret,
            "exit_signal": False,
            "reasons": ["non_long_option_position_not_auto_managed"],
            "management_block": "non_long_option_position",
        }
    if ret is not None and ret >= _num(strat.get("take_profit_pct"), 0.8):
        exit_signal = True
        reasons.append("take_profit")
    if ret is not None and ret <= -_num(strat.get("stop_loss_pct"), 0.45):
        exit_signal = True
        reasons.append("stop_loss")
    dte_exit_days = int(strat.get("dte_exit_days") or 0)
    if dte_exit_days > 0 and parsed.get("dte") is not None and int(parsed["dte"]) <= dte_exit_days:
        exit_signal = True
        reasons.append("dte_exit")
    trend_exit = None
    if parsed.get("underlying"):
        trend_exit = _fetch_underlying_trend_exit(str(parsed.get("underlying")), raw)
        if isinstance(trend_exit, dict) and trend_exit.get("exit"):
            exit_signal = True
            reasons.append("trend_break")
    return {
        "symbol": sym,
        **parsed,
        "quantity": qty,
        "cost_price": cost,
        "current_price": cur,
        "premium": round(_position_premium(row), 2),
        "return_pct": ret,
        "exit_signal": exit_signal,
        "reasons": reasons,
        "trend_exit": trend_exit,
    }


def _spread_leg_mark(row: dict[str, Any], entry_side: str) -> dict[str, Any]:
    side = str(entry_side or "").strip().lower()
    current = _num(row.get("current_price"), 0.0)
    bid = _num(row.get("bid") or row.get("best_bid") or row.get("bid_price"), 0.0)
    ask = _num(row.get("ask") or row.get("best_ask") or row.get("ask_price"), 0.0)
    if side == "buy":
        close_side = "sell"
        close_price = bid if bid > 0 else current
        value_sign = 1.0
    else:
        close_side = "buy"
        close_price = ask if ask > 0 else current
        value_sign = -1.0
    return {
        "close_side": close_side,
        "close_price": close_price if close_price > 0 else None,
        "value_sign": value_sign,
        "bid": bid or None,
        "ask": ask or None,
        "last": current or None,
    }


def _build_managed_spread_positions(managed_rows: list[dict[str, Any]], raw: dict[str, Any]) -> list[dict[str, Any]]:
    by_symbol = {str(row.get("symbol") or "").strip().upper(): row for row in managed_rows if isinstance(row, dict)}
    spreads: list[dict[str, Any]] = []
    for group in _managed_spread_groups(raw).values():
        legs = group.get("legs") if isinstance(group.get("legs"), list) else []
        if bool(group.get("partial_entry")):
            spreads.append(
                {
                    "position_group_id": group.get("position_group_id"),
                    "structure": "call_debit_spread",
                    "underlying": group.get("underlying"),
                    "legs": [],
                    "exit_signal": False,
                    "reasons": ["spread_entry_partial_submitted"],
                    "management_block": "spread_entry_partial_submitted",
                }
            )
            continue
        if len(legs) < 2:
            continue
        eval_legs: list[dict[str, Any]] = []
        missing: list[str] = []
        qty_values: list[float] = []
        entry_debit = _num(group.get("entry_net_debit"), 0.0)
        current_value = 0.0
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            sym = str(leg.get("symbol") or "").strip().upper()
            broker_row = by_symbol.get(sym)
            if broker_row is None:
                missing.append(sym)
                continue
            side = str(leg.get("side") or "").strip().lower()
            mark = _spread_leg_mark(broker_row, side)
            qty = abs(_option_position_qty(broker_row))
            qty_values.append(qty)
            close_price = _num(mark.get("close_price"), 0.0)
            current_value += float(mark["value_sign"]) * close_price
            parsed = _parse_occ_option_symbol(sym)
            eval_legs.append(
                {
                    "symbol": sym,
                    **parsed,
                    "entry_side": side,
                    "close_side": mark["close_side"],
                    "quantity": qty,
                    "entry_price": _num(leg.get("price"), 0.0),
                    "current_price": _num(broker_row.get("current_price"), 0.0),
                    "close_price": mark.get("close_price"),
                    "bid": mark.get("bid"),
                    "ask": mark.get("ask"),
                    "last": mark.get("last"),
                }
            )
        if missing or len(eval_legs) != len(legs):
            spreads.append(
                {
                    "position_group_id": group.get("position_group_id"),
                    "structure": "call_debit_spread",
                    "underlying": group.get("underlying"),
                    "legs": eval_legs,
                    "missing_legs": missing,
                    "exit_signal": False,
                    "reasons": ["spread_incomplete_broker_positions"],
                    "management_block": "spread_incomplete_broker_positions",
                }
            )
            continue
        qty = min([x for x in qty_values if x > 0], default=0.0)
        entry_value = entry_debit
        if entry_value <= 0:
            entry_value = _event_debit_premium(group.get("entry_event") if isinstance(group.get("entry_event"), dict) else {}) / 100.0
        ret = ((current_value - entry_value) / entry_value) if entry_value > 0 else None
        parsed_first = _parse_occ_option_symbol(str(eval_legs[0].get("symbol") or ""))
        trend_exit = None
        reasons: list[str] = []
        exit_signal = False
        strat = raw.get("strategy") if isinstance(raw.get("strategy"), dict) else {}
        if ret is not None and ret >= _num(strat.get("take_profit_pct"), 0.8):
            exit_signal = True
            reasons.append("spread_take_profit")
        if ret is not None and ret <= -_num(strat.get("stop_loss_pct"), 0.45):
            exit_signal = True
            reasons.append("spread_stop_loss")
        dte_values = [int(x.get("dte")) for x in eval_legs if x.get("dte") is not None]
        dte = min(dte_values) if dte_values else parsed_first.get("dte")
        dte_exit_days = int(strat.get("dte_exit_days") or 0)
        if dte_exit_days > 0 and dte is not None and int(dte) <= dte_exit_days:
            exit_signal = True
            reasons.append("spread_dte_exit")
        underlying = str(group.get("underlying") or parsed_first.get("underlying") or "").strip().upper()
        if underlying:
            trend_exit = _fetch_underlying_trend_exit(underlying, raw)
            if isinstance(trend_exit, dict) and trend_exit.get("exit"):
                exit_signal = True
                reasons.append("spread_trend_break")
        spreads.append(
            {
                "symbol": str(group.get("option_symbol") or eval_legs[0].get("symbol") or ""),
                "position_group_id": group.get("position_group_id"),
                "structure": "call_debit_spread",
                "underlying": underlying,
                "opened_at": group.get("opened_at"),
                "quantity": qty,
                "contracts": qty,
                "entry_net_debit": round(entry_value, 4) if entry_value else None,
                "current_net_value": round(current_value, 4),
                "premium": round(entry_value * qty * 100.0, 2) if entry_value else None,
                "return_pct": ret,
                "dte": dte,
                "legs": eval_legs,
                "exit_signal": exit_signal,
                "reasons": reasons,
                "trend_exit": trend_exit,
            }
        )
    return spreads


def _submit_exit_order(row: dict[str, Any], raw: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    if str(row.get("structure") or "") == "call_debit_spread":
        return _submit_spread_exit_order(row, raw, reasons)
    sym = str(row.get("symbol") or "").strip().upper()
    qty = max(0, int(abs(_option_position_qty(row))))
    if not sym or qty <= 0:
        return {"ok": False, "error": "invalid_exit_position"}
    price = _num(row.get("current_price"), 0.0)
    payload = {
        "symbol": sym,
        "side": "sell",
        "contracts": qty,
        "price": price if price > 0 else None,
        "confirmation_token": raw.get("confirmation_token"),
    }
    if _effective_account_id(raw):
        payload["account_id"] = _effective_account_id(raw)
    event_base = {
        "at": datetime.now(timezone.utc).isoformat(),
        "underlying": _option_symbol_underlying(sym),
        "option_symbol": sym,
        "symbol": sym,
        "contracts": qty,
        "price": payload["price"],
        "reasons": list(reasons),
        **_worker_context(raw),
    }
    if bool(raw.get("dry_run", True)) or not bool(raw.get("auto_submit_orders", False)):
        event = {"event": "exit_dry_run", **event_base, "order_preview": payload}
        _append_jsonl(LEDGER_FILE, event)
        return {"ok": True, "dry_run": True, "order_preview": payload}
    guard_ok, guard_reason = _live_submit_guard(raw)
    if not guard_ok:
        event = {"event": "exit_blocked", **event_base, "order_preview": payload, "reason": guard_reason}
        _append_jsonl(LEDGER_FILE, event)
        return {"ok": False, "blocked": True, "error": guard_reason, "order_preview": payload}
    ok, res = _api_post_json("/options/order", payload, timeout=API_TIMEOUT)
    event = {"event": "exit_submitted" if ok else "exit_failed", **event_base, "response": res}
    _append_jsonl(LEDGER_FILE, event)
    return {"ok": ok, "dry_run": False, "response": res}


def _submit_spread_exit_order(row: dict[str, Any], raw: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    gid = str(row.get("position_group_id") or "").strip()
    legs_src = row.get("legs") if isinstance(row.get("legs"), list) else []
    qty = max(0, int(abs(_num(row.get("quantity") or row.get("contracts"), 0.0))))
    if not gid or qty <= 0 or len(legs_src) < 2:
        return {"ok": False, "error": "invalid_spread_exit_position"}
    close_legs: list[dict[str, Any]] = []
    for leg in legs_src:
        if not isinstance(leg, dict):
            continue
        sym = str(leg.get("symbol") or "").strip().upper()
        side = str(leg.get("close_side") or "").strip().lower()
        price = _num(leg.get("close_price"), 0.0)
        if not sym or side not in {"buy", "sell"}:
            continue
        close_legs.append({"symbol": sym, "side": side, "contracts": qty, "price": price if price > 0 else None})
    # Close short legs first, then sell long legs. Current multi-leg implementation
    # submits sequentially, so this avoids leaving an uncovered short option.
    close_legs.sort(key=lambda x: 0 if x.get("side") == "buy" else 1)
    if len(close_legs) < 2:
        return {"ok": False, "error": "invalid_spread_exit_legs"}
    payload = {"legs": close_legs, "confirmation_token": raw.get("confirmation_token")}
    if _effective_account_id(raw):
        payload["account_id"] = _effective_account_id(raw)
    event_base = {
        "at": datetime.now(timezone.utc).isoformat(),
        "underlying": row.get("underlying"),
        "option_symbol": row.get("symbol"),
        "symbol": row.get("symbol"),
        "position_group_id": gid,
        "structure": "call_debit_spread",
        "contracts": qty,
        "legs": close_legs,
        "entry_net_debit": row.get("entry_net_debit"),
        "current_net_value": row.get("current_net_value"),
        "return_pct": row.get("return_pct"),
        "reasons": list(reasons),
        **_worker_context(raw),
    }
    if bool(raw.get("dry_run", True)) or not bool(raw.get("auto_submit_orders", False)):
        event = {"event": "exit_dry_run", **event_base, "order_preview": payload}
        _append_jsonl(LEDGER_FILE, event)
        return {"ok": True, "dry_run": True, "order_preview": payload}
    guard_ok, guard_reason = _live_submit_guard(raw, structure="call_debit_spread")
    if not guard_ok:
        event = {"event": "exit_blocked", **event_base, "order_preview": payload, "reason": guard_reason}
        _append_jsonl(LEDGER_FILE, event)
        return {"ok": False, "blocked": True, "error": guard_reason, "order_preview": payload}
    ok, res = _api_post_json("/options/order", payload, timeout=API_TIMEOUT)
    event = {"event": "exit_submitted" if ok else "exit_failed", **event_base, "response": res}
    _append_jsonl(LEDGER_FILE, event)
    return {"ok": ok, "dry_run": False, "response": res}


def _submit_entry_order(symbol: str, contract_result: dict[str, Any], raw: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any]:
    risk = raw.get("risk") if isinstance(raw.get("risk"), dict) else {}
    structure = str(contract_result.get("structure") or "long_call")
    contract = contract_result.get("contract") if isinstance(contract_result.get("contract"), dict) else contract_result
    max_contracts = max(1, int(risk.get("max_contracts_per_order") or raw.get("contracts") or 1))
    contracts = min(max(1, int(raw.get("contracts") or 1)), max_contracts)
    legs = contract_result.get("legs") if isinstance(contract_result.get("legs"), list) else []
    position_group_id = ""
    if structure == "call_debit_spread" and legs:
        normalized_legs = []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            normalized_legs.append(
                {
                    "symbol": str(leg.get("symbol") or "").strip().upper(),
                    "side": str(leg.get("side") or "").strip().lower(),
                    "contracts": contracts,
                    "price": _num(leg.get("price"), 0.0) or None,
                }
            )
        payload = {"legs": normalized_legs, "confirmation_token": raw.get("confirmation_token")}
        price = _num(contract_result.get("net_debit"), 0.0)
        option_symbol = str((normalized_legs[0] if normalized_legs else {}).get("symbol") or "").strip().upper()
        group_raw = "|".join([datetime.now(timezone.utc).isoformat(), symbol, *[str(x.get("symbol") or "") for x in normalized_legs]])
        position_group_id = f"spread-{hashlib.sha1(group_raw.encode('utf-8', errors='ignore')).hexdigest()[:12]}"
        payload["position_group_id"] = position_group_id
    else:
        ask = _num(contract.get("ask"), 0.0)
        last = _num(contract.get("last"), 0.0)
        price = ask if ask > 0 else last
        option_symbol = str(contract.get("symbol") or "").strip().upper()
        payload = {
            "symbol": option_symbol,
            "side": "buy",
            "contracts": contracts,
            "price": price if price > 0 else None,
            "confirmation_token": raw.get("confirmation_token"),
        }
    if _effective_account_id(raw):
        payload["account_id"] = _effective_account_id(raw)
    if bool(raw.get("dry_run", True)) or not bool(raw.get("auto_submit_orders", False)):
        event = {
            "event": "entry_dry_run",
            "at": datetime.now(timezone.utc).isoformat(),
            "underlying": symbol,
            "option_symbol": option_symbol,
            "contracts": contracts,
            "price": price if price > 0 else None,
            "structure": structure,
            "position_group_id": position_group_id or None,
            "legs": payload.get("legs"),
            "net_debit": contract_result.get("net_debit"),
            "signal": signal,
            **_worker_context(raw),
        }
        _append_jsonl(LEDGER_FILE, event)
        return {"ok": True, "dry_run": True, "order_preview": payload}
    guard_ok, guard_reason = _live_submit_guard(raw, structure=structure)
    if not guard_ok:
        event = {
            "event": "entry_blocked",
            "at": datetime.now(timezone.utc).isoformat(),
            "underlying": symbol,
            "option_symbol": option_symbol,
            "contracts": contracts,
            "price": price if price > 0 else None,
            "structure": structure,
            "position_group_id": position_group_id or None,
            "legs": payload.get("legs"),
            "net_debit": contract_result.get("net_debit"),
            "reason": guard_reason,
            "signal": signal,
            **_worker_context(raw),
        }
        _append_jsonl(LEDGER_FILE, event)
        return {"ok": False, "blocked": True, "error": guard_reason, "order_preview": payload}
    order_premium = max(0.0, float(price or 0.0) * contracts * 100.0)
    gate_positions, gate_pos_err, _gate_positions_source = _fetch_option_positions(raw)
    if gate_pos_err and bool(_account_risk_config(raw).get("fail_closed_for_live", True)):
        reason = f"positions_unavailable:{gate_pos_err}"
        event = {
            "event": "entry_blocked",
            "at": datetime.now(timezone.utc).isoformat(),
            "underlying": symbol,
            "option_symbol": option_symbol,
            "contracts": contracts,
            "price": price if price > 0 else None,
            "structure": structure,
            "position_group_id": position_group_id or None,
            "legs": payload.get("legs"),
            "net_debit": contract_result.get("net_debit"),
            "reason": reason,
            "signal": signal,
            **_worker_context(raw),
        }
        _append_jsonl(LEDGER_FILE, event)
        return {"ok": False, "blocked": True, "error": reason, "order_preview": payload}
    account_gate = _account_level_risk_gate(raw, positions=gate_positions, order_premium=order_premium, dry_run=False)
    if bool(account_gate.get("blocked")):
        blocks = account_gate.get("blocks") if isinstance(account_gate.get("blocks"), list) else []
        reason = "account_risk:" + ",".join(str(x) for x in blocks if str(x)) if blocks else "account_risk"
        event = {
            "event": "entry_blocked",
            "at": datetime.now(timezone.utc).isoformat(),
            "underlying": symbol,
            "option_symbol": option_symbol,
            "contracts": contracts,
            "price": price if price > 0 else None,
            "structure": structure,
            "position_group_id": position_group_id or None,
            "legs": payload.get("legs"),
            "net_debit": contract_result.get("net_debit"),
            "reason": reason,
            "account_risk_gate": account_gate,
            "signal": signal,
            **_worker_context(raw),
        }
        _append_jsonl(LEDGER_FILE, event)
        return {"ok": False, "blocked": True, "error": reason, "account_risk_gate": account_gate, "order_preview": payload}
    ok, res = _api_post_json("/options/order", payload, timeout=API_TIMEOUT)
    event_name = "entry_submitted" if ok else "entry_failed"
    if ok and structure == "call_debit_spread":
        submitted_legs = []
        if isinstance(res.get("result"), dict) and isinstance(res["result"].get("legs_submitted"), list):
            submitted_legs = res["result"]["legs_submitted"]
        expected_legs = payload.get("legs") if isinstance(payload.get("legs"), list) else []
        if len(submitted_legs) != len(expected_legs):
            event_name = "entry_partial_submitted"
    event = {
        "event": event_name,
        "at": datetime.now(timezone.utc).isoformat(),
        "underlying": symbol,
        "option_symbol": option_symbol,
        "contracts": contracts,
        "price": price if price > 0 else None,
        "structure": structure,
        "position_group_id": position_group_id or None,
        "legs": payload.get("legs"),
        "net_debit": contract_result.get("net_debit"),
        "response": res,
        "signal": signal,
        **_worker_context(raw),
    }
    _append_jsonl(LEDGER_FILE, event)
    return {"ok": ok, "dry_run": False, "response": res}


def _risk_block_for_entry(
    symbol: str,
    contract_result: dict[str, Any],
    raw: dict[str, Any],
    positions: list[dict[str, Any]],
) -> str | None:
    risk = raw.get("risk") if isinstance(raw.get("risk"), dict) else {}
    structure = str(contract_result.get("structure") or "long_call")
    contract = contract_result.get("contract") if isinstance(contract_result.get("contract"), dict) else contract_result
    contracts = max(1, int(raw.get("contracts") or 1))
    price = _num(contract_result.get("net_debit"), 0.0) if structure == "call_debit_spread" else _num(contract.get("ask") or contract.get("last"), 0.0)
    order_premium = contracts * price * 100.0 if price > 0 else 0.0
    account_gate = _account_level_risk_gate(raw, positions=positions, order_premium=order_premium, dry_run=not _is_live_auto_submit(raw))
    if bool(account_gate.get("blocked")):
        blocks = account_gate.get("blocks") if isinstance(account_gate.get("blocks"), list) else []
        return "account_risk:" + ",".join(str(x) for x in blocks if str(x)) if blocks else "account_risk"
    max_order = _num(risk.get("max_premium_per_order"), 0.0)
    if max_order > 0 and order_premium > max_order:
        return f"max_premium_per_order:{round(order_premium, 2)}>{max_order}"
    max_daily = _num(risk.get("max_new_premium_per_day"), 0.0)
    if max_daily > 0:
        today_premium = _new_premium_today(raw)
        if today_premium + order_premium > max_daily:
            return f"max_new_premium_per_day:{round(today_premium + order_premium, 2)}>{max_daily}"
    open_contracts = sum(abs(_option_position_qty(x)) for x in positions)
    max_open_contracts = _num(risk.get("max_open_contracts"), 0.0)
    if max_open_contracts > 0 and open_contracts + contracts > max_open_contracts:
        return f"max_open_contracts:{open_contracts + contracts}>{max_open_contracts}"
    total_premium = sum(_position_premium(x) for x in positions)
    max_total = _num(risk.get("max_total_option_premium"), 0.0)
    if max_total > 0 and total_premium + order_premium > max_total:
        return f"max_total_option_premium:{round(total_premium + order_premium, 2)}>{max_total}"
    symbol_positions = [x for x in positions if _option_symbol_underlying(str(x.get("symbol") or "")) == symbol.upper()]
    symbol_premium = sum(_position_premium(x) for x in symbol_positions)
    max_symbol = _num(risk.get("max_premium_per_symbol"), 0.0)
    if max_symbol > 0 and symbol_premium + order_premium > max_symbol:
        return f"max_premium_per_symbol:{round(symbol_premium + order_premium, 2)}>{max_symbol}"
    return None


def _process_symbol(
    symbol: str,
    raw: dict[str, Any],
    positions: list[dict[str, Any]],
    positions_by_underlying: dict[str, list[dict[str, Any]]],
    entry_block_reason: str | None = None,
) -> dict[str, Any]:
    days = max(60, min(3650, int(raw.get("history_days") or 260)))
    kline = str(raw.get("kline") or "1d")
    bars, bars_source = _fetch_bars(symbol, days, kline)
    quote, quote_source = _fetch_quote(symbol)
    signal = _trend_signal(symbol, bars, raw, quote)
    if symbol.upper() in _blacklisted_underlyings(raw):
        signal = {**signal, "action": "skip_blacklisted", "reason": "symbol_blacklisted"}
    blackout = _active_event_blackout(symbol, raw)
    if blackout:
        signal = {**signal, "action": "skip_event_blackout", "reason": "event_blackout", "event_blackout": blackout}
    existing = positions_by_underlying.get(symbol.upper(), [])
    if existing and bool(raw.get("skip_existing_broker_positions", True)):
        signal = {
            **signal,
            "action": "skip_existing_position",
            "reason": "broker_already_has_option_position_for_underlying",
            "existing_option_positions": [x.get("symbol") for x in existing],
        }
    contract_result: dict[str, Any] | None = None
    order_result: dict[str, Any] | None = None
    if signal.get("action") == "candidate_long_call":
        if entry_block_reason:
            signal["action"] = "watch"
            signal["reason"] = f"entry_block:{entry_block_reason}"
            signal["entry_block"] = entry_block_reason
        else:
            spot = _num(signal.get("last"), 0.0)
            if spot > 0:
                contract_result = _choose_option_contract(symbol, spot, raw)
                if bool(contract_result.get("ok")) and isinstance(contract_result.get("contract"), dict):
                    signal["selected_structure"] = contract_result.get("structure") or "long_call"
                    signal["selected_contract"] = contract_result["contract"]
                    if contract_result.get("legs"):
                        signal["selected_legs"] = contract_result.get("legs")
                        signal["net_debit"] = contract_result.get("net_debit")
                    risk_block = _risk_block_for_entry(symbol, contract_result, raw, positions)
                    if risk_block:
                        signal["action"] = "watch"
                        signal["reason"] = f"risk_block:{risk_block}"
                        signal["risk_block"] = risk_block
                        order_result = None
                    elif bool(raw.get("auto_submit_orders", False)) or bool(raw.get("dry_run", True)):
                        order_result = _submit_entry_order(symbol, contract_result, raw, signal)
                else:
                    signal["action"] = "watch"
                    signal["reason"] = str(contract_result.get("reason") or "contract_selection_failed")
    _append_decision(
        symbol,
        action={"action": signal.get("action"), "reason": signal.get("reason"), "signal": signal, "order_result": order_result},
        logs=[{"message": str(signal.get("action") or "watch"), "extra": {"bars_source": bars_source, "quote_source": quote_source}}],
        raw=raw,
    )
    return {
        "symbol": symbol,
        "status": "ok",
        "bars": len(bars),
        "bars_source": bars_source,
        "quote_source": quote_source,
        "signal": signal,
        "contract_result": contract_result,
        "order_result": order_result,
    }


def _next_scan_hint(raw: dict[str, Any]) -> dict[str, Any]:
    tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    times = []
    for key in ("scan_time_hhmm_et", "second_scan_time_hhmm_et"):
        s = str(raw.get(key) or "").strip()
        if not s:
            continue
        try:
            h, m = [int(x) for x in s.split(":", 1)]
            times.append(dt_time(max(0, min(23, h)), max(0, min(59, m))))
        except Exception:
            continue
    if not times:
        return {"now_et": now.strftime("%Y-%m-%d %H:%M"), "configured": []}
    candidates = []
    for t in times:
        dt = datetime.combine(now.date(), t, tz)
        if dt <= now:
            dt += timedelta(days=1)
        candidates.append(dt)
    nxt = min(candidates)
    return {
        "now_et": now.strftime("%Y-%m-%d %H:%M"),
        "configured": [t.strftime("%H:%M") for t in sorted(times)],
        "next_scan_et": nxt.strftime("%Y-%m-%d %H:%M"),
        "seconds_until_next": int((nxt - now).total_seconds()),
    }


def _run_once(raw: dict[str, Any]) -> dict[str, Any]:
    positions, pos_err, positions_source = _fetch_option_positions(raw)
    reconcile_events: list[dict[str, Any]] = []
    if not pos_err:
        reconcile_events = _reconcile_missing_managed_positions(positions, raw)
    classified = _classify_positions(positions, raw)
    unmanaged = classified["unmanaged"]
    managed = classified["managed"]
    spread_positions = _build_managed_spread_positions(managed, raw)
    spread_leg_symbols = {
        str(leg.get("symbol") or "").strip().upper()
        for spread in spread_positions
        for leg in (spread.get("legs") if isinstance(spread.get("legs"), list) else [])
        if isinstance(leg, dict)
    }
    managed_single = [row for row in managed if str(row.get("symbol") or "").strip().upper() not in spread_leg_symbols]
    positions_by_underlying: dict[str, list[dict[str, Any]]] = {}
    for row in positions:
        und = _option_symbol_underlying(str(row.get("symbol") or ""))
        if und:
            positions_by_underlying.setdefault(und, []).append(row)

    managed_eval = []
    exit_actions = []
    for spread in spread_positions:
        if spread.get("exit_signal"):
            exit_result = _submit_spread_exit_order(spread, raw, [str(x) for x in spread.get("reasons", [])])
            spread["exit_order_result"] = exit_result
            exit_actions.append({"symbol": spread.get("symbol"), "position_group_id": spread.get("position_group_id"), "reasons": spread.get("reasons"), "result": exit_result})
            _append_decision(
                str(spread.get("underlying") or spread.get("symbol") or ""),
                action={"action": "managed_spread_exit_signal", "detail": spread},
                logs=[{"message": "managed_spread_exit_signal", "extra": spread}],
                raw=raw,
            )
        managed_eval.append(spread)
    for row in managed_single:
        evaluated = _evaluate_managed_position(row, raw)
        if evaluated.get("exit_signal"):
            exit_result = _submit_exit_order(row, raw, [str(x) for x in evaluated.get("reasons", [])])
            evaluated["exit_order_result"] = exit_result
            exit_actions.append({"symbol": evaluated.get("symbol"), "reasons": evaluated.get("reasons"), "result": exit_result})
            _append_decision(
                str(evaluated.get("underlying") or evaluated.get("symbol") or ""),
                action={"action": "managed_position_exit_signal", "detail": evaluated},
                logs=[{"message": "managed_position_exit_signal", "extra": evaluated}],
                raw=raw,
            )
        managed_eval.append(evaluated)
    pool = _normalize_stock_pool(raw)
    live_guard_ok, live_guard_reason = _live_submit_guard(raw, structure=str((raw.get("strategy") or {}).get("mode") if isinstance(raw.get("strategy"), dict) else "long_call"))
    account_gate = _account_level_risk_gate(raw, positions=positions, order_premium=0.0, dry_run=not _is_live_auto_submit(raw))
    entry_block_reason = None
    if pos_err:
        entry_block_reason = f"positions_unavailable:{pos_err}"
    elif _is_live_auto_submit(raw) and not live_guard_ok:
        entry_block_reason = live_guard_reason
    elif bool(account_gate.get("blocked")):
        blocks = account_gate.get("blocks") if isinstance(account_gate.get("blocks"), list) else []
        entry_block_reason = "account_risk:" + ",".join(str(x) for x in blocks if str(x)) if blocks else "account_risk"
    summaries = [_process_symbol(sym, raw, positions, positions_by_underlying, entry_block_reason=entry_block_reason) for sym in pool]
    candidates = [x for x in summaries if str(((x.get("signal") or {}).get("action")) or "") == "candidate_long_call"]
    warnings: list[str] = []
    if pos_err:
        warnings.append(f"positions_check_failed:{pos_err}")
    if reconcile_events:
        warnings.append(f"managed_positions_missing_marked_closed:{len(reconcile_events)}")
    if any(bool(x.get("partial_entry")) for x in spread_positions):
        warnings.append("spread_partial_entry_requires_manual_review")
    if any(str(x.get("management_block") or "") == "spread_incomplete_broker_positions" for x in spread_positions):
        warnings.append("spread_incomplete_broker_positions")
    if entry_block_reason:
        warnings.append(f"entry_blocked:{entry_block_reason}")
    if bool(account_gate.get("blocks")):
        warnings.append("account_risk_gate:" + ",".join(str(x) for x in account_gate.get("blocks", [])))
    if unmanaged:
        warnings.append(f"unmanaged_option_positions_detected:{len(unmanaged)}")
    if any(bool(x.get("partial_entry")) for x in spread_positions):
        warnings.append("spread_partial_entry_requires_manual_review")
    if any(str(x.get("management_block") or "") == "spread_incomplete_broker_positions" for x in spread_positions):
        warnings.append("spread_incomplete_broker_positions")
    if any(bool(x.get("exit_signal")) for x in managed_eval):
        warnings.append("managed_exit_signal_detected")
    return {
        "status": "ok",
        "mode": "stock_options_swing",
        "stock_pool": pool,
        "symbols_state": summaries,
        "candidate_count": len(candidates),
        "managed_positions": managed_eval,
        "managed_spread_positions": spread_positions,
        "unmanaged_positions": unmanaged,
        "exit_actions": exit_actions,
        "reconcile_events": reconcile_events,
        "symbol_blacklist": sorted(_blacklisted_underlyings(raw)),
        "event_blackouts": raw.get("event_blackouts") if isinstance(raw.get("event_blackouts"), list) else [],
        "risk": raw.get("risk") if isinstance(raw.get("risk"), dict) else {},
        "account_risk": _account_risk_config(raw),
        "account_risk_gate": account_gate,
        "warnings": warnings,
        "positions_error": pos_err,
        "positions_source": positions_source,
        "managed_positions_only": bool(raw.get("managed_positions_only", True)),
        "auto_submit_orders": bool(raw.get("auto_submit_orders", False)),
        "dry_run": bool(raw.get("dry_run", True)),
        "live_submit_guard": {"ok": live_guard_ok, "reason": live_guard_reason},
        "new_premium_today": _new_premium_today(raw),
        "scheduler": _next_scan_hint(raw),
        **_worker_context(raw),
    }


def build_position_management_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    """Build a runtime-safe positions view without scanning symbols or submitting orders."""
    positions, pos_err, positions_source = _fetch_option_positions(raw)
    classified = _classify_positions(positions, raw)
    unmanaged = classified["unmanaged"]
    managed = classified["managed"]
    spread_positions = _build_managed_spread_positions(managed, raw)
    spread_leg_symbols = {
        str(leg.get("symbol") or "").strip().upper()
        for spread in spread_positions
        for leg in (spread.get("legs") if isinstance(spread.get("legs"), list) else [])
        if isinstance(leg, dict)
    }
    managed_single = [row for row in managed if str(row.get("symbol") or "").strip().upper() not in spread_leg_symbols]

    managed_eval = [*spread_positions, *[_evaluate_managed_position(row, raw) for row in managed_single]]
    warnings: list[str] = []
    if pos_err:
        warnings.append(f"positions_check_failed:{pos_err}")
    if unmanaged:
        warnings.append(f"unmanaged_option_positions_detected:{len(unmanaged)}")
    if any(bool(x.get("exit_signal")) for x in managed_eval):
        warnings.append("managed_exit_signal_detected")
    strategy = raw.get("strategy") if isinstance(raw.get("strategy"), dict) else {}
    live_guard_ok, live_guard_reason = _live_submit_guard(raw, structure=str(strategy.get("mode") or "long_call"))
    account_gate = _account_level_risk_gate(raw, positions=positions, order_premium=0.0, dry_run=not _is_live_auto_submit(raw))
    if bool(account_gate.get("blocks")):
        warnings.append("account_risk_gate:" + ",".join(str(x) for x in account_gate.get("blocks", [])))
    return {
        "status": "ok",
        "mode": "stock_options_swing",
        "position_snapshot_only": True,
        "position_snapshot_refreshed_at": datetime.now(timezone.utc).isoformat(),
        "stock_pool": _normalize_stock_pool(raw),
        "managed_positions": managed_eval,
        "managed_spread_positions": spread_positions,
        "unmanaged_positions": unmanaged,
        "exit_actions": [],
        "symbol_blacklist": sorted(_blacklisted_underlyings(raw)),
        "event_blackouts": raw.get("event_blackouts") if isinstance(raw.get("event_blackouts"), list) else [],
        "risk": raw.get("risk") if isinstance(raw.get("risk"), dict) else {},
        "account_risk": _account_risk_config(raw),
        "account_risk_gate": account_gate,
        "warnings": warnings,
        "positions_error": pos_err,
        "positions_source": positions_source,
        "managed_positions_only": bool(raw.get("managed_positions_only", True)),
        "auto_submit_orders": bool(raw.get("auto_submit_orders", False)),
        "dry_run": bool(raw.get("dry_run", True)),
        "live_submit_guard": {"ok": live_guard_ok, "reason": live_guard_reason},
        "new_premium_today": _new_premium_today(raw),
        **_worker_context(raw),
    }


def run_loop(config_path: str = CONFIG_FILE) -> None:
    global _API_BASE_URL, _API_BEARER_TOKEN, _API_KEY, _API_ACCOUNT_ID, _API_BROKER_PROVIDER
    os.makedirs(DATA_DIR, exist_ok=True)
    _write_pid()
    boot_dt = datetime.now(timezone.utc)
    raw = _load_config(config_path)
    _API_BASE_URL = str(raw.get("api_base_url") or _API_BASE_URL).strip().rstrip("/")
    _API_BEARER_TOKEN = _resolve_api_bearer(raw)
    _API_KEY = _resolve_api_key(raw, config_path)
    if not _API_ACCOUNT_ID and str(raw.get("account_id") or "").strip():
        _API_ACCOUNT_ID = str(raw.get("account_id") or "").strip()
    if not _API_BROKER_PROVIDER and str(raw.get("broker_provider") or "").strip():
        _API_BROKER_PROVIDER = str(raw.get("broker_provider") or "").strip().lower()
    _append_decision(
        ",".join(_normalize_stock_pool(raw)),
        {
            "action": "stock_options_swing_worker_started",
            "config_path": config_path,
            "dry_run": bool(raw.get("dry_run", True)),
            "auto_submit_orders": bool(raw.get("auto_submit_orders", False)),
            "poll_seconds": max(300, int(raw.get("poll_seconds") or 3600)),
            **_worker_context(raw),
        },
        logs=[{"message": "stock_options_swing_worker_started", "extra": {"boot_at": boot_dt.isoformat()}}],
        raw=raw,
    )
    while not _should_stop():
        loop_started = datetime.now(timezone.utc).isoformat()
        try:
            fresh = _load_config(config_path)
            _API_BASE_URL = str(fresh.get("api_base_url") or _API_BASE_URL).strip().rstrip("/")
            _API_BEARER_TOKEN = _resolve_api_bearer(fresh)
            _API_KEY = _resolve_api_key(fresh, config_path)
            matches_config_context, config_context_reason = _config_matches_worker_context(fresh)
            if matches_config_context:
                raw = fresh
            else:
                _append_decision(
                    ",".join(_normalize_stock_pool(raw)),
                    {
                        "action": "config_account_switch_ignored",
                        "reason": config_context_reason,
                        "configured_owner_id": str(fresh.get("owner_id") or "").strip().lower() or None,
                        "configured_account_id": str(fresh.get("account_id") or "").strip() or None,
                        "configured_broker_provider": str(fresh.get("broker_provider") or "").strip().lower() or None,
                        "worker_owner_id": _API_LOCAL_OWNER or None,
                        "worker_account_id": _API_ACCOUNT_ID or None,
                        "worker_broker_provider": _API_BROKER_PROVIDER or None,
                    },
                    logs=[{"message": "config_account_switch_ignored", "extra": {"reason": config_context_reason}}],
                    raw=raw,
                )
            result = _run_once(raw)
            result["last_loop_at"] = loop_started
            _write_runtime(result, force=True)
        except Exception as e:
            _write_runtime(
                {
                    "status": "error",
                    "mode": "stock_options_swing",
                    "error": str(e),
                    "last_loop_at": loop_started,
                    **_worker_context(raw if "raw" in locals() else {}),
                },
                force=True,
            )
            _append_decision(
                "",
                {"action": "worker_loop_error", "error": str(e)},
                logs=[{"message": "worker_loop_error", "extra": {"error": str(e)}}],
                raw=raw if "raw" in locals() else {},
            )
        poll = max(300.0, float((raw if "raw" in locals() else DEFAULT_CONFIG).get("poll_seconds") or 3600))
        end = time.monotonic() + poll
        while time.monotonic() < end and not _should_stop():
            time.sleep(min(5.0, end - time.monotonic()))
    _remove_pid()
    try:
        if os.path.exists(STOP_FILE):
            os.remove(STOP_FILE)
    except Exception:
        pass
    _remove_runtime()


def main() -> None:
    _acquire_process_singleton_or_exit()
    path = os.getenv("STOCK_OPTIONS_SWING_CONFIG") or CONFIG_FILE
    try:
        run_loop(path)
    finally:
        _release_process_singleton()


if __name__ == "__main__":
    main()
