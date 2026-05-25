"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { PageShell } from "@/components/ui/page-shell";
import { localAgentGet as apiGet } from "@/lib/local-agent-api";
import { isNewsFeedUsable, readNewsFeedCache, writeNewsFeedCache } from "@/lib/news-feed-cache";
import { buildSwrOptions, SWR_INTERVALS, visibilityAwareInterval } from "@/lib/swr-config";

type Sentiment = "bullish" | "bearish" | "neutral";

type NewsItem = {
  id: string;
  title: string;
  summary?: string;
  url?: string;
  source?: string;
  published_at?: string | null;
  symbol?: string | null;
  region?: string;
  category?: "market" | "holding" | string;
  origin?: string;
  likes_count?: number | null;
  comments_count?: number | null;
  sentiment?: Sentiment;
  sentiment_score?: number;
  sentiment_reasons?: string[];
};

type NewsFeed = {
  ok?: boolean;
  generated_at?: string;
  cache?: boolean;
  region?: string;
  positions?: {
    symbols?: string[];
    available?: boolean;
    error?: string | null;
    count?: number;
    source?: string;
  };
  counts?: {
    total?: number;
    bullish?: number;
    bearish?: number;
    neutral?: number;
    holding?: number;
    market?: number;
  };
  stale?: boolean;
  stale_reason?: string;
  items?: NewsItem[];
  sources?: string[];
  errors?: { symbol?: string; error?: string }[];
  sentiment_method?: string;
};

const sentimentLabel: Record<Sentiment, string> = {
  bullish: "利多",
  bearish: "利空",
  neutral: "中性",
};

const sentimentClass: Record<Sentiment, string> = {
  bullish: "border-emerald-400/40 bg-emerald-500/12 text-emerald-200",
  bearish: "border-rose-400/40 bg-rose-500/12 text-rose-200",
  neutral: "border-slate-500/40 bg-slate-700/40 text-slate-200",
};

const regionTabs = [
  { value: "all", label: "全部" },
  { value: "global", label: "国际" },
  { value: "china", label: "国内/港股" },
];

const sentimentTabs: Array<{ value: "all" | Sentiment; label: string }> = [
  { value: "all", label: "全部情绪" },
  { value: "bullish", label: "利多" },
  { value: "bearish", label: "利空" },
  { value: "neutral", label: "中性" },
];

const formatTime = (value?: string | null) => {
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return String(value);
  return dt.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const compactReason = (reasons?: string[]) => {
  if (!reasons?.length) return "关键词中性";
  return reasons
    .map((reason) =>
      reason
        .replace("positive:", "利多词 ")
        .replace("negative:", "利空词 ")
        .replace("keyword_neutral", "关键词中性")
    )
    .join("；");
};

function SentimentBadge({ sentiment = "neutral" }: { sentiment?: Sentiment }) {
  const key = sentiment in sentimentClass ? sentiment : "neutral";
  return (
    <span className={`inline-flex shrink-0 items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${sentimentClass[key]}`}>
      {sentimentLabel[key]}
    </span>
  );
}

function SegmentButton({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg border px-3 py-2 text-sm font-semibold transition ${
        active
          ? "border-cyan-400/50 bg-cyan-500/15 text-cyan-100"
          : "border-slate-700 bg-slate-900/60 text-slate-300 hover:border-slate-500 hover:text-slate-100"
      }`}
    >
      {children}
    </button>
  );
}

function CountCard({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <div className="metric-card">
      <div className="field-label">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${tone || "text-slate-100"}`}>{value}</div>
    </div>
  );
}

function NewsCard({ item, featured = false }: { item: NewsItem; featured?: boolean }) {
  const sentiment = item.sentiment || "neutral";
  const content = (
    <article
      className={`group border-b border-slate-800/90 py-4 last:border-b-0 ${
        featured ? "rounded-xl border border-cyan-500/20 bg-slate-950/40 p-5" : ""
      }`}
    >
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
        <SentimentBadge sentiment={sentiment} />
        {item.symbol ? <span className="tag-muted px-2 py-1">{item.symbol}</span> : null}
        <span>{item.source || "Unknown"}</span>
        <span>·</span>
        <span>{formatTime(item.published_at)}</span>
        <span>·</span>
        <span>{item.category === "holding" ? "持仓股" : item.region === "china" ? "国内" : "国际"}</span>
      </div>
      <h2 className={`mt-3 font-semibold leading-snug text-slate-50 group-hover:text-cyan-100 ${featured ? "text-2xl" : "text-lg"}`}>
        {item.title}
      </h2>
      {item.summary ? <p className="mt-2 line-clamp-3 text-sm leading-6 text-slate-300">{item.summary}</p> : null}
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span>{compactReason(item.sentiment_reasons)}</span>
        {typeof item.comments_count === "number" ? <span>评论 {item.comments_count}</span> : null}
        {typeof item.likes_count === "number" ? <span>点赞 {item.likes_count}</span> : null}
      </div>
    </article>
  );
  if (!item.url) return content;
  return (
    <a href={item.url} target="_blank" rel="noreferrer" className="block">
      {content}
    </a>
  );
}

