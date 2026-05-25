"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { localAgentGet as apiGet, localAgentPost as apiPost } from "@/lib/local-agent-api";
import { PageShell } from "@/components/ui/page-shell";
import { buildSwrOptions, SWR_INTERVALS } from "@/lib/swr-config";
import { AutoTradingTabs } from "./auto-trading-tabs";
import { EntitlementNotice } from "@/components/entitlement-guard";
import { useEntitlements } from "@/lib/use-entitlements";

type RuntimeSummary = {
  updated_at?: string;
  last_error?: string;
  worker_running?: boolean;
  runtime?: RuntimeSummary;
};

type AutoTradingModuleId = "stocks" | "options-0dte" | "options-1dte" | "options-swing";

type AutoTradingModuleStatus = {
  id: AutoTradingModuleId | string;
  kind?: string;
  label?: string;
  running?: boolean;
  pid?: number | null;
  runtime?: RuntimeSummary | null;
  last_error?: string | null;
  updated_at?: string | null;
  pending_signals?: number | null;
  executed_signals?: number | null;
};

type AutoTradingStatus = {
  ok?: boolean;
  any_running?: boolean;
  running_count?: number;
  modules?: AutoTradingModuleStatus[];
};

type AutoTradingRiskSummary = {
  ok?: boolean;
  risk?: {
    auto_execute?: boolean;
    dry_run_mode?: boolean;
    max_daily_trades?: number;
    max_total_exposure?: number;
    dry_run?: boolean;
  };
};

const text = {
  title: "\u81ea\u52a8\u4ea4\u6613",
  subtitle: "\u80a1\u7968\u81ea\u52a8\u4ea4\u6613\u3001QQQ 0DTE\u3001\u80a1\u7968\u671f\u6743\u65e5\u5185/\u4e2d\u957f\u7ebf\u7684\u7edf\u4e00\u5165\u53e3\u548c\u8fd0\u884c\u603b\u89c8\u3002",
  running: "\u8fd0\u884c\u4e2d",
  stopped: "\u672a\u8fd0\u884c",
  anyRunning: "\u6709\u6a21\u5757\u8fd0\u884c\u4e2d",
  allStopped: "\u5168\u90e8\u672a\u8fd0\u884c",
  dryRun: "\u6f14\u7ec3\u6a21\u5f0f",
  liveConfig: "\u5b9e\u76d8\u914d\u7f6e",
  noRuntime: "\u6682\u65e0\u8fd0\u884c\u6458\u8981",
  updated: "\u66f4\u65b0",
  error: "\u9519\u8bef",
  moduleEntry: "\u6a21\u5757\u5165\u53e3",
  enterChild: "\u8fdb\u5165\u4e8c\u7ea7\u9875\u9762",
  riskTitle: "\u7edf\u4e00\u98ce\u63a7\u63d0\u9192",
  stockAutoExecute: "\u80a1\u7968\u81ea\u52a8\u6267\u884c",
  enabled: "\u5df2\u542f\u7528",
  semiAuto: "\u534a\u81ea\u52a8/\u672a\u542f\u7528",
  start: "\u542f\u52a8",
  stop: "\u505c\u6b62",
  startAll: "\u5168\u90e8\u542f\u52a8",
  stopAll: "\u5168\u90e8\u505c\u6b62",
  processing: "\u5904\u7406\u4e2d...",
  actionOk: "\u64cd\u4f5c\u5df2\u53d1\u9001",
  noAction: "\u6ca1\u6709\u9700\u8981\u5904\u7406\u7684\u6a21\u5757",
  actionFailed: "\u64cd\u4f5c\u5931\u8d25",
  maxDailyTrades: "\u5355\u65e5\u6700\u5927\u4ea4\u6613\u6570",
  maxExposure: "\u6700\u5927\u603b\u655e\u53e3",
};

type ModuleCard = {
  id: AutoTradingModuleId;
  title: string;
  href: string;
  description: string;
};

