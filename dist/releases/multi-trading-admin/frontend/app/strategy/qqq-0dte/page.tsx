"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { localAgentGet as apiGet, localAgentPost as apiPost, localAgentPut as apiPut } from "@/lib/local-agent-api";
import { PageShell } from "@/components/ui/page-shell";
import { Qqq0dteLiveAutoPanel } from "./live-auto-panel";
import { StrategyConfigJsonTextarea } from "./strategy-config-json-textarea";
import { stringifyStrategyConfigWithHints, stripStrategyConfigHashComments } from "./strategy-config-json-hints";
import { allowedKeysForStrategyVariant, filterStrategyConfigByVariant } from "./strategy-config-variant-keys";

const STORAGE_KEY = "qqq_0dte_test_form_v2";

/** 回测成交表「方向/行权价」：宽跨展示 Call/Put 两腿行权价（后端字段 call_strike / put_strike）。 */
function formatBacktestDirectionStrike(t: Record<string, unknown>): string {
  const side = String(t.side ?? "");
  if (side === "strangle") {
    const fmtK = (x: unknown) =>
      x != null && Number.isFinite(Number(x)) ? Number(x).toFixed(2) : "—";
    return `宽跨 · Call K=${fmtK(t.call_strike)} · Put K=${fmtK(t.put_strike)}`;
  }
  const strike = t.strike;
  const base = side || "—";
  if (strike != null && strike !== "" && Number.isFinite(Number(strike))) {
    return `${base} · K=${strike}`;
  }
  return base;
}

/** 矩阵删选：可读维度定义（key 与后端 grid / Qqq0dteConfig 对齐） */
const MATRIX_PARAM_DEFS: readonly {
  key: string;
  labelZh: string;
  hint: string;
  defaultValuesText: string;
  /** string：逗号分隔标识符（如策略变体）；number：逗号分隔数字 */
  valueKind?: "number" | "string";
  /** 默认是否参与笛卡尔积；早盘/变体行默认关闭以免与反应区组合爆炸 */
  defaultEnabled?: boolean;
}[] = [
  {
    key: "reaction_zone_width_pct",
    labelZh: "反应区半宽（占标的价格的 %）",
    hint: "与策略表单「反应区半宽」同一单位；例 0.08≈±0.08% 带宽。",
    defaultValuesText: "0.08, 0.1, 1",
  },
  {
    key: "psychological_step",
    labelZh: "心理价位步长",
    hint: "整数位网格间隔，如 1 或 2.5。",
    defaultValuesText: "1, 2.5",
  },
  {
    key: "psychological_levels_max",
    labelZh: "心理价位最多条数（单侧）",
    hint: "网格在现价上下的展开条数上限相关，与表单一致。",
    defaultValuesText: "10, 12",
  },
  {
    key: "volume_spike_multiplier",
    labelZh: "成交量突增倍数",
    hint: "当前 K 量需 ≥ 回看均值×该值；可与「回看根数」配合（见高级 JSON）。",
    defaultValuesText: "1.5, 2",
  },
  {
    key: "strangle_range_pct_ui",
    labelZh: "宽跨：允许偏离前收（%）",
    hint: "仅早盘宽跨用；与表单「相对前收允许偏离」同单位，后端写入 strangle_range_pct。",
    defaultValuesText: "0.2, 0.3, 0.5",
    defaultEnabled: true,
  },
  {
    key: "strangle_take_profit_return_ui",
    labelZh: "宽跨：组合止盈盈亏率（%）",
    hint: "已实现平仓金额 + 剩余腿 bid，相对原始建仓权利金；100 表示 +100%。",
    defaultValuesText: "80, 100, 120",
    defaultEnabled: true,
  },
  {
    key: "strangle_stop_loss_return_ui",
    labelZh: "宽跨：组合止损盈亏率（%）",
    hint: "相对建仓权利金；30 表示组合亏损达 30% 时平仓；0 表示关闭。",
    defaultValuesText: "0, 30, 40",
    defaultEnabled: true,
  },
  {
    key: "strangle_stop_loss_cooldown_minutes",
    labelZh: "宽跨：组合止损冷静期（分钟）",
    hint: "开仓后该时长内不触发组合止损；0 表示关闭。",
    defaultValuesText: "0, 1, 2",
    defaultEnabled: true,
  },
  {
    key: "directional_down_pct_ui",
    labelZh: "方向单：下跌买 Call 阈值（%）",
    hint: "相对前收跌幅绝对值，与表单一致。",
    defaultValuesText: "0.8, 1, 1.2",
    defaultEnabled: true,
  },
  {
    key: "directional_up_pct_ui",
    labelZh: "方向单：上涨买 Put 阈值（%）",
    hint: "相对前收涨幅绝对值，与表单一致。",
    defaultValuesText: "0.8, 1, 1.2",
    defaultEnabled: true,
  },
  {
    key: "directional_take_profit_return_ui",
    labelZh: "方向单：单腿止盈盈亏率（%）",
    hint: "相对该腿建仓权利金；100 表示 +100%。",
    defaultValuesText: "80, 100, 120",
    defaultEnabled: true,
  },
  {
    key: "directional_stop_loss_pct_ui",
    labelZh: "方向单：单腿止损（%）",
    hint: "相对建仓成本亏损达该比例则平仓（盯市 last 优先）；0 表示关闭。",
    defaultValuesText: "0, 30, 40",
    defaultEnabled: true,
  },
  {
    key: "call_strikes_otm",
    labelZh: "Call 外移档数（OTM）",
    hint: "早盘/反应区选约共用；整数。",
    defaultValuesText: "0, 1",
    defaultEnabled: false,
  },
  {
    key: "put_strikes_otm",
    labelZh: "Put 外移档数（OTM）",
    hint: "早盘/反应区选约共用；整数。",
    defaultValuesText: "0, 1",
    defaultEnabled: false,
  },
  {
    key: "gamma_hard_stop_loss_pct_ui",
    labelZh: "Gamma：硬止损（%）",
    hint: "单腿权利金跌幅硬止损（默认 30%）。",
    defaultValuesText: "25, 30, 35",
    defaultEnabled: true,
  },
  {
    key: "gamma_take_profit_min_return_ui",
    labelZh: "Gamma：止盈下限（%）",
    hint: "达到该盈亏率先止盈（默认 50%）。",
    defaultValuesText: "40, 50, 60",
    defaultEnabled: true,
  },
  {
    key: "gamma_take_profit_max_return_ui",
    labelZh: "Gamma：止盈上限（%）",
    hint: "高位止盈参考（默认 100%）。",
    defaultValuesText: "80, 100, 120",
    defaultEnabled: false,
  },
  {
    key: "gamma_vix_rising_min_pct",
    labelZh: "Gamma：VIX 同步上行阈值（%）",
    hint: "突破入场时要求 VIX 涨幅 ≥ 该值（默认 0.3）。",
    defaultValuesText: "0.2, 0.3, 0.5",
    defaultEnabled: true,
  },
  {
    key: "gamma_vwap_deviation_pct_ui",
    labelZh: "Gamma：VWAP 偏离阈值（%）",
    hint: "触发 VWAP 回归信号的偏离幅度（默认 0.3）。",
    defaultValuesText: "0.2, 0.3, 0.5",
    defaultEnabled: false,
  },
  {
    key: "gamma_leader_min_move_pct",
    labelZh: "Gamma：NVDA/TSLA 最小领先涨跌幅（%）",
    hint: "龙头确认门槛（默认 0.6）。",
    defaultValuesText: "0.4, 0.6, 0.8",
    defaultEnabled: false,
  },
  {
    key: "gamma_leader_lag_pct",
    labelZh: "Gamma：QQQ 滞后阈值（%）",
    hint: "龙头领先 QQQ 的最小差值（默认 0.1）。",
    defaultValuesText: "0.05, 0.1, 0.2",
    defaultEnabled: false,
  },
  {
    key: "gamma_call_otm_steps",
    labelZh: "Gamma：Call 外移档数（OTM）",
    hint: "近 ATM/1-2 档 OTM 的 Delta 近似。",
    defaultValuesText: "0, 1, 2",
    defaultEnabled: false,
  },
  {
    key: "gamma_put_otm_steps",
    labelZh: "Gamma：Put 外移档数（OTM）",
    hint: "近 ATM/1-2 档 OTM 的 Delta 近似。",
    defaultValuesText: "0, 1, 2",
    defaultEnabled: false,
  },
];

type MatrixStrategyVariant = "reaction_zone" | "morning_strangle" | "morning_directional" | "gamma_scalping" | "gamma_pro";

const MATRIX_KEYS_REACTION = new Set<string>([
  "reaction_zone_width_pct",
  "psychological_step",
  "psychological_levels_max",
  "volume_spike_multiplier",
  "call_strikes_otm",
  "put_strikes_otm",
]);

const MATRIX_KEYS_MORNING_STRANGLE = new Set<string>([
  "strangle_range_pct_ui",
  "strangle_take_profit_return_ui",
  "strangle_stop_loss_return_ui",
  "strangle_stop_loss_cooldown_minutes",
  "call_strikes_otm",
  "put_strikes_otm",
]);

const MATRIX_KEYS_MORNING_DIRECTIONAL = new Set<string>([
  "directional_down_pct_ui",
  "directional_up_pct_ui",
  "directional_take_profit_return_ui",
  "directional_stop_loss_pct_ui",
  "call_strikes_otm",
  "put_strikes_otm",
]);

const MATRIX_KEYS_GAMMA_SCALPING = new Set<string>([
  "gamma_hard_stop_loss_pct_ui",
  "gamma_take_profit_min_return_ui",
  "gamma_take_profit_max_return_ui",
  "gamma_vix_rising_min_pct",
  "gamma_vwap_deviation_pct_ui",
  "gamma_leader_min_move_pct",
  "gamma_leader_lag_pct",
  "gamma_call_otm_steps",
  "gamma_put_otm_steps",
]);

function matrixKeysForVariant(v: MatrixStrategyVariant): Set<string> {
  if (v === "morning_strangle") return MATRIX_KEYS_MORNING_STRANGLE;
  if (v === "morning_directional") return MATRIX_KEYS_MORNING_DIRECTIONAL;
  if (v === "gamma_scalping" || v === "gamma_pro") return MATRIX_KEYS_GAMMA_SCALPING;
  return MATRIX_KEYS_REACTION;
}

function matrixDefAppliesToVariant(key: string, variant: MatrixStrategyVariant): boolean {
  return matrixKeysForVariant(variant).has(key);
}

/** 切换矩阵策略时，为当前策略可见行设置推荐默认勾选（隐藏行状态保留） */
function recommendedMatrixRowEnabled(variant: MatrixStrategyVariant, key: string): boolean {
  if (variant === "reaction_zone") {
    return (
      key === "reaction_zone_width_pct" ||
      key === "psychological_step" ||
      key === "psychological_levels_max" ||
      key === "volume_spike_multiplier"
    );
  }
  if (variant === "morning_strangle") {
    return (
      key === "strangle_range_pct_ui" ||
      key === "strangle_take_profit_return_ui" ||
      key === "strangle_stop_loss_return_ui" ||
      key === "strangle_stop_loss_cooldown_minutes"
    );
  }
  if (variant === "gamma_scalping" || variant === "gamma_pro") {
    return (
      key === "gamma_hard_stop_loss_pct_ui" ||
      key === "gamma_take_profit_min_return_ui" ||
      key === "gamma_vix_rising_min_pct"
    );
  }
  return (
    key === "directional_down_pct_ui" ||
    key === "directional_up_pct_ui" ||
    key === "directional_take_profit_return_ui" ||
    key === "directional_stop_loss_pct_ui"
  );
}

type MatrixAxisRowState = {
  key: string;
  labelZh: string;
  hint: string;
  enabled: boolean;
  valuesText: string;
  valueKind?: "number" | "string";
};

function defaultMatrixAxisRows(): MatrixAxisRowState[] {
  return MATRIX_PARAM_DEFS.map((d) => ({
    key: d.key,
    labelZh: d.labelZh,
    hint: d.hint,
    enabled: d.defaultEnabled !== false,
    valuesText: d.defaultValuesText,
    valueKind: d.valueKind,
  }));
}

/** 支持英文逗号或中文逗号分隔 */
function parseCommaNumberList(raw: string): number[] {
  const parts = raw
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  if (parts.length === 0) throw new Error("候选值不能为空");
  const out: number[] = [];
  for (const p of parts) {
    const n = Number(p);
    if (!Number.isFinite(n)) throw new Error(`「${p}」不是有效数字`);
    out.push(n);
  }
  return out;
}

/** 策略变体等：逗号分隔的标识符，大小写敏感 */
function parseCommaStringList(raw: string): string[] {
  const parts = raw
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  if (parts.length === 0) throw new Error("候选值不能为空");
  return parts;
}

function buildGridFromMatrixRows(
  rows: MatrixAxisRowState[],
  matrixVariant: MatrixStrategyVariant
): Record<string, unknown[]> {
  const grid: Record<string, unknown[]> = {};
  for (const r of rows) {
    if (!r.enabled) continue;
    if (!matrixDefAppliesToVariant(r.key, matrixVariant)) continue;
    grid[r.key] =
      r.valueKind === "string"
        ? (parseCommaStringList(r.valuesText) as unknown[])
        : (parseCommaNumberList(r.valuesText) as unknown[]);
  }
  if (Object.keys(grid).length === 0) {
    throw new Error("请至少勾选一行参数，并填写候选值");
  }
  return grid;
}

function formatMatrixGridParamsReadable(params: Record<string, unknown> | undefined): string {
  if (!params || typeof params !== "object") return "—";
  return Object.entries(params)
    .map(([k, v]) => {
      const def = MATRIX_PARAM_DEFS.find((d) => d.key === k);
      const name = def ? def.labelZh : k;
      return `${name} = ${String(v)}`;
    })
    .join("；");
}

const DEFAULT_MATRIX_GRID_JSON = JSON.stringify(
  {
    reaction_zone_width_pct: [0.08, 0.1, 1],
    psychological_step: [1, 2.5],
    psychological_levels_max: [10, 12],
    volume_spike_multiplier: [1.5, 2],
  },
  null,
  2
);

/** 与后端 Qqq0dteConfig 对齐的表单状态（数值已为 API 使用的单位，百分比类在构建时再转换） */
type StrategyFormState = {
  strategy_variant: "reaction_zone" | "morning_strangle" | "morning_directional" | "gamma_scalping" | "gamma_pro";
  strangle_entry_start_hhmm_et: string;
  strangle_entry_end_hhmm_et: string;
  strangle_force_close_hhmm_et: string;
  /** 界面：相对前收允许偏离，如 0.3 表示 0.3% */
  strangle_range_pct_ui: number;
  /** 界面：组合权利金盈亏率阈值（已实现平仓金额 + 剩余腿 bid，相对原始建仓 ask 合计），100 表示 +100%（不含手续费） */
  strangle_take_profit_return_ui: number;
  /** 界面：组合止损阈值（相对建仓 ask 合计），30 表示 -30%；0 表示关闭 */
  strangle_stop_loss_return_ui: number;
  /** 界面：组合止损冷静期（分钟），开仓后该时长内不触发组合止损 */
  strangle_stop_loss_cooldown_minutes: number;
  strangle_underlying_field: "open" | "high" | "low" | "close";
  /** 早盘方向单：跌幅 ≥ 该值（%）买 Call，如 1 表示 1% */
  directional_down_pct_ui: number;
  /** 早盘方向单：涨幅 ≥ 该值（%）买 Put */
  directional_up_pct_ui: number;
  /** 单腿止盈盈亏率（%），相对该腿建仓 ask，不含手续费 */
  directional_take_profit_return_ui: number;
  /** 单腿止损（%），相对建仓成本；0 表示关闭 */
  directional_stop_loss_pct_ui: number;
  gamma_entry_start_hhmm_et: string;
  gamma_entry_end_hhmm_et: string;
  gamma_force_close_hhmm_et: string;
  gamma_max_hold_minutes: number;
  gamma_hard_stop_loss_pct_ui: number;
  gamma_take_profit_min_return_ui: number;
  gamma_take_profit_max_return_ui: number;
  gamma_call_otm_steps: number;
  gamma_put_otm_steps: number;
  gamma_require_breakout_prev_day: boolean;
  gamma_require_vix_rising: boolean;
  gamma_vix_rising_min_pct: number;
  gamma_enable_vwap_reversion: boolean;
  gamma_vwap_deviation_pct_ui: number;
  gamma_require_leader_confirmation: boolean;
  gamma_leader_min_move_pct: number;
  gamma_leader_lag_pct: number;
  gamma_pro_entry_start_hhmm_et: string;
  gamma_pro_entry_end_hhmm_et: string;
  gamma_pro_midday_skip_start_hhmm_et: string;
  gamma_pro_midday_skip_end_hhmm_et: string;
  gamma_pro_afternoon_start_hhmm_et: string;
  gamma_pro_force_close_hhmm_et: string;
  gamma_pro_max_hold_minutes: number;
  gamma_pro_hard_stop_loss_pct_ui: number;
  gamma_pro_take_profit_return_ui: number;
  gamma_pro_call_otm_steps: number;
  gamma_pro_put_otm_steps: number;
  gamma_pro_require_leader_confirmation: boolean;
  gamma_pro_enable_false_breakout_reversal: boolean;
  gamma_pro_vwap_pullback_pct_ui: number;
  no_trade_first_minutes: number;
  restricted_opening_minutes: number;
  no_new_trades_after_enabled: boolean;
  no_new_trades_after_hour_et: number;
  no_new_trades_after_minute_et: number;
  max_hold_minutes: number;
  max_trades_per_day: number;
  volume_lookback_bars: number;
  volume_spike_multiplier: number;
  reaction_zone_width_pct: number;
  psychological_step: number;
  psychological_levels_max: number;
  breakout_hold_bars: number;
  reversal_pullback_pct: number;
  gap_threshold_pct: number;
  strike_step: number;
  call_strikes_otm: number;
  put_strikes_otm: number;
  risk_free_rate_pct: number;
  dividend_yield_pct: number;
  vol_window_bars: number;
  min_sigma_pct: number;
  option_expiry_hour_et: number;
  option_expiry_minute_et: number;
  take_profit_pct: number;
  stop_loss_pct: number;
  option_slippage_pct: number;
  initial_option_contracts: number;
  contract_multiplier: number;
  log_decisions: boolean;
  assume_bars_timezone: string;
};

