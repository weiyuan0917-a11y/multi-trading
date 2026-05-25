"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { MarketTable } from "@/components/market-table";
import { MarketSourceControl } from "@/components/market-source-control";
import { StatCard } from "@/components/stat-card";
import { PageShell } from "@/components/ui/page-shell";
import {
  isDashboardSummaryDegraded,
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

const MACRO_MERGE_LABEL: Record<string, string> = {
  fear_greed_index: "情绪指数",
  vix: "VIX",
  treasury_10y: "10Y 国债",
  dollar_index: "美元指数",
};

type Summary = {
  markets: { cn_hk: any[]; us: any[] };
  market_data_status?: Record<string, any>;
  analysis: any;
  sector_data_source: string;
  sector_data_source_label: string;
  sector_age_seconds?: number;
  sector_last_refresh_ts?: string;
  sector_top3: any[];
  sector_bottom3: any[];
};

export default function DashboardPage() {
  const [persisted, setPersisted] = useState<Summary | null>(null);

  useEffect(() => {
    const cached = readDashboardSummaryCache();
    if (cached) setPersisted(cached as Summary);
  }, []);

  const { data, error, isLoading, isValidating, mutate } = useSWR<Summary>(
    "/dashboard/summary",
    (path: string) => apiGet<Summary>(path, { timeoutMs: 25000, retries: 2, cacheTtlMs: 0 }),
    {
      ...buildSwrOptions(
        visibilityAwareInterval(SWR_INTERVALS.dashboardPage.refreshInterval),
        SWR_INTERVALS.dashboardPage.dedupingInterval,
        { revalidateOnFocus: true }
      ),
      onSuccess: (next) => {
        if (!isDashboardSummaryPersistable(next)) return;
        const previous = readDashboardSummaryCache();
        let toPersist = next as DashboardSummaryCache;
        if (previous && isDashboardSummaryUsable(previous) && !isDashboardSummaryDegraded(previous)) {
          toPersist = mergeDashboardMacroIndicators(toPersist, previous).merged;
        }
        writeDashboardSummaryCache(toPersist);
        setPersisted(toPersist as Summary);
      },
    }
  );

  const { display, showingPersisted, macroMergedKeys, analysisFromCache } = useMemo(() => {
    const liveStructOk = data != null && isDashboardSummaryUsable(data);
    const liveGood = liveStructOk && !isDashboardSummaryDegraded(data);
    const cacheGood = persisted != null && isDashboardSummaryUsable(persisted) && !isDashboardSummaryDegraded(persisted);

    let displayBase: Summary | undefined;
    let showingPersistedFlag = false;
    let analysisFromCacheFlag = false;

    if (liveGood) displayBase = data!;
    else if (liveStructOk) {
      displayBase = data!;
      if (cacheGood) {
        const analysisMerged = mergeDashboardAnalysisFromCache(data as DashboardSummaryCache, persisted as DashboardSummaryCache);
        const marketMerged = mergeDashboardMarketRowsFromCache(analysisMerged.merged, persisted as DashboardSummaryCache);
        displayBase = marketMerged.merged as Summary;
        analysisFromCacheFlag = analysisMerged.usedFallbackAnalysis;
      }
    } else if (cacheGood) {
      displayBase = persisted!;
      showingPersistedFlag = true;
    }
    else if (persisted != null && isDashboardSummaryUsable(persisted)) {
      displayBase = persisted;
      showingPersistedFlag = true;
    } else displayBase = data ?? persisted ?? undefined;

    let usedFallbackKeys: string[] = [];
    if (displayBase && persisted && isDashboardSummaryUsable(persisted) && !isDashboardSummaryDegraded(persisted)) {
      const merged = mergeDashboardMacroIndicators(
        displayBase as DashboardSummaryCache,
        persisted as DashboardSummaryCache
      );
      if (merged.usedFallbackKeys.length > 0) {
        displayBase = merged.merged as Summary;
        usedFallbackKeys = merged.usedFallbackKeys;
      }
    }

    return {
      display: displayBase,
      showingPersisted: showingPersistedFlag,
      macroMergedKeys: usedFallbackKeys,
      analysisFromCache: analysisFromCacheFlag,
    };
  }, [data, persisted]);

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">总览 Dashboard</h1>
            <div className="mt-1 text-sm text-slate-300">跨市场监控、风险评估、板块轮动</div>
          </div>
          <MarketSourceControl
            meta={display}
            refreshSeconds={SWR_INTERVALS.dashboardPage.refreshInterval / 1000}
            isRefreshing={isValidating}
            onRefresh={() => mutate()}
          />
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <div className="metric-card">
            <div className="field-label">风险评分</div>
            <div className="mt-1 text-xl font-semibold text-rose-500">{display?.analysis?.score ?? "-"}/5</div>
          </div>
          <div className="metric-card">
            <div className="field-label">市场情绪</div>
            <div className="mt-1 text-xl font-semibold text-slate-800">
              {display?.analysis?.indicators?.fear_greed_index?.value ?? "-"}
            </div>
          </div>
          <div className="metric-card">
            <div className="field-label">VIX 波动率</div>
            <div className="mt-1 text-xl font-semibold text-rose-500">
              {display?.analysis?.indicators?.vix?.value ?? "-"}
            </div>
          </div>
          <div className="metric-card">
            <div className="field-label">10Y 国债</div>
            <div className="mt-1 text-xl font-semibold text-slate-800">
              {display?.analysis?.indicators?.treasury_10y?.value ?? "-"}%
            </div>
          </div>
        </div>
      </div>

      {showingPersisted || macroMergedKeys.length > 0 ? (
        <div className="panel border-amber-200 bg-amber-50 text-amber-800">
          {showingPersisted ? (
            <>
              <div className="font-medium">当前显示的是最近一次成功加载的本地缓存。</div>
              {error ? (
                <div className="mt-1 text-sm text-amber-700">
                  刷新失败：{String((error as Error)?.message || error)}
                </div>
              ) : data != null && isDashboardSummaryUsable(data) && isDashboardSummaryDegraded(data) ? (
                <div className="mt-1 text-sm text-amber-700">
                  本次宏观指标返回降级占位数据，已回退到最近一次完整缓存。
                </div>
              ) : data != null && !isDashboardSummaryUsable(data) ? (
                <div className="mt-1 text-sm text-amber-700">本次返回结构不完整，已回退到缓存。</div>
              ) : null}
            </>
          ) : null}

          {!showingPersisted && macroMergedKeys.length > 0 ? (
            <div className="font-medium">
              部分宏观指标沿用了上一次有效值：
              {macroMergedKeys.map((key) => MACRO_MERGE_LABEL[key] || key).join("、")}
            </div>
          ) : null}

          {!showingPersisted && analysisFromCache ? (
            <div className="font-medium">宏观分析沿用了上一次有效值；跨市场行情仍使用本次公共源/券商快照。</div>
          ) : null}
        </div>
      ) : null}

      {display ? (
        <>
          <div className="page-header">
            <h2 className="section-title">关键指标</h2>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
            <StatCard title="风险评分" value={`${display.analysis?.score ?? "-"}/5`} sub={display.analysis?.market_environment} />
            <StatCard title="情绪指数" value={display.analysis?.indicators?.fear_greed_index?.value ?? "-"} />
            <StatCard title="VIX" value={display.analysis?.indicators?.vix?.value ?? "-"} />
            <StatCard title="10Y 国债" value={`${display.analysis?.indicators?.treasury_10y?.value ?? "-"}%`} />
          </div>

          <div className="page-header">
            <h2 className="section-title">跨市场行情</h2>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <MarketTable title="A股/港股" rows={display.markets?.cn_hk || []} />
            <MarketTable title="美股" rows={display.markets?.us || []} />
          </div>

          <div className="page-header">
            <h2 className="section-title">板块轮动</h2>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="panel">
              <div className="mb-2 text-sm font-semibold text-slate-800">板块强势 Top3</div>
              {(display.sector_top3 || []).map((item) => (
                <div key={item.symbol} className="rounded-md px-2 py-1 text-sm text-emerald-600 hover:bg-blue-50">
                  {item.name} ({item.change_pct >= 0 ? "+" : ""}
                  {item.change_pct}%)
                </div>
              ))}
            </div>

            <div className="panel">
              <div className="mb-2 text-sm font-semibold text-slate-800">板块弱势 Top3</div>
              {(display.sector_bottom3 || []).map((item) => (
                <div key={item.symbol} className="rounded-md px-2 py-1 text-sm text-rose-600 hover:bg-blue-50">
                  {item.name} ({item.change_pct >= 0 ? "+" : ""}
                  {item.change_pct}%)
                </div>
              ))}
            </div>
          </div>
        </>
      ) : isLoading ? (
        <div className="panel">加载中...</div>
      ) : (
        <div className="panel">暂无数据</div>
      )}
    </PageShell>
  );
}
