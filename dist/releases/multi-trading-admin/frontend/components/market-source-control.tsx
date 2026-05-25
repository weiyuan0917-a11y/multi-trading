"use client";

import {
  formatSectorLastRefresh,
  formatSectorSource,
  SECTOR_SOURCE_HELP,
  type SectorSourceMeta,
} from "@/lib/market-summary-display";

const TEXT = {
  sourcePrefix: "\u677f\u5757\u6570\u636e\u6765\u6e90\uff1a",
  refresh: "\u7acb\u5373\u5237\u65b0",
  refreshing: "\u5237\u65b0\u4e2d...",
  tooltipTitle: "\u6570\u636e\u6765\u6e90\u8bf4\u660e",
};

type MarketSourceControlProps = {
  meta?: SectorSourceMeta;
  refreshSeconds: number;
  isRefreshing?: boolean;
  onRefresh: () => void | Promise<unknown>;
};

export function MarketSourceControl({
  meta,
  refreshSeconds,
  isRefreshing = false,
  onRefresh,
}: MarketSourceControlProps) {
  const sourceText = formatSectorSource(meta);
  const refreshText = formatSectorLastRefresh(meta, refreshSeconds);

  return (
    <div className="flex flex-col items-end gap-2 text-right">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <div className="group relative">
          <span
            className="tag-muted cursor-help"
            tabIndex={0}
            title={SECTOR_SOURCE_HELP}
          >
            {TEXT.sourcePrefix}
            {sourceText}
          </span>
          <div className="pointer-events-none absolute right-0 top-full z-30 mt-2 hidden w-80 rounded-lg border border-slate-600 bg-slate-950/95 p-3 text-left text-xs leading-relaxed text-slate-200 shadow-xl group-hover:block group-focus-within:block">
            <div className="mb-2 font-semibold text-slate-100">{TEXT.tooltipTitle}</div>
            <div className="whitespace-pre-line">{SECTOR_SOURCE_HELP}</div>
          </div>
        </div>

        <button
          type="button"
          className="btn-secondary px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-60"
          disabled={isRefreshing}
          onClick={() => {
            void onRefresh();
          }}
        >
          {isRefreshing ? TEXT.refreshing : TEXT.refresh}
        </button>
      </div>

      <span className="text-xs text-slate-400">{refreshText}</span>
    </div>
  );
}
