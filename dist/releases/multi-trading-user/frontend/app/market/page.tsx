"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { MarketSourceControl } from "@/components/market-source-control";
import { PageShell } from "@/components/ui/page-shell";
import {
  isDashboardSummaryPersistable,
  isDashboardSummaryUsable,
  mergeDashboardAnalysisFromCache,
  mergeDashboardMacroIndicators,
  mergeDashboardMarketRowsFromCache,
  readDashboardSummaryCache,
  type DashboardSummaryCache,
  writeDashboardSummaryCache,
} from "@/lib/dashboard-summary-cache";
import { localAgentGet as apiGet } from "@/lib/local-agent-api";
import { buildSwrOptions, SWR_INTERVALS, visibilityAwareInterval } from "@/lib/swr-config";

type MarketRow = {
  symbol: string;
  name: string;
  last?: number | string | null;
  change_pct?: number | string | null;
  high?: number | string | null;
  low?: number | string | null;
  price_type?: string | null;
  source?: string | null;
  source_label?: string | null;
  as_of?: string | null;
  cached_at?: string | null;
  realtime?: boolean | null;
  cache?: boolean | null;
  stale?: boolean | null;
};

type SectorRow = {
  symbol: string;
  name: string;
  change_pct?: number | string | null;
};

type DashboardSummary = {
  markets?: {
    cn_hk?: MarketRow[];
    us?: MarketRow[];
  };
  market_data_status?: Record<
    string,
    {
      requested?: number;
      available?: number;
      missing_symbols?: string[];
      sources?: Record<string, number>;
      public_fallback_used?: boolean;
      broker_required?: boolean;
    }
  >;
  analysis?: {
    score?: number | string | null;
    market_environment?: string | null;
    strategy_recommendation?: string | null;
    indicators?: Record<string, any>;
  };
  sector_data_source?: string;
  sector_data_source_label?: string;
  sector_age_seconds?: number;
  sector_last_refresh_ts?: string;
  sector_top3?: SectorRow[];
  sector_bottom3?: SectorRow[];
};

type MarketDataGroupStatus = NonNullable<DashboardSummary["market_data_status"]>[string];

type ProviderStatusResponse = {
  providers?: ProviderStatusItem[];
};

type ProviderStatusItem = {
  id: string;
  name?: string;
  enabled?: boolean;
  configured?: boolean;
  status_text?: string;
};

type MarketStats = {
  count: number;
  advancers: number;
  decliners: number;
  flat: number;
  avgChange: number | null;
  leader?: MarketRow;
  laggard?: MarketRow;
};

const toNumber = (value: unknown): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
};

const formatNumber = (value: unknown, digits = 2) => {
  const num = toNumber(value);
  return num === null ? "-" : num.toFixed(digits);
};

const formatPct = (value: unknown) => {
  const num = toNumber(value);
  if (num === null) return "-";
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
};

