"""熔断占位：断线、拒单、异常量时可由自动交易外层调用。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CircuitBreakerState:
    halted: bool = False
    reason: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)

    def trip(self, reason: str) -> None:
        self.halted = True
        self.reason = reason
        self.events.append({"ts": datetime.utcnow().isoformat(), "action": "trip", "reason": reason})

    def reset(self) -> None:
        self.halted = False
        self.reason = ""
        self.events.append({"ts": datetime.utcnow().isoformat(), "action": "reset"})