const DEFAULT_FORM: StrategyFormState = {
  strategy_variant: "reaction_zone",
  strangle_entry_start_hhmm_et: "09:35",
  strangle_entry_end_hhmm_et: "10:00",
  strangle_force_close_hhmm_et: "12:00",
  strangle_range_pct_ui: 0.3,
  strangle_take_profit_return_ui: 100,
  strangle_stop_loss_return_ui: 0,
  strangle_stop_loss_cooldown_minutes: 1,
  strangle_underlying_field: "low",
  directional_down_pct_ui: 1,
  directional_up_pct_ui: 1,
  directional_take_profit_return_ui: 100,
  directional_stop_loss_pct_ui: 0,
  gamma_entry_start_hhmm_et: "09:30",
  gamma_entry_end_hhmm_et: "10:00",
  gamma_force_close_hhmm_et: "14:00",
  gamma_max_hold_minutes: 15,
  gamma_hard_stop_loss_pct_ui: 30,
  gamma_take_profit_min_return_ui: 50,
  gamma_take_profit_max_return_ui: 100,
  gamma_call_otm_steps: 1,
  gamma_put_otm_steps: 1,
  gamma_require_breakout_prev_day: true,
  gamma_require_vix_rising: true,
  gamma_vix_rising_min_pct: 0.3,
  gamma_enable_vwap_reversion: true,
  gamma_vwap_deviation_pct_ui: 0.3,
  gamma_require_leader_confirmation: true,
  gamma_leader_min_move_pct: 0.6,
  gamma_leader_lag_pct: 0.1,
  gamma_pro_entry_start_hhmm_et: "10:00",
  gamma_pro_entry_end_hhmm_et: "15:30",
  gamma_pro_midday_skip_start_hhmm_et: "12:00",
  gamma_pro_midday_skip_end_hhmm_et: "13:00",
  gamma_pro_afternoon_start_hhmm_et: "13:30",
  gamma_pro_force_close_hhmm_et: "15:45",
  gamma_pro_max_hold_minutes: 45,
  gamma_pro_hard_stop_loss_pct_ui: 30,
  gamma_pro_take_profit_return_ui: 60,
  gamma_pro_call_otm_steps: 1,
  gamma_pro_put_otm_steps: 1,
  gamma_pro_require_leader_confirmation: true,
  gamma_pro_enable_false_breakout_reversal: true,
  gamma_pro_vwap_pullback_pct_ui: 0.15,
  no_trade_first_minutes: 2,
  restricted_opening_minutes: 5,
  no_new_trades_after_enabled: false,
  no_new_trades_after_hour_et: 12,
  no_new_trades_after_minute_et: 0,
  max_hold_minutes: 60,
  max_trades_per_day: 2,
  volume_lookback_bars: 20,
  volume_spike_multiplier: 2,
  reaction_zone_width_pct: 0.08,
  psychological_step: 2.5,
  psychological_levels_max: 12,
  breakout_hold_bars: 2,
  reversal_pullback_pct: 0.15,
  gap_threshold_pct: 0.2,
  strike_step: 1,
  call_strikes_otm: 0,
  put_strikes_otm: 0,
  risk_free_rate_pct: 5.2,
  dividend_yield_pct: 0,
  vol_window_bars: 30,
  min_sigma_pct: 12,
  option_expiry_hour_et: 16,
  option_expiry_minute_et: 0,
  take_profit_pct: 40,
  stop_loss_pct: 35,
  option_slippage_pct: 5,
  initial_option_contracts: 1,
  contract_multiplier: 100,
  log_decisions: true,
  assume_bars_timezone: "UTC",
};

type StrategyPreset = {
  key: "balanced" | "aggressive" | "conservative_day" | "event_day" | "choppy_day";
  title: string;
  description: string;
  formPatch: Partial<StrategyFormState>;
  matrixGrid: Record<string, number[]>;
};

const PRESET_BALANCED_MATRIX_GRID: Record<string, number[]> = {
  reaction_zone_width_pct: [0.06, 0.08, 0.1],
  psychological_step: [1, 2.5],
  psychological_levels_max: [10, 12, 14],
  volume_spike_multiplier: [1.5, 1.8, 2],
};

const PRESET_AGGRESSIVE_MATRIX_GRID: Record<string, number[]> = {
  reaction_zone_width_pct: [0.08, 0.1, 0.12],
  psychological_step: [1, 2.5],
  psychological_levels_max: [12, 14],
  volume_spike_multiplier: [1.3, 1.5, 1.8],
};

/** 保守日：少交易、确认更严 */
const PRESET_CONSERVATIVE_MATRIX_GRID: Record<string, number[]> = {
  reaction_zone_width_pct: [0.05, 0.06, 0.08],
  psychological_step: [2.5],
  psychological_levels_max: [10, 12],
  volume_spike_multiplier: [2, 2.2, 2.5],
};

/** 事件日：防假突破、博单边但次数少；矩阵略小 */
const PRESET_EVENT_MATRIX_GRID: Record<string, number[]> = {
  reaction_zone_width_pct: [0.08, 0.1],
  psychological_step: [1, 2.5],
  psychological_levels_max: [12],
  volume_spike_multiplier: [1.8, 2, 2.2],
};

/** 震荡日：过滤假突破、略宽反应区、快进快出 */
const PRESET_CHOPPY_MATRIX_GRID: Record<string, number[]> = {
  reaction_zone_width_pct: [0.1, 0.12, 0.14],
  psychological_step: [1, 2.5],
  psychological_levels_max: [12, 14],
  volume_spike_multiplier: [2, 2.2, 2.5],
};

const STRATEGY_PRESETS: readonly StrategyPreset[] = [
  {
    key: "balanced",
    title: "稳健趋势（建议起步）",
    description: "减少过度交易，优先控制回撤。",
    formPatch: {
      no_trade_first_minutes: 5,
      restricted_opening_minutes: 10,
      no_new_trades_after_enabled: true,
      no_new_trades_after_hour_et: 12,
      no_new_trades_after_minute_et: 30,
      max_hold_minutes: 45,
      max_trades_per_day: 2,
      volume_lookback_bars: 20,
      volume_spike_multiplier: 1.8,
      reaction_zone_width_pct: 0.08,
      psychological_step: 2.5,
      psychological_levels_max: 12,
      breakout_hold_bars: 2,
      reversal_pullback_pct: 0.15,
      gap_threshold_pct: 0.2,
      strike_step: 1,
      call_strikes_otm: 0,
      put_strikes_otm: 0,
      vol_window_bars: 30,
      min_sigma_pct: 12,
      take_profit_pct: 45,
      stop_loss_pct: 30,
      option_slippage_pct: 5,
      initial_option_contracts: 1,
      contract_multiplier: 100,
      log_decisions: true,
      assume_bars_timezone: "UTC",
    },
    matrixGrid: PRESET_BALANCED_MATRIX_GRID,
  },
  {
    key: "aggressive",
    title: "激进趋势（高波动）",
    description: "提高触发频率，接受更高噪音与回撤。",
    formPatch: {
      no_trade_first_minutes: 2,
      restricted_opening_minutes: 5,
      no_new_trades_after_enabled: true,
      no_new_trades_after_hour_et: 13,
      no_new_trades_after_minute_et: 0,
      max_hold_minutes: 60,
      max_trades_per_day: 3,
      volume_lookback_bars: 15,
      volume_spike_multiplier: 1.5,
      reaction_zone_width_pct: 0.1,
      psychological_step: 1,
      psychological_levels_max: 14,
      breakout_hold_bars: 2,
      reversal_pullback_pct: 0.12,
      gap_threshold_pct: 0.15,
      strike_step: 1,
      call_strikes_otm: 0,
      put_strikes_otm: 0,
      vol_window_bars: 20,
      min_sigma_pct: 10,
      take_profit_pct: 50,
      stop_loss_pct: 35,
      option_slippage_pct: 5,
      initial_option_contracts: 1,
      contract_multiplier: 100,
      log_decisions: true,
      assume_bars_timezone: "UTC",
    },
    matrixGrid: PRESET_AGGRESSIVE_MATRIX_GRID,
  },
  {
    key: "conservative_day",
    title: "保守日（少交易）",
    description: "更长开盘回避、更晚截止可选，压低日交易次数。",
    formPatch: {
      no_trade_first_minutes: 10,
      restricted_opening_minutes: 15,
      no_new_trades_after_enabled: true,
      no_new_trades_after_hour_et: 11,
      no_new_trades_after_minute_et: 30,
      max_hold_minutes: 30,
      max_trades_per_day: 1,
      volume_lookback_bars: 25,
      volume_spike_multiplier: 2.2,
      reaction_zone_width_pct: 0.06,
      psychological_step: 2.5,
      psychological_levels_max: 12,
      breakout_hold_bars: 3,
      reversal_pullback_pct: 0.2,
      gap_threshold_pct: 0.25,
      strike_step: 1,
      call_strikes_otm: 0,
      put_strikes_otm: 0,
      vol_window_bars: 35,
      min_sigma_pct: 12,
      take_profit_pct: 35,
      stop_loss_pct: 25,
      option_slippage_pct: 5,
      initial_option_contracts: 1,
      contract_multiplier: 100,
      log_decisions: true,
      assume_bars_timezone: "UTC",
    },
    matrixGrid: PRESET_CONSERVATIVE_MATRIX_GRID,
  },
  {
    key: "event_day",
    title: "事件日（重要数据/FOMC）",
    description: "加强形态确认、略抬定价波动下界；止盈放宽、止损果断。",
    formPatch: {
      no_trade_first_minutes: 5,
      restricted_opening_minutes: 12,
      no_new_trades_after_enabled: false,
      no_new_trades_after_hour_et: 15,
      no_new_trades_after_minute_et: 30,
      max_hold_minutes: 40,
      max_trades_per_day: 1,
      volume_lookback_bars: 20,
      volume_spike_multiplier: 2,
      reaction_zone_width_pct: 0.08,
      psychological_step: 2.5,
      psychological_levels_max: 12,
      breakout_hold_bars: 3,
      reversal_pullback_pct: 0.18,
      gap_threshold_pct: 0.15,
      strike_step: 1,
      call_strikes_otm: 0,
      put_strikes_otm: 0,
      vol_window_bars: 25,
      min_sigma_pct: 16,
      take_profit_pct: 55,
      stop_loss_pct: 38,
      option_slippage_pct: 7,
      initial_option_contracts: 1,
      contract_multiplier: 100,
      log_decisions: true,
      assume_bars_timezone: "UTC",
    },
    matrixGrid: PRESET_EVENT_MATRIX_GRID,
  },
  {
    key: "choppy_day",
    title: "震荡日（区间噪音）",
    description: "更宽反应区 + 更高成交量确认，持仓略短、快进快出。",
    formPatch: {
      no_trade_first_minutes: 5,
      restricted_opening_minutes: 8,
      no_new_trades_after_enabled: true,
      no_new_trades_after_hour_et: 13,
      no_new_trades_after_minute_et: 0,
      max_hold_minutes: 35,
      max_trades_per_day: 2,
      volume_lookback_bars: 20,
      volume_spike_multiplier: 2.2,
      reaction_zone_width_pct: 0.12,
      psychological_step: 1,
      psychological_levels_max: 14,
      breakout_hold_bars: 3,
      reversal_pullback_pct: 0.12,
      gap_threshold_pct: 0.12,
      strike_step: 1,
      call_strikes_otm: 0,
      put_strikes_otm: 0,
      vol_window_bars: 30,
      min_sigma_pct: 11,
      take_profit_pct: 32,
      stop_loss_pct: 32,
      option_slippage_pct: 6,
      initial_option_contracts: 1,
      contract_multiplier: 100,
      log_decisions: true,
      assume_bars_timezone: "UTC",
    },
    matrixGrid: PRESET_CHOPPY_MATRIX_GRID,
  },
];

function formToStrategyConfig(f: StrategyFormState): Record<string, unknown> {
  return {
    strategy_variant: f.strategy_variant,
    strangle_entry_start_hhmm_et: String(f.strangle_entry_start_hhmm_et || "09:35").trim(),
    strangle_entry_end_hhmm_et: String(f.strangle_entry_end_hhmm_et || "10:00").trim(),
    strangle_force_close_hhmm_et: String(f.strangle_force_close_hhmm_et || "12:00").trim(),
    strangle_range_pct: Math.max(0, f.strangle_range_pct_ui / 100),
    strangle_take_profit_return: Math.max(0, f.strangle_take_profit_return_ui / 100),
    strangle_stop_loss_return: Math.max(0, f.strangle_stop_loss_return_ui / 100),
    strangle_stop_loss_cooldown_minutes: Math.max(0, Math.floor(f.strangle_stop_loss_cooldown_minutes)),
    strangle_underlying_field: f.strangle_underlying_field,
    directional_down_pct: Math.max(0, f.directional_down_pct_ui / 100),
    directional_up_pct: Math.max(0, f.directional_up_pct_ui / 100),
    directional_take_profit_return: Math.max(0, f.directional_take_profit_return_ui / 100),
    directional_stop_loss_pct: Math.max(0, f.directional_stop_loss_pct_ui / 100),
    gamma_entry_start_hhmm_et: String(f.gamma_entry_start_hhmm_et || "09:30").trim(),
    gamma_entry_end_hhmm_et: String(f.gamma_entry_end_hhmm_et || "10:00").trim(),
    gamma_force_close_hhmm_et: String(f.gamma_force_close_hhmm_et || "14:00").trim(),
    gamma_max_hold_minutes: Math.max(1, Math.floor(f.gamma_max_hold_minutes)),
    gamma_hard_stop_loss_pct: Math.max(0.01, f.gamma_hard_stop_loss_pct_ui / 100),
    gamma_take_profit_min_return: Math.max(0, f.gamma_take_profit_min_return_ui / 100),
    gamma_take_profit_max_return: Math.max(0, f.gamma_take_profit_max_return_ui / 100),
    gamma_call_otm_steps: Math.max(0, Math.floor(f.gamma_call_otm_steps)),
    gamma_put_otm_steps: Math.max(0, Math.floor(f.gamma_put_otm_steps)),
    gamma_require_breakout_prev_day: Boolean(f.gamma_require_breakout_prev_day),
    gamma_require_vix_rising: Boolean(f.gamma_require_vix_rising),
    gamma_vix_rising_min_pct: Math.max(0, f.gamma_vix_rising_min_pct),
    gamma_enable_vwap_reversion: Boolean(f.gamma_enable_vwap_reversion),
    gamma_vwap_deviation_pct: Math.max(0, f.gamma_vwap_deviation_pct_ui / 100),
    gamma_require_leader_confirmation: Boolean(f.gamma_require_leader_confirmation),
    gamma_leader_min_move_pct: Math.max(0, f.gamma_leader_min_move_pct),
    gamma_leader_lag_pct: Math.max(0, f.gamma_leader_lag_pct),
    gamma_pro_entry_start_hhmm_et: String(f.gamma_pro_entry_start_hhmm_et || "10:00").trim(),
    gamma_pro_entry_end_hhmm_et: String(f.gamma_pro_entry_end_hhmm_et || "15:30").trim(),
    gamma_pro_midday_skip_start_hhmm_et: String(f.gamma_pro_midday_skip_start_hhmm_et || "12:00").trim(),
    gamma_pro_midday_skip_end_hhmm_et: String(f.gamma_pro_midday_skip_end_hhmm_et || "13:00").trim(),
    gamma_pro_afternoon_start_hhmm_et: String(f.gamma_pro_afternoon_start_hhmm_et || "13:30").trim(),
    gamma_pro_force_close_hhmm_et: String(f.gamma_pro_force_close_hhmm_et || "15:45").trim(),
    gamma_pro_max_hold_minutes: Math.max(1, Math.floor(f.gamma_pro_max_hold_minutes)),
    gamma_pro_hard_stop_loss_pct: Math.max(0.01, f.gamma_pro_hard_stop_loss_pct_ui / 100),
    gamma_pro_take_profit_return: Math.max(0, f.gamma_pro_take_profit_return_ui / 100),
    gamma_pro_call_otm_steps: Math.max(0, Math.floor(f.gamma_pro_call_otm_steps)),
    gamma_pro_put_otm_steps: Math.max(0, Math.floor(f.gamma_pro_put_otm_steps)),
    gamma_pro_require_leader_confirmation: Boolean(f.gamma_pro_require_leader_confirmation),
    gamma_pro_enable_false_breakout_reversal: Boolean(f.gamma_pro_enable_false_breakout_reversal),
    gamma_pro_vwap_pullback_pct: Math.max(0, f.gamma_pro_vwap_pullback_pct_ui / 100),
    assume_bars_timezone: f.assume_bars_timezone,
    no_trade_first_minutes: Math.max(0, Math.floor(f.no_trade_first_minutes)),
    restricted_opening_minutes: Math.max(0, Math.floor(f.restricted_opening_minutes)),
    no_new_trades_after_enabled: Boolean(f.no_new_trades_after_enabled),
    no_new_trades_after_hour_et: Math.min(23, Math.max(0, Math.floor(f.no_new_trades_after_hour_et))),
    no_new_trades_after_minute_et: Math.min(59, Math.max(0, Math.floor(f.no_new_trades_after_minute_et))),
    max_hold_minutes: Math.max(1, Math.floor(f.max_hold_minutes)),
    max_trades_per_day: Math.max(1, Math.floor(f.max_trades_per_day)),
    reaction_zone_half_width_pct: Math.max(1e-6, f.reaction_zone_width_pct / 100),
    psychological_step: Math.max(0.01, f.psychological_step),
    psychological_levels_max: Math.max(1, Math.floor(f.psychological_levels_max)),
    volume_lookback_bars: Math.max(2, Math.floor(f.volume_lookback_bars)),
    volume_spike_multiplier: Math.max(0.1, f.volume_spike_multiplier),
    breakout_hold_bars: Math.max(2, Math.floor(f.breakout_hold_bars)),
    reversal_pullback_pct: Math.max(1e-6, f.reversal_pullback_pct / 100),
    gap_threshold_pct: Math.max(0, f.gap_threshold_pct / 100),
    strike_step: Math.max(0.01, f.strike_step),
    call_strikes_otm: Math.floor(f.call_strikes_otm),
    put_strikes_otm: Math.floor(f.put_strikes_otm),
    risk_free_rate: Math.max(0, f.risk_free_rate_pct / 100),
    dividend_yield: Math.max(0, f.dividend_yield_pct / 100),
    vol_window_bars: Math.max(2, Math.floor(f.vol_window_bars)),
    min_sigma: Math.max(0.01, f.min_sigma_pct / 100),
    option_expiry_hour_et: Math.min(23, Math.max(0, Math.floor(f.option_expiry_hour_et))),
    option_expiry_minute_et: Math.min(59, Math.max(0, Math.floor(f.option_expiry_minute_et))),
    take_profit_pct: Math.max(0.01, f.take_profit_pct / 100),
    stop_loss_pct: Math.max(0.01, f.stop_loss_pct / 100),
    option_slippage_pct: Math.max(0, f.option_slippage_pct / 100),
    initial_option_contracts: Math.max(1, Math.floor(f.initial_option_contracts)),
    contract_multiplier: Math.max(1, Math.floor(f.contract_multiplier)),
    log_decisions: Boolean(f.log_decisions),
  };
}

