"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { ADMIN_EDITION_ENABLED } from "@/lib/edition";

const NAV_COLLAPSED_KEY = "lp_console_nav_collapsed";

function Icon({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <svg
      className={clsx("h-5 w-5 shrink-0 opacity-95", className)}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {children}
    </svg>
  );
}

type NavItem = { href: string; label: string; icon: ReactNode; adminOnly?: boolean };

const labels = {
  onboarding: "本地 Agent 向导",
  setup: "\u9996\u6b21\u914d\u7f6e Setup",
  dashboard: "\u603b\u89c8 Dashboard",
  market: "\u5e02\u573a\u5206\u6790",
  signals: "\u4fe1\u53f7\u4e2d\u5fc3",
  backtest: "\u56de\u6d4b\u4e2d\u5fc3",
  autoTrading: "\u81ea\u52a8\u4ea4\u6613",
  research: "\u7814\u7a76\u4e2d\u5fc3",
  tradingAgents: "TradingAgents \u667a\u80fd\u4f53",
  agentStrategyLab: "Agent Strategy Lab",
  trade: "\u4ea4\u6613\u9762\u677f",
  options: "\u671f\u6743\u4ea4\u6613",
  notifications: "\u901a\u77e5\u4e2d\u5fc3",
  billing: "\u8ba2\u8d2d\u4e0e\u5347\u7ea7",
  orderAdmin: "\u6536\u6b3e\u8ba2\u5355",
  licenseAdmin: "License \u53d1\u653e",
  expand: "\u5c55\u5f00\u5bfc\u822a\u6587\u5b57",
  collapse: "\u6536\u8d77\u4ec5\u663e\u793a\u56fe\u6807",
};

const items: NavItem[] = [
  {
    href: "/onboarding",
    label: labels.onboarding,
    icon: (
      <Icon>
        <path d="M12 3v3" />
        <rect x="5" y="7" width="14" height="10" rx="2" />
        <path d="M8 21h8" />
        <path d="M12 17v4" />
        <path d="M9 11h.01M15 11h.01" />
      </Icon>
    ),
  },
  {
    href: "/setup",
    label: labels.setup,
    icon: (
      <Icon>
        <circle cx="12" cy="12" r="3" />
        <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
      </Icon>
    ),
  },
  {
    href: "/dashboard",
    label: labels.dashboard,
    icon: (
      <Icon>
        <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
        <polyline points="9 22 9 12 15 12 15 22" />
      </Icon>
    ),
  },
  {
    href: "/market",
    label: labels.market,
    icon: (
      <Icon>
        <path d="M3 3v18h18" />
        <path d="M7 16l4-4 4 4 6-8" />
      </Icon>
    ),
  },
  {
    href: "/signals",
    label: labels.signals,
    icon: (
      <Icon>
        <path d="M22 12h-4l-3 9L9 3 6 12H2" />
      </Icon>
    ),
  },
  {
    href: "/backtest",
    label: labels.backtest,
    icon: (
      <Icon>
        <path d="M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" />
        <path d="M12 6v6l4 2" />
      </Icon>
    ),
  },
  {
    href: "/auto-trading",
    label: labels.autoTrading,
    icon: (
      <Icon>
        <rect x="4" y="8" width="16" height="10" rx="2" />
        <path d="M9 8V6a3 3 0 0 1 6 0v2" />
        <circle cx="9" cy="14" r="1" fill="currentColor" stroke="none" />
        <circle cx="15" cy="14" r="1" fill="currentColor" stroke="none" />
      </Icon>
    ),
  },
  {
    href: "/research",
    label: labels.research,
    icon: (
      <Icon>
        <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
        <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
      </Icon>
    ),
  },
  {
    href: "/tradingagents",
    label: labels.tradingAgents,
    icon: (
      <Icon>
        <path d="M12 3v3" />
        <rect x="5" y="7" width="14" height="11" rx="3" />
        <circle cx="10" cy="12" r="1" fill="currentColor" stroke="none" />
        <circle cx="14" cy="12" r="1" fill="currentColor" stroke="none" />
        <path d="M9 15h6" />
        <path d="M3 10h2M19 10h2" />
      </Icon>
    ),
  },
  {
    href: "/agent-strategy-lab",
    label: labels.agentStrategyLab,
    icon: (
      <Icon>
        <path d="M4 19V5" />
        <path d="M4 19h16" />
        <path d="M8 15l3-3 3 2 5-7" />
        <circle cx="8" cy="15" r="1" fill="currentColor" stroke="none" />
        <circle cx="11" cy="12" r="1" fill="currentColor" stroke="none" />
        <circle cx="14" cy="14" r="1" fill="currentColor" stroke="none" />
      </Icon>
    ),
  },
  {
    href: "/trade",
    label: labels.trade,
    icon: (
      <Icon>
        <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
      </Icon>
    ),
  },
  {
    href: "/options",
    label: labels.options,
    icon: (
      <Icon>
        <path d="M12 3v18" />
        <path d="M5 9h14M5 15h8" />
      </Icon>
    ),
  },
  {
    href: "/notifications",
    label: labels.notifications,
    icon: (
      <Icon>
        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
        <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      </Icon>
    ),
  },
  {
    href: "/billing",
    label: labels.billing,
    icon: (
      <Icon>
        <path d="M4 7h16v10H4z" />
        <path d="M7 11h4" />
        <path d="M15 14h2" />
        <path d="M4 9h16" />
      </Icon>
    ),
  },
  {
    href: "/admin/orders",
    label: labels.orderAdmin,
    adminOnly: true,
    icon: (
      <Icon>
        <path d="M6 3h12v18H6z" />
        <path d="M9 7h6M9 11h6M9 15h3" />
      </Icon>
    ),
  },
  {
    href: "/admin/licenses",
    label: labels.licenseAdmin,
    adminOnly: true,
    icon: (
      <Icon>
        <rect x="4" y="4" width="16" height="16" rx="2" />
        <path d="M8 9h8M8 13h5" />
        <path d="M15.5 16.5l1.5 1.5 3-3" />
      </Icon>
    ),
  },
];

