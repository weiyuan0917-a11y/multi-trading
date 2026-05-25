"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { localAgentGet as apiGet, localAgentPost as apiPost, localAgentPut as apiPut } from "@/lib/local-agent-api";
import { StrategyConfigJsonTextarea } from "./strategy-config-json-textarea";
import { stringifyStrategyConfigWithHints, stripStrategyConfigHashComments } from "./strategy-config-json-hints";
import { coerceStrategyVariant, completeStrategyConfigByVariant } from "./strategy-config-variant-keys";
import { EntitlementNotice } from "@/components/entitlement-guard";
import { useEntitlements } from "@/lib/use-entitlements";

type LiveResolve = {
  strike_window: number;
  standard_only: boolean;
  max_strike_diff: number;
};

export type QqqLiveWorkerVariant = "0dte" | "1dte";

type LiveWorkerConfig = {
  api_base_url: string;
  symbol: string;
  history_days: number;
  kline: string;
  poll_seconds: number;
  dry_run: boolean;
  confirmation_token: string | null;
  expiry_date: string | null;
  /** 无 expiry_date 时相对美东「交易日日期」的偏移天数：0=当日，1=次日（1DTE） */
  expiry_offset_days?: number;
  /** 无时区 K 线 date 的墙钟解释；会覆盖 strategy_config.assume_bars_timezone（Worker 内统一用此时区） */
  kline_wall_clock_timezone: string;
  resolve: LiveResolve;
  strategy_config: Record<string, unknown>;
};

type ServicesStatus = {
  qqq_0dte_live_running?: boolean;
  qqq_0dte_live_tracking?: string;
  qqq_0dte_live_pid?: number | null;
  qqq_0dte_live_runtime?: {
    runtime?: Record<string, unknown>;
    pid?: number | null;
    worker_running?: boolean;
    state?: string;
    state_label?: string;
    state_severity?: string;
    runtime_status?: string | null;
  };
  qqq_1dte_live_running?: boolean;
  qqq_1dte_live_tracking?: string;
  qqq_1dte_live_pid?: number | null;
  qqq_1dte_live_runtime?: {
    runtime?: Record<string, unknown>;
    pid?: number | null;
    worker_running?: boolean;
    state?: string;
    state_label?: string;
    state_severity?: string;
    runtime_status?: string | null;
  };
};

type SnapshotMetrics = {
  realized_pnl?: number;
  return_pct?: number | null;
  closed_trades?: number;
};

type SnapshotRun = {
  id?: string;
  created_at?: string;
  request?: Record<string, unknown>;
  strategy_config?: Record<string, unknown>;
  metrics?: SnapshotMetrics;
};

type TopSnapshotsResponse = {
  sort?: string;
  top_n?: number;
  total_stored?: number;
  runs?: SnapshotRun[];
};

/** GET /strategy/qqq-0dte/strategy-recommendation（Worker 写入，约每 10 分钟更新） */
type StrategyRecommendationResponse = {
  ok?: boolean;
  error?: string;
  message?: string;
  recommended_variant?: string;
  recommended_name_zh?: string;
  reasons?: string[];
  scores?: Record<string, number>;
  features?: Record<string, unknown>;
  disclaimer?: string;
  generated_at?: string;
  scan_interval_seconds?: number;
  source?: string;
  note?: string;
};

type LiveWorkerDecisionLogLine = {
  message?: string;
  as_of?: string;
  extra?: Record<string, unknown>;
};

type LiveWorkerDecisionRow = {
  at?: string;
  symbol?: string;
  session_date?: string;
  bar_utc?: string;
  bar_naive_wall?: string;
  action?: Record<string, unknown>;
  logs?: LiveWorkerDecisionLogLine[];
};

type LiveWorkerDecisionTailResponse = {
  ok?: boolean;
  error?: string;
  items?: LiveWorkerDecisionRow[];
  path?: string;
  returned?: number;
};

function liveWorkerDecisionPrimaryLine(row: LiveWorkerDecisionRow): string {
  const logs = row.logs || [];
  for (let i = logs.length - 1; i >= 0; i--) {
    const m = logs[i]?.message;
    if (m) return m;
  }
  const a = row.action && typeof row.action === "object" ? row.action : {};
  return typeof a.action === "string" ? a.action : "—";
}

function liveWorkerActionLabel(row: LiveWorkerDecisionRow): string {
  const a = row.action;
  if (!a || typeof a !== "object") return "—";
  const kind = typeof a.action === "string" ? a.action : "?";
  if (kind === "entry" && a.ok === false) return "entry（失败）";
  if (kind === "exit" && a.ok === false) return "exit（失败）";
  return kind;
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function asNumber(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v)) ? Number(v) : null;
}

