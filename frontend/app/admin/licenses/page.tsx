"use client";

import { useEffect, useMemo, useState } from "react";
import { PageShell } from "@/components/ui/page-shell";
import { authHeaders, getAuthToken } from "@/lib/auth";
import {
  localAgentGet as apiGet,
  localAgentPost as apiPost,
  setLocalAgentCloudIdentity,
} from "@/lib/local-agent-api";
import { useEntitlements } from "@/lib/use-entitlements";

type Plan = "free" | "pro" | "premium";
type EmailStatus = "pending" | "sent" | "skipped" | "failed";

type DeliveryRow = {
  id: string;
  email: string;
  ownerId: string;
  plan: Plan;
  status: string;
  provider: string;
  providerEventId: string;
  emailStatus: EmailStatus;
  emailProvider: string;
  emailMessageId: string;
  emailError: string;
  currentPeriodEnd: number | null;
  issuedAt: number;
  expiresAt: number;
  createdAt: number;
  updatedAt?: number;
  licenseJson: string;
};

type LicenseLifecycle = "current" | "history" | "expired" | "revoked";
type DeliveryViewRow = DeliveryRow & {
  lifecycle: LicenseLifecycle;
};

type DeliveryListResponse = {
  ok?: boolean;
  rows?: DeliveryRow[];
  error?: string;
};

type AdminActionResponse = {
  ok?: boolean;
  action?: string;
  deliveryId?: string;
  ownerId?: string;
  plan?: Plan;
  emailStatus?: EmailStatus;
  emailError?: string;
  row?: DeliveryRow;
  error?: string;
};

type ConvexDevStatus = {
  ok?: boolean;
  action?: string;
  running?: boolean;
  pid?: number | null;
  tracking?: string;
  detected_pids?: number[];
  cwd?: string;
  command?: string[];
  pid_file?: string;
  started_at?: string;
  stopped_at?: string;
  last_action?: string;
  stdout_tail?: string;
  stderr_tail?: string;
  logs?: {
    stdout?: string;
    stderr?: string;
  };
};

const PLAN_LABELS: Record<Plan, string> = {
  free: "Free",
  pro: "Pro",
  premium: "Premium",
};

const PLAN_RANK: Record<Plan, number> = {
  free: 0,
  pro: 1,
  premium: 2,
};

const LIFECYCLE_LABELS: Record<LicenseLifecycle, string> = {
  current: "当前有效",
  history: "历史（已被覆盖）",
  expired: "已过期",
  revoked: "已撤销",
};

const LIFECYCLE_TITLES: Record<LicenseLifecycle, string> = {
  current: "当前 owner 实际生效的最高档位、最长有效期 License",
  history: "同一 owner 已存在更新或更长有效期的 License，这条记录仅保留为历史凭证",
  expired: "订阅或本地密钥已经过期",
  revoked: "该 License 已被管理员撤销",
};

function formatTime(value?: number | string | null) {
  const raw = typeof value === "number" ? value : Date.parse(String(value || ""));
  if (!Number.isFinite(raw) || raw <= 0) return "-";
  return new Date(raw).toLocaleString("zh-CN");
}

function formatMaybeTime(value?: number | string | null) {
  const formatted = formatTime(value);
  return formatted === "-" ? "未同步" : formatted;
}

function statusTone(status: string) {
  if (status === "sent") return "border-emerald-400/30 bg-emerald-400/10 text-emerald-100";
  if (status === "failed") return "border-rose-400/35 bg-rose-400/10 text-rose-100";
  if (status === "skipped") return "border-amber-400/35 bg-amber-400/10 text-amber-100";
  return "border-slate-500/40 bg-slate-700/30 text-slate-200";
}

function licenseStatusTone(status: string) {
  if (status === "active" || status === "trialing") return "border-emerald-400/30 bg-emerald-400/10 text-emerald-100";
  if (status === "canceled" || status === "expired") return "border-rose-400/35 bg-rose-400/10 text-rose-100";
  return "border-amber-400/35 bg-amber-400/10 text-amber-100";
}

