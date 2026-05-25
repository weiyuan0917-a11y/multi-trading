const STORAGE_KEY = "multi-trading.news-feed.v1";

export type CachedNewsFeed = {
  ok?: boolean;
  generated_at?: string;
  items?: unknown[];
  counts?: Record<string, unknown>;
  positions?: Record<string, unknown>;
  sources?: unknown[];
  [key: string]: unknown;
};

export function isNewsFeedUsable(value: unknown): value is CachedNewsFeed {
  if (!value || typeof value !== "object") return false;
  const obj = value as Record<string, unknown>;
  return Array.isArray(obj.items) && obj.items.length > 0;
}

export function readNewsFeedCache(): CachedNewsFeed | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    return isNewsFeedUsable(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function writeNewsFeedCache(value: unknown): void {
  if (typeof window === "undefined" || !isNewsFeedUsable(value)) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // ignore quota / private mode
  }
}
