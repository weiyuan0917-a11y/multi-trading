"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { PageShell } from "@/components/ui/page-shell";
import { localAgentGet as apiGet, localAgentPost as apiPost } from "@/lib/local-agent-api";

type LabInstance = "0dte" | "1dte";
type LabStrategyVariant = "morning_strangle" | "morning_directional";
type CandidateGenerator = "deterministic" | "tradingagents";
type ResearchDimension = "risk_controls" | "time_window" | "combined";

type DataQualityCheck = {
  id?: string;
  severity?: "ok" | "info" | "warn" | "error" | string;
  title?: string;
  detail?: string;
  value?: any;
};

type ValidationRow = {
  days?: number;
  ok?: boolean;
  error?: string;
  metrics?: Record<string, any>;
};

type LabCandidate = {
  candidate_id?: string;
  title?: string;
  generator?: string;
  generator_mode?: string;
  agent_action?: string;
  confidence?: number;
  reasoning?: string[];
  strategy_config_patch?: Record<string, any>;
  strategy_config?: Record<string, any>;
  research_controls?: Record<string, any>;
  validation?: {
    passed?: boolean;
    summary?: Record<string, any>;
    rows?: ValidationRow[];
    blockers?: string[];
    gate?: Record<string, any>;
  };
  safety_note?: string;
};

type LabRun = {
  run_id?: string;
  created_at?: string;
  instance?: LabInstance | string;
  status?: string;
  pipeline?: Array<{ stage?: string; label?: string; status?: string; mode?: string }>;
  data_quality?: {
    ok?: boolean;
    summary?: Record<string, any>;
    checks?: DataQualityCheck[];
    current_config?: Record<string, any>;
  };
  candidates?: LabCandidate[];
  approvals?: any[];
  approved_candidate_id?: string;
  disclaimer?: string;
};

type LabStatus = {
  ok?: boolean;
  instance?: string;
  data_quality?: {
    ok?: boolean;
    summary?: Record<string, any>;
    checks?: DataQualityCheck[];
    current_config?: Record<string, any>;
  };
  last_run?: LabRun | null;
  last_approval?: Record<string, any> | null;
  approval_history?: LabApproval[];
  capabilities?: Record<string, any>;
};

type DiffRow = {
  field?: string;
  before?: any;
  after?: any;
};

type LabApproval = {
  approval_id?: string;
  approved_at?: string;
  approved_by?: string;
  run_id?: string;
  candidate_id?: string;
  instance?: string;
  live_config_path?: string;
  diff?: DiffRow[];
};

type DiffPreview = {
  ok?: boolean;
  run_id?: string;
  candidate_id?: string;
  instance?: string;
  live_config_path?: string;
  strategy_config_patch?: Record<string, any>;
  diff?: DiffRow[];
  force?: boolean;
};

type LabTask = {
  task_id?: string;
  status?: "queued" | "running" | "completed" | "failed" | string;
  created_at?: string;
  updated_at?: string;
  completed_at?: string;
  instance?: string;
  progress_pct?: number;
  progress_stage?: string;
  progress_text?: string;
  error?: string;
  run_id?: string;
  run?: LabRun | null;
  events?: Array<{ ts?: string; stage?: string; pct?: number; text?: string }>;
};

const INSTANCE_OPTIONS: Array<{ value: LabInstance; label: string }> = [
  { value: "0dte", label: "QQQ 0DTE" },
  { value: "1dte", label: "QQQ 1DTE" },
];

const STRATEGY_OPTIONS: Array<{ value: LabStrategyVariant; label: string; description: string }> = [
  { value: "morning_strangle", label: "早盘宽跨", description: "窄幅震荡假设，双买 Call + Put" },
  { value: "morning_directional", label: "早盘方向单", description: "涨跌幅阈值触发单腿方向" },
];

const GENERATOR_OPTIONS: Array<{ value: CandidateGenerator; label: string }> = [
  { value: "deterministic", label: "规则生成器" },
  { value: "tradingagents", label: "TradingAgents 入口" },
];

const RESEARCH_DIMENSION_OPTIONS: Array<{ value: ResearchDimension; label: string; description: string }> = [
  { value: "risk_controls", label: "风控优先", description: "验证步长、止盈止损，不主动改时间" },
  { value: "time_window", label: "时间窗口优先", description: "验证入场窗口和强平时间，不主动改 TP/SL" },
  { value: "combined", label: "综合候选", description: "时间、步长和风控一起变化，需二次归因" },
];

const PIPELINE = [
  "智能体研究层",
  "回测与验证层",
  "人工确认 / 自动审批闸门",
  "live_worker_config 草稿",
  "QQQ 实盘 worker",
  "券商 API 下单",
];

const VALIDATION_WINDOWS_DAYS = [60, 120, 180];

function toneClass(tone?: string): string {
  const s = String(tone || "").toLowerCase();
  if (s === "ok" || s === "completed" || s === "passed") return "border-emerald-400/35 bg-emerald-400/10 text-emerald-100";
  if (s === "warn" || s === "waiting_for_human") return "border-amber-300/40 bg-amber-300/10 text-amber-100";
  if (s === "error" || s === "failed" || s === "blocked") return "border-rose-400/40 bg-rose-400/10 text-rose-100";
  return "border-slate-600 bg-slate-800/70 text-slate-300";
}

function actionLabel(action?: string): string {
  const a = String(action || "");
  if (a === "skip") return "建议跳过";
  if (a === "reduce_size") return "建议降尺寸";
  if (a === "normal_size") return "正常尺寸";
  return a || "-";
}

function fmt(value: any, digits = 2): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return value === null || value === undefined || value === "" ? "-" : String(value);
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function shortJson(value: any): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return "{}";
  }
}

function inlineValue(value: any): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function formatBeijingTime(value?: string): string {
  const raw = String(value || "").trim();
  if (!raw) return "-";
  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) return raw;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(dt);
}

function instanceLabel(value?: string): string {
  const raw = String(value || "").toLowerCase();
  if (raw === "1dte") return "QQQ 1DTE";
  if (raw === "0dte") return "QQQ 0DTE";
  return value || "-";
}

function strategyLabel(value?: string): string {
  const raw = String(value || "");
  return STRATEGY_OPTIONS.find((item) => item.value === raw)?.label || raw || "-";
}

function researchDimensionLabel(value?: string): string {
  const raw = String(value || "");
  return RESEARCH_DIMENSION_OPTIONS.find((item) => item.value === raw)?.label || raw || "风控优先";
}

