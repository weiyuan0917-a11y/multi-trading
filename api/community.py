from __future__ import annotations

import math
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ExecutionIntent:
    symbol: str
    asset_class: str
    side: str
    quantity: float
    reference_price: float
    strategy: str
    created_at: str
    execution_mode: str = "paper"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class PaperBroker:
    """An in-memory simulator. It intentionally has no broker adapter API."""

    def __init__(self, starting_cash: float = 100_000.0) -> None:
        self._lock = threading.RLock()
        self._cash = float(starting_cash)
        self._positions: dict[str, dict[str, float]] = {}
        self._orders: list[dict[str, object]] = []

    def submit(self, intent: ExecutionIntent) -> dict[str, object]:
        if intent.execution_mode not in {"paper", "rehearsal"}:
            raise ValueError("Community execution mode must be paper or rehearsal")
        if intent.quantity <= 0 or intent.reference_price <= 0:
            raise ValueError("quantity and reference_price must be positive")
        if intent.side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")

        with self._lock:
            order_id = f"PAPER-{uuid4().hex[:12].upper()}"
            notional = round(intent.quantity * intent.reference_price, 2)
            signed_quantity = intent.quantity if intent.side == "buy" else -intent.quantity
            position = self._positions.setdefault(
                intent.symbol.upper(), {"quantity": 0.0, "average_cost": 0.0}
            )
            if intent.side == "buy":
                if notional > self._cash:
                    raise ValueError("insufficient paper cash")
                old_value = position["quantity"] * position["average_cost"]
                position["quantity"] += intent.quantity
                position["average_cost"] = round(
                    (old_value + notional) / position["quantity"], 6
                )
                self._cash -= notional
            else:
                position["quantity"] += signed_quantity
                self._cash += notional
                if math.isclose(position["quantity"], 0.0, abs_tol=1e-9):
                    position["quantity"] = 0.0
                    position["average_cost"] = 0.0
            record: dict[str, object] = {
                "order_id": order_id,
                "status": "simulated_fill",
                "fill_price": intent.reference_price,
                "notional": notional,
                "created_at": utc_now(),
                "intent": intent.to_dict(),
            }
            self._orders.append(record)
            return record

    def account(self) -> dict[str, object]:
        with self._lock:
            return {
                "mode": "paper",
                "cash": round(self._cash, 2),
                "positions": [
                    {"symbol": symbol, **value}
                    for symbol, value in sorted(self._positions.items())
                    if not math.isclose(value["quantity"], 0.0, abs_tol=1e-9)
                ],
                "orders": list(reversed(self._orders[-100:])),
            }


PAPER_BROKER = PaperBroker()


def quote_snapshot(symbol: str) -> dict[str, object]:
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("symbol is required")
    # Deterministic fallback gives research and tests a usable offline response.
    seed = sum(ord(char) for char in normalized)
    last = round(20 + (seed % 800) + ((seed % 37) / 100), 2)
    return {
        "symbol": normalized,
        "last": last,
        "currency": "USD",
        "source": "community_synthetic_quote",
        "delayed": True,
        "as_of": utc_now(),
    }


def option_research(symbol: str, spot: float | None = None) -> dict[str, object]:
    quote = quote_snapshot(symbol)
    underlying = float(spot or quote["last"])
    strike = round(underlying / 5) * 5
    return {
        "symbol": quote["symbol"],
        "underlying_price": underlying,
        "source": "community_synthetic_option_research",
        "chain": [
            {
                "contract_type": contract_type,
                "strike": strike,
                "expiry_days": 30,
                "implied_volatility": 0.30,
                "delta": 0.52 if contract_type == "call" else -0.48,
                "gamma": 0.03,
                "theta": -0.04,
                "vega": 0.11,
            }
            for contract_type in ("call", "put")
        ],
        "disclaimer": "Research estimate only; not a tradable quote.",
    }


def simple_backtest(prices: list[float], starting_cash: float = 100_000.0) -> dict[str, object]:
    if len(prices) < 3 or any(price <= 0 for price in prices):
        raise ValueError("at least three positive prices are required")
    cash = float(starting_cash)
    units = 0.0
    trades = 0
    for index in range(1, len(prices)):
        previous, current = float(prices[index - 1]), float(prices[index])
        if current > previous and units == 0:
            units = cash / current
            cash = 0.0
            trades += 1
        elif current < previous and units > 0:
            cash = units * current
            units = 0.0
            trades += 1
    equity = cash + units * float(prices[-1])
    return {
        "mode": "research_backtest",
        "starting_cash": starting_cash,
        "ending_equity": round(equity, 2),
        "return_pct": round((equity / starting_cash - 1) * 100, 4),
        "trades": trades,
        "assumptions": ["no slippage", "no taxes", "not investment advice"],
    }
