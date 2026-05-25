"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";

const tabs = [
  { href: "/auto-trading", label: "\u603b\u89c8" },
  { href: "/auto-trading/stocks", label: "\u80a1\u7968\u81ea\u52a8\u4ea4\u6613" },
  { href: "/auto-trading/options-0dte", label: "\u671f\u6743 0DTE" },
  { href: "/auto-trading/options-1dte", label: "\u671f\u6743 1DTE" },
];

export function AutoTradingTabs() {
  const pathname = usePathname();

  return (
    <div className="mb-4 overflow-x-auto rounded-xl border border-slate-700/70 bg-slate-900/80 p-1">
      <div className="flex min-w-max gap-1">
        {tabs.map((tab) => {
          const active = pathname === tab.href;
          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={clsx(
                "rounded-lg px-4 py-2 text-sm transition",
                active
                  ? "bg-cyan-500 text-white shadow-sm shadow-cyan-500/30"
                  : "text-slate-300 hover:bg-slate-800 hover:text-slate-100"
              )}
            >
              {tab.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