/** 与后端 `mcp_server/strategy_qqq_0dte/config.py` 中 Qqq0dteConfig 默认一致，用于补全「一键导出」缺失键。 */
const STRATEGY_CONFIG_BACKEND_DEFAULTS: Record<string, unknown> = {
  symbol: "QQQ.US",
  assume_bars_timezone: "UTC",
  rth_open_hour: 9,
  rth_open_minute: 30,
  rth_close_hour: 16,
  rth_close_minute: 0,
  no_trade_first_minutes: 2,
  restricted_opening_minutes: 5,
  no_new_trades_after_enabled: false,
  no_new_trades_after_hour_et: 12,
  no_new_trades_after_minute_et: 0,
  max_hold_minutes: 60,
  max_trades_per_day: 2,
  reaction_zone_half_width_pct: 0.0008,
  psychological_step: 2.5,
  psychological_levels_max: 12,
  volume_lookback_bars: 20,
  volume_spike_multiplier: 2.0,
  breakout_hold_bars: 2,
  reversal_pullback_pct: 0.0015,
  gap_threshold_pct: 0.002,
  strike_step: 1.0,
  call_strikes_otm: 0,
  put_strikes_otm: 0,
  risk_free_rate: 0.052,
  dividend_yield: 0.0,
  vol_window_bars: 30,
  min_sigma: 0.12,
  option_expiry_hour_et: 16,
  option_expiry_minute_et: 0,
  take_profit_pct: 0.4,
  stop_loss_pct: 0.35,
  option_slippage_pct: 0.05,
  initial_option_contracts: 1,
  contract_multiplier: 100,
  log_decisions: true,
  strategy_variant: "reaction_zone",
  strangle_entry_start_hhmm_et: "09:35",
  strangle_entry_end_hhmm_et: "10:00",
  strangle_force_close_hhmm_et: "12:00",
  strangle_range_pct: 0.003,
  strangle_take_profit_return: 1.0,
  strangle_stop_loss_return: 0.0,
  strangle_stop_loss_cooldown_minutes: 0,
  strangle_leg_take_profit_pct: 0.0,
  strangle_leg_stop_loss_pct: 0.0,
  strangle_underlying_field: "low",
  directional_down_pct: 0.01,
  directional_up_pct: 0.01,
  directional_take_profit_return: 1.0,
  directional_stop_loss_pct: 0.0,
  gamma_entry_start_hhmm_et: "09:30",
  gamma_entry_end_hhmm_et: "10:00",
  gamma_force_close_hhmm_et: "14:00",
  gamma_max_hold_minutes: 15,
  gamma_hard_stop_loss_pct: 0.3,
  gamma_take_profit_min_return: 0.5,
  gamma_take_profit_max_return: 1.0,
  gamma_call_otm_steps: 1,
  gamma_put_otm_steps: 1,
  gamma_require_breakout_prev_day: true,
  gamma_require_vix_rising: true,
  gamma_vix_rising_min_pct: 0.3,
  gamma_enable_vwap_reversion: true,
  gamma_vwap_deviation_pct: 0.003,
  gamma_require_leader_confirmation: true,
  gamma_leader_min_move_pct: 0.6,
  gamma_leader_lag_minutes: 2,
  gamma_leader_lag_pct: 0.1,
  gamma_vix_symbol: "VIX.US",
  gamma_leader_symbol_1: "NVDA.US",
  gamma_leader_symbol_2: "TSLA.US",
  gamma_rt_vix_change_pct: 0.0,
  gamma_rt_qqq_change_pct: 0.0,
  gamma_rt_leader1_change_pct: 0.0,
  gamma_rt_leader2_change_pct: 0.0,
  gamma_pro_entry_start_hhmm_et: "10:00",
  gamma_pro_entry_end_hhmm_et: "15:30",
  gamma_pro_midday_skip_start_hhmm_et: "12:00",
  gamma_pro_midday_skip_end_hhmm_et: "13:00",
  gamma_pro_afternoon_start_hhmm_et: "13:30",
  gamma_pro_force_close_hhmm_et: "15:45",
  gamma_pro_max_hold_minutes: 45,
  gamma_pro_hard_stop_loss_pct: 0.3,
  gamma_pro_take_profit_return: 0.6,
  gamma_pro_call_otm_steps: 1,
  gamma_pro_put_otm_steps: 1,
  gamma_pro_require_leader_confirmation: true,
  gamma_pro_enable_false_breakout_reversal: true,
  gamma_pro_vwap_pullback_pct: 0.0015,
};

/**
 * 后端默认值 ⊕ 表单 formToStrategyConfig ⊕ 高级 JSON 覆盖（后者可覆盖前者）；
 * 再强制使用表单上的 `assume_bars_timezone`、`strategy_variant` 与页面标的 `symbol`；
 * 最后按当前变体白名单输出「键齐全」的对象（缺键用后端默认补），便于粘贴 live_worker_config.json。
 */
function buildCompleteStrategyConfigForPaste(
  strategy: StrategyFormState,
  pageSymbol: string,
  advancedJsonText: string
): { ok: true; config: Record<string, unknown> } | { ok: false; error: string } {
  let extra: Record<string, unknown> = {};
  const trimmedAdv = advancedJsonText.trim();
  if (trimmedAdv) {
    try {
      const p = JSON.parse(stripStrategyConfigHashComments(trimmedAdv)) as unknown;
      if (typeof p !== "object" || p === null || Array.isArray(p)) {
        return { ok: false, error: "高级 JSON 须为对象，无法合并导出。" };
      }
      extra = p as Record<string, unknown>;
    } catch {
      return { ok: false, error: "高级 JSON 语法无效，无法合并导出。" };
    }
  }

  const defaults = STRATEGY_CONFIG_BACKEND_DEFAULTS;
  const fromForm = formToStrategyConfig(strategy);
  const sym = pageSymbol.trim() || String(defaults.symbol ?? "QQQ.US");

  const merged: Record<string, unknown> = {
    ...defaults,
    ...fromForm,
    ...extra,
    symbol: sym,
    assume_bars_timezone: strategy.assume_bars_timezone,
    strategy_variant: strategy.strategy_variant,
  };

  const variant = strategy.strategy_variant;
  const allow = allowedKeysForStrategyVariant(variant);
  const out: Record<string, unknown> = {};
  for (const k of [...allow].sort()) {
    if (Object.prototype.hasOwnProperty.call(merged, k)) {
      out[k] = merged[k];
    } else if (Object.prototype.hasOwnProperty.call(defaults, k)) {
      out[k] = defaults[k];
    }
  }
  return { ok: true, config: out };
}

/**
 * 高级 JSON 清理：仅去掉「不属于当前表单策略变体白名单」的键；保留本策略全部字段（含与默认相同项、strategy_variant、时区等），便于整段粘贴到 live_worker_config.json。
 */
function pruneAdvancedJsonText(
  jsonText: string,
  variant: StrategyFormState["strategy_variant"]
): { ok: true; text: string; droppedInvalid: number } | { ok: false; error: string } {
  const trimmed = jsonText.trim();
  if (!trimmed) return { ok: true, text: "", droppedInvalid: 0 };
  let extra: Record<string, unknown>;
  try {
    const p = JSON.parse(stripStrategyConfigHashComments(trimmed)) as unknown;
    if (typeof p !== "object" || p === null || Array.isArray(p)) {
      return { ok: false, error: "高级 JSON 须为对象 {...}，无法清理。" };
    }
    extra = p as Record<string, unknown>;
  } catch {
    return { ok: false, error: "JSON 语法无效，无法清理。" };
  }

  const allow = allowedKeysForStrategyVariant(variant);
  let droppedInvalid = 0;
  const out: Record<string, unknown> = {};

  for (const k of Object.keys(extra)) {
    if (!allow.has(k)) {
      droppedInvalid += 1;
      continue;
    }
    out[k] = extra[k];
  }

  return {
    ok: true,
    text: Object.keys(out).length ? stringifyStrategyConfigWithHints(out) : "",
    droppedInvalid,
  };
}

/** 快照表格/保存提示等展示用策略名（读 strategy_config.strategy_variant） */
function qqq0dteStrategyDisplayName(strategy_config: Record<string, unknown> | undefined | null): string {
  if (!strategy_config || typeof strategy_config !== "object") return "反应区";
  const v = strategy_config.strategy_variant;
  if (v === "morning_strangle") return "早盘宽跨";
  if (v === "morning_directional") return "早盘方向单";
  if (v === "gamma_scalping") return "Gamma 剥头皮";
  if (v === "gamma_pro") return "Gamma Pro";
  if (v === "reaction_zone") return "反应区";
  if (typeof v === "string" && v.trim()) return v.trim();
  return "反应区";
}

function tryParseStrategyFormFromLegacyJson(json: string): Partial<StrategyFormState> | null {
  try {
    const o = JSON.parse(json) as Record<string, unknown>;
    if (!o || typeof o !== "object") return null;
    const p: Partial<StrategyFormState> = {};
    const num = (k: string) => (typeof o[k] === "number" ? (o[k] as number) : undefined);
    if (num("no_trade_first_minutes") != null) p.no_trade_first_minutes = num("no_trade_first_minutes")!;
    if (num("restricted_opening_minutes") != null) p.restricted_opening_minutes = num("restricted_opening_minutes")!;
    if (num("max_hold_minutes") != null) p.max_hold_minutes = num("max_hold_minutes")!;
    if (num("max_trades_per_day") != null) p.max_trades_per_day = num("max_trades_per_day")!;
    if (num("volume_spike_multiplier") != null) p.volume_spike_multiplier = num("volume_spike_multiplier")!;
    if (num("take_profit_pct") != null) p.take_profit_pct = (o.take_profit_pct as number) * 100;
    if (num("stop_loss_pct") != null) p.stop_loss_pct = (o.stop_loss_pct as number) * 100;
    return Object.keys(p).length ? p : null;
  } catch {
    return null;
  }
}

type DecisionSummaryRow = {
  message?: string;
  label_zh?: string;
  count?: number;
  pct_of_logs?: number;
  pct_of_bars?: number;
  is_entry_blocker?: boolean;
};

type SnapshotMetrics = {
  realized_pnl?: number;
  total_fee?: number;
  bar_count?: number;
  closed_trades?: number;
  win_rate_pct?: number;
  return_pct?: number | null;
  /** 回测内累计开仓权利金（美元）；与后端 metrics 一致 */
  open_premium_debit_usd?: number | null;
  /** 旧快照：曾用假定本金计算 return_pct */
  backtest_capital_usd?: number | null;
};

type SnapshotRun = {
  id?: string;
  created_at?: string;
  request?: Record<string, unknown>;
  strategy_config?: Record<string, unknown>;
  metrics?: SnapshotMetrics;
};

type TopSnapshotsResponse = {
  sort?: string;
  top_n?: number;
  total_stored?: number;
  runs?: SnapshotRun[];
};

type MatrixRow = {
  grid_params?: Record<string, unknown>;
  strategy_config?: Record<string, unknown>;
  realized_pnl?: number;
  return_pct?: number | null;
  open_premium_debit_usd?: number | null;
  closed_trades?: number;
  win_rate_pct?: number;
  total_fee?: number;
  open_events?: number;
  close_events?: number;
};

type MatrixResponse = {
  symbol?: string;
  combinations_run?: number;
  sort_by?: string;
  rth_only?: boolean;
  bar_count_total?: number;
  bar_count_rth?: number;
  bar_count_non_rth?: number;
  bar_count_first?: number;
  top?: MatrixRow[];
  disclaimer?: string;
};

type DecisionSummary = {
  log_decisions_enabled?: boolean;
  total_log_lines?: number;
  bar_count?: number;
  by_message?: DecisionSummaryRow[];
  entry_blocker?: {
    total_hits?: number;
    pct_of_logs?: number;
    hint?: string;
  };
  preview_tail?: Array<{
    bar_index?: number;
    as_of?: string;
    message?: string;
    label_zh?: string;
  }>;
};

type BacktestResponse = {
  symbol?: string;
  bar_count?: number;
  rth_only?: boolean;
  bar_count_total?: number;
  bar_count_rth?: number;
  bar_count_non_rth?: number;
  open_events?: number;
  close_events?: number;
  realized_pnl?: number;
  total_fee?: number;
  stats?: {
    closed_trades?: number;
    wins?: number;
    losses?: number;
    win_rate_pct?: number;
  };
  trades?: Array<Record<string, unknown>>;
  decision_summary?: DecisionSummary;
  return_pct?: number | null;
  open_premium_debit_usd?: number | null;
  snapshot?: { saved?: boolean; id?: string; created_at?: string };
  disclaimer?: string;
  config?: Record<string, unknown>;
  detail?: unknown;
};

