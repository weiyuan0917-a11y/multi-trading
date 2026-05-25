"""
QQQ 0DTE system recommendation.

This module is deliberately read-only: it scores the current market snapshot for
UI display and Agent Strategy Lab context. It does not write live config, start a
worker, or send orders.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timezone
from typing import Any, Sequence

from backtest_engine import Bar

from .config import Qqq0dteConfig
from .session_us import get_zone, ny_date, to_ny

_DEFAULT_STRANGLE_CHG_MAX_PCT = 0.35
_DEFAULT_DIRECTIONAL_CHG_MIN_PCT = 0.85
_DEFAULT_VOL_SPIKE_RATIO = 1.55
_VIX_CHG_BOOST_PCT = 0.28

_VARIANT_ZH = {
    "reaction_zone": "反应区 + 成交量确认",
    "morning_strangle": "早盘宽跨",
    "morning_double_strangle": "早盘双宽跨",
    "morning_directional": "早盘方向单",
    "gamma_scalping": "Gamma 剥头皮",
    "gamma_pro": "Gamma Pro",
    "unspecified": "暂无明确倾向",
}

_DISCLAIMER = (
    "本推荐由实盘 Worker 根据 QQQ 现价、相对前收涨跌、前日高低突破倾向、同时间段量能比、"
    "VIX 涨跌和当前配置阈值等快照自动生成，每 10 分钟更新一次；仅供参考，不构成投资建议，且不触发任何实盘下单。"
)


def _bars_by_session_date(bars: Sequence[Bar], tz: str) -> dict[date, list[Bar]]:
    by_d: dict[date, list[Bar]] = defaultdict(list)
    for b in bars:
        by_d[ny_date(b.date, tz)].append(b)
    for xs in by_d.values():
        xs.sort(key=lambda x: x.date)
    return dict(by_d)


def _to_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        out = float(v)
        return out if out == out else None
    except Exception:
        return None


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.3f}%"


def _hhmm_minutes(hhmm: str, fallback: str) -> int:
    s = str(hhmm or fallback).strip() or fallback
    try:
        h_s, m_s = s.split(":", 1)
        h = min(23, max(0, int(h_s)))
        m = min(59, max(0, int(m_s)))
        return h * 60 + m
    except Exception:
        h_s, m_s = fallback.split(":", 1)
        return int(h_s) * 60 + int(m_s)


def _ny_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(get_zone("America/New_York"))


def _window_state(now_ny: datetime, start_hhmm: str, end_hhmm: str) -> dict[str, Any]:
    now_m = now_ny.hour * 60 + now_ny.minute
    start_m = _hhmm_minutes(start_hhmm, "09:30")
    end_m = _hhmm_minutes(end_hhmm, "16:00")
    if now_m < start_m:
        state = "before"
    elif now_m <= end_m:
        state = "open"
    else:
        state = "after"
    return {
        "state": state,
        "start": f"{start_m // 60:02d}:{start_m % 60:02d}",
        "end": f"{end_m // 60:02d}:{end_m % 60:02d}",
        "now": f"{now_m // 60:02d}:{now_m % 60:02d}",
        "active": state == "open",
    }


def _build_time_windows(cfg: Qqq0dteConfig, now_ny: datetime) -> dict[str, dict[str, Any]]:
    strangle = _window_state(now_ny, cfg.strangle_entry_start_hhmm_et, cfg.strangle_entry_end_hhmm_et)
    gamma = _window_state(now_ny, cfg.gamma_entry_start_hhmm_et, cfg.gamma_entry_end_hhmm_et)
    gamma_pro = _window_state(now_ny, cfg.gamma_pro_entry_start_hhmm_et, cfg.gamma_pro_entry_end_hhmm_et)
    return {
        "reaction_zone": _window_state(now_ny, "09:30", "15:45"),
        "morning_strangle": strangle,
        "morning_double_strangle": dict(strangle),
        "morning_directional": dict(strangle),
        "gamma_scalping": gamma,
        "gamma_pro": gamma_pro,
    }


def prior_trading_day_high_low(bars: Sequence[Bar], today_d: date, tz: str) -> tuple[float | None, float | None]:
    by_d = _bars_by_session_date(bars, tz)
    prev_days = [d for d in sorted(by_d.keys()) if d < today_d]
    if not prev_days:
        return None, None
    bs = by_d[prev_days[-1]]
    if not bs:
        return None, None
    return max(float(x.high) for x in bs), min(float(x.low) for x in bs)


def intraday_volume_ratio_vs_recent_days(
    bars: Sequence[Bar],
    today_d: date,
    tz: str,
    *,
    now_ny: datetime | None = None,
    lookback_days: int = 5,
) -> tuple[float | None, float | None]:
    """
    Return (same_time_ratio, full_day_ratio).

    same_time_ratio compares today's cumulative volume up to the current NY
    minute with the average cumulative volume up to the same NY minute on recent
    sessions. full_day_ratio is retained as a secondary reference.
    """
    by_d = _bars_by_session_date(bars, tz)
    past = [d for d in sorted(by_d.keys()) if d < today_d]
    tbs = by_d.get(today_d) or []
    if not past or not tbs:
        return None, None

    now_ny = now_ny or _ny_now()
    cutoff = now_ny.hour * 60 + now_ny.minute

    def bar_minute_ny(b: Bar) -> int:
        n = to_ny(b.date, tz)
        return n.hour * 60 + n.minute

    today_same_time = sum(float(b.volume) for b in tbs if bar_minute_ny(b) <= cutoff)
    same_time_vols: list[float] = []
    full_day_vols: list[float] = []
    for d in past[-lookback_days:]:
        day_bars = by_d[d]
        same_time_vols.append(sum(float(b.volume) for b in day_bars if bar_minute_ny(b) <= cutoff))
        full_day_vols.append(sum(float(b.volume) for b in day_bars))

    same_avg = sum(same_time_vols) / max(len(same_time_vols), 1) if same_time_vols else 0.0
    full_avg = sum(full_day_vols) / max(len(full_day_vols), 1) if full_day_vols else 0.0
    today_full = sum(float(b.volume) for b in tbs)
    same_ratio = today_same_time / max(same_avg, 1e-12) if same_avg > 0 else None
    full_ratio = today_full / max(full_avg, 1e-12) if full_avg > 0 else None
    return same_ratio, full_ratio


def _configured_thresholds(cfg: Qqq0dteConfig) -> dict[str, float]:
    strangle_pct = _to_float(getattr(cfg, "strangle_range_pct", None))
    if strangle_pct is not None and strangle_pct > 0:
        strangle_chg_max = strangle_pct * 100.0
    else:
        strangle_chg_max = _DEFAULT_STRANGLE_CHG_MAX_PCT
    down = abs(_to_float(getattr(cfg, "directional_down_pct", None)) or 0.0) * 100.0
    up = abs(_to_float(getattr(cfg, "directional_up_pct", None)) or 0.0) * 100.0
    directional_candidates = [x for x in [down, up] if x > 0]
    directional_min = min(directional_candidates) if directional_candidates else _DEFAULT_DIRECTIONAL_CHG_MIN_PCT
    vol_spike = _to_float(getattr(cfg, "volume_spike_multiplier", None)) or _DEFAULT_VOL_SPIKE_RATIO
    return {
        "strangle_chg_max_pct": max(0.05, strangle_chg_max),
        "directional_chg_min_pct": max(strangle_chg_max + 0.05, directional_min),
        "volume_spike_ratio": max(0.1, vol_spike),
    }


def _quality_flags(
    *,
    bars: Sequence[Bar],
    today_bars: Sequence[Bar],
    spot: float | None,
    prev_close: float | None,
    same_time_volume_ratio: float | None,
    quote_available: bool,
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    notes: list[str] = []
    if not quote_available or spot is None:
        warnings.append("实时行情不可用，推荐只基于有限 K 线数据，置信度下调。")
    if prev_close is None or prev_close <= 0:
        warnings.append("缺少前收价，方向/宽跨判断可能不稳定。")
    if len(today_bars) < 5:
        warnings.append("当日分时线不足 5 根，暂不宜强解读早盘形态。")
    if len(bars) < 60:
        warnings.append("历史 K 线样本偏少，前日高低和量能统计可能不稳。")
    if same_time_volume_ratio is None:
        warnings.append("缺少同时间段历史量能对比。")
    else:
        notes.append(f"同时间段量能比约 {same_time_volume_ratio:.2f}×。")
    return warnings, notes


def _confidence(best: str, scores: dict[str, float], warnings: Sequence[str]) -> dict[str, Any]:
    if best == "unspecified":
        return {"level": "low", "score_gap": 0.0, "label": "低"}
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top = ordered[0][1] if ordered else 0.0
    second = ordered[1][1] if len(ordered) > 1 else 0.0
    gap = round(top - second, 3)
    if top >= 4.0 and gap >= 1.5 and len(warnings) <= 1:
        level = "high"
        label = "高"
    elif top >= 2.0 and gap >= 0.75 and len(warnings) <= 2:
        level = "medium"
        label = "中"
    else:
        level = "low"
        label = "低"
    return {"level": level, "score_gap": gap, "label": label}


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
    quote_available = bool(rq.get("available", False))
    spot = _to_float(rq.get("last"))
    prev_close = _to_float(rq.get("prev_close"))
    change_pct = _to_float(rq.get("change_pct"))
    if change_pct is None and spot is not None and prev_close and prev_close > 0:
        change_pct = (spot - prev_close) / prev_close * 100.0

    tz = cfg.assume_bars_timezone
    now_ny = _ny_now()
    by_d = _bars_by_session_date(bars, tz)
    today_bars = by_d.get(today_d) or []
    prev_high, prev_low = prior_trading_day_high_low(bars, today_d, tz)
    same_time_vol_ratio, full_day_vol_ratio = intraday_volume_ratio_vs_recent_days(
        bars,
        today_d,
        tz,
        now_ny=now_ny,
        lookback_days=5,
    )
    thresholds = _configured_thresholds(cfg)
    windows = _build_time_windows(cfg, now_ny)
    current_variant = str(getattr(cfg, "strategy_variant", "") or "").strip() or "unspecified"

    reasons: list[str] = []
    warnings, quality_notes = _quality_flags(
        bars=bars,
        today_bars=today_bars,
        spot=spot,
        prev_close=prev_close,
        same_time_volume_ratio=same_time_vol_ratio,
        quote_available=quote_available,
    )
    scores: dict[str, float] = {
        "reaction_zone": 0.0,
        "morning_strangle": 0.0,
        "morning_double_strangle": 0.0,
        "morning_directional": 0.0,
        "gamma_scalping": 0.0,
        "gamma_pro": 0.0,
    }

    abs_chg = abs(change_pct) if change_pct is not None else None
    if abs_chg is not None:
        reasons.append(f"{symbol} 相对前收涨跌约 {change_pct:+.3f}%（快照）。")
        if abs_chg >= thresholds["directional_chg_min_pct"]:
            scores["morning_directional"] += 3.0
            scores["gamma_pro"] += 0.8
            reasons.append(
                f"涨跌幅绝对值 ≥ 当前方向单阈值 {thresholds['directional_chg_min_pct']:.2f}% ，方向性较强。"
            )
        elif abs_chg <= thresholds["strangle_chg_max_pct"]:
            scores["morning_strangle"] += 2.6
            scores["morning_double_strangle"] += 2.1
            reasons.append(
                f"涨跌幅绝对值 ≤ 当前宽跨区间阈值 {thresholds['strangle_chg_max_pct']:.2f}% ，接近早盘震荡/宽跨假设。"
            )
            if abs_chg <= thresholds["strangle_chg_max_pct"] * 0.55:
                scores["morning_double_strangle"] += 0.8
                reasons.append("涨跌幅处在宽跨区间更内侧，早盘双宽跨可作为更分层的震荡候选。")
        else:
            scores["reaction_zone"] += 1.5
            reasons.append("涨跌幅处于宽跨与方向单之间，更适合先观察关键位反应。")

    breakout_call = False
    breakout_put = False
    if spot is not None and prev_high is not None and prev_high > 0 and spot >= prev_high:
        breakout_call = True
        scores["gamma_scalping"] += 2.2
        scores["gamma_pro"] += 2.5
        scores["morning_directional"] += 0.8
        reasons.append(f"现价 {spot:.4g} ≥ 前交易日高 {prev_high:.4g}，有上沿突破倾向。")
    if spot is not None and prev_low is not None and prev_low > 0 and spot <= prev_low:
        breakout_put = True
        scores["gamma_scalping"] += 2.2
        scores["gamma_pro"] += 2.5
        scores["morning_directional"] += 0.8
        reasons.append(f"现价 {spot:.4g} ≤ 前交易日低 {prev_low:.4g}，有下沿突破倾向。")
    if not breakout_call and not breakout_put and abs_chg is not None and abs_chg <= thresholds["strangle_chg_max_pct"]:
        scores["morning_double_strangle"] += 0.35
        reasons.append("暂未触及前日高低，双宽跨不会被突破场景优先压低。")

    if same_time_vol_ratio is not None:
        reasons.append(f"今日同时间段累计量 / 近几日同时间段均量 ≈ {same_time_vol_ratio:.2f}×。")
        if same_time_vol_ratio >= thresholds["volume_spike_ratio"]:
            scores["reaction_zone"] += 2.0
            scores["gamma_pro"] += 1.0
            reasons.append(f"量能达到当前放量阈值 {thresholds['volume_spike_ratio']:.2f}×，关键位反应可信度提升。")
        elif same_time_vol_ratio < 0.65:
            scores["morning_strangle"] += 0.4
            scores["morning_double_strangle"] += 0.3
            reasons.append("同时间段量能偏低，趋势延续证据不足，宽跨类策略相对更可观察。")

    if vix_change_pct >= _VIX_CHG_BOOST_PCT:
        scores["gamma_scalping"] += 1.0
        scores["gamma_pro"] += 0.8
        reasons.append(f"VIX 相对前收约 +{vix_change_pct:.3f}%，短线波动/Gamma 场景权重上调。")
    elif vix_change_pct <= -_VIX_CHG_BOOST_PCT:
        scores["morning_strangle"] += 0.3
        scores["morning_double_strangle"] += 0.3
        reasons.append(f"VIX 走弱约 {vix_change_pct:.3f}%，短线波动预期偏低。")

    # Time-window gating: recommendations outside their entry windows are still
    # visible but penalized so the UI does not overstate late/early ideas.
    for variant, w in windows.items():
        state = str(w.get("state") or "")
        if state == "before":
            scores[variant] -= 0.4
        elif state == "after":
            scores[variant] -= 1.6
    active_labels = [f"{_VARIANT_ZH[k]} {v['start']}-{v['end']}" for k, v in windows.items() if v.get("active")]
    if active_labels:
        reasons.append("当前仍在适用窗口内：" + "；".join(active_labels[:3]) + (" 等" if len(active_labels) > 3 else "") + "。")
    else:
        warnings.append(f"当前美东时间 {now_ny:%H:%M} 不在主要早盘入场窗口内，推荐应以观察为主。")

    # If the live config already uses a supported variant, give a small
    # consistency bonus. This makes the panel less jumpy without hiding evidence.
    if current_variant in scores:
        scores[current_variant] += 0.25

    tiebreak = ("morning_directional", "gamma_pro", "gamma_scalping", "morning_double_strangle", "morning_strangle", "reaction_zone")
    best = max(tiebreak, key=lambda k: scores[k])
    if scores[best] <= 0.25:
        best = "unspecified"
        reasons.append("当前快照没有足够强的策略环境信号，显示为暂无明确倾向。")
    confidence = _confidence(best, scores, warnings)
    if best != "unspecified" and current_variant and current_variant != best:
        warnings.append(f"当前 live 配置为 {_VARIANT_ZH.get(current_variant, current_variant)}，与系统推荐不一致；不会自动切换。")

    features: dict[str, Any] = {
        "symbol": symbol,
        "spot": spot,
        "prev_close": prev_close,
        "change_pct_from_prev_close": change_pct,
        "prev_session_high": prev_high,
        "prev_session_low": prev_low,
        "breakout_vs_prev_high": breakout_call,
        "breakout_vs_prev_low": breakout_put,
        "volume_ratio_today_vs_recent_days": full_day_vol_ratio,
        "same_time_volume_ratio_today_vs_recent_days": same_time_vol_ratio,
        "vix_change_pct_snapshot": vix_change_pct,
        "assume_bars_timezone": cfg.assume_bars_timezone,
        "session_date_ny": today_d.isoformat(),
        "current_time_et": now_ny.isoformat(),
        "current_strategy_variant": current_variant,
        "recommended_matches_current_strategy": bool(best == current_variant),
        "thresholds": thresholds,
        "time_windows": windows,
        "data_quality_warnings": warnings,
        "quality_notes": quality_notes,
        "quote_available": quote_available,
        "today_bars": len(today_bars),
    }

    option_quality = {
        "checked": False,
        "status": "not_checked",
        "note": "系统推荐不解析期权链；下单前仍由 resolve-contract、bid/ask 和 worker 风控校验。",
    }

    return {
        "ok": True,
        "recommended_variant": best,
        "recommended_name_zh": _VARIANT_ZH.get(best, _VARIANT_ZH["unspecified"]),
        "confidence": confidence,
        "scores": scores,
        "reasons": reasons,
        "warnings": warnings,
        "features": features,
        "option_quality": option_quality,
        "disclaimer": _DISCLAIMER,
        "scan_interval_seconds": scan_interval_seconds,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
