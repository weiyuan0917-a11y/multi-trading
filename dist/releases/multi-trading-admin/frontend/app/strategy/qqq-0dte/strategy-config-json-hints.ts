/**
 * strategy_config 展示用：行尾附加 `  # 中文说明 #`（非标准 JSON，保存/解析前会剥离）。
 * 说明内请勿使用 # 号。兼容旧版 `  # （说明） #`。
 */

export const STRATEGY_CONFIG_FIELD_HINTS: Record<string, string> = {
  strategy_variant: "策略变体：reaction_zone 反应区 / morning_strangle 早盘宽跨 / morning_directional 早盘方向单 / gamma_scalping / gamma_pro",
  symbol: "标的代码，如 QQQ.US",
  assume_bars_timezone: "无时区 K 线 datetime 按此时区解释墙钟",
  rth_open_hour: "常规交易时段开盘：美东小时",
  rth_open_minute: "常规交易时段开盘：美东分钟",
  rth_close_hour: "常规交易时段收盘：美东小时",
  rth_close_minute: "常规交易时段收盘：美东分钟",
  no_trade_first_minutes: "开盘后若干分钟内禁止新开仓",
  restricted_opening_minutes: "开盘初期受限阶段长度（分钟）",
  no_new_trades_after_enabled: "是否启用「过指定美东时刻后禁止新开仓」",
  no_new_trades_after_hour_et: "禁止新开仓：美东小时",
  no_new_trades_after_minute_et: "禁止新开仓：美东分钟",
  max_hold_minutes: "单笔最大持有时间（分钟）",
  max_trades_per_day: "每个交易日最大开仓次数",
  reaction_zone_half_width_pct: "反应区半宽，占标的价格的比例（小数）",
  psychological_step: "心理整数价位网格步长，如 2.5",
  psychological_levels_max: "心理价位单侧最多条数",
  volume_lookback_bars: "成交量均值回看的 K 线根数",
  volume_spike_multiplier: "放量倍数：当前量需 ≥ 均值×该值",
  breakout_hold_bars: "突破形态需连续确认的 K 线根数",
  reversal_pullback_pct: "反转回踩幅度，占标的价格比例",
  gap_threshold_pct: "缺口阈值，相对昨收涨跌幅比例",
  strike_step: "选约行权价步长（点）",
  call_strikes_otm: "Call 向外 OTM 档数",
  put_strikes_otm: "Put 向外 OTM 档数",
  risk_free_rate: "无风险利率（年化，用于期权定价）",
  dividend_yield: "连续股息率（用于定价）",
  vol_window_bars: "历史波动估计窗口（K 线根数）",
  min_sigma: "隐含波动下限 σ（小数，如 0.12）",
  option_expiry_hour_et: "期权到期时刻：美东小时",
  option_expiry_minute_et: "期权到期时刻：美东分钟",
  take_profit_pct: "止盈：相对成本的比例（小数）",
  stop_loss_pct: "止损：相对成本的比例（小数）",
  option_slippage_pct: "成交滑点：占期权价的比例（恶化方向）",
  initial_option_contracts: "每次开仓张数",
  contract_multiplier: "合约乘数，如 100",
  log_decisions: "是否写入决策日志",

  strangle_entry_start_hhmm_et: "早盘宽跨：允许开仓起始时刻（美东 HH:MM）",
  strangle_entry_end_hhmm_et: "早盘宽跨：允许开仓结束时刻（美东 HH:MM）",
  strangle_force_close_hhmm_et: "早盘宽跨/方向单：强制平仓时刻（美东 HH:MM）",
  strangle_range_pct: "宽跨：相对前收涨跌幅绝对值须低于该比例才开仓",
  strangle_take_profit_return: "宽跨：组合止盈目标盈亏率（已实现平仓金额 + 剩余腿 bid，相对原始建仓权利金）",
  strangle_stop_loss_return: "宽跨：组合止损亏损率阈值；0 表示关闭",
  strangle_stop_loss_cooldown_minutes: "组合止损冷静期（分钟），开仓后该时长内不触发",
  strangle_leg_take_profit_pct: "宽跨：单腿止盈比例（相对该腿开仓成本）；0 表示关闭",
  strangle_long_leg_take_profit_pct: "宽跨：长腿单腿止盈比例（按 OTM 档数较大的一腿）；0 表示沿用旧单腿止盈",
  strangle_short_leg_take_profit_pct: "宽跨：短腿单腿止盈比例（OTM 档数较小；两腿相同都按短腿）；0 表示沿用旧单腿止盈",
  strangle_leg_stop_loss_pct: "宽跨：单腿止损比例（相对该腿开仓成本）；0 表示关闭",
  strangle_underlying_field: "回测用 K 线哪一档近似现价：open/high/low/close",

  directional_down_pct: "方向单：相对前收跌幅达该比例时买 Call",
  directional_up_pct: "方向单：相对前收涨幅达该比例时买 Put",
  directional_take_profit_return: "方向单：单腿止盈目标盈亏率",
  directional_stop_loss_pct: "方向单：单腿止损亏损比例；0 关闭",

  gamma_entry_start_hhmm_et: "Gamma：开仓窗口起始（美东 HH:MM）",
  gamma_entry_end_hhmm_et: "Gamma：开仓窗口结束（美东 HH:MM）",
  gamma_force_close_hhmm_et: "Gamma：强制平仓时刻（美东 HH:MM）",
  gamma_max_hold_minutes: "Gamma：最大持仓（分钟）",
  gamma_hard_stop_loss_pct: "Gamma：硬止损亏损比例",
  gamma_take_profit_min_return: "Gamma：止盈盈亏率下限",
  gamma_take_profit_max_return: "Gamma：止盈盈亏率上限",
  gamma_call_otm_steps: "Gamma：Call 取近 ATM 外第几档",
  gamma_put_otm_steps: "Gamma：Put 取近 ATM 外第几档",
  gamma_require_breakout_prev_day: "是否要求突破昨高/昨低才做突破腿",
  gamma_require_vix_rising: "是否要求 VIX 相对开盘上涨",
  gamma_vix_rising_min_pct: "VIX 最小涨幅（百分比口径，如 0.3 表示 0.3%）",
  gamma_enable_vwap_reversion: "是否启用 VWAP 回归分支",
  gamma_vwap_deviation_pct: "偏离 VWAP 的阈值（占价比例）",
  gamma_require_leader_confirmation: "是否要求龙头标的联动确认",
  gamma_leader_min_move_pct: "龙头最小波动（%）",
  gamma_leader_lag_minutes: "龙头信号滞后窗口（分钟）",
  gamma_leader_lag_pct: "龙头相对标的滞后幅度（%）",
  gamma_vix_symbol: "VIX 行情代码",
  gamma_leader_symbol_1: "龙头标的 1",
  gamma_leader_symbol_2: "龙头标的 2",
  gamma_rt_vix_change_pct: "实盘上下文：VIX 即时涨跌（%）",
  gamma_rt_qqq_change_pct: "实盘上下文：QQQ 即时涨跌（%）",
  gamma_rt_leader1_change_pct: "实盘上下文：龙头1 即时涨跌（%）",
  gamma_rt_leader2_change_pct: "实盘上下文：龙头2 即时涨跌（%）",

  gamma_pro_entry_start_hhmm_et: "Gamma Pro：允许开仓起始（美东）",
  gamma_pro_entry_end_hhmm_et: "Gamma Pro：允许开仓结束（美东）",
  gamma_pro_midday_skip_start_hhmm_et: "Gamma Pro：午间跳过段开始",
  gamma_pro_midday_skip_end_hhmm_et: "Gamma Pro：午间跳过段结束",
  gamma_pro_afternoon_start_hhmm_et: "Gamma Pro：午后续航段开始",
  gamma_pro_force_close_hhmm_et: "Gamma Pro：强制平仓时刻",
  gamma_pro_max_hold_minutes: "Gamma Pro：最大持仓（分钟）",
  gamma_pro_hard_stop_loss_pct: "Gamma Pro：硬止损亏损比例",
  gamma_pro_take_profit_return: "Gamma Pro：止盈盈亏率",
  gamma_pro_call_otm_steps: "Gamma Pro：Call OTM 档数",
  gamma_pro_put_otm_steps: "Gamma Pro：Put OTM 档数",
  gamma_pro_require_leader_confirmation: "Gamma Pro：是否要求龙头确认",
  gamma_pro_enable_false_breakout_reversal: "Gamma Pro：是否启用假突破反手",
  gamma_pro_vwap_pullback_pct: "Gamma Pro：回踩 VWAP 允许偏离（占价比例）",
};