const modules: ModuleCard[] = [
  {
    id: "stocks",
    title: "\u80a1\u7968\u81ea\u52a8\u4ea4\u6613",
    href: "/auto-trading/stocks",
    description: "\u5f3a\u52bf\u80a1\u626b\u63cf\u3001\u7b56\u7565\u8bc4\u5206\u3001\u534a\u81ea\u52a8\u786e\u8ba4\u6216\u5168\u81ea\u52a8\u6267\u884c\u3002",
  },
  {
    id: "options-0dte",
    title: "\u671f\u6743 0DTE",
    href: "/auto-trading/options-0dte",
    description: "QQQ 0DTE \u5b9e\u76d8 Worker\u3001\u56de\u6d4b\u77e9\u9635\u3001\u5408\u7ea6\u89e3\u6790\u4e0e\u98ce\u9669\u53c2\u6570\u3002",
  },
  {
    id: "options-1dte",
    title: "\u80a1\u7968\u671f\u6743\u65e5\u5185\u4ea4\u6613",
    href: "/auto-trading/options-1dte",
    description: "\u8f6e\u8be2\u80a1\u7968\u6c60\u5206\u65f6\u7ebf\u4e0e\u671f\u6743\u94fe\uff0c\u6bcf\u4e2a\u6807\u7684\u72ec\u7acb\u4fe1\u53f7\u3001\u4e0b\u5355\u4e0e\u65ad\u8fde\u6062\u590d\u3002",
  },
  {
    id: "options-swing",
    title: "\u80a1\u7968\u671f\u6743\u4e2d\u957f\u7ebf\u4ea4\u6613",
    href: "/auto-trading/options-swing",
    description: "\u65e5\u7ebf\u8d8b\u52bf\u626b\u63cf\u300130-180 DTE \u5408\u7ea6\u7b5b\u9009\u3001\u6258\u7ba1\u4ed3\u4f4d\u751f\u547d\u5468\u671f\u548c\u672a\u6258\u7ba1\u6301\u4ed3\u9694\u79bb\u3002",
  },
];

function statusPill(running: boolean, label?: string) {
  return (
    <span
      className={`inline-flex rounded-full border px-2 py-0.5 text-xs ${
        running
          ? "border-emerald-400/50 bg-emerald-500/10 text-emerald-200"
          : "border-slate-600 bg-slate-800/80 text-slate-300"
      }`}
    >
      {label || (running ? text.running : text.stopped)}
    </span>
  );
}

function controlButtonClass(action: "start" | "stop") {
  const base =
    "inline-flex min-w-20 items-center justify-center rounded-lg border px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50";
  if (action === "stop") {
    return `${base} border-rose-400/50 bg-rose-500/10 text-rose-100 hover:bg-rose-500/20`;
  }
  return `${base} border-emerald-400/50 bg-emerald-500/10 text-emerald-100 hover:bg-emerald-500/20`;
}

function runtimeText(runtime?: RuntimeSummary | null): string {
  if (!runtime || typeof runtime !== "object") return text.noRuntime;
  const inner = runtime.runtime && typeof runtime.runtime === "object" ? runtime.runtime : {};
  const parts = [
    runtime.updated_at || inner.updated_at ? `${text.updated} ${runtime.updated_at || inner.updated_at}` : "",
    runtime.last_error || inner.last_error ? `${text.error} ${runtime.last_error || inner.last_error}` : "",
    runtime.worker_running !== undefined ? `worker=${runtime.worker_running ? "on" : "off"}` : "",
  ].filter(Boolean);
  return parts.join(" / ") || text.noRuntime;
}

