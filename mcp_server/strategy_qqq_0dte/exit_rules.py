"""止盈、止损、最长持仓时间。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from .config import Qqq0dteConfig
from .session_us import is_at_or_after_et_hhmm, minutes_between, to_ny
from .state import OpenPosition


ExitReason = Literal["hold", "take_profit", "stop_loss", "time_exit"]
StrangleLegToClose = Literal["none", "call", "put"]
DoubleStrangleLegToClose = Literal["none", "call_long", "call_short", "put_long", "put_short"]
DOUBLE_STRANGLE_LEG_KEYS = ("call_long", "call_short", "put_long", "put_short")


def _strangle_leg_take_profit_thresholds(cfg: Qqq0dteConfig, pos: OpenPosition) -> tuple[float, float]:
    legacy = float(getattr(cfg, "strangle_leg_take_profit_pct", 0.0) or 0.0)
    long_tp = float(getattr(cfg, "strangle_long_leg_take_profit_pct", 0.0) or 0.0)
    short_tp = float(getattr(cfg, "strangle_short_leg_take_profit_pct", 0.0) or 0.0)
    if long_tp <= 0:
        long_tp = legacy
    if short_tp <= 0:
        short_tp = legacy
    call_steps = int(getattr(pos, "call_strikes_otm", getattr(cfg, "call_strikes_otm", 0)) or 0)
    put_steps = int(getattr(pos, "put_strikes_otm", getattr(cfg, "put_strikes_otm", 0)) or 0)
    if call_steps > put_steps:
        return long_tp, short_tp
    if call_steps < put_steps:
        return short_tp, long_tp
    return short_tp, short_tp


def evaluate_exit(
    pos: OpenPosition,
    mark_px: float,
    now: datetime,
    cfg: Qqq0dteConfig,
    tz_name: str,
    *,
    mark_sl: float | None = None,
) -> tuple[ExitReason, str]:
    """mark_px：止盈盯市（实盘建议买一 bid）；mark_sl：止损盯市（建议传 last，None 表示本轮不做止损判定）。"""
    m_tp = float(mark_px)
    m_sl = None if mark_sl is None else float(mark_sl)
    if m_tp <= 0:
        return "hold", "invalid_mark"
    pnl_tp = (m_tp - pos.entry_px) / max(pos.entry_px, 1e-12)
    if pnl_tp >= float(cfg.take_profit_pct):
        return "take_profit", f"pnl_pct={pnl_tp:.4f}>={cfg.take_profit_pct}"
    if m_sl is not None and m_sl > 0:
        pnl_sl = (m_sl - pos.entry_px) / max(pos.entry_px, 1e-12)
        if pnl_sl <= -float(cfg.stop_loss_pct):
            return "stop_loss", f"pnl_pct={pnl_sl:.4f}<=-{cfg.stop_loss_pct}"
    held = minutes_between(pos.entry_time, now, tz_name)
    if held >= float(cfg.max_hold_minutes):
        return "time_exit", f"held_min={held:.1f}>={cfg.max_hold_minutes}"
    return "hold", ""


def evaluate_strangle_exit(
    pos: OpenPosition,
    call_mark_tp: float,
    put_mark_tp: float,
    call_mark_sl: float,
    put_mark_sl: float,
    now: datetime,
    cfg: Qqq0dteConfig,
    tz_name: str,
) -> tuple[ExitReason, str, StrangleLegToClose]:
    """
    宽跨平仓判断（返回第三项表示是否仅平一条腿）：
    - 单腿止损：由 strangle_leg_stop_loss_pct 控制（>0 启用），不区分长短腿；
    - 单腿止盈：由 strangle_long_leg_take_profit_pct / strangle_short_leg_take_profit_pct 控制；
      旧字段 strangle_leg_take_profit_pct 仅作为兼容默认值；
    - 组合止损/止盈：已实现平仓金额 + 剩余腿权利金合计，相对原始两腿成本；
    - 强平时刻 strangle_force_close_hhmm_et：平掉所有仍持仓的腿（第三项为 none）。

    盯市：止盈用 call_mark_tp/put_mark_tp（实盘为买一 bid），止损用 call_mark_sl/put_mark_sl（实盘为 last）。
    回测若未区分，可将四者传为同一合成价。
    """
    call_active = bool(getattr(pos, "strangle_call_active", True))
    put_active = bool(getattr(pos, "strangle_put_active", True))
    call_e = float(pos.call_entry_px) if call_active else 0.0
    put_e = float(pos.put_entry_px) if put_active else 0.0
    cb_tp = max(0.0, float(call_mark_tp))
    pb_tp = max(0.0, float(put_mark_tp))
    cb_sl = max(0.0, float(call_mark_sl))
    pb_sl = max(0.0, float(put_mark_sl))
    call_leg_tp, put_leg_tp = _strangle_leg_take_profit_thresholds(cfg, pos)
    leg_sl = float(getattr(cfg, "strangle_leg_stop_loss_pct", 0.0) or 0.0)

    # 1) 单腿止损（先处理风险）
    if call_active and call_e > 0 and leg_sl > 0 and cb_sl > 0:
        rpc = (cb_sl - call_e) / max(call_e, 1e-12)
        if rpc <= -leg_sl:
            return "stop_loss", f"strangle_call_leg_sl={rpc:.4f}<=-{leg_sl}", "call"
    if put_active and put_e > 0 and leg_sl > 0 and pb_sl > 0:
        rpp = (pb_sl - put_e) / max(put_e, 1e-12)
        if rpp <= -leg_sl:
            return "stop_loss", f"strangle_put_leg_sl={rpp:.4f}<=-{leg_sl}", "put"

    # 2) 单腿止盈
    if call_active and call_e > 0 and call_leg_tp > 0:
        rpc = (cb_tp - call_e) / max(call_e, 1e-12)
        if rpc >= call_leg_tp:
            return "take_profit", f"strangle_call_leg_tp={rpc:.4f}>={call_leg_tp}", "call"
    if put_active and put_e > 0 and put_leg_tp > 0:
        rpp = (pb_tp - put_e) / max(put_e, 1e-12)
        if rpp >= put_leg_tp:
            return "take_profit", f"strangle_put_leg_tp={rpp:.4f}>={put_leg_tp}", "put"

    # 3) 组合止损/止盈（仅统计仍持仓的腿）
    remaining_cost = call_e + put_e
    original_cost = float(getattr(pos, "strangle_original_entry_px", 0.0) or 0.0)
    if original_cost <= 0:
        original_cost = remaining_cost
    if original_cost <= 0:
        return "hold", "invalid_cost", "none"
    realized = max(0.0, float(getattr(pos, "strangle_realized_exit_px", 0.0) or 0.0))

    # 组合止损：使用 last 口径（cb_sl/pb_sl）计算，避免 bid 瞬时折价导致秒止损。
    v_sl = realized + (cb_sl if call_active else 0.0) + (pb_sl if put_active else 0.0)
    r_sl = (v_sl - original_cost) / max(original_cost, 1e-12)
    sl = float(getattr(cfg, "strangle_stop_loss_return", 0.0) or 0.0)
    cooldown_min = max(0.0, float(getattr(cfg, "strangle_stop_loss_cooldown_minutes", 0) or 0))
    if sl > 0:
        held = minutes_between(pos.entry_time, now, tz_name)
        if held >= cooldown_min and r_sl <= -sl:
            return "stop_loss", f"strangle_R={r_sl:.4f}<=-{sl}", "none"

    # 组合止盈：保持原口径，使用可成交性更强的 tp 盯市（bid 优先）。
    v = realized + (cb_tp if call_active else 0.0) + (pb_tp if put_active else 0.0)
    r = (v - original_cost) / max(original_cost, 1e-12)
    tp = float(getattr(cfg, "strangle_take_profit_return", 1.0))
    if r >= tp:
        return "take_profit", f"strangle_R={r:.4f}>={tp}", "none"

    # 4) 强平：平掉所有仍持仓腿
    ny = to_ny(now, tz_name)
    fc = str(getattr(cfg, "strangle_force_close_hhmm_et", "12:00") or "12:00")
    if is_at_or_after_et_hhmm(ny, fc):
        return "time_exit", f"strangle_force_close_et={fc}", "none"

    return "hold", "", "none"


def _double_strangle_active_legs(pos: OpenPosition) -> dict[str, dict]:
    legs = getattr(pos, "double_strangle_legs", None)
    if not isinstance(legs, dict):
        return {}
    out: dict[str, dict] = {}
    for key in DOUBLE_STRANGLE_LEG_KEYS:
        leg = legs.get(key)
        if isinstance(leg, dict) and bool(leg.get("active", True)):
            out[key] = leg
    return out


def _double_strangle_leg_tp_threshold(cfg: Qqq0dteConfig, key: str) -> float:
    fallback_short = float(getattr(cfg, "strangle_short_leg_take_profit_pct", 0.0) or 0.0)
    fallback_long = float(getattr(cfg, "strangle_long_leg_take_profit_pct", 0.0) or fallback_short or 0.0)
    if key == "call_long":
        return float(getattr(cfg, "double_strangle_call_long_leg_take_profit_pct", fallback_long) or 0.0)
    if key == "call_short":
        return float(getattr(cfg, "double_strangle_call_short_leg_take_profit_pct", fallback_short) or 0.0)
    if key == "put_long":
        return float(getattr(cfg, "double_strangle_put_long_leg_take_profit_pct", fallback_long) or 0.0)
    if key == "put_short":
        return float(getattr(cfg, "double_strangle_put_short_leg_take_profit_pct", fallback_short) or 0.0)
    return 0.0


def evaluate_double_strangle_exit(
    pos: OpenPosition,
    leg_marks_tp: dict[str, float],
    leg_marks_sl: dict[str, float],
    now: datetime,
    cfg: Qqq0dteConfig,
    tz_name: str,
) -> tuple[ExitReason, str, DoubleStrangleLegToClose]:
    """
    Four-leg morning double strangle exit rules:
    - Single-leg stop loss uses one shared threshold.
    - Single-leg take profit uses per-leg thresholds.
    - Combo stop/take-profit uses realized proceeds plus remaining active legs.
    - Force close closes all active legs.
    """
    active = _double_strangle_active_legs(pos)
    if not active:
        return "hold", "invalid_legs", "none"

    leg_sl = float(
        getattr(cfg, "double_strangle_single_leg_stop_loss_pct", getattr(cfg, "strangle_leg_stop_loss_pct", 0.0)) or 0.0
    )
    for key in DOUBLE_STRANGLE_LEG_KEYS:
        leg = active.get(key)
        if not isinstance(leg, dict):
            continue
        entry = float(leg.get("entry_px") or 0.0)
        mark = max(0.0, float(leg_marks_sl.get(key) or 0.0))
        if entry > 0 and leg_sl > 0 and mark > 0:
            r = (mark - entry) / max(entry, 1e-12)
            if r <= -leg_sl:
                return "stop_loss", f"double_strangle_{key}_sl={r:.4f}<=-{leg_sl}", key  # type: ignore[return-value]

    for key in DOUBLE_STRANGLE_LEG_KEYS:
        leg = active.get(key)
        if not isinstance(leg, dict):
            continue
        entry = float(leg.get("entry_px") or 0.0)
        mark = max(0.0, float(leg_marks_tp.get(key) or 0.0))
        tp = _double_strangle_leg_tp_threshold(cfg, key)
        if entry > 0 and tp > 0:
            r = (mark - entry) / max(entry, 1e-12)
            if r >= tp:
                return "take_profit", f"double_strangle_{key}_tp={r:.4f}>={tp}", key  # type: ignore[return-value]

    original_cost = float(getattr(pos, "strangle_original_entry_px", 0.0) or 0.0)
    if original_cost <= 0:
        original_cost = sum(float(x.get("entry_px") or 0.0) for x in active.values())
    if original_cost <= 0:
        return "hold", "invalid_cost", "none"
    realized = max(0.0, float(getattr(pos, "strangle_realized_exit_px", 0.0) or 0.0))

    v_sl = realized + sum(max(0.0, float(leg_marks_sl.get(k) or 0.0)) for k in active)
    r_sl = (v_sl - original_cost) / max(original_cost, 1e-12)
    combo_sl = float(
        getattr(cfg, "double_strangle_combo_stop_loss_pct", getattr(cfg, "strangle_stop_loss_return", 0.0)) or 0.0
    )
    cooldown_min = max(0.0, float(getattr(cfg, "strangle_stop_loss_cooldown_minutes", 0) or 0))
    if combo_sl > 0:
        held = minutes_between(pos.entry_time, now, tz_name)
        if held >= cooldown_min and r_sl <= -combo_sl:
            return "stop_loss", f"double_strangle_R={r_sl:.4f}<=-{combo_sl}", "none"

    v_tp = realized + sum(max(0.0, float(leg_marks_tp.get(k) or 0.0)) for k in active)
    r_tp = (v_tp - original_cost) / max(original_cost, 1e-12)
    combo_tp = float(
        getattr(cfg, "double_strangle_combo_take_profit_pct", getattr(cfg, "strangle_take_profit_return", 1.0)) or 0.0
    )
    if combo_tp > 0 and r_tp >= combo_tp:
        return "take_profit", f"double_strangle_R={r_tp:.4f}>={combo_tp}", "none"

    ny = to_ny(now, tz_name)
    fc = str(getattr(cfg, "strangle_force_close_hhmm_et", "12:00") or "12:00")
    if is_at_or_after_et_hhmm(ny, fc):
        return "time_exit", f"double_strangle_force_close_et={fc}", "none"

    return "hold", "", "none"


def evaluate_morning_directional_exit(
    pos: OpenPosition,
    mark_tp: float,
    now: datetime,
    cfg: Qqq0dteConfig,
    tz_name: str,
    *,
    mark_sl: float | None = None,
) -> tuple[ExitReason, str]:
    """
    单腿：C=entry_px（含开仓滑点近似）；止盈用 mark_tp（bid 优先），止损用 mark_sl（None 时跳过止损）。
    directional_stop_loss_pct>0 时先判止损；再判止盈；强平与 strangle_force_close_hhmm_et 共用。
    """
    c = float(pos.entry_px)
    if c <= 0:
        return "hold", "invalid_cost"
    m_tp = max(0.0, float(mark_tp))
    m_sl = None if mark_sl is None else max(0.0, float(mark_sl))
    sl_pct = float(getattr(cfg, "directional_stop_loss_pct", 0.0) or 0.0)
    if sl_pct > 0 and m_sl is not None and m_sl > 0:
        r_sl = (m_sl - c) / max(c, 1e-12)
        if r_sl <= -sl_pct:
            return "stop_loss", f"directional_sl={r_sl:.4f}<=-{sl_pct}"
    r_tp = (m_tp - c) / max(c, 1e-12)
    tp = float(getattr(cfg, "directional_take_profit_return", 1.0))
    if r_tp >= tp:
        return "take_profit", f"directional_R={r_tp:.4f}>={tp}"
    ny = to_ny(now, tz_name)
    fc = str(getattr(cfg, "strangle_force_close_hhmm_et", "12:00") or "12:00")
    if is_at_or_after_et_hhmm(ny, fc):
        return "time_exit", f"directional_force_close_et={fc}"
    return "hold", ""


def evaluate_gamma_exit(
    pos: OpenPosition,
    mark_tp: float,
    now: datetime,
    cfg: Qqq0dteConfig,
    tz_name: str,
    *,
    mark_sl: float | None = None,
) -> tuple[ExitReason, str]:
    c = float(pos.entry_px)
    if c <= 0:
        return "hold", "invalid_cost"
    v_tp = max(0.0, float(mark_tp))
    v_sl = None if mark_sl is None else max(0.0, float(mark_sl))
    hard_sl = float(getattr(cfg, "gamma_hard_stop_loss_pct", 0.30))
    if v_sl is not None and v_sl > 0:
        r_sl = (v_sl - c) / max(c, 1e-12)
        if r_sl <= -hard_sl:
            return "stop_loss", f"gamma_R={r_sl:.4f}<=-{hard_sl}"
    r_tp = (v_tp - c) / max(c, 1e-12)
    tp = float(getattr(cfg, "gamma_take_profit_min_return", 0.50))
    if r_tp >= tp:
        return "take_profit", f"gamma_R={r_tp:.4f}>={tp}"
    held = minutes_between(pos.entry_time, now, tz_name)
    mh = float(getattr(cfg, "gamma_max_hold_minutes", 15))
    if held >= mh:
        return "time_exit", f"gamma_held_min={held:.1f}>={mh}"
    ny = to_ny(now, tz_name)
    fc = str(getattr(cfg, "gamma_force_close_hhmm_et", "14:00") or "14:00")
    if is_at_or_after_et_hhmm(ny, fc):
        return "time_exit", f"gamma_force_close_et={fc}"
    return "hold", ""


def evaluate_gamma_pro_exit(
    pos: OpenPosition,
    mark_tp: float,
    now: datetime,
    cfg: Qqq0dteConfig,
    tz_name: str,
    *,
    mark_sl: float | None = None,
) -> tuple[ExitReason, str]:
    c = float(pos.entry_px)
    if c <= 0:
        return "hold", "invalid_cost"
    v_tp = max(0.0, float(mark_tp))
    v_sl = None if mark_sl is None else max(0.0, float(mark_sl))
    hard_sl = float(getattr(cfg, "gamma_pro_hard_stop_loss_pct", 0.30))
    if v_sl is not None and v_sl > 0:
        r_sl = (v_sl - c) / max(c, 1e-12)
        if r_sl <= -hard_sl:
            return "stop_loss", f"gamma_pro_R={r_sl:.4f}<=-{hard_sl}"
    r_tp = (v_tp - c) / max(c, 1e-12)
    tp = float(getattr(cfg, "gamma_pro_take_profit_return", 0.60))
    if r_tp >= tp:
        return "take_profit", f"gamma_pro_R={r_tp:.4f}>={tp}"
    held = minutes_between(pos.entry_time, now, tz_name)
    mh = float(getattr(cfg, "gamma_pro_max_hold_minutes", 45))
    if held >= mh:
        return "time_exit", f"gamma_pro_held_min={held:.1f}>={mh}"
    ny = to_ny(now, tz_name)
    fc = str(getattr(cfg, "gamma_pro_force_close_hhmm_et", "15:45") or "15:45")
    if is_at_or_after_et_hhmm(ny, fc):
        return "time_exit", f"gamma_pro_force_close_et={fc}"
    return "hold", ""
