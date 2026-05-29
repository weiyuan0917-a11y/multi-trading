"""
标的 K 线 + 历史波动率 + Black-Scholes（欧式，连续分红 q）合成单腿期权理论价路径。

用途：LongPort 不提供美股期权历史 K 线时，用标的（如 QQQ.US）序列近似期权价格轨迹用于回测。
说明：理论价 ≠ 实盘成交价；未建模 IV 曲面、买卖价差与流动性。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Sequence

try:
    from .backtest_engine import Bar
except ImportError:
    from backtest_engine import Bar

Right = Literal["C", "P", "call", "put"]


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def year_fraction_calendar(start: datetime, end: datetime) -> float:
    """按日历时间折算为年（365 日），用于 0DTE / 短到期近似。"""
    if end <= start:
        return 0.0
    delta = end - start
    return max(0.0, delta.total_seconds() / (365.0 * 24 * 3600))


def periods_per_year_for_kline(kline: str) -> float:
    """
    将各类 K 线频率映射为年化波动率缩放用的「每年周期数」。
    美股常规时段按约 6.5 小时/日、252 交易日估算分钟类周期。
    """
    k = str(kline or "1d").strip().lower()
    minutes_per_trading_day = 390.0
    days = 252.0
    table = {
        "1m": days * minutes_per_trading_day,
        "5m": days * (minutes_per_trading_day / 5.0),
        "10m": days * (minutes_per_trading_day / 10.0),
        "30m": days * (minutes_per_trading_day / 30.0),
        "1h": days * (minutes_per_trading_day / 60.0),
        "2h": days * (minutes_per_trading_day / 120.0),
        "4h": days * (minutes_per_trading_day / 240.0),
        "1d": days,
    }
    return float(table.get(k, days))


def black_scholes_european(
    spot: float,
    strike: float,
    time_years: float,
    rate: float,
    div_yield: float,
    sigma: float,
    right: Right,
) -> float:
    """
    Black-Scholes 欧式期权（标的支付连续收益率 q）。
    spot/strike 与期权报价同单位；time_years 为年化剩余期限；sigma 为年化波动率。
    """
    r = float(rate)
    q = float(div_yield)
    sig = max(float(sigma), 1e-8)
    # 防御脏数据：strike<=0 会在 log(s/k) 触发 division by zero。
    # 对外保持容错，避免实盘 worker 在单次异常参数上整轮失败。
    k = max(abs(float(strike)), 1e-12)
    s = max(float(spot), 1e-12)
    cp = str(right).upper()
    is_call = cp in ("C", "CALL")

    if time_years <= 1e-10:
        if is_call:
            return max(0.0, s - k)
        return max(0.0, k - s)

    sqrt_t = math.sqrt(time_years)
    d1 = (math.log(s / k) + (r - q + 0.5 * sig * sig) * time_years) / (sig * sqrt_t)
    d2 = d1 - sig * sqrt_t
    disc_s = s * math.exp(-q * time_years)
    disc_k = k * math.exp(-r * time_years)
    if is_call:
        return disc_s * _norm_cdf(d1) - disc_k * _norm_cdf(d2)
    return disc_k * _norm_cdf(-d2) - disc_s * _norm_cdf(-d1)


def rolling_sigma_annualized(
    closes: Sequence[float],
    index: int,
    *,
    vol_window: int,
    periods_per_year: float,
    min_sigma: float = 0.01,
) -> float:
    """
    仅用 index 及之前的收盘价估计年化 σ，避免前视。
    使用最近 vol_window 个对数收益（需要至少 2 个价格点产生 1 个收益；多收益更稳）。
    """
    if index < 1 or vol_window < 2:
        return max(float(min_sigma), 1e-8)
    start = max(1, index - vol_window + 1)
    rets: list[float] = []
    for j in range(start, index + 1):
        c0 = float(closes[j - 1])
        c1 = float(closes[j])
        if c0 <= 0 or c1 <= 0:
            continue
        rets.append(math.log(c1 / c0))
    if len(rets) < 2:
        return max(float(min_sigma), 1e-8)
    mean = sum(rets) / max(len(rets), 1)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    vol_bar = math.sqrt(max(var, 1e-16))
    sigma = vol_bar * math.sqrt(float(periods_per_year))
    return max(sigma, float(min_sigma))


@dataclass
class SyntheticOptionBar:
    """单根 K 上合成的期权理论价与中间变量。"""

    bar_index: int
    as_of: datetime
    spot: float
    strike: float
    time_years: float
    sigma: float
    theoretical: float
    intrinsic: float


def build_synthetic_option_path(
    bars: Sequence[Bar],
    *,
    strike: float,
    expiry: datetime,
    right: Right = "C",
    rate: float = 0.052,
    div_yield: float = 0.0,
    vol_window: int = 20,
    periods_per_year: float | None = None,
    kline: str | None = None,
    min_sigma: float = 0.01,
    spot_source: Literal["close", "open"] = "close",
) -> list[SyntheticOptionBar]:
    """
    对每根标的 K 线计算欧式期权理论价（无 lookahead 的滚动历史波动率）。

    :param bars: 标的 Bar 序列（时间升序）
    :param strike: 行权价
    :param expiry: 到期时刻（naive 或 UTC 均可，与 bar.date 一致即可）
    :param vol_window: 滚动窗口（按「根数」，对数收益条数约 window-1）
    :param periods_per_year: 若为空则根据 kline 推断；日 K 默认 252
    """
    if not bars:
        return []
    ppy = float(periods_per_year) if periods_per_year is not None else periods_per_year_for_kline(kline or "1d")
    exp = expiry.replace(tzinfo=None) if getattr(expiry, "tzinfo", None) else expiry
    closes = [float(getattr(b, spot_source)) for b in bars]
    out: list[SyntheticOptionBar] = []

    for i, b in enumerate(bars):
        ts = b.date.replace(tzinfo=None) if getattr(b.date, "tzinfo", None) else b.date
        s = float(closes[i])
        t = year_fraction_calendar(ts, exp)
        sig = rolling_sigma_annualized(closes, i, vol_window=vol_window, periods_per_year=ppy, min_sigma=min_sigma)
        theo = black_scholes_european(s, float(strike), t, rate, div_yield, sig, right)
        k = float(strike)
        cp = str(right).upper()
        is_call = cp in ("C", "CALL")
        intrinsic = max(0.0, s - k) if is_call else max(0.0, k - s)
        out.append(
            SyntheticOptionBar(
                bar_index=i,
                as_of=ts,
                spot=s,
                strike=k,
                time_years=t,
                sigma=sig,
                theoretical=theo,
                intrinsic=intrinsic,
            )
        )
    return out


def synthetic_path_to_dict_rows(rows: Sequence[SyntheticOptionBar]) -> list[dict[str, Any]]:
    """便于 JSON / API 返回。"""
    return [
        {
            "bar_index": r.bar_index,
            "as_of": r.as_of.isoformat(),
            "spot": round(r.spot, 6),
            "strike": round(r.strike, 6),
            "time_years": round(r.time_years, 8),
            "sigma": round(r.sigma, 6),
            "theoretical": round(r.theoretical, 6),
            "intrinsic": round(r.intrinsic, 6),
        }
        for r in rows
    ]


def synthetic_vertical_spread_path(
    bars: Sequence[Bar],
    *,
    long_strike: float,
    short_strike: float,
    expiry: datetime,
    right: Right = "C",
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """
    垂直价差（买低卖高 Call 或买高卖低 Put）：两腿理论价差，每份价差按「每股」计。
    kwargs 传入 build_synthetic_option_path 的其余参数。
    """
    low = min(long_strike, short_strike)
    high = max(long_strike, short_strike)
    cp = str(right).upper()
    is_call = cp in ("C", "CALL")
    long_k = low if is_call else high
    short_k = high if is_call else low

    path_long = build_synthetic_option_path(bars, strike=long_k, expiry=expiry, right=right, **kwargs)
    path_short = build_synthetic_option_path(bars, strike=short_k, expiry=expiry, right=right, **kwargs)
    rows: list[dict[str, Any]] = []
    for a, b in zip(path_long, path_short):
        net = a.theoretical - b.theoretical
        rows.append(
            {
                "bar_index": a.bar_index,
                "as_of": a.as_of.isoformat(),
                "spot": round(a.spot, 6),
                "expiry": expiry.replace(tzinfo=None).isoformat() if expiry else "",
                "long_strike": long_k,
                "short_strike": short_k,
                "theoretical_spread_per_share": round(net, 6),
                "sigma_long": round(a.sigma, 6),
                "sigma_short": round(b.sigma, 6),
            }
        )
    return rows