/** 去掉行尾 `  # … #`（说明内勿使用 #）；兼容旧版带括号包裹的写法 */
export function stripStrategyConfigHashComments(raw: string): string {
  return raw
    .split("\n")
    .map((line) => line.replace(/\s+#\s*[^#]*#\s*$/, "").trimEnd())
    .join("\n");
}

/** 拆分行尾 `# … #` 注释，供语法着色叠加层使用 */
export function splitStrategyConfigLineComment(line: string): { head: string; comment: string | null } {
  const m = line.match(/^(.*?)(\s+#\s*[^#]*#\s*)$/);
  if (!m) return { head: line, comment: null };
  return { head: m[1], comment: m[2] };
}

/** 带行尾含义注释的格式化（仅用于界面展示与手工编辑） */
export function stringifyStrategyConfigWithHints(sc: Record<string, unknown>): string {
  const keys = Object.keys(sc);
  if (keys.length === 0) return "{}";
  const lines: string[] = ["{"];
  for (let i = 0; i < keys.length; i++) {
    const k = keys[i];
    const v = sc[k];
    const comma = i < keys.length - 1 ? "," : "";
    const hintText = STRATEGY_CONFIG_FIELD_HINTS[k];
    const tail = hintText ? `  # ${hintText} #` : "";
    lines.push(`  ${JSON.stringify(k)}: ${JSON.stringify(v)}${comma}${tail}`);
  }
  lines.push("}");
  return lines.join("\n");
}
