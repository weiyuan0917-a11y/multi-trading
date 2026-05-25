"""
QQQ 0DTE 四变体「系统推荐」：仅基于行情快照与 K 线统计的启发式规则，供展示参考，不参与下单逻辑。
与实盘 Worker 解耦，由 Worker 定时调用并写入 JSON；阈值与策略表单默认量级大致对齐。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Sequence

from backtest_engine import Bar

from .config import Qqq0dteConfig
from .session_us import ny_date

# 与默认 strangle_range_pct≈0.3%、directional≈1% 同量级
_STRANGLE_CHG_MAX_PCT = 0.35
_DIRECTIONAL_CHG_MIN_PCT = 0.85
_VOL_SPIKE_RATIO = 1.55
_VIX_CHG_BOOST_PCT = 0.28

_VARIANT_ZH = {
    "reaction_zone": "反应区 + 成交量确认",
    "morning_strangle": "早盘宽跨",
    "morning_directional": "早盘方向单",
    "gamma_scalping": "Gamma 剥头皮",
    "unspecified": "暂无明确倾向",
}

_DISCLAIMER = (
    "本推荐由实盘 Worker 根据 QQQ 现价、相对前收涨跌、前日高低突破倾向、日内量能相对近几日均值、"
    "VIX 涨跌等快照自动生成，每 10 分钟更新一次；仅供参考，不构成投资建议，且不触发任何实盘下单。"
)


def _bars_by_session_date(bars: Sequence[Bar], tz: str) -> dict[date, list[Bar]]:
    by_d: dict[date, list[Bar]] = defaultdict(list)
    for b in bars:
        by_d[ny_date(b.date, tz)].append(b)
    return dict(by_d)


def prior_trading_day_high_low(bars: Sequence[Bar], today_d: date, tz: str) -> tuple[float | None, float | None]:
    """严格早于 today_d 的最近一个交易日的日内高/低。"""
    by_d = _bars_by_session_date(bars, tz)
    ds = sorted(by_d.keys())
    prev_days = [d for d in ds if d < today_d]
    if not prev_days:
        return None, None
    prev = prev_days[-1]
    bs = by_d[prev]
    hi = max(float(x.high) for x in bs)
    lo = min(float(x.low) for x in bs)
    return hi, lo


def intraday_volume_ratio_vs_recent_days(
    bars: Sequence[Bar], today_d: date, tz: str, lookback_days: int = 5
) -> float | None:
    """当日已成交 K 线量之和 / 近 lookback_days 个完整交易日的日均总成交量。"""
    by_d = _bars_by_session_date(bars, tz)
    ds = sorted(by_d.keys())
    past = [d for d in ds if d < today_d]
    if not past:
        return None
    daily_vols: list[float] = []
    for d in past[-lookback_days:]:
        daily_vols.append(sum(float(b.volume) for b in by_d[d]))
    if not daily_vols:
        return None
    avg = sum(daily_vols) / max(len(daily_vols), 1)
    if avg <= 0:
        return None
    tbs = by_d.get(today_d)
    if not tbs:
        return None
    today_vol = sum(float(b.volume) for b in tbs)
    return today_vol / max(avg, 1e-12)


def compute_strategy_recommendation(
    *,
    symbol: str,
    cfg: Qqq0dteConfig,
    bars: list[Bar],
    today_d: date,
    rt_fields: dict[str, Any],
    vix_change_pct: float,
    scan_interval_seconds: int = 600,
) -> dict[str, Any]:
    rq = rt_fields.get("realtime_quote")
    if not isinstance(rq, dict):
        rq = {}
    spot: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None
    try:
        if rq.get("last") is not None:
            spot = float(rq["last"])
    except Exception:
        spot = None
    try:
        if rq.get("prev_close") is not None:
            prev_close = float(rq["prev_close"])
    except Exception:
        prev_close = None
    try:
        if rq.get("change_pct") is not None:
            change_pct = float(rq["change_pct"])
    except Exception:
        change_pct = None

    prev_high, prev_low = prior_trading_day_high_low(bars, today_d, cfg.assume_bars_timezone)
    vol_ratio = intraday_volume_ratio_vs_recent_days(bars, today_d, cfg.assume_bars_timezone)

    reasons: list[str] = []
    scores: dict[str, float] = {
        "reaction_zone": 0.0,
        "morning_strangle": 0.0,
        "morning_directional": 0.0,
        "gamma_scalping": 0.0,
    }

    abs_chg = abs(change_pct) if change_pct is not None else None

    if abs_chg is not None:
        reasons.append(f"{symbol} 相对前收涨跌约 {change_pct:+.3f}%（快照）。")
        if abs_chg >= _DIRECTIONAL_CHG_MIN_PCT:
            scores["morning_directional"] += 3.0
            reasons.append(
                f"涨跌幅绝对值 ≥ {_DIRECTIONAL_CHG_MIN_PCT}% ，与「早盘方向单」阈值风格接近，方向性较强。"
            )
        elif abs_chg <= _STRANGLE_CHG_MAX_PCT:
            scores["morning_strangle"] += 3.0
            reasons.append(
                f"涨跌幅绝对值 ≤ {_STRANGLE_CHG_MAX_PCT}% ，接近「早盘宽跨」窄幅震荡假设。"
            )
        elif _STRANGLE_CHG_MAX_PCT < abs_chg < _DIRECTIONAL_CHG_MIN_PCT:
            scores["reaction_zone"] += 1.5
            reasons.append("涨跌幅处于中等区间，可结合关键位与放量用「反应区」思路观察。")

    breakout_call = False
    breakout_put = False
    if spot is not None and prev_high is not None and prev_high > 0:
        if spot >= prev_high:
            breakout_call = True
            scores["gamma_scalping"] += 2.5
            reasons.append(f"现价 {spot:.4g} ≥ 前交易日高 {prev_high:.4g}，偏向 Gamma 站上沿突破（买 Call 方向）。")
    if spot is not None and prev_low is not None and prev_low > 0:
        if spot <= prev_low:
            breakout_put = True
            scores["gamma_scalping"] += 2.5
            reasons.append(f"现价 {spot:.4g} ≤ 前交易日低 {prev_low:.4g}，偏向 Gamma 跌破下沿（买 Put 方向）。")

    if vol_ratio is not None:
        reasons.append(f"今日累计成交量 / 近几日日均量 ≈ {vol_ratio:.2f}×。")
        if vol_ratio >= _VOL_SPIKE_RATIO:
            scores["reaction_zone"] += 2.0
            reasons.append(f"量能相对近期偏高（≥{_VOL_SPIKE_RATIO}×），利于「反应区 + 成交量确认」。")

    if vix_change_pct >= _VIX_CHG_BOOST_PCT:
        scores["gamma_scalping"] += 1.0
        reasons.append(f"VIX 相对前收约 +{vix_change_pct:.3f}%，略提振短线波动/Gamma 场景权重。")
    elif vix_change_pct <= -_VIX_CHG_BOOST_PCT:
        reasons.append(f"VIX 走弱约 {vix_change_pct:.3f}%，短线波动预期偏低（仅供参考）。")

    # 四策略同分且均为 0：不强行指定某一变体；有正分时同分按方向性 > Gamma > 宽跨 > 反应区
    _tiebreak = ("morning_directional", "gamma_scalping", "morning_strangle", "reaction_zone")
    best = max(_tiebreak, key=lambda k: scores[k])
    if scores[best] <= 0:
        best = "unspecified"
        reasons.append("当前快照未触发任一策略维度的加分规则，四策略启发式得分均为 0，显示为「暂无明确倾向」（仅供参考）。")

    features: dict[str, Any] = {
        "symbol": symbol,
        "spot": spot,
        "prev_close": prev_close,
        "change_pct_from_prev_close": change_pct,
        "prev_session_high": prev_high,
        "prev_session_low": prev_low,
        "breakout_vs_prev_high": breakout_call,
        "breakout_vs_prev_low": breakout_put,
        "volume_ratio_today_vs_recent_days": vol_ratio,
        "vix_change_pct_snapshot": vix_change_pct,
        "assume_bars_timezone": cfg.assume_bars_timezone,
        "session_date_ny": today_d.isoformat(),
    }

    return {
        "ok": True,
        "recommended_variant": best,
        "recommended_name_zh": _VARIANT_ZH.get(best, _VARIANT_ZH["unspecified"]),
        "scores": scores,
        "reasons": reasons,
        "features": features,
        "disclaimer": _DISCLAIMER,
        "scan_interval_seconds": scan_interval_seconds,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
