from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


_OPTION_SYMBOL_RE = re.compile(r"^(?P<underlying>[A-Z0-9]+)(?P<expiry>\d{6,8})(?P<right>[CP])(?P<strike>\d+)(?:\.US)?$")


@dataclass(frozen=True)
class OptionSymbolMeta:
    symbol: str
    underlying: str
    expiry: str
    right: str
    strike: int


def normalize_option_symbol(symbol: Any) -> str:
    s = str(symbol or "").strip().upper().replace(" ", "")
    if s and not s.endswith(".US") and _OPTION_SYMBOL_RE.match(s):
        return f"{s}.US"
    return s


def parse_option_symbol(symbol: Any) -> OptionSymbolMeta | None:
    s = normalize_option_symbol(symbol)
    m = _OPTION_SYMBOL_RE.match(s)
    if not m:
        return None
    try:
        strike = int(str(m.group("strike") or "0"))
    except Exception:
        return None
    return OptionSymbolMeta(
        symbol=s,
        underlying=str(m.group("underlying") or "").upper(),
        expiry=str(m.group("expiry") or ""),
        right="call" if str(m.group("right") or "").upper() == "C" else "put",
        strike=strike,
    )


def option_symbol_key(symbol: Any) -> str:
    meta = parse_option_symbol(symbol)
    return meta.symbol if meta else normalize_option_symbol(symbol)


def option_spread_group_key(symbol: Any) -> tuple[str, str, str] | None:
    meta = parse_option_symbol(symbol)
    if meta is None:
        return None
    return (meta.underlying, meta.expiry, meta.right)


def _quantity_from_position(row: dict[str, Any]) -> int:
    for key in ("quantity", "qty", "available_quantity", "available_qty"):
        try:
            return max(0, int(float(row.get(key) or 0) or 0))
        except Exception:
            continue
    return 0


def broker_option_qty_by_symbol(positions: Iterable[dict[str, Any]] | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in positions or []:
        if not isinstance(row, dict):
            continue
        key = option_symbol_key(row.get("symbol"))
        qty = _quantity_from_position(row)
        if key and qty > 0:
            out[key] = out.get(key, 0) + qty
    return out


def is_opening_short_options_allowed(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in ("allow_opening_short_options", "allow_naked_short_options", "allow_short_options"):
        raw = str(payload.get(key) or "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
    return False


def validate_option_sell_covered(
    *,
    legs: Iterable[Any],
    positions: Iterable[dict[str, Any]] | None,
    allow_opening_short_options: bool = False,
    allow_same_order_spread_cover: bool = True,
) -> dict[str, Any]:
    """
    Ensure sell option legs are covered by broker positions or same-order long legs.

    This is intentionally conservative for standalone sell orders: without explicit
    opt-in, a sell leg must not turn into an opening short option order.
    """
    normalized_legs: list[dict[str, Any]] = []
    for leg in legs or []:
        if isinstance(leg, dict):
            symbol = leg.get("symbol")
            side = leg.get("side")
            contracts = leg.get("contracts")
        else:
            symbol = getattr(leg, "symbol", "")
            side = getattr(leg, "side", "")
            contracts = getattr(leg, "contracts", 0)
        try:
            qty = max(0, int(float(contracts or 0) or 0))
        except Exception:
            qty = 0
        normalized_legs.append(
            {
                "symbol": option_symbol_key(symbol),
                "side": str(side or "").strip().lower(),
                "contracts": qty,
                "spread_group": option_spread_group_key(symbol),
            }
        )

    sell_legs = [x for x in normalized_legs if x["side"] == "sell" and parse_option_symbol(x["symbol"]) and x["contracts"] > 0]
    if not sell_legs or allow_opening_short_options:
        return {"ok": True, "blocked": False, "reason": None, "details": []}

    position_qty = broker_option_qty_by_symbol(positions)
    same_order_buy_by_group: dict[tuple[str, str, str], int] = {}
    if allow_same_order_spread_cover:
        for leg in normalized_legs:
            group = leg.get("spread_group")
            if leg["side"] == "buy" and group is not None and leg["contracts"] > 0:
                same_order_buy_by_group[group] = same_order_buy_by_group.get(group, 0) + int(leg["contracts"])

    blocked: list[dict[str, Any]] = []
    for leg in sell_legs:
        sym = str(leg["symbol"])
        qty = int(leg["contracts"])
        broker_qty = int(position_qty.get(sym) or 0)
        group = leg.get("spread_group")
        # Same-order long legs only cover an opening short order. If there is
        # already broker quantity in this symbol, treat the sell as close-only
        # and do not use another same-order buy to hide an over-sized sell.
        spread_cover = 0 if broker_qty > 0 else (int(same_order_buy_by_group.get(group, 0) or 0) if group is not None else 0)
        covered_qty = broker_qty + spread_cover
        if covered_qty < qty:
            blocked.append(
                {
                    "symbol": sym,
                    "requested_contracts": qty,
                    "broker_quantity": broker_qty,
                    "same_order_spread_cover": spread_cover,
                    "covered_quantity": covered_qty,
                    "missing_contracts": qty - covered_qty,
                }
            )

    if not blocked:
        return {"ok": True, "blocked": False, "reason": None, "details": []}
    return {
        "ok": False,
        "blocked": True,
        "reason": "option_sell_uncovered",
        "details": blocked,
        "allow_opening_short_options": False,
    }
