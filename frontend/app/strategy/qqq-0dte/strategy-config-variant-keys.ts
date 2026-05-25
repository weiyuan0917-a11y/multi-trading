/**
 * strategy_config 按 strategy_variant 的字段白名单（与后端 Qqq0dteConfig / 回测表单一致）。
 * 用于写入 live_worker_config.json 前剔除其它策略的无关键。
 */
import { STRATEGY_CONFIG_BACKEND_DEFAULTS } from "./strategy-config-defaults";

export type QqqStrategyVariant =
  | "reaction_zone"
  | "morning_strangle"
  | "morning_double_strangle"
  | "morning_directional"
  | "gamma_scalping"
  | "gamma_pro";

const STRATEGY_CONFIG_KEYS_REACTION_ZONE = new Set<string>([
  "strategy_variant",
  "symbol",
  "assume_bars_timezone",
  "rth_open_hour",
  "rth_open_minute",
  "rth_close_hour",
  "rth_close_minute",
  "no_trade_first_minutes",
  "restricted_opening_minutes",
  "no_new_trades_after_enabled",
  "no_new_trades_after_hour_et",
  "no_new_trades_after_minute_et",
  "max_hold_minutes",
  "max_trades_per_day",
  "reaction_zone_half_width_pct",
  "psychological_step",
  "psychological_levels_max",
  "volume_lookback_bars",
  "volume_spike_multiplier",
  "breakout_hold_bars",
  "reversal_pullback_pct",
  "gap_threshold_pct",
  "strike_step",
  "call_strikes_otm",
  "put_strikes_otm",
  "risk_free_rate",
  "dividend_yield",
  "vol_window_bars",
  "min_sigma",
  "option_expiry_hour_et",
  "option_expiry_minute_et",
  "take_profit_pct",
  "stop_loss_pct",
  "option_slippage_pct",
  "initial_option_contracts",
  "contract_multiplier",
  "log_decisions",
]);

const STRATEGY_CONFIG_KEYS_MORNING_STRANGLE = new Set<string>([
  "strategy_variant",
  "symbol",
  "assume_bars_timezone",
  "rth_open_hour",
  "rth_open_minute",
  "rth_close_hour",
  "rth_close_minute",
  "max_trades_per_day",
  "strangle_entry_start_hhmm_et",
  "strangle_entry_end_hhmm_et",
  "strangle_force_close_hhmm_et",
  "strangle_range_pct",
  "strangle_take_profit_return",
  "strangle_stop_loss_return",
  "strangle_stop_loss_cooldown_minutes",
  "strangle_leg_take_profit_pct",
  "strangle_long_leg_take_profit_pct",
  "strangle_short_leg_take_profit_pct",
  "strangle_leg_stop_loss_pct",
  "strangle_underlying_field",
  "strike_step",
  "call_strikes_otm",
  "put_strikes_otm",
  "risk_free_rate",
  "dividend_yield",
  "vol_window_bars",
  "min_sigma",
  "option_expiry_hour_et",
  "option_expiry_minute_et",
  "option_slippage_pct",
  "initial_option_contracts",
  "contract_multiplier",
  "log_decisions",
]);

const STRATEGY_CONFIG_KEYS_MORNING_DIRECTIONAL = new Set<string>([
  "strategy_variant",
  "symbol",
  "assume_bars_timezone",
  "rth_open_hour",
  "rth_open_minute",
  "rth_close_hour",
  "rth_close_minute",
  "max_trades_per_day",
  "strangle_entry_start_hhmm_et",
  "strangle_entry_end_hhmm_et",
  "strangle_force_close_hhmm_et",
  "strangle_underlying_field",
  "directional_down_pct",
  "directional_up_pct",
  "directional_take_profit_return",
  "directional_stop_loss_pct",
  "strike_step",
  "call_strikes_otm",
  "put_strikes_otm",
  "risk_free_rate",
  "dividend_yield",
  "vol_window_bars",
  "min_sigma",
  "option_expiry_hour_et",
  "option_expiry_minute_et",
  "option_slippage_pct",
  "initial_option_contracts",
  "contract_multiplier",
  "log_decisions",
]);

const STRATEGY_CONFIG_KEYS_MORNING_DOUBLE_STRANGLE = new Set<string>([
  "strategy_variant",
  "symbol",
  "assume_bars_timezone",
  "rth_open_hour",
  "rth_open_minute",
  "rth_close_hour",
  "rth_close_minute",
  "max_trades_per_day",
  "strangle_entry_start_hhmm_et",
  "strangle_entry_end_hhmm_et",
  "strangle_force_close_hhmm_et",
  "strangle_range_pct",
  "strangle_stop_loss_cooldown_minutes",
  "strangle_underlying_field",
  "double_strangle_call_long_strikes_otm",
  "double_strangle_call_short_strikes_otm",
  "double_strangle_put_long_strikes_otm",
  "double_strangle_put_short_strikes_otm",
  "double_strangle_call_long_leg_take_profit_pct",
  "double_strangle_call_short_leg_take_profit_pct",
  "double_strangle_put_long_leg_take_profit_pct",
  "double_strangle_put_short_leg_take_profit_pct",
  "double_strangle_single_leg_stop_loss_pct",
  "double_strangle_combo_take_profit_pct",
  "double_strangle_combo_stop_loss_pct",
  "double_strangle_max_total_debit",
  "double_strangle_require_all_legs_filled",
  "strike_step",
  "risk_free_rate",
  "dividend_yield",
  "vol_window_bars",
  "min_sigma",
  "option_expiry_hour_et",
  "option_expiry_minute_et",
  "option_slippage_pct",
  "initial_option_contracts",
  "contract_multiplier",
  "log_decisions",
]);

