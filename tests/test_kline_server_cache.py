from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from api import main as main_mod


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_cache(path: Path, start: datetime, count: int) -> None:
    items = []
    for i in range(count):
        dt = start + timedelta(days=i)
        items.append({"date": dt.isoformat(), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1})
    _write_json(path, {"items": items})


def test_server_kline_cache_accepts_one_day_end_skew_when_long_window_is_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "data" / "klines"
    monkeypatch.setattr(main_mod, "KLINE_SERVER_CACHE_DIR", str(cache_dir))
    today = datetime.now(timezone.utc).date()

    long_start = datetime.combine(today - timedelta(days=181), datetime.min.time(), tzinfo=timezone.utc)
    shorter_start = datetime.combine(today - timedelta(days=120), datetime.min.time(), tzinfo=timezone.utc)
    _write_cache(cache_dir / "QQQ_US__1m__d180.json", long_start, 180)
    _write_cache(cache_dir / "QQQ_US__1m__d120.json", shorter_start, 120)

    bars = main_mod._load_bars_from_server_kline_cache("QQQ.US", "1m", 0, 180)

    assert len(bars) == 180
    assert bars[-1].date.date() == today - timedelta(days=2)


def test_server_kline_cache_rejects_stale_exact_when_newer_shorter_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "data" / "klines"
    monkeypatch.setattr(main_mod, "KLINE_SERVER_CACHE_DIR", str(cache_dir))
    today = datetime.now(timezone.utc).date()

    stale_start = datetime.combine(today - timedelta(days=260), datetime.min.time(), tzinfo=timezone.utc)
    fresh_start = datetime.combine(today - timedelta(days=120), datetime.min.time(), tzinfo=timezone.utc)
    _write_cache(cache_dir / "QQQ_US__1m__d180.json", stale_start, 180)
    _write_cache(cache_dir / "QQQ_US__1m__d120.json", fresh_start, 120)

    with pytest.raises(HTTPException) as exc:
        main_mod._load_bars_from_server_kline_cache("QQQ.US", "1m", 0, 180)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "kline_server_cache_incomplete"
