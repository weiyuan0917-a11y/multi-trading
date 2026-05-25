"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { PageShell } from "@/components/ui/page-shell";
import { localAgentGet as apiGet, localAgentPost as apiPost } from "@/lib/local-agent-api";

type LabInstance = "0dte" | "1dte" | "stock_options_swing";
type LabStrategyVariant =
  | "morning_strangle"
  | "morning_double_strangle"
  | "morning_directional"
  | "swing_trend_call"
  | "swing_pullback_call"
  | "swing_breakout_call"
  | "swing_event_filtered_call";
type CandidateGenerator = "deterministic" | "tradingagents";
type ResearchDimension = "risk_controls" | "time_window" | "combined" | "leg_gap";

type DataQualityCheck = {
  id?: string;
  severity?: "ok" | "info" | "warn" | "error" | string;
  title?: string;
  detail?: string;
  value?: any;
};

type ValidationRow = {
  days?: number;
  ok?: boolean;
  error?: string;
  metrics?: Record<string, any>;
};

type LabCandidate = {
  candidate_id?: string;
  title?: string;
  generator?: string;
  generator_mode?: string;
  agent_action?: string;
  confidence?: number;
  reasoning?: string[];
  strategy_config_patch?: Record<string, any>;
  strategy_config?: Record<string, any>;
  research_controls?: Record<string, any>;
  validation?: {
    passed?: boolean;
    summary?: Record<string, any>;
    rows?: ValidationRow[];
    blockers?: string[];
    gate?: Record<string, any>;
  };
  safety_note?: string;
};

type LabRun = {
  run_id?: string;
  created_at?: string;
  instance?: LabInstance | string;
  status?: string;
  request?: Record<string, any>;
  pipeline?: Array<{ stage?: string; label?: string; status?: string; mode?: string }>;
  data_quality?: {
    ok?: boolean;
    summary?: Record<string, any>;
    checks?: DataQualityCheck[];
    current_config?: Record<string, any>;
  };
  candidates?: LabCandidate[];
  approvals?: any[];
  approved_candidate_id?: string;
  disclaimer?: string;
};

type LabStatus = {
  ok?: boolean;
  instance?: string;
  data_quality?: {
    ok?: boolean;
    summary?: Record<string, any>;
    checks?: DataQualityCheck[];
    current_config?: Record<string, any>;
  };
  last_run?: LabRun | null;
  last_approval?: Record<string, any> | null;
  approval_history?: LabApproval[];
  capabilities?: Record<string, any>;
};

type DiffRow = {
  field?: string;
  before?: any;
  after?: any;
};

type LabApproval = {
  approval_id?: string;
  approved_at?: string;
  approved_by?: string;
  run_id?: string;
  candidate_id?: string;
  instance?: string;
  live_config_path?: string;
  diff?: DiffRow[];
};

type DiffPreview = {
  ok?: boolean;
  run_id?: string;
  candidate_id?: string;
  instance?: string;
  live_config_path?: string;
  strategy_config_patch?: Record<string, any>;
  diff?: DiffRow[];
  force?: boolean;
};

type LabTask = {
  task_id?: string;
  status?: "queued" | "running" | "completed" | "failed" | string;
  created_at?: string;
  updated_at?: string;
  completed_at?: string;
  instance?: string;
  progress_pct?: number;
  progress_stage?: string;
  progress_text?: string;
  error?: string;
  run_id?: string;
  run?: LabRun | null;
  events?: Array<{ ts?: string; stage?: string; pct?: number; text?: string }>;
};

const INSTANCE_OPTIONS: Array<{ value: LabInstance; label: string }> = [
  { value: "0dte", label: "QQQ 0DTE" },
  { value: "1dte", label: "股票期权日内" },
  { value: "stock_options_swing", label: "股票期权中长线" },
];

const INTRADAY_STRATEGY_OPTIONS: Array<{ value: LabStrategyVariant; label: string; description: string }> = [
  { value: "morning_strangle", label: "早盘宽跨", description: "窄幅震荡假设，双买 Call + Put" },
  { value: "morning_double_strangle", label: "早盘双宽跨", description: "Call/Put 各两条腿，验证四腿步长和分腿止盈" },
  { value: "morning_directional", label: "早盘方向单", description: "涨跌幅阈值触发单腿方向" },
];

const SWING_STRATEGY_OPTIONS: Array<{ value: LabStrategyVariant; label: string; description: string }> = [
  { value: "swing_trend_call", label: "趋势买入 Call", description: "日线多头排列和趋势分触发，验证 DTE、止盈止损和预算" },
  { value: "swing_pullback_call", label: "回调买入 Call", description: "大趋势向上但不过度远离 MA20，减少追高" },
  { value: "swing_breakout_call", label: "突破买入 Call", description: "近期新高和强趋势触发，止损更严格" },
  { value: "swing_event_filtered_call", label: "事件过滤趋势 Call", description: "更长 DTE 和事件窗口过滤，降低财报/宏观跳空风险" },
];

const STRATEGY_OPTIONS = [...INTRADAY_STRATEGY_OPTIONS, ...SWING_STRATEGY_OPTIONS];

const GENERATOR_OPTIONS: Array<{ value: CandidateGenerator; label: string }> = [
  { value: "deterministic", label: "规则生成器" },
  { value: "tradingagents", label: "TradingAgents 入口" },
];

const RESEARCH_DIMENSION_OPTIONS: Array<{ value: ResearchDimension; label: string; description: string }> = [
  { value: "risk_controls", label: "风控优先", description: "验证步长、止盈止损，不主动改时间" },
  { value: "leg_gap", label: "Leg gap", description: "Double strangle only: compare long/short leg gap = 1/2/3; timing unchanged" },
  { value: "time_window", label: "时间窗口优先", description: "验证入场窗口和强平时间，不主动改 TP/SL" },
  { value: "combined", label: "综合候选", description: "时间、步长和风控一起变化，需二次归因" },
];

const PIPELINE = [
  "智能体研究层",
  "回测与验证层",
  "人工确认 / 自动审批闸门",
  "live_worker_config 草稿",
  "QQQ 实盘 worker",
  "券商 API 下单",
];

const VALIDATION_WINDOWS_DAYS = [60, 120, 180];

function toneClass(tone?: string): string {
  const s = String(tone || "").toLowerCase();
  if (s === "ok" || s === "completed" || s === "passed") return "border-emerald-400/35 bg-emerald-400/10 text-emerald-100";
  if (s === "warn" || s === "waiting_for_human") return "border-amber-300/40 bg-amber-300/10 text-amber-100";
  if (s === "error" || s === "failed" || s === "blocked") return "border-rose-400/40 bg-rose-400/10 text-rose-100";
  return "border-slate-600 bg-slate-800/70 text-slate-300";
}

function actionLabel(action?: string): string {
  const a = String(action || "");
  if (a === "skip") return "建议跳过";
  if (a === "reduce_size") return "建议降尺寸";
  if (a === "normal_size") return "正常尺寸";
  return a || "-";
}

function fmt(value: any, digits = 2): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return value === null || value === undefined || value === "" ? "-" : String(value);
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function shortJson(value: any): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return "{}";
  }
}

function inlineValue(value: any): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function normalizeSymbols(value: any): string[] {
  const raw = Array.isArray(value) ? value : String(value || "").split(/[\s,;，；]+/);
  const out: string[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    let symbol = String(item || "").trim().toUpperCase();
    if (!symbol) continue;
    if (!symbol.includes(".")) symbol = `${symbol}.US`;
    if (!seen.has(symbol)) {
      seen.add(symbol);
      out.push(symbol);
    }
  }
  return out;
}

function formatBeijingTime(value?: string): string {
  const raw = String(value || "").trim();
  if (!raw) return "-";
  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) return raw;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(dt);
}

function instanceLabel(value?: string): string {
  const raw = String(value || "").toLowerCase();
  if (raw === "stock_options_swing") return "股票期权中长线";
  if (raw === "1dte") return "股票期权日内";
  if (raw === "0dte") return "QQQ 0DTE";
  return value || "-";
}

function strategyLabel(value?: string): string {
  const raw = String(value || "");
  return STRATEGY_OPTIONS.find((item) => item.value === raw)?.label || raw || "-";
}

function researchDimensionLabel(value?: string): string {
  const raw = String(value || "");
  return RESEARCH_DIMENSION_OPTIONS.find((item) => item.value === raw)?.label || raw || "风控优先";
}

function researchDimensionDisplay(value: ResearchDimension, instance?: LabInstance): { label: string; description: string } {
  const base = RESEARCH_DIMENSION_OPTIONS.find((item) => item.value === value);
  if (instance === "stock_options_swing") {
    if (value === "time_window") {
      return {
        label: "持有周期 / DTE 优先",
        description: "验证目标 DTE、最小/最大 DTE、到期前退出 DTE 和趋势退出确认，不主动改入场时间。",
      };
    }
    if (value === "risk_controls") {
      return {
        label: "风控优先",
        description: "验证权利金预算、价差结构、止盈止损、bid/ask spread 和趋势门槛。",
      };
    }
    if (value === "leg_gap") {
      return {
        label: "价差宽度 / OTM 优先",
        description: "中长线下用于比较 Debit Spread 宽度、OTM 百分比和持有周期；不是 0DTE 双宽跨 leg gap。",
      };
    }
    if (value === "combined") {
      return {
        label: "综合候选",
        description: "同时变化 DTE、价差结构、预算和退出规则，适合探索但需要二次归因。",
      };
    }
  }
  return {
    label: base?.label || researchDimensionLabel(value),
    description: base?.description || "验证步长、止盈止损，不主动改时间",
  };
}

function statusLabel(value?: string): string {
  const raw = String(value || "");
  if (raw === "completed") return "已完成";
  if (raw === "approved") return "已审批";
  if (raw === "failed") return "失败";
  if (raw === "running") return "运行中";
  if (raw === "queued") return "排队中";
  return raw || "-";
}

function runSummary(run?: LabRun | null): string {
  if (!run) return "暂无运行记录";
  const total = run.candidates?.length || 0;
  const passed = (run.candidates || []).filter((candidate) => Boolean(candidate.validation?.passed)).length;
  const patch = run.candidates?.[0]?.strategy_config_patch || {};
  const strategyPatch = patch.strategy && typeof patch.strategy === "object" ? patch.strategy : {};
  const variant = String(run.candidates?.[0]?.strategy_config?.strategy_variant || strategyPatch.strategy_variant || run.request?.strategy_variant || "");
  return `${formatBeijingTime(run.created_at)} 北京时间 · ${instanceLabel(String(run.instance || ""))} · ${strategyLabel(variant)} · ${passed}/${total} 通过 · ${statusLabel(run.status)}`;
}

function approvalSummary(item?: LabApproval | null): string {
  if (!item) return "暂无审批记录";
  return `${formatBeijingTime(item.approved_at)} 北京时间 · 候选 ${item.candidate_id || "-"} · ${item.diff?.length || 0} 个字段变化`;
}

