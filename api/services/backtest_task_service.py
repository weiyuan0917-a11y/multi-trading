from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

_TASKS: dict[str, dict[str, Any]] = {}
_ROOT = Path(os.getenv("MULTITRADING_ROOT") or Path(__file__).resolve().parents[2]).resolve()
_STORE_PATH = _ROOT / "data" / "backtests" / "unified_backtests.json"


def _load_store_once() -> None:
    if _TASKS:
        return
    try:
        if not _STORE_PATH.is_file():
            return
        data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
        rows = data.get("tasks") if isinstance(data, dict) else []
        if not isinstance(rows, list):
            return
        for row in rows[-500:]:
            if isinstance(row, dict) and row.get("task_id"):
                _TASKS[str(row["task_id"])] = row
    except Exception:
        return


def _save_store() -> None:
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        rows = sorted(_TASKS.values(), key=lambda x: str(x.get("created_at") or ""))[-500:]
        tmp = _STORE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"tasks": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(_STORE_PATH)
    except Exception:
        return


def normalize_backtest_result(
    *,
    task_id: str,
    kind: str,
    source_module: str,
    request: dict[str, Any],
    raw_result: dict[str, Any],
) -> dict[str, Any]:
    stats = raw_result.get("stats") if isinstance(raw_result.get("stats"), dict) else {}
    metrics = {
        "total_trades": stats.get("total_trades") or stats.get("closed_trades"),
        "closed_trades": stats.get("closed_trades"),
        "win_rate_pct": stats.get("win_rate_pct"),
        "total_net_pnl": stats.get("total_net_pnl"),
        "total_return_pct": stats.get("total_return_pct"),
        "realized_pnl": raw_result.get("realized_pnl"),
        "return_pct": raw_result.get("return_pct"),
        "total_fee": raw_result.get("total_fee") or stats.get("total_fee"),
        "bar_count": raw_result.get("bar_count"),
    }
    metrics = {k: v for k, v in metrics.items() if v is not None}
    trades = raw_result.get("trades") if isinstance(raw_result.get("trades"), list) else []
    risk = raw_result.get("risk") if isinstance(raw_result.get("risk"), dict) else {}
    equity_curve = raw_result.get("equity_curve") if isinstance(raw_result.get("equity_curve"), list) else []
    snapshot = raw_result.get("snapshot") if isinstance(raw_result.get("snapshot"), dict) else None
    params = raw_result.get("config") if isinstance(raw_result.get("config"), dict) else request
    snapshot_id = snapshot.get("id") if snapshot and snapshot.get("id") else task_id
    return {
        "schema": "unified_backtest_result.v1",
        "task_id": task_id,
        "kind": kind,
        "source_module": source_module,
        "status": "completed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "request": request,
        "metrics": metrics,
        "trades": trades,
        "risk": risk,
        "equity_curve": equity_curve,
        "params": params,
        "snapshot_id": snapshot_id,
        "snapshot": snapshot,
        "raw": raw_result,
    }


def run_sync_backtest_task(
    *,
    kind: str,
    source_module: str,
    request: dict[str, Any],
    runner: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    _load_store_once()
    task_id = f"bt_{uuid4().hex[:16]}"
    now = datetime.now(timezone.utc).isoformat()
    task = {
        "task_id": task_id,
        "kind": kind,
        "source_module": source_module,
        "status": "running",
        "created_at": now,
        "request": dict(request or {}),
        "events": [{"ts": now, "event": "created"}],
    }
    _TASKS[task_id] = task
    try:
        raw = runner(dict(request or {}))
        if not isinstance(raw, dict):
            raw = {"result": raw}
        result = normalize_backtest_result(
            task_id=task_id,
            kind=kind,
            source_module=source_module,
            request=dict(request or {}),
            raw_result=raw,
        )
        task.update({"status": "completed", "completed_at": result["completed_at"], "result": result})
        task["events"].append({"ts": result["completed_at"], "event": "completed"})
        _save_store()
        return {"ok": True, "task": task, "result": result}
    except Exception as exc:
        ts = datetime.now(timezone.utc).isoformat()
        task.update({"status": "failed", "completed_at": ts, "error": str(exc)})
        task["events"].append({"ts": ts, "event": "failed", "error": str(exc)})
        _save_store()
        raise


def get_backtest_task(task_id: str) -> dict[str, Any]:
    _load_store_once()
    return dict(_TASKS.get(str(task_id or "").strip()) or {})


def get_backtest_events(task_id: str) -> list[dict[str, Any]]:
    task = get_backtest_task(task_id)
    events = task.get("events")
    return events if isinstance(events, list) else []


def list_backtest_tasks(limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
    _load_store_once()
    lim = max(1, min(200, int(limit)))
    rows = list(_TASKS.values())
    if kind:
        rows = [x for x in rows if str(x.get("kind") or "") == str(kind)]
    rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return [dict(x) for x in rows[:lim]]