function asString(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function formatPct(v: unknown): string {
  const n = asNumber(v);
  return n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function qqq0dteStrategyDisplayName(strategy_config: Record<string, unknown> | undefined | null): string {
  if (!strategy_config || typeof strategy_config !== "object") return "反应区";
  const v = strategy_config.strategy_variant;
  if (v === "morning_strangle") return "早盘宽跨";
  if (v === "morning_directional") return "早盘方向单";
  if (v === "gamma_scalping") return "Gamma 剥头皮";
  if (v === "gamma_pro") return "Gamma Pro";
  if (v === "reaction_zone") return "反应区";
  if (typeof v === "string" && v.trim()) return v.trim();
  return "反应区";
}

function snapshotOptionLabel(run: SnapshotRun, idx: number, sortKey: string): string {
  const strat = qqq0dteStrategyDisplayName(run.strategy_config);
  const m = run.metrics || {};
  const pnl = Number(m.realized_pnl ?? 0);
  const rp = m.return_pct;
  const main =
    sortKey === "return_pct" && rp != null && Number.isFinite(Number(rp))
      ? `盈亏率 ${Number(rp).toFixed(2)}%`
      : `PnL ${pnl.toFixed(2)}`;
  const day = typeof run.created_at === "string" ? run.created_at.slice(0, 10) : "?";
  const idShort = (run.id || "").slice(0, 6);
  return `#${idx + 1} ${strat} · ${main} · ${day} · ${idShort}`;
}

function defaultLiveConfig(variant: QqqLiveWorkerVariant): LiveWorkerConfig {
  return {
    api_base_url: "http://127.0.0.1:8010",
    symbol: "QQQ.US",
    history_days: 2,
    kline: "1m",
    poll_seconds: 30,
    dry_run: true,
    confirmation_token: null,
    expiry_date: null,
    expiry_offset_days: variant === "1dte" ? 1 : 0,
    kline_wall_clock_timezone: "Asia/Shanghai",
    resolve: { strike_window: 5, standard_only: false, max_strike_diff: 1.5 },
    strategy_config: {},
  };
}

function mergeConfig(server: Partial<LiveWorkerConfig> | undefined, variant: QqqLiveWorkerVariant): LiveWorkerConfig {
  const base = defaultLiveConfig(variant);
  if (!server || typeof server !== "object") return { ...base, strategy_config: {} };
  const r = server.resolve;
  const kwtz =
    typeof server.kline_wall_clock_timezone === "string" && server.kline_wall_clock_timezone.trim()
      ? server.kline_wall_clock_timezone.trim()
      : base.kline_wall_clock_timezone;
  const offRaw = server.expiry_offset_days;
  const off =
    typeof offRaw === "number" && Number.isFinite(offRaw)
      ? Math.max(0, Math.floor(offRaw))
      : base.expiry_offset_days ?? 0;
  return {
    ...base,
    ...server,
    expiry_offset_days: off,
    kline_wall_clock_timezone: kwtz,
    resolve: {
      ...base.resolve,
      ...(typeof r === "object" && r !== null ? r : {}),
    },
    strategy_config: pruneLiveWorkerStrategyConfig(
      server.strategy_config &&
        typeof server.strategy_config === "object" &&
        !Array.isArray(server.strategy_config)
        ? { ...(server.strategy_config as Record<string, unknown>) }
        : {}
    ),
  };
}

function parseStrategyConfigObject(raw: string): Record<string, unknown> {
  try {
    const cleaned = stripStrategyConfigHashComments(raw || "");
    const o = JSON.parse(cleaned || "{}") as unknown;
    if (typeof o !== "object" || o === null || Array.isArray(o)) return {};
    return o as Record<string, unknown>;
  } catch {
    return {};
  }
}

/** 按 strategy_variant 白名单筛选并补齐默认值，写入 live_worker_config 时保留本策略完整字段 */
function pruneLiveWorkerStrategyConfig(sc: Record<string, unknown>): Record<string, unknown> {
  const v = coerceStrategyVariant(sc.strategy_variant);
  return completeStrategyConfigByVariant(v, { ...sc, strategy_variant: v });
}

function numFromSc(sc: Record<string, unknown>, key: string, fallback: number): number {
  const v = sc[key];
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return fallback;
}

function strFromSc(sc: Record<string, unknown>, key: string, fallback: string): string {
  const v = sc[key];
  if (typeof v === "string") return v;
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  return fallback;
}

function boolFromSc(sc: Record<string, unknown>, key: string, fallback: boolean): boolean {
  const v = sc[key];
  if (typeof v === "boolean") return v;
  return fallback;
}

export function Qqq0dteLiveAutoPanel({
  liveVariant = "0dte",
  pageSymbol,
  pageKline,
  strategyConfig,
}: {
  liveVariant?: QqqLiveWorkerVariant;
  pageSymbol: string;
  pageKline: string;
  strategyConfig: Record<string, unknown> | null;
}) {
  const entitlements = useEntitlements();
  const canRunOptionAuto = entitlements.canUse("option_auto_trading");
  const liveApiBase = useMemo(
    () => (liveVariant === "1dte" ? "/strategy/qqq-1dte" : "/strategy/qqq-0dte"),
    [liveVariant]
  );
  const strategyApiBase = "/strategy/qqq-0dte";
  const dataDirLabel = liveVariant === "1dte" ? "data/qqq_1dte" : "data/qqq_0dte";

  const [draft, setDraft] = useState<LiveWorkerConfig>(() => ({
    ...defaultLiveConfig(liveVariant),
    strategy_config: {},
  }));
  const [strategyJson, setStrategyJson] = useState("{}");
  const [cfgLoading, setCfgLoading] = useState(false);
  const [cfgErr, setCfgErr] = useState("");
  const [cfgMsg, setCfgMsg] = useState("");
  const [jsonInlineErr, setJsonInlineErr] = useState("");
  const [pinSaving, setPinSaving] = useState(false);
  const [svc, setSvc] = useState<ServicesStatus | null>(null);
  const [svcLoading, setSvcLoading] = useState(false);
  const [svcErr, setSvcErr] = useState("");
  const [opLoading, setOpLoading] = useState(false);

  const [snapshotSort, setSnapshotSort] = useState<"realized_pnl" | "return_pct">("realized_pnl");
  const [topSnapshots, setTopSnapshots] = useState<TopSnapshotsResponse | null>(null);
  const [snapshotsLoading, setSnapshotsLoading] = useState(false);
  const [snapshotsErr, setSnapshotsErr] = useState("");
  const [snapshotPickIndex, setSnapshotPickIndex] = useState(0);
  const [syncSnapshotRequestFields, setSyncSnapshotRequestFields] = useState(true);

  const [strategyRec, setStrategyRec] = useState<StrategyRecommendationResponse | null>(null);
  const [strategyRecLoading, setStrategyRecLoading] = useState(false);

  const [decisionLogOpen, setDecisionLogOpen] = useState(false);
  const [decisionTail, setDecisionTail] = useState<LiveWorkerDecisionTailResponse | null>(null);
  const [decisionTailErr, setDecisionTailErr] = useState("");
  const [decisionTailLoading, setDecisionTailLoading] = useState(false);

  const strategyConfigParsed = useMemo(() => parseStrategyConfigObject(strategyJson), [strategyJson]);

  const patchStrategyConfig = useCallback((patch: Record<string, unknown>) => {
    setStrategyJson((prev) => {
      const cur = parseStrategyConfigObject(prev);
      const next = { ...cur, ...patch };
      return stringifyStrategyConfigWithHints(next);
    });
  }, []);

  const fetchStrategyRecommendation = useCallback(async () => {
    setStrategyRecLoading(true);
    try {
      const r = await apiGet<StrategyRecommendationResponse>(`${liveApiBase}/strategy-recommendation`, {
        cacheTtlMs: 0,
        timeoutMs: 12000,
        retries: 0,
      });
      setStrategyRec(r);
    } catch {
      setStrategyRec({
        ok: false,
        message: "无法加载系统推荐（请确认 API 已启动并可访问）。",
      });
    } finally {
      setStrategyRecLoading(false);
    }
  }, [liveApiBase]);

  const fetchDecisionTail = useCallback(async () => {
    setDecisionTailLoading(true);
    setDecisionTailErr("");
    try {
      const r = await apiGet<LiveWorkerDecisionTailResponse>(`${liveApiBase}/live-worker-decision-tail?limit=20`, {
        cacheTtlMs: 0,
        timeoutMs: 12000,
        retries: 0,
      });
      if (r && typeof r === "object" && r.ok === false) {
        setDecisionTailErr(typeof r.error === "string" ? r.error : "无法读取决策日志");
        setDecisionTail(null);
        return;
      }
      setDecisionTail(r && typeof r === "object" ? r : null);
    } catch (e: unknown) {
      setDecisionTailErr(e instanceof Error ? e.message : String(e));
      setDecisionTail(null);
    } finally {
      setDecisionTailLoading(false);
    }
  }, [liveApiBase]);

  useEffect(() => {
    void fetchStrategyRecommendation();
    const t = window.setInterval(() => void fetchStrategyRecommendation(), 600_000);
    return () => window.clearInterval(t);
  }, [fetchStrategyRecommendation]);

  useEffect(() => {
    if (!decisionLogOpen) return;
    void fetchDecisionTail();
    const t = window.setInterval(() => void fetchDecisionTail(), 30_000);
    return () => window.clearInterval(t);
  }, [decisionLogOpen, fetchDecisionTail]);

  const applyDraftStrategyFromJson = useCallback((cfg: LiveWorkerConfig) => {
    try {
      const sc = cfg.strategy_config && Object.keys(cfg.strategy_config).length ? cfg.strategy_config : {};
      setStrategyJson(stringifyStrategyConfigWithHints(sc));
    } catch {
      setStrategyJson("{}");
    }
  }, []);

  const loadConfig = useCallback(async () => {
    setCfgErr("");
    setCfgLoading(true);
    try {
      const r = await apiGet<{ config?: LiveWorkerConfig }>(`${liveApiBase}/live-worker-config`, {
        cacheTtlMs: 0,
        timeoutMs: 15000,
        retries: 1,
      });
      const cfg = mergeConfig(r?.config, liveVariant);
      setDraft(cfg);
      applyDraftStrategyFromJson(cfg);
    } catch (e: unknown) {
      setCfgErr(e instanceof Error ? e.message : String(e));
    } finally {
      setCfgLoading(false);
    }
  }, [applyDraftStrategyFromJson, liveApiBase, liveVariant]);

  const refreshStatus = useCallback(async () => {
    setSvcLoading(true);
    setSvcErr("");
    try {
      const r = await apiGet<ServicesStatus>("/setup/services/status", {
        cacheTtlMs: 0,
        timeoutMs: 10000,
        retries: 1,
      });
      setSvc(r);
      setSvcErr("");
    } catch (e: unknown) {
      // 保留最近一次成功状态，避免单次请求失败造成“运行中/已停止”闪烁。
      setSvcErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSvcLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  useEffect(() => {
    void refreshStatus();
    const t = window.setInterval(() => {
      void refreshStatus();
    }, 5000);
    return () => window.clearInterval(t);
  }, [refreshStatus]);

  const fetchTopSnapshots = useCallback(async () => {
    setSnapshotsErr("");
    setSnapshotsLoading(true);
    try {
      const r = await apiGet<TopSnapshotsResponse>(
        `${strategyApiBase}/snapshots/top?top=5&sort=${encodeURIComponent(snapshotSort)}`,
        { cacheTtlMs: 0, timeoutMs: 30000, retries: 1 }
      );
      setTopSnapshots(r);
      const maxIdx = Math.max(0, (r?.runs?.length || 1) - 1);
      setSnapshotPickIndex((prev) => Math.max(0, Math.min(prev, maxIdx)));
    } catch (e: unknown) {
      setSnapshotsErr(e instanceof Error ? e.message : String(e));
      setTopSnapshots(null);
    } finally {
      setSnapshotsLoading(false);
    }
  }, [snapshotSort]);

  useEffect(() => {
    void fetchTopSnapshots();
  }, [fetchTopSnapshots]);

  const applySelectedTopSnapshot = () => {
    const runs = topSnapshots?.runs || [];
    const run = runs[snapshotPickIndex];
    if (!run) {
      setCfgErr("没有可选快照，请先跑回测并保存快照，或点击刷新。");
      return;
    }
    const sc = run.strategy_config;
    if (!sc || typeof sc !== "object" || Array.isArray(sc)) {
      setCfgErr("该条快照缺少 strategy_config，无法同步。");
      return;
    }
    const nextSc = pruneLiveWorkerStrategyConfig({ ...sc });
    setStrategyJson(stringifyStrategyConfigWithHints(nextSc));
    setDraft((d) => {
      let next: LiveWorkerConfig = { ...d, strategy_config: nextSc };
      if (syncSnapshotRequestFields && run.request && typeof run.request === "object" && !Array.isArray(run.request)) {
        const req = run.request;
        if (typeof req.symbol === "string" && req.symbol.trim()) {
          next = { ...next, symbol: req.symbol.trim().toUpperCase() };
        }
        if (typeof req.kline === "string" && req.kline.trim()) {
          next = { ...next, kline: req.kline.trim() };
        }
        if (Number.isFinite(Number(req.days))) {
          next = { ...next, history_days: Math.max(1, Math.min(60, Math.floor(Number(req.days)))) };
        }
      }
      return next;
    });
    setCfgErr("");
    setCfgMsg(
      `已载入 TOP5 第 ${snapshotPickIndex + 1} 条快照的 strategy_config${syncSnapshotRequestFields ? "，并已按快照 request 更新标的/K 线/历史天数（若存在）" : ""}。请点「保存配置」写入 live_worker_config.json。`
    );
    window.setTimeout(() => setCfgMsg(""), 8000);
  };

  const buildBodyForApi = useCallback((): LiveWorkerConfig | null => {
    try {
      const cleaned = stripStrategyConfigHashComments(strategyJson || "");
      const parsed = JSON.parse(cleaned || "{}") as unknown;
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setCfgErr("策略 JSON 必须是对象");
        setJsonInlineErr("JSON 必须是对象（最外层请用 { ... }）");
        return null;
      }
      setCfgErr("");
      setJsonInlineErr("");
      const v = coerceStrategyVariant((parsed as Record<string, unknown>).strategy_variant);
      const filtered = completeStrategyConfigByVariant(v, { ...(parsed as Record<string, unknown>), strategy_variant: v });
      return { ...draft, strategy_config: filtered };
    } catch {
      setCfgErr("策略 JSON 解析失败");
      setJsonInlineErr("JSON 解析失败，请检查逗号、引号与括号是否匹配。");
      return null;
    }
  }, [draft, strategyJson]);

  const saveConfig = async () => {
    setCfgMsg("");
    setCfgErr("");
    const body = buildBodyForApi();
    if (!body) return;
    setOpLoading(true);
    setPinSaving(true);
    try {
      await apiPut(`${liveApiBase}/live-worker-config`, body, { timeoutMs: 15000, retries: 0 });
      // 写后回读校验，避免“点了但未落盘”的错觉
      const verify = await apiGet<{ config?: LiveWorkerConfig }>(`${liveApiBase}/live-worker-config`, {
        cacheTtlMs: 0,
        timeoutMs: 15000,
        retries: 0,
      });
      const saved = mergeConfig(verify?.config, liveVariant);
      setDraft(saved);
      applyDraftStrategyFromJson(saved);
      const wanted = JSON.stringify(body.strategy_config ?? {});
      const got = JSON.stringify(saved.strategy_config ?? {});
      if (wanted !== got) {
        setCfgErr("保存后校验失败：配置文件中的 strategy_config 与当前编辑内容不一致（可能被其它操作覆盖）。");
        return;
      }
      setCfgMsg(`已固定并写入 ${dataDirLabel}/live_worker_config.json`);
      setTimeout(() => setCfgMsg(""), 4000);
    } catch (e: unknown) {
      setCfgErr(e instanceof Error ? e.message : String(e));
    } finally {
      setOpLoading(false);
      setPinSaving(false);
    }
  };

  const syncFromPage = () => {
    setCfgErr("");
    if (strategyConfig === null) {
      setCfgErr("当前页「高级策略 JSON」无效，无法同步；请先修正 JSON。");
      return;
    }
    const pruned = pruneLiveWorkerStrategyConfig({ ...strategyConfig });
    setDraft((d) => ({
      ...d,
      symbol: pageSymbol.trim() || d.symbol,
      kline: pageKline || d.kline,
      strategy_config: pruned,
    }));
    try {
      setStrategyJson(stringifyStrategyConfigWithHints(pruned));
    } catch {
      setStrategyJson("{}");
    }
    setCfgMsg("已从本页表单同步标的、K 线与策略参数（尚未写入服务器文件，请点「保存配置」）");
    setTimeout(() => setCfgMsg(""), 5000);
  };

  useEffect(() => {
    try {
      const cleaned = stripStrategyConfigHashComments(strategyJson || "");
      const parsed = JSON.parse(cleaned || "{}") as unknown;
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setJsonInlineErr("JSON 必须是对象（最外层请用 { ... }）");
        return;
      }
      setJsonInlineErr("");
    } catch {
      setJsonInlineErr("JSON 解析失败，请检查逗号、引号与括号是否匹配。");
    }
  }, [strategyJson]);

  const startWorker = async () => {
    setCfgErr("");
    if (!canRunOptionAuto) {
      setCfgErr("期权自动交易需要 Premium。");
      return;
    }
    if (!draft.dry_run && !String(draft.confirmation_token || "").trim()) {
      setCfgErr("非模拟模式需要提供 L3 confirmation_token（与 Setup 中 OPENCLAW_MCP_L3_CONFIRMATION_TOKEN 一致）。");
      return;
    }
    if (
      !draft.dry_run &&
      !confirm(
        "当前为实盘模式（dry_run 已关闭），Worker 可能在信号触发时真实下单。已确认保存最新配置并理解风险？"
      )
    ) {
      return;
    }
    const who = liveVariant === "1dte" ? "QQQ 1DTE" : "QQQ 0DTE";
    if (!confirm(`将仅启动 ${who} 实盘 Worker，不会启动飞书或股票 Auto Trader。继续？`)) return;
    setOpLoading(true);
    try {
      const body = buildBodyForApi();
      if (!body) {
        setOpLoading(false);
        return;
      }
      await apiPut(`${liveApiBase}/live-worker-config`, body, {
        timeoutMs: 15000,
        retries: 0,
      });
      setDraft(body);
      await apiPost(
        "/setup/services/start",
        {
          start_feishu_bot: false,
          enable_auto_trader: false,
          ...(liveVariant === "1dte"
            ? { enable_qqq_1dte_live: true }
            : { enable_qqq_0dte_live: true }),
        },
        { timeoutMs: 30000, retries: 0 }
      );
      setCfgMsg("已保存配置并发送启动命令");
      setTimeout(() => setCfgMsg(""), 4000);
      void refreshStatus();
    } catch (e: unknown) {
      setCfgErr(e instanceof Error ? e.message : String(e));
    } finally {
      setOpLoading(false);
    }
  };

  const stopWorker = async () => {
    const who = liveVariant === "1dte" ? "QQQ 1DTE" : "QQQ 0DTE";
    if (!confirm(`停止 ${who} 实盘 Worker？`)) return;
    setOpLoading(true);
    try {
      await apiPost(
        "/setup/services/stop",
        {
          stop_feishu_bot: false,
          stop_auto_trader: false,
          ...(liveVariant === "1dte"
            ? { stop_qqq_1dte_live: true }
            : { stop_qqq_0dte_live: true }),
        },
        { timeoutMs: 30000, retries: 0 }
      );
      setCfgMsg("已发送停止命令");
      setTimeout(() => setCfgMsg(""), 4000);
      void refreshStatus();
    } catch (e: unknown) {
      setCfgErr(e instanceof Error ? e.message : String(e));
    } finally {
      setOpLoading(false);
    }
  };

  const qRunning = liveVariant === "1dte" ? Boolean(svc?.qqq_1dte_live_running) : Boolean(svc?.qqq_0dte_live_running);
  const qPid = liveVariant === "1dte" ? svc?.qqq_1dte_live_pid : svc?.qqq_0dte_live_pid;
  const qTracking = liveVariant === "1dte" ? svc?.qqq_1dte_live_tracking : svc?.qqq_0dte_live_tracking;
  const qRuntime = liveVariant === "1dte" ? svc?.qqq_1dte_live_runtime : svc?.qqq_0dte_live_runtime;
  const qRuntimeLabel = liveVariant === "1dte" ? "qqq_1dte_live_runtime" : "qqq_0dte_live_runtime";
  const panelTitle = liveVariant === "1dte" ? "实盘自动交易（QQQ 1DTE）" : "实盘自动交易（QQQ 0DTE）";
  const qRuntimeInner = asRecord(qRuntime?.runtime);
  const qRuntimeStateLabel = asString(qRuntime?.state_label) || (qRunning ? "进程存活" : "已停止");
  const qRuntimeStatus = asString(qRuntime?.runtime_status) || "—";
  const qRuntimeSeverity = asString(qRuntime?.state_severity);
  const qStatusText = svcLoading ? "刷新…" : svc ? qRuntimeStateLabel : "状态未知";
  const qStatusClass =
    !svc
      ? "text-amber-300"
      : qRuntimeSeverity === "good"
        ? "font-medium text-emerald-400"
        : qRuntimeSeverity === "bad"
          ? "font-medium text-rose-300"
          : qRuntimeSeverity === "warn"
            ? "font-medium text-amber-300"
            : "text-slate-500";
  const qProcessText = qRunning ? "存活" : "已停止";
  const qProcessClass = qRunning ? "text-emerald-400" : "text-slate-500";
  const qOwnerId = asString(qRuntimeInner?.owner_id) || "—";
  const qAccountId = asString(qRuntimeInner?.account_id) || "—";
  const qBrokerProvider = asString(qRuntimeInner?.broker_provider) || "—";
  const qBarsSource = asString(qRuntimeInner?.bars_source) || "—";
  const qQuoteSource = asString(qRuntimeInner?.realtime_quote_source) || "—";
  const qBarsToday = asNumber(qRuntimeInner?.bars_today);
  const qLastBar = asString(qRuntimeInner?.last_bar_naive_wall) || asString(qRuntimeInner?.last_bar) || "—";
  const qLastLoopAt = asString(qRuntimeInner?.last_loop_at) || "—";
  const qQuote = asRecord(qRuntimeInner?.realtime_quote);
  const qQuoteAvailable = qQuote?.available === true;
  const qQuoteLast = asNumber(qQuote?.last);
  const qQuoteChangePct = formatPct(qQuote?.change_pct);
  const qQuoteAvailText = qQuoteAvailable ? "可用" : "不可用";
  const qQuoteAvailClass = qQuoteAvailable ? "text-emerald-300" : "text-amber-300";
  const qLastAction = asRecord(qRuntimeInner?.last_action);
  const qLastActionText = asString(qLastAction?.action) || "—";
  const qRestored = asRecord(qRuntimeInner?.restored_open_position);
  const qRestoreOn = qRestored?.restored === true;
  const qRestoreReason = asString(qRestored?.reason);
  const qRestoreAccountId = asString(qRestored?.account_id) || qAccountId;
  const qRestoreBrokerProvider = asString(qRestored?.broker_provider) || qBrokerProvider;
  const qRestoreMode = asString(qRestored?.mode) || "—";
  const qRestoreSource = asString(qRestored?.source) || "—";
  const qRestoreCall = asString(qRestored?.call_symbol);
  const qRestorePut = asString(qRestored?.put_symbol);
  const qRestoreSingle = asString(qRestored?.symbol);
  const qRestoreEntryTime = asString(qRestored?.entry_time) || "—";
  const qRestoreContracts = asNumber(qRestored?.contracts);
  const qSnapshotPath = asString(qRuntimeInner?.open_state_snapshot_path) || "—";
  const qSnapshotSavedAt = asString(qRuntimeInner?.open_state_snapshot_saved_at) || "—";
  const qRestoreSummary =
    qRestoreMode === "strangle"
      ? `${qRestoreCall || "—"} / ${qRestorePut || "—"}`
      : qRestoreSingle || "—";

  return (
    <div className="panel space-y-4 border-emerald-500/25 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-emerald-950/20">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="section-title text-emerald-200/90">{panelTitle}</div>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-slate-400">
            后台进程轮询 K 线并调用 <span className="font-mono text-slate-300">resolve-contract</span> 与{" "}
            <span className="font-mono text-slate-300">POST /options/order</span>
            ；与 LongPort Launcher / Setup 共用启停。首次请复制{" "}
            <span className="font-mono text-cyan-300/90">live_worker_config.example.json</span> 为{" "}
            <span className="font-mono text-cyan-300/90">{dataDirLabel}/live_worker_config.json</span> 或直接在本页保存。
          </p>
        </div>
        <div className="flex flex-col items-end gap-1 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-slate-500">进程</span>
            <span className={qProcessClass}>{qProcessText}</span>
            <span className="text-slate-600">|</span>
            <span className="text-slate-500">运行态</span>
            <span className={qStatusClass}>{qStatusText}</span>
            {qPid != null ? (
              <span className="rounded border border-slate-600 bg-slate-950/60 px-2 py-0.5 font-mono text-slate-300">
                pid {qPid}
              </span>
            ) : null}
            {qTracking ? (
              <span className="text-slate-600" title="tracking">
                {qTracking}
              </span>
            ) : null}
          </div>
          <button type="button" className="text-cyan-400/90 underline-offset-2 hover:underline" onClick={() => void refreshStatus()}>
            刷新状态
          </button>
          {svcErr ? <span className="max-w-xs truncate text-[10px] text-amber-300/90">状态刷新失败，显示最近一次结果</span> : null}
        </div>
      </div>

      {cfgErr ? <div className="rounded-lg border border-rose-500/40 bg-rose-950/30 px-3 py-2 text-sm text-rose-200">{cfgErr}</div> : null}
      {cfgMsg ? <div className="rounded-lg border border-emerald-500/35 bg-emerald-950/25 px-3 py-2 text-sm text-emerald-100">{cfgMsg}</div> : null}
      {!canRunOptionAuto ? (
        <EntitlementNotice feature="option_auto_trading" plan={entitlements.plan} title="期权自动交易需要 Premium" />
      ) : null}

      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-secondary" disabled={cfgLoading || opLoading} onClick={() => void loadConfig()}>
          重新加载配置
        </button>
        <button type="button" className="btn-secondary" disabled={opLoading} onClick={() => syncFromPage()}>
          从本页表单同步策略
        </button>
        <button type="button" className="btn-primary" disabled={opLoading} onClick={() => void saveConfig()}>
          保存配置
        </button>
        <button type="button" className="btn-primary disabled:opacity-40" disabled={opLoading || qRunning || !canRunOptionAuto} onClick={() => void startWorker()}>
          启动 Worker
        </button>
        <button
          type="button"
          className="btn-secondary border-rose-500/40 text-rose-200 hover:bg-rose-950/40"
          disabled={opLoading || !qRunning}
          onClick={() => void stopWorker()}
        >
          停止 Worker
        </button>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-5">
        <div className="rounded-lg border border-slate-700 bg-slate-950/40 px-3 py-2">
          <div className="text-[11px] text-slate-500">Worker 状态</div>
          <div className={`mt-1 text-sm ${qStatusClass}`}>{qStatusText}</div>
          <div className="mt-1 text-[10px] font-mono text-slate-500">runtime.status = {qRuntimeStatus}</div>
        </div>
        <div className="rounded-lg border border-slate-700 bg-slate-950/40 px-3 py-2">
          <div className="text-[11px] text-slate-500">行情与分时</div>
          <div className={`mt-1 text-sm ${qQuoteAvailClass}`}>
            Quote {qQuoteAvailText}
            {qQuoteLast != null ? ` · ${qQuoteLast.toFixed(2)} · ${qQuoteChangePct}` : ""}
          </div>
          <div className="mt-1 text-[10px] text-slate-500">quote={qQuoteSource} · bars={qBarsSource}</div>
        </div>
        <div className="rounded-lg border border-slate-700 bg-slate-950/40 px-3 py-2">
          <div className="text-[11px] text-slate-500">当日进度</div>
          <div className="mt-1 text-sm text-slate-200">bars_today {qBarsToday ?? "—"}</div>
          <div className="mt-1 text-[10px] text-slate-500">last_bar {qLastBar}</div>
        </div>
        <div className="rounded-lg border border-slate-700 bg-slate-950/40 px-3 py-2">
          <div className="text-[11px] text-slate-500">上下文</div>
          <div className="mt-1 text-sm text-slate-200">owner {qOwnerId}</div>
          <div className="mt-1 break-all text-[10px] text-slate-500">account {qAccountId}</div>
          <div className="mt-1 text-[10px] text-slate-500">broker {qBrokerProvider} · last_action {qLastActionText} · loop {qLastLoopAt}</div>
        </div>
        <div className="rounded-lg border border-slate-700 bg-slate-950/40 px-3 py-2">
          <div className="text-[11px] text-slate-500">断连恢复</div>
          <div className={`mt-1 text-sm ${qRestoreOn ? "text-emerald-300" : "text-slate-400"}`}>
            {qRestoreOn ? "已恢复持仓" : "本次启动未恢复"}
          </div>
          <div className="mt-1 text-[10px] text-slate-500">
            {qRestoreOn
              ? `source=${qRestoreSource} · mode=${qRestoreMode} · contracts=${qRestoreContracts ?? "—"}`
              : qRestoreReason
                ? `reason=${qRestoreReason}`
                : `snapshot ${qSnapshotSavedAt}`}
          </div>
          <div className="mt-1 break-all text-[10px] text-slate-500">
            account {qRestoreAccountId} · broker {qRestoreBrokerProvider}
          </div>
          {qRestoreOn ? (
            <>
              <div className="mt-1 break-all font-mono text-[10px] text-slate-400">{qRestoreSummary}</div>
              <div className="mt-1 text-[10px] text-slate-500">entry {qRestoreEntryTime}</div>
            </>
          ) : (
            <div className="mt-1 break-all text-[10px] text-slate-600">{qSnapshotPath}</div>
          )}
        </div>
      </div>

      <div className="rounded-lg border border-amber-500/30 bg-amber-950/15 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm font-semibold text-amber-100/95">系统推荐策略（仅供参考）</div>
          <div className="flex items-center gap-2">
            {strategyRecLoading ? <span className="text-xs text-slate-500">加载中…</span> : null}
            <button
              type="button"
              className="text-xs text-cyan-400/90 underline-offset-2 hover:underline"
              onClick={() => void fetchStrategyRecommendation()}
            >
              立即刷新
            </button>
          </div>
        </div>
        <p className="mt-1 text-[11px] leading-snug text-slate-500">
          优先展示<strong>实盘 Worker</strong>约每 <span className="font-mono">10 分钟</span> 写入的 JSON；若尚无文件，后端会用同套规则<strong>即时拉行情/K 线</strong>计算。
          依据包括现价、相对前收涨跌、前交易日高低、量能比、VIX 等；<strong>不参与下单</strong>，与下方 strategy_variant 无关。Worker 失败时可查看{" "}
          <span className="font-mono">{dataDirLabel}/strategy_recommendation_error.json</span>。
        </p>
        {strategyRec?.ok === false ? (
          <p className="mt-2 text-xs text-amber-200/90">
            {strategyRec.message || strategyRec.error || "暂无推荐数据"}
          </p>
        ) : strategyRec?.recommended_name_zh ? (
          <div className="mt-2 space-y-2 text-xs">
            <div>
              <span className="text-slate-500">当前推荐：</span>
              <span className="font-medium text-cyan-200">{strategyRec.recommended_name_zh}</span>
              {strategyRec.recommended_variant ? (
                <span className="ml-2 font-mono text-[10px] text-slate-500">({strategyRec.recommended_variant})</span>
              ) : null}
            </div>
            {strategyRec.generated_at ? (
              <p className="font-mono text-[10px] text-slate-500">生成时间（UTC）：{strategyRec.generated_at}</p>
            ) : null}
            {strategyRec.source ? (
              <p className="text-[10px] text-slate-500">
                来源：
                {strategyRec.source === "worker_file"
                  ? "Worker 定时写入"
                  : strategyRec.source === "api_on_demand"
                    ? "API 即时计算（无 Worker 文件时）"
                    : strategyRec.source}
              </p>
            ) : null}
            {strategyRec.note ? <p className="text-[10px] text-slate-500">{strategyRec.note}</p> : null}
            {strategyRec.reasons && strategyRec.reasons.length > 0 ? (
              <ul className="list-inside list-disc space-y-0.5 text-slate-300">
                {strategyRec.reasons.map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            ) : null}
            {strategyRec.scores && Object.keys(strategyRec.scores).length > 0 ? (
              <p className="font-mono text-[10px] text-slate-500">
                启发式得分：{" "}
                {Object.entries(strategyRec.scores)
                  .map(([k, v]) => `${k}=${Number(v).toFixed(1)}`)
                  .join(" · ")}
              </p>
            ) : null}
            {strategyRec.disclaimer ? <p className="text-[10px] leading-snug text-slate-500">{strategyRec.disclaimer}</p> : null}
          </div>
        ) : (
          <p className="mt-2 text-xs text-slate-500">尚无推荐内容，启动 Worker 后等待最多约 10 分钟或点击「立即刷新」。</p>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        <label className="space-y-1">
          <div className="field-label">API 基址（Worker 请求用）</div>
          <input
            className="input-base font-mono text-sm"
            value={draft.api_base_url}
            onChange={(e) => setDraft((d) => ({ ...d, api_base_url: e.target.value.trim() }))}
          />
        </label>
        <label className="space-y-1">
          <div className="field-label">标的</div>
          <input
            className="input-base"
            value={draft.symbol}
            onChange={(e) => setDraft((d) => ({ ...d, symbol: e.target.value.trim().toUpperCase() }))}
          />
        </label>
        <label className="space-y-1">
          <div className="field-label">拉历史天数（轮询）</div>
          <input
            className="input-base"
            type="number"
            min={1}
            max={60}
            value={draft.history_days}
            onChange={(e) => setDraft((d) => ({ ...d, history_days: Math.max(1, Math.floor(Number(e.target.value) || 1)) }))}
          />
        </label>
        <label className="space-y-1">
          <div className="field-label">K 线周期（与 Worker 一致）</div>
          <select className="input-base" value={draft.kline} onChange={(e) => setDraft((d) => ({ ...d, kline: e.target.value }))}>
            <option value="1m">1分K</option>
            <option value="5m">5分K</option>
            <option value="10m">10分K</option>
            <option value="30m">30分K</option>
            <option value="1h">1小时K</option>
            <option value="1d">日K</option>
          </select>
        </label>
        <label className="space-y-1">
          <div className="field-label">轮询间隔（秒）</div>
          <input
            className="input-base"
            type="number"
            min={5}
            max={600}
            value={draft.poll_seconds}
            onChange={(e) => setDraft((d) => ({ ...d, poll_seconds: Math.max(5, Math.floor(Number(e.target.value) || 30)) }))}
          />
        </label>
        <label className="space-y-1 md:col-span-2 lg:col-span-3">
          <div className="field-label">K 线无时区时间按此时区解释（覆盖策略 JSON 里的 assume_bars_timezone）</div>
          <select
            className="input-base"
            value={draft.kline_wall_clock_timezone}
            onChange={(e) => setDraft((d) => ({ ...d, kline_wall_clock_timezone: e.target.value }))}
          >
            <option value="Asia/Shanghai">Asia/Shanghai（境内网关常见：北京时间墙钟）</option>
            <option value="UTC">UTC（无时区字段实为 UTC 墙钟时选）</option>
            <option value="America/New_York">America/New_York（美东墙钟）</option>
          </select>
          <p className="text-[11px] leading-snug text-slate-500">
            与 <span className="font-mono">runtime.last_bar</span>（UTC）配套：naive 时刻先按此处解释，再换算为 UTC；选错会导致 RTH、session 与最后一根 K 全部错位。
          </p>
        </label>
        <label className="space-y-1">
          <div className="field-label">到期日 YYYY-MM-DD（留空则按下方「偏移天数」相对美东交易日解析）</div>
          <input
            className="input-base font-mono"
            placeholder="可选"
            value={draft.expiry_date ?? ""}
            onChange={(e) => {
              const t = e.target.value.trim();
              setDraft((d) => ({ ...d, expiry_date: t ? t : null }));
            }}
          />
        </label>
        <label className="space-y-1">
          <div className="field-label">到期偏移天数（无固定到期日时；0=美东当日，1=下一自然日等）</div>
          <input
            className="input-base"
            type="number"
            min={0}
            max={30}
            value={draft.expiry_offset_days ?? 0}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                expiry_offset_days: Math.max(0, Math.min(30, Math.floor(Number(e.target.value) || 0))),
              }))
            }
          />
        </label>
        <label className="flex cursor-pointer items-center gap-2 md:col-span-2">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-600"
            checked={draft.dry_run}
            onChange={(e) => setDraft((d) => ({ ...d, dry_run: e.target.checked }))}
          />
          <span className="text-sm text-slate-300">模拟模式（dry_run：仅记录意图，不下真实单）</span>
        </label>
        <label className="space-y-1 md:col-span-2">
          <div className="field-label">L3 确认令牌（实盘下单必填，见 Setup / OPENCLAW_MCP_L3_CONFIRMATION_TOKEN）</div>
          <input
            className="input-base font-mono text-sm"
            type="password"
            autoComplete="off"
            placeholder="dry_run 时可不填"
            value={draft.confirmation_token ?? ""}
            onChange={(e) => {
              const t = e.target.value;
              setDraft((d) => ({ ...d, confirmation_token: t.trim() ? t : null }));
            }}
          />
        </label>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <label className="space-y-1">
          <div className="field-label">解析：行权价半宽</div>
          <input
            className="input-base"
            type="number"
            min={0.5}
            step={0.5}
            value={draft.resolve.strike_window}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                resolve: { ...d.resolve, strike_window: Math.max(0.5, Number(e.target.value) || 5) },
              }))
            }
          />
        </label>
        <label className="flex cursor-pointer items-center gap-2 pt-7">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-600"
            checked={draft.resolve.standard_only}
            onChange={(e) => setDraft((d) => ({ ...d, resolve: { ...d.resolve, standard_only: e.target.checked } }))}
          />
          <span className="text-sm text-slate-300">仅标准合约</span>
        </label>
        <label className="space-y-1">
          <div className="field-label">与请求行权价最大偏差</div>
          <input
            className="input-base"
            type="number"
            min={0.01}
            step={0.1}
            value={draft.resolve.max_strike_diff}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                resolve: { ...d.resolve, max_strike_diff: Math.max(0.01, Number(e.target.value) || 1.5) },
              }))
            }
          />
        </label>
      </div>

      <div className="rounded-lg border border-indigo-500/25 bg-indigo-950/20 px-3 py-3 space-y-3">
        <div className="field-label text-indigo-200/90">从回测快照同步（TOP5）</div>
        <p className="text-[11px] leading-relaxed text-slate-500">
          与页面「快照 TOP」同源：按{" "}
          <span className="font-mono text-slate-400">GET {strategyApiBase}/snapshots/top?top=5</span> 拉取；将选中条的{" "}
          <span className="font-mono text-slate-400">strategy_config</span> 一键填入下方文本框。需先在回测/矩阵中勾选保存快照。
        </p>
        {snapshotsErr ? <div className="text-xs text-rose-300">{snapshotsErr}</div> : null}
        <div className="flex flex-wrap items-end gap-2">
          <label className="space-y-1">
            <div className="text-[11px] text-slate-500">排序</div>
            <select
              className="input-base text-sm"
              value={snapshotSort}
              onChange={(e) => setSnapshotSort(e.target.value === "return_pct" ? "return_pct" : "realized_pnl")}
            >
              <option value="realized_pnl">按已实现盈亏（realized_pnl）</option>
              <option value="return_pct">按盈亏率（return_pct；旧快照无分母时排后）</option>
            </select>
          </label>
          <label className="space-y-1 min-w-[220px] flex-1">
            <div className="text-[11px] text-slate-500">选择快照</div>
            <select
              className="input-base w-full text-sm"
              value={snapshotPickIndex}
              onChange={(e) => setSnapshotPickIndex(Math.max(0, Math.min(4, Number(e.target.value) || 0)))}
              disabled={!topSnapshots?.runs?.length}
            >
              {(topSnapshots?.runs || []).length === 0 ? (
                <option value={0}>暂无快照</option>
              ) : (
                (topSnapshots?.runs || []).map((run, idx) => (
                  <option key={run.id || idx} value={idx}>
                    {snapshotOptionLabel(run, idx, snapshotSort)}
                  </option>
                ))
              )}
            </select>
          </label>
          <button type="button" className="btn-secondary text-sm" disabled={snapshotsLoading} onClick={() => void fetchTopSnapshots()}>
            {snapshotsLoading ? "刷新中…" : "刷新 TOP5"}
          </button>
          <button
            type="button"
            className="btn-primary text-sm"
            disabled={opLoading || !(topSnapshots?.runs || []).length}
            onClick={() => applySelectedTopSnapshot()}
          >
            一键同步到 strategy_config
          </button>
        </div>
        <label className="flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-600"
            checked={syncSnapshotRequestFields}
            onChange={(e) => setSyncSnapshotRequestFields(e.target.checked)}
          />
          <span className="text-xs text-slate-400">同时用快照里的标的、K 线周期、日历天数（request）更新上方 Worker 字段</span>
        </label>
      </div>

      <div className="space-y-1">
        <div className="field-label">strategy_config（写入 live_worker_config.json；可与表单「高级 JSON」一致）</div>
        <p className="text-[11px] leading-snug text-slate-500">
          每行字段后可带行尾注释，格式为 <span className="font-mono text-purple-400/90"># 含义说明 #</span>
          （紫色段为说明）；保存/启动前会自动剥离，落盘仍为标准 JSON。
        </p>
        <details className="mb-2 rounded-lg border border-slate-700 bg-slate-950/40 px-3 py-2">
          <summary className="cursor-pointer select-none text-sm text-slate-300">
            可读表单（折叠）· 修改后同步到下方 JSON；落盘仍点「确定并固定」
          </summary>
          <div className="mt-3 space-y-4 text-xs">
            {jsonInlineErr ? (
              <p className="text-rose-300">当前策略 JSON 无效，无法使用表单：请先在文本框中修正为合法对象。</p>
            ) : (
              <>
                <label className="block space-y-1">
                  <span className="text-[11px] text-slate-500">策略变体 strategy_variant</span>
                  <select
                    className="input-base w-full max-w-md text-sm"
                    value={strFromSc(strategyConfigParsed, "strategy_variant", "reaction_zone")}
                    onChange={(e) => {
                      const v = coerceStrategyVariant(e.target.value);
                      const cur = parseStrategyConfigObject(strategyJson);
                      const filtered = completeStrategyConfigByVariant(v, { ...cur, strategy_variant: v });
                      setStrategyJson(stringifyStrategyConfigWithHints(filtered));
                      setDraft((d) => ({ ...d, strategy_config: filtered }));
                    }}
                  >
                    <option value="reaction_zone">反应区 reaction_zone</option>
                    <option value="morning_strangle">早盘宽跨 morning_strangle</option>
                    <option value="morning_directional">早盘方向单 morning_directional</option>
                    <option value="gamma_scalping">Gamma 剥头皮 gamma_scalping</option>
                    <option value="gamma_pro">Gamma Pro gamma_pro</option>
                  </select>
                </label>

                <div className="rounded-md border border-slate-700/60 bg-slate-900/30 p-2 space-y-2">
                  <div className="text-[11px] font-medium text-slate-400">通用</div>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <label className="space-y-1">
                      <span className="text-[11px] text-slate-500">K 线无时区解释 assume_bars_timezone</span>
                      <input
                        className="input-base w-full font-mono text-xs"
                        value={strFromSc(strategyConfigParsed, "assume_bars_timezone", "UTC")}
                        onChange={(e) => patchStrategyConfig({ assume_bars_timezone: e.target.value })}
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-[11px] text-slate-500">行权价步长 strike_step</span>
                      <input
                        className="input-base w-full"
                        type="number"
                        step={0.5}
                        min={0.01}
                        value={numFromSc(strategyConfigParsed, "strike_step", 1)}
                        onChange={(e) => patchStrategyConfig({ strike_step: Math.max(0.01, Number(e.target.value) || 1) })}
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-[11px] text-slate-500">Call OTM 档数 call_strikes_otm</span>
                      <input
                        className="input-base w-full"
                        type="number"
                        step={1}
                        min={0}
                        value={Math.floor(numFromSc(strategyConfigParsed, "call_strikes_otm", 0))}
                        onChange={(e) => patchStrategyConfig({ call_strikes_otm: Math.max(0, Math.floor(Number(e.target.value) || 0)) })}
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-[11px] text-slate-500">Put OTM 档数 put_strikes_otm</span>
                      <input
                        className="input-base w-full"
                        type="number"
                        step={1}
                        min={0}
                        value={Math.floor(numFromSc(strategyConfigParsed, "put_strikes_otm", 0))}
                        onChange={(e) => patchStrategyConfig({ put_strikes_otm: Math.max(0, Math.floor(Number(e.target.value) || 0)) })}
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-[11px] text-slate-500">初始张数 initial_option_contracts</span>
                      <input
                        className="input-base w-full"
                        type="number"
                        step={1}
                        min={1}
                        value={Math.max(1, Math.floor(numFromSc(strategyConfigParsed, "initial_option_contracts", 1)))}
                        onChange={(e) =>
                          patchStrategyConfig({ initial_option_contracts: Math.max(1, Math.floor(Number(e.target.value) || 1)) })
                        }
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-[11px] text-slate-500">每日最大开仓次数 max_trades_per_day</span>
                      <input
                        className="input-base w-full"
                        type="number"
                        step={1}
                        min={1}
                        value={Math.max(1, Math.floor(numFromSc(strategyConfigParsed, "max_trades_per_day", 2)))}
                        onChange={(e) =>
                          patchStrategyConfig({ max_trades_per_day: Math.max(1, Math.floor(Number(e.target.value) || 1)) })
                        }
                      />
                    </label>
                    <label className="flex cursor-pointer items-center gap-2 pt-5">
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border-slate-600"
                        checked={boolFromSc(strategyConfigParsed, "log_decisions", true)}
                        onChange={(e) => patchStrategyConfig({ log_decisions: e.target.checked })}
                      />
                      <span className="text-slate-300">记录决策 log_decisions</span>
                    </label>
                  </div>
                </div>

                {strFromSc(strategyConfigParsed, "strategy_variant", "reaction_zone") === "reaction_zone" ? (
                  <div className="rounded-md border border-indigo-500/20 bg-indigo-950/15 p-2 space-y-2">
                    <div className="text-[11px] font-medium text-indigo-200/80">反应区</div>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">反应区半宽（占价 %）reaction_zone_half_width_pct×100</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={0.01}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "reaction_zone_half_width_pct", 0.0008) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({ reaction_zone_half_width_pct: Math.max(0, (Number(e.target.value) || 0) / 100) })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">放量倍数 volume_spike_multiplier</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={0.1}
                          min={0.1}
                          value={numFromSc(strategyConfigParsed, "volume_spike_multiplier", 2)}
                          onChange={(e) =>
                            patchStrategyConfig({ volume_spike_multiplier: Math.max(0.1, Number(e.target.value) || 2) })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">成交量回看根数 volume_lookback_bars</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={1}
                          value={Math.max(1, Math.floor(numFromSc(strategyConfigParsed, "volume_lookback_bars", 20)))}
                          onChange={(e) =>
                            patchStrategyConfig({ volume_lookback_bars: Math.max(1, Math.floor(Number(e.target.value) || 20)) })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">缺口阈值（%）gap_threshold_pct×100</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={0.01}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "gap_threshold_pct", 0.002) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({ gap_threshold_pct: Math.max(0, (Number(e.target.value) || 0) / 100) })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">止盈（%）take_profit_pct×100</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "take_profit_pct", 0.4) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({ take_profit_pct: Math.max(0, (Number(e.target.value) || 0) / 100) })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">止损（%）stop_loss_pct×100</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "stop_loss_pct", 0.35) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({ stop_loss_pct: Math.max(0, (Number(e.target.value) || 0) / 100) })
                          }
                        />
                      </label>
                    </div>
                  </div>
                ) : null}

                {strFromSc(strategyConfigParsed, "strategy_variant", "reaction_zone") === "morning_strangle" ? (
                  <div className="rounded-md border border-indigo-500/20 bg-indigo-950/15 p-2 space-y-2">
                    <div className="text-[11px] font-medium text-indigo-200/80">早盘宽跨</div>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">开仓开始（美东 HH:MM）strangle_entry_start_hhmm_et</span>
                        <input
                          className="input-base w-full font-mono text-xs"
                          placeholder="09:35"
                          value={strFromSc(strategyConfigParsed, "strangle_entry_start_hhmm_et", "09:35")}
                          onChange={(e) => patchStrategyConfig({ strangle_entry_start_hhmm_et: e.target.value })}
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">开仓结束 strangle_entry_end_hhmm_et</span>
                        <input
                          className="input-base w-full font-mono text-xs"
                          placeholder="12:00"
                          value={strFromSc(strategyConfigParsed, "strangle_entry_end_hhmm_et", "10:00")}
                          onChange={(e) => patchStrategyConfig({ strangle_entry_end_hhmm_et: e.target.value })}
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">强制平仓 strangle_force_close_hhmm_et</span>
                        <input
                          className="input-base w-full font-mono text-xs"
                          placeholder="14:00"
                          value={strFromSc(strategyConfigParsed, "strangle_force_close_hhmm_et", "12:00")}
                          onChange={(e) => patchStrategyConfig({ strangle_force_close_hhmm_et: e.target.value })}
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">相对前收最大偏离（%）strangle_range_pct×100</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={0.01}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "strangle_range_pct", 0.003) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({ strangle_range_pct: Math.max(0, (Number(e.target.value) || 0) / 100) })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">组合止盈盈亏率（%）strangle_take_profit_return×100</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "strangle_take_profit_return", 1) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({ strangle_take_profit_return: Math.max(0, (Number(e.target.value) || 0) / 100) })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">
                          组合止损盈亏率（%）strangle_stop_loss_return×100；0=关闭
                        </span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "strangle_stop_loss_return", 0) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({
                              strangle_stop_loss_return: Math.max(0, (Number(e.target.value) || 0) / 100),
                            })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">
                          组合止损冷静期（分钟）strangle_stop_loss_cooldown_minutes；0=关闭
                        </span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "strangle_stop_loss_cooldown_minutes", 0)}
                          onChange={(e) =>
                            patchStrategyConfig({
                              strangle_stop_loss_cooldown_minutes: Math.max(0, Math.floor(Number(e.target.value) || 0)),
                            })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">
                          长腿单腿止盈（%）OTM 档数较大；0=沿用旧单腿止盈 strangle_long_leg_take_profit_pct×100
                        </span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "strangle_long_leg_take_profit_pct", 0) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({
                              strangle_long_leg_take_profit_pct: Math.max(0, (Number(e.target.value) || 0) / 100),
                            })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">
                          短腿单腿止盈（%）OTM 档数较小；相同档数两腿都按短腿 strangle_short_leg_take_profit_pct×100
                        </span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "strangle_short_leg_take_profit_pct", 0) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({
                              strangle_short_leg_take_profit_pct: Math.max(0, (Number(e.target.value) || 0) / 100),
                            })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">
                          单腿止损（%）相对该腿开仓成本；0=关闭 strangle_leg_stop_loss_pct×100
                        </span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "strangle_leg_stop_loss_pct", 0) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({
                              strangle_leg_stop_loss_pct: Math.max(0, (Number(e.target.value) || 0) / 100),
                            })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">回测标的价字段 strangle_underlying_field</span>
                        <select
                          className="input-base w-full text-sm"
                          value={strFromSc(strategyConfigParsed, "strangle_underlying_field", "low")}
                          onChange={(e) => patchStrategyConfig({ strangle_underlying_field: e.target.value })}
                        >
                          <option value="open">open</option>
                          <option value="high">high</option>
                          <option value="low">low</option>
                          <option value="close">close</option>
                        </select>
                      </label>
                    </div>
                  </div>
                ) : null}

                {strFromSc(strategyConfigParsed, "strategy_variant", "reaction_zone") === "morning_directional" ? (
                  <div className="rounded-md border border-indigo-500/20 bg-indigo-950/15 p-2 space-y-2">
                    <div className="text-[11px] font-medium text-indigo-200/80">早盘方向单（与宽跨共用时间窗）</div>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">开仓开始 strangle_entry_start_hhmm_et</span>
                        <input
                          className="input-base w-full font-mono text-xs"
                          value={strFromSc(strategyConfigParsed, "strangle_entry_start_hhmm_et", "09:35")}
                          onChange={(e) => patchStrategyConfig({ strangle_entry_start_hhmm_et: e.target.value })}
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">开仓结束 strangle_entry_end_hhmm_et</span>
                        <input
                          className="input-base w-full font-mono text-xs"
                          value={strFromSc(strategyConfigParsed, "strangle_entry_end_hhmm_et", "10:00")}
                          onChange={(e) => patchStrategyConfig({ strangle_entry_end_hhmm_et: e.target.value })}
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">强制平仓 strangle_force_close_hhmm_et</span>
                        <input
                          className="input-base w-full font-mono text-xs"
                          value={strFromSc(strategyConfigParsed, "strangle_force_close_hhmm_et", "12:00")}
                          onChange={(e) => patchStrategyConfig({ strangle_force_close_hhmm_et: e.target.value })}
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">标的价字段 strangle_underlying_field</span>
                        <select
                          className="input-base w-full text-sm"
                          value={strFromSc(strategyConfigParsed, "strangle_underlying_field", "low")}
                          onChange={(e) => patchStrategyConfig({ strangle_underlying_field: e.target.value })}
                        >
                          <option value="open">open</option>
                          <option value="high">high</option>
                          <option value="low">low</option>
                          <option value="close">close</option>
                        </select>
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">下跌阈值（%）directional_down_pct×100</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={0.01}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "directional_down_pct", 0.01) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({ directional_down_pct: Math.max(0, (Number(e.target.value) || 0) / 100) })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">上涨阈值（%）directional_up_pct×100</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={0.01}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "directional_up_pct", 0.01) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({ directional_up_pct: Math.max(0, (Number(e.target.value) || 0) / 100) })
                          }
                        />
                      </label>
                      <label className="space-y-1 sm:col-span-2">
                        <span className="text-[11px] text-slate-500">单腿止盈盈亏率（%）directional_take_profit_return×100</span>
                        <input
                          className="input-base w-full max-w-xs"
                          type="number"
                          step={1}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "directional_take_profit_return", 1) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({ directional_take_profit_return: Math.max(0, (Number(e.target.value) || 0) / 100) })
                          }
                        />
                      </label>
                      <label className="space-y-1 sm:col-span-2">
                        <span className="text-[11px] text-slate-500">单腿止损（%）directional_stop_loss_pct×100，0 关闭</span>
                        <input
                          className="input-base w-full max-w-xs"
                          type="number"
                          step={1}
                          min={0}
                          max={95}
                          value={numFromSc(strategyConfigParsed, "directional_stop_loss_pct", 0) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({ directional_stop_loss_pct: Math.max(0, (Number(e.target.value) || 0) / 100) })
                          }
                        />
                      </label>
                    </div>
                  </div>
                ) : null}

                {strFromSc(strategyConfigParsed, "strategy_variant", "reaction_zone") === "gamma_scalping" ? (
                  <div className="rounded-md border border-indigo-500/20 bg-indigo-950/15 p-2 space-y-2">
                    <div className="text-[11px] font-medium text-indigo-200/80">Gamma 剥头皮</div>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">开仓开始 gamma_entry_start_hhmm_et</span>
                        <input
                          className="input-base w-full font-mono text-xs"
                          value={strFromSc(strategyConfigParsed, "gamma_entry_start_hhmm_et", "09:30")}
                          onChange={(e) => patchStrategyConfig({ gamma_entry_start_hhmm_et: e.target.value })}
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">开仓结束 gamma_entry_end_hhmm_et</span>
                        <input
                          className="input-base w-full font-mono text-xs"
                          value={strFromSc(strategyConfigParsed, "gamma_entry_end_hhmm_et", "10:00")}
                          onChange={(e) => patchStrategyConfig({ gamma_entry_end_hhmm_et: e.target.value })}
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">强制平仓 gamma_force_close_hhmm_et</span>
                        <input
                          className="input-base w-full font-mono text-xs"
                          value={strFromSc(strategyConfigParsed, "gamma_force_close_hhmm_et", "14:00")}
                          onChange={(e) => patchStrategyConfig({ gamma_force_close_hhmm_et: e.target.value })}
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">最长持有（分钟）gamma_max_hold_minutes</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={1}
                          value={Math.max(1, Math.floor(numFromSc(strategyConfigParsed, "gamma_max_hold_minutes", 15)))}
                          onChange={(e) =>
                            patchStrategyConfig({ gamma_max_hold_minutes: Math.max(1, Math.floor(Number(e.target.value) || 15)) })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">硬止损（%）gamma_hard_stop_loss_pct×100</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "gamma_hard_stop_loss_pct", 0.3) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({ gamma_hard_stop_loss_pct: Math.max(0, (Number(e.target.value) || 0) / 100) })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">止盈下限（%）gamma_take_profit_min_return×100</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "gamma_take_profit_min_return", 0.5) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({ gamma_take_profit_min_return: Math.max(0, (Number(e.target.value) || 0) / 100) })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">止盈上限（%）gamma_take_profit_max_return×100</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={0}
                          value={numFromSc(strategyConfigParsed, "gamma_take_profit_max_return", 1) * 100}
                          onChange={(e) =>
                            patchStrategyConfig({ gamma_take_profit_max_return: Math.max(0, (Number(e.target.value) || 0) / 100) })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">Call OTM 步数 gamma_call_otm_steps</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={0}
                          value={Math.max(0, Math.floor(numFromSc(strategyConfigParsed, "gamma_call_otm_steps", 1)))}
                          onChange={(e) =>
                            patchStrategyConfig({ gamma_call_otm_steps: Math.max(0, Math.floor(Number(e.target.value) || 0)) })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-slate-500">Put OTM 步数 gamma_put_otm_steps</span>
                        <input
                          className="input-base w-full"
                          type="number"
                          step={1}
                          min={0}
                          value={Math.max(0, Math.floor(numFromSc(strategyConfigParsed, "gamma_put_otm_steps", 1)))}
                          onChange={(e) =>
                            patchStrategyConfig({ gamma_put_otm_steps: Math.max(0, Math.floor(Number(e.target.value) || 0)) })
                          }
                        />
                      </label>
                      <label className="flex cursor-pointer items-center gap-2 sm:col-span-2">
                        <input
                          type="checkbox"
                          className="h-4 w-4 rounded border-slate-600"
                          checked={boolFromSc(strategyConfigParsed, "gamma_require_breakout_prev_day", true)}
                          onChange={(e) => patchStrategyConfig({ gamma_require_breakout_prev_day: e.target.checked })}
                        />
                        <span className="text-slate-300">要求突破昨高/低 gamma_require_breakout_prev_day</span>
                      </label>
                      <label className="flex cursor-pointer items-center gap-2 sm:col-span-2">
                        <input
                          type="checkbox"
                          className="h-4 w-4 rounded border-slate-600"
                          checked={boolFromSc(strategyConfigParsed, "gamma_require_vix_rising", true)}
                          onChange={(e) => patchStrategyConfig({ gamma_require_vix_rising: e.target.checked })}
                        />
                        <span className="text-slate-300">要求 VIX 上行 gamma_require_vix_rising</span>
                      </label>
                      <label className="flex cursor-pointer items-center gap-2 sm:col-span-2">
                        <input
                          type="checkbox"
                          className="h-4 w-4 rounded border-slate-600"
                          checked={boolFromSc(strategyConfigParsed, "gamma_enable_vwap_reversion", true)}
                          onChange={(e) => patchStrategyConfig({ gamma_enable_vwap_reversion: e.target.checked })}
                        />
                        <span className="text-slate-300">启用 VWAP 回归 gamma_enable_vwap_reversion</span>
                      </label>
                      <label className="flex cursor-pointer items-center gap-2 sm:col-span-2">
                        <input
                          type="checkbox"
                          className="h-4 w-4 rounded border-slate-600"
                          checked={boolFromSc(strategyConfigParsed, "gamma_require_leader_confirmation", true)}
                          onChange={(e) => patchStrategyConfig({ gamma_require_leader_confirmation: e.target.checked })}
                        />
                        <span className="text-slate-300">要求龙头确认 gamma_require_leader_confirmation</span>
                      </label>
                    </div>
                  </div>
                ) : null}
                {strFromSc(strategyConfigParsed, "strategy_variant", "reaction_zone") === "gamma_pro" ? (
                  <div className="rounded-md border border-indigo-500/20 bg-indigo-950/15 p-2 space-y-2">
                    <div className="text-[11px] font-medium text-indigo-200/80">Gamma Pro</div>
                    <p className="text-[10px] leading-snug text-slate-500">参数分三组，可点击标题折叠。</p>
                    <details className="rounded border border-indigo-500/20 bg-slate-950/30 px-2 py-1" open>
                      <summary className="cursor-pointer select-none text-[11px] font-medium text-indigo-100/90">入场信号与时间窗</summary>
                      <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                        <label className="space-y-1">
                          <span className="text-[11px] text-slate-500">开仓开始 gamma_pro_entry_start_hhmm_et</span>
                          <input className="input-base w-full font-mono text-xs" value={strFromSc(strategyConfigParsed, "gamma_pro_entry_start_hhmm_et", "10:00")} onChange={(e) => patchStrategyConfig({ gamma_pro_entry_start_hhmm_et: e.target.value })} />
                        </label>
                        <label className="space-y-1">
                          <span className="text-[11px] text-slate-500">开仓结束 gamma_pro_entry_end_hhmm_et</span>
                          <input className="input-base w-full font-mono text-xs" value={strFromSc(strategyConfigParsed, "gamma_pro_entry_end_hhmm_et", "15:30")} onChange={(e) => patchStrategyConfig({ gamma_pro_entry_end_hhmm_et: e.target.value })} />
                        </label>
                        <label className="space-y-1">
                          <span className="text-[11px] text-slate-500">午间暂停开始 gamma_pro_midday_skip_start_hhmm_et</span>
                          <input className="input-base w-full font-mono text-xs" value={strFromSc(strategyConfigParsed, "gamma_pro_midday_skip_start_hhmm_et", "12:00")} onChange={(e) => patchStrategyConfig({ gamma_pro_midday_skip_start_hhmm_et: e.target.value })} />
                        </label>
                        <label className="space-y-1">
                          <span className="text-[11px] text-slate-500">午间暂停结束 gamma_pro_midday_skip_end_hhmm_et</span>
                          <input className="input-base w-full font-mono text-xs" value={strFromSc(strategyConfigParsed, "gamma_pro_midday_skip_end_hhmm_et", "13:00")} onChange={(e) => patchStrategyConfig({ gamma_pro_midday_skip_end_hhmm_et: e.target.value })} />
                        </label>
                        <label className="space-y-1">
                          <span className="text-[11px] text-slate-500">午后信号开始 gamma_pro_afternoon_start_hhmm_et</span>
                          <input className="input-base w-full font-mono text-xs" value={strFromSc(strategyConfigParsed, "gamma_pro_afternoon_start_hhmm_et", "13:30")} onChange={(e) => patchStrategyConfig({ gamma_pro_afternoon_start_hhmm_et: e.target.value })} />
                        </label>
                        <label className="space-y-1">
                          <span className="text-[11px] text-slate-500">Call OTM 步数 gamma_pro_call_otm_steps</span>
                          <input className="input-base w-full" type="number" step={1} min={0} value={Math.max(0, Math.floor(numFromSc(strategyConfigParsed, "gamma_pro_call_otm_steps", 1)))} onChange={(e) => patchStrategyConfig({ gamma_pro_call_otm_steps: Math.max(0, Math.floor(Number(e.target.value) || 0)) })} />
                        </label>
                        <label className="space-y-1">
                          <span className="text-[11px] text-slate-500">Put OTM 步数 gamma_pro_put_otm_steps</span>
                          <input className="input-base w-full" type="number" step={1} min={0} value={Math.max(0, Math.floor(numFromSc(strategyConfigParsed, "gamma_pro_put_otm_steps", 1)))} onChange={(e) => patchStrategyConfig({ gamma_pro_put_otm_steps: Math.max(0, Math.floor(Number(e.target.value) || 0)) })} />
                        </label>
                        <label className="space-y-1 sm:col-span-2">
                          <span className="text-[11px] text-slate-500">VWAP 回踩容差（%）gamma_pro_vwap_pullback_pct×100</span>
                          <input className="input-base w-full" type="number" step={0.01} min={0} value={numFromSc(strategyConfigParsed, "gamma_pro_vwap_pullback_pct", 0.0015) * 100} onChange={(e) => patchStrategyConfig({ gamma_pro_vwap_pullback_pct: Math.max(0, (Number(e.target.value) || 0) / 100) })} />
                        </label>
                        <label className="flex cursor-pointer items-center gap-2 sm:col-span-2">
                          <input type="checkbox" className="h-4 w-4 rounded border-slate-600" checked={boolFromSc(strategyConfigParsed, "gamma_pro_enable_false_breakout_reversal", true)} onChange={(e) => patchStrategyConfig({ gamma_pro_enable_false_breakout_reversal: e.target.checked })} />
                          <span className="text-slate-300">启用假突破反向 gamma_pro_enable_false_breakout_reversal</span>
                        </label>
                      </div>
                    </details>
                    <details className="rounded border border-indigo-500/20 bg-slate-950/30 px-2 py-1" open>
                      <summary className="cursor-pointer select-none text-[11px] font-medium text-indigo-100/90">风控出场</summary>
                      <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                        <label className="space-y-1">
                          <span className="text-[11px] text-slate-500">强制平仓 gamma_pro_force_close_hhmm_et</span>
                          <input className="input-base w-full font-mono text-xs" value={strFromSc(strategyConfigParsed, "gamma_pro_force_close_hhmm_et", "15:45")} onChange={(e) => patchStrategyConfig({ gamma_pro_force_close_hhmm_et: e.target.value })} />
                        </label>
                        <label className="space-y-1">
                          <span className="text-[11px] text-slate-500">最长持有（分钟）gamma_pro_max_hold_minutes</span>
                          <input className="input-base w-full" type="number" step={1} min={1} value={Math.max(1, Math.floor(numFromSc(strategyConfigParsed, "gamma_pro_max_hold_minutes", 45)))} onChange={(e) => patchStrategyConfig({ gamma_pro_max_hold_minutes: Math.max(1, Math.floor(Number(e.target.value) || 45)) })} />
                        </label>
                        <label className="space-y-1">
                          <span className="text-[11px] text-slate-500">硬止损（%）gamma_pro_hard_stop_loss_pct×100</span>
                          <input className="input-base w-full" type="number" step={1} min={0} value={numFromSc(strategyConfigParsed, "gamma_pro_hard_stop_loss_pct", 0.3) * 100} onChange={(e) => patchStrategyConfig({ gamma_pro_hard_stop_loss_pct: Math.max(0, (Number(e.target.value) || 0) / 100) })} />
                        </label>
                        <label className="space-y-1">
                          <span className="text-[11px] text-slate-500">止盈阈值（%）gamma_pro_take_profit_return×100</span>
                          <input className="input-base w-full" type="number" step={1} min={0} value={numFromSc(strategyConfigParsed, "gamma_pro_take_profit_return", 0.6) * 100} onChange={(e) => patchStrategyConfig({ gamma_pro_take_profit_return: Math.max(0, (Number(e.target.value) || 0) / 100) })} />
                        </label>
                      </div>
                    </details>
                    <details className="rounded border border-indigo-500/20 bg-slate-950/30 px-2 py-1">
                      <summary className="cursor-pointer select-none text-[11px] font-medium text-indigo-100/90">过滤与确认</summary>
                      <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                        <label className="flex cursor-pointer items-center gap-2 sm:col-span-2">
                          <input type="checkbox" className="h-4 w-4 rounded border-slate-600" checked={boolFromSc(strategyConfigParsed, "gamma_pro_require_leader_confirmation", true)} onChange={(e) => patchStrategyConfig({ gamma_pro_require_leader_confirmation: e.target.checked })} />
                          <span className="text-slate-300">要求龙头确认 gamma_pro_require_leader_confirmation</span>
                        </label>
                        <label className="space-y-1">
                          <span className="text-[11px] text-slate-500">成交量回看 volume_lookback_bars</span>
                          <input className="input-base w-full" type="number" step={1} min={2} value={Math.max(2, Math.floor(numFromSc(strategyConfigParsed, "volume_lookback_bars", 20)))} onChange={(e) => patchStrategyConfig({ volume_lookback_bars: Math.max(2, Math.floor(Number(e.target.value) || 20)) })} />
                        </label>
                        <label className="space-y-1">
                          <span className="text-[11px] text-slate-500">突增倍数 volume_spike_multiplier</span>
                          <input className="input-base w-full" type="number" step={0.1} min={0.5} value={numFromSc(strategyConfigParsed, "volume_spike_multiplier", 2)} onChange={(e) => patchStrategyConfig({ volume_spike_multiplier: Math.max(0.1, Number(e.target.value) || 2) })} />
                        </label>
                      </div>
                    </details>
                  </div>
                ) : null}
              </>
            )}
          </div>
        </details>
        <StrategyConfigJsonTextarea value={strategyJson} onChange={setStrategyJson} minHeightClass="min-h-[140px]" />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn-primary text-sm disabled:opacity-40"
            disabled={opLoading || pinSaving || !!jsonInlineErr}
            onClick={() => void saveConfig()}
          >
            {pinSaving ? "固定中…" : "确定并固定（写入 live_worker_config）"}
          </button>
          <span className="text-[11px] text-slate-500">
            注意：仅在点击“确定并固定”后才会落盘；否则切换页面/重新加载会回到文件中的旧值。
          </span>
        </div>
        {jsonInlineErr ? <div className="text-[11px] text-rose-300">{jsonInlineErr}</div> : null}
      </div>

      <details
        className="rounded-lg border border-emerald-500/20 bg-slate-950/40 px-3 py-2"
        onToggle={(e) => setDecisionLogOpen(e.currentTarget.open)}
      >
        <summary className="cursor-pointer select-none text-sm text-slate-300">
          实盘决策日志（最近 20 条）· 来自 Worker 写入的 JSONL
        </summary>
        <div className="mt-2 space-y-2 text-xs">
          <p className="text-[11px] leading-relaxed text-slate-500">
            展开后每 30 秒自动刷新。可查看策略日志里的{" "}
            <span className="font-mono text-slate-400">skip_strangle_range</span>、
            <span className="font-mono text-slate-400">skip_strangle_entry_window</span>、
            <span className="font-mono text-slate-400">enter_strangle</span>，以及{" "}
            <span className="font-mono text-slate-400">entry</span> 下单是否 <span className="font-mono text-slate-400">ok</span>。
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn-secondary text-sm" disabled={decisionTailLoading} onClick={() => void fetchDecisionTail()}>
              {decisionTailLoading ? "加载中…" : "立即刷新"}
            </button>
            {decisionTail?.path ? (
              <span className="max-w-xl truncate font-mono text-[10px] text-slate-600" title={decisionTail.path}>
                {decisionTail.path}
              </span>
            ) : null}
          </div>
          {decisionTailErr ? <div className="text-[11px] text-rose-300">{decisionTailErr}</div> : null}
          {decisionLogOpen && !decisionTailLoading && !decisionTailErr && !(decisionTail?.items || []).length ? (
            <p className="text-[11px] text-slate-500">
              尚无记录：请确认 API 与 Worker 已更新到含决策日志的版本并已重启；若 API 读的 <span className="font-mono">{dataDirLabel}/</span>{" "}
              与 Worker 写入目录不是同一项目根，也会一直为空。展开后等待至多约 2 分钟（noop 心跳）或重启 Worker 后应至少出现{" "}
              <span className="font-mono">worker_started</span>。
            </p>
          ) : null}
          <div className="max-h-72 space-y-2 overflow-y-auto rounded border border-slate-800 bg-slate-950/60 p-2">
            {[...(decisionTail?.items || [])].reverse().map((row, idx) => (
              <div key={`${row.at || ""}-${row.bar_utc || ""}-${idx}`} className="border-b border-slate-800/80 pb-2 last:border-0 last:pb-0">
                <div className="flex flex-wrap items-baseline justify-between gap-1">
                  <span className="font-mono text-[10px] text-slate-500">{row.bar_naive_wall || row.bar_utc || row.at || "?"}</span>
                  <span
                    className={
                      liveWorkerActionLabel(row).includes("失败")
                        ? "text-rose-300"
                        : liveWorkerActionLabel(row) === "entry"
                          ? "text-emerald-300"
                          : liveWorkerActionLabel(row) === "hold"
                            ? "text-slate-400"
                            : "text-slate-300"
                    }
                  >
                    {liveWorkerActionLabel(row)}
                  </span>
                </div>
                <div className="mt-0.5 text-[11px] text-slate-200">{liveWorkerDecisionPrimaryLine(row)}</div>
                {row.action && typeof row.action === "object" && row.action.ok === false && row.action.detail != null ? (
                  <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] text-amber-200/90">
                    {(() => {
                      try {
                        return JSON.stringify(row.action.detail).slice(0, 500);
                      } catch {
                        return String(row.action.detail);
                      }
                    })()}
                  </pre>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      </details>

      {qRuntime ? (
        <details className="rounded-lg border border-slate-700 bg-slate-950/40 px-3 py-2">
          <summary className="cursor-pointer text-xs text-slate-400">调试：原始 setup 状态 JSON · {qRuntimeLabel}</summary>
          <pre className="mt-2 max-h-48 overflow-auto text-[11px] text-slate-500">{JSON.stringify(qRuntime, null, 2)}</pre>
        </details>
      ) : null}

      {cfgLoading ? <p className="text-xs text-slate-500">正在加载配置文件…</p> : null}
    </div>
  );
}