function isActivePath(pathname: string | null, href: string) {
  if (pathname === href) return true;
  return href === "/auto-trading" && Boolean(pathname?.startsWith("/auto-trading/"));
}

export function Nav() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const visibleItems = ADMIN_EDITION_ENABLED ? items : items.filter((item) => !item.adminOnly);

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(NAV_COLLAPSED_KEY) === "1");
    } catch {
      /* ignore */
    }
  }, []);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(NAV_COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  return (
    <aside
      className={clsx(
        "sticky top-4 h-fit shrink-0 overflow-hidden rounded-2xl border border-slate-700/60 bg-slate-900/90 shadow-[0_18px_40px_rgba(2,6,23,0.45)] backdrop-blur-sm transition-[width,padding] duration-300 ease-out",
        collapsed ? "w-[4.25rem] p-2" : "w-64 p-4"
      )}
    >
      <div
        className={clsx(
          "mb-3 flex items-center gap-2",
          collapsed ? "flex-col px-0" : "justify-between px-2"
        )}
      >
        <div
          className={clsx(
            "min-w-0 font-bold tracking-tight text-slate-100 transition-opacity duration-200",
            collapsed ? "text-center text-xs" : "text-base"
          )}
        >
          {collapsed ? (
            <>
              <span className="block" aria-hidden>
                MT
              </span>
              <span className="sr-only">MultiTrading</span>
            </>
          ) : (
            <>MultiTrading</>
          )}
        </div>
        <button
          type="button"
          onClick={toggleCollapsed}
          title={collapsed ? labels.expand : labels.collapse}
          aria-expanded={!collapsed}
          aria-label={collapsed ? labels.expand : labels.collapse}
          className={clsx(
            "rounded-lg border border-slate-600/80 bg-slate-800/60 p-2 text-slate-300 transition hover:border-cyan-500/40 hover:bg-slate-800 hover:text-cyan-200",
            collapsed ? "w-full" : "shrink-0"
          )}
        >
          <Icon className="mx-auto h-4 w-4">
            {collapsed ? <path d="M15 18l-6-6 6-6" /> : <path d="M9 18l6-6-6-6" />}
          </Icon>
        </button>
      </div>

      <nav className="space-y-1">
        {visibleItems.map((item) => {
          const active = isActivePath(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              title={item.label}
              className={clsx(
                "flex items-center gap-3 rounded-lg text-sm transition-all duration-200",
                collapsed ? "justify-center px-2 py-2.5" : "px-3 py-2.5",
                active
                  ? "bg-gradient-to-r from-cyan-500 to-indigo-500 text-white shadow-md shadow-cyan-500/30"
                  : "text-slate-300 hover:bg-slate-800/80 hover:text-slate-100"
              )}
            >
              {item.icon}
              <span className={clsx("min-w-0 flex-1 truncate", collapsed && "sr-only")}>{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
