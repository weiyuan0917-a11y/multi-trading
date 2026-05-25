"""QQQ 0DTE 回测参数与结果快照：落盘 JSON，便于筛选高收益参数供实盘参考。"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

_FILE_LOCK = threading.Lock()
_MAX_RUNS = 500
_VERSION = 1


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _default_store_path() -> str:
    d = os.path.join(_repo_root(), "data", "qqq_0dte")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "backtest_snapshots.json")


def _read_raw(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {"version": _VERSION, "runs": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"version": _VERSION, "runs": []}
    if not isinstance(raw, dict):
        return {"version": _VERSION, "runs": []}
    runs = raw.get("runs")
    if not isinstance(runs, list):
        runs = []
    return {"version": int(raw.get("version") or _VERSION), "runs": runs}


def _write_raw(path: str, data: dict[str, Any]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def append_backtest_snapshot(
    *,
    request_meta: dict[str, Any],
    strategy_config: dict[str, Any],
    metrics: dict[str, Any],
    store_path: str | None = None,
) -> dict[str, Any]:
    """追加一条快照；返回写入的 run 记录（含 id）。"""
    path = store_path or _default_store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    run = {
        "id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "request": {k: v for k, v in request_meta.items() if v is not None},
        "strategy_config": dict(strategy_config) if isinstance(strategy_config, dict) else {},
        "metrics": dict(metrics) if isinstance(metrics, dict) else {},
    }
    with _FILE_LOCK:
        data = _read_raw(path)
        runs: list[Any] = list(data.get("runs") or [])
        runs.append(run)
        if len(runs) > _MAX_RUNS:
            runs = runs[-_MAX_RUNS:]
        data["version"] = _VERSION
        data["runs"] = runs
        _write_raw(path, data)
    return run


def list_snapshots(
    *,
    limit: int = 100,
    store_path: str | None = None,
) -> dict[str, Any]:
    """按时间倒序列出最近 limit 条。"""
    path = store_path or _default_store_path()
    data = _read_raw(path)
    runs = list(data.get("runs") or [])
    runs.sort(key=lambda r: str((r or {}).get("created_at") or ""), reverse=True)
    lim = max(1, min(int(limit), 500))
    return {
        "version": data.get("version", _VERSION),
        "total_stored": len(runs),
        "runs": runs[:lim],
    }


SortKey = Literal["realized_pnl", "return_pct"]


def top_snapshots(
    *,
    top_n: int = 5,
    sort: SortKey = "realized_pnl",
    store_path: str | None = None,
) -> dict[str, Any]:
    """按收益指标排序，返回前 top_n 条（全量内存排序，数据量上限 _MAX_RUNS）。"""
    path = store_path or _default_store_path()
    data = _read_raw(path)
    runs = [r for r in (data.get("runs") or []) if isinstance(r, dict)]
    n = max(1, min(int(top_n), 50))

    def sort_tuple(r: dict[str, Any]) -> tuple[Any, ...]:
        m = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
        pnl = float(m.get("realized_pnl") or 0.0)
        if sort == "return_pct":
            rp = m.get("return_pct")
            has_rp = rp is not None
            rp_f = float(rp) if has_rp else float("-inf")
            return (has_rp, rp_f, pnl)
        return (pnl,)

    runs_sorted = sorted(runs, key=sort_tuple, reverse=True)
    return {
        "sort": sort,
        "top_n": n,
        "total_stored": len(runs),
        "runs": runs_sorted[:n],
    }