const FIELD_LABELS: Record<string, string> = {
  strategy_variant: "策略变体",
  max_trades_per_day: "最大开仓次数",
  initial_option_contracts: "每次张数",
  call_strikes_otm: "Call OTM 步长",
  put_strikes_otm: "Put OTM 步长",
  strangle_entry_start_hhmm_et: "宽跨入场开始",
  strangle_entry_end_hhmm_et: "宽跨入场结束",
  strangle_force_close_hhmm_et: "强制平仓时间",
  strangle_range_pct: "允许偏离前收",
  strangle_take_profit_return: "组合止盈",
  strangle_stop_loss_return: "组合止损",
  strangle_stop_loss_cooldown_minutes: "组合止损冷却",
  strangle_long_leg_take_profit_pct: "长腿单腿止盈",
  strangle_short_leg_take_profit_pct: "短腿单腿止盈",
  strangle_leg_stop_loss_pct: "单腿止损",
  double_strangle_call_long_strikes_otm: "双宽跨 Call 长腿步长",
  double_strangle_call_short_strikes_otm: "双宽跨 Call 短腿步长",
  double_strangle_put_long_strikes_otm: "双宽跨 Put 长腿步长",
  double_strangle_put_short_strikes_otm: "双宽跨 Put 短腿步长",
  double_strangle_call_long_leg_take_profit_pct: "Call 长腿止盈",
  double_strangle_call_short_leg_take_profit_pct: "Call 短腿止盈",
  double_strangle_put_long_leg_take_profit_pct: "Put 长腿止盈",
  double_strangle_put_short_leg_take_profit_pct: "Put 短腿止盈",
  double_strangle_single_leg_stop_loss_pct: "双宽跨单腿止损",
  double_strangle_combo_take_profit_pct: "双宽跨组合止盈",
  double_strangle_combo_stop_loss_pct: "双宽跨组合止损",
  double_strangle_max_total_debit: "双宽跨最大总权利金",
  directional_down_pct: "方向单下跌阈值",
  directional_up_pct: "方向单上涨阈值",
  directional_take_profit_return: "方向单止盈",
  directional_stop_loss_pct: "方向单止损",
  "strategy.strategy_variant": "中长线策略",
  "strategy.mode": "期权结构",
  "strategy.trend_fast_ma": "快线 MA",
  "strategy.trend_slow_ma": "慢线 MA",
  "strategy.long_ma": "长线 MA",
  "strategy.min_trend_score": "最小趋势分",
  "strategy.max_price_above_fast_ma_pct": "距快线最大偏离",
  "strategy.min_dte": "最小 DTE",
  "strategy.target_dte": "目标 DTE",
  "strategy.max_dte": "最大 DTE",
  "strategy.fallback_otm_pct": "OTM 百分比",
  "strategy.spread_width_pct": "价差宽度",
  "strategy.max_spread_debit": "价差最大净权利金",
  "strategy.max_spread_debit_to_width_pct": "净权利金/价差上限",
  "strategy.spread_min_hold_days_before_stop": "价差止损最小持有天数",
  "strategy.sim_spread_slippage_pct": "价差模拟滑点",
  "strategy.max_bid_ask_spread_pct": "最大 bid/ask spread",
  "strategy.take_profit_pct": "止盈",
  "strategy.stop_loss_pct": "止损",
  "strategy.dte_exit_days": "到期前退出 DTE",
  "strategy.earnings_blackout_days": "财报黑窗天数",
  "strategy.trend_exit_below_ma": "跌破 MA 退出",
  "strategy.trend_exit_confirm_bars": "趋势退出确认",
  "risk.max_contracts_per_order": "单笔最大张数",
  "risk.max_open_contracts": "最大总张数",
  "risk.max_premium_per_order": "单笔预算",
  "risk.max_premium_per_symbol": "单标的预算",
  "risk.max_total_option_premium": "总预算",
  "risk.max_new_premium_per_day": "今日新增预算",
};

function fieldLabel(field?: string): string {
  return FIELD_LABELS[String(field || "")] || String(field || "-");
}

function candidateValue(candidate: LabCandidate | undefined, key: string): any {
  const patch = candidate?.strategy_config_patch || {};
  if (Object.prototype.hasOwnProperty.call(patch, key)) return patch[key];
  const strategyPatch = patch.strategy && typeof patch.strategy === "object" ? patch.strategy : {};
  const riskPatch = patch.risk && typeof patch.risk === "object" ? patch.risk : {};
  if (Object.prototype.hasOwnProperty.call(strategyPatch, key)) return strategyPatch[key];
  if (Object.prototype.hasOwnProperty.call(riskPatch, key)) return riskPatch[key];
  return candidate?.strategy_config?.[key];
}

function valueFromDiffOrCandidate(candidate: LabCandidate | undefined, diffRows: DiffRow[] | undefined, key: string): any {
  const row = (diffRows || []).find((item) => item.field === key || item.field === `strategy.${key}` || item.field === `risk.${key}`);
  if (row && Object.prototype.hasOwnProperty.call(row, "after")) return row.after;
  return candidateValue(candidate, key);
}

function changedFieldSet(candidate: LabCandidate | undefined, diffRows?: DiffRow[]): Set<string> {
  if (diffRows) return new Set(diffRows.map((row) => String(row.field || "")).filter(Boolean));
  const patch = candidate?.strategy_config_patch || {};
  const keys = new Set(Object.keys(patch));
  const strategy = patch.strategy && typeof patch.strategy === "object" ? patch.strategy : {};
  const risk = patch.risk && typeof patch.risk === "object" ? patch.risk : {};
  Object.keys(strategy).forEach((key) => {
    keys.add(key);
    keys.add(`strategy.${key}`);
  });
  Object.keys(risk).forEach((key) => {
    keys.add(key);
    keys.add(`risk.${key}`);
  });
  return keys;
}

function ratioPct(value: any, digits = 0): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return `${fmt(n * 100, digits)}%`;
}

function lossThreshold(value: any, digits = 0): string {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "关闭";
  return `-${ratioPct(n, digits)}`;
}

function validationRow(candidate: LabCandidate, days: number): ValidationRow | undefined {
  return (candidate.validation?.rows || []).find((row) => Number(row.days) === days);
}

function validationMetricText(candidate: LabCandidate, days: number, key: string, suffix = ""): string {
  const row = validationRow(candidate, days);
  if (!row) return "-";
  if (!row.ok) return "失败";
  const value = row.metrics?.[key];
  return value === null || value === undefined ? "-" : `${fmt(value)}${suffix}`;
}

function noTradeReasonLabel(reason?: string): string {
  const raw = String(reason || "").trim();
  const labels: Record<string, string> = {
    insufficient_daily_bars: "K线不足",
    no_daily_bars_for_stock_pool: "股票池无K线",
    no_entry_signal: "无入场信号",
    trend_score_low: "趋势分不足",
    below_slow_ma_threshold: "未站稳慢线",
    extended_above_fast_ma: "偏离快线过大",
    pullback_trend_not_confirmed: "回调趋势未确认",
    pullback_wait_for_cooldown: "等待回调冷却",
    pullback_rsi_too_hot: "RSI偏热",
    breakout_not_confirmed: "未确认突破",
    event_filtered_rsi_too_hot: "事件过滤偏热",
    premium_over_budget: "权利金超预算",
    spread_debit_too_high_vs_width: "价差净权利金占比过高",
    no_accepted_entry: "无有效入场",
    no_exit_before_window_end: "未形成退出",
    no_closed_trades: "无闭合交易",
    approx_no_closed_trades_in_at_least_one_window: "存在无交易窗口",
    approx_return_unavailable: "收益不可用",
  };
  return labels[raw] || raw || "无交易";
}

function exitReasonLabel(reason?: string): string {
  const raw = String(reason || "").trim();
  const labels: Record<string, string> = {
    take_profit: "止盈",
    stop_loss: "止损",
    trend_exit: "趋势退出",
    time_exit: "时间退出",
    unknown: "未知",
  };
  return labels[raw] || raw || "-";
}

function validationNoTradeReason(candidate: LabCandidate, days: number): string {
  const row = validationRow(candidate, days);
  if (!row || !row.ok) return "";
  const closed = Number(row.metrics?.closed_trades);
  if (!Number.isFinite(closed) || closed > 0) return "";
  const primary =
    row.metrics?.primary_no_trade_reason ||
    row.metrics?.primary_signal_reason ||
    Object.keys(row.metrics?.no_trade_reason_counts || {})[0] ||
    Object.keys(row.metrics?.signal_reason_counts || {})[0] ||
    "no_closed_trades";
  return noTradeReasonLabel(primary);
}

function validationWindowReturnText(candidate: LabCandidate, days: number): string {
  const reason = validationNoTradeReason(candidate, days);
  if (reason) return "无交易";
  return validationMetricText(candidate, days, "return_pct", "%");
}

function validationWindowSubtext(candidate: LabCandidate, days: number): string {
  const reason = validationNoTradeReason(candidate, days);
  if (reason) return reason;
  return `胜 ${validationMetricText(candidate, days, "win_rate_pct", "%")}`;
}

function candidateTotalClosedTrades(candidate: LabCandidate): number {
  const summaryValue = Number(candidate.validation?.summary?.total_closed_trades);
  if (Number.isFinite(summaryValue)) return summaryValue;
  return (candidate.validation?.rows || []).reduce((acc, row) => acc + (Number(row.metrics?.closed_trades) || 0), 0);
}

function candidateNoTradeWindows(candidate: LabCandidate): number[] {
  const fromSummary = candidate.validation?.summary?.no_trade_windows_days;
  if (Array.isArray(fromSummary)) {
    return fromSummary.map((item) => Number(item)).filter((item) => Number.isFinite(item) && item > 0);
  }
  return (candidate.validation?.rows || [])
    .filter((row) => row.ok && (Number(row.metrics?.closed_trades) || 0) <= 0)
    .map((row) => Number(row.days))
    .filter((item) => Number.isFinite(item) && item > 0);
}

function candidateAvgReturnText(candidate: LabCandidate): string {
  const summary = candidate.validation?.summary || {};
  const totalClosed = candidateTotalClosedTrades(candidate);
  if (summary.avg_return_pct === null || summary.avg_return_pct === undefined) {
    return totalClosed > 0 ? "-" : "无交易";
  }
  return `${fmt(summary.avg_return_pct)}%`;
}

function countSummary(counts: any, limit = 4): string {
  if (!counts || typeof counts !== "object") return "-";
  const rows = Object.entries(counts)
    .map(([key, value]) => ({ key, value: Number(value) }))
    .filter((item) => Number.isFinite(item.value) && item.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, limit);
  return rows.length ? rows.map((item) => `${noTradeReasonLabel(item.key)} ${fmt(item.value, 0)}`).join(" · ") : "-";
}

function compactPatchText(patch: any): string {
  if (!patch || typeof patch !== "object") return "";
  const strategy = patch.strategy && typeof patch.strategy === "object" ? patch.strategy : {};
  const risk = patch.risk && typeof patch.risk === "object" ? patch.risk : {};
  const parts: string[] = [];
  Object.entries(strategy).forEach(([key, value]) => parts.push(`${fieldLabel(`strategy.${key}`)}=${inlineValue(value)}`));
  Object.entries(risk).forEach(([key, value]) => parts.push(`${fieldLabel(`risk.${key}`)}=${inlineValue(value)}`));
  return parts.join("，");
}

function suggestedAdjustments(value: any): any[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") : [];
}

function symbolNoTradeText(item: any): string {
  if (!item || typeof item !== "object") return "-";
  const bits = [
    noTradeReasonLabel(item.reason),
    item.primary_signal_reason ? `信号过滤：${noTradeReasonLabel(item.primary_signal_reason)}` : "",
    Number(item.entry_signals || 0) > 0 ? `信号 ${fmt(item.entry_signals, 0)}` : "",
    Number(item.budget_blocks || 0) > 0 ? `预算过滤 ${fmt(item.budget_blocks, 0)}` : "",
  ].filter(Boolean);
  const block = item.last_budget_block && typeof item.last_budget_block === "object" ? item.last_budget_block : null;
  if (block?.estimated_premium) {
    bits.push(`估算权利金 $${fmt(block.estimated_premium)} / 上限 $${fmt(block.max_premium_per_order)}`);
  }
  return bits.join(" · ");
}

function validationModeText(candidate: LabCandidate, days: number): string {
  const row = validationRow(candidate, days);
  const mode = String(row?.metrics?.mode || candidate.validation?.summary?.mode || "");
  if (mode === "approx_option_path" || mode === "approx_option_backtest") return "粗略";
  if (mode === "research_only") return "研究";
  return "";
}

function swingModeLabel(value: any): string {
  const raw = String(value || "long_call");
  if (raw === "call_debit_spread") return "Debit Spread";
  return "Long Call";
}

