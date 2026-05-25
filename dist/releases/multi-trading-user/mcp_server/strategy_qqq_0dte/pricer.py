"""单腿 0DTE 合成期权价（BS + 滚动 HV）。"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from backtest_engine import Bar

from synthetic_option_pricing import black_scholes_european, periods_per_year_for_kline, rolling_sigma_annualized

from .config import Qqq0dteConfig
from .session_us import option_expiry_datetime, to_ny


def synthetic_option_price_at_bar(
    bars: Sequence[Bar],
    index: int,
    *,
    strike: float,
    right: str,
    session_date,
    cfg: Qqq0dteConfig,
    tz_name: str,
) -> float:
    closes = [float(b.close) for b in bars]
    spot = closes[index]
    exp = option_expiry_datetime(session_date, cfg)
    asof = to_ny(bars[index].date, tz_name)
    t_sec = max(0.0, (exp - asof).total_seconds())
    t_years = t_sec / (365.0 * 24 * 3600)
    ppy = periods_per_year_for_kline("1m")
    sig = rolling_sigma_annualized(
        closes,
        index,
        vol_window=int(cfg.vol_window_bars),
        periods_per_year=ppy,
        min_sigma=float(cfg.min_sigma),
    )
    return black_scholes_european(
        spot,
        float(strike),
        t_years,
        float(cfg.risk_free_rate),
        float(cfg.dividend_yield),
        sig,
        "C" if str(right).lower() in ("call", "c") else "P",
    )
