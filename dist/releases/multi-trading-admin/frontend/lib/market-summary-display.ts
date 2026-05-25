export type SectorSourceMeta = {
  sector_data_source?: string | null;
  sector_data_source_label?: string | null;
  sector_age_seconds?: number | null;
  sector_last_refresh_ts?: string | null;
};

const TEXT = {
  unknown: "\u672a\u77e5",
  publicDailySource: "\u516c\u5171\u65e5\u7ebf\u6e90",
  secondsAgoSync: "\u79d2\u524d\u540c\u6b65",
  secondsAgoCache: "\u79d2\u524d\u7f13\u5b58",
  secondsAgo: "\u79d2\u524d",
  waitingFirstSync: "\u7b49\u5f85\u9996\u6b21\u540c\u6b65",
  autoRefresh: "\u81ea\u52a8\u5237\u65b0\u7ea6",
  seconds: "\u79d2",
  lastSync: "\u6700\u8fd1\u540c\u6b65",
};

export const SECTOR_SOURCE_HELP = [
  "\u5b9e\u65f6\uff1a\u901a\u8fc7\u5238\u5546\u5b9e\u65f6\u884c\u60c5\u6216\u5b9e\u65f6\u62a5\u4ef7\u8ba1\u7b97\u3002",
  "\u7f13\u5b58\uff1a\u540e\u7aef\u8fd4\u56de\u6700\u8fd1\u4e00\u6b21\u6210\u529f\u6293\u53d6\u7684\u6570\u636e\uff0c\u7528\u4e8e\u51cf\u5c11\u91cd\u590d\u8bf7\u6c42\u3002",
  "\u516c\u5171\u5907\u7528\u6e90\uff1a\u5b9e\u65f6\u6e90\u4e0d\u53ef\u7528\u65f6\uff0c\u4f7f\u7528 Stooq \u7b49\u516c\u5171\u65e5\u7ebf\u6570\u636e\uff0c\u77ed\u65f6\u95f4\u5185\u6570\u503c\u53ef\u80fd\u4e0d\u53d8\u3002",
  "\u515c\u5e95\uff1a\u5b9e\u65f6\u6e90\u548c\u516c\u5171\u5907\u7528\u6e90\u90fd\u4e0d\u53ef\u7528\u65f6\u7684\u5360\u4f4d\u6570\u636e\u3002",
].join("\n");

function formatTime(raw?: string | null): string | null {
  if (!raw) return null;
  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) return null;
  return dt.toLocaleTimeString("zh-CN", { hour12: false });
}

export function formatSectorSource(meta?: SectorSourceMeta): string {
  const label = String(meta?.sector_data_source_label || TEXT.unknown);
  const age = typeof meta?.sector_age_seconds === "number" ? Math.max(0, Math.round(meta.sector_age_seconds)) : null;
  const source = String(meta?.sector_data_source || "");

  if (source === "public_fallback" && age !== null) {
    return `${label}\uff08${TEXT.publicDailySource}\uff0c${age}s ${TEXT.secondsAgoSync}\uff09`;
  }
  if (source === "cache" && age !== null) {
    return `${label}\uff08${age}s ${TEXT.secondsAgoCache}\uff09`;
  }
  if (source === "real_time" && age !== null) {
    return `${label}\uff08${age}s ${TEXT.secondsAgo}\uff09`;
  }
  return label;
}

export function formatSectorLastRefresh(meta?: SectorSourceMeta, refreshSeconds = 20): string {
  const timeText = formatTime(meta?.sector_last_refresh_ts);
  if (!timeText) return TEXT.waitingFirstSync;
  return `${TEXT.autoRefresh} ${refreshSeconds} ${TEXT.seconds} | ${TEXT.lastSync} ${timeText}`;
}
