from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Callable

from api.services.runtime_state import RuntimeState

BROKER_CONNECT_BREAKER_SECONDS = max(
    5.0,
    float(
        os.getenv(
            "BROKER_CONNECT_BREAKER_SECONDS",
            os.getenv("LONGPORT_CONNECT_BREAKER_SECONDS", "45"),
        )
    ),
)
BROKER_RESET_MIN_INTERVAL_SECONDS = max(
    0.5,
    float(
        os.getenv(
            "BROKER_RESET_MIN_INTERVAL_SECONDS",
            os.getenv("LONGPORT_RESET_MIN_INTERVAL_SECONDS", "3"),
        )
    ),
)


def is_broker_connect_error(err: Exception | str) -> bool:
    text = str(err or "").lower()
    return any(
        key in text
        for key in (
            "openapiexception",
            "client error (connect)",
            "error sending request for url",
            "/v1/socket/token",
            "connection reset",
            "name or service not known",
            "timed out",
            "connection refused",
            "breaker_open",
        )
    )


def can_try_context_init(state: RuntimeState) -> bool:
    return time.time() >= float(state.broker_connect_breaker_until_ts or 0.0)


def mark_context_connect_success(state: RuntimeState) -> None:
    state.broker_connect_breaker_until_ts = 0.0
    state.broker_last_error = None
    state.broker_last_init_at = datetime.now().isoformat()


def mark_context_connect_failure(state: RuntimeState, err: Exception | str) -> None:
    if is_broker_connect_error(err):
        state.broker_connect_breaker_until_ts = time.time() + BROKER_CONNECT_BREAKER_SECONDS
    state.broker_last_error = str(err)


def mark_context_connect_failure_with_checker(
    state: RuntimeState,
    err: Exception | str,
    connect_error_checker: Callable[[Exception | str], bool],
) -> None:
    if connect_error_checker(err):
        state.broker_connect_breaker_until_ts = time.time() + BROKER_CONNECT_BREAKER_SECONDS
    state.broker_last_error = str(err)


def throttled_reset_contexts(reset_fn: Callable[[], None], state: RuntimeState) -> bool:
    now = time.time()
    last = float(state.broker_last_reset_ts or 0.0)
    if (now - last) < BROKER_RESET_MIN_INTERVAL_SECONDS:
        return False
    state.broker_last_reset_ts = now
    try:
        reset_fn()
        return True
    except Exception:
        return False


# Backward-compatible aliases.
is_longport_connect_error = is_broker_connect_error
