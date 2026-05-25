"""可配置参数：关键位、闸门、信号阈值、出场与回测。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Qqq0dteConfig:
    """QQQ 0DTE 策略参数（可 JSON 序列化后给 API）。"""

    symbol: str = "QQQ.US"
    # 标的 K 线时间解释：naive datetime 视为该时区（服务器 K 线缓存 JSON 多为无时区 ISO，按 UTC 墙钟存）
    assume_bars_timezone: str = "UTC"

    # 会话
    rth_open_hour: int = 9
    rth_open_minute: int = 30
    rth_close_hour: int = 16
    rth_close_minute: int = 0
    no_trade_first_minutes: int = 2
    restricted_opening_minutes: int = 5
    # 美东时间：超过该时刻后禁止新开仓（平仓不受影响）；关闭则不限
    no_new_trades_after_enabled: bool = False
    no_new_trades_after_hour_et: int = 12
    no_new_trades_after_minute_et: int = 0
    max_hold_minutes: int = 60
    max_trades_per_day: int = 2

    # 反应区（占价位比例）
    reaction_zone_half_width_pct: float = 0.0008
    # 心理整数网格步长（如 2.5 → 570, 572.5…）
    psychological_step: float = 2.5
    psychological_levels_max: int = 12

    # 成交量
    volume_lookback_bars: int = 20
    volume_spike_multiplier: float = 2.0

    # 形态确认
    breakout_hold_bars: int = 2
    reversal_pullback_pct: float = 0.0015

    # 缺口阈值（相对昨收比例）
    gap_threshold_pct: float = 0.002

    # 选约：OTM 方向上的行权价步长（QQQ 常见 1 点）
    strike_step: float = 1.0
    call_strikes_otm: int = 0
    put_strikes_otm: int = 0

    # 定价（合成）
    risk_free_rate: float = 0.052
    dividend_yield: float = 0.0
    vol_window_bars: int = 30
    min_sigma: float = 0.12
    # 0DTE：到期时刻（美东）默认 16:00
    option_expiry_hour_et: int = 16
    option_expiry_minute_et: int = 0

    # 出场
    take_profit_pct: float = 0.40
    stop_loss_pct: float = 0.35
    # 滑点：按期权价的比例恶化成交价（买入 +，卖出 -）
    option_slippage_pct: float = 0.05

    # 回测
    initial_option_contracts: int = 1
    contract_multiplier: int = 100

    # 日志
    log_decisions: bool = True

    # 策略变体：reaction_zone=反应区+成交量单边；morning_strangle=早盘宽跨；
    # morning_double_strangle=早盘双宽跨；morning_directional=早盘涨跌幅阈值单边；
    # gamma_scalping=开盘突破/回归的剥头皮
    strategy_variant: str = "reaction_zone"
    # 早盘宽跨（美东绝对时刻，格式 "HH:MM"，如 09:35）
    strangle_entry_start_hhmm_et: str = "09:35"
    strangle_entry_end_hhmm_et: str = "10:00"
    strangle_force_close_hhmm_et: str = "12:00"
    # 相对前收涨跌幅绝对值不超过该值才开仓（如 0.003 = 0.3%）
    strangle_range_pct: float = 0.003
    # 组合权利金盈亏率阈值：(已实现平仓金额 + 剩余腿 bid 合计 - 原始两腿开仓 ask 合计) / 原始两腿开仓 ask 合计；不含手续费
    strangle_take_profit_return: float = 1.0
    # 组合止损阈值：当组合盈亏率 <= -该值触发平仓；0 表示关闭该规则
    strangle_stop_loss_return: float = 0.0
    # 组合止损冷静期（分钟）：开仓后该时长内不触发组合止损；0 表示不启用
    strangle_stop_loss_cooldown_minutes: int = 0
    # 单腿止盈/止损（相对该腿开仓成本 call_entry_px/put_entry_px）；0 表示关闭该规则。与组合止盈、强平时刻独立判断。
    # strangle_leg_take_profit_pct 为旧字段；新配置优先使用长腿/短腿止盈，缺省时兼容旧字段。
    strangle_leg_take_profit_pct: float = 0.0
    strangle_long_leg_take_profit_pct: float = 0.0
    strangle_short_leg_take_profit_pct: float = 0.0
    strangle_leg_stop_loss_pct: float = 0.0
    # 回测无真实 bid：用 K 线哪一档近似标的现价（open/high/low/close）；实盘应以行情 bid 为准
    strangle_underlying_field: str = "low"

    # 早盘双宽跨：Call/Put 各一条长腿和一条短腿。长短腿由 OTM 步长配置显式指定。
    # 单腿止盈按四条腿分别配置；单腿止损不拆；组合止盈/止损按四条腿合计权利金口径。
    double_strangle_call_long_strikes_otm: int = 2
    double_strangle_call_short_strikes_otm: int = 1
    double_strangle_put_long_strikes_otm: int = 2
    double_strangle_put_short_strikes_otm: int = 1
    double_strangle_call_long_leg_take_profit_pct: float = 1.0
    double_strangle_call_short_leg_take_profit_pct: float = 0.35
    double_strangle_put_long_leg_take_profit_pct: float = 1.0
    double_strangle_put_short_leg_take_profit_pct: float = 0.35
    double_strangle_single_leg_stop_loss_pct: float = 0.35
    double_strangle_combo_take_profit_pct: float = 0.60
    double_strangle_combo_stop_loss_pct: float = 0.30
    double_strangle_max_total_debit: float = 0.0
    double_strangle_require_all_legs_filled: bool = True

    # 早盘方向单：与宽跨共用 strangle_entry_* 开仓窗、strangle_force_close_hhmm_et 强平、strangle_underlying_field
    # 相对前收 chg：chg <= -directional_down_pct 买入 Call；chg >= directional_up_pct 买入 Put（默认均为 1%）
    directional_down_pct: float = 0.01
    directional_up_pct: float = 0.01
    # 单腿权利金盈亏率 (bid−ask 近似)/ask，不含手续费；默认 1.0=+100%
    directional_take_profit_return: float = 1.0
    # 单腿止损：亏损比例 ≥ 该阈值则平仓，(last 盯市价−entry)/entry ≤ −threshold；0 表示关闭（与宽跨单腿止损口径一致）
    directional_stop_loss_pct: float = 0.0

    # Gamma Scalping（单腿）
    gamma_entry_start_hhmm_et: str = "09:30"
    gamma_entry_end_hhmm_et: str = "10:00"
    gamma_force_close_hhmm_et: str = "14:00"
    gamma_max_hold_minutes: int = 15
    gamma_hard_stop_loss_pct: float = 0.30
    gamma_take_profit_min_return: float = 0.50
    gamma_take_profit_max_return: float = 1.00
    # 选约以近 ATM 或 1-2 档 OTM 近似 Delta 0.40-0.45（当前无链上 Greeks）
    gamma_call_otm_steps: int = 1
    gamma_put_otm_steps: int = 1
    # True：开盘窗口内 spot>=昨高 → long_call；spot<=昨低 → long_put（与 VWAP 回归分支独立）
    gamma_require_breakout_prev_day: bool = True
    gamma_require_vix_rising: bool = True
    gamma_vix_rising_min_pct: float = 0.30
    gamma_enable_vwap_reversion: bool = True
    gamma_vwap_deviation_pct: float = 0.003
    gamma_require_leader_confirmation: bool = True
    gamma_leader_min_move_pct: float = 0.60
    gamma_leader_lag_minutes: int = 2
    gamma_leader_lag_pct: float = 0.10
    # 实盘轮询时填充的实时上下文（百分数口径，如 +0.5 表示 +0.5%）
    gamma_vix_symbol: str = "VIX.US"
    gamma_leader_symbol_1: str = "NVDA.US"
    gamma_leader_symbol_2: str = "TSLA.US"
    gamma_rt_vix_change_pct: float = 0.0
    gamma_rt_qqq_change_pct: float = 0.0
    gamma_rt_leader1_change_pct: float = 0.0
    gamma_rt_leader2_change_pct: float = 0.0

    # Gamma Pro（单腿，突破/假突破/午后续航）
    gamma_pro_entry_start_hhmm_et: str = "10:00"
    gamma_pro_entry_end_hhmm_et: str = "15:30"
    gamma_pro_midday_skip_start_hhmm_et: str = "12:00"
    gamma_pro_midday_skip_end_hhmm_et: str = "13:00"
    gamma_pro_afternoon_start_hhmm_et: str = "13:30"
    gamma_pro_force_close_hhmm_et: str = "15:45"
    gamma_pro_max_hold_minutes: int = 45
    gamma_pro_hard_stop_loss_pct: float = 0.30
    gamma_pro_take_profit_return: float = 0.60
    gamma_pro_call_otm_steps: int = 1
    gamma_pro_put_otm_steps: int = 1
    gamma_pro_require_leader_confirmation: bool = True
    # 假突破判定：当前 bar 穿越关键位后收回关键位内，且量能未放大（基于 volume_spike_multiplier）
    gamma_pro_enable_false_breakout_reversal: bool = True
    # 午后续航：回踩 VWAP 的允许偏离（如 0.0015 = 0.15%）
    gamma_pro_vwap_pullback_pct: float = 0.0015

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Qqq0dteConfig:
        if not d:
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
