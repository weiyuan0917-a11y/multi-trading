"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import clsx from "clsx";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { localAgentGet as apiGet, localAgentPost as apiPost } from "@/lib/local-agent-api";
import { authHeaders, getAuthToken, setAuthToken } from "@/lib/auth";
import { PLAN_LABELS, normalizePlan, planMeets, type SubscriptionPlan } from "@/lib/entitlements";
import { cloudGet, cloudPost } from "@/lib/cloud-api";
import { getLocalOwnerBinding } from "@/lib/local-owner-binding";
import { loadLocalLicense, localLicensePlan, localLicenseRole, type LocalLicense } from "@/lib/local-license";

type AccountItem = {
  account_id: string;
  broker_provider: string;
  is_default: boolean;
  status: string;
  last_error?: string | null;
  quote_ready?: boolean;
  trade_ready?: boolean;
  manual_disconnected?: boolean;
};

type AccountsResponse = {
  ok: boolean;
  default_account_id?: string | null;
  accounts?: AccountItem[];
};

type AuthMeResponse = {
  ok?: boolean;
  user?: {
    username?: string;
    plan?: string;
    role?: string;
    is_admin?: boolean;
  };
  session_created_at?: string;
};

type TradeAccount = {
  net_assets?: number;
  buy_power?: number;
  cash?: number;
  currency?: string;
};

type SetupConfig = {
  values?: Record<string, string>;
};

function normalizeRole(value: unknown): string {
  const role = String(value || "user").trim().toLowerCase();
  return role || "user";
}

function isAdminRole(value: unknown): boolean {
  const role = normalizeRole(value);
  return role === "admin" || role === "owner";
}

function strongerPlan(left: SubscriptionPlan, right: SubscriptionPlan): SubscriptionPlan {
  return planMeets(left, right) ? left : right;
}

function mergeLicenseIdentity(
  basePlan: SubscriptionPlan,
  baseRole: string,
  baseIsAdmin: boolean,
  license?: LocalLicense | null
): { plan: SubscriptionPlan; role: string; isAdmin: boolean } {
  const cleanBaseRole = normalizeRole(baseRole);
  const cleanBaseIsAdmin = Boolean(baseIsAdmin) || isAdminRole(cleanBaseRole);
  if (!license) {
    return {
      plan: cleanBaseIsAdmin ? "premium" : basePlan,
      role: cleanBaseIsAdmin && cleanBaseRole === "user" ? "admin" : cleanBaseRole,
      isAdmin: cleanBaseIsAdmin,
    };
  }

  const licenseRole = localLicenseRole(license);
  const licenseIsAdmin = Boolean(license.is_admin || license.isAdmin) || isAdminRole(licenseRole);
  const nextIsAdmin = cleanBaseIsAdmin || licenseIsAdmin;
  const nextRole = nextIsAdmin
    ? isAdminRole(cleanBaseRole)
      ? cleanBaseRole
      : isAdminRole(licenseRole)
        ? licenseRole
        : "admin"
    : licenseRole || cleanBaseRole;

  return {
    plan: nextIsAdmin ? "premium" : strongerPlan(basePlan, localLicensePlan(license)),
    role: nextRole,
    isAdmin: nextIsAdmin,
  };
}

