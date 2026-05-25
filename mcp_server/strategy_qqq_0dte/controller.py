"""汇总闸门、关键位、信号，生成开平仓意图与日志。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Sequence

from backtest_engine import Bar

from .config import Qqq0dteConfig
from .confirmation import breakout_confirmed, reversal_after_zone_touch
from .contract_select import select_strike
from .exit_rules import (
    DOUBLE_STRANGLE_LEG_KEYS,
    evaluate_exit,
    evaluate_double_strangle_exit,
    evaluate_gamma_exit,
    evaluate_gamma_pro_exit,
    evaluate_morning_directional_exit,
    evaluate_strangle_exit,
)
from .levels import (
    build_key_levels,
    collect_level_prices,
    prior_trading_date_with_data,
)
from .pricer import synthetic_option_price_at_bar
from .regime import opening_regime
from .session_us import (
    is_at_or_after_et_hhmm,
    is_no_trade_opening_window,
    is_past_new_trade_cutoff,
    is_within_et_hhmm_interval,
    is_within_rth,
    minutes_since_rth_open,
    new_trades_cutoff_datetime,
    ny_date,
    rth_bounds,
    to_ny,
)
from .state import DecisionLogEntry, OpenPosition, TradeIntent, TradeIntentKind
from .volume_signal import volume_spike_at
from .zones import build_zones_from_levels, find_active_zone


@dataclass
class BarProcessResult:
    intents: list[TradeIntent] = field(default_factory=list)
    close_position: bool = False
    close_reason: str = ""
    entry_snapshot: dict[str, Any] | None = None
    exit_snapshot: dict[str, Any] | None = None
    logs: list[DecisionLogEntry] = field(default_factory=list)


def _bars_by_ny_date(bars: Sequence[Bar], tz_name: str) -> dict[date, list[Bar]]:
    m: dict[date, list[Bar]] = defaultdict(list)
    for b in bars:
        m[ny_date(b.date, tz_name)].append(b)
    for d in m:
        m[d].sort(key=lambda x: x.date)
    return dict(m)


def _underlying_px_strangle(bar: Bar, field: str) -> float:
    f = (field or "low").strip().lower()
    if f == "open":
        return float(bar.open)
    if f == "high":
        return float(bar.high)
    if f == "close":
        return float(bar.close)
    return float(bar.low)


def _strangle_original_cost(pos: OpenPosition) -> float:
    c = float(getattr(pos, "strangle_original_entry_px", 0.0) or 0.0)
    if c > 0:
        return c
    return float(getattr(pos, "entry_px", 0.0) or 0.0)


def _strangle_combo_r(pos: OpenPosition, call_mark: float, put_mark: float) -> float:
    c = _strangle_original_cost(pos)
    realized = max(0.0, float(getattr(pos, "strangle_realized_exit_px", 0.0) or 0.0))
    call_active = bool(getattr(pos, "strangle_call_active", True))
    put_active = bool(getattr(pos, "strangle_put_active", True))
    v = realized
    if call_active:
        v += max(0.0, float(call_mark))
    if put_active:
        v += max(0.0, float(put_mark))
    return (v - c) / max(c, 1e-12)


def _apply_strangle_leg_closed_to_pos(pos: OpenPosition, which: str, exit_px: float) -> None:
    if which == "call":
        pos.strangle_realized_exit_px += max(0.0, float(exit_px))
        pos.strangle_call_active = False
        pos.call_entry_px = 0.0
        pos.call_strike = 0.0
    elif which == "put":
        pos.strangle_realized_exit_px += max(0.0, float(exit_px))
        pos.strangle_put_active = False
        pos.put_entry_px = 0.0
        pos.put_strike = 0.0
    ac = float(pos.call_entry_px) if pos.strangle_call_active else 0.0
    ap = float(pos.put_entry_px) if pos.strangle_put_active else 0.0
    pos.entry_px = float(ac + ap)


def _double_strangle_active_legs(pos: OpenPosition) -> dict[str, dict[str, Any]]:
    legs = getattr(pos, "double_strangle_legs", None)
    if not isinstance(legs, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key in DOUBLE_STRANGLE_LEG_KEYS:
        leg = legs.get(key)
        if isinstance(leg, dict) and bool(leg.get("active", True)):
            out[key] = leg
    return out


def _double_strangle_original_cost(pos: OpenPosition) -> float:
    c = float(getattr(pos, "strangle_original_entry_px", 0.0) or 0.0)
    if c > 0:
        return c
    legs = getattr(pos, "double_strangle_legs", None)
    if isinstance(legs, dict):
        return sum(float(x.get("entry_px") or 0.0) for x in legs.values() if isinstance(x, dict))
    return float(getattr(pos, "entry_px", 0.0) or 0.0)


def _double_strangle_combo_r(pos: OpenPosition, leg_marks: dict[str, float]) -> float:
    c = _double_strangle_original_cost(pos)
    realized = max(0.0, float(getattr(pos, "strangle_realized_exit_px", 0.0) or 0.0))
    v = realized
    for key in _double_strangle_active_legs(pos):
        v += max(0.0, float(leg_marks.get(key) or 0.0))
    return (v - c) / max(c, 1e-12)


def _apply_double_strangle_leg_closed_to_pos(pos: OpenPosition, leg_key: str, exit_px: float) -> None:
    legs = getattr(pos, "double_strangle_legs", None)
    if not isinstance(legs, dict):
        return
    leg = legs.get(leg_key)
    if not isinstance(leg, dict):
        return
    pos.strangle_realized_exit_px += max(0.0, float(exit_px))
    leg["active"] = False
    leg["entry_px"] = 0.0
    remaining = _double_strangle_active_legs(pos)
    pos.entry_px = float(sum(float(x.get("entry_px") or 0.0) for x in remaining.values()))
    pos.call_entry_px = float(
        sum(float(x.get("entry_px") or 0.0) for x in remaining.values() if str(x.get("right") or "") == "call")
    )
    pos.put_entry_px = float(
        sum(float(x.get("entry_px") or 0.0) for x in remaining.values() if str(x.get("right") or "") == "put")
    )


def _first_rth_open_of_day(today_bars: Sequence[Bar], session_date: date, cfg: Qqq0dteConfig, tz_name: str) -> float | None:
    open_t, _ = rth_bounds(cfg, session_date)
    for b in today_bars:
        if to_ny(b.date, tz_name) >= open_t:
            return float(b.open)
    return None


def _single_leg_marks_tp_sl(
    mark_syn_raw: float,
    slip: float,
    live_bid: float | None,
    live_last: float | None,
    *,
    synthetic_baseline: str = "mid",
) -> tuple[float, float, bool]:
    """
    K 线内单腿：止盈用 bid 优先；止损仅使用 last（last 缺失则本轮跳过止损）。
    无注入时：synthetic_baseline=\"mid\" 用合成 mid（反应区历史口径）；否则用 synthetic*(1-slip)（gamma / 方向等历史口径）。
    """
    raw = float(mark_syn_raw)
    disc = max(0.0, raw * (1.0 - float(slip)))
    base_syn = raw if str(synthetic_baseline) == "mid" else disc
    b_ok = live_bid is not None and float(live_bid) > 0
    l_ok = live_last is not None and float(live_last) > 0
    if not b_ok and not l_ok:
        return base_syn, 0.0, False
    bid_v = float(live_bid) if b_ok else None
    last_v = float(live_last) if l_ok else None
    tp_src = bid_v if bid_v is not None else last_v
    sl_src = last_v if last_v is not None else 0.0
    if tp_src is None:
        return base_syn, 0.0, False
    return max(0.0, float(tp_src)), max(0.0, float(sl_src)), True


def _today_vwap(today_bars: Sequence[Bar]) -> float | None:
    pv = 0.0
    vv = 0.0
    for b in today_bars:
        vol = max(0.0, float(getattr(b, "volume", 0.0) or 0.0))
        if vol <= 0:
            continue
        tp = (float(b.high) + float(b.low) + float(b.close)) / 3.0
        pv += tp * vol
        vv += vol
    if vv <= 0:
        return None
    return pv / max(vv, 1e-12)


class StrategyController:
    def __init__(self, cfg: Qqq0dteConfig | None = None) -> None:
        self.cfg = cfg or Qqq0dteConfig()
        self._pos: OpenPosition | None = None
        self._trades_today: dict[date, int] = defaultdict(int)
        self._by_date: dict[date, list[Bar]] | None = None
        self._sorted_dates: list[date] | None = None
        self._gamma_vwap_armed: dict[date, str] = defaultdict(str)
        # LIVE：Worker 每根 K 前注入两腿 bid（止盈/卖一口径）与 last（止损）；缺省为 None 时用合成价。
        self.strangle_live_leg_bids: tuple[float | None, float | None] | None = None
        self.strangle_live_leg_lasts: tuple[float | None, float | None] | None = None
        self.double_strangle_live_leg_bids: dict[str, float | None] | None = None
        self.double_strangle_live_leg_lasts: dict[str, float | None] | None = None
        self.option_live_bid: float | None = None
        self.option_live_last: float | None = None

    def reset(self) -> None:
        self._pos = None
        self._trades_today.clear()
        self._by_date = None
        self._sorted_dates = None
        self._gamma_vwap_armed.clear()
        self.strangle_live_leg_bids = None
        self.strangle_live_leg_lasts = None
        self.double_strangle_live_leg_bids = None
        self.double_strangle_live_leg_lasts = None
        self.option_live_bid = None
        self.option_live_last = None

    def prepare(self, all_bars: Sequence[Bar]) -> None:
        tz = self.cfg.assume_bars_timezone
        self._by_date = _bars_by_ny_date(all_bars, tz)
        self._sorted_dates = sorted(self._by_date.keys())

    def process_bar(self, index: int, all_bars: Sequence[Bar]) -> BarProcessResult:
        variant = str(getattr(self.cfg, "strategy_variant", "reaction_zone") or "reaction_zone").strip().lower()
        if variant == "morning_strangle":
            return self._process_morning_strangle_bar(index, all_bars)
        if variant == "morning_double_strangle":
            return self._process_morning_double_strangle_bar(index, all_bars)
        if variant == "morning_directional":
            return self._process_morning_directional_bar(index, all_bars)
        if variant == "gamma_scalping":
            return self._process_gamma_scalping_bar(index, all_bars)
        if variant == "gamma_pro":
            return self._process_gamma_pro_bar(index, all_bars)
        return self._process_reaction_zone_bar(index, all_bars)

    def _gamma_leader_gate(self, side: str) -> tuple[bool, dict[str, Any]]:
        cfg = self.cfg
        if not bool(getattr(cfg, "gamma_require_leader_confirmation", True)):
            return True, {"gate": "disabled"}
        qqq = float(getattr(cfg, "gamma_rt_qqq_change_pct", 0.0))
        l1 = float(getattr(cfg, "gamma_rt_leader1_change_pct", 0.0))
        l2 = float(getattr(cfg, "gamma_rt_leader2_change_pct", 0.0))
        mn = float(getattr(cfg, "gamma_leader_min_move_pct", 0.6))
        lag = float(getattr(cfg, "gamma_leader_lag_pct", 0.1))
        if side == "long_call":
            lead = max(l1, l2)
            ok = lead >= mn and qqq <= (lead - lag)
            return ok, {"qqq": qqq, "leader_max": lead, "min_move": mn, "lag_pct": lag}
        lead = min(l1, l2)
        ok = lead <= -mn and qqq >= (lead + lag)
        return ok, {"qqq": qqq, "leader_min": lead, "min_move": mn, "lag_pct": lag}

    def _gamma_pro_leader_gate(self, side: str) -> tuple[bool, dict[str, Any]]:
        cfg = self.cfg
        if not bool(getattr(cfg, "gamma_pro_require_leader_confirmation", True)):
            return True, {"gate": "disabled"}
        qqq = float(getattr(cfg, "gamma_rt_qqq_change_pct", 0.0))
        l1 = float(getattr(cfg, "gamma_rt_leader1_change_pct", 0.0))
        l2 = float(getattr(cfg, "gamma_rt_leader2_change_pct", 0.0))
        mn = float(getattr(cfg, "gamma_leader_min_move_pct", 0.6))
        lag = float(getattr(cfg, "gamma_leader_lag_pct", 0.1))
        if side == "long_call":
            lead = max(l1, l2)
            ok = lead >= mn and qqq <= (lead - lag)
            return ok, {"qqq": qqq, "leader_max": lead, "min_move": mn, "lag_pct": lag}
        lead = min(l1, l2)
        ok = lead <= -mn and qqq >= (lead + lag)
        return ok, {"qqq": qqq, "leader_min": lead, "min_move": mn, "lag_pct": lag}

    def _process_gamma_scalping_bar(self, index: int, all_bars: Sequence[Bar]) -> BarProcessResult:
        cfg = self.cfg
        tz = cfg.assume_bars_timezone
        out = BarProcessResult()
        bar = all_bars[index]
        session_d = ny_date(bar.date, tz)

        if self._by_date is None:
            self.prepare(all_bars)
        assert self._by_date is not None and self._sorted_dates is not None
        prior_d = prior_trading_date_with_data(self._sorted_dates, session_d)
        prior_day_bars: list[Bar] = self._by_date.get(prior_d, []) if prior_d else []
        today_all: list[Bar] = self._by_date.get(session_d, [])
        today_sofar = [b for b in today_all if b.date <= bar.date]
        levels = build_key_levels(
            session_date=session_d,
            prior_day_bars=prior_day_bars,
            today_bars_before_now=today_sofar,
            cfg=cfg,
            tz_name=tz,
            spot_for_psych=float(bar.close),
        )
        prev_close = levels.prev_close
        ny = to_ny(bar.date, tz)
        slip = float(cfg.option_slippage_pct)
        spot = float(bar.close)

        def log(msg: str, **extra: Any) -> None:
            if cfg.log_decisions:
                out.logs.append(
                    DecisionLogEntry(
                        bar_index=index,
                        as_of=ny.isoformat(),
                        message=msg,
                        extra=extra,
                    )
                )

        if not is_within_rth(bar.date, cfg, tz):
            log("skip_not_rth")
            return out

        if self._pos is not None:
            if self._pos.side not in ("long_call", "long_put"):
                log("gamma_unexpected_position", side=self._pos.side)
                return out
            pos = self._pos
            right = "call" if pos.side == "long_call" else "put"
            mark_raw = synthetic_option_price_at_bar(
                all_bars, index, strike=pos.strike, right=right, session_date=session_d, cfg=cfg, tz_name=tz
            )
            lb = getattr(self, "option_live_bid", None)
            ll = getattr(self, "option_live_last", None)
            mark_tp, mark_sl, live_on = _single_leg_marks_tp_sl(
                mark_raw, slip, lb, ll, synthetic_baseline="discounted"
            )
            reason, detail = (
                evaluate_gamma_exit(pos, mark_tp, ny, cfg, tz, mark_sl=mark_sl)
                if live_on
                else evaluate_gamma_exit(pos, mark_tp, ny, cfg, tz)
            )
            if reason != "hold":
                exit_px = max(0.0, mark_tp if live_on else float(mark_raw) * (1.0 - slip))
                out.close_position = True
                out.close_reason = f"{reason}:{detail}"
                out.exit_snapshot = {
                    "side": pos.side,
                    "strike": pos.strike,
                    "entry_px": pos.entry_px,
                    "exit_px": exit_px,
                    "contracts": pos.contracts,
                    "mark_mid": mark_raw,
                    "mark_tp": mark_tp,
                    "mark_sl": mark_sl,
                }
                log("exit", reason=reason, detail=detail, mark_tp=mark_tp, mark_sl=mark_sl)
                self._pos = None
            else:
                r = (mark_tp - pos.entry_px) / max(pos.entry_px, 1e-12)
                held = (ny - pos.entry_time).total_seconds() / 60.0
                log("hold_gamma", R=r, mark_tp=mark_tp, mark_sl=mark_sl, held_min=held)
            return out

        if self._trades_today[session_d] >= int(cfg.max_trades_per_day):
            log("skip_max_trades_day")
            return out
        start_s = str(getattr(cfg, "gamma_entry_start_hhmm_et", "09:30") or "09:30")
        end_s = str(getattr(cfg, "gamma_entry_end_hhmm_et", "10:00") or "10:00")
        if not is_within_et_hhmm_interval(ny, start_s, end_s):
            log("skip_gamma_entry_window", start=start_s, end=end_s)
            return out
        if prev_close is None or prev_close <= 0:
            log("skip_gamma_no_prev_close")
            return out

        side: str | None = None
        # 1) 前日区间突破 + VIX 上行：站上昨高 → 买 Call；跌破昨低 → 买 Put
        if bool(getattr(cfg, "gamma_require_breakout_prev_day", True)) and levels.prev_high and levels.prev_low:
            ph = float(levels.prev_high)
            pl = float(levels.prev_low)
            if spot >= ph:
                side = "long_call"
            elif spot <= pl:
                side = "long_put"
            if side is not None and bool(getattr(cfg, "gamma_require_vix_rising", True)):
                vix_chg = float(getattr(cfg, "gamma_rt_vix_change_pct", 0.0))
                vix_min = float(getattr(cfg, "gamma_vix_rising_min_pct", 0.3))
                if vix_chg < vix_min:
                    log("skip_gamma_vix_not_rising", vix_change_pct=vix_chg, min_pct=vix_min, breakout_side=side)
                    side = None

        # 2) VWAP 偏离后首次回抽确认（若突破未触发）
        if side is None and bool(getattr(cfg, "gamma_enable_vwap_reversion", True)):
            vwap = _today_vwap(today_sofar)
            if vwap and vwap > 0:
                dev = (spot - vwap) / max(vwap, 1e-12)
                thr = float(getattr(cfg, "gamma_vwap_deviation_pct", 0.003))
                arm = self._gamma_vwap_armed.get(session_d, "")
                if dev <= -thr:
                    self._gamma_vwap_armed[session_d] = "below"
                    arm = "below"
                elif dev >= thr:
                    self._gamma_vwap_armed[session_d] = "above"
                    arm = "above"
                if arm == "below" and spot >= vwap:
                    side = "long_call"
                    self._gamma_vwap_armed[session_d] = ""
                elif arm == "above" and spot <= vwap:
                    side = "long_put"
                    self._gamma_vwap_armed[session_d] = ""
                if side is None:
                    log("skip_gamma_vwap_wait", dev=dev, thr=thr, arm=arm)

        if side is None:
            log("skip_gamma_no_entry_signal")
            return out

        ok_leader, leader_info = self._gamma_leader_gate(side)
        if not ok_leader:
            log("skip_gamma_leader_gate", side=side, **leader_info)
            return out

        und = _underlying_px_strangle(bar, str(getattr(cfg, "strangle_underlying_field", "close") or "close"))
        if side == "long_call":
            k = select_strike(
                und,
                "call",
                strike_step=cfg.strike_step,
                otm_steps=int(getattr(cfg, "gamma_call_otm_steps", cfg.call_strikes_otm)),
            )
            right = "call"
            intent_kind = TradeIntentKind.BUY_CALL
            reason = "gamma_scalping_call"
        else:
            k = select_strike(
                und,
                "put",
                strike_step=cfg.strike_step,
                otm_steps=int(getattr(cfg, "gamma_put_otm_steps", cfg.put_strikes_otm)),
            )
            right = "put"
            intent_kind = TradeIntentKind.BUY_PUT
            reason = "gamma_scalping_put"
        theo = synthetic_option_price_at_bar(all_bars, index, strike=k, right=right, session_date=session_d, cfg=cfg, tz_name=tz)
        entry = theo * (1.0 + slip)
        qty = int(cfg.initial_option_contracts)
        self._pos = OpenPosition(
            side=side, strike=k, entry_bar_index=index, entry_time=ny, entry_px=entry, contracts=qty
        )
        self._trades_today[session_d] += 1
        out.intents.append(
            TradeIntent(
                kind=intent_kind,
                underlying=cfg.symbol,
                strike=k,
                right=right,  # type: ignore[arg-type]
                contracts=qty,
                reason=reason,
            )
        )
        out.entry_snapshot = {
            "side": side,
            "strike": k,
            "entry_px": entry,
            "theoretical": theo,
            "contracts": qty,
            "und_px": und,
            "prev_close": float(prev_close),
            "leader_gate": leader_info,
        }
        log("enter_gamma", side=side, strike=k, entry_px=entry, und=und, leader_gate=leader_info)
        return out

    def _process_gamma_pro_bar(self, index: int, all_bars: Sequence[Bar]) -> BarProcessResult:
        cfg = self.cfg
        tz = cfg.assume_bars_timezone
        out = BarProcessResult()
        bar = all_bars[index]
        session_d = ny_date(bar.date, tz)

        if self._by_date is None:
            self.prepare(all_bars)
        assert self._by_date is not None and self._sorted_dates is not None
        prior_d = prior_trading_date_with_data(self._sorted_dates, session_d)
        prior_day_bars: list[Bar] = self._by_date.get(prior_d, []) if prior_d else []
        today_all: list[Bar] = self._by_date.get(session_d, [])
        today_sofar = [b for b in today_all if b.date <= bar.date]
        levels = build_key_levels(
            session_date=session_d,
            prior_day_bars=prior_day_bars,
            today_bars_before_now=today_sofar,
            cfg=cfg,
            tz_name=tz,
            spot_for_psych=float(bar.close),
        )
        ny = to_ny(bar.date, tz)
        slip = float(cfg.option_slippage_pct)
        spot = float(bar.close)

        def log(msg: str, **extra: Any) -> None:
            if cfg.log_decisions:
                out.logs.append(
                    DecisionLogEntry(
                        bar_index=index,
                        as_of=ny.isoformat(),
                        message=msg,
                        extra=extra,
                    )
                )

        if not is_within_rth(bar.date, cfg, tz):
            log("skip_not_rth")
            return out

        if self._pos is not None:
            if self._pos.side not in ("long_call", "long_put"):
                log("gamma_pro_unexpected_position", side=self._pos.side)
                return out
            pos = self._pos
            right = "call" if pos.side == "long_call" else "put"
            mark_raw = synthetic_option_price_at_bar(
                all_bars, index, strike=pos.strike, right=right, session_date=session_d, cfg=cfg, tz_name=tz
            )
            lb = getattr(self, "option_live_bid", None)
            ll = getattr(self, "option_live_last", None)
            mark_tp, mark_sl, live_on = _single_leg_marks_tp_sl(
                mark_raw, slip, lb, ll, synthetic_baseline="discounted"
            )
            reason, detail = (
                evaluate_gamma_pro_exit(pos, mark_tp, ny, cfg, tz, mark_sl=mark_sl)
                if live_on
                else evaluate_gamma_pro_exit(pos, mark_tp, ny, cfg, tz)
            )
            if reason != "hold":
                exit_px = max(0.0, mark_tp if live_on else float(mark_raw) * (1.0 - slip))
                out.close_position = True
                out.close_reason = f"{reason}:{detail}"
                out.exit_snapshot = {
                    "side": pos.side,
                    "strike": pos.strike,
                    "entry_px": pos.entry_px,
                    "exit_px": exit_px,
                    "contracts": pos.contracts,
                    "mark_mid": mark_raw,
                    "mark_tp": mark_tp,
                    "mark_sl": mark_sl,
                }
                log("exit", reason=reason, detail=detail, mark_tp=mark_tp, mark_sl=mark_sl)
                self._pos = None
            else:
                r = (mark_tp - pos.entry_px) / max(pos.entry_px, 1e-12)
                held = (ny - pos.entry_time).total_seconds() / 60.0
                log("hold_gamma_pro", R=r, mark_tp=mark_tp, mark_sl=mark_sl, held_min=held)
            return out

        if self._trades_today[session_d] >= int(cfg.max_trades_per_day):
            log("skip_max_trades_day")
            return out

        st = str(getattr(cfg, "gamma_pro_entry_start_hhmm_et", "10:00") or "10:00")
        ed = str(getattr(cfg, "gamma_pro_entry_end_hhmm_et", "15:30") or "15:30")
        if not is_within_et_hhmm_interval(ny, st, ed):
            log("skip_gamma_pro_entry_window", start=st, end=ed)
            return out
        md_s = str(getattr(cfg, "gamma_pro_midday_skip_start_hhmm_et", "12:00") or "12:00")
        md_e = str(getattr(cfg, "gamma_pro_midday_skip_end_hhmm_et", "13:00") or "13:00")
        if is_within_et_hhmm_interval(ny, md_s, md_e):
            log("skip_gamma_pro_midday_pause", start=md_s, end=md_e)
            return out

        ph = float(levels.prev_high) if levels.prev_high else 0.0
        pl = float(levels.prev_low) if levels.prev_low else 0.0
        vwap = _today_vwap(today_sofar)
        vol_spike = volume_spike_at(all_bars, index, cfg)
        side: str | None = None
        signal = "none"

        # 1) 真突破：关键位突破 + 放量 + 实体方向一致
        body = abs(float(bar.close) - float(bar.open))
        rng = max(1e-12, float(bar.high) - float(bar.low))
        body_ratio = body / rng
        if ph > 0 and spot > ph and vol_spike and body_ratio >= 0.45:
            side = "long_call"
            signal = "breakout_call"
        elif pl > 0 and spot < pl and vol_spike and body_ratio >= 0.45:
            side = "long_put"
            signal = "breakout_put"

        # 2) 假突破：穿越关键位后收回关键位内 + 未放量
        if side is None and bool(getattr(cfg, "gamma_pro_enable_false_breakout_reversal", True)):
            if ph > 0 and float(bar.high) > ph and spot < ph and not vol_spike:
                side = "long_put"
                signal = "false_breakout_put"
            elif pl > 0 and float(bar.low) < pl and spot > pl and not vol_spike:
                side = "long_call"
                signal = "false_breakout_call"

        # 3) 午后趋势续航：顺着 VWAP 方向回踩 + 量缩后重新放量
        aft = str(getattr(cfg, "gamma_pro_afternoon_start_hhmm_et", "13:30") or "13:30")
        if side is None and is_within_et_hhmm_interval(ny, aft, ed) and vwap and len(today_sofar) >= 3:
            b1 = today_sofar[-2]
            b2 = today_sofar[-3]
            v0 = max(0.0, float(bar.volume))
            v1 = max(0.0, float(b1.volume))
            v2 = max(0.0, float(b2.volume))
            pb = float(getattr(cfg, "gamma_pro_vwap_pullback_pct", 0.0015))
            pullback_call = float(b1.low) <= float(vwap) * (1.0 + pb) and spot > float(vwap)
            pullback_put = float(b1.high) >= float(vwap) * (1.0 - pb) and spot < float(vwap)
            vol_pattern = v2 > v1 and v0 > v1
            if pullback_call and vol_pattern:
                side = "long_call"
                signal = "afternoon_follow_call"
            elif pullback_put and vol_pattern:
                side = "long_put"
                signal = "afternoon_follow_put"

        if side is None:
            log("skip_gamma_pro_no_entry_signal", prev_high=ph, prev_low=pl, vol_spike=vol_spike, vwap=vwap)
            return out

        ok_leader, leader_info = self._gamma_pro_leader_gate(side)
        if not ok_leader:
            log("skip_gamma_pro_leader_gate", side=side, signal=signal, **leader_info)
            return out

        und = _underlying_px_strangle(bar, str(getattr(cfg, "strangle_underlying_field", "close") or "close"))
        if side == "long_call":
            k = select_strike(
                und,
                "call",
                strike_step=cfg.strike_step,
                otm_steps=int(getattr(cfg, "gamma_pro_call_otm_steps", cfg.call_strikes_otm)),
            )
            right = "call"
            intent_kind = TradeIntentKind.BUY_CALL
            reason = "gamma_pro_call"
        else:
            k = select_strike(
                und,
                "put",
                strike_step=cfg.strike_step,
                otm_steps=int(getattr(cfg, "gamma_pro_put_otm_steps", cfg.put_strikes_otm)),
            )
            right = "put"
            intent_kind = TradeIntentKind.BUY_PUT
            reason = "gamma_pro_put"
        theo = synthetic_option_price_at_bar(all_bars, index, strike=k, right=right, session_date=session_d, cfg=cfg, tz_name=tz)
        entry = theo * (1.0 + slip)
        qty = int(cfg.initial_option_contracts)
        self._pos = OpenPosition(
            side=side, strike=k, entry_bar_index=index, entry_time=ny, entry_px=entry, contracts=qty
        )
        self._trades_today[session_d] += 1
        out.intents.append(
            TradeIntent(
                kind=intent_kind,
                underlying=cfg.symbol,
                strike=k,
                right=right,  # type: ignore[arg-type]
                contracts=qty,
                reason=reason,
            )
        )
        out.entry_snapshot = {
            "side": side,
            "signal": signal,
            "strike": k,
            "entry_px": entry,
            "theoretical": theo,
            "contracts": qty,
            "und_px": und,
            "leader_gate": leader_info,
            "vwap": vwap,
            "vol_spike": vol_spike,
        }
        log("enter_gamma_pro", side=side, signal=signal, strike=k, entry_px=entry, und=und, leader_gate=leader_info)
        return out

    def _process_morning_directional_bar(self, index: int, all_bars: Sequence[Bar]) -> BarProcessResult:
        cfg = self.cfg
        tz = cfg.assume_bars_timezone
        out = BarProcessResult()
        bar = all_bars[index]
        session_d = ny_date(bar.date, tz)

        if self._by_date is None:
            self.prepare(all_bars)

        assert self._by_date is not None and self._sorted_dates is not None
        prior_d = prior_trading_date_with_data(self._sorted_dates, session_d)
        prior_day_bars: list[Bar] = self._by_date.get(prior_d, []) if prior_d else []
        today_all: list[Bar] = self._by_date.get(session_d, [])
        today_sofar = [b for b in today_all if b.date <= bar.date]

        levels = build_key_levels(
            session_date=session_d,
            prior_day_bars=prior_day_bars,
            today_bars_before_now=today_sofar,
            cfg=cfg,
            tz_name=tz,
            spot_for_psych=float(bar.close),
        )
        prev_close = levels.prev_close

        def log(msg: str, **extra: Any) -> None:
            if cfg.log_decisions:
                out.logs.append(
                    DecisionLogEntry(
                        bar_index=index,
                        as_of=to_ny(bar.date, tz).isoformat(),
                        message=msg,
                        extra=extra,
                    )
                )

        if not is_within_rth(bar.date, cfg, tz):
            log("skip_not_rth")
            return out

        ny = to_ny(bar.date, tz)
        slip = float(cfg.option_slippage_pct)

        if self._pos is not None:
            if self._pos.side == "strangle":
                log("directional_mode_unexpected_strangle")
                return out
            if self._pos.side not in ("long_call", "long_put"):
                log("directional_mode_unexpected_position", side=self._pos.side)
                return out
            pos = self._pos
            right = "call" if pos.side == "long_call" else "put"
            mark_raw = synthetic_option_price_at_bar(
                all_bars,
                index,
                strike=pos.strike,
                right=right,
                session_date=session_d,
                cfg=cfg,
                tz_name=tz,
            )
            lb = getattr(self, "option_live_bid", None)
            ll = getattr(self, "option_live_last", None)
            mark_tp, mark_sl, live_on = _single_leg_marks_tp_sl(
                mark_raw, slip, lb, ll, synthetic_baseline="discounted"
            )
            reason, detail = evaluate_morning_directional_exit(
                pos, mark_tp, ny, cfg, tz, mark_sl=mark_sl
            )
            if reason != "hold":
                if live_on:
                    lim = mark_sl if reason == "stop_loss" else mark_tp
                else:
                    lim = float(mark_raw) * (1.0 - slip)
                exit_px = max(0.0, lim)
                out.close_position = True
                out.close_reason = f"{reason}:{detail}"
                out.exit_snapshot = {
                    "side": pos.side,
                    "strike": pos.strike,
                    "entry_px": pos.entry_px,
                    "exit_px": exit_px,
                    "contracts": pos.contracts,
                    "mark_mid": mark_raw,
                    "mark_tp": mark_tp,
                    "mark_sl": mark_sl,
                }
                log("exit", reason=reason, detail=detail, mark_tp=mark_tp, mark_sl=mark_sl, exit_px=exit_px)
                self._pos = None
            else:
                log(
                    "hold_directional",
                    mark_tp=mark_tp,
                    mark_sl=mark_sl,
                    R=(mark_tp - pos.entry_px) / max(pos.entry_px, 1e-12),
                )
            return out

        if self._trades_today[session_d] >= int(cfg.max_trades_per_day):
            log("skip_max_trades_day")
            return out

        fc_s = str(getattr(cfg, "strangle_force_close_hhmm_et", "12:00") or "12:00")
        if is_at_or_after_et_hhmm(ny, fc_s):
            log("skip_directional_past_force_close", ny=ny.isoformat(), force_close_et=fc_s)
            return out

        start_s = str(getattr(cfg, "strangle_entry_start_hhmm_et", "09:35") or "09:35")
        end_s = str(getattr(cfg, "strangle_entry_end_hhmm_et", "10:00") or "10:00")
        if not is_within_et_hhmm_interval(ny, start_s, end_s):
            log("skip_directional_entry_window", ny=ny.isoformat(), start=start_s, end=end_s)
            return out

        if prev_close is None or prev_close <= 0:
            log("skip_directional_no_prev_close")
            return out

        und = _underlying_px_strangle(bar, str(getattr(cfg, "strangle_underlying_field", "low") or "low"))
        denom_prev_close = max(float(prev_close), 1e-12)
        chg = (und - float(prev_close)) / denom_prev_close
        down_thr = float(getattr(cfg, "directional_down_pct", 0.01))
        up_thr = float(getattr(cfg, "directional_up_pct", 0.01))
        qty = int(cfg.initial_option_contracts)

        if chg <= -down_thr:
            k = select_strike(und, "call", strike_step=cfg.strike_step, otm_steps=int(cfg.call_strikes_otm))
            theo = synthetic_option_price_at_bar(
                all_bars, index, strike=k, right="call", session_date=session_d, cfg=cfg, tz_name=tz
            )
            entry = theo * (1.0 + slip)
            self._pos = OpenPosition(
                side="long_call",
                strike=k,
                entry_bar_index=index,
                entry_time=ny,
                entry_px=entry,
                contracts=qty,
            )
            self._trades_today[session_d] += 1
            out.intents.append(
                TradeIntent(
                    kind=TradeIntentKind.BUY_CALL,
                    underlying=cfg.symbol,
                    strike=k,
                    right="call",
                    contracts=qty,
                    reason="morning_directional_call",
                )
            )
            out.entry_snapshot = {
                "side": "long_call",
                "strike": k,
                "entry_px": entry,
                "theoretical": theo,
                "contracts": qty,
                "und_px": und,
                "prev_close": float(prev_close),
                "chg_from_prev_close": chg,
            }
            log("enter_directional_call", strike=k, entry_px=entry, chg=chg, down_thr=-down_thr)
            return out

        if chg >= up_thr:
            k = select_strike(und, "put", strike_step=cfg.strike_step, otm_steps=int(cfg.put_strikes_otm))
            theo = synthetic_option_price_at_bar(
                all_bars, index, strike=k, right="put", session_date=session_d, cfg=cfg, tz_name=tz
            )
            entry = theo * (1.0 + slip)
            self._pos = OpenPosition(
                side="long_put",
                strike=k,
                entry_bar_index=index,
                entry_time=ny,
                entry_px=entry,
                contracts=qty,
            )
            self._trades_today[session_d] += 1
            out.intents.append(
                TradeIntent(
                    kind=TradeIntentKind.BUY_PUT,
                    underlying=cfg.symbol,
                    strike=k,
                    right="put",
                    contracts=qty,
                    reason="morning_directional_put",
                )
            )
            out.entry_snapshot = {
                "side": "long_put",
                "strike": k,
                "entry_px": entry,
                "theoretical": theo,
                "contracts": qty,
                "und_px": und,
                "prev_close": float(prev_close),
                "chg_from_prev_close": chg,
            }
            log("enter_directional_put", strike=k, entry_px=entry, chg=chg, up_thr=up_thr)
            return out

        log("skip_directional_threshold", chg=chg, down_trigger=-down_thr, up_trigger=up_thr)
        return out

    def _process_morning_strangle_bar(self, index: int, all_bars: Sequence[Bar]) -> BarProcessResult:
        cfg = self.cfg
        tz = cfg.assume_bars_timezone
        out = BarProcessResult()
        bar = all_bars[index]
        session_d = ny_date(bar.date, tz)

        if self._by_date is None:
            self.prepare(all_bars)

        assert self._by_date is not None and self._sorted_dates is not None
        prior_d = prior_trading_date_with_data(self._sorted_dates, session_d)
        prior_day_bars: list[Bar] = self._by_date.get(prior_d, []) if prior_d else []
        today_all: list[Bar] = self._by_date.get(session_d, [])
        today_sofar = [b for b in today_all if b.date <= bar.date]

        levels = build_key_levels(
            session_date=session_d,
            prior_day_bars=prior_day_bars,
            today_bars_before_now=today_sofar,
            cfg=cfg,
            tz_name=tz,
            spot_for_psych=float(bar.close),
        )
        prev_close = levels.prev_close

        def log(msg: str, **extra: Any) -> None:
            if cfg.log_decisions:
                out.logs.append(
                    DecisionLogEntry(
                        bar_index=index,
                        as_of=to_ny(bar.date, tz).isoformat(),
                        message=msg,
                        extra=extra,
                    )
                )

        if not is_within_rth(bar.date, cfg, tz):
            log("skip_not_rth")
            return out

        ny = to_ny(bar.date, tz)
        slip = float(cfg.option_slippage_pct)

        if self._pos is not None:
            if self._pos.side != "strangle":
                log("strangle_mode_unexpected_position", side=self._pos.side)
                return out
            pos = self._pos
            call_active = bool(getattr(pos, "strangle_call_active", True))
            put_active = bool(getattr(pos, "strangle_put_active", True))
            mark_c_syn = synthetic_option_price_at_bar(
                all_bars, index, strike=pos.call_strike, right="call", session_date=session_d, cfg=cfg, tz_name=tz
            )
            mark_p_syn = synthetic_option_price_at_bar(
                all_bars, index, strike=pos.put_strike, right="put", session_date=session_d, cfg=cfg, tz_name=tz
            )
            live_b = getattr(self, "strangle_live_leg_bids", None)
            live_l = getattr(self, "strangle_live_leg_lasts", None)
            applied_c_b = applied_c_l = applied_p_b = applied_p_l = False
            raw_c_tp = float(mark_c_syn)
            raw_p_tp = float(mark_p_syn)
            raw_c_sl = 0.0
            raw_p_sl = 0.0
            if isinstance(live_b, (tuple, list)) and len(live_b) >= 2:
                b_c, b_p = live_b[0], live_b[1]
                if call_active and b_c is not None and float(b_c) > 0:
                    raw_c_tp = float(b_c)
                    applied_c_b = True
                if put_active and b_p is not None and float(b_p) > 0:
                    raw_p_tp = float(b_p)
                    applied_p_b = True
            if isinstance(live_l, (tuple, list)) and len(live_l) >= 2:
                l_c, l_p = live_l[0], live_l[1]
                if call_active and l_c is not None and float(l_c) > 0:
                    raw_c_sl = float(l_c)
                    applied_c_l = True
                if put_active and l_p is not None and float(l_p) > 0:
                    raw_p_sl = float(l_p)
                    applied_p_l = True
            call_tp = max(0.0, float(raw_c_tp) if applied_c_b else float(raw_c_tp) * (1.0 - slip))
            put_tp = max(0.0, float(raw_p_tp) if applied_p_b else float(raw_p_tp) * (1.0 - slip))
            call_sl = max(0.0, float(raw_c_sl)) if applied_c_l else 0.0
            put_sl = max(0.0, float(raw_p_sl)) if applied_p_l else 0.0
            reason, detail, leg_close = evaluate_strangle_exit(pos, call_tp, put_tp, call_sl, put_sl, ny, cfg, tz)
            if reason != "hold":
                out.close_position = True
                out.close_reason = f"{reason}:{detail}"
                if leg_close == "none":
                    close_call = bool(getattr(pos, "strangle_call_active", True))
                    close_put = bool(getattr(pos, "strangle_put_active", True))
                elif leg_close == "call":
                    close_call, close_put = True, False
                else:
                    close_call, close_put = False, True
                out.exit_snapshot = {
                    "side": "strangle",
                    "call_strike": pos.call_strike,
                    "put_strike": pos.put_strike,
                    "entry_px": pos.entry_px,
                    "call_entry_px": pos.call_entry_px,
                    "put_entry_px": pos.put_entry_px,
                    "strangle_original_entry_px": _strangle_original_cost(pos),
                    "strangle_realized_exit_px": float(getattr(pos, "strangle_realized_exit_px", 0.0) or 0.0),
                    "exit_px": (call_tp if close_call else 0.0) + (put_tp if close_put else 0.0),
                    "call_exit_px": call_tp if close_call else 0.0,
                    "put_exit_px": put_tp if close_put else 0.0,
                    "contracts": pos.contracts,
                    "mark_call_mid": mark_c_syn,
                    "mark_put_mid": mark_p_syn,
                    "mark_call_tp": call_tp,
                    "mark_put_tp": put_tp,
                    "mark_call_sl": call_sl,
                    "mark_put_sl": put_sl,
                    "close_call": close_call,
                    "close_put": close_put,
                    "strangle_partial_leg": leg_close,
                }
                log(
                    "exit",
                    reason=reason,
                    detail=detail,
                    call_tp=call_tp,
                    put_tp=put_tp,
                    call_sl=call_sl,
                    put_sl=put_sl,
                    R=_strangle_combo_r(pos, call_tp, put_tp),
                    strangle_partial_leg=leg_close,
                )
                if leg_close == "none":
                    self._pos = None
            else:
                log(
                    "hold_strangle",
                    call_tp=call_tp,
                    put_tp=put_tp,
                    call_sl=call_sl,
                    put_sl=put_sl,
                    R=_strangle_combo_r(pos, call_tp, put_tp),
                )
            return out

        if self._trades_today[session_d] >= int(cfg.max_trades_per_day):
            log("skip_max_trades_day")
            return out

        fc_s = str(getattr(cfg, "strangle_force_close_hhmm_et", "12:00") or "12:00")
        if is_at_or_after_et_hhmm(ny, fc_s):
            log("skip_strangle_past_force_close", ny=ny.isoformat(), force_close_et=fc_s)
            return out

        start_s = str(getattr(cfg, "strangle_entry_start_hhmm_et", "09:35") or "09:35")
        end_s = str(getattr(cfg, "strangle_entry_end_hhmm_et", "10:00") or "10:00")
        if not is_within_et_hhmm_interval(ny, start_s, end_s):
            log("skip_strangle_entry_window", ny=ny.isoformat(), start=start_s, end=end_s)
            return out

        if prev_close is None or prev_close <= 0:
            log("skip_strangle_no_prev_close")
            return out

        und = _underlying_px_strangle(bar, str(getattr(cfg, "strangle_underlying_field", "low") or "low"))
        denom_prev_close = max(float(prev_close), 1e-12)
        chg = (und - float(prev_close)) / denom_prev_close
        rng = float(getattr(cfg, "strangle_range_pct", 0.003))
        if abs(chg) > rng:
            log("skip_strangle_range", chg=chg, max_abs=rng, und=und, prev_close=prev_close)
            return out

        kc = select_strike(und, "call", strike_step=cfg.strike_step, otm_steps=int(cfg.call_strikes_otm))
        kp = select_strike(und, "put", strike_step=cfg.strike_step, otm_steps=int(cfg.put_strikes_otm))
        theo_c = synthetic_option_price_at_bar(
            all_bars, index, strike=kc, right="call", session_date=session_d, cfg=cfg, tz_name=tz
        )
        theo_p = synthetic_option_price_at_bar(
            all_bars, index, strike=kp, right="put", session_date=session_d, cfg=cfg, tz_name=tz
        )
        call_entry = theo_c * (1.0 + slip)
        put_entry = theo_p * (1.0 + slip)
        cost = call_entry + put_entry
        qty = int(cfg.initial_option_contracts)
        self._pos = OpenPosition(
            side="strangle",
            strike=0.0,
            call_strike=kc,
            put_strike=kp,
            entry_bar_index=index,
            entry_time=ny,
            entry_px=cost,
            call_entry_px=call_entry,
            put_entry_px=put_entry,
            strangle_original_entry_px=cost,
            contracts=qty,
            call_strikes_otm=int(cfg.call_strikes_otm),
            put_strikes_otm=int(cfg.put_strikes_otm),
        )
        self._trades_today[session_d] += 1
        out.intents.append(
            TradeIntent(
                kind=TradeIntentKind.BUY_CALL,
                underlying=cfg.symbol,
                strike=kc,
                right="call",
                contracts=qty,
                reason="morning_strangle",
            )
        )
        out.intents.append(
            TradeIntent(
                kind=TradeIntentKind.BUY_PUT,
                underlying=cfg.symbol,
                strike=kp,
                right="put",
                contracts=qty,
                reason="morning_strangle",
            )
        )
        out.entry_snapshot = {
            "side": "strangle",
            "call_strike": kc,
            "put_strike": kp,
            "entry_px": cost,
            "call_entry_px": call_entry,
            "put_entry_px": put_entry,
            "theoretical_call": theo_c,
            "theoretical_put": theo_p,
            "contracts": qty,
            "und_px": und,
            "prev_close": float(prev_close),
            "chg_from_prev_close": chg,
        }
        log("enter_strangle", call_strike=kc, put_strike=kp, cost=cost, und=und, chg=chg)
        return out

    def _process_morning_double_strangle_bar(self, index: int, all_bars: Sequence[Bar]) -> BarProcessResult:
        cfg = self.cfg
        tz = cfg.assume_bars_timezone
        out = BarProcessResult()
        bar = all_bars[index]
        session_d = ny_date(bar.date, tz)

        if self._by_date is None:
            self.prepare(all_bars)

        assert self._by_date is not None and self._sorted_dates is not None
        prior_d = prior_trading_date_with_data(self._sorted_dates, session_d)
        prior_day_bars: list[Bar] = self._by_date.get(prior_d, []) if prior_d else []
        today_all: list[Bar] = self._by_date.get(session_d, [])
        today_sofar = [b for b in today_all if b.date <= bar.date]

        levels = build_key_levels(
            session_date=session_d,
            prior_day_bars=prior_day_bars,
            today_bars_before_now=today_sofar,
            cfg=cfg,
            tz_name=tz,
            spot_for_psych=float(bar.close),
        )
        prev_close = levels.prev_close

        def log(msg: str, **extra: Any) -> None:
            if cfg.log_decisions:
                out.logs.append(
                    DecisionLogEntry(
                        bar_index=index,
                        as_of=to_ny(bar.date, tz).isoformat(),
                        message=msg,
                        extra=extra,
                    )
                )

        if not is_within_rth(bar.date, cfg, tz):
            log("skip_not_rth")
            return out

        ny = to_ny(bar.date, tz)
        slip = float(cfg.option_slippage_pct)

        if self._pos is not None:
            if self._pos.side != "double_strangle":
                log("double_strangle_mode_unexpected_position", side=self._pos.side)
                return out
            pos = self._pos
            active = _double_strangle_active_legs(pos)
            live_b = getattr(self, "double_strangle_live_leg_bids", None)
            live_l = getattr(self, "double_strangle_live_leg_lasts", None)
            if not isinstance(live_b, dict):
                live_b = {}
            if not isinstance(live_l, dict):
                live_l = {}
            leg_mid: dict[str, float] = {}
            leg_tp: dict[str, float] = {}
            leg_sl: dict[str, float] = {}
            live_applied: dict[str, dict[str, bool]] = {}
            for key, leg in active.items():
                strike = float(leg.get("strike") or 0.0)
                right = str(leg.get("right") or "")
                mark_syn = synthetic_option_price_at_bar(
                    all_bars, index, strike=strike, right=right, session_date=session_d, cfg=cfg, tz_name=tz
                )
                leg_mid[key] = float(mark_syn)
                b = live_b.get(key)
                l = live_l.get(key)
                b_ok = b is not None and float(b) > 0
                l_ok = l is not None and float(l) > 0
                leg_tp[key] = max(0.0, float(b) if b_ok else float(mark_syn) * (1.0 - slip))
                leg_sl[key] = max(0.0, float(l) if l_ok else 0.0)
                live_applied[key] = {"bid": bool(b_ok), "last": bool(l_ok)}

            reason, detail, leg_close = evaluate_double_strangle_exit(pos, leg_tp, leg_sl, ny, cfg, tz)
            if reason != "hold":
                out.close_position = True
                out.close_reason = f"{reason}:{detail}"
                close_keys = list(active.keys()) if leg_close == "none" else [leg_close]
                leg_exit_px = {key: float(leg_tp.get(key) or 0.0) for key in close_keys}
                out.exit_snapshot = {
                    "side": "double_strangle",
                    "contracts": pos.contracts,
                    "entry_px": pos.entry_px,
                    "strangle_original_entry_px": _double_strangle_original_cost(pos),
                    "strangle_realized_exit_px": float(getattr(pos, "strangle_realized_exit_px", 0.0) or 0.0),
                    "double_strangle_legs": pos.double_strangle_legs,
                    "close_leg_keys": close_keys,
                    "leg_exit_px": leg_exit_px,
                    "exit_px": sum(leg_exit_px.values()),
                    "mark_leg_mid": leg_mid,
                    "mark_leg_tp": leg_tp,
                    "mark_leg_sl": leg_sl,
                    "strangle_partial_leg": leg_close,
                }
                log(
                    "exit_double_strangle",
                    reason=reason,
                    detail=detail,
                    close_leg_keys=close_keys,
                    leg_tp=leg_tp,
                    leg_sl=leg_sl,
                    live_applied=live_applied,
                    R=_double_strangle_combo_r(pos, leg_tp),
                )
                if leg_close == "none":
                    self._pos = None
            else:
                log(
                    "hold_double_strangle",
                    leg_tp=leg_tp,
                    leg_sl=leg_sl,
                    live_applied=live_applied,
                    R=_double_strangle_combo_r(pos, leg_tp),
                )
            return out

        if self._trades_today[session_d] >= int(cfg.max_trades_per_day):
            log("skip_max_trades_day")
            return out

        fc_s = str(getattr(cfg, "strangle_force_close_hhmm_et", "12:00") or "12:00")
        if is_at_or_after_et_hhmm(ny, fc_s):
            log("skip_double_strangle_past_force_close", ny=ny.isoformat(), force_close_et=fc_s)
            return out

        start_s = str(getattr(cfg, "strangle_entry_start_hhmm_et", "09:35") or "09:35")
        end_s = str(getattr(cfg, "strangle_entry_end_hhmm_et", "10:00") or "10:00")
        if not is_within_et_hhmm_interval(ny, start_s, end_s):
            log("skip_double_strangle_entry_window", ny=ny.isoformat(), start=start_s, end=end_s)
            return out

        if prev_close is None or prev_close <= 0:
            log("skip_double_strangle_no_prev_close")
            return out

        und = _underlying_px_strangle(bar, str(getattr(cfg, "strangle_underlying_field", "low") or "low"))
        denom_prev_close = max(float(prev_close), 1e-12)
        chg = (und - float(prev_close)) / denom_prev_close
        rng = float(getattr(cfg, "strangle_range_pct", 0.003))
        if abs(chg) > rng:
            log("skip_double_strangle_range", chg=chg, max_abs=rng, und=und, prev_close=prev_close)
            return out

        call_long_steps = int(getattr(cfg, "double_strangle_call_long_strikes_otm", 2) or 0)
        call_short_steps = int(getattr(cfg, "double_strangle_call_short_strikes_otm", 1) or 0)
        put_long_steps = int(getattr(cfg, "double_strangle_put_long_strikes_otm", 2) or 0)
        put_short_steps = int(getattr(cfg, "double_strangle_put_short_strikes_otm", 1) or 0)
        if call_long_steps <= call_short_steps or put_long_steps <= put_short_steps:
            log(
                "skip_double_strangle_invalid_steps",
                call_long=call_long_steps,
                call_short=call_short_steps,
                put_long=put_long_steps,
                put_short=put_short_steps,
            )
            return out

        specs = [
            ("call_long", "call", call_long_steps),
            ("call_short", "call", call_short_steps),
            ("put_long", "put", put_long_steps),
            ("put_short", "put", put_short_steps),
        ]
        legs: dict[str, dict[str, Any]] = {}
        for key, right, steps in specs:
            strike = select_strike(und, right, strike_step=cfg.strike_step, otm_steps=steps)
            theo = synthetic_option_price_at_bar(
                all_bars, index, strike=strike, right=right, session_date=session_d, cfg=cfg, tz_name=tz
            )
            entry = float(theo) * (1.0 + slip)
            legs[key] = {
                "right": right,
                "strike": float(strike),
                "entry_px": float(entry),
                "theoretical": float(theo),
                "active": True,
                "strikes_otm": int(steps),
            }
        cost = sum(float(x.get("entry_px") or 0.0) for x in legs.values())
        max_debit = float(getattr(cfg, "double_strangle_max_total_debit", 0.0) or 0.0)
        if max_debit > 0 and cost > max_debit:
            log("skip_double_strangle_max_debit", cost=cost, max_debit=max_debit)
            return out

        qty = int(cfg.initial_option_contracts)
        call_entry = float(legs["call_long"]["entry_px"]) + float(legs["call_short"]["entry_px"])
        put_entry = float(legs["put_long"]["entry_px"]) + float(legs["put_short"]["entry_px"])
        self._pos = OpenPosition(
            side="double_strangle",
            strike=0.0,
            call_strike=float(legs["call_short"]["strike"]),
            put_strike=float(legs["put_short"]["strike"]),
            entry_bar_index=index,
            entry_time=ny,
            entry_px=cost,
            call_entry_px=call_entry,
            put_entry_px=put_entry,
            strangle_original_entry_px=cost,
            contracts=qty,
            call_strikes_otm=call_short_steps,
            put_strikes_otm=put_short_steps,
            double_strangle_legs=legs,
        )
        self._trades_today[session_d] += 1
        for key, leg in legs.items():
            out.intents.append(
                TradeIntent(
                    kind=TradeIntentKind.BUY_CALL if str(leg.get("right")) == "call" else TradeIntentKind.BUY_PUT,
                    underlying=cfg.symbol,
                    strike=float(leg.get("strike") or 0.0),
                    right=str(leg.get("right") or "call"),  # type: ignore[arg-type]
                    contracts=qty,
                    reason="morning_double_strangle",
                    leg_key=key,
                )
            )
        out.entry_snapshot = {
            "side": "double_strangle",
            "entry_px": cost,
            "call_entry_px": call_entry,
            "put_entry_px": put_entry,
            "contracts": qty,
            "double_strangle_legs": legs,
            "und_px": und,
            "prev_close": float(prev_close),
            "chg_from_prev_close": chg,
        }
        log("enter_double_strangle", cost=cost, legs=legs, und=und, chg=chg)
        return out

    def _process_reaction_zone_bar(self, index: int, all_bars: Sequence[Bar]) -> BarProcessResult:
        cfg = self.cfg
        tz = cfg.assume_bars_timezone
        out = BarProcessResult()
        bar = all_bars[index]
        session_d = ny_date(bar.date, tz)

        if self._by_date is None:
            self.prepare(all_bars)

        assert self._by_date is not None and self._sorted_dates is not None
        prior_d = prior_trading_date_with_data(self._sorted_dates, session_d)
        prior_day_bars: list[Bar] = self._by_date.get(prior_d, []) if prior_d else []
        today_all: list[Bar] = self._by_date.get(session_d, [])
        today_sofar = [b for b in today_all if b.date <= bar.date]

        spot = float(bar.close)
        levels = build_key_levels(
            session_date=session_d,
            prior_day_bars=prior_day_bars,
            today_bars_before_now=today_sofar,
            cfg=cfg,
            tz_name=tz,
            spot_for_psych=spot,
        )
        level_prices = collect_level_prices(levels)
        zones = build_zones_from_levels(level_prices, float(cfg.reaction_zone_half_width_pct))
        zone = find_active_zone(spot, zones)

        open_px = _first_rth_open_of_day(today_sofar, session_d, cfg, tz)
        if open_px is None:
            open_px = float(bar.open)
        regime = opening_regime(open_px, levels.prev_close, cfg)

        def log(msg: str, **extra: Any) -> None:
            if cfg.log_decisions:
                out.logs.append(
                    DecisionLogEntry(
                        bar_index=index,
                        as_of=to_ny(bar.date, tz).isoformat(),
                        message=msg,
                        extra=extra,
                    )
                )

        if not is_within_rth(bar.date, cfg, tz):
            log("skip_not_rth")
            return out

        if self._pos is not None:
            if self._pos.side not in ("long_call", "long_put"):
                log("reaction_zone_unexpected_position", side=self._pos.side)
                return out
            strike = self._pos.strike
            right = "call" if self._pos.side == "long_call" else "put"
            slip = float(cfg.option_slippage_pct)
            mark = synthetic_option_price_at_bar(
                all_bars,
                index,
                strike=strike,
                right=right,
                session_date=session_d,
                cfg=cfg,
                tz_name=tz,
            )
            lb = getattr(self, "option_live_bid", None)
            ll = getattr(self, "option_live_last", None)
            mark_tp, mark_sl, live_on = _single_leg_marks_tp_sl(mark, slip, lb, ll, synthetic_baseline="mid")
            reason, detail = (
                evaluate_exit(self._pos, mark_tp, to_ny(bar.date, tz), cfg, tz, mark_sl=mark_sl)
                if live_on
                else evaluate_exit(self._pos, mark_tp, to_ny(bar.date, tz), cfg, tz)
            )
            if reason != "hold":
                exit_px = max(0.0, mark_tp if live_on else float(mark) * (1.0 - slip))
                pos = self._pos
                out.close_position = True
                out.close_reason = f"{reason}:{detail}"
                out.exit_snapshot = {
                    "side": pos.side,
                    "strike": pos.strike,
                    "entry_px": pos.entry_px,
                    "exit_px": exit_px,
                    "contracts": pos.contracts,
                    "mark_mid": mark,
                    "mark_tp": mark_tp,
                    "mark_sl": mark_sl,
                }
                log("exit", reason=reason, detail=detail, mark_mid=mark, mark_tp=mark_tp, mark_sl=mark_sl, exit_px=exit_px)
                self._pos = None
            else:
                log("hold_position", mark_mid=mark, mark_tp=mark_tp, mark_sl=mark_sl)
            return out

        if is_no_trade_opening_window(bar.date, cfg, tz):
            log("skip_no_trade_opening")
            return out

        if is_past_new_trade_cutoff(bar.date, cfg, tz):
            co = new_trades_cutoff_datetime(session_d, cfg)
            log(
                "skip_past_new_trade_cutoff",
                cutoff_et=co.isoformat() if co is not None else None,
            )
            return out

        if self._trades_today[session_d] >= int(cfg.max_trades_per_day):
            log("skip_max_trades_day")
            return out

        if zone is None:
            log("no_reaction_zone")
            return out

        if not volume_spike_at(all_bars, index, cfg):
            log("no_volume_spike")
            return out

        m_open = minutes_since_rth_open(bar.date, cfg, tz)
        if m_open is not None and m_open >= float(cfg.restricted_opening_minutes):
            pass
        else:
            log("restricted_opening_period")
            return out

        call_confirm = breakout_confirmed(all_bars, index, zone.high, "up", cfg) or reversal_after_zone_touch(
            all_bars, index, zone, "up", cfg
        )
        put_confirm = breakout_confirmed(all_bars, index, zone.low, "down", cfg) or reversal_after_zone_touch(
            all_bars, index, zone, "down", cfg
        )

        slip = float(cfg.option_slippage_pct)

        if regime.bias_calls and call_confirm:
            k = select_strike(spot, "call", strike_step=cfg.strike_step, otm_steps=int(cfg.call_strikes_otm))
            theo = synthetic_option_price_at_bar(
                all_bars, index, strike=k, right="call", session_date=session_d, cfg=cfg, tz_name=tz
            )
            entry = theo * (1.0 + slip)
            self._pos = OpenPosition(
                side="long_call",
                strike=k,
                entry_bar_index=index,
                entry_time=to_ny(bar.date, tz),
                entry_px=entry,
                contracts=int(cfg.initial_option_contracts),
            )
            self._trades_today[session_d] += 1
            out.intents.append(
                TradeIntent(
                    kind=TradeIntentKind.BUY_CALL,
                    underlying=cfg.symbol,
                    strike=k,
                    right="call",
                    contracts=int(cfg.initial_option_contracts),
                    reason="reaction_zone_volume_confirmation_call",
                )
            )
            out.entry_snapshot = {
                "side": "long_call",
                "strike": k,
                "entry_px": entry,
                "theoretical": theo,
                "contracts": int(cfg.initial_option_contracts),
            }
            log("enter_call", strike=k, entry_px=entry, theo=theo)
            return out

        if regime.bias_puts and put_confirm:
            k = select_strike(spot, "put", strike_step=cfg.strike_step, otm_steps=int(cfg.put_strikes_otm))
            theo = synthetic_option_price_at_bar(
                all_bars, index, strike=k, right="put", session_date=session_d, cfg=cfg, tz_name=tz
            )
            entry = theo * (1.0 + slip)
            self._pos = OpenPosition(
                side="long_put",
                strike=k,
                entry_bar_index=index,
                entry_time=to_ny(bar.date, tz),
                entry_px=entry,
                contracts=int(cfg.initial_option_contracts),
            )
            self._trades_today[session_d] += 1
            out.intents.append(
                TradeIntent(
                    kind=TradeIntentKind.BUY_PUT,
                    underlying=cfg.symbol,
                    strike=k,
                    right="put",
                    contracts=int(cfg.initial_option_contracts),
                    reason="reaction_zone_volume_confirmation_put",
                )
            )
            out.entry_snapshot = {
                "side": "long_put",
                "strike": k,
                "entry_px": entry,
                "theoretical": theo,
                "contracts": int(cfg.initial_option_contracts),
            }
            log("enter_put", strike=k, entry_px=entry, theo=theo)
            return out

        log("no_directional_signal", bias_calls=regime.bias_calls, bias_puts=regime.bias_puts)
        return out