function lifecycleTone(lifecycle: LicenseLifecycle) {
  if (lifecycle === "current") return "border-emerald-300/35 bg-emerald-400/10 text-emerald-100";
  if (lifecycle === "revoked") return "border-rose-400/35 bg-rose-400/10 text-rose-100";
  if (lifecycle === "expired") return "border-amber-400/35 bg-amber-400/10 text-amber-100";
  return "border-slate-500/40 bg-slate-700/30 text-slate-300";
}

function rowTone(lifecycle: LicenseLifecycle) {
  if (lifecycle === "current") return "bg-emerald-400/[0.035]";
  if (lifecycle === "revoked" || lifecycle === "expired") return "opacity-60";
  return "opacity-75";
}

function convexDevTone(running?: boolean) {
  return running
    ? "border-emerald-300/35 bg-emerald-400/10 text-emerald-100"
    : "border-slate-500/40 bg-slate-700/30 text-slate-300";
}

function ownerIsValid(ownerId: string) {
  return /^[a-z0-9][a-z0-9_-]{2,39}$/.test(ownerId.trim().toLowerCase());
}

function clampDays(value: number) {
  if (!Number.isFinite(value)) return 30;
  return Math.max(1, Math.min(370, Math.floor(value)));
}

function upgradeTargets(plan: Plan): Plan[] {
  if (plan === "pro") return ["premium"];
  return [];
}

function normalizeRowStatus(row: DeliveryRow) {
  return String(row.status || "").trim().toLowerCase();
}

function rowActiveUntil(row: DeliveryRow) {
  const subscriptionEnd = Number(row.currentPeriodEnd || 0);
  if (Number.isFinite(subscriptionEnd) && subscriptionEnd > 0) return subscriptionEnd;
  const licenseEnd = Number(row.expiresAt || 0);
  return Number.isFinite(licenseEnd) ? licenseEnd : 0;
}

function hasActiveCurrentPeriod(row: DeliveryRow) {
  return Number(row.currentPeriodEnd || 0) > Date.now();
}

function isRevokedRow(row: DeliveryRow) {
  return normalizeRowStatus(row) === "canceled";
}

function isExpiredRow(row: DeliveryRow, nowMs: number) {
  const status = normalizeRowStatus(row);
  if (status === "expired") return true;
  if (status === "canceled") return false;
  return rowActiveUntil(row) > 0 && rowActiveUntil(row) <= nowMs;
}

function isEffectiveCandidate(row: DeliveryRow, nowMs: number) {
  const status = normalizeRowStatus(row);
  return (status === "active" || status === "trialing") && rowActiveUntil(row) > nowMs;
}

function betterEffectiveRow(left: DeliveryRow, right: DeliveryRow) {
  const leftRank = PLAN_RANK[left.plan] ?? 0;
  const rightRank = PLAN_RANK[right.plan] ?? 0;
  if (leftRank !== rightRank) return leftRank > rightRank ? left : right;
  const leftEnd = rowActiveUntil(left);
  const rightEnd = rowActiveUntil(right);
  if (leftEnd !== rightEnd) return leftEnd > rightEnd ? left : right;
  const leftIssued = Number(left.issuedAt || left.createdAt || 0);
  const rightIssued = Number(right.issuedAt || right.createdAt || 0);
  return leftIssued >= rightIssued ? left : right;
}

