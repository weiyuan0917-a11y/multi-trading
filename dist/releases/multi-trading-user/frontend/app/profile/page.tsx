"use client";

import { useClerk, useUser } from "@clerk/nextjs";
import { useConvexAuth, useMutation, useQuery_experimental as useQuery } from "convex/react";
import { useEffect, useMemo, useState } from "react";
import { PageShell } from "@/components/ui/page-shell";
import { authHeaders, getAuthToken } from "@/lib/auth";
import { CLERK_ENABLED } from "@/lib/clerk-mode";
import { cloudGet } from "@/lib/cloud-api";
import { convexFunctions } from "@/lib/convex-api";
import { CONVEX_ENABLED } from "@/lib/convex-mode";
import { PLAN_LABELS, type EntitlementKey } from "@/lib/entitlements";
import { LOCAL_AGENT_API_BASE } from "@/lib/local-agent-api";
import {
  getLocalLicenseStatus,
  importLocalLicense,
  previewLocalLicenseImport,
  type LocalLicense,
  type LocalLicenseImportPreview,
  type LocalLicenseStatus,
} from "@/lib/local-license";
import { getLocalOwnerBinding } from "@/lib/local-owner-binding";
import { useCloudSession } from "@/lib/use-cloud-session";
import { useEntitlements } from "@/lib/use-entitlements";

type LocalMeResponse = {
  user?: {
    username?: string;
    plan?: string;
    role?: string;
    is_admin?: boolean;
  };
  session_created_at?: string;
};

const FEATURES: Array<{ key: EntitlementKey; label: string }> = [
  { key: "research", label: "Research" },
  { key: "backtest", label: "回测" },
  { key: "tradingagents", label: "TradingAgents" },
  { key: "openbb", label: "OpenBB" },
  { key: "stock_auto_trading", label: "股票自动交易" },
  { key: "option_auto_trading", label: "期权自动交易" },
  { key: "multi_broker", label: "多券商" },
  { key: "multi_account", label: "多账户" },
];