const formatTime = (value: unknown) => {
  if (!value) return "-";
  const dt = new Date(String(value));
  if (Number.isNaN(dt.getTime())) return String(value);
  return dt.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const changeClass = (value: unknown) => {
  const num = toNumber(value);
  if (num === null) return "text-slate-400";
  if (num > 0) return "text-emerald-300";
  if (num < 0) return "text-rose-300";
  return "text-slate-300";
};

const rowMarket = (row: MarketRow) => {
  const symbol = String(row.symbol || "").toUpperCase();
  if (symbol.endsWith(".HK")) return "hk";
  if (symbol.endsWith(".SH") || symbol.endsWith(".SZ") || symbol.endsWith(".BJ")) return "cn";
  if (symbol.endsWith(".US")) return "us";
  return "other";
};

const computeStats = (rows: MarketRow[]): MarketStats => {
  const withChanges = rows
    .map((row) => ({ row, change: toNumber(row.change_pct) }))
    .filter((item): item is { row: MarketRow; change: number } => item.change !== null);

  const advancers = withChanges.filter((item) => item.change > 0).length;
  const decliners = withChanges.filter((item) => item.change < 0).length;
  const flat = withChanges.length - advancers - decliners;
  const avgChange = withChanges.length
    ? withChanges.reduce((sum, item) => sum + item.change, 0) / withChanges.length
    : null;
  const sorted = [...withChanges].sort((a, b) => b.change - a.change);

  return {
    count: rows.length,
    advancers,
    decliners,
    flat,
    avgChange,
    leader: sorted[0]?.row,
    laggard: sorted[sorted.length - 1]?.row,
  };
};

const marketTone = (stats: MarketStats) => {
  if (stats.avgChange === null || stats.count === 0) return "等待行情";
  if (stats.avgChange >= 1 && stats.advancers >= stats.decliners) return "强势上行";
  if (stats.avgChange > 0.15) return "温和修复";
  if (stats.avgChange <= -1) return "承压回落";
  if (stats.avgChange < -0.15) return "偏弱震荡";
  return "窄幅震荡";
};

const regionalSuggestion = (stats: MarketStats, marketName: string) => {
  if (stats.avgChange === null || stats.count === 0) {
    return `${marketName} 行情暂未返回，先等待快照刷新后再判断方向。`;
  }
  if (stats.avgChange > 0.6 && stats.advancers >= stats.decliners) {
    return "指数和宽度同步改善，可以关注强势主线延续，同时以前一个交易日低点控制回撤。";
  }
  if (stats.avgChange < -0.6 && stats.decliners > stats.advancers) {
    return "短线风险偏好偏弱，先降低追涨动作，等待核心指数企稳或放量反包。";
  }
  return "市场仍偏轮动，适合小仓位观察强弱切换，避免在无量区间里过度交易。";
};

const sourceSummary = (status?: MarketDataGroupStatus) => {
  if (!status) return "数据源：-";
  const sources = status.sources || {};
  const text = Object.entries(sources)
    .map(([source, count]) => `${source} ${count}`)
    .join("、");
  const base = text || "-";
  const fallback = status.public_fallback_used ? "公共源兜底" : "券商源";
  return `数据源：${base} · ${fallback} · ${status.available ?? 0}/${status.requested ?? 0}`;
};

const rowFreshness = (row: MarketRow) => {
  const source = row.source_label || row.source || row.price_type || "快照";
  const stamp = row.cached_at || row.as_of;
  if (row.stale || row.cache) {
    return {
      label: "缓存",
      className: "text-amber-300",
      detail: `${source} · ${formatTime(stamp)}`,
    };
  }
  if (row.realtime) {
    return {
      label: "实时",
      className: "text-emerald-300",
      detail: `${source} · ${formatTime(stamp)}`,
    };
  }
  return {
    label: "延迟",
    className: "text-cyan-300",
    detail: `${source} · ${formatTime(stamp)}`,
  };
};

function StatCell({ label, value, valueClass = "text-slate-100" }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="border-l border-slate-700/70 pl-3 first:border-l-0 first:pl-0">
      <div className="field-label">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${valueClass}`}>{value}</div>
    </div>
  );
}

function QuoteTable({ rows }: { rows: MarketRow[] }) {
  if (!rows.length) {
    return <div className="rounded-lg border border-slate-700/70 px-3 py-4 text-sm text-slate-400">暂无可用行情，等待接口刷新。</div>;
  }

  return (
    <div className="table-shell">
      <table className="min-w-full text-sm">
        <thead className="table-head">
          <tr className="text-left">
            <th className="px-3 py-2">名称</th>
            <th className="px-3 py-2">代码</th>
            <th className="px-3 py-2">最新价</th>
            <th className="px-3 py-2">涨跌幅</th>
            <th className="px-3 py-2">新鲜度</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.symbol} className="border-t border-slate-700/70 hover:bg-blue-50">
              <td className="px-3 py-2 text-slate-100">{row.name || row.symbol}</td>
              <td className="px-3 py-2 font-mono text-xs text-slate-400">{row.symbol}</td>
              <td className="px-3 py-2 font-mono">{formatNumber(row.last)}</td>
              <td className={`px-3 py-2 font-mono ${changeClass(row.change_pct)}`}>{formatPct(row.change_pct)}</td>
              <td className="px-3 py-2 text-xs text-slate-400">
                {(() => {
                  const freshness = rowFreshness(row);
                  return (
                    <div className="min-w-[150px]">
                      <span className={freshness.className}>{freshness.label}</span>
                      <div className="mt-0.5 text-[11px] text-slate-500">{freshness.detail}</div>
                    </div>
                  );
                })()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SectorList({ title, rows, tone }: { title: string; rows: SectorRow[]; tone: "up" | "down" }) {
  return (
    <div>
      <div className="section-title mb-2">{title}</div>
      <div className="space-y-1">
        {(rows || []).slice(0, 3).map((row) => (
          <div key={row.symbol} className="flex items-center justify-between rounded-md px-2 py-1 text-sm hover:bg-blue-50">
            <span className="text-slate-200">{row.name}</span>
            <span className={tone === "up" ? "text-emerald-300" : "text-rose-300"}>{formatPct(row.change_pct)}</span>
          </div>
        ))}
        {!rows?.length ? <div className="px-2 py-1 text-sm text-slate-500">暂无板块数据</div> : null}
      </div>
    </div>
  );
}

function ApiProviderPill({ provider }: { provider?: ProviderStatusItem }) {
  const ready = Boolean(provider?.enabled && provider?.configured);
  const label = provider?.name || provider?.id || "-";
  return (
    <span className={ready ? "tag-success" : "tag-muted"}>
      {label}: {ready ? "已启用" : provider?.configured ? "未启用" : "未配置"}
    </span>
  );
}

function RegionalMarketPanel({
  title,
  subtitle,
  rows,
  status,
}: {
  title: string;
  subtitle: string;
  rows: MarketRow[];
  status?: MarketDataGroupStatus;
}) {
  const stats = computeStats(rows);
  return (
    <div className="panel space-y-4">
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="section-title">{title}</div>
          <div className="mt-1 text-sm text-slate-400">{subtitle}</div>
          <div className="mt-1 text-xs text-slate-500">{sourceSummary(status)}</div>
        </div>
        <span className="tag-muted">{marketTone(stats)}</span>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCell label="平均涨跌" value={formatPct(stats.avgChange)} valueClass={changeClass(stats.avgChange)} />
        <StatCell label="上涨/下跌" value={`${stats.advancers}/${stats.decliners}`} />
        <StatCell
          label="领涨"
          value={stats.leader ? `${stats.leader.name || stats.leader.symbol} ${formatPct(stats.leader.change_pct)}` : "-"}
        />
        <StatCell
          label="领跌"
          value={stats.laggard ? `${stats.laggard.name || stats.laggard.symbol} ${formatPct(stats.laggard.change_pct)}` : "-"}
        />
      </div>

      <div className="rounded-lg border border-slate-700/70 bg-slate-950/30 px-3 py-2 text-sm text-slate-300">
        {regionalSuggestion(stats, title)}
      </div>

      <QuoteTable rows={rows} />
    </div>
  );
}

export default function MarketPage() {
  const [persisted, setPersisted] = useState<DashboardSummary | null>(null);

  useEffect(() => {
    const cached = readDashboardSummaryCache();
    if (cached) setPersisted(cached as DashboardSummary);
  }, []);

  const { data, error, isLoading, isValidating, mutate } = useSWR<DashboardSummary>(
    "/dashboard/summary",
    (path: string) => apiGet<DashboardSummary>(path, { timeoutMs: 30000, retries: 2, cacheTtlMs: 0 }),
    {
      ...buildSwrOptions(
        visibilityAwareInterval(SWR_INTERVALS.marketAnalysisPage.refreshInterval),
        SWR_INTERVALS.marketAnalysisPage.dedupingInterval,
        { revalidateOnFocus: true }
      ),
      onSuccess: (next) => {
        if (!isDashboardSummaryPersistable(next)) return;
        const previous = readDashboardSummaryCache();
        let toPersist = next as DashboardSummaryCache;
        if (previous && isDashboardSummaryUsable(previous)) {
          toPersist = mergeDashboardMarketRowsFromCache(toPersist, previous).merged;
          toPersist = mergeDashboardMacroIndicators(toPersist, previous).merged;
        }
        writeDashboardSummaryCache(toPersist);
        setPersisted(toPersist as DashboardSummary);
      },
    }
  );

  const { data: providerStatus } = useSWR<ProviderStatusResponse>(
    "/market-data/public/providers/status",
    (path: string) => apiGet<ProviderStatusResponse>(path, { timeoutMs: 12000, retries: 1, cacheTtlMs: 0 }),
    buildSwrOptions(60000, 30000)
  );

  const display = useMemo(() => {
    const liveOk = data != null && isDashboardSummaryUsable(data);
    const cacheOk = persisted != null && isDashboardSummaryUsable(persisted);
    if (liveOk && cacheOk) {
      const marketMerged = mergeDashboardMarketRowsFromCache(data as DashboardSummaryCache, persisted as DashboardSummaryCache);
      const analysisMerged = mergeDashboardAnalysisFromCache(marketMerged.merged, persisted as DashboardSummaryCache);
      return analysisMerged.merged as DashboardSummary;
    }
    if (liveOk) return data;
    if (cacheOk) return persisted;
    return data ?? persisted ?? undefined;
  }, [data, persisted]);

  const usRows = display?.markets?.us || [];
  const cnHkRows = display?.markets?.cn_hk || [];
  const hkRows = cnHkRows.filter((row) => rowMarket(row) === "hk");
  const cnRows = cnHkRows.filter((row) => rowMarket(row) === "cn");
  const analysis = display?.analysis;
  const usStats = computeStats(usRows);
  const apiProviders = providerStatus?.providers || [];
  const polygonProvider = apiProviders.find((p) => p.id === "polygon");
  const twelveDataProvider = apiProviders.find((p) => p.id === "twelvedata");

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">市场分析</h1>
            <div className="mt-1 text-sm text-slate-300">美股、港股、A股三市场联动跟踪</div>
          </div>
          <MarketSourceControl
            meta={display}
            refreshSeconds={SWR_INTERVALS.marketAnalysisPage.refreshInterval / 1000}
            isRefreshing={isValidating}
            onRefresh={() => mutate()}
          />
        </div>

        <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-4">
          <StatCell label="美股环境" value={String(analysis?.market_environment || "-")} />
          <StatCell label="美股评分" value={`${analysis?.score ?? "-"}/5`} valueClass="text-rose-300" />
          <StatCell label="港股状态" value={marketTone(computeStats(hkRows))} />
          <StatCell label="A股状态" value={marketTone(computeStats(cnRows))} />
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <ApiProviderPill provider={polygonProvider} />
          <ApiProviderPill provider={twelveDataProvider} />
        </div>
      </div>

      {error ? (
        <div className="panel border-amber-200 bg-amber-50 text-amber-700">
          数据刷新较慢，当前显示的是可用快照或缓存：{String((error as Error)?.message || error)}
        </div>
      ) : null}
      {isLoading && !data ? <div className="panel">市场分析加载中...</div> : null}

      <div className="panel space-y-5">
        <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
          <div>
            <div className="section-title">美股市场分析</div>
            <div className="mt-1 text-sm text-slate-400">宏观风险、情绪指标、ETF 快照与板块轮动</div>
            <div className="mt-1 text-xs text-slate-500">{sourceSummary(display?.market_data_status?.us)}</div>
          </div>
          <span className="tag-muted">{marketTone(usStats)}</span>
        </div>

        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <StatCell label="综合评分" value={`${analysis?.score ?? "-"}/5`} valueClass="text-rose-300" />
          <StatCell label="情绪指数" value={String(analysis?.indicators?.fear_greed_index?.value ?? "-")} />
          <StatCell label="VIX" value={String(analysis?.indicators?.vix?.value ?? "-")} valueClass="text-rose-300" />
          <StatCell label="10Y 国债" value={`${analysis?.indicators?.treasury_10y?.value ?? "-"}%`} />
        </div>

        <div>
          <div className="field-label">综合环境</div>
          <div className="mt-2 text-xl font-semibold text-slate-100">{analysis?.market_environment ?? "-"}</div>
          <div className="mt-2 rounded-lg border border-slate-700/70 bg-slate-950/30 px-3 py-2 text-sm text-slate-300">
            {analysis?.strategy_recommendation ?? "-"}
          </div>
        </div>

        <QuoteTable rows={usRows} />

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <SectorList title="美股板块强势 Top3" rows={display?.sector_top3 || []} tone="up" />
          <SectorList title="美股板块弱势 Top3" rows={display?.sector_bottom3 || []} tone="down" />
        </div>
      </div>

      <RegionalMarketPanel title="港股市场分析" subtitle="恒生指数、恒生科技等港股核心快照" rows={hkRows} />
      <RegionalMarketPanel title="A股市场分析" subtitle="上证综指、深证成指等 A 股核心快照" rows={cnRows} status={display?.market_data_status?.cn_hk} />
    </PageShell>
  );
}