function candidateStepText(candidate: LabCandidate): string {
  const variant = String(candidateValue(candidate, "strategy_variant") || candidate.strategy_config?.strategy_variant || "");
  if (variant.startsWith("swing_")) {
    return `${inlineValue(candidateValue(candidate, "min_dte"))}/${inlineValue(candidateValue(candidate, "target_dte"))}/${inlineValue(candidateValue(candidate, "max_dte"))} DTE`;
  }
  if (variant === "morning_double_strangle") {
    return `CL ${inlineValue(candidateValue(candidate, "double_strangle_call_long_strikes_otm"))} / CS ${inlineValue(candidateValue(candidate, "double_strangle_call_short_strikes_otm"))} · PL ${inlineValue(candidateValue(candidate, "double_strangle_put_long_strikes_otm"))} / PS ${inlineValue(candidateValue(candidate, "double_strangle_put_short_strikes_otm"))}`;
  }
  return `C ${inlineValue(candidateValue(candidate, "call_strikes_otm"))} / P ${inlineValue(candidateValue(candidate, "put_strikes_otm"))}`;
}

function candidateComboText(candidate: LabCandidate): { tp: string; sl: string } {
  const variant = String(candidateValue(candidate, "strategy_variant") || candidate.strategy_config?.strategy_variant || "");
  if (variant.startsWith("swing_")) {
    return {
      tp: ratioPct(candidateValue(candidate, "take_profit_pct")),
      sl: lossThreshold(candidateValue(candidate, "stop_loss_pct")),
    };
  }
  if (variant === "morning_double_strangle") {
    return {
      tp: ratioPct(candidateValue(candidate, "double_strangle_combo_take_profit_pct")),
      sl: lossThreshold(candidateValue(candidate, "double_strangle_combo_stop_loss_pct")),
    };
  }
  return {
    tp: ratioPct(candidateValue(candidate, "strangle_take_profit_return")),
    sl: lossThreshold(candidateValue(candidate, "strangle_stop_loss_return")),
  };
}

function candidateLegTpText(candidate: LabCandidate): string[] {
  const variant = String(candidateValue(candidate, "strategy_variant") || candidate.strategy_config?.strategy_variant || "");
  if (variant.startsWith("swing_")) {
    const debitCap = candidateValue(candidate, "max_spread_debit_to_width_pct");
    const minHold = candidateValue(candidate, "spread_min_hold_days_before_stop");
    return [
      `${swingModeLabel(candidateValue(candidate, "mode"))} · 趋势分 ${inlineValue(candidateValue(candidate, "min_trend_score"))}`,
      `OTM ${ratioPct(candidateValue(candidate, "fallback_otm_pct"), 1)} · 宽度 ${ratioPct(candidateValue(candidate, "spread_width_pct"), 1)}${debitCap !== null && debitCap !== undefined && debitCap !== "" ? ` · 净权利金≤${ratioPct(debitCap)}` : ""}${minHold ? ` · 止损${inlineValue(minHold)}天后` : ""}`,
    ];
  }
  if (variant === "morning_double_strangle") {
    return [
      `CL ${ratioPct(candidateValue(candidate, "double_strangle_call_long_leg_take_profit_pct"))} / CS ${ratioPct(candidateValue(candidate, "double_strangle_call_short_leg_take_profit_pct"))}`,
      `PL ${ratioPct(candidateValue(candidate, "double_strangle_put_long_leg_take_profit_pct"))} / PS ${ratioPct(candidateValue(candidate, "double_strangle_put_short_leg_take_profit_pct"))}`,
    ];
  }
  return [
    `长 ${ratioPct(candidateValue(candidate, "strangle_long_leg_take_profit_pct"))}`,
    `短 ${ratioPct(candidateValue(candidate, "strangle_short_leg_take_profit_pct"))}`,
  ];
}

function candidateLegSlText(candidate: LabCandidate): { sl: string; note: string } {
  const variant = String(candidateValue(candidate, "strategy_variant") || candidate.strategy_config?.strategy_variant || "");
  if (variant.startsWith("swing_")) {
    return {
      sl: `MA${inlineValue(candidateValue(candidate, "trend_exit_below_ma"))}`,
      note: `${inlineValue(candidateValue(candidate, "trend_exit_confirm_bars"))} 根确认`,
    };
  }
  if (variant === "morning_double_strangle") {
    return {
      sl: lossThreshold(candidateValue(candidate, "double_strangle_single_leg_stop_loss_pct")),
      note: `冷却 ${inlineValue(candidateValue(candidate, "strangle_stop_loss_cooldown_minutes"))}m`,
    };
  }
  return {
    sl: lossThreshold(candidateValue(candidate, "strangle_leg_stop_loss_pct")),
    note: `冷却 ${inlineValue(candidateValue(candidate, "strangle_stop_loss_cooldown_minutes"))}m`,
  };
}

function riskSummary(candidate: LabCandidate | undefined, diffRows?: DiffRow[]): string[] {
  if (!candidate) return ["未找到候选参数。"];
  const fields = changedFieldSet(candidate, diffRows);
  const hasAny = (keys: string[]) => keys.some((key) => fields.has(key));
  const get = (key: string) => valueFromDiffOrCandidate(candidate, diffRows, key);
  const lines: string[] = [];
  const variant = String(get("strategy_variant") || "");

  if (variant.startsWith("swing_")) {
    if (hasAny(["strategy.strategy_variant", "strategy.mode", "strategy.min_trend_score", "strategy.trend_fast_ma", "strategy.trend_slow_ma", "strategy.long_ma"])) {
      lines.push(
        `中长线策略：${strategyLabel(variant)}，结构 ${swingModeLabel(get("mode"))}，趋势分 ${inlineValue(get("min_trend_score"))}，MA ${inlineValue(get("trend_fast_ma"))}/${inlineValue(get("trend_slow_ma"))}/${inlineValue(get("long_ma"))}。`
      );
    }
    if (
      hasAny([
        "strategy.min_dte",
        "strategy.target_dte",
        "strategy.max_dte",
        "strategy.fallback_otm_pct",
        "strategy.spread_width_pct",
        "strategy.max_spread_debit",
        "strategy.max_spread_debit_to_width_pct",
        "strategy.spread_min_hold_days_before_stop",
      ])
    ) {
      lines.push(
        `选约口径：DTE ${inlineValue(get("min_dte"))}/${inlineValue(get("target_dte"))}/${inlineValue(get("max_dte"))}，OTM ${ratioPct(get("fallback_otm_pct"), 1)}，价差宽度 ${ratioPct(get("spread_width_pct"), 1)}，净权利金上限 $${inlineValue(get("max_spread_debit"))}，净权利金/价差≤${ratioPct(get("max_spread_debit_to_width_pct"))}，价差止损至少持有 ${inlineValue(get("spread_min_hold_days_before_stop"))} 天。`
      );
    }
    if (hasAny(["strategy.take_profit_pct", "strategy.stop_loss_pct", "strategy.dte_exit_days"])) {
      lines.push(`退出规则：止盈 ${ratioPct(get("take_profit_pct"))}，止损 ${lossThreshold(get("stop_loss_pct"))}，到期前 ${inlineValue(get("dte_exit_days"))} DTE 退出。`);
    }
    if (hasAny(["strategy.trend_exit_below_ma", "strategy.trend_exit_confirm_bars"])) {
      lines.push(`趋势破坏：跌破 MA${inlineValue(get("trend_exit_below_ma"))} 且连续 ${inlineValue(get("trend_exit_confirm_bars"))} 根确认后退出。`);
    }
    if (hasAny(["risk.max_contracts_per_order", "risk.max_open_contracts", "risk.max_premium_per_order", "risk.max_premium_per_symbol", "risk.max_total_option_premium", "risk.max_new_premium_per_day"])) {
      lines.push(
        `预算：单笔 ${inlineValue(get("max_contracts_per_order"))} 张 / $${inlineValue(get("max_premium_per_order"))}，今日新增 $${inlineValue(get("max_new_premium_per_day"))}，单标的 $${inlineValue(get("max_premium_per_symbol"))}，总预算 $${inlineValue(get("max_total_option_premium"))}。`
      );
    }
  }

  if (hasAny(["call_strikes_otm", "put_strikes_otm"])) {
    lines.push(`选约步长：Call ${inlineValue(get("call_strikes_otm"))} OTM，Put ${inlineValue(get("put_strikes_otm"))} OTM；近一步通常成本和 Gamma 更高，远一步通常更依赖较大波动。`);
  }
  if (
    hasAny([
      "double_strangle_call_long_strikes_otm",
      "double_strangle_call_short_strikes_otm",
      "double_strangle_put_long_strikes_otm",
      "double_strangle_put_short_strikes_otm",
    ])
  ) {
    lines.push(
      `双宽跨步长：Call 长/短 ${inlineValue(get("double_strangle_call_long_strikes_otm"))}/${inlineValue(get("double_strangle_call_short_strikes_otm"))}，Put 长/短 ${inlineValue(get("double_strangle_put_long_strikes_otm"))}/${inlineValue(get("double_strangle_put_short_strikes_otm"))}。`
    );
  }
  if (hasAny(["strangle_take_profit_return"])) {
    lines.push(`组合止盈：${ratioPct(get("strangle_take_profit_return"))}；阈值越低越快落袋，也可能错过后续扩大收益。`);
  }
  if (hasAny(["double_strangle_combo_take_profit_pct", "double_strangle_combo_stop_loss_pct"])) {
    lines.push(
      `双宽跨组合风控：止盈 ${ratioPct(get("double_strangle_combo_take_profit_pct"))}，止损 ${lossThreshold(get("double_strangle_combo_stop_loss_pct"))}。`
    );
  }
  if (hasAny(["strangle_long_leg_take_profit_pct", "strangle_short_leg_take_profit_pct"])) {
    lines.push(`单腿止盈：长腿 ${ratioPct(get("strangle_long_leg_take_profit_pct"))}，短腿 ${ratioPct(get("strangle_short_leg_take_profit_pct"))}。`);
  }
  if (
    hasAny([
      "double_strangle_call_long_leg_take_profit_pct",
      "double_strangle_call_short_leg_take_profit_pct",
      "double_strangle_put_long_leg_take_profit_pct",
      "double_strangle_put_short_leg_take_profit_pct",
    ])
  ) {
    lines.push(
      `双宽跨单腿止盈：Call 长/短 ${ratioPct(get("double_strangle_call_long_leg_take_profit_pct"))}/${ratioPct(get("double_strangle_call_short_leg_take_profit_pct"))}，Put 长/短 ${ratioPct(get("double_strangle_put_long_leg_take_profit_pct"))}/${ratioPct(get("double_strangle_put_short_leg_take_profit_pct"))}。`
    );
  }
  if (hasAny(["strangle_stop_loss_return", "strangle_leg_stop_loss_pct", "strangle_stop_loss_cooldown_minutes"])) {
    lines.push(`止损保护：组合 ${lossThreshold(get("strangle_stop_loss_return"))}，单腿 ${lossThreshold(get("strangle_leg_stop_loss_pct"))}，冷却 ${inlineValue(get("strangle_stop_loss_cooldown_minutes"))} 分钟。`);
  }
  if (hasAny(["double_strangle_single_leg_stop_loss_pct"])) {
    lines.push(`双宽跨单腿止损：${lossThreshold(get("double_strangle_single_leg_stop_loss_pct"))}，不区分长腿/短腿。`);
  }
  if (hasAny(["directional_down_pct", "directional_up_pct"])) {
    lines.push(`方向触发：下跌 ${ratioPct(get("directional_down_pct"), 2)} / 上涨 ${ratioPct(get("directional_up_pct"), 2)}。`);
  }
  if (hasAny(["directional_take_profit_return", "directional_stop_loss_pct"])) {
    lines.push(`方向单风控：止盈 ${ratioPct(get("directional_take_profit_return"))}，止损 ${lossThreshold(get("directional_stop_loss_pct"))}。`);
  }
  if (hasAny(["max_trades_per_day", "initial_option_contracts"])) {
    lines.push(`交易频率与尺寸：最多开仓 ${inlineValue(get("max_trades_per_day"))} 次，每次 ${inlineValue(get("initial_option_contracts"))} 张。`);
  }
  if (hasAny(["strangle_entry_start_hhmm_et", "strangle_entry_end_hhmm_et", "strangle_force_close_hhmm_et"])) {
    lines.push(`时间窗口：${inlineValue(get("strangle_entry_start_hhmm_et"))} - ${inlineValue(get("strangle_entry_end_hhmm_et"))} 入场，${inlineValue(get("strangle_force_close_hhmm_et"))} 强平。`);
  }

  const summary = candidate.validation?.summary || {};
  if (Object.keys(summary).length) {
    const totalClosed = candidateTotalClosedTrades(candidate);
    const noTradeWindows = candidateNoTradeWindows(candidate);
    if (totalClosed <= 0 || summary.avg_return_pct === null || summary.avg_return_pct === undefined) {
      const reason = noTradeReasonLabel(summary.primary_no_trade_reason || candidate.validation?.blockers?.[0]);
      lines.push(`验证摘要：存在无交易窗口，主要原因 ${reason}；最大回撤 ${fmt(summary.worst_drawdown_usd)}，最长连亏 ${fmt(summary.max_consecutive_losses, 0)}。`);
    } else {
      const partial = noTradeWindows.length ? `，其中 ${noTradeWindows.map((days) => `${days}天`).join("/")} 无交易` : "";
      lines.push(`验证摘要：平均收益 ${fmt(summary.avg_return_pct)}%，总闭合 ${fmt(totalClosed, 0)} 笔${partial}；最大回撤 ${fmt(summary.worst_drawdown_usd)}，最长连亏 ${fmt(summary.max_consecutive_losses, 0)}。`);
    }
  }
  if (candidate.validation?.blockers?.length) {
    lines.push(`未通过原因：${candidate.validation.blockers.join(" · ")}`);
  }
  return lines.length ? lines : ["本候选没有检测到会改变实盘草稿的关键参数。"];
}