function EmptyState({ error }: { error?: unknown }) {
  return (
    <div className="panel">
      <div className="text-lg font-semibold text-slate-100">暂无可展示新闻</div>
      <p className="mt-2 text-sm text-slate-400">
        {error ? String((error as Error)?.message || error) : "新闻源可能暂时不可用，或当前账户没有可读取的持仓。"}
      </p>
    </div>
  );
}

export default function NewsPage() {
  const [region, setRegion] = useState("all");
  const [sentiment, setSentiment] = useState<"all" | Sentiment>("all");
  const [scope, setScope] = useState<"all" | "holding" | "market">("all");
  const [cachedFeed, setCachedFeed] = useState<NewsFeed | null>(null);

  useEffect(() => {
    const cached = readNewsFeedCache();
    if (cached) setCachedFeed(cached as NewsFeed);
  }, []);

  const feedPath = `/market/news-feed?region=${encodeURIComponent(region)}&limit=90`;
  const { data, error, isLoading, isValidating, mutate } = useSWR<NewsFeed>(
    feedPath,
    (path: string) => apiGet<NewsFeed>(path, { timeoutMs: 30000, retries: 1, cacheTtlMs: 0 }),
    {
      ...buildSwrOptions(
        visibilityAwareInterval(SWR_INTERVALS.dashboardPage.refreshInterval),
        SWR_INTERVALS.dashboardPage.dedupingInterval,
        { revalidateOnFocus: true }
      ),
      fallbackData: cachedFeed || undefined,
      onSuccess: (next) => {
        if (!isNewsFeedUsable(next)) return;
        writeNewsFeedCache(next);
        setCachedFeed(next);
      },
    }
  );

  const display = isNewsFeedUsable(data) ? data : cachedFeed;
  const showingCached = !isNewsFeedUsable(data) && isNewsFeedUsable(cachedFeed);
  const items = display?.items || [];
  const filtered = useMemo(() => {
    return items.filter((item) => {
      if (sentiment !== "all" && item.sentiment !== sentiment) return false;
      if (scope !== "all" && item.category !== scope) return false;
      return true;
    });
  }, [items, sentiment, scope]);

  const featured = filtered[0];
  const rest = featured ? filtered.slice(1) : filtered;
  const holdingItems = items.filter((item) => item.category === "holding").slice(0, 8);
  const marketItems = items.filter((item) => item.category !== "holding").slice(0, 6);
  const positionSymbols = display?.positions?.symbols || [];

  return (
    <PageShell>
      <section className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900 via-slate-950 to-slate-900">
        <div className="page-header items-start">
          <div>
            <h1 className="page-title">新闻信息流</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
              聚合国内、国际和持仓股相关新闻，并用关键词规则标注利多、利空或中性，方便快速筛选交易相关催化。
            </p>
          </div>
          <button type="button" onClick={() => mutate()} className="btn-primary shrink-0" disabled={isValidating}>
            {isValidating ? "刷新中" : "刷新"}
          </button>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-5">
          <CountCard label="新闻总数" value={display?.counts?.total ?? (isLoading ? "..." : 0)} />
          <CountCard label="利多" value={display?.counts?.bullish ?? 0} tone="text-emerald-300" />
          <CountCard label="利空" value={display?.counts?.bearish ?? 0} tone="text-rose-300" />
          <CountCard label="中性" value={display?.counts?.neutral ?? 0} tone="text-slate-200" />
          <CountCard label="持仓新闻" value={display?.counts?.holding ?? 0} tone="text-cyan-200" />
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-2">
          {regionTabs.map((tab) => (
            <SegmentButton key={tab.value} active={region === tab.value} onClick={() => setRegion(tab.value)}>
              {tab.label}
            </SegmentButton>
          ))}
          <span className="mx-1 h-6 w-px bg-slate-700" />
          {sentimentTabs.map((tab) => (
            <SegmentButton key={tab.value} active={sentiment === tab.value} onClick={() => setSentiment(tab.value)}>
              {tab.label}
            </SegmentButton>
          ))}
          <span className="mx-1 h-6 w-px bg-slate-700" />
          <SegmentButton active={scope === "all"} onClick={() => setScope("all")}>
            全部来源
          </SegmentButton>
          <SegmentButton active={scope === "holding"} onClick={() => setScope("holding")}>
            只看持仓
          </SegmentButton>
          <SegmentButton active={scope === "market"} onClick={() => setScope("market")}>
            只看市场
          </SegmentButton>
        </div>

        <div className="mt-4 flex flex-wrap gap-2 text-xs text-slate-400">
          <span>更新时间 {formatTime(display?.generated_at)}</span>
          {showingCached || display?.stale ? <span className="text-amber-300">显示旧缓存</span> : display?.cache ? <span className="text-amber-300">缓存</span> : <span className="text-emerald-300">多源拉取</span>}
          {display?.sentiment_method ? <span>标签规则 {display.sentiment_method}</span> : null}
          {display?.sources?.length ? <span>来源 {display.sources.join(" / ")}</span> : null}
        </div>
      </section>

      {display?.positions?.available === false ? (
        <div className="panel border-amber-500/30 bg-amber-950/20 text-amber-100">
          <div className="font-semibold">持仓读取失败，当前仅展示市场新闻</div>
          <div className="mt-1 text-sm text-amber-200/80">{display.positions.error}</div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <main className="panel">
          <div className="page-header">
            <div>
              <h2 className="section-title">精选新闻</h2>
              <div className="mt-1 text-sm text-slate-400">按发布时间排序，点击新闻可打开来源页面。</div>
            </div>
            <div className="tag-muted">{filtered.length} 条</div>
          </div>

          {error || (!isLoading && !filtered.length) ? (
            <div className="mt-4">
              <EmptyState error={error} />
            </div>
          ) : isLoading && !filtered.length ? (
            <div className="mt-6 space-y-4">
              {Array.from({ length: 5 }).map((_, idx) => (
                <div key={idx} className="h-28 animate-pulse rounded-xl bg-slate-800/60" />
              ))}
            </div>
          ) : (
            <div className="mt-4">
              {featured ? <NewsCard item={featured} featured /> : null}
              <div className="mt-2 divide-y divide-slate-800/90">
                {rest.map((item) => (
                  <NewsCard key={item.id} item={item} />
                ))}
              </div>
            </div>
          )}
        </main>

        <aside className="space-y-5">
          <section className="panel">
            <div className="section-title">持仓股</div>
            <div className="mt-3 flex flex-wrap gap-2">
              {positionSymbols.length ? (
                positionSymbols.map((symbol) => (
                  <span key={symbol} className="tag-muted">
                    {symbol}
                  </span>
                ))
              ) : (
                <div className="text-sm text-slate-400">暂无持仓标的或尚未连接账户。</div>
              )}
            </div>
          </section>

          <section className="panel">
            <div className="section-title">持仓股新闻</div>
            <div className="mt-3 space-y-3">
              {holdingItems.length ? (
                holdingItems.map((item) => (
                  <a key={item.id} href={item.url || "#"} target="_blank" rel="noreferrer" className="block rounded-lg border border-slate-800 bg-slate-950/35 p-3 hover:border-cyan-500/30">
                    <div className="flex items-center gap-2">
                      <SentimentBadge sentiment={item.sentiment || "neutral"} />
                      <span className="text-xs text-slate-400">{item.symbol}</span>
                    </div>
                    <div className="mt-2 line-clamp-2 text-sm font-semibold leading-5 text-slate-100">{item.title}</div>
                    <div className="mt-2 text-xs text-slate-500">{formatTime(item.published_at)}</div>
                  </a>
                ))
              ) : (
                <div className="text-sm text-slate-400">暂无持仓股新闻。</div>
              )}
            </div>
          </section>

          <section className="panel">
            <div className="section-title">市场快讯</div>
            <div className="mt-3 space-y-3">
              {marketItems.length ? (
                marketItems.map((item) => (
                  <a key={item.id} href={item.url || "#"} target="_blank" rel="noreferrer" className="block rounded-lg border border-slate-800 bg-slate-950/35 p-3 hover:border-cyan-500/30">
                    <div className="flex items-center gap-2">
                      <SentimentBadge sentiment={item.sentiment || "neutral"} />
                      <span className="text-xs text-slate-400">{item.source}</span>
                    </div>
                    <div className="mt-2 line-clamp-2 text-sm font-semibold leading-5 text-slate-100">{item.title}</div>
                  </a>
                ))
              ) : (
                <div className="text-sm text-slate-400">暂无市场快讯。</div>
              )}
            </div>
          </section>
        </aside>
      </div>
    </PageShell>
  );
}