function HeaderIcon({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <svg
      className={clsx("h-4 w-4 shrink-0", className)}
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

function Dot({ tone }: { tone: "ok" | "warn" | "danger" | "idle" }) {
  return (
    <span
      className={clsx(
        "h-2 w-2 rounded-full",
        tone === "ok" && "bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.75)]",
        tone === "warn" && "bg-amber-300 shadow-[0_0_10px_rgba(252,211,77,0.65)]",
        tone === "danger" && "bg-rose-400 shadow-[0_0_10px_rgba(251,113,133,0.65)]",
        tone === "idle" && "bg-slate-500"
      )}
    />
  );
}

function StatusPill({
  label,
  value,
  tone = "idle",
}: {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "danger" | "idle";
}) {
  return (
    <div className="topbar-pill">
      <Dot tone={tone} />
      <span className="text-slate-500">{label}</span>
      <span className="max-w-[8rem] truncate text-slate-100">{value}</span>
    </div>
  );
}

function money(value: unknown, currency?: string) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  const ccy = String(currency || "USD").trim() || "USD";
  return `${ccy} ${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function routeTitle(pathname: string | null) {
  const path = String(pathname || "");
  if (path.startsWith("/auto-trading/options-0dte")) return "QQQ 0DTE";
  if (path.startsWith("/auto-trading/options-1dte")) return "QQQ 1DTE";
  if (path.startsWith("/auto-trading/stocks")) return "股票自动交易";
  if (path.startsWith("/auto-trading")) return "自动交易";
  if (path.startsWith("/dashboard")) return "总览";
  if (path.startsWith("/market")) return "市场分析";
  if (path.startsWith("/signals")) return "信号中心";
  if (path.startsWith("/backtest")) return "回测中心";
  if (path.startsWith("/research")) return "研究中心";
  if (path.startsWith("/tradingagents")) return "TradingAgents";
  if (path.startsWith("/agent-strategy-lab")) return "Agent Strategy Lab";
  if (path.startsWith("/trade")) return "交易面板";
  if (path.startsWith("/options")) return "期权交易";
  if (path.startsWith("/notifications")) return "通知中心";
  if (path.startsWith("/billing")) return "订购与升级";
  if (path.startsWith("/admin/orders")) return "收款订单";
  if (path.startsWith("/admin/licenses")) return "License 发放";
  if (path.startsWith("/profile")) return "个人中心";
  if (path.startsWith("/onboarding")) return "本地 Agent 配置向导";
  if (path.startsWith("/setup")) return "系统设置";
  return "控制台";
}

function initials(name: string) {
  const cleaned = String(name || "U").trim();
  return cleaned.slice(0, 2).toUpperCase();
}

function PlanBadge({ plan, admin = false }: { plan: SubscriptionPlan; admin?: boolean }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold",
        admin
          ? "border-amber-300/45 bg-amber-300/10 text-amber-100"
          : plan === "premium"
            ? "border-cyan-300/40 bg-cyan-400/10 text-cyan-100"
            : plan === "pro"
              ? "border-violet-300/40 bg-violet-400/10 text-violet-100"
              : "border-slate-500/40 bg-slate-800/70 text-slate-300"
      )}
    >
      {admin ? "Admin" : PLAN_LABELS[plan]}
    </span>
  );
}

type TopBarProps = {
  authMode?: "local" | "clerk";
  cloudUsername?: string;
  cloudEmail?: string;
  cloudOwnerId?: string;
  cloudPlan?: string;
  cloudRole?: string;
  cloudIsAdmin?: boolean;
  onCloudSignOut?: () => Promise<void> | void;
};

export function TopBar({
  authMode = "local",
  cloudUsername = "",
  cloudEmail = "",
  cloudOwnerId = "",
  cloudPlan = "",
  cloudRole = "",
  cloudIsAdmin = false,
  onCloudSignOut,
}: TopBarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const [me, setMe] = useState<AuthMeResponse | null>(null);
  const [accounts, setAccounts] = useState<AccountsResponse | null>(null);
  const [tradeAccount, setTradeAccount] = useState<TradeAccount | null>(null);
  const [setupConfig, setSetupConfig] = useState<SetupConfig | null>(null);
  const [localLicense, setLocalLicense] = useState<LocalLicense | null>(null);

  const defaultAccount = useMemo(() => {
    const list = accounts?.accounts || [];
    const defaultId = String(accounts?.default_account_id || "").trim();
    return list.find((a) => a.account_id === defaultId) || list.find((a) => a.is_default) || list[0] || null;
  }, [accounts]);

  const envLocalBinding = getLocalOwnerBinding(cloudEmail);
  const normalizedCloudOwnerId = String(cloudOwnerId || "").trim().toLowerCase();
  const normalizedCloudRole = normalizeRole(cloudRole);
  const cloudOwnerIsAdmin = Boolean(cloudIsAdmin) || isAdminRole(normalizedCloudRole);
  const localBinding = normalizedCloudOwnerId
    ? {
        ...envLocalBinding,
        matched: true,
        ownerId: normalizedCloudOwnerId,
        plan: cloudOwnerIsAdmin ? ("premium" as const) : normalizePlan(cloudPlan),
        role: normalizedCloudRole,
        isAdmin: cloudOwnerIsAdmin,
      }
    : envLocalBinding;
  const hasLocalOwner = authMode !== "clerk" || localBinding.matched;
  const connected = Boolean(defaultAccount && !defaultAccount.manual_disconnected && defaultAccount.quote_ready && defaultAccount.trade_ready);
  const accountTone = !hasLocalOwner
    ? "warn"
    : connected
      ? "ok"
      : defaultAccount?.manual_disconnected
        ? "idle"
        : defaultAccount?.last_error
          ? "danger"
          : "warn";
  const username = String((authMode === "clerk" ? cloudUsername : "") || me?.user?.username || "user");
  const ownerId = String(hasLocalOwner ? localBinding.ownerId || me?.user?.username || username : "未绑定");
  const setupValues = setupConfig?.values || {};
  const l3Allowed = String(setupValues.openclaw_mcp_allow_l3 || "").toLowerCase() === "true";
  const l3Level = String(setupValues.openclaw_mcp_max_level || "").toUpperCase();
  const l3Ready = l3Allowed && l3Level === "L3" && Boolean(String(setupValues.openclaw_mcp_l3_confirmation_token || "").trim());
  const hasTradeModeSignal =
    "live_trading_dry_run" in setupValues ||
    "trade_dry_run" in setupValues ||
    "live_trading_disabled" in setupValues ||
    "trade_kill_switch" in setupValues;
  const tradingDryRun = String(setupValues.live_trading_dry_run || setupValues.trade_dry_run || "").toLowerCase() === "true";
  const killSwitch = String(setupValues.live_trading_disabled || setupValues.trade_kill_switch || "").toLowerCase() === "true";
  const tradeModeLabel = killSwitch ? "禁用" : tradingDryRun ? "模拟" : hasTradeModeSignal ? "实盘" : "待确认";
  const tradeModeTone = killSwitch ? "danger" : tradingDryRun ? "warn" : hasTradeModeSignal ? "ok" : "idle";
  const baseRole = localBinding.matched ? localBinding.role : normalizeRole(me?.user?.role);
  const baseIsAdmin = localBinding.matched ? localBinding.isAdmin : Boolean(me?.user?.is_admin) || isAdminRole(baseRole);
  const basePlan = baseIsAdmin ? "premium" : localBinding.matched ? localBinding.plan : normalizePlan(me?.user?.plan);
  const effectiveIdentity = mergeLicenseIdentity(basePlan, baseRole, baseIsAdmin, localLicense);
  const plan = effectiveIdentity.plan;
  const isAdmin = effectiveIdentity.isAdmin;
  const setupHref = hasLocalOwner ? "/setup" : "/onboarding";
  const accountSubtitle = hasLocalOwner ? defaultAccount?.account_id || "未选择账户" : "未绑定本地 Agent";

  const load = useCallback(async () => {
    if (!hasLocalOwner) {
      setMe(null);
      setAccounts(null);
      setSetupConfig(null);
      setLocalLicense(null);
      return;
    }
    const [meResp, accountsResp, setupResp] = await Promise.allSettled([
      cloudGet<AuthMeResponse>("/auth/me", { cacheTtlMs: 0, retries: 0 }),
      apiGet<AccountsResponse>("/setup/accounts", { cacheTtlMs: 0, retries: 0 }),
      apiGet<SetupConfig>("/setup/config", { cacheTtlMs: 0, retries: 0 }),
    ]);
    if (meResp.status === "fulfilled") setMe(meResp.value);
    if (accountsResp.status === "fulfilled") setAccounts(accountsResp.value);
    if (setupResp.status === "fulfilled") setSetupConfig(setupResp.value);
  }, [hasLocalOwner]);

  useEffect(() => {
    let cancelled = false;
    const loadLicense = async () => {
      if (!hasLocalOwner || !ownerId) {
        setLocalLicense(null);
        return;
      }
      const license = await loadLocalLicense(ownerId);
      if (!cancelled) setLocalLicense(license);
    };
    void loadLicense();
    return () => {
      cancelled = true;
    };
  }, [hasLocalOwner, ownerId]);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 30000);
    return () => window.clearInterval(id);
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      if (!connected || !defaultAccount?.account_id) {
        setTradeAccount(null);
        return;
      }
      try {
        const data = await apiGet<TradeAccount>(
          `/trade/account?account_id=${encodeURIComponent(defaultAccount.account_id)}`,
          { cacheTtlMs: 0, retries: 0, timeoutMs: 8000 }
        );
        if (!cancelled) setTradeAccount(data);
      } catch {
        if (!cancelled) setTradeAccount(null);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [connected, defaultAccount?.account_id]);

  useEffect(() => {
    const onDown = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, []);

  const goToAuth = useCallback(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem("mt_force_clerk_login", "1");
      window.location.replace("/auth?forceLogin=1");
      return;
    }
    router.replace("/auth?forceLogin=1");
  }, [router]);

  const logout = useCallback(async () => {
    setOpen(false);
    if (authMode === "clerk") {
      setAuthToken("");
      if (typeof window === "undefined") {
        router.replace("/auth?forceLogin=1");
        return;
      }
      const fallback = window.setTimeout(goToAuth, 900);
      void Promise.resolve(onCloudSignOut?.())
        .catch(() => undefined)
        .finally(() => {
          window.clearTimeout(fallback);
          goToAuth();
        });
      return;
    }
    try {
      const token = getAuthToken();
      if (token) {
        await cloudPost("/auth/logout", {}, { headers: authHeaders(token), retries: 0, cacheTtlMs: 0 });
      }
    } catch {
      /* local logout still wins */
    } finally {
      setAuthToken("");
      router.replace("/auth?forceLogin=1");
    }
  }, [authMode, goToAuth, onCloudSignOut, router]);

  return (
    <header className="topbar">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <div className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-cyan-400/30 bg-cyan-400/10 text-cyan-200 md:flex">
          <HeaderIcon>
            <path d="M4 18V6" />
            <path d="M8 18V9" />
            <path d="M12 18V3" />
            <path d="M16 18v-7" />
            <path d="M20 18V5" />
          </HeaderIcon>
        </div>
        <div className="min-w-0">
          <div className="text-xs font-medium text-slate-500">Multi Trading</div>
          <div className="truncate text-base font-semibold text-slate-100">{routeTitle(pathname)}</div>
        </div>
      </div>

      <div className="hidden min-w-0 flex-1 items-center justify-center gap-2 xl:flex">
        <StatusPill label="券商" value={defaultAccount?.broker_provider || "-"} tone={accountTone} />
        <StatusPill label="账户" value={defaultAccount?.account_id || "-"} tone={accountTone} />
        <StatusPill label="交易" value={tradeModeLabel} tone={tradeModeTone} />
        <StatusPill label="L3" value={l3Ready ? "已就绪" : "未授权"} tone={l3Ready ? "ok" : "warn"} />
      </div>

      <div className="flex items-center gap-2">
        <Link className="topbar-icon-btn" href="/notifications" aria-label="通知中心" title="通知中心">
          <HeaderIcon>
            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
            <path d="M13.73 21a2 2 0 0 1-3.46 0" />
          </HeaderIcon>
        </Link>
        <Link className="topbar-icon-btn" href={setupHref} aria-label={hasLocalOwner ? "系统设置" : "本地 Agent 配置向导"} title={hasLocalOwner ? "系统设置" : "本地 Agent 配置向导"}>
          <HeaderIcon>
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.1V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 8.6 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1.1-.4H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 8.6a1.7 1.7 0 0 0-.34-1.88l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1.1V3a2 2 0 1 1 4 0v.09A1.7 1.7 0 0 0 15.4 4.6a1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9c.38.21.72.5 1 .86.28.36.43.81.43 1.26V12a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.34-.99z" />
          </HeaderIcon>
        </Link>

        <div className="relative" ref={menuRef}>
          <button type="button" className="topbar-user-btn" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            <span className="relative flex h-8 w-8 items-center justify-center rounded-lg bg-cyan-400/15 text-xs font-bold text-cyan-100">
              {initials(username)}
              <span className="absolute -right-0.5 -top-0.5">
                <Dot tone={accountTone} />
              </span>
            </span>
            <span className="hidden min-w-0 text-left lg:block">
              <span className="block max-w-[9rem] truncate text-xs font-semibold text-slate-100">{username}</span>
              <span className="block max-w-[9rem] truncate text-[11px] text-slate-500">
                {accountSubtitle}
              </span>
            </span>
            <HeaderIcon className="hidden text-slate-500 sm:block">
              <path d="M6 9l6 6 6-6" />
            </HeaderIcon>
          </button>

          {open ? (
            <div className="topbar-menu">
              <div className="border-b border-slate-800/90 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="text-sm font-semibold text-slate-100">{username}</div>
                  <PlanBadge plan={plan} />
                  {isAdmin ? <PlanBadge plan={plan} admin /> : null}
                </div>
                <div className="mt-1 text-xs text-slate-500">owner_id: {ownerId}</div>
              </div>

              <div className="grid gap-3 p-4">
                <div className="topbar-menu-section">
                  <div className="topbar-menu-label">交易账户</div>
                  <div className="mt-2 grid gap-1 text-xs">
                    <div className="flex justify-between gap-3">
                      <span className="text-slate-500">券商</span>
                      <span className="text-slate-200">{defaultAccount?.broker_provider || "-"}</span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span className="text-slate-500">账户</span>
                      <span className="text-slate-200">{defaultAccount?.account_id || "-"}</span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span className="text-slate-500">状态</span>
                      <span className={clsx(connected ? "text-emerald-300" : "text-amber-300")}>
                        {connected ? "已连接" : defaultAccount?.manual_disconnected ? "手动断开" : "未连接"}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="topbar-menu-section">
                  <div className="topbar-menu-label">资金摘要</div>
                  <div className="mt-2 grid gap-1 text-xs">
                    <div className="flex justify-between gap-3">
                      <span className="text-slate-500">净资产</span>
                      <span className="text-slate-200">{money(tradeAccount?.net_assets, tradeAccount?.currency)}</span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span className="text-slate-500">可用购买力</span>
                      <span className="text-slate-200">{money(tradeAccount?.buy_power, tradeAccount?.currency)}</span>
                    </div>
                  </div>
                </div>

                <div className="topbar-menu-section">
                  <div className="topbar-menu-label">安全状态</div>
                  <div className="mt-2 grid gap-1 text-xs">
                    <div className="flex justify-between gap-3">
                      <span className="text-slate-500">交易模式</span>
                      <span className={killSwitch ? "text-rose-300" : tradingDryRun ? "text-amber-300" : hasTradeModeSignal ? "text-emerald-300" : "text-slate-300"}>{tradeModeLabel}</span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span className="text-slate-500">L3 授权</span>
                      <span className={l3Ready ? "text-emerald-300" : "text-amber-300"}>{l3Ready ? "已就绪" : "未就绪"}</span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="grid gap-2 border-t border-slate-800/90 p-3">
                <Link className="topbar-menu-action" href="/profile" onClick={() => setOpen(false)}>
                  个人中心
                </Link>
                {!hasLocalOwner ? (
                  <Link className="topbar-menu-action" href="/onboarding" onClick={() => setOpen(false)}>
                    本地 Agent 配置向导
                  </Link>
                ) : null}
                {hasLocalOwner ? (
                  <Link className="topbar-menu-action" href="/trade" onClick={() => setOpen(false)}>
                    打开交易面板
                  </Link>
                ) : null}
                <button type="button" className="topbar-menu-action text-left text-rose-300" onClick={logout}>
                  退出登录
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}