export default function AutoTradingHomePage() {
  const [busyAction, setBusyAction] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const entitlements = useEntitlements();

  const { data: unifiedStatus, mutate: mutateStatus } = useSWR<AutoTradingStatus>(
    "/auto-trading/status",
    () => apiGet<AutoTradingStatus>("/auto-trading/status", { timeoutMs: 8000, retries: 0, cacheTtlMs: 0 }),
    buildSwrOptions(SWR_INTERVALS.mediumPoll.refreshInterval, SWR_INTERVALS.mediumPoll.dedupingInterval)
  );
  const { data: stockRiskSummary, mutate: mutateStockRisk } = useSWR<AutoTradingRiskSummary>(
    "/auto-trading/stocks/risk-summary",
    () =>
      apiGet<AutoTradingRiskSummary>("/auto-trading/stocks/risk-summary", {
        timeoutMs: 8000,
        retries: 0,
        cacheTtlMs: 0,
      }),
    buildSwrOptions(SWR_INTERVALS.mediumPoll.refreshInterval, SWR_INTERVALS.mediumPoll.dedupingInterval)
  );

  const moduleStatus = useMemo(() => new Map((unifiedStatus?.modules || []).map((item) => [item.id, item])), [unifiedStatus]);
  const anyRunning = Boolean(unifiedStatus?.any_running);
  const allRunning = modules.every((module) => Boolean(moduleStatus.get(module.id)?.running));
  const stockRisk = stockRiskSummary?.risk || {};
  const stockDryRun = Boolean(stockRisk.dry_run_mode ?? stockRisk.dry_run);
  const globalAction: "start" | "stop" = anyRunning ? "stop" : "start";
  const hasStartableModule = modules.some((module) => canRunModule(module.id));

  async function refreshAutoTrading() {
    await Promise.all([mutateStatus(), mutateStockRisk()]);
  }

  async function runModuleAction(moduleId: AutoTradingModuleId, action: "start" | "stop") {
    const module = modules.find((item) => item.id === moduleId);
    if (action === "start" && !canRunModule(moduleId)) {
      setActionMessage(`${module?.title || moduleId}: 当前订阅暂不可启动。`);
      return;
    }
    setBusyAction(`${moduleId}:${action}`);
    setActionMessage("");
    try {
      await apiPost(`/auto-trading/${moduleId}/${action}`, { start_feishu_bot: false }, { timeoutMs: 30000, retries: 0 });
      setActionMessage(`${module?.title || moduleId}: ${text.actionOk}`);
      await refreshAutoTrading();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`${module?.title || moduleId}: ${text.actionFailed}: ${msg}`);
    } finally {
      setBusyAction("");
    }
  }

  async function runAllAction(action: "start" | "stop") {
    setBusyAction(`all:${action}`);
    setActionMessage("");
    const targets = modules.filter((module) => {
      const running = Boolean(moduleStatus.get(module.id)?.running);
      if (action === "start" && !canRunModule(module.id)) return false;
      return action === "start" ? !running : running;
    });
    if (targets.length === 0) {
      setActionMessage(text.noAction);
      setBusyAction("");
      return;
    }
    const failures: string[] = [];
    for (const module of targets) {
      try {
        await apiPost(`/auto-trading/${module.id}/${action}`, { start_feishu_bot: false }, { timeoutMs: 30000, retries: 0 });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        failures.push(`${module.title}: ${msg}`);
      }
    }
    await refreshAutoTrading();
    setActionMessage(failures.length ? `${text.actionFailed}: ${failures.join(" / ")}` : text.actionOk);
    setBusyAction("");
  }

  return (
    <PageShell>
      <AutoTradingTabs />

      <div className="panel border-cyan-500/20 bg-slate-900/95">
        <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-white">{text.title}</h1>
            <p className="mt-1 text-sm text-slate-300">{text.subtitle}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {statusPill(anyRunning, anyRunning ? text.anyRunning : text.allStopped)}
            {statusPill(stockDryRun, stockDryRun ? text.dryRun : text.liveConfig)}
            <button
              type="button"
              disabled={Boolean(busyAction) || (globalAction === "start" && !hasStartableModule)}
              onClick={() => runAllAction(globalAction)}
              className={controlButtonClass(globalAction)}
            >
              {busyAction.startsWith("all:") ? text.processing : allRunning || anyRunning ? text.stopAll : text.startAll}
            </button>
          </div>
        </div>
        {actionMessage ? <div className="mt-3 text-sm text-slate-300">{actionMessage}</div> : null}
      </div>

        <div className="grid gap-4 lg:grid-cols-4">
        {modules.map((module) => {
          const status = moduleStatus.get(module.id);
          const running = Boolean(status?.running);
          const locked = !canRunModule(module.id);
          return (
            <div className="panel" key={module.id}>
              <div className="text-sm text-slate-400">{status?.label || module.title}</div>
              <div className="mt-2 flex items-center justify-between gap-2">
                <div className="text-xl font-semibold text-slate-100">{running ? text.running : text.stopped}</div>
                {statusPill(running)}
              </div>
              <div className="mt-2 text-xs text-slate-400">PID: {status?.pid || "-"}</div>
              <div className="mt-2 text-xs text-slate-500">
                {runtimeText({
                  ...(status?.runtime || {}),
                  updated_at: status?.updated_at || status?.runtime?.updated_at,
                  last_error: status?.last_error || status?.runtime?.last_error,
                })}
              </div>
              {locked ? (
                <EntitlementNotice
                  className="mt-3"
                  feature={module.id === "stocks" ? "stock_auto_trading" : "option_auto_trading"}
                  plan={entitlements.plan}
                  title={module.id === "stocks" ? "股票自动交易需要升级" : "期权自动交易需要升级"}
                />
              ) : null}
              <div className="mt-4 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled={Boolean(busyAction) || (!running && locked)}
                  onClick={() => runModuleAction(module.id, running ? "stop" : "start")}
                  className={controlButtonClass(running ? "stop" : "start")}
                >
                  {busyAction === `${module.id}:${running ? "stop" : "start"}`
                    ? text.processing
                    : running
                      ? text.stop
                      : text.start}
                </button>
                <Link
                  href={module.href}
                  className="inline-flex min-w-20 items-center justify-center rounded-lg border border-slate-600 bg-slate-800/70 px-3 py-2 text-sm text-slate-200 transition hover:border-cyan-500/50 hover:text-cyan-100"
                >
                  {text.enterChild}
                </Link>
              </div>
            </div>
          );
        })}
      </div>

      <div className="panel">
        <div className="mb-3 text-sm font-semibold text-slate-100">{text.moduleEntry}</div>
        <div className="grid gap-3 lg:grid-cols-4">
          {modules.map((module) => (
            <Link
              key={module.href}
              href={module.href}
              className="rounded-lg border border-slate-700 bg-slate-900/70 p-4 transition hover:border-cyan-500/50 hover:bg-slate-900"
            >
              <div className="text-base font-semibold text-slate-100">{module.title}</div>
              <div className="mt-2 min-h-12 text-sm leading-6 text-slate-400">{module.description}</div>
              <div className="mt-3 text-xs text-cyan-300">{text.enterChild}</div>
            </Link>
          ))}
        </div>
      </div>

      <div className="panel border-amber-500/30 bg-amber-500/5">
        <div className="text-sm font-semibold text-amber-100">{text.riskTitle}</div>
        <div className="mt-2 grid gap-2 text-sm text-amber-50/80 md:grid-cols-3">
          <div>
            {text.stockAutoExecute}: {stockRisk.auto_execute ? text.enabled : text.semiAuto}
          </div>
          <div>
            {text.maxDailyTrades}: {stockRisk.max_daily_trades ?? "-"}
          </div>
          <div>
            {text.maxExposure}: {stockRisk.max_total_exposure ?? "-"}
          </div>
        </div>
      </div>
    </PageShell>
  );

  function canRunModule(moduleId: AutoTradingModuleId): boolean {
    if (moduleId === "stocks") return entitlements.canUse("stock_auto_trading");
    return entitlements.canUse("option_auto_trading");
  }
}
