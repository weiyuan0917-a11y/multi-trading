"use client";

import Link from "next/link";
import { PageShell } from "@/components/ui/page-shell";
import { Qqq0dteLiveAutoPanel } from "../qqq-0dte/live-auto-panel";

export default function Qqq1dteStrategyPage() {
  return (
    <PageShell>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-slate-100">QQQ 1DTE · 自动</h1>
        <p className="mt-1 text-sm text-slate-500">与 0DTE 页相同策略与下单链路；独立配置文件与 Worker，可与 0DTE 同时运行。</p>
      </div>
      <div className="mb-4 space-y-2 text-sm text-slate-400">
        <p>
          回测、参数矩阵、合约解析与快照仍使用{" "}
          <Link className="text-cyan-400 underline-offset-2 hover:underline" href="/auto-trading/options-0dte">
            QQQ 0DTE 策略页
          </Link>{" "}
          的同一套 API（<span className="font-mono text-slate-500">/strategy/qqq-0dte/…</span>）。本页仅管理{" "}
          <span className="font-mono text-slate-500">data/qqq_1dte/</span> 下的实盘 Worker 配置与启停。
        </p>
        <p>
          默认按配置中的 <span className="font-mono text-slate-300">expiry_offset_days: 1</span> 解析次日到期合约；也可填写固定{" "}
          <span className="font-mono text-slate-300">expiry_date</span> 或调整偏移天数。
        </p>
      </div>
      <Qqq0dteLiveAutoPanel liveVariant="1dte" pageSymbol="QQQ.US" pageKline="1m" strategyConfig={null} />
    </PageShell>
  );
}