function statusLabel(value?: string): string {
  const raw = String(value || "");
  if (raw === "completed") return "已完成";
  if (raw === "approved") return "已审批";
  if (raw === "failed") return "失败";
  if (raw === "running") return "运行中";
  if (raw === "queued") return "排队中";
  return raw || "-";
}

function runSummary(run?: LabRun | null): string {
  if (!run) return "暂无运行记录";
  const total = run.candidates?.length || 0;
  const passed = (run.candidates || []).filter((candidate) => Boolean(candidate.validation?.passed)).length;
  const variant = String(run.candidates?.[0]?.strategy_config?.strategy_variant || "");
  return `${formatBeijingTime(run.created_at)} 北京时间 · ${instanceLabel(String(run.instance || ""))} · ${strategyLabel(variant)} · ${passed}/${total} 通过 · ${statusLabel(run.status)}`;
}

function approvalSummary(item?: LabApproval | null): string {
  if (!item) return "暂无审批记录";
  return `${formatBeijingTime(item.approved_at)} 北京时间 · 候选 ${item.candidate_id || "-"} · ${item.diff?.length || 0} 个字段变化`;
}

const FIELD_LABELS: Record<string, string> = {
  strategy_variant: "策略变体",
  max_trades_per_day: "最大开仓次数",
  initial_option_contracts: "每次张数",
  call_strikes_otm: "Call OTM 步长",
  put_strikes_otm: "Put OTM 步长",
  strangle_entry_start_hhmm_et: "宽跨入场开始",
  strangle_entry_end_hhmm_et: "宽跨入场结束",
  strangle_force_close_hhmm_et: "强制平仓时间",
  strangle_range_pct: "允许偏离前收",
  strangle_take_profit_return: "组合止盈",
  strangle_stop_loss_return: "组合止损",
  strangle_stop_loss_cooldown_minutes: "组合止损冷却",
  strangle_long_leg_take_profit_pct: "长腿单腿止盈",
  strangle_short_leg_take_profit_pct: "短腿单腿止盈",
  strangle_leg_stop_loss_pct: "单腿止损",
  directional_down_pct: "方向单下跌阈值",
  directional_up_pct: "方向单上涨阈值",
  directional_take_profit_return: "方向单止盈",
  directional_stop_loss_pct: "方向单止损",
};

function fieldLabel(field?: string): string {
  return FIELD_LABELS[String(field || "")] || String(field || "-");
}

function candidateValue(candidate: LabCandidate | undefined, key: string): any {
  const patch = candidate?.strategy_config_patch || {};
  if (Object.prototype.hasOwnProperty.call(patch, key)) return patch[key];
  return candidate?.strategy_config?.[key];
}

function valueFromDiffOrCandidate(candidate: LabCandidate | undefined, diffRows: DiffRow[] | undefined, key: string): any {
  const row = (diffRows || []).find((item) => item.field === key);
  if (row && Object.prototype.hasOwnProperty.call(row, "after")) return row.after;
  return candidateValue(candidate, key);
}

function changedFieldSet(candidate: LabCandidate | undefined, diffRows?: DiffRow[]): Set<string> {
  if (diffRows) return new Set(diffRows.map((row) => String(row.field || "")).filter(Boolean));
  return new Set(Object.keys(candidate?.strategy_config_patch || {}));
}

function ratioPct(value: any, digits = 0): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return `${fmt(n * 100, digits)}%`;
}

function lossThreshold(value: any, digits = 0): string {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "关闭";
  return `-${ratioPct(n, digits)}`;
}

function validationRow(candidate: LabCandidate, days: number): ValidationRow | undefined {
  return (candidate.validation?.rows || []).find((row) => Number(row.days) === days);
}

function validationMetricText(candidate: LabCandidate, days: number, key: string, suffix = ""): string {
  const row = validationRow(candidate, days);
  if (!row) return "-";
  if (!row.ok) return "失败";
  const value = row.metrics?.[key];
  return value === null || value === undefined ? "-" : `${fmt(value)}${suffix}`;
}

function riskSummary(candidate: LabCandidate | undefined, diffRows?: DiffRow[]): string[] {
  if (!candidate) return ["未找到候选参数。"];
  const fields = changedFieldSet(candidate, diffRows);
  const hasAny = (keys: string[]) => keys.some((key) => fields.has(key));
  const get = (key: string) => valueFromDiffOrCandidate(candidate, diffRows, key);
  const lines: string[] = [];

  if (hasAny(["call_strikes_otm", "put_strikes_otm"])) {
    lines.push(`选约步长：Call ${inlineValue(get("call_strikes_otm"))} OTM，Put ${inlineValue(get("put_strikes_otm"))} OTM；近一步通常成本和 Gamma 更高，远一步通常更依赖较大波动。`);
  }
  if (hasAny(["strangle_take_profit_return"])) {
    lines.push(`组合止盈：${ratioPct(get("strangle_take_profit_return"))}；阈值越低越快落袋，也可能错过后续扩大收益。`);
  }
  if (hasAny(["strangle_long_leg_take_profit_pct", "strangle_short_leg_take_profit_pct"])) {
    lines.push(`单腿止盈：长腿 ${ratioPct(get("strangle_long_leg_take_profit_pct"))}，短腿 ${ratioPct(get("strangle_short_leg_take_profit_pct"))}。`);
  }
  if (hasAny(["strangle_stop_loss_return", "strangle_leg_stop_loss_pct", "strangle_stop_loss_cooldown_minutes"])) {
    lines.push(`止损保护：组合 ${lossThreshold(get("strangle_stop_loss_return"))}，单腿 ${lossThreshold(get("strangle_leg_stop_loss_pct"))}，冷却 ${inlineValue(get("strangle_stop_loss_cooldown_minutes"))} 分钟。`);
  }
  if (hasAny(["directional_down_pct", "directional_up_pct"])) {
    lines.push(`方向触发：下跌 ${ratioPct(get("directional_down_pct"), 2)} / 上涨 ${ratioPct(get("directional_up_pct"), 2)}。`);
  }
  if (hasAny(["directional_take_profit_return", "directional_stop_loss_pct"])) {
    lines.push(`方向单风控：止盈 ${ratioPct(get("directional_take_profit_return"))}，止损 ${lossThreshold(get("directional_stop_loss_pct"))}。`);
  }
  if (hasAny(["max_trades_per_day", "initial_option_contracts"])) {
    lines.push(`交易频率与尺寸：最多开仓 ${inlineValue(get("max_trades_per_day"))} 次，每次 ${inlineValue(get("initial_option_contracts"))} 张。`);
  }
  if (hasAny(["strangle_entry_start_hhmm_et", "strangle_entry_end_hhmm_et", "strangle_force_close_hhmm_et"])) {
    lines.push(`时间窗口：${inlineValue(get("strangle_entry_start_hhmm_et"))} - ${inlineValue(get("strangle_entry_end_hhmm_et"))} 入场，${inlineValue(get("strangle_force_close_hhmm_et"))} 强平。`);
  }

  const summary = candidate.validation?.summary || {};
  if (Object.keys(summary).length) {
    lines.push(`验证摘要：平均收益 ${fmt(summary.avg_return_pct)}%，最大回撤 ${fmt(summary.worst_drawdown_usd)}，最长连亏 ${fmt(summary.max_consecutive_losses, 0)}。`);
  }
  if (candidate.validation?.blockers?.length) {
    lines.push(`未通过原因：${candidate.validation.blockers.join(" · ")}`);
  }
  return lines.length ? lines : ["本候选没有检测到会改变实盘草稿的关键参数。"];
}

