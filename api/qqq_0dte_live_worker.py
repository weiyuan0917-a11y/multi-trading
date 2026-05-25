"""
QQQ 0DTE 实盘轮询 Worker：通过本地 API 拉 K 线、跑 Qqq0dteLiveSession，
在本根 K 线上若产生开仓/平仓意图则调用 resolve-contract + /options/order；开仓带 use_ask_for_buy_limit（卖一/ask），平仓带 use_bid_for_sell_limit（买一/bid），均优先 LongPort depth。

由 Launcher / Setup 与 api.main 统一拉起与停止；停止时写入 .qqq_0dte_live_worker.stop 并终止进程。

鉴权（二选一）：
- 推荐：控制台「API Key」页生成 Key，配置环境变量 QQQ_LIVE_API_KEY 或 live_worker_config.json 的 api_key；
  Worker 对 /options/* 请求携带请求头 X-Api-Key。
- 兼容：浏览器登录态 session token，配置 QQQ_LIVE_API_BEARER_TOKEN 或 api_bearer_token，携带 Authorization: Bearer。
每轮轮询会重新读取配置文件以便热更新。
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
import traceback
from collections import defaultdict, deque
from dataclasses import asdict
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _parse_worker_instance_from_argv() -> str | None:
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--instance" and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--instance="):
            return a.split("=", 1)[-1].strip()
    return None


def _sanitize_worker_instance(s: str) -> str:
    t = re.sub(r"[^a-z0-9_-]", "", (s or "").strip().lower())[:32]
    return t if t else "0dte"


# 多实例：默认 0dte；1dte 由 Launcher/API 传 --instance=1dte 或环境变量 QQQ_LIVE_WORKER_INSTANCE。
_WORKER_INSTANCE = _sanitize_worker_instance(
    _parse_worker_instance_from_argv() or os.getenv("QQQ_LIVE_WORKER_INSTANCE", "0dte")
)
MCP_DIR = os.path.join(ROOT, "mcp_server")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

from mcp_server.backtest_engine import Bar, coerce_bar_datetime
from mcp_server.strategy_qqq_0dte.config import Qqq0dteConfig
from mcp_server.strategy_qqq_0dte.exit_rules import (
    DOUBLE_STRANGLE_LEG_KEYS,
    evaluate_double_strangle_exit,
    evaluate_exit,
    evaluate_gamma_exit,
    evaluate_gamma_pro_exit,
    evaluate_morning_directional_exit,
    evaluate_strangle_exit,
)
from mcp_server.strategy_qqq_0dte.oms_adapter import intent_to_legs_resolved
from mcp_server.strategy_qqq_0dte.levels import prior_trading_date_with_data
from mcp_server.strategy_qqq_0dte.runner_live import Qqq0dteLiveSession
from mcp_server.strategy_qqq_0dte.session_us import is_at_or_after_et_hhmm, ny_date, option_expiry_datetime, to_ny
from mcp_server.strategy_qqq_0dte.state import OpenPosition, TradeIntent
from api.services.option_short_guard import broker_option_qty_by_symbol, option_symbol_key, validate_option_sell_covered

PID_FILE = os.path.join(ROOT, f".qqq_{_WORKER_INSTANCE}_live_worker.pid")
STOP_FILE = os.path.join(ROOT, f".qqq_{_WORKER_INSTANCE}_live_worker.stop")
RUNTIME_FILE = os.path.join(ROOT, f".qqq_{_WORKER_INSTANCE}_live_worker.runtime.json")
_SINGLETON_LOCK_FILE = os.path.join(ROOT, f".qqq_{_WORKER_INSTANCE}_live_worker.singleton.lock")
_SINGLETON_WIN_MUTEX_HANDLE: Any = None
_SINGLETON_POSIX_LOCK_FD: Any = None
DEFAULT_CONFIG_PATH = os.path.join(ROOT, "data", f"qqq_{_WORKER_INSTANCE}", "live_worker_config.json")
DECISION_TAIL_FILE = os.path.join(ROOT, "data", f"qqq_{_WORKER_INSTANCE}", "live_worker_decision_tail.jsonl")
OPEN_STATE_FILE = os.path.join(ROOT, "data", f"qqq_{_WORKER_INSTANCE}", "live_worker_open_state.json")
MANUAL_REVIEW_LOCK_FILE = os.path.join(ROOT, "data", f"qqq_{_WORKER_INSTANCE}", "live_worker_manual_review_lock.json")
ORDER_LIFECYCLE_FILE = os.path.join(ROOT, "data", f"qqq_{_WORKER_INSTANCE}", "live_worker_order_lifecycle.jsonl")
# 写入 runtime，便于确认当前进程是否已加载「决策 JSONL」相关逻辑（旧进程无此字段）。
LIVE_WORKER_DECISION_TAIL_REV = 2
DECISION_TAIL_MAX_BYTES = int(os.getenv("QQQ_0DTE_LIVE_DECISION_TAIL_MAX_BYTES", str(2 * 1024 * 1024)))
DECISION_TAIL_KEEP_BYTES = int(os.getenv("QQQ_0DTE_LIVE_DECISION_TAIL_KEEP_BYTES", str(1 * 1024 * 1024)))
_NOOP_DECISION_TAIL_INTERVAL = max(30.0, float(os.getenv("QQQ_0DTE_LIVE_NOOP_DECISION_LOG_SECONDS", "120")))
_LAST_NOOP_DECISION_TAIL_MONO = 0.0
_DECISION_TAIL_FAIL_LOGGED = False


def _resolve_api_bearer(raw: dict[str, Any]) -> str:
    """环境变量优先；否则使用 live_worker_config.json 的 api_bearer_token（浏览器 session）。"""
    env = str(os.getenv("QQQ_LIVE_API_BEARER_TOKEN") or os.getenv("QQQ_0DTE_LIVE_API_BEARER_TOKEN") or "").strip()
    if env:
        return env
    t = raw.get("api_bearer_token")
    if t is None:
        return ""
    return str(t).strip()


def _resolve_api_key_legacy_unused(raw: dict[str, Any]) -> str:
    """个人 API Key（X-Api-Key）；优先于 Bearer。"""
    env = str(os.getenv("QQQ_LIVE_API_KEY") or os.getenv("QQQ_0DTE_LIVE_API_KEY") or "").strip()
    if env:
        return env
    t = raw.get("api_key")
    if t is None:
        return ""
    return str(t).strip()


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


def _worker_auth_legacy_config_paths(raw: dict[str, Any] | None = None, config_path: str = "") -> list[str]:
    raw = raw if isinstance(raw, dict) else {}
    current = os.path.abspath(config_path) if config_path else ""
    marker = f"{config_path} {_WORKER_INSTANCE} {raw.get('strategy_variant') or ''}".lower()
    is_1dte = (
        "qqq_1dte" in marker
        or _WORKER_INSTANCE == "1dte"
        or bool(raw.get("stock_options_mode"))
        or str(raw.get("expiry_offset_days") or "").strip() == "1"
    )
    ordered = ["qqq_1dte", "qqq_0dte"] if is_1dte else ["qqq_0dte", "qqq_1dte"]
    candidates = [os.path.join(ROOT, "data", name, "live_worker_config.json") for name in ordered]
    candidates.append(os.path.join(ROOT, "api", "auto_trader_config.json"))

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


def _api_key_allowed_for_owner(raw_key: str, owner_id: str) -> bool:
    owner = str(owner_id or "").strip().lower()
    if not str(raw_key or "").strip():
        return False
    return not owner or _api_key_owner_matches(raw_key, owner)


def _resolve_api_key(raw: dict[str, Any], config_path: str = "") -> str:
    """Personal API key for X-Api-Key; only accept owner-matching keys when owner is known."""
    owner = str(globals().get("_API_LOCAL_OWNER") or "").strip().lower()
    env = str(os.getenv("QQQ_LIVE_API_KEY") or os.getenv("QQQ_0DTE_LIVE_API_KEY") or "").strip()
    if env and _api_key_allowed_for_owner(env, owner):
        return env
    key = str(raw.get("api_key") or "").strip() if isinstance(raw, dict) else ""
    if key and _api_key_allowed_for_owner(key, owner):
        return key
    for path in _worker_auth_legacy_config_paths(raw, config_path):
        legacy_key, _legacy_bearer = _read_api_auth_from_config(path)
        if legacy_key and _api_key_allowed_for_owner(legacy_key, owner):
            _maybe_migrate_api_key_to_config(config_path, legacy_key, path)
            return legacy_key
    return ""


def _api_path_for_auth(url_or_path: str) -> str:
    """从完整 URL 或带 query 的路径取出用于前缀判断的路径段。"""
    s = str(url_or_path or "")
    if "://" in s:
        try:
            parsed = urllib.parse.urlparse(s)
            return parsed.path or ""
        except Exception:
            return ""
    return s.split("?", 1)[0]


def _api_apply_trade_auth(req: urllib.request.Request, path_or_url: str) -> None:
    """对实盘交易相关 API 附加鉴权：优先 X-Api-Key，否则 Authorization Bearer。"""
    p = _api_path_for_auth(path_or_url)
    needs_auth = (
        p.startswith("/options/")
        or p.startswith("/strategy/qqq-0dte/")
        or p.startswith("/strategy/qqq-1dte/")
        or p.startswith("/trade/order/")
    )
    if not needs_auth:
        return
    ak = str(_API_KEY or "").strip()
    if ak:
        req.add_header("X-Api-Key", ak)
        return
    tok = str(_API_BEARER_TOKEN or "").strip()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")


def _api_apply_options_auth(req: urllib.request.Request, path_or_url: str) -> None:
    _api_apply_trade_auth(req, path_or_url)


def _option_price_tick() -> float:
    t = float(os.getenv("QQQ_0DTE_OPTION_PRICE_TICK", "0.01"))
    return t if t > 0 else 0.01


def _quantize_limit_price_per_share(px: float, *, side: str) -> float:
    """
    LongPort 对期权限价常要求落在最小报价单位上；合成价经滑点后易出现长小数触发 602035 Wrong bid size。
    卖单向下取整、买单向上取整，更易成交且合规。
    """
    tick = _option_price_tick()
    if not (px > 0) or not math.isfinite(px):
        return 0.0
    s = str(side or "").strip().lower()
    if s == "sell":
        q = math.floor(px / tick + 1e-12) * tick
    else:
        q = math.ceil(px / tick - 1e-12) * tick
    return float(max(tick, q))


def _response_suggests_bad_bid_size(res: dict[str, Any]) -> bool:
    try:
        blob = json.dumps(res, ensure_ascii=False, default=str).lower()
    except Exception:
        blob = str(res).lower()
    return "602035" in blob or "wrong bid" in blob or "wrong bid size" in blob


def _sell_exit_price_max_retries() -> int:
    return max(1, min(8, int(os.getenv("QQQ_0DTE_SELL_EXIT_PRICE_RETRIES", "5"))))


_API_BASE_URL = str(os.getenv("QQQ_0DTE_LIVE_API_BASE_URL", "http://127.0.0.1:8010")).strip().rstrip("/")
_USE_API_PROXY = str(os.getenv("QQQ_0DTE_LIVE_USE_API_PROXY", "true")).strip().lower() in {"1", "true", "yes", "on"}
_API_TIMEOUT = max(1.0, float(os.getenv("QQQ_0DTE_LIVE_API_TIMEOUT_SECONDS", "25")))
# 与控制台登录态或个人 API Key 一致；/options/* 需其一
_API_BEARER_TOKEN = str(os.getenv("QQQ_LIVE_API_BEARER_TOKEN") or os.getenv("QQQ_0DTE_LIVE_API_BEARER_TOKEN") or "").strip()
_API_KEY = str(os.getenv("QQQ_LIVE_API_KEY") or os.getenv("QQQ_0DTE_LIVE_API_KEY") or "").strip()
_API_LOCAL_OWNER = str(os.getenv("QQQ_LIVE_OWNER_ID") or os.getenv("X_MT_LOCAL_OWNER") or "").strip().lower()
_API_ACCOUNT_ID = str(os.getenv("QQQ_LIVE_ACCOUNT_ID") or "").strip()
_API_BROKER_PROVIDER = str(os.getenv("QQQ_LIVE_BROKER_PROVIDER") or "").strip().lower()
_stop = threading.Event()
_LAST_RUNTIME_DIGEST: Optional[str] = None
_LAST_RUNTIME_TS = 0.0
_LAST_STRATEGY_REC_WALL = 0.0
_OPTION_SYMBOL_RE = re.compile(r"^(?P<underlying>[A-Z]+)(?P<expiry>\d{6})(?P<right>[CP])(?P<strike>\d+)\.US$")


def _effective_account_id(raw: dict[str, Any] | None = None) -> str:
    if isinstance(raw, dict):
        account_id = str(raw.get("account_id") or "").strip()
        if account_id:
            return account_id
    return _API_ACCOUNT_ID


def _effective_broker_provider(raw: dict[str, Any] | None = None) -> str:
    if isinstance(raw, dict):
        provider = str(raw.get("broker_provider") or "").strip().lower()
        if provider:
            return provider
    return _API_BROKER_PROVIDER


def _apply_worker_account_context(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    if _API_ACCOUNT_ID and not str(raw.get("account_id") or "").strip():
        raw["account_id"] = _API_ACCOUNT_ID
    if _API_BROKER_PROVIDER and not str(raw.get("broker_provider") or "").strip():
        raw["broker_provider"] = _API_BROKER_PROVIDER
    return raw


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


def _worker_account_context(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "owner_id": _API_LOCAL_OWNER or None,
        "account_id": _effective_account_id(raw) or None,
        "broker_provider": _effective_broker_provider(raw) or None,
    }


def _append_order_lifecycle_event(event: dict[str, Any]) -> None:
    if not isinstance(event, dict):
        return
    try:
        parent = os.path.dirname(ORDER_LIFECYCLE_FILE)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {
            "at": datetime.now(timezone.utc).isoformat(),
            "instance": _WORKER_INSTANCE,
            **_worker_account_context(),
            **event,
        }
        with open(ORDER_LIFECYCLE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str))
            f.write("\n")
    except Exception:
        return


def _recent_order_lifecycle_events(limit: int = 20) -> list[dict[str, Any]]:
    if not os.path.isfile(ORDER_LIFECYCLE_FILE):
        return []
    lim = max(1, min(int(limit or 20), 100))
    try:
        with open(ORDER_LIFECYCLE_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[-lim:]
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        ln = line.strip()
        if not ln:
            continue
        try:
            row = json.loads(ln)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _order_lifecycle_summary(limit: int = 8) -> dict[str, Any]:
    events = _recent_order_lifecycle_events(limit)
    latest = events[-1] if events else None
    by_state: dict[str, int] = {}
    orders_by_latest_state: dict[str, str] = {}
    for row in events:
        state = str(row.get("state") or "").strip() or "unknown"
        by_state[state] = by_state.get(state, 0) + 1
        order_ids = row.get("order_ids")
        if isinstance(order_ids, list):
            for oid in order_ids:
                order_id = str(oid or "").strip()
                if order_id:
                    orders_by_latest_state[order_id] = state
    latest_state = str((latest or {}).get("state") or "").strip()
    attention_states = {"manual_review", "uncertain", "rejected", "active", "cancel_requested", "submitted"}
    pending_states = {"manual_review", "uncertain", "active", "cancel_requested", "submitted"}
    needs_attention = bool(latest_state in attention_states)
    severity = (
        "bad"
        if latest_state in {"manual_review", "rejected"}
        else "warn"
        if latest_state in {"uncertain", "active", "cancel_requested", "submitted"}
        else "good"
        if latest_state in {"filled", "cancelled"}
        else "muted"
    )
    pending_order_ids = [oid for oid, state in orders_by_latest_state.items() if state in pending_states]
    attention_reasons: list[str] = []
    if latest:
        reason = str(latest.get("reason") or "").strip()
        if reason:
            attention_reasons.append(reason)
    return {
        "path": ORDER_LIFECYCLE_FILE,
        "recent_count": len(events),
        "latest": latest,
        "latest_state": latest_state or None,
        "latest_event": str((latest or {}).get("event") or "").strip() or None,
        "latest_at": str((latest or {}).get("at") or "").strip() or None,
        "recent": events,
        "by_state": by_state,
        "orders_by_latest_state": orders_by_latest_state,
        "pending_order_ids": pending_order_ids,
        "needs_attention": needs_attention,
        "severity": severity,
        "attention_reasons": attention_reasons,
    }


def _load_manual_review_lock() -> dict[str, Any] | None:
    try:
        if not os.path.isfile(MANUAL_REVIEW_LOCK_FILE):
            return None
        with open(MANUAL_REVIEW_LOCK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not bool(data.get("locked", True)):
            return None
        if str(data.get("instance") or _WORKER_INSTANCE).strip().lower() != _WORKER_INSTANCE:
            return None
        return data
    except Exception:
        return None


def _manual_review_lock_matches_context(lock: dict[str, Any] | None, raw: dict[str, Any] | None = None) -> bool:
    if not isinstance(lock, dict):
        return False
    ctx = _worker_account_context(raw)
    for key in ("owner_id", "account_id", "broker_provider"):
        current = str(ctx.get(key) or "").strip().lower()
        locked = str(lock.get(key) or "").strip().lower()
        if current and not locked:
            return False
        if current and locked and current != locked:
            return False
    return True


def _ledger_row_matches_worker_context(row: dict[str, Any] | None, raw: dict[str, Any] | None = None) -> bool:
    if not isinstance(row, dict):
        return False
    ctx = _worker_account_context(raw)
    for key in ("owner_id", "account_id", "broker_provider"):
        current = str(ctx.get(key) or "").strip().lower()
        got = str(row.get(key) or "").strip().lower()
        if current and (not got or got != current):
            return False
    return True


def _write_manual_review_lock(
    *,
    raw: dict[str, Any],
    reason: str,
    detail: dict[str, Any] | None = None,
    expected_legs: list[dict[str, Any]] | None = None,
    order_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    detail = detail if isinstance(detail, dict) else {}
    order_response = order_response if isinstance(order_response, dict) else {}
    legs = [dict(x) for x in (expected_legs or []) if isinstance(x, dict)]
    order_ids = _extract_order_ids_from_payload({"detail": detail, "order_response": order_response})
    expected_symbols = sorted(
        {
            str(x.get("symbol") or "").strip().upper()
            for x in legs
            if str(x.get("symbol") or "").strip()
        }
    )
    lock: dict[str, Any] = {
        "locked": True,
        "instance": _WORKER_INSTANCE,
        "created_at": now,
        "reason": str(reason or "manual_review_required"),
        "message": "manual review required before new intraday entries",
        "order_ids": order_ids,
        "expected_symbols": expected_symbols,
        "expected_legs": legs,
        "detail": detail,
        **_worker_account_context(raw),
    }
    _append_order_lifecycle_event(
        {
            "event": "manual_review_lock",
            "state": "manual_review",
            "reason": lock["reason"],
            "order_ids": order_ids,
            "expected_symbols": expected_symbols,
            "detail": detail,
            **_worker_account_context(raw),
        }
    )
    parent = os.path.dirname(MANUAL_REVIEW_LOCK_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = MANUAL_REVIEW_LOCK_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lock, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
        os.replace(tmp, MANUAL_REVIEW_LOCK_FILE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    return lock


def _intraday_manual_review_entry_guard(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not _is_stock_options_intraday_mode(raw):
        return None
    safety = _intraday_safety_config(raw)
    if not bool(safety.get("enabled", True)):
        return None
    lock = _load_manual_review_lock()
    if not lock or not _manual_review_lock_matches_context(lock, raw):
        return None
    return {
        "reason": "manual_review_lock_active",
        "blocked": True,
        "manual_review_lock": lock,
        **_worker_account_context(raw),
    }


def _api_get_json(path: str, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
    url = f"{_API_BASE_URL}{path}"
    try:
        req = urllib.request.Request(url)
        _api_apply_options_auth(req, path)
        if _API_LOCAL_OWNER:
            req.add_header("X-MT-Local-Owner", _API_LOCAL_OWNER)
        with urllib.request.urlopen(req, timeout=float(timeout or _API_TIMEOUT)) as resp:
            if int(getattr(resp, "status", 200) or 200) != 200:
                return None
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _api_post_json(path: str, payload: dict[str, Any], timeout: Optional[float] = None) -> tuple[bool, dict[str, Any]]:
    url = f"{_API_BASE_URL}{path}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    _api_apply_options_auth(req, path)
    if _API_LOCAL_OWNER:
        req.add_header("X-MT-Local-Owner", _API_LOCAL_OWNER)
    try:
        with urllib.request.urlopen(req, timeout=float(timeout or _API_TIMEOUT)) as resp:
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


def _parse_option_symbol_meta(symbol: str) -> dict[str, Any] | None:
    s = str(symbol or "").strip().upper()
    m = _OPTION_SYMBOL_RE.match(s)
    if not m:
        return None
    strike_raw = str(m.group("strike") or "").strip()
    try:
        strike = int(strike_raw) / 1000.0
    except Exception:
        return None
    expiry_raw = str(m.group("expiry") or "").strip()
    try:
        expiry_date = datetime.strptime(expiry_raw, "%y%m%d").date().isoformat()
    except Exception:
        expiry_date = ""
    right = "call" if str(m.group("right") or "").upper() == "C" else "put"
    return {
        "symbol": s,
        "underlying": str(m.group("underlying") or "").strip().upper(),
        "expiry_date": expiry_date,
        "right": right,
        "strike": strike,
    }


def _api_get_option_positions(raw: dict[str, Any]) -> list[dict[str, Any]]:
    qs: dict[str, Any] = {}
    account_id = _effective_account_id(raw)
    if account_id:
        qs["account_id"] = account_id
    q = urllib.parse.urlencode(qs)
    path = "/options/positions" + (f"?{q}" if q else "")
    data = _api_get_json(path, timeout=min(_API_TIMEOUT, 12.0))
    items = data.get("positions") if isinstance(data, dict) else None
    return [x for x in (items or []) if isinstance(x, dict)] if isinstance(items, list) else []


def _api_get_option_positions_checked(raw: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, str | None]:
    qs: dict[str, Any] = {}
    account_id = _effective_account_id(raw)
    if account_id:
        qs["account_id"] = account_id
    q = urllib.parse.urlencode(qs)
    path = "/options/positions" + (f"?{q}" if q else "")
    data = _api_get_json(path, timeout=min(_API_TIMEOUT, 12.0))
    if not isinstance(data, dict):
        return None, "positions_api_unavailable"
    items = data.get("positions")
    if not isinstance(items, list):
        return None, "positions_api_bad_response"
    return [x for x in items if isinstance(x, dict)], None


def _api_get_option_orders(raw: dict[str, Any], status: str = "all") -> list[dict[str, Any]]:
    qs: dict[str, Any] = {"status": str(status or "all")}
    account_id = _effective_account_id(raw)
    if account_id:
        qs["account_id"] = account_id
    q = urllib.parse.urlencode(qs)
    data = _api_get_json(f"/options/orders?{q}", timeout=min(_API_TIMEOUT, 12.0))
    items = data.get("orders") if isinstance(data, dict) else None
    return [x for x in (items or []) if isinstance(x, dict)] if isinstance(items, list) else []


def _api_get_option_orders_by_ids(raw: dict[str, Any], order_ids: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    ids = [str(x or "").strip() for x in order_ids if str(x or "").strip()]
    if not ids:
        return {}, {"ok": True, "requested": 0, "matched": 0, "missing": []}
    by_id: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for status in ("all", "active", "filled", "cancelled"):
        try:
            for row in _api_get_option_orders(raw, status=status):
                oid = str(row.get("order_id") or "").strip()
                if oid in ids and oid not in by_id:
                    by_id[oid] = row
        except Exception as e:
            errors.append(f"{status}:{e}")
    missing = [oid for oid in ids if oid not in by_id]
    return by_id, {"ok": not errors, "requested": len(ids), "matched": len(by_id), "missing": missing, "errors": errors}


def _api_cancel_order(raw: dict[str, Any], order_id: str) -> tuple[bool, dict[str, Any]]:
    oid = str(order_id or "").strip()
    if not oid:
        return False, {"error": "missing_order_id"}
    qs: dict[str, Any] = {}
    account_id = _effective_account_id(raw)
    if account_id:
        qs["account_id"] = account_id
    q = urllib.parse.urlencode(qs)
    path = f"/options/order/{urllib.parse.quote(oid, safe='')}/cancel" + (f"?{q}" if q else "")
    return _api_post_json(path, {}, timeout=min(_API_TIMEOUT, 12.0))


def _account_risk_config(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    src = raw.get("account_risk") if isinstance(raw, dict) and isinstance(raw.get("account_risk"), dict) else {}
    out: dict[str, Any] = {
        "enabled": True,
        "fail_closed_for_live": True,
        "min_buy_power": 0.0,
        "min_buy_power_pct": 0.0,
        "max_order_premium_pct": 0.05,
        "max_total_option_premium_pct": 0.25,
    }
    out.update(src)
    out["enabled"] = bool(out.get("enabled", True))
    out["fail_closed_for_live"] = bool(out.get("fail_closed_for_live", True))
    return out


def _api_get_trade_account(raw: dict[str, Any]) -> dict[str, Any] | None:
    qs: dict[str, Any] = {}
    account_id = _effective_account_id(raw)
    if account_id:
        qs["account_id"] = account_id
    q = urllib.parse.urlencode(qs)
    return _api_get_json("/trade/account" + (f"?{q}" if q else ""), timeout=min(_API_TIMEOUT, 12.0))


def _estimate_option_position_premium(row: dict[str, Any]) -> float:
    try:
        qty = abs(float(row.get("quantity") or row.get("qty") or 0.0))
    except Exception:
        qty = 0.0
    price = _to_float_or_none(
        row.get("cost_price")
        or row.get("avg_cost")
        or row.get("average_cost")
        or row.get("market_price")
        or row.get("price")
    )
    return max(0.0, qty * float(price or 0.0) * 100.0)


def _estimate_order_premium_from_legs(legs: list[dict[str, Any]]) -> float:
    total = 0.0
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        side = _normalize_order_side_text(leg.get("side"))
        if side != "buy":
            continue
        qty = max(0, int(float(leg.get("contracts") or 0) or 0))
        px = _to_float_or_none(leg.get("price")) or 0.0
        total += qty * px * 100.0
    return round(max(0.0, total), 2)


def _account_level_risk_gate(
    raw: dict[str, Any],
    *,
    legs: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    cfg = _account_risk_config(raw)
    order_premium = _estimate_order_premium_from_legs(legs)
    detail: dict[str, Any] = {
        "enabled": bool(cfg.get("enabled", True)),
        "order_premium": order_premium,
        "dry_run": bool(dry_run),
        "blocked": False,
        "blocks": [],
        **_worker_account_context(raw),
    }
    if not bool(cfg.get("enabled", True)):
        return detail
    account = _api_get_trade_account(raw)
    if not isinstance(account, dict):
        detail["account_available"] = False
        detail["blocks"].append("account_unavailable")
        detail["blocked"] = bool((not dry_run) and cfg.get("fail_closed_for_live", True))
        return detail
    net_assets = _to_float_or_none(account.get("net_assets") or account.get("total_assets") or account.get("equity"))
    buy_power = _to_float_or_none(account.get("buy_power") or account.get("buying_power") or account.get("available_cash"))
    detail.update({"account_available": True, "net_assets": net_assets, "buy_power": buy_power, "currency": account.get("currency")})
    min_buy_power = _to_float_or_none(cfg.get("min_buy_power")) or 0.0
    min_buy_power_pct = _to_float_or_none(cfg.get("min_buy_power_pct")) or 0.0
    required_bp = max(min_buy_power, (float(net_assets or 0.0) * min_buy_power_pct) if net_assets else 0.0)
    if buy_power is not None and required_bp > 0 and buy_power < required_bp:
        detail["blocks"].append("buy_power_below_min")
        detail["required_buy_power"] = round(required_bp, 2)
    if buy_power is not None and order_premium > 0 and buy_power < order_premium:
        detail["blocks"].append("buy_power_below_order_premium")
    max_order_pct = _to_float_or_none(cfg.get("max_order_premium_pct")) or 0.0
    if net_assets and max_order_pct > 0 and order_premium > net_assets * max_order_pct:
        detail["blocks"].append("order_premium_pct_exceeded")
        detail["max_order_premium"] = round(net_assets * max_order_pct, 2)
    positions, pos_err = _api_get_option_positions_checked(raw)
    if positions is None:
        detail["positions_error"] = pos_err
        if not dry_run and bool(cfg.get("fail_closed_for_live", True)):
            detail["blocks"].append("positions_unavailable")
    else:
        total_premium = sum(_estimate_option_position_premium(x) for x in positions if isinstance(x, dict))
        detail["current_option_premium"] = round(total_premium, 2)
        max_total_pct = _to_float_or_none(cfg.get("max_total_option_premium_pct")) or 0.0
        if net_assets and max_total_pct > 0 and total_premium + order_premium > net_assets * max_total_pct:
            detail["blocks"].append("total_option_premium_pct_exceeded")
            detail["max_total_option_premium"] = round(net_assets * max_total_pct, 2)
    detail["blocks"] = sorted(set(str(x) for x in detail["blocks"] if str(x)))
    detail["blocked"] = bool(detail["blocks"] and not dry_run)
    return detail


def _is_stock_options_intraday_mode(raw: dict[str, Any] | None = None) -> bool:
    if _WORKER_INSTANCE == "1dte":
        return True
    return bool(isinstance(raw, dict) and raw.get("stock_options_mode"))


def _to_float_or_none(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        if math.isfinite(f):
            return f
    except Exception:
        return None
    return None


def _to_int_or_none(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except Exception:
        return None


def _first_nested_value(obj: Any, keys: tuple[str, ...], *, max_depth: int = 4) -> Any:
    want = {str(k).lower() for k in keys}
    seen: set[int] = set()

    def walk(x: Any, depth: int) -> Any:
        if depth > max_depth:
            return None
        if isinstance(x, dict):
            ident = id(x)
            if ident in seen:
                return None
            seen.add(ident)
            for k, v in x.items():
                if str(k).lower() in want:
                    return v
            for v in x.values():
                found = walk(v, depth + 1)
                if found is not None:
                    return found
        elif isinstance(x, list):
            for v in x[:20]:
                found = walk(v, depth + 1)
                if found is not None:
                    return found
        return None

    return walk(obj, 0)


def _first_nested_float(obj: Any, keys: tuple[str, ...]) -> float | None:
    return _to_float_or_none(_first_nested_value(obj, keys))


def _first_nested_int(obj: Any, keys: tuple[str, ...]) -> int | None:
    return _to_int_or_none(_first_nested_value(obj, keys))


def _intraday_safety_config(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    src = raw.get("intraday_safety") if isinstance(raw, dict) and isinstance(raw.get("intraday_safety"), dict) else {}
    strategy = raw.get("strategy_config") if isinstance(raw, dict) and isinstance(raw.get("strategy_config"), dict) else {}
    force_close = str(strategy.get("strangle_force_close_hhmm_et") or "12:00").strip() or "12:00"
    entry_end = str(strategy.get("strangle_entry_end_hhmm_et") or "").strip()
    out: dict[str, Any] = {
        "enabled": True,
        "max_bid_ask_spread_pct": 0.18,
        "min_bid": 0.01,
        "min_ask": 0.01,
        "min_mid": 0.03,
        "min_volume": 0,
        "min_open_interest": 0,
        "require_bid": True,
        "latest_entry_hhmm_et": entry_end or "15:00",
        "force_close_hhmm_et": force_close,
        "block_entry_when_unmanaged_positions": True,
        "block_market_order": True,
        "post_entry_check_delay_seconds": 1.0,
        "cancel_open_entry_orders_after_submit": True,
        "cancel_active_orders_at_force_close": True,
    }
    if isinstance(src, dict):
        out.update(src)
    out["enabled"] = bool(out.get("enabled", True))
    out["block_entry_when_unmanaged_positions"] = bool(out.get("block_entry_when_unmanaged_positions", True))
    out["block_market_order"] = bool(out.get("block_market_order", True))
    out["cancel_open_entry_orders_after_submit"] = bool(out.get("cancel_open_entry_orders_after_submit", True))
    out["cancel_active_orders_at_force_close"] = bool(out.get("cancel_active_orders_at_force_close", True))
    return out


def _option_quote_quality_from_resolve(res: dict[str, Any], *, leg_price: float | None = None) -> dict[str, Any]:
    bid = _first_nested_float(res, ("bid", "bid_price", "bidprice", "best_bid", "bestbid", "bid1", "bid_1"))
    ask = _first_nested_float(res, ("ask", "ask_price", "askprice", "best_ask", "bestask", "ask1", "ask_1"))
    last = _first_nested_float(res, ("last", "last_done", "lastdone", "latest_price", "price"))
    src = str(res.get("suggested_limit_price_source") or "").strip().lower() if isinstance(res, dict) else ""
    suggested = _to_float_or_none(res.get("suggested_limit_price")) if isinstance(res, dict) else None
    if suggested is None and isinstance(res, dict):
        suggested = _to_float_or_none(res.get("suggested_limit_price_per_share"))
    if ask is None and suggested is not None and suggested > 0 and "ask" in src:
        ask = float(suggested)
    if bid is None and suggested is not None and suggested > 0 and "bid" in src:
        bid = float(suggested)
    mid: float | None = None
    if bid is not None and bid > 0 and ask is not None and ask > 0:
        mid = (bid + ask) / 2.0
    elif leg_price is not None and leg_price > 0:
        mid = float(leg_price)
    elif ask is not None and ask > 0:
        mid = float(ask)
    elif last is not None and last > 0:
        mid = float(last)
    spread_pct: float | None = None
    if bid is not None and bid > 0 and ask is not None and ask > 0 and mid and mid > 0:
        spread_pct = (ask - bid) / mid
    return {
        "bid": bid,
        "ask": ask,
        "last": last,
        "mid": mid,
        "spread_pct": spread_pct,
        "volume": _first_nested_int(res, ("volume", "vol", "trade_volume")),
        "open_interest": _first_nested_int(res, ("open_interest", "openinterest", "oi")),
    }


def _validate_intraday_option_quote_quality(
    *,
    raw: dict[str, Any],
    resolve_response: dict[str, Any],
    leg: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    if not _is_stock_options_intraday_mode(raw):
        return True, {"enabled": False}
    cfg = _intraday_safety_config(raw)
    if not bool(cfg.get("enabled", True)):
        return True, {"enabled": False}
    side = _normalize_order_side_text(leg.get("side"))
    if side != "buy":
        return True, {"enabled": True, "skipped": "not_buy_entry"}
    price = _to_float_or_none(leg.get("price")) or 0.0
    quote = _option_quote_quality_from_resolve(resolve_response, leg_price=price)
    blocks: list[str] = []
    bid = quote.get("bid")
    ask = quote.get("ask")
    mid = quote.get("mid")
    spread_pct = quote.get("spread_pct")
    if bool(cfg.get("block_market_order", True)) and price <= 0:
        blocks.append("limit_price_missing")
    if bool(cfg.get("require_bid", True)) and (bid is None or float(bid) < float(cfg.get("min_bid") or 0.0)):
        blocks.append("bid_unavailable")
    if ask is None or float(ask) < float(cfg.get("min_ask") or 0.0):
        blocks.append("ask_unavailable")
    if mid is None or float(mid) < float(cfg.get("min_mid") or 0.0):
        blocks.append("mid_too_low")
    if spread_pct is not None and float(spread_pct) > float(cfg.get("max_bid_ask_spread_pct") or 1.0):
        blocks.append("bid_ask_spread_too_wide")
    vol = quote.get("volume")
    min_vol = int(float(cfg.get("min_volume") or 0) or 0)
    if min_vol > 0 and vol is not None and int(vol) < min_vol:
        blocks.append("volume_too_low")
    oi = quote.get("open_interest")
    min_oi = int(float(cfg.get("min_open_interest") or 0) or 0)
    if min_oi > 0 and oi is not None and int(oi) < min_oi:
        blocks.append("open_interest_too_low")
    detail = {
        "enabled": True,
        "symbol": str(leg.get("symbol") or "").strip().upper(),
        "side": side,
        "price": price,
        "quote": quote,
        "thresholds": {
            "max_bid_ask_spread_pct": float(cfg.get("max_bid_ask_spread_pct") or 0.0),
            "min_bid": float(cfg.get("min_bid") or 0.0),
            "min_ask": float(cfg.get("min_ask") or 0.0),
            "min_mid": float(cfg.get("min_mid") or 0.0),
            "min_volume": min_vol,
            "min_open_interest": min_oi,
        },
        "blocks": blocks,
    }
    return len(blocks) == 0, detail


def _execution_ledger_path() -> str:
    return DECISION_TAIL_FILE.replace("decision_tail", "execution_ledger")


def _iter_execution_legs_from_detail(detail: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(detail, dict):
        return out
    order = detail.get("order")
    if not isinstance(order, dict):
        return out
    mode = str(order.get("mode") or "").strip().lower()
    if mode == "single_leg":
        leg = order.get("order")
        if isinstance(leg, dict):
            out.append(leg)
        return out
    if mode != "multi_leg":
        return out
    result = order.get("result")
    if not isinstance(result, dict):
        return out
    legs = result.get("legs_submitted")
    if not isinstance(legs, list):
        return out
    for leg in legs:
        if isinstance(leg, dict):
            out.append(leg)
    return out


def _normalize_execution_ledger_row(leg: dict[str, Any], *, ts: int, at_iso: str | None = None) -> dict[str, Any] | None:
    oid = str(leg.get("order_id") or "").strip()
    sym = str(leg.get("symbol") or "").strip().upper()
    side = _normalize_order_side_text(leg.get("side"))
    qty = max(0, int(float(leg.get("contracts") or 0) or 0))
    try:
        px = float(leg.get("price") or 0.0)
    except Exception:
        px = 0.0
    ts_i = int(ts or 0)
    if ts_i <= 0:
        ts_i = int(datetime.now(timezone.utc).timestamp())
    if not oid or not sym or side not in {"buy", "sell"} or qty <= 0 or px <= 0:
        return None
    out = {
        "at": str(at_iso or datetime.now(timezone.utc).isoformat()),
        "order_id": oid,
        "symbol": sym,
        "side": side,
        "contracts": qty,
        "price": px,
        "ts": ts_i,
    }
    for key in ("position_group_id", "combo_mode"):
        val = str(leg.get(key) or "").strip()
        if val:
            out[key] = val
    return out


def _position_group_id_for_legs(legs: list[dict[str, Any]], *, mode_name: str = "") -> str:
    parts = [str(mode_name or "combo").strip().lower()]
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        parts.append(str(leg.get("symbol") or "").strip().upper())
        parts.append(_normalize_order_side_text(leg.get("side")))
        parts.append(str(max(0, int(float(leg.get("contracts") or 0) or 0))))
    parts.append(str(int(time.time() * 1000)))
    digest = hashlib.sha1("|".join(parts).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{str(mode_name or 'combo').strip().lower() or 'combo'}-{digest}"


def _combo_state_from_ledger(raw: dict[str, Any] | None = None, limit: int = 12) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for row in _load_execution_ledger_rows():
        if not isinstance(row, dict):
            continue
        if not _ledger_row_matches_worker_context(row, raw):
            continue
        gid = str(row.get("position_group_id") or "").strip()
        if not gid:
            continue
        group = groups.setdefault(
            gid,
            {
                "position_group_id": gid,
                "mode": row.get("combo_mode") or row.get("mode"),
                "opened_at": row.get("at"),
                "symbols": set(),
                "buy_contracts": 0,
                "sell_contracts": 0,
                "net_debit": 0.0,
            },
        )
        sym = str(row.get("symbol") or "").strip().upper()
        if sym:
            group["symbols"].add(sym)
        qty = max(0, int(float(row.get("contracts") or 0) or 0))
        px = _to_float_or_none(row.get("price")) or 0.0
        if _normalize_order_side_text(row.get("side")) == "buy":
            group["buy_contracts"] += qty
            group["net_debit"] += qty * px * 100.0
        elif _normalize_order_side_text(row.get("side")) == "sell":
            group["sell_contracts"] += qty
            group["net_debit"] -= qty * px * 100.0
    out = []
    for group in groups.values():
        symbols = sorted(str(x) for x in group.pop("symbols", set()))
        open_contracts = max(0, int(group.get("buy_contracts") or 0) - int(group.get("sell_contracts") or 0))
        out.append({**group, "symbols": symbols, "open_contracts": open_contracts, "net_debit": round(float(group.get("net_debit") or 0.0), 2)})
    out.sort(key=lambda x: str(x.get("opened_at") or ""), reverse=True)
    active = [x for x in out if int(x.get("open_contracts") or 0) > 0]
    return {"recent": out[: max(1, min(int(limit or 12), 50))], "active": active, "active_count": len(active)}


def _append_execution_ledger_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path = _execution_ledger_path()
    parent = os.path.dirname(path)
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                f.write(json.dumps(row, ensure_ascii=False, default=str))
                f.write("\n")
    except Exception:
        return


def _append_execution_ledger_from_detail(detail: dict[str, Any], *, at: datetime | None = None) -> None:
    when = at if isinstance(at, datetime) else datetime.now(timezone.utc)
    ts = int(when.timestamp())
    at_iso = when.isoformat()
    rows: list[dict[str, Any]] = []
    order = detail.get("order") if isinstance(detail, dict) else None
    reconciled = order.get("reconciled_ledger_rows") if isinstance(order, dict) else None
    if isinstance(reconciled, list) and reconciled:
        for item in reconciled:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if not str(row.get("at") or "").strip():
                row["at"] = at_iso
            if not _to_int_or_none(row.get("ts")):
                row["ts"] = ts
            row.update(_worker_account_context())
            norm = _normalize_execution_ledger_row(row, ts=int(row.get("ts") or ts), at_iso=str(row.get("at") or at_iso))
            if norm is not None:
                norm.update({k: v for k, v in row.items() if k not in norm})
                rows.append(norm)
        _append_execution_ledger_rows(rows)
        return
    for leg in _iter_execution_legs_from_detail(detail):
        row = _normalize_execution_ledger_row(leg, ts=ts, at_iso=at_iso)
        if row is not None:
            row.update(_worker_account_context())
            rows.append(row)
    _append_execution_ledger_rows(rows)


def _extract_execution_ledger_rows_from_decision_tail() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not os.path.isfile(DECISION_TAIL_FILE):
        return rows
    try:
        with open(DECISION_TAIL_FILE, "r", encoding="utf-8") as f:
            for line in f:
                ln = line.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                action = row.get("action")
                if not isinstance(action, dict) or not bool(action.get("ok", False)):
                    continue
                detail = action.get("detail")
                if not isinstance(detail, dict):
                    continue
                ts = _coerce_timestamp_seconds(row.get("at"))
                at_iso = str(row.get("at") or "").strip() or datetime.now(timezone.utc).isoformat()
                for leg in _iter_execution_legs_from_detail(detail):
                    norm = _normalize_execution_ledger_row(leg, ts=ts, at_iso=at_iso)
                    if norm is not None:
                        for key in ("owner_id", "account_id", "broker_provider"):
                            val = row.get(key)
                            if val is None and isinstance(action, dict):
                                val = action.get(key)
                            if val is None and isinstance(detail, dict):
                                val = detail.get(key)
                            if val is not None and str(val).strip():
                                norm[key] = str(val).strip().lower() if key in {"owner_id", "broker_provider"} else str(val).strip()
                        rows.append(norm)
    except Exception:
        return rows
    return rows


def _load_execution_ledger_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_order_ids: set[str] = set()
    path = _execution_ledger_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    ln = line.strip()
                    if not ln:
                        continue
                    try:
                        row = json.loads(ln)
                    except Exception:
                        continue
                    if not isinstance(row, dict):
                        continue
                    rows.append(row)
                    oid = str(row.get("order_id") or "").strip()
                    if oid:
                        seen_order_ids.add(oid)
        except Exception:
            return []
    missing_rows: list[dict[str, Any]] = []
    for row in _extract_execution_ledger_rows_from_decision_tail():
        oid = str(row.get("order_id") or "").strip()
        if not oid or oid in seen_order_ids:
            continue
        seen_order_ids.add(oid)
        rows.append(row)
        missing_rows.append(row)
    if missing_rows:
        _append_execution_ledger_rows(missing_rows)
    return rows


def _normalize_order_side_text(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if "buy" in s:
        return "buy"
    if "sell" in s:
        return "sell"
    return s


def _coerce_timestamp_seconds(v: Any) -> int:
    if isinstance(v, datetime):
        dt = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    try:
        if v is None or v == "":
            return 0
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return 0
            if "T" in s or s.endswith("Z") or "+" in s:
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
        return int(float(v))
    except Exception:
        return 0


def _open_position_to_payload(pos: OpenPosition | None) -> dict[str, Any] | None:
    if pos is None:
        return None
    payload = asdict(pos)
    if not isinstance(payload, dict):
        return None
    entry_time = payload.get("entry_time")
    if isinstance(entry_time, datetime):
        payload["entry_time"] = entry_time.isoformat()
    return payload


def _open_position_from_payload(payload: dict[str, Any] | None) -> OpenPosition | None:
    if not isinstance(payload, dict):
        return None
    side = str(payload.get("side") or "").strip().lower()
    if side not in {"long_call", "long_put", "strangle", "double_strangle"}:
        return None
    entry_time_raw = payload.get("entry_time")
    if isinstance(entry_time_raw, datetime):
        entry_time = entry_time_raw
    elif isinstance(entry_time_raw, str) and entry_time_raw.strip():
        try:
            entry_time = datetime.fromisoformat(entry_time_raw.strip())
        except Exception:
            return None
    else:
        return None
    if entry_time.tzinfo is not None:
        entry_time = entry_time.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    try:
        return OpenPosition(
            side=side,
            strike=float(payload.get("strike") or 0.0),
            entry_bar_index=int(payload.get("entry_bar_index") or 0),
            entry_time=entry_time,
            entry_px=float(payload.get("entry_px") or 0.0),
            contracts=max(1, int(payload.get("contracts") or 1)),
            call_strike=float(payload.get("call_strike") or 0.0),
            put_strike=float(payload.get("put_strike") or 0.0),
            call_entry_px=float(payload.get("call_entry_px") or 0.0),
            put_entry_px=float(payload.get("put_entry_px") or 0.0),
            strangle_call_active=bool(payload.get("strangle_call_active", True)),
            strangle_put_active=bool(payload.get("strangle_put_active", True)),
            strangle_original_entry_px=float(payload.get("strangle_original_entry_px") or 0.0),
            strangle_realized_exit_px=float(payload.get("strangle_realized_exit_px") or 0.0),
            call_strikes_otm=int(payload.get("call_strikes_otm") or 0),
            put_strikes_otm=int(payload.get("put_strikes_otm") or 0),
            double_strangle_legs=payload.get("double_strangle_legs") if isinstance(payload.get("double_strangle_legs"), dict) else {},
        )
    except Exception:
        return None


def _required_open_symbols(open_live: dict[str, Any] | None, pos: OpenPosition | None = None) -> list[str]:
    if not isinstance(open_live, dict):
        return []
    mode = str(open_live.get("mode") or "").strip().lower()
    if mode == "strangle":
        out: list[str] = []
        call_on = True if pos is None else bool(getattr(pos, "strangle_call_active", True))
        put_on = True if pos is None else bool(getattr(pos, "strangle_put_active", True))
        if call_on:
            cs = str(open_live.get("call_symbol") or "").strip().upper()
            if cs:
                out.append(cs)
        if put_on:
            ps = str(open_live.get("put_symbol") or "").strip().upper()
            if ps:
                out.append(ps)
        return out
    if mode == "double_strangle":
        leg_symbols = open_live.get("leg_symbols")
        if not isinstance(leg_symbols, dict):
            return []
        active_keys = set(DOUBLE_STRANGLE_LEG_KEYS)
        if pos is not None and str(getattr(pos, "side", "") or "") == "double_strangle":
            legs = getattr(pos, "double_strangle_legs", None)
            if isinstance(legs, dict):
                active_keys = {
                    str(k)
                    for k, leg in legs.items()
                    if str(k) in DOUBLE_STRANGLE_LEG_KEYS and isinstance(leg, dict) and bool(leg.get("active", True))
                }
        out = []
        for key in DOUBLE_STRANGLE_LEG_KEYS:
            if key not in active_keys:
                continue
            sym = str(leg_symbols.get(key) or "").strip().upper()
            if sym:
                out.append(sym)
        return out
    sym = str(open_live.get("symbol") or "").strip().upper()
    return [sym] if sym else []


def _worker_unclosed_lots_from_ledger(
    *,
    symbol: str,
    expiry_date: str | None = None,
    raw: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    ledger_rows = _load_execution_ledger_rows()
    lots: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    qsym = str(symbol or "").strip().upper().replace(".US", "")
    exp = str(expiry_date or "").strip()
    for row in sorted(ledger_rows, key=lambda x: int(_coerce_timestamp_seconds(x.get("ts") or x.get("at")))):
        if not _ledger_row_matches_worker_context(row, raw):
            continue
        sym = str(row.get("symbol") or "").strip().upper()
        meta = _parse_option_symbol_meta(sym)
        if not meta or meta.get("underlying") != qsym:
            continue
        if exp and str(meta.get("expiry_date") or "") != exp:
            continue
        side = _normalize_order_side_text(row.get("side"))
        qty = max(0, int(float(row.get("contracts") or 0) or 0))
        try:
            px = float(row.get("price") or 0.0)
        except Exception:
            px = 0.0
        ts = _coerce_timestamp_seconds(row.get("ts") or row.get("at"))
        if qty <= 0 or px <= 0 or side not in {"buy", "sell"} or ts <= 0:
            continue
        if side == "buy":
            lots[sym].append({"qty": qty, "price": px, "ts": ts, **meta})
            continue
        remain = qty
        while remain > 0 and lots[sym]:
            lot = lots[sym][0]
            matched = min(remain, int(lot["qty"]))
            lot["qty"] = int(lot["qty"]) - matched
            remain -= matched
            if int(lot["qty"]) <= 0:
                lots[sym].popleft()

    out: dict[str, dict[str, Any]] = {}
    for sym, remaining_lots in lots.items():
        qty = 0
        weighted_cost = 0.0
        earliest_ts = 0
        meta: dict[str, Any] = {}
        for lot in remaining_lots:
            q = max(0, int(lot.get("qty") or 0))
            if q <= 0:
                continue
            qty += q
            weighted_cost += q * float(lot.get("price") or 0.0)
            ts = int(lot.get("ts") or 0)
            if earliest_ts <= 0 or (ts > 0 and ts < earliest_ts):
                earliest_ts = ts
            meta = {k: lot.get(k) for k in ("symbol", "underlying", "expiry_date", "right", "strike")}
        if qty > 0:
            out[sym] = {
                **meta,
                "symbol": sym,
                "quantity": qty,
                "entry_px": weighted_cost / max(qty, 1) if weighted_cost > 0 else 0.0,
                "entry_ts": earliest_ts,
            }
    return out


def _broker_qty_by_symbol(positions: list[dict[str, Any]]) -> dict[str, int]:
    return broker_option_qty_by_symbol(positions)


def _broker_qty_for_symbol(positions: list[dict[str, Any]], symbol: str) -> int:
    return int(broker_option_qty_by_symbol(positions).get(option_symbol_key(symbol)) or 0)


def _manual_close_detected_detail(legs: list[dict[str, Any]], positions: list[dict[str, Any]]) -> dict[str, Any]:
    qty_by_symbol = broker_option_qty_by_symbol(positions)
    details: list[dict[str, Any]] = []
    for leg in legs:
        sym = option_symbol_key(leg.get("symbol"))
        try:
            requested = max(0, int(float(leg.get("contracts") or 0) or 0))
        except Exception:
            requested = 0
        broker_qty = int(qty_by_symbol.get(sym) or 0)
        if requested > 0 and broker_qty < requested:
            details.append(
                {
                    "symbol": sym,
                    "requested_contracts": requested,
                    "broker_quantity": broker_qty,
                    "missing_contracts": requested - broker_qty,
                }
            )
    return {
        "step": "preflight_option_sell_position_guard",
        "error": "manual_close_detected_or_position_insufficient",
        "details": details,
        **_worker_account_context(None),
    }


def _preflight_sell_legs_against_broker_positions(
    legs: list[dict[str, Any]],
    raw: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    positions, err = _api_get_option_positions_checked(raw)
    if positions is None:
        return False, {
            "step": "preflight_option_sell_position_guard",
            "error": "positions_api_unavailable",
            "reason": err or "positions_api_unavailable",
            **_worker_account_context(raw),
        }
    guard = validate_option_sell_covered(legs=legs, positions=positions, allow_same_order_spread_cover=False)
    if guard.get("blocked"):
        detail = _manual_close_detected_detail(legs, positions)
        detail["guard"] = guard
        return False, detail
    return True, {"positions_checked": True}


def _symbols_underlying(symbols: list[str]) -> set[str]:
    out: set[str] = set()
    for sym in symbols:
        meta = _parse_option_symbol_meta(sym)
        if meta and meta.get("underlying"):
            out.add(str(meta.get("underlying") or "").strip().upper())
    return out


def _position_symbol(row: dict[str, Any]) -> str:
    return str(row.get("symbol") or row.get("option_symbol") or "").strip().upper()


def _position_underlying(row: dict[str, Any]) -> str:
    direct = str(row.get("underlying") or row.get("underlying_symbol") or "").strip().upper().replace(".US", "")
    if direct:
        return direct
    meta = _parse_option_symbol_meta(_position_symbol(row))
    return str((meta or {}).get("underlying") or "").strip().upper()


def _managed_option_symbols_for_underlying(
    *,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    symbol: str,
    open_live: dict[str, Any] | None,
    session: Qqq0dteLiveSession | None = None,
) -> set[str]:
    qsym = str(symbol or "").strip().upper().replace(".US", "")
    managed = set(_open_live_symbols(open_live))
    try:
        pos = session.open_position() if isinstance(session, Qqq0dteLiveSession) else None
        for sym in _required_open_symbols(open_live, pos):
            if sym:
                managed.add(str(sym).strip().upper())
    except Exception:
        pass
    try:
        expiry = _expiry_for_resolve(raw, cfg, underlying=symbol)
        lots = _worker_unclosed_lots_from_ledger(symbol=symbol, expiry_date=expiry, raw=raw)
        managed.update(str(x).strip().upper() for x in lots.keys() if str(x).strip())
    except Exception:
        pass
    return {
        sym
        for sym in managed
        if (_parse_option_symbol_meta(sym) or {}).get("underlying") == qsym
    }


def _intraday_unmanaged_position_entry_guard(
    *,
    session: Qqq0dteLiveSession,
    open_live: dict[str, Any] | None,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    symbol: str,
) -> dict[str, Any] | None:
    if not _is_stock_options_intraday_mode(raw):
        return None
    safety = _intraday_safety_config(raw)
    if not bool(safety.get("enabled", True)) or not bool(safety.get("block_entry_when_unmanaged_positions", True)):
        return None
    if session.open_position() is not None:
        return None
    positions, err = _api_get_option_positions_checked(raw)
    if positions is None:
        return {
            "reason": "positions_api_unconfirmed",
            "detail": {"reason": err or "positions_api_unavailable"},
            "blocked": True,
        }
    qsym = str(symbol or "").strip().upper().replace(".US", "")
    managed = _managed_option_symbols_for_underlying(raw=raw, cfg=cfg, symbol=symbol, open_live=open_live, session=session)
    unmanaged: list[dict[str, Any]] = []
    for row in positions:
        psym = _position_symbol(row)
        if not psym:
            continue
        if _position_underlying(row) != qsym:
            continue
        qty = max(0, int(float(row.get("quantity") or 0) or 0))
        if qty <= 0:
            continue
        if psym in managed:
            continue
        unmanaged.append({"symbol": psym, "quantity": qty})
    if not unmanaged:
        return None
    return {
        "reason": "unmanaged_option_positions_detected",
        "blocked": True,
        "symbol": str(symbol or "").strip().upper(),
        "managed_symbols": sorted(managed),
        "positions": unmanaged,
        "count": len(unmanaged),
        **_worker_account_context(raw),
    }


def _extract_order_ids_from_payload(obj: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def walk(x: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(x, dict):
            for key in ("order_id", "orderId"):
                val = str(x.get(key) or "").strip()
                if val and val not in seen:
                    seen.add(val)
                    out.append(val)
            for v in x.values():
                walk(v, depth + 1)
        elif isinstance(x, list):
            for v in x:
                walk(v, depth + 1)

    walk(obj)
    return out


def _order_status_text(row: dict[str, Any]) -> str:
    return str(row.get("status") or row.get("order_status") or "").strip()


def _order_status_kind(row: dict[str, Any]) -> str:
    s = _order_status_text(row).strip().lower().replace("_", "").replace(" ", "")
    if not s:
        return "unknown"
    if s in {"new", "submitted", "pending", "pendingnew", "partiallyfilled", "partialfilled", "partial"}:
        return "active"
    if "partial" in s and "fill" in s:
        return "active"
    if "fill" in s or s in {"done", "completed", "complete", "executed"}:
        return "filled"
    if "cancel" in s or "withdraw" in s:
        return "cancelled"
    if "reject" in s or "fail" in s or "error" in s:
        return "rejected"
    return "unknown"


def _order_requested_qty(row: dict[str, Any]) -> int:
    for key in ("quantity", "contracts", "submitted_quantity", "qty"):
        val = _to_int_or_none(row.get(key))
        if val is not None and val > 0:
            return val
    return 0


def _order_filled_qty(row: dict[str, Any]) -> int:
    for key in (
        "filled_quantity",
        "filled_qty",
        "executed_quantity",
        "executed_qty",
        "dealt_quantity",
        "dealt_qty",
        "filledQuantity",
        "filledQty",
        "dealQuantity",
    ):
        val = _to_int_or_none(row.get(key))
        if val is not None and val > 0:
            return val
    return _order_requested_qty(row) if _order_status_kind(row) == "filled" else 0


def _order_avg_fill_price(row: dict[str, Any]) -> float:
    for key in (
        "avg_fill_price",
        "average_fill_price",
        "executed_price",
        "dealt_avg_price",
        "filledPrice",
        "avgFilledPrice",
        "dealAvgPrice",
        "price",
    ):
        val = _to_float_or_none(row.get(key))
        if val is not None and val > 0:
            return float(val)
    return 0.0


def _order_symbol(row: dict[str, Any]) -> str:
    return str(row.get("symbol") or row.get("option_symbol") or "").strip().upper()


def _order_side(row: dict[str, Any]) -> str:
    return _normalize_order_side_text(row.get("side") or row.get("action"))


def _order_row_for_ledger(row: dict[str, Any], *, fallback: dict[str, Any] | None = None) -> dict[str, Any] | None:
    fallback = fallback if isinstance(fallback, dict) else {}
    oid = str(row.get("order_id") or fallback.get("order_id") or "").strip()
    sym = _order_symbol(row) or str(fallback.get("symbol") or "").strip().upper()
    side = _order_side(row) or _normalize_order_side_text(fallback.get("side"))
    qty = _order_filled_qty(row)
    if qty <= 0:
        qty = _order_requested_qty(row) if _order_status_kind(row) == "filled" else 0
    px = _order_avg_fill_price(row) or _to_float_or_none(fallback.get("price")) or 0.0
    if not oid or not sym or side not in {"buy", "sell"} or qty <= 0 or px <= 0:
        return None
    now = datetime.now(timezone.utc)
    out = {
        "at": now.isoformat(),
        "order_id": oid,
        "symbol": sym,
        "side": side,
        "contracts": int(qty),
        "price": float(px),
        "ts": int(now.timestamp()),
        "source": "order_reconciliation",
        "order_status": _order_status_text(row),
        **_worker_account_context(),
    }
    for key in ("position_group_id", "combo_mode"):
        val = str(row.get(key) or fallback.get(key) or "").strip()
        if val:
            out[key] = val
    return out


def _append_order_lifecycle_from_reconciliation(
    *,
    raw: dict[str, Any],
    reconciliation: dict[str, Any],
    reason: str,
) -> None:
    if not isinstance(reconciliation, dict):
        return
    state = "filled"
    if reconciliation.get("uncertain"):
        state = "uncertain"
    if reconciliation.get("active_orders"):
        state = "active"
    if reconciliation.get("rejected_orders"):
        state = "rejected"
    if reconciliation.get("cancelled_orders") and not reconciliation.get("filled_ledger_rows"):
        state = "cancelled"
    _append_order_lifecycle_event(
        {
            "event": "entry_reconciliation",
            "state": state,
            "reason": reason,
            "order_ids": reconciliation.get("order_ids") if isinstance(reconciliation.get("order_ids"), list) else [],
            "expected_contracts": int(reconciliation.get("expected_contracts") or 0),
            "filled_contracts": int(reconciliation.get("filled_contracts") or 0),
            "all_expected_filled": bool(reconciliation.get("all_expected_filled")),
            "uncertain": bool(reconciliation.get("uncertain")),
            "orders": reconciliation.get("orders") if isinstance(reconciliation.get("orders"), list) else [],
            "active_orders": reconciliation.get("active_orders") if isinstance(reconciliation.get("active_orders"), list) else [],
            "rejected_orders": reconciliation.get("rejected_orders") if isinstance(reconciliation.get("rejected_orders"), list) else [],
            "cancelled_orders": reconciliation.get("cancelled_orders") if isinstance(reconciliation.get("cancelled_orders"), list) else [],
            "unknown_orders": reconciliation.get("unknown_orders") if isinstance(reconciliation.get("unknown_orders"), list) else [],
            **_worker_account_context(raw),
        }
    )


def _reconcile_submitted_entry_orders(
    *,
    raw: dict[str, Any],
    order_response: dict[str, Any],
    expected_legs: list[dict[str, Any]],
    wait_seconds: float = 0.0,
) -> dict[str, Any]:
    order_ids = _extract_order_ids_from_payload(order_response)
    if wait_seconds > 0:
        time.sleep(max(0.0, min(5.0, float(wait_seconds))))
    by_id, query = _api_get_option_orders_by_ids(raw, order_ids)
    fallback_by_id: dict[str, dict[str, Any]] = {}
    for leg in _iter_execution_legs_from_detail({"order": order_response}):
        oid = str(leg.get("order_id") or "").strip()
        if oid:
            fallback_by_id[oid] = leg
    expected_by_symbol = {
        str(x.get("symbol") or "").strip().upper(): max(0, int(float(x.get("contracts") or 0) or 0))
        for x in expected_legs
        if isinstance(x, dict) and str(x.get("symbol") or "").strip()
    }
    expected_meta_by_symbol: dict[str, dict[str, str]] = {}
    for leg in expected_legs:
        if not isinstance(leg, dict):
            continue
        sym = str(leg.get("symbol") or "").strip().upper()
        if not sym:
            continue
        meta = {
            key: str(leg.get(key) or "").strip()
            for key in ("position_group_id", "combo_mode")
            if str(leg.get(key) or "").strip()
        }
        if meta:
            expected_meta_by_symbol[sym] = meta
    positions, pos_err = _api_get_option_positions_checked(raw)
    pos_qty = _broker_qty_by_symbol(positions or []) if isinstance(positions, list) else {}
    rows: list[dict[str, Any]] = []
    filled_rows: list[dict[str, Any]] = []
    active_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    unknown_rows: list[dict[str, Any]] = []
    cancelled_rows: list[dict[str, Any]] = []
    inferred_rows: list[dict[str, Any]] = []
    for oid in order_ids:
        row = by_id.get(oid) or dict(fallback_by_id.get(oid) or {"order_id": oid})
        kind = _order_status_kind(row)
        filled_qty = _order_filled_qty(row)
        ledger_row = _order_row_for_ledger(row, fallback=fallback_by_id.get(oid))
        summary = {
            "order_id": oid,
            "symbol": _order_symbol(row) or str((fallback_by_id.get(oid) or {}).get("symbol") or "").strip().upper(),
            "side": _order_side(row) or _normalize_order_side_text((fallback_by_id.get(oid) or {}).get("side")),
            "status": _order_status_text(row),
            "kind": kind,
            "requested_quantity": _order_requested_qty(row) or max(0, int(float((fallback_by_id.get(oid) or {}).get("contracts") or 0) or 0)),
            "filled_quantity": filled_qty,
            "avg_fill_price": _order_avg_fill_price(row),
        }
        rows.append(summary)
        if ledger_row is not None:
            meta = expected_meta_by_symbol.get(str(ledger_row.get("symbol") or "").strip().upper())
            if meta:
                ledger_row.update(meta)
            filled_rows.append(ledger_row)
        elif kind == "active":
            active_rows.append(summary)
        elif kind == "rejected":
            rejected_rows.append(summary)
        elif kind == "cancelled":
            cancelled_rows.append(summary)
        else:
            unknown_rows.append(summary)
    for sym, expected_qty in expected_by_symbol.items():
        if expected_qty <= 0:
            continue
        if any(str(x.get("symbol") or "").strip().upper() == sym for x in filled_rows):
            continue
        broker_qty = int(pos_qty.get(sym) or 0)
        if broker_qty >= expected_qty:
            match = next((x for x in expected_legs if str(x.get("symbol") or "").strip().upper() == sym), {})
            inferred = {
                "at": datetime.now(timezone.utc).isoformat(),
                "order_id": f"inferred:{sym}:{int(time.time())}",
                "symbol": sym,
                "side": _normalize_order_side_text(match.get("side")) or "buy",
                "contracts": int(expected_qty),
                "price": float(_to_float_or_none(match.get("price")) or 0.0),
                "ts": int(datetime.now(timezone.utc).timestamp()),
                "source": "position_reconciliation",
                "order_status": "broker_position_confirmed",
                **_worker_account_context(raw),
            }
            meta = expected_meta_by_symbol.get(sym)
            if meta:
                inferred.update(meta)
            if inferred["price"] > 0:
                filled_rows.append(inferred)
                inferred_rows.append(inferred)
    expected_total = sum(max(0, int(float(x.get("contracts") or 0) or 0)) for x in expected_legs if isinstance(x, dict))
    filled_total = sum(int(x.get("contracts") or 0) for x in filled_rows)
    all_expected_filled = bool(expected_total > 0 and filled_total >= expected_total)
    uncertain = bool(active_rows or rejected_rows or unknown_rows or query.get("missing") or pos_err)
    if not all_expected_filled:
        uncertain = True
    return {
        "enabled": True,
        "order_ids": order_ids,
        "query": query,
        "orders": rows,
        "expected_contracts": expected_total,
        "filled_contracts": filled_total,
        "all_expected_filled": all_expected_filled,
        "uncertain": uncertain,
        "active_orders": active_rows,
        "rejected_orders": rejected_rows,
        "cancelled_orders": cancelled_rows,
        "unknown_orders": unknown_rows,
        "filled_ledger_rows": filled_rows,
        "inferred_fills": inferred_rows,
        "positions_error": pos_err,
    }


def _intraday_post_entry_order_protection(
    *,
    raw: dict[str, Any],
    order_response: dict[str, Any],
    expected_legs: list[dict[str, Any]],
    dry_run: bool,
) -> tuple[bool, dict[str, Any]]:
    if dry_run or not _is_stock_options_intraday_mode(raw):
        return True, {"enabled": False if not _is_stock_options_intraday_mode(raw) else True, "dry_run": bool(dry_run)}
    safety = _intraday_safety_config(raw)
    if not bool(safety.get("enabled", True)):
        return True, {"enabled": False}
    order_ids = _extract_order_ids_from_payload(order_response)
    submitted_legs = list(_iter_execution_legs_from_detail({"order": order_response}))
    if not submitted_legs and isinstance(order_response.get("order"), dict):
        submitted_legs = [order_response["order"]]
    expected_count = len(expected_legs)
    submitted_count = len(submitted_legs)
    detail: dict[str, Any] = {
        "enabled": True,
        "order_ids": order_ids,
        "expected_legs": expected_count,
        "submitted_legs": submitted_count,
    }
    if order_ids:
        position_group_ids = sorted(
            {
                str(leg.get("position_group_id") or "").strip()
                for leg in expected_legs
                if isinstance(leg, dict) and str(leg.get("position_group_id") or "").strip()
            }
        )
        _append_order_lifecycle_event(
            {
                "event": "entry_submitted",
                "state": "submitted",
                "order_ids": order_ids,
                "expected_legs": expected_count,
                "submitted_legs": submitted_count,
                "orders": submitted_legs,
                "position_group_ids": position_group_ids,
                **_worker_account_context(raw),
            }
        )
    if expected_count > 0 and submitted_count > 0 and submitted_count < expected_count:
        detail["error"] = "partial_multi_leg_submit"
        detail["manual_review_required"] = True
        detail["manual_review_lock"] = _write_manual_review_lock(
            raw=raw,
            reason="partial_multi_leg_submit",
            detail=detail,
            expected_legs=expected_legs,
            order_response=order_response,
        )
        return False, detail
    if not bool(safety.get("cancel_open_entry_orders_after_submit", True)):
        reconciliation = _reconcile_submitted_entry_orders(
            raw=raw,
            order_response=order_response,
            expected_legs=expected_legs,
            wait_seconds=0.0,
        )
        detail["reconciliation"] = reconciliation
        _append_order_lifecycle_from_reconciliation(raw=raw, reconciliation=reconciliation, reason="post_entry_no_cancel")
        if reconciliation.get("all_expected_filled") and not reconciliation.get("uncertain"):
            return True, detail
        detail["error"] = "entry_order_reconciliation_uncertain"
        detail["manual_review_required"] = True
        detail["manual_review_lock"] = _write_manual_review_lock(
            raw=raw,
            reason="entry_order_reconciliation_uncertain",
            detail=detail,
            expected_legs=expected_legs,
            order_response=order_response,
        )
        return False, detail
    delay = max(0.0, min(5.0, float(safety.get("post_entry_check_delay_seconds") or 0.0)))
    reconciliation = _reconcile_submitted_entry_orders(
        raw=raw,
        order_response=order_response,
        expected_legs=expected_legs,
        wait_seconds=delay,
    )
    detail["reconciliation"] = reconciliation
    _append_order_lifecycle_from_reconciliation(raw=raw, reconciliation=reconciliation, reason="post_entry_check")
    active = _api_get_option_orders(raw, status="active")
    active_by_id = {
        str(x.get("order_id") or "").strip(): x
        for x in active
        if str(x.get("order_id") or "").strip()
    }
    lingering = [active_by_id[oid] for oid in order_ids if oid in active_by_id]
    if not lingering and reconciliation.get("all_expected_filled") and not reconciliation.get("uncertain"):
        detail["active_after_submit"] = 0
        return True, detail
    if not lingering and reconciliation.get("uncertain"):
        detail.update(
            {
                "error": "entry_order_reconciliation_uncertain",
                "active_after_submit": 0,
                "manual_review_required": True,
            }
        )
        detail["manual_review_lock"] = _write_manual_review_lock(
            raw=raw,
            reason="entry_order_reconciliation_uncertain",
            detail=detail,
            expected_legs=expected_legs,
            order_response=order_response,
        )
        return False, detail
    cancels: list[dict[str, Any]] = []
    for row in lingering:
        oid = str(row.get("order_id") or "").strip()
        ok, resp = _api_cancel_order(raw, oid)
        cancels.append({"order_id": oid, "ok": ok, "response": resp, "status": _order_status_text(row)})
    if cancels:
        _append_order_lifecycle_event(
            {
                "event": "entry_active_orders_cancelled",
                "state": "cancel_requested",
                "reason": "entry_order_not_fully_filled",
                "order_ids": [str(x.get("order_id") or "") for x in cancels if str(x.get("order_id") or "")],
                "cancels": cancels,
                **_worker_account_context(raw),
            }
        )
    detail.update(
        {
            "error": "entry_order_not_fully_filled",
            "active_after_submit": len(lingering),
            "active_orders": lingering,
            "cancels": cancels,
            "manual_review_required": True,
        }
    )
    detail["manual_review_lock"] = _write_manual_review_lock(
        raw=raw,
        reason="entry_order_not_fully_filled",
        detail=detail,
        expected_legs=expected_legs,
        order_response=order_response,
    )
    return False, detail


def _intraday_cancel_active_option_orders(raw: dict[str, Any], *, reason: str, dry_run: bool = False) -> dict[str, Any] | None:
    if not _is_stock_options_intraday_mode(raw):
        return None
    safety = _intraday_safety_config(raw)
    if not bool(safety.get("enabled", True)) or not bool(safety.get("cancel_active_orders_at_force_close", True)):
        return None
    active = _api_get_option_orders(raw, status="active")
    if not active:
        return {"reason": reason, "active_orders": 0, "cancels": []}
    if dry_run:
        return {"reason": reason, "active_orders": len(active), "cancels": [], "dry_run": True}
    cancels: list[dict[str, Any]] = []
    for row in active:
        oid = str(row.get("order_id") or "").strip()
        if not oid:
            continue
        ok, resp = _api_cancel_order(raw, oid)
        cancels.append({"order_id": oid, "ok": ok, "response": resp, "status": _order_status_text(row)})
    return {"reason": reason, "active_orders": len(active), "cancels": cancels}


def _intraday_entry_time_guard(raw: dict[str, Any], cfg: Qqq0dteConfig) -> dict[str, Any] | None:
    if not _is_stock_options_intraday_mode(raw):
        return None
    safety = _intraday_safety_config(raw)
    if not bool(safety.get("enabled", True)):
        return None
    latest = str(safety.get("latest_entry_hhmm_et") or "").strip()
    force_close = str(safety.get("force_close_hhmm_et") or getattr(cfg, "strangle_force_close_hhmm_et", "") or "").strip()
    now = datetime.now(timezone.utc)
    if latest and is_at_or_after_et_hhmm(to_ny(now, cfg.assume_bars_timezone), latest):
        return {"reason": "intraday_latest_entry_time_passed", "latest_entry_hhmm_et": latest}
    if force_close and is_at_or_after_et_hhmm(to_ny(now, cfg.assume_bars_timezone), force_close):
        return {"reason": "intraday_force_close_time_passed", "force_close_hhmm_et": force_close}
    return None


def _intraday_safety_runtime_summary(
    raw: dict[str, Any],
    action: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not _is_stock_options_intraday_mode(raw):
        return None
    cfg = _intraday_safety_config(raw)
    action = action if isinstance(action, dict) else {}
    detail = action.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    response = detail.get("response")
    response = response if isinstance(response, dict) else {}
    guard = response.get("post_entry_guard")
    guard = guard if isinstance(guard, dict) else detail.get("post_entry_guard")
    guard = guard if isinstance(guard, dict) else {}
    reconciliation = guard.get("reconciliation") if isinstance(guard.get("reconciliation"), dict) else {}
    cancel_scan = action.get("active_order_cancel_scan")
    cancel_scan = cancel_scan if isinstance(cancel_scan, dict) else action.get("active_order_cancel_scan")
    cancel_scan = cancel_scan if isinstance(cancel_scan, dict) else None
    quality = detail.get("detail") if str(detail.get("step") or "").startswith("intraday_option_quality_gate") else None
    quality = quality if isinstance(quality, dict) else None
    reason = str(action.get("reason") or detail.get("reason") or detail.get("error") or guard.get("error") or "")
    action_name = str(action.get("action") or "")
    lock = _load_manual_review_lock()
    if lock and not _manual_review_lock_matches_context(lock, raw):
        lock = None
    lock = lock if isinstance(lock, dict) else None
    try:
        account_gate = _account_level_risk_gate(raw, legs=[], dry_run=True)
    except Exception as e:
        account_gate = {"enabled": bool(_account_risk_config(raw).get("enabled", True)), "blocked": False, "error": str(e)}
    blocked = (
        bool(action.get("blocked"))
        or action_name in {"skip_entry_position_guard"}
        or str(detail.get("step") or "").startswith("intraday_")
        or bool(lock)
    )
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "block_market_order": bool(cfg.get("block_market_order", True)),
        "block_entry_when_unmanaged_positions": bool(cfg.get("block_entry_when_unmanaged_positions", True)),
        "max_bid_ask_spread_pct": cfg.get("max_bid_ask_spread_pct"),
        "latest_entry_hhmm_et": cfg.get("latest_entry_hhmm_et"),
        "account_risk": _account_risk_config(raw),
        "account_risk_gate": account_gate,
        "combo_management": _combo_state_from_ledger(raw),
        "last_action": action_name or None,
        "last_blocked": blocked,
        "last_reason": reason or None,
        "unmanaged_positions_count": int(action.get("count") or 0) if action.get("reason") == "unmanaged_option_positions_detected" else 0,
        "post_entry_active_orders": int(guard.get("active_after_submit") or 0),
        "post_entry_manual_review_required": bool(guard.get("manual_review_required")),
        "reconciliation_expected_contracts": int(reconciliation.get("expected_contracts") or 0),
        "reconciliation_filled_contracts": int(reconciliation.get("filled_contracts") or 0),
        "reconciliation_uncertain": bool(reconciliation.get("uncertain")),
        "reconciliation_all_filled": bool(reconciliation.get("all_expected_filled")),
        "order_lifecycle": _order_lifecycle_summary(),
        "manual_review_locked": bool(lock),
        "manual_review_reason": str((lock or {}).get("reason") or "") or None,
        "manual_review_created_at": str((lock or {}).get("created_at") or "") or None,
        "manual_review_order_ids": (lock or {}).get("order_ids") if isinstance((lock or {}).get("order_ids"), list) else [],
        "manual_review_expected_symbols": (lock or {}).get("expected_symbols") if isinstance((lock or {}).get("expected_symbols"), list) else [],
        "manual_review_lock": lock,
        "force_close_active_orders": int(cancel_scan.get("active_orders") or 0) if cancel_scan else 0,
        "quality_blocks": quality.get("blocks") if isinstance(quality, dict) and isinstance(quality.get("blocks"), list) else [],
    }


def _sync_session_after_manual_close(
    session: Qqq0dteLiveSession,
    open_live: dict[str, Any] | None,
    raw: dict[str, Any],
) -> dict[str, Any] | None:
    pos = session.open_position()
    if pos is None or not isinstance(open_live, dict):
        return open_live
    positions = _api_get_option_positions(raw)
    side = str(getattr(pos, "side", "") or "")
    if side == "strangle":
        cs = str(open_live.get("call_symbol") or "").strip().upper()
        ps = str(open_live.get("put_symbol") or "").strip().upper()
        call_on = bool(getattr(pos, "strangle_call_active", True))
        put_on = bool(getattr(pos, "strangle_put_active", True))
        need_qty = max(1, int(getattr(pos, "contracts", 1) or 1))
        if call_on and cs and _broker_qty_for_symbol(positions, cs) < need_qty:
            session.apply_strangle_leg_closed("call", 0.0)
        if put_on and ps and _broker_qty_for_symbol(positions, ps) < need_qty:
            session.apply_strangle_leg_closed("put", 0.0)
        return _open_live_after_strangle_partial(session, open_live)
    if side == "double_strangle":
        leg_symbols = open_live.get("leg_symbols")
        if not isinstance(leg_symbols, dict):
            return open_live
        legs = getattr(pos, "double_strangle_legs", None)
        if not isinstance(legs, dict):
            return open_live
        need_qty = max(1, int(getattr(pos, "contracts", 1) or 1))
        for key in DOUBLE_STRANGLE_LEG_KEYS:
            leg = legs.get(key)
            if not isinstance(leg, dict) or not bool(leg.get("active", True)):
                continue
            sym = str(leg_symbols.get(key) or "").strip().upper()
            if sym and _broker_qty_for_symbol(positions, sym) < need_qty:
                session.apply_double_strangle_leg_closed(key, 0.0)
        return _open_live_after_double_strangle_partial(session, open_live)
    sym = str(open_live.get("symbol") or "").strip().upper()
    need_qty = max(1, int(getattr(pos, "contracts", 1) or 1))
    if sym and _broker_qty_for_symbol(positions, sym) < need_qty:
        session.clear_open_position()
        return None
    return open_live


def _detect_unrecovered_worker_positions(
    *,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    symbol: str,
    positions: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    checked_err: str | None = None
    if positions is None:
        positions, checked_err = _api_get_option_positions_checked(raw)
    if positions is None:
        return None, {"reason": checked_err or "positions_api_unavailable"}

    expiry = _expiry_for_resolve(raw, cfg, underlying=symbol)
    lots = _worker_unclosed_lots_from_ledger(symbol=symbol, expiry_date=expiry, raw=raw)
    if not lots:
        return [], None
    qty_by_symbol = _broker_qty_by_symbol(positions)
    live: list[dict[str, Any]] = []
    for sym, lot in lots.items():
        broker_qty = int(qty_by_symbol.get(sym) or 0)
        if broker_qty <= 0:
            continue
        qty = min(broker_qty, int(lot.get("quantity") or 0))
        if qty <= 0:
            continue
        live.append({**lot, "quantity": qty, "broker_quantity": broker_qty})
    live.sort(key=lambda x: (int(x.get("entry_ts") or 0), str(x.get("right") or ""), float(x.get("strike") or 0.0)))
    return live, None


def _open_live_symbols(open_live: dict[str, Any] | None) -> set[str]:
    if not isinstance(open_live, dict):
        return set()
    syms = set()
    for key in ("symbol", "call_symbol", "put_symbol"):
        sym = str(open_live.get(key) or "").strip().upper()
        if sym:
            syms.add(sym)
    leg_symbols = open_live.get("leg_symbols")
    if isinstance(leg_symbols, dict):
        for sym_raw in leg_symbols.values():
            sym = str(sym_raw or "").strip().upper()
            if sym:
                syms.add(sym)
    return syms


def _unrecovered_entry_guard(
    *,
    session: Qqq0dteLiveSession,
    open_live: dict[str, Any] | None,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    symbol: str,
) -> dict[str, Any] | None:
    if session.open_position() is not None:
        return None
    live, err = _detect_unrecovered_worker_positions(raw=raw, cfg=cfg, symbol=symbol)
    if err is not None:
        return {
            "reason": "positions_api_unconfirmed",
            "detail": err,
            "blocked": True,
        }
    known = _open_live_symbols(open_live)
    unknown = [x for x in (live or []) if str(x.get("symbol") or "").strip().upper() not in known]
    if not unknown:
        return None
    return {
        "reason": "unrecovered_worker_open_position",
        "blocked": True,
        "symbols": [str(x.get("symbol") or "") for x in unknown],
        "positions": unknown,
    }


def _open_state_path_for_symbol(symbol: str, *, multi_symbol: bool = False) -> str:
    if not multi_symbol:
        return OPEN_STATE_FILE
    sym = re.sub(r"[^A-Z0-9_]+", "_", str(symbol or "").strip().upper().replace(".", "_")).strip("_")
    if not sym:
        sym = "UNKNOWN"
    return os.path.join(os.path.dirname(OPEN_STATE_FILE), f"live_worker_open_state_{sym}.json")


def _remove_open_state_file(state_file: str | None = None) -> None:
    path = state_file or OPEN_STATE_FILE
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _load_open_state_snapshot(state_file: str | None = None) -> dict[str, Any] | None:
    path = state_file or OPEN_STATE_FILE
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _open_state_snapshot_saved_at(state_file: str | None = None) -> str | None:
    snap = _load_open_state_snapshot(state_file)
    if not isinstance(snap, dict):
        return None
    ts = str(snap.get("saved_at") or "").strip()
    return ts or None


def _sync_open_state_snapshot(
    *,
    session: Qqq0dteLiveSession,
    open_live: dict[str, Any] | None,
    symbol: str,
    session_date: str,
    raw: dict[str, Any] | None = None,
    state_file: str | None = None,
) -> None:
    path = state_file or OPEN_STATE_FILE
    pos = session.open_position()
    payload = _open_position_to_payload(pos)
    required_symbols = _required_open_symbols(open_live, pos)
    if payload is None or not required_symbols:
        _remove_open_state_file(path)
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    doc = {
        "version": 1,
        "instance": _WORKER_INSTANCE,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "symbol": str(symbol or "").strip().upper(),
        "session_date": str(session_date or "").strip(),
        "owner_id": _API_LOCAL_OWNER or None,
        "account_id": _effective_account_id(raw) or None,
        "broker_provider": _effective_broker_provider(raw) or None,
        "open_live": open_live,
        "required_symbols": required_symbols,
        "open_position": payload,
    }
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _restore_open_state_from_snapshot(
    *,
    session: Qqq0dteLiveSession,
    raw: dict[str, Any],
    symbol: str,
    positions: list[dict[str, Any]] | None = None,
    state_file: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    path = state_file or OPEN_STATE_FILE
    snap = _load_open_state_snapshot(path)
    if not isinstance(snap, dict):
        return None, None
    if str(snap.get("instance") or _WORKER_INSTANCE).strip().lower() != _WORKER_INSTANCE:
        return None, None
    qsym = str(symbol or "").strip().upper()
    if str(snap.get("symbol") or "").strip().upper() != qsym:
        return None, None
    snap_owner = str(snap.get("owner_id") or "").strip().lower()
    if _API_LOCAL_OWNER and not snap_owner:
        return None, {
            "restored": False,
            "source": "snapshot",
            "reason": "snapshot_owner_missing",
            **_worker_account_context(raw),
        }
    if snap_owner and _API_LOCAL_OWNER and snap_owner != _API_LOCAL_OWNER:
        return None, None
    snap_account_id = str(snap.get("account_id") or "").strip()
    current_account_id = _effective_account_id(raw)
    if current_account_id and not snap_account_id:
        return None, {
            "restored": False,
            "source": "snapshot",
            "reason": "snapshot_account_missing",
            "account_id": current_account_id,
            **_worker_account_context(raw),
        }
    if snap_account_id and current_account_id and snap_account_id != current_account_id:
        return None, {
            "restored": False,
            "source": "snapshot",
            "reason": "snapshot_account_mismatch",
            "snapshot_account_id": snap_account_id,
            "account_id": current_account_id,
            **_worker_account_context(raw),
        }
    snap_broker_provider = str(snap.get("broker_provider") or "").strip().lower()
    current_broker_provider = _effective_broker_provider(raw)
    if current_broker_provider and not snap_broker_provider:
        return None, {
            "restored": False,
            "source": "snapshot",
            "reason": "snapshot_broker_missing",
            "broker_provider": current_broker_provider,
            **_worker_account_context(raw),
        }
    if snap_broker_provider and current_broker_provider and snap_broker_provider != current_broker_provider:
        return None, {
            "restored": False,
            "source": "snapshot",
            "reason": "snapshot_broker_mismatch",
            "snapshot_broker_provider": snap_broker_provider,
            "broker_provider": current_broker_provider,
            **_worker_account_context(raw),
        }
    open_live = snap.get("open_live")
    pos = _open_position_from_payload(snap.get("open_position"))
    if not isinstance(open_live, dict) or pos is None:
        _remove_open_state_file(path)
        return None, None
    required_symbols = _required_open_symbols(open_live, pos)
    if not required_symbols:
        _remove_open_state_file(path)
        return None, None
    broker_positions = positions if positions is not None else _api_get_option_positions(raw)
    qty_by_symbol: dict[str, int] = {}
    for row in broker_positions:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").strip().upper()
        qty = max(0, int(float(row.get("quantity") or 0) or 0))
        if sym and qty > 0:
            qty_by_symbol[sym] = qty
    need_qty = max(1, int(getattr(pos, "contracts", 1) or 1))
    if any(int(qty_by_symbol.get(sym) or 0) < need_qty for sym in required_symbols):
        _remove_open_state_file(path)
        return None, None
    session.restore_open_position(pos)
    session.set_trades_today_count(pos.entry_time.date(), max(1, session.trades_today_count(pos.entry_time.date())))
    meta: dict[str, Any] = {
        "restored": True,
        "source": "snapshot",
        **_worker_account_context(raw),
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_saved_at": snap.get("saved_at"),
        "entry_time": pos.entry_time.isoformat(),
        "contracts": need_qty,
    }
    if str(getattr(pos, "side", "") or "") == "strangle":
        meta.update(
            {
                "mode": "strangle",
                "call_symbol": str(open_live.get("call_symbol") or ""),
                "put_symbol": str(open_live.get("put_symbol") or ""),
            }
        )
    elif str(getattr(pos, "side", "") or "") == "double_strangle":
        meta.update(
            {
                "mode": "double_strangle",
                "leg_symbols": open_live.get("leg_symbols") if isinstance(open_live.get("leg_symbols"), dict) else {},
            }
        )
    else:
        meta.update({"mode": "single", "symbol": str(open_live.get("symbol") or "")})
    return open_live, meta


def _restore_open_state_from_broker(
    *,
    session: Qqq0dteLiveSession,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    symbol: str,
    positions: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """
    从券商真实持仓 + 本地执行账本恢复当前 Worker 的未平仓状态。
    仅恢复当前标的、当前实例对应的 QQQ 期权持仓；不接管其它手工仓位。
    """
    positions = positions if positions is not None else _api_get_option_positions(raw)
    if not positions:
        return None, None
    qsym = str(symbol or "").strip().upper()
    same_day_expiry = _expiry_for_resolve(raw, cfg, underlying=symbol)
    lots = _worker_unclosed_lots_from_ledger(symbol=symbol, expiry_date=same_day_expiry, raw=raw)
    live_rows: list[dict[str, Any]] = []
    qty_by_symbol = _broker_qty_by_symbol(positions)
    for pos in positions:
        sym = str(pos.get("symbol") or "").strip().upper()
        meta = _parse_option_symbol_meta(sym)
        if not meta or meta.get("underlying") != qsym.replace(".US", ""):
            continue
        qty = max(0, int(float(pos.get("quantity") or 0) or 0))
        if qty <= 0:
            continue
        cost = float(pos.get("cost_price") or 0.0)
        lot = lots.get(sym)
        if not isinstance(lot, dict):
            continue
        lot_qty = max(0, int(lot.get("quantity") or 0))
        earliest_ts = int(lot.get("entry_ts") or 0)
        worker_qty = min(qty, lot_qty)
        if worker_qty <= 0:
            continue
        entry_px = cost
        if float(lot.get("entry_px") or 0.0) > 0:
            entry_px = float(lot.get("entry_px") or 0.0)
        live_rows.append(
            {
                **meta,
                "quantity": worker_qty,
                "broker_quantity": int(qty_by_symbol.get(sym) or qty),
                "entry_px": float(entry_px) if entry_px > 0 else float(cost),
                "entry_ts": earliest_ts,
            }
        )
    if not live_rows:
        return None, None
    live_rows.sort(key=lambda x: (str(x.get("expiry_date") or ""), str(x.get("right") or ""), float(x.get("strike") or 0.0)))
    same_day = [x for x in live_rows if str(x.get("expiry_date") or "") == same_day_expiry]
    active = same_day if same_day else live_rows
    latest_entry_ts = max(int(x.get("entry_ts") or 0) for x in active)
    recent_cutoff = latest_entry_ts - 300 if latest_entry_ts > 0 else 0
    recent_active = [x for x in active if int(x.get("entry_ts") or 0) >= recent_cutoff] if recent_cutoff > 0 else list(active)
    calls = [x for x in recent_active if str(x.get("right") or "") == "call"]
    puts = [x for x in recent_active if str(x.get("right") or "") == "put"]
    restored_at = datetime.now(timezone.utc).isoformat()
    if str(getattr(cfg, "strategy_variant", "") or "").strip().lower() == "morning_double_strangle" and len(calls) >= 2 and len(puts) >= 2:
        calls2 = sorted(calls, key=lambda x: float(x.get("strike") or 0.0))
        puts2 = sorted(puts, key=lambda x: float(x.get("strike") or 0.0))
        call_short = calls2[0]
        call_long = calls2[-1]
        put_long = puts2[0]
        put_short = puts2[-1]
        selected = {
            "call_long": call_long,
            "call_short": call_short,
            "put_long": put_long,
            "put_short": put_short,
        }
        entry_ts = min(
            [int(x.get("entry_ts") or 0) for x in selected.values() if int(x.get("entry_ts") or 0) > 0]
            or [int(datetime.now(timezone.utc).timestamp())]
        )
        entry_dt = datetime.fromtimestamp(entry_ts, timezone.utc).astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
        legs = {
            key: {
                "right": str(row.get("right") or ""),
                "strike": float(row.get("strike") or 0.0),
                "entry_px": float(row.get("entry_px") or 0.0),
                "active": True,
                "strikes_otm": int(getattr(cfg, f"double_strangle_{key}_strikes_otm", 0) or 0),
            }
            for key, row in selected.items()
        }
        qty = max(1, min(int(row.get("quantity") or 1) for row in selected.values()))
        cost = sum(float(x.get("entry_px") or 0.0) for x in legs.values())
        pos = OpenPosition(
            side="double_strangle",
            strike=0.0,
            call_strike=float(call_short.get("strike") or 0.0),
            put_strike=float(put_short.get("strike") or 0.0),
            entry_bar_index=0,
            entry_time=entry_dt,
            entry_px=cost,
            call_entry_px=float(legs["call_long"]["entry_px"]) + float(legs["call_short"]["entry_px"]),
            put_entry_px=float(legs["put_long"]["entry_px"]) + float(legs["put_short"]["entry_px"]),
            strangle_original_entry_px=cost,
            contracts=qty,
            call_strikes_otm=int(getattr(cfg, "double_strangle_call_short_strikes_otm", 0) or 0),
            put_strikes_otm=int(getattr(cfg, "double_strangle_put_short_strikes_otm", 0) or 0),
            double_strangle_legs=legs,
        )
        leg_symbols = {key: str(row.get("symbol") or "") for key, row in selected.items()}
        session.restore_open_position(pos)
        session.set_trades_today_count(entry_dt.date(), max(1, session.trades_today_count(entry_dt.date())))
        return (
            {"mode": "double_strangle", "leg_symbols": leg_symbols},
            {
                "restored": True,
                "source": "broker_ledger",
                "mode": "double_strangle",
                **_worker_account_context(raw),
                "restored_at": restored_at,
                "leg_symbols": leg_symbols,
                "entry_time": entry_dt.isoformat(),
                "contracts": pos.contracts,
            },
        )
    if calls and puts:
        call_row = sorted(calls, key=lambda x: (int(x.get("entry_ts") or 0), float(x.get("strike") or 0.0)), reverse=True)[0]
        put_row = sorted(puts, key=lambda x: (int(x.get("entry_ts") or 0), float(x.get("strike") or 0.0)), reverse=True)[0]
        entry_ts = min([x for x in [int(call_row.get("entry_ts") or 0), int(put_row.get("entry_ts") or 0)] if x > 0] or [int(datetime.now(timezone.utc).timestamp())])
        entry_dt = datetime.fromtimestamp(entry_ts, timezone.utc).astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
        pos = OpenPosition(
            side="strangle",
            strike=0.0,
            call_strike=float(call_row.get("strike") or 0.0),
            put_strike=float(put_row.get("strike") or 0.0),
            entry_bar_index=0,
            entry_time=entry_dt,
            entry_px=float(call_row.get("entry_px") or 0.0) + float(put_row.get("entry_px") or 0.0),
            call_entry_px=float(call_row.get("entry_px") or 0.0),
            put_entry_px=float(put_row.get("entry_px") or 0.0),
            strangle_original_entry_px=float(call_row.get("entry_px") or 0.0) + float(put_row.get("entry_px") or 0.0),
            contracts=max(1, min(int(call_row.get("quantity") or 1), int(put_row.get("quantity") or 1))),
            call_strikes_otm=int(getattr(cfg, "call_strikes_otm", 0) or 0),
            put_strikes_otm=int(getattr(cfg, "put_strikes_otm", 0) or 0),
        )
        session.restore_open_position(pos)
        session.set_trades_today_count(entry_dt.date(), max(1, session.trades_today_count(entry_dt.date())))
        return (
            {"mode": "strangle", "call_symbol": str(call_row.get("symbol") or ""), "put_symbol": str(put_row.get("symbol") or "")},
            {
                "restored": True,
                "source": "broker_ledger",
                "mode": "strangle",
                **_worker_account_context(raw),
                "restored_at": restored_at,
                "call_symbol": str(call_row.get("symbol") or ""),
                "put_symbol": str(put_row.get("symbol") or ""),
                "entry_time": entry_dt.isoformat(),
                "contracts": pos.contracts,
            },
        )
    single = sorted(
        recent_active,
        key=lambda x: (int(x.get("entry_ts") or 0), str(x.get("expiry_date") or ""), float(x.get("strike") or 0.0)),
        reverse=True,
    )[0]
    side = "long_call" if str(single.get("right") or "") == "call" else "long_put"
    entry_ts = int(single.get("entry_ts") or 0) or int(datetime.now(timezone.utc).timestamp())
    entry_dt = datetime.fromtimestamp(entry_ts, timezone.utc).astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    pos = OpenPosition(
        side=side,
        strike=float(single.get("strike") or 0.0),
        entry_bar_index=0,
        entry_time=entry_dt,
        entry_px=float(single.get("entry_px") or 0.0),
        contracts=max(1, int(single.get("quantity") or 1)),
    )
    session.restore_open_position(pos)
    session.set_trades_today_count(entry_dt.date(), max(1, session.trades_today_count(entry_dt.date())))
    return (
        {"mode": "single", "symbol": str(single.get("symbol") or "")},
        {
            "restored": True,
            "source": "broker_ledger",
            "mode": "single",
            **_worker_account_context(raw),
            "restored_at": restored_at,
            "symbol": str(single.get("symbol") or ""),
            "entry_time": entry_dt.isoformat(),
            "contracts": pos.contracts,
        },
    )


def _restore_open_state_on_startup(
    *,
    session: Qqq0dteLiveSession,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    symbol: str,
    state_file: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    positions = _api_get_option_positions(raw)
    open_live, meta = _restore_open_state_from_snapshot(
        session=session,
        raw=raw,
        symbol=symbol,
        positions=positions,
        state_file=state_file,
    )
    if open_live:
        return open_live, meta
    if state_file and os.path.abspath(state_file) != os.path.abspath(OPEN_STATE_FILE):
        open_live, meta = _restore_open_state_from_snapshot(
            session=session,
            raw=raw,
            symbol=symbol,
            positions=positions,
            state_file=OPEN_STATE_FILE,
        )
        if open_live:
            return open_live, {
                **(meta or {}),
                "source": "legacy_snapshot",
                "migrated_from": OPEN_STATE_FILE,
                "migrated_to": state_file,
            }
    return _restore_open_state_from_broker(
        session=session,
        raw=raw,
        cfg=cfg,
        symbol=symbol,
        positions=positions,
    )


def _win_singleton_mutex_name() -> str:
    """0dte 与历史版本共用同一互斥体名，避免新旧 Worker 各持一名导致双进程争用同一 pid/runtime。"""
    if _WORKER_INSTANCE == "0dte":
        return "Global\\OpenClaw_QQQ_0DTE_LiveWorker_Singleton_v1"
    return f"Global\\OpenClaw_QQQ_LiveWorker_{_WORKER_INSTANCE}_v1"


def _acquire_process_singleton_or_exit() -> None:
    """
    保证每个实例（0dte / 1dte / …）全机仅一个 live worker。
    Windows：全局 Mutex；其它：flock 锁文件（非阻塞）。
    第二个实例立即退出 0，不写 pid / 不覆盖 runtime。
    """
    global _SINGLETON_WIN_MUTEX_HANDLE, _SINGLETON_POSIX_LOCK_FD
    if os.name == "nt":
        import ctypes

        ERROR_ALREADY_EXISTS = 183
        kernel32 = ctypes.windll.kernel32
        kernel32.SetLastError(0)
        mutex_name = _win_singleton_mutex_name()
        h = kernel32.CreateMutexW(None, False, mutex_name)
        if not h:
            sys.exit(1)
        if int(kernel32.GetLastError() or 0) == ERROR_ALREADY_EXISTS:
            try:
                kernel32.CloseHandle(h)
            except Exception:
                pass
            print(
                f"[qqq_live_worker] instance={_WORKER_INSTANCE} mutex busy (another copy running), exit 0.",
                file=sys.stderr,
            )
            sys.exit(0)
        _SINGLETON_WIN_MUTEX_HANDLE = h
        return
    import fcntl

    try:
        fd = os.open(_SINGLETON_LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
        _SINGLETON_POSIX_LOCK_FD = fd
    except OSError:
        sys.exit(0)


def _release_process_singleton() -> None:
    global _SINGLETON_WIN_MUTEX_HANDLE, _SINGLETON_POSIX_LOCK_FD
    if os.name == "nt":
        import ctypes

        h = _SINGLETON_WIN_MUTEX_HANDLE
        _SINGLETON_WIN_MUTEX_HANDLE = None
        if h:
            try:
                ctypes.windll.kernel32.CloseHandle(h)
            except Exception:
                pass
        return
    import fcntl

    fd = _SINGLETON_POSIX_LOCK_FD
    _SINGLETON_POSIX_LOCK_FD = None
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        os.close(fd)
    except Exception:
        pass


def _write_pid() -> None:
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _remove_pid() -> None:
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def _write_runtime(data: dict[str, Any], *, force: bool = False) -> None:
    global _LAST_RUNTIME_DIGEST, _LAST_RUNTIME_TS
    try:
        ctx = _worker_account_context()
        out = {
            **data,
            **{k: v for k, v in ctx.items() if not data.get(k)},
            "decision_tail_rev": LIVE_WORKER_DECISION_TAIL_REV,
            "decision_tail_path": DECISION_TAIL_FILE,
        }
        digest = json.dumps(out, ensure_ascii=False, default=str, sort_keys=True)
        now = time.monotonic()
        if not force and digest == _LAST_RUNTIME_DIGEST and (now - _LAST_RUNTIME_TS) < 3.0:
            return
        with open(RUNTIME_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
        _LAST_RUNTIME_DIGEST = digest
        _LAST_RUNTIME_TS = now
    except Exception:
        pass


def _remove_runtime() -> None:
    try:
        if os.path.exists(RUNTIME_FILE):
            os.remove(RUNTIME_FILE)
    except Exception:
        pass


def _maybe_refresh_strategy_recommendation(
    *,
    symbol: str,
    cfg: Qqq0dteConfig,
    bars: list[Bar],
    today_d: date,
    rt_fields: dict[str, Any],
    vix_change_pct: float,
) -> None:
    """每 10 分钟写入一次系统推荐 JSON，仅供前端展示，与下单无关。"""
    global _LAST_STRATEGY_REC_WALL
    now = time.time()
    if _LAST_STRATEGY_REC_WALL and (now - _LAST_STRATEGY_REC_WALL) < 600.0:
        return
    try:
        from mcp_server.strategy_qqq_0dte.strategy_recommendation import compute_strategy_recommendation

        payload = compute_strategy_recommendation(
            symbol=symbol,
            cfg=cfg,
            bars=bars,
            today_d=today_d,
            rt_fields=rt_fields,
            vix_change_pct=float(vix_change_pct),
        )
        out_path = os.path.join(ROOT, "data", f"qqq_{_WORKER_INSTANCE}", "strategy_recommendation.json")
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
        _LAST_STRATEGY_REC_WALL = now
        try:
            err_path = os.path.join(ROOT, "data", f"qqq_{_WORKER_INSTANCE}", "strategy_recommendation_error.json")
            if os.path.isfile(err_path):
                os.remove(err_path)
        except Exception:
            pass
    except Exception as e:
        err_path = os.path.join(ROOT, "data", f"qqq_{_WORKER_INSTANCE}", "strategy_recommendation_error.json")
        try:
            ep = os.path.dirname(err_path)
            if ep:
                os.makedirs(ep, exist_ok=True)
            with open(err_path, "w", encoding="utf-8") as ef:
                json.dump(
                    {
                        "at": datetime.now(timezone.utc).isoformat(),
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    },
                    ef,
                    ensure_ascii=False,
                    indent=2,
                )
                ef.write("\n")
        except Exception:
            pass
        print(f"[qqq_0dte_live_worker] strategy_recommendation failed: {e}", file=sys.stderr)


def _load_worker_config(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve_cfg(raw: dict[str, Any]) -> tuple[Qqq0dteConfig, dict[str, Any]]:
    strat = raw.get("strategy_config") if isinstance(raw.get("strategy_config"), dict) else {}
    cfg = Qqq0dteConfig.from_dict(strat)
    # 网关/券商返回的无时区 K 线时刻，在境内环境常为「北京时间墙钟」而非 UTC。
    # 顶层 kline_wall_clock_timezone 优先于 strategy_config.assume_bars_timezone，避免与错误 UTC 假设冲突。
    kwtz = raw.get("kline_wall_clock_timezone")
    if isinstance(kwtz, str) and kwtz.strip():
        cfg.assume_bars_timezone = kwtz.strip()
    else:
        top_tz = raw.get("assume_bars_timezone")
        if isinstance(top_tz, str) and top_tz.strip():
            cfg.assume_bars_timezone = top_tz.strip()
    return cfg, raw


def _bar_to_utc_iso(dt: datetime, cfg: Qqq0dteConfig) -> str:
    """
    Bar.date 在引擎里多为 naive：按 cfg.assume_bars_timezone 视为该时区的墙钟时刻，
    再转为 UTC，输出与 last_loop_at 一致的带偏移 ISO（+00:00），避免与无时区字符串混用产生误解。
    """
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).isoformat()
    tz_name = (getattr(cfg, "assume_bars_timezone", None) or "UTC").strip() or "UTC"
    try:
        local_tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        local_tz = timezone.utc
    return dt.replace(tzinfo=local_tz).astimezone(timezone.utc).isoformat()


def _bar_to_utc_datetime(dt: datetime, cfg: Qqq0dteConfig) -> datetime:
    """将 bar 墙钟时刻规范为 UTC aware datetime。"""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    tz_name = (getattr(cfg, "assume_bars_timezone", None) or "UTC").strip() or "UTC"
    try:
        local_tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        local_tz = timezone.utc
    return dt.replace(tzinfo=local_tz).astimezone(timezone.utc)


def _bar_debug_fields(dt: datetime, cfg: Qqq0dteConfig) -> dict[str, Any]:
    naive = dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
    return {
        "last_bar": _bar_to_utc_iso(dt, cfg),
        "last_bar_naive_wall": naive.isoformat(sep="T", timespec="seconds"),
        "assume_bars_timezone": str(getattr(cfg, "assume_bars_timezone", None) or "UTC"),
    }


def _action_bar_fields(dt: datetime, cfg: Qqq0dteConfig) -> dict[str, str]:
    naive = dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
    return {
        "bar_utc": _bar_to_utc_iso(dt, cfg),
        "bar_naive_wall": naive.isoformat(sep="T", timespec="seconds"),
    }


def _append_decision_tail(
    *,
    symbol: str,
    session_date: str,
    bar_dt: datetime,
    cfg: Qqq0dteConfig,
    logs: list[Any],
    action: dict[str, Any],
) -> None:
    """
    追加实盘每根 K 线决策摘要（JSONL），用于定位“为何没下单/为何跳过”。
    自动做轻量截断，避免文件无限膨胀。
    """
    try:
        parent = os.path.dirname(DECISION_TAIL_FILE)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.isfile(DECISION_TAIL_FILE):
            sz = int(os.path.getsize(DECISION_TAIL_FILE) or 0)
            if sz > max(1024, DECISION_TAIL_MAX_BYTES):
                keep = max(512, min(sz, DECISION_TAIL_KEEP_BYTES))
                with open(DECISION_TAIL_FILE, "rb") as f:
                    if keep < sz:
                        f.seek(sz - keep)
                    tail = f.read()
                # 从下一行开头截断，尽量保证 JSONL 行完整
                cut = tail.find(b"\n")
                if cut >= 0 and cut + 1 < len(tail):
                    tail = tail[cut + 1 :]
                with open(DECISION_TAIL_FILE, "wb") as f:
                    f.write(tail)
                    if tail and not tail.endswith(b"\n"):
                        f.write(b"\n")
        payload = {
            "at": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "session_date": session_date,
            **_worker_account_context(),
            **_action_bar_fields(bar_dt, cfg),
            "action": action,
            "logs": logs if isinstance(logs, list) else [],
        }
        with open(DECISION_TAIL_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str))
            f.write("\n")
    except Exception as e:
        global _DECISION_TAIL_FAIL_LOGGED
        if not _DECISION_TAIL_FAIL_LOGGED:
            _DECISION_TAIL_FAIL_LOGGED = True
            print(f"[qqq_0dte_live_worker] decision_tail write failed: {e}", file=sys.stderr)


def _fetch_bars(symbol: str, days: int, kline: str) -> tuple[list[Bar], str]:
    if _USE_API_PROXY:
        q = urllib.parse.urlencode({"symbol": symbol.upper(), "days": int(days), "kline": str(kline), "priority": "high"})
        data = _api_get_json(f"/internal/longport/history-bars?{q}", timeout=max(_API_TIMEOUT, 20.0))
        items = data.get("items") if isinstance(data, dict) else None
        source = str(data.get("source") or "internal_longport") if isinstance(data, dict) else "internal_longport"
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
                out.sort(key=lambda b: b.date)
                return out, source
        return [], source
    return [], "disabled"


def _quote_positive_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        p = float(v)
    except Exception:
        return None
    return p if p > 0 else None


def _fetch_realtime_bid_last(symbol: str) -> tuple[float | None, float | None]:
    """返回 (bid, last)；bid 优先盘口/quote 字段，用于止盈盯市；last 用于止损。"""
    q = urllib.parse.urlencode({"symbol": str(symbol).strip().upper()})
    data = _api_get_json(f"/internal/longport/quote?{q}", timeout=min(_API_TIMEOUT, 8.0))
    if not isinstance(data, dict) or not bool(data.get("available")):
        return None, None
    last = _quote_positive_float(data.get("last")) or _quote_positive_float(data.get("last_done"))
    bid = (
        _quote_positive_float(data.get("bid"))
        or _quote_positive_float(data.get("best_bid"))
        or _quote_positive_float(data.get("bid_price"))
    )
    if bid is None:
        bids = data.get("bids")
        if isinstance(bids, list) and bids:
            b0 = bids[0]
            if isinstance(b0, dict):
                bid = _quote_positive_float(b0.get("price")) or _quote_positive_float(b0.get("bid"))
    return bid, last


def _fetch_realtime_last(symbol: str) -> float | None:
    """取单个标的实时最新价（可用于期权 OPRA）。"""
    _, last = _fetch_realtime_bid_last(symbol)
    return last


def _strangle_leg_tp_sl(bid: float | None, last: float | None) -> tuple[float | None, float | None]:
    """
    止盈优先 bid（更保守、可成交），止损仅使用 last。
    说明：0DTE 盘口在开仓后短时间常出现 bid 深折价；若止损回退到 bid，容易被点差误触发「买入即止损」。
    """
    tp = bid if bid is not None else last
    sl = last
    return tp, sl


def _fetch_realtime_quote(symbol: str) -> tuple[dict[str, Any] | None, str]:
    q = urllib.parse.urlencode({"symbol": str(symbol).strip().upper()})
    data = _api_get_json(f"/internal/longport/quote?{q}", timeout=min(_API_TIMEOUT, 8.0))
    if not isinstance(data, dict) or not bool(data.get("available")):
        source = str(data.get("source") or "internal_longport") if isinstance(data, dict) else "internal_longport"
        return None, source
    return data, str(data.get("source") or "internal_longport")


def _runtime_realtime_quote_fields(symbol: str, quote: dict[str, Any] | None, source: str | None = None) -> dict[str, Any]:
    """写入 runtime 的实时标的价格快照（供 setup 状态直接展示）。"""
    out: dict[str, Any] = {"realtime_quote_symbol": str(symbol or "").strip().upper()}
    if source:
        out["realtime_quote_source"] = str(source)
    if not isinstance(quote, dict):
        out["realtime_quote"] = {"available": False}
        return out
    last = quote.get("last")
    prev = quote.get("prev_close")
    chg_pct: float | None = None
    try:
        if prev is not None and float(prev) > 0 and last is not None:
            prev_f = max(float(prev), 1e-12)
            chg_pct = round((float(last) - float(prev)) / prev_f * 100.0, 4)
    except Exception:
        chg_pct = None
    out["realtime_quote"] = {
        "available": bool(quote.get("available", False)),
        "last": last,
        "prev_close": prev,
        "change_pct": chg_pct,
        "timestamp": quote.get("timestamp"),
    }
    return out


def _today_bars(cfg: Qqq0dteConfig, bars: list[Bar]) -> tuple[date, list[Bar]]:
    today_d = ny_date(datetime.now(timezone.utc), cfg.assume_bars_timezone)
    out = [b for b in bars if ny_date(b.date, cfg.assume_bars_timezone) == today_d]
    out.sort(key=lambda b: b.date)
    return today_d, out


def _fetch_option_expiries(symbol: str) -> list[date]:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return []
    q = urllib.parse.urlencode({"symbol": sym})
    data = _api_get_json(f"/options/expiries?{q}", timeout=min(_API_TIMEOUT, 8.0))
    xs = data.get("expiries") if isinstance(data, dict) else None
    if not isinstance(xs, list):
        return []
    out: list[date] = []
    for x in xs:
        try:
            out.append(date.fromisoformat(str(x)))
        except Exception:
            continue
    out.sort()
    return out


def _add_weekday_days(base: date, offset_days: int) -> date:
    if offset_days <= 0:
        return base
    d = base
    moved = 0
    while moved < offset_days:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            moved += 1
    return d


def _expiry_for_resolve(raw: dict[str, Any], cfg: Qqq0dteConfig, *, underlying: str) -> str | None:
    exp_raw = raw.get("expiry_date")
    exp_override: date | None = None
    if isinstance(exp_raw, str) and exp_raw.strip():
        try:
            exp_override = date.fromisoformat(exp_raw.strip())
        except Exception:
            exp_override = None
    try:
        off = max(0, int(raw.get("expiry_offset_days", 0) or 0))
    except Exception:
        off = 0
    base = ny_date(datetime.now(timezone.utc), cfg.assume_bars_timezone)
    expiries = _fetch_option_expiries(underlying)

    # 显式日期优先：若当天无链，自动向后贴合到最近可交易到期日，避免周末/假日空链。
    if exp_override is not None:
        if not expiries:
            return exp_override.isoformat()
        for d in expiries:
            if d >= exp_override:
                return d.isoformat()
        return expiries[-1].isoformat()

    # 默认走交易日偏移：从「>= base」的可交易到期日中按偏移取第 N 个。
    if expiries:
        future = [d for d in expiries if d >= base]
        if future:
            idx = min(off, len(future) - 1)
            return future[idx].isoformat()
        return expiries[-1].isoformat()

    # 兜底：无 expiries 数据时至少按工作日偏移，避免 +1 落到周六。
    return _add_weekday_days(base, off).isoformat()


def _place_order(
    legs_payload: dict[str, Any],
    raw: dict[str, Any],
    *,
    dry_run: bool,
) -> tuple[bool, dict[str, Any]]:
    if dry_run:
        return True, {"dry_run": True, "would_submit": legs_payload}
    tok = raw.get("confirmation_token")
    body = {**legs_payload, "confirmation_token": tok}
    account_id = _effective_account_id(raw)
    if account_id:
        body["account_id"] = account_id
    ok, resp = _api_post_json("/options/order", body, timeout=_API_TIMEOUT)
    return ok, resp


def _legs_from_order_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    legs = payload.get("legs")
    if isinstance(legs, list):
        return [x for x in legs if isinstance(x, dict)]
    sym = str(payload.get("symbol") or "").strip()
    side = str(payload.get("side") or "").strip()
    contracts = payload.get("contracts")
    if sym and side and contracts:
        leg = {"symbol": sym, "side": side, "contracts": contracts, "price": payload.get("price")}
        for key in ("position_group_id", "combo_mode"):
            val = str(payload.get(key) or "").strip()
            if val:
                leg[key] = val
        return [leg]
    return []


def _place_order_with_intraday_protection(
    legs_payload: dict[str, Any],
    raw: dict[str, Any],
    *,
    dry_run: bool,
) -> tuple[bool, dict[str, Any]]:
    legs = _legs_from_order_payload(legs_payload)
    account_gate: dict[str, Any] | None = None
    if _is_stock_options_intraday_mode(raw):
        safety = _intraday_safety_config(raw)
        if bool(safety.get("enabled", True)) and bool(safety.get("block_market_order", True)):
            bad = [
                {
                    "symbol": str(leg.get("symbol") or "").strip().upper(),
                    "side": _normalize_order_side_text(leg.get("side")),
                    "price": leg.get("price"),
                }
                for leg in legs
                if _normalize_order_side_text(leg.get("side")) == "buy" and (_to_float_or_none(leg.get("price")) or 0.0) <= 0
            ]
            if bad:
                return False, {
                    "step": "intraday_order_guard",
                    "error": "market_order_blocked",
                    "legs": bad,
                }
        account_gate = _account_level_risk_gate(raw, legs=legs, dry_run=dry_run)
        if bool(account_gate.get("blocked")):
            return False, {
                "step": "account_level_risk_gate",
                "error": "account_risk_blocked",
                "account_risk_gate": account_gate,
            }
    ok, resp = _place_order(legs_payload, raw, dry_run=dry_run)
    if isinstance(resp, dict) and account_gate is not None:
        resp = {**resp, "account_risk_gate": account_gate}
    if not ok:
        if _is_stock_options_intraday_mode(raw) and not dry_run:
            order_ids = _extract_order_ids_from_payload(resp)
            cancels: list[dict[str, Any]] = []
            for oid in order_ids:
                ok_cancel, cancel_resp = _api_cancel_order(raw, oid)
                cancels.append({"order_id": oid, "ok": ok_cancel, "response": cancel_resp})
            if cancels and isinstance(resp, dict):
                resp = {**resp, "intraday_failed_submit_cancel_scan": {"order_ids": order_ids, "cancels": cancels}}
            if order_ids and isinstance(resp, dict):
                _append_order_lifecycle_event(
                    {
                        "event": "entry_submit_failed_after_order_ids",
                        "state": "manual_review",
                        "order_ids": order_ids,
                        "response": resp,
                        "cancels": cancels,
                        **_worker_account_context(raw),
                    }
                )
                resp = {
                    **resp,
                    "manual_review_required": True,
                    "manual_review_lock": _write_manual_review_lock(
                        raw=raw,
                        reason="entry_submit_failed_after_order_ids",
                        detail=resp,
                        expected_legs=legs,
                        order_response=resp,
                    ),
                }
        return False, resp
    ok_guard, guard = _intraday_post_entry_order_protection(
        raw=raw,
        order_response=resp,
        expected_legs=legs,
        dry_run=dry_run,
    )
    if not ok_guard:
        return False, {"order": resp, "post_entry_guard": guard}
    if isinstance(resp, dict):
        resp = {**resp, "post_entry_guard": guard}
        reconciliation = guard.get("reconciliation") if isinstance(guard, dict) else None
        if isinstance(reconciliation, dict) and isinstance(reconciliation.get("filled_ledger_rows"), list):
            resp["reconciled_ledger_rows"] = reconciliation.get("filled_ledger_rows")
    return True, resp


def _process_resolve_and_enter(
    intent: TradeIntent,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    *,
    dry_run: bool,
    resolve_defaults: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    entry_time_guard = _intraday_entry_time_guard(raw, cfg)
    if entry_time_guard is not None:
        return False, {"step": "intraday_entry_time_guard", **entry_time_guard}
    exp = _expiry_for_resolve(raw, cfg, underlying=intent.underlying)
    resolve_body = {
        "symbol": intent.underlying,
        "strike": float(intent.strike),
        "right": intent.right,
        "expiry_date": exp,
        "strike_window": float(raw.get("resolve_strike_window", resolve_defaults.get("strike_window", 5.0))),
        "standard_only": bool(raw.get("resolve_standard_only", resolve_defaults.get("standard_only", False))),
        "max_strike_diff": float(raw.get("resolve_max_strike_diff", resolve_defaults.get("max_strike_diff", 1.5))),
        "use_ask_for_buy_limit": True,
    }
    ok_r, res_r = _api_post_json("/strategy/qqq-0dte/resolve-contract", resolve_body, timeout=_API_TIMEOUT)
    if not ok_r or not bool(res_r.get("ok")):
        return False, {"step": "resolve-contract", "response": res_r}
    op = str(res_r.get("symbol") or "").strip().upper()
    if not op:
        return False, {"step": "resolve-contract", "error": "missing symbol", "response": res_r}
    limit_px = float(res_r.get("suggested_limit_price_per_share") or 0.0)
    if limit_px > 0:
        limit_px = _quantize_limit_price_per_share(limit_px, side="buy")
    legs_src = intent_to_legs_resolved(intent, op, limit_price_per_share=limit_px if limit_px > 0 else None)
    legs_for_guard = _legs_from_order_payload(legs_src)
    if legs_for_guard:
        gid = _position_group_id_for_legs(legs_for_guard, mode_name=str(intent.reason or "single"))
        legs_src["position_group_id"] = gid
        legs_src["combo_mode"] = str(intent.reason or "single")
        for leg in legs_for_guard:
            leg["position_group_id"] = gid
            leg["combo_mode"] = str(intent.reason or "single")
    if legs_for_guard:
        ok_q, q_detail = _validate_intraday_option_quote_quality(raw=raw, resolve_response=res_r, leg=legs_for_guard[0])
        if not ok_q:
            return False, {"step": "intraday_option_quality_gate", "error": "option_quote_quality_failed", "detail": q_detail, "response": res_r}
    ok_o, res_o = _place_order_with_intraday_protection(legs_src, raw, dry_run=dry_run)
    if not ok_o:
        return False, {"step": "options/order", "response": res_o}
    return True, {"step": "entry", "symbol": op, "order": res_o}


def _process_resolve_and_enter_multi_leg(
    intents: list[TradeIntent] | tuple[TradeIntent, ...],
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    *,
    dry_run: bool,
    resolve_defaults: dict[str, Any],
    mode_name: str,
) -> tuple[bool, dict[str, Any]]:
    entry_time_guard = _intraday_entry_time_guard(raw, cfg)
    if entry_time_guard is not None:
        return False, {"step": f"intraday_entry_time_guard-{mode_name}", **entry_time_guard}
    if not intents:
        return False, {"step": f"resolve-contract-{mode_name}", "error": "no intents"}
    exp = _expiry_for_resolve(raw, cfg, underlying=intents[0].underlying)
    legs: list[dict[str, Any]] = []
    meta_steps: list[dict[str, Any]] = []
    for intent in intents:
        resolve_body = {
            "symbol": intent.underlying,
            "strike": float(intent.strike),
            "right": intent.right,
            "expiry_date": exp,
            "strike_window": float(raw.get("resolve_strike_window", resolve_defaults.get("strike_window", 5.0))),
            "standard_only": bool(raw.get("resolve_standard_only", resolve_defaults.get("standard_only", False))),
            "max_strike_diff": float(raw.get("resolve_max_strike_diff", resolve_defaults.get("max_strike_diff", 1.5))),
            "use_ask_for_buy_limit": True,
        }
        ok_r, res_r = _api_post_json("/strategy/qqq-0dte/resolve-contract", resolve_body, timeout=_API_TIMEOUT)
        if not ok_r or not bool(res_r.get("ok")):
            return False, {"step": f"resolve-contract-{mode_name}", "response": res_r, "partial_legs": legs}
        op = str(res_r.get("symbol") or "").strip().upper()
        if not op:
            return False, {"step": f"resolve-contract-{mode_name}", "error": "missing symbol", "response": res_r}
        limit_px = float(res_r.get("suggested_limit_price_per_share") or 0.0)
        if limit_px > 0:
            limit_px = _quantize_limit_price_per_share(limit_px, side="buy")
        leg = {
            "symbol": op,
            "side": "buy",
            "contracts": max(1, int(intent.contracts)),
            "price": max(0.0, limit_px),
        }
        ok_q, q_detail = _validate_intraday_option_quote_quality(raw=raw, resolve_response=res_r, leg=leg)
        if not ok_q:
            return False, {
                "step": f"intraday_option_quality_gate-{mode_name}",
                "error": "option_quote_quality_failed",
                "detail": q_detail,
                "response": res_r,
                "partial_legs": legs,
            }
        legs.append(leg)
        meta_steps.append(
            {
                "symbol": op,
                "strike": intent.strike,
                "right": intent.right,
                "leg_key": getattr(intent, "leg_key", "") or "",
                "resolve": res_r,
            }
        )
    position_group_id = _position_group_id_for_legs(legs, mode_name=mode_name)
    for leg in legs:
        leg["position_group_id"] = position_group_id
        leg["combo_mode"] = mode_name
    ok_o, res_o = _place_order_with_intraday_protection({"legs": legs, "position_group_id": position_group_id}, raw, dry_run=dry_run)
    if not ok_o:
        return False, {"step": f"options/order-{mode_name}", "response": res_o}
    return True, {"step": f"entry_{mode_name}", "order": res_o, "resolved": meta_steps, "position_group_id": position_group_id}


def _process_resolve_and_enter_strangle(
    call_it: TradeIntent,
    put_it: TradeIntent,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    *,
    dry_run: bool,
    resolve_defaults: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    return _process_resolve_and_enter_multi_leg(
        [call_it, put_it],
        raw,
        cfg,
        dry_run=dry_run,
        resolve_defaults=resolve_defaults,
        mode_name="strangle",
    )
    exp = _expiry_for_resolve(raw, cfg, underlying=call_it.underlying)
    legs: list[dict[str, Any]] = []
    meta_steps: list[dict[str, Any]] = []
    for intent in (call_it, put_it):
        resolve_body = {
            "symbol": intent.underlying,
            "strike": float(intent.strike),
            "right": intent.right,
            "expiry_date": exp,
            "strike_window": float(raw.get("resolve_strike_window", resolve_defaults.get("strike_window", 5.0))),
            "standard_only": bool(raw.get("resolve_standard_only", resolve_defaults.get("standard_only", False))),
            "max_strike_diff": float(raw.get("resolve_max_strike_diff", resolve_defaults.get("max_strike_diff", 1.5))),
            "use_ask_for_buy_limit": True,
        }
        ok_r, res_r = _api_post_json("/strategy/qqq-0dte/resolve-contract", resolve_body, timeout=_API_TIMEOUT)
        if not ok_r or not bool(res_r.get("ok")):
            return False, {"step": "resolve-contract-strangle", "response": res_r, "partial_legs": legs}
        op = str(res_r.get("symbol") or "").strip().upper()
        if not op:
            return False, {"step": "resolve-contract-strangle", "error": "missing symbol", "response": res_r}
        limit_px = float(res_r.get("suggested_limit_price_per_share") or 0.0)
        if limit_px > 0:
            limit_px = _quantize_limit_price_per_share(limit_px, side="buy")
        legs.append(
            {
                "symbol": op,
                "side": "buy",
                "contracts": max(1, int(intent.contracts)),
                "price": max(0.0, limit_px),
            }
        )
        meta_steps.append({"symbol": op, "strike": intent.strike, "right": intent.right, "resolve": res_r})
    legs_payload = {"legs": legs}
    ok_o, res_o = _place_order(legs_payload, raw, dry_run=dry_run)
    if not ok_o:
        return False, {"step": "options/order-strangle", "response": res_o}
    return True, {"step": "entry_strangle", "order": res_o, "resolved": meta_steps}


def _resolve_and_exit_strangle(
    ex: dict[str, Any],
    *,
    underlying: str,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    dry_run: bool,
    resolve_defaults: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    exp = _expiry_for_resolve(raw, cfg, underlying=underlying)
    contracts = max(1, int(ex.get("contracts") or 1))
    close_call = bool(ex.get("close_call", True))
    close_put = bool(ex.get("close_put", True))
    meta_steps: list[dict[str, Any]] = []
    resolved_rows: list[dict[str, Any]] = []
    leg_specs: list[tuple[float, str, str]] = []
    if close_call:
        leg_specs.append((float(ex.get("call_strike") or 0.0), "call", "call_exit_px"))
    if close_put:
        leg_specs.append((float(ex.get("put_strike") or 0.0), "put", "put_exit_px"))
    if not leg_specs:
        return False, {"step": "resolve-contract-strangle-exit", "error": "no legs selected to close", "ex": ex}
    for strike, right, snap_key in leg_specs:
        resolve_body = {
            "symbol": underlying,
            "strike": float(strike),
            "right": right,
            "expiry_date": exp,
            "strike_window": float(raw.get("resolve_strike_window", resolve_defaults.get("strike_window", 5.0))),
            "standard_only": bool(raw.get("resolve_standard_only", resolve_defaults.get("standard_only", False))),
            "max_strike_diff": float(raw.get("resolve_max_strike_diff", resolve_defaults.get("max_strike_diff", 1.5))),
            "use_bid_for_sell_limit": True,
        }
        ok_r, res_r = _api_post_json("/strategy/qqq-0dte/resolve-contract", resolve_body, timeout=_API_TIMEOUT)
        if not ok_r or not bool(res_r.get("ok")):
            plegs: list[dict[str, Any]] = []
            tk = _option_price_tick()
            for L in resolved_rows:
                qr = max(0.0, L["q_api"] if L["q_api"] > 0 else L["q_snap"])
                lp = _quantize_limit_price_per_share(qr, side="sell") if qr > 0 else 0.0
                plegs.append(
                    {
                        "symbol": L["op"],
                        "side": "sell",
                        "contracts": contracts,
                        "price": max(tk, lp) if lp > 0 else 0.0,
                    }
                )
            return False, {
                "step": "resolve-contract-strangle-exit",
                "response": res_r,
                "partial_legs": plegs,
                "partial_resolved": meta_steps,
            }
        op = str(res_r.get("symbol") or "").strip().upper()
        if not op:
            return False, {"step": "resolve-contract-strangle-exit", "error": "missing symbol", "response": res_r}
        q_api = float(res_r.get("suggested_limit_price_per_share") or 0.0)
        q_snap = float(ex.get(snap_key) or 0.0)
        resolved_rows.append({"op": op, "q_api": q_api, "q_snap": q_snap})
        meta_steps.append({"symbol": op, "strike": strike, "right": right, "resolve": res_r})
    tick = _option_price_tick()
    max_retry = _sell_exit_price_max_retries()
    down_steps = 0
    last_o: dict[str, Any] = {}
    while down_steps < max_retry:
        legs: list[dict[str, Any]] = []
        for L in resolved_rows:
            q_raw = max(0.0, L["q_api"] if L["q_api"] > 0 else L["q_snap"])
            if q_raw > 0:
                base = _quantize_limit_price_per_share(q_raw, side="sell")
                q_px = max(tick, base - tick * down_steps)
            else:
                q_px = 0.0
            legs.append({"symbol": L["op"], "side": "sell", "contracts": contracts, "price": q_px})
        legs_payload = {"legs": legs}
        guard_ok, guard_detail = _preflight_sell_legs_against_broker_positions(legs, raw)
        if not guard_ok:
            return False, guard_detail
        ok_o, res_o = _place_order(legs_payload, raw, dry_run=dry_run)
        if ok_o:
            return True, {
                "step": "exit_strangle",
                "order": res_o,
                "resolved": meta_steps,
                "exit_price_retries": down_steps,
            }
        last_o = res_o
        if dry_run:
            break
        if not _response_suggests_bad_bid_size(res_o):
            break
        if not any(max(0.0, r["q_snap"] if r["q_snap"] > 0 else r["q_api"]) > 0 for r in resolved_rows):
            break
        down_steps += 1
    return False, {
        "step": "options/order-strangle-exit",
        "response": last_o,
        "resolved": meta_steps,
        "exit_price_retries": down_steps,
    }


def _resolve_and_exit_double_strangle(
    ex: dict[str, Any],
    *,
    underlying: str,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    dry_run: bool,
    resolve_defaults: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    exp = _expiry_for_resolve(raw, cfg, underlying=underlying)
    contracts = max(1, int(ex.get("contracts") or 1))
    close_keys = ex.get("close_leg_keys")
    legs_map = ex.get("double_strangle_legs")
    leg_exit_px = ex.get("leg_exit_px")
    if not isinstance(close_keys, list):
        close_keys = []
    if not isinstance(legs_map, dict):
        legs_map = {}
    if not isinstance(leg_exit_px, dict):
        leg_exit_px = {}
    meta_steps: list[dict[str, Any]] = []
    resolved_rows: list[dict[str, Any]] = []
    if not close_keys:
        return False, {"step": "resolve-contract-double-strangle-exit", "error": "no legs selected to close", "ex": ex}
    for key in close_keys:
        leg_key = str(key or "")
        leg = legs_map.get(leg_key)
        if not isinstance(leg, dict):
            return False, {"step": "resolve-contract-double-strangle-exit", "error": "missing leg", "leg_key": leg_key}
        strike = float(leg.get("strike") or 0.0)
        right = str(leg.get("right") or "").strip().lower()
        resolve_body = {
            "symbol": underlying,
            "strike": strike,
            "right": right,
            "expiry_date": exp,
            "strike_window": float(raw.get("resolve_strike_window", resolve_defaults.get("strike_window", 5.0))),
            "standard_only": bool(raw.get("resolve_standard_only", resolve_defaults.get("standard_only", False))),
            "max_strike_diff": float(raw.get("resolve_max_strike_diff", resolve_defaults.get("max_strike_diff", 1.5))),
            "use_bid_for_sell_limit": True,
        }
        ok_r, res_r = _api_post_json("/strategy/qqq-0dte/resolve-contract", resolve_body, timeout=_API_TIMEOUT)
        if not ok_r or not bool(res_r.get("ok")):
            plegs: list[dict[str, Any]] = []
            tk = _option_price_tick()
            for row in resolved_rows:
                qr = max(0.0, row["q_api"] if row["q_api"] > 0 else row["q_snap"])
                lp = _quantize_limit_price_per_share(qr, side="sell") if qr > 0 else 0.0
                plegs.append(
                    {"symbol": row["op"], "side": "sell", "contracts": contracts, "price": max(tk, lp) if lp > 0 else 0.0}
                )
            return False, {
                "step": "resolve-contract-double-strangle-exit",
                "response": res_r,
                "partial_legs": plegs,
                "partial_resolved": meta_steps,
            }
        op = str(res_r.get("symbol") or "").strip().upper()
        if not op:
            return False, {"step": "resolve-contract-double-strangle-exit", "error": "missing symbol", "response": res_r}
        q_api = float(res_r.get("suggested_limit_price_per_share") or 0.0)
        q_snap = float(leg_exit_px.get(leg_key) or 0.0)
        resolved_rows.append({"op": op, "q_api": q_api, "q_snap": q_snap, "leg_key": leg_key})
        meta_steps.append({"symbol": op, "strike": strike, "right": right, "leg_key": leg_key, "resolve": res_r})

    tick = _option_price_tick()
    max_retry = _sell_exit_price_max_retries()
    down_steps = 0
    last_o: dict[str, Any] = {}
    while down_steps < max_retry:
        legs: list[dict[str, Any]] = []
        for row in resolved_rows:
            q_raw = max(0.0, row["q_api"] if row["q_api"] > 0 else row["q_snap"])
            if q_raw > 0:
                base = _quantize_limit_price_per_share(q_raw, side="sell")
                q_px = max(tick, base - tick * down_steps)
            else:
                q_px = 0.0
            legs.append({"symbol": row["op"], "side": "sell", "contracts": contracts, "price": q_px})
        guard_ok, guard_detail = _preflight_sell_legs_against_broker_positions(legs, raw)
        if not guard_ok:
            return False, guard_detail
        ok_o, res_o = _place_order({"legs": legs}, raw, dry_run=dry_run)
        if ok_o:
            return True, {
                "step": "exit_double_strangle",
                "order": res_o,
                "resolved": meta_steps,
                "exit_price_retries": down_steps,
            }
        last_o = res_o
        if dry_run:
            break
        if not _response_suggests_bad_bid_size(res_o):
            break
        if not any(max(0.0, row["q_snap"] if row["q_snap"] > 0 else row["q_api"]) > 0 for row in resolved_rows):
            break
        down_steps += 1
    return False, {
        "step": "options/order-double-strangle-exit",
        "response": last_o,
        "resolved": meta_steps,
        "exit_price_retries": down_steps,
    }


def _resolve_and_exit(
    *,
    strike: float,
    right: str,
    contracts: int,
    limit_px: float,
    underlying: str,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    dry_run: bool,
    resolve_defaults: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    exp = _expiry_for_resolve(raw, cfg, underlying=underlying)
    resolve_body = {
        "symbol": underlying,
        "strike": float(strike),
        "right": right,
        "expiry_date": exp,
        "strike_window": float(raw.get("resolve_strike_window", resolve_defaults.get("strike_window", 5.0))),
        "standard_only": bool(raw.get("resolve_standard_only", resolve_defaults.get("standard_only", False))),
        "max_strike_diff": float(raw.get("resolve_max_strike_diff", resolve_defaults.get("max_strike_diff", 1.5))),
        "use_bid_for_sell_limit": True,
    }
    ok_r, res_r = _api_post_json("/strategy/qqq-0dte/resolve-contract", resolve_body, timeout=_API_TIMEOUT)
    if not ok_r or not bool(res_r.get("ok")):
        return False, {"step": "resolve-contract-exit", "response": res_r}
    op = str(res_r.get("symbol") or "").strip().upper()
    if not op:
        return False, {"step": "resolve-contract-exit", "error": "missing symbol", "response": res_r}
    q_px = float(res_r.get("suggested_limit_price_per_share") or limit_px or 0.0)
    tick = _option_price_tick()
    max_retry = _sell_exit_price_max_retries()
    down_steps = 0
    last_o: dict[str, Any] = {}
    while down_steps < max_retry:
        if q_px > 0:
            base = _quantize_limit_price_per_share(q_px, side="sell")
            leg_px = max(tick, base - tick * down_steps)
        else:
            leg_px = max(0.0, q_px)
        legs = {
            "legs": [{"symbol": op, "side": "sell", "contracts": max(1, int(contracts)), "price": leg_px}],
        }
        guard_ok, guard_detail = _preflight_sell_legs_against_broker_positions(legs["legs"], raw)
        if not guard_ok:
            return False, guard_detail
        ok_o, res_o = _place_order(legs, raw, dry_run=dry_run)
        if ok_o:
            return True, {"step": "exit", "symbol": op, "order": res_o, "exit_price_retries": down_steps}
        last_o = res_o
        if dry_run:
            break
        if not _response_suggests_bad_bid_size(res_o):
            break
        if q_px <= 0:
            break
        down_steps += 1
    return False, {"step": "options/order-exit", "response": last_o, "exit_price_retries": down_steps}


def _place_symbol_sell_order(
    *,
    symbol: str,
    contracts: int,
    raw: dict[str, Any],
    dry_run: bool,
) -> tuple[bool, dict[str, Any]]:
    sym = str(symbol or "").strip().upper()
    qty = max(1, int(contracts or 1))
    bid, last = _fetch_realtime_bid_last(sym)
    px = float((bid if bid is not None else last) or 0.0)
    tick = _option_price_tick()
    max_retry = _sell_exit_price_max_retries()
    down_steps = 0
    last_o: dict[str, Any] = {}
    while down_steps < max_retry:
        if px > 0:
            base = _quantize_limit_price_per_share(px, side="sell")
            leg_px = max(tick, base - tick * down_steps)
        else:
            leg_px = 0.0
        legs = [{"symbol": sym, "side": "sell", "contracts": qty, "price": leg_px}]
        guard_ok, guard_detail = _preflight_sell_legs_against_broker_positions(legs, raw)
        if not guard_ok:
            return False, {
                "step": "force_close_unrecovered_symbol_blocked",
                "symbol": sym,
                "guard": guard_detail,
                "bid": bid,
                "last": last,
            }
        ok, resp = _place_order(
            {"legs": legs},
            raw,
            dry_run=dry_run,
        )
        if ok:
            return True, {
                "step": "force_close_unrecovered_symbol",
                "symbol": sym,
                "order": resp,
                "bid": bid,
                "last": last,
                "exit_price_retries": down_steps,
            }
        last_o = resp
        if dry_run or not _response_suggests_bad_bid_size(resp) or px <= 0:
            break
        down_steps += 1
    return False, {
        "step": "force_close_unrecovered_symbol_failed",
        "symbol": sym,
        "response": last_o,
        "bid": bid,
        "last": last,
        "exit_price_retries": down_steps,
    }


def _force_close_unrecovered_worker_positions(
    *,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    symbol: str,
    dry_run: bool,
    open_live: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    fc_s = str(getattr(cfg, "strangle_force_close_hhmm_et", "12:00") or "12:00")
    if not is_at_or_after_et_hhmm(to_ny(datetime.now(timezone.utc), cfg.assume_bars_timezone), fc_s):
        return None
    cancel_scan = _intraday_cancel_active_option_orders(
        raw,
        reason="force_close_time_reached",
        dry_run=dry_run,
    )
    live, err = _detect_unrecovered_worker_positions(raw=raw, cfg=cfg, symbol=symbol)
    if err is not None:
        return {"action": "force_close_unrecovered_scan_failed", "ok": False, "detail": err, "active_order_cancel_scan": cancel_scan}
    targets = list(live or [])
    if not targets:
        if cancel_scan and int(cancel_scan.get("active_orders") or 0) > 0:
            return {
                "action": "force_close_active_orders_cancelled",
                "ok": all(bool(x.get("ok")) for x in (cancel_scan.get("cancels") or [])) if not dry_run else True,
                "force_close_et": fc_s,
                "active_order_cancel_scan": cancel_scan,
            }
        return None
    now_ny = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    expired_targets: list[dict[str, Any]] = []
    tradable_targets: list[dict[str, Any]] = []
    for row in targets:
        exp_raw = str(row.get("expiry_date") or "").strip()
        expired = False
        try:
            exp_d = date.fromisoformat(exp_raw)
            expired = now_ny >= option_expiry_datetime(exp_d, cfg)
        except Exception:
            expired = False
        if expired:
            expired_targets.append(row)
        else:
            tradable_targets.append(row)
    if expired_targets and not tradable_targets:
        return {
            "action": "force_close_unrecovered_positions_expired",
            "ok": False,
            "force_close_et": fc_s,
            "targets": expired_targets,
            "reason": "option_expired_not_tradable",
            "active_order_cancel_scan": cancel_scan,
        }
    targets = tradable_targets
    results: list[dict[str, Any]] = []
    all_ok = True
    for row in targets:
        sym = str(row.get("symbol") or "").strip().upper()
        qty = max(1, int(row.get("quantity") or 1))
        ok, detail = _place_symbol_sell_order(symbol=sym, contracts=qty, raw=raw, dry_run=dry_run)
        all_ok = all_ok and bool(ok)
        if ok:
            _append_execution_ledger_from_detail(detail if isinstance(detail, dict) else {})
        results.append({"ok": ok, "position": row, "detail": detail})
    return {
        "action": "force_close_unrecovered_positions",
        "ok": bool(all_ok),
        "force_close_et": fc_s,
        "targets": targets,
        "results": results,
        "active_order_cancel_scan": cancel_scan,
    }


def _should_stop() -> bool:
    if _stop.is_set():
        return True
    try:
        return os.path.exists(STOP_FILE)
    except Exception:
        return False


def _extract_open_live_from_entry(detail: dict[str, Any], intents: list[TradeIntent]) -> dict[str, Any] | None:
    if not isinstance(detail, dict):
        return None
    if (
        len(intents) >= 4
        and all(getattr(x, "reason", "") == "morning_double_strangle" for x in intents[:4])
    ):
        resolved = detail.get("resolved")
        if isinstance(resolved, list):
            leg_symbols: dict[str, str] = {}
            for x in resolved:
                if not isinstance(x, dict):
                    continue
                key = str(x.get("leg_key") or "").strip()
                op = str(x.get("symbol") or "").strip().upper()
                if key in DOUBLE_STRANGLE_LEG_KEYS and op:
                    leg_symbols[key] = op
            if all(leg_symbols.get(key) for key in DOUBLE_STRANGLE_LEG_KEYS):
                return {"mode": "double_strangle", "leg_symbols": leg_symbols}
        return None
    if (
        len(intents) >= 2
        and getattr(intents[0], "reason", "") == "morning_strangle"
        and getattr(intents[1], "reason", "") == "morning_strangle"
    ):
        resolved = detail.get("resolved")
        if isinstance(resolved, list):
            call_symbol = None
            put_symbol = None
            for x in resolved:
                if not isinstance(x, dict):
                    continue
                right = str(x.get("right") or "").lower()
                op = str(x.get("symbol") or "").strip().upper()
                if right == "call" and op:
                    call_symbol = op
                elif right == "put" and op:
                    put_symbol = op
            if call_symbol and put_symbol:
                return {"mode": "strangle", "call_symbol": call_symbol, "put_symbol": put_symbol}
        return None
    op = str(detail.get("symbol") or "").strip().upper()
    if not op:
        return None
    return {"mode": "single", "symbol": op}


def _open_live_after_strangle_partial(
    session: Qqq0dteLiveSession,
    prev_open: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """宽跨单腿平掉后：仍用原 OPRA 订阅剩余腿，已平腿 symbol 置空。"""
    pos = session.open_position()
    if pos is None:
        return None
    if str(getattr(pos, "side", "") or "") != "strangle":
        return None
    pc = str((prev_open or {}).get("call_symbol") or "").strip().upper()
    pp = str((prev_open or {}).get("put_symbol") or "").strip().upper()
    cs = pc if getattr(pos, "strangle_call_active", True) else ""
    ps = pp if getattr(pos, "strangle_put_active", True) else ""
    if not cs and not ps:
        return None
    return {"mode": "strangle", "call_symbol": cs, "put_symbol": ps}


def _open_live_after_double_strangle_partial(
    session: Qqq0dteLiveSession,
    prev_open: dict[str, Any] | None,
) -> dict[str, Any] | None:
    pos = session.open_position()
    if pos is None or str(getattr(pos, "side", "") or "") != "double_strangle":
        return None
    prev_symbols = (prev_open or {}).get("leg_symbols")
    if not isinstance(prev_symbols, dict):
        return None
    legs = getattr(pos, "double_strangle_legs", None)
    if not isinstance(legs, dict):
        return None
    out_symbols: dict[str, str] = {}
    for key in DOUBLE_STRANGLE_LEG_KEYS:
        leg = legs.get(key)
        if isinstance(leg, dict) and bool(leg.get("active", True)):
            sym = str(prev_symbols.get(key) or "").strip().upper()
            if sym:
                out_symbols[key] = sym
    if not out_symbols:
        return None
    return {"mode": "double_strangle", "leg_symbols": out_symbols}


def _entry_fill_prices_from_detail(detail: dict[str, Any]) -> dict[str, float]:
    """
    从下单回包中提取本次开仓真实成交价（每股）。
    返回：
      - strangle: {"call": x, "put": y}
      - single: {"single": x}
    """
    out: dict[str, float] = {}
    if not isinstance(detail, dict):
        return out
    order = detail.get("order")
    if not isinstance(order, dict):
        return out
    # 单腿：submit_option_order_with_risk 返回 { mode, order: {order_id, symbol, side, price, ...}, risk }
    # 与 multi_leg 的 { mode, result: { legs_submitted, ... } } 不同；此前未解析会导致 entry_px 仍为合成价，
    # 实时止盈 evaluate_exit 误判为「刚买就达到 take_profit」。
    mode = str(order.get("mode") or "").strip().lower()
    if mode == "single_leg":
        inner = order.get("order")
        if isinstance(inner, dict):
            side = str(inner.get("side") or "").strip().lower()
            # 开仓单腿恒为 buy；兼容大小写或网关偶发省略 side 字段。
            px_raw = inner.get("price")
            px_f: float | None = None
            if isinstance(px_raw, (int, float)) and float(px_raw) > 0:
                px_f = float(px_raw)
            elif isinstance(px_raw, str) and px_raw.strip():
                try:
                    v = float(px_raw.strip())
                    if v > 0:
                        px_f = v
                except ValueError:
                    px_f = None
            if px_f is not None and side in ("buy", ""):
                out["single"] = px_f
        return out

    result = order.get("result")
    if not isinstance(result, dict):
        return out
    legs = result.get("legs_submitted")
    if not isinstance(legs, list) or not legs:
        return out

    sym_right: dict[str, str] = {}
    sym_key: dict[str, str] = {}
    resolved = detail.get("resolved")
    if isinstance(resolved, list):
        for row in resolved:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").strip().upper()
            right = str(row.get("right") or "").strip().lower()
            if sym and right in {"call", "put"}:
                sym_right[sym] = right
            leg_key = str(row.get("leg_key") or "").strip()
            if sym and leg_key:
                sym_key[sym] = leg_key

    for leg in legs:
        if not isinstance(leg, dict):
            continue
        side = str(leg.get("side") or "").strip().lower()
        if side != "buy":
            continue
        px = leg.get("price")
        if not isinstance(px, (int, float)):
            continue
        sym = str(leg.get("symbol") or "").strip().upper()
        leg_key = sym_key.get(sym)
        if leg_key:
            out[leg_key] = float(px)
            continue
        right = sym_right.get(sym)
        if right == "call":
            out["call"] = float(px)
        elif right == "put":
            out["put"] = float(px)
        elif "single" not in out:
            out["single"] = float(px)
    return out


def _strangle_exit_fill_prices_from_detail(detail: dict[str, Any]) -> dict[str, float]:
    """Extract per-share sell prices from a strangle exit order response."""
    out: dict[str, float] = {}
    if not isinstance(detail, dict):
        return out
    order = detail.get("order")
    if not isinstance(order, dict):
        return out

    sym_right: dict[str, str] = {}
    sym_key: dict[str, str] = {}
    resolved = detail.get("resolved")
    if isinstance(resolved, list):
        for row in resolved:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").strip().upper()
            right = str(row.get("right") or "").strip().lower()
            if sym and right in {"call", "put"}:
                sym_right[sym] = right
            leg_key = str(row.get("leg_key") or "").strip()
            if sym and leg_key:
                sym_key[sym] = leg_key

    mode = str(order.get("mode") or "").strip().lower()
    if mode == "single_leg":
        inner = order.get("order")
        if isinstance(inner, dict):
            side = str(inner.get("side") or "").strip().lower()
            px = inner.get("price")
            sym = str(inner.get("symbol") or "").strip().upper()
            leg_key = sym_key.get(sym)
            right = sym_right.get(sym)
            if side == "sell" and leg_key and isinstance(px, (int, float)) and float(px) > 0:
                out[leg_key] = float(px)
                return out
            if side == "sell" and right in {"call", "put"} and isinstance(px, (int, float)) and float(px) > 0:
                out[right] = float(px)
        return out

    result = order.get("result")
    if not isinstance(result, dict):
        return out
    legs = result.get("legs_submitted")
    if not isinstance(legs, list):
        return out
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        side = str(leg.get("side") or "").strip().lower()
        if side != "sell":
            continue
        px = leg.get("price")
        if not isinstance(px, (int, float)) or float(px) <= 0:
            continue
        sym = str(leg.get("symbol") or "").strip().upper()
        leg_key = sym_key.get(sym)
        if leg_key:
            out[leg_key] = float(px)
            continue
        right = sym_right.get(sym)
        if right in {"call", "put"}:
            out[right] = float(px)
    return out


def _is_force_close_reason(reason: Any) -> bool:
    return "force_close_et" in str(reason or "").lower()


def _patch_session_entry_px_from_order(
    session: Qqq0dteLiveSession,
    *,
    detail: dict[str, Any],
    intents: list[TradeIntent],
) -> None:
    """
    开仓成功后，用真实成交价回填 session 内持仓成本，避免用合成价导致实时止盈误触发。
    """
    pos = session.open_position()
    if pos is None:
        return
    fills = _entry_fill_prices_from_detail(detail)
    if (
        len(intents) >= 4
        and all(getattr(x, "reason", "") == "morning_double_strangle" for x in intents[:4])
        and str(getattr(pos, "side", "") or "") == "double_strangle"
    ):
        legs = getattr(pos, "double_strangle_legs", None)
        if not isinstance(legs, dict):
            return
        changed = False
        for key in DOUBLE_STRANGLE_LEG_KEYS:
            px = fills.get(key)
            leg = legs.get(key)
            if isinstance(px, float) and px > 0 and isinstance(leg, dict):
                leg["entry_px"] = float(px)
                changed = True
        if changed:
            try:
                pos.entry_px = float(sum(float(v.get("entry_px") or 0.0) for v in legs.values() if isinstance(v, dict)))
                pos.call_entry_px = float(
                    sum(float(v.get("entry_px") or 0.0) for v in legs.values() if isinstance(v, dict) and str(v.get("right") or "") == "call")
                )
                pos.put_entry_px = float(
                    sum(float(v.get("entry_px") or 0.0) for v in legs.values() if isinstance(v, dict) and str(v.get("right") or "") == "put")
                )
                pos.strangle_original_entry_px = float(pos.entry_px)
                pos.strangle_realized_exit_px = 0.0
            except Exception:
                pass
        return
    if (
        len(intents) >= 2
        and getattr(intents[0], "reason", "") == "morning_strangle"
        and getattr(intents[1], "reason", "") == "morning_strangle"
    ):
        c = fills.get("call")
        p = fills.get("put")
        if isinstance(c, float) and c > 0 and isinstance(p, float) and p > 0:
            try:
                pos.call_entry_px = float(c)
                pos.put_entry_px = float(p)
                pos.entry_px = float(c + p)
                pos.strangle_original_entry_px = float(c + p)
                pos.strangle_realized_exit_px = 0.0
            except Exception:
                pass
        return

    s = fills.get("single")
    if isinstance(s, float) and s > 0:
        try:
            pos.entry_px = float(s)
        except Exception:
            pass


def _append_realtime_exit_decision_tail(
    *,
    symbol: str,
    session_date: str,
    bar_dt: datetime,
    cfg: Qqq0dteConfig,
    rt_action: dict[str, Any],
) -> None:
    reason = str(rt_action.get("reason") or "")
    _append_decision_tail(
        symbol=symbol,
        session_date=session_date,
        bar_dt=bar_dt,
        cfg=cfg,
        logs=[
            {
                "message": "exit_realtime",
                "as_of": "",
                "extra": {"reason": reason},
            }
        ],
        action=rt_action,
    )


def _realtime_exit_check(
    *,
    session: Qqq0dteLiveSession,
    open_live: dict[str, Any] | None,
    symbol: str,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    dry_run: bool,
    resolve_defaults: dict[str, Any],
) -> tuple[bool, dict[str, Any], dict[str, Any] | None]:
    """
    持仓期间按实时期权价轮询，触发平仓。
    返回 (triggered, action_detail, new_open_live)。
    """
    pos = session.open_position()
    if pos is None or not isinstance(open_live, dict):
        return False, {}, open_live
    now = datetime.now(timezone.utc)
    side = str(getattr(pos, "side", "") or "")

    if side == "strangle":
        cs = str(open_live.get("call_symbol") or "").strip().upper()
        ps = str(open_live.get("put_symbol") or "").strip().upper()
        call_on = bool(getattr(pos, "strangle_call_active", True))
        put_on = bool(getattr(pos, "strangle_put_active", True))
        if call_on and not cs:
            return False, {"reason": "missing_open_live_symbols", "leg": "call"}, open_live
        if put_on and not ps:
            return False, {"reason": "missing_open_live_symbols", "leg": "put"}, open_live
        call_b, call_l = (None, None)
        put_b, put_l = (None, None)
        if call_on and cs:
            call_b, call_l = _fetch_realtime_bid_last(cs)
        if put_on and ps:
            put_b, put_l = _fetch_realtime_bid_last(ps)
        cb_tp, cb_sl = _strangle_leg_tp_sl(call_b, call_l)
        pb_tp, pb_sl = _strangle_leg_tp_sl(put_b, put_l)
        if call_on and cb_tp is None:
            return False, {"reason": "quote_unavailable", "call_symbol": cs, "put_symbol": ps}, open_live
        if put_on and pb_tp is None:
            return False, {"reason": "quote_unavailable", "call_symbol": cs, "put_symbol": ps}, open_live
        cb_tp_f = float(cb_tp or 0.0)
        pb_tp_f = float(pb_tp or 0.0)
        cb_sl_f = float(cb_sl or 0.0)
        pb_sl_f = float(pb_sl or 0.0)
        ex_reason, ex_detail, leg_close = evaluate_strangle_exit(
            pos, cb_tp_f, pb_tp_f, cb_sl_f, pb_sl_f, now, cfg, cfg.assume_bars_timezone
        )
        if ex_reason == "hold":
            return False, {"reason": "hold", "detail": ex_detail}, open_live
        if leg_close == "none":
            close_call = call_on
            close_put = put_on
        elif leg_close == "call":
            close_call, close_put = True, False
        else:
            close_call, close_put = False, True
        call_lim = float((call_b if call_b is not None else call_l) or 0.0)
        put_lim = float((put_b if put_b is not None else put_l) or 0.0)
        ex = {
            "side": "strangle",
            "call_strike": float(getattr(pos, "call_strike", 0.0) or 0.0),
            "put_strike": float(getattr(pos, "put_strike", 0.0) or 0.0),
            "contracts": int(getattr(pos, "contracts", 1) or 1),
            "call_exit_px": call_lim if close_call else 0.0,
            "put_exit_px": put_lim if close_put else 0.0,
            "close_call": close_call,
            "close_put": close_put,
            "strangle_partial_leg": leg_close,
        }
        ok, detail = _resolve_and_exit_strangle(
            ex,
            underlying=symbol,
            raw=raw,
            cfg=cfg,
            dry_run=dry_run,
            resolve_defaults=resolve_defaults,
        )
        if ok:
            _append_execution_ledger_from_detail(detail if isinstance(detail, dict) else {})
            prev_ol = open_live
            if leg_close == "none":
                session.clear_open_position()
                return True, {"action": "exit_realtime", "ok": True, "reason": f"{ex_reason}:{ex_detail}", "detail": detail}, None
            exit_fills = _strangle_exit_fill_prices_from_detail(detail)
            if leg_close == "call":
                session.apply_strangle_leg_closed("call", float(exit_fills.get("call") or call_lim))
            else:
                session.apply_strangle_leg_closed("put", float(exit_fills.get("put") or put_lim))
            new_ol = _open_live_after_strangle_partial(session, prev_ol)
            return True, {"action": "exit_realtime", "ok": True, "reason": f"{ex_reason}:{ex_detail}", "detail": detail}, new_ol
        if isinstance(detail, dict) and str(detail.get("error") or "") == "manual_close_detected_or_position_insufficient":
            new_ol = _sync_session_after_manual_close(session, open_live, raw)
            return True, {
                "action": "manual_close_detected",
                "ok": False,
                "reason": f"{ex_reason}:{ex_detail}",
                "detail": detail,
            }, new_ol
        return True, {"action": "exit_realtime", "ok": False, "reason": f"{ex_reason}:{ex_detail}", "detail": detail}, open_live

    if side == "double_strangle":
        leg_symbols = open_live.get("leg_symbols")
        legs_map = getattr(pos, "double_strangle_legs", None)
        if not isinstance(leg_symbols, dict) or not isinstance(legs_map, dict):
            return False, {"reason": "missing_open_live_symbols", "side": side}, open_live
        leg_tp: dict[str, float] = {}
        leg_sl: dict[str, float] = {}
        quotes: dict[str, dict[str, Any]] = {}
        for key in DOUBLE_STRANGLE_LEG_KEYS:
            leg = legs_map.get(key)
            if not isinstance(leg, dict) or not bool(leg.get("active", True)):
                continue
            sym = str(leg_symbols.get(key) or "").strip().upper()
            if not sym:
                return False, {"reason": "missing_open_live_symbols", "leg": key}, open_live
            bid, last = _fetch_realtime_bid_last(sym)
            mark_tp, mark_sl = _strangle_leg_tp_sl(bid, last)
            if mark_tp is None:
                return False, {"reason": "quote_unavailable", "leg": key, "symbol": sym}, open_live
            leg_tp[key] = float(mark_tp or 0.0)
            leg_sl[key] = float(mark_sl or 0.0)
            quotes[key] = {"symbol": sym, "bid": bid, "last": last}
        ex_reason, ex_detail, leg_close = evaluate_double_strangle_exit(
            pos, leg_tp, leg_sl, now, cfg, cfg.assume_bars_timezone
        )
        if ex_reason == "hold":
            return False, {"reason": "hold", "detail": ex_detail, "quotes": quotes}, open_live
        active_keys = [
            key
            for key in DOUBLE_STRANGLE_LEG_KEYS
            if isinstance(legs_map.get(key), dict) and bool(legs_map[key].get("active", True))
        ]
        close_keys = active_keys if leg_close == "none" else [leg_close]
        leg_exit_px = {key: float(leg_tp.get(key) or 0.0) for key in close_keys}
        ex = {
            "side": "double_strangle",
            "contracts": int(getattr(pos, "contracts", 1) or 1),
            "double_strangle_legs": legs_map,
            "close_leg_keys": close_keys,
            "leg_exit_px": leg_exit_px,
            "strangle_partial_leg": leg_close,
        }
        ok, detail = _resolve_and_exit_double_strangle(
            ex,
            underlying=symbol,
            raw=raw,
            cfg=cfg,
            dry_run=dry_run,
            resolve_defaults=resolve_defaults,
        )
        if ok:
            _append_execution_ledger_from_detail(detail if isinstance(detail, dict) else {})
            prev_ol = open_live
            if leg_close == "none":
                session.clear_open_position()
                return True, {"action": "exit_realtime", "ok": True, "reason": f"{ex_reason}:{ex_detail}", "detail": detail}, None
            exit_fills = _strangle_exit_fill_prices_from_detail(detail)
            session.apply_double_strangle_leg_closed(str(leg_close), float(exit_fills.get(str(leg_close)) or leg_exit_px.get(str(leg_close)) or 0.0))
            new_ol = _open_live_after_double_strangle_partial(session, prev_ol)
            return True, {"action": "exit_realtime", "ok": True, "reason": f"{ex_reason}:{ex_detail}", "detail": detail}, new_ol
        if isinstance(detail, dict) and str(detail.get("error") or "") == "manual_close_detected_or_position_insufficient":
            new_ol = _sync_session_after_manual_close(session, open_live, raw)
            return True, {
                "action": "manual_close_detected",
                "ok": False,
                "reason": f"{ex_reason}:{ex_detail}",
                "detail": detail,
            }, new_ol
        return True, {"action": "exit_realtime", "ok": False, "reason": f"{ex_reason}:{ex_detail}", "detail": detail}, open_live

    sym = str(open_live.get("symbol") or "").strip().upper()
    if not sym:
        return False, {"reason": "missing_open_live_symbol"}, open_live
    q_bid, q_last = _fetch_realtime_bid_last(sym)
    mark_tp, mark_sl = _strangle_leg_tp_sl(q_bid, q_last)
    if mark_tp is None:
        return False, {"reason": "quote_unavailable", "symbol": sym}, open_live

    if side in ("long_call", "long_put"):
        variant = str(getattr(cfg, "strategy_variant", "reaction_zone") or "")
        mark_sl_arg = (None if mark_sl is None else float(mark_sl))
        if variant == "reaction_zone":
            ex_reason, ex_detail = evaluate_exit(
                pos, float(mark_tp), now, cfg, cfg.assume_bars_timezone, mark_sl=mark_sl_arg
            )
        elif variant == "gamma_scalping":
            ex_reason, ex_detail = evaluate_gamma_exit(
                pos, float(mark_tp), now, cfg, cfg.assume_bars_timezone, mark_sl=mark_sl_arg
            )
        elif str(variant).strip().lower() == "gamma_pro":
            ex_reason, ex_detail = evaluate_gamma_pro_exit(
                pos, float(mark_tp), now, cfg, cfg.assume_bars_timezone, mark_sl=mark_sl_arg
            )
        else:
            ex_reason, ex_detail = evaluate_morning_directional_exit(
                pos, float(mark_tp), now, cfg, cfg.assume_bars_timezone, mark_sl=mark_sl_arg
            )
    else:
        return False, {"reason": "unsupported_side", "side": side}, open_live

    if ex_reason == "hold":
        return False, {"reason": "hold", "detail": ex_detail}, open_live
    right = "call" if side == "long_call" else "put"
    sell_lim = float((q_bid if q_bid is not None else q_last) or mark_tp or 0.0)
    ok, detail = _resolve_and_exit(
        strike=float(getattr(pos, "strike", 0.0) or 0.0),
        right=right,
        contracts=int(getattr(pos, "contracts", 1) or 1),
        limit_px=sell_lim,
        underlying=symbol,
        raw=raw,
        cfg=cfg,
        dry_run=dry_run,
        resolve_defaults=resolve_defaults,
    )
    if ok:
        _append_execution_ledger_from_detail(detail if isinstance(detail, dict) else {})
        session.clear_open_position()
        return True, {"action": "exit_realtime", "ok": True, "reason": f"{ex_reason}:{ex_detail}", "detail": detail}, None
    if isinstance(detail, dict) and str(detail.get("error") or "") == "manual_close_detected_or_position_insufficient":
        new_ol = _sync_session_after_manual_close(session, open_live, raw)
        return True, {
            "action": "manual_close_detected",
            "ok": False,
            "reason": f"{ex_reason}:{ex_detail}",
            "detail": detail,
        }, new_ol
    return True, {"action": "exit_realtime", "ok": False, "reason": f"{ex_reason}:{ex_detail}", "detail": detail}, open_live


def _normalize_stock_pool(raw: dict[str, Any], cfg: Qqq0dteConfig | None = None) -> list[str]:
    values: list[str] = []
    for key in ("stock_pool", "symbols", "underlyings"):
        src = raw.get(key)
        if isinstance(src, str):
            values.extend(re.split(r"[\s,;，；]+", src))
        elif isinstance(src, list):
            values.extend(str(x) for x in src)
    primary = str(raw.get("symbol") or getattr(cfg, "symbol", "") or "QQQ.US").strip()
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


def _stock_options_pool_enabled(raw: dict[str, Any], pool: list[str]) -> bool:
    if _WORKER_INSTANCE != "1dte":
        return False
    for key in ("stock_options_mode", "enable_stock_pool", "stock_pool_enabled"):
        if key in raw:
            return bool(raw.get(key))
    return True


def _cfg_for_underlying(raw: dict[str, Any], symbol: str) -> Qqq0dteConfig:
    scoped = copy.deepcopy(raw)
    scoped["symbol"] = str(symbol or "").strip().upper()
    strat = scoped.get("strategy_config") if isinstance(scoped.get("strategy_config"), dict) else {}
    strat = copy.deepcopy(strat)
    strat["symbol"] = scoped["symbol"]
    scoped["strategy_config"] = strat
    cfg, _ = _resolve_cfg(scoped)
    cfg.symbol = scoped["symbol"]
    return cfg


def _new_stock_symbol_state(raw: dict[str, Any], symbol: str) -> dict[str, Any]:
    cfg = _cfg_for_underlying(raw, symbol)
    return {
        "symbol": str(symbol or "").strip().upper(),
        "cfg": cfg,
        "session": Qqq0dteLiveSession(cfg),
        "last_session_key": None,
        "open_live": None,
        "restored_boot": None,
        "rt_prev_last": {},
        "state_file": _open_state_path_for_symbol(symbol, multi_symbol=True),
    }


def _state_has_open_position(state: dict[str, Any]) -> bool:
    session = state.get("session")
    if isinstance(session, Qqq0dteLiveSession) and session.open_position() is not None:
        return True
    return isinstance(state.get("open_live"), dict)


def _refresh_session_live_quotes(session: Qqq0dteLiveSession, open_live: dict[str, Any] | None) -> None:
    pos_loop = session.open_position()
    if (
        open_live
        and isinstance(open_live, dict)
        and str(open_live.get("mode") or "") == "double_strangle"
        and pos_loop is not None
        and str(getattr(pos_loop, "side", "") or "") == "double_strangle"
    ):
        leg_symbols = open_live.get("leg_symbols")
        legs_map = getattr(pos_loop, "double_strangle_legs", None)
        bids: dict[str, float | None] = {}
        lasts: dict[str, float | None] = {}
        if isinstance(leg_symbols, dict) and isinstance(legs_map, dict):
            for key in DOUBLE_STRANGLE_LEG_KEYS:
                leg = legs_map.get(key)
                if not isinstance(leg, dict) or not bool(leg.get("active", True)):
                    continue
                sym = str(leg_symbols.get(key) or "").strip().upper()
                if sym:
                    bid, last = _fetch_realtime_bid_last(sym)
                    bids[key] = bid
                    lasts[key] = last
        session.set_double_strangle_live_quotes(bids, lasts)
        session.set_strangle_live_quotes(None, None, None, None)
        session.set_option_live_quotes(None, None)
    elif (
        open_live
        and isinstance(open_live, dict)
        and str(open_live.get("mode") or "") == "strangle"
        and pos_loop is not None
        and str(getattr(pos_loop, "side", "") or "") == "strangle"
    ):
        cs = str(open_live.get("call_symbol") or "").strip().upper()
        ps = str(open_live.get("put_symbol") or "").strip().upper()
        ca = bool(getattr(pos_loop, "strangle_call_active", True))
        pa = bool(getattr(pos_loop, "strangle_put_active", True))
        cb, cl = _fetch_realtime_bid_last(cs) if (cs and ca) else (None, None)
        pb, pl = _fetch_realtime_bid_last(ps) if (ps and pa) else (None, None)
        session.set_strangle_live_quotes(cb, pb, cl, pl)
        session.set_double_strangle_live_quotes(None, None)
        session.set_option_live_quotes(None, None)
    elif (
        open_live
        and isinstance(open_live, dict)
        and str(open_live.get("mode") or "") not in {"strangle", "double_strangle"}
        and pos_loop is not None
        and str(getattr(pos_loop, "side", "") or "") in ("long_call", "long_put")
    ):
        sym = str(open_live.get("symbol") or "").strip().upper()
        if sym:
            ob, ol = _fetch_realtime_bid_last(sym)
            session.set_option_live_quotes(ob, ol)
        else:
            session.set_option_live_quotes(None, None)
        session.set_strangle_live_quotes(None, None, None, None)
        session.set_double_strangle_live_quotes(None, None)
    else:
        session.set_strangle_live_quotes(None, None, None, None)
        session.set_double_strangle_live_quotes(None, None)
        session.set_option_live_quotes(None, None)


def _process_stock_symbol_once(
    *,
    state: dict[str, Any],
    raw: dict[str, Any],
    days: int,
    kline: str,
    dry_run: bool,
    trade_freshness_sec: float,
    skip_historical_on_startup: bool,
    restore_open_positions_on_startup: bool,
    resolve_defaults: dict[str, Any],
    loop_started: str,
    boot_dt: datetime,
) -> dict[str, Any]:
    symbol = str(state.get("symbol") or "").strip().upper()
    cfg = state.get("cfg")
    session = state.get("session")
    if not isinstance(cfg, Qqq0dteConfig) or not isinstance(session, Qqq0dteLiveSession):
        raise RuntimeError(f"invalid_symbol_state:{symbol}")
    open_live = state.get("open_live") if isinstance(state.get("open_live"), dict) else None
    restored_boot = state.get("restored_boot") if isinstance(state.get("restored_boot"), dict) else None
    rt_prev_last = state.get("rt_prev_last") if isinstance(state.get("rt_prev_last"), dict) else {}
    state_file = str(state.get("state_file") or _open_state_path_for_symbol(symbol, multi_symbol=True))

    quote, quote_source = _fetch_realtime_quote(symbol)
    rt_quote_fields = _runtime_realtime_quote_fields(symbol, quote, quote_source)
    vix_sym = str(getattr(cfg, "gamma_vix_symbol", "VIX.US") or "VIX.US").strip().upper()
    l1_sym = str(getattr(cfg, "gamma_leader_symbol_1", "NVDA.US") or "NVDA.US").strip().upper()
    l2_sym = str(getattr(cfg, "gamma_leader_symbol_2", "TSLA.US") or "TSLA.US").strip().upper()
    vix_quote, _ = _fetch_realtime_quote(vix_sym)
    l1_quote, _ = _fetch_realtime_quote(l1_sym)
    l2_quote, _ = _fetch_realtime_quote(l2_sym)
    rt_quotes: dict[str, dict[str, Any] | None] = {
        symbol: quote,
        vix_sym: vix_quote,
        l1_sym: l1_quote,
        l2_sym: l2_quote,
    }

    def _chg_pct(sym: str) -> float:
        q = rt_quotes.get(sym)
        if not isinstance(q, dict):
            return 0.0
        last = q.get("last")
        prev = q.get("prev_close")
        try:
            if prev is not None and float(prev) > 0:
                prev_f = max(float(prev), 1e-12)
                return (float(last) - float(prev)) / prev_f * 100.0
        except Exception:
            pass
        try:
            prev_last = float(rt_prev_last.get(sym) or 0.0)
            if prev_last > 0:
                return (float(last) - prev_last) / max(prev_last, 1e-12) * 100.0
        except Exception:
            pass
        return 0.0

    cfg.gamma_rt_qqq_change_pct = _chg_pct(symbol)
    cfg.gamma_rt_vix_change_pct = _chg_pct(vix_sym)
    cfg.gamma_rt_leader1_change_pct = _chg_pct(l1_sym)
    cfg.gamma_rt_leader2_change_pct = _chg_pct(l2_sym)
    for sym, q in rt_quotes.items():
        if isinstance(q, dict) and q.get("last") is not None:
            try:
                rt_prev_last[sym] = float(q.get("last"))
            except Exception:
                pass
    state["rt_prev_last"] = rt_prev_last

    bars, bars_source = _fetch_bars(symbol, days, kline)
    today_d, tday = _today_bars(cfg, bars)
    sk = str(today_d)
    if state.get("last_session_key") != sk:
        session.reset()
        state["last_session_key"] = sk
        open_live = None
        restored_boot = None
        if restore_open_positions_on_startup:
            try:
                restored_open_live, restored_meta = _restore_open_state_on_startup(
                    session=session,
                    raw=raw,
                    cfg=cfg,
                    symbol=symbol,
                    state_file=state_file,
                )
                if restored_open_live:
                    open_live = restored_open_live
                    restored_boot = restored_meta if isinstance(restored_meta, dict) else {"restored": True}
                    _append_decision_tail(
                        symbol=symbol,
                        session_date=sk,
                        bar_dt=tday[-1].date if tday else boot_dt,
                        cfg=cfg,
                        logs=[{"message": "worker_restored_open_position", "as_of": "", "extra": restored_boot}],
                        action={
                            "action": "worker_restored_open_position",
                            **(restored_boot or {}),
                            **_action_bar_fields(tday[-1].date if tday else boot_dt, cfg),
                        },
                    )
            except Exception as e:
                _append_decision_tail(
                    symbol=symbol,
                    session_date=sk,
                    bar_dt=tday[-1].date if tday else boot_dt,
                    cfg=cfg,
                    logs=[{"message": "worker_restore_open_position_failed", "as_of": "", "extra": {"error": str(e)}}],
                    action={
                        "action": "worker_restore_open_position_failed",
                        "error": str(e),
                        **_action_bar_fields(tday[-1].date if tday else boot_dt, cfg),
                    },
                )
    state["open_live"] = open_live
    state["restored_boot"] = restored_boot

    if not tday:
        return {
            "status": "idle_no_intraday_bars",
            "symbol": symbol,
            "restored_open_position": restored_boot,
            "open_state_snapshot_path": state_file,
            "open_state_snapshot_saved_at": _open_state_snapshot_saved_at(state_file),
            "session_date": sk,
            "last_loop_at": loop_started,
            "bars_today": 0,
            "intraday_safety": _intraday_safety_config(raw) if _is_stock_options_intraday_mode(raw) else None,
            "intraday_safety_summary": _intraday_safety_runtime_summary(raw, None),
            "dry_run": dry_run,
            "bars_source": bars_source,
            **rt_quote_fields,
        }

    n_have = len(session.bars_snapshot())
    if n_have > len(tday):
        session.reset()
        n_have = 0
    if skip_historical_on_startup and n_have == 0 and len(tday) > 1:
        n_have = len(tday) - 1
        _append_decision_tail(
            symbol=symbol,
            session_date=sk,
            bar_dt=tday[-1].date,
            cfg=cfg,
            logs=[{"message": "skip_historical_bars_on_startup", "as_of": "", "extra": {"skipped_bars": n_have, "bars_today": len(tday)}}],
            action={"action": "skip_historical_bars_on_startup", **_action_bar_fields(tday[-1].date, cfg)},
        )

    tz_nm = str(getattr(cfg, "assume_bars_timezone", None) or "UTC")
    ndates = sorted({ny_date(b.date, tz_nm) for b in bars})
    prior_d = prior_trading_date_with_data(ndates, today_d)
    if prior_d:
        anchor = [b for b in bars if ny_date(b.date, tz_nm) == prior_d]
        anchor.sort(key=lambda b: b.date)
        session.set_anchor_bars(anchor)
    else:
        session.set_anchor_bars([])

    _refresh_session_live_quotes(session, open_live)

    last_result: Any = None
    act: dict[str, Any] = {"action": "no_trade", **_action_bar_fields(tday[-1].date, cfg)}
    for j in range(n_have, len(tday)):
        bar_j = tday[j]
        pos_before_bar = copy.deepcopy(session.open_position())
        had_pos_before = pos_before_bar is not None
        res = session.push_bar(bar_j)
        last_result = res
        act_j: dict[str, Any]
        bar_j_utc = _bar_to_utc_datetime(bar_j.date, cfg)
        bar_age_sec = (datetime.now(timezone.utc) - bar_j_utc).total_seconds()
        force_close_exit = bool(res.close_position and _is_force_close_reason(res.close_reason))
        trade_allowed = dry_run or bar_age_sec <= trade_freshness_sec or force_close_exit

        if res.close_position and res.exit_snapshot:
            if not trade_allowed:
                session.restore_open_position(pos_before_bar)
                act = {
                    "action": "skip_stale_trade_signal",
                    "reason": "stale_bar_close_signal",
                    "close_reason": res.close_reason,
                    "bar_age_seconds": round(bar_age_sec, 3),
                    "freshness_seconds": trade_freshness_sec,
                    **_action_bar_fields(bar_j.date, cfg),
                }
                act_j = act
                _append_decision_tail(
                    symbol=symbol,
                    session_date=sk,
                    bar_dt=bar_j.date,
                    cfg=cfg,
                    logs=[{"message": getattr(x, "message", ""), "as_of": getattr(x, "as_of", ""), "extra": getattr(x, "extra", {})} for x in (res.logs or [])],
                    action=act_j,
                )
                continue
            ex = res.exit_snapshot
            side = str(ex.get("side") or "")
            if side == "double_strangle":
                ok, detail = _resolve_and_exit_double_strangle(
                    ex,
                    underlying=symbol,
                    raw=raw,
                    cfg=cfg,
                    dry_run=dry_run,
                    resolve_defaults=resolve_defaults,
                )
            elif side == "strangle":
                ok, detail = _resolve_and_exit_strangle(
                    ex,
                    underlying=symbol,
                    raw=raw,
                    cfg=cfg,
                    dry_run=dry_run,
                    resolve_defaults=resolve_defaults,
                )
            else:
                right = "call" if "call" in side else "put"
                ok, detail = _resolve_and_exit(
                    strike=float(ex.get("strike") or 0.0),
                    right=right,
                    contracts=int(ex.get("contracts") or 1),
                    limit_px=float(ex.get("exit_px") or 0.0),
                    underlying=symbol,
                    raw=raw,
                    cfg=cfg,
                    dry_run=dry_run,
                    resolve_defaults=resolve_defaults,
                )
            act = {
                "action": "exit",
                "ok": ok,
                "detail": detail,
                "close_reason": res.close_reason,
                "force_close_freshness_bypass": bool(force_close_exit and bar_age_sec > trade_freshness_sec),
                **_action_bar_fields(bar_j.date, cfg),
            }
            act_j = act
            if ok:
                _append_execution_ledger_from_detail(detail if isinstance(detail, dict) else {})
                if side == "double_strangle":
                    lc = str(ex.get("strangle_partial_leg") or "none")
                    prev_ol = open_live
                    if lc == "none":
                        session.clear_open_position()
                        open_live = None
                    else:
                        exit_fills = _strangle_exit_fill_prices_from_detail(detail)
                        leg_exit_px = ex.get("leg_exit_px") if isinstance(ex.get("leg_exit_px"), dict) else {}
                        px = float(exit_fills.get(lc) or leg_exit_px.get(lc) or 0.0)
                        session.apply_double_strangle_leg_closed(lc, px)
                        open_live = _open_live_after_double_strangle_partial(session, prev_ol)
                elif side == "strangle":
                    lc = str(ex.get("strangle_partial_leg") or "none")
                    prev_ol = open_live
                    if lc == "none":
                        session.clear_open_position()
                        open_live = None
                    elif lc == "call":
                        exit_fills = _strangle_exit_fill_prices_from_detail(detail)
                        px = float(exit_fills.get("call") or ex.get("call_exit_px") or 0.0)
                        session.apply_strangle_leg_closed("call", px)
                        open_live = _open_live_after_strangle_partial(session, prev_ol)
                    elif lc == "put":
                        exit_fills = _strangle_exit_fill_prices_from_detail(detail)
                        px = float(exit_fills.get("put") or ex.get("put_exit_px") or 0.0)
                        session.apply_strangle_leg_closed("put", px)
                        open_live = _open_live_after_strangle_partial(session, prev_ol)
                else:
                    open_live = None
            elif isinstance(detail, dict) and str(detail.get("error") or "") == "manual_close_detected_or_position_insufficient":
                open_live = _sync_session_after_manual_close(session, open_live, raw)
        elif res.intents:
            if not trade_allowed:
                if not had_pos_before:
                    session.clear_open_position()
                act = {
                    "action": "skip_stale_trade_signal",
                    "reason": "stale_bar_entry_signal",
                    "bar_age_seconds": round(bar_age_sec, 3),
                    "freshness_seconds": trade_freshness_sec,
                    **_action_bar_fields(bar_j.date, cfg),
                }
                act_j = act
                _append_decision_tail(
                    symbol=symbol,
                    session_date=sk,
                    bar_dt=bar_j.date,
                    cfg=cfg,
                    logs=[{"message": getattr(x, "message", ""), "as_of": getattr(x, "as_of", ""), "extra": getattr(x, "extra", {})} for x in (res.logs or [])],
                    action=act_j,
                )
                continue
            entry_guard = _unrecovered_entry_guard(
                session=session,
                open_live=open_live,
                raw=raw,
                cfg=cfg,
                symbol=symbol,
            )
            if entry_guard is None:
                entry_guard = _intraday_manual_review_entry_guard(raw)
            if entry_guard is None:
                entry_guard = _intraday_unmanaged_position_entry_guard(
                    session=session,
                    open_live=open_live,
                    raw=raw,
                    cfg=cfg,
                    symbol=symbol,
                )
            if entry_guard is not None:
                if not had_pos_before:
                    session.clear_open_position()
                act = {
                    "action": "skip_entry_unrecovered_open_position"
                    if str(entry_guard.get("reason") or "") == "unrecovered_worker_open_position"
                    else "skip_entry_position_guard",
                    **entry_guard,
                    **_action_bar_fields(bar_j.date, cfg),
                }
                act_j = act
                guard_message = str(act.get("action") or "skip_entry_position_guard")
                _append_decision_tail(
                    symbol=symbol,
                    session_date=sk,
                    bar_dt=bar_j.date,
                    cfg=cfg,
                    logs=[{"message": getattr(x, "message", ""), "as_of": getattr(x, "as_of", ""), "extra": getattr(x, "extra", {})} for x in (res.logs or [])]
                    + [{"message": guard_message, "as_of": "", "extra": entry_guard}],
                    action=act_j,
                )
                continue
            intents = res.intents
            if len(intents) >= 4 and all(getattr(x, "reason", "") == "morning_double_strangle" for x in intents[:4]):
                ok, detail = _process_resolve_and_enter_multi_leg(
                    intents[:4],
                    raw,
                    cfg,
                    dry_run=dry_run,
                    resolve_defaults=resolve_defaults,
                    mode_name="double_strangle",
                )
            elif (
                len(intents) >= 2
                and getattr(intents[0], "reason", "") == "morning_strangle"
                and getattr(intents[1], "reason", "") == "morning_strangle"
            ):
                ok, detail = _process_resolve_and_enter_strangle(
                    intents[0],
                    intents[1],
                    raw,
                    cfg,
                    dry_run=dry_run,
                    resolve_defaults=resolve_defaults,
                )
            else:
                ok, detail = _process_resolve_and_enter(
                    intents[0],
                    raw,
                    cfg,
                    dry_run=dry_run,
                    resolve_defaults=resolve_defaults,
                )
            act = {"action": "entry", "ok": ok, "detail": detail, **_action_bar_fields(bar_j.date, cfg)}
            act_j = act
            if ok:
                _append_execution_ledger_from_detail(detail if isinstance(detail, dict) else {})
                _patch_session_entry_px_from_order(session, detail=detail if isinstance(detail, dict) else {}, intents=intents)
                open_live = _extract_open_live_from_entry(detail if isinstance(detail, dict) else {}, intents)
        else:
            act = {"action": "hold" if session.open_position() is not None else "no_trade", **_action_bar_fields(bar_j.date, cfg)}
            act_j = act

        _append_decision_tail(
            symbol=symbol,
            session_date=sk,
            bar_dt=bar_j.date,
            cfg=cfg,
            logs=[{"message": getattr(x, "message", ""), "as_of": getattr(x, "as_of", ""), "extra": getattr(x, "extra", {})} for x in (res.logs or [])],
            action=act_j,
        )
        state["open_live"] = open_live
        _sync_open_state_snapshot(session=session, open_live=open_live, symbol=symbol, session_date=sk, raw=raw, state_file=state_file)

    if last_result is not None:
        bar = tday[-1]
        if not (last_result.close_position and last_result.exit_snapshot):
            bar_utc = _bar_to_utc_datetime(bar.date, cfg)
            bar_age_sec = (datetime.now(timezone.utc) - bar_utc).total_seconds()
            if dry_run or bar_age_sec <= trade_freshness_sec:
                rt_triggered, rt_action, open_live = _realtime_exit_check(
                    session=session,
                    open_live=open_live,
                    symbol=symbol,
                    raw=raw,
                    cfg=cfg,
                    dry_run=dry_run,
                    resolve_defaults=resolve_defaults,
                )
                if rt_triggered:
                    act = {**rt_action, **_action_bar_fields(bar.date, cfg)}
                    _append_realtime_exit_decision_tail(symbol=symbol, session_date=sk, bar_dt=bar.date, cfg=cfg, rt_action=act)
        force_scan = _force_close_unrecovered_worker_positions(raw=raw, cfg=cfg, symbol=symbol, dry_run=dry_run, open_live=open_live)
        if force_scan is not None:
            act = {**force_scan, **_action_bar_fields(bar.date, cfg)}
            _append_decision_tail(
                symbol=symbol,
                session_date=sk,
                bar_dt=bar.date,
                cfg=cfg,
                logs=[{"message": str(force_scan.get("action") or "force_close_unrecovered_positions"), "as_of": "", "extra": force_scan}],
                action=act,
            )
        state["open_live"] = open_live
        _sync_open_state_snapshot(session=session, open_live=open_live, symbol=symbol, session_date=sk, raw=raw, state_file=state_file)
        status = "noop_no_new_bars" if str(act.get("action") or "") == "force_close_unrecovered_positions_expired" else "ok"
    else:
        rt_triggered, rt_action, open_live = _realtime_exit_check(
            session=session,
            open_live=open_live,
            symbol=symbol,
            raw=raw,
            cfg=cfg,
            dry_run=dry_run,
            resolve_defaults=resolve_defaults,
        )
        if rt_triggered:
            act = {**rt_action, **_action_bar_fields(tday[-1].date, cfg)}
            _append_realtime_exit_decision_tail(symbol=symbol, session_date=sk, bar_dt=tday[-1].date, cfg=cfg, rt_action=act)
        else:
            force_scan = _force_close_unrecovered_worker_positions(raw=raw, cfg=cfg, symbol=symbol, dry_run=dry_run, open_live=open_live)
            if force_scan is not None:
                act = {**force_scan, **_action_bar_fields(tday[-1].date, cfg)}
                _append_decision_tail(
                    symbol=symbol,
                    session_date=sk,
                    bar_dt=tday[-1].date,
                    cfg=cfg,
                    logs=[{"message": str(force_scan.get("action") or "force_close_unrecovered_positions"), "as_of": "", "extra": force_scan}],
                    action=act,
                )
            else:
                act = {"action": "noop_no_new_bars", **_action_bar_fields(tday[-1].date, cfg)}
        state["open_live"] = open_live
        _sync_open_state_snapshot(session=session, open_live=open_live, symbol=symbol, session_date=sk, raw=raw, state_file=state_file)
        status = "noop_no_new_bars" if str(act.get("action") or "") in {"noop_no_new_bars", "force_close_unrecovered_positions_expired"} else "ok"

    return {
        "status": status,
        "symbol": symbol,
        "restored_open_position": state.get("restored_boot"),
        "open_state_snapshot_path": state_file,
        "open_state_snapshot_saved_at": _open_state_snapshot_saved_at(state_file),
        "session_date": sk,
        "last_loop_at": loop_started,
        "bars_today": len(tday),
        "last_action": act,
        "intraday_safety": _intraday_safety_config(raw) if _is_stock_options_intraday_mode(raw) else None,
        "intraday_safety_summary": _intraday_safety_runtime_summary(raw, act),
        "dry_run": dry_run,
        "bars_source": bars_source,
        **rt_quote_fields,
        **_bar_debug_fields(tday[-1].date, cfg),
    }


def _run_stock_options_pool_loop(
    *,
    config_path: str,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    initial_pool: list[str],
) -> None:
    global _API_BASE_URL, _API_BEARER_TOKEN, _API_KEY

    pool = list(initial_pool)
    days = max(1, int(raw.get("history_days", 2)))
    kline = str(raw.get("kline", "1m"))
    poll = max(5.0, float(raw.get("poll_seconds", 30)))
    dry_run = bool(raw.get("dry_run", True))
    trade_freshness_sec = max(0.0, float(raw.get("trade_bar_freshness_seconds", 90)))
    skip_historical_on_startup = bool(raw.get("skip_historical_bars_on_startup", True))
    restore_open_positions_on_startup = bool(raw.get("restore_open_positions_on_startup", True))
    if str(os.getenv("QQQ_0DTE_LIVE_DRY_RUN", "")).strip().lower() in {"1", "true", "yes", "on"}:
        dry_run = True

    _API_BASE_URL = str(raw.get("api_base_url") or _API_BASE_URL).strip().rstrip("/")
    _API_BEARER_TOKEN = _resolve_api_bearer(raw)
    _API_KEY = _resolve_api_key(raw, config_path)
    resolve_defaults: dict[str, Any] = raw.get("resolve") if isinstance(raw.get("resolve"), dict) else {}

    _write_pid()
    boot_dt = datetime.now(timezone.utc)
    states: dict[str, dict[str, Any]] = {sym: _new_stock_symbol_state(raw, sym) for sym in pool}
    account_switch_logged = False
    _append_decision_tail(
        symbol=",".join(pool),
        session_date="(boot)",
        bar_dt=boot_dt,
        cfg=cfg,
        logs=[
            {
                "message": "stock_options_worker_started",
                "as_of": "",
                "extra": {"config_path": config_path, "stock_pool": pool, **_worker_account_context(raw)},
            }
        ],
        action={
            "action": "stock_options_worker_started",
            "mode": "stock_options_pool",
            "stock_pool": pool,
            "intraday_safety": _intraday_safety_config(raw) if _is_stock_options_intraday_mode(raw) else None,
            "dry_run": dry_run,
            "poll_seconds": poll,
            "options_auth_configured": bool(str(_API_KEY or "").strip() or str(_API_BEARER_TOKEN or "").strip()),
            **_worker_account_context(raw),
            **_action_bar_fields(boot_dt, cfg),
        },
    )
    if not dry_run and not str(_API_KEY or "").strip() and not str(_API_BEARER_TOKEN or "").strip():
        print("[qqq_live_worker] stock options worker warning: missing API auth for /options/*", file=sys.stderr)

    while not _should_stop():
        loop_started = datetime.now(timezone.utc).isoformat()
        summaries: list[dict[str, Any]] = []
        try:
            fresh = _load_worker_config(config_path)
            if isinstance(fresh, dict):
                _API_BEARER_TOKEN = _resolve_api_bearer(fresh)
                _API_KEY = _resolve_api_key(fresh, config_path)
                matches_config_context, config_context_reason = _config_matches_worker_context(fresh)
                configured_account_id = str(fresh.get("account_id") or "").strip()
                if not matches_config_context and not account_switch_logged:
                    account_switch_logged = True
                    _append_decision_tail(
                        symbol=",".join(pool),
                        session_date="(unknown)",
                        bar_dt=boot_dt,
                        cfg=cfg,
                        logs=[
                            {
                                "message": "config_account_switch_ignored",
                                "as_of": "",
                                "extra": {
                                    "reason": config_context_reason,
                                    "configured_owner_id": str(fresh.get("owner_id") or "").strip().lower() or None,
                                    "configured_account_id": configured_account_id,
                                    "configured_broker_provider": str(fresh.get("broker_provider") or "").strip().lower() or None,
                                    "worker_owner_id": _API_LOCAL_OWNER or None,
                                    "worker_account_id": _API_ACCOUNT_ID,
                                    "worker_broker_provider": _API_BROKER_PROVIDER or None,
                                    **_worker_account_context(raw),
                                },
                            }
                        ],
                        action={
                            "action": "config_account_switch_ignored",
                            "reason": config_context_reason,
                            "configured_owner_id": str(fresh.get("owner_id") or "").strip().lower() or None,
                            "configured_account_id": configured_account_id,
                            "configured_broker_provider": str(fresh.get("broker_provider") or "").strip().lower() or None,
                            "worker_owner_id": _API_LOCAL_OWNER or None,
                            "worker_account_id": _API_ACCOUNT_ID,
                            "worker_broker_provider": _API_BROKER_PROVIDER or None,
                            **_worker_account_context(raw),
                            **_action_bar_fields(boot_dt, cfg),
                        },
                    )
                if not matches_config_context:
                    fresh = None
            if isinstance(fresh, dict):
                next_raw = copy.deepcopy(fresh)
                if _API_ACCOUNT_ID:
                    next_raw["account_id"] = _API_ACCOUNT_ID
                elif str(raw.get("account_id") or "").strip():
                    next_raw["account_id"] = str(raw.get("account_id") or "").strip()
                if _API_BROKER_PROVIDER:
                    next_raw["broker_provider"] = _API_BROKER_PROVIDER
                elif str(raw.get("broker_provider") or "").strip():
                    next_raw["broker_provider"] = str(raw.get("broker_provider") or "").strip().lower()
                raw = _apply_worker_account_context(next_raw)
                resolve_defaults = raw.get("resolve") if isinstance(raw.get("resolve"), dict) else {}
                fresh_pool = _normalize_stock_pool(fresh, cfg)
                if fresh_pool:
                    pool = fresh_pool
                    for sym in pool:
                        if sym not in states:
                            states[sym] = _new_stock_symbol_state(raw, sym)
                        else:
                            next_cfg = _cfg_for_underlying(fresh, sym)
                            states[sym]["cfg"] = next_cfg
                            sess = states[sym].get("session")
                            if isinstance(sess, Qqq0dteLiveSession):
                                sess.cfg = next_cfg
                                ctl = getattr(sess, "_ctl", None)
                                if ctl is not None:
                                    try:
                                        ctl.cfg = next_cfg
                                    except Exception:
                                        pass

            active_symbols = list(pool)
            for sym, st in states.items():
                if sym not in active_symbols and _state_has_open_position(st):
                    active_symbols.append(sym)

            for sym in active_symbols:
                st = states.get(sym)
                if st is None:
                    continue
                try:
                    summaries.append(
                        _process_stock_symbol_once(
                            state=st,
                            raw=raw,
                            days=days,
                            kline=kline,
                            dry_run=dry_run,
                            trade_freshness_sec=trade_freshness_sec,
                            skip_historical_on_startup=skip_historical_on_startup,
                            restore_open_positions_on_startup=restore_open_positions_on_startup,
                            resolve_defaults=resolve_defaults,
                            loop_started=loop_started,
                            boot_dt=boot_dt,
                        )
                    )
                except Exception as e:
                    err_summary = {
                        "status": "error",
                        "symbol": sym,
                        "error": str(e),
                        "last_loop_at": loop_started,
                        "open_state_snapshot_path": str(st.get("state_file") or ""),
                        "open_state_snapshot_saved_at": _open_state_snapshot_saved_at(str(st.get("state_file") or "")),
                    }
                    summaries.append(err_summary)
                    scfg = st.get("cfg") if isinstance(st.get("cfg"), Qqq0dteConfig) else cfg
                    _append_decision_tail(
                        symbol=sym,
                        session_date=str(st.get("last_session_key") or "(unknown)"),
                        bar_dt=boot_dt,
                        cfg=scfg,
                        logs=[{"message": "stock_options_symbol_loop_error", "as_of": "", "extra": {"error": str(e)}}],
                        action={"action": "stock_options_symbol_loop_error", "error": str(e), **_action_bar_fields(boot_dt, scfg)},
                    )

            runtime_status = "ok"
            if summaries and all(str(x.get("status") or "") == "idle_no_intraday_bars" for x in summaries):
                runtime_status = "idle_no_intraday_bars"
            if any(str(x.get("status") or "") == "error" for x in summaries):
                runtime_status = "degraded"
            primary = summaries[0] if summaries else {}
            last_action = next((x.get("last_action") for x in reversed(summaries) if isinstance(x.get("last_action"), dict)), None)
            _write_runtime(
                {
                    "status": runtime_status,
                    "mode": "stock_options_pool",
                    "symbol": pool[0] if pool else "QQQ.US",
                    "stock_pool": pool,
                    "active_symbols": active_symbols,
                    "symbols_state": summaries,
                    "last_action": last_action,
                    "intraday_safety": _intraday_safety_config(raw) if _is_stock_options_intraday_mode(raw) else None,
                    "intraday_safety_summary": _intraday_safety_runtime_summary(raw, last_action if isinstance(last_action, dict) else None),
                    "owner_id": _API_LOCAL_OWNER or None,
                    "last_loop_at": loop_started,
                    "dry_run": dry_run,
                    "kline": kline,
                    "history_days": days,
                    "bars_source": primary.get("bars_source"),
                    "bars_today": primary.get("bars_today"),
                    "restored_open_position": {str(x.get("symbol") or ""): x.get("restored_open_position") for x in summaries if x.get("restored_open_position")},
                    "open_state_snapshots": {
                        str(x.get("symbol") or ""): {
                            "path": x.get("open_state_snapshot_path"),
                            "saved_at": x.get("open_state_snapshot_saved_at"),
                        }
                        for x in summaries
                    },
                    "open_state_snapshot_path": os.path.dirname(OPEN_STATE_FILE),
                    **({k: v for k, v in primary.items() if k.startswith("realtime_quote")} if isinstance(primary, dict) else {}),
                    **(
                        {
                            "last_bar": primary.get("last_bar"),
                            "last_bar_naive_wall": primary.get("last_bar_naive_wall"),
                            "assume_bars_timezone": primary.get("assume_bars_timezone"),
                        }
                        if isinstance(primary, dict)
                        else {}
                    ),
                },
                force=True,
            )
        except Exception as e:
            _write_runtime(
                {
                    "status": "error",
                    "mode": "stock_options_pool",
                    "error": str(e),
                    "owner_id": _API_LOCAL_OWNER or None,
                    "stock_pool": pool,
                    "symbols_state": summaries,
                    "intraday_safety": _intraday_safety_config(raw) if _is_stock_options_intraday_mode(raw) else None,
                    "last_loop_at": loop_started,
                    "dry_run": dry_run,
                    "open_state_snapshot_path": os.path.dirname(OPEN_STATE_FILE),
                },
                force=True,
            )
        time.sleep(poll)

    _remove_pid()
    try:
        if os.path.exists(STOP_FILE):
            os.remove(STOP_FILE)
    except Exception:
        pass
    _remove_runtime()


def run_loop(config_path: str) -> None:
    global _API_BASE_URL, _API_BEARER_TOKEN, _API_KEY, _API_ACCOUNT_ID, _API_BROKER_PROVIDER
    raw_all = _load_worker_config(config_path)
    cfg, raw = _resolve_cfg(raw_all)
    raw = _apply_worker_account_context(raw)
    if not _API_ACCOUNT_ID and str(raw.get("account_id") or "").strip():
        _API_ACCOUNT_ID = str(raw.get("account_id") or "").strip()
    if not _API_BROKER_PROVIDER and str(raw.get("broker_provider") or "").strip():
        _API_BROKER_PROVIDER = str(raw.get("broker_provider") or "").strip().lower()
    stock_pool = _normalize_stock_pool(raw, cfg)
    if _stock_options_pool_enabled(raw, stock_pool):
        _run_stock_options_pool_loop(config_path=config_path, raw=raw, cfg=cfg, initial_pool=stock_pool)
        return
    symbol = str(raw.get("symbol") or cfg.symbol or "QQQ.US").strip().upper()
    days = max(1, int(raw.get("history_days", 2)))
    kline = str(raw.get("kline", "1m"))
    poll = max(5.0, float(raw.get("poll_seconds", 30)))
    dry_run = bool(raw.get("dry_run", True))
    trade_freshness_sec = max(0.0, float(raw.get("trade_bar_freshness_seconds", 90)))
    skip_historical_on_startup = bool(raw.get("skip_historical_bars_on_startup", True))
    if str(os.getenv("QQQ_0DTE_LIVE_DRY_RUN", "")).strip().lower() in {"1", "true", "yes", "on"}:
        dry_run = True

    _API_BASE_URL = str(raw.get("api_base_url") or _API_BASE_URL).strip().rstrip("/")
    _API_BEARER_TOKEN = _resolve_api_bearer(raw)
    _API_KEY = _resolve_api_key(raw, config_path)

    resolve_defaults: dict[str, Any] = raw.get("resolve") if isinstance(raw.get("resolve"), dict) else {}

    _write_pid()
    session = Qqq0dteLiveSession(cfg)
    last_session_key: str | None = None
    open_live: dict[str, Any] | None = None
    restored_boot: dict[str, Any] | None = None
    rt_prev_last: dict[str, float] = {}
    boot_dt = datetime.now(timezone.utc)
    restore_open_positions_on_startup = bool(raw.get("restore_open_positions_on_startup", True))
    _append_decision_tail(
        symbol=symbol,
        session_date="(boot)",
        bar_dt=boot_dt,
        cfg=cfg,
        logs=[
            {
                "message": "worker_started",
                "as_of": "",
                "extra": {"config_path": config_path, **_worker_account_context(raw)},
            }
        ],
        action={
            "action": "worker_started",
            "dry_run": dry_run,
            "poll_seconds": poll,
            "intraday_safety": _intraday_safety_config(raw) if _is_stock_options_intraday_mode(raw) else None,
            "options_auth_configured": bool(
                str(_API_KEY or "").strip() or str(_API_BEARER_TOKEN or "").strip()
            ),
            **_worker_account_context(raw),
            **_action_bar_fields(boot_dt, cfg),
        },
    )
    if not dry_run and not str(_API_KEY or "").strip() and not str(_API_BEARER_TOKEN or "").strip():
        print(
            "[qqq_live_worker] 警告：未配置 QQQ_LIVE_API_KEY（推荐）或 QQQ_LIVE_API_BEARER_TOKEN / api_bearer_token，"
            "实盘 /options/* 将返回 401。请在控制台 POST /auth/api-keys 生成 Key 后写入 api_key 或环境变量 QQQ_LIVE_API_KEY。",
            file=sys.stderr,
        )
    while not _should_stop():
        loop_started = datetime.now(timezone.utc).isoformat()
        try:
            # 允许不重启进程即可更新 api_key / bearer（从配置文件热读）
            _fresh = _load_worker_config(config_path)
            if isinstance(_fresh, dict):
                _API_BEARER_TOKEN = _resolve_api_bearer(_fresh)
                _API_KEY = _resolve_api_key(_fresh, config_path)
                matches_config_context, config_context_reason = _config_matches_worker_context(_fresh)
                configured_account_id = str(_fresh.get("account_id") or "").strip()
                if not matches_config_context:
                    _append_decision_tail(
                        symbol=symbol,
                        session_date=str(last_session_key or "(unknown)"),
                        bar_dt=boot_dt,
                        cfg=cfg,
                        logs=[
                            {
                                "message": "config_account_switch_ignored",
                                "as_of": "",
                                "extra": {
                                    "reason": config_context_reason,
                                    "configured_owner_id": str(_fresh.get("owner_id") or "").strip().lower() or None,
                                    "configured_account_id": configured_account_id,
                                    "configured_broker_provider": str(_fresh.get("broker_provider") or "").strip().lower() or None,
                                    "worker_owner_id": _API_LOCAL_OWNER or None,
                                    "worker_account_id": _API_ACCOUNT_ID,
                                    "worker_broker_provider": _API_BROKER_PROVIDER or None,
                                    **_worker_account_context(raw),
                                },
                            }
                        ],
                        action={
                            "action": "config_account_switch_ignored",
                            "reason": config_context_reason,
                            "configured_owner_id": str(_fresh.get("owner_id") or "").strip().lower() or None,
                            "configured_account_id": configured_account_id,
                            "configured_broker_provider": str(_fresh.get("broker_provider") or "").strip().lower() or None,
                            "worker_owner_id": _API_LOCAL_OWNER or None,
                            "worker_account_id": _API_ACCOUNT_ID,
                            "worker_broker_provider": _API_BROKER_PROVIDER or None,
                            **_worker_account_context(raw),
                            **_action_bar_fields(boot_dt, cfg),
                        },
                    )
                    _fresh = None
            if isinstance(_fresh, dict):
                next_raw = copy.deepcopy(_fresh)
                if _API_ACCOUNT_ID:
                    next_raw["account_id"] = _API_ACCOUNT_ID
                elif str(raw.get("account_id") or "").strip():
                    next_raw["account_id"] = str(raw.get("account_id") or "").strip()
                if _API_BROKER_PROVIDER:
                    next_raw["broker_provider"] = _API_BROKER_PROVIDER
                elif str(raw.get("broker_provider") or "").strip():
                    next_raw["broker_provider"] = str(raw.get("broker_provider") or "").strip().lower()
                raw = _apply_worker_account_context(next_raw)
                cfg, _ = _resolve_cfg(raw)
                session.cfg = cfg
                ctl = getattr(session, "_ctl", None)
                if ctl is not None:
                    try:
                        ctl.cfg = cfg
                    except Exception:
                        pass
                resolve_defaults = raw.get("resolve") if isinstance(raw.get("resolve"), dict) else {}

            # 实时环境上下文（用于 gamma_scalping 过滤：VIX 与龙头联动）
            qqq_quote, qqq_quote_source = _fetch_realtime_quote(symbol)
            rt_quote_fields = _runtime_realtime_quote_fields(symbol, qqq_quote, qqq_quote_source)
            vix_sym = str(getattr(cfg, "gamma_vix_symbol", "VIX.US") or "VIX.US").strip().upper()
            l1_sym = str(getattr(cfg, "gamma_leader_symbol_1", "NVDA.US") or "NVDA.US").strip().upper()
            l2_sym = str(getattr(cfg, "gamma_leader_symbol_2", "TSLA.US") or "TSLA.US").strip().upper()
            vix_quote, _ = _fetch_realtime_quote(vix_sym)
            l1_quote, _ = _fetch_realtime_quote(l1_sym)
            l2_quote, _ = _fetch_realtime_quote(l2_sym)
            rt_quotes: dict[str, dict[str, Any] | None] = {
                symbol: qqq_quote,
                vix_sym: vix_quote,
                l1_sym: l1_quote,
                l2_sym: l2_quote,
            }

            def _chg_pct(sym: str) -> float:
                q = rt_quotes.get(sym)
                if not isinstance(q, dict):
                    return 0.0
                last = q.get("last")
                prev = q.get("prev_close")
                try:
                    if prev is not None and float(prev) > 0:
                        prev_f = max(float(prev), 1e-12)
                        return (float(last) - float(prev)) / prev_f * 100.0
                except Exception:
                    pass
                try:
                    if sym in rt_prev_last and rt_prev_last[sym] > 0:
                        prev_last = max(float(rt_prev_last[sym]), 1e-12)
                        return (float(last) - rt_prev_last[sym]) / prev_last * 100.0
                except Exception:
                    pass
                return 0.0

            cfg.gamma_rt_qqq_change_pct = _chg_pct(symbol)
            cfg.gamma_rt_vix_change_pct = _chg_pct(vix_sym)
            cfg.gamma_rt_leader1_change_pct = _chg_pct(l1_sym)
            cfg.gamma_rt_leader2_change_pct = _chg_pct(l2_sym)
            for sym, q in rt_quotes.items():
                if isinstance(q, dict) and q.get("last") is not None:
                    try:
                        rt_prev_last[sym] = float(q.get("last"))
                    except Exception:
                        pass

            bars, bars_source = _fetch_bars(symbol, days, kline)
            today_d, tday = _today_bars(cfg, bars)
            _maybe_refresh_strategy_recommendation(
                symbol=symbol,
                cfg=cfg,
                bars=bars,
                today_d=today_d,
                rt_fields=_runtime_realtime_quote_fields(symbol, qqq_quote, qqq_quote_source),
                vix_change_pct=float(getattr(cfg, "gamma_rt_vix_change_pct", 0.0) or 0.0),
            )
            sk = str(today_d)
            if last_session_key != sk:
                session.reset()
                last_session_key = sk
                open_live = None
                if restore_open_positions_on_startup:
                    try:
                        restored_open_live, restored_meta = _restore_open_state_on_startup(
                            session=session,
                            raw=raw,
                            cfg=cfg,
                            symbol=symbol,
                        )
                        if restored_open_live:
                            open_live = restored_open_live
                            restored_boot = restored_meta if isinstance(restored_meta, dict) else {"restored": True}
                            _append_decision_tail(
                                symbol=symbol,
                                session_date=sk,
                                bar_dt=tday[-1].date if tday else boot_dt,
                                cfg=cfg,
                                logs=[{"message": "worker_restored_open_position", "as_of": "", "extra": restored_boot}],
                                action={
                                    "action": "worker_restored_open_position",
                                    **(restored_boot or {}),
                                    **_action_bar_fields(tday[-1].date if tday else boot_dt, cfg),
                                },
                            )
                    except Exception as e:
                        _append_decision_tail(
                            symbol=symbol,
                            session_date=sk,
                            bar_dt=tday[-1].date if tday else boot_dt,
                            cfg=cfg,
                            logs=[{"message": "worker_restore_open_position_failed", "as_of": "", "extra": {"error": str(e)}}],
                            action={
                                "action": "worker_restore_open_position_failed",
                                "error": str(e),
                                **_action_bar_fields(tday[-1].date if tday else boot_dt, cfg),
                            },
                        )

            if not tday:
                _write_runtime(
                    {
                        "status": "idle_no_intraday_bars",
                        "symbol": symbol,
                        "owner_id": _API_LOCAL_OWNER or None,
                        "restored_open_position": restored_boot,
                        "open_state_snapshot_path": OPEN_STATE_FILE,
                        "open_state_snapshot_saved_at": _open_state_snapshot_saved_at(),
                        "session_date": sk,
                        "last_loop_at": loop_started,
                        "intraday_safety": _intraday_safety_config(raw) if _is_stock_options_intraday_mode(raw) else None,
                        "intraday_safety_summary": _intraday_safety_runtime_summary(raw, None),
                        "dry_run": dry_run,
                        "assume_bars_timezone": str(getattr(cfg, "assume_bars_timezone", None) or "UTC"),
                        "bars_source": bars_source,
                        **rt_quote_fields,
                    }
                )
                time.sleep(poll)
                continue

            n_have = len(session.bars_snapshot())
            if n_have > len(tday):
                session.reset()
                n_have = 0
            if len(tday) < 1:
                time.sleep(poll)
                continue
            if skip_historical_on_startup and n_have == 0 and len(tday) > 1:
                # 防止重启后回放整天历史 bar 触发实盘“补单”。
                n_have = len(tday) - 1
                _append_decision_tail(
                    symbol=symbol,
                    session_date=sk,
                    bar_dt=tday[-1].date,
                    cfg=cfg,
                    logs=[
                        {
                            "message": "skip_historical_bars_on_startup",
                            "as_of": "",
                            "extra": {"skipped_bars": n_have, "bars_today": len(tday)},
                        }
                    ],
                    action={"action": "skip_historical_bars_on_startup", **_action_bar_fields(tday[-1].date, cfg)},
                )

            # 仅 push 当日 K 线时策略只有「一天」数据，昨收 prev_close 会永远缺失；注入前一美东交易日的 K 线作上下文。
            # 须在 session.reset()（n_have 异常）之后设置，否则会被 reset 清空。
            tz_nm = str(getattr(cfg, "assume_bars_timezone", None) or "UTC")
            ndates = sorted({ny_date(b.date, tz_nm) for b in bars})
            prior_d = prior_trading_date_with_data(ndates, today_d)
            if prior_d:
                anchor = [b for b in bars if ny_date(b.date, tz_nm) == prior_d]
                anchor.sort(key=lambda b: b.date)
                session.set_anchor_bars(anchor)
            else:
                session.set_anchor_bars([])

            # 一次轮询可能连续 push 多根新 K 线（追进度 / API 延迟）。必须在「产生 intents / 平仓」的那一根
            # 立即下单；若只读最后一根的 BarProcessResult，会丢掉中间 bar 的开仓信号（策略已写入持仓但从未下单）。
            pos_loop = session.open_position()
            if (
                open_live
                and isinstance(open_live, dict)
                and str(open_live.get("mode") or "") == "double_strangle"
                and pos_loop is not None
                and str(getattr(pos_loop, "side", "") or "") == "double_strangle"
            ):
                leg_symbols = open_live.get("leg_symbols")
                legs_map = getattr(pos_loop, "double_strangle_legs", None)
                bids: dict[str, float | None] = {}
                lasts: dict[str, float | None] = {}
                if isinstance(leg_symbols, dict) and isinstance(legs_map, dict):
                    for key in DOUBLE_STRANGLE_LEG_KEYS:
                        leg = legs_map.get(key)
                        if not isinstance(leg, dict) or not bool(leg.get("active", True)):
                            continue
                        sym = str(leg_symbols.get(key) or "").strip().upper()
                        if sym:
                            bid, last = _fetch_realtime_bid_last(sym)
                            bids[key] = bid
                            lasts[key] = last
                session.set_double_strangle_live_quotes(bids, lasts)
                session.set_strangle_live_quotes(None, None, None, None)
                session.set_option_live_quotes(None, None)
            elif (
                open_live
                and isinstance(open_live, dict)
                and str(open_live.get("mode") or "") == "strangle"
                and pos_loop is not None
                and str(getattr(pos_loop, "side", "") or "") == "strangle"
            ):
                cs = str(open_live.get("call_symbol") or "").strip().upper()
                ps = str(open_live.get("put_symbol") or "").strip().upper()
                ca = bool(getattr(pos_loop, "strangle_call_active", True))
                pa = bool(getattr(pos_loop, "strangle_put_active", True))
                cb, cl = _fetch_realtime_bid_last(cs) if (cs and ca) else (None, None)
                pb, pl = _fetch_realtime_bid_last(ps) if (ps and pa) else (None, None)
                session.set_strangle_live_quotes(cb, pb, cl, pl)
                session.set_double_strangle_live_quotes(None, None)
                session.set_option_live_quotes(None, None)
            elif (
                open_live
                and isinstance(open_live, dict)
                and str(open_live.get("mode") or "") not in {"strangle", "double_strangle"}
                and pos_loop is not None
                and str(getattr(pos_loop, "side", "") or "") in ("long_call", "long_put")
            ):
                sym = str(open_live.get("symbol") or "").strip().upper()
                if sym:
                    ob, ol = _fetch_realtime_bid_last(sym)
                    session.set_option_live_quotes(ob, ol)
                else:
                    session.set_option_live_quotes(None, None)
                session.set_strangle_live_quotes(None, None, None, None)
                session.set_double_strangle_live_quotes(None, None)
            else:
                session.set_strangle_live_quotes(None, None, None, None)
                session.set_double_strangle_live_quotes(None, None)
                session.set_option_live_quotes(None, None)

            last_result: Any = None
            act: dict[str, Any] = {"action": "no_trade", **_action_bar_fields(tday[-1].date, cfg)}
            for j in range(n_have, len(tday)):
                bar_j = tday[j]
                pos_before_bar = copy.deepcopy(session.open_position())
                had_pos_before = pos_before_bar is not None
                res = session.push_bar(bar_j)
                last_result = res
                act_j: dict[str, Any]
                bar_j_utc = _bar_to_utc_datetime(bar_j.date, cfg)
                bar_age_sec = (datetime.now(timezone.utc) - bar_j_utc).total_seconds()
                force_close_exit = bool(res.close_position and _is_force_close_reason(res.close_reason))
                trade_allowed = dry_run or bar_age_sec <= trade_freshness_sec or force_close_exit

                if res.close_position and res.exit_snapshot:
                    if not trade_allowed:
                        session.restore_open_position(pos_before_bar)
                        act = {
                            "action": "skip_stale_trade_signal",
                            "reason": "stale_bar_close_signal",
                            "close_reason": res.close_reason,
                            "bar_age_seconds": round(bar_age_sec, 3),
                            "freshness_seconds": trade_freshness_sec,
                            **_action_bar_fields(bar_j.date, cfg),
                        }
                        act_j = act
                        _append_decision_tail(
                            symbol=symbol,
                            session_date=sk,
                            bar_dt=bar_j.date,
                            cfg=cfg,
                            logs=[
                                {"message": getattr(x, "message", ""), "as_of": getattr(x, "as_of", ""), "extra": getattr(x, "extra", {})}
                                for x in (res.logs or [])
                            ],
                            action=act_j,
                        )
                        continue
                    ex = res.exit_snapshot
                    side = str(ex.get("side") or "")
                    if side == "double_strangle":
                        ok, detail = _resolve_and_exit_double_strangle(
                            ex,
                            underlying=symbol,
                            raw=raw,
                            cfg=cfg,
                            dry_run=dry_run,
                            resolve_defaults=resolve_defaults,
                        )
                    elif side == "strangle":
                        ok, detail = _resolve_and_exit_strangle(
                            ex,
                            underlying=symbol,
                            raw=raw,
                            cfg=cfg,
                            dry_run=dry_run,
                            resolve_defaults=resolve_defaults,
                        )
                    else:
                        right = "call" if "call" in side else "put"
                        strike = float(ex.get("strike") or 0.0)
                        contracts = int(ex.get("contracts") or 1)
                        exit_px = float(ex.get("exit_px") or 0.0)
                        ok, detail = _resolve_and_exit(
                            strike=strike,
                            right=right,
                            contracts=contracts,
                            limit_px=exit_px,
                            underlying=symbol,
                            raw=raw,
                            cfg=cfg,
                            dry_run=dry_run,
                            resolve_defaults=resolve_defaults,
                        )
                    act = {
                        "action": "exit",
                        "ok": ok,
                        "detail": detail,
                        "close_reason": res.close_reason,
                        "force_close_freshness_bypass": bool(force_close_exit and bar_age_sec > trade_freshness_sec),
                        **_action_bar_fields(bar_j.date, cfg),
                    }
                    act_j = act
                    if ok:
                        _append_execution_ledger_from_detail(detail if isinstance(detail, dict) else {})
                        if side == "double_strangle":
                            lc = str(ex.get("strangle_partial_leg") or "none")
                            prev_ol = open_live
                            if lc == "none":
                                session.clear_open_position()
                                open_live = None
                            else:
                                exit_fills = _strangle_exit_fill_prices_from_detail(detail)
                                leg_exit_px = ex.get("leg_exit_px") if isinstance(ex.get("leg_exit_px"), dict) else {}
                                px = float(exit_fills.get(lc) or leg_exit_px.get(lc) or 0.0)
                                session.apply_double_strangle_leg_closed(lc, px)
                                open_live = _open_live_after_double_strangle_partial(session, prev_ol)
                        elif side == "strangle":
                            lc = str(ex.get("strangle_partial_leg") or "none")
                            prev_ol = open_live
                            if lc == "none":
                                session.clear_open_position()
                                open_live = None
                            elif lc == "call":
                                exit_fills = _strangle_exit_fill_prices_from_detail(detail)
                                px = float(exit_fills.get("call") or ex.get("call_exit_px") or 0.0)
                                session.apply_strangle_leg_closed("call", px)
                                open_live = _open_live_after_strangle_partial(session, prev_ol)
                            elif lc == "put":
                                exit_fills = _strangle_exit_fill_prices_from_detail(detail)
                                px = float(exit_fills.get("put") or ex.get("put_exit_px") or 0.0)
                                session.apply_strangle_leg_closed("put", px)
                                open_live = _open_live_after_strangle_partial(session, prev_ol)
                        else:
                            open_live = None
                    elif isinstance(detail, dict) and str(detail.get("error") or "") == "manual_close_detected_or_position_insufficient":
                        open_live = _sync_session_after_manual_close(session, open_live, raw)
                elif res.intents:
                    if not trade_allowed:
                        # push_bar 可能已在策略层创建虚拟持仓；若本轮未真正下单，需要回滚。
                        if not had_pos_before:
                            session.clear_open_position()
                        act = {
                            "action": "skip_stale_trade_signal",
                            "reason": "stale_bar_entry_signal",
                            "bar_age_seconds": round(bar_age_sec, 3),
                            "freshness_seconds": trade_freshness_sec,
                            **_action_bar_fields(bar_j.date, cfg),
                        }
                        act_j = act
                        _append_decision_tail(
                            symbol=symbol,
                            session_date=sk,
                            bar_dt=bar_j.date,
                            cfg=cfg,
                            logs=[
                                {"message": getattr(x, "message", ""), "as_of": getattr(x, "as_of", ""), "extra": getattr(x, "extra", {})}
                                for x in (res.logs or [])
                            ],
                            action=act_j,
                        )
                        continue
                    entry_guard = _unrecovered_entry_guard(
                        session=session,
                        open_live=open_live,
                        raw=raw,
                        cfg=cfg,
                        symbol=symbol,
                    )
                    if entry_guard is None:
                        entry_guard = _intraday_manual_review_entry_guard(raw)
                    if entry_guard is None:
                        entry_guard = _intraday_unmanaged_position_entry_guard(
                            session=session,
                            open_live=open_live,
                            raw=raw,
                            cfg=cfg,
                            symbol=symbol,
                        )
                    if entry_guard is not None:
                        if not had_pos_before:
                            session.clear_open_position()
                        act = {
                            "action": "skip_entry_unrecovered_open_position"
                            if str(entry_guard.get("reason") or "") == "unrecovered_worker_open_position"
                            else "skip_entry_position_guard",
                            **entry_guard,
                            **_action_bar_fields(bar_j.date, cfg),
                        }
                        act_j = act
                        guard_message = str(act.get("action") or "skip_entry_position_guard")
                        _append_decision_tail(
                            symbol=symbol,
                            session_date=sk,
                            bar_dt=bar_j.date,
                            cfg=cfg,
                            logs=[
                                {"message": getattr(x, "message", ""), "as_of": getattr(x, "as_of", ""), "extra": getattr(x, "extra", {})}
                                for x in (res.logs or [])
                            ]
                            + [{"message": guard_message, "as_of": "", "extra": entry_guard}],
                            action=act_j,
                        )
                        continue
                    intents = res.intents
                    if (
                        len(intents) >= 4
                        and all(getattr(x, "reason", "") == "morning_double_strangle" for x in intents[:4])
                    ):
                        ok, detail = _process_resolve_and_enter_multi_leg(
                            intents[:4],
                            raw,
                            cfg,
                            dry_run=dry_run,
                            resolve_defaults=resolve_defaults,
                            mode_name="double_strangle",
                        )
                    elif (
                        len(intents) >= 2
                        and getattr(intents[0], "reason", "") == "morning_strangle"
                        and getattr(intents[1], "reason", "") == "morning_strangle"
                    ):
                        ok, detail = _process_resolve_and_enter_strangle(
                            intents[0],
                            intents[1],
                            raw,
                            cfg,
                            dry_run=dry_run,
                            resolve_defaults=resolve_defaults,
                        )
                    else:
                        ok, detail = _process_resolve_and_enter(
                            intents[0],
                            raw,
                            cfg,
                            dry_run=dry_run,
                            resolve_defaults=resolve_defaults,
                        )
                    act = {
                        "action": "entry",
                        "ok": ok,
                        "detail": detail,
                        **_action_bar_fields(bar_j.date, cfg),
                    }
                    act_j = act
                    if ok:
                        _append_execution_ledger_from_detail(detail if isinstance(detail, dict) else {})
                        _patch_session_entry_px_from_order(
                            session,
                            detail=detail if isinstance(detail, dict) else {},
                            intents=intents,
                        )
                        open_live = _extract_open_live_from_entry(detail if isinstance(detail, dict) else {}, intents)
                else:
                    if session.open_position() is not None:
                        act = {"action": "hold", **_action_bar_fields(bar_j.date, cfg)}
                    else:
                        act = {"action": "no_trade", **_action_bar_fields(bar_j.date, cfg)}
                    act_j = act
                _append_decision_tail(
                    symbol=symbol,
                    session_date=sk,
                    bar_dt=bar_j.date,
                    cfg=cfg,
                    logs=[{"message": getattr(x, "message", ""), "as_of": getattr(x, "as_of", ""), "extra": getattr(x, "extra", {})} for x in (res.logs or [])],
                    action=act_j,
                )
                _sync_open_state_snapshot(
                    session=session,
                    open_live=open_live,
                    symbol=symbol,
                    session_date=sk,
                )

            if last_result is not None:
                bar = tday[-1]

                if not (last_result.close_position and last_result.exit_snapshot):
                    bar_utc = _bar_to_utc_datetime(bar.date, cfg)
                    bar_age_sec = (datetime.now(timezone.utc) - bar_utc).total_seconds()
                    rt_allowed = dry_run or bar_age_sec <= trade_freshness_sec
                    if rt_allowed:
                        rt_triggered, rt_action, open_live = _realtime_exit_check(
                            session=session,
                            open_live=open_live,
                            symbol=symbol,
                            raw=raw,
                            cfg=cfg,
                            dry_run=dry_run,
                            resolve_defaults=resolve_defaults,
                        )
                        if rt_triggered:
                            act = {**rt_action, **_action_bar_fields(bar.date, cfg)}
                            _append_realtime_exit_decision_tail(
                                symbol=symbol,
                                session_date=sk,
                                bar_dt=bar.date,
                                cfg=cfg,
                                rt_action=act,
                            )
                            _sync_open_state_snapshot(
                                session=session,
                                open_live=open_live,
                                symbol=symbol,
                                session_date=sk,
                            )

                force_scan = _force_close_unrecovered_worker_positions(
                    raw=raw,
                    cfg=cfg,
                    symbol=symbol,
                    dry_run=dry_run,
                    open_live=open_live,
                )
                if force_scan is not None:
                    act = {**force_scan, **_action_bar_fields(bar.date, cfg)}
                    _append_decision_tail(
                        symbol=symbol,
                        session_date=sk,
                        bar_dt=bar.date,
                        cfg=cfg,
                        logs=[{"message": str(force_scan.get("action") or "force_close_unrecovered_positions"), "as_of": "", "extra": force_scan}],
                        action=act,
                    )

                _sync_open_state_snapshot(
                    session=session,
                    open_live=open_live,
                    symbol=symbol,
                    session_date=sk,
                )

                _write_runtime(
                    {
                        "status": (
                            "noop_no_new_bars"
                            if str(act.get("action") or "") == "force_close_unrecovered_positions_expired"
                            else "ok"
                        ),
                        "symbol": symbol,
                        "restored_open_position": restored_boot,
                        "open_state_snapshot_path": OPEN_STATE_FILE,
                        "open_state_snapshot_saved_at": _open_state_snapshot_saved_at(),
                        "session_date": sk,
                        "last_loop_at": loop_started,
                        "bars_today": len(tday),
                        "last_action": act,
                        "dry_run": dry_run,
                        "owner_id": _API_LOCAL_OWNER or None,
                        "bars_source": bars_source,
                        **rt_quote_fields,
                        **_bar_debug_fields(bar.date, cfg),
                    },
                    force=True,
                )
            else:
                rt_triggered, rt_action, open_live = _realtime_exit_check(
                    session=session,
                    open_live=open_live,
                    symbol=symbol,
                    raw=raw,
                    cfg=cfg,
                    dry_run=dry_run,
                    resolve_defaults=resolve_defaults,
                )
                if rt_triggered:
                    rt_action_with_bar = {**rt_action, **_action_bar_fields(tday[-1].date, cfg)}
                    _append_realtime_exit_decision_tail(
                        symbol=symbol,
                        session_date=sk,
                        bar_dt=tday[-1].date,
                        cfg=cfg,
                        rt_action=rt_action_with_bar,
                    )
                    _sync_open_state_snapshot(
                        session=session,
                        open_live=open_live,
                        symbol=symbol,
                        session_date=sk,
                    )
                    _write_runtime(
                        {
                            "status": "ok",
                            "symbol": symbol,
                            "owner_id": _API_LOCAL_OWNER or None,
                            "restored_open_position": restored_boot,
                            "open_state_snapshot_path": OPEN_STATE_FILE,
                            "open_state_snapshot_saved_at": _open_state_snapshot_saved_at(),
                            "session_date": sk,
                            "last_loop_at": loop_started,
                            "bars_today": len(tday),
                            "last_action": rt_action_with_bar,
                            "intraday_safety": _intraday_safety_config(raw) if _is_stock_options_intraday_mode(raw) else None,
                            "intraday_safety_summary": _intraday_safety_runtime_summary(raw, rt_action_with_bar),
                            "dry_run": dry_run,
                            "bars_source": bars_source,
                            **rt_quote_fields,
                            **_bar_debug_fields(tday[-1].date, cfg),
                        },
                        force=True,
                    )
                    time.sleep(poll)
                    continue
                force_scan = _force_close_unrecovered_worker_positions(
                    raw=raw,
                    cfg=cfg,
                    symbol=symbol,
                    dry_run=dry_run,
                    open_live=open_live,
                )
                if force_scan is not None:
                    force_action = {**force_scan, **_action_bar_fields(tday[-1].date, cfg)}
                    _append_decision_tail(
                        symbol=symbol,
                        session_date=sk,
                        bar_dt=tday[-1].date,
                        cfg=cfg,
                        logs=[{"message": str(force_scan.get("action") or "force_close_unrecovered_positions"), "as_of": "", "extra": force_scan}],
                        action=force_action,
                    )
                    _write_runtime(
                        {
                            "status": (
                                "noop_no_new_bars"
                                if str(force_action.get("action") or "") == "force_close_unrecovered_positions_expired"
                                else "ok"
                            ),
                            "symbol": symbol,
                            "owner_id": _API_LOCAL_OWNER or None,
                            "restored_open_position": restored_boot,
                            "open_state_snapshot_path": OPEN_STATE_FILE,
                            "open_state_snapshot_saved_at": _open_state_snapshot_saved_at(),
                            "session_date": sk,
                            "last_loop_at": loop_started,
                            "bars_today": len(tday),
                            "last_action": force_action,
                            "intraday_safety": _intraday_safety_config(raw) if _is_stock_options_intraday_mode(raw) else None,
                            "intraday_safety_summary": _intraday_safety_runtime_summary(raw, force_action),
                            "dry_run": dry_run,
                            "bars_source": bars_source,
                            **rt_quote_fields,
                            **_bar_debug_fields(tday[-1].date, cfg),
                        },
                        force=True,
                    )
                    time.sleep(poll)
                    continue
                _sync_open_state_snapshot(
                    session=session,
                    open_live=open_live,
                    symbol=symbol,
                    session_date=sk,
                )
                global _LAST_NOOP_DECISION_TAIL_MONO
                now_mono = time.monotonic()
                if now_mono - _LAST_NOOP_DECISION_TAIL_MONO >= _NOOP_DECISION_TAIL_INTERVAL:
                    _LAST_NOOP_DECISION_TAIL_MONO = now_mono
                    _append_decision_tail(
                        symbol=symbol,
                        session_date=sk,
                        bar_dt=tday[-1].date,
                        cfg=cfg,
                        logs=[
                            {
                                "message": "noop_no_new_bars",
                                "as_of": "",
                                "extra": {"n_have": n_have, "bars_today": len(tday), "hint": "本圈无新K线，仅更新runtime"},
                            }
                        ],
                        action={"action": "noop_no_new_bars", **_action_bar_fields(tday[-1].date, cfg)},
                    )
                _write_runtime(
                    {
                        "status": "noop_no_new_bars",
                        "symbol": symbol,
                        "owner_id": _API_LOCAL_OWNER or None,
                        "restored_open_position": restored_boot,
                        "open_state_snapshot_path": OPEN_STATE_FILE,
                        "open_state_snapshot_saved_at": _open_state_snapshot_saved_at(),
                        "session_date": sk,
                        "last_loop_at": loop_started,
                        "bars_today": len(tday),
                        "intraday_safety": _intraday_safety_config(raw) if _is_stock_options_intraday_mode(raw) else None,
                        "intraday_safety_summary": _intraday_safety_runtime_summary(raw, {"action": "noop_no_new_bars"}),
                        "dry_run": dry_run,
                        "bars_source": bars_source,
                        **rt_quote_fields,
                        **_bar_debug_fields(tday[-1].date, cfg),
                    }
                )
        except Exception as e:
            _write_runtime(
                {
                    "status": "error",
                    "error": str(e),
                    "owner_id": _API_LOCAL_OWNER or None,
                    "restored_open_position": restored_boot,
                    "open_state_snapshot_path": OPEN_STATE_FILE,
                    "open_state_snapshot_saved_at": _open_state_snapshot_saved_at(),
                    "last_loop_at": loop_started,
                    "intraday_safety": _intraday_safety_config(raw) if _is_stock_options_intraday_mode(raw) else None,
                    "intraday_safety_summary": _intraday_safety_runtime_summary(raw, None),
                    "dry_run": dry_run,
                    "assume_bars_timezone": str(getattr(cfg, "assume_bars_timezone", None) or "UTC"),
                    "bars_source": locals().get("bars_source") or None,
                    # 异常时也尽量保留最近一次标的实时价
                    **(rt_quote_fields if "rt_quote_fields" in locals() else {}),
                },
                force=True,
            )
        time.sleep(poll)

    _remove_pid()
    try:
        if os.path.exists(STOP_FILE):
            os.remove(STOP_FILE)
    except Exception:
        pass
    _remove_runtime()


def main() -> None:
    _acquire_process_singleton_or_exit()
    path = os.getenv("QQQ_0DTE_LIVE_CONFIG") or os.getenv("QQQ_LIVE_WORKER_CONFIG") or DEFAULT_CONFIG_PATH
    try:
        run_loop(path)
    finally:
        _release_process_singleton()


if __name__ == "__main__":
    main()
