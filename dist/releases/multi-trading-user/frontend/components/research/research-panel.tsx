"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type SyntheticEvent } from "react";
import { localAgentGet as apiGet, localAgentPost as apiPost } from "@/lib/local-agent-api";
import { formatTime, mapQueueBusyError, INPUT_CLS, PANEL_TITLE_CLS, SUB_TITLE_CLS } from "./research-utils";
import type {
  FactorABMarkdownResult,
  MlMatrixPayload,
  MlMatrixResult,
  ModelCompareResult,
  ResearchSnapshot,
  ResearchStatus,
  StrategyMatrixPayload,
  StrategyMatrixResult,
} from "./types";

type AtCfgSlice = {
  market: "us" | "hk" | "cn";
  kline: "1m" | "5m" | "10m" | "30m" | "1h" | "2h" | "4h" | "1d";
  top_n: number;
  backtest_days: number;
  signal_bars_days: number;
  universe?: Partial<Record<"us" | "hk" | "cn", string[]>>;
};

type MlMatrixApplyVariant = "auto" | "balanced" | "high_precision" | "high_coverage" | "best_score";
const RESEARCH_UI_CACHE_KEY = "lp_research_panel_cache_v2";
const RESEARCH_UI_SELECTED_SYMBOLS_KEY = "lp_research_selected_symbols_v1";

type ResearchCandidateRow = {
  symbol?: string;
  strength_score?: number;
  candidate_source?: "strong" | "manual" | "public" | string;
};

type ResearchUiCache = {
  cfg?: AtCfgSlice | null;
  researchStatus?: ResearchStatus | null;
  researchSnapshot?: ResearchSnapshot | null;
  modelCompare?: ModelCompareResult | null;
  strategyMatrix?: StrategyMatrixPayload | null;
  mlMatrix?: MlMatrixPayload | null;
  abMarkdown?: string;
};

type ResearchRunOptions = {
  run_openbb: boolean;
  run_tradingagents: boolean;
  run_pair_backtest: boolean;
  run_ml_diagnostics: boolean;
};

type ResearchResultTab = "overview" | "research" | "strategy" | "ml" | "pair" | "export";

type TaskProgress = {
  taskId: string;
  status: string;
  progressPct: number;
  progressStage: string;
  progressText: string;
  queuePosition: number;
  queueAhead: number;
};

type OpenBBHealthStatus = {
  ok?: boolean;
  provider?: string;
  health?: {
    enabled?: boolean;
    ok?: boolean;
    base_url?: string;
    reason?: string;
  };
};

type ConfigApplyKind = "research" | "strategy" | "ml";
type SnapshotHistoryType = "research" | "strategy_matrix" | "ml_matrix";

type SnapshotHistoryRow = {
  snapshot_id?: string;
  type?: string;
  market?: string;
  generated_at?: string;
  kline?: string;
  top_n?: number;
  backtest_days_requested?: number;
  backtest_days_used?: number;
  signal_bars_days_requested?: number;
  profile_tag?: string;
  note?: string;
};

type ConfigDiffRow = {
  key: string;
  label: string;
  before: string;
  after: string;
  changed: boolean;
};

type ConfigApplyPreview = {
  kind: ConfigApplyKind;
  title: string;
  description: string;
  sourceLabel: string;
  patch: Record<string, unknown>;
  successMessage: string;
  mlVariant?: MlMatrixApplyVariant;
  mlSnapshotId?: string;
};

type SnapshotCompareRow = {
  label: string;
  left: string;
  right: string;
  changed: boolean;
};

const CACHE_MAX_MODEL_ROWS = 20;
const CACHE_MAX_MATRIX_ROWS = 24;
const CACHE_MAX_STRATEGY_ROWS = 20;
const CACHE_MAX_ALLOC_ROWS = 20;
const CACHE_MAX_AB_ITEMS = 20;
const CACHE_MAX_MD_LEN = 120000;
const MAX_RENDER_ALLOC_ROWS = 120;
const MAX_RENDER_STRONG_ROWS = 160;
const MAX_RENDER_PAIR_POOL_ROWS = 120;
const MAX_RENDER_SELECTED_PAIR_ROWS = 160;
const MAX_RENDER_ML_DIAG_ROWS = 120;
const MAX_RENDER_PAIR_TRADE_ROWS = 300;
const MAX_FILTER_SCAN_PAIR_TRADE_ROWS = 4000;
const DEFAULT_RESEARCH_RUN_OPTIONS: ResearchRunOptions = {
  run_openbb: true,
  run_tradingagents: true,
  run_pair_backtest: true,
  run_ml_diagnostics: true,
};

const SNAPSHOT_HISTORY_TYPE_OPTIONS: Array<{ key: SnapshotHistoryType; label: string }> = [
  { key: "research", label: "Research 快照" },
  { key: "strategy_matrix", label: "策略矩阵" },
  { key: "ml_matrix", label: "ML 矩阵" },
];

const CONFIG_FIELD_LABELS: Record<string, string> = {
  research_allocation_enabled: "Research 分配开关",
  research_allocation_snapshot_id: "Research 快照来源",
  research_allocation_max_age_minutes: "快照有效期",
  research_allocation_notional_scale: "分配名义倍数",
  merge_strategy_matrix_top3: "并入策略矩阵前 3",
  merge_strategy_matrix_top3_snapshot_id: "策略矩阵快照来源",
  ml_filter_enabled: "ML 过滤开关",
  ml_model_type: "ML 模型",
  ml_threshold: "ML 阈值",
  ml_horizon_days: "ML 预测周期",
  ml_train_ratio: "ML 训练比例",
  ml_walk_forward_windows: "ML Walk-forward 窗口",
};

const MATRIX_PROFILE_LABEL: Record<string, string> = {
  balanced: "平衡",
  aggressive: "激进",
  defensive: "防守",
  ranked: "已排序",
  none: "无结果",
};

function normalizeTaskProgress(raw: any, fallbackTaskId: string): TaskProgress {
  const status = String(raw?.status || "").toLowerCase();
  const stage = String(raw?.progress_stage || status || "running");
  const taskId = String(raw?.task_id || fallbackTaskId || "");
  let pct = Number(raw?.progress_pct);
  if (!Number.isFinite(pct)) {
    pct = status === "completed" ? 100 : status === "queued" ? 0 : 10;
  }
  pct = Math.max(0, Math.min(100, Math.round(pct)));
  const text =
    String(raw?.progress_text || "").trim() ||
    (status === "completed"
      ? "任务完成"
      : status === "failed"
        ? "任务失败"
        : status === "cancelled"
          ? "任务已取消"
          : status === "queued"
            ? "任务排队中"
            : "任务运行中");
  const queuePosition = Math.max(0, Number(raw?.queue_position || 0) || 0);
  const queueAhead = Math.max(0, Number(raw?.queue_ahead || (queuePosition > 0 ? queuePosition - 1 : 0)) || 0);
  return {
    taskId,
    status,
    progressPct: pct,
    progressStage: stage,
    progressText: text,
    queuePosition,
    queueAhead,
  };
}

function pickLatestTaskByType(tasks: any[], taskType: string): any | null {
  const rows = tasks.filter((x) => String(x?.task_type || "").toLowerCase() === taskType);
  if (!rows.length) return null;
  rows.sort((a, b) => {
    const as = String(a?.started_at || a?.created_at || "");
    const bs = String(b?.started_at || b?.created_at || "");
    return bs.localeCompare(as);
  });
  return rows[0] || null;
}

function isTransientRequestError(err: any): boolean {
  const msg = String(err?.message || err || "").toLowerCase();
  return (
    msg.includes("请求超时") ||
    msg.includes("failed to fetch") ||
    msg.includes("networkerror") ||
    msg.includes("aborterror")
  );
}

async function recoverAcceptedTask(taskType: "research" | "strategy_matrix" | "ml_matrix"): Promise<any | null> {
  try {
    const rs = await apiGet<ResearchStatus>("/auto-trader/research/status", {
      timeoutMs: 10000,
      retries: 0,
    });
    const activeTasks = Array.isArray((rs as any)?.task_queue?.active_tasks)
      ? ((rs as any).task_queue.active_tasks as any[])
      : [];
    const picked = pickLatestTaskByType(activeTasks, taskType);
    if (!picked) return null;
    const taskId = String(picked?.task_id || "");
    if (!taskId) return null;
    return {
      ok: true,
      accepted: true,
      mode: "async",
      task_id: taskId,
      message: "recovered_after_timeout",
    };
  } catch {
    return null;
  }
}

function readResearchUiCache(): ResearchUiCache | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(RESEARCH_UI_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as ResearchUiCache) : null;
  } catch {
    return null;
  }
}

function writeResearchUiCache(cache: ResearchUiCache): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(RESEARCH_UI_CACHE_KEY, JSON.stringify(cache));
  } catch {
    // ignore cache write errors
  }
}

function readSelectedResearchSymbolsCache(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(RESEARCH_UI_SELECTED_SYMBOLS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const seen = new Set<string>();
    const out: string[] = [];
    for (const v of parsed) {
      const sym = String(v || "").trim().toUpperCase();
      if (!sym || seen.has(sym)) continue;
      seen.add(sym);
      out.push(sym);
    }
    return out;
  } catch {
    return [];
  }
}

function writeSelectedResearchSymbolsCache(symbols: string[]): void {
  if (typeof window === "undefined") return;
  try {
    const rows = Array.isArray(symbols)
      ? symbols
          .map((x) => String(x || "").trim().toUpperCase())
          .filter((x) => Boolean(x))
      : [];
    const unique = Array.from(new Set(rows));
    if (!unique.length) {
      window.sessionStorage.removeItem(RESEARCH_UI_SELECTED_SYMBOLS_KEY);
      return;
    }
    window.sessionStorage.setItem(RESEARCH_UI_SELECTED_SYMBOLS_KEY, JSON.stringify(unique));
  } catch {
    // ignore cache write errors
  }
}

function normalizeResearchSymbolInput(raw: string, market?: string): string {
  let sym = String(raw || "").trim().toUpperCase();
  if (!sym) return "";
  sym = sym.replace(/\s+/g, "");
  sym = sym.replace(/[\uFF0C,\uFF1B;\u3001|]+/g, "");
  if (!sym) return "";
  if (sym.startsWith("^")) return sym;
  if (/^\d{5}\.HK$/.test(sym) || /^\d{6}\.(SH|SZ|BJ)$/.test(sym) || /^[A-Z0-9.-]+\.[A-Z]{2,3}$/.test(sym)) {
    if (sym.endsWith(".SS")) return `${sym.slice(0, -3)}.SH`;
    return sym;
  }
  const mk = String(market || "").trim().toLowerCase();
  if (/^\d{6}$/.test(sym)) {
    return `${sym}${sym.startsWith("6") || sym.startsWith("9") ? ".SH" : ".SZ"}`;
  }
  if (/^\d{1,5}$/.test(sym)) {
    return `${sym.padStart(5, "0")}.HK`;
  }
  if (/^[A-Z][A-Z0-9.-]*$/.test(sym)) {
    return mk === "cn" ? sym : `${sym}.US`;
  }
  return sym;
}

function parseResearchSymbolText(text: string, market?: string): string[] {
  const raw = String(text || "")
    .replace(/[\r\n\t]+/g, ",")
    .split(/[\s,\uFF0C;\uFF1B\u3001|]+/g);
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of raw) {
    const sym = normalizeResearchSymbolInput(item, market);
    if (!sym || seen.has(sym)) continue;
    seen.add(sym);
    out.push(sym);
  }
  return out;
}

function mergeResearchSymbols(base: string[], extra: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of [...(Array.isArray(base) ? base : []), ...(Array.isArray(extra) ? extra : [])]) {
    const sym = String(item || "").trim().toUpperCase();
    if (!sym || seen.has(sym)) continue;
    seen.add(sym);
    out.push(sym);
  }
  return out;
}

function mergeResearchCandidates(base: ResearchCandidateRow[], extra: ResearchCandidateRow[]): ResearchCandidateRow[] {
  const seen = new Set<string>();
  const out: ResearchCandidateRow[] = [];
  for (const item of [...(Array.isArray(base) ? base : []), ...(Array.isArray(extra) ? extra : [])]) {
    const sym = String(item?.symbol || "").trim().toUpperCase();
    if (!sym || seen.has(sym)) continue;
    seen.add(sym);
    out.push({
      symbol: sym,
      strength_score: typeof item?.strength_score === "number" ? item.strength_score : undefined,
      candidate_source: item?.candidate_source || "manual",
    });
  }
  return out;
}

