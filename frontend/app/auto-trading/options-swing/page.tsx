"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { FeatureLockedPanel } from "@/components/entitlement-guard";
import { PageShell } from "@/components/ui/page-shell";
import { localAgentGet as apiGet, localAgentPost as apiPost, localAgentPut as apiPut } from "@/lib/local-agent-api";
import { buildSwrOptions, SWR_INTERVALS } from "@/lib/swr-config";
import { useEntitlements } from "@/lib/use-entitlements";
import { AutoTradingTabs } from "../auto-trading-tabs";

type SwingStrategyConfig = {
  mode?: string;
  strategy_variant?: string;
  trend_fast_ma?: number;
  trend_slow_ma?: number;
  long_ma?: number;
  min_trend_score?: number;
  min_price_above_slow_ma_pct?: number;
  max_price_above_fast_ma_pct?: number;
  min_dte?: number;
  target_dte?: number;
  max_dte?: number;
  target_delta_min?: number;
  target_delta_max?: number;
  fallback_otm_pct?: number;
  spread_width_pct?: number;
  max_spread_debit?: number;
  min_open_interest?: number;
  min_option_volume?: number;
  max_bid_ask_spread_pct?: number;
  take_profit_pct?: number;
  stop_loss_pct?: number;
  dte_exit_days?: number;
  trend_exit_below_ma?: number;
  trend_exit_confirm_bars?: number;
};

type SwingRiskConfig = {
  max_contracts_per_order?: number;
  max_open_contracts?: number;
  max_premium_per_order?: number;
  max_premium_per_symbol?: number;
  max_total_option_premium?: number;
  max_new_premium_per_day?: number;
};

type EventBlackout = {
  symbol?: string;
  start?: string;
  end?: string;
  reason?: string;
};

type SwingConfig = {
  api_base_url?: string;
  account_id?: string | null;
  symbol?: string;
  stock_pool?: string[];
  history_days?: number;
  kline?: string;
  poll_seconds?: number;
  scan_time_hhmm_et?: string;
  second_scan_time_hhmm_et?: string;
  dry_run?: boolean;
  auto_submit_orders?: boolean;
  live_submit_confirmed_at?: string | null;
  live_submit_confirmed_by?: string | null;
  confirmation_token?: string | null;
  contracts?: number;
  managed_positions_only?: boolean;
  strict_account_ledger_match?: boolean;
  allow_import_existing_positions?: boolean;
  skip_existing_broker_positions?: boolean;
  strategy?: SwingStrategyConfig;
  risk?: SwingRiskConfig;
  symbol_blacklist?: string[];
  event_blackouts?: EventBlackout[];
};

type ModuleStatus = {
  ok?: boolean;
  module?: {
    running?: boolean;
    pid?: number | null;
    runtime?: {
      runtime?: Record<string, unknown>;
      state_label?: string;
      runtime_status?: string | null;
    } | null;
  };
};

type DecisionTail = {
  ok?: boolean;
  items?: Array<Record<string, unknown>>;
  path?: string;
};

const defaultStrategy: SwingStrategyConfig = {
  strategy_variant: "swing_trend_call",
  mode: "long_call",
  trend_fast_ma: 20,
  trend_slow_ma: 50,
  long_ma: 200,
  min_trend_score: 3,
  min_price_above_slow_ma_pct: 0,
  max_price_above_fast_ma_pct: 0.12,
  min_dte: 45,
  target_dte: 90,
  max_dte: 180,
  target_delta_min: 0.35,
  target_delta_max: 0.7,
  fallback_otm_pct: 0.03,
  spread_width_pct: 0.05,
  max_spread_debit: 600,
  min_open_interest: 50,
  min_option_volume: 1,
  max_bid_ask_spread_pct: 0.18,
  take_profit_pct: 0.8,
  stop_loss_pct: 0.45,
  dte_exit_days: 21,
  trend_exit_below_ma: 50,
  trend_exit_confirm_bars: 2,
};

const defaultRisk: SwingRiskConfig = {
  max_contracts_per_order: 1,
  max_open_contracts: 10,
  max_premium_per_order: 800,
  max_premium_per_symbol: 1500,
  max_total_option_premium: 4000,
  max_new_premium_per_day: 1500,
};

const defaultConfig: SwingConfig = {
  api_base_url: "http://127.0.0.1:8010",
  account_id: null,
  symbol: "QQQ.US",
  stock_pool: ["QQQ.US", "NVDA.US", "AAPL.US", "MSFT.US", "TSLA.US"],
  history_days: 260,
  kline: "1d",
  poll_seconds: 3600,
  scan_time_hhmm_et: "10:00",
  second_scan_time_hhmm_et: "15:30",
  dry_run: true,
  auto_submit_orders: false,
  live_submit_confirmed_at: null,
  live_submit_confirmed_by: null,
  confirmation_token: null,
  contracts: 1,
  managed_positions_only: true,
  strict_account_ledger_match: true,
  allow_import_existing_positions: false,
  skip_existing_broker_positions: true,
  strategy: defaultStrategy,
  risk: defaultRisk,
  symbol_blacklist: [],
  event_blackouts: [],
};

function mergeConfig(input?: SwingConfig | null): SwingConfig {
  return {
    ...defaultConfig,
    ...(input || {}),
    strategy: { ...defaultStrategy, ...(input?.strategy || {}) },
    risk: { ...defaultRisk, ...(input?.risk || {}) },
    stock_pool: Array.isArray(input?.stock_pool) && input.stock_pool.length ? input.stock_pool : defaultConfig.stock_pool,
    symbol_blacklist: Array.isArray(input?.symbol_blacklist) ? input.symbol_blacklist : [],
    event_blackouts: Array.isArray(input?.event_blackouts) ? input.event_blackouts : [],
  };
}

