from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RuntimeState:
    # Broker contexts
    ctx_lock: threading.RLock = field(default_factory=threading.RLock)
    quote_ctx: Optional[Any] = None
    trade_ctx: Optional[Any] = None
    broker_last_error: Optional[str] = None
    broker_last_init_at: Optional[str] = None
    broker_connect_breaker_until_ts: float = 0.0
    broker_last_reset_ts: float = 0.0

    # Research runtime states
    research_tasks_lock: threading.RLock = field(default_factory=threading.RLock)
    research_tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    research_busy_lock: threading.RLock = field(default_factory=threading.RLock)
    research_busy_active: int = 0

    # Managed subprocess handles (feishu/supervisor, etc.)
    managed_processes: dict[str, Any] = field(default_factory=dict)

    # Backward-compatible aliases (to be removed after all callers migrate).
    @property
    def longport_last_error(self) -> Optional[str]:
        return self.broker_last_error

    @longport_last_error.setter
    def longport_last_error(self, value: Optional[str]) -> None:
        self.broker_last_error = value

    @property
    def longport_last_init_at(self) -> Optional[str]:
        return self.broker_last_init_at

    @longport_last_init_at.setter
    def longport_last_init_at(self, value: Optional[str]) -> None:
        self.broker_last_init_at = value

    @property
    def longport_connect_breaker_until_ts(self) -> float:
        return self.broker_connect_breaker_until_ts

    @longport_connect_breaker_until_ts.setter
    def longport_connect_breaker_until_ts(self, value: float) -> None:
        self.broker_connect_breaker_until_ts = value

    @property
    def longport_last_reset_ts(self) -> float:
        return self.broker_last_reset_ts

    @longport_last_reset_ts.setter
    def longport_last_reset_ts(self, value: float) -> None:
        self.broker_last_reset_ts = value


_RUNTIME_STATE = RuntimeState()


def get_runtime_state() -> RuntimeState:
    return _RUNTIME_STATE