function formatConfigValue(value: unknown, key?: string): string {
  if (value === undefined || value === null || value === "") {
    if (key?.endsWith("_snapshot_id")) return "最新快照";
    return "-";
  }
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  if (typeof value === "number") {
    if (key === "research_allocation_max_age_minutes") {
      if (value === 0) return "不检查";
      if (value === 10080) return "7 天";
      return `${value} 分钟`;
    }
    return String(value);
  }
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function normalizeConfigComparable(value: unknown): string {
  if (value === undefined || value === null || value === "") return "";
  if (typeof value === "number") return Number.isFinite(value) ? String(Number(value)) : "";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function buildConfigDiffRows(currentConfig: Record<string, any> | null, patch: Record<string, unknown>): ConfigDiffRow[] {
  return Object.keys(patch).map((key) => {
    const beforeValue = currentConfig ? currentConfig[key] : undefined;
    const afterValue = patch[key];
    return {
      key,
      label: CONFIG_FIELD_LABELS[key] || key,
      before: formatConfigValue(beforeValue, key),
      after: formatConfigValue(afterValue, key),
      changed: normalizeConfigComparable(beforeValue) !== normalizeConfigComparable(afterValue),
    };
  });
}

function resolveMlMatrixRowForPreview(
  result: MlMatrixPayload | null | undefined,
  variant: MlMatrixApplyVariant
): { row: any | null; source: string } {
  if (!result?.ok) return { row: null, source: "" };
  const v = String(variant || "auto");
  if (v === "balanced") return { row: result.best_balanced || null, source: "best_balanced" };
  if (v === "high_precision") return { row: result.best_high_precision || null, source: "best_high_precision" };
  if (v === "high_coverage") return { row: result.best_high_coverage || null, source: "best_high_coverage" };
  const rows = Array.isArray(result.items) ? result.items : [];
  if (v === "best_score") {
    const picked = [...rows].sort((a, b) => Number(b?.score ?? -1e9) - Number(a?.score ?? -1e9))[0] || null;
    return { row: picked, source: picked ? "items_top_score" : "" };
  }
  for (const key of ["best_balanced", "best_high_precision", "best_high_coverage"] as const) {
    const row = result[key];
    if (row?.params) return { row, source: key };
  }
  const picked = [...rows].sort((a, b) => Number(b?.score ?? -1e9) - Number(a?.score ?? -1e9))[0] || null;
  return { row: picked, source: picked ? "items_top_score" : "" };
}

function mlMatrixRowToConfigPatch(row: any, enableMlFilter = true): Record<string, unknown> {
  const params = row?.params && typeof row.params === "object" ? row.params : {};
  const out: Record<string, unknown> = {
    ml_filter_enabled: enableMlFilter,
  };
  if (params.model_type) out.ml_model_type = params.model_type;
  if (params.ml_threshold !== undefined) out.ml_threshold = params.ml_threshold;
  if (params.ml_horizon_days !== undefined) out.ml_horizon_days = params.ml_horizon_days;
  if (params.ml_train_ratio !== undefined) out.ml_train_ratio = params.ml_train_ratio;
  if (params.ml_walk_forward_windows !== undefined) out.ml_walk_forward_windows = params.ml_walk_forward_windows;
  return out;
}

function formatSnapshotHistoryLabel(row: SnapshotHistoryRow | null | undefined): string {
  if (!row) return "未选择";
  const parts = [
    row.snapshot_id ? String(row.snapshot_id) : "未命名快照",
    row.generated_at ? formatTime(row.generated_at) : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

function snapshotMetaSummary(row: SnapshotHistoryRow | null | undefined): string {
  if (!row) return "-";
  const t = String(row.type || "");
  if (t === "strategy_matrix") {
    return [
      row.kline ? String(row.kline).toUpperCase() : "",
      row.backtest_days_requested ? `${row.backtest_days_requested}天` : "",
      row.profile_tag ? MATRIX_PROFILE_LABEL[String(row.profile_tag)] || String(row.profile_tag) : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (t === "ml_matrix") {
    return [
      row.kline ? String(row.kline).toUpperCase() : "",
      row.signal_bars_days_requested ? `信号${row.signal_bars_days_requested}天` : "",
      row.top_n ? `TopN ${row.top_n}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  return [
    row.kline ? String(row.kline).toUpperCase() : "",
    row.backtest_days_requested ? `${row.backtest_days_requested}天` : "",
    row.backtest_days_used ? `使用${row.backtest_days_used}天` : "",
    row.top_n ? `TopN ${row.top_n}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function topSymbolsText(rows: any[], symbolKey = "symbol", limit = 5): string {
  if (!Array.isArray(rows) || !rows.length) return "-";
  return rows
    .slice(0, limit)
    .map((x) => String(x?.[symbolKey] || "").trim())
    .filter(Boolean)
    .join(", ") || "-";
}

function buildSnapshotCompareRows(
  type: SnapshotHistoryType,
  left: any | null,
  right: any | null
): SnapshotCompareRow[] {
  const rows: Array<{ label: string; left: unknown; right: unknown }> = [];
  if (type === "research") {
    const leftAlloc = Array.isArray(left?.allocation_plan) ? left.allocation_plan : [];
    const rightAlloc = Array.isArray(right?.allocation_plan) ? right.allocation_plan : [];
    const leftStrong = Array.isArray(left?.strong_stocks) ? left.strong_stocks : [];
    const rightStrong = Array.isArray(right?.strong_stocks) ? right.strong_stocks : [];
    rows.push(
      { label: "生成时间", left: formatTime(left?.generated_at), right: formatTime(right?.generated_at) },
      { label: "市场 / K线", left: `${left?.market || "-"} / ${left?.kline || "-"}`, right: `${right?.market || "-"} / ${right?.kline || "-"}` },
      { label: "TopN", left: left?.top_n ?? "-", right: right?.top_n ?? "-" },
      { label: "分配数量", left: leftAlloc.length, right: rightAlloc.length },
      { label: "Top分配", left: topSymbolsText(leftAlloc), right: topSymbolsText(rightAlloc) },
      { label: "强势标的", left: topSymbolsText(leftStrong), right: topSymbolsText(rightStrong) }
    );
  } else if (type === "strategy_matrix") {
    const leftRows = Array.isArray(left?.items) ? left.items : [];
    const rightRows = Array.isArray(right?.items) ? right.items : [];
    rows.push(
      { label: "生成时间", left: formatTime(left?.generated_at), right: formatTime(right?.generated_at) },
      { label: "候选 / 表格行", left: `${left?.candidate_count ?? "-"} / ${leftRows.length}`, right: `${right?.candidate_count ?? "-"} / ${rightRows.length}` },
      { label: "网格 / 策略数", left: `${left?.grid_size ?? "-"} / ${left?.strategy_count ?? "-"}`, right: `${right?.grid_size ?? "-"} / ${right?.strategy_count ?? "-"}` },
      {
        label: "平衡推荐",
        left: left?.best_balanced?.strategy_label || left?.best_balanced?.strategy || "-",
        right: right?.best_balanced?.strategy_label || right?.best_balanced?.strategy || "-",
      },
      { label: "矩阵分", left: left?.best_balanced?.matrix_score ?? "-", right: right?.best_balanced?.matrix_score ?? "-" },
      { label: "平均收益%", left: left?.best_balanced?.avg_net_return_pct ?? "-", right: right?.best_balanced?.avg_net_return_pct ?? "-" }
    );
  } else {
    const leftRows = Array.isArray(left?.items) ? left.items : [];
    const rightRows = Array.isArray(right?.items) ? right.items : [];
    rows.push(
      { label: "生成时间", left: formatTime(left?.generated_at), right: formatTime(right?.generated_at) },
      { label: "评估 / 通过", left: `${left?.evaluated_count ?? "-"} / ${left?.passed_constraints_count ?? "-"}`, right: `${right?.evaluated_count ?? "-"} / ${right?.passed_constraints_count ?? "-"}` },
      { label: "信号窗口", left: left?.signal_bars_days ?? "-", right: right?.signal_bars_days ?? "-" },
      {
        label: "平衡模型",
        left: left?.best_balanced?.params?.model_type || "-",
        right: right?.best_balanced?.params?.model_type || "-",
      },
      { label: "阈值", left: left?.best_balanced?.params?.ml_threshold ?? "-", right: right?.best_balanced?.params?.ml_threshold ?? "-" },
      { label: "Precision", left: left?.best_balanced?.metrics?.precision ?? "-", right: right?.best_balanced?.metrics?.precision ?? "-" },
      { label: "Coverage", left: left?.best_balanced?.metrics?.coverage ?? "-", right: right?.best_balanced?.metrics?.coverage ?? "-" },
      { label: "结果行", left: leftRows.length, right: rightRows.length }
    );
  }
  return rows.map((x) => ({
    label: x.label,
    left: formatConfigValue(x.left),
    right: formatConfigValue(x.right),
    changed: normalizeConfigComparable(x.left) !== normalizeConfigComparable(x.right),
  }));
}

function trimResearchCache(cache: ResearchUiCache): ResearchUiCache {
  const out: ResearchUiCache = {
    cfg: cache.cfg ?? null,
    researchStatus: cache.researchStatus ?? null,
    researchSnapshot: cache.researchSnapshot ?? null,
    modelCompare: cache.modelCompare ?? null,
    strategyMatrix: cache.strategyMatrix ?? null,
    mlMatrix: cache.mlMatrix ?? null,
    abMarkdown: typeof cache.abMarkdown === "string" ? cache.abMarkdown.slice(0, CACHE_MAX_MD_LEN) : "",
  };

  if (out.modelCompare?.items && Array.isArray(out.modelCompare.items)) {
    out.modelCompare = { ...out.modelCompare, items: out.modelCompare.items.slice(0, CACHE_MAX_MODEL_ROWS) };
  }
  if (out.strategyMatrix?.items && Array.isArray(out.strategyMatrix.items)) {
    out.strategyMatrix = { ...out.strategyMatrix, items: out.strategyMatrix.items.slice(0, CACHE_MAX_MATRIX_ROWS) };
  }
  if (out.mlMatrix?.items && Array.isArray(out.mlMatrix.items)) {
    out.mlMatrix = { ...out.mlMatrix, items: out.mlMatrix.items.slice(0, CACHE_MAX_MATRIX_ROWS) };
  }

  const snap = out.researchSnapshot?.snapshot;
  if (snap && typeof snap === "object") {
    const trimmedSnapshot: any = { ...snap };
    if (Array.isArray(trimmedSnapshot.strategy_rankings)) {
      trimmedSnapshot.strategy_rankings = trimmedSnapshot.strategy_rankings.slice(0, CACHE_MAX_STRATEGY_ROWS);
    }
    if (Array.isArray(trimmedSnapshot.allocation_plan)) {
      trimmedSnapshot.allocation_plan = trimmedSnapshot.allocation_plan.slice(0, CACHE_MAX_ALLOC_ROWS);
    }
    if (trimmedSnapshot.factor_ab_report && typeof trimmedSnapshot.factor_ab_report === "object") {
      const ab = { ...trimmedSnapshot.factor_ab_report };
      if (Array.isArray(ab.items)) ab.items = ab.items.slice(0, CACHE_MAX_AB_ITEMS);
      trimmedSnapshot.factor_ab_report = ab;
    }
    // 交易明细可能很大，缓存中移除，避免切页卡顿；页面可后台重新拉取。
    if (trimmedSnapshot.pair_backtest && typeof trimmedSnapshot.pair_backtest === "object") {
      trimmedSnapshot.pair_backtest = {
        ...trimmedSnapshot.pair_backtest,
        selected_pairs: [],
      };
    }
    out.researchSnapshot = {
      ...(out.researchSnapshot as any),
      snapshot: trimmedSnapshot,
    };
  }
  return out;
}
type StrategyMatrixPresetKey = "conservative" | "balanced" | "aggressive";

const STRATEGY_MATRIX_PRESETS: Record<
  StrategyMatrixPresetKey,
  {
    label: string;
    top_n: number;
    max_strategies: number;
    max_drawdown_limit_pct: number;
    min_symbols_used: number;
    matrix_overrides: Record<string, unknown>;
  }
> = {
  conservative: {
    label: "保守（最快）",
    top_n: 6,
    max_strategies: 4,
    max_drawdown_limit_pct: 25,
    min_symbols_used: 3,
    matrix_overrides: {
      use_config_strategies_only: true,
      parallel_workers: 4,
      backtest_days: 90,
      max_total_variants: 80,
      max_variants_per_strategy: 6,
      max_eval_cache_entries: 30000,
    },
  },
  balanced: {
    label: "平衡（推荐）",
    top_n: 8,
    max_strategies: 6,
    max_drawdown_limit_pct: 30,
    min_symbols_used: 4,
    matrix_overrides: {
      use_config_strategies_only: true,
      parallel_workers: 6,
      backtest_days: 120,
      max_total_variants: 160,
      max_variants_per_strategy: 10,
      max_eval_cache_entries: 50000,
    },
  },
  aggressive: {
    label: "激进（更全面）",
    top_n: 10,
    max_strategies: 8,
    max_drawdown_limit_pct: 35,
    min_symbols_used: 4,
    matrix_overrides: {
      use_config_strategies_only: false,
      parallel_workers: 8,
      backtest_days: 180,
      max_total_variants: 320,
      max_variants_per_strategy: 16,
      max_eval_cache_entries: 80000,
    },
  },
};

type ResearchPanelOpenSnapshot = {
  pairBacktestPanelOpen: boolean;
  allocationPanelOpen: boolean;
  abPanelOpen: boolean;
  mlDiagPanelOpen: boolean;
  modelComparePanelOpen: boolean;
  strategyMatrixPanelOpen: boolean;
  strategySummaryPanelOpen: boolean;
  mlMatrixPanelOpen: boolean;
};

const RESEARCH_PANEL_OVERRIDE_KEYS: Record<string, keyof ResearchPanelOpenSnapshot> = {
  model_compare: "modelComparePanelOpen",
  strategy_summary: "strategySummaryPanelOpen",
  strategy_matrix: "strategyMatrixPanelOpen",
  ml_matrix: "mlMatrixPanelOpen",
  ml_diag: "mlDiagPanelOpen",
  ab_report: "abPanelOpen",
  allocation: "allocationPanelOpen",
  pair_backtest: "pairBacktestPanelOpen",
};

export function ResearchPanel() {
  const [cachedSeed] = useState<ResearchUiCache | null>(() => readResearchUiCache());
  const [error, setError] = useState("");
  const loadingRef = useRef(false);
  const [cfg, setCfg] = useState<AtCfgSlice | null>(cachedSeed?.cfg ?? null);
  const [autoTraderConfig, setAutoTraderConfig] = useState<Record<string, any> | null>(null);
  const [researchStatus, setResearchStatus] = useState<ResearchStatus | null>(cachedSeed?.researchStatus ?? null);
  const [researchSnapshot, setResearchSnapshot] = useState<ResearchSnapshot | null>(cachedSeed?.researchSnapshot ?? null);
  const [openbbHealth, setOpenbbHealth] = useState<OpenBBHealthStatus | null>(null);
  const [modelCompare, setModelCompare] = useState<ModelCompareResult | null>(cachedSeed?.modelCompare ?? null);
  const [strategyMatrix, setStrategyMatrix] = useState<StrategyMatrixPayload | null>(cachedSeed?.strategyMatrix ?? null);
  const [mlMatrix, setMlMatrix] = useState<MlMatrixPayload | null>(cachedSeed?.mlMatrix ?? null);
  const [researchStarting, setResearchStarting] = useState(false);
  const [researchRunning, setResearchRunning] = useState(false);
  const [strategyMatrixRunning, setStrategyMatrixRunning] = useState(false);
  const [mlMatrixRunning, setMlMatrixRunning] = useState(false);
  const [researchTaskId, setResearchTaskId] = useState<string>("");
  const [strategyMatrixTaskId, setStrategyMatrixTaskId] = useState<string>("");
  const [mlMatrixTaskId, setMlMatrixTaskId] = useState<string>("");
  const [researchProgress, setResearchProgress] = useState<TaskProgress | null>(null);
  const [strategyMatrixProgress, setStrategyMatrixProgress] = useState<TaskProgress | null>(null);
  const [mlMatrixProgress, setMlMatrixProgress] = useState<TaskProgress | null>(null);
  const [researchRunOptions, setResearchRunOptions] = useState<ResearchRunOptions>(DEFAULT_RESEARCH_RUN_OPTIONS);
  const [mlApplyVariant, setMlApplyVariant] = useState<MlMatrixApplyVariant>("auto");
  const [mlMatrixSnapshots, setMlMatrixSnapshots] = useState<any[]>([]);
  // 空字符串表示使用“最新结果”（后端兼容逻辑）。
  const [mlApplySnapshotId, setMlApplySnapshotId] = useState<string>("");
  const [strategyPreset, setStrategyPreset] = useState<StrategyMatrixPresetKey>("balanced");
  const [mlApplyBusy, setMlApplyBusy] = useState(false);
  const [configApplyBusy, setConfigApplyBusy] = useState<"" | "research" | "strategy">("");
  const [configApplyMessage, setConfigApplyMessage] = useState("");
  const [configApplyPreview, setConfigApplyPreview] = useState<ConfigApplyPreview | null>(null);
  const [snapshotHistoryType, setSnapshotHistoryType] = useState<SnapshotHistoryType>("research");
  const [snapshotHistories, setSnapshotHistories] = useState<Record<SnapshotHistoryType, SnapshotHistoryRow[]>>({
    research: [],
    strategy_matrix: [],
    ml_matrix: [],
  });
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyCompareIds, setHistoryCompareIds] = useState<Record<SnapshotHistoryType, { left: string; right: string }>>({
    research: { left: "", right: "" },
    strategy_matrix: { left: "", right: "" },
    ml_matrix: { left: "", right: "" },
  });
  const [historyComparePayload, setHistoryComparePayload] = useState<{
    type: SnapshotHistoryType;
    leftId: string;
    rightId: string;
    left: any | null;
    right: any | null;
  } | null>(null);
  const [historyCompareLoading, setHistoryCompareLoading] = useState(false);
  const [abMarkdown, setAbMarkdown] = useState<string>(String(cachedSeed?.abMarkdown || ""));
  const [pairTradeFilterPair, setPairTradeFilterPair] = useState("");
  const [pairTradeFilterSymbol, setPairTradeFilterSymbol] = useState("");
  const [syncingStrongCandidates, setSyncingStrongCandidates] = useState(false);
  const [importingPublicUniverse, setImportingPublicUniverse] = useState(false);
  const [researchSymbolsInput, setResearchSymbolsInput] = useState("");
  const [strongCandidates, setStrongCandidates] = useState<ResearchCandidateRow[]>([]);
  const [selectedResearchSymbols, setSelectedResearchSymbols] = useState<string[]>(
    () => readSelectedResearchSymbolsCache()
  );
  const [strongCandidatesMeta, setStrongCandidatesMeta] = useState<{
    source?: string;
    scan_time?: string;
    worker_last_scan_summary_at?: string;
  }>({});
  const [strategyMatrixPanelOpen, setStrategyMatrixPanelOpen] = useState(false);
  const [mlMatrixPanelOpen, setMlMatrixPanelOpen] = useState(false);
  const [mlDiagPanelOpen, setMlDiagPanelOpen] = useState(false);
  const [abPanelOpen, setAbPanelOpen] = useState(false);
  const [allocationPanelOpen, setAllocationPanelOpen] = useState(false);
  const [pairBacktestPanelOpen, setPairBacktestPanelOpen] = useState(false);
  const [modelComparePanelOpen, setModelComparePanelOpen] = useState(false);
  const [strategySummaryPanelOpen, setStrategySummaryPanelOpen] = useState(false);
  const [resultTab, setResultTab] = useState<ResearchResultTab>("overview");
  /** 折叠块展开后触发的按需刷新：显示「正在加载」且不依赖 loadResearch 引用变化 */
  const [sectionLoading, setSectionLoading] = useState<Record<string, boolean>>({});

  const panelOpenRef = useRef<ResearchPanelOpenSnapshot>({
    pairBacktestPanelOpen: false,
    allocationPanelOpen: false,
    abPanelOpen: false,
    mlDiagPanelOpen: false,
    modelComparePanelOpen: false,
    strategyMatrixPanelOpen: false,
    strategySummaryPanelOpen: false,
    mlMatrixPanelOpen: false,
  });
  useEffect(() => {
    panelOpenRef.current = {
      pairBacktestPanelOpen,
      allocationPanelOpen,
      abPanelOpen,
      mlDiagPanelOpen,
      modelComparePanelOpen,
      strategyMatrixPanelOpen,
      strategySummaryPanelOpen,
      mlMatrixPanelOpen,
    };
  }, [
    pairBacktestPanelOpen,
    allocationPanelOpen,
    abPanelOpen,
    mlDiagPanelOpen,
    modelComparePanelOpen,
    strategyMatrixPanelOpen,
    strategySummaryPanelOpen,
    mlMatrixPanelOpen,
  ]);

  const taskRunningRef = useRef({
    researchRunning: false,
    strategyMatrixRunning: false,
    mlMatrixRunning: false,
  });
  useEffect(() => {
    taskRunningRef.current = {
      researchRunning,
      strategyMatrixRunning,
      mlMatrixRunning,
    };
  }, [researchRunning, strategyMatrixRunning, mlMatrixRunning]);

  const latestStateRef = useRef<{
    cfg: AtCfgSlice | null;
    researchStatus: ResearchStatus | null;
    researchSnapshot: ResearchSnapshot | null;
    modelCompare: ModelCompareResult | null;
    strategyMatrix: StrategyMatrixPayload | null;
    mlMatrix: MlMatrixPayload | null;
    abMarkdown: string;
  }>({
    cfg,
    researchStatus,
    researchSnapshot,
    modelCompare,
    strategyMatrix,
    mlMatrix,
    abMarkdown,
  });

  useEffect(() => {
    latestStateRef.current = {
      cfg,
      researchStatus,
      researchSnapshot,
      modelCompare,
      strategyMatrix,
      mlMatrix,
      abMarkdown,
    };
  }, [cfg, researchStatus, researchSnapshot, modelCompare, strategyMatrix, mlMatrix, abMarkdown]);

  // 三类 Research history 列表：用于应用前来源确认与历史快照对比。
  useEffect(() => {
    if (!cfg?.market) return;
    let cancelled = false;
    (async () => {
      setHistoryLoading(true);
      try {
        const results = await Promise.allSettled(
          SNAPSHOT_HISTORY_TYPE_OPTIONS.map((opt) =>
            apiGet<any>(
              `/auto-trader/research/snapshots?type=${encodeURIComponent(opt.key)}&market=${encodeURIComponent(cfg.market)}`,
              { timeoutMs: 12000, retries: 0 }
            )
          )
        );
        if (cancelled) return;
        const next: Record<SnapshotHistoryType, SnapshotHistoryRow[]> = {
          research: [],
          strategy_matrix: [],
          ml_matrix: [],
        };
        results.forEach((res, idx) => {
          const key = SNAPSHOT_HISTORY_TYPE_OPTIONS[idx]?.key;
          if (!key || res.status !== "fulfilled") return;
          next[key] = Array.isArray(res.value?.snapshots) ? res.value.snapshots : [];
        });
        setSnapshotHistories(next);
        setMlMatrixSnapshots(next.ml_matrix);
        setHistoryCompareIds((prev) => {
          const out = { ...prev };
          for (const opt of SNAPSHOT_HISTORY_TYPE_OPTIONS) {
            const rows = next[opt.key] || [];
            const prevPair = prev[opt.key] || { left: "", right: "" };
            const leftStillValid = rows.some((x) => String(x?.snapshot_id || "") === prevPair.left);
            const rightStillValid = rows.some((x) => String(x?.snapshot_id || "") === prevPair.right);
            out[opt.key] = {
              left: leftStillValid ? prevPair.left : String(rows[0]?.snapshot_id || ""),
              right: rightStillValid ? prevPair.right : String(rows[1]?.snapshot_id || rows[0]?.snapshot_id || ""),
            };
          }
          return out;
        });
      } catch {
        // ignore: 历史对比区会显示暂无快照
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cfg?.market, researchSnapshot?.snapshot?.generated_at, strategyMatrix?.generated_at, mlMatrix?.generated_at]);

  const buildPairTradeRows = useCallback((rows: any[], maxRows?: number) => {
    const out: any[] = [];
    for (const row of rows) {
      const pair = row?.pair || {};
      const metrics = row?.selected_metrics || {};
      const symbol = row?.selected_symbol || metrics?.symbol || "-";
      const strategy = metrics?.strategy_label || row?.selected_strategy || "-";
      const trades = Array.isArray(metrics?.trade_history) ? metrics.trade_history : [];
      for (let idx = 0; idx < trades.length; idx += 1) {
        const t = trades[idx];
        out.push({
          id: `${pair?.long || "long"}-${pair?.short || "short"}-${symbol}-${idx}`,
          pair: `${pair?.long || "-"} / ${pair?.short || "-"}`,
          symbol,
          strategy,
          entry_date: t?.entry_date,
          exit_date: t?.exit_date,
          entry_price: t?.entry_price,
          exit_price: t?.exit_price,
          quantity: t?.quantity,
          pnl_pct: t?.pnl_pct,
          pnl: t?.pnl,
          hold_days: t?.hold_days,
        });
        if (typeof maxRows === "number" && maxRows > 0 && out.length >= maxRows) {
          return out;
        }
      }
    }
    return out;
  }, []);

  const loadResearch = useCallback(
    async (
      force = false,
      opts?: { retainError?: boolean; panelOverrides?: Partial<ResearchPanelOpenSnapshot> }
    ) => {
    const retainError = Boolean(opts?.retainError);
    if ((loadingRef.current && !force) || (!force && typeof document !== "undefined" && document.hidden)) {
      return;
    }
    loadingRef.current = true;
    try {
      const latest = latestStateRef.current;
      const panels: ResearchPanelOpenSnapshot = { ...panelOpenRef.current, ...(opts?.panelOverrides || {}) };
      const tr = taskRunningRef.current;
      const heavyTaskRunning = tr.strategyMatrixRunning || tr.mlMatrixRunning || tr.researchRunning;

      const [statusResult, rsResult] = await Promise.allSettled([
        apiGet<any>("/auto-trader/status", {
          timeoutMs: 10000,
          retries: 0,
        }),
        apiGet<ResearchStatus>("/auto-trader/research/status", {
          timeoutMs: 10000,
          retries: 0,
        }),
      ]);
      const openbbHealthPromise = researchRunOptions.run_openbb
        ? apiGet<OpenBBHealthStatus>("/research/external/openbb/health", {
            cacheTtlMs: 0,
            timeoutMs: 20000,
            retries: 0,
          }).catch(() => null)
        : Promise.resolve(null);

      const st = statusResult.status === "fulfilled" ? statusResult.value : null;
      const c = st?.config;
      if (c && typeof c === "object") {
        setAutoTraderConfig(c);
      } else if (statusResult.status === "fulfilled") {
        setAutoTraderConfig(null);
      }
      const nextCfg: AtCfgSlice | null = c
        ? {
            market: (c.market as AtCfgSlice["market"]) || "us",
            kline: (c.kline as AtCfgSlice["kline"]) || "1d",
            top_n: Number(c.top_n) || 8,
            backtest_days: Number(c.backtest_days) || 120,
            signal_bars_days: Number(c.signal_bars_days) || 90,
            universe: c.universe && typeof c.universe === "object" ? c.universe : undefined,
          }
        : latest.cfg;
      if (c) {
        setCfg(nextCfg);
      } else if (statusResult.status === "fulfilled") {
        setCfg(null);
      }

      const mkt = c ? String(c.market || "us") : String(nextCfg?.market || "us");
      let nextResearchStatus: ResearchStatus | null = latest.researchStatus;
      let nextResearchSnapshot: ResearchSnapshot | null = latest.researchSnapshot;
      let nextModelCompare: ModelCompareResult | null = latest.modelCompare;
      let nextStrategyMatrix: StrategyMatrixPayload | null = latest.strategyMatrix;
      let nextMlMatrix: MlMatrixPayload | null = latest.mlMatrix;

      const overviewNeedsSummary = resultTab === "overview";
      const shouldFetchSnapshot =
        force ||
        (!heavyTaskRunning &&
          (overviewNeedsSummary ||
            panels.pairBacktestPanelOpen ||
            panels.allocationPanelOpen ||
            panels.abPanelOpen ||
            panels.mlDiagPanelOpen));
      const shouldFetchModelCompare = force || (!heavyTaskRunning && panels.modelComparePanelOpen);
      const shouldFetchStrategyMatrix =
        force || (!heavyTaskRunning && (overviewNeedsSummary || panels.strategyMatrixPanelOpen || panels.strategySummaryPanelOpen));
      const shouldFetchMlMatrix = force || (!heavyTaskRunning && (overviewNeedsSummary || panels.mlMatrixPanelOpen));

      if (rsResult.status === "fulfilled") {
        try {
          const rs = rsResult.value;
          nextResearchStatus = rs || null;
          setResearchStatus(nextResearchStatus);
          const activeTasks = Array.isArray((rs as any)?.task_queue?.active_tasks)
            ? ((rs as any).task_queue.active_tasks as any[])
            : [];
          const researchTask = pickLatestTaskByType(activeTasks, "research");
          const strategyTask = pickLatestTaskByType(activeTasks, "strategy_matrix");
          const mlTask = pickLatestTaskByType(activeTasks, "ml_matrix");

          if (researchTask) {
            const tid = String(researchTask?.task_id || "");
            if (tid) setResearchTaskId(tid);
            setResearchRunning(true);
            setResearchProgress(normalizeTaskProgress(researchTask, tid));
          }
          if (strategyTask) {
            const tid = String(strategyTask?.task_id || "");
            if (tid) setStrategyMatrixTaskId(tid);
            setStrategyMatrixRunning(true);
            setStrategyMatrixProgress(normalizeTaskProgress(strategyTask, tid));
          }
          if (mlTask) {
            const tid = String(mlTask?.task_id || "");
            if (tid) setMlMatrixTaskId(tid);
            setMlMatrixRunning(true);
            setMlMatrixProgress(normalizeTaskProgress(mlTask, tid));
          }
        } catch {
          // 保留上次状态，等待下一轮刷新
        }
      }
      const liveOpenbbHealth = await openbbHealthPromise;
      if (liveOpenbbHealth) {
        setOpenbbHealth(liveOpenbbHealth);
        const hp = liveOpenbbHealth.health || {};
        nextResearchStatus = {
          ...(nextResearchStatus || {}),
          data_providers: {
            ...((nextResearchStatus || {}) as any).data_providers,
            primary: ((nextResearchStatus || {}) as any).data_providers?.primary || "longport",
            openbb_enabled: Boolean(hp.enabled),
            openbb_connected: Boolean(hp.ok),
            openbb_base_url: String(hp.base_url || ""),
            cn_public_data: ((nextResearchStatus || {}) as any).data_providers?.cn_public_data,
          },
        };
        setResearchStatus(nextResearchStatus);
      }

      try {
        const results = await Promise.allSettled([
          shouldFetchSnapshot
            ? apiGet<ResearchSnapshot>("/auto-trader/research/snapshot", { cacheTtlMs: 0, retries: 0 })
            : Promise.resolve(nextResearchSnapshot),
          shouldFetchModelCompare
            ? apiGet<ModelCompareResult>("/auto-trader/research/model-compare?top=10")
            : Promise.resolve(nextModelCompare),
          shouldFetchStrategyMatrix
            ? apiGet<StrategyMatrixResult>(
                `/auto-trader/research/strategy-matrix/result?market=${encodeURIComponent(mkt)}`
              )
            : Promise.resolve(nextStrategyMatrix ? { ok: true, result: nextStrategyMatrix } : null),
          shouldFetchMlMatrix
            ? apiGet<MlMatrixResult>(`/auto-trader/research/ml-matrix/result?market=${encodeURIComponent(mkt)}`)
            : Promise.resolve(nextMlMatrix ? { ok: true, result: nextMlMatrix } : null),
        ]);

        const [snapRes, mcRes, smRes, mmRes] = results;
        if (shouldFetchSnapshot && snapRes.status === "fulfilled") {
          nextResearchSnapshot = (snapRes.value as ResearchSnapshot) || null;
          setResearchSnapshot(nextResearchSnapshot);
        }
        if (shouldFetchModelCompare && mcRes.status === "fulfilled") {
          nextModelCompare = (mcRes.value as ModelCompareResult) || null;
          setModelCompare(nextModelCompare);
        }
        if (shouldFetchStrategyMatrix && smRes.status === "fulfilled") {
          nextStrategyMatrix = ((smRes.value as StrategyMatrixResult | null)?.result as StrategyMatrixPayload) || null;
          setStrategyMatrix(nextStrategyMatrix);
        }
        if (shouldFetchMlMatrix && mmRes.status === "fulfilled") {
          nextMlMatrix = ((mmRes.value as MlMatrixResult | null)?.result as MlMatrixPayload) || null;
          setMlMatrix(nextMlMatrix);
        }
      } catch {
        // 保留上次缓存/已展示数据，避免切页后出现“整块清空再加载”
      }
      let nextAbMarkdown = latest.abMarkdown;
      if (!heavyTaskRunning && (force || panels.abPanelOpen)) {
        try {
          const md = await apiGet<FactorABMarkdownResult>("/auto-trader/research/ab-report/markdown");
          nextAbMarkdown = String(md?.markdown || "");
          setAbMarkdown(nextAbMarkdown);
        } catch {
          // 保留上次 markdown，避免后台繁忙时反复清空
        }
      }
      writeResearchUiCache(
        trimResearchCache({
          cfg: nextCfg,
          researchStatus: nextResearchStatus,
          researchSnapshot: nextResearchSnapshot,
          modelCompare: nextModelCompare,
          strategyMatrix: nextStrategyMatrix,
          mlMatrix: nextMlMatrix,
          abMarkdown: nextAbMarkdown,
        })
      );
      if (!retainError) {
        setError("");
      }
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      loadingRef.current = false;
    }
  }, [researchRunOptions.run_openbb, resultTab]);

  const onResearchPanelToggle = useCallback(
    (setter: (open: boolean) => void, sectionKey: string) => (e: SyntheticEvent<HTMLDetailsElement>) => {
      if (e.target !== e.currentTarget) return;
      const open = e.currentTarget.open;
      setter(open);
      if (open) {
        const k = RESEARCH_PANEL_OVERRIDE_KEYS[sectionKey];
        const panelOverrides = k ? ({ [k]: true } as Partial<ResearchPanelOpenSnapshot>) : undefined;
        setSectionLoading((s) => ({ ...s, [sectionKey]: true }));
        void loadResearch(true, { panelOverrides }).finally(() =>
          setSectionLoading((s) => ({ ...s, [sectionKey]: false }))
        );
      }
    },
    [loadResearch]
  );

  useEffect(() => {
    void loadResearch(true);
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const scheduleNext = () => {
      if (disposed) return;
      const heavyRunning = strategyMatrixRunning || mlMatrixRunning || researchRunning;
      const visible = typeof document === "undefined" ? true : !document.hidden;
      const intervalMs = heavyRunning ? (visible ? 5000 : 15000) : visible ? 30000 : 120000;
      timer = setTimeout(async () => {
        await loadResearch(false);
        scheduleNext();
      }, intervalMs);
    };

    scheduleNext();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [loadResearch, strategyMatrixRunning, mlMatrixRunning, researchRunning]);

  useEffect(() => {
    writeSelectedResearchSymbolsCache(selectedResearchSymbols);
  }, [selectedResearchSymbols]);

  const syncStrongCandidatesFromAutoTrader = async () => {
    if (!cfg) return;
    setSyncingStrongCandidates(true);
    try {
      const limit = Math.max(30, Number(cfg.top_n || 8));
      const res = await apiGet<any>(
        `/auto-trader/strong-stocks?market=${encodeURIComponent(cfg.market)}&limit=${encodeURIComponent(
          String(limit)
        )}&kline=${encodeURIComponent(cfg.kline)}`,
        { timeoutMs: 20000, retries: 0 }
      );
      const items = Array.isArray(res?.items) ? res.items : [];
      const rows = items
        .map((x: any) => ({
          symbol: String(x?.symbol || "").trim().toUpperCase(),
          strength_score: typeof x?.strength_score === "number" ? x.strength_score : undefined,
          candidate_source: "strong",
        }))
        .filter((x: any) => Boolean(x.symbol));
      setStrongCandidates((prev) => mergeResearchCandidates(prev, rows));
      setStrongCandidatesMeta({
        source: String(res?.source || ""),
        scan_time: String(res?.scan_time || ""),
        worker_last_scan_summary_at: String(res?.worker_last_scan_summary_at || ""),
      });
      // 首次同步时给出默认勾选：前 top_n
      setSelectedResearchSymbols((prev) => {
        if (prev.length > 0) return prev;
        const defaults = rows.slice(0, Math.max(1, Number(cfg.top_n || 8))).map((x: any) => String(x.symbol));
        return Array.from(new Set(defaults));
      });
      setError("");
    } catch (e: any) {
      setError(`同步强势股失败: ${String(e?.message || e)}`);
    } finally {
      setSyncingStrongCandidates(false);
    }
  };

  const addManualResearchSymbols = (mode: "append" | "replace" = "append") => {
    const rows = parseResearchSymbolText(researchSymbolsInput, cfg?.market);
    if (!rows.length) {
      setError("请输入研究标的代码，例如 600519.SH、00700.HK、AAPL.US。");
      return;
    }
    const manualRows = rows.map((symbol) => ({
      symbol,
      strength_score: undefined,
      candidate_source: "manual",
    }));
    setStrongCandidates((prev) => (mode === "replace" ? manualRows : mergeResearchCandidates(prev, manualRows)));
    setSelectedResearchSymbols((prev) => (mode === "replace" ? rows : mergeResearchSymbols(prev, rows)));
    setResearchSymbolsInput("");
    setError("");
  };

  const importPublicResearchUniverse = async () => {
    if (!cfg) return;
    setImportingPublicUniverse(true);
    try {
      let symbols: string[] = [];
      let source = "config_universe";
      if (cfg.market === "cn") {
        const res = await apiGet<any>("/market-data/cn/universe?market=cn", { timeoutMs: 12000, retries: 0 });
        const items = Array.isArray(res?.items) ? res.items : [];
        symbols = items
          .map((x: any) => normalizeResearchSymbolInput(String(x?.symbol || ""), cfg.market))
          .filter(Boolean);
        source = String(res?.source || "cn_public_universe");
      }
      if (!symbols.length) {
        const cfgUniverse = cfg.universe?.[cfg.market] || [];
        symbols = cfgUniverse
          .map((x) => normalizeResearchSymbolInput(String(x || ""), cfg.market))
          .filter(Boolean);
        source = "auto_trader_config_universe";
      }
      const maxN = Math.max(1, Number(cfg.top_n || 8), 30);
      const picked = mergeResearchSymbols([], symbols).slice(0, maxN);
      if (!picked.length) {
        setError("当前市场还没有可导入的公共股票池，请先手工输入股票代码。");
        return;
      }
      const rows = picked.map((symbol) => ({
        symbol,
        strength_score: undefined,
        candidate_source: "public",
      }));
      setStrongCandidates((prev) => mergeResearchCandidates(prev, rows));
      setSelectedResearchSymbols((prev) => (prev.length ? mergeResearchSymbols(prev, picked) : picked));
      setStrongCandidatesMeta((prev) => ({
        ...prev,
        source,
      }));
      setError("");
    } catch (e: any) {
      setError(`导入公共股票池失败: ${String(e?.message || e)}`);
    } finally {
      setImportingPublicUniverse(false);
    }
  };

  const toggleResearchSymbol = (symbol: string, checked: boolean) => {
    const sym = String(symbol || "").trim().toUpperCase();
    if (!sym) return;
    setSelectedResearchSymbols((prev) => {
      if (checked) return prev.includes(sym) ? prev : [...prev, sym];
      return prev.filter((x) => x !== sym);
    });
  };

  const toggleResearchRunOption = (key: keyof ResearchRunOptions) => {
    if (researchStarting || researchRunning) return;
    setResearchRunOptions((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  useEffect(() => {
    const q: any = researchStatus?.task_queue;
    if (!q) return;
    const queued = Number(q?.queued ?? 0);
    const running = Number(q?.running ?? 0);
    if (queued + running > 0) return;
    // 后端队列已空时，自动清理前端本地“运行中”残留态。
    if (researchRunning) setResearchRunning(false);
    if (strategyMatrixRunning) setStrategyMatrixRunning(false);
    if (mlMatrixRunning) setMlMatrixRunning(false);
    if (researchTaskId) setResearchTaskId("");
    if (strategyMatrixTaskId) setStrategyMatrixTaskId("");
    if (mlMatrixTaskId) setMlMatrixTaskId("");
    if (researchProgress) setResearchProgress(null);
    if (strategyMatrixProgress) setStrategyMatrixProgress(null);
    if (mlMatrixProgress) setMlMatrixProgress(null);
  }, [
    researchStatus,
    researchRunning,
    strategyMatrixRunning,
    mlMatrixRunning,
    researchTaskId,
    strategyMatrixTaskId,
    mlMatrixTaskId,
    researchProgress,
    strategyMatrixProgress,
    mlMatrixProgress,
  ]);

  const runResearch = async () => {
    if (!cfg || researchRunning || researchStarting) return;
    setResearchStarting(true);
    setError("");
    let trackingTaskId = "";
    try {
      let accepted: any = null;
      try {
        accepted = await apiPost<any>(
          "/auto-trader/research/run",
          {
            market: cfg.market,
            kline: cfg.kline,
            top_n: cfg.top_n,
            backtest_days: cfg.backtest_days,
            symbols: selectedResearchSymbols.length ? selectedResearchSymbols : undefined,
            ...researchRunOptions,
            async_run: true,
          },
          {
            timeoutMs: 15000,
            retries: 0,
          }
        );
      } catch (startErr: any) {
        if (!isTransientRequestError(startErr)) throw startErr;
        const recovered = await recoverAcceptedTask("research");
        if (!recovered) throw startErr;
        accepted = recovered;
        setError("Research 启动请求超时，已自动接管后台任务并继续跟踪进度。");
      }
      trackingTaskId = String(accepted?.task_id || "").trim();
      if (trackingTaskId) {
        setResearchRunning(true);
        setResearchTaskId(trackingTaskId);
        setResearchProgress({
          taskId: trackingTaskId,
          status: "queued",
          progressPct: 0,
          progressStage: "queued",
          progressText: "任务排队中",
          queuePosition: 0,
          queueAhead: 0,
        });
      }
      void loadResearch(true).catch(() => {
        // 后台刷新失败时保留当前运行态，等待下一轮轮询自动恢复
      });
    } catch (e: any) {
      const queueMsg = mapQueueBusyError(e, "Research");
      setError(queueMsg || String(e.message || e));
      setResearchRunning(false);
      setResearchTaskId("");
      setResearchProgress(null);
    } finally {
      setResearchStarting(false);
    }
  };

  const cancelResearch = async () => {
    const taskId = String(researchTaskId || "").trim();
    if (!taskId) return;
    try {
      await apiPost<any>(`/auto-trader/research/tasks/${encodeURIComponent(taskId)}/cancel`, {});
      setError("已请求取消 Research 任务，稍后会停止。");
      setResearchProgress((prev) =>
        prev ? { ...prev, status: "cancelled", progressStage: "cancelled", progressText: "任务已取消" } : prev
      );
    } catch (e: any) {
      setError(`取消 Research 任务失败: ${String(e?.message || e)}`);
    }
  };

  const runStrategyMatrix = async () => {
    if (!cfg) return;
    let retainErr = false;
    let trackingTaskId = "";
    try {
      const preset = STRATEGY_MATRIX_PRESETS[strategyPreset] || STRATEGY_MATRIX_PRESETS.balanced;
      let accepted: any = null;
      try {
        accepted = await apiPost<any>(
          "/auto-trader/research/strategy-matrix/run",
          {
            market: cfg.market,
            top_n: preset.top_n,
            max_strategies: preset.max_strategies,
            max_drawdown_limit_pct: preset.max_drawdown_limit_pct,
            min_symbols_used: preset.min_symbols_used,
            matrix_overrides: preset.matrix_overrides,
            async_run: true,
          },
          {
            timeoutMs: 15000,
            retries: 0,
          }
        );
      } catch (startErr: any) {
        if (!isTransientRequestError(startErr)) throw startErr;
        const recovered = await recoverAcceptedTask("strategy_matrix");
        if (!recovered) throw startErr;
        accepted = recovered;
        retainErr = true;
        setError("策略矩阵启动请求超时，已自动接管后台任务并继续跟踪进度。");
      }
      if (accepted && accepted.accepted === false && accepted.message === "duplicate_task_reused") {
        retainErr = true;
        setError("已复用进行中的相同参数矩阵任务，未启动新任务；结果文件可能仍是上一次完成的快照。");
      }
      trackingTaskId = String(accepted?.task_id || "").trim();
      if (trackingTaskId) {
        setStrategyMatrixRunning(true);
        setStrategyMatrixTaskId(trackingTaskId);
        setStrategyMatrixProgress({
          taskId: trackingTaskId,
          status: "queued",
          progressPct: 0,
          progressStage: "queued",
          progressText: "任务排队中",
          queuePosition: 0,
          queueAhead: 0,
        });
      }
      await loadResearch(true, { retainError: retainErr });
      if (!retainErr) setError("");
      if (!trackingTaskId) {
        setStrategyMatrixRunning(false);
        setStrategyMatrixTaskId("");
        setStrategyMatrixProgress(null);
      }
    } catch (e: any) {
      retainErr = true;
      const queueMsg = mapQueueBusyError(e, "策略矩阵");
      setError(queueMsg || `策略参数矩阵运行失败: ${String(e?.message || e)}`);
      setStrategyMatrixRunning(false);
      setStrategyMatrixTaskId("");
      setStrategyMatrixProgress(null);
      await loadResearch(true, { retainError: true });
    }
  };

  const cancelStrategyMatrix = async () => {
    const taskId = String(strategyMatrixTaskId || "").trim();
    if (!taskId) return;
    try {
      await apiPost<any>(`/auto-trader/research/tasks/${encodeURIComponent(taskId)}/cancel`, {});
      setError("已请求取消矩阵任务，稍后会停止。");
      setStrategyMatrixProgress((prev) =>
        prev ? { ...prev, status: "cancelled", progressStage: "cancelled", progressText: "任务已取消" } : prev
      );
    } catch (e: any) {
      setError(`取消矩阵任务失败: ${String(e?.message || e)}`);
    }
  };

  const runMlMatrix = async () => {
    if (!cfg) return;
    let retainErr = false;
    let trackingTaskId = "";
    try {
      let accepted: any = null;
      try {
        accepted = await apiPost<any>(
          "/auto-trader/research/ml-matrix/run",
          {
            market: cfg.market,
            kline: cfg.kline,
            top_n: Math.max(6, cfg.top_n || 8),
            signal_bars_days: Math.max(300, cfg.signal_bars_days || 300),
            async_run: true,
            matrix_overrides: {
              model_type_choices: ["random_forest", "gbdt", "logreg"],
              ml_threshold_choices: [0.5, 0.53, 0.56, 0.6],
              ml_horizon_days_choices: [3, 5, 8],
              ml_train_ratio_choices: [0.65, 0.7, 0.75],
              ml_walk_forward_windows_choices: [4, 6],
            },
            constraints: {
              min_oos_samples: 200,
              min_coverage: 0.05,
              min_precision: 0.45,
              min_accuracy: 0.52,
            },
          },
          {
            timeoutMs: 15000,
            retries: 0,
          }
        );
      } catch (startErr: any) {
        if (!isTransientRequestError(startErr)) throw startErr;
        const recovered = await recoverAcceptedTask("ml_matrix");
        if (!recovered) throw startErr;
        accepted = recovered;
        retainErr = true;
        setError("ML矩阵启动请求超时，已自动接管后台任务并继续跟踪进度。");
      }
      trackingTaskId = String(accepted?.task_id || "").trim();
      if (trackingTaskId) {
        setMlMatrixRunning(true);
        setMlMatrixTaskId(trackingTaskId);
        setMlMatrixProgress({
          taskId: trackingTaskId,
          status: "queued",
          progressPct: 0,
          progressStage: "queued",
          progressText: "任务排队中",
          queuePosition: 0,
          queueAhead: 0,
        });
      }
      await loadResearch(true, { retainError: retainErr });
      if (!retainErr) setError("");
      if (!trackingTaskId) {
        setMlMatrixRunning(false);
        setMlMatrixTaskId("");
        setMlMatrixProgress(null);
      }
    } catch (e: any) {
      retainErr = true;
      const queueMsg = mapQueueBusyError(e, "ML矩阵");
      setError(queueMsg || `ML矩阵运行失败: ${String(e?.message || e)}`);
      setMlMatrixRunning(false);
      setMlMatrixTaskId("");
      setMlMatrixProgress(null);
      await loadResearch(true, { retainError: true });
    }
  };

  const cancelMlMatrix = async () => {
    const taskId = String(mlMatrixTaskId || "").trim();
    if (!taskId) return;
    try {
      await apiPost<any>(`/auto-trader/research/tasks/${encodeURIComponent(taskId)}/cancel`, {});
      setError("已请求取消ML矩阵任务，稍后会停止。");
      setMlMatrixProgress((prev) =>
        prev ? { ...prev, status: "cancelled", progressStage: "cancelled", progressText: "任务已取消" } : prev
      );
    } catch (e: any) {
      setError(`取消ML矩阵任务失败: ${String(e?.message || e)}`);
    }
  };

  const canApplyMlMatrix =
    (Boolean(mlMatrix?.ok) &&
      Boolean(
        (mlMatrix?.items && mlMatrix.items.length > 0) ||
          mlMatrix?.best_balanced ||
          mlMatrix?.best_high_precision ||
          mlMatrix?.best_high_coverage
      )) ||
    Boolean(mlApplySnapshotId && mlMatrixSnapshots.some((x) => String(x?.snapshot_id || "") === mlApplySnapshotId));
  const canApplyResearchAllocation = Boolean(
    researchSnapshot?.snapshot?.generated_at &&
      Array.isArray(researchSnapshot?.snapshot?.allocation_plan) &&
      researchSnapshot.snapshot.allocation_plan.length > 0
  );
  const canApplyStrategyMatrix = Boolean(
    strategyMatrix?.ok && Array.isArray(strategyMatrix?.items) && strategyMatrix.items.length > 0
  );

  const applyAutoTraderConfigPatch = async (
    patch: Record<string, unknown>,
    successMessage: string,
    busyKey: "research" | "strategy"
  ) => {
    setConfigApplyBusy(busyKey);
    setConfigApplyMessage("");
    try {
      const res = await apiPost<any>("/auto-trader/config", patch, { timeoutMs: 20000, retries: 0 });
      setError("");
      setConfigApplyMessage(String(res?.message || successMessage));
      await loadResearch(true, { retainError: true });
    } catch (e: any) {
      setError(`应用到 AutoTrader 失败: ${String(e?.message || e)}`);
    } finally {
      setConfigApplyBusy("");
    }
  };

  const openResearchAllocationPreview = () => {
    if (!canApplyResearchAllocation) return;
    setConfigApplyPreview({
      kind: "research",
      title: "应用 Research 分配",
      description: "开启 allocation_plan 仓位裁剪，并默认使用最新 Research 快照。",
      sourceLabel: `最新快照 · ${formatTime(researchSnapshotData?.generated_at)}`,
      patch: {
        research_allocation_enabled: true,
        research_allocation_snapshot_id: "",
        research_allocation_max_age_minutes: 10080,
        research_allocation_notional_scale: 1,
      },
      successMessage: "已开启 Research 分配并应用最新快照。",
    });
  };

  const openStrategyMatrixPreview = () => {
    if (!canApplyStrategyMatrix) return;
    setConfigApplyPreview({
      kind: "strategy",
      title: "并入策略矩阵前 3",
      description: "将 matrix_score 排名前 3 的策略变体并入 AutoTrader 扫描评分。",
      sourceLabel: `最新策略矩阵 · ${formatTime(strategyMatrix?.generated_at)}`,
      patch: {
        merge_strategy_matrix_top3: true,
        merge_strategy_matrix_top3_snapshot_id: "",
      },
      successMessage: "已开启策略矩阵优选前 3 并入 AutoTrader 扫描评分。",
    });
  };

  const openMlMatrixPreview = async () => {
    if (!canApplyMlMatrix) return;
    let sourceMatrix: MlMatrixPayload | null | undefined = mlMatrix;
    const snap = mlApplySnapshotId
      ? mlMatrixSnapshots.find((x) => String(x?.snapshot_id || "") === mlApplySnapshotId)
      : null;
    if (mlApplySnapshotId && cfg?.market) {
      try {
        sourceMatrix = await apiGet<MlMatrixPayload>(
          `/auto-trader/research/snapshots/ml_matrix/${encodeURIComponent(mlApplySnapshotId)}?market=${encodeURIComponent(
            cfg.market
          )}`,
          { timeoutMs: 12000, retries: 0 }
        );
      } catch (e: any) {
        setError(`读取 ML 快照失败: ${String(e?.message || e)}`);
        return;
      }
    }
    const { row, source } = resolveMlMatrixRowForPreview(sourceMatrix, mlApplyVariant);
    const patch = mlMatrixRowToConfigPatch(row, true);
    if (!row || Object.keys(patch).length <= 1) {
      setError("没有可应用的 ML 矩阵行，请先运行 ML 矩阵或更换应用策略。");
      return;
    }
    setConfigApplyPreview({
      kind: "ml",
      title: "应用 ML 最优参数",
      description: "写入 ML 模型、阈值与 walk-forward 参数，并开启 ML 过滤。",
      sourceLabel: snap
        ? `${formatSnapshotHistoryLabel(snap)} · ${source || mlApplyVariant}`
        : `当前最新 ML 矩阵 · ${formatTime(mlMatrix?.generated_at)} · ${source || mlApplyVariant}`,
      patch,
      successMessage: "已合并 ML 参数到自动交易配置。",
      mlVariant: mlApplyVariant,
      mlSnapshotId: mlApplySnapshotId,
    });
  };

  const confirmApplyPreview = async () => {
    const preview = configApplyPreview;
    if (!preview) return;
    if (preview.kind === "ml") {
      setMlApplyBusy(true);
      try {
        const res = await apiPost<any>("/auto-trader/research/ml-matrix/apply-to-config", {
          variant: preview.mlVariant || mlApplyVariant,
          enable_ml_filter: true,
          snapshot_id: preview.mlSnapshotId || undefined,
        });
        setError("");
        const src = String(res?.applied_from || "");
        const msg = String(res?.message || preview.successMessage);
        setConfigApplyMessage(src ? `来源 ${src} · ${msg}` : msg);
        setConfigApplyPreview(null);
        await loadResearch(true, { retainError: true });
      } catch (e: any) {
        let detail = String(e?.message || e);
        try {
          const j = JSON.parse(detail);
          const d = j?.detail;
          if (typeof d === "string") detail = d;
          else if (d && typeof d === "object") detail = JSON.stringify(d, null, 2);
        } catch {
          /* keep */
        }
        setError(`应用 ML 配置失败: ${detail}`);
      } finally {
        setMlApplyBusy(false);
      }
      return;
    }
    await applyAutoTraderConfigPatch(preview.patch, preview.successMessage, preview.kind);
    setConfigApplyPreview(null);
  };

  const exportResearchSnapshot = () => {
    const payload = researchSnapshot || { has_snapshot: false, snapshot: null };
    try {
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `auto-trader-research-snapshot-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setError(`导出研究快照失败: ${String(e?.message || e)}`);
    }
  };

  const exportModelCompareCsv = () => {
    try {
      const rows = modelRows || [];
      if (!rows.length) {
        setError("暂无模型对比数据可导出，请先执行一次 Research。");
        return;
      }
      const header = ["模型名称", "运行次数", "平均分", "平均Acc", "最佳分"];
      const escapeCell = (v: unknown): string => {
        const s = String(v ?? "");
        if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
        return s;
      };
      const body = rows.map((x) =>
        [
          escapeCell(x.model_name ?? ""),
          escapeCell(x.runs ?? ""),
          escapeCell(x.avg_score ?? ""),
          escapeCell(x.avg_accuracy ?? ""),
          escapeCell(x.best_score ?? ""),
        ].join(",")
      );
      const csv = [header.join(","), ...body].join("\n");
      const blob = new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `auto-trader-model-compare-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      setError("");
    } catch (e: any) {
      setError(`导出模型对比CSV失败: ${String(e?.message || e)}`);
    }
  };

  const exportPairTradeCsv = () => {
    try {
      const pairKey = pairTradeFilterPair.trim().toUpperCase();
      const symbolKey = pairTradeFilterSymbol.trim().toUpperCase();
      const allTradeRows = buildPairTradeRows(selectedPairRows);
      const rows = allTradeRows.filter((r: any) => {
        const pairOk = !pairKey || String(r?.pair || "").toUpperCase().includes(pairKey);
        const symOk = !symbolKey || String(r?.symbol || "").toUpperCase().includes(symbolKey);
        return pairOk && symOk;
      });
      if (!rows.length) {
        setError("当前筛选条件下没有可导出的交易明细。");
        return;
      }
      const header = ["配对", "入选标的", "策略", "买入时间", "卖出时间", "买入价", "卖出价", "数量", "收益%", "收益额", "持有天数"];
      const escapeCell = (v: unknown): string => {
        const s = String(v ?? "");
        if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
        return s;
      };
      const body = rows.map((r: any) =>
        [
          escapeCell(r.pair),
          escapeCell(r.symbol),
          escapeCell(r.strategy),
          escapeCell(r.entry_date),
          escapeCell(r.exit_date),
          escapeCell(r.entry_price),
          escapeCell(r.exit_price),
          escapeCell(r.quantity),
          escapeCell(r.pnl_pct),
          escapeCell(r.pnl),
          escapeCell(r.hold_days),
        ].join(",")
      );
      const csv = [header.join(","), ...body].join("\n");
      const blob = new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `pair-trade-history-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      setError("");
    } catch (e: any) {
      setError(`导出组合交易明细CSV失败: ${String(e?.message || e)}`);
    }
  };

  const exportAbReportJson = () => {
    try {
      const payload = researchSnapshotData?.factor_ab_report || null;
      if (!payload) {
        setError("暂无 A/B 报告数据可导出，请先执行一次 Research。");
        return;
      }
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `auto-trader-ab-report-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setError("");
    } catch (e: any) {
      setError(`导出A/B报告JSON失败: ${String(e?.message || e)}`);
    }
  };

  const exportAbReportMarkdown = () => {
    try {
      const text = String(abMarkdown || "").trim();
      if (!text) {
        setError("暂无 A/B 报告 Markdown 可导出，请先执行一次 Research。");
        return;
      }
      const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `auto-trader-ab-report-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.md`;
      a.click();
      URL.revokeObjectURL(url);
      setError("");
    } catch (e: any) {
      setError(`导出A/B报告Markdown失败: ${String(e?.message || e)}`);
    }
  };
  const buildTradingAgentsReportMarkdown = (row: any, index: number) => {
    const fullReport = String(row?.research_report_markdown || "").trim();
    if (fullReport) {
      return fullReport;
    }
    const symbol = String(row?.symbol || "-");
    const requestSymbol = String(row?.request_symbol || symbol);
    const market = String(row?.market || "-");
    const source = String(row?.source || "tradingagents");
    const available = row?.available ? "yes" : "no";
    const action = String(row?.action || "-").toUpperCase();
    const confidence =
      typeof row?.confidence === "number" ? Number(row.confidence).toFixed(4) : "-";
    const reason = String(row?.reason || "-");
    const errorDetail = String(row?.error || "");
    const generatedAt = String(row?.generated_at || "");
    const decisionText = String(row?.decision_text || "").trim();
    return [
      `# TradingAgents 研究过程报告`,
      ``,
      `- 行号: ${index + 1}`,
      `- Symbol: ${symbol}`,
      `- Request Symbol: ${requestSymbol}`,
      `- Market: ${market}`,
      `- Source: ${source}`,
      `- Available: ${available}`,
      `- Action: ${action}`,
      `- Confidence: ${confidence}`,
      `- Reason: ${reason}`,
      `- Generated At: ${generatedAt || "-"}`,
      `- Error: ${errorDetail || "-"}`,
      ``,
      `## 决策原文`,
      ``,
      decisionText || "（无原文，可能是超时或上游失败降级）",
      ``,
    ].join("\n");
  };
  const downloadTradingAgentsReport = (row: any, index: number) => {
    try {
      const symbol = String(row?.symbol || "unknown").replace(/[^\w.-]+/g, "_");
      const report = buildTradingAgentsReportMarkdown(row, index);
      const blob = new Blob([report], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `tradingagents-report-${symbol}-${new Date()
        .toISOString()
        .slice(0, 19)
        .replace(/[:T]/g, "-")}.md`;
      a.click();
      URL.revokeObjectURL(url);
      setError("");
    } catch (e: any) {
      setError(`下载 TradingAgents 报告失败: ${String(e?.message || e)}`);
    }
  };
  const formatTradingAgentsReason = (row: any) => {
    const reason = String(row?.reason || "").trim();
    if (reason === "tradingagents_rate_limited") {
      const retryAfter = Number(row?.retry_after_seconds || 0);
      return retryAfter > 0
        ? `上游限流(429)，建议 ${retryAfter}s 后重试`
        : "上游限流(429)，建议稍后重试";
    }
    if (reason === "tradingagents_rate_limited_cooldown") {
      const retryAfter = Number(row?.retry_after_seconds || 0);
      return retryAfter > 0
        ? `限流冷却中，约 ${retryAfter}s 后再试`
        : "限流冷却中，请稍后再试";
    }
    return reason || String(row?.decision_text || "-");
  };
  const exportTradingAgentsReportsJson = () => {
    try {
      if (!tradingagentsInsights.length) {
        setError("暂无 TradingAgents 报告可导出，请先执行一次 Research。");
        return;
      }
      const reports = tradingagentsInsights.map((row: any, idx: number) => ({
        index: idx + 1,
        symbol: String(row?.symbol || "-"),
        request_symbol: String(row?.request_symbol || row?.symbol || "-"),
        market: String(row?.market || "-"),
        source: String(row?.source || "tradingagents"),
        available: Boolean(row?.available),
        action: String(row?.action || "-").toUpperCase(),
        confidence: typeof row?.confidence === "number" ? Number(row.confidence) : null,
        reason: String(row?.reason || ""),
        error: String(row?.error || ""),
        generated_at: String(row?.generated_at || ""),
        decision_text: String(row?.decision_text || ""),
        stage_reports: row?.stage_reports && typeof row.stage_reports === "object" ? row.stage_reports : {},
        report_markdown: buildTradingAgentsReportMarkdown(row, idx),
      }));
      const payload = {
        generated_at: new Date().toISOString(),
        count: reports.length,
        reports,
      };
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `tradingagents-reports-${new Date()
        .toISOString()
        .slice(0, 19)
        .replace(/[:T]/g, "-")}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setError("");
    } catch (e: any) {
      setError(`批量导出 TradingAgents 报告失败: ${String(e?.message || e)}`);
    }
  };
  const researchSnapshotData = researchSnapshot?.snapshot;
  const loadSnapshotCompare = useCallback(
    async (typeArg?: SnapshotHistoryType, pairArg?: { left: string; right: string }) => {
      const type = typeArg || snapshotHistoryType;
      const pair = pairArg || historyCompareIds[type];
      const leftId = String(pair?.left || "").trim();
      const rightId = String(pair?.right || "").trim();
      if (!cfg?.market || !leftId || !rightId) return;
      setHistoryCompareLoading(true);
      try {
        const [left, right] = await Promise.all([
          apiGet<any>(
            `/auto-trader/research/snapshots/${encodeURIComponent(type)}/${encodeURIComponent(leftId)}?market=${encodeURIComponent(
              cfg.market
            )}`,
            { timeoutMs: 12000, retries: 0 }
          ),
          apiGet<any>(
            `/auto-trader/research/snapshots/${encodeURIComponent(type)}/${encodeURIComponent(rightId)}?market=${encodeURIComponent(
              cfg.market
            )}`,
            { timeoutMs: 12000, retries: 0 }
          ),
        ]);
        setHistoryComparePayload({ type, leftId, rightId, left, right });
        setError("");
      } catch (e: any) {
        setError(`加载历史快照对比失败: ${String(e?.message || e)}`);
      } finally {
        setHistoryCompareLoading(false);
      }
    },
    [cfg?.market, historyCompareIds, snapshotHistoryType]
  );
  const strongRows = Array.isArray(researchSnapshotData?.strong_stocks) ? researchSnapshotData.strong_stocks : [];
  const allocationRows = researchSnapshotData?.allocation_plan || [];
  const modelRows = modelCompare?.items || [];
  const matrixRows = strategyMatrix?.items || [];
  const matrixBestBalanced = strategyMatrix?.best_balanced || null;
  const mlMatrixRows = mlMatrix?.items || [];
  const mlMatrixBestBalanced = mlMatrix?.best_balanced || null;
  const strategyRows = researchSnapshotData?.strategy_rankings || [];
  const matrixTopSymbolRows = useMemo(() => {
    if (!matrixRows.length) return [];
    const flat = matrixRows.flatMap((row) => {
      const tops = Array.isArray(row?.top_symbols) ? row.top_symbols : [];
      return tops.map((x) => ({
        symbol: String(x?.symbol || "").trim().toUpperCase(),
        net_return_pct: Number(x?.net_return_pct ?? Number.NEGATIVE_INFINITY),
        strategy: row?.strategy || "-",
        strategy_label: row?.strategy_label || row?.strategy || "-",
      }));
    });
    flat.sort((a, b) => Number(b.net_return_pct) - Number(a.net_return_pct));
    const picked: Array<{ symbol: string; net_return_pct: number; strategy: string; strategy_label: string }> = [];
    const seen = new Set<string>();
    for (const row of flat) {
      const sym = String(row.symbol || "");
      if (!sym || seen.has(sym)) continue;
      seen.add(sym);
      picked.push(row);
      if (picked.length >= 5) break;
    }
    return picked;
  }, [matrixRows]);
  const strategySummaryRows = useMemo(
    () =>
    matrixTopSymbolRows.length
      ? matrixTopSymbolRows.map((x) => ({
          symbol: x?.symbol || "-",
          best_strategy: {
            strategy: x?.strategy || "-",
            strategy_label: x?.strategy_label || x?.strategy || "-",
            composite_score: x?.net_return_pct,
          },
        }))
      : strategyRows,
    [matrixTopSymbolRows, strategyRows]
  );
  const pairBacktest = researchSnapshotData?.pair_backtest || null;
  const openbbHealthPayload = researchRunOptions.run_openbb ? openbbHealth?.health || {} : {};
  const providerStatus = {
    ...(researchSnapshotData?.data_providers || {}),
    ...(researchStatus?.data_providers || {}),
    ...(openbbHealthPayload
      ? {
          primary: researchStatus?.data_providers?.primary || researchSnapshotData?.data_providers?.primary || "longport",
          openbb_enabled: Boolean(openbbHealthPayload.enabled),
          openbb_connected: Boolean(openbbHealthPayload.ok),
          openbb_base_url: String(openbbHealthPayload.base_url || ""),
          cn_public_data: researchStatus?.data_providers?.cn_public_data || researchSnapshotData?.data_providers?.cn_public_data,
        }
      : {}),
  };
  const cnPublicData = providerStatus?.cn_public_data || {};
  const cnProviderNames = Array.isArray(cnPublicData?.providers)
    ? cnPublicData.providers
        .filter((p: any) => p?.enabled && p?.ready)
        .map((p: any) => String(p?.id || p?.name || ""))
        .filter(Boolean)
    : [];
  const cnLatestNewsDiag = Array.isArray(cnPublicData?.latest_news_diagnostics)
    ? cnPublicData.latest_news_diagnostics
    : [];
  const cnLatestNoticeCount = cnLatestNewsDiag
    .filter((x: any) => String(x?.source || "").includes("notice") || String(x?.source || "").includes("disclosure"))
    .reduce((sum: number, x: any) => sum + (Number(x?.count) || 0), 0);
  const externalResearch = researchSnapshotData?.external_research || {};
  const regimeGating = researchSnapshotData?.regime_gating || {};
  const factorGating = researchSnapshotData?.factor_gating || {};
  const agentGating = researchSnapshotData?.agent_gating || {};
  const mlDiag = researchSnapshotData?.ml_diagnostics || {};
  const regimeInfo = externalResearch?.market_regime || {};
  const externalFactors = externalResearch?.symbol_factors || [];
  const tradingagentsInsights = Array.isArray(externalResearch?.tradingagents_insights)
    ? externalResearch.tradingagents_insights
    : [];
  const tradingagentsAvailableCount = tradingagentsInsights.filter((x: any) => Boolean(x?.available)).length;
  const abReport = researchSnapshotData?.factor_ab_report || null;
  const abSummary = abReport?.summary || {};
  const abItems = abReport?.items || [];
  const mlDiagModels = mlDiag?.models || [];
  const selectedPairRows = Array.isArray(pairBacktest?.selected_pairs) ? pairBacktest.selected_pairs : [];
  const pairPoolUsedFromSnapshot = researchSnapshotData?.pair_pool_used || [];
  const pairPoolUsed = useMemo(
    () =>
    pairPoolUsedFromSnapshot.length > 0
      ? pairPoolUsedFromSnapshot
      : Array.from(
          new Map(
            selectedPairRows
              .map((row: any) => {
                const longSym = String(row?.pair?.long || "").trim();
                const shortSym = String(row?.pair?.short || "").trim();
                if (!longSym || !shortSym) return null;
                return [`${longSym}=>${shortSym}`, { long_symbol: longSym, short_symbol: shortSym }] as const;
              })
              .filter(Boolean) as Array<readonly [string, { long_symbol: string; short_symbol: string }]>
          ).values()
        ),
    [pairPoolUsedFromSnapshot, selectedPairRows]
  );
  const pairTradeTotalCount = useMemo(
    () =>
      selectedPairRows.reduce((acc: number, row: any) => {
        const trades = Array.isArray(row?.selected_metrics?.trade_history) ? row.selected_metrics.trade_history : [];
        return acc + trades.length;
      }, 0),
    [selectedPairRows]
  );
  const pairTradeRows = useMemo(
    () => buildPairTradeRows(selectedPairRows, MAX_FILTER_SCAN_PAIR_TRADE_ROWS),
    [buildPairTradeRows, selectedPairRows]
  );
  const filteredPairTradeRows = useMemo(() => {
    const pairKey = pairTradeFilterPair.trim().toUpperCase();
    const symbolKey = pairTradeFilterSymbol.trim().toUpperCase();
    return pairTradeRows.filter((r: any) => {
      const pairOk = !pairKey || String(r?.pair || "").toUpperCase().includes(pairKey);
      const symOk = !symbolKey || String(r?.symbol || "").toUpperCase().includes(symbolKey);
      return pairOk && symOk;
    });
  }, [pairTradeRows, pairTradeFilterPair, pairTradeFilterSymbol]);
  const visibleAllocationRows = useMemo(() => allocationRows.slice(0, MAX_RENDER_ALLOC_ROWS), [allocationRows]);
  const visibleStrongRows = useMemo(() => strongRows.slice(0, MAX_RENDER_STRONG_ROWS), [strongRows]);
  const visiblePairPoolRows = useMemo(() => pairPoolUsed.slice(0, MAX_RENDER_PAIR_POOL_ROWS), [pairPoolUsed]);
  const visibleSelectedPairRows = useMemo(
    () => selectedPairRows.slice(0, MAX_RENDER_SELECTED_PAIR_ROWS),
    [selectedPairRows]
  );
  const visibleMlDiagModels = useMemo(() => mlDiagModels.slice(0, MAX_RENDER_ML_DIAG_ROWS), [mlDiagModels]);
  const visibleFilteredPairTradeRows = useMemo(
    () => filteredPairTradeRows.slice(0, MAX_RENDER_PAIR_TRADE_ROWS),
    [filteredPairTradeRows]
  );
  const showResearchProgress = Boolean(researchRunning && (researchTaskId || researchProgress));
  const showStrategyProgress = Boolean(strategyMatrixRunning && (strategyMatrixTaskId || strategyMatrixProgress));
  const showMlProgress = Boolean(mlMatrixRunning && (mlMatrixTaskId || mlMatrixProgress));
  const resultTabs: Array<{ key: ResearchResultTab; label: string; badge?: string | number }> = [
    { key: "overview", label: "概览" },
    { key: "research", label: "Research 快照", badge: strongRows.length || undefined },
    { key: "strategy", label: "策略矩阵", badge: matrixRows.length || undefined },
    { key: "ml", label: "ML 矩阵", badge: mlMatrixRows.length || undefined },
    { key: "pair", label: "组合回测", badge: selectedPairRows.length || undefined },
    { key: "export", label: "导出" },
  ];
  const selectedResearchOptionLabels = [
    researchRunOptions.run_openbb ? "OpenBB" : "",
    researchRunOptions.run_tradingagents ? "TradingAgents" : "",
    researchRunOptions.run_pair_backtest ? "组合回测" : "",
    researchRunOptions.run_ml_diagnostics ? "轻量 ML" : "",
  ].filter(Boolean);
  const activeTaskCount = Number(researchStatus?.task_queue?.active ?? 0) || 0;
  const queuedTaskCount = Number(researchStatus?.task_queue?.queued ?? 0) || 0;
  const runningTaskCount = Number(researchStatus?.task_queue?.running ?? 0) || 0;
  const topAllocationRows = useMemo(() => {
    const rows = Array.isArray(allocationRows) ? allocationRows : [];
    return [...rows]
      .sort((a, b) => Number(b?.weight ?? b?.weight_raw ?? 0) - Number(a?.weight ?? a?.weight_raw ?? 0))
      .slice(0, 5);
  }, [allocationRows]);
  const topStrongRows = useMemo(() => {
    const rows = Array.isArray(strongRows) ? strongRows : [];
    return [...rows]
      .sort((a, b) => Number(b?.strength_score ?? 0) - Number(a?.strength_score ?? 0))
      .slice(0, 5);
  }, [strongRows]);
  const pickedMlMatrixRow =
    mlMatrixBestBalanced ||
    mlMatrix?.best_high_precision ||
    mlMatrix?.best_high_coverage ||
    (Array.isArray(mlMatrixRows) ? mlMatrixRows[0] : null) ||
    null;
  const pairPortfolioEstimate =
    pairBacktest && typeof pairBacktest === "object" ? (pairBacktest as any).portfolio_estimate || {} : {};
  const overviewHasAnyResult = Boolean(
    researchSnapshotData?.generated_at || strategyMatrix?.generated_at || mlMatrix?.generated_at
  );
  const configDiffRows = useMemo(
    () => (configApplyPreview ? buildConfigDiffRows(autoTraderConfig, configApplyPreview.patch) : []),
    [autoTraderConfig, configApplyPreview]
  );
  const historyRowsForType = snapshotHistories[snapshotHistoryType] || [];
  const historyPair = historyCompareIds[snapshotHistoryType] || { left: "", right: "" };
  const historyCompareRows = useMemo(() => {
    if (!historyComparePayload) return [];
    if (historyComparePayload.type !== snapshotHistoryType) return [];
    return buildSnapshotCompareRows(snapshotHistoryType, historyComparePayload.left, historyComparePayload.right);
  }, [historyComparePayload, snapshotHistoryType]);
  const renderTaskProgress = (x: TaskProgress | null, fallbackTaskId: string) => {
    if (!x && !fallbackTaskId) return null;
    const pct = Math.max(0, Math.min(100, Math.round(Number(x?.progressPct ?? 0))));
    const taskId = x?.taskId || fallbackTaskId;
    const text = x?.progressText || (x?.status === "queued" ? "任务排队中" : "任务运行中");
    const stage = String(x?.progressStage || x?.status || "running");
    const queueHint =
      stage === "queued" && Number(x?.queuePosition || 0) > 0
        ? ` · 队列第${Number(x?.queuePosition)}（前方${Number(x?.queueAhead || 0)}）`
        : "";
    return (
      <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
        <div className="mb-1 flex items-center justify-between">
          <span className="truncate pr-2">{text}</span>
          <span className="font-mono text-cyan-300">{pct}%</span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded bg-slate-800">
          <div
            className="h-full rounded bg-gradient-to-r from-cyan-500 to-emerald-500 transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
        <div className="mt-1 text-[11px] text-slate-500">
          {taskId ? `任务ID: ${taskId} · ` : ""}阶段: {stage}
          {queueHint}
        </div>
      </div>
    );
  };
  return (
    <div className="space-y-3">
      {error ? (
        <div className="panel border-rose-200 bg-rose-50 text-rose-700">
          <div className={SUB_TITLE_CLS}>错误信息</div>
          <div className="mt-1 text-sm">{error}</div>
        </div>
      ) : null}
      <div className="rounded-lg border border-indigo-500/30 bg-indigo-950/30 p-3 text-xs text-slate-300">
        研究任务参数取自{" "}
        <Link className="text-cyan-300 underline hover:text-cyan-200" href="/auto-trading/stocks">
          Auto Trader
        </Link>
        {" "}
        当前保存的配置（市场、K线、TopN、回测天数、信号天数等）。修改后请在 Auto Trader 页面保存配置。
      </div>
<div className="panel space-y-4">
  <div className="flex flex-wrap items-start justify-between gap-3">
    <div>
      <div className={PANEL_TITLE_CLS}>研究中心（P0 / P1 / P2）</div>
      <div className="mt-1 text-xs text-slate-400">
        当前配置：{(cfg?.market ?? researchStatus?.market ?? "-").toString().toUpperCase()} /{" "}
        {(cfg?.kline ?? researchStatus?.kline ?? "-").toString().toUpperCase()} · TopN{" "}
        {cfg?.top_n ?? researchStatus?.top_n ?? "-"}
      </div>
    </div>
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="rounded border border-slate-700/70 bg-slate-900/60 px-2.5 py-1 text-slate-300">
        队列 {activeTaskCount} / {researchStatus?.task_queue?.max_pending ?? "-"}
      </span>
      <span className="rounded border border-slate-700/70 bg-slate-900/60 px-2.5 py-1 text-slate-300">
        运行 {runningTaskCount} · 排队 {queuedTaskCount}
      </span>
    </div>
  </div>

  <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
    <div className="rounded-lg border border-indigo-500/30 bg-slate-900/55 p-3 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-slate-100">生成 Research 快照</div>
          <div className="mt-1 text-xs text-slate-400">综合扫描、策略评分、仓位建议与外部研究。</div>
        </div>
        <span className="rounded border border-indigo-400/30 bg-indigo-400/10 px-2 py-0.5 text-[11px] text-indigo-200">
          快照
        </span>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {selectedResearchOptionLabels.length ? (
          selectedResearchOptionLabels.map((label) => (
            <span key={label} className="rounded border border-cyan-400/30 bg-cyan-400/10 px-2 py-0.5 text-[11px] text-cyan-200">
              {label}
            </span>
          ))
        ) : (
          <span className="rounded border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 text-[11px] text-amber-200">
            未选择运行项
          </span>
        )}
      </div>
      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {[
          ["run_openbb", "OpenBB 增强", "市场状态 + 外部因子", "适用于美股"],
          ["run_tradingagents", "TradingAgents", "多智能体研判", ""],
          ["run_pair_backtest", "组合回测快照", "Pair Pool 回测", ""],
          ["run_ml_diagnostics", "轻量 ML 诊断", "模型诊断", ""],
        ].map(([key, label, hint, badge]) => {
          const k = key as keyof ResearchRunOptions;
          return (
            <label
              key={key}
              className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-2 text-xs ${
                researchRunOptions[k]
                  ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-100"
                  : "border-slate-700/80 bg-slate-950/35 text-slate-300"
              } ${researchStarting || researchRunning ? "cursor-not-allowed opacity-60" : ""}`}
            >
              <input
                className="mt-0.5 h-3.5 w-3.5 accent-cyan-400"
                type="checkbox"
                checked={researchRunOptions[k]}
                disabled={researchStarting || researchRunning}
                onChange={() => toggleResearchRunOption(k)}
              />
              <span className="min-w-0">
                <span className="flex flex-wrap items-center gap-1.5 font-medium">
                  <span>{label}</span>
                  {badge ? (
                    <span className="rounded border border-cyan-400/40 bg-cyan-400/10 px-1.5 py-0.5 text-[10px] font-medium text-cyan-200">
                      {badge}
                    </span>
                  ) : null}
                </span>
                <span className="mt-0.5 block text-[11px] text-slate-400">{hint}</span>
              </span>
            </label>
          );
        })}
      </div>
      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-[auto_1fr]">
        <button
          type="button"
          className="rounded-lg border border-slate-600/70 bg-slate-800/60 px-3 py-2 text-sm text-slate-200 hover:bg-slate-700/70 disabled:opacity-50"
          onClick={() => setResearchRunOptions(DEFAULT_RESEARCH_RUN_OPTIONS)}
          disabled={researchStarting || researchRunning}
        >
          全选运行项
        </button>
        <button
          className="w-full rounded-lg bg-gradient-to-r from-indigo-600 to-violet-600 px-3 py-2 text-sm font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
          onClick={runResearch}
          disabled={researchStarting || researchRunning || !cfg}
        >
          {researchStarting
            ? "启动中..."
            : researchRunning
              ? `运行中${researchTaskId ? ` (${researchTaskId})` : "..."}`
              : "运行 Research"}
        </button>
      </div>
    </div>

    <div className="rounded-lg border border-emerald-500/30 bg-slate-900/55 p-3 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-slate-100">优化策略参数</div>
          <div className="mt-1 text-xs text-slate-400">批量测试策略参数，筛出当前标的池更稳的组合。</div>
        </div>
        <span className="rounded border border-emerald-400/30 bg-emerald-400/10 px-2 py-0.5 text-[11px] text-emerald-200">
          策略矩阵
        </span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-300">
        <div className="rounded border border-slate-700/70 bg-slate-950/35 p-2">
          预设：<span className="text-emerald-300">{STRATEGY_MATRIX_PRESETS[strategyPreset]?.label || "-"}</span>
        </div>
        <div className="rounded border border-slate-700/70 bg-slate-950/35 p-2">
          最近结果：<span className="text-slate-200">{formatTime(strategyMatrix?.generated_at)}</span>
        </div>
      </div>
      <select
        className={`${INPUT_CLS} mt-3 w-full`}
        value={strategyPreset}
        onChange={(e) => setStrategyPreset(e.target.value as StrategyMatrixPresetKey)}
        title="策略矩阵筛选预设"
        disabled={strategyMatrixRunning || !cfg}
      >
        <option value="conservative">保守（最快）</option>
        <option value="balanced">平衡（推荐）</option>
        <option value="aggressive">激进（更全面）</option>
      </select>
      <button
        className="mt-3 w-full rounded-lg bg-gradient-to-r from-emerald-600 to-teal-600 px-3 py-2 text-sm font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
        onClick={runStrategyMatrix}
        disabled={strategyMatrixRunning || !cfg}
      >
        {strategyMatrixRunning ? "策略矩阵运行中..." : "运行策略矩阵"}
      </button>
    </div>

    <div className="rounded-lg border border-sky-500/30 bg-slate-900/55 p-3 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-slate-100">优化 ML 参数</div>
          <div className="mt-1 text-xs text-slate-400">测试模型、阈值、窗口与覆盖率，产出 ML 过滤参数。</div>
        </div>
        <span className="rounded border border-sky-400/30 bg-sky-400/10 px-2 py-0.5 text-[11px] text-sky-200">
          ML 矩阵
        </span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-300">
        <div className="rounded border border-slate-700/70 bg-slate-950/35 p-2">
          结果：<span className="text-sky-300">{mlMatrixRows.length || 0}</span>
        </div>
        <div className="rounded border border-slate-700/70 bg-slate-950/35 p-2">
          推荐：<span className="text-emerald-300">{mlMatrixBestBalanced?.params?.model_type || "-"}</span>
        </div>
      </div>
      <div className="mt-3 rounded border border-slate-700/70 bg-slate-950/35 p-2 text-xs text-slate-400">
        当前使用 {Math.max(300, Number(cfg?.signal_bars_days || 300))} 天信号数据，任务通常比 Research 更重。
      </div>
      <button
        className="mt-3 w-full rounded-lg bg-gradient-to-r from-sky-600 to-blue-700 px-3 py-2 text-sm font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
        onClick={runMlMatrix}
        disabled={mlMatrixRunning || !cfg}
      >
        {mlMatrixRunning ? "ML矩阵运行中..." : "运行 ML 矩阵"}
      </button>
    </div>
  </div>

  <details className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3">
    <summary className="cursor-pointer text-sm font-medium text-slate-200">
      自选研究标的（同步强势股 / 手工录入 / 公共股票池）
    </summary>
    <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-[1fr_auto]">
      <textarea
        className={`${INPUT_CLS} min-h-[76px] resize-y text-xs`}
        value={researchSymbolsInput}
        onChange={(e) => setResearchSymbolsInput(e.target.value)}
        placeholder={
          cfg?.market === "cn"
            ? "手工输入 A 股代码，例如：600519.SH, 300750.SZ, 603776.SH"
            : cfg?.market === "hk"
              ? "手工输入港股代码，例如：00700.HK, 09988.HK"
              : "手工输入美股代码，例如：AAPL.US, NVDA.US, TSLA.US"
        }
      />
      <div className="flex flex-row flex-wrap gap-2 md:flex-col md:items-stretch">
        <button
          className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-200 hover:bg-emerald-500/20 disabled:opacity-50"
          onClick={() => addManualResearchSymbols("append")}
          disabled={!researchSymbolsInput.trim()}
        >
          添加手工标的
        </button>
        <button
          className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-200 hover:bg-amber-500/20 disabled:opacity-50"
          onClick={() => addManualResearchSymbols("replace")}
          disabled={!researchSymbolsInput.trim()}
        >
          替换为手工标的
        </button>
        <button
          className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-3 py-1.5 text-xs font-medium text-violet-200 hover:bg-violet-500/20 disabled:opacity-50"
          onClick={importPublicResearchUniverse}
          disabled={importingPublicUniverse || !cfg}
        >
          {importingPublicUniverse ? "导入中..." : "导入公共股票池"}
        </button>
      </div>
    </div>
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <button
        className="rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-3 py-1.5 text-xs font-medium text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-50"
        onClick={syncStrongCandidatesFromAutoTrader}
        disabled={syncingStrongCandidates || !cfg}
      >
        {syncingStrongCandidates ? "同步中..." : "同步强势股列表"}
      </button>
      <button
        className="rounded-lg border border-slate-600/60 bg-slate-800/60 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700/70 disabled:opacity-50"
        onClick={() =>
          setSelectedResearchSymbols(
            strongCandidates.slice(0, Math.max(1, Number(cfg?.top_n || 8))).map((x) => String(x.symbol || ""))
          )
        }
        disabled={!strongCandidates.length}
      >
        选择前 TopN
      </button>
      <button
        className="rounded-lg border border-slate-600/60 bg-slate-800/60 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700/70 disabled:opacity-50"
        onClick={() => setSelectedResearchSymbols(strongCandidates.map((x) => String(x.symbol || "")))}
        disabled={!strongCandidates.length}
      >
        全选
      </button>
      <button
        className="rounded-lg border border-slate-600/60 bg-slate-800/60 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700/70 disabled:opacity-50"
        onClick={() => setSelectedResearchSymbols([])}
        disabled={!selectedResearchSymbols.length}
      >
        清空
      </button>
      <span className="text-xs text-slate-400">
        已选 {selectedResearchSymbols.length} / 候选 {strongCandidates.length}
      </span>
    </div>
    <div className="mt-2 text-[11px] text-slate-500">
      来源: {strongCandidatesMeta.source || "-"} · 最近扫描:
      {strongCandidatesMeta.scan_time ? ` ${formatTime(strongCandidatesMeta.scan_time)}` : " -"}
    </div>
    {!strongCandidates.length ? (
      <div className="mt-2 text-xs text-slate-400">
        还没有候选标的。可以同步自动交易强势股，也可以直接手工输入；手工标的会绕过 worker/券商强势股扫描，交给 Research 使用公共日线做策略评分。
      </div>
    ) : (
      <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
        {strongCandidates.map((x, idx) => {
          const sym = String(x.symbol || "");
          const checked = selectedResearchSymbols.includes(sym);
          return (
            <label
              key={`${sym || "cand"}-${idx}`}
              className={`flex cursor-pointer items-center justify-between rounded border px-2 py-1 text-xs ${
                checked
                  ? "border-cyan-500/50 bg-cyan-500/10 text-cyan-100"
                  : "border-slate-700/80 bg-slate-900/60 text-slate-300"
              }`}
            >
              <span className="truncate pr-2">{sym || "-"}</span>
              <span className="ml-2 text-[11px] text-slate-400">
                {typeof x.strength_score === "number"
                  ? x.strength_score.toFixed(2)
                  : x.candidate_source === "manual"
                    ? "手工"
                    : x.candidate_source === "public"
                      ? "公共"
                      : "-"}
              </span>
              <input
                className="ml-2 h-3.5 w-3.5 accent-cyan-400"
                type="checkbox"
                checked={checked}
                onChange={(e) => toggleResearchSymbol(sym, e.target.checked)}
              />
            </label>
          );
        })}
      </div>
    )}
    <div className="mt-2 text-[11px] text-slate-500">
      说明：已选列表会随“运行 Research”一起提交；有券商/worker 时可以先同步强势股再补充手工标的，未绑定券商时可直接手工录入或导入公共池。
    </div>
  </details>
  <div className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="text-sm font-medium text-slate-200">任务状态</div>
      <div className="flex flex-wrap gap-2">
        <button
          className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-medium text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
          onClick={cancelResearch}
          disabled={!researchRunning || !researchTaskId}
        >
          取消 Research
        </button>
        <button
          className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-medium text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
          onClick={cancelStrategyMatrix}
          disabled={!strategyMatrixRunning || !strategyMatrixTaskId}
        >
          取消策略矩阵
        </button>
        <button
          className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-medium text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
          onClick={cancelMlMatrix}
          disabled={!mlMatrixRunning || !mlMatrixTaskId}
        >
          取消 ML 矩阵
        </button>
      </div>
    </div>
    {showResearchProgress || showStrategyProgress || showMlProgress ? (
      <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
        {showResearchProgress ? renderTaskProgress(researchProgress, researchTaskId) : <div />}
        {showStrategyProgress ? renderTaskProgress(strategyMatrixProgress, strategyMatrixTaskId) : <div />}
        {showMlProgress ? renderTaskProgress(mlMatrixProgress, mlMatrixTaskId) : <div />}
      </div>
    ) : (
      <div className="mt-2 text-xs text-slate-400">
        当前没有运行中的研究任务。队列容量 {researchStatus?.task_queue?.max_pending ?? "-"}，最近快照{" "}
        {formatTime(researchStatus?.generated_at || undefined)}。
      </div>
    )}
  </div>

  <div className="rounded-lg border border-slate-700/70 bg-slate-900/45 p-2">
    <div className="flex flex-wrap gap-2">
      {resultTabs.map((tab) => {
        const active = resultTab === tab.key;
        return (
          <button
            key={tab.key}
            type="button"
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              active
                ? "bg-cyan-500/15 text-cyan-100 ring-1 ring-cyan-400/40"
                : "text-slate-400 hover:bg-slate-800/70 hover:text-slate-200"
            }`}
            onClick={() => setResultTab(tab.key)}
          >
            {tab.label}
            {tab.badge ? <span className="ml-1 text-[11px] opacity-75">{tab.badge}</span> : null}
          </button>
        );
      })}
    </div>
  </div>
  {resultTab === "overview" ? (
    <>
  <div className="rounded-lg border border-cyan-500/25 bg-slate-900/55 p-3">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="text-sm font-semibold text-slate-100">本次结论</div>
        <div className="mt-1 text-xs text-slate-400">
          {overviewHasAnyResult
            ? `最近结果：Research ${formatTime(researchSnapshotData?.generated_at)} · 策略矩阵 ${formatTime(
                strategyMatrix?.generated_at
              )} · ML矩阵 ${formatTime(mlMatrix?.generated_at)}`
            : "暂无结果，先运行 Research 或矩阵任务。"}
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="rounded-lg border border-slate-600/70 bg-slate-800/60 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700/70 disabled:opacity-50"
          onClick={() => setResultTab("research")}
          disabled={!strongRows.length}
        >
          查看快照
        </button>
        <button
          type="button"
          className="rounded-lg border border-slate-600/70 bg-slate-800/60 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700/70 disabled:opacity-50"
          onClick={() => setResultTab("strategy")}
          disabled={!matrixRows.length}
        >
          查看策略矩阵
        </button>
        <button
          type="button"
          className="rounded-lg border border-slate-600/70 bg-slate-800/60 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700/70 disabled:opacity-50"
          onClick={() => setResultTab("ml")}
          disabled={!mlMatrixRows.length}
        >
          查看ML矩阵
        </button>
      </div>
    </div>

    <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-4">
      <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
        <div className="text-xs text-slate-400">推荐标的权重</div>
        {topAllocationRows.length ? (
          <div className="mt-2 space-y-1.5">
            {topAllocationRows.map((row, idx) => (
              <div key={`${row?.symbol || "alloc"}-${idx}`} className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate text-cyan-200">{row?.symbol || "-"}</span>
                <span className="font-mono text-emerald-300">
                  {typeof row?.weight === "number" ? `${(row.weight * 100).toFixed(2)}%` : "-"}
                </span>
              </div>
            ))}
          </div>
        ) : topStrongRows.length ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {topStrongRows.map((row, idx) => (
              <span
                key={`${row?.symbol || "strong"}-${idx}`}
                className="rounded border border-cyan-400/25 bg-cyan-400/10 px-2 py-0.5 text-[11px] text-cyan-100"
              >
                {row?.symbol || "-"}
              </span>
            ))}
          </div>
        ) : (
          <div className="mt-2 text-xs text-slate-500">-</div>
        )}
      </div>

      <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
        <div className="text-xs text-slate-400">策略矩阵推荐</div>
        <div className="mt-2 text-sm font-semibold text-emerald-300">
          {matrixBestBalanced?.strategy_label || matrixBestBalanced?.strategy || "-"}
        </div>
        <div className="mt-1 text-xs text-slate-400">
          score {matrixBestBalanced?.matrix_score ?? "-"} · 收益 {matrixBestBalanced?.avg_net_return_pct ?? "-"}%
        </div>
        {matrixBestBalanced?.strategy_params && Object.keys(matrixBestBalanced.strategy_params).length ? (
          <div className="mt-1 truncate text-[11px] text-slate-500" title={JSON.stringify(matrixBestBalanced.strategy_params)}>
            {JSON.stringify(matrixBestBalanced.strategy_params)}
          </div>
        ) : null}
      </div>

      <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
        <div className="text-xs text-slate-400">ML 矩阵推荐</div>
        <div className="mt-2 text-sm font-semibold text-sky-300">
          {pickedMlMatrixRow?.params?.model_type || "-"}
        </div>
        <div className="mt-1 text-xs text-slate-400">
          阈值 {pickedMlMatrixRow?.params?.ml_threshold ?? "-"} · Precision{" "}
          {pickedMlMatrixRow?.metrics?.precision ?? "-"} · Coverage {pickedMlMatrixRow?.metrics?.coverage ?? "-"}
        </div>
        <div className="mt-1 text-[11px] text-slate-500">score {pickedMlMatrixRow?.score ?? "-"}</div>
      </div>

      <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
        <div className="text-xs text-slate-400">组合回测摘要</div>
        <div className="mt-2 text-sm font-semibold text-violet-300">
          {selectedPairRows.length ? `${selectedPairRows.length} 组` : "-"}
        </div>
        <div className="mt-1 text-xs text-slate-400">
          收益 {pairPortfolioEstimate?.total_return_pct ?? "-"}% · 回撤{" "}
          {pairPortfolioEstimate?.avg_selected_max_drawdown_pct ?? "-"}%
        </div>
        <div className="mt-1 text-[11px] text-slate-500">交易 {pairTradeTotalCount || 0} 笔</div>
      </div>
    </div>
  </div>

  <div className="rounded-lg border border-emerald-500/25 bg-slate-900/55 p-3">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="text-sm font-semibold text-slate-100">下一步动作</div>
        <div className="mt-1 text-xs text-slate-400">把已经生成的 Research 结果接入 AutoTrader 配置。</div>
      </div>
      {configApplyMessage ? <div className="text-xs text-emerald-300">{configApplyMessage}</div> : null}
    </div>
    <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-3">
      <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
        <div className="text-sm font-medium text-slate-100">Research 分配</div>
        <div className="mt-1 text-xs text-slate-400">
          可用权重 {allocationRows.length} 条 · 有效期 7 天
        </div>
        <button
          type="button"
          className="mt-3 w-full rounded-lg bg-gradient-to-r from-emerald-600 to-teal-600 px-3 py-2 text-xs font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
          onClick={openResearchAllocationPreview}
          disabled={!canApplyResearchAllocation || configApplyBusy === "research"}
        >
          {configApplyBusy === "research" ? "应用中..." : "应用分配到 AutoTrader"}
        </button>
      </div>

      <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
        <div className="text-sm font-medium text-slate-100">策略矩阵前 3</div>
        <div className="mt-1 text-xs text-slate-400">
          可用矩阵 {matrixRows.length} 条 · 推荐 {matrixBestBalanced?.strategy || "-"}
        </div>
        <button
          type="button"
          className="mt-3 w-full rounded-lg bg-gradient-to-r from-cyan-600 to-blue-600 px-3 py-2 text-xs font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
          onClick={openStrategyMatrixPreview}
          disabled={!canApplyStrategyMatrix || configApplyBusy === "strategy"}
        >
          {configApplyBusy === "strategy" ? "应用中..." : "并入 AutoTrader 评分"}
        </button>
      </div>

      <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
        <div className="text-sm font-medium text-slate-100">ML 最优参数</div>
        <div className="mt-1 text-xs text-slate-400">
          可用矩阵 {mlMatrixRows.length} 条 · 推荐 {pickedMlMatrixRow?.params?.model_type || "-"}
        </div>
        {mlMatrixSnapshots.length ? (
          <select
            className={`${INPUT_CLS} mt-3 h-9 text-xs`}
            value={mlApplySnapshotId}
            onChange={(e) => setMlApplySnapshotId(e.target.value)}
            disabled={mlApplyBusy || !canApplyMlMatrix}
            title="ML矩阵快照来源"
          >
            <option value="">使用当前最新 ML 矩阵</option>
            {mlMatrixSnapshots.map((snap: SnapshotHistoryRow) => (
              <option key={String(snap?.snapshot_id || "")} value={String(snap?.snapshot_id || "")}>
                {formatTime(snap?.generated_at)} {snapshotMetaSummary(snap) ? `· ${snapshotMetaSummary(snap)}` : ""}
              </option>
            ))}
          </select>
        ) : null}
        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-[1fr_auto]">
          <select
            className={`${INPUT_CLS} h-9 text-xs`}
            value={mlApplyVariant}
            onChange={(e) => setMlApplyVariant(e.target.value as MlMatrixApplyVariant)}
            disabled={mlApplyBusy || !canApplyMlMatrix}
          >
            <option value="auto">自动</option>
            <option value="balanced">平衡</option>
            <option value="high_precision">高精确</option>
            <option value="high_coverage">高覆盖</option>
            <option value="best_score">最高分</option>
          </select>
          <button
            type="button"
            className="rounded-lg bg-gradient-to-r from-amber-600 to-orange-600 px-3 py-2 text-xs font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
            onClick={openMlMatrixPreview}
            disabled={!canApplyMlMatrix || mlApplyBusy}
          >
            {mlApplyBusy ? "应用中..." : "应用 ML"}
          </button>
        </div>
      </div>
    </div>
    {configApplyPreview ? (
      <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-amber-100">{configApplyPreview.title}</div>
            <div className="mt-1 text-xs text-amber-100/80">{configApplyPreview.description}</div>
            <div className="mt-1 text-[11px] text-slate-400">来源：{configApplyPreview.sourceLabel}</div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded-lg border border-slate-600/70 bg-slate-900/60 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800 disabled:opacity-50"
              onClick={() => setConfigApplyPreview(null)}
              disabled={Boolean(configApplyBusy) || mlApplyBusy}
            >
              取消
            </button>
            <button
              type="button"
              className="rounded-lg bg-gradient-to-r from-amber-600 to-orange-600 px-3 py-1.5 text-xs font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
              onClick={confirmApplyPreview}
              disabled={Boolean(configApplyBusy) || mlApplyBusy}
            >
              {configApplyBusy || mlApplyBusy ? "应用中..." : "确认应用"}
            </button>
          </div>
        </div>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[760px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">配置项</th>
                <th className="py-1">当前值</th>
                <th className="py-1">应用后</th>
                <th className="py-1">变化</th>
              </tr>
            </thead>
            <tbody>
              {configDiffRows.map((row) => (
                <tr key={row.key} className="border-t border-slate-800/80 text-slate-200">
                  <td className="py-1">{row.label}</td>
                  <td className="py-1 text-slate-400">{row.before}</td>
                  <td className="py-1 text-amber-100">{row.after}</td>
                  <td className={row.changed ? "py-1 text-emerald-300" : "py-1 text-slate-500"}>
                    {row.changed ? "会修改" : "不变"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    ) : null}
  </div>

  <div className="rounded-lg border border-slate-700/70 bg-slate-900/55 p-3">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="text-sm font-semibold text-slate-100">历史快照对比</div>
        <div className="mt-1 text-xs text-slate-400">对比最近保留的 Research、策略矩阵或 ML 矩阵结果，确认这次优化是否真的更好。</div>
      </div>
      <span className="rounded border border-slate-700/70 bg-slate-950/40 px-2 py-1 text-[11px] text-slate-400">
        {historyLoading ? "加载中" : `${historyRowsForType.length} 份`}
      </span>
    </div>
    <div className="mt-3 flex flex-wrap gap-2">
      {SNAPSHOT_HISTORY_TYPE_OPTIONS.map((opt) => {
        const active = snapshotHistoryType === opt.key;
        return (
          <button
            key={opt.key}
            type="button"
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              active
                ? "bg-cyan-500/15 text-cyan-100 ring-1 ring-cyan-400/40"
                : "border border-slate-700/70 bg-slate-950/35 text-slate-400 hover:bg-slate-800/70 hover:text-slate-200"
            }`}
            onClick={() => setSnapshotHistoryType(opt.key)}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
    <div className="mt-3 grid grid-cols-1 gap-2 lg:grid-cols-[1fr_1fr_auto]">
      <select
        className={`${INPUT_CLS} h-9 text-xs`}
        value={historyPair.left}
        onChange={(e) =>
          setHistoryCompareIds((prev) => ({
            ...prev,
            [snapshotHistoryType]: { ...(prev[snapshotHistoryType] || { left: "", right: "" }), left: e.target.value },
          }))
        }
        disabled={!historyRowsForType.length}
      >
        <option value="">选择左侧快照</option>
        {historyRowsForType.map((snap) => (
          <option key={String(snap?.snapshot_id || "")} value={String(snap?.snapshot_id || "")}>
            {formatTime(snap?.generated_at)} {snapshotMetaSummary(snap) ? `· ${snapshotMetaSummary(snap)}` : ""}
          </option>
        ))}
      </select>
      <select
        className={`${INPUT_CLS} h-9 text-xs`}
        value={historyPair.right}
        onChange={(e) =>
          setHistoryCompareIds((prev) => ({
            ...prev,
            [snapshotHistoryType]: { ...(prev[snapshotHistoryType] || { left: "", right: "" }), right: e.target.value },
          }))
        }
        disabled={!historyRowsForType.length}
      >
        <option value="">选择右侧快照</option>
        {historyRowsForType.map((snap) => (
          <option key={String(snap?.snapshot_id || "")} value={String(snap?.snapshot_id || "")}>
            {formatTime(snap?.generated_at)} {snapshotMetaSummary(snap) ? `· ${snapshotMetaSummary(snap)}` : ""}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-3 py-2 text-xs font-medium text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-50"
        onClick={() => void loadSnapshotCompare()}
        disabled={!historyPair.left || !historyPair.right || historyCompareLoading}
      >
        {historyCompareLoading ? "对比中..." : "对比快照"}
      </button>
    </div>
    {historyRowsForType.length < 2 ? (
      <div className="mt-3 rounded border border-slate-700/70 bg-slate-950/35 p-2 text-xs text-slate-400">
        当前类型历史不足 2 份。后端每个市场、每类结果最多保留最近 3 份，继续运行任务后这里会自动出现可对比项。
      </div>
    ) : null}
    {historyCompareRows.length ? (
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[760px] text-xs">
          <thead className="text-left text-slate-400">
            <tr>
              <th className="py-1">指标</th>
              <th className="py-1">左侧快照</th>
              <th className="py-1">右侧快照</th>
              <th className="py-1">变化</th>
            </tr>
          </thead>
          <tbody>
            {historyCompareRows.map((row) => (
              <tr key={row.label} className="border-t border-slate-800/90 text-slate-200">
                <td className="py-1">{row.label}</td>
                <td className="py-1 text-slate-400">{row.left}</td>
                <td className="py-1 text-cyan-100">{row.right}</td>
                <td className={row.changed ? "py-1 text-amber-300" : "py-1 text-slate-500"}>
                  {row.changed ? "有变化" : "不变"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ) : (
      <div className="mt-3 text-xs text-slate-500">
        选择两份快照后点击对比。默认左侧是最新结果，右侧是上一份结果。
      </div>
    )}
  </div>

  <details className="rounded-lg border border-slate-700/70 bg-slate-900/40 p-3" open>
    <summary className="cursor-pointer text-sm font-medium text-slate-200">研究状态卡</summary>
    <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-6">
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
      <div className={SUB_TITLE_CLS}>快照状态</div>
      <div className={`mt-1 text-sm font-semibold ${researchStatus?.has_snapshot ? "text-emerald-300" : "text-slate-300"}`}>
        {researchStatus?.has_snapshot ? "已生成" : "未生成"}
      </div>
    </div>
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
      <div className={SUB_TITLE_CLS}>最近生成</div>
      <div className="mt-1 text-sm text-slate-200">{formatTime(researchStatus?.generated_at || undefined)}</div>
    </div>
    <div
      className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3"
      title="与 Auto Trader 当前保存配置一致；下次运行 Research 将使用此项"
    >
      <div className={SUB_TITLE_CLS}>市场/K线（配置）</div>
      <div className="mt-1 text-sm text-cyan-300">
        {(cfg?.market ?? researchStatus?.market ?? "-").toString().toUpperCase()} /{" "}
        {(cfg?.kline ?? researchStatus?.kline ?? "-").toString().toUpperCase()}
      </div>
    </div>
    <div
      className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3"
      title="与 Auto Trader 当前保存配置一致"
    >
      <div className={SUB_TITLE_CLS}>TopN（配置）</div>
      <div className="mt-1 text-sm text-slate-200">{cfg?.top_n ?? researchStatus?.top_n ?? "-"}</div>
    </div>
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
      <div className={SUB_TITLE_CLS}>版本号</div>
      <div className="mt-1 truncate text-xs text-slate-300" title={researchStatus?.version || "-"}>
        {researchStatus?.version || "-"}
      </div>
    </div>
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
      <div className={SUB_TITLE_CLS}>任务队列</div>
      <div className="mt-1 text-sm text-amber-300">
        {researchStatus?.task_queue?.active ?? 0} / {researchStatus?.task_queue?.max_pending ?? "-"}
      </div>
      <div className="mt-1 text-[11px] text-slate-400">
        运行 {researchStatus?.task_queue?.running ?? 0} · 排队 {researchStatus?.task_queue?.queued ?? 0}
      </div>
    </div>
    </div>
    <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-6">
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>券商主源：{providerStatus?.primary || "longport"}</div>
        <div className="mt-1 text-slate-400">
          OpenBB：{providerStatus?.openbb_enabled ? (providerStatus?.openbb_connected ? "已连接" : "未连接") : "未启用"}
        </div>
        <div className="mt-1 truncate text-slate-500" title={providerStatus?.openbb_base_url || ""}>
          {providerStatus?.openbb_base_url || "无外部因子服务"}
        </div>
      </div>
      <div
        className={`rounded-lg border p-3 text-xs ${
          cnPublicData?.ready
            ? "border-emerald-500/40 bg-emerald-950/20 text-emerald-100"
            : "border-amber-500/40 bg-amber-950/20 text-amber-100"
        }`}
      >
        <div>A股公共源：{cnPublicData?.ready ? "可用" : "待配置"}</div>
        <div className="mt-1 text-slate-300">
          {cnPublicData?.schema || "a_share_research_data.v2"}
        </div>
        <div className="mt-1 text-slate-400">
          行情源 {cnPublicData?.quote_ready ?? 0}/{cnPublicData?.quote_enabled ?? 0} · 估值{" "}
          {cnPublicData?.valuation_ready ? "可用" : "不可用"}
        </div>
        <div className="mt-1 truncate text-slate-400" title={cnProviderNames.join(" / ")}>
          {cnProviderNames.length ? cnProviderNames.join(" / ") : "mootdx / Tencent / AkShare / CNInfo / EastMoney / Sina"}
        </div>
      </div>
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>A股研究数据</div>
        <div className="mt-1 text-slate-400">
          财报期：{cnPublicData?.latest_fundamental_period || "-"}
        </div>
        <div className="mt-1 text-slate-400">
          新闻 {cnPublicData?.latest_news_items ?? "-"} · 公告 {cnLatestNoticeCount || "-"}
        </div>
        <div className="mt-1 truncate text-slate-500" title={cnPublicData?.research_cache?.latest_at || ""}>
          缓存：{cnPublicData?.research_cache?.available ? formatTime(cnPublicData.research_cache.latest_at) : "未生成"}
        </div>
      </div>
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>外部市场状态：{regimeInfo?.available ? regimeInfo?.regime || "unknown" : "-"}</div>
        <div className="mt-1 text-slate-400">
          置信度：{typeof regimeInfo?.confidence === "number" ? regimeInfo.confidence : "-"} | 基准：
          {regimeInfo?.symbol || "-"}
        </div>
        <div className="mt-1 text-slate-400">
          特征：ret20={typeof regimeInfo?.features?.ret_20 === "number" ? regimeInfo.features.ret_20 : "-"} | vol_z=
          {typeof regimeInfo?.features?.vol_z === "number" ? regimeInfo.features.vol_z : "-"}
        </div>
      </div>
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>TradingAgents：{agentGating?.applied ? "已应用" : "未应用"}</div>
        <div className="mt-1 text-slate-400">
          Insight：{tradingagentsAvailableCount} / {tradingagentsInsights.length}
        </div>
        <div className="mt-1 text-slate-400">
          方向：B {agentGating?.buy_signals ?? "-"} · S {agentGating?.sell_signals ?? "-"} · H {agentGating?.hold_signals ?? "-"}
        </div>
      </div>
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>Regime风控：{regimeGating?.applied ? "已应用" : "未应用"}</div>
        <div className="mt-1 text-slate-400">
          仓位上限：{typeof regimeGating?.effective_exposure === "number" ? `${(regimeGating.effective_exposure * 100).toFixed(1)}%` : "-"}
        </div>
        <div className="mt-1 text-slate-400">
          单标的上限：
          {typeof regimeGating?.max_single_ratio === "number" ? `${(regimeGating.max_single_ratio * 100).toFixed(1)}%` : "-"}
        </div>
      </div>
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>ML诊断：{mlDiag?.enabled ? "已生成" : "未生成"}</div>
        <div className="mt-1 text-slate-400">
          样本：{mlDiag?.dataset?.samples ?? "-"} | 标签正样本率：
          {typeof mlDiag?.label_distribution?.positive_ratio === "number"
            ? `${(mlDiag.label_distribution.positive_ratio * 100).toFixed(1)}%`
            : "-"}
        </div>
        <div className="mt-1 text-slate-400">
          成本标签(bps)：{mlDiag?.settings?.transaction_cost_bps ?? "-"} | 预测周期：{mlDiag?.settings?.horizon_days ?? "-"}天
        </div>
      </div>
    </div>
  </details>

    </>
  ) : null}

  {resultTab === "research" ? (
    <>
  <details className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3">
    <summary className="cursor-pointer text-sm font-medium text-slate-200">TradingAgents 研判详情</summary>
    <div className="mt-2 text-xs text-slate-400">
      应用：{agentGating?.applied ? "是" : "否"} · 权重：{typeof agentGating?.weight === "number" ? agentGating.weight : "-"} ·
      应用标的：{agentGating?.applied_symbols ?? "-"} / {agentGating?.available_symbols ?? "-"}
    </div>
    <div className="mt-2">
      <button
        className="rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-200 hover:bg-emerald-500/20 disabled:opacity-50"
        onClick={exportTradingAgentsReportsJson}
        disabled={!tradingagentsInsights.length}
      >
        批量下载全部报告（JSON）
      </button>
    </div>
    {!tradingagentsInsights.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无 TradingAgents 研判数据，请先运行一次 Research。</div>
    ) : (
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[760px] text-xs">
          <thead className="text-left text-slate-400">
            <tr>
              <th className="py-1">Symbol</th>
              <th className="py-1">可用</th>
              <th className="py-1">Action</th>
              <th className="py-1">Confidence</th>
              <th className="py-1">Reason</th>
              <th className="py-1">报告</th>
            </tr>
          </thead>
          <tbody>
            {tradingagentsInsights.slice(0, 20).map((row: any, idx: number) => (
              <tr key={`${row?.symbol || "ta"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                <td className="py-1">{row?.symbol || "-"}</td>
                <td className="py-1">{row?.available ? "yes" : "no"}</td>
                <td className="py-1 text-cyan-300">{String(row?.action || "-").toUpperCase()}</td>
                <td className="py-1">
                  {typeof row?.confidence === "number" ? Number(row.confidence).toFixed(2) : "-"}
                </td>
                <td className="max-w-[420px] truncate py-1 text-slate-400" title={formatTradingAgentsReason(row)}>
                  {formatTradingAgentsReason(row)}
                </td>
                <td className="py-1">
                  <details className="rounded border border-slate-700/60 bg-slate-950/40 px-2 py-1">
                    <summary className="cursor-pointer text-[11px] text-cyan-300">查看</summary>
                    <div className="mt-2 max-w-[640px] space-y-2">
                      <div className="text-[11px] text-slate-400">
                        request_symbol: {row?.request_symbol || row?.symbol || "-"} · source:{" "}
                        {row?.source || "tradingagents"}
                      </div>
                      <pre className="max-h-[220px] overflow-auto rounded border border-slate-800/90 bg-slate-950/70 p-2 text-[11px] leading-5 text-slate-300 whitespace-pre-wrap">
                        {String(
                          row?.research_report_markdown ||
                            row?.decision_text ||
                            row?.error ||
                            row?.reason ||
                            "无可展示内容"
                        )}
                      </pre>
                      <button
                        className="rounded border border-cyan-500/40 bg-cyan-500/10 px-2 py-1 text-[11px] text-cyan-200 hover:bg-cyan-500/20"
                        onClick={() => downloadTradingAgentsReport(row, idx)}
                      >
                        下载报告
                      </button>
                    </div>
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )}
  </details>

  <details className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3" open>
    <summary className="cursor-pointer text-sm font-medium text-slate-200">
      Strong 标的列表（本次实际参与 Research）
    </summary>
    {!strongRows.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无 strong 列表，请先执行一次 Research。</div>
    ) : (
      <>
        {strongRows.length > visibleStrongRows.length ? (
          <div className="mt-2 text-[11px] text-amber-300">
            为保证页面流畅，仅展示前 {visibleStrongRows.length} 条（共 {strongRows.length} 条）。
          </div>
        ) : null}
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[820px] text-sm">
            <thead className="bg-slate-900/60 text-left text-slate-300">
              <tr>
                <th className="px-3 py-2">代码</th>
                <th className="px-3 py-2">强度分数</th>
                <th className="px-3 py-2">现价</th>
                <th className="px-3 py-2">涨跌幅</th>
                <th className="px-3 py-2">5日收益</th>
                <th className="px-3 py-2">20日收益</th>
                <th className="px-3 py-2">价格类型</th>
              </tr>
            </thead>
            <tbody>
              {visibleStrongRows.map((x, idx) => (
                <tr key={`${x.symbol || "strong"}-${idx}`} className="border-t border-slate-800/90">
                  <td className="px-3 py-2 font-medium text-slate-100">{x.symbol || "-"}</td>
                  <td className="px-3 py-2 text-cyan-300">{typeof x.strength_score === "number" ? x.strength_score.toFixed(2) : "-"}</td>
                  <td className="px-3 py-2 text-slate-200">{typeof x.last === "number" ? x.last.toFixed(4) : "-"}</td>
                  <td className={`px-3 py-2 ${typeof x.change_pct === "number" ? (x.change_pct >= 0 ? "text-emerald-300" : "text-amber-300") : "text-slate-400"}`}>
                    {typeof x.change_pct === "number" ? `${x.change_pct.toFixed(2)}%` : "-"}
                  </td>
                  <td className={`px-3 py-2 ${typeof x.ret5_pct === "number" ? (x.ret5_pct >= 0 ? "text-emerald-300" : "text-amber-300") : "text-slate-400"}`}>
                    {typeof x.ret5_pct === "number" ? `${x.ret5_pct.toFixed(2)}%` : "-"}
                  </td>
                  <td className={`px-3 py-2 ${typeof x.ret20_pct === "number" ? (x.ret20_pct >= 0 ? "text-emerald-300" : "text-amber-300") : "text-slate-400"}`}>
                    {typeof x.ret20_pct === "number" ? `${x.ret20_pct.toFixed(2)}%` : "-"}
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-300">{x.price_type || x.price_source || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    )}
  </details>

    </>
  ) : null}

  {resultTab === "strategy" ? (
    <>
  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
    <details
      className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
      onToggle={onResearchPanelToggle(setModelComparePanelOpen, "model_compare")}
    >
      <summary className="cursor-pointer text-sm font-medium text-slate-200">模型对比榜（P2）</summary>
      {modelComparePanelOpen && sectionLoading.model_compare ? (
        <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
      ) : null}
      {modelComparePanelOpen && !sectionLoading.model_compare && !modelRows.length ? (
        <div className="mt-2 text-xs text-slate-400">暂无模型对比数据，先执行一次 Research。</div>
      ) : modelComparePanelOpen && !sectionLoading.model_compare ? (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[520px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">模型</th>
                <th className="py-1">运行次数</th>
                <th className="py-1">平均分</th>
                <th className="py-1">平均Acc</th>
                <th className="py-1">最佳分</th>
              </tr>
            </thead>
            <tbody>
              {modelRows.slice(0, 10).map((x, idx) => (
                <tr key={`${x.model_name || "model"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">{x.model_name || "-"}</td>
                  <td className="py-1">{x.runs ?? "-"}</td>
                  <td className="py-1 text-cyan-300">{x.avg_score ?? "-"}</td>
                  <td className="py-1 text-sky-300">{x.avg_accuracy ?? "-"}</td>
                  <td className="py-1 text-emerald-300">{x.best_score ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </details>

    <details
      className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
      onToggle={onResearchPanelToggle(setStrategySummaryPanelOpen, "strategy_summary")}
    >
      <summary className="cursor-pointer text-sm font-medium text-slate-200">
        策略优选摘要（P1）{matrixTopSymbolRows.length ? " · 已同步矩阵全组合Top5" : ""}
      </summary>
      {strategySummaryPanelOpen && sectionLoading.strategy_summary ? (
        <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
      ) : null}
      {strategySummaryPanelOpen && !sectionLoading.strategy_summary && !strategySummaryRows.length ? (
        <div className="mt-2 text-xs text-slate-400">暂无策略摘要数据，先执行一次 Research。</div>
      ) : strategySummaryPanelOpen && !sectionLoading.strategy_summary ? (
        <div className="mt-2 space-y-1">
          {strategySummaryRows.slice(0, 5).map((row, idx) => {
            const best = row?.best_strategy || {};
            return (
              <div
                key={`${row.symbol || "symbol"}-${idx}`}
                className="rounded border border-slate-800/90 bg-slate-950/40 px-2 py-1 text-xs text-slate-300"
              >
                <span className="text-cyan-300">{row.symbol || "-"}</span>
                {" | "}
                <span>{best.strategy_label || best.strategy || "-"}</span>
                {" | "}
                <span className="text-emerald-300">score: {best.composite_score ?? "-"}</span>
              </div>
            );
          })}
        </div>
      ) : null}
    </details>
  </div>

  <details
    className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
    onToggle={onResearchPanelToggle(setStrategyMatrixPanelOpen, "strategy_matrix")}
  >
    <summary className="cursor-pointer text-sm font-medium text-slate-200">策略参数矩阵（优秀策略筛选）</summary>
    {strategyMatrixPanelOpen && sectionLoading.strategy_matrix ? (
      <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
    ) : null}
    {strategyMatrixPanelOpen && strategyMatrixRunning ? (
      <div className="mt-2">{renderTaskProgress(strategyMatrixProgress, strategyMatrixTaskId)}</div>
    ) : null}
    {strategyMatrixPanelOpen && strategyMatrix?.generated_at ? (
      <div className="mt-2 text-[11px] text-slate-500">
        结果快照：<span className="text-slate-400">{strategyMatrix.generated_at}</span>
        {strategyMatrix.trace_id ? (
          <>
            {" "}
            · trace <span className="font-mono text-slate-400">{strategyMatrix.trace_id}</span>
          </>
        ) : null}
        {typeof strategyMatrix.candidate_count === "number" ? (
          <>
            {" "}
            · 过滤前候选 <span className="text-cyan-600/90">{strategyMatrix.candidate_count}</span> / 表格行{" "}
            <span className="text-cyan-600/90">{matrixRows.length}</span>
          </>
        ) : null}
      </div>
    ) : null}
    {strategyMatrixPanelOpen && !sectionLoading.strategy_matrix && !matrixRows.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无矩阵结果，点击“运行策略矩阵”开始。</div>
    ) : strategyMatrixPanelOpen && !sectionLoading.strategy_matrix ? (
      <div className="mt-2 space-y-3">
        <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            结果条数：<span className="text-cyan-300">{matrixRows.length}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            组合网格：<span className="text-cyan-300">{strategyMatrix?.grid_size ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            策略数：<span className="text-cyan-300">{strategyMatrix?.strategy_count ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            推荐（平衡）：
            <span className="text-emerald-300">{matrixBestBalanced?.strategy || "-"}</span>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">策略</th>
                <th className="py-1">K线</th>
                <th className="py-1">回测天数</th>
                <th className="py-1">成本(bps)</th>
                <th className="py-1">净收益%</th>
                <th className="py-1">回撤%</th>
                <th className="py-1">Sharpe</th>
                <th className="py-1">胜率%</th>
                <th className="py-1">样本</th>
                <th className="py-1">矩阵分</th>
              </tr>
            </thead>
            <tbody>
              {matrixRows.slice(0, 20).map((x, idx) => (
                <tr key={`${x.strategy || "s"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">
                    <div>{x.strategy_label || x.strategy || "-"}</div>
                    {x.strategy_params && Object.keys(x.strategy_params).length ? (
                      <div className="text-[11px] text-slate-400">{JSON.stringify(x.strategy_params)}</div>
                    ) : null}
                  </td>
                  <td className="py-1">{x.kline || "-"}</td>
                  <td className="py-1">{x.backtest_days ?? "-"}</td>
                  <td className="py-1">
                    {(x.commission_bps ?? "-")}/{(x.slippage_bps ?? "-")}
                  </td>
                  <td className="py-1 text-emerald-300">{x.avg_net_return_pct ?? "-"}</td>
                  <td className="py-1 text-amber-300">{x.avg_max_drawdown_pct ?? "-"}</td>
                  <td className="py-1">{x.avg_sharpe_ratio ?? "-"}</td>
                  <td className="py-1">{x.avg_win_rate_pct ?? "-"}</td>
                  <td className="py-1">
                    {x.symbols_used ?? "-"}/{x.symbols_total ?? "-"}
                  </td>
                  <td className="py-1 text-cyan-300">{x.matrix_score ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    ) : null}
  </details>

    </>
  ) : null}

  {resultTab === "ml" ? (
    <>
  <details
    className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
    onToggle={onResearchPanelToggle(setMlMatrixPanelOpen, "ml_matrix")}
  >
    <summary className="cursor-pointer text-sm font-medium text-slate-200">ML参数矩阵（可信参数筛选）</summary>
    {mlMatrixPanelOpen && sectionLoading.ml_matrix ? (
      <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
    ) : null}
    {mlMatrixPanelOpen && mlMatrixRunning ? (
      <div className="mt-2">{renderTaskProgress(mlMatrixProgress, mlMatrixTaskId)}</div>
    ) : null}
    {mlMatrixPanelOpen && !sectionLoading.ml_matrix && !mlMatrixRows.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无ML矩阵结果，点击“运行 ML 矩阵”开始。</div>
    ) : mlMatrixPanelOpen && !sectionLoading.ml_matrix ? (
      <div className="mt-2 space-y-3">
        {mlMatrix?.signal_bars_days_note ? (
          <div className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-200">
            {mlMatrix.signal_bars_days_note}
          </div>
        ) : null}
        {Array.isArray(mlMatrix?.bar_fetch_preflight) && mlMatrix!.bar_fetch_preflight!.length ? (
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            <div className="mb-1 font-medium text-slate-200">K 线预检（raw=接口返回根数，feature=净特征行）</div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {mlMatrix!.bar_fetch_preflight!.map((p) => (
                <span key={String(p.symbol)}>
                  <span className="text-cyan-300">{p.symbol || "-"}</span>: raw {p.raw_bars ?? "-"} / feat{" "}
                  {p.feature_rows ?? "-"}{p.meets_matrix_min ? "" : " ⚠"}
                  {p.error ? <span className="text-rose-400"> {p.error}</span> : null}
                </span>
              ))}
            </div>
          </div>
        ) : null}
        <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            结果条数：<span className="text-cyan-300">{mlMatrixRows.length}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            组合网格：<span className="text-cyan-300">{mlMatrix?.grid_size ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            通过约束：<span className="text-cyan-300">{mlMatrix?.passed_constraints_count ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            推荐（平衡）：
            <span className="text-emerald-300">{mlMatrixBestBalanced?.params?.model_type || "-"}</span>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1120px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">模型</th>
                <th className="py-1">阈值</th>
                <th className="py-1">Horizon</th>
                <th className="py-1">TrainRatio</th>
                <th className="py-1">WF窗口</th>
                <th className="py-1">Acc</th>
                <th className="py-1">Precision</th>
                <th className="py-1">Recall</th>
                <th className="py-1">Coverage</th>
                <th className="py-1">OOS</th>
                <th className="py-1">通过</th>
                <th className="py-1">评分</th>
              </tr>
            </thead>
            <tbody>
              {mlMatrixRows.slice(0, 20).map((x, idx) => (
                <tr key={`${x?.params?.model_type || "mm"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">{x?.params?.model_type || "-"}</td>
                  <td className="py-1">{x?.params?.ml_threshold ?? "-"}</td>
                  <td className="py-1">{x?.params?.ml_horizon_days ?? "-"}</td>
                  <td className="py-1">{x?.params?.ml_train_ratio ?? "-"}</td>
                  <td className="py-1">{x?.params?.ml_walk_forward_windows ?? "-"}</td>
                  <td className="py-1">{x?.metrics?.accuracy ?? "-"}</td>
                  <td className="py-1 text-cyan-300">{x?.metrics?.precision ?? "-"}</td>
                  <td className="py-1">{x?.metrics?.recall ?? "-"}</td>
                  <td className="py-1 text-emerald-300">{x?.metrics?.coverage ?? "-"}</td>
                  <td className="py-1">{x?.metrics?.oos_samples ?? "-"}</td>
                  <td className="py-1">{x?.pass_constraints ? "是" : "否"}</td>
                  <td className="py-1 text-sky-300">{x?.score ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    ) : null}
  </details>

  <details
    className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
    onToggle={onResearchPanelToggle(setMlDiagPanelOpen, "ml_diag")}
  >
    <summary className="cursor-pointer text-sm font-medium text-slate-200">ML诊断详情（Walk-forward）</summary>
    {mlDiagPanelOpen && sectionLoading.ml_diag ? (
      <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
    ) : null}
    {mlDiagPanelOpen && !sectionLoading.ml_diag && !mlDiagModels.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无 ML 诊断详情，先执行一次 Research。</div>
    ) : mlDiagPanelOpen && !sectionLoading.ml_diag ? (
      <div className="mt-2 space-y-2">
        <div className="text-xs text-slate-400">
          样本 {mlDiag?.dataset?.samples ?? "-"} · 使用标的 {mlDiag?.dataset?.symbols_used ?? "-"} /
          {mlDiag?.dataset?.symbols_requested ?? "-"} · 特征 {mlDiag?.settings?.feature_count ?? "-"} 个
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[960px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">模型</th>
                <th className="py-1">最新UpProb</th>
                <th className="py-1">Acc</th>
                <th className="py-1">Precision</th>
                <th className="py-1">Recall</th>
                <th className="py-1">Coverage</th>
                <th className="py-1">窗口数</th>
                <th className="py-1">OOS样本</th>
                <th className="py-1">WF覆盖条数</th>
              </tr>
            </thead>
            <tbody>
              {visibleMlDiagModels.map((x, idx) => {
                const wf = x?.walk_forward || {};
                return (
                  <tr key={`${x.model_name || "ml"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                    <td className="py-1">{x.model_name || "-"}</td>
                    <td className="py-1 text-cyan-300">{x.latest_up_probability ?? "-"}</td>
                    <td className="py-1">{wf.accuracy ?? "-"}</td>
                    <td className="py-1">{wf.precision ?? "-"}</td>
                    <td className="py-1">{wf.recall ?? "-"}</td>
                    <td className="py-1">{wf.coverage ?? "-"}</td>
                    <td className="py-1">{wf.windows ?? "-"}</td>
                    <td className="py-1">{wf.oos_samples ?? "-"}</td>
                    <td className="py-1">{x.walk_forward_coverage ?? "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    ) : null}
  </details>

    </>
  ) : null}

  {resultTab === "research" ? (
    <>
  <details
    className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
    onToggle={onResearchPanelToggle(setAbPanelOpen, "ab_report")}
  >
    <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium text-slate-200">
      <span>A/B 报告（Baseline vs WithFactor）</span>
      <span
        className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] ${
          Number(factorGating?.available_symbols || 0) > 0
            ? "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30"
            : "bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/30"
        }`}
        title={
          Number(factorGating?.available_symbols || 0) > 0
            ? "已检测到可用外部因子样本"
            : "当前外部因子样本不可用，报告仍可用于结构对比"
        }
      >
        {Number(factorGating?.available_symbols || 0) > 0 ? "因子数据可用" : "因子数据不足"}
      </span>
    </summary>
    {abPanelOpen && sectionLoading.ab_report ? (
      <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
    ) : null}
    {abPanelOpen && !sectionLoading.ab_report && !abReport ? (
      <div className="mt-2 text-xs text-slate-400">暂无 A/B 报告，先执行一次 Research。</div>
    ) : abPanelOpen && !sectionLoading.ab_report ? (
      <div className="mt-2 space-y-3">
        <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            Top5重合：<span className="text-cyan-300">{abSummary?.overlap_count ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            平均最佳分Δ：<span className="text-emerald-300">{abSummary?.avg_best_score_delta ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            分配换手：<span className="text-amber-300">{abSummary?.allocation_turnover ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            生成时间：<span className="text-slate-200">{formatTime(abReport?.generated_at)}</span>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-2 text-xs text-slate-300 md:grid-cols-2">
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2">
            Baseline Top5：{(abSummary?.top5_baseline || []).join(", ") || "-"}
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2">
            WithFactor Top5：{(abSummary?.top5_with_factor || []).join(", ") || "-"}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-xs">
            <thead className="bg-slate-900/60 text-left text-slate-300">
              <tr>
                <th className="px-3 py-2">标的</th>
                <th className="px-3 py-2">Score(B)</th>
                <th className="px-3 py-2">Score(F)</th>
                <th className="px-3 py-2">ΔScore</th>
                <th className="px-3 py-2">Multiplier</th>
                <th className="px-3 py-2">W(B)</th>
                <th className="px-3 py-2">W(F)</th>
                <th className="px-3 py-2">ΔW</th>
              </tr>
            </thead>
            <tbody>
              {abItems.slice(0, 10).map((x, idx) => (
                <tr key={`${x.symbol || "ab"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="px-3 py-2">{x.symbol || "-"}</td>
                  <td className="px-3 py-2">{x.score_baseline ?? "-"}</td>
                  <td className="px-3 py-2">{x.score_with_factor ?? "-"}</td>
                  <td className="px-3 py-2">{x.score_delta ?? "-"}</td>
                  <td className="px-3 py-2">{x.factor_multiplier ?? "-"}</td>
                  <td className="px-3 py-2">{x.weight_baseline ?? "-"}</td>
                  <td className="px-3 py-2">{x.weight_with_factor ?? "-"}</td>
                  <td className="px-3 py-2">{x.weight_delta ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    ) : null}
  </details>

  <details
    className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
    onToggle={onResearchPanelToggle(setAllocationPanelOpen, "allocation")}
  >
    <summary className="cursor-pointer text-sm font-medium text-slate-200">分配计划表（P1）</summary>
    {allocationPanelOpen && sectionLoading.allocation ? (
      <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
    ) : null}
    {allocationPanelOpen && !sectionLoading.allocation && !allocationRows.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无分配计划，先执行一次 Research。</div>
    ) : allocationPanelOpen && !sectionLoading.allocation ? (
      <>
        {allocationRows.length > visibleAllocationRows.length ? (
          <div className="mt-2 text-[11px] text-amber-300">
            为保证页面流畅，仅展示前 {visibleAllocationRows.length} 条（共 {allocationRows.length} 条）。
          </div>
        ) : null}
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="bg-slate-900/60 text-left text-slate-300">
              <tr>
                <th className="px-3 py-2">代码</th>
                <th className="px-3 py-2">原始权重</th>
                <th className="px-3 py-2">建议权重</th>
                <th className="px-3 py-2">Δ权重</th>
                <th className="px-3 py-2">强度分数</th>
                <th className="px-3 py-2">价格类型</th>
              </tr>
            </thead>
            <tbody>
              {visibleAllocationRows.map((x, idx) => (
                <tr key={`${x.symbol || "alloc"}-${idx}`} className="border-t border-slate-800/90">
                  <td className="px-3 py-2 font-medium text-slate-100">{x.symbol || "-"}</td>
                  <td className="px-3 py-2 text-slate-300">
                    {typeof x.weight_raw === "number" ? `${(x.weight_raw * 100).toFixed(2)}%` : "-"}
                  </td>
                  <td className="px-3 py-2 text-cyan-300">
                    {typeof x.weight === "number" ? `${(x.weight * 100).toFixed(2)}%` : "-"}
                  </td>
                  <td
                    className={`px-3 py-2 ${
                      typeof x.weight === "number" && typeof x.weight_raw === "number"
                        ? x.weight - x.weight_raw >= 0
                          ? "text-emerald-300"
                          : "text-amber-300"
                        : "text-slate-400"
                    }`}
                  >
                    {typeof x.weight === "number" && typeof x.weight_raw === "number"
                      ? `${((x.weight - x.weight_raw) * 100).toFixed(2)}%`
                      : "-"}
                  </td>
                  <td className="px-3 py-2">{x.strength_score ?? "-"}</td>
                  <td className="px-3 py-2 text-xs text-slate-300">{x.price_type || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    ) : null}
  </details>

    </>
  ) : null}

  {resultTab === "pair" ? (
    <>
  <details
    className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
    onToggle={onResearchPanelToggle(setPairBacktestPanelOpen, "pair_backtest")}
  >
    <summary className="cursor-pointer text-sm font-medium text-slate-200">ETF配对回测（只读快照）</summary>
    {pairBacktestPanelOpen && sectionLoading.pair_backtest ? (
      <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
    ) : null}
    {pairBacktestPanelOpen && !sectionLoading.pair_backtest && !pairBacktest ? (
      <div className="mt-2 text-xs text-slate-400">暂无快照回测结果，请先执行一次 Research。</div>
    ) : pairBacktestPanelOpen && !sectionLoading.pair_backtest && pairBacktest?.error ? (
      <div className="mt-2 text-xs text-rose-300">回测快照错误：{String(pairBacktest.error)}</div>
    ) : pairBacktestPanelOpen && !sectionLoading.pair_backtest ? (
      <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-5">
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm">市场：{pairBacktest?.market ?? "-"}</div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm">K线：{pairBacktest?.kline ?? "-"}</div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm text-emerald-300">
          总收益估算：{pairBacktest?.portfolio_estimate?.total_return_pct ?? "-"}%
        </div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm text-amber-300">
          平均回撤估算：{pairBacktest?.portfolio_estimate?.avg_selected_max_drawdown_pct ?? "-"}%
        </div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm">
          入选配对数：{(pairBacktest?.selected_pairs || []).length}
        </div>
      </div>
    ) : null}
    {pairBacktestPanelOpen && !sectionLoading.pair_backtest ? (
      <div className="mt-3 rounded-lg border border-slate-700/70 bg-slate-900/50 p-3">
      <div className="text-xs text-slate-400">
        配对池配置：{pairPoolUsed.length} 组
      </div>
      {!pairPoolUsed.length ? (
        <div className="mt-2 text-xs text-slate-400">当前市场未配置 ETF 配对池。</div>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[520px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">多头ETF</th>
                <th className="py-1">反向ETF</th>
              </tr>
            </thead>
            <tbody>
              {visiblePairPoolRows.map((row, idx) => (
                <tr key={`${row.long_symbol || "long"}-${row.short_symbol || "short"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">{row.long_symbol || "-"}</td>
                  <td className="py-1">{row.short_symbol || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pairPoolUsed.length > visiblePairPoolRows.length ? (
        <div className="mt-2 text-[11px] text-amber-300">
          为保证页面流畅，仅展示前 {visiblePairPoolRows.length} 组（共 {pairPoolUsed.length} 组）。
        </div>
      ) : null}
      </div>
    ) : null}
    {pairBacktestPanelOpen && !sectionLoading.pair_backtest ? (
      <div className="mt-3 rounded-lg border border-slate-700/70 bg-slate-900/50 p-3">
      <div className="text-xs text-slate-400">
        本次回测入选组合：{selectedPairRows.length} 组
      </div>
      {!selectedPairRows.length ? (
        <div className="mt-2 text-xs text-slate-400">暂无入选组合（可能是配对池为空或全部评分未通过）。</div>
      ) : (
        <div className="mt-2 overflow-x-auto">
          {selectedPairRows.length > visibleSelectedPairRows.length ? (
            <div className="mb-2 text-[11px] text-amber-300">
              为保证页面流畅，仅展示前 {visibleSelectedPairRows.length} 组（共 {selectedPairRows.length} 组）。
            </div>
          ) : null}
          <table className="w-full min-w-[760px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">配对</th>
                <th className="py-1">入选标的</th>
                <th className="py-1">入选策略</th>
                <th className="py-1">综合分</th>
                <th className="py-1">收益%</th>
                <th className="py-1">回撤%</th>
              </tr>
            </thead>
            <tbody>
              {visibleSelectedPairRows.map((row: any, idx: number) => {
                const pair = row?.pair || {};
                const metrics = row?.selected_metrics || {};
                return (
                  <tr key={`${pair?.long || "long"}-${pair?.short || "short"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                    <td className="py-1">{`${pair?.long || "-"} / ${pair?.short || "-"}`}</td>
                    <td className="py-1 text-cyan-300">{row?.selected_symbol || "-"}</td>
                    <td className="py-1">{metrics?.strategy_label || row?.selected_strategy || "-"}</td>
                    <td className="py-1">{row?.selected_score ?? "-"}</td>
                    <td className="py-1 text-emerald-300">{metrics?.total_return_pct ?? "-"}</td>
                    <td className="py-1 text-amber-300">{metrics?.max_drawdown_pct ?? "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      </div>
    ) : null}
    {pairBacktestPanelOpen && !sectionLoading.pair_backtest ? (
      <details className="mt-3 rounded-lg border border-slate-700/70 bg-slate-900/50 p-3">
      <summary className="cursor-pointer text-xs text-slate-300">
        组合交易明细（买入/卖出时间）：{pairTradeTotalCount} 笔（默认折叠）
      </summary>
      <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-2">
        <input
          className={INPUT_CLS}
          placeholder="按组合筛选（示例：SPY.US / SH.US）"
          value={pairTradeFilterPair}
          onChange={(e) => setPairTradeFilterPair(e.target.value)}
        />
        <input
          className={INPUT_CLS}
          placeholder="按标的筛选（示例：SPY.US）"
          value={pairTradeFilterSymbol}
          onChange={(e) => setPairTradeFilterSymbol(e.target.value)}
        />
      </div>
      {!filteredPairTradeRows.length ? (
        <div className="mt-2 text-xs text-slate-400">暂无交易明细（可能当前组合无成交或快照版本较旧）。</div>
      ) : (
        <div className="mt-2 overflow-x-auto">
          {filteredPairTradeRows.length > visibleFilteredPairTradeRows.length ? (
            <div className="mb-2 text-[11px] text-amber-300">
              为保证页面流畅，仅展示前 {visibleFilteredPairTradeRows.length} 笔（筛选后共 {filteredPairTradeRows.length} 笔）。
            </div>
          ) : null}
          {pairTradeTotalCount > pairTradeRows.length ? (
            <div className="mb-2 text-[11px] text-slate-400">
              当前仅在前端扫描前 {pairTradeRows.length} 笔用于实时筛选；如需全量请使用“导出组合交易明细 CSV”。
            </div>
          ) : null}
          <table className="w-full min-w-[1080px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">配对</th>
                <th className="py-1">入选标的</th>
                <th className="py-1">策略</th>
                <th className="py-1">买入时间</th>
                <th className="py-1">卖出时间</th>
                <th className="py-1">买入价</th>
                <th className="py-1">卖出价</th>
                <th className="py-1">数量</th>
                <th className="py-1">收益%</th>
                <th className="py-1">收益额</th>
                <th className="py-1">持有天数</th>
              </tr>
            </thead>
            <tbody>
              {visibleFilteredPairTradeRows.map((r: any) => (
                <tr key={r.id} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">{r.pair}</td>
                  <td className="py-1 text-cyan-300">{r.symbol}</td>
                  <td className="py-1">{r.strategy}</td>
                  <td className="py-1">{r.entry_date || "-"}</td>
                  <td className="py-1">{r.exit_date || "-"}</td>
                  <td className="py-1">{r.entry_price ?? "-"}</td>
                  <td className="py-1">{r.exit_price ?? "-"}</td>
                  <td className="py-1">{r.quantity ?? "-"}</td>
                  <td className={`py-1 ${Number(r.pnl_pct) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                    {r.pnl_pct ?? "-"}
                  </td>
                  <td className={`py-1 ${Number(r.pnl) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                    {r.pnl ?? "-"}
                  </td>
                  <td className="py-1">{r.hold_days ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      </details>
    ) : null}
  </details>
    </>
  ) : null}

  {resultTab === "export" ? (
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3">
      <div className="text-sm font-medium text-slate-200">导出工具</div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          className="rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-3 py-2 text-xs font-medium text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-50"
          onClick={exportResearchSnapshot}
          disabled={!researchSnapshotData}
        >
          导出研究快照 JSON
        </button>
        <button
          className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs font-medium text-emerald-200 hover:bg-emerald-500/20 disabled:opacity-50"
          onClick={exportModelCompareCsv}
          disabled={!modelRows.length}
        >
          导出模型对比 CSV
        </button>
        <button
          className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs font-medium text-amber-200 hover:bg-amber-500/20 disabled:opacity-50"
          onClick={exportPairTradeCsv}
          disabled={!pairTradeRows.length}
        >
          导出组合交易明细 CSV
        </button>
        <button
          className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-3 py-2 text-xs font-medium text-violet-200 hover:bg-violet-500/20 disabled:opacity-50"
          onClick={exportAbReportJson}
          disabled={!abReport}
        >
          导出 A/B 报告 JSON
        </button>
        <button
          className="rounded-lg border border-fuchsia-500/40 bg-fuchsia-500/10 px-3 py-2 text-xs font-medium text-fuchsia-200 hover:bg-fuchsia-500/20 disabled:opacity-50"
          onClick={exportAbReportMarkdown}
          disabled={!abMarkdown.trim()}
        >
          导出 A/B 报告 Markdown
        </button>
      </div>
    </div>
  ) : null}
</div>

    </div>
  );
}