function NumField({
  label,
  hint,
  value,
  onChange,
  min,
  max,
  step,
}: {
  label: string;
  hint?: string;
  value: number;
  onChange: (n: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <label className="space-y-1">
      <div className="field-label">{label}</div>
      <input
        className="input-base"
        type="number"
        min={min}
        max={max}
        step={step ?? 1}
        value={Number.isFinite(value) ? value : 0}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      {hint ? <p className="text-[11px] leading-snug text-slate-500">{hint}</p> : null}
    </label>
  );
}

export default function Qqq0dteStrategyPage() {
  const [symbol, setSymbol] = useState("QQQ.US");
  const [days, setDays] = useState(5);
  const [periods, setPeriods] = useState(0);
  const [kline, setKline] = useState("1m");
  const [useServerKline, setUseServerKline] = useState(false);
  const [rthOnly, setRthOnly] = useState(false);
  const [strategy, setStrategy] = useState<StrategyFormState>(DEFAULT_FORM);
  const [showMore, setShowMore] = useState(false);
  const [showAdvancedJson, setShowAdvancedJson] = useState(false);
  const [advancedJson, setAdvancedJson] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<BacktestResponse | null>(null);

  const [resolveStrike, setResolveStrike] = useState(500);
  const [resolveRight, setResolveRight] = useState<"call" | "put">("call");
  const [resolveExpiry, setResolveExpiry] = useState("");
  const [resolveLoading, setResolveLoading] = useState(false);
  const [resolveErr, setResolveErr] = useState("");
  const [resolveOut, setResolveOut] = useState<Record<string, unknown> | null>(null);

  const [saveSnapshot, setSaveSnapshot] = useState(true);
  const [snapshotSort, setSnapshotSort] = useState<"realized_pnl" | "return_pct">("realized_pnl");
  const [topSnapshots, setTopSnapshots] = useState<TopSnapshotsResponse | null>(null);
  const [snapshotsLoading, setSnapshotsLoading] = useState(false);
  const [snapshotsErr, setSnapshotsErr] = useState("");

  const [matrixAxisRows, setMatrixAxisRows] = useState<MatrixAxisRowState[]>(() => defaultMatrixAxisRows());
  /** 矩阵删选固定使用的策略变体（与主表单「策略变体」独立） */
  const [matrixStrategySelect, setMatrixStrategySelect] = useState<MatrixStrategyVariant>("reaction_zone");
  const [matrixUseJsonMode, setMatrixUseJsonMode] = useState(false);
  const [matrixAdvancedJsonOpen, setMatrixAdvancedJsonOpen] = useState(false);
  const [matrixGridJson, setMatrixGridJson] = useState(DEFAULT_MATRIX_GRID_JSON);
  const [matrixTopN, setMatrixTopN] = useState(15);
  const [matrixMaxComb, setMatrixMaxComb] = useState(2000);
  const [matrixSort, setMatrixSort] = useState<"realized_pnl" | "return_pct">("realized_pnl");
  const [matrixLoading, setMatrixLoading] = useState(false);
  const [matrixErr, setMatrixErr] = useState("");
  const [matrixResult, setMatrixResult] = useState<MatrixResponse | null>(null);
  const [matrixLiveSyncMsg, setMatrixLiveSyncMsg] = useState("");
  const [matrixLiveSyncErr, setMatrixLiveSyncErr] = useState("");
  const [matrixLiveSyncRow, setMatrixLiveSyncRow] = useState<number | null>(null);
  const [loadConfigToast, setLoadConfigToast] = useState("");

  const patchStrategy = useCallback((patch: Partial<StrategyFormState>) => {
    setStrategy((s) => ({ ...s, ...patch }));
  }, []);

  const onMatrixStrategySelectChange = useCallback((v: MatrixStrategyVariant) => {
    setMatrixStrategySelect(v);
    setMatrixAxisRows((prev) =>
      prev.map((row) =>
        matrixDefAppliesToVariant(row.key, v)
          ? { ...row, enabled: recommendedMatrixRowEnabled(v, row.key) }
          : row
      )
    );
  }, []);

  const showLoadConfigToast = useCallback((msg: string) => {
    setLoadConfigToast(msg);
  }, []);

  const onPruneAdvancedJson = useCallback(() => {
    const r = pruneAdvancedJsonText(advancedJson, strategy.strategy_variant);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setError("");
    setAdvancedJson(r.text);
    const parts = ["已按变体白名单清理高级 JSON"];
    if (r.droppedInvalid > 0) parts.push(`去掉非本策略键 ${r.droppedInvalid} 个`);
    if (!r.text) parts.push("（无本策略键可保留，覆盖区已清空）");
    showLoadConfigToast(parts.join(" · "));
  }, [advancedJson, strategy.strategy_variant, showLoadConfigToast]);

  const onExportCompleteStrategyConfig = useCallback(() => {
    const r = buildCompleteStrategyConfigForPaste(strategy, symbol, advancedJson);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setError("");
    setAdvancedJson(stringifyStrategyConfigWithHints(r.config));
    setShowAdvancedJson(true);
    showLoadConfigToast(
      `已生成完整 strategy_config（${Object.keys(r.config).length} 项，按表单变体白名单），可粘贴至 live_worker_config.json`
    );
  }, [strategy, symbol, advancedJson, showLoadConfigToast]);

  const applyPreset = useCallback((presetKey: StrategyPreset["key"]) => {
    const preset = STRATEGY_PRESETS.find((p) => p.key === presetKey);
    if (!preset) return;
    setStrategy((s) => ({ ...s, ...preset.formPatch }));
    setAdvancedJson("");
    setShowAdvancedJson(false);
    setMatrixUseJsonMode(true);
    setMatrixAdvancedJsonOpen(true);
    setMatrixGridJson(JSON.stringify(preset.matrixGrid, null, 2));
    setMatrixErr("");
  }, []);

  const syncMatrixRowToLiveWorker = useCallback(
    async (row: MatrixRow, rowIndex: number) => {
      setMatrixLiveSyncErr("");
      setMatrixLiveSyncMsg("");
      const sc = row.strategy_config;
      if (!sc || typeof sc !== "object" || Array.isArray(sc)) {
        setMatrixLiveSyncErr("该行缺少 strategy_config，无法同步到实盘配置。");
        return;
      }
      setMatrixLiveSyncRow(rowIndex);
      try {
        const strategy_config = { ...sc, log_decisions: true };
        await apiPut(
          "/strategy/qqq-0dte/live-worker-config",
          {
            symbol: symbol.trim(),
            kline,
            strategy_config,
          },
          { timeoutMs: 20000, retries: 0 }
        );
        setMatrixLiveSyncMsg(
          `已写入 live_worker_config.json（矩阵第 ${rowIndex + 1} 名的完整 strategy_config，且已开启 log_decisions；标的/K 线已与当前页一致）。请到上方「实盘自动交易」刷新配置后再启动 Worker。`
        );
      } catch (e: unknown) {
        setMatrixLiveSyncErr(e instanceof Error ? e.message : String(e));
      } finally {
        setMatrixLiveSyncRow(null);
      }
    },
    [symbol, kline]
  );

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const p = JSON.parse(raw);
        if (typeof p.symbol === "string") setSymbol(p.symbol);
        if (Number.isFinite(Number(p.days))) setDays(Number(p.days));
        if (Number.isFinite(Number(p.periods))) setPeriods(Number(p.periods));
        if (typeof p.kline === "string") setKline(p.kline);
        if (typeof p.useServerKline === "boolean") setUseServerKline(p.useServerKline);
        if (typeof p.rthOnly === "boolean") setRthOnly(p.rthOnly);
        if (p.strategy && typeof p.strategy === "object") {
          const merged = { ...DEFAULT_FORM, ...p.strategy } as StrategyFormState;
          if (p.useServerKline === true && merged.assume_bars_timezone === "America/New_York") {
            merged.assume_bars_timezone = "UTC";
          }
          setStrategy(merged);
        }
        if (typeof p.advancedJson === "string") setAdvancedJson(p.advancedJson);
        if (typeof p.showMore === "boolean") setShowMore(p.showMore);
        if (typeof p.showAdvancedJson === "boolean") setShowAdvancedJson(p.showAdvancedJson);
        if (Array.isArray(p.matrixAxisRows) && p.matrixAxisRows.length > 0) {
          const saved = p.matrixAxisRows as MatrixAxisRowState[];
          const merged = defaultMatrixAxisRows().map((def) => {
            const found = saved.find((x) => x && x.key === def.key);
            if (!found) return def;
            return {
              ...def,
              enabled: typeof found.enabled === "boolean" ? found.enabled : def.enabled,
              valuesText: typeof found.valuesText === "string" ? found.valuesText : def.valuesText,
              valueKind: def.valueKind,
            };
          });
          setMatrixAxisRows(merged);
        }
        if (typeof p.matrixUseJsonMode === "boolean") setMatrixUseJsonMode(p.matrixUseJsonMode);
        if (typeof p.matrixGridJson === "string") setMatrixGridJson(p.matrixGridJson);
        if (typeof p.matrixAdvancedJsonOpen === "boolean") setMatrixAdvancedJsonOpen(p.matrixAdvancedJsonOpen);
        if (Number.isFinite(Number(p.matrixTopN))) setMatrixTopN(Number(p.matrixTopN));
        if (Number.isFinite(Number(p.matrixMaxComb))) setMatrixMaxComb(Number(p.matrixMaxComb));
        if (p.matrixSort === "realized_pnl" || p.matrixSort === "return_pct") setMatrixSort(p.matrixSort);
        if (
          p.matrixStrategySelect === "reaction_zone" ||
          p.matrixStrategySelect === "morning_strangle" ||
          p.matrixStrategySelect === "morning_directional" ||
          p.matrixStrategySelect === "gamma_scalping" ||
          p.matrixStrategySelect === "gamma_pro"
        ) {
          setMatrixStrategySelect(p.matrixStrategySelect);
        }
        return;
      }
      const legacy = localStorage.getItem("qqq_0dte_test_form_v1");
      if (legacy) {
        const p = JSON.parse(legacy);
        if (typeof p.strategyJson === "string") {
          const merged = tryParseStrategyFormFromLegacyJson(p.strategyJson);
          if (merged) setStrategy((s) => ({ ...s, ...merged }));
        }
      }
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          symbol,
          days,
          periods,
          kline,
          useServerKline,
          rthOnly,
          strategy,
          advancedJson,
          showMore,
          showAdvancedJson,
          matrixAxisRows,
          matrixUseJsonMode,
          matrixGridJson,
          matrixAdvancedJsonOpen,
          matrixTopN,
          matrixMaxComb,
          matrixSort,
          matrixStrategySelect,
        })
      );
    } catch {
      /* ignore */
    }
  }, [
    symbol,
    days,
    periods,
    kline,
    useServerKline,
    rthOnly,
    strategy,
    advancedJson,
    showMore,
    showAdvancedJson,
    matrixAxisRows,
    matrixUseJsonMode,
    matrixGridJson,
    matrixAdvancedJsonOpen,
    matrixTopN,
    matrixMaxComb,
    matrixSort,
    matrixStrategySelect,
  ]);

  useEffect(() => {
    if (!loadConfigToast) return;
    const tid = window.setTimeout(() => setLoadConfigToast(""), 2200);
    return () => window.clearTimeout(tid);
  }, [loadConfigToast]);

  const fetchTopSnapshots = useCallback(async () => {
    setSnapshotsErr("");
    setSnapshotsLoading(true);
    try {
      const r = await apiGet<TopSnapshotsResponse>(
        `/strategy/qqq-0dte/snapshots/top?top=5&sort=${encodeURIComponent(snapshotSort)}`,
        { cacheTtlMs: 0, timeoutMs: 30000, retries: 1 }
      );
      setTopSnapshots(r);
    } catch (e: unknown) {
      setSnapshotsErr(e instanceof Error ? e.message : String(e));
      setTopSnapshots(null);
    } finally {
      setSnapshotsLoading(false);
    }
  }, [snapshotSort]);

  useEffect(() => {
    void fetchTopSnapshots();
  }, [fetchTopSnapshots]);

  const applyMatrixRow = useCallback((row: MatrixRow) => {
    const sc = row.strategy_config;
    if (sc && typeof sc === "object" && !Array.isArray(sc)) {
      setAdvancedJson(stringifyStrategyConfigWithHints(sc));
      setShowAdvancedJson(true);
      showLoadConfigToast("已载入到高级 JSON");
    }
  }, [showLoadConfigToast]);

  const applySnapshotRun = useCallback((run: SnapshotRun) => {
    const req = run.request;
    if (req && typeof req === "object") {
      if (typeof req.symbol === "string") setSymbol(req.symbol);
      if (Number.isFinite(Number(req.days))) setDays(Math.max(1, Math.floor(Number(req.days))));
      if (Number.isFinite(Number(req.periods))) setPeriods(Math.max(0, Math.floor(Number(req.periods))));
      if (typeof req.kline === "string") setKline(req.kline);
      if (typeof req.use_server_kline_cache === "boolean") setUseServerKline(req.use_server_kline_cache);
    }
    const sc = run.strategy_config;
    if (sc && typeof sc === "object" && !Array.isArray(sc)) {
      setAdvancedJson(stringifyStrategyConfigWithHints(sc));
      setShowAdvancedJson(true);
      showLoadConfigToast("已载入到高级 JSON");
    }
  }, [showLoadConfigToast]);

  /** 高级 JSON 与表单冲突提示；实际请求以表单时区与策略变体为准，并会按变体剔除无关键。 */
  const jsonTimezoneConflictHint = useMemo((): string | null => {
    if (!advancedJson.trim()) return null;
    try {
      const extra = JSON.parse(stripStrategyConfigHashComments(advancedJson)) as Record<string, unknown>;
      if (typeof extra !== "object" || extra === null || Array.isArray(extra)) return null;
      const parts: string[] = [];
      const j = extra.assume_bars_timezone;
      if (typeof j === "string" && j !== strategy.assume_bars_timezone) {
        parts.push(
          `assume_bars_timezone="${j}" 与表单「${strategy.assume_bars_timezone}」不一致，请求中已改用表单时区。`
        );
      }
      const sv = extra.strategy_variant;
      if (
        typeof sv === "string" &&
        sv !== strategy.strategy_variant &&
        (
          sv === "reaction_zone" ||
          sv === "morning_strangle" ||
          sv === "morning_directional" ||
          sv === "gamma_scalping" ||
          sv === "gamma_pro"
        )
      ) {
        parts.push(`strategy_variant="${sv}" 与表单「${strategy.strategy_variant}」不一致，请求中已改用表单变体并剔除无关键。`);
      }
      if (!parts.length) return null;
      return parts.join(" ");
    } catch {
      return null;
    }
  }, [advancedJson, strategy.assume_bars_timezone, strategy.strategy_variant]);

  const strategyConfig = useMemo(() => {
    const base = formToStrategyConfig(strategy);
    const variant = strategy.strategy_variant;
    if (!advancedJson.trim()) {
      return filterStrategyConfigByVariant(variant, base);
    }
    try {
      const extra = JSON.parse(stripStrategyConfigHashComments(advancedJson)) as Record<string, unknown>;
      if (typeof extra !== "object" || extra === null || Array.isArray(extra)) {
        return filterStrategyConfigByVariant(variant, base);
      }
      const merged: Record<string, unknown> = {
        ...base,
        ...extra,
        assume_bars_timezone: strategy.assume_bars_timezone,
        strategy_variant: variant,
      };
      return filterStrategyConfigByVariant(variant, merged);
    } catch {
      return null;
    }
  }, [strategy, advancedJson]);

  /** 矩阵请求专用：按矩阵顶部所选策略过滤/合并后的基线 config */
  const strategyConfigForMatrix = useMemo(() => {
    const base = formToStrategyConfig(strategy);
    const variant = matrixStrategySelect;
    if (!advancedJson.trim()) {
      return filterStrategyConfigByVariant(variant, { ...base, strategy_variant: variant });
    }
    try {
      const extra = JSON.parse(stripStrategyConfigHashComments(advancedJson)) as Record<string, unknown>;
      if (typeof extra !== "object" || extra === null || Array.isArray(extra)) {
        return filterStrategyConfigByVariant(variant, { ...base, strategy_variant: variant });
      }
      const merged: Record<string, unknown> = {
        ...base,
        ...extra,
        assume_bars_timezone: strategy.assume_bars_timezone,
        strategy_variant: variant,
      };
      return filterStrategyConfigByVariant(variant, merged);
    } catch {
      return null;
    }
  }, [strategy, advancedJson, matrixStrategySelect]);

  const matrixGridPreviewJson = useMemo(() => {
    try {
      return JSON.stringify(buildGridFromMatrixRows(matrixAxisRows, matrixStrategySelect), null, 2);
    } catch {
      return null;
    }
  }, [matrixAxisRows, matrixStrategySelect]);

  const patchMatrixAxisRow = useCallback((idx: number, patch: Partial<MatrixAxisRowState>) => {
    setMatrixAxisRows((rows) => rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  }, []);

  const resetMatrixAxisDefaults = useCallback(() => {
    setMatrixAxisRows(
      defaultMatrixAxisRows().map((row) => ({
        ...row,
        enabled:
          matrixDefAppliesToVariant(row.key, matrixStrategySelect) &&
          recommendedMatrixRowEnabled(matrixStrategySelect, row.key),
      }))
    );
    setMatrixErr("");
  }, [matrixStrategySelect]);

  const runMatrix = async () => {
    setMatrixErr("");
    setMatrixResult(null);
    if (strategyConfigForMatrix === null) {
      setMatrixErr("高级 JSON 无效，无法作为矩阵基线 strategy_config。");
      return;
    }
    let grid: Record<string, unknown[]>;
    try {
      if (matrixUseJsonMode) {
        const g = JSON.parse(matrixGridJson) as unknown;
        if (typeof g !== "object" || g === null || Array.isArray(g)) {
          throw new Error("grid 须为 JSON 对象");
        }
        grid = g as Record<string, unknown[]>;
        for (const [k, vals] of Object.entries(grid)) {
          if (!Array.isArray(vals) || vals.length === 0) {
            throw new Error(`参数「${k}」的候选列表不能为空`);
          }
        }
        const allowedKeys = matrixKeysForVariant(matrixStrategySelect);
        for (const k of Object.keys(grid)) {
          if (!allowedKeys.has(k)) {
            throw new Error(`grid 键「${k}」与当前「矩阵针对策略」不匹配，请改下拉或删去该键`);
          }
        }
      } else {
        grid = buildGridFromMatrixRows(matrixAxisRows, matrixStrategySelect) as unknown as Record<
          string,
          unknown[]
        >;
      }
    } catch (e: unknown) {
      setMatrixErr(e instanceof Error ? e.message : "参数解析失败");
      return;
    }
    setMatrixLoading(true);
    try {
      const payload: Record<string, unknown> = {
        symbol: symbol.trim(),
        days: Math.max(1, Math.floor(Number(days) || 1)),
        periods: Math.max(0, Math.floor(Number(periods) || 0)),
        kline,
        use_server_kline_cache: useServerKline,
        rth_only: rthOnly,
        strategy_config: strategyConfigForMatrix,
        grid,
        top_n: Math.max(1, Math.min(100, Math.floor(Number(matrixTopN) || 15))),
        sort_by: matrixSort,
        max_combinations: Math.max(1, Math.min(10000, Math.floor(Number(matrixMaxComb) || 2000))),
        suppress_logs: true,
      };
      const r = await apiPost<MatrixResponse>("/strategy/qqq-0dte/matrix", payload, {
        timeoutMs: 600000,
        retries: 0,
      });
      setMatrixResult(r);
    } catch (e: unknown) {
      setMatrixErr(e instanceof Error ? e.message : String(e));
    } finally {
      setMatrixLoading(false);
    }
  };

  const runBacktest = async () => {
    setError("");
    setResult(null);
    if (strategyConfig === null) {
      setError("高级 JSON 覆盖格式无效，请检查 JSON 对象语法。");
      return;
    }
    setLoading(true);
    try {
      const payload: Record<string, unknown> = {
        symbol: symbol.trim(),
        days: Math.max(1, Math.floor(Number(days) || 1)),
        periods: Math.max(0, Math.floor(Number(periods) || 0)),
        kline,
        use_server_kline_cache: useServerKline,
        rth_only: rthOnly,
        strategy_config: strategyConfig,
        save_snapshot: saveSnapshot,
      };
      const task = await apiPost<any>("/backtests", { kind: "qqq_0dte_strategy", request: payload }, {
        timeoutMs: 300000,
        retries: 0,
      });
      const r = (task?.result?.raw || task) as BacktestResponse;
      setResult(r);
      void fetchTopSnapshots();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const runResolveContract = async () => {
    setResolveErr("");
    setResolveOut(null);
    setResolveLoading(true);
    try {
      const payload: Record<string, unknown> = {
        symbol: symbol.trim(),
        strike: Math.max(0.01, Number(resolveStrike) || 0),
        right: resolveRight,
      };
      const exp = resolveExpiry.trim();
      if (exp) payload.expiry_date = exp;
      const r = await apiPost<Record<string, unknown>>("/strategy/qqq-0dte/resolve-contract", payload, {
        timeoutMs: 120000,
        retries: 0,
      });
      setResolveOut(r);
      if (r && r.ok === false && typeof r.error === "string") setResolveErr(r.error);
    } catch (e: unknown) {
      setResolveErr(e instanceof Error ? e.message : String(e));
    } finally {
      setResolveLoading(false);
    }
  };

  const trades = result?.trades ?? [];

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">QQQ 0DTE 自动期权交易</h1>
            <div className="mt-1 text-sm text-slate-300">
              <strong className="text-slate-200">策略验证</strong>：策略回测、参数矩阵和回测快照用于上线前验证自动交易参数；调用{" "}
              <span className="font-mono text-cyan-200/90">POST /strategy/qqq-0dte/backtest</span>
              ，标的 K 线 + 模块内 BS 合成期权价（与实盘报价不同）。通用期权结构测试请使用{" "}
              <span className="font-mono text-slate-400">/options</span> 的组合回测。
            </div>
          </div>
        </div>
      </div>

      {error ? (
        <div className="panel border-rose-500/40 bg-rose-950/30 text-sm text-rose-200">{error}</div>
      ) : null}

      {loadConfigToast ? (
        <div className="fixed right-5 top-5 z-50 rounded-lg border border-emerald-400/35 bg-emerald-950/90 px-3 py-2 text-sm text-emerald-100 shadow-lg">
          {loadConfigToast}
        </div>
      ) : null}

      <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/5 px-4 py-3">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="text-3xl font-bold text-emerald-100">实盘自动交易</div>
            <div className="text-xs leading-relaxed text-emerald-100/70">
              Worker 状态、启停、实盘参数与下单安全集中在这里；这里的操作会影响运行中的自动交易。
            </div>
          </div>
          <span className="w-fit rounded-full border border-emerald-400/40 bg-emerald-400/10 px-2 py-0.5 text-xs text-emerald-100">
            影响实盘运行
          </span>
        </div>
      </div>

      <Qqq0dteLiveAutoPanel pageSymbol={symbol} pageKline={kline} strategyConfig={strategyConfig} />

      <div className="rounded-xl border border-cyan-500/25 bg-cyan-500/5 px-4 py-3">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="text-3xl font-bold text-cyan-100">Agent Strategy Lab</div>
            <div className="text-xs leading-relaxed text-cyan-100/70">
              日常参数研究统一从这里开始：自动生成候选、验证 60/120/180 天、审批前 diff，并只写入配置草稿。
            </div>
          </div>
          <a className="btn-primary w-fit" href="/agent-strategy-lab">
            打开 Lab
          </a>
        </div>
      </div>

      <details className="space-y-4">
        <summary className="cursor-pointer select-none list-none rounded-xl border border-slate-700/80 bg-slate-950/40 px-4 py-3 [&::-webkit-details-marker]:hidden">
          <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div>
              <div className="text-xl font-semibold text-slate-100">高级调试 / 手动回测</div>
              <div className="mt-1 text-xs leading-relaxed text-slate-500">
                保留旧策略验证平台用于 OPRA 解析、参数矩阵、快照查看和单次手动回测；默认折叠，不作为日常参数研究主入口。
              </div>
            </div>
            <span className="w-fit rounded-full border border-slate-600 bg-slate-900 px-2 py-0.5 text-xs text-slate-300">
              点击展开
            </span>
          </div>
        </summary>
        <div className="space-y-4">
      <div className="rounded-xl border border-violet-500/25 bg-violet-500/5 px-4 py-3">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="text-3xl font-bold text-violet-100">策略验证工作台</div>
            <div className="text-xs leading-relaxed text-violet-100/70">
              快照、行情范围、OPRA 解析、参数矩阵和策略回测都放在验证区；这里只做验证和参数准备，不会触发下单。
            </div>
          </div>
          <span className="w-fit rounded-full border border-violet-400/40 bg-violet-400/10 px-2 py-0.5 text-xs text-violet-100">
            高级调试
          </span>
        </div>
      </div>

      <div className="panel space-y-3 border-emerald-500/20">
        <details>
          <summary className="cursor-pointer select-none list-none [&::-webkit-details-marker]:hidden">
            <span className="section-title inline">
              <span className="mr-1.5 text-emerald-400/80">▸</span>
              策略验证快照 · TOP5
              <span className="ml-2 text-sm font-normal text-slate-500">（表内「策略」列为策略名称，便于区分）</span>
            </span>
            <span className="ml-2 text-xs font-normal text-slate-500">点击展开</span>
          </summary>
          <div className="mt-3 space-y-3">
        <p className="text-xs text-slate-500">
          每次回测（默认）会把<strong className="font-medium text-slate-400">本次合并后的策略 config</strong>与指标写入{" "}
          <span className="font-mono text-emerald-200/80">data/qqq_0dte/backtest_snapshots.json</span>（最多保留约 500
          条）。下方展示按排序取前 5；实盘面板下拉选项中亦带策略名称。载入时用「高级 JSON」同步参数。
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <p className="max-w-xl pb-1 text-[11px] leading-snug text-slate-500">
            回测结果中的 <span className="font-mono">return_pct</span> = 已实现盈亏 ÷ 本段回测<strong>累计开仓权利金</strong>（每股价×张数×乘数，不含手续费）×100；无开仓时为「—」。快照 metrics 含{" "}
            <span className="font-mono">open_premium_debit_usd</span>，可按盈亏率排序 TOP5。
          </p>
          <label className="flex cursor-pointer items-center gap-2 pb-1">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-slate-600"
              checked={saveSnapshot}
              onChange={(e) => setSaveSnapshot(e.target.checked)}
            />
            <span className="text-sm text-slate-300">本次回测写入快照</span>
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 text-sm text-slate-400">
            <span>TOP5 排序</span>
            <select
              className="input-base py-1 text-sm"
              value={snapshotSort}
              onChange={(e) => setSnapshotSort(e.target.value as "realized_pnl" | "return_pct")}
            >
              <option value="realized_pnl">按已实现盈亏（realized_pnl）</option>
              <option value="return_pct">按盈亏率（return_pct；旧快照无分母时排后）</option>
            </select>
          </label>
          <button type="button" className="btn-secondary text-sm" disabled={snapshotsLoading} onClick={() => void fetchTopSnapshots()}>
            {snapshotsLoading ? "刷新中…" : "刷新 TOP5"}
          </button>
        </div>
        {snapshotsErr ? <div className="text-xs text-rose-300">{snapshotsErr}</div> : null}
        <div className="table-shell max-h-[320px] overflow-auto">
          <table className="w-full min-w-[800px] text-xs">
            <thead className="table-head sticky top-0 z-10 text-left">
              <tr>
                <th className="px-2 py-1.5">#</th>
                <th className="px-2 py-1.5">策略</th>
                <th className="px-2 py-1.5">时间 (UTC)</th>
                <th className="px-2 py-1.5">标的 / K线</th>
                <th className="px-2 py-1.5">已实现盈亏</th>
                <th className="px-2 py-1.5">盈亏率%</th>
                <th className="px-2 py-1.5">平仓/胜率</th>
                <th className="px-2 py-1.5">操作</th>
              </tr>
            </thead>
            <tbody>
              {(topSnapshots?.runs?.length ?? 0) === 0 ? (
                <tr>
                  <td colSpan={8} className="px-2 py-3 text-slate-500">
                    暂无快照。运行一次回测并勾选「写入快照」后将出现在此。
                  </td>
                </tr>
              ) : (
                (topSnapshots?.runs ?? []).map((run, idx) => {
                  const m = run.metrics ?? {};
                  const req = run.request ?? {};
                  const sc = run.strategy_config;
                  const strat =
                    sc && typeof sc === "object" && !Array.isArray(sc)
                      ? qqq0dteStrategyDisplayName(sc as Record<string, unknown>)
                      : "反应区";
                  const sym = typeof req.symbol === "string" ? req.symbol : "—";
                  const kl = typeof req.kline === "string" ? req.kline : "—";
                  const created = typeof run.created_at === "string" ? run.created_at : "—";
                  return (
                    <tr key={run.id ?? String(idx)} className="border-t border-slate-800/90">
                      <td className="px-2 py-1.5 font-mono text-slate-500">{idx + 1}</td>
                      <td className="px-2 py-1.5 text-slate-200">{strat}</td>
                      <td className="px-2 py-1.5 font-mono text-[10px] text-slate-400">{created}</td>
                      <td className="px-2 py-1.5 font-mono text-[10px] text-slate-300">
                        {sym} · {kl}
                      </td>
                      <td className="px-2 py-1.5 font-mono text-emerald-200/90">{m.realized_pnl ?? "—"}</td>
                      <td className="px-2 py-1.5 font-mono text-slate-300">
                        {m.return_pct != null ? `${m.return_pct}` : "—"}
                      </td>
                      <td className="px-2 py-1.5 text-slate-400">
                        {m.closed_trades ?? "—"} / {m.win_rate_pct ?? "—"}%
                      </td>
                      <td className="px-2 py-1.5">
                        <button type="button" className="btn-secondary py-0.5 text-[11px]" onClick={() => applySnapshotRun(run)}>
                          载入参数
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        {topSnapshots?.total_stored != null ? (
          <p className="text-[11px] text-slate-600">库内共 {topSnapshots.total_stored} 条快照。</p>
        ) : null}
          </div>
        </details>
      </div>

      <div className="panel space-y-4">
        <details className="rounded-lg border border-cyan-500/25 bg-cyan-950/10">
          <summary className="cursor-pointer select-none list-none px-3 py-2.5 text-sm font-semibold text-cyan-100 [&::-webkit-details-marker]:hidden">
            <span className="mr-1.5 inline-block text-cyan-400/80">▸</span>
            一键套用参数模板
            <span className="ml-2 text-xs font-normal text-slate-500">（点击展开）</span>
          </summary>
          <div className="space-y-2 border-t border-cyan-500/20 px-3 pb-3 pt-2">
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
              {STRATEGY_PRESETS.map((preset) => (
                <button
                  key={preset.key}
                  type="button"
                  className="btn-secondary w-full justify-start text-left"
                  onClick={() => applyPreset(preset.key)}
                  title={`${preset.title}：${preset.description}`}
                >
                  <span className="font-medium text-slate-100">{preset.title}</span>
                  <span className="ml-2 text-xs text-slate-400">{preset.description}</span>
                </button>
              ))}
            </div>
            <p className="text-[11px] text-slate-500">
              点击后会更新下方表单参数，并自动填充「矩阵删选」JSON 网格（开发者模式）。
            </p>
          </div>
        </details>

        <div className="section-title">策略验证：行情与回测范围</div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
          <label className="space-y-1">
            <div className="field-label">标的</div>
            <input className="input-base" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
          </label>
          <NumField
            label="日历天数"
            hint="仅在「周期数」为 0 时按自然日拉 K 线。"
            value={days}
            onChange={setDays}
            min={1}
            max={3650}
          />
          <NumField
            label="周期数"
            hint="大于 0 时优先按最近 N 根 K 拉取（与回测中心一致）。"
            value={periods}
            onChange={(n) => setPeriods(Math.max(0, Math.floor(n)))}
            min={0}
            max={500000}
          />
          <label className="space-y-1">
            <div className="field-label">K 线周期</div>
            <select className="input-base" value={kline} onChange={(e) => setKline(e.target.value)}>
              <option value="1m">1分K</option>
              <option value="5m">5分K</option>
              <option value="10m">10分K</option>
              <option value="30m">30分K</option>
              <option value="1h">1小时K</option>
              <option value="2h">2小时K</option>
              <option value="4h">4小时K</option>
              <option value="1d">日K</option>
            </select>
          </label>
        </div>
        <label className="flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-600"
            checked={useServerKline}
            onChange={(e) => setUseServerKline(e.target.checked)}
          />
          <span className="text-sm text-slate-300">使用服务器 K 线缓存（data/klines，与回测中心一致）</span>
        </label>
        <label className="flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-600"
            checked={rthOnly}
            onChange={(e) => setRthOnly(e.target.checked)}
          />
          <span className="text-sm text-slate-300">仅使用 RTH 时段 K 线（09:30–16:00 ET）</span>
        </label>
      </div>

      <div className="panel space-y-3 border-indigo-500/20">
        <div className="section-title">与实盘对齐：解析 OPRA</div>
        <p className="text-xs text-slate-500">
          与回测相同的行权价与方向，调用{" "}
          <span className="font-mono text-indigo-200/90">POST /strategy/qqq-0dte/resolve-contract</span>。0DTE 请在「到期日」填
          <strong className="font-medium text-slate-400">当日到期</strong>的 YYYY-MM-DD；留空则用券商返回的最近到期（可能不是 0DTE）。
        </p>
        {resolveErr ? (
          <div className="rounded border border-rose-500/40 bg-rose-950/30 px-3 py-2 text-sm text-rose-200">{resolveErr}</div>
        ) : null}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <NumField label="行权价 K" value={resolveStrike} onChange={setResolveStrike} min={0.01} step={0.5} />
          <label className="space-y-1">
            <div className="field-label">方向</div>
            <select
              className="input-base"
              value={resolveRight}
              onChange={(e) => setResolveRight(e.target.value as "call" | "put")}
            >
              <option value="call">Call</option>
              <option value="put">Put</option>
            </select>
          </label>
          <label className="space-y-1 sm:col-span-2">
            <div className="field-label">到期日（可选）</div>
            <input
              className="input-base"
              placeholder="例如 2026-03-28"
              value={resolveExpiry}
              onChange={(e) => setResolveExpiry(e.target.value)}
            />
          </label>
        </div>
        <button
          type="button"
          className="btn-primary"
          disabled={resolveLoading}
          onClick={() => void runResolveContract()}
        >
          {resolveLoading ? "解析中…" : "从期权链解析"}
        </button>
        {resolveOut && resolveOut.ok === true ? (
          <pre className="max-h-48 overflow-auto rounded border border-slate-700/80 bg-slate-950/80 p-3 text-xs text-emerald-100/90">
            {JSON.stringify(resolveOut, null, 2)}
          </pre>
        ) : resolveOut ? (
          <pre className="max-h-48 overflow-auto rounded border border-slate-700/80 bg-slate-950/80 p-3 text-xs text-slate-300">
            {JSON.stringify(resolveOut, null, 2)}
          </pre>
        ) : null}
      </div>

      <div className="panel space-y-3 border-violet-500/20">
        <div className="section-title">策略验证：参数矩阵（同一批 K 线）</div>
        <p className="text-xs text-slate-500">
          在<strong className="font-medium text-slate-400">不改动下方策略表单其它项</strong>的前提下，先在下拉框选择<strong className="text-slate-400">矩阵针对的策略</strong>，
          表格<strong className="text-slate-400">只显示该策略可参与删选的参数</strong>；勾选行与候选值做笛卡尔积（组合数 = 各勾选行个数相乘），请勿超过「组合数上限」。
          矩阵请求的基线 <span className="font-mono">strategy_config</span> 会<strong className="text-slate-400">固定为所选策略变体</strong>（与主表单「策略变体」可不同）。
          需要手写更多维度可开启「JSON 写 grid」。
        </p>
        {matrixErr ? <div className="text-sm text-rose-300">{matrixErr}</div> : null}
        <label className="flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-600"
            checked={matrixUseJsonMode}
            onChange={(e) => {
              const on = e.target.checked;
              setMatrixUseJsonMode(on);
              if (on) setMatrixAdvancedJsonOpen(true);
            }}
          />
          <span className="text-sm text-slate-300">使用 JSON 直接写 grid（开发者）</span>
        </label>
        {!matrixUseJsonMode ? (
          <div className="space-y-2">
            <div className="flex flex-wrap items-end gap-3 rounded-lg border border-violet-500/25 bg-violet-950/20 px-3 py-2.5">
              <label className="space-y-1">
                <div className="field-label text-violet-200/90">矩阵针对策略</div>
                <select
                  className="input-base min-w-[220px] text-sm"
                  value={matrixStrategySelect}
                  onChange={(e) => onMatrixStrategySelectChange(e.target.value as MatrixStrategyVariant)}
                >
                  <option value="reaction_zone">反应区 + 成交量确认</option>
                  <option value="morning_strangle">早盘宽跨</option>
                  <option value="morning_directional">早盘方向单</option>
                  <option value="gamma_scalping">Gamma 剥头皮</option>
                  <option value="gamma_pro">Gamma Pro</option>
                </select>
              </label>
              <p className="max-w-xl text-[11px] leading-snug text-slate-500">
                切换策略后会刷新<strong className="text-slate-400">当前可见行</strong>的默认勾选；隐藏行的勾选状态仍保留，切回该策略时恢复。
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className="btn-secondary text-xs" onClick={() => resetMatrixAxisDefaults()}>
                重置表格为默认候选
              </button>
              <span className="text-[11px] text-slate-500">
                取消勾选某行则不参与删选；候选值用<strong className="text-slate-400">英文逗号或中文逗号</strong>分隔。
              </span>
            </div>
            <div className="table-shell max-h-[420px] overflow-auto">
              <table className="w-full min-w-[640px] text-sm">
                <thead className="table-head sticky top-0 z-10 text-left">
                  <tr>
                    <th className="w-10 px-2 py-2">参与</th>
                    <th className="px-2 py-2 min-w-[200px]">参数</th>
                    <th className="px-2 py-2">说明</th>
                    <th className="min-w-[220px] px-2 py-2">候选值（逗号分隔）</th>
                  </tr>
                </thead>
                <tbody>
                  {matrixAxisRows
                    .map((row, idx) => ({ row, idx }))
                    .filter(({ row }) => matrixDefAppliesToVariant(row.key, matrixStrategySelect))
                    .map(({ row, idx }) => (
                      <tr key={row.key} className="border-t border-slate-800/90">
                        <td className="px-2 py-2 align-top">
                          <input
                            type="checkbox"
                            className="h-4 w-4 rounded border-slate-600"
                            checked={row.enabled}
                            onChange={(e) => patchMatrixAxisRow(idx, { enabled: e.target.checked })}
                          />
                        </td>
                        <td className="px-2 py-2 align-top font-medium text-slate-200">{row.labelZh}</td>
                        <td className="px-2 py-2 align-top text-xs text-slate-500">{row.hint}</td>
                        <td className="px-2 py-2 align-top">
                          <input
                            className="input-base w-full font-mono text-xs"
                            spellCheck={false}
                            placeholder="例：0.08, 0.1, 1"
                            value={row.valuesText}
                            onChange={(e) => patchMatrixAxisRow(idx, { valuesText: e.target.value })}
                          />
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
        {matrixUseJsonMode ? (
          <div className="space-y-1 rounded border border-amber-500/25 bg-amber-950/10 p-3">
            <p className="text-xs text-amber-200/90">
              JSON 模式下请提供对象：键为参数名，值为数组。基线 <span className="font-mono">strategy_config</span> 仍由上方「矩阵针对策略」下拉决定（
              {matrixStrategySelect === "reaction_zone"
                ? "反应区"
                : matrixStrategySelect === "morning_strangle"
                  ? "早盘宽跨"
                  : matrixStrategySelect === "morning_directional"
                    ? "早盘方向单"
                    : "Gamma 剥头皮"}
              ）。grid 键示例：{" "}
              <span className="font-mono text-amber-100/80">reaction_zone_width_pct</span>、
              <span className="font-mono text-amber-100/80">strangle_range_pct_ui</span>、
              <span className="font-mono text-amber-100/80">directional_down_pct_ui</span> 等；勿与所选策略无关的键混用以免无效组合。
            </p>
            <textarea
              className="input-base min-h-[160px] w-full resize-y font-mono text-xs"
              spellCheck={false}
              value={matrixGridJson}
              onChange={(e) => setMatrixGridJson(e.target.value)}
            />
          </div>
        ) : null}
        {!matrixUseJsonMode ? (
          <button
            type="button"
            className="text-xs text-violet-300/90 underline-offset-2 hover:underline"
            onClick={() => setMatrixAdvancedJsonOpen((v) => !v)}
          >
            {matrixAdvancedJsonOpen ? "收起「仅预览当前 grid」" : "展开「仅预览当前 grid」"}
          </button>
        ) : null}
        {matrixAdvancedJsonOpen && !matrixUseJsonMode ? (
          matrixGridPreviewJson != null ? (
            <pre className="max-h-32 overflow-auto rounded border border-slate-700/80 bg-slate-950/80 p-2 text-[10px] text-slate-400">
              {matrixGridPreviewJson}
            </pre>
          ) : (
            <p className="text-xs text-rose-300/90">
              当前无法生成预览：请检查已勾选行的候选值（须为数字，用逗号分隔）。
            </p>
          )
        ) : null}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          <NumField label="TOP N" value={matrixTopN} onChange={setMatrixTopN} min={1} max={100} />
          <NumField
            label="组合数上限"
            hint="超过则 API 拒绝"
            value={matrixMaxComb}
            onChange={setMatrixMaxComb}
            min={1}
            max={10000}
          />
          <label className="space-y-1">
            <div className="field-label">排序</div>
            <select
              className="input-base"
              value={matrixSort}
              onChange={(e) => setMatrixSort(e.target.value as "realized_pnl" | "return_pct")}
            >
              <option value="realized_pnl">已实现盈亏</option>
              <option value="return_pct">盈亏率（相对累计开仓权利金）</option>
            </select>
          </label>
        </div>
        <button type="button" className="btn-primary" disabled={matrixLoading} onClick={() => void runMatrix()}>
          {matrixLoading ? "参数矩阵运行中（可能数分钟）…" : "运行参数矩阵"}
        </button>
        {matrixResult ? (
          <div className="space-y-2">
            {matrixResult.disclaimer ? <p className="text-xs text-amber-200/85">{matrixResult.disclaimer}</p> : null}
            {matrixLiveSyncErr ? <div className="text-xs text-rose-300">{matrixLiveSyncErr}</div> : null}
            {matrixLiveSyncMsg ? <div className="text-xs text-emerald-200/90">{matrixLiveSyncMsg}</div> : null}
            <p className="text-xs text-slate-500">
              已跑组合{" "}
              <span className="font-mono text-slate-300">{matrixResult.combinations_run ?? "—"}</span>
              ，展示前 {matrixResult.top?.length ?? 0} 名（排序：
              {matrixResult.sort_by === "return_pct"
                ? "盈亏率(return_pct)"
                : matrixResult.sort_by === "realized_pnl"
                  ? "已实现盈亏"
                  : (matrixResult.sort_by ?? "—")}
              ）。
            </p>
            <p className="text-[11px] text-slate-500">
              本次样本：总 K 线{" "}
              <span className="font-mono text-slate-300">{matrixResult.bar_count_total ?? "—"}</span>，
              RTH{" "}
              <span className="font-mono text-slate-300">{matrixResult.bar_count_rth ?? "—"}</span>，
              非 RTH{" "}
              <span className="font-mono text-slate-300">{matrixResult.bar_count_non_rth ?? "—"}</span>
              （rth_only={String(Boolean(matrixResult.rth_only))}）。
            </p>
            <p className="text-[11px] text-slate-500">
              「写入实盘配置」会把该行的 <span className="font-mono text-slate-400">strategy_config</span> 合并写入{" "}
              <span className="font-mono text-slate-500">live_worker_config.json</span>，并同步当前页的标的与 K 线周期（不改变 dry_run 等其它字段）。
            </p>
            <div className="table-shell max-h-[360px] overflow-auto">
              <table className="w-full min-w-[840px] text-xs">
                <thead className="table-head sticky top-0 z-10 text-left">
                  <tr>
                    <th className="px-2 py-1.5">#</th>
                    <th className="px-2 py-1.5">参数组合（中文）</th>
                    <th className="px-2 py-1.5">PnL</th>
                    <th className="px-2 py-1.5">盈亏率%</th>
                    <th className="px-2 py-1.5">平仓/胜率</th>
                    <th className="px-2 py-1.5">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {(matrixResult.top ?? []).map((row, idx) => (
                    <tr key={idx} className="border-t border-slate-800/90">
                      <td className="px-2 py-1.5 font-mono text-slate-500">{idx + 1}</td>
                      <td className="max-w-md px-2 py-1.5 text-[11px] leading-snug text-slate-300">
                        {formatMatrixGridParamsReadable(row.grid_params as Record<string, unknown> | undefined)}
                      </td>
                      <td className="px-2 py-1.5 font-mono text-emerald-200/85">{row.realized_pnl ?? "—"}</td>
                      <td className="px-2 py-1.5 font-mono text-slate-300">
                        {row.return_pct != null ? row.return_pct : "—"}
                      </td>
                      <td className="px-2 py-1.5 text-slate-400">
                        {row.closed_trades ?? "—"} / {row.win_rate_pct ?? "—"}%
                      </td>
                      <td className="px-2 py-1.5">
                        <div className="flex flex-col gap-1 sm:flex-row sm:flex-wrap">
                          <button
                            type="button"
                            className="btn-secondary py-0.5 text-[11px]"
                            disabled={matrixLiveSyncRow === idx}
                            onClick={() => applyMatrixRow(row)}
                          >
                            载入配置
                          </button>
                          <button
                            type="button"
                            className="btn-secondary border-indigo-500/30 py-0.5 text-[11px] text-indigo-100/90"
                            disabled={matrixLiveSyncRow === idx}
                            onClick={() => void syncMatrixRowToLiveWorker(row, idx)}
                          >
                            {matrixLiveSyncRow === idx ? "写入中…" : "写入实盘配置"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </div>

      <div className="panel space-y-6">
        <div className="section-title">策略参数（无代码）</div>
        {strategy.strategy_variant === "reaction_zone" ? (
          <p className="text-xs text-slate-500">
            下列表单项对应后端 <span className="font-mono">Qqq0dteConfig</span>。止盈/止损等为「占权利金的比例」百分数；无成交时可试着
            <strong className="font-medium text-slate-400">降低成交量倍数</strong>或
            <strong className="font-medium text-slate-400">缩短开盘限制分钟</strong>。
          </p>
        ) : strategy.strategy_variant === "gamma_scalping" || strategy.strategy_variant === "gamma_pro" ? (
          <p className="text-xs text-slate-500">
            当前为<strong className="font-medium text-slate-400">{strategy.strategy_variant === "gamma_pro" ? "Gamma Pro" : "Gamma 剥头皮"}</strong>：
            Gamma Pro 在剥头皮基础上增加了假突破反向与午后续航分支；出场按硬止损 + 止盈 + 最长持仓控制。
          </p>
        ) : (
          <p className="text-xs text-slate-500">
            当前为<strong className="font-medium text-slate-400">早盘模式</strong>：仅显示与该变体相关的参数；开仓窗、强平与止盈规则见上方紫色区域。
            行权价由下方「选约」（步长 + Call/Put 外移档数）决定；合成定价与滑点/张数亦见下方。
          </p>
        )}

        <div className="rounded-lg border border-violet-500/30 bg-violet-950/20 p-3">
          <h3 className="mb-2 text-sm font-semibold text-slate-200">策略变体</h3>
          <label className="space-y-1">
            <div className="field-label">模式</div>
            <select
              className="input-base max-w-md"
              value={strategy.strategy_variant}
              onChange={(e) => {
                const v = e.target.value;
                patchStrategy({
                  strategy_variant:
                    v === "morning_strangle"
                      ? "morning_strangle"
                      : v === "morning_directional"
                        ? "morning_directional"
                        : v === "gamma_scalping"
                          ? "gamma_scalping"
                          : v === "gamma_pro"
                            ? "gamma_pro"
                            : "reaction_zone",
                });
              }}
            >
              <option value="reaction_zone">反应区 + 成交量 + 单边 Call/Put（默认）</option>
              <option value="morning_strangle">早盘宽跨（双买 Call+Put，选约见下方；按组合权利金盈亏率止盈 + 美东强平）</option>
              <option value="morning_directional">早盘方向单（跌超阈买 Call / 涨超阈买 Put，按单腿权利金盈亏率止盈 + 强平）</option>
              <option value="gamma_scalping">Gamma 剥头皮（站上昨高买 Call / 跌破昨低买 Put，或 VWAP 回归；硬止损 + 快速止盈）</option>
              <option value="gamma_pro">Gamma Pro（突破追随 + 假突破反打 + 午后续航）</option>
            </select>
          </label>
          {strategy.strategy_variant !== "reaction_zone" ? (
            <p className="mt-2 text-[11px] leading-snug text-slate-500">
              <strong className="font-medium text-slate-400">宽跨</strong>：前收附近窄幅震荡时双买 Call+Put，行权价由下方<strong>选约</strong>
              （步长 + Call/Put 外移档数）决定；按<strong>组合权利金盈亏率</strong>止盈（已实现平仓金额 + 剩余腿 bid，不含手续费）。
              <strong className="font-medium text-slate-400">方向单</strong>：相对前收<strong>跌幅 ≥ 阈值</strong>买 Call、
              <strong>涨幅 ≥ 阈值</strong>买 Put，选约同样用下方外移档数；按<strong>单腿权利金盈亏率</strong>止盈。时间窗与强平配置两模式共用。
            </p>
          ) : null}
          {strategy.strategy_variant === "morning_strangle" || strategy.strategy_variant === "morning_directional" ? (
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <label className="space-y-1">
                <div className="field-label">开仓窗开始（美东 HH:MM）</div>
                <input
                  className="input-base"
                  value={strategy.strangle_entry_start_hhmm_et}
                  onChange={(e) => patchStrategy({ strangle_entry_start_hhmm_et: e.target.value })}
                />
              </label>
              <label className="space-y-1">
                <div className="field-label">开仓窗结束（美东 HH:MM）</div>
                <input
                  className="input-base"
                  value={strategy.strangle_entry_end_hhmm_et}
                  onChange={(e) => patchStrategy({ strangle_entry_end_hhmm_et: e.target.value })}
                />
              </label>
              <label className="space-y-1">
                <div className="field-label">{strategy.strategy_variant === "morning_strangle" ? "强制双平" : "强制平仓"}（美东 HH:MM）</div>
                <input
                  className="input-base"
                  value={strategy.strangle_force_close_hhmm_et}
                  onChange={(e) => patchStrategy({ strangle_force_close_hhmm_et: e.target.value })}
                />
              </label>
              <label className="space-y-1 sm:col-span-2 lg:col-span-3">
                <div className="field-label">回测标的价位（近似 bid）</div>
                <select
                  className="input-base max-w-md"
                  value={strategy.strangle_underlying_field}
                  onChange={(e) =>
                    patchStrategy({
                      strangle_underlying_field: e.target.value as StrategyFormState["strangle_underlying_field"],
                    })
                  }
                >
                  <option value="low">low（偏保守）</option>
                  <option value="close">close</option>
                  <option value="open">open</option>
                  <option value="high">high</option>
                </select>
              </label>
            </div>
          ) : null}
          {strategy.strategy_variant === "morning_strangle" ? (
            <div className="mt-3 grid grid-cols-1 gap-3 border-t border-violet-500/20 pt-3 sm:grid-cols-2 lg:grid-cols-3">
              <NumField
                label="前收偏离上限（%）"
                hint="如 0.3 表示 |涨跌幅|≤0.3% 才开仓。"
                value={strategy.strangle_range_pct_ui}
                onChange={(n) => patchStrategy({ strangle_range_pct_ui: n })}
                min={0.01}
                max={5}
                step={0.05}
              />
              <NumField
                label="组合止盈盈亏率（%）"
                hint="相对开仓权利金合计（非账户本金）；不含手续费。100 表示翻倍即平。"
                value={strategy.strangle_take_profit_return_ui}
                onChange={(n) => patchStrategy({ strangle_take_profit_return_ui: n })}
                min={0}
                max={500}
                step={5}
              />
              <NumField
                label="组合止损盈亏率（%）"
                hint="相对开仓权利金合计；30 表示组合亏损到 -30% 触发平仓。0 表示关闭。"
                value={strategy.strangle_stop_loss_return_ui}
                onChange={(n) => patchStrategy({ strangle_stop_loss_return_ui: n })}
                min={0}
                max={100}
                step={1}
              />
              <NumField
                label="组合止损冷静期（分钟）"
                hint="开仓后该时长内不触发组合止损；建议 1-2 分钟。"
                value={strategy.strangle_stop_loss_cooldown_minutes}
                onChange={(n) => patchStrategy({ strangle_stop_loss_cooldown_minutes: Math.max(0, Math.floor(n)) })}
                min={0}
                max={10}
                step={1}
              />
            </div>
          ) : null}
          {strategy.strategy_variant === "morning_directional" ? (
            <div className="mt-3 grid grid-cols-1 gap-3 border-t border-violet-500/20 pt-3 sm:grid-cols-2 lg:grid-cols-3">
              <NumField
                label="买 Call：跌幅阈值（%）"
                hint="相对前收 chg ≤ −该值时买入 Call（默认 1 即跌≥1%）。"
                value={strategy.directional_down_pct_ui}
                onChange={(n) => patchStrategy({ directional_down_pct_ui: n })}
                min={0.05}
                max={10}
                step={0.05}
              />
              <NumField
                label="买 Put：涨幅阈值（%）"
                hint="相对前收 chg ≥ 该值时买入 Put（默认 1 即涨≥1%）。"
                value={strategy.directional_up_pct_ui}
                onChange={(n) => patchStrategy({ directional_up_pct_ui: n })}
                min={0.05}
                max={10}
                step={0.05}
              />
              <NumField
                label="单腿止盈盈亏率（%）"
                hint="相对该腿建仓权利金（非账户本金）；不含手续费。100 表示翻倍即平。"
                value={strategy.directional_take_profit_return_ui}
                onChange={(n) => patchStrategy({ directional_take_profit_return_ui: n })}
                min={0}
                max={500}
                step={5}
              />
              <NumField
                label="单腿止损（%）"
                hint="相对建仓成本，亏损达该比例平仓（实盘 K 线/轮询用 last 盯止损）；0 关闭。"
                value={strategy.directional_stop_loss_pct_ui}
                onChange={(n) => patchStrategy({ directional_stop_loss_pct_ui: n })}
                min={0}
                max={95}
                step={1}
              />
            </div>
          ) : null}
          {strategy.strategy_variant === "gamma_scalping" ? (
            <div className="mt-3 grid grid-cols-1 gap-3 border-t border-violet-500/20 pt-3 sm:grid-cols-2 lg:grid-cols-3">
              <label className="space-y-1">
                <div className="field-label">开仓窗开始（美东 HH:MM）</div>
                <input className="input-base" value={strategy.gamma_entry_start_hhmm_et} onChange={(e) => patchStrategy({ gamma_entry_start_hhmm_et: e.target.value })} />
              </label>
              <label className="space-y-1">
                <div className="field-label">开仓窗结束（美东 HH:MM）</div>
                <input className="input-base" value={strategy.gamma_entry_end_hhmm_et} onChange={(e) => patchStrategy({ gamma_entry_end_hhmm_et: e.target.value })} />
              </label>
              <label className="space-y-1">
                <div className="field-label">强制平仓（美东 HH:MM）</div>
                <input className="input-base" value={strategy.gamma_force_close_hhmm_et} onChange={(e) => patchStrategy({ gamma_force_close_hhmm_et: e.target.value })} />
              </label>
              <NumField label="最长持仓（分钟）" value={strategy.gamma_max_hold_minutes} onChange={(n) => patchStrategy({ gamma_max_hold_minutes: n })} min={1} max={30} />
              <NumField label="硬止损（%）" value={strategy.gamma_hard_stop_loss_pct_ui} onChange={(n) => patchStrategy({ gamma_hard_stop_loss_pct_ui: n })} min={5} max={80} step={1} />
              <NumField label="止盈下限（%）" value={strategy.gamma_take_profit_min_return_ui} onChange={(n) => patchStrategy({ gamma_take_profit_min_return_ui: n })} min={10} max={200} step={5} />
              <NumField label="止盈上限（%）" value={strategy.gamma_take_profit_max_return_ui} onChange={(n) => patchStrategy({ gamma_take_profit_max_return_ui: n })} min={20} max={400} step={5} />
              <label className="flex cursor-pointer items-center gap-2">
                <input type="checkbox" className="h-4 w-4 rounded border-slate-600" checked={strategy.gamma_require_breakout_prev_day} onChange={(e) => patchStrategy({ gamma_require_breakout_prev_day: e.target.checked })} />
                <span className="text-sm text-slate-300">要求前日高低突破</span>
              </label>
              <label className="flex cursor-pointer items-center gap-2">
                <input type="checkbox" className="h-4 w-4 rounded border-slate-600" checked={strategy.gamma_require_vix_rising} onChange={(e) => patchStrategy({ gamma_require_vix_rising: e.target.checked })} />
                <span className="text-sm text-slate-300">要求 VIX 同步上升</span>
              </label>
              <NumField label="VIX 上升阈值（%）" value={strategy.gamma_vix_rising_min_pct} onChange={(n) => patchStrategy({ gamma_vix_rising_min_pct: n })} min={0} max={5} step={0.05} />
              <label className="flex cursor-pointer items-center gap-2">
                <input type="checkbox" className="h-4 w-4 rounded border-slate-600" checked={strategy.gamma_enable_vwap_reversion} onChange={(e) => patchStrategy({ gamma_enable_vwap_reversion: e.target.checked })} />
                <span className="text-sm text-slate-300">启用 VWAP 回归信号</span>
              </label>
              <NumField label="VWAP 偏离阈值（%）" value={strategy.gamma_vwap_deviation_pct_ui} onChange={(n) => patchStrategy({ gamma_vwap_deviation_pct_ui: n })} min={0.05} max={3} step={0.05} />
              <label className="flex cursor-pointer items-center gap-2">
                <input type="checkbox" className="h-4 w-4 rounded border-slate-600" checked={strategy.gamma_require_leader_confirmation} onChange={(e) => patchStrategy({ gamma_require_leader_confirmation: e.target.checked })} />
                <span className="text-sm text-slate-300">要求 NVDA/TSLA 领先确认</span>
              </label>
              <NumField label="龙头最小涨跌幅（%）" value={strategy.gamma_leader_min_move_pct} onChange={(n) => patchStrategy({ gamma_leader_min_move_pct: n })} min={0} max={5} step={0.05} />
              <NumField label="QQQ 滞后阈值（%）" value={strategy.gamma_leader_lag_pct} onChange={(n) => patchStrategy({ gamma_leader_lag_pct: n })} min={0} max={2} step={0.01} />
            </div>
          ) : null}
          {strategy.strategy_variant === "gamma_pro" ? (
            <div className="mt-3 space-y-2 border-t border-violet-500/20 pt-3">
              <p className="text-[11px] leading-snug text-slate-500">
                以下按<strong className="font-medium text-slate-400">入场</strong>、<strong className="font-medium text-slate-400">风控出场</strong>、
                <strong className="font-medium text-slate-400">过滤</strong>分组；各组可折叠。
              </p>
              <details className="rounded-md border border-violet-500/25 bg-violet-950/15 px-2 py-1" open>
                <summary className="cursor-pointer select-none text-sm font-medium text-violet-200/90">入场信号与时间窗</summary>
                <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 pb-2">
                  <label className="space-y-1">
                    <div className="field-label">开仓窗开始（美东 HH:MM）</div>
                    <input className="input-base" value={strategy.gamma_pro_entry_start_hhmm_et} onChange={(e) => patchStrategy({ gamma_pro_entry_start_hhmm_et: e.target.value })} />
                  </label>
                  <label className="space-y-1">
                    <div className="field-label">开仓窗结束（美东 HH:MM）</div>
                    <input className="input-base" value={strategy.gamma_pro_entry_end_hhmm_et} onChange={(e) => patchStrategy({ gamma_pro_entry_end_hhmm_et: e.target.value })} />
                  </label>
                  <label className="space-y-1">
                    <div className="field-label">午后信号开始（美东 HH:MM）</div>
                    <input className="input-base" value={strategy.gamma_pro_afternoon_start_hhmm_et} onChange={(e) => patchStrategy({ gamma_pro_afternoon_start_hhmm_et: e.target.value })} />
                  </label>
                  <label className="space-y-1">
                    <div className="field-label">午间暂停开始（美东 HH:MM）</div>
                    <input className="input-base" value={strategy.gamma_pro_midday_skip_start_hhmm_et} onChange={(e) => patchStrategy({ gamma_pro_midday_skip_start_hhmm_et: e.target.value })} />
                  </label>
                  <label className="space-y-1">
                    <div className="field-label">午间暂停结束（美东 HH:MM）</div>
                    <input className="input-base" value={strategy.gamma_pro_midday_skip_end_hhmm_et} onChange={(e) => patchStrategy({ gamma_pro_midday_skip_end_hhmm_et: e.target.value })} />
                  </label>
                  <NumField label="Call OTM 档数" value={strategy.gamma_pro_call_otm_steps} onChange={(n) => patchStrategy({ gamma_pro_call_otm_steps: n })} min={0} max={10} step={1} />
                  <NumField label="Put OTM 档数" value={strategy.gamma_pro_put_otm_steps} onChange={(n) => patchStrategy({ gamma_pro_put_otm_steps: n })} min={0} max={10} step={1} />
                  <NumField label="VWAP 回踩容差（%）" value={strategy.gamma_pro_vwap_pullback_pct_ui} onChange={(n) => patchStrategy({ gamma_pro_vwap_pullback_pct_ui: n })} min={0.01} max={2} step={0.01} />
                  <label className="flex cursor-pointer items-center gap-2 sm:col-span-2 lg:col-span-3">
                    <input type="checkbox" className="h-4 w-4 rounded border-slate-600" checked={strategy.gamma_pro_enable_false_breakout_reversal} onChange={(e) => patchStrategy({ gamma_pro_enable_false_breakout_reversal: e.target.checked })} />
                    <span className="text-sm text-slate-300">启用假突破反向</span>
                  </label>
                </div>
              </details>
              <details className="rounded-md border border-violet-500/25 bg-violet-950/15 px-2 py-1" open>
                <summary className="cursor-pointer select-none text-sm font-medium text-violet-200/90">风控出场</summary>
                <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 pb-2">
                  <label className="space-y-1">
                    <div className="field-label">强制平仓（美东 HH:MM）</div>
                    <input className="input-base" value={strategy.gamma_pro_force_close_hhmm_et} onChange={(e) => patchStrategy({ gamma_pro_force_close_hhmm_et: e.target.value })} />
                  </label>
                  <NumField label="最长持仓（分钟）" value={strategy.gamma_pro_max_hold_minutes} onChange={(n) => patchStrategy({ gamma_pro_max_hold_minutes: n })} min={1} max={240} />
                  <NumField label="硬止损（%）" value={strategy.gamma_pro_hard_stop_loss_pct_ui} onChange={(n) => patchStrategy({ gamma_pro_hard_stop_loss_pct_ui: n })} min={5} max={80} step={1} />
                  <NumField label="止盈阈值（%）" value={strategy.gamma_pro_take_profit_return_ui} onChange={(n) => patchStrategy({ gamma_pro_take_profit_return_ui: n })} min={10} max={300} step={5} />
                </div>
              </details>
              <details className="rounded-md border border-violet-500/25 bg-violet-950/15 px-2 py-1">
                <summary className="cursor-pointer select-none text-sm font-medium text-violet-200/90">过滤与确认</summary>
                <div className="mt-2 space-y-3 pb-2">
                  <label className="flex cursor-pointer items-center gap-2">
                    <input type="checkbox" className="h-4 w-4 rounded border-slate-600" checked={strategy.gamma_pro_require_leader_confirmation} onChange={(e) => patchStrategy({ gamma_pro_require_leader_confirmation: e.target.checked })} />
                    <span className="text-sm text-slate-300">要求 NVDA/TSLA 领先确认</span>
                  </label>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <NumField
                      label="成交量回看根数"
                      hint="与后端 volume_spike_at 一致；用于判定放量/未放量。"
                      value={strategy.volume_lookback_bars}
                      onChange={(n) => patchStrategy({ volume_lookback_bars: n })}
                      min={2}
                      max={200}
                    />
                    <NumField
                      label="成交量突增倍数"
                      hint="当前量 ≥ 均量×该值视为放量；无信号时可试 1.2～1.5。"
                      value={strategy.volume_spike_multiplier}
                      onChange={(n) => patchStrategy({ volume_spike_multiplier: n })}
                      min={0.5}
                      max={20}
                      step={0.1}
                    />
                  </div>
                </div>
              </details>
            </div>
          ) : null}
        </div>

        <div>
          <h3 className="mb-3 text-sm font-semibold text-slate-200">K 线时间解释（回测 / RTH）</h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="space-y-1 sm:col-span-2 lg:col-span-4">
              <div className="field-label">K 线时间按此时区解释</div>
              <select
                className="input-base max-w-md"
                value={strategy.assume_bars_timezone}
                onChange={(e) => patchStrategy({ assume_bars_timezone: e.target.value })}
              >
                <option value="UTC">UTC（服务器 K 线缓存无时区 ISO 默认按此）</option>
                <option value="America/New_York">美东 America/New_York</option>
                <option value="Asia/Shanghai">Asia/Shanghai</option>
              </select>
              <p className="text-[11px] leading-snug text-slate-500">
                勾选「使用服务器 K 线缓存」时，JSON 里无时区时间戳与 LongPort 常见约定一致，多为{" "}
                <strong className="font-medium text-slate-400">UTC 墙钟</strong>；此时应选 UTC。若误选美东，会把 16:00
                当成美东收盘时刻，导致 <span className="font-mono">rth_only</span> 下 RTH 根数接近 0。
              </p>
              {jsonTimezoneConflictHint ? (
                <p className="text-[11px] leading-snug text-amber-200/90">{jsonTimezoneConflictHint}</p>
              ) : null}
            </label>
          </div>
        </div>

        {strategy.strategy_variant === "reaction_zone" ? (
          <div>
            <h3 className="mb-3 text-sm font-semibold text-slate-200">交易时段与纪律</h3>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <NumField
                label="开盘后禁止交易（分钟）"
                hint="开盘后前 N 分钟不下新单。"
                value={strategy.no_trade_first_minutes}
                onChange={(n) => patchStrategy({ no_trade_first_minutes: n })}
                min={0}
                max={120}
              />
              <NumField
                label="开盘限制期（分钟）"
                hint="策略内「受限开盘」时长，应 ≥ 上一项时更严。"
                value={strategy.restricted_opening_minutes}
                onChange={(n) => patchStrategy({ restricted_opening_minutes: n })}
                min={0}
                max={120}
              />
              <NumField
                label="单笔最长持仓（分钟）"
                value={strategy.max_hold_minutes}
                onChange={(n) => patchStrategy({ max_hold_minutes: n })}
                min={1}
                max={480}
              />
              <NumField
                label="每日最多交易次数"
                value={strategy.max_trades_per_day}
                onChange={(n) => patchStrategy({ max_trades_per_day: n })}
                min={1}
                max={50}
              />
            </div>
            <div className="flex flex-col gap-3 rounded-lg border border-slate-700/50 bg-slate-950/40 p-3 sm:col-span-2 lg:col-span-4">
              <label className="flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-slate-600"
                  checked={strategy.no_new_trades_after_enabled}
                  onChange={(e) => patchStrategy({ no_new_trades_after_enabled: e.target.checked })}
                />
                <span className="text-sm font-medium text-slate-200">启用美东新开仓截止时间</span>
              </label>
              <p className="text-[11px] leading-snug text-slate-500">
                勾选后：美东时间达到下方时刻起<strong className="font-medium text-slate-400">不再开新仓</strong>（已有持仓仍可平仓/止盈止损）。未勾选则仅受 RTH 9:30–16:00 限制。
              </p>
              <div className="grid grid-cols-2 gap-3 sm:max-w-md sm:grid-cols-2">
                <NumField
                  label="截止 · 时（美东 0–23）"
                  hint="例如 12 表示中午 12:00 起禁止新开仓。"
                  value={strategy.no_new_trades_after_hour_et}
                  onChange={(n) => patchStrategy({ no_new_trades_after_hour_et: n })}
                  min={0}
                  max={23}
                />
                <NumField
                  label="截止 · 分（0–59）"
                  value={strategy.no_new_trades_after_minute_et}
                  onChange={(n) => patchStrategy({ no_new_trades_after_minute_et: n })}
                  min={0}
                  max={59}
                />
              </div>
            </div>
          </div>
        ) : (
          <div>
            <h3 className="mb-3 text-sm font-semibold text-slate-200">交易纪律</h3>
            <div className="grid grid-cols-1 gap-3 sm:max-w-xs">
              <NumField
                label="每日最多交易次数"
                hint="早盘模式每日本策略最多开仓次数（宽跨算 1 次）。"
                value={strategy.max_trades_per_day}
                onChange={(n) => patchStrategy({ max_trades_per_day: n })}
                min={1}
                max={50}
              />
            </div>
          </div>
        )}

        {strategy.strategy_variant === "reaction_zone" ? (
          <div>
            <h3 className="mb-3 text-sm font-semibold text-slate-200">反应区与心理价位</h3>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <NumField
                label="反应区半宽（标的价格的 %）"
                hint="例如 0.08 表示约 ±0.08% 带宽。"
                value={strategy.reaction_zone_width_pct}
                onChange={(n) => patchStrategy({ reaction_zone_width_pct: n })}
                min={0.01}
                max={1}
                step={0.01}
              />
              <NumField
                label="心理价位步长"
                hint="如 2.5 生成 570、572.5…"
                value={strategy.psychological_step}
                onChange={(n) => patchStrategy({ psychological_step: n })}
                min={0.1}
                step={0.1}
              />
              <NumField
                label="心理价位最多条数（单侧）"
                value={strategy.psychological_levels_max}
                onChange={(n) => patchStrategy({ psychological_levels_max: n })}
                min={2}
                max={40}
              />
            </div>
          </div>
        ) : null}

        {strategy.strategy_variant === "reaction_zone" ? (
          <div>
            <h3 className="mb-3 text-sm font-semibold text-slate-200">成交量与形态</h3>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <NumField
                label="成交量回看根数"
                value={strategy.volume_lookback_bars}
                onChange={(n) => patchStrategy({ volume_lookback_bars: n })}
                min={2}
                max={200}
              />
              <NumField
                label="成交量突增倍数"
                hint="当前量 ≥ 均量×该值；无成交时可降到 1.2～1.5 试验。"
                value={strategy.volume_spike_multiplier}
                onChange={(n) => patchStrategy({ volume_spike_multiplier: n })}
                min={0.5}
                max={20}
                step={0.1}
              />
              <NumField
                label="突破需连续站稳（根数）"
                value={strategy.breakout_hold_bars}
                onChange={(n) => patchStrategy({ breakout_hold_bars: n })}
                min={2}
                max={20}
              />
              <NumField
                label="反转回踩幅度（标的价格 %）"
                hint="相对价位的百分比，如 0.15 表示 0.15%。"
                value={strategy.reversal_pullback_pct}
                onChange={(n) => patchStrategy({ reversal_pullback_pct: n })}
                min={0.01}
                max={2}
                step={0.01}
              />
            </div>
          </div>
        ) : null}

        <div>
          <h3 className="mb-3 text-sm font-semibold text-slate-200">
            {strategy.strategy_variant === "reaction_zone" ? "缺口与选约" : "选约（行权价）"}
          </h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {strategy.strategy_variant === "reaction_zone" ? (
              <NumField
                label="显著缺口阈值（标的价格 %）"
                hint="如 0.2 表示 0.2% 涨跌视为缺口。"
                value={strategy.gap_threshold_pct}
                onChange={(n) => patchStrategy({ gap_threshold_pct: n })}
                min={0}
                max={5}
                step={0.05}
              />
            ) : null}
            <NumField
              label="行权价步长（点）"
              hint={
                strategy.strategy_variant === "morning_strangle" || strategy.strategy_variant === "morning_directional"
                  ? "与 Call/Put 外移档数配合：先按现价对齐步长，再向外移若干档。"
                  : undefined
              }
              value={strategy.strike_step}
              onChange={(n) => patchStrategy({ strike_step: n })}
              min={0.01}
              step={0.5}
            />
            {strategy.strategy_variant === "reaction_zone" ||
            strategy.strategy_variant === "morning_strangle" ||
            strategy.strategy_variant === "morning_directional" ? (
              <>
                <NumField
                  label="Call 外移档数（0=近 ATM）"
                  value={strategy.call_strikes_otm}
                  onChange={(n) => patchStrategy({ call_strikes_otm: n })}
                  min={0}
                  max={30}
                />
                <NumField
                  label="Put 外移档数（0=近 ATM）"
                  value={strategy.put_strikes_otm}
                  onChange={(n) => patchStrategy({ put_strikes_otm: n })}
                  min={0}
                  max={30}
                />
              </>
            ) : null}
          </div>
        </div>

        <details className="rounded-lg border border-slate-700/60 bg-slate-950/35">
          <summary className="cursor-pointer select-none list-none px-3 py-2.5 text-sm font-semibold text-slate-200 [&::-webkit-details-marker]:hidden hover:text-white">
            <span className="mr-1.5 text-slate-500">▸</span>
            合成定价（Black-Scholes）
            <span className="ml-2 text-xs font-normal text-slate-500">默认折叠</span>
          </summary>
          <div className="border-t border-slate-800/80 px-3 pb-3 pt-2">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <NumField
              label="无风险利率（年化 %）"
              value={strategy.risk_free_rate_pct}
              onChange={(n) => patchStrategy({ risk_free_rate_pct: n })}
              min={0}
              max={20}
              step={0.1}
            />
            <NumField
              label="连续分红率（年化 %）"
              value={strategy.dividend_yield_pct}
              onChange={(n) => patchStrategy({ dividend_yield_pct: n })}
              min={0}
              max={20}
              step={0.05}
            />
            <NumField
              label="波动率估计窗口（根 K）"
              value={strategy.vol_window_bars}
              onChange={(n) => patchStrategy({ vol_window_bars: n })}
              min={2}
              max={500}
            />
            <NumField
              label="最小年化波动率（%）"
              value={strategy.min_sigma_pct}
              onChange={(n) => patchStrategy({ min_sigma_pct: n })}
              min={1}
              max={200}
              step={0.5}
            />
            <NumField
              label="0DTE 到期时刻（美东 时）"
              value={strategy.option_expiry_hour_et}
              onChange={(n) => patchStrategy({ option_expiry_hour_et: n })}
              min={0}
              max={23}
            />
            <NumField
              label="0DTE 到期时刻（分）"
              value={strategy.option_expiry_minute_et}
              onChange={(n) => patchStrategy({ option_expiry_minute_et: n })}
              min={0}
              max={59}
            />
            </div>
          </div>
        </details>

        <details className="rounded-lg border border-slate-700/60 bg-slate-950/35">
          <summary className="cursor-pointer select-none list-none px-3 py-2.5 text-sm font-semibold text-slate-200 [&::-webkit-details-marker]:hidden hover:text-white">
            <span className="mr-1.5 text-slate-500">▸</span>
            出场、滑点与回测
            <span className="ml-2 text-xs font-normal text-slate-500">默认折叠</span>
          </summary>
          <div className="border-t border-slate-800/80 px-3 pb-3 pt-2">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {strategy.strategy_variant === "reaction_zone" ? (
              <>
                <NumField
                  label="止盈（权利金涨幅 %）"
                  hint="例如 40 表示较入场价上涨 40% 触发。"
                  value={strategy.take_profit_pct}
                  onChange={(n) => patchStrategy({ take_profit_pct: n })}
                  min={1}
                  max={500}
                />
                <NumField
                  label="止损（权利金跌幅 %）"
                  value={strategy.stop_loss_pct}
                  onChange={(n) => patchStrategy({ stop_loss_pct: n })}
                  min={1}
                  max={100}
                />
              </>
            ) : null}
            <NumField
              label="期权价滑点（%）"
              hint="买卖各按理论价恶化一定比例。"
              value={strategy.option_slippage_pct}
              onChange={(n) => patchStrategy({ option_slippage_pct: n })}
              min={0}
              max={50}
              step={0.5}
            />
            <NumField
              label="每次张数"
              value={strategy.initial_option_contracts}
              onChange={(n) => patchStrategy({ initial_option_contracts: n })}
              min={1}
              max={50}
            />
            <NumField
              label="合约乘数（美股期权 100）"
              value={strategy.contract_multiplier}
              onChange={(n) => patchStrategy({ contract_multiplier: n })}
              min={1}
              max={1000}
            />
            <label className="flex cursor-pointer items-end gap-2 pb-2">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-slate-600"
                checked={strategy.log_decisions}
                onChange={(e) => patchStrategy({ log_decisions: e.target.checked })}
              />
              <span className="text-sm text-slate-300">输出决策日志（后端）</span>
            </label>
          </div>
          {strategy.strategy_variant !== "reaction_zone" ? (
            <p className="mt-2 text-[11px] text-slate-500">
              早盘模式的止盈/止损由上方紫色区域内的权利金盈亏率阈值与强平时刻控制，此处不再使用反应区模式的「止盈/止损（权利金%）」。
            </p>
          ) : null}
          </div>
        </details>

        <button
          type="button"
          className="btn-secondary text-sm"
          onClick={() => setShowMore((v) => !v)}
        >
          {showMore ? "收起说明" : "参数说明与提示"}
        </button>
        {showMore ? (
          <ul className="list-inside list-disc space-y-1 text-xs text-slate-500">
            <li>1m K 线 + 长日历天回测耗时可观，建议先用较小天数或日 K 冒烟。</li>
            <li>「反应区半宽」越大，越容易落在反应区内，信号可能变多但噪音也大。</li>
            <li>
              高级用户可用下方 JSON 覆盖 <span className="font-mono">Qqq0dteConfig</span> 字段；提交回测/矩阵时{" "}
              <span className="font-mono">assume_bars_timezone</span> 与 <span className="font-mono">strategy_variant</span>{" "}
              以表单为准，并会按当前变体<strong>剔除</strong>无关键（高级 JSON 里多写的字段不会发往 API）。
            </li>
          </ul>
        ) : null}

        <div className="border-t border-slate-700/60 pt-4">
          <button
            type="button"
            className="btn-secondary mb-3 text-sm"
            onClick={() => setShowAdvancedJson((v) => !v)}
          >
            {showAdvancedJson ? "隐藏高级 JSON 覆盖" : "高级：JSON 覆盖（合并到上方表单）"}
          </button>
          {showAdvancedJson ? (
            <div className="space-y-1">
              <p className="text-xs text-slate-500">
                解析为 JSON 对象后与表单生成的配置合并，同名键以这里为准；例外：{" "}
                <span className="font-mono">assume_bars_timezone</span> 与{" "}
                <span className="font-mono">strategy_variant</span> 始终采用表单（策略变体下拉 + K 线时区）。合并后按变体白名单
                <strong>剔除</strong>未使用字段再提交 API。留空则仅提交表单字段（同样经白名单过滤）。
                点「去掉非本策略键」仅删除<strong>当前表单策略变体</strong>白名单以外的字段；本策略相关键（含{" "}
                <span className="font-mono">strategy_variant</span>、<span className="font-mono">assume_bars_timezone</span>、
                <span className="font-mono">gamma_rt_*</span> 等）一律保留原样，便于整段复制到{" "}
                <span className="font-mono">live_worker_config.json</span>。
                点「生成完整 strategy_config」会按<strong>后端默认 ⊕ 当前表单 ⊕ 下方 JSON 覆盖</strong>合并，强制使用页面标的与表单的变体/时区，再按白名单写出<strong>键齐全</strong>的对象并填入本框。
                合并规则与回测一致：同名键以框内 JSON 为准；若只改了表单、未改框内，请再点一次本按钮刷新。
                支持行尾注释 <span className="font-mono text-purple-400/90"># 字段含义 #</span>（紫色为说明），解析前会自动剥离；生成完整配置与快照载入等会自动附上说明。
              </p>
              {jsonTimezoneConflictHint ? (
                <p className="text-xs text-amber-200/90">{jsonTimezoneConflictHint}</p>
              ) : null}
              <div className="flex flex-wrap justify-end gap-2">
                <button type="button" className="btn-secondary text-xs" onClick={() => void onPruneAdvancedJson()}>
                  去掉非本策略键（保留本策略全字段）
                </button>
                <button type="button" className="btn-primary text-xs" onClick={() => void onExportCompleteStrategyConfig()}>
                  生成完整 strategy_config（默认+表单+JSON）
                </button>
              </div>
              <StrategyConfigJsonTextarea
                value={advancedJson}
                onChange={setAdvancedJson}
                minHeightClass="min-h-[120px]"
                className="w-full"
                placeholder='例如 {"rth_open_hour": 9, "rth_open_minute": 30}'
              />
            </div>
          ) : null}
        </div>

        <button
          type="button"
          className="btn-primary"
          disabled={loading || !symbol.trim()}
          onClick={() => void runBacktest()}
        >
          {loading ? "策略回测中（最长约 5 分钟）…" : "运行策略回测"}
        </button>
      </div>

      {result ? (
        <div className="panel space-y-4">
          <div className="section-title">策略验证结果</div>
          {result.disclaimer ? <p className="text-xs text-amber-200/90">{result.disclaimer}</p> : null}
          <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
            <div className="rounded border border-slate-700/70 p-2">
              K 线根数 <span className="font-mono text-cyan-200">{result.bar_count ?? "—"}</span>
            </div>
            <div className="rounded border border-slate-700/70 p-2">
              已实现盈亏{" "}
              <span className="font-mono text-cyan-200">{result.realized_pnl ?? "—"}</span>
            </div>
            <div className="rounded border border-slate-700/70 p-2">
              费用合计 <span className="font-mono text-cyan-200">{result.total_fee ?? "—"}</span>
            </div>
            <div className="rounded border border-slate-700/70 p-2">
              平仓笔数{" "}
              <span className="font-mono text-cyan-200">{result.stats?.closed_trades ?? "—"}</span>
            </div>
          </div>
          <p className="text-xs text-slate-500">
            样本拆分：总 K 线{" "}
            <span className="font-mono text-slate-300">{result.bar_count_total ?? "—"}</span>，
            RTH{" "}
            <span className="font-mono text-slate-300">{result.bar_count_rth ?? "—"}</span>，
            非 RTH{" "}
            <span className="font-mono text-slate-300">{result.bar_count_non_rth ?? "—"}</span>
            （rth_only={String(Boolean(result.rth_only))}）。
          </p>
          <p className="text-xs text-slate-500">
            本次回测使用的{" "}
            <span className="font-mono text-slate-400">assume_bars_timezone</span>（来自合并后的策略配置）：{" "}
            <span className="font-mono text-slate-300">
              {typeof result.config?.assume_bars_timezone === "string"
                ? result.config.assume_bars_timezone
                : "—"}
            </span>
          </p>
          <div className="flex flex-wrap gap-3 text-sm text-slate-300">
            <span>开仓事件: {result.open_events ?? "—"}</span>
            <span>平仓事件: {result.close_events ?? "—"}</span>
            <span>胜率: {result.stats?.win_rate_pct ?? "—"}%</span>
            <span>
              胜/负: {result.stats?.wins ?? "—"} / {result.stats?.losses ?? "—"}
            </span>
          </div>
          {result.return_pct != null ? (
            <p className="text-sm text-slate-300">
              盈亏率（相对累计开仓权利金）:{" "}
              <span className="font-mono text-cyan-200">{result.return_pct}%</span>
              {result.open_premium_debit_usd != null ? (
                <span className="ml-2 text-xs text-slate-500">
                  · 分母{" "}
                  <span className="font-mono text-slate-400">{result.open_premium_debit_usd}</span> USD
                </span>
              ) : null}
            </p>
          ) : result.open_premium_debit_usd != null && Number(result.open_premium_debit_usd) <= 0 ? (
            <p className="text-sm text-slate-500">盈亏率：无开仓，分母为 0</p>
          ) : null}
          {result.snapshot ? (
            <p className="text-xs text-slate-500">
              {result.snapshot.saved ? (
                <>
                  快照已保存 · 策略{" "}
                  <span className="font-medium text-slate-300">
                    {qqq0dteStrategyDisplayName(
                      result.config && typeof result.config === "object" && !Array.isArray(result.config)
                        ? (result.config as Record<string, unknown>)
                        : null
                    )}
                  </span>
                  {" · "}
                  <span className="font-mono text-emerald-200/85">{result.snapshot.id ?? "—"}</span>
                  {result.snapshot.created_at ? (
                    <span className="ml-2 font-mono text-slate-500">{result.snapshot.created_at}</span>
                  ) : null}
                </>
              ) : (
                <>本次未写入快照（已取消勾选「本次回测写入快照」）。</>
              )}
            </p>
          ) : null}

          {result.decision_summary ? (
            <div className="space-y-3 rounded-lg border border-amber-500/25 bg-amber-950/15 p-4">
              <div className="text-sm font-semibold text-amber-100/95">无成交 / 决策原因汇总</div>
              {!result.decision_summary.log_decisions_enabled ? (
                <p className="text-xs text-amber-200/85">
                  当前配置关闭了 <span className="font-mono">log_decisions</span>，无法统计。请勾选「输出决策日志（后端）」或在高级 JSON 中设置{" "}
                  <span className="font-mono">{`"log_decisions": true`}</span> 后重新回测。
                </p>
              ) : result.decision_summary.total_log_lines === 0 ? (
                <p className="text-xs text-slate-400">无决策日志行（K 线根数为 0 或未产生日志）。</p>
              ) : (
                <>
                  <p className="text-xs text-slate-400">
                    按每根 K 线策略返回的 <span className="font-mono">message</span> 聚合（共{" "}
                    <span className="font-mono text-cyan-200/90">{result.decision_summary.total_log_lines}</span>{" "}
                    条，约覆盖{" "}
                    <span className="font-mono text-cyan-200/90">{result.decision_summary.bar_count}</span> 根
                    bar）。占比列：占全部日志条数 / 占 K 线根数。
                  </p>
                  {result.decision_summary.entry_blocker ? (
                    <p className="text-xs text-slate-500">
                      「开仓闸门」类合计{" "}
                      <span className="font-mono text-slate-300">
                        {result.decision_summary.entry_blocker.total_hits ?? 0}
                      </span>{" "}
                      次（占日志{" "}
                      <span className="font-mono text-slate-300">
                        {result.decision_summary.entry_blocker.pct_of_logs ?? 0}%
                      </span>
                      ）。{result.decision_summary.entry_blocker.hint}
                    </p>
                  ) : null}
                  <div className="table-shell max-h-[280px] overflow-auto">
                    <table className="w-full min-w-[560px] text-xs">
                      <thead className="table-head sticky top-0 z-10 text-left">
                        <tr>
                          <th className="px-2 py-1.5">原因（中文）</th>
                          <th className="px-2 py-1.5">代码</th>
                          <th className="px-2 py-1.5">次数</th>
                          <th className="px-2 py-1.5">占日志%</th>
                          <th className="px-2 py-1.5">占bar%</th>
                          <th className="px-2 py-1.5">开仓闸</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(result.decision_summary.by_message ?? []).map((row, idx) => (
                          <tr key={`${row.message ?? idx}-${idx}`} className="border-t border-slate-800/90">
                            <td className="px-2 py-1.5 text-slate-200">{row.label_zh ?? row.message ?? "—"}</td>
                            <td className="px-2 py-1.5 font-mono text-[10px] text-slate-500">{row.message ?? "—"}</td>
                            <td className="px-2 py-1.5 font-mono text-slate-300">{row.count ?? "—"}</td>
                            <td className="px-2 py-1.5 font-mono text-slate-400">{row.pct_of_logs ?? "—"}</td>
                            <td className="px-2 py-1.5 font-mono text-slate-400">{row.pct_of_bars ?? "—"}</td>
                            <td className="px-2 py-1.5 text-slate-500">{row.is_entry_blocker ? "是" : ""}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {(result.decision_summary.preview_tail?.length ?? 0) > 0 ? (
                    <details className="text-xs text-slate-500">
                      <summary className="cursor-pointer text-slate-400 hover:text-slate-300">
                        最近 {result.decision_summary.preview_tail?.length} 条日志（尾部采样）
                      </summary>
                      <pre className="mt-2 max-h-40 overflow-auto rounded border border-slate-800 bg-slate-950/80 p-2 font-mono text-[10px] text-slate-400">
                        {JSON.stringify(result.decision_summary.preview_tail, null, 2)}
                      </pre>
                    </details>
                  ) : null}
                </>
              )}
            </div>
          ) : null}

          <div className="table-shell max-h-[480px] overflow-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="table-head sticky top-0 z-10 text-left">
                <tr>
                  <th className="px-3 py-2">事件</th>
                  <th className="px-3 py-2">bar</th>
                  <th className="px-3 py-2">时间点</th>
                  <th className="px-3 py-2">方向/行权价</th>
                  <th className="px-3 py-2">价格/盈亏</th>
                  <th className="px-3 py-2">备注</th>
                </tr>
              </thead>
              <tbody>
                {trades.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-4 text-slate-500">
                      无成交记录（可能整段行情未触发信号，或参数过严）。
                    </td>
                  </tr>
                ) : (
                  trades.slice(0, 200).map((t, idx) => {
                    const ev = String(t.event ?? "");
                    const barIdx = t.bar_index;
                    const reason = String(t.reason ?? "");
                    const barTimeEt = typeof t.bar_time_et === "string"
                      ? t.bar_time_et
                      : typeof t.bar_time_local === "string"
                        ? t.bar_time_local
                        : "";
                    const barTimeRaw = typeof t.bar_time_raw === "string"
                      ? t.bar_time_raw
                      : typeof t.bar_time_utc === "string"
                        ? t.bar_time_utc
                        : "";
                    const timeText = barTimeEt || barTimeRaw
                      ? [barTimeEt ? `美东 ${barTimeEt}` : "", barTimeRaw ? `原始K线 ${barTimeRaw}` : ""]
                          .filter((x) => x && String(x).trim().length > 0)
                          .join(" | ")
                      : "—";
                    const note = [reason]
                      .filter((x) => x && String(x).trim().length > 0)
                      .join(" | ");
                    const net = t.net_pnl;
                    const fee = t.fee;
                    const entry = t.entry_px;
                    const exitPx = t.exit_px;
                    return (
                      <tr key={`${ev}-${idx}`} className="border-t border-slate-800/90">
                        <td className="px-3 py-2 font-mono text-xs">{ev}</td>
                        <td className="px-3 py-2 font-mono">{barIdx != null ? String(barIdx) : "—"}</td>
                        <td className="max-w-[320px] truncate px-3 py-2 font-mono text-[11px] text-slate-300" title={timeText}>
                          {timeText}
                        </td>
                        <td
                          className="max-w-[280px] px-3 py-2 font-mono text-[11px] leading-snug text-slate-200"
                          title={formatBacktestDirectionStrike(t as Record<string, unknown>)}
                        >
                          {formatBacktestDirectionStrike(t as Record<string, unknown>)}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs">
                          {entry != null ? `入 ${Number(entry).toFixed(4)}` : ""}
                          {exitPx != null ? ` → 出 ${Number(exitPx).toFixed(4)}` : ""}
                          {net != null ? ` | 净 ${Number(net).toFixed(2)}` : ""}
                          {fee != null ? ` (费 ${Number(fee).toFixed(2)})` : ""}
                        </td>
                        <td className="max-w-[320px] truncate px-3 py-2 text-xs text-slate-400" title={note || "—"}>
                          {note || "—"}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          {trades.length > 200 ? (
            <p className="text-xs text-slate-500">仅展示前 200 条，完整数据请从 API 响应复制。</p>
          ) : null}
        </div>
      ) : null}
        </div>
      </details>
    </PageShell>
  );
}
