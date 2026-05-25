"""前一日 H/L/C、盘前高/低、心理整数位、简易小时区间。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from backtest_engine import Bar

from .config import Qqq0dteConfig
from .session_us import rth_bounds, to_ny


@dataclass
class KeyLevels:
    session_date: date
    prev_high: float | None = None
    prev_low: float | None = None
    prev_close: float | None = None
    premkt_high: float | None = None
    premkt_low: float | None = None
    psychological: list[float] = field(default_factory=list)
    hourly_highs: list[float] = field(default_factory=list)
    hourly_lows: list[float] = field(default_factory=list)


def prior_trading_date_with_data(all_dates_sorted: list[date], current: date) -> date | None:
    idx = None
    for i, d in enumerate(all_dates_sorted):
        if d == current:
            idx = i
            break
    if idx is None or idx == 0:
        return None
    return all_dates_sorted[idx - 1]


def build_key_levels(
    *,
    session_date: date,
    prior_day_bars: Sequence[Bar],
    today_bars_before_now: Sequence[Bar],
    cfg: Qqq0dteConfig,
    tz_name: str,
    spot_for_psych: float,
) -> KeyLevels:
    kl = KeyLevels(session_date=session_date)
    if prior_day_bars:
        kl.prev_high = max(float(b.high) for b in prior_day_bars)
        kl.prev_low = min(float(b.low) for b in prior_day_bars)
        kl.prev_close = float(prior_day_bars[-1].close)

    open_t, _ = rth_bounds(cfg, session_date)
    premkt: list[Bar] = []
    for b in today_bars_before_now:
        ny = to_ny(b.date, tz_name)
        if ny.date() != session_date:
            continue
        if ny < open_t:
            premkt.append(b)
    if premkt:
        kl.premkt_high = max(float(b.high) for b in premkt)
        kl.premkt_low = min(float(b.low) for b in premkt)

    step = float(cfg.psychological_step)
    if step > 0 and spot_for_psych > 0:
        denom_step = max(step, 1e-12)
        base = round(spot_for_psych / denom_step) * step
        n = max(1, int(cfg.psychological_levels_max) // 2)
        for k in range(-n, n + 1):
            kl.psychological.append(round(base + k * step, 4))

    # 简易「小时」：当日已交易时段按 60 根 1m 为一块（若不足则忽略）
    rth_bars: list[Bar] = []
    for b in today_bars_before_now:
        ny = to_ny(b.date, tz_name)
        if ny.date() != session_date:
            continue
        if ny >= open_t:
            rth_bars.append(b)
    rth_bars.sort(key=lambda x: x.date)
    chunk = 60
    for i in range(0, len(rth_bars), chunk):
        block = rth_bars[i : i + chunk]
        if len(block) < 5:
            continue
        kl.hourly_highs.append(max(float(x.high) for x in block))
        kl.hourly_lows.append(min(float(x.low) for x in block))

    return kl


def collect_level_prices(kl: KeyLevels) -> list[float]:
    xs: list[float] = []
    for v in (kl.prev_high, kl.prev_low, kl.prev_close, kl.premkt_high, kl.premkt_low):
        if v is not None and v > 0:
            xs.append(float(v))
    xs.extend(kl.psychological)
    xs.extend(kl.hourly_highs)
    xs.extend(kl.hourly_lows)
    return sorted(set(round(x, 4) for x in xs))
