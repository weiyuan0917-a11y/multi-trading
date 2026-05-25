"use client";

import { useEffect, useMemo, useState } from "react";
import { localAgentGet as apiGet, localAgentPost as apiPost } from "@/lib/local-agent-api";
import { positionContractMultiplier } from "@/lib/us-option-position";
import { EntitlementNotice } from "@/components/entitlement-guard";
import { PageShell } from "@/components/ui/page-shell";
import { buildSwrOptions, SWR_INTERVALS } from "@/lib/swr-config";
import { useEntitlements } from "@/lib/use-entitlements";
import useSWR from "swr";

type SetupAccountItem = {
  account_id: string;
  is_default?: boolean;
};

type SetupAccountsResponse = {
  default_account_id?: string | null;
  accounts?: SetupAccountItem[];
};

type Leg = { symbol: string; side: "buy" | "sell"; contracts: number; price: string };
type OptionTemplate = "bull_call_spread" | "bear_put_spread" | "straddle" | "strangle";

type OptionLegQuote = {
  last_done?: number | null;
  prev_close?: number | null;
  volume?: number | null;
  timestamp?: string | null;
};

function formatOptionLast(q: OptionLegQuote | null | undefined): string {
  if (!q || q.last_done == null || Number.isNaN(Number(q.last_done))) return "—";
  return Number(q.last_done).toFixed(2);
}

const fmtUsd = (v: number) =>
  Number.isFinite(v) ? v.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) : "—";

function optionQuoteTitle(side: string, q: OptionLegQuote | null | undefined): string | undefined {
  if (!q?.timestamp && q?.prev_close == null && (q?.volume == null || q.volume === 0)) return undefined;
  const parts = [`${side} 行情`];
  if (q.last_done != null) parts.push(`最新 ${Number(q.last_done).toFixed(4)}`);
  if (q.prev_close != null) parts.push(`昨收 ${Number(q.prev_close).toFixed(4)}`);
  if (q.volume != null && q.volume > 0) parts.push(`量 ${q.volume}`);
  if (q.timestamp) parts.push(`时间 ${q.timestamp}`);
  return parts.join(" · ");
}

const BT_FORM_STORAGE_KEY = "options_backtest_form_v1";

type ChainPagination = { offset: number; limit: number; total: number; has_more: boolean };
type OptionPnlDay = {
  date: string;
  realized_pnl: number;
  realized_return_pct?: number | null;
  closed_contracts: number;
  trades: number;
};
type OptionPnlDetail = {
  symbol: string;
  close_side: "buy" | "sell";
  contracts: number;
  entry_price: number;
  exit_price: number;
  entry_fee_per_contract: number;
  exit_fee_per_contract: number;
  realized_pnl: number;
};

function ymd(d: Date): string {
  const y = d.getFullYear();
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const dd = `${d.getDate()}`.padStart(2, "0");
  return `${y}-${m}-${dd}`;
}

function monthStart(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function monthEnd(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0);
}

function addMonth(d: Date, delta: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + delta, 1);
}

