from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_AUDIT_FILE = _ROOT / "logs" / "trade_audit.jsonl"
_IDEMPOTENCY_LOCK = threading.RLock()
_RECENT_ORDER_KEYS: dict[str, float] = {}


class TradeSafetyBlocked(RuntimeError):
    """Raised when a global live-trading safety guard blocks an order."""

    def __init__(self, reason: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = dict(details or {})


@dataclass(frozen=True)
class DryRunOrderResponse:
    order_id: str
    dry_run: bool = True


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "value"):
        return str(getattr(value, "value"))
    return str(value)


def _audit_file() -> Path:
    raw = str(os.getenv("TRADE_AUDIT_LOG_FILE", "")).strip()
    return Path(raw) if raw else _DEFAULT_AUDIT_FILE


def append_trade_audit(event: str, payload: dict[str, Any]) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **payload,
    }
    try:
        path = _audit_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=_to_jsonable, sort_keys=True))
            f.write("\n")
    except Exception:
        pass


def _order_fingerprint(
    *,
    symbol: str,
    side: Any,
    submitted_quantity: int,
    order_type: Any,
    time_in_force: Any,
    submitted_price: Any = None,
) -> str:
    payload = {
        "symbol": str(symbol or "").strip().upper(),
        "side": _to_jsonable(side),
        "quantity": int(submitted_quantity),
        "order_type": _to_jsonable(order_type),
        "time_in_force": _to_jsonable(time_in_force),
        "price": _to_jsonable(submitted_price),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_to_jsonable)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _dedupe_window_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("TRADE_IDEMPOTENCY_WINDOW_SECONDS", "20")))
    except ValueError:
        return 20.0


def _assert_not_duplicate(order_key: str, now: float) -> None:
    window = _dedupe_window_seconds()
    if window <= 0:
        return
    cutoff = now - window
    with _IDEMPOTENCY_LOCK:
        stale = [k for k, ts in _RECENT_ORDER_KEYS.items() if ts < cutoff]
        for key in stale:
            _RECENT_ORDER_KEYS.pop(key, None)
        prev = _RECENT_ORDER_KEYS.get(order_key)
        if prev is not None and prev >= cutoff:
            raise TradeSafetyBlocked(
                "duplicate_order_window",
                details={"order_key": order_key, "window_seconds": window},
            )
        _RECENT_ORDER_KEYS[order_key] = now


def live_trading_kill_switch_enabled() -> bool:
    return _env_bool("TRADE_KILL_SWITCH", False) or _env_bool("LIVE_TRADING_DISABLED", False)


def live_trading_dry_run_enabled() -> bool:
    return _env_bool("TRADE_DRY_RUN", False) or _env_bool("LIVE_TRADING_DRY_RUN", False)


def guard_before_submit_order(
    *,
    symbol: str,
    side: Any,
    submitted_quantity: int,
    order_type: Any,
    time_in_force: Any,
    submitted_price: Any = None,
    source: str = "broker_service.submit_order",
) -> tuple[str, DryRunOrderResponse | None]:
    order_key = _order_fingerprint(
        symbol=symbol,
        side=side,
        submitted_quantity=int(submitted_quantity),
        order_type=order_type,
        time_in_force=time_in_force,
        submitted_price=submitted_price,
    )
    base = {
        "source": source,
        "order_key": order_key,
        "symbol": str(symbol or "").strip().upper(),
        "side": _to_jsonable(side),
        "quantity": int(submitted_quantity),
        "order_type": _to_jsonable(order_type),
        "time_in_force": _to_jsonable(time_in_force),
        "submitted_price": _to_jsonable(submitted_price),
    }
    if live_trading_kill_switch_enabled():
        append_trade_audit("order.blocked", {**base, "reason": "kill_switch"})
        raise TradeSafetyBlocked("live_trading_kill_switch_enabled", details=base)
    _assert_not_duplicate(order_key, time.monotonic())
    if live_trading_dry_run_enabled():
        order_id = f"DRYRUN-{order_key}"
        append_trade_audit("order.dry_run", {**base, "order_id": order_id})
        return order_key, DryRunOrderResponse(order_id=order_id)
    append_trade_audit("order.submit_attempt", base)
    return order_key, None


def record_submit_result(order_key: str, response: Any) -> None:
    append_trade_audit(
        "order.submit_result",
        {
            "order_key": order_key,
            "order_id": getattr(response, "order_id", None),
            "dry_run": bool(getattr(response, "dry_run", False)),
        },
    )


def record_submit_error(order_key: str, error: Exception) -> None:
    append_trade_audit(
        "order.submit_error",
        {
            "order_key": order_key,
            "error_type": type(error).__name__,
            "error": str(error),
        },
    )