function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "premium" | "admin" | "ok" | "neutral" }) {
  const cls =
    tone === "admin"
      ? "border-amber-300/45 bg-amber-300/10 text-amber-100"
      : tone === "premium"
        ? "border-cyan-300/40 bg-cyan-400/10 text-cyan-100"
        : tone === "ok"
          ? "border-emerald-300/35 bg-emerald-400/10 text-emerald-100"
          : "border-slate-500/40 bg-slate-800/70 text-slate-300";
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${cls}`}>{children}</span>;
}

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-4 border-b border-slate-800/80 py-2 last:border-b-0">
      <span className="shrink-0 text-sm text-slate-500">{label}</span>
      <span className="min-w-0 truncate text-right text-sm font-medium text-slate-100">{value}</span>
    </div>
  );
}

function initials(name: string) {
  return String(name || "U").trim().slice(0, 2).toUpperCase();
}

function entitlementSourceLabel(source: string, cloudStatus: string) {
  if (source === "convex") return "Convex 云端订阅";
  if (source === "local_license") return "本地授权密钥";
  if (source === "local_owner") return "本地 owner 绑定";
  if (source === "local_session") return "本地账号";
  if (cloudStatus === "loading") return "Convex 同步中";
  if (cloudStatus === "error") return "Convex 未就绪，使用本地降级";
  return "本地默认";
}

function ConvexStatusRows() {
  const session = useQuery({ query: convexFunctions.users.me, args: {}, throwOnError: false });
  const data = session.status === "success" ? session.data : null;
  return (
    <div className="mt-3">
      <InfoRow
        label="Convex"
        value={session.status === "pending" ? "同步中" : session.status === "error" ? "未就绪" : data ? "已连接" : "未同步"}
      />
      <InfoRow label="云端套餐" value={data?.subscription?.plan || "-"} />
      <InfoRow label="订阅状态" value={data?.subscription?.status || "-"} />
      <InfoRow label="云端订阅到期" value={formatEpochTime(data?.subscription?.currentPeriodEnd)} />
      <InfoRow label="云端 owner" value={data?.localOwnerBinding?.ownerId || "-"} />
    </div>
  );
}

function ConvexStatusPanel() {
  const { isAuthenticated, isLoading } = useConvexAuth();
  return (
    <section className="panel">
      <div className="section-title">云端控制台</div>
      {isLoading ? (
        <div className="mt-3">
          <InfoRow label="Convex" value="同步中" />
          <InfoRow label="云端套餐" value="-" />
          <InfoRow label="订阅状态" value="-" />
          <InfoRow label="云端订阅到期" value="-" />
          <InfoRow label="云端 owner" value="-" />
        </div>
      ) : isAuthenticated ? (
        <ConvexStatusRows />
      ) : (
        <div className="mt-3">
          <InfoRow label="Convex" value="等待登录认证" />
          <InfoRow label="云端套餐" value="-" />
          <InfoRow label="订阅状态" value="-" />
          <InfoRow label="云端订阅到期" value="-" />
          <InfoRow label="云端 owner" value="-" />
        </div>
      )}
    </section>
  );
}

function formatLicenseTime(value: unknown) {
  if (typeof value === "number") return formatEpochTime(value);
  const raw = String(value || "").trim();
  if (!raw) return "-";
  const ts = Date.parse(raw);
  if (!Number.isFinite(ts)) return raw;
  return new Date(ts).toLocaleString();
}

function formatEpochTime(value: unknown) {
  const raw = Number(value || 0);
  if (!Number.isFinite(raw) || raw <= 0) return "-";
  const ms = raw < 100000000000 ? raw * 1000 : raw;
  return new Date(ms).toLocaleString();
}

const EXPIRY_DAY_MS = 24 * 60 * 60 * 1000;

type ExpiryLevel = "unknown" | "ok" | "notice" | "warning" | "danger";

function expiryTimestampMs(value: unknown) {
  if (typeof value === "number") {
    if (!Number.isFinite(value) || value <= 0) return null;
    return value < 100000000000 ? value * 1000 : value;
  }
  const raw = String(value || "").trim();
  if (!raw) return null;
  const numeric = Number(raw);
  if (Number.isFinite(numeric) && numeric > 0) return numeric < 100000000000 ? numeric * 1000 : numeric;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

function subscriptionExpiryValue(cloudCurrentPeriodEnd: unknown, license: LocalLicense | null | undefined) {
  return (
    cloudCurrentPeriodEnd ||
    license?.subscription_expires_at ||
    license?.subscriptionExpiresAt ||
    license?.subscription_current_period_end ||
    license?.subscriptionCurrentPeriodEnd
  );
}

function subscriptionExpiresLabel(cloudCurrentPeriodEnd: unknown, license: LocalLicense | null | undefined) {
  const expiry = subscriptionExpiryValue(cloudCurrentPeriodEnd, license);
  const fromEpoch = formatEpochTime(expiry);
  if (fromEpoch !== "-") return fromEpoch;
  const fromLicense = formatLicenseTime(expiry);
  if (fromLicense !== "-") return fromLicense;
  return "未随本地密钥同步";
}

function expiryNotice(value: unknown, noun: string, expiredImpact: string) {
  const ts = expiryTimestampMs(value);
  if (!ts) {
    return {
      level: "unknown" as ExpiryLevel,
      title: "未同步",
      daysText: "-",
      body: `${noun}到期时间暂未同步，请刷新状态或重新导入 License。`,
    };
  }
  const diff = ts - Date.now();
  const days = Math.ceil(diff / EXPIRY_DAY_MS);
  if (diff <= 0) {
    return {
      level: "danger" as ExpiryLevel,
      title: "已过期",
      daysText: "已过期",
      body: `${noun}已过期。${expiredImpact}`,
    };
  }
  if (days <= 1) {
    return {
      level: "danger" as ExpiryLevel,
      title: "1 天内到期",
      daysText: "≤ 1 天",
      body: `${noun}将在 1 天内到期，建议立即续期并更新本地 License。`,
    };
  }
  if (days <= 3) {
    return {
      level: "warning" as ExpiryLevel,
      title: `${days} 天后到期`,
      daysText: `${days} 天`,
      body: `${noun}即将到期，请尽快续期，避免付费功能被锁定。`,
    };
  }
  if (days <= 7) {
    return {
      level: "notice" as ExpiryLevel,
      title: `${days} 天后到期`,
      daysText: `${days} 天`,
      body: `${noun}将在一周内到期，建议提前安排续期。`,
    };
  }
  return {
    level: "ok" as ExpiryLevel,
    title: "状态正常",
    daysText: `${days} 天`,
    body: `${noun}仍在有效期内。`,
  };
}

function expiryTone(level: ExpiryLevel) {
  if (level === "danger") {
    return {
      card: "border-rose-300/35 bg-rose-400/10",
      badge: "border-rose-300/40 bg-rose-400/15 text-rose-100",
      value: "text-rose-200",
    };
  }
  if (level === "warning") {
    return {
      card: "border-amber-300/35 bg-amber-400/10",
      badge: "border-amber-300/40 bg-amber-400/15 text-amber-100",
      value: "text-amber-100",
    };
  }
  if (level === "notice") {
    return {
      card: "border-cyan-300/30 bg-cyan-400/10",
      badge: "border-cyan-300/40 bg-cyan-400/15 text-cyan-100",
      value: "text-cyan-100",
    };
  }
  if (level === "ok") {
    return {
      card: "border-emerald-300/25 bg-emerald-400/10",
      badge: "border-emerald-300/35 bg-emerald-400/15 text-emerald-100",
      value: "text-emerald-100",
    };
  }
  return {
    card: "border-slate-700/75 bg-slate-950/45",
    badge: "border-slate-600/60 bg-slate-800/70 text-slate-300",
    value: "text-slate-300",
  };
}

function ExpiryReminderCard({
  title,
  value,
  noun,
  expiredImpact,
}: {
  title: string;
  value: unknown;
  noun: string;
  expiredImpact: string;
}) {
  const notice = expiryNotice(value, noun, expiredImpact);
  const tone = expiryTone(notice.level);
  return (
    <div className={`rounded-xl border p-4 ${tone.card}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm font-semibold text-slate-100">{title}</div>
        <span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${tone.badge}`}>{notice.title}</span>
      </div>
      <div className={`mt-3 text-2xl font-bold ${tone.value}`}>{notice.daysText}</div>
      <p className="mt-2 min-h-10 text-sm leading-5 text-slate-400">{notice.body}</p>
      <div className="mt-3 text-xs text-slate-500">
        到期时间：<span className="text-slate-300">{formatLicenseTime(value)}</span>
      </div>
    </div>
  );
}

function licensePlanLabel(license: LocalLicense | null | undefined) {
  if (!license) return "-";
  const plan = String(license.plan || "free").toLowerCase();
  if (license.is_admin || license.isAdmin) return "Premium / Admin";
  return plan === "premium" ? "Premium" : plan === "pro" ? "Pro" : "Free";
}

function parseLicenseJsonText(text: string): LocalLicense {
  const parsed = JSON.parse(text) as unknown;
  const license =
    parsed && typeof parsed === "object" && "license" in parsed
      ? (parsed as { license?: LocalLicense }).license
      : (parsed as LocalLicense);
  if (!license || typeof license !== "object") throw new Error("invalid_license_json");
  return license;
}

function importPreviewActionLabel(action: unknown) {
  const raw = String(action || "").trim();
  const labels: Record<string, string> = {
    activate: "可激活",
    duplicate: "重复导入",
    upgrade: "可升级",
    renew: "可续期",
    upgrade_and_renew: "可升级并续期",
    replace: "可替换",
    rejected: "将被拒绝",
    invalid: "无效 License",
  };
  return labels[raw] || raw || "-";
}

function importPreviewReasonLabel(reason: unknown) {
  const raw = String(reason || "").trim();
  const labels: Record<string, string> = {
    ok: "校验通过",
    no_active_license: "当前本地没有有效 License",
    same_license: "这和当前本地 License 是同一张",
    higher_plan: "新 License 的套餐更高",
    longer_period: "新 License 的订阅到期更晚",
    higher_plan_longer_period: "新 License 套餐更高且订阅周期更长",
    newer_license: "新 License 签发时间更新",
    valid_license: "License 有效，可导入",
    owner_required: "License 缺少 owner_id",
    expired: "License 密钥已过期",
    invalid_signature: "License 签名无效",
    missing_signature: "License 缺少签名",
    older_license_rejected: "当前本地已有更新的 License",
    lower_plan_rejected: "当前本地套餐更高且仍有效",
    shorter_subscription_rejected: "当前本地订阅到期时间更晚",
    missing_issued_at_rejected: "新 License 缺少签发时间",
  };
  return labels[raw] || raw || "-";
}

function importPreviewTone(preview: LocalLicenseImportPreview) {
  if (!preview.can_import) return "border-rose-300/35 bg-rose-400/10 text-rose-100";
  if (preview.action === "upgrade" || preview.action === "upgrade_and_renew") {
    return "border-cyan-300/35 bg-cyan-400/10 text-cyan-100";
  }
  if (preview.action === "renew" || preview.action === "activate") {
    return "border-emerald-300/35 bg-emerald-400/10 text-emerald-100";
  }
  return "border-slate-600/70 bg-slate-900/70 text-slate-200";
}

function LocalLicensePanel({
  onCloudActivate,
  cloudActivateLabel = "激活本地授权",
  allowManualImport = false,
}: {
  onCloudActivate?: () => Promise<string>;
  cloudActivateLabel?: string;
  allowManualImport?: boolean;
}) {
  const [status, setStatus] = useState<LocalLicenseStatus | null>(null);
  const [licenseText, setLicenseText] = useState("");
  const [pendingLicense, setPendingLicense] = useState<LocalLicense | null>(null);
  const [importPreview, setImportPreview] = useState<LocalLicenseImportPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const cloudSession = useCloudSession();

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      setStatus(await getLocalLicenseStatus());
    } catch (err: any) {
      setError(String(err?.message || err));
      setStatus(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const activate = async () => {
    if (!onCloudActivate) return;
    setLoading(true);
    setMessage("");
    setError("");
    try {
      const msg = await onCloudActivate();
      setMessage(msg || "本地授权已激活。");
      await refresh();
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setLoading(false);
    }
  };

  const importPastedLicense = async () => {
    setLoading(true);
    setMessage("");
    setError("");
    try {
      const license = pendingLicense || parseLicenseJsonText(licenseText);
      await importLocalLicense(license);
      setLicenseText("");
      setPendingLicense(null);
      setImportPreview(null);
      setMessage("License 已导入本地。");
      await refresh();
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setLoading(false);
    }
  };

  const previewPastedLicense = async () => {
    setLoading(true);
    setMessage("");
    setError("");
    try {
      const license = parseLicenseJsonText(licenseText);
      const preview = await previewLocalLicenseImport(license);
      setPendingLicense(license);
      setImportPreview(preview);
      setMessage(preview.can_import ? "License 预览完成，可以确认导入。" : "License 预览完成，但当前不能导入。");
    } catch (err: any) {
      setPendingLicense(null);
      setImportPreview(null);
      setError(String(err?.message || err));
    } finally {
      setLoading(false);
    }
  };

  const license = status?.license || null;
  const valid = Boolean(status?.valid);
  const licenseExpiryValue = license?.expires_at || license?.expiresAt;
  const subscriptionExpiry = subscriptionExpiryValue(cloudSession.data?.subscription?.currentPeriodEnd, license);
  return (
    <section className="panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="section-title">本地授权 License</div>
          <p className="mt-1 text-sm text-slate-500">
            这是本地 Agent 用于离线验权的密钥，不等同于 Pro / Premium 订阅周期；下方会分开提示订阅到期和密钥到期。
          </p>
        </div>
        <Badge tone={valid ? "ok" : "neutral"}>{valid ? "已激活" : "未激活"}</Badge>
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <ExpiryReminderCard
          title="订阅到期提醒"
          value={subscriptionExpiry}
          noun="Pro / Premium 订阅"
          expiredImpact="系统会降级为 Free；股票自动交易、期权自动交易、多券商/多账户等付费功能会锁定。"
        />
        <ExpiryReminderCard
          title="本地密钥到期提醒"
          value={licenseExpiryValue}
          noun="本地授权密钥"
          expiredImpact="本地 Agent 离线验权会失效；请重新从云端激活或导入新的 License。"
        />
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <div>
          <InfoRow label="Local owner" value={status?.owner_id || license?.owner_id || "-"} />
          <InfoRow label="本地套餐" value={licensePlanLabel(license)} />
          <InfoRow label="License 状态" value={valid ? "valid" : status?.reason || "not_found"} />
          <InfoRow label="密钥到期时间" value={formatLicenseTime(licenseExpiryValue)} />
        </div>
        <div>
          <InfoRow label="签发来源" value={license?.source || "-"} />
          <InfoRow label="签名状态" value={license?.signature_status || "-"} />
          <InfoRow label="签发时间" value={formatLicenseTime(license?.issued_at)} />
          <InfoRow label="订阅到期时间" value={subscriptionExpiresLabel(subscriptionExpiry, license)} />
          <InfoRow label="Local Agent" value={LOCAL_AGENT_API_BASE} />
        </div>
      </div>

      {(message || error) ? (
        <div className="mt-4 space-y-2">
          {message ? <div className="rounded-lg border border-emerald-300/25 bg-emerald-400/10 p-3 text-sm text-emerald-100">{message}</div> : null}
          {error ? <div className="rounded-lg border border-rose-300/30 bg-rose-400/10 p-3 text-sm text-rose-100">{error}</div> : null}
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" className="btn-secondary" onClick={() => void refresh()} disabled={loading}>
          刷新状态
        </button>
        {onCloudActivate ? (
          <button type="button" className="btn-primary" onClick={() => void activate()} disabled={loading}>
            {loading ? "处理中..." : cloudActivateLabel}
          </button>
        ) : null}
      </div>

      {allowManualImport ? (
        <div className="mt-4 grid gap-3">
          <textarea
            className="input-base min-h-28"
            value={licenseText}
            onChange={(event) => {
              setLicenseText(event.target.value);
              setPendingLicense(null);
              setImportPreview(null);
            }}
            placeholder="粘贴云端签发的 License JSON"
          />
          {importPreview ? (
            <div className={`rounded-xl border p-4 ${importPreviewTone(importPreview)}`}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="text-sm font-semibold">导入预览：{importPreviewActionLabel(importPreview.action)}</div>
                <Badge tone={importPreview.can_import ? "ok" : "neutral"}>
                  {importPreview.can_import ? "可导入" : "不可导入"}
                </Badge>
              </div>
              <p className="mt-2 text-sm text-slate-300">{importPreviewReasonLabel(importPreview.reason)}</p>
              <div className="mt-4 grid gap-3 lg:grid-cols-2">
                <div className="rounded-lg border border-slate-700/70 bg-slate-950/45 p-3">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">当前本地</div>
                  <InfoRow label="owner" value={importPreview.current?.owner_id || status?.owner_id || "-"} />
                  <InfoRow label="套餐" value={licensePlanLabel(importPreview.current)} />
                  <InfoRow label="订阅到期" value={subscriptionExpiresLabel(null, importPreview.current)} />
                  <InfoRow label="密钥到期" value={formatLicenseTime(importPreview.current?.expires_at || importPreview.current?.expiresAt)} />
                  <InfoRow label="签发时间" value={formatLicenseTime(importPreview.current?.issued_at)} />
                </div>
                <div className="rounded-lg border border-slate-700/70 bg-slate-950/45 p-3">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">准备导入</div>
                  <InfoRow label="owner" value={importPreview.incoming?.owner_id || "-"} />
                  <InfoRow label="套餐" value={licensePlanLabel(importPreview.incoming)} />
                  <InfoRow label="订阅到期" value={subscriptionExpiresLabel(null, importPreview.incoming)} />
                  <InfoRow label="密钥到期" value={formatLicenseTime(importPreview.incoming?.expires_at || importPreview.incoming?.expiresAt)} />
                  <InfoRow label="签发时间" value={formatLicenseTime(importPreview.incoming?.issued_at)} />
                  <InfoRow label="签名状态" value={importPreview.incoming?.signature_status || "-"} />
                </div>
              </div>
            </div>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-secondary" onClick={() => void previewPastedLicense()} disabled={loading || !licenseText.trim()}>
              预览 License
            </button>
            <button
              type="button"
              className="btn-primary disabled:cursor-not-allowed disabled:opacity-45"
              onClick={() => void importPastedLicense()}
              disabled={loading || !pendingLicense || !importPreview?.can_import}
            >
              确认导入
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function ProfileLayout({
  displayName,
  email,
  userId,
  loginMethod,
  ownerId,
  bindingStatus,
  sessionCreatedAt,
  planLabel,
  role,
  isAdmin,
  onManageSecurity,
  licensePanel,
}: {
  displayName: string;
  email: string;
  userId: string;
  loginMethod: string;
  ownerId: string;
  bindingStatus: string;
  sessionCreatedAt?: string;
  planLabel: string;
  role: string;
  isAdmin: boolean;
  onManageSecurity?: () => void;
  licensePanel?: React.ReactNode;
}) {
  const entitlements = useEntitlements({ email });
  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-4">
            <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl border border-cyan-300/30 bg-cyan-400/10 text-lg font-bold text-cyan-100">
              {initials(displayName || email || ownerId)}
            </div>
            <div className="min-w-0">
              <div className="text-xs font-medium uppercase text-slate-500">Personal Center</div>
              <h1 className="truncate text-2xl font-bold text-slate-100">个人中心</h1>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Badge tone={entitlements.plan === "premium" ? "premium" : "neutral"}>{entitlements.planLabel}</Badge>
                {entitlements.isAdmin ? <Badge tone="admin">Admin</Badge> : null}
                <Badge tone="ok">owner_id: {ownerId || "-"}</Badge>
              </div>
            </div>
          </div>
          {onManageSecurity ? (
            <button type="button" className="btn-secondary" onClick={onManageSecurity}>
              管理登录与密码
            </button>
          ) : null}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="panel">
          <div className="section-title">登录身份</div>
          <div className="mt-3">
            <InfoRow label="登录账号" value={email || displayName || "-"} />
            <InfoRow label="显示名称" value={displayName || "-"} />
            <InfoRow label="用户 ID" value={userId || "-"} />
            <InfoRow label="登录方式" value={loginMethod || "-"} />
            {sessionCreatedAt ? <InfoRow label="会话创建" value={sessionCreatedAt} /> : null}
          </div>
        </section>

        <section className="panel">
          <div className="section-title">本地绑定</div>
          <div className="mt-3">
            <InfoRow label="Local owner_id" value={ownerId || "-"} />
            <InfoRow label="Local Agent" value={LOCAL_AGENT_API_BASE} />
            <InfoRow label="绑定状态" value={<Badge tone={ownerId ? "ok" : "neutral"}>{bindingStatus}</Badge>} />
            <InfoRow label="本地数据" value="证券 Key / 数据源 / 历史记录保留在本机" />
          </div>
        </section>
      </div>

      {CLERK_ENABLED && CONVEX_ENABLED ? <ConvexStatusPanel /> : null}

      {licensePanel || null}

      <section className="panel">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="section-title">权限等级</div>
            <p className="mt-1 text-sm text-slate-500">当前权限决定自动交易、多券商、多账户等功能是否开放。</p>
            <div className="mt-2 text-xs text-slate-400">
              权限来源：<span className="text-slate-200">{entitlementSourceLabel(entitlements.source, entitlements.cloudStatus)}</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge tone={entitlements.plan === "premium" ? "premium" : "neutral"}>{planLabel}</Badge>
            <Badge tone={isAdmin ? "admin" : "neutral"}>{isAdmin ? "Admin" : role || "User"}</Badge>
          </div>
        </div>
        <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {FEATURES.map((feature) => {
            const enabled = entitlements.canUse(feature.key);
            return (
              <div key={feature.key} className="rounded-lg border border-slate-700/70 bg-slate-900/50 px-3 py-2">
                <div className="text-sm font-semibold text-slate-100">{feature.label}</div>
                <div className={enabled ? "mt-1 text-xs text-emerald-300" : "mt-1 text-xs text-slate-500"}>
                  {enabled ? "已解锁" : "未解锁"}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="panel">
        <div className="section-title">安全设置</div>
        <div className="mt-3 grid gap-3 md:grid-cols-[1fr_auto] md:items-center">
          <p className="text-sm leading-6 text-slate-400">
            Clerk 登录模式下，邮箱、密码、第三方登录和多因素验证由 Clerk 统一管理；MultiTrading 只保存本地 owner 绑定和交易相关配置。
          </p>
          {onManageSecurity ? (
            <button type="button" className="btn-primary" onClick={onManageSecurity}>
              打开安全设置
            </button>
          ) : (
            <span className="text-sm text-slate-500">本地账号密码管理暂未接入</span>
          )}
        </div>
      </section>
    </PageShell>
  );
}

function ClerkCloudLicensePanel({ ownerId }: { ownerId: string }) {
  const issueLocalLicense = useMutation(convexFunctions.users.issueLocalLicense);

  return (
    <LocalLicensePanel
      cloudActivateLabel="从云端订阅激活本地授权"
      onCloudActivate={async () => {
        const issued = await issueLocalLicense({ ownerId: ownerId || undefined });
        await importLocalLicense(issued.license);
        return `本地授权已激活，密钥到期时间：${formatLicenseTime(issued.expiresAt)}`;
      }}
    />
  );
}

function ClerkProfilePage() {
  const { openUserProfile } = useClerk();
  const { user } = useUser();
  const email = user?.primaryEmailAddress?.emailAddress || user?.emailAddresses?.[0]?.emailAddress || "";
  const displayName = user?.fullName || user?.username || email || "User";
  const externalProviders = useMemo(() => {
    const accounts = user?.externalAccounts || [];
    return accounts.map((account) => account.provider).filter(Boolean).join(", ");
  }, [user?.externalAccounts]);
  const binding = getLocalOwnerBinding(email);
  const entitlements = useEntitlements({ email });

  return (
    <ProfileLayout
      displayName={displayName}
      email={email}
      userId={user?.id || ""}
      loginMethod={externalProviders || "Email / Password"}
      ownerId={binding.ownerId || ""}
      bindingStatus={binding.matched ? "已绑定" : "未绑定"}
      planLabel={entitlements.planLabel}
      role={entitlements.role}
      isAdmin={entitlements.isAdmin}
      onManageSecurity={() => openUserProfile()}
      licensePanel={
        CONVEX_ENABLED ? <ClerkCloudLicensePanel ownerId={binding.ownerId || ""} /> : <LocalLicensePanel allowManualImport />
      }
    />
  );
}

function LocalProfilePage() {
  const [me, setMe] = useState<LocalMeResponse | null>(null);
  const entitlements = useEntitlements();
  const username = String(me?.user?.username || entitlements.username || "user");

  useEffect(() => {
    let cancelled = false;
    const token = getAuthToken();
    if (!token) return;
    cloudGet<LocalMeResponse>("/auth/me", {
      headers: authHeaders(token),
      cacheTtlMs: 0,
      retries: 0,
      timeoutMs: 8000,
    })
      .then((data) => {
        if (!cancelled) setMe(data);
      })
      .catch(() => {
        if (!cancelled) setMe(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <ProfileLayout
      displayName={username}
      email=""
      userId={username}
      loginMethod="本地账号"
      ownerId={username}
      bindingStatus="本地登录"
      sessionCreatedAt={me?.session_created_at}
      planLabel={PLAN_LABELS[entitlements.plan]}
      role={entitlements.role}
      isAdmin={entitlements.isAdmin}
      licensePanel={<LocalLicensePanel allowManualImport />}
    />
  );
}

export default function ProfilePage() {
  return CLERK_ENABLED ? <ClerkProfilePage /> : <LocalProfilePage />;
}