export default function OptionsPage() {
  const entitlements = useEntitlements();
  const canTradeOptions = entitlements.canUse("option_auto_trading");
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [symbol, setSymbol] = useState("AAPL.US");
  const [expiry, setExpiry] = useState("");
  const [expiries, setExpiries] = useState<string[]>([]);
  const [chain, setChain] = useState<any[]>([]);
  const [chainOffset, setChainOffset] = useState(0);
  const [chainLimit, setChainLimit] = useState(80);
  const [chainPagination, setChainPagination] = useState<ChainPagination | null>(null);
  const [chainLoading, setChainLoading] = useState(false);
  const [token, setToken] = useState("");
  const [legs, setLegs] = useState<Leg[]>([
    { symbol: "", side: "buy", contracts: 1, price: "" },
    { symbol: "", side: "sell", contracts: 1, price: "" },
  ]);
  const [feeEstimate, setFeeEstimate] = useState<any>(null);
  const [btTemplate, setBtTemplate] = useState<OptionTemplate>("straddle");
  const [btDays, setBtDays] = useState(180);
  const [btPeriods, setBtPeriods] = useState(180);
  const [btKline, setBtKline] = useState("1d");
  const [btUseServerKline, setBtUseServerKline] = useState(false);
  const [btHoldingDays, setBtHoldingDays] = useState(20);
  const [btContracts, setBtContracts] = useState(1);
  const [btWidthPct, setBtWidthPct] = useState("0.05");
  const [btResult, setBtResult] = useState<any>(null);
  const [btLoading, setBtLoading] = useState(false);
  const [calendarMonth, setCalendarMonth] = useState<Date>(() => monthStart(new Date()));
  const [pnlSymbolFilter, setPnlSymbolFilter] = useState("");
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [pnlRefreshNonce, setPnlRefreshNonce] = useState(0);
  const [message, setMessage] = useState("");
  const { data: accountsResp } = useSWR(
    "/setup/accounts",
    (path: string) => apiGet<SetupAccountsResponse>(path),
    buildSwrOptions(SWR_INTERVALS.slowPage.refreshInterval, SWR_INTERVALS.slowPage.dedupingInterval)
  );
  const accountOptions = accountsResp?.accounts || [];
  const defaultAccountId = String(accountsResp?.default_account_id || "").trim();
  const effectiveAccountId = selectedAccountId || defaultAccountId;
  const accountQuery = effectiveAccountId ? `account_id=${encodeURIComponent(effectiveAccountId)}` : "";

  useEffect(() => {
    if (selectedAccountId) return;
    if (defaultAccountId) setSelectedAccountId(defaultAccountId);
  }, [defaultAccountId, selectedAccountId]);

  const { data: ordersResp, mutate: mutateOrders } = useSWR(
    canTradeOptions ? `/options/orders${accountQuery ? `?${accountQuery}` : ""}` : null,
    (path: string) => apiGet<any>(path),
    buildSwrOptions(SWR_INTERVALS.normalPoll.refreshInterval, SWR_INTERVALS.normalPoll.dedupingInterval)
  );
  const { data: positionsResp, mutate: mutatePositions } = useSWR(
    canTradeOptions ? `/options/positions${accountQuery ? `?${accountQuery}` : ""}` : null,
    (path: string) => apiGet<any>(path),
    buildSwrOptions(SWR_INTERVALS.normalPoll.refreshInterval, SWR_INTERVALS.normalPoll.dedupingInterval)
  );
  const fromDate = ymd(monthStart(calendarMonth));
  const toDate = ymd(monthEnd(calendarMonth));
  const pnlSymbolQuery = pnlSymbolFilter.trim().toUpperCase();
  const pnlApiPath = `/options/pnl-calendar?from_date=${fromDate}&to_date=${toDate}&tz=${encodeURIComponent(
    "America/New_York"
  )}${pnlSymbolQuery ? `&symbol=${encodeURIComponent(pnlSymbolQuery)}` : ""}${
    accountQuery ? `&${accountQuery}` : ""
  }${
    pnlRefreshNonce ? `&_t=${pnlRefreshNonce}` : ""
  }`;
  const { data: pnlResp, mutate: mutatePnl, isValidating: pnlLoading } = useSWR(
    canTradeOptions ? pnlApiPath : null,
    (path: string) => apiGet<any>(path),
    buildSwrOptions(SWR_INTERVALS.normalPoll.refreshInterval, SWR_INTERVALS.normalPoll.dedupingInterval)
  );
  const orders: any[] = ordersResp?.orders || [];
  const positions: any[] = positionsResp?.positions || [];
  const pnlDays: OptionPnlDay[] = pnlResp?.days || [];
  const pnlMap = useMemo(() => {
    const m = new Map<string, OptionPnlDay>();
    for (const d of pnlDays) m.set(String(d.date), d);
    return m;
  }, [pnlDays]);
  const pnlDetailsByDate = (pnlResp?.details_by_date || {}) as Record<string, OptionPnlDetail[]>;
  const selectedDayDetails = selectedDay ? pnlDetailsByDate[selectedDay] || [] : [];
  const monthGrid = useMemo(() => {
    const first = monthStart(calendarMonth);
    const last = monthEnd(calendarMonth);
    const leading = (first.getDay() + 6) % 7;
    const total = last.getDate();
    const out: Array<Date | null> = [];
    for (let i = 0; i < leading; i += 1) out.push(null);
    for (let d = 1; d <= total; d += 1) out.push(new Date(first.getFullYear(), first.getMonth(), d));
    while (out.length % 7 !== 0) out.push(null);
    return out;
  }, [calendarMonth]);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(BT_FORM_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      const templates: OptionTemplate[] = ["bull_call_spread", "bear_put_spread", "straddle", "strangle"];
      if (templates.includes(parsed?.btTemplate)) setBtTemplate(parsed.btTemplate);
      if (Number.isFinite(Number(parsed?.btDays))) setBtDays(Number(parsed.btDays));
      if (Number.isFinite(Number(parsed?.btPeriods))) setBtPeriods(Number(parsed.btPeriods));
      if (typeof parsed?.btKline === "string") setBtKline(parsed.btKline);
      if (typeof parsed?.btUseServerKline === "boolean") setBtUseServerKline(parsed.btUseServerKline);
      if (Number.isFinite(Number(parsed?.btHoldingDays))) setBtHoldingDays(Number(parsed.btHoldingDays));
      if (Number.isFinite(Number(parsed?.btContracts))) setBtContracts(Number(parsed.btContracts));
      if (typeof parsed?.btWidthPct === "string") setBtWidthPct(parsed.btWidthPct);
    } catch {
      // Ignore invalid local cache.
    }
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        BT_FORM_STORAGE_KEY,
        JSON.stringify({
          btTemplate,
          btDays,
          btPeriods,
          btKline,
          btUseServerKline,
          btHoldingDays,
          btContracts,
          btWidthPct,
        })
      );
    } catch {
      // Ignore storage failures (private mode / quota).
    }
  }, [btTemplate, btDays, btPeriods, btKline, btUseServerKline, btHoldingDays, btContracts, btWidthPct]);

  const canSubmit = useMemo(
    () => legs.filter((x) => x.symbol.trim()).length > 0 && token.trim().length > 0,
    [legs, token]
  );

  const loadExpiries = async () => {
    if (!canTradeOptions) {
      setMessage("期权交易链路需要 Premium。");
      return;
    }
    try {
      const r = await apiGet<any>(
        `/options/expiries?symbol=${encodeURIComponent(symbol)}${accountQuery ? `&${accountQuery}` : ""}`
      );
      setExpiries(r.expiries || []);
      if (!expiry && r.expiries?.length) setExpiry(String(r.expiries[0]));
      setMessage("");
    } catch (e: any) {
      setMessage(String(e.message || e));
    }
  };

  const loadChain = async (offsetArg: number = chainOffset) => {
    if (!canTradeOptions) {
      setMessage("期权链查询需要 Premium。");
      return;
    }
    try {
      setChainLoading(true);
      const params = new URLSearchParams({
        symbol,
        limit: String(chainLimit),
        offset: String(Math.max(0, Math.floor(offsetArg))),
      });
      if (effectiveAccountId) params.set("account_id", effectiveAccountId);
      if (expiry) params.set("expiry_date", expiry);
      const r = await apiGet<any>(`/options/chain?${params.toString()}`);
      setChain(r.options || []);
      const p = r.pagination as ChainPagination | undefined;
      setChainPagination(p ?? null);
      if (p) setChainOffset(p.offset);
      setMessage("");
    } catch (e: any) {
      setMessage(String(e.message || e));
    } finally {
      setChainLoading(false);
    }
  };

  const estimateFee = async () => {
    if (!canTradeOptions) {
      setMessage("期权费用试算需要 Premium。");
      return;
    }
    try {
      const payload = {
        legs: legs
          .filter((x) => x.symbol.trim())
          .map((x) => ({
            symbol: x.symbol.trim(),
            side: x.side,
            contracts: Number(x.contracts),
            price: Number(x.price || 0),
          })),
      };
      if (effectiveAccountId) (payload as any).account_id = effectiveAccountId;
      const r = await apiPost<any>("/options/fee-estimate", payload);
      setFeeEstimate(r.estimate);
      setMessage("");
    } catch (e: any) {
      setMessage(String(e.message || e));
    }
  };

  const submitOrder = async () => {
    if (!canTradeOptions) {
      setMessage("期权下单需要 Premium。");
      return;
    }
    if (!confirm("确认提交期权订单？")) return;
    try {
      const payload = {
        legs: legs
          .filter((x) => x.symbol.trim())
          .map((x) => ({
            symbol: x.symbol.trim(),
            side: x.side,
            contracts: Number(x.contracts),
            price: Number(x.price || 0),
          })),
        confirmation_token: token.trim(),
      };
      if (effectiveAccountId) (payload as any).account_id = effectiveAccountId;
      const r = await apiPost<any>("/options/order", payload);
      setMessage(`下单成功: ${JSON.stringify(r)}`);
      await loadOrders();
      await loadPositions();
      await loadPnl();
    } catch (e: any) {
      setMessage(String(e.message || e));
    }
  };

  const loadOrders = async () => {
    try {
      await mutateOrders();
    } catch (e: any) {
      setMessage(String(e.message || e));
    }
  };

  const loadPositions = async () => {
    try {
      await mutatePositions();
    } catch (e: any) {
      setMessage(String(e.message || e));
    }
  };

  const loadPnl = async () => {
    try {
      const nonce = Date.now();
      setPnlRefreshNonce(nonce);
      await mutatePnl(undefined, { revalidate: true });
    } catch (e: any) {
      setMessage(String(e.message || e));
    }
  };

  useEffect(() => {
    if (!selectedDay) return;
    if (selectedDay < fromDate || selectedDay > toDate) setSelectedDay(null);
  }, [selectedDay, fromDate, toDate]);

  const runOptionBacktest = async () => {
    try {
      setBtLoading(true);
      const payload = {
        symbol: symbol.trim(),
        template: btTemplate,
        days: Number(btDays),
        periods: Math.max(0, Math.floor(Number(btPeriods) || 0)),
        kline: btKline,
        use_server_kline_cache: btUseServerKline,
        holding_days: Number(btHoldingDays),
        contracts: Number(btContracts),
        width_pct: Number(btWidthPct || "0.05"),
      };
      const r = await apiPost<any>(
        "/backtests",
        { kind: "options_combo", request: payload },
        { timeoutMs: 60000, retries: 0 }
      );
      setBtResult(r?.result?.raw || r);
      setMessage("");
    } catch (e: any) {
      setMessage(String(e.message || e));
    } finally {
      setBtLoading(false);
    }
  };

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">期权交易中心</h1>
            <div className="mt-1 text-sm text-slate-300">期权链路 · 多腿建仓 · 成本试算 · 通用期权组合回测</div>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="tag-muted">
              账户
              <select
                className="ml-2 rounded border border-slate-700/70 bg-slate-900/80 px-2 py-0.5 text-xs text-slate-200"
                value={effectiveAccountId}
                onChange={(e) => setSelectedAccountId(e.target.value)}
              >
                {accountOptions.map((ac) => (
                  <option key={ac.account_id} value={ac.account_id}>
                    {ac.account_id}
                    {ac.is_default ? " (default)" : ""}
                  </option>
                ))}
                {!accountOptions.length ? <option value={effectiveAccountId || ""}>default</option> : null}
              </select>
            </span>
            <span className="tag-muted">标的 {symbol || "-"}</span>
            <span className="tag-muted">到期日 {expiry || "未选择"}</span>
          </div>
        </div>
      </div>
      {!canTradeOptions ? (
        <EntitlementNotice feature="option_auto_trading" plan={entitlements.plan} title="期权交易链路需要 Premium" />
      ) : null}
      {message ? <div className="panel border-amber-200 bg-amber-50 text-amber-700">{message}</div> : null}

      <div className="panel space-y-3">
        <div className="section-title">期权链路查询</div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-5">
          <input className="input-base" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
          <button className="btn-secondary" disabled={!canTradeOptions} onClick={loadExpiries}>
            加载到期日
          </button>
          <select className="input-base" value={expiry} onChange={(e) => setExpiry(e.target.value)}>
            <option value="">选择到期日</option>
            {expiries.map((x) => (
              <option key={x} value={x}>
                {x}
              </option>
            ))}
          </select>
          <select
            className="input-base"
            value={String(chainLimit)}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (!Number.isFinite(n)) return;
              setChainLimit(n);
              setChainPagination(null);
              setChain([]);
            }}
            title="每页行权价条数"
          >
            {[40, 80, 120, 200].map((n) => (
              <option key={n} value={n}>
                每页 {n}
              </option>
            ))}
          </select>
          <button className="btn-primary" disabled={chainLoading || !canTradeOptions} onClick={() => void loadChain(0)}>
            查询期权链
          </button>
        </div>
        <p className="text-xs text-slate-500">
          最新价来自 LongPort 实时行情（<span className="font-mono">last_done</span>）；需账户具备美股期权（OPRA）行情权限，无权限或无成交时可能显示「—」。
        </p>
        <div className="table-shell overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="table-head text-left">
              <tr>
                <th className="px-3 py-2">执行价</th>
                <th className="px-3 py-2">Call 代码</th>
                <th className="px-3 py-2 text-right">Call 最新</th>
                <th className="px-3 py-2">Put 代码</th>
                <th className="px-3 py-2 text-right">Put 最新</th>
              </tr>
            </thead>
            <tbody>
              {chain.map((x, idx) => (
                <tr key={`${x.strike_price}-${idx}`} className="border-t border-slate-800/90">
                  <td className="px-3 py-2 font-mono">{x.strike_price ?? "-"}</td>
                  <td className="max-w-[200px] truncate px-3 py-2 font-mono text-xs" title={x.call_symbol || undefined}>
                    {x.call_symbol || "—"}
                  </td>
                  <td
                    className="px-3 py-2 text-right font-mono text-cyan-200"
                    title={optionQuoteTitle("Call", x.call_quote as OptionLegQuote | undefined)}
                  >
                    {formatOptionLast(x.call_quote as OptionLegQuote | undefined)}
                  </td>
                  <td className="max-w-[200px] truncate px-3 py-2 font-mono text-xs" title={x.put_symbol || undefined}>
                    {x.put_symbol || "—"}
                  </td>
                  <td
                    className="px-3 py-2 text-right font-mono text-cyan-200"
                    title={optionQuoteTitle("Put", x.put_quote as OptionLegQuote | undefined)}
                  >
                    {formatOptionLast(x.put_quote as OptionLegQuote | undefined)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {chainPagination ? (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800/80 pt-3 text-xs text-slate-400">
            <span>
              行权价{" "}
              {chain.length
                ? `${chainPagination.offset + 1}–${chainPagination.offset + chain.length}`
                : "0"}{" "}
              / 共 {chainPagination.total}
              {chainLoading ? <span className="ml-2 text-cyan-300/90">加载中…</span> : null}
            </span>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="btn-secondary text-xs"
                disabled={chainLoading || chainPagination.offset <= 0}
                onClick={() => {
                  const prev = Math.max(0, chainPagination.offset - chainPagination.limit);
                  void loadChain(prev);
                }}
              >
                上一页
              </button>
              <button
                type="button"
                className="btn-secondary text-xs"
                disabled={chainLoading || !chainPagination.has_more}
                onClick={() => void loadChain(chainPagination.offset + chainPagination.limit)}
              >
                下一页
              </button>
            </div>
          </div>
        ) : null}
      </div>

      <div className="panel space-y-3">
        <div className="section-title">策略建仓器 + 下单确认</div>
        {legs.map((leg, idx) => (
          <div key={idx} className="grid grid-cols-1 gap-2 md:grid-cols-4">
            <input
              className="input-base"
              placeholder="期权代码"
              value={leg.symbol}
              onChange={(e) => setLegs((s) => s.map((x, i) => (i === idx ? { ...x, symbol: e.target.value } : x)))}
            />
            <select
              className="input-base"
              value={leg.side}
              onChange={(e) => setLegs((s) => s.map((x, i) => (i === idx ? { ...x, side: e.target.value as "buy" | "sell" } : x)))}
            >
              <option value="buy">买入</option>
              <option value="sell">卖出</option>
            </select>
            <input
              className="input-base"
              type="number"
              value={leg.contracts}
              onChange={(e) => setLegs((s) => s.map((x, i) => (i === idx ? { ...x, contracts: Number(e.target.value) } : x)))}
            />
            <input
              className="input-base"
              placeholder="限价(可空)"
              value={leg.price}
              onChange={(e) => setLegs((s) => s.map((x, i) => (i === idx ? { ...x, price: e.target.value } : x)))}
            />
          </div>
        ))}
        <input
          className="input-base"
          placeholder="confirmation_token (L3 必填)"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <div className="flex gap-2">
          <button className="btn-secondary" disabled={!canTradeOptions} onClick={estimateFee}>
            试算费用
          </button>
          <button className="btn-primary" disabled={!canSubmit || !canTradeOptions} onClick={submitOrder}>
            确认下单
          </button>
        </div>
        {feeEstimate ? (
          <div className="rounded-lg border border-slate-700/70 p-3 text-sm text-slate-300">
            总费用: {feeEstimate.total_fee} | 最大亏损估算: {feeEstimate.max_loss_estimate}
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="panel">
          <div className="mb-2 flex items-center justify-between">
            <div className="field-label">期权订单</div>
            <button className="btn-secondary" disabled={!canTradeOptions} onClick={loadOrders}>
              刷新
            </button>
          </div>
          <div className="space-y-2 text-sm">
            {orders.map((o, idx) => (
              <div key={`${o.order_id}-${idx}`} className="rounded border border-slate-700/60 p-2">
                {o.symbol} | {o.side} | {o.quantity} | {o.status}
              </div>
            ))}
          </div>
        </div>
        <div className="panel">
          <div className="mb-2 flex items-center justify-between">
            <div className="field-label">期权持仓</div>
            <button className="btn-secondary" disabled={!canTradeOptions} onClick={loadPositions}>
              刷新
            </button>
          </div>
          <div className="space-y-2 text-sm">
            <p className="text-xs text-slate-500">
              成本/市值按「张数 × 每股权利金 × 100」显示为美元（OCC 代码 .US）；盈亏同口径。
            </p>
            {positions.map((p, idx) => {
              const sym = String(p.symbol ?? "");
              const mult = positionContractMultiplier(sym);
              const qty = Number(p.quantity ?? 0);
              const cost = Number(p.cost_price ?? 0);
              const last = Number(p.current_price ?? p.last_done ?? 0);
              const costUsd = mult !== 1 ? qty * cost * mult : null;
              const lastUsd = mult !== 1 ? qty * last * mult : null;
              const pnlUsd = mult !== 1 ? (last - cost) * qty * mult : Number(p.pnl ?? 0);
              return (
                <div key={`${p.symbol}-${idx}`} className="rounded border border-slate-700/60 p-2">
                  {sym} | 张数 {qty}
                  {mult !== 1 ? (
                    <>
                      {" "}
                      | 成本(美元) {fmtUsd(costUsd ?? 0)} | 市值(美元) {fmtUsd(lastUsd ?? 0)} | 浮动盈亏 {fmtUsd(pnlUsd)}
                    </>
                  ) : (
                    <> | PnL {p.pnl}</>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="panel space-y-3">
        <div className="mb-2 flex items-center justify-between">
          <div className="section-title">期权交易收益日历（已实现）</div>
          <button className="btn-secondary" onClick={loadPnl} disabled={pnlLoading || !canTradeOptions}>
            {pnlLoading ? "刷新中..." : "刷新"}
          </button>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <button className="btn-secondary text-xs" onClick={() => setCalendarMonth((d) => addMonth(d, -1))}>
              上月
            </button>
            <div className="rounded border border-slate-700/70 px-3 py-1 text-sm text-slate-200">
              {calendarMonth.getFullYear()}-{`${calendarMonth.getMonth() + 1}`.padStart(2, "0")}
            </div>
            <button className="btn-secondary text-xs" onClick={() => setCalendarMonth((d) => addMonth(d, 1))}>
              下月
            </button>
          </div>
          <div className="flex items-center gap-2">
            <input
              className="input-base h-8 w-44 text-xs"
              placeholder="筛选标的，如 QQQ"
              value={pnlSymbolFilter}
              onChange={(e) => setPnlSymbolFilter(e.target.value)}
            />
          </div>
          <div className="text-xs text-slate-400">
            区间 {fromDate} ~ {toDate}（时区：美东）
          </div>
        </div>
        <div className="grid grid-cols-7 gap-2 text-xs text-slate-400">
          {["一", "二", "三", "四", "五", "六", "日"].map((w) => (
            <div key={w} className="px-2 text-center">
              周{w}
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-2">
          {monthGrid.map((d, idx) => {
            if (!d) return <div key={`empty-${idx}`} className="min-h-[88px] rounded border border-transparent" />;
            const key = ymd(d);
            const row = pnlMap.get(key);
            const pnl = Number(row?.realized_pnl ?? 0);
            const ret = row?.realized_return_pct;
            const pnlClass = pnl > 0 ? "text-emerald-300" : pnl < 0 ? "text-rose-300" : "text-slate-300";
            const selected = selectedDay === key;
            return (
              <button
                key={key}
                type="button"
                onClick={() => setSelectedDay(key)}
                className={`min-h-[88px] rounded border bg-slate-900/40 p-2 text-left ${
                  selected ? "border-cyan-400/80 ring-1 ring-cyan-400/50" : "border-slate-700/70"
                }`}
              >
                <div className="text-xs text-slate-400">{d.getDate()}</div>
                <div className={`mt-1 text-sm font-medium ${pnlClass}`}>{fmtUsd(pnl)}</div>
                <div className="mt-1 text-[11px] text-slate-400">{ret == null ? "收益率 —" : `收益率 ${ret.toFixed(2)}%`}</div>
                <div className="text-[11px] text-slate-500">
                  {Number(row?.closed_contracts ?? 0)} 张 / {Number(row?.trades ?? 0)} 笔
                </div>
              </button>
            );
          })}
        </div>
        <div className="rounded border border-slate-700/70 p-2 text-xs text-slate-400">
          月累计：已实现收益{" "}
          <span
            className={`${Number(pnlResp?.summary?.total_realized_pnl ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}
          >
            {fmtUsd(Number(pnlResp?.summary?.total_realized_pnl ?? 0))}
          </span>
          {" · "}
          收益率{" "}
          {pnlResp?.summary?.total_realized_return_pct == null ? "—" : `${Number(pnlResp.summary.total_realized_return_pct).toFixed(2)}%`}
          {" · "}
          已平仓 {Number(pnlResp?.summary?.total_closed_contracts ?? 0)} 张
        </div>
        <div className="text-[11px] text-slate-500">
          数据诊断：订单 {Number(pnlResp?.debug?.orders_scanned ?? 0)}，详情 {Number(pnlResp?.debug?.order_details_loaded ?? 0)}，
          成交 {Number(pnlResp?.debug?.executions_parsed ?? 0)}（回退 {Number(pnlResp?.debug?.fallback_executions ?? 0)}）{" "}
          · 来源 {String(pnlResp?.debug?.execution_source ?? "-")} · 日志成交 {Number(pnlResp?.debug?.log_executions ?? 0)}
        </div>
        {selectedDay ? (
          <div className="space-y-2 rounded border border-slate-700/70 p-3">
            <div className="flex items-center justify-between text-sm">
              <div className="text-slate-200">
                {selectedDay} 成交配对明细（{selectedDayDetails.length} 条）
              </div>
              <button className="btn-secondary text-xs" onClick={() => setSelectedDay(null)}>
                关闭
              </button>
            </div>
            {selectedDayDetails.length === 0 ? (
              <div className="text-xs text-slate-500">该日无已实现平仓记录。</div>
            ) : (
              <div className="table-shell overflow-x-auto">
                <table className="w-full min-w-[760px] text-xs">
                  <thead className="table-head text-left">
                    <tr>
                      <th className="px-2 py-1">标的</th>
                      <th className="px-2 py-1">平仓方向</th>
                      <th className="px-2 py-1">张数</th>
                      <th className="px-2 py-1">开仓价</th>
                      <th className="px-2 py-1">平仓价</th>
                      <th className="px-2 py-1">开仓费/张</th>
                      <th className="px-2 py-1">平仓费/张</th>
                      <th className="px-2 py-1">已实现收益</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedDayDetails.map((r, idx) => (
                      <tr key={`${r.symbol}-${idx}`} className="border-t border-slate-800/90">
                        <td className="px-2 py-1 font-mono">{r.symbol}</td>
                        <td className="px-2 py-1">{r.close_side === "sell" ? "卖出平多" : "买入平空"}</td>
                        <td className="px-2 py-1">{r.contracts}</td>
                        <td className="px-2 py-1">{r.entry_price}</td>
                        <td className="px-2 py-1">{r.exit_price}</td>
                        <td className="px-2 py-1">{r.entry_fee_per_contract}</td>
                        <td className="px-2 py-1">{r.exit_fee_per_contract}</td>
                        <td className={`px-2 py-1 ${Number(r.realized_pnl) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                          {fmtUsd(Number(r.realized_pnl))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : null}
      </div>

      <div className="panel space-y-3">
        <div className="section-title">通用期权组合回测</div>
        <p className="text-xs text-slate-500">
          这里用于验证自定义期权结构本身，例如价差、跨式、宽跨式等组合的理论收益路径；自动交易策略参数请到「自动交易 / 期权 0DTE」里的「策略验证」区域处理。
        </p>
        <p className="text-xs text-slate-500">
          K 线参数与「回测中心」一致：<span className="font-mono">周期数 &gt; 0</span> 时按根数拉取；为{" "}
          <span className="font-mono">0</span> 时按「日历天数」拉取。持有时间为{" "}
          <span className="font-mono">K</span> 线根数（例如 1 分 K 下 60 根约 1 小时）。
        </p>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3 lg:grid-cols-6">
          <label className="space-y-1">
            <div className="field-label" title="选择期权策略模板：牛市价差/熊市价差/跨式/宽跨式。">
              策略模板
            </div>
            <select
              className="input-base"
              value={btTemplate}
              onChange={(e) => setBtTemplate(e.target.value as OptionTemplate)}
            >
              <option value="bull_call_spread">Bull Call Spread</option>
              <option value="bear_put_spread">Bear Put Spread</option>
              <option value="straddle">Straddle</option>
              <option value="strangle">Strangle</option>
            </select>
          </label>
          <label className="space-y-1">
            <div className="field-label" title="与回测中心相同：周期数为 0 时，按最近多少日历天拉 K 线。">
              日历天数
            </div>
            <input
              className="input-base"
              type="number"
              min={1}
              max={3650}
              value={btDays}
              onChange={(e) => setBtDays(Number(e.target.value))}
              placeholder="例如 180"
            />
          </label>
          <label className="space-y-1">
            <div className="field-label" title="与回测中心相同：大于 0 时优先按根数取最近 N 根 K 线。">
              周期数
            </div>
            <input
              className="input-base"
              type="number"
              min={0}
              value={btPeriods}
              onChange={(e) => setBtPeriods(Math.max(0, Math.floor(Number(e.target.value) || 0)))}
              placeholder="0 表示仅用日历天"
            />
          </label>
          <label className="space-y-1">
            <div className="field-label" title="与回测中心相同的 K 线周期。">
              K 线周期
            </div>
            <select className="input-base" value={btKline} onChange={(e) => setBtKline(e.target.value)}>
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
          <label className="flex cursor-pointer items-end gap-2 pb-2 md:col-span-2 lg:col-span-1">
            <input
              type="checkbox"
              className="h-4 w-4 shrink-0 rounded border-slate-600"
              checked={btUseServerKline}
              onChange={(e) => setBtUseServerKline(e.target.checked)}
            />
            <span className="text-sm text-slate-300" title="使用服务器 data/klines 已下载缓存，与回测中心「服务器 K 线」一致。">
              使用服务器 K 线缓存
            </span>
          </label>
          <label className="space-y-1">
            <div className="field-label" title="开仓到平仓之间相隔的 K 线根数（与日 K / 分钟 K 一致）。">
              持有K线根数
            </div>
            <input
              className="input-base"
              type="number"
              min={2}
              max={20000}
              value={btHoldingDays}
              onChange={(e) => setBtHoldingDays(Number(e.target.value))}
              placeholder="例如 20"
            />
          </label>
          <label className="space-y-1">
            <div className="field-label" title="每笔交易使用的期权合约张数，通常 1 张=100 股名义规模。">
              合约手数
            </div>
            <input
              className="input-base"
              type="number"
              min={1}
              max={50}
              value={btContracts}
              onChange={(e) => setBtContracts(Number(e.target.value))}
              placeholder="例如 1"
            />
          </label>
          <label className="space-y-1">
            <div className="field-label" title="执行价间距比例（如 0.05 表示 5%），用于价差/宽跨式模板。">
              价差宽度%
            </div>
            <input className="input-base" value={btWidthPct} onChange={(e) => setBtWidthPct(e.target.value)} placeholder="例如 0.05" />
          </label>
          <div className="space-y-1 lg:col-span-2">
            <div className="field-label opacity-0">运行</div>
            <button className="btn-primary w-full" onClick={runOptionBacktest} disabled={btLoading || !symbol.trim()}>
              {btLoading ? "组合回测中..." : "运行组合回测"}
            </button>
          </div>
        </div>

        {btResult?.stats ? (
          <div className="space-y-3">
            {btResult.kline != null ? (
              <div className="text-xs text-slate-400">
                本次 K 线：<span className="font-mono text-slate-300">{String(btResult.kline)}</span>
                {btResult.periods != null ? (
                  <>
                    {" "}
                    · 周期数 <span className="font-mono text-slate-300">{Number(btResult.periods)}</span>
                  </>
                ) : null}
                {btResult.holding_bars != null || btResult.holding_days != null ? (
                  <>
                    {" "}
                    · 持有{" "}
                    <span className="font-mono text-slate-300">{Number(btResult.holding_bars ?? btResult.holding_days)}</span> 根
                  </>
                ) : null}
              </div>
            ) : null}
            <div className="grid grid-cols-1 gap-2 md:grid-cols-4 text-sm">
              <div className="rounded border border-slate-700/70 p-2">总交易: {btResult.stats.total_trades}</div>
              <div className="rounded border border-slate-700/70 p-2">胜率: {btResult.stats.win_rate_pct}%</div>
              <div className="rounded border border-slate-700/70 p-2">净收益: {btResult.stats.total_net_pnl}</div>
              <div className="rounded border border-slate-700/70 p-2">收益率: {btResult.stats.total_return_pct}%</div>
            </div>
            <div className="rounded border border-slate-700/70 p-2 text-sm">
              总费用: {btResult.stats.total_fee} | 费用拆分:{" "}
              {Object.entries(btResult.stats.fee_breakdown || {})
                .map(([k, v]) => `${k}:${v}`)
                .join(" | ") || "-"}
            </div>
            <div className="table-shell">
              <table className="w-full text-sm">
                <thead className="table-head text-left">
                  <tr>
                    <th className="px-3 py-2">开仓</th>
                    <th className="px-3 py-2">平仓</th>
                    <th className="px-3 py-2">入场价</th>
                    <th className="px-3 py-2">出场价</th>
                    <th className="px-3 py-2">毛收益</th>
                    <th className="px-3 py-2">费用</th>
                    <th className="px-3 py-2">净收益</th>
                  </tr>
                </thead>
                <tbody>
                  {(btResult.trades || []).slice(0, 30).map((t: any, idx: number) => (
                    <tr key={`${t.entry_date}-${idx}`} className="border-t border-slate-800/90">
                      <td className="px-3 py-2">{t.entry_date}</td>
                      <td className="px-3 py-2">{t.exit_date}</td>
                      <td className="px-3 py-2">{t.entry_spot}</td>
                      <td className="px-3 py-2">{t.exit_spot}</td>
                      <td className="px-3 py-2">{t.gross_pnl}</td>
                      <td className="px-3 py-2">{t.fee}</td>
                      <td className={`px-3 py-2 ${Number(t.net_pnl) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                        {t.net_pnl}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </div>
    </PageShell>
  );
}
