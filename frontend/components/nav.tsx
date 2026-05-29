"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";

const NAV_COLLAPSED_KEY = "lp_console_nav_collapsed";
const IS_CUSTOMER_BUILD = process.env.NEXT_PUBLIC_MT_BUILD_TARGET === "customer";

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

type NavItem = { href: string; label: string; icon: ReactNode };

const labels = {
  onboarding: "\u672c\u5730 Agent \u5411\u5bfc",
  setup: "\u9996\u6b21\u914d\u7f6e Setup",
  dashboard: "\u603b\u89c8 Dashboard",
  market: "\u5e02\u573a\u5206\u6790",
  news: "\u65b0\u95fb\u4fe1\u606f\u6d41",
  signals: "\u4fe1\u53f7\u4e2d\u5fc3",
  backtest: "\u56de\u6d4b\u4e2d\u5fc3",
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
        <path d="M5 20V4" />
        <path d="M5 5h10l-1.5 3L15 11H5" />
        <path d="M8 17h7" />
        <path d="M15 17l4-4-4-4" />
        <path d="M19 13H9" />
      </Icon>
    ),
  },
  {
    href: "/setup",
    label: labels.setup,
    icon: (
      <Icon>
        <path d="M4 6h9" />
        <path d="M17 6h3" />
        <circle cx="15" cy="6" r="2" />
        <path d="M4 12h3" />
        <path d="M11 12h9" />
        <circle cx="9" cy="12" r="2" />
        <path d="M4 18h11" />
        <path d="M19 18h1" />
        <circle cx="17" cy="18" r="2" />
      </Icon>
    ),
  },
  {
    href: "/dashboard",
    label: labels.dashboard,
    icon: (
      <Icon>
        <rect x="3" y="3" width="7" height="8" rx="1.5" />
        <rect x="14" y="3" width="7" height="5" rx="1.5" />
        <rect x="14" y="12" width="7" height="9" rx="1.5" />
        <rect x="3" y="15" width="7" height="6" rx="1.5" />
      </Icon>
    ),
  },
  {
    href: "/market",
    label: labels.market,
    icon: (
      <Icon>
        <path d="M4 20V4" />
        <path d="M4 20h16" />
        <path d="M8 17V8" />
        <path d="M7 12h2" />
        <path d="M13 17V6" />
        <path d="M12 10h2" />
        <path d="M18 17v-7" />
        <path d="M17 14h2" />
      </Icon>
    ),
  },
  {
    href: "/news",
    label: labels.news,
    icon: (
      <Icon>
        <path d="M4 5h12v14H4z" />
        <path d="M16 8h4v11a2 2 0 0 1-2 2H6" />
        <path d="M7 9h6M7 13h6M7 17h3" />
      </Icon>
    ),
  },
  {
    href: "/signals",
    label: labels.signals,
    icon: (
      <Icon>
        <path d="M12 20V10" />
        <path d="M8 20h8" />
        <circle cx="12" cy="8" r="1.5" fill="currentColor" stroke="none" />
        <path d="M8.5 11.5a5 5 0 0 1 7 0" />
        <path d="M5.5 8.5a9 9 0 0 1 13 0" />
      </Icon>
    ),
  },
  {
    href: "/backtest",
    label: labels.backtest,
    icon: (
      <Icon>
        <path d="M3 12a9 9 0 1 0 3-6.7" />
        <path d="M3 4v5h5" />
        <path d="M12 6v6l4 2" />
      </Icon>
    ),
  },
  {
    href: "/research",
    label: labels.research,
    icon: (
      <Icon>
        <path d="M10 4l4 4" />
        <path d="M9 5 6 8l7 7 3-3-7-7z" />
        <path d="M14 14l4 4" />
        <path d="M5 21h14" />
        <path d="M9 18h6" />
      </Icon>
    ),
  },
  {
    href: "/tradingagents",
    label: labels.tradingAgents,
    icon: (
      <Icon>
        <circle cx="6" cy="8" r="2.5" />
        <circle cx="18" cy="8" r="2.5" />
        <circle cx="12" cy="17" r="2.5" />
        <path d="M8.5 8h7" />
        <path d="M7.5 10l3.2 5" />
        <path d="M16.5 10l-3.2 5" />
      </Icon>
    ),
  },
  {
    href: "/agent-strategy-lab",
    label: labels.agentStrategyLab,
    icon: (
      <Icon>
        <path d="M10 2h4" />
        <path d="M11 2v6l-5 9a3 3 0 0 0 2.6 5h6.8a3 3 0 0 0 2.6-5l-5-9V2" />
        <path d="M8 16h8" />
        <path d="M9 19h6" />
      </Icon>
    ),
  },
  {
    href: "/trade",
    label: labels.trade,
    icon: (
      <Icon>
        <path d="M6 7h12" />
        <path d="M15 4l3 3-3 3" />
        <path d="M18 17H6" />
        <path d="M9 14l-3 3 3 3" />
      </Icon>
    ),
  },
  {
    href: "/options",
    label: labels.options,
    icon: (
      <Icon>
        <path d="M4 19l5-10 3 6 3-6 5 10" />
        <path d="M7 5h4" />
        <path d="M13 5h4" />
        <path d="M9 5v4" />
        <path d="M15 5v4" />
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
    icon: (
      <Icon>
        <path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3z" />
        <path d="M9 7h6" />
        <path d="M9 11h6" />
        <path d="M9 15h4" />
      </Icon>
    ),
  },
  {
    href: "/admin/licenses",
    label: labels.licenseAdmin,
    icon: (
      <Icon>
        <circle cx="7" cy="14" r="3" />
        <path d="M10 14h10" />
        <path d="M16 14v3" />
        <path d="M20 14v-3" />
      </Icon>
    ),
  },
];

function isActivePath(pathname: string | null, href: string) {
  if (pathname === href) return true;
  return false;
}

export function Nav() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

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
            "min-w-0 transition-opacity duration-200",
            collapsed ? "flex justify-center" : "flex items-center"
          )}
        >
          {collapsed ? (
            <>
              <img
                src="/brand/multitrading-mark.svg"
                alt=""
                aria-hidden
                className="h-10 w-10 shrink-0"
              />
              <span className="sr-only">MultiTrading</span>
            </>
          ) : (
            <img
              src="/brand/multitrading-logo.svg"
              alt="MultiTrading"
              className="h-10 w-auto max-w-[11.5rem] shrink-0"
            />
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
        {items.filter((item) => {
          if (!IS_CUSTOMER_BUILD) return true;
          return !["/admin/orders", "/admin/licenses"].includes(item.href);
        }).map((item) => {
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