const STRATEGY_CONFIG_KEYS_GAMMA_COMMON = new Set<string>([
  "strategy_variant",
  "symbol",
  "assume_bars_timezone",
  "rth_open_hour",
  "rth_open_minute",
  "rth_close_hour",
  "rth_close_minute",
  "max_trades_per_day",
  "strangle_underlying_field",
  "strike_step",
  "call_strikes_otm",
  "put_strikes_otm",
  "risk_free_rate",
  "dividend_yield",
  "vol_window_bars",
  "min_sigma",
  "option_expiry_hour_et",
  "option_expiry_minute_et",
  "option_slippage_pct",
  "initial_option_contracts",
  "contract_multiplier",
  "log_decisions",
  "volume_lookback_bars",
  "volume_spike_multiplier",
  "gamma_leader_min_move_pct",
  "gamma_leader_lag_minutes",
  "gamma_leader_lag_pct",
  "gamma_leader_symbol_1",
  "gamma_leader_symbol_2",
  "gamma_rt_qqq_change_pct",
  "gamma_rt_leader1_change_pct",
  "gamma_rt_leader2_change_pct",
]);

const STRATEGY_CONFIG_KEYS_GAMMA_SCALPING = new Set<string>([
  ...STRATEGY_CONFIG_KEYS_GAMMA_COMMON,
  "gamma_entry_start_hhmm_et",
  "gamma_entry_end_hhmm_et",
  "gamma_force_close_hhmm_et",
  "gamma_max_hold_minutes",
  "gamma_hard_stop_loss_pct",
  "gamma_take_profit_min_return",
  "gamma_take_profit_max_return",
  "gamma_call_otm_steps",
  "gamma_put_otm_steps",
  "gamma_require_breakout_prev_day",
  "gamma_require_vix_rising",
  "gamma_vix_rising_min_pct",
  "gamma_enable_vwap_reversion",
  "gamma_vwap_deviation_pct",
  "gamma_require_leader_confirmation",
  "gamma_vix_symbol",
  "gamma_rt_vix_change_pct",
]);

const STRATEGY_CONFIG_KEYS_GAMMA_PRO = new Set<string>([
  ...STRATEGY_CONFIG_KEYS_GAMMA_COMMON,
  "gamma_pro_entry_start_hhmm_et",
  "gamma_pro_entry_end_hhmm_et",
  "gamma_pro_midday_skip_start_hhmm_et",
  "gamma_pro_midday_skip_end_hhmm_et",
  "gamma_pro_afternoon_start_hhmm_et",
  "gamma_pro_force_close_hhmm_et",
  "gamma_pro_max_hold_minutes",
  "gamma_pro_hard_stop_loss_pct",
  "gamma_pro_take_profit_return",
  "gamma_pro_call_otm_steps",
  "gamma_pro_put_otm_steps",
  "gamma_pro_require_leader_confirmation",
  "gamma_pro_enable_false_breakout_reversal",
  "gamma_pro_vwap_pullback_pct",
]);

export function allowedKeysForStrategyVariant(v: QqqStrategyVariant): Set<string> {
  if (v === "morning_strangle") return STRATEGY_CONFIG_KEYS_MORNING_STRANGLE;
  if (v === "morning_double_strangle") return STRATEGY_CONFIG_KEYS_MORNING_DOUBLE_STRANGLE;
  if (v === "morning_directional") return STRATEGY_CONFIG_KEYS_MORNING_DIRECTIONAL;
  if (v === "gamma_scalping") return STRATEGY_CONFIG_KEYS_GAMMA_SCALPING;
  if (v === "gamma_pro") return STRATEGY_CONFIG_KEYS_GAMMA_PRO;
  return STRATEGY_CONFIG_KEYS_REACTION_ZONE;
}

export function filterStrategyConfigByVariant(
  variant: QqqStrategyVariant,
  config: Record<string, unknown>
): Record<string, unknown> {
  const allow = allowedKeysForStrategyVariant(variant);
  const out: Record<string, unknown> = {};
  for (const k of allow) {
    if (Object.prototype.hasOwnProperty.call(config, k)) {
      out[k] = config[k];
    }
  }
  return out;
}

/**
 * 补齐当前策略白名单键：优先用传入 config，其次用后端默认值。
 * 这样写入 live_worker_config.json 时可得到“本策略键齐全”的 strategy_config。
 */
export function completeStrategyConfigByVariant(
  variant: QqqStrategyVariant,
  config: Record<string, unknown>
): Record<string, unknown> {
  const allow = allowedKeysForStrategyVariant(variant);
  const out: Record<string, unknown> = {};
  for (const k of allow) {
    if (Object.prototype.hasOwnProperty.call(config, k)) {
      out[k] = config[k];
      continue;
    }
    if (Object.prototype.hasOwnProperty.call(STRATEGY_CONFIG_BACKEND_DEFAULTS, k)) {
      out[k] = STRATEGY_CONFIG_BACKEND_DEFAULTS[k];
    }
  }
  out.strategy_variant = variant;
  return out;
}

/** 解析 JSON / 下拉值中的 strategy_variant，未知则回落 reaction_zone */
export function coerceStrategyVariant(raw: unknown): QqqStrategyVariant {
  const s = typeof raw === "string" ? raw.trim().toLowerCase() : "";
  if (
    s === "morning_strangle" ||
    s === "morning_double_strangle" ||
    s === "morning_directional" ||
    s === "gamma_scalping" ||
    s === "gamma_pro" ||
    s === "reaction_zone"
  ) {
    return s;
  }
  return "reaction_zone";
}