export default function AdminLicensePage() {
  if (process.env.NEXT_PUBLIC_MT_BUILD_TARGET === "customer") {
    return (
      <PageShell>
        <div className="panel space-y-3 border-slate-700 bg-slate-950/40 text-sm text-slate-300">
          <div className="text-xl font-semibold text-slate-50">License 发放</div>
          <p>客户安装包不包含管理员发证功能。</p>
          该功能仅保留在管理员版本中；客户只需要在个人中心导入管理员签发的 License。
        </div>
      </PageShell>
    );
  }

  const entitlements = useEntitlements();
  const [email, setEmail] = useState("");
  const [ownerId, setOwnerId] = useState("");
  const [plan, setPlan] = useState<Plan>("pro");
  const [periodDays, setPeriodDays] = useState(30);
  const [renewDays, setRenewDays] = useState(30);
  const [query, setQuery] = useState("");
  const [note, setNote] = useState("");
  const [rows, setRows] = useState<DeliveryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [actionLoadingId, setActionLoadingId] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [selectedLicense, setSelectedLicense] = useState("");
  const [convexDev, setConvexDev] = useState<ConvexDevStatus | null>(null);
  const [convexDevLoading, setConvexDevLoading] = useState(false);
  const [convexDevAction, setConvexDevAction] = useState<"" | "start" | "stop" | "restart">("");
  const [convexDevError, setConvexDevError] = useState("");

  useEffect(() => {
    if (!entitlements.username) return;
    setLocalAgentCloudIdentity({
      email: entitlements.username,
      ownerId: entitlements.username,
      plan: entitlements.plan,
      role: entitlements.role,
      isAdmin: entitlements.isAdmin,
    });
  }, [entitlements.isAdmin, entitlements.plan, entitlements.role, entitlements.username]);

  const canSubmit = useMemo(() => {
    return Boolean(email.trim().includes("@") && ownerIsValid(ownerId) && periodDays >= 1 && periodDays <= 370);
  }, [email, ownerId, periodDays]);

  const viewRows = useMemo<DeliveryViewRow[]>(() => {
    const nowMs = Date.now();
    const currentByOwner = new Map<string, DeliveryRow>();
    for (const row of rows) {
      if (!isEffectiveCandidate(row, nowMs)) continue;
      const owner = row.ownerId.trim().toLowerCase();
      if (!owner) continue;
      const existing = currentByOwner.get(owner);
      currentByOwner.set(owner, existing ? betterEffectiveRow(existing, row) : row);
    }
    return [...rows]
      .sort((left, right) => {
        const ownerSort = left.ownerId.localeCompare(right.ownerId);
        if (ownerSort !== 0) return ownerSort;
        return Number(right.createdAt || 0) - Number(left.createdAt || 0);
      })
      .map((row) => {
        const owner = row.ownerId.trim().toLowerCase();
        const current = currentByOwner.get(owner);
        let lifecycle: LicenseLifecycle = "history";
        if (isRevokedRow(row)) lifecycle = "revoked";
        else if (isExpiredRow(row, nowMs)) lifecycle = "expired";
        else if (current?.id === row.id) lifecycle = "current";
        return { ...row, lifecycle };
      });
  }, [rows]);

  const lifecycleCounts = useMemo(() => {
    return viewRows.reduce(
      (acc, row) => {
        acc[row.lifecycle] += 1;
        return acc;
      },
      { current: 0, history: 0, expired: 0, revoked: 0 } as Record<LicenseLifecycle, number>
    );
  }, [viewRows]);

  const loadHistory = async (nextQuery = query) => {
    setHistoryLoading(true);
    setError("");
    try {
      const token = getAuthToken();
      const params = new URLSearchParams({ limit: "100" });
      const q = nextQuery.trim();
      if (q) params.set("q", q);
      const response = await fetch(`/api/admin/license-deliveries?${params.toString()}`, {
        headers: authHeaders(token),
        cache: "no-store",
      });
      const data = (await response.json()) as DeliveryListResponse;
      if (!response.ok || data.error) throw new Error(data.error || `request_${response.status}`);
      setRows(data.rows || []);
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setHistoryLoading(false);
    }
  };

  const loadConvexDevStatus = async () => {
    setConvexDevLoading(true);
    setConvexDevError("");
    try {
      const data = await apiGet<ConvexDevStatus>("/setup/convex-dev/status", {
        cacheTtlMs: 0,
        retries: 0,
        timeoutMs: 7000,
      });
      setConvexDev(data);
    } catch (err: any) {
      setConvexDevError(String(err?.message || err));
    } finally {
      setConvexDevLoading(false);
    }
  };

  useEffect(() => {
    void loadHistory("");
    void loadConvexDevStatus();
  }, []);

  const issueLicense = async () => {
    if (!canSubmit) {
      setError("请填写有效邮箱、本地 owner_id 和订阅周期天数。");
      return;
    }
    setLoading(true);
    setMessage("");
    setError("");
    try {
      const token = getAuthToken();
      const periodEndSeconds = Math.floor((Date.now() + clampDays(periodDays) * 24 * 60 * 60 * 1000) / 1000);
      const response = await fetch("/api/admin/license-deliveries", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          ...authHeaders(token),
        },
        body: JSON.stringify({
          email: email.trim(),
          owner_id: ownerId.trim().toLowerCase(),
          plan,
          status: "active",
          current_period_end: periodEndSeconds,
          metadata: {
            note: note.trim(),
            issued_from: "admin_license_page",
          },
        }),
      });
      const data = (await response.json()) as AdminActionResponse;
      if (!response.ok || data.error) throw new Error(data.error || `request_${response.status}`);
      setMessage(
        data.emailStatus === "sent"
          ? `License 已发送给 ${email.trim()}。`
          : `License 已生成，邮件状态：${data.emailStatus || "unknown"}。`
      );
      setNote("");
      await loadHistory();
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setLoading(false);
    }
  };

  const runAdminAction = async (row: DeliveryViewRow, action: "resend" | "renew" | "upgrade" | "revoke", targetPlan?: Plan) => {
    if ((action === "resend" || action === "renew" || action === "upgrade") && row.lifecycle !== "current") {
      setError("请在该 owner 的“当前有效” License 记录上执行重发、续期或升级，历史记录仅建议复制查看。");
      return;
    }
    if (action === "revoke") {
      const ok = window.confirm(
        `确认撤销 ${row.ownerId} 的 ${PLAN_LABELS[row.plan]} License 记录？已导入到客户本地的离线密钥不会被远程强制删除，但该记录会标记为已撤销。`
      );
      if (!ok) return;
    }
    if (action === "upgrade") {
      if (!targetPlan || targetPlan === "free") {
        setError("请选择要升级到的 Pro / Premium 套餐。");
        return;
      }
      const ok = window.confirm(
        `确认将 ${row.ownerId} 从 ${PLAN_LABELS[row.plan]} 升级到 ${PLAN_LABELS[targetPlan]}？升级不会增加订阅天数，会沿用当前订阅到期时间。`
      );
      if (!ok) return;
    }

    setActionLoadingId(`${row.id}:${action}${targetPlan ? `:${targetPlan}` : ""}`);
    setMessage("");
    setError("");
    try {
      const token = getAuthToken();
      const response = await fetch("/api/admin/license-deliveries", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          ...authHeaders(token),
        },
        body: JSON.stringify({
          action,
          deliveryId: row.id,
          periodDays: clampDays(renewDays),
          plan: action === "upgrade" ? targetPlan : undefined,
          reason: "admin_revoke",
          metadata: {
            note: note.trim(),
            acted_from: "admin_license_page",
          },
        }),
      });
      const data = (await response.json()) as AdminActionResponse;
      if (!response.ok || data.error) throw new Error(data.error || `request_${response.status}`);

      if (action === "resend") {
        setMessage(data.emailStatus === "sent" ? `License 已重新发送给 ${row.email}。` : `重发完成，邮件状态：${data.emailStatus || "unknown"}。`);
      } else if (action === "renew") {
        setMessage(
          data.emailStatus === "sent"
            ? `${row.ownerId} 已续期 ${clampDays(renewDays)} 天，并已发送新 License。`
            : `${row.ownerId} 已续期，邮件状态：${data.emailStatus || "unknown"}。`
        );
      } else if (action === "upgrade") {
        setMessage(
          data.emailStatus === "sent"
            ? `${row.ownerId} 已升级到 ${PLAN_LABELS[data.plan || targetPlan || row.plan]}，并已发送新 License。`
            : `${row.ownerId} 已升级到 ${PLAN_LABELS[data.plan || targetPlan || row.plan]}，邮件状态：${data.emailStatus || "unknown"}。`
        );
      } else {
        setMessage(`${row.ownerId} 的 License 记录已标记为撤销。`);
      }
      await loadHistory();
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setActionLoadingId("");
    }
  };

  const copyLicense = async (licenseJson: string) => {
    setSelectedLicense(licenseJson);
    try {
      await navigator.clipboard.writeText(licenseJson);
      setMessage("License JSON 已复制。");
    } catch {
      setMessage("已展开 License JSON，可以手动复制。");
    }
  };

  const runConvexDevAction = async (action: "start" | "stop" | "restart") => {
    setConvexDevAction(action);
    setConvexDevError("");
    setMessage("");
    try {
      const data = await apiPost<ConvexDevStatus>(`/setup/convex-dev/${action}`, {}, {
        cacheTtlMs: 0,
        retries: 0,
        timeoutMs: action === "stop" ? 12000 : 25000,
      });
      setConvexDev(data);
      const label = action === "start" ? "启动" : action === "stop" ? "停止" : "重启";
      setMessage(`Convex dev ${label}命令已执行。`);
    } catch (err: any) {
      setConvexDevError(String(err?.message || err));
    } finally {
      setConvexDevAction("");
    }
  };

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-cyan-200/80">MultiTrading Admin</div>
            <h1 className="mt-2 text-3xl font-bold text-slate-50">License 发放中心</h1>
            <p className="mt-2 text-sm leading-6 text-slate-400">
              用于手工给已付款客户签发、重发、续期和撤销 Pro / Premium 本地授权密钥。
            </p>
          </div>
          <span
            className={`rounded-full border px-3 py-1 text-sm font-semibold ${
              entitlements.isAdmin
                ? "border-amber-300/40 bg-amber-300/10 text-amber-100"
                : "border-slate-500/40 bg-slate-700/30 text-slate-300"
            }`}
          >
            {entitlements.isAdmin ? "Admin" : "非管理员"}
          </span>
        </div>
      </div>

      {!entitlements.isAdmin ? (
        <div className="panel border-amber-400/35 bg-amber-400/10 text-amber-100">
          当前账号不是管理员。页面可打开，但发放、重发、续期和撤销接口会拒绝非管理员请求。
        </div>
      ) : null}

      <details className="panel group space-y-4 border-slate-700/70 bg-slate-950/35">
        <summary className="flex cursor-pointer list-none flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-slate-300">开发调试工具</div>
            <div className="mt-1 text-lg font-semibold text-slate-100">Convex Dev 本地进程</div>
            <p className="mt-1 text-sm leading-6 text-slate-500">
              生产发证和付款订单走云端 Convex；本地 Convex dev 只用于开发、调试 schema 或 HTTP Action，日常可以保持停止。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full border border-cyan-300/25 bg-cyan-300/10 px-3 py-1 text-xs font-semibold text-cyan-100">
              默认收起
            </span>
            <span className={`rounded-full border px-3 py-1 text-sm font-semibold ${convexDevTone(convexDev?.running)}`}>
              {convexDevLoading ? "检测中" : convexDev?.running ? "本地运行中" : "本地未运行"}
            </span>
          </div>
        </summary>

        <div className="rounded-xl border border-cyan-300/20 bg-cyan-300/10 p-3 text-sm leading-6 text-cyan-50/85">
          云端 Dashboard 请用 <span className="font-mono text-cyan-100">npx convex dashboard --prod</span> 打开；只有修改 Convex 函数或本地模拟发证时，才需要启动这里的本地进程。
        </div>

        {convexDev?.running ? (
          <div className="rounded-xl border border-amber-300/25 bg-amber-300/10 p-3 text-sm leading-6 text-amber-100">
            检测到本地 Convex dev 正在运行。生产发证不依赖它，如无调试需要，可以停止以释放本地端口。
          </div>
        ) : null}

        <div className="grid gap-2 text-sm text-slate-300 md:grid-cols-2 xl:grid-cols-4">
          <div>
            <div className="text-xs text-slate-500">PID</div>
            <div className="mt-1 font-mono text-slate-100">{convexDev?.pid || "-"}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">tracking</div>
            <div className="mt-1 font-mono text-slate-100">{convexDev?.tracking || "-"}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">detected</div>
            <div className="mt-1 font-mono text-slate-100">{convexDev?.detected_pids?.join(", ") || "-"}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">last_action</div>
            <div className="mt-1 font-mono text-slate-100">{convexDev?.last_action || convexDev?.action || "-"}</div>
          </div>
        </div>

        <div className="grid gap-2 text-xs text-slate-500 md:grid-cols-2">
          <div className="truncate">cwd: <span className="font-mono text-slate-300">{convexDev?.cwd || "-"}</span></div>
          <div className="truncate">log: <span className="font-mono text-slate-300">{convexDev?.logs?.stderr || "-"}</span></div>
        </div>

        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-secondary" onClick={() => void loadConvexDevStatus()} disabled={convexDevLoading || Boolean(convexDevAction)}>
            {convexDevLoading ? "刷新中..." : "刷新状态"}
          </button>
          <button
            type="button"
            className="btn-secondary disabled:cursor-not-allowed disabled:opacity-45"
            onClick={() => void runConvexDevAction("start")}
            disabled={Boolean(convexDevAction) || Boolean(convexDev?.running)}
          >
            {convexDevAction === "start" ? "启动中..." : "启动本地 Convex Dev"}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => void runConvexDevAction("restart")}
            disabled={Boolean(convexDevAction)}
          >
            {convexDevAction === "restart" ? "重启中..." : "重启"}
          </button>
          <button
            type="button"
            className="btn-secondary text-rose-200"
            onClick={() => void runConvexDevAction("stop")}
            disabled={Boolean(convexDevAction) || !convexDev?.running}
          >
            {convexDevAction === "stop" ? "停止中..." : "停止"}
          </button>
        </div>

        {convexDevError ? <div className="rounded-xl border border-rose-400/35 bg-rose-400/10 p-3 text-sm text-rose-100">{convexDevError}</div> : null}

        {convexDev?.stderr_tail || convexDev?.stdout_tail ? (
          <details className="text-sm text-slate-400">
            <summary className="cursor-pointer text-cyan-200">查看最近日志</summary>
            <pre className="mt-3 max-h-72 overflow-auto rounded-xl border border-slate-700 bg-slate-950/70 p-4 text-xs leading-5 text-slate-200">
              {convexDev.stderr_tail || convexDev.stdout_tail}
            </pre>
          </details>
        ) : null}
      </details>

      {message ? <div className="panel border-emerald-400/30 bg-emerald-400/10 text-emerald-100">{message}</div> : null}
      {error ? <div className="panel border-rose-400/35 bg-rose-400/10 text-rose-100">{error}</div> : null}

      <div className="grid gap-5 xl:grid-cols-[minmax(360px,0.85fr)_minmax(0,1.35fr)]">
        <section className="panel space-y-4">
          <div>
            <div className="section-title">发放 License</div>
            <p className="mt-1 text-sm text-slate-500">
              用户付款后，在这里填写邮箱和本地 owner_id，系统会签发本地授权密钥并发送邮件。
            </p>
          </div>

          <label className="grid gap-2">
            <span className="field-label">客户邮箱</span>
            <input className="input-base" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="customer@example.com" />
          </label>

          <label className="grid gap-2">
            <span className="field-label">本地 owner_id</span>
            <input className="input-base" value={ownerId} onChange={(event) => setOwnerId(event.target.value)} placeholder="例如 zhangsan" />
            <span className="text-xs text-slate-500">必须和用户本地登录 / 绑定的 owner_id 一致。</span>
          </label>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="grid gap-2">
              <span className="field-label">套餐</span>
              <select className="input-base" value={plan} onChange={(event) => setPlan(event.target.value as Plan)}>
                <option value="free">Free</option>
                <option value="pro">Pro</option>
                <option value="premium">Premium</option>
              </select>
            </label>
            <label className="grid gap-2">
              <span className="field-label">订阅周期天数</span>
              <input
                className="input-base"
                type="number"
                min={1}
                max={370}
                value={periodDays}
                onChange={(event) => setPeriodDays(Number(event.target.value || 0))}
              />
              <span className="text-xs text-slate-500">用于记录客户 Pro / Premium 订阅到期；本地密钥可能按离线策略更早轮换。</span>
            </label>
          </div>

          <label className="grid gap-2">
            <span className="field-label">备注</span>
            <textarea className="input-base min-h-24" value={note} onChange={(event) => setNote(event.target.value)} placeholder="付款渠道、订单号或客服备注" />
          </label>

          <button type="button" className="btn-primary w-full disabled:cursor-not-allowed disabled:opacity-45" disabled={loading || !canSubmit} onClick={() => void issueLicense()}>
            {loading ? "正在发放..." : "签发并发送 License"}
          </button>
        </section>

        <section className="panel space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="section-title">License 管理</div>
              <p className="mt-1 text-sm text-slate-500">
                搜索、重发、续期、升级和撤销 License；续期只加时长，升级用于有效期内 Pro 到 Premium，并沿用原订阅到期时间。
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-2 text-xs text-slate-500">
                续期天数
                <input
                  className="input-base h-9 w-24 px-3 py-1 text-sm"
                  type="number"
                  min={1}
                  max={370}
                  value={renewDays}
                  onChange={(event) => setRenewDays(Number(event.target.value || 0))}
                />
              </label>
              <button type="button" className="btn-secondary" onClick={() => void loadHistory()} disabled={historyLoading}>
                {historyLoading ? "刷新中..." : "刷新"}
              </button>
            </div>
          </div>

          <form
            className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto_auto]"
            onSubmit={(event) => {
              event.preventDefault();
              void loadHistory(query);
            }}
          >
            <input
              className="input-base"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索邮箱 / owner_id / 套餐 / 状态"
            />
            <button type="submit" className="btn-secondary" disabled={historyLoading}>
              搜索
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setQuery("");
                void loadHistory("");
              }}
              disabled={historyLoading}
            >
              清空
            </button>
          </form>

          <div className="flex flex-wrap gap-2 text-xs">
            {(Object.keys(LIFECYCLE_LABELS) as LicenseLifecycle[]).map((key) => (
              <span key={key} className={`rounded-full border px-2.5 py-1 font-semibold ${lifecycleTone(key)}`}>
                {LIFECYCLE_LABELS[key]} {lifecycleCounts[key]}
              </span>
            ))}
          </div>

          <div className="table-shell">
            <table className="min-w-full text-left text-sm">
              <thead className="table-head">
                <tr>
                  <th className="px-4 py-3">客户</th>
                  <th className="px-4 py-3">owner</th>
                  <th className="px-4 py-3">套餐 / 状态</th>
                  <th className="px-4 py-3">邮件</th>
                  <th className="px-4 py-3">订阅到期</th>
                  <th className="px-4 py-3">密钥到期</th>
                  <th className="px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {viewRows.map((row) => {
                  const isCurrent = row.lifecycle === "current";
                  const canResend = isCurrent;
                  const canRenew = isCurrent && renewDays >= 1;
                  const canRevoke = normalizeRowStatus(row) !== "canceled";
                  return (
                  <tr key={row.id} className={`border-t border-slate-700/70 ${rowTone(row.lifecycle)}`}>
                    <td className="px-4 py-3">
                      <div className="font-semibold text-slate-100">{row.email}</div>
                      <div className="mt-1 text-xs text-slate-500">签发 {formatTime(row.createdAt)}</div>
                    </td>
                    <td className="px-4 py-3 text-cyan-100">{row.ownerId}</td>
                    <td className="px-4 py-3">
                      <div className="font-semibold text-slate-100">{PLAN_LABELS[row.plan] || row.plan}</div>
                      <span className={`mt-1 inline-flex rounded-full border px-2 py-0.5 text-xs ${licenseStatusTone(row.status)}`}>
                        {row.status}
                      </span>
                      <span
                        className={`ml-2 mt-1 inline-flex rounded-full border px-2 py-0.5 text-xs ${lifecycleTone(row.lifecycle)}`}
                        title={LIFECYCLE_TITLES[row.lifecycle]}
                      >
                        {LIFECYCLE_LABELS[row.lifecycle]}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full border px-2 py-1 text-xs font-semibold ${statusTone(row.emailStatus)}`}>
                        {row.emailStatus}
                      </span>
                      {row.emailError ? <div className="mt-1 max-w-[16rem] truncate text-xs text-rose-200">{row.emailError}</div> : null}
                    </td>
                    <td className="px-4 py-3 text-slate-300">{formatMaybeTime(row.currentPeriodEnd)}</td>
                    <td className="px-4 py-3 text-slate-300">{formatTime(row.expiresAt)}</td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-2">
                        <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => void copyLicense(row.licenseJson)}>
                          复制
                        </button>
                        <button
                          type="button"
                          className="btn-secondary px-3 py-1.5 text-xs"
                          disabled={!canResend || actionLoadingId === `${row.id}:resend`}
                          title={canResend ? "重发当前有效 License" : "历史记录不建议重发，请重发当前有效记录"}
                          onClick={() => void runAdminAction(row, "resend")}
                        >
                          {actionLoadingId === `${row.id}:resend` ? "发送中" : "重发"}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary px-3 py-1.5 text-xs"
                          disabled={!canRenew || actionLoadingId === `${row.id}:renew`}
                          title={canRenew ? "在当前有效 License 上续期" : "只能续期当前有效记录"}
                          onClick={() => void runAdminAction(row, "renew")}
                        >
                          {actionLoadingId === `${row.id}:renew` ? "续期中" : "续期"}
                        </button>
                        {upgradeTargets(row.plan).map((target) => {
                          const loadingId = `${row.id}:upgrade:${target}`;
                          const canUpgrade = isCurrent && normalizeRowStatus(row) === "active" && hasActiveCurrentPeriod(row);
                          return (
                            <button
                              key={target}
                              type="button"
                              className="btn-secondary px-3 py-1.5 text-xs text-cyan-100 disabled:opacity-45"
                              disabled={!canUpgrade || actionLoadingId === loadingId}
                              title={canUpgrade ? "升级只改套餐，不增加订阅天数" : "只有未到期的 Pro 订阅可以升级"}
                              onClick={() => void runAdminAction(row, "upgrade", target)}
                            >
                              {actionLoadingId === loadingId ? "升级中" : `升${PLAN_LABELS[target]}`}
                            </button>
                          );
                        })}
                        <button
                          type="button"
                          className="btn-secondary px-3 py-1.5 text-xs text-rose-200 disabled:opacity-45"
                          disabled={!canRevoke || actionLoadingId === `${row.id}:revoke`}
                          onClick={() => void runAdminAction(row, "revoke")}
                        >
                          {actionLoadingId === `${row.id}:revoke` ? "撤销中" : "撤销"}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
                })}
                {!viewRows.length ? (
                  <tr>
                    <td className="px-4 py-8 text-center text-slate-500" colSpan={7}>
                      暂无发放记录
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {selectedLicense ? (
        <section className="panel space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div className="section-title">License JSON</div>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setSelectedLicense("")}>
              收起
            </button>
          </div>
          <pre className="max-h-80 overflow-auto rounded-xl border border-slate-700 bg-slate-950/70 p-4 text-xs leading-5 text-slate-200">
            {selectedLicense}
          </pre>
        </section>
      ) : null}
    </PageShell>
  );
}
