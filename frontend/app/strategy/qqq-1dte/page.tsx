"use client";

import Link from "next/link";
import { PageShell } from "@/components/ui/page-shell";
import { Qqq0dteLiveAutoPanel } from "../qqq-0dte/live-auto-panel";

export default function Qqq1dteStrategyPage() {
  return (
    <PageShell>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-slate-100">股票期权日内交易</h1>
        <p className="mt-1 max-w-4xl text-sm leading-relaxed text-slate-400">
          默认股票池包含 <span className="font-mono text-slate-200">QQQ.US</span>，可扩展为多个美股标的；Worker
          会轮询股票池分时线与期权链，按每个标的独立生成日内开仓和平仓信号。
        </p>
      </div>
      <div className="mb-4 space-y-2 text-sm text-slate-400">
        <p>
          回测、参数矩阵、合约解析与快照仍使用{" "}
          <Link className="text-cyan-400 underline-offset-2 hover:underline" href="/auto-trading/options-0dte">
            QQQ 0DTE 策略页
          </Link>{" "}
          的同一套 API。本页管理 <span className="font-mono text-slate-500">data/qqq_1dte/</span> 下的实盘 Worker 配置与启停。
        </p>
        <p>
          默认按 <span className="font-mono text-slate-300">expiry_offset_days: 1</span> 解析下一可交易到期日合约；也可填写固定{" "}
          <span className="font-mono text-slate-300">expiry_date</span>。股票池模式会为每个标的保存独立恢复快照。
        </p>
      </div>
      <Qqq0dteLiveAutoPanel liveVariant="1dte" pageSymbol="QQQ.US" pageKline="1m" strategyConfig={null} />
    </PageShell>
  );
}