function pipelineStatus(run?: LabRun | null, label?: string): string {
  const rows = run?.pipeline || [];
  const found = rows.find((x) => String(x.label || "") === label || String(x.stage || "") === label);
  return found?.status || "pending";
}

export default function AgentStrategyLabPage() {
  const [instance, setInstance] = useState<LabInstance>("0dte");
  const [strategyVariant, setStrategyVariant] = useState<LabStrategyVariant>("morning_strangle");
  const [candidateGenerator, setCandidateGenerator] = useState<CandidateGenerator>("deterministic");
  const [researchDimension, setResearchDimension] = useState<ResearchDimension>("risk_controls");
  const [candidateCount, setCandidateCount] = useState(3);
  const [selectedWindows, setSelectedWindows] = useState<number[]>([60, 120, 180]);
  const [status, setStatus] = useState<LabStatus | null>(null);
  const [run, setRun] = useState<LabRun | null>(null);
  const [runs, setRuns] = useState<LabRun[]>([]);
  const [selectedRecord, setSelectedRecord] = useState("latest");
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [task, setTask] = useState<LabTask | null>(null);
  const [cacheLoading, setCacheLoading] = useState(false);
  const [cacheResults, setCacheResults] = useState<Array<Record<string, any>>>([]);
  const [approvingId, setApprovingId] = useState("");
  const [diffLoadingId, setDiffLoadingId] = useState("");
  const [revalidatingId, setRevalidatingId] = useState("");
  const [diffPreview, setDiffPreview] = useState<DiffPreview | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [rollbackLoading, setRollbackLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const latestRun = run || status?.last_run || null;
  const dataQuality = latestRun?.data_quality || status?.data_quality || null;
  const checks = dataQuality?.checks || [];
  const candidates = latestRun?.candidates || [];
  const candidateIds = candidates.map((candidate) => String(candidate.candidate_id || "")).filter(Boolean);
  const candidateIdsKey = candidateIds.join("|");
  const latestRunId = String(latestRun?.run_id || "");
  const currentConfig = dataQuality?.current_config || status?.data_quality?.current_config || {};
  const strategyConfig =
    instance === "stock_options_swing"
      ? currentConfig?.strategy && typeof currentConfig.strategy === "object"
        ? currentConfig.strategy
        : {}
      : currentConfig?.strategy_config && typeof currentConfig.strategy_config === "object"
        ? currentConfig.strategy_config
        : {};
  const cacheSymbol = String(currentConfig?.symbol || strategyConfig?.symbol || "QQQ.US").trim().toUpperCase() || "QQQ.US";
  const cacheKline = String(currentConfig?.kline || (instance === "stock_options_swing" ? "1d" : "1m")).trim() || "1m";
  const cacheSymbols =
    instance === "stock_options_swing"
      ? normalizeSymbols([currentConfig?.symbol || "QQQ.US", ...normalizeSymbols(currentConfig?.stock_pool || [])])
      : [cacheSymbol];
  const approvalHistory = status?.approval_history || (status?.last_approval ? [status.last_approval as LabApproval] : []);
  const approvalForCandidate = useCallback(
    (candidateId: string, sameRunOnly = false): LabApproval | null => {
      const cid = String(candidateId || "");
      if (!cid) return null;
      const rows = approvalHistory.filter((item) => String(item.candidate_id || "") === cid);
      if (sameRunOnly) {
        return rows.find((item) => latestRunId && String(item.run_id || "") === latestRunId) || null;
      }
      return rows.find((item) => !latestRunId || String(item.run_id || "") !== latestRunId) || null;
    },
    [approvalHistory, latestRunId]
  );

  const strategyOptions = useMemo(
    () => (instance === "stock_options_swing" ? SWING_STRATEGY_OPTIONS : INTRADAY_STRATEGY_OPTIONS),
    [instance]
  );

  useEffect(() => {
    const valid = strategyOptions.some((item) => item.value === strategyVariant);
    if (!valid) setStrategyVariant(instance === "stock_options_swing" ? "swing_trend_call" : "morning_strangle");
  }, [instance, strategyOptions, strategyVariant]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [statusResp, runsResp] = await Promise.all([
        apiGet<LabStatus>(`/agent-strategy-lab/status?instance=${instance}`, { cacheTtlMs: 0, retries: 0, timeoutMs: 20000 }),
        apiGet<{ ok?: boolean; items?: LabRun[] }>(`/agent-strategy-lab/runs?instance=${instance}&limit=10`, {
          cacheTtlMs: 0,
          retries: 0,
          timeoutMs: 20000,
        }),
      ]);
      setStatus(statusResp);
      setRun(statusResp?.last_run || null);
      setRuns(runsResp?.items || []);
    } catch (e: any) {
      setError(e?.message || "加载失败");
    } finally {
      setLoading(false);
    }
  }, [instance]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!candidateIdsKey) {
      setSelectedCandidateId((prev) => (prev ? "" : prev));
      return;
    }
    const ids = candidateIdsKey.split("|");
    if (!selectedCandidateId || !ids.includes(selectedCandidateId)) setSelectedCandidateId(ids[0] || "");
  }, [candidateIdsKey, selectedCandidateId]);

  useEffect(() => {
    if (!task?.task_id || !["queued", "running"].includes(String(task.status || ""))) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const resp = await apiGet<{ ok?: boolean; task?: LabTask }>(`/agent-strategy-lab/tasks/${task.task_id}`, {
            cacheTtlMs: 0,
            retries: 0,
            timeoutMs: 20000,
          });
          if (cancelled || !resp?.task) return;
          setTask(resp.task);
          if (resp.task.status === "completed") {
            if (resp.task.run) setRun(resp.task.run);
            setRunning(false);
            setMessage("Lab 后台任务完成，候选参数和验证结果已生成。");
            await load();
          } else if (resp.task.status === "failed") {
            setRunning(false);
            setError(resp.task.error || "Lab 后台任务失败");
          }
        } catch (e: any) {
          if (!cancelled) setError(e?.message || "查询 Lab 任务失败");
        }
      })();
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [load, task?.status, task?.task_id]);

  const createRun = useCallback(async () => {
    setRunning(true);
    setTask(null);
    setDiffPreview(null);
    setError("");
    setMessage("");
    try {
      const resp = await apiPost<{ ok?: boolean; async_run?: boolean; task?: LabTask }>(
        "/agent-strategy-lab/tasks",
        {
          instance,
          strategy_variant: strategyVariant,
          candidate_generator: candidateGenerator,
          research_dimension: researchDimension,
          validation_windows_days: selectedWindows.length ? selectedWindows : [60],
          max_candidates: candidateCount,
          kline: cacheKline,
          use_server_kline_cache: true,
          rth_only: true,
        },
        { timeoutMs: 30000, retries: 0 }
      );
      if (!resp?.task?.task_id) throw new Error("Lab task 没有返回 task_id");
      setTask(resp.task);
      setMessage("Lab 后台任务已创建，页面会自动刷新进度。");
    } catch (e: any) {
      setRunning(false);
      setError(e?.message || "运行 Lab 失败");
    }
  }, [cacheKline, candidateCount, candidateGenerator, instance, researchDimension, selectedWindows, strategyVariant]);

  const toggleWindow = useCallback((days: number) => {
    setSelectedWindows((prev) => {
      const has = prev.includes(days);
      const next = has ? prev.filter((x) => x !== days) : [...prev, days];
      return next.length ? next.sort((a, b) => a - b) : [days];
    });
  }, []);

  const downloadMissingKlineCache = useCallback(async () => {
    setCacheLoading(true);
    setError("");
    setMessage("");
    setCacheResults([]);
    const rows: Array<Record<string, any>> = [];
    try {
      const windows = instance === "stock_options_swing" ? Array.from(new Set([...VALIDATION_WINDOWS_DAYS, 260])) : VALIDATION_WINDOWS_DAYS;
      for (const symbol of cacheSymbols) {
        for (const days of windows) {
          try {
            const resp = await apiPost<Record<string, any>>(
              "/backtest/kline-cache/fetch",
              {
                symbol,
                periods: 0,
                days,
                kline: instance === "stock_options_swing" ? "1d" : cacheKline,
                force_refresh: false,
                source: "auto",
              },
              { timeoutMs: 600000, retries: 0 }
            );
            rows.push({ symbol, days, ...resp });
          } catch (e: any) {
            rows.push({ symbol, days, ok: false, error: e?.message || "下载失败" });
          }
          setCacheResults([...rows]);
        }
      }
      const totalBars = rows.reduce((sum, row) => sum + Number(row.bar_count || 0), 0);
      const failed = rows.filter((row) => row.ok === false).length;
      setMessage(
        instance === "stock_options_swing"
          ? `股票池K线缓存检查完成：${cacheSymbols.length} 个股票 × ${windows.length} 个窗口，共 ${fmt(totalBars, 0)} 根${failed ? `，失败 ${failed} 项` : ""}。`
          : `K线缓存检查完成：${cacheSymbol} ${cacheKline}，${rows.length} 个窗口，共 ${fmt(totalBars, 0)} 根。`
      );
      await load();
    } catch (e: any) {
      setError(e?.message || "下载K线缓存失败");
      if (rows.length) setCacheResults(rows);
    } finally {
      setCacheLoading(false);
    }
  }, [cacheKline, cacheSymbol, cacheSymbols, instance, load]);

  const approve = useCallback(
    async (candidateId: string, force = false) => {
      if (!latestRun?.run_id || !candidateId) return;
      setApprovingId(candidateId);
      setError("");
      setMessage("");
      try {
        await apiPost(`/agent-strategy-lab/runs/${latestRun.run_id}/approve`, {
          candidate_id: candidateId,
          force,
        });
        setDiffPreview(null);
        setMessage("已写入 live_worker_config 草稿；不会启动 worker，也不会下单。");
        await load();
      } catch (e: any) {
        setError(e?.message || "审批写入失败");
      } finally {
        setApprovingId("");
      }
    },
    [latestRun?.run_id, load]
  );

  const previewDiff = useCallback(
    async (candidateId: string, force = false) => {
      if (!latestRun?.run_id || !candidateId) return;
      setDiffLoadingId(candidateId);
      setError("");
      setMessage("");
      try {
        const resp = await apiGet<DiffPreview>(
          `/agent-strategy-lab/runs/${encodeURIComponent(latestRun.run_id)}/candidates/${encodeURIComponent(candidateId)}/diff`,
          { cacheTtlMs: 0, retries: 0, timeoutMs: 20000 }
        );
        setDiffPreview({ ...resp, force });
      } catch (e: any) {
        setError(e?.message || "读取审批差异失败");
      } finally {
        setDiffLoadingId("");
      }
    },
    [latestRun?.run_id]
  );

  const revalidateCandidate = useCallback(
    async (candidateId: string) => {
      if (!latestRun?.run_id || !candidateId) return;
      setRunning(true);
      setRevalidatingId(candidateId);
      setTask(null);
      setDiffPreview(null);
      setError("");
      setMessage("");
      try {
        const resp = await apiPost<{ ok?: boolean; async_run?: boolean; task?: LabTask }>(
          `/agent-strategy-lab/runs/${encodeURIComponent(latestRun.run_id)}/candidates/${encodeURIComponent(candidateId)}/revalidate`,
          {
            instance,
            validation_windows_days: selectedWindows.length ? selectedWindows : [60, 120, 180],
            kline: "1m",
            use_server_kline_cache: true,
            rth_only: true,
          },
          { timeoutMs: 30000, retries: 0 }
        );
        if (!resp?.task?.task_id) throw new Error("重新验证任务没有返回 task_id");
        setTask(resp.task);
        setMessage("已创建候选重新验证任务：复用该候选原始参数，只重新跑回测。");
      } catch (e: any) {
        setRunning(false);
        setError(e?.message || "重新验证候选失败");
      } finally {
        setRevalidatingId("");
      }
    },
    [instance, latestRun?.run_id, selectedWindows]
  );

  const rollbackLastApproval = useCallback(async () => {
    const latestApproval = approvalHistory[0];
    if (!latestApproval?.approval_id) return;
    setRollbackLoading(true);
    setError("");
    setMessage("");
    try {
      await apiPost("/agent-strategy-lab/approvals/rollback", {
        instance,
        approval_id: latestApproval.approval_id,
      });
      setMessage("已回滚到上一次审批前配置；不会启动 worker，也不会下单。");
      await load();
    } catch (e: any) {
      setError(e?.message || "回滚审批失败");
    } finally {
      setRollbackLoading(false);
    }
  }, [approvalHistory, instance, load]);

  const summaryCards = useMemo(() => {
    const s = dataQuality?.summary || {};
    return [
      { label: "数据状态", value: dataQuality?.ok ? "可研究" : "需检查", tone: dataQuality?.ok ? "ok" : "warn" },
      { label: "检查项", value: `${fmt(s.checks_total, 0)} 项`, tone: "info" },
      { label: "警告", value: fmt(s.warnings, 0), tone: Number(s.warnings || 0) ? "warn" : "ok" },
      { label: "最新日志", value: s.latest_decision_age_minutes == null ? "-" : `${fmt(s.latest_decision_age_minutes)} 分钟前`, tone: "info" },
    ];
  }, [dataQuality]);

  const activeStrategy = STRATEGY_OPTIONS.find((x) => x.value === strategyVariant);
  const activeGenerator = GENERATOR_OPTIONS.find((x) => x.value === candidateGenerator);
  const activeResearchDimension = researchDimensionDisplay(researchDimension, instance);
  const passedCount = candidates.filter((candidate) => Boolean(candidate.validation?.passed)).length;
  const blockedCount = Math.max(0, candidates.length - passedCount);
  const diffCandidate = diffPreview
    ? candidates.find((candidate) => String(candidate.candidate_id || "") === String(diffPreview.candidate_id || ""))
    : undefined;
  const selectedCandidate =
    candidates.find((candidate) => String(candidate.candidate_id || "") === selectedCandidateId) || candidates[0] || null;
  const latestApproval = approvalHistory[0] || null;
  const selectedApproval = selectedRecord.startsWith("approval:")
    ? approvalHistory.find((item) => `approval:${item.approval_id}` === selectedRecord) || latestApproval
    : latestApproval;
  const currentRunApproval = approvalHistory.find((item) => latestRunId && String(item.run_id || "") === latestRunId) || null;
  const progressPct = Math.max(0, Math.min(100, Number(task?.progress_pct || 0)));
  const selectedWindowsLabel = selectedWindows.join(" / ");

  const selectRecord = useCallback(
    (value: string) => {
      setSelectedRecord(value);
      if (value.startsWith("run:")) {
        const runId = value.slice("run:".length);
        const found = runs.find((item) => String(item.run_id || "") === runId);
        if (found) setRun(found);
      }
    },
    [runs]
  );

  return (
    <PageShell>
      <div className="space-y-4">
        <div className="page-header items-start">
          <div>
            <h1 className="page-title">Agent Strategy Lab</h1>
            <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-400">
              生成候选参数、跑回测验证、审批写入配置草稿；实盘 worker 和券商下单不在这里触发。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className={`rounded-full border px-3 py-1 ${toneClass(dataQuality?.ok ? "ok" : "warn")}`}>
              数据 {dataQuality?.ok ? "可用" : "待检查"}
            </span>
            <span className="tag-muted">
              {passedCount}/{candidates.length || 0} 通过
            </span>
            <span className="tag-muted">3010 页面</span>
          </div>
        </div>

        <section className="panel">
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="section-title">研究配置</div>
                <p className="mt-1 text-xs text-slate-500">只保留影响本次 Lab 运行的选项。</p>
              </div>
              <span className="tag-muted">
                {activeResearchDimension.label} · {candidateCount} 候选 × {selectedWindows.length || 1} 窗口
              </span>
            </div>

            <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-[1fr_1.1fr_1.1fr_1.15fr_0.9fr]">
              <select className="input-base" value={instance} onChange={(e) => setInstance(e.target.value as LabInstance)} aria-label="实例">
                {INSTANCE_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
              <select className="input-base" value={strategyVariant} onChange={(e) => setStrategyVariant(e.target.value as LabStrategyVariant)} aria-label="策略">
                {strategyOptions.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
              <select className="input-base" value={researchDimension} onChange={(e) => setResearchDimension(e.target.value as ResearchDimension)} aria-label="研究维度">
                {RESEARCH_DIMENSION_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {researchDimensionDisplay(item.value, instance).label}
                  </option>
                ))}
              </select>
              <select className="input-base" value={candidateGenerator} onChange={(e) => setCandidateGenerator(e.target.value as CandidateGenerator)} aria-label="候选来源">
                {GENERATOR_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
              <select
                className="input-base"
                value={candidateCount}
                onChange={(e) => setCandidateCount(Math.max(1, Math.min(3, Math.floor(Number(e.target.value) || 1))))}
                aria-label="候选数量"
              >
                <option value={1}>1 个候选</option>
                <option value={2}>2 个候选</option>
                <option value={3}>3 个候选</option>
              </select>
            </div>
            <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 px-3 py-2 text-xs leading-5 text-slate-400">
              <span className="font-semibold text-slate-300">研究维度：</span>
              {activeResearchDimension?.description || "验证步长、止盈止损，不主动改时间"}
            </div>

            {instance === "stock_options_swing" ? (
              <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-xs font-semibold text-slate-200">生成时点建议</div>
                  <span className="rounded-full border border-cyan-300/30 bg-cyan-400/10 px-2 py-0.5 text-[11px] text-cyan-100">
                    中长线 · 收盘后优先
                  </span>
                </div>
                <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
                  <div className="rounded-md border border-slate-800 bg-slate-950/45 p-3">
                    <div className="text-xs font-semibold text-cyan-100">正式生成</div>
                    <div className="mt-1 text-xs leading-5 text-slate-400">
                      优先在美股收盘后 30-90 分钟运行；日线、成交量和趋势信号更完整。
                    </div>
                  </div>
                  <div className="rounded-md border border-slate-800 bg-slate-950/45 p-3">
                    <div className="text-xs font-semibold text-slate-300">次日盘前</div>
                    <div className="mt-1 text-xs leading-5 text-slate-400">
                      适合复核昨日候选、补 K 线、检查期权链和托管持仓，不建议频繁改参数。
                    </div>
                  </div>
                  <div className="rounded-md border border-amber-300/25 bg-amber-300/10 p-3">
                    <div className="text-xs font-semibold text-amber-100">盘中谨慎</div>
                    <div className="mt-1 text-xs leading-5 text-amber-100/75">
                      盘中可重新验证，但不要只因短时波动审批中长线配置；切换账户或股票池后先刷新持仓。
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
                  <div className="text-xs font-semibold text-slate-300">盘前</div>
                  <div className="mt-1 text-xs leading-5 text-slate-500">
                    只做 K 线缓存、数据质量、配置检查；可用“用此候选重新验证”复核历史候选。
                  </div>
                </div>
                <div className="rounded-lg border border-cyan-300/30 bg-cyan-400/10 p-3">
                  <div className="text-xs font-semibold text-cyan-100">正式生成候选</div>
                  <div className="mt-1 text-xs leading-5 text-cyan-100/75">
                    建议美东 09:45-10:00，北京时间 21:45-22:00。
                  </div>
                </div>
                <div className="rounded-lg border border-amber-300/30 bg-amber-300/10 p-3">
                  <div className="text-xs font-semibold text-amber-100">二次确认</div>
                  <div className="mt-1 text-xs leading-5 text-amber-100/75">
                    行情分歧或首次未通过时，美东 10:05-10:15 再跑一次。
                  </div>
                </div>
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-700/70 bg-slate-950/35 px-3 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-semibold text-slate-400">回测窗口</span>
                {VALIDATION_WINDOWS_DAYS.map((days) => (
                  <label
                    key={days}
                    className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-xs ${
                      selectedWindows.includes(days)
                        ? "border-cyan-300/40 bg-cyan-400/10 text-cyan-100"
                        : "border-slate-700 bg-slate-950/35 text-slate-300"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 rounded border-slate-600"
                      checked={selectedWindows.includes(days)}
                      onChange={() => toggleWindow(days)}
                      disabled={running}
                    />
                    {days}天
                  </label>
                ))}
                <button type="button" className="btn-secondary text-xs" onClick={() => setSelectedWindows([60])} disabled={running}>
                  快速
                </button>
                <button type="button" className="btn-secondary text-xs" onClick={() => setSelectedWindows([60, 120, 180])} disabled={running}>
                  完整
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="hidden text-xs text-slate-500 md:inline">
                  {activeStrategy?.label || "-"} · {activeResearchDimension?.label || "-"} · {activeGenerator?.label || "-"} · {selectedWindowsLabel || "-"}天
                </span>
                <button type="button" className="btn-secondary" onClick={() => void load()} disabled={loading || running}>
                  {loading ? "刷新中..." : "刷新"}
                </button>
                <button type="button" className="btn-secondary" onClick={() => void downloadMissingKlineCache()} disabled={loading || running || cacheLoading}>
                  {cacheLoading ? "下载中..." : "补K线"}
                </button>
                <button type="button" className="btn-primary" onClick={() => void createRun()} disabled={running}>
                  {running ? "验证中..." : "生成候选并验证"}
                </button>
              </div>
            </div>
          </div>
        </section>

        {error ? <div className="rounded-lg border border-rose-400/35 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">{error}</div> : null}
        {message ? <div className="rounded-lg border border-emerald-400/35 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-100">{message}</div> : null}
        {instance === "stock_options_swing" ? (
          <div className="rounded-lg border border-amber-300/35 bg-amber-300/10 px-4 py-3 text-sm text-amber-100">
            股票期权中长线会做粗略验证：用股票日线模拟入场信号，并用历史波动率近似 IV 的理论期权路径估算收益；这仍不是真实期权历史成交回测。审批需要人工确认，且不会启动 worker 或下单。
          </div>
        ) : null}

        {cacheResults.length ? (
          <div className="rounded-lg border border-cyan-400/25 bg-cyan-500/10 px-4 py-3 text-xs text-cyan-100">
            <div className="font-semibold">
              服务器 K 线缓存：{instance === "stock_options_swing" ? `${cacheSymbols.length} 个股票 · 1d` : `${cacheSymbol} · ${cacheKline}`}
            </div>
            <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-3 xl:grid-cols-4">
              {cacheResults.map((row) => (
                <div key={`${String(row.symbol || cacheSymbol)}-${String(row.days)}`} className={`rounded-md border p-2 ${row.ok === false ? "border-rose-300/30 bg-rose-500/10 text-rose-100" : "border-cyan-300/20 bg-slate-950/35"}`}>
                  <div>
                    <span className="font-mono">{String(row.symbol || cacheSymbol)}</span> · {row.days} 天 · {row.ok === false ? "失败" : row.cached ? "已有缓存" : "已下载"}
                  </div>
                  <div className="mt-1 text-cyan-100/75">{row.ok === false ? String(row.error || "-") : `${fmt(row.bar_count, 0)} 根`}</div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <section className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          <div className="panel">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="section-title">运行状态</div>
                <p className="mt-1 text-xs text-slate-500">异步任务进度和最近一次 Lab 结果。</p>
              </div>
              <span className={`rounded-full border px-2 py-1 text-xs ${toneClass(task?.status || latestRun?.status || "pending")}`}>
                {task?.status || latestRun?.status || "未运行"}
              </span>
            </div>
            <div className="mt-4">
              <div className="flex items-end justify-between gap-3">
                <div>
                  <div className="text-2xl font-semibold text-slate-100">{fmt(progressPct, 0)}%</div>
                  <div className="mt-1 text-xs text-slate-500">{task?.progress_stage || task?.progress_text || latestRun?.run_id || "等待创建任务"}</div>
                </div>
                <div className="text-right text-xs text-slate-500">
                  <div>通过 {passedCount}</div>
                  <div>阻断 {blockedCount}</div>
                </div>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-950/70">
                <div className="h-full rounded-full bg-cyan-300 transition-all" style={{ width: `${progressPct}%` }} />
              </div>
              {task?.task_id ? <div className="mt-3 break-all font-mono text-xs text-slate-500">{task.task_id}</div> : null}
            </div>
          </div>

          <div className="panel">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="section-title">数据质量</div>
                <p className="mt-1 text-xs text-slate-500">日志、ledger、推荐快照与配置完整性。</p>
              </div>
              <span className={`rounded-full border px-2 py-1 text-xs ${toneClass(dataQuality?.ok ? "ok" : "warn")}`}>
                {dataQuality?.ok ? "可进入研究" : "需检查"}
              </span>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2">
              {summaryCards.map((item) => (
                <div key={item.label} className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
                  <div className="text-xs text-slate-500">{item.label}</div>
                  <div className="mt-1 text-lg font-semibold text-slate-100">{item.value}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="section-title">审批状态</div>
                <p className="mt-1 text-xs text-slate-500">只写入草稿，不启动 worker。</p>
              </div>
              <span className={`rounded-full border px-2 py-1 text-xs ${toneClass(currentRunApproval ? "ok" : latestApproval ? "warn" : "pending")}`}>
                {currentRunApproval ? "本次已审批" : latestApproval ? "仅有历史审批" : "待审批"}
              </span>
            </div>
            {latestApproval ? (
              <div className="mt-4 space-y-3">
                <div className="rounded-lg border border-emerald-400/25 bg-emerald-500/10 p-3">
                  <div className="text-xs text-emerald-100/70">{currentRunApproval ? "本次审批候选" : "最近历史审批候选"}</div>
                  <div className="mt-1 break-all font-mono text-sm text-emerald-100">{latestApproval.candidate_id || "-"}</div>
                  {latestRunId && String(latestApproval.run_id || "") !== latestRunId ? (
                    <div className="mt-1 text-xs text-amber-100/80">来自历史运行：{String(latestApproval.run_id || "-")}</div>
                  ) : null}
                  <div className="mt-1 text-xs text-emerald-100/70">{latestApproval.diff?.length || 0} 个字段变化</div>
                </div>
                <button
                  type="button"
                  className="w-full rounded-xl border border-amber-300/40 bg-amber-300/10 px-3 py-2 text-xs font-semibold text-amber-100 disabled:opacity-50"
                  disabled={rollbackLoading}
                  onClick={() => void rollbackLastApproval()}
                >
                  {rollbackLoading ? "回滚中..." : "回滚上一版"}
                </button>
              </div>
            ) : (
              <div className="mt-4 rounded-lg border border-slate-700/70 bg-slate-950/35 p-3 text-sm text-slate-400">
                先选择一个验证通过的候选，再查看 diff 并确认写入。
              </div>
            )}
          </div>
        </section>

        <section className="panel">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="section-title">安全管线</div>
              <p className="mt-1 text-xs text-slate-500">前四步在 Lab 内完成；worker 与券商 API 始终保持 not_touched。</p>
            </div>
            <span className="tag-muted">
              {instanceLabel(instance)} · {activeStrategy?.label || "-"} · {activeResearchDimension?.label || "-"} · {activeGenerator?.label || "-"}
            </span>
          </div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-6">
            {PIPELINE.map((label, idx) => {
              const statusText =
                label === "智能体研究层"
                  ? pipelineStatus(latestRun, "智能体研究层")
                  : label === "回测与验证层"
                    ? pipelineStatus(latestRun, "确定性回测层")
                    : label === "人工确认 / 自动审批闸门"
                      ? pipelineStatus(latestRun, "人工确认 / 自动审批闸门")
                      : label === "QQQ 实盘 worker"
                        ? pipelineStatus(latestRun, "QQQ 实盘 worker")
                        : label === "券商 API 下单"
                          ? "not_touched"
                          : currentRunApproval
                            ? "draft_written"
                            : "pending";
              return (
                <div key={label} className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
                  <div className="text-[11px] text-slate-500">Step {idx + 1}</div>
                  <div className="mt-1 min-h-10 text-sm font-semibold text-slate-100">{label}</div>
                  <div className={`mt-3 inline-flex rounded-full border px-2 py-0.5 text-[11px] ${toneClass(statusText)}`}>{statusText}</div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="grid grid-cols-1 gap-4 xl:grid-cols-[0.95fr_1.05fr]">
          <div className="panel">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <div className="section-title">数据质量明细</div>
                <p className="mt-1 text-xs text-slate-500">只显示真正需要你判断的输入健康状况。</p>
              </div>
              <span className="tag-muted">{checks.length} checks</span>
            </div>
            <div className="max-h-[24rem] space-y-2 overflow-auto pr-1">
              {checks.length ? (
                checks.map((check) => (
                  <div key={check.id || check.title} className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-semibold text-slate-100">{check.title || check.id}</div>
                      <span className={`rounded-full border px-2 py-0.5 text-[11px] ${toneClass(check.severity)}`}>{check.severity || "info"}</span>
                    </div>
                    <div className="mt-1 text-xs leading-5 text-slate-400">{check.detail || "-"}</div>
                  </div>
                ))
              ) : (
                <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3 text-sm text-slate-400">暂无数据质量结果。</div>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <div className="section-title">运行与审批记录</div>
                <p className="mt-1 text-xs text-slate-500">用下拉选择历史记录，下面显示中文摘要。</p>
              </div>
              <span className="tag-muted">{runs.length} 次运行 / {approvalHistory.length} 次审批</span>
            </div>

            <select className="input-base" value={selectedRecord} onChange={(event) => selectRecord(event.target.value)} aria-label="选择运行或审批记录">
              <option value="latest">最近运行：{runSummary(latestRun)}</option>
              {runs.map((item) => (
                <option key={`run:${item.run_id}`} value={`run:${item.run_id}`}>
                  运行：{runSummary(item)}
                </option>
              ))}
              {approvalHistory.map((item) => (
                <option key={`approval:${item.approval_id}`} value={`approval:${item.approval_id}`}>
                  审批：{approvalSummary(item)}
                </option>
              ))}
            </select>

            <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
              <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
                <div className="text-xs font-semibold text-slate-400">运行摘要</div>
                <div className="mt-2 text-sm font-semibold text-slate-100">{runSummary(latestRun)}</div>
                <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
                  <div>
                    <div className="text-slate-500">状态</div>
                    <div className="mt-1 text-slate-200">{statusLabel(latestRun?.status)}</div>
                  </div>
                  <div>
                    <div className="text-slate-500">候选</div>
                    <div className="mt-1 text-slate-200">{candidates.length || 0}</div>
                  </div>
                  <div>
                    <div className="text-slate-500">通过</div>
                    <div className="mt-1 text-slate-200">{passedCount}</div>
                  </div>
                </div>
                <div className="mt-2 break-all font-mono text-[11px] text-slate-500">{latestRun?.run_id || "暂无 run_id"}</div>
              </div>

              <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
                <div className="text-xs font-semibold text-slate-400">审批摘要</div>
                <div className="mt-2 text-sm font-semibold text-slate-100">{approvalSummary(selectedApproval)}</div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                  <div>
                    <div className="text-slate-500">字段变化</div>
                    <div className="mt-1 text-slate-200">{selectedApproval?.diff?.length || 0}</div>
                  </div>
                  <div>
                    <div className="text-slate-500">写入目标</div>
                    <div className="mt-1 truncate text-slate-200">{selectedApproval?.live_config_path ? "live_worker_config" : "-"}</div>
                  </div>
                </div>
                <div className="mt-2 break-all font-mono text-[11px] text-slate-500">{selectedApproval?.approval_id || "暂无 approval_id"}</div>
              </div>
            </div>
          </div>
        </section>

      <section className="panel">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="section-title">候选参数与验证结果</div>
            <p className="mt-1 text-xs text-slate-500">审批只写入配置草稿；L3 下单权限、confirmation token 和 worker 风控继续由实盘模块控制。</p>
          </div>
          <span className="tag-muted">{candidates.length} candidates</span>
        </div>

        {diffPreview ? (
          <div className="mb-4 rounded-lg border border-cyan-400/30 bg-cyan-500/10 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-cyan-100">审批前差异对比</div>
                <div className="mt-1 text-xs text-cyan-100/75">
                  仅展示候选实际改动字段 · {diffPreview.candidate_id || "-"} · {diffPreview.live_config_path || "live_worker_config.json"}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn-primary text-xs"
                  disabled={Boolean(approvingId)}
                  onClick={() => void approve(String(diffPreview.candidate_id || ""), Boolean(diffPreview.force))}
                >
                  {approvingId ? "写入中..." : diffPreview.force ? "确认强制写入" : "确认写入草稿"}
                </button>
                <button type="button" className="btn-secondary text-xs" disabled={Boolean(approvingId)} onClick={() => setDiffPreview(null)}>
                  取消
                </button>
              </div>
            </div>
            <div className="mt-3 rounded-lg border border-cyan-300/20 bg-slate-950/40 p-3">
              <div className="text-xs font-semibold text-cyan-100">审批前风险摘要</div>
              <ul className="mt-2 space-y-1 text-xs leading-5 text-cyan-100/80">
                {riskSummary(diffCandidate, diffPreview.diff).map((line, idx) => (
                  <li key={`diff-risk-${idx}`}>{line}</li>
                ))}
              </ul>
            </div>
            <div className="mt-3 table-shell rounded-lg">
              <table className="min-w-full text-left text-xs">
                <thead className="table-head">
                  <tr>
                    <th className="px-2 py-1.5">字段</th>
                    <th className="px-2 py-1.5">含义</th>
                    <th className="px-2 py-1.5">当前</th>
                    <th className="px-2 py-1.5">写入后</th>
                  </tr>
                </thead>
                <tbody>
                  {diffPreview.diff?.length ? (
                    diffPreview.diff.map((row) => (
                      <tr key={row.field || `${row.before}-${row.after}`} className="border-t border-slate-800">
                        <td className="px-2 py-1.5 font-mono text-cyan-100">{row.field || "-"}</td>
                        <td className="px-2 py-1.5 text-slate-300">{fieldLabel(row.field)}</td>
                        <td className="px-2 py-1.5 font-mono text-slate-300">{inlineValue(row.before)}</td>
                        <td className="px-2 py-1.5 font-mono text-slate-100">{inlineValue(row.after)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td className="px-2 py-3 text-slate-400" colSpan={4}>
                        没有检测到候选 patch 字段变化。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        {candidates.length ? (
          <div className="mb-4 rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-semibold text-slate-100">候选入口</div>
                <div className="mt-1 text-xs text-slate-500">
                  点击一个候选后，下方只展示该候选的完整详情、诊断和 diff 入口。
                </div>
              </div>
              <span className="tag-muted">不会触发下单</span>
            </div>
            <div className="grid gap-3 lg:grid-cols-3">
              {candidates.map((candidate) => {
                const cid = String(candidate.candidate_id || "");
                const passed = Boolean(candidate.validation?.passed);
                const selected = String(selectedCandidate?.candidate_id || "") === cid;
                const currentApproval = approvalForCandidate(cid, true);
                const historicalApproval = !currentApproval ? approvalForCandidate(cid, false) : null;
                const comboText = candidateComboText(candidate);
                const legTpText = candidateLegTpText(candidate);
                const legSlText = candidateLegSlText(candidate);
                const summary = candidate.validation?.summary || {};
                return (
                  <div
                    key={`${cid}-entry`}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedCandidateId(cid)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelectedCandidateId(cid);
                      }
                    }}
                    className={`min-w-0 cursor-pointer rounded-lg border p-4 transition ${
                      selected
                        ? "border-cyan-300/70 bg-cyan-400/10 shadow-[0_0_0_1px_rgba(103,232,249,0.22)]"
                        : "border-slate-700 bg-slate-950/45 hover:border-cyan-400/45 hover:bg-slate-950/70"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-base font-semibold text-slate-100">{candidate.title || cid}</div>
                        <div className="mt-1 truncate font-mono text-[10px] text-slate-500">{cid}</div>
                      </div>
                      <span className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${toneClass(passed ? "passed" : "blocked")}`}>
                        {passed ? "通过" : "未通过"}
                      </span>
                    </div>

                    <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                      <div className="rounded-md border border-slate-800 bg-slate-950/45 p-2">
                        <div className="text-slate-500">{instance === "stock_options_swing" ? "DTE" : "步长"}</div>
                        <div className="mt-1 font-mono text-slate-200">{candidateStepText(candidate)}</div>
                      </div>
                      <div className="rounded-md border border-slate-800 bg-slate-950/45 p-2">
                        <div className="text-slate-500">TP / SL</div>
                        <div className="mt-1 text-slate-200">{comboText.tp} / {comboText.sl}</div>
                      </div>
                      <div className="rounded-md border border-slate-800 bg-slate-950/45 p-2">
                        <div className="text-slate-500">{instance === "stock_options_swing" ? "结构" : "单腿 TP"}</div>
                        <div className="mt-1 text-slate-200">{legTpText[0]}</div>
                        <div className="mt-1 text-slate-500">{legTpText[1]}</div>
                      </div>
                      <div className="rounded-md border border-slate-800 bg-slate-950/45 p-2">
                        <div className="text-slate-500">{instance === "stock_options_swing" ? "回撤 / 连亏" : "单腿 SL"}</div>
                        <div className="mt-1 text-slate-200">
                          {instance === "stock_options_swing" ? fmt(summary.worst_drawdown_usd) : legSlText.sl}
                        </div>
                        <div className="mt-1 text-slate-500">
                          {instance === "stock_options_swing" ? `连亏 ${fmt(summary.max_consecutive_losses, 0)}` : legSlText.note}
                        </div>
                      </div>
                    </div>

                    <div className="mt-3 grid grid-cols-3 gap-2 text-[11px] text-slate-300">
                      {[60, 120, 180].map((days) => (
                        <div key={`${cid}-entry-${days}`} className="rounded-md border border-slate-800 bg-slate-950/35 p-2">
                          <div className="text-slate-500">{days}天</div>
                          <div className="mt-1">{validationWindowReturnText(candidate, days)}</div>
                        </div>
                      ))}
                    </div>

                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {selected ? <span className="rounded-full border border-cyan-300/35 bg-cyan-400/10 px-2 py-1 text-xs text-cyan-100">当前详情</span> : null}
                      {currentApproval ? <span className="rounded-full border border-emerald-400/35 bg-emerald-400/10 px-2 py-1 text-xs text-emerald-100">本次已审批</span> : null}
                      {historicalApproval ? <span className="rounded-full border border-amber-300/35 bg-amber-300/10 px-2 py-1 text-xs text-amber-100">历史审批</span> : null}
                    </div>

                    <div className="mt-4 flex flex-wrap gap-2">
                      <button
                        type="button"
                        className="btn-secondary text-xs"
                        onClick={(event) => {
                          event.stopPropagation();
                          setSelectedCandidateId(cid);
                        }}
                      >
                        查看详情
                      </button>
                      <button
                        type="button"
                        className="btn-secondary text-xs"
                        disabled={running || !passed || Boolean(approvingId) || Boolean(diffLoadingId)}
                        onClick={(event) => {
                          event.stopPropagation();
                          void previewDiff(cid, false);
                        }}
                      >
                        看摘要与 diff
                      </button>
                      <button
                        type="button"
                        className="rounded-xl border border-cyan-300/35 bg-cyan-400/10 px-3 py-2 text-xs font-semibold text-cyan-100 disabled:opacity-50"
                        disabled={running || Boolean(revalidatingId)}
                        onClick={(event) => {
                          event.stopPropagation();
                          void revalidateCandidate(cid);
                        }}
                      >
                        {revalidatingId === cid ? "重新验证中..." : "重新验证"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}

        <div className="grid gap-4">
          {selectedCandidate ? (
            [selectedCandidate].map((candidate) => {
              const validation = candidate.validation || {};
              const passed = Boolean(validation.passed);
              const cid = String(candidate.candidate_id || "");
              const currentApproval = approvalForCandidate(cid, true);
              const historicalApproval = !currentApproval ? approvalForCandidate(cid, false) : null;
              const summary = validation.summary || {};
              const totalClosedTrades = candidateTotalClosedTrades(candidate);
              const noTradeWindows = candidateNoTradeWindows(candidate);
              const aggregateSuggestions = suggestedAdjustments(summary.suggested_adjustments);
              const hasSwingDiagnostics =
                instance === "stock_options_swing" &&
                (aggregateSuggestions.length > 0 ||
                  Boolean(summary.primary_no_trade_reason) ||
                  Boolean((validation.rows || []).some((row) => row.metrics?.diagnostics || row.metrics?.symbols_no_trade?.length || row.metrics?.symbols_skipped?.length)));
              return (
                <div key={cid} className="min-w-0 rounded-lg border border-slate-700/70 bg-slate-950/35 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-base font-semibold text-slate-100">{candidate.title || cid}</h2>
                        <span className={`rounded-full border px-2 py-0.5 text-xs ${toneClass(passed ? "passed" : "blocked")}`}>
                          {passed ? "验证通过" : "验证未通过"}
                        </span>
                        <span className="rounded-full border border-slate-600 bg-slate-800/70 px-2 py-0.5 text-xs text-slate-300">
                          {actionLabel(candidate.agent_action)}
                        </span>
                        {candidate.generator ? (
                          <span className="rounded-full border border-cyan-400/30 bg-cyan-400/10 px-2 py-0.5 text-xs text-cyan-100">
                            {candidate.generator === "tradingagents" ? "TradingAgents" : "规则生成"}
                          </span>
                        ) : null}
                        {currentApproval ? (
                          <span className="rounded-full border border-emerald-400/35 bg-emerald-400/10 px-2 py-0.5 text-xs text-emerald-100">
                            本次已审批
                          </span>
                        ) : null}
                        {historicalApproval ? (
                          <span className="rounded-full border border-amber-300/35 bg-amber-300/10 px-2 py-0.5 text-xs text-amber-100">
                            历史审批
                          </span>
                        ) : null}
                      </div>
                      <div className="mt-1 text-xs text-slate-500">confidence {fmt(Number(candidate.confidence || 0) * 100, 0)}%</div>
                    </div>
                    <div className="flex w-full flex-wrap gap-2 sm:w-auto">
                      <button
                        type="button"
                        className="btn-secondary text-xs"
                        disabled={running || !passed || Boolean(approvingId) || Boolean(diffLoadingId)}
                        onClick={() => void previewDiff(cid, false)}
                      >
                        {diffLoadingId === cid ? "读取差异..." : "审批写入草稿"}
                      </button>
                      <button
                        type="button"
                        className="rounded-xl border border-cyan-300/35 bg-cyan-400/10 px-3 py-2 text-xs font-semibold text-cyan-100 disabled:opacity-50"
                        disabled={running || Boolean(revalidatingId)}
                        onClick={() => void revalidateCandidate(cid)}
                      >
                        {revalidatingId === cid ? "重新验证中..." : "用此候选重新验证"}
                      </button>
                      {!passed ? (
                        <button
                          type="button"
                          className="rounded-xl border border-amber-300/40 bg-amber-300/10 px-3 py-2 text-xs font-semibold text-amber-100 disabled:opacity-50"
                          disabled={Boolean(approvingId) || Boolean(diffLoadingId)}
                          onClick={() => void previewDiff(cid, true)}
                        >
                          强制写入
                        </button>
                      ) : null}
                    </div>
                  </div>

                  <div className="mt-4 grid grid-cols-2 gap-2">
                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="text-xs text-slate-500">平均收益</div>
                      <div className="mt-1 text-lg font-semibold text-slate-100">
                        {candidateAvgReturnText(candidate)}
                      </div>
                      {String(summary.mode || "") === "approx_option_backtest" ? <div className="mt-1 text-[10px] text-amber-200">粗略模型</div> : null}
                      {totalClosedTrades > 0 && noTradeWindows.length ? (
                        <div className="mt-1 text-[10px] text-amber-200">
                          部分窗口无交易：{noTradeWindows.map((days) => `${days}天`).join("、")}
                        </div>
                      ) : null}
                      {totalClosedTrades <= 0 && summary.primary_no_trade_reason ? (
                        <div className="mt-1 text-[10px] text-amber-200">{noTradeReasonLabel(summary.primary_no_trade_reason)}</div>
                      ) : null}
                    </div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="text-xs text-slate-500">最少 / 总平仓</div>
                      <div className="mt-1 text-lg font-semibold text-slate-100">{fmt(summary.min_closed_trades, 0)} / {fmt(totalClosedTrades, 0)}</div>
                    </div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="text-xs text-slate-500">最大回撤</div>
                      <div className="mt-1 text-lg font-semibold text-slate-100">{fmt(summary.worst_drawdown_usd)}</div>
                    </div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="text-xs text-slate-500">最长连亏</div>
                      <div className="mt-1 text-lg font-semibold text-slate-100">{fmt(summary.max_consecutive_losses, 0)}</div>
                    </div>
                  </div>

                  {validation.blockers?.length ? (
                    <div className="mt-3 rounded-md border border-rose-400/25 bg-rose-500/10 p-2 text-xs text-rose-100">
                      {validation.blockers.join(" · ")}
                    </div>
                  ) : null}
                  {String(summary.mode || "") === "approx_option_backtest" ? (
                    <div className="mt-3 rounded-md border border-amber-300/25 bg-amber-300/10 p-2 text-xs text-amber-100">
                      粗略验证：股票日线触发信号 + 理论 Call/Call Debit Spread 路径估算，并加入买卖价差和滑点；不代表真实期权历史成交价格。
                    </div>
                  ) : null}

                  {hasSwingDiagnostics ? (
                    <div className="mt-3 rounded-lg border border-cyan-300/20 bg-cyan-400/5 p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div>
                          <div className="text-xs font-semibold text-cyan-100">无交易诊断与调参建议</div>
                          <div className="mt-1 text-[11px] text-slate-500">
                            只解释本次 Lab 验证，不会自动写入配置。
                          </div>
                        </div>
                        <div className="text-[11px] text-slate-400">
                          {summary.primary_no_trade_reason ? `主因：${noTradeReasonLabel(summary.primary_no_trade_reason)}` : "含逐窗口诊断"}
                        </div>
                      </div>

                      {aggregateSuggestions.length ? (
                        <div className="mt-3 grid grid-cols-1 gap-2">
                          {aggregateSuggestions.slice(0, 4).map((item, idx) => {
                            const patchText = compactPatchText(item.suggested_patch);
                            return (
                              <div key={`${cid}-suggestion-${idx}`} className="rounded-md border border-slate-700/70 bg-slate-950/45 p-2">
                                <div className="text-xs font-semibold text-slate-100">{item.title || "建议调整"}</div>
                                <div className="mt-1 text-[11px] leading-5 text-slate-400">{item.reason || "-"}</div>
                                {patchText ? <div className="mt-1 font-mono text-[11px] leading-5 text-cyan-100">{patchText}</div> : null}
                                {item.caution ? <div className="mt-1 text-[11px] leading-5 text-amber-200">{item.caution}</div> : null}
                              </div>
                            );
                          })}
                        </div>
                      ) : null}

                      <div className="mt-3 space-y-2">
                        {(validation.rows || []).map((row) => {
                          const diag = row.metrics?.diagnostics && typeof row.metrics.diagnostics === "object" ? row.metrics.diagnostics : {};
                          const skipped = Array.isArray(diag.symbols_insufficient_bars)
                            ? diag.symbols_insufficient_bars
                            : Array.isArray(row.metrics?.symbols_skipped)
                              ? row.metrics?.symbols_skipped
                              : [];
                          const noTradeSymbols = Array.isArray(diag.symbols_no_trade)
                            ? diag.symbols_no_trade
                            : Array.isArray(row.metrics?.symbols_no_trade)
                              ? row.metrics?.symbols_no_trade
                              : [];
                          const closedDetails = Array.isArray(diag.closed_trades)
                            ? diag.closed_trades
                            : Array.isArray(row.metrics?.trade_details)
                              ? row.metrics?.trade_details
                              : [];
                          const rowSuggestions = suggestedAdjustments(row.metrics?.suggested_adjustments);
                          const closedTrades = Number(row.metrics?.closed_trades || 0);
                          if (!row.ok || (!skipped.length && !noTradeSymbols.length && !closedDetails.length && !rowSuggestions.length && closedTrades > 0)) return null;
                          return (
                            <details key={`${cid}-diag-${row.days}`} className="rounded-md border border-slate-800 bg-slate-950/40 p-2">
                              <summary className="cursor-pointer text-xs font-semibold text-slate-200">
                                {row.days} 天诊断：{closedTrades > 0 ? `闭合 ${fmt(closedTrades, 0)} 笔` : noTradeReasonLabel(row.metrics?.primary_no_trade_reason || row.metrics?.primary_signal_reason)}
                              </summary>
                              <div className="mt-2 grid grid-cols-1 gap-2 text-[11px] leading-5 text-slate-400">
                                <div>
                                  <div className="font-semibold text-slate-300">过滤统计</div>
                                  <div>无交易：{countSummary(row.metrics?.no_trade_reason_counts)}</div>
                                  <div>信号过滤：{countSummary(row.metrics?.signal_reason_counts)}</div>
                                  <div>
                                    股票 {fmt(row.metrics?.symbols_checked, 0)}/{fmt(row.metrics?.symbols_requested, 0)}
                                    {Number(row.metrics?.entry_signals || 0) > 0 ? ` · 信号 ${fmt(row.metrics?.entry_signals, 0)}` : ""}
                                    {Number(row.metrics?.budget_blocks || 0) > 0 ? ` · 预算过滤 ${fmt(row.metrics?.budget_blocks, 0)}` : ""}
                                  </div>
                                </div>
                                <div>
                                  <div className="font-semibold text-slate-300">股票明细</div>
                                  {skipped.slice(0, 4).map((item: any, idx: number) => (
                                    <div key={`${cid}-skip-${row.days}-${idx}`}>
                                      {item.symbol || "-"}：K线 {fmt(item.bars, 0)}/{fmt(item.required, 0)}
                                    </div>
                                  ))}
                                  {noTradeSymbols.slice(0, 5).map((item: any, idx: number) => (
                                    <div key={`${cid}-nt-${row.days}-${idx}`}>
                                      {item.symbol || "-"}：{symbolNoTradeText(item)}
                                    </div>
                                  ))}
                                  {!skipped.length && !noTradeSymbols.length ? <div>-</div> : null}
                                </div>
                                <div>
                                  <div className="font-semibold text-slate-300">本窗口建议</div>
                                  {rowSuggestions.slice(0, 3).map((item, idx) => {
                                    const patchText = compactPatchText(item.suggested_patch);
                                    return (
                                      <div key={`${cid}-row-suggestion-${row.days}-${idx}`} className="mb-1">
                                        <span className="text-slate-200">{item.title || "建议"}</span>
                                        {patchText ? <div className="font-mono text-cyan-100">{patchText}</div> : null}
                                      </div>
                                    );
                                  })}
                                  {!rowSuggestions.length ? <div>-</div> : null}
                                </div>
                              </div>
                              {closedDetails.length ? (
                                <div className="mt-3">
                                  <div className="mb-1 text-[11px] font-semibold text-slate-300">闭合交易明细</div>
                                  <div className="table-shell overflow-auto rounded-md">
                                    <table className="min-w-[760px] text-left text-[11px]">
                                      <thead className="table-head">
                                        <tr>
                                          <th className="px-2 py-1.5">标的</th>
                                          <th className="px-2 py-1.5">入场</th>
                                          <th className="px-2 py-1.5">退出</th>
                                          <th className="px-2 py-1.5">权利金</th>
                                          <th className="px-2 py-1.5">PnL</th>
                                          <th className="px-2 py-1.5">收益</th>
                                          <th className="px-2 py-1.5">原因</th>
                                        </tr>
                                      </thead>
                                      <tbody>
                                        {closedDetails.slice(0, 12).map((item: any, idx: number) => (
                                          <tr key={`${cid}-closed-${row.days}-${idx}`} className="border-t border-slate-800">
                                            <td className="px-2 py-1.5 font-mono text-slate-200">{item.symbol || "-"}</td>
                                            <td className="px-2 py-1.5">
                                              <div>{item.entry_date || "-"}</div>
                                              <div className="text-slate-500">spot {fmt(item.entry_spot)}</div>
                                            </td>
                                            <td className="px-2 py-1.5">
                                              <div>{item.exit_date || "-"}</div>
                                              <div className="text-slate-500">持有 {fmt(item.hold_days, 0)} 天</div>
                                            </td>
                                            <td className="px-2 py-1.5">
                                              <div>${fmt(item.estimated_entry_premium)}</div>
                                              <div className="text-slate-500">
                                                {swingModeLabel(item.structure)} · strike {fmt(item.strike)}
                                                {item.short_strike ? `/${fmt(item.short_strike)}` : ""}
                                                {item.debit_to_width_pct ? ` · debit/width ${fmt(item.debit_to_width_pct)}%` : ""}
                                                {item.min_stop_hold_days ? ` · 止损${fmt(item.min_stop_hold_days, 0)}天后` : ""}
                                              </div>
                                            </td>
                                            <td className={`px-2 py-1.5 ${Number(item.pnl_usd || 0) >= 0 ? "text-emerald-200" : "text-rose-200"}`}>
                                              ${fmt(item.pnl_usd)}
                                            </td>
                                            <td className={`px-2 py-1.5 ${Number(item.return_pct || 0) >= 0 ? "text-emerald-200" : "text-rose-200"}`}>
                                              {fmt(item.return_pct)}%
                                            </td>
                                            <td className="px-2 py-1.5">{exitReasonLabel(item.exit_reason)}</td>
                                          </tr>
                                        ))}
                                      </tbody>
                                    </table>
                                  </div>
                                </div>
                              ) : null}
                            </details>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}

                  <div className="mt-3 rounded-md border border-slate-700/70 bg-slate-950/40 p-3">
                    <div className="text-xs font-semibold text-slate-300">参数影响摘要</div>
                    <ul className="mt-2 space-y-1 text-xs leading-5 text-slate-400">
                      {riskSummary(candidate).slice(0, 5).map((line, idx) => (
                        <li key={`${cid}-risk-${idx}`}>{line}</li>
                      ))}
                    </ul>
                  </div>

                  <div className="mt-4 grid grid-cols-1 gap-3">
                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="text-xs font-semibold text-slate-300">研究解释</div>
                      <ul className="mt-2 space-y-1 text-xs leading-5 text-slate-400">
                        {(candidate.reasoning || []).slice(0, 5).map((line, idx) => (
                          <li key={`${cid}-reason-${idx}`}>{line}</li>
                        ))}
                      </ul>
                    </div>

                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="text-xs font-semibold text-slate-300">风控控制</div>
                      <pre className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap text-xs leading-5 text-slate-400">
                        {shortJson(candidate.research_controls)}
                      </pre>
                    </div>
                  </div>

                  <div className="mt-4 grid grid-cols-1 gap-3">
                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="mb-2 text-xs font-semibold text-slate-300">回测窗口</div>
                      <div className="table-shell rounded-lg">
                        <table className="min-w-full text-left text-xs">
                          <thead className="table-head">
                            <tr>
                              <th className="px-2 py-1.5">Days</th>
                              <th className="px-2 py-1.5">PnL</th>
                              <th className="px-2 py-1.5">Return</th>
                              <th className="px-2 py-1.5">Win</th>
                              <th className="px-2 py-1.5">Closed</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(validation.rows || []).map((row) => {
                              const closedTrades = Number(row.metrics?.closed_trades || 0);
                              const noTrade = Boolean(row.ok) && closedTrades <= 0;
                              const reason = noTrade
                                ? noTradeReasonLabel(
                                    row.metrics?.primary_no_trade_reason ||
                                      row.metrics?.primary_signal_reason ||
                                      Object.keys(row.metrics?.no_trade_reason_counts || {})[0] ||
                                      Object.keys(row.metrics?.signal_reason_counts || {})[0]
                                  )
                                : "";
                              return (
                                <tr key={`${cid}-${row.days}`} className="border-t border-slate-800">
                                  <td className="px-2 py-1.5">{row.days}</td>
                                  <td className="px-2 py-1.5">{row.ok ? (noTrade ? "无交易" : fmt(row.metrics?.realized_pnl)) : row.error}</td>
                                  <td className="px-2 py-1.5">{row.ok ? (noTrade ? reason : `${fmt(row.metrics?.return_pct)}%`) : "-"}</td>
                                  <td className="px-2 py-1.5">{row.ok ? (noTrade ? "-" : `${fmt(row.metrics?.win_rate_pct)}%`) : "-"}</td>
                                  <td className="px-2 py-1.5">
                                    {row.ok ? fmt(row.metrics?.closed_trades, 0) : "-"}
                                    {row.ok && row.metrics?.symbols_requested ? (
                                      <div className="mt-1 text-[10px] text-slate-500">
                                        股票 {fmt(row.metrics?.symbols_checked, 0)}/{fmt(row.metrics?.symbols_requested, 0)}
                                        {Number(row.metrics?.entry_signals || 0) > 0 ? ` · 信号 ${fmt(row.metrics?.entry_signals, 0)}` : ""}
                                        {Number(row.metrics?.budget_blocks || 0) > 0 ? ` · 预算过滤 ${fmt(row.metrics?.budget_blocks, 0)}` : ""}
                                      </div>
                                    ) : null}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="mb-2 text-xs font-semibold text-slate-300">写入配置预览</div>
                      <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs leading-5 text-slate-400">
                        {shortJson(candidate.strategy_config_patch)}
                      </pre>
                    </div>
                  </div>
                </div>
              );
            })
          ) : (
            <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-4 text-sm text-slate-400">
              还没有候选参数。点击“生成候选并验证”开始一次 Lab 运行。
            </div>
          )}
        </div>
      </section>
      </div>
    </PageShell>
  );
}
