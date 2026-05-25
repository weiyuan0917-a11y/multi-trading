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


def _resolve_api_key(raw: dict[str, Any]) -> str:
    """个人 API Key（X-Api-Key）；优先于 Bearer。"""
    env = str(os.getenv("QQQ_LIVE_API_KEY") or os.getenv("QQQ_0DTE_LIVE_API_KEY") or "").strip()
    if env:
        return env
    t = raw.get("api_key")
    if t is None:
        return ""
    return str(t).strip()


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


def _worker_account_context(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "owner_id": _API_LOCAL_OWNER or None,
        "account_id": _effective_account_id(raw) or None,
        "broker_provider": _effective_broker_provider(raw) or None,
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
    return {
        "at": str(at_iso or datetime.now(timezone.utc).isoformat()),
        "order_id": oid,
        "symbol": sym,
        "side": side,
        "contracts": qty,
        "price": px,
        "ts": ts_i,
    }


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
    if side not in {"long_call", "long_put", "strangle"}:
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
    current_account_id = _effective_account_id(raw)
    current_broker_provider = _effective_broker_provider(raw)
    for row in sorted(ledger_rows, key=lambda x: int(_coerce_timestamp_seconds(x.get("ts") or x.get("at")))):
        row_account_id = str(row.get("account_id") or "").strip()
        if current_account_id and row_account_id and row_account_id != current_account_id:
            continue
        row_broker_provider = str(row.get("broker_provider") or "").strip().lower()
        if current_broker_provider and row_broker_provider and row_broker_provider != current_broker_provider:
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


def _remove_open_state_file() -> None:
    try:
        if os.path.exists(OPEN_STATE_FILE):
            os.remove(OPEN_STATE_FILE)
    except Exception:
        pass


def _load_open_state_snapshot() -> dict[str, Any] | None:
    if not os.path.isfile(OPEN_STATE_FILE):
        return None
    try:
        with open(OPEN_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _open_state_snapshot_saved_at() -> str | None:
    snap = _load_open_state_snapshot()
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
) -> None:
    pos = session.open_position()
    payload = _open_position_to_payload(pos)
    required_symbols = _required_open_symbols(open_live, pos)
    if payload is None or not required_symbols:
        _remove_open_state_file()
        return
    parent = os.path.dirname(OPEN_STATE_FILE)
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
    tmp = OPEN_STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, OPEN_STATE_FILE)
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
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    snap = _load_open_state_snapshot()
    if not isinstance(snap, dict):
        return None, None
    if str(snap.get("instance") or _WORKER_INSTANCE).strip().lower() != _WORKER_INSTANCE:
        return None, None
    qsym = str(symbol or "").strip().upper()
    if str(snap.get("symbol") or "").strip().upper() != qsym:
        return None, None
    snap_owner = str(snap.get("owner_id") or "").strip().lower()
    if snap_owner and _API_LOCAL_OWNER and snap_owner != _API_LOCAL_OWNER:
        return None, None
    snap_account_id = str(snap.get("account_id") or "").strip()
    current_account_id = _effective_account_id(raw)
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
        _remove_open_state_file()
        return None, None
    required_symbols = _required_open_symbols(open_live, pos)
    if not required_symbols:
        _remove_open_state_file()
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
        _remove_open_state_file()
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
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    positions = _api_get_option_positions(raw)
    open_live, meta = _restore_open_state_from_snapshot(
        session=session,
        raw=raw,
        symbol=symbol,
        positions=positions,
    )
    if open_live:
        return open_live, meta
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


def _process_resolve_and_enter(
    intent: TradeIntent,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    *,
    dry_run: bool,
    resolve_defaults: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
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
    ok_o, res_o = _place_order(legs_src, raw, dry_run=dry_run)
    if not ok_o:
        return False, {"step": "options/order", "response": res_o}
    return True, {"step": "entry", "symbol": op, "order": res_o}


def _process_resolve_and_enter_strangle(
    call_it: TradeIntent,
    put_it: TradeIntent,
    raw: dict[str, Any],
    cfg: Qqq0dteConfig,
    *,
    dry_run: bool,
    resolve_defaults: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
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
    live, err = _detect_unrecovered_worker_positions(raw=raw, cfg=cfg, symbol=symbol)
    if err is not None:
        return {"action": "force_close_unrecovered_scan_failed", "ok": False, "detail": err}
    targets = list(live or [])
    if not targets:
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
    resolved = detail.get("resolved")
    if isinstance(resolved, list):
        for row in resolved:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").strip().upper()
            right = str(row.get("right") or "").strip().lower()
            if sym and right in {"call", "put"}:
                sym_right[sym] = right

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
    resolved = detail.get("resolved")
    if isinstance(resolved, list):
        for row in resolved:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").strip().upper()
            right = str(row.get("right") or "").strip().lower()
            if sym and right in {"call", "put"}:
                sym_right[sym] = right

    mode = str(order.get("mode") or "").strip().lower()
    if mode == "single_leg":
        inner = order.get("order")
        if isinstance(inner, dict):
            side = str(inner.get("side") or "").strip().lower()
            px = inner.get("price")
            sym = str(inner.get("symbol") or "").strip().upper()
            right = sym_right.get(sym)
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


def run_loop(config_path: str) -> None:
    global _API_BASE_URL, _API_BEARER_TOKEN, _API_KEY, _API_ACCOUNT_ID, _API_BROKER_PROVIDER
    raw_all = _load_worker_config(config_path)
    cfg, raw = _resolve_cfg(raw_all)
    raw = _apply_worker_account_context(raw)
    if not _API_ACCOUNT_ID and str(raw.get("account_id") or "").strip():
        _API_ACCOUNT_ID = str(raw.get("account_id") or "").strip()
    if not _API_BROKER_PROVIDER and str(raw.get("broker_provider") or "").strip():
        _API_BROKER_PROVIDER = str(raw.get("broker_provider") or "").strip().lower()
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
    _API_KEY = _resolve_api_key(raw)

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
                _API_KEY = _resolve_api_key(_fresh)
                configured_account_id = str(_fresh.get("account_id") or "").strip()
                if _API_ACCOUNT_ID and configured_account_id and configured_account_id != _API_ACCOUNT_ID:
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
                                    "configured_account_id": configured_account_id,
                                    "worker_account_id": _API_ACCOUNT_ID,
                                    **_worker_account_context(raw),
                                },
                            }
                        ],
                        action={
                            "action": "config_account_switch_ignored",
                            "configured_account_id": configured_account_id,
                            "worker_account_id": _API_ACCOUNT_ID,
                            **_worker_account_context(raw),
                            **_action_bar_fields(boot_dt, cfg),
                        },
                    )

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
                session.set_option_live_quotes(None, None)
            elif (
                open_live
                and isinstance(open_live, dict)
                and str(open_live.get("mode") or "") != "strangle"
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
            else:
                session.set_strangle_live_quotes(None, None, None, None)
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
                    if side == "strangle":
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
                        if side == "strangle":
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
                    if entry_guard is not None:
                        if not had_pos_before:
                            session.clear_open_position()
                        act = {
                            "action": "skip_entry_unrecovered_open_position",
                            **entry_guard,
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
                            ]
                            + [{"message": "skip_entry_unrecovered_open_position", "as_of": "", "extra": entry_guard}],
                            action=act_j,
                        )
                        continue
                    intents = res.intents
                    if (
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