function pipelineStatus(run?: LabRun | null, label?: string): string {
  const rows = run?.pipeline || [];
  const found = rows.find((x) => String(x.label || "") === label || String(x.stage || "") === label);
  return found?.status || "pending";
}

export default function AgentStrategyLabPage() {
  const [instance, setInstance] = useState<LabInstance>("0dte");
  const [strategyVariant, setStrategyVariant] = useState<LabStrategyVariant>("morning_strangle");
  const [candidateGenerator, setCandidateGenerator] = useState<CandidateGenerator>("deterministic");
  const [researchDimension, setResearchDimension] = useState<ResearchDimension>("risk_controls");
  const [candidateCount, setCandidateCount] = useState(3);
  const [selectedWindows, setSelectedWindows] = useState<number[]>([60, 120, 180]);
  const [status, setStatus] = useState<LabStatus | null>(null);
  const [run, setRun] = useState<LabRun | null>(null);
  const [runs, setRuns] = useState<LabRun[]>([]);
  const [selectedRecord, setSelectedRecord] = useState("latest");
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [task, setTask] = useState<LabTask | null>(null);
  const [cacheLoading, setCacheLoading] = useState(false);
  const [cacheResults, setCacheResults] = useState<Array<Record<string, any>>>([]);
  const [approvingId, setApprovingId] = useState("");
  const [diffLoadingId, setDiffLoadingId] = useState("");
  const [diffPreview, setDiffPreview] = useState<DiffPreview | null>(null);
  const [rollbackLoading, setRollbackLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const latestRun = run || status?.last_run || null;
  const dataQuality = latestRun?.data_quality || status?.data_quality || null;
  const checks = dataQuality?.checks || [];
  const candidates = latestRun?.candidates || [];
  const approvedCandidateId = latestRun?.approved_candidate_id || status?.last_approval?.candidate_id || "";
  const currentConfig = dataQuality?.current_config || status?.data_quality?.current_config || {};
  const strategyConfig = currentConfig?.strategy_config && typeof currentConfig.strategy_config === "object" ? currentConfig.strategy_config : {};
  const cacheSymbol = String(currentConfig?.symbol || strategyConfig?.symbol || "QQQ.US").trim().toUpperCase() || "QQQ.US";
  const cacheKline = String(currentConfig?.kline || "1m").trim() || "1m";
  const approvalHistory = status?.approval_history || (status?.last_approval ? [status.last_approval as LabApproval] : []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [statusResp, runsResp] = await Promise.all([
        apiGet<LabStatus>(`/agent-strategy-lab/status?instance=${instance}`, { cacheTtlMs: 0, retries: 0, timeoutMs: 20000 }),
        apiGet<{ ok?: boolean; items?: LabRun[] }>(`/agent-strategy-lab/runs?instance=${instance}&limit=10`, {
          cacheTtlMs: 0,
          retries: 0,
          timeoutMs: 20000,
        }),
      ]);
      setStatus(statusResp);
      setRun(statusResp?.last_run || null);
      setRuns(runsResp?.items || []);
    } catch (e: any) {
      setError(e?.message || "加载失败");
    } finally {
      setLoading(false);
    }
  }, [instance]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!task?.task_id || !["queued", "running"].includes(String(task.status || ""))) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const resp = await apiGet<{ ok?: boolean; task?: LabTask }>(`/agent-strategy-lab/tasks/${task.task_id}`, {
            cacheTtlMs: 0,
            retries: 0,
            timeoutMs: 20000,
          });
          if (cancelled || !resp?.task) return;
          setTask(resp.task);
          if (resp.task.status === "completed") {
            if (resp.task.run) setRun(resp.task.run);
            setRunning(false);
            setMessage("Lab 后台任务完成，候选参数和验证结果已生成。");
            await load();
          } else if (resp.task.status === "failed") {
            setRunning(false);
            setError(resp.task.error || "Lab 后台任务失败");
          }
        } catch (e: any) {
          if (!cancelled) setError(e?.message || "查询 Lab 任务失败");
        }
      })();
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [load, task?.status, task?.task_id]);

  const createRun = useCallback(async () => {
    setRunning(true);
    setTask(null);
    setDiffPreview(null);
    setError("");
    setMessage("");
    try {
      const resp = await apiPost<{ ok?: boolean; async_run?: boolean; task?: LabTask }>(
        "/agent-strategy-lab/tasks",
        {
          instance,
          strategy_variant: strategyVariant,
          candidate_generator: candidateGenerator,
          research_dimension: researchDimension,
          validation_windows_days: selectedWindows.length ? selectedWindows : [60],
          max_candidates: candidateCount,
          kline: "1m",
          use_server_kline_cache: true,
          rth_only: true,
        },
        { timeoutMs: 30000, retries: 0 }
      );
      if (!resp?.task?.task_id) throw new Error("Lab task 没有返回 task_id");
      setTask(resp.task);
      setMessage("Lab 后台任务已创建，页面会自动刷新进度。");
    } catch (e: any) {
      setRunning(false);
      setError(e?.message || "运行 Lab 失败");
    }
  }, [candidateCount, candidateGenerator, instance, researchDimension, selectedWindows, strategyVariant]);

  const toggleWindow = useCallback((days: number) => {
    setSelectedWindows((prev) => {
      const has = prev.includes(days);
      const next = has ? prev.filter((x) => x !== days) : [...prev, days];
      return next.length ? next.sort((a, b) => a - b) : [days];
    });
  }, []);

  const downloadMissingKlineCache = useCallback(async () => {
    setCacheLoading(true);
    setError("");
    setMessage("");
    setCacheResults([]);
    const rows: Array<Record<string, any>> = [];
    try {
      for (const days of VALIDATION_WINDOWS_DAYS) {
        const resp = await apiPost<Record<string, any>>(
          "/backtest/kline-cache/fetch",
          {
            symbol: cacheSymbol,
            periods: 0,
            days,
            kline: cacheKline,
            force_refresh: false,
            source: "auto",
          },
          { timeoutMs: 600000, retries: 0 }
        );
        rows.push({ days, ...resp });
        setCacheResults([...rows]);
      }
      const totalBars = rows.reduce((sum, row) => sum + Number(row.bar_count || 0), 0);
      setMessage(`K线缓存检查完成：${cacheSymbol} ${cacheKline}，${rows.length} 个窗口，共 ${fmt(totalBars, 0)} 根。`);
      await load();
    } catch (e: any) {
      setError(e?.message || "下载K线缓存失败");
      if (rows.length) setCacheResults(rows);
    } finally {
      setCacheLoading(false);
    }
  }, [cacheKline, cacheSymbol, load]);

  const approve = useCallback(
    async (candidateId: string, force = false) => {
      if (!latestRun?.run_id || !candidateId) return;
      setApprovingId(candidateId);
      setError("");
      setMessage("");
      try {
        await apiPost(`/agent-strategy-lab/runs/${latestRun.run_id}/approve`, {
          candidate_id: candidateId,
          force,
        });
        setDiffPreview(null);
        setMessage("已写入 live_worker_config 草稿；不会启动 worker，也不会下单。");
        await load();
      } catch (e: any) {
        setError(e?.message || "审批写入失败");
      } finally {
        setApprovingId("");
      }
    },
    [latestRun?.run_id, load]
  );

  const previewDiff = useCallback(
    async (candidateId: string, force = false) => {
      if (!latestRun?.run_id || !candidateId) return;
      setDiffLoadingId(candidateId);
      setError("");
      setMessage("");
      try {
        const resp = await apiGet<DiffPreview>(
          `/agent-strategy-lab/runs/${encodeURIComponent(latestRun.run_id)}/candidates/${encodeURIComponent(candidateId)}/diff`,
          { cacheTtlMs: 0, retries: 0, timeoutMs: 20000 }
        );
        setDiffPreview({ ...resp, force });
      } catch (e: any) {
        setError(e?.message || "读取审批差异失败");
      } finally {
        setDiffLoadingId("");
      }
    },
    [latestRun?.run_id]
  );

  const rollbackLastApproval = useCallback(async () => {
    const latestApproval = approvalHistory[0];
    if (!latestApproval?.approval_id) return;
    setRollbackLoading(true);
    setError("");
    setMessage("");
    try {
      await apiPost("/agent-strategy-lab/approvals/rollback", {
        instance,
        approval_id: latestApproval.approval_id,
      });
      setMessage("已回滚到上一次审批前配置；不会启动 worker，也不会下单。");
      await load();
    } catch (e: any) {
      setError(e?.message || "回滚审批失败");
    } finally {
      setRollbackLoading(false);
    }
  }, [approvalHistory, instance, load]);

  const summaryCards = useMemo(() => {
    const s = dataQuality?.summary || {};
    return [
      { label: "数据状态", value: dataQuality?.ok ? "可研究" : "需检查", tone: dataQuality?.ok ? "ok" : "warn" },
      { label: "检查项", value: `${fmt(s.checks_total, 0)} 项`, tone: "info" },
      { label: "警告", value: fmt(s.warnings, 0), tone: Number(s.warnings || 0) ? "warn" : "ok" },
      { label: "最新日志", value: s.latest_decision_age_minutes == null ? "-" : `${fmt(s.latest_decision_age_minutes)} 分钟前`, tone: "info" },
    ];
  }, [dataQuality]);

  const activeStrategy = STRATEGY_OPTIONS.find((x) => x.value === strategyVariant);
  const activeGenerator = GENERATOR_OPTIONS.find((x) => x.value === candidateGenerator);
  const activeResearchDimension = RESEARCH_DIMENSION_OPTIONS.find((x) => x.value === researchDimension);
  const passedCount = candidates.filter((candidate) => Boolean(candidate.validation?.passed)).length;
  const blockedCount = Math.max(0, candidates.length - passedCount);
  const diffCandidate = diffPreview
    ? candidates.find((candidate) => String(candidate.candidate_id || "") === String(diffPreview.candidate_id || ""))
    : undefined;
  const latestApproval = approvalHistory[0] || null;
  const selectedApproval = selectedRecord.startsWith("approval:")
    ? approvalHistory.find((item) => `approval:${item.approval_id}` === selectedRecord) || latestApproval
    : latestApproval;
  const progressPct = Math.max(0, Math.min(100, Number(task?.progress_pct || 0)));
  const selectedWindowsLabel = selectedWindows.join(" / ");

  const selectRecord = useCallback(
    (value: string) => {
      setSelectedRecord(value);
      if (value.startsWith("run:")) {
        const runId = value.slice("run:".length);
        const found = runs.find((item) => String(item.run_id || "") === runId);
        if (found) setRun(found);
      }
    },
    [runs]
  );

  return (
    <PageShell>
      <div className="space-y-4">
        <div className="page-header items-start">
          <div>
            <h1 className="page-title">Agent Strategy Lab</h1>
            <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-400">
              生成候选参数、跑回测验证、审批写入配置草稿；实盘 worker 和券商下单不在这里触发。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className={`rounded-full border px-3 py-1 ${toneClass(dataQuality?.ok ? "ok" : "warn")}`}>
              数据 {dataQuality?.ok ? "可用" : "待检查"}
            </span>
            <span className="tag-muted">
              {passedCount}/{candidates.length || 0} 通过
            </span>
            <span className="tag-muted">3010 页面</span>
          </div>
        </div>

        <section className="panel">
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="section-title">研究配置</div>
                <p className="mt-1 text-xs text-slate-500">只保留影响本次 Lab 运行的选项。</p>
              </div>
              <span className="tag-muted">
                {researchDimensionLabel(researchDimension)} · {candidateCount} 候选 × {selectedWindows.length || 1} 窗口
              </span>
            </div>

            <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-[1fr_1.1fr_1.1fr_1.15fr_0.9fr]">
              <select className="input-base" value={instance} onChange={(e) => setInstance(e.target.value as LabInstance)} aria-label="实例">
                {INSTANCE_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
              <select className="input-base" value={strategyVariant} onChange={(e) => setStrategyVariant(e.target.value as LabStrategyVariant)} aria-label="策略">
                {STRATEGY_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
              <select className="input-base" value={researchDimension} onChange={(e) => setResearchDimension(e.target.value as ResearchDimension)} aria-label="研究维度">
                {RESEARCH_DIMENSION_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
              <select className="input-base" value={candidateGenerator} onChange={(e) => setCandidateGenerator(e.target.value as CandidateGenerator)} aria-label="候选来源">
                {GENERATOR_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
              <select
                className="input-base"
                value={candidateCount}
                onChange={(e) => setCandidateCount(Math.max(1, Math.min(3, Math.floor(Number(e.target.value) || 1))))}
                aria-label="候选数量"
              >
                <option value={1}>1 个候选</option>
                <option value={2}>2 个候选</option>
                <option value={3}>3 个候选</option>
              </select>
            </div>
            <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 px-3 py-2 text-xs leading-5 text-slate-400">
              <span className="font-semibold text-slate-300">研究维度：</span>
              {activeResearchDimension?.description || "验证步长、止盈止损，不主动改时间"}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-700/70 bg-slate-950/35 px-3 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-semibold text-slate-400">回测窗口</span>
                {VALIDATION_WINDOWS_DAYS.map((days) => (
                  <label
                    key={days}
                    className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-xs ${
                      selectedWindows.includes(days)
                        ? "border-cyan-300/40 bg-cyan-400/10 text-cyan-100"
                        : "border-slate-700 bg-slate-950/35 text-slate-300"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 rounded border-slate-600"
                      checked={selectedWindows.includes(days)}
                      onChange={() => toggleWindow(days)}
                      disabled={running}
                    />
                    {days}天
                  </label>
                ))}
                <button type="button" className="btn-secondary text-xs" onClick={() => setSelectedWindows([60])} disabled={running}>
                  快速
                </button>
                <button type="button" className="btn-secondary text-xs" onClick={() => setSelectedWindows([60, 120, 180])} disabled={running}>
                  完整
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="hidden text-xs text-slate-500 md:inline">
                  {activeStrategy?.label || "-"} · {activeResearchDimension?.label || "-"} · {activeGenerator?.label || "-"} · {selectedWindowsLabel || "-"}天
                </span>
                <button type="button" className="btn-secondary" onClick={() => void load()} disabled={loading || running}>
                  {loading ? "刷新中..." : "刷新"}
                </button>
                <button type="button" className="btn-secondary" onClick={() => void downloadMissingKlineCache()} disabled={loading || running || cacheLoading}>
                  {cacheLoading ? "下载中..." : "补K线"}
                </button>
                <button type="button" className="btn-primary" onClick={() => void createRun()} disabled={running}>
                  {running ? "验证中..." : "生成候选并验证"}
                </button>
              </div>
            </div>
          </div>
        </section>

        {error ? <div className="rounded-lg border border-rose-400/35 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">{error}</div> : null}
        {message ? <div className="rounded-lg border border-emerald-400/35 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-100">{message}</div> : null}

        {cacheResults.length ? (
          <div className="rounded-lg border border-cyan-400/25 bg-cyan-500/10 px-4 py-3 text-xs text-cyan-100">
            <div className="font-semibold">服务器 K 线缓存：{cacheSymbol} · {cacheKline}</div>
            <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-3">
              {cacheResults.map((row) => (
                <div key={String(row.days)} className="rounded-md border border-cyan-300/20 bg-slate-950/35 p-2">
                  <div>
                    {row.days} 天 · {row.cached ? "已有缓存" : "已下载"}
                  </div>
                  <div className="mt-1 text-cyan-100/75">{fmt(row.bar_count, 0)} 根</div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <section className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          <div className="panel">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="section-title">运行状态</div>
                <p className="mt-1 text-xs text-slate-500">异步任务进度和最近一次 Lab 结果。</p>
              </div>
              <span className={`rounded-full border px-2 py-1 text-xs ${toneClass(task?.status || latestRun?.status || "pending")}`}>
                {task?.status || latestRun?.status || "未运行"}
              </span>
            </div>
            <div className="mt-4">
              <div className="flex items-end justify-between gap-3">
                <div>
                  <div className="text-2xl font-semibold text-slate-100">{fmt(progressPct, 0)}%</div>
                  <div className="mt-1 text-xs text-slate-500">{task?.progress_stage || task?.progress_text || latestRun?.run_id || "等待创建任务"}</div>
                </div>
                <div className="text-right text-xs text-slate-500">
                  <div>通过 {passedCount}</div>
                  <div>阻断 {blockedCount}</div>
                </div>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-950/70">
                <div className="h-full rounded-full bg-cyan-300 transition-all" style={{ width: `${progressPct}%` }} />
              </div>
              {task?.task_id ? <div className="mt-3 break-all font-mono text-xs text-slate-500">{task.task_id}</div> : null}
            </div>
          </div>

          <div className="panel">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="section-title">数据质量</div>
                <p className="mt-1 text-xs text-slate-500">日志、ledger、推荐快照与配置完整性。</p>
              </div>
              <span className={`rounded-full border px-2 py-1 text-xs ${toneClass(dataQuality?.ok ? "ok" : "warn")}`}>
                {dataQuality?.ok ? "可进入研究" : "需检查"}
              </span>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2">
              {summaryCards.map((item) => (
                <div key={item.label} className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
                  <div className="text-xs text-slate-500">{item.label}</div>
                  <div className="mt-1 text-lg font-semibold text-slate-100">{item.value}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="section-title">审批状态</div>
                <p className="mt-1 text-xs text-slate-500">只写入草稿，不启动 worker。</p>
              </div>
              <span className={`rounded-full border px-2 py-1 text-xs ${toneClass(latestApproval ? "ok" : "pending")}`}>
                {latestApproval ? "已有审批" : "待审批"}
              </span>
            </div>
            {latestApproval ? (
              <div className="mt-4 space-y-3">
                <div className="rounded-lg border border-emerald-400/25 bg-emerald-500/10 p-3">
                  <div className="text-xs text-emerald-100/70">最近候选</div>
                  <div className="mt-1 break-all font-mono text-sm text-emerald-100">{latestApproval.candidate_id || "-"}</div>
                  <div className="mt-1 text-xs text-emerald-100/70">{latestApproval.diff?.length || 0} 个字段变化</div>
                </div>
                <button
                  type="button"
                  className="w-full rounded-xl border border-amber-300/40 bg-amber-300/10 px-3 py-2 text-xs font-semibold text-amber-100 disabled:opacity-50"
                  disabled={rollbackLoading}
                  onClick={() => void rollbackLastApproval()}
                >
                  {rollbackLoading ? "回滚中..." : "回滚上一版"}
                </button>
              </div>
            ) : (
              <div className="mt-4 rounded-lg border border-slate-700/70 bg-slate-950/35 p-3 text-sm text-slate-400">
                先选择一个验证通过的候选，再查看 diff 并确认写入。
              </div>
            )}
          </div>
        </section>

        <section className="panel">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="section-title">安全管线</div>
              <p className="mt-1 text-xs text-slate-500">前四步在 Lab 内完成；worker 与券商 API 始终保持 not_touched。</p>
            </div>
            <span className="tag-muted">
              {instance === "1dte" ? "QQQ 1DTE" : "QQQ 0DTE"} · {activeStrategy?.label || "-"} · {activeResearchDimension?.label || "-"} · {activeGenerator?.label || "-"}
            </span>
          </div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-6">
            {PIPELINE.map((label, idx) => {
              const statusText =
                label === "智能体研究层"
                  ? pipelineStatus(latestRun, "智能体研究层")
                  : label === "回测与验证层"
                    ? pipelineStatus(latestRun, "确定性回测层")
                    : label === "人工确认 / 自动审批闸门"
                      ? pipelineStatus(latestRun, "人工确认 / 自动审批闸门")
                      : label === "QQQ 实盘 worker"
                        ? pipelineStatus(latestRun, "QQQ 实盘 worker")
                        : label === "券商 API 下单"
                          ? "not_touched"
                          : approvedCandidateId
                            ? "draft_written"
                            : "pending";
              return (
                <div key={label} className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
                  <div className="text-[11px] text-slate-500">Step {idx + 1}</div>
                  <div className="mt-1 min-h-10 text-sm font-semibold text-slate-100">{label}</div>
                  <div className={`mt-3 inline-flex rounded-full border px-2 py-0.5 text-[11px] ${toneClass(statusText)}`}>{statusText}</div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="grid grid-cols-1 gap-4 xl:grid-cols-[0.95fr_1.05fr]">
          <div className="panel">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <div className="section-title">数据质量明细</div>
                <p className="mt-1 text-xs text-slate-500">只显示真正需要你判断的输入健康状况。</p>
              </div>
              <span className="tag-muted">{checks.length} checks</span>
            </div>
            <div className="max-h-[24rem] space-y-2 overflow-auto pr-1">
              {checks.length ? (
                checks.map((check) => (
                  <div key={check.id || check.title} className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-semibold text-slate-100">{check.title || check.id}</div>
                      <span className={`rounded-full border px-2 py-0.5 text-[11px] ${toneClass(check.severity)}`}>{check.severity || "info"}</span>
                    </div>
                    <div className="mt-1 text-xs leading-5 text-slate-400">{check.detail || "-"}</div>
                  </div>
                ))
              ) : (
                <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3 text-sm text-slate-400">暂无数据质量结果。</div>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <div className="section-title">运行与审批记录</div>
                <p className="mt-1 text-xs text-slate-500">用下拉选择历史记录，下面显示中文摘要。</p>
              </div>
              <span className="tag-muted">{runs.length} 次运行 / {approvalHistory.length} 次审批</span>
            </div>

            <select className="input-base" value={selectedRecord} onChange={(event) => selectRecord(event.target.value)} aria-label="选择运行或审批记录">
              <option value="latest">最近运行：{runSummary(latestRun)}</option>
              {runs.map((item) => (
                <option key={`run:${item.run_id}`} value={`run:${item.run_id}`}>
                  运行：{runSummary(item)}
                </option>
              ))}
              {approvalHistory.map((item) => (
                <option key={`approval:${item.approval_id}`} value={`approval:${item.approval_id}`}>
                  审批：{approvalSummary(item)}
                </option>
              ))}
            </select>

            <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
              <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
                <div className="text-xs font-semibold text-slate-400">运行摘要</div>
                <div className="mt-2 text-sm font-semibold text-slate-100">{runSummary(latestRun)}</div>
                <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
                  <div>
                    <div className="text-slate-500">状态</div>
                    <div className="mt-1 text-slate-200">{statusLabel(latestRun?.status)}</div>
                  </div>
                  <div>
                    <div className="text-slate-500">候选</div>
                    <div className="mt-1 text-slate-200">{candidates.length || 0}</div>
                  </div>
                  <div>
                    <div className="text-slate-500">通过</div>
                    <div className="mt-1 text-slate-200">{passedCount}</div>
                  </div>
                </div>
                <div className="mt-2 break-all font-mono text-[11px] text-slate-500">{latestRun?.run_id || "暂无 run_id"}</div>
              </div>

              <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
                <div className="text-xs font-semibold text-slate-400">审批摘要</div>
                <div className="mt-2 text-sm font-semibold text-slate-100">{approvalSummary(selectedApproval)}</div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                  <div>
                    <div className="text-slate-500">字段变化</div>
                    <div className="mt-1 text-slate-200">{selectedApproval?.diff?.length || 0}</div>
                  </div>
                  <div>
                    <div className="text-slate-500">写入目标</div>
                    <div className="mt-1 truncate text-slate-200">{selectedApproval?.live_config_path ? "live_worker_config" : "-"}</div>
                  </div>
                </div>
                <div className="mt-2 break-all font-mono text-[11px] text-slate-500">{selectedApproval?.approval_id || "暂无 approval_id"}</div>
              </div>
            </div>
          </div>
        </section>

      <section className="panel">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="section-title">候选参数与验证结果</div>
            <p className="mt-1 text-xs text-slate-500">审批只写入配置草稿；L3 下单权限、confirmation token 和 worker 风控继续由实盘模块控制。</p>
          </div>
          <span className="tag-muted">{candidates.length} candidates</span>
        </div>

        {diffPreview ? (
          <div className="mb-4 rounded-lg border border-cyan-400/30 bg-cyan-500/10 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-cyan-100">审批前差异对比</div>
                <div className="mt-1 text-xs text-cyan-100/75">
                  仅展示候选实际改动字段 · {diffPreview.candidate_id || "-"} · {diffPreview.live_config_path || "live_worker_config.json"}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn-primary text-xs"
                  disabled={Boolean(approvingId)}
                  onClick={() => void approve(String(diffPreview.candidate_id || ""), Boolean(diffPreview.force))}
                >
                  {approvingId ? "写入中..." : diffPreview.force ? "确认强制写入" : "确认写入草稿"}
                </button>
                <button type="button" className="btn-secondary text-xs" disabled={Boolean(approvingId)} onClick={() => setDiffPreview(null)}>
                  取消
                </button>
              </div>
            </div>
            <div className="mt-3 rounded-lg border border-cyan-300/20 bg-slate-950/40 p-3">
              <div className="text-xs font-semibold text-cyan-100">审批前风险摘要</div>
              <ul className="mt-2 space-y-1 text-xs leading-5 text-cyan-100/80">
                {riskSummary(diffCandidate, diffPreview.diff).map((line, idx) => (
                  <li key={`diff-risk-${idx}`}>{line}</li>
                ))}
              </ul>
            </div>
            <div className="mt-3 table-shell rounded-lg">
              <table className="min-w-full text-left text-xs">
                <thead className="table-head">
                  <tr>
                    <th className="px-2 py-1.5">字段</th>
                    <th className="px-2 py-1.5">含义</th>
                    <th className="px-2 py-1.5">当前</th>
                    <th className="px-2 py-1.5">写入后</th>
                  </tr>
                </thead>
                <tbody>
                  {diffPreview.diff?.length ? (
                    diffPreview.diff.map((row) => (
                      <tr key={row.field || `${row.before}-${row.after}`} className="border-t border-slate-800">
                        <td className="px-2 py-1.5 font-mono text-cyan-100">{row.field || "-"}</td>
                        <td className="px-2 py-1.5 text-slate-300">{fieldLabel(row.field)}</td>
                        <td className="px-2 py-1.5 font-mono text-slate-300">{inlineValue(row.before)}</td>
                        <td className="px-2 py-1.5 font-mono text-slate-100">{inlineValue(row.after)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td className="px-2 py-3 text-slate-400" colSpan={4}>
                        没有检测到候选 patch 字段变化。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        {candidates.length ? (
          <div className="mb-4 rounded-lg border border-slate-700/70 bg-slate-950/35 p-3">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-semibold text-slate-100">候选横向对比</div>
                <div className="mt-1 text-xs text-slate-500">先比较步长、止盈止损和 60/120/180 天结果，再进入审批 diff。</div>
              </div>
              <span className="tag-muted">不会触发下单</span>
            </div>
            <div className="table-shell max-h-[520px] overflow-auto rounded-lg">
              <table className="min-w-[1320px] text-left text-xs">
                <thead className="table-head sticky top-0 z-10">
                  <tr>
                    <th className="px-2 py-1.5">候选</th>
                    <th className="px-2 py-1.5">状态</th>
                    <th className="px-2 py-1.5">Call/Put 步长</th>
                    <th className="px-2 py-1.5">组合 TP / SL</th>
                    <th className="px-2 py-1.5">单腿 TP</th>
                    <th className="px-2 py-1.5">单腿 SL</th>
                    <th className="px-2 py-1.5">60天</th>
                    <th className="px-2 py-1.5">120天</th>
                    <th className="px-2 py-1.5">180天</th>
                    <th className="px-2 py-1.5">回撤/连亏</th>
                    <th className="px-2 py-1.5">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.map((candidate) => {
                    const cid = String(candidate.candidate_id || "");
                    const passed = Boolean(candidate.validation?.passed);
                    const summary = candidate.validation?.summary || {};
                    const approved = approvedCandidateId === cid;
                    return (
                      <tr key={`${cid}-compare`} className="border-t border-slate-800">
                        <td className="px-2 py-2 align-top">
                          <div className="font-semibold text-slate-100">{candidate.title || cid}</div>
                          <div className="mt-1 font-mono text-[10px] text-slate-500">{cid}</div>
                        </td>
                        <td className="px-2 py-2 align-top">
                          <span className={`rounded-full border px-2 py-0.5 ${toneClass(passed ? "passed" : "blocked")}`}>
                            {passed ? "通过" : "未通过"}
                          </span>
                          {approved ? <div className="mt-1 text-emerald-200">已审批</div> : null}
                          <div className="mt-1 text-slate-500">{actionLabel(candidate.agent_action)}</div>
                        </td>
                        <td className="px-2 py-2 align-top font-mono text-slate-300">
                          C {inlineValue(candidateValue(candidate, "call_strikes_otm"))} / P {inlineValue(candidateValue(candidate, "put_strikes_otm"))}
                        </td>
                        <td className="px-2 py-2 align-top text-slate-300">
                          <div>TP {ratioPct(candidateValue(candidate, "strangle_take_profit_return"))}</div>
                          <div className="text-slate-500">SL {lossThreshold(candidateValue(candidate, "strangle_stop_loss_return"))}</div>
                        </td>
                        <td className="px-2 py-2 align-top text-slate-300">
                          <div>长 {ratioPct(candidateValue(candidate, "strangle_long_leg_take_profit_pct"))}</div>
                          <div className="text-slate-500">短 {ratioPct(candidateValue(candidate, "strangle_short_leg_take_profit_pct"))}</div>
                        </td>
                        <td className="px-2 py-2 align-top text-slate-300">
                          <div>{lossThreshold(candidateValue(candidate, "strangle_leg_stop_loss_pct"))}</div>
                          <div className="text-slate-500">冷却 {inlineValue(candidateValue(candidate, "strangle_stop_loss_cooldown_minutes"))}m</div>
                        </td>
                        {[60, 120, 180].map((days) => (
                          <td key={`${cid}-${days}`} className="px-2 py-2 align-top text-slate-300">
                            <div>{validationMetricText(candidate, days, "return_pct", "%")}</div>
                            <div className="text-slate-500">胜 {validationMetricText(candidate, days, "win_rate_pct", "%")}</div>
                          </td>
                        ))}
                        <td className="px-2 py-2 align-top text-slate-300">
                          <div>{fmt(summary.worst_drawdown_usd)}</div>
                          <div className="text-slate-500">连亏 {fmt(summary.max_consecutive_losses, 0)}</div>
                        </td>
                        <td className="px-2 py-2 align-top">
                          <button
                            type="button"
                            className="btn-secondary whitespace-nowrap text-xs"
                            disabled={!passed || Boolean(approvingId) || Boolean(diffLoadingId)}
                            onClick={() => void previewDiff(cid, false)}
                          >
                            看摘要与 diff
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        <div className="grid grid-cols-1 gap-4">
          {candidates.length ? (
            candidates.map((candidate) => {
              const validation = candidate.validation || {};
              const passed = Boolean(validation.passed);
              const cid = String(candidate.candidate_id || "");
              const approved = approvedCandidateId === cid;
              const summary = validation.summary || {};
              return (
                <div key={cid} className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-base font-semibold text-slate-100">{candidate.title || cid}</h2>
                        <span className={`rounded-full border px-2 py-0.5 text-xs ${toneClass(passed ? "passed" : "blocked")}`}>
                          {passed ? "验证通过" : "验证未通过"}
                        </span>
                        <span className="rounded-full border border-slate-600 bg-slate-800/70 px-2 py-0.5 text-xs text-slate-300">
                          {actionLabel(candidate.agent_action)}
                        </span>
                        {candidate.generator ? (
                          <span className="rounded-full border border-cyan-400/30 bg-cyan-400/10 px-2 py-0.5 text-xs text-cyan-100">
                            {candidate.generator === "tradingagents" ? "TradingAgents" : "规则生成"}
                          </span>
                        ) : null}
                        {approved ? <span className="rounded-full border border-emerald-400/35 bg-emerald-400/10 px-2 py-0.5 text-xs text-emerald-100">已审批</span> : null}
                      </div>
                      <div className="mt-1 text-xs text-slate-500">confidence {fmt(Number(candidate.confidence || 0) * 100, 0)}%</div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        className="btn-secondary text-xs"
                        disabled={!passed || Boolean(approvingId) || Boolean(diffLoadingId)}
                        onClick={() => void previewDiff(cid, false)}
                      >
                        {diffLoadingId === cid ? "读取差异..." : "审批写入草稿"}
                      </button>
                      {!passed ? (
                        <button
                          type="button"
                          className="rounded-xl border border-amber-300/40 bg-amber-300/10 px-3 py-2 text-xs font-semibold text-amber-100 disabled:opacity-50"
                          disabled={Boolean(approvingId) || Boolean(diffLoadingId)}
                          onClick={() => void previewDiff(cid, true)}
                        >
                          强制写入
                        </button>
                      ) : null}
                    </div>
                  </div>

                  <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="text-xs text-slate-500">平均收益</div>
                      <div className="mt-1 text-lg font-semibold text-slate-100">{fmt(summary.avg_return_pct)}%</div>
                    </div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="text-xs text-slate-500">最少平仓</div>
                      <div className="mt-1 text-lg font-semibold text-slate-100">{fmt(summary.min_closed_trades, 0)}</div>
                    </div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="text-xs text-slate-500">最大回撤</div>
                      <div className="mt-1 text-lg font-semibold text-slate-100">{fmt(summary.worst_drawdown_usd)}</div>
                    </div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="text-xs text-slate-500">最长连亏</div>
                      <div className="mt-1 text-lg font-semibold text-slate-100">{fmt(summary.max_consecutive_losses, 0)}</div>
                    </div>
                  </div>

                  {validation.blockers?.length ? (
                    <div className="mt-3 rounded-md border border-rose-400/25 bg-rose-500/10 p-2 text-xs text-rose-100">
                      {validation.blockers.join(" · ")}
                    </div>
                  ) : null}

                  <div className="mt-3 rounded-md border border-slate-700/70 bg-slate-950/40 p-3">
                    <div className="text-xs font-semibold text-slate-300">参数影响摘要</div>
                    <ul className="mt-2 space-y-1 text-xs leading-5 text-slate-400">
                      {riskSummary(candidate).slice(0, 5).map((line, idx) => (
                        <li key={`${cid}-risk-${idx}`}>{line}</li>
                      ))}
                    </ul>
                  </div>

                  <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="text-xs font-semibold text-slate-300">研究解释</div>
                      <ul className="mt-2 space-y-1 text-xs leading-5 text-slate-400">
                        {(candidate.reasoning || []).slice(0, 5).map((line, idx) => (
                          <li key={`${cid}-reason-${idx}`}>{line}</li>
                        ))}
                      </ul>
                    </div>

                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="text-xs font-semibold text-slate-300">风控控制</div>
                      <pre className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap text-xs leading-5 text-slate-400">
                        {shortJson(candidate.research_controls)}
                      </pre>
                    </div>
                  </div>

                  <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="mb-2 text-xs font-semibold text-slate-300">回测窗口</div>
                      <div className="table-shell rounded-lg">
                        <table className="min-w-full text-left text-xs">
                          <thead className="table-head">
                            <tr>
                              <th className="px-2 py-1.5">Days</th>
                              <th className="px-2 py-1.5">PnL</th>
                              <th className="px-2 py-1.5">Return</th>
                              <th className="px-2 py-1.5">Win</th>
                              <th className="px-2 py-1.5">Closed</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(validation.rows || []).map((row) => (
                              <tr key={`${cid}-${row.days}`} className="border-t border-slate-800">
                                <td className="px-2 py-1.5">{row.days}</td>
                                <td className="px-2 py-1.5">{row.ok ? fmt(row.metrics?.realized_pnl) : row.error}</td>
                                <td className="px-2 py-1.5">{row.ok ? `${fmt(row.metrics?.return_pct)}%` : "-"}</td>
                                <td className="px-2 py-1.5">{row.ok ? `${fmt(row.metrics?.win_rate_pct)}%` : "-"}</td>
                                <td className="px-2 py-1.5">{row.ok ? fmt(row.metrics?.closed_trades, 0) : "-"}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    <div className="rounded-lg border border-slate-800 bg-slate-950/45 p-3">
                      <div className="mb-2 text-xs font-semibold text-slate-300">写入配置预览</div>
                      <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs leading-5 text-slate-400">
                        {shortJson(candidate.strategy_config_patch)}
                      </pre>
                    </div>
                  </div>
                </div>
              );
            })
          ) : (
            <div className="rounded-lg border border-slate-700/70 bg-slate-950/35 p-4 text-sm text-slate-400">
              还没有候选参数。点击“生成候选并验证”开始一次 Lab 运行。
            </div>
          )}
        </div>
      </section>
      </div>
    </PageShell>
  );
}
