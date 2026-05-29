from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


REMOVED_REASON = "auto_trading_removed"


def _removed(**extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "disabled": True,
        "reason": REMOVED_REASON,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    out.update(extra)
    return out


def get_research_status() -> dict[str, Any]:
    return _removed(running=False, tasks={})


def get_research_snapshot() -> dict[str, Any]:
    return _removed(snapshot=None)


def run_research_snapshot(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _removed(snapshot=None)


def list_research_snapshot_history(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return []


def get_research_snapshot_history_result(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _removed(snapshot=None)


def get_model_compare(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _removed(items=[])


def run_strategy_param_matrix(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _removed(items=[], rows=[])


def get_strategy_param_matrix_result(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _removed(items=[], rows=[])


def run_ml_param_matrix(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _removed(items=[], rows=[])


def get_ml_param_matrix_result(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _removed(items=[], rows=[])


def resolve_ml_matrix_row_for_apply(raw: dict[str, Any] | None, variant: str | None = None) -> tuple[dict[str, Any], str]:
    return {}, "removed"


def ml_matrix_row_to_auto_trader_patch(row: dict[str, Any] | None) -> dict[str, Any]:
    return {"enabled": False}


def get_factor_ab_report(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _removed(items=[])


def get_factor_ab_report_markdown(*args: Any, **kwargs: Any) -> str:
    return "Auto trading research has been removed."
