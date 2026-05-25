"""意图、持仓、决策日志条目。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal


Side = Literal["long_call", "long_put", "strangle"]


class TradeIntentKind(str, Enum):
    NONE = "none"
    BUY_CALL = "buy_call"
    BUY_PUT = "buy_put"


@dataclass
class TradeIntent:
    kind: TradeIntentKind
    underlying: str
    strike: float
    right: Literal["call", "put"]
    contracts: int
    reason: str


@dataclass
class OpenPosition:
    side: Side
    strike: float
    entry_bar_index: int
    entry_time: datetime
    entry_px: float
    contracts: int
    call_strike: float = 0.0
    put_strike: float = 0.0
    call_entry_px: float = 0.0
    put_entry_px: float = 0.0
    # 早盘宽跨：单腿平仓后仍用 side=strangle，用下列标记剩余腿（用于组合止盈/强平与行情订阅）。
    strangle_call_active: bool = True
    strangle_put_active: bool = True
    # Original two-leg debit and realized sell proceeds, per share. Kept so combo exits
    # can keep using whole-strangle R after one leg has been closed.
    strangle_original_entry_px: float = 0.0
    strangle_realized_exit_px: float = 0.0
    # Morning strangle entry OTM steps. Used only to choose the take-profit
    # threshold for each individual leg; combo exits and stop losses are unchanged.
    call_strikes_otm: int = 0
    put_strikes_otm: int = 0


@dataclass
class DecisionLogEntry:
    bar_index: int
    as_of: str
    message: str
    extra: dict[str, Any] = field(default_factory=dict)