function poolToText(pool: unknown): string {
  return Array.isArray(pool) ? pool.map((x) => String(x || "").trim().toUpperCase()).filter(Boolean).join(", ") : "QQQ.US";
}

function parseSymbols(value: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of String(value || "").split(/[\s,;，；]+/)) {
    let sym = raw.trim().toUpperCase();
    if (!sym) continue;
    if (!sym.includes(".")) sym = `${sym}.US`;
    if (seen.has(sym)) continue;
    seen.add(sym);
    out.push(sym);
  }
  return out;
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function num(v: unknown): number | null {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function fmt(v: unknown, digits = 2): string {
  const n = num(v);
  if (n == null) return v === null || v === undefined || v === "" ? "-" : String(v);
  return n.toLocaleString("en-US", { maximumFractionDigits: digits });
}

function pct(v: unknown): string {
  const n = num(v);
  return n == null ? "-" : `${(n * 100).toFixed(0)}%`;
}

function shortTime(value: unknown): string {
  const s = typeof value === "string" ? value : "";
  if (!s) return "-";
  const d = new Date(s);
  if (!Number.isFinite(d.getTime())) return s;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}

function posSymbol(row: unknown): string {
  return String(asRecord(row)?.symbol || "-");
}

function posUnderlying(row: unknown): string {
  return String(asRecord(row)?.underlying || "-");
}

function normalizeEventRows(rows: unknown): EventBlackout[] {
  if (!Array.isArray(rows)) return [];
  return rows
    .map((row) => {
      const rec = asRecord(row) || {};
      let symbol = String(rec.symbol || "").trim().toUpperCase();
      if (symbol && !symbol.includes(".")) symbol = `${symbol}.US`;
      return {
        symbol,
        start: String(rec.start || "").trim(),
        end: String(rec.end || "").trim(),
        reason: String(rec.reason || "").trim(),
      };
    })
    .filter((row) => row.symbol || row.start || row.end || row.reason);
}

function positionPremium(row: unknown): number {
  const rec = asRecord(row) || {};
  if (String(rec.structure || "") === "call_debit_spread") {
    const qty = Math.abs(num(rec.quantity) ?? num(rec.contracts) ?? 0);
    const debit = num(rec.entry_net_debit) ?? 0;
    return qty * debit * 100;
  }
  const qty = Math.abs(num(rec.quantity) ?? 0);
  const cost = num(rec.cost_price) ?? 0;
  return qty * cost * 100;
}

function isSpreadPosition(row: unknown): boolean {
  return String(asRecord(row)?.structure || "") === "call_debit_spread";
}

function spreadLegSummary(row: unknown): string {
  const legs = asArray(asRecord(row)?.legs).map(asRecord).filter(Boolean) as Record<string, unknown>[];
  if (!legs.length) return "-";
  return legs
    .map((leg) => {
      const side = String(leg.entry_side || "").toLowerCase() === "sell" ? "短" : "长";
      const strike = fmt(leg.strike);
      return `${side}${String(leg.right || "").slice(0, 1).toUpperCase()} ${strike}`;
    })
    .join(" / ");
}

export default function StockOptionsSwingPage() {
  const entitlements = useEntitlements();
  const [draft, setDraft] = useState<SwingConfig>(defaultConfig);
  const [poolText, setPoolText] = useState(poolToText(defaultConfig.stock_pool));
  const [blacklistText, setBlacklistText] = useState("");
  const [eventRows, setEventRows] = useState<EventBlackout[]>([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");

  const { data: status, mutate: mutateStatus } = useSWR<ModuleStatus>(
    "/auto-trading/options-swing/status",
    () => apiGet<ModuleStatus>("/auto-trading/options-swing/status", { timeoutMs: 8000, retries: 0, cacheTtlMs: 0 }),
    buildSwrOptions(SWR_INTERVALS.mediumPoll.refreshInterval, SWR_INTERVALS.mediumPoll.dedupingInterval)
  );
  const { data: tail, mutate: mutateTail } = useSWR<DecisionTail>(
    "/strategy/stock-options-swing/live-worker-decision-tail",
    () => apiGet<DecisionTail>("/strategy/stock-options-swing/live-worker-decision-tail?limit=40", { timeoutMs: 8000, retries: 0, cacheTtlMs: 0 }),
    buildSwrOptions(SWR_INTERVALS.slowPage.refreshInterval, SWR_INTERVALS.slowPage.dedupingInterval)
  );

  const runtime = asRecord(status?.module?.runtime?.runtime);
  const running = Boolean(status?.module?.running);
  const symbolsState = asArray(runtime?.symbols_state).map(asRecord).filter(Boolean) as Record<string, unknown>[];
  const unmanagedPositions = asArray(runtime?.unmanaged_positions);
  const managedPositions = asArray(runtime?.managed_positions);
  const warnings = asArray(runtime?.warnings).map(String);
  const scheduler = asRecord(runtime?.scheduler);
  const exitActions = asArray(runtime?.exit_actions);
  const blacklistedSet = useMemo(() => new Set((draft.symbol_blacklist || []).map((x) => String(x || "").toUpperCase())), [draft.symbol_blacklist]);
  const underlyingGroups = useMemo(() => {
    const map = new Map<string, { underlying: string; managed: number; unmanaged: number; contracts: number; premium: number; positions: unknown[] }>();
    for (const row of [...managedPositions, ...unmanagedPositions]) {
      const underlying = posUnderlying(row);
      const rec = asRecord(row) || {};
      const current = map.get(underlying) || { underlying, managed: 0, unmanaged: 0, contracts: 0, premium: 0, positions: [] };
      const isManaged = managedPositions.includes(row);
      if (isManaged) current.managed += 1;
      else current.unmanaged += 1;
      current.contracts += Math.abs(num(rec.quantity) ?? 0);
      current.premium += positionPremium(row);
      current.positions.push(row);
      map.set(underlying, current);
    }
    return Array.from(map.values()).sort((a, b) => b.positions.length - a.positions.length || a.underlying.localeCompare(b.underlying));
  }, [managedPositions, unmanagedPositions]);

  const exitPreviewRows = useMemo(
    () =>
      managedPositions
        .map((row) => asRecord(row))
        .filter((row): row is Record<string, unknown> => Boolean(row?.exit_signal)),
    [managedPositions]
  );

  const loadConfig = useCallback(async () => {
    setErr("");
    try {
      const res = await apiGet<{ config?: SwingConfig }>("/strategy/stock-options-swing/live-worker-config", {
        timeoutMs: 10000,
        retries: 0,
        cacheTtlMs: 0,
      });
      const next = mergeConfig(res.config);
      setDraft(next);
      setPoolText(poolToText(next.stock_pool));
      setBlacklistText(poolToText(next.symbol_blacklist || []));
      setEventRows(normalizeEventRows(next.event_blackouts || []));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  const canRun = entitlements.canUse("option_auto_trading");
  const liveAutoSubmitEnabled = draft.dry_run === false && Boolean(draft.auto_submit_orders);
  const riskMode = useMemo(() => {
    if (draft.dry_run) return "演练模式：只写候选、系统托管和退出预览，不发真实订单";
    if (!draft.auto_submit_orders) return "半自动模式：扫描和预览退出，但不自动提交订单";
    return "实盘自动提交已开启：只管理系统托管或本 worker ledger 仓位";
  }, [draft.auto_submit_orders, draft.dry_run]);

  function updateStrategy(patch: Partial<SwingStrategyConfig>) {
    setDraft((d) => ({ ...d, strategy: { ...defaultStrategy, ...(d.strategy || {}), ...patch } }));
  }

  function updateRisk(patch: Partial<SwingRiskConfig>) {
    setDraft((d) => ({ ...d, risk: { ...defaultRisk, ...(d.risk || {}), ...patch } }));
  }

  async function saveConfig(): Promise<boolean> {
    setBusy("save");
    setMsg("");
    setErr("");
    try {
      const eventBlackouts = normalizeEventRows(eventRows);
      const body = {
        ...draft,
        stock_pool: parseSymbols(poolText).length ? parseSymbols(poolText) : ["QQQ.US"],
        symbol_blacklist: parseSymbols(blacklistText),
        event_blackouts: eventBlackouts,
        strategy: { ...defaultStrategy, ...(draft.strategy || {}) },
        risk: { ...defaultRisk, ...(draft.risk || {}) },
      };
      const enablingLiveAutoSubmit = !body.dry_run && body.auto_submit_orders && !(draft.dry_run === false && draft.auto_submit_orders);
      if (!body.dry_run && body.auto_submit_orders && !String(body.confirmation_token || "").trim()) {
        throw new Error("开启真实自动提交前必须填写 confirmation_token");
      }
      if (enablingLiveAutoSubmit) {
        const ok = window.confirm(
          "即将开启实盘自动提交。\n\nworker 可能按系统托管仓位和平仓信号直接提交真实期权订单。确认继续保存？"
        );
        if (!ok) return false;
        const typed = window.prompt("请输入 CONFIRM_LIVE_AUTO_SUBMIT 以确认开启实盘自动提交");
        if (typed !== "CONFIRM_LIVE_AUTO_SUBMIT") {
          throw new Error("未完成实盘自动提交二次确认，配置未保存");
        }
        (body as SwingConfig & { live_submit_confirm?: string }).live_submit_confirm = "CONFIRM_LIVE_AUTO_SUBMIT";
      }
      const res = await apiPut<{ config?: SwingConfig }>("/strategy/stock-options-swing/live-worker-config", body, { timeoutMs: 12000, retries: 0 });
      const next = mergeConfig(res.config || body);
      setDraft(next);
      setPoolText(poolToText(next.stock_pool));
      setBlacklistText(poolToText(next.symbol_blacklist || []));
      setEventRows(normalizeEventRows(next.event_blackouts || []));
      setMsg("配置已保存。运行中的 worker 会在下一轮读取新配置。");
      await mutateStatus();
      return true;
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy("");
    }
  }

  async function toggleWorker() {
    setBusy(running ? "stop" : "start");
    setMsg("");
    setErr("");
    try {
      if (running) {
        await apiPost("/auto-trading/options-swing/stop", {}, { timeoutMs: 20000, retries: 0 });
        setMsg("停止命令已发送。");
      } else {
        const saved = await saveConfig();
        if (!saved) return;
        await apiPost("/auto-trading/options-swing/start", { start_feishu_bot: false }, { timeoutMs: 30000, retries: 0 });
        setMsg("启动命令已发送。");
      }
      await Promise.all([mutateStatus(), mutateTail()]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  async function positionAction(action: "import" | "unmanage" | "blacklist" | "unblacklist", row: unknown) {
    const rec = asRecord(row) || {};
    const symbol = String(rec.symbol || "");
    if (action === "import") {
      const ok = window.confirm(
        `确认把 ${symbol} 改为系统托管？\n\n系统托管后，worker 会按当前中长线规则管理这张期权，包括止盈、止损、DTE 退出和趋势破坏退出。`
      );
      if (!ok) return;
    }
    if (action === "unmanage") {
      const ok = window.confirm(`确认取消 ${symbol} 的系统托管？取消后 worker 不会自动管理这张期权。`);
      if (!ok) return;
    }
    setBusy(`${action}:${symbol}`);
    setMsg("");
    setErr("");
    try {
      await apiPost(
        "/strategy/stock-options-swing/position-action",
        {
          action,
          symbol,
          underlying: rec.underlying,
          quantity: rec.quantity,
          cost_price: rec.cost_price,
          current_price: rec.current_price,
        },
        { timeoutMs: 12000, retries: 0 }
      );
      if (action === "import") setMsg(`${symbol} 已改为系统托管；持仓状态已立即刷新。`);
      if (action === "blacklist") setMsg(`${rec.underlying || symbol} 已加入黑名单；持仓状态已立即刷新。`);
      if (action === "unblacklist") setMsg(`${rec.underlying || symbol} 已解除黑名单；持仓状态已立即刷新。`);
      if (action === "unmanage") setMsg(`${symbol} 已从托管 ledger 移除；持仓状态已立即刷新。`);
      await Promise.all([loadConfig(), mutateStatus(), mutateTail()]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  function setUnderlyingBlacklist(underlying: string, enabled: boolean) {
    const target = String(underlying || "").trim().toUpperCase();
    if (!target || target === "-") return;
    setDraft((d) => {
      const values = new Set((d.symbol_blacklist || []).map((x) => String(x || "").trim().toUpperCase()).filter(Boolean));
      if (enabled) values.add(target);
      else values.delete(target);
      const next = Array.from(values).sort();
      setBlacklistText(poolToText(next));
      return { ...d, symbol_blacklist: next };
    });
  }

  function updateEventRow(index: number, patch: Partial<EventBlackout>) {
    setEventRows((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  if (!canRun) {
    return (
      <>
        <AutoTradingTabs />
        <PageShell>
          <FeatureLockedPanel feature="option_auto_trading" plan={entitlements.plan} title="期权自动交易需要 Premium" />
        </PageShell>
      </>
    );
  }

  return (
    <>
      <AutoTradingTabs />
      <PageShell>
        <div className="panel border-cyan-500/20 bg-slate-900/95">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-white">股票期权中长线交易</h1>
              <p className="mt-2 max-w-5xl text-sm leading-6 text-slate-300">
                独立 worker，使用日线趋势和 45-180 DTE 合约筛选。账户已有期权默认识别为未托管持仓，不会自动平仓；只有改为系统托管后才按止盈、止损、DTE 和趋势破坏规则管理。
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-full border px-3 py-1 text-sm ${running ? "border-emerald-400/50 bg-emerald-500/10 text-emerald-200" : "border-slate-600 bg-slate-800 text-slate-300"}`}>
                {running ? "运行中" : "未运行"}
              </span>
              <button className={running ? "btn-secondary" : "btn-primary"} disabled={Boolean(busy)} onClick={() => void toggleWorker()}>
                {busy === "start" || busy === "stop" ? "处理中..." : running ? "停止 Worker" : "启动 Worker"}
              </button>
            </div>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-6">
            <div className="rounded-lg border border-slate-700 bg-slate-950/50 p-3 md:col-span-2">
              <div className="text-xs text-slate-500">运行模式</div>
              <div className={`mt-1 text-sm font-semibold ${liveAutoSubmitEnabled ? "text-rose-200" : "text-slate-100"}`}>{riskMode}</div>
              {liveAutoSubmitEnabled ? (
                <div className="mt-1 text-xs text-rose-200/80">
                  已确认：{String(draft.live_submit_confirmed_at || "本次保存后生效")}
                </div>
              ) : null}
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-950/50 p-3">
              <div className="text-xs text-slate-500">候选数量</div>
              <div className="mt-1 text-xl font-semibold text-cyan-100">{String(runtime?.candidate_count ?? "-")}</div>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-950/50 p-3">
              <div className="text-xs text-slate-500">托管 / 未托管</div>
              <div className="mt-1 text-sm text-slate-100">{managedPositions.length} / {unmanagedPositions.length}</div>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-950/50 p-3">
              <div className="text-xs text-slate-500">今日新增权利金</div>
              <div className="mt-1 text-sm text-slate-100">${fmt(runtime?.new_premium_today)}</div>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-950/50 p-3">
              <div className="text-xs text-slate-500">持仓来源 / 下次扫描</div>
              <div className="mt-1 text-xs text-slate-100">{String(runtime?.positions_source || "-")}</div>
              <div className="mt-1 text-xs text-slate-400">{String(scheduler?.next_scan_et || "-")} ET</div>
            </div>
          </div>

          {warnings.length ? <div className="mt-3 rounded-lg border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-100">{warnings.join(" / ")}</div> : null}
          {liveAutoSubmitEnabled ? (
            <div className="mt-3 rounded-lg border border-rose-400/50 bg-rose-500/15 p-3 text-sm font-semibold text-rose-100">
              实盘自动提交已开启：退出信号可能直接提交真实卖单。请确认只托管你希望系统管理的期权仓位。
            </div>
          ) : null}
          {exitActions.length ? <div className="mt-3 rounded-lg border border-cyan-400/30 bg-cyan-400/10 p-3 text-sm text-cyan-100">检测到 {exitActions.length} 个托管仓位退出信号，演练模式只生成平仓预览。</div> : null}
          {msg ? <div className="mt-3 text-sm text-emerald-200">{msg}</div> : null}
          {err ? <div className="mt-3 text-sm text-rose-300">{err}</div> : null}
        </div>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(28rem,0.9fr)]">
          <div className="space-y-4">
            <section className="panel">
              <div className="mb-3 flex items-center justify-between gap-2">
                <div>
                  <div className="text-lg font-semibold text-slate-100">持仓隔离</div>
                  <div className="mt-1 text-xs text-slate-500">系统托管前只展示和阻断，不自动平仓。改为系统托管后才进入中长线退出规则。</div>
                </div>
                <button className="btn-secondary" disabled={Boolean(busy)} onClick={() => void mutateStatus()}>刷新</button>
              </div>

              {exitPreviewRows.length ? (
                <div className="mb-4 rounded-lg border border-cyan-400/30 bg-cyan-400/10 p-3">
                  <div className="mb-2 text-sm font-semibold text-cyan-100">退出预览</div>
                  <div className="space-y-2">
                    {exitPreviewRows.map((row, idx) => {
                      const result = asRecord(row.exit_order_result);
                      const preview = asRecord(result?.order_preview);
                      return (
                        <div key={`${String(row.symbol)}-${idx}`} className="rounded-md border border-cyan-300/20 bg-slate-950/40 p-2 text-xs text-cyan-50/90">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="font-mono text-cyan-100">{String(row.symbol || "-")}</span>
                            <span>{draft.dry_run || !draft.auto_submit_orders ? "仅预览，不提交" : isSpreadPosition(row) ? "可能提交组合平仓单" : "可能提交真实卖单"}</span>
                          </div>
                          {isSpreadPosition(row) ? (
                            <div className="mt-1 text-cyan-100/75">
                              触发：{asArray(row.reasons).join(" / ") || "-"} · 组合平仓 {asArray(preview?.legs).length || asArray(row.legs).length} 腿 · 净值 {fmt(row.current_net_value)} · 收益 {pct(row.return_pct)}
                            </div>
                          ) : (
                            <div className="mt-1 text-cyan-100/75">
                              触发：{asArray(row.reasons).join(" / ") || "-"} · 卖出张数 {String(preview?.contracts || row.quantity || "-")} · 参考价 {fmt(preview?.price || row.current_price)}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              <div className="mb-4 rounded-lg border border-slate-700 bg-slate-950/35 p-3">
                <div className="mb-2 text-sm font-semibold text-slate-100">底层聚合</div>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left text-xs">
                    <thead className="text-slate-400">
                      <tr><th className="py-2 pr-3">底层</th><th className="py-2 pr-3">托管/未托管</th><th className="py-2 pr-3">张数</th><th className="py-2 pr-3">成本权利金</th><th className="py-2 pr-3">黑名单</th></tr>
                    </thead>
                    <tbody>
                      {underlyingGroups.map((group) => (
                        <tr key={group.underlying} className="border-t border-slate-800">
                          <td className="py-2 pr-3 font-mono text-slate-100">{group.underlying}</td>
                          <td className="py-2 pr-3 text-slate-300">{group.managed} / {group.unmanaged}</td>
                          <td className="py-2 pr-3 text-slate-300">{fmt(group.contracts, 0)}</td>
                          <td className="py-2 pr-3 text-slate-300">${fmt(group.premium)}</td>
                          <td className="py-2 pr-3">
                            <label className="inline-flex items-center gap-2 text-slate-300">
                              <input type="checkbox" checked={blacklistedSet.has(group.underlying)} onChange={(e) => setUnderlyingBlacklist(group.underlying, e.target.checked)} />
                              <span>{blacklistedSet.has(group.underlying) ? "已拉黑" : "允许扫描"}</span>
                            </label>
                          </td>
                        </tr>
                      ))}
                      {!underlyingGroups.length ? <tr><td className="py-3 text-slate-500" colSpan={5}>暂无期权持仓底层。</td></tr> : null}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="mb-4 rounded-lg border border-emerald-400/25 bg-emerald-400/5 p-3">
                <div className="mb-2 text-sm font-semibold text-emerald-100">托管仓位</div>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left text-xs">
                    <thead className="text-slate-400">
                      <tr><th className="py-2 pr-3">期权</th><th className="py-2 pr-3">DTE</th><th className="py-2 pr-3">数量</th><th className="py-2 pr-3">成本/现价</th><th className="py-2 pr-3">收益</th><th className="py-2 pr-3">退出</th><th className="py-2 pr-3">操作</th></tr>
                    </thead>
                    <tbody>
                      {managedPositions.map((row, idx) => {
                        const rec = asRecord(row) || {};
                        const exitSignal = Boolean(rec.exit_signal);
                        const spread = isSpreadPosition(row);
                        return (
                          <tr key={`${posSymbol(row)}-${idx}`} className="border-t border-slate-800">
                            <td className="py-2 pr-3 font-mono text-slate-100">
                              {spread ? "Call Debit Spread" : posSymbol(row)}
                              <div className="text-slate-500">
                                {spread ? `${String(rec.underlying || "-")} · ${spreadLegSummary(row)}` : `${String(rec.underlying || "-")} ${String(rec.right || "")} ${fmt(rec.strike)}`}
                              </div>
                              {spread && rec.position_group_id ? <div className="mt-1 text-[10px] text-slate-600">{String(rec.position_group_id)}</div> : null}
                            </td>
                            <td className="py-2 pr-3 text-slate-300">{fmt(rec.dte, 0)}</td>
                            <td className="py-2 pr-3 text-slate-300">{fmt(rec.quantity, 0)}</td>
                            <td className="py-2 pr-3 text-slate-300">{spread ? `${fmt(rec.entry_net_debit)} / ${fmt(rec.current_net_value)}` : `${fmt(rec.cost_price)} / ${fmt(rec.current_price)}`}</td>
                            <td className="py-2 pr-3 text-slate-300">{pct(rec.return_pct)}</td>
                            <td className={`py-2 pr-3 ${exitSignal ? "text-amber-200" : rec.management_block ? "text-rose-200" : "text-slate-500"}`}>{exitSignal ? asArray(rec.reasons).join(", ") || "exit" : rec.management_block ? String(rec.management_block) : "继续持有"}</td>
                            <td className="py-2 pr-3"><button className="text-cyan-300 hover:underline" disabled={Boolean(busy)} onClick={() => void positionAction("unmanage", row)}>取消托管</button></td>
                          </tr>
                        );
                      })}
                      {!managedPositions.length ? <tr><td className="py-3 text-slate-500" colSpan={7}>暂无托管仓位。</td></tr> : null}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="rounded-lg border border-amber-400/25 bg-amber-400/5 p-3">
                <div className="mb-2 text-sm font-semibold text-amber-100">未托管仓位</div>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left text-xs">
                    <thead className="text-slate-400">
                      <tr><th className="py-2 pr-3">期权</th><th className="py-2 pr-3">DTE</th><th className="py-2 pr-3">数量</th><th className="py-2 pr-3">成本/现价</th><th className="py-2 pr-3">底层</th><th className="py-2 pr-3">操作</th></tr>
                    </thead>
                    <tbody>
                      {unmanagedPositions.map((row, idx) => {
                        const rec = asRecord(row) || {};
                        const underlying = posUnderlying(row);
                        const blacklisted = (draft.symbol_blacklist || []).includes(underlying);
                        return (
                          <tr key={`${posSymbol(row)}-${idx}`} className="border-t border-slate-800">
                            <td className="py-2 pr-3 font-mono text-slate-100">{posSymbol(row)}<div className="text-slate-500">{String(rec.right || "")} {fmt(rec.strike)}</div></td>
                            <td className="py-2 pr-3 text-slate-300">{fmt(rec.dte, 0)}</td>
                            <td className="py-2 pr-3 text-slate-300">{fmt(rec.quantity, 0)}</td>
                            <td className="py-2 pr-3 text-slate-300">{fmt(rec.cost_price)} / {fmt(rec.current_price)}</td>
                            <td className="py-2 pr-3 text-slate-300">{underlying}</td>
                            <td className="py-2 pr-3">
                              <div className="flex flex-wrap gap-2">
                                <button className="text-cyan-300 hover:underline" disabled={Boolean(busy)} onClick={() => void positionAction("import", row)}>系统托管</button>
                                <button className="text-amber-300 hover:underline" disabled={Boolean(busy)} onClick={() => void positionAction(blacklisted ? "unblacklist" : "blacklist", row)}>{blacklisted ? "解除拉黑" : "拉黑底层"}</button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                      {!unmanagedPositions.length ? <tr><td className="py-3 text-slate-500" colSpan={6}>暂无未托管期权仓位。</td></tr> : null}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>

            <section className="panel">
              <div className="mb-3 text-lg font-semibold text-slate-100">扫描结果</div>
              <div className="grid gap-2 md:grid-cols-2">
                {symbolsState.length ? symbolsState.map((row) => {
                  const sig = asRecord(row.signal);
                  return (
                    <div key={String(row.symbol)} className="rounded-lg border border-slate-700 bg-slate-950/50 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-sm text-slate-100">{String(row.symbol || "-")}</span>
                        <span className="rounded-full border border-slate-600 px-2 py-0.5 text-xs text-slate-300">{String(sig?.action || row.status || "-")}</span>
                      </div>
                      <div className="mt-2 text-xs text-slate-400">score {String(sig?.score ?? "-")} · {String(sig?.reason || "-")}</div>
                      <div className="mt-1 text-xs text-slate-500">last {String(sig?.last ?? "-")} · MA20 {String(num(sig?.ma_fast)?.toFixed(2) || "-")} · MA50 {String(num(sig?.ma_slow)?.toFixed(2) || "-")}</div>
                    </div>
                  );
                }) : <div className="text-sm text-slate-500">暂无扫描结果。启动 worker 后会显示每个股票的候选状态。</div>}
              </div>
            </section>
          </div>

          <div className="space-y-4">
            <section className="panel">
              <div className="mb-3 text-lg font-semibold text-slate-100">研究与风控配置</div>
              <div className="grid gap-3">
                <label className="space-y-1">
                  <div className="field-label">股票池</div>
                  <textarea className="input-base min-h-20 font-mono text-sm" value={poolText} onChange={(e) => setPoolText(e.target.value)} />
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <label className="space-y-1"><div className="field-label">轮询间隔(秒)</div><input className="input-base" type="number" min={300} value={draft.poll_seconds ?? 3600} onChange={(e) => setDraft((d) => ({ ...d, poll_seconds: Math.max(300, Number(e.target.value) || 3600) }))} /></label>
                  <label className="space-y-1"><div className="field-label">历史日线天数</div><input className="input-base" type="number" min={60} value={draft.history_days ?? 260} onChange={(e) => setDraft((d) => ({ ...d, history_days: Math.max(60, Number(e.target.value) || 260) }))} /></label>
                  <label className="space-y-1"><div className="field-label">第一扫描 ET</div><input className="input-base font-mono" value={draft.scan_time_hhmm_et || "10:00"} onChange={(e) => setDraft((d) => ({ ...d, scan_time_hhmm_et: e.target.value }))} /></label>
                  <label className="space-y-1"><div className="field-label">第二扫描 ET</div><input className="input-base font-mono" value={draft.second_scan_time_hhmm_et || "15:30"} onChange={(e) => setDraft((d) => ({ ...d, second_scan_time_hhmm_et: e.target.value }))} /></label>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <label className="flex items-center gap-2"><input type="checkbox" checked={draft.dry_run !== false} onChange={(e) => setDraft((d) => ({ ...d, dry_run: e.target.checked }))} /><span className="text-sm text-slate-300">演练模式</span></label>
                  <label className="flex items-center gap-2"><input type="checkbox" checked={Boolean(draft.auto_submit_orders)} onChange={(e) => setDraft((d) => ({ ...d, auto_submit_orders: e.target.checked }))} /><span className={`text-sm ${liveAutoSubmitEnabled ? "font-semibold text-rose-200" : "text-slate-300"}`}>允许自动提交</span></label>
                  <label className="flex items-center gap-2"><input type="checkbox" checked={draft.managed_positions_only !== false} onChange={(e) => setDraft((d) => ({ ...d, managed_positions_only: e.target.checked }))} /><span className="text-sm text-slate-300">只管托管仓位</span></label>
                  <label className="flex items-center gap-2"><input type="checkbox" checked={draft.skip_existing_broker_positions !== false} onChange={(e) => setDraft((d) => ({ ...d, skip_existing_broker_positions: e.target.checked }))} /><span className="text-sm text-slate-300">已有期权则跳过开仓</span></label>
                  <label className="col-span-2 flex items-center gap-2"><input type="checkbox" checked={draft.strict_account_ledger_match !== false} onChange={(e) => setDraft((d) => ({ ...d, strict_account_ledger_match: e.target.checked }))} /><span className="text-sm text-slate-300">托管 ledger 必须匹配当前 owner/account</span></label>
                </div>
                <label className="space-y-1">
                  <div className="field-label">confirmation_token</div>
                  <input
                    className={`input-base font-mono ${liveAutoSubmitEnabled && !String(draft.confirmation_token || "").trim() ? "border-rose-400/70 text-rose-100" : ""}`}
                    value={draft.confirmation_token || ""}
                    onChange={(e) => setDraft((d) => ({ ...d, confirmation_token: e.target.value }))}
                    placeholder="开启实盘自动提交前必填"
                  />
                  <div className={`text-xs ${liveAutoSubmitEnabled && !String(draft.confirmation_token || "").trim() ? "text-rose-200" : "text-slate-500"}`}>
                    只有关闭演练模式且允许自动提交时才需要；保存时还会要求输入 CONFIRM_LIVE_AUTO_SUBMIT。
                  </div>
                </label>
              </div>

              <div className="mt-5 grid grid-cols-2 gap-3">
                <label className="col-span-2 space-y-1">
                  <div className="field-label">期权结构</div>
                  <select className="input-base" value={draft.strategy?.mode || "long_call"} onChange={(e) => updateStrategy({ mode: e.target.value })}>
                    <option value="long_call">Long Call</option>
                    <option value="call_debit_spread">Call Debit Spread（组合托管）</option>
                  </select>
                  {draft.strategy?.mode === "call_debit_spread" ? (
                    <div className="text-xs text-amber-200">价差会按组合托管：先买回短腿，再卖出长腿；只在组合止盈/止损/DTE/趋势退出时整组平仓。</div>
                  ) : null}
                </label>
                <label className="space-y-1"><div className="field-label">最小趋势分</div><input className="input-base" type="number" min={1} max={5} value={draft.strategy?.min_trend_score ?? 3} onChange={(e) => updateStrategy({ min_trend_score: Math.max(1, Number(e.target.value) || 3) })} /></label>
                <label className="space-y-1"><div className="field-label">目标 DTE</div><input className="input-base" type="number" min={21} value={draft.strategy?.target_dte ?? 90} onChange={(e) => updateStrategy({ target_dte: Math.max(21, Number(e.target.value) || 90) })} /></label>
                <label className="space-y-1"><div className="field-label">OTM 百分比</div><input className="input-base" type="number" min={0} step={0.5} value={(draft.strategy?.fallback_otm_pct ?? 0.03) * 100} onChange={(e) => updateStrategy({ fallback_otm_pct: Math.max(0, Number(e.target.value) || 0) / 100 })} /></label>
                <label className="space-y-1"><div className="field-label">价差宽度 %</div><input className="input-base" type="number" min={1} step={1} value={(draft.strategy?.spread_width_pct ?? 0.05) * 100} onChange={(e) => updateStrategy({ spread_width_pct: Math.max(1, Number(e.target.value) || 5) / 100 })} /></label>
                <label className="space-y-1"><div className="field-label">价差最大净权利金($)</div><input className="input-base" type="number" min={0} value={draft.strategy?.max_spread_debit ?? 600} onChange={(e) => updateStrategy({ max_spread_debit: Math.max(0, Number(e.target.value) || 0) })} /></label>
                <label className="space-y-1"><div className="field-label">最大 bid/ask spread %</div><input className="input-base" type="number" min={0} step={1} value={(draft.strategy?.max_bid_ask_spread_pct ?? 0.18) * 100} onChange={(e) => updateStrategy({ max_bid_ask_spread_pct: Math.max(0, Number(e.target.value) || 0) / 100 })} /></label>
                <label className="space-y-1"><div className="field-label">止盈 %</div><input className="input-base" type="number" min={0} step={5} value={(draft.strategy?.take_profit_pct ?? 0.8) * 100} onChange={(e) => updateStrategy({ take_profit_pct: Math.max(0, Number(e.target.value) || 0) / 100 })} /></label>
                <label className="space-y-1"><div className="field-label">止损 %</div><input className="input-base" type="number" min={0} step={5} value={(draft.strategy?.stop_loss_pct ?? 0.45) * 100} onChange={(e) => updateStrategy({ stop_loss_pct: Math.max(0, Number(e.target.value) || 0) / 100 })} /></label>
                <label className="space-y-1"><div className="field-label">到期前退出 DTE</div><input className="input-base" type="number" min={1} value={draft.strategy?.dte_exit_days ?? 21} onChange={(e) => updateStrategy({ dte_exit_days: Math.max(1, Number(e.target.value) || 21) })} /></label>
                <label className="space-y-1"><div className="field-label">跌破 MA 退出</div><input className="input-base" type="number" min={5} value={draft.strategy?.trend_exit_below_ma ?? 50} onChange={(e) => updateStrategy({ trend_exit_below_ma: Math.max(5, Number(e.target.value) || 50) })} /></label>
              </div>

              <div className="mt-5 grid grid-cols-2 gap-3">
                <label className="space-y-1"><div className="field-label">单笔最大张数</div><input className="input-base" type="number" min={1} value={draft.risk?.max_contracts_per_order ?? 1} onChange={(e) => updateRisk({ max_contracts_per_order: Math.max(1, Number(e.target.value) || 1) })} /></label>
                <label className="space-y-1"><div className="field-label">最大总张数</div><input className="input-base" type="number" min={1} value={draft.risk?.max_open_contracts ?? 10} onChange={(e) => updateRisk({ max_open_contracts: Math.max(1, Number(e.target.value) || 10) })} /></label>
                <label className="space-y-1"><div className="field-label">单笔最大权利金($)</div><input className="input-base" type="number" min={0} value={draft.risk?.max_premium_per_order ?? 800} onChange={(e) => updateRisk({ max_premium_per_order: Math.max(0, Number(e.target.value) || 0) })} /></label>
                <label className="space-y-1"><div className="field-label">单标的最大权利金($)</div><input className="input-base" type="number" min={0} value={draft.risk?.max_premium_per_symbol ?? 1500} onChange={(e) => updateRisk({ max_premium_per_symbol: Math.max(0, Number(e.target.value) || 0) })} /></label>
                <label className="space-y-1"><div className="field-label">今日新增权利金上限($)</div><input className="input-base" type="number" min={0} value={draft.risk?.max_new_premium_per_day ?? 1500} onChange={(e) => updateRisk({ max_new_premium_per_day: Math.max(0, Number(e.target.value) || 0) })} /></label>
                <label className="space-y-1"><div className="field-label">总期权权利金预算($)</div><input className="input-base" type="number" min={0} value={draft.risk?.max_total_option_premium ?? 4000} onChange={(e) => updateRisk({ max_total_option_premium: Math.max(0, Number(e.target.value) || 0) })} /></label>
              </div>

              <div className="mt-5 space-y-3">
                <label className="space-y-1">
                  <div className="field-label">底层黑名单</div>
                  <input className="input-base font-mono" value={blacklistText} onChange={(e) => setBlacklistText(e.target.value)} placeholder="ROBN.US, DRAM.US" />
                </label>
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="field-label">事件黑名单</div>
                    <button
                      type="button"
                      className="text-xs text-cyan-300 hover:underline"
                      onClick={() => setEventRows((rows) => [...rows, { symbol: "", start: "", end: "", reason: "manual" }])}
                    >
                      添加事件
                    </button>
                  </div>
                  <div className="space-y-2">
                    {eventRows.map((row, idx) => (
                      <div key={idx} className="grid grid-cols-[1fr_1fr_1fr_1fr_auto] gap-2 rounded-lg border border-slate-700 bg-slate-950/35 p-2">
                        <input className="input-base font-mono text-xs" placeholder="标的" value={row.symbol || ""} onChange={(e) => updateEventRow(idx, { symbol: e.target.value.toUpperCase() })} />
                        <input className="input-base text-xs" type="date" value={row.start || ""} onChange={(e) => updateEventRow(idx, { start: e.target.value })} />
                        <input className="input-base text-xs" type="date" value={row.end || ""} onChange={(e) => updateEventRow(idx, { end: e.target.value })} />
                        <input className="input-base text-xs" placeholder="原因" value={row.reason || ""} onChange={(e) => updateEventRow(idx, { reason: e.target.value })} />
                        <button type="button" className="px-2 text-xs text-rose-300 hover:underline" onClick={() => setEventRows((rows) => rows.filter((_, i) => i !== idx))}>删除</button>
                      </div>
                    ))}
                    {!eventRows.length ? <div className="rounded-lg border border-slate-700 bg-slate-950/35 p-3 text-xs text-slate-500">暂无事件窗口。可添加财报、FDA、宏观事件等禁止新开仓时间段。</div> : null}
                  </div>
                </div>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                <button className="btn-primary" disabled={Boolean(busy)} onClick={() => void saveConfig()}>{busy === "save" ? "保存中..." : "保存配置"}</button>
                <button className="btn-secondary" disabled={Boolean(busy)} onClick={() => void loadConfig()}>重新加载</button>
              </div>
            </section>

            <section className="panel">
              <div className="mb-3 flex items-center justify-between gap-2">
                <div className="text-lg font-semibold text-slate-100">决策日志</div>
                <button className="text-xs text-cyan-300 underline-offset-2 hover:underline" onClick={() => void mutateTail()}>刷新</button>
              </div>
              <div className="max-h-80 space-y-2 overflow-auto">
                {(tail?.items || []).slice().reverse().map((item, idx) => {
                  const action = asRecord(item.action);
                  return (
                    <div key={`${String(item.at || "")}-${idx}`} className="rounded border border-slate-800 bg-slate-950/50 p-2 text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-slate-500">{shortTime(item.at)}</span>
                        <span className="text-slate-200">{String(action?.action || "-")}</span>
                      </div>
                      <div className="mt-1 text-slate-500">{String(item.symbol || "-")}</div>
                    </div>
                  );
                })}
                {!(tail?.items || []).length ? <div className="text-sm text-slate-500">暂无日志。</div> : null}
              </div>
              {tail?.path ? <div className="mt-2 truncate font-mono text-[10px] text-slate-600">{tail.path}</div> : null}
            </section>
          </div>
        </div>
      </PageShell>
    </>
  );
}
