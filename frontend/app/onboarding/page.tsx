"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useUser } from "@clerk/nextjs";
import { useMutation } from "convex/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { SetupApiKeysPanel } from "@/components/setup-api-keys-panel";
import { authHeaders, getAuthToken } from "@/lib/auth";
import { CLERK_ENABLED } from "@/lib/clerk-mode";
import { PLAN_LABELS, normalizePlan } from "@/lib/entitlements";
import { convexFunctions } from "@/lib/convex-api";
import { getLocalOwnerBinding } from "@/lib/local-owner-binding";
import {
  LOCAL_AGENT_API_BASE,
  localAgentGet,
  localAgentHealth,
  localAgentPost,
  setLocalAgentCloudIdentity,
  type LocalAgentStatus,
} from "@/lib/local-agent-api";
import {
  getMissingOnboardingStepKeys,
  loadLocalOnboardingSnapshot,
  ONBOARDING_STEP_DESCRIPTIONS,
  ONBOARDING_STEPS,
  type OnboardingStepKey,
  type OnboardingSnapshot,
} from "@/lib/onboarding-state";
import { activeLocalOwnerId, useCloudSession } from "@/lib/use-cloud-session";

type Tone = "ok" | "warn" | "danger" | "neutral";
type StepKey = OnboardingStepKey;

type AuthMeResponse = {
  user?: {
    username?: string;
    plan?: string;
    role?: string;
    is_admin?: boolean;
  };
};

function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: Tone }) {
  return (
    <span
      className={[
        "inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold",
        tone === "ok" ? "border-emerald-300/40 bg-emerald-400/10 text-emerald-200" : "",
        tone === "warn" ? "border-amber-300/40 bg-amber-400/10 text-amber-100" : "",
        tone === "danger" ? "border-rose-300/40 bg-rose-400/10 text-rose-100" : "",
        tone === "neutral" ? "border-slate-500/50 bg-slate-800/70 text-slate-300" : "",
      ].join(" ")}
    >
      {children}
    </span>
  );
}

function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="grid gap-1.5">
      <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</span>
      {children}
      {hint ? <span className="text-xs leading-5 text-slate-500">{hint}</span> : null}
    </label>
  );
}

function StepButton({
  active,
  done,
  index,
  title,
  onClick,
}: {
  active: boolean;
  done: boolean;
  index: number;
  title: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        "flex min-h-14 items-center gap-3 rounded-xl border p-3 text-left transition",
        active
          ? "border-cyan-300/45 bg-cyan-400/10 text-cyan-100"
          : done
            ? "border-emerald-300/25 bg-emerald-400/10 text-emerald-100"
            : "border-slate-700/70 bg-slate-950/30 text-slate-300 hover:border-slate-500",
      ].join(" ")}
    >
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-current/25 text-sm font-bold">
        {done ? "✓" : index + 1}
      </span>
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold">{title}</span>
        <span className="mt-0.5 block text-xs opacity-70">{done ? "已处理" : active ? "当前步骤" : "待处理"}</span>
      </span>
    </button>
  );
}

function suggestOwnerId(email: string, userId: string) {
  const local = String(email || "").split("@")[0] || userId || "user";
  const cleaned = local.toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");
  return (cleaned || `user_${String(userId || "").slice(-8) || "local"}`).slice(0, 40);
}

function normalizeOwnerId(value: string) {
  return String(value || "").trim().toLowerCase().replace(/[^a-z0-9_-]/g, "_").replace(/^_+|_+$/g, "");
}

function validOwnerId(value: string) {
  return /^[a-z0-9][a-z0-9_-]{2,39}$/.test(value);
}

export default function LocalAgentOnboardingPage() {
  if (CLERK_ENABLED) return <CloudAgentOnboardingPage />;
  return <LocalFirstOnboardingPage />;
}

function LocalFirstOnboardingPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextPath = String(searchParams?.get("next") || "/setup");
  const setupHref = nextPath.startsWith("/") && !nextPath.startsWith("//") ? nextPath : "/setup";
  const [me, setMe] = useState<AuthMeResponse | null>(null);
  const [agentStatus, setAgentStatus] = useState<LocalAgentStatus | null>(null);
  const [snapshot, setSnapshot] = useState<OnboardingSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [stepIndex, setStepIndex] = useState(0);
  const [done, setDone] = useState<Record<StepKey, boolean>>({
    owner: false,
    broker: false,
    llm: false,
    feishu: false,
    market: false,
    notify: false,
    research: false,
    apiKey: false,
  });
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [apiKeyConfirmed, setApiKeyConfirmed] = useState(false);
  const [brokerForm, setBrokerForm] = useState({
    broker_provider: "longbridge",
    account_id: "",
    longport_app_key: "",
    longport_app_secret: "",
    longport_access_token: "",
    tiger_id: "",
    tiger_account: "",
    tiger_license: "",
    tiger_token_path: "",
  });
  const [llmForm, setLlmForm] = useState({
    tradingagents_llm_provider: "openai",
    tradingagents_deep_think_llm: "gpt-4o",
    tradingagents_quick_think_llm: "gpt-4o-mini",
    llm_api_key: "",
    azure_openai_endpoint: "",
  });
  const [feishuForm, setFeishuForm] = useState({ feishu_app_id: "", feishu_app_secret: "", feishu_scheduled_chat_id: "" });
  const [marketForm, setMarketForm] = useState({ finnhub_api_key: "", tiingo_api_key: "", fred_api_key: "", coingecko_api_key: "" });
  const [notifyForm, setNotifyForm] = useState({ openclaw_mcp_max_level: "L2", openclaw_mcp_allow_l3: "false", openclaw_mcp_l3_confirmation_token: "" });
  const [researchForm, setResearchForm] = useState({
    openbb_enabled: "false",
    openbb_base_url: "http://127.0.0.1:6900",
    openbb_auto_start: "1",
    cn_market_data_provider_order: "mootdx,local_cache,akshare,tushare,baostock",
    cn_market_mootdx_enabled: "true",
    cn_market_tencent_enabled: "true",
    cn_market_akshare_enabled: "true",
    cn_market_tushare_enabled: "true",
    cn_market_baostock_enabled: "true",
    tushare_token: "",
  });

  const user = me?.user || {};
  const ownerId = String(user.username || "").trim().toLowerCase();
  const isOwnerBound = Boolean(ownerId);
  const plan = normalizePlan(user.plan);
  const role = String(user.role || "user").trim().toLowerCase();
  const isAdmin = Boolean(user.is_admin) || role === "admin" || role === "owner";
  const apiKeyRequired = isAdmin || plan === "pro" || plan === "premium";
  const agentOnline = Boolean(agentStatus?.ok || agentStatus?.agent || agentStatus?.version);
  const missingStepKeys = useMemo(
    () =>
      getMissingOnboardingStepKeys({
        ownerBound: isOwnerBound,
        snapshot,
        apiKeyRequired,
        done,
      }),
    [apiKeyRequired, done, isOwnerBound, snapshot]
  );
  const steps = useMemo(
    () => ONBOARDING_STEPS.filter((step) => missingStepKeys.includes(step.key)),
    [missingStepKeys]
  );
  const currentStep = steps[stepIndex] || steps[0] || ONBOARDING_STEPS[0];
  const hasVisibleSteps = steps.length > 0;
  const totalVisibleSteps = Math.max(1, steps.length);
  const progressPct = Math.round(((Math.min(stepIndex, totalVisibleSteps - 1) + 1) / totalVisibleSteps) * 100);
  const displayTitle = loading ? "正在检查配置" : hasVisibleSteps ? currentStep.title : "配置已完成";
  const currentDescription = loading
    ? "正在读取当前本地 owner 的配置完整度。"
    : hasVisibleSteps
      ? ONBOARDING_STEP_DESCRIPTIONS[currentStep.key]
      : "当前账号没有待补充的配置步骤，可以进入系统。";

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      const token = getAuthToken();
      if (!token) {
        router.replace("/auth");
        return;
      }
      setLoading(true);
      setError("");
      try {
        const authResp = await localAgentGet<AuthMeResponse>("/auth/me", {
          headers: authHeaders(token),
          cacheTtlMs: 0,
          retries: 0,
          timeoutMs: 5000,
        });
        if (cancelled) return;
        setMe(authResp);
        const nextUser = authResp.user || {};
        const nextOwner = String(nextUser.username || "").trim().toLowerCase();
        const nextRole = String(nextUser.role || "user").trim().toLowerCase();
        const nextIsAdmin = Boolean(nextUser.is_admin) || nextRole === "admin" || nextRole === "owner";
        setLocalAgentCloudIdentity({
          ownerId: nextOwner,
          plan: nextIsAdmin ? "premium" : normalizePlan(nextUser.plan),
          role: nextRole,
          isAdmin: nextIsAdmin,
        });
        const nextPlan = normalizePlan(nextUser.plan);
        const nextApiKeyRequired = nextIsAdmin || nextPlan === "pro" || nextPlan === "premium";
        const [health, snap] = await Promise.allSettled([
          localAgentHealth(),
          loadLocalOnboardingSnapshot(nextApiKeyRequired),
        ]);
        if (cancelled) return;
        setAgentStatus(health.status === "fulfilled" ? health.value : null);
        setSnapshot(snap.status === "fulfilled" ? snap.value : null);
      } catch (err: any) {
        if (!cancelled) {
          setError(String(err?.message || err));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [router]);

  useEffect(() => {
    setStepIndex((idx) => Math.min(idx, Math.max(0, steps.length - 1)));
  }, [steps.length]);

  const markDone = useCallback((key: StepKey) => {
    setDone((prev) => ({ ...prev, [key]: true }));
  }, []);

  const saveConfig = useCallback(async (payload: Record<string, string>) => {
    const compact = Object.fromEntries(
      Object.entries(payload).filter(([, value]) => String(value || "").trim()).map(([key, value]) => [key, String(value).trim()])
    );
    if (!Object.keys(compact).length) return { skipped: true };
    await localAgentPost("/setup/config", compact, { retries: 0, timeoutMs: 30000 });
    return { skipped: false };
  }, []);

  const saveBroker = async () => {
    if (!brokerForm.account_id.trim()) {
      markDone("broker");
      setMessage("已跳过券商 API，稍后可在 Setup 页面补充。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const payload: Record<string, any> = {
        broker_provider: brokerForm.broker_provider,
        account_id: brokerForm.account_id.trim(),
      };
      if (brokerForm.broker_provider === "longbridge") {
        if (brokerForm.longport_app_key.trim()) payload.longport_app_key = brokerForm.longport_app_key.trim();
        if (brokerForm.longport_app_secret.trim()) payload.longport_app_secret = brokerForm.longport_app_secret.trim();
        if (brokerForm.longport_access_token.trim()) payload.longport_access_token = brokerForm.longport_access_token.trim();
      } else {
        payload.credentials = {
          tiger_id: brokerForm.tiger_id.trim(),
          tiger_account: brokerForm.tiger_account.trim(),
          tiger_license: brokerForm.tiger_license.trim(),
          token_path: brokerForm.tiger_token_path.trim(),
        };
      }
      await localAgentPost("/setup/accounts/register", payload, { retries: 0, timeoutMs: 30000 });
      markDone("broker");
      setMessage("券商账户已保存。");
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setSaving(false);
    }
  };

  const saveCurrentStep = async () => {
    setMessage("");
    if (currentStep.key === "owner") {
      if (!isOwnerBound) {
        setError("请先登录本地账号。");
        return;
      }
      markDone("owner");
      return;
    }
    if (currentStep.key === "broker") {
      await saveBroker();
      return;
    }
    if (currentStep.key === "apiKey") {
      if (apiKeyRequired && !apiKeyConfirmed) {
        setError("Pro / Premium / Admin 账号需要确认个人 API Key 已创建并应用。");
        return;
      }
      markDone("apiKey");
      router.replace(setupHref);
      return;
    }
    setSaving(true);
    setError("");
    try {
      if (currentStep.key === "llm") await saveConfig(llmForm);
      if (currentStep.key === "feishu") await saveConfig(feishuForm);
      if (currentStep.key === "market") await saveConfig(marketForm);
      if (currentStep.key === "notify") await saveConfig(notifyForm);
      if (currentStep.key === "research") await saveConfig(researchForm);
      markDone(currentStep.key);
      setMessage(`${currentStep.title} 已处理。`);
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setSaving(false);
    }
  };

  const skipStep = () => {
    if (!currentStep.skippable) return;
    markDone(currentStep.key);
    setMessage(`${currentStep.title} 已跳过，稍后可在 Setup 页面补充。`);
    setError("");
  };

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center overflow-hidden bg-slate-950/90 p-4 text-slate-100 backdrop-blur-xl sm:p-6">
      <section className="flex h-full max-h-[860px] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-cyan-300/25 bg-slate-950/95 shadow-2xl shadow-cyan-950/30 lg:flex-row">
        <aside className="flex min-h-0 shrink-0 flex-col border-b border-slate-700/70 bg-slate-950/85 p-5 lg:w-80 lg:border-b-0 lg:border-r">
          <div>
            <div className="text-sm font-semibold text-cyan-200">MultiTrading Local Agent</div>
            <h1 className="mt-2 text-2xl font-semibold text-slate-50">本地配置向导</h1>
            <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-800">
              <div className="h-full rounded-full bg-cyan-300 transition-all" style={{ width: `${progressPct}%` }} />
            </div>
            <div className="mt-2 text-xs text-slate-500">
              {hasVisibleSteps ? `第 ${Math.min(stepIndex + 1, totalVisibleSteps)} / ${totalVisibleSteps} 步` : "无需补充步骤"}
            </div>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-2 text-xs lg:grid-cols-1">
            <Badge tone={agentOnline ? "ok" : "warn"}>{agentOnline ? "Local Agent 在线" : "Local Agent 待确认"}</Badge>
            <Badge tone={isOwnerBound ? "ok" : "warn"}>{isOwnerBound ? `owner: ${ownerId}` : "未登录 owner"}</Badge>
            <Badge tone={isAdmin || plan === "premium" ? "ok" : plan === "pro" ? "warn" : "neutral"}>{isAdmin ? "Premium / Admin" : PLAN_LABELS[plan]}</Badge>
          </div>

          <div className="mt-5 min-h-0 flex-1 overflow-y-auto pr-1">
            <div className="mb-3">
              <div className="truncate text-sm font-semibold text-slate-100">{ownerId || "本地账号"}</div>
              <div className="mt-1 truncate text-xs text-slate-500">本地优先模式</div>
            </div>
            <div className="grid gap-2">
              {loading ? <div className="rounded-xl border border-slate-700/70 bg-slate-950/35 p-3 text-sm text-slate-400">正在检查配置...</div> : null}
              {!loading && !hasVisibleSteps ? <div className="rounded-xl border border-emerald-300/25 bg-emerald-400/10 p-3 text-sm text-emerald-100">当前配置已完成</div> : null}
              {steps.map((step, index) => (
                <StepButton
                  key={step.key}
                  active={index === stepIndex}
                  done={Boolean(done[step.key])}
                  index={index}
                  title={step.title}
                  onClick={() => {
                    setStepIndex(index);
                    setError("");
                    setMessage("");
                  }}
                />
              ))}
            </div>
          </div>

          <div className="mt-4 border-t border-slate-700/70 pt-4 text-xs text-slate-500">
            <div className="truncate">{LOCAL_AGENT_API_BASE}</div>
            <Link className="mt-3 inline-flex text-slate-300 hover:text-cyan-200" href={setupHref}>
              进入 Setup 页面
            </Link>
          </div>
        </aside>

        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="border-b border-slate-700/70 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-cyan-950/20 p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">当前配置</div>
                <h2 className="mt-1 text-2xl font-semibold text-slate-50">{displayTitle}</h2>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">{currentDescription}</p>
              </div>
              {hasVisibleSteps ? currentStep.skippable ? <Badge>可跳过</Badge> : <Badge tone="warn">必填</Badge> : null}
            </div>
          </div>

          {(message || error) ? (
            <div className="space-y-3 border-b border-slate-800/80 p-4">
              {message ? <div className="rounded-xl border border-emerald-300/25 bg-emerald-400/10 p-3 text-sm text-emerald-100">{message}</div> : null}
              {error ? <div className="rounded-xl border border-rose-300/30 bg-rose-400/10 p-3 text-sm text-rose-100">{error}</div> : null}
            </div>
          ) : null}

          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            <div className="mx-auto max-w-4xl">
              {loading ? (
                <div className="rounded-xl border border-slate-700/70 bg-slate-950/35 p-6 text-sm leading-6 text-slate-300">正在检查本地配置...</div>
              ) : !hasVisibleSteps ? (
                <div className="rounded-xl border border-emerald-300/25 bg-emerald-400/10 p-6 text-sm leading-6 text-emerald-100">
                  当前账号没有待补充的配置步骤，可以直接进入系统。
                </div>
              ) : (
                <>
                  {currentStep.key === "broker" ? (
                    <div className="grid gap-4">
                      <div className="grid gap-4 md:grid-cols-2">
                        <Field label="券商">
                          <select className="input-base" value={brokerForm.broker_provider} onChange={(e) => setBrokerForm((s) => ({ ...s, broker_provider: e.target.value }))}>
                            <option value="longbridge">Longbridge</option>
                            <option value="tiger">Tiger</option>
                          </select>
                        </Field>
                        <Field label="账户 ID">
                          <input className="input-base" value={brokerForm.account_id} onChange={(e) => setBrokerForm((s) => ({ ...s, account_id: e.target.value }))} placeholder="例如 main 或 longbridge-main" />
                        </Field>
                      </div>
                      {brokerForm.broker_provider === "longbridge" ? (
                        <div className="grid gap-3 md:grid-cols-3">
                          <input className="input-base" value={brokerForm.longport_app_key} onChange={(e) => setBrokerForm((s) => ({ ...s, longport_app_key: e.target.value }))} placeholder="LONGPORT_APP_KEY" />
                          <input className="input-base" type="password" value={brokerForm.longport_app_secret} onChange={(e) => setBrokerForm((s) => ({ ...s, longport_app_secret: e.target.value }))} placeholder="LONGPORT_APP_SECRET" />
                          <input className="input-base" type="password" value={brokerForm.longport_access_token} onChange={(e) => setBrokerForm((s) => ({ ...s, longport_access_token: e.target.value }))} placeholder="LONGPORT_ACCESS_TOKEN" />
                        </div>
                      ) : (
                        <div className="grid gap-3 md:grid-cols-2">
                          <input className="input-base" value={brokerForm.tiger_id} onChange={(e) => setBrokerForm((s) => ({ ...s, tiger_id: e.target.value }))} placeholder="TIGER_ID" />
                          <input className="input-base" value={brokerForm.tiger_account} onChange={(e) => setBrokerForm((s) => ({ ...s, tiger_account: e.target.value }))} placeholder="TIGER_ACCOUNT" />
                          <input className="input-base" type="password" value={brokerForm.tiger_license} onChange={(e) => setBrokerForm((s) => ({ ...s, tiger_license: e.target.value }))} placeholder="TIGER_LICENSE" />
                          <input className="input-base" value={brokerForm.tiger_token_path} onChange={(e) => setBrokerForm((s) => ({ ...s, tiger_token_path: e.target.value }))} placeholder="token_path" />
                        </div>
                      )}
                    </div>
                  ) : null}

                  {currentStep.key === "llm" ? (
                    <div className="grid gap-4 md:grid-cols-2">
                      <Field label="Provider">
                        <select className="input-base" value={llmForm.tradingagents_llm_provider} onChange={(e) => setLlmForm((s) => ({ ...s, tradingagents_llm_provider: e.target.value }))}>
                          {["openai", "anthropic", "google", "deepseek", "openrouter", "qwen", "glm", "azure", "ollama"].map((x) => <option key={x} value={x}>{x}</option>)}
                        </select>
                      </Field>
                      <Field label="API Key">
                        <input className="input-base" type="password" value={llmForm.llm_api_key} onChange={(e) => setLlmForm((s) => ({ ...s, llm_api_key: e.target.value }))} placeholder="留空则跳过" />
                      </Field>
                      <Field label="Deep Think Model">
                        <input className="input-base" value={llmForm.tradingagents_deep_think_llm} onChange={(e) => setLlmForm((s) => ({ ...s, tradingagents_deep_think_llm: e.target.value }))} />
                      </Field>
                      <Field label="Quick Think Model">
                        <input className="input-base" value={llmForm.tradingagents_quick_think_llm} onChange={(e) => setLlmForm((s) => ({ ...s, tradingagents_quick_think_llm: e.target.value }))} />
                      </Field>
                      <Field label="Azure Endpoint">
                        <input className="input-base" value={llmForm.azure_openai_endpoint} onChange={(e) => setLlmForm((s) => ({ ...s, azure_openai_endpoint: e.target.value }))} placeholder="仅 Azure 需要" />
                      </Field>
                    </div>
                  ) : null}

                  {currentStep.key === "feishu" ? (
                    <div className="grid gap-4 md:grid-cols-2">
                      <Field label="FEISHU_APP_ID"><input className="input-base" value={feishuForm.feishu_app_id} onChange={(e) => setFeishuForm((s) => ({ ...s, feishu_app_id: e.target.value }))} /></Field>
                      <Field label="FEISHU_APP_SECRET"><input className="input-base" type="password" value={feishuForm.feishu_app_secret} onChange={(e) => setFeishuForm((s) => ({ ...s, feishu_app_secret: e.target.value }))} /></Field>
                      <Field label="FEISHU_SCHEDULED_CHAT_ID"><input className="input-base md:col-span-2" value={feishuForm.feishu_scheduled_chat_id} onChange={(e) => setFeishuForm((s) => ({ ...s, feishu_scheduled_chat_id: e.target.value }))} /></Field>
                    </div>
                  ) : null}

                  {currentStep.key === "market" ? (
                    <div className="grid gap-4 md:grid-cols-2">
                      {Object.keys(marketForm).map((key) => (
                        <Field key={key} label={key.toUpperCase()}>
                          <input className="input-base" type="password" value={(marketForm as any)[key]} onChange={(e) => setMarketForm((s) => ({ ...s, [key]: e.target.value }))} />
                        </Field>
                      ))}
                    </div>
                  ) : null}

                  {currentStep.key === "notify" ? (
                    <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
                      <div className="rounded-xl border border-slate-700/70 bg-slate-950/35 p-4 text-sm leading-6 text-slate-300">
                        <div className="font-semibold text-slate-100">MCP 工具安全等级</div>
                        <div className="mt-2 text-slate-400">L1/L2 为常规权限；L3 用于更高风险的本地工具调用，需要额外确认。</div>
                      </div>
                      <div className="grid gap-4 md:grid-cols-2">
                        <Field label="MCP 最高等级">
                          <select className="input-base" value={notifyForm.openclaw_mcp_max_level} onChange={(e) => setNotifyForm((s) => ({ ...s, openclaw_mcp_max_level: e.target.value }))}>
                            <option value="L1">L1</option><option value="L2">L2</option><option value="L3">L3</option>
                          </select>
                        </Field>
                        <Field label="允许 L3">
                          <select className="input-base" value={notifyForm.openclaw_mcp_allow_l3} onChange={(e) => setNotifyForm((s) => ({ ...s, openclaw_mcp_allow_l3: e.target.value }))}>
                            <option value="false">false</option><option value="true">true</option>
                          </select>
                        </Field>
                        <Field label="L3 Confirmation Token">
                          <input className="input-base md:col-span-2" type="password" value={notifyForm.openclaw_mcp_l3_confirmation_token} onChange={(e) => setNotifyForm((s) => ({ ...s, openclaw_mcp_l3_confirmation_token: e.target.value }))} />
                        </Field>
                      </div>
                    </div>
                  ) : null}

                  {currentStep.key === "research" ? (
                    <div className="grid gap-4 md:grid-cols-2">
                      <Field label="OpenBB 启用">
                        <select className="input-base" value={researchForm.openbb_enabled} onChange={(e) => setResearchForm((s) => ({ ...s, openbb_enabled: e.target.value }))}>
                          <option value="false">false</option><option value="true">true</option>
                        </select>
                      </Field>
                      <Field label="OpenBB Base URL"><input className="input-base" value={researchForm.openbb_base_url} onChange={(e) => setResearchForm((s) => ({ ...s, openbb_base_url: e.target.value }))} /></Field>
                      <Field label="OpenBB Auto Start">
                        <select className="input-base" value={researchForm.openbb_auto_start} onChange={(e) => setResearchForm((s) => ({ ...s, openbb_auto_start: e.target.value }))}>
                          <option value="1">1</option><option value="0">0</option>
                        </select>
                      </Field>
                      <Field label="A股数据源优先级"><input className="input-base" value={researchForm.cn_market_data_provider_order} onChange={(e) => setResearchForm((s) => ({ ...s, cn_market_data_provider_order: e.target.value }))} /></Field>
                      <Field label="mootdx 启用"><select className="input-base" value={researchForm.cn_market_mootdx_enabled} onChange={(e) => setResearchForm((s) => ({ ...s, cn_market_mootdx_enabled: e.target.value }))}><option value="true">true</option><option value="false">false</option></select></Field>
                      <Field label="腾讯估值启用"><select className="input-base" value={researchForm.cn_market_tencent_enabled} onChange={(e) => setResearchForm((s) => ({ ...s, cn_market_tencent_enabled: e.target.value }))}><option value="true">true</option><option value="false">false</option></select></Field>
                      <Field label="AkShare 启用"><select className="input-base" value={researchForm.cn_market_akshare_enabled} onChange={(e) => setResearchForm((s) => ({ ...s, cn_market_akshare_enabled: e.target.value }))}><option value="true">true</option><option value="false">false</option></select></Field>
                      <Field label="Tushare 启用"><select className="input-base" value={researchForm.cn_market_tushare_enabled} onChange={(e) => setResearchForm((s) => ({ ...s, cn_market_tushare_enabled: e.target.value }))}><option value="true">true</option><option value="false">false</option></select></Field>
                      <Field label="BaoStock 启用"><select className="input-base" value={researchForm.cn_market_baostock_enabled} onChange={(e) => setResearchForm((s) => ({ ...s, cn_market_baostock_enabled: e.target.value }))}><option value="true">true</option><option value="false">false</option></select></Field>
                      <Field label="TUSHARE_TOKEN"><input className="input-base" type="password" value={researchForm.tushare_token} onChange={(e) => setResearchForm((s) => ({ ...s, tushare_token: e.target.value }))} /></Field>
                    </div>
                  ) : null}

                  {currentStep.key === "apiKey" ? (
                    <div className="space-y-4">
                      <div className="rounded-xl border border-slate-700/70 bg-slate-950/35 p-4 text-sm leading-6 text-slate-300">
                        个人 API Key 用于本机 Worker / 脚本调用 Local Agent。{apiKeyRequired ? "当前账号需要确认 API Key 后再进入系统。" : "Free 用户可以先跳过。"}
                      </div>
                      <SetupApiKeysPanel />
                      {apiKeyRequired ? (
                        <label className="flex items-center gap-3 rounded-xl border border-amber-300/25 bg-amber-400/10 p-4 text-sm text-amber-100">
                          <input type="checkbox" checked={apiKeyConfirmed} onChange={(e) => setApiKeyConfirmed(e.target.checked)} />
                          我已创建个人 API Key，并按需应用到自动交易 Worker。
                        </label>
                      ) : null}
                    </div>
                  ) : null}
                </>
              )}
            </div>
          </div>

          <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-slate-700/70 bg-slate-950/90 p-4">
            <div className="text-xs text-slate-500">
              {hasVisibleSteps ? "跳过不会丢失进度，可以稍后补充。" : "没有待补充步骤。"}
            </div>
            <div className="flex flex-wrap gap-2">
              {stepIndex > 0 && hasVisibleSteps ? <button type="button" className="btn-secondary" onClick={() => setStepIndex((x) => Math.max(0, x - 1))}>上一步</button> : null}
              {hasVisibleSteps && currentStep.skippable ? <button type="button" className="btn-secondary" onClick={skipStep} disabled={saving}>跳过</button> : null}
              {hasVisibleSteps ? (
                <button type="button" className="btn-primary" onClick={() => void saveCurrentStep()} disabled={saving || loading}>
                  {saving ? "保存中..." : currentStep.key === "apiKey" ? "完成并进入系统" : "保存并继续"}
                </button>
              ) : (
                <button type="button" className="btn-primary" onClick={() => router.replace(setupHref)}>进入系统</button>
              )}
            </div>
          </div>
        </main>
      </section>
    </div>
  );
}

function CloudAgentOnboardingPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextPath = String(searchParams?.get("next") || "/setup");
  const setupHref = nextPath.startsWith("/") && !nextPath.startsWith("//") ? nextPath : "/setup";
  const { user } = useUser();
  const cloudSession = useCloudSession();
  const selfBindLocalOwner = useMutation(convexFunctions.users.selfBindLocalOwner);
  const completeOnboarding = useMutation(convexFunctions.users.completeOnboarding);
  const email = user?.primaryEmailAddress?.emailAddress || user?.emailAddresses?.[0]?.emailAddress || "";
  const displayName = user?.fullName || user?.username || email || "新用户";
  const localBinding = getLocalOwnerBinding(email);
  const cloudOwnerId = activeLocalOwnerId(cloudSession.data);
  const initialOwnerId = useMemo(() => suggestOwnerId(email, user?.id || ""), [email, user?.id]);
  const [boundOwnerId, setBoundOwnerId] = useState("");
  const ownerId = cloudOwnerId || boundOwnerId || localBinding.ownerId;
  const isOwnerBound = Boolean(ownerId);
  const plan = normalizePlan(cloudSession.data?.subscription?.plan || localBinding.plan);
  const role = String(cloudSession.data?.user?.role || localBinding.role || "user");
  const isAdmin = Boolean(cloudSession.data?.user?.isAdmin || localBinding.isAdmin);
  const apiKeyRequired = isAdmin || plan === "pro" || plan === "premium";

  const [stepIndex, setStepIndex] = useState(0);
  const [done, setDone] = useState<Record<StepKey, boolean>>({
    owner: false,
    broker: false,
    llm: false,
    feishu: false,
    market: false,
    notify: false,
    research: false,
    apiKey: false,
  });
  const [ownerDraft, setOwnerDraft] = useState(initialOwnerId);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [agentStatus, setAgentStatus] = useState<LocalAgentStatus | null>(null);
  const [snapshot, setSnapshot] = useState<OnboardingSnapshot | null>(null);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [snapshotChecked, setSnapshotChecked] = useState(false);
  const [apiKeyConfirmed, setApiKeyConfirmed] = useState(false);

  const [brokerForm, setBrokerForm] = useState({
    broker_provider: "longbridge",
    account_id: "",
    longport_app_key: "",
    longport_app_secret: "",
    longport_access_token: "",
    tiger_id: "",
    tiger_account: "",
    tiger_license: "",
    tiger_token_path: "",
  });
  const [llmForm, setLlmForm] = useState({
    tradingagents_llm_provider: "openai",
    tradingagents_deep_think_llm: "gpt-4o",
    tradingagents_quick_think_llm: "gpt-4o-mini",
    llm_api_key: "",
    azure_openai_endpoint: "",
  });
  const [feishuForm, setFeishuForm] = useState({ feishu_app_id: "", feishu_app_secret: "", feishu_scheduled_chat_id: "" });
  const [marketForm, setMarketForm] = useState({ finnhub_api_key: "", tiingo_api_key: "", fred_api_key: "", coingecko_api_key: "" });
  const [notifyForm, setNotifyForm] = useState({ openclaw_mcp_max_level: "L2", openclaw_mcp_allow_l3: "false", openclaw_mcp_l3_confirmation_token: "" });
  const [researchForm, setResearchForm] = useState({
    openbb_enabled: "false",
    openbb_base_url: "http://127.0.0.1:6900",
    openbb_auto_start: "1",
    cn_market_data_provider_order: "mootdx,local_cache,akshare,tushare,baostock",
    cn_market_mootdx_enabled: "true",
    cn_market_tencent_enabled: "true",
    cn_market_akshare_enabled: "true",
    cn_market_tushare_enabled: "true",
    cn_market_baostock_enabled: "true",
    tushare_token: "",
  });

  useEffect(() => setOwnerDraft((prev) => prev || initialOwnerId), [initialOwnerId]);

  useEffect(() => {
    if (!isOwnerBound) return;
    setDone((prev) => ({ ...prev, owner: true }));
    setStepIndex((idx) => (idx === 0 ? 1 : idx));
    setLocalAgentCloudIdentity({
      email,
      ownerId,
      plan,
      role,
      isAdmin,
    });
  }, [email, isAdmin, isOwnerBound, ownerId, plan, role]);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      try {
        const status = await localAgentHealth();
        if (!cancelled) setAgentStatus(status);
      } catch {
        if (!cancelled) setAgentStatus(null);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  const refreshSnapshot = useCallback(async () => {
    if (!isOwnerBound) {
      setSnapshot(null);
      setSnapshotChecked(false);
      return;
    }
    setSnapshotLoading(true);
    setSnapshotChecked(false);
    try {
      setSnapshot(await loadLocalOnboardingSnapshot(apiKeyRequired));
    } catch {
      setSnapshot(null);
    } finally {
      setSnapshotLoading(false);
      setSnapshotChecked(true);
    }
  }, [apiKeyRequired, isOwnerBound]);

  useEffect(() => {
    void refreshSnapshot();
  }, [refreshSnapshot]);

  const visibleStepKeys = useMemo(
    () => {
      if (isOwnerBound && !snapshotChecked) return [];
      return getMissingOnboardingStepKeys({
        ownerBound: isOwnerBound,
        snapshot,
        apiKeyRequired,
        done,
      });
    },
    [apiKeyRequired, done, isOwnerBound, snapshot, snapshotChecked]
  );
  const steps = useMemo(
    () => ONBOARDING_STEPS.filter((step) => visibleStepKeys.includes(step.key)),
    [visibleStepKeys]
  );
  const currentStep = steps[stepIndex] || steps[0] || ONBOARDING_STEPS[0];
  const agentOnline = Boolean(agentStatus?.ok || agentStatus?.agent || agentStatus?.version);

  useEffect(() => {
    setStepIndex((idx) => Math.min(idx, Math.max(0, steps.length - 1)));
  }, [steps.length]);

  useEffect(() => {
    if (!isOwnerBound || snapshotLoading || !snapshotChecked || !snapshot || steps.length > 0) return;
    void completeOnboarding({}).finally(() => {
      router.replace(setupHref);
    });
  }, [completeOnboarding, isOwnerBound, router, setupHref, snapshot, snapshotChecked, snapshotLoading, steps.length]);

  const markDone = useCallback((key: StepKey) => {
    setDone((prev) => ({ ...prev, [key]: true }));
  }, []);

  const goNext = useCallback(() => {
    setError("");
    setMessage("");
  }, []);

  const saveConfig = useCallback(async (payload: Record<string, string>) => {
    const compact = Object.fromEntries(
      Object.entries(payload).filter(([, value]) => String(value || "").trim()).map(([key, value]) => [key, String(value).trim()])
    );
    if (!Object.keys(compact).length) return { skipped: true };
    await localAgentPost("/setup/config", compact, { retries: 0, timeoutMs: 30000 });
    return { skipped: false };
  }, []);

  const saveOwner = async () => {
    const owner = normalizeOwnerId(ownerDraft);
    setOwnerDraft(owner);
    if (!validOwnerId(owner)) {
      setError("用户名需为 3-40 位小写字母、数字、下划线或短横线，并以字母或数字开头。");
      return;
    }
    if (cloudSession.status !== "success" || !cloudSession.data?.user) {
      setError("云端账号还在同步，请稍等几秒后再绑定。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await selfBindLocalOwner({ ownerId: owner });
      setBoundOwnerId(owner);
      setLocalAgentCloudIdentity({ email, ownerId: owner, plan, role, isAdmin });
      markDone("owner");
      setMessage(`已绑定本地 owner：${owner}`);
      goNext();
    } catch (err: any) {
      const msg = String(err?.message || err);
      setError(msg.includes("owner_already_bound") ? "这个用户名已经被其他云端账号绑定，请换一个。" : msg);
    } finally {
      setSaving(false);
    }
  };

  const skipStep = () => {
    if (!currentStep.skippable) return;
    markDone(currentStep.key);
    goNext();
  };

  const saveBroker = async () => {
    if (!brokerForm.account_id.trim()) {
      markDone("broker");
      goNext();
      return;
    }
    setSaving(true);
    setError("");
    try {
      const payload: Record<string, any> = {
        broker_provider: brokerForm.broker_provider,
        account_id: brokerForm.account_id.trim(),
      };
      if (brokerForm.broker_provider === "longbridge") {
        if (brokerForm.longport_app_key.trim()) payload.longport_app_key = brokerForm.longport_app_key.trim();
        if (brokerForm.longport_app_secret.trim()) payload.longport_app_secret = brokerForm.longport_app_secret.trim();
        if (brokerForm.longport_access_token.trim()) payload.longport_access_token = brokerForm.longport_access_token.trim();
      } else {
        payload.credentials = {
          tiger_id: brokerForm.tiger_id.trim(),
          tiger_account: brokerForm.tiger_account.trim(),
          tiger_license: brokerForm.tiger_license.trim(),
          token_path: brokerForm.tiger_token_path.trim(),
        };
      }
      await localAgentPost("/setup/accounts/register", payload, { retries: 0, timeoutMs: 30000 });
      markDone("broker");
      setMessage("券商账户已保存。");
      goNext();
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setSaving(false);
    }
  };

  const saveCurrentStep = async () => {
    if (currentStep.key === "owner") {
      await saveOwner();
      return;
    }
    if (!isOwnerBound) {
      setError("请先完成用户名绑定。");
      setStepIndex(0);
      return;
    }
    if (currentStep.key === "broker") {
      await saveBroker();
      return;
    }
    setSaving(true);
    setError("");
    try {
      if (currentStep.key === "llm") await saveConfig(llmForm);
      if (currentStep.key === "feishu") await saveConfig(feishuForm);
      if (currentStep.key === "market") await saveConfig(marketForm);
      if (currentStep.key === "notify") await saveConfig(notifyForm);
      if (currentStep.key === "research") await saveConfig(researchForm);
      markDone(currentStep.key);
      setMessage(`${currentStep.title} 已处理。`);
      goNext();
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setSaving(false);
    }
  };

  const finish = async () => {
    if (!isOwnerBound) {
      setStepIndex(0);
      setError("用户名是正式进入系统前的必填项。");
      return;
    }
    if (apiKeyRequired && !apiKeyConfirmed) {
      setStepIndex(7);
      setError("Pro / Premium / Admin 账号需要先确认个人 API Key 已创建并应用。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await completeOnboarding({});
      router.replace(setupHref);
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setSaving(false);
    }
  };

  const totalVisibleSteps = Math.max(1, steps.length);
  const progressPct = Math.round(((Math.min(stepIndex, totalVisibleSteps - 1) + 1) / totalVisibleSteps) * 100);
  const hasVisibleSteps = steps.length > 0;
  const checkingConfig = isOwnerBound && !snapshotChecked;
  const displayTitle = hasVisibleSteps ? currentStep.title : checkingConfig ? "正在检查配置" : "配置已完成";
  const currentDescription = hasVisibleSteps
    ? ONBOARDING_STEP_DESCRIPTIONS[currentStep.key]
    : checkingConfig
      ? "正在读取当前 owner 的本地配置，只保留还没有完成的步骤。"
      : "当前账户没有待补充的配置步骤，正在进入系统。";

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center overflow-hidden bg-slate-950/90 p-4 backdrop-blur-xl sm:p-6">
      <section className="flex h-full max-h-[860px] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-cyan-300/25 bg-slate-950/95 shadow-2xl shadow-cyan-950/30 lg:flex-row">
        <aside className="flex min-h-0 shrink-0 flex-col border-b border-slate-700/70 bg-slate-950/85 p-5 lg:w-80 lg:border-b-0 lg:border-r">
          <div>
            <div className="text-sm font-semibold text-cyan-200">MultiTrading SaaS</div>
            <h1 className="mt-2 text-2xl font-semibold text-slate-50">首次配置向导</h1>
            <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-800">
              <div className="h-full rounded-full bg-cyan-300 transition-all" style={{ width: `${progressPct}%` }} />
            </div>
            <div className="mt-2 text-xs text-slate-500">
              {hasVisibleSteps ? `第 ${Math.min(stepIndex + 1, totalVisibleSteps)} / ${totalVisibleSteps} 步` : "无需补充步骤"}
            </div>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-2 text-xs lg:grid-cols-1">
            <Badge tone={agentOnline ? "ok" : "warn"}>{agentOnline ? "Local Agent 在线" : "Local Agent 待确认"}</Badge>
            <Badge tone={isOwnerBound ? "ok" : "warn"}>{isOwnerBound ? `owner: ${ownerId}` : "未绑定 owner"}</Badge>
            <Badge tone={plan === "premium" ? "ok" : plan === "pro" ? "warn" : "neutral"}>{PLAN_LABELS[plan]}</Badge>
          </div>

          <div className="mt-5 min-h-0 flex-1 overflow-y-auto pr-1">
            <div className="mb-3">
              <div className="truncate text-sm font-semibold text-slate-100">{displayName}</div>
              <div className="mt-1 truncate text-xs text-slate-500">{email || "等待 Clerk 登录"}</div>
            </div>
            <div className="grid gap-2">
              {steps.map((step, index) => (
                <StepButton
                  key={step.key}
                  active={index === stepIndex}
                  done={Boolean(done[step.key])}
                  index={index}
                  title={step.title}
                  onClick={() => {
                    if (index > 0 && !isOwnerBound) return;
                    setStepIndex(index);
                    setError("");
                    setMessage("");
                  }}
                />
              ))}
            </div>
          </div>

          <div className="mt-4 border-t border-slate-700/70 pt-4 text-xs text-slate-500">
            <div className="truncate">{LOCAL_AGENT_API_BASE}</div>
            <Link className="mt-3 inline-flex text-slate-300 hover:text-cyan-200" href={setupHref}>
              已完成配置，进入 Setup
            </Link>
          </div>
        </aside>

        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="border-b border-slate-700/70 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-cyan-950/20 p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">当前配置</div>
                <h2 className="mt-1 text-2xl font-semibold text-slate-50">{displayTitle}</h2>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">{currentDescription}</p>
              </div>
              {hasVisibleSteps ? currentStep.skippable ? <Badge>可跳过</Badge> : <Badge tone="warn">必填</Badge> : null}
            </div>
          </div>

          {(message || error) ? (
            <div className="space-y-3 border-b border-slate-800/80 p-4">
              {message ? <div className="rounded-xl border border-emerald-300/25 bg-emerald-400/10 p-3 text-sm text-emerald-100">{message}</div> : null}
              {error ? <div className="rounded-xl border border-rose-300/30 bg-rose-400/10 p-3 text-sm text-rose-100">{error}</div> : null}
            </div>
          ) : null}

          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            <div className="mx-auto max-w-4xl">
              {!hasVisibleSteps ? (
                <div className="rounded-xl border border-slate-700/70 bg-slate-950/35 p-6 text-sm leading-6 text-slate-300">
                  {checkingConfig ? "正在检查本地配置完整度..." : "没有待补充步骤，正在进入系统。"}
                </div>
              ) : (
                <>
              {currentStep.key === "owner" ? (
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
                  <Field label="用户名 / local owner" hint="建议使用邮箱前缀。保存后，本地券商 Key、配置和历史记录都会按这个 owner 隔离。">
                    <input className="input-base" value={ownerDraft} onChange={(e) => setOwnerDraft(normalizeOwnerId(e.target.value))} placeholder="例如 weiyuan 或 davies1983" />
                  </Field>
                  <div className="rounded-xl border border-slate-700/70 bg-slate-950/35 p-4 text-sm leading-6 text-slate-300">
                    当前账号：<span className="text-slate-100">{email || "-"}</span>
                    <br />
                    云端状态：<span className="text-slate-100">{cloudSession.status === "success" ? "已连接" : cloudSession.status}</span>
                    <br />
                    已绑定 owner：<span className="text-cyan-100">{ownerId || "未绑定"}</span>
                  </div>
                </div>
              ) : null}

              {currentStep.key === "broker" ? (
                <div className="grid gap-4">
                  <div className="grid gap-4 md:grid-cols-2">
                    <Field label="券商">
                      <select className="input-base" value={brokerForm.broker_provider} onChange={(e) => setBrokerForm((s) => ({ ...s, broker_provider: e.target.value }))}>
                        <option value="longbridge">Longbridge</option>
                        <option value="tiger">Tiger</option>
                      </select>
                    </Field>
                    <Field label="账户 ID">
                      <input className="input-base" value={brokerForm.account_id} onChange={(e) => setBrokerForm((s) => ({ ...s, account_id: e.target.value }))} placeholder="例如 main 或 longbridge-main" />
                    </Field>
                  </div>
                  {brokerForm.broker_provider === "longbridge" ? (
                    <div className="grid gap-3 md:grid-cols-3">
                      <input className="input-base" value={brokerForm.longport_app_key} onChange={(e) => setBrokerForm((s) => ({ ...s, longport_app_key: e.target.value }))} placeholder="LONGPORT_APP_KEY" />
                      <input className="input-base" type="password" value={brokerForm.longport_app_secret} onChange={(e) => setBrokerForm((s) => ({ ...s, longport_app_secret: e.target.value }))} placeholder="LONGPORT_APP_SECRET" />
                      <input className="input-base" type="password" value={brokerForm.longport_access_token} onChange={(e) => setBrokerForm((s) => ({ ...s, longport_access_token: e.target.value }))} placeholder="LONGPORT_ACCESS_TOKEN" />
                    </div>
                  ) : (
                    <div className="grid gap-3 md:grid-cols-2">
                      <input className="input-base" value={brokerForm.tiger_id} onChange={(e) => setBrokerForm((s) => ({ ...s, tiger_id: e.target.value }))} placeholder="TIGER_ID" />
                      <input className="input-base" value={brokerForm.tiger_account} onChange={(e) => setBrokerForm((s) => ({ ...s, tiger_account: e.target.value }))} placeholder="TIGER_ACCOUNT" />
                      <input className="input-base" type="password" value={brokerForm.tiger_license} onChange={(e) => setBrokerForm((s) => ({ ...s, tiger_license: e.target.value }))} placeholder="TIGER_LICENSE" />
                      <input className="input-base" value={brokerForm.tiger_token_path} onChange={(e) => setBrokerForm((s) => ({ ...s, tiger_token_path: e.target.value }))} placeholder="token_path" />
                    </div>
                  )}
                </div>
              ) : null}

              {currentStep.key === "llm" ? (
                <div className="grid gap-4 md:grid-cols-2">
                  <Field label="Provider">
                    <select className="input-base" value={llmForm.tradingagents_llm_provider} onChange={(e) => setLlmForm((s) => ({ ...s, tradingagents_llm_provider: e.target.value }))}>
                      {["openai", "anthropic", "google", "deepseek", "openrouter", "qwen", "glm", "azure", "ollama"].map((x) => <option key={x} value={x}>{x}</option>)}
                    </select>
                  </Field>
                  <Field label="API Key">
                    <input className="input-base" type="password" value={llmForm.llm_api_key} onChange={(e) => setLlmForm((s) => ({ ...s, llm_api_key: e.target.value }))} placeholder="留空则跳过" />
                  </Field>
                  <Field label="Deep Think Model">
                    <input className="input-base" value={llmForm.tradingagents_deep_think_llm} onChange={(e) => setLlmForm((s) => ({ ...s, tradingagents_deep_think_llm: e.target.value }))} />
                  </Field>
                  <Field label="Quick Think Model">
                    <input className="input-base" value={llmForm.tradingagents_quick_think_llm} onChange={(e) => setLlmForm((s) => ({ ...s, tradingagents_quick_think_llm: e.target.value }))} />
                  </Field>
                  <Field label="Azure Endpoint">
                    <input className="input-base" value={llmForm.azure_openai_endpoint} onChange={(e) => setLlmForm((s) => ({ ...s, azure_openai_endpoint: e.target.value }))} placeholder="仅 Azure 需要" />
                  </Field>
                </div>
              ) : null}

              {currentStep.key === "feishu" ? (
                <div className="grid gap-4 md:grid-cols-2">
                  <Field label="FEISHU_APP_ID"><input className="input-base" value={feishuForm.feishu_app_id} onChange={(e) => setFeishuForm((s) => ({ ...s, feishu_app_id: e.target.value }))} /></Field>
                  <Field label="FEISHU_APP_SECRET"><input className="input-base" type="password" value={feishuForm.feishu_app_secret} onChange={(e) => setFeishuForm((s) => ({ ...s, feishu_app_secret: e.target.value }))} /></Field>
                  <Field label="FEISHU_SCHEDULED_CHAT_ID"><input className="input-base md:col-span-2" value={feishuForm.feishu_scheduled_chat_id} onChange={(e) => setFeishuForm((s) => ({ ...s, feishu_scheduled_chat_id: e.target.value }))} /></Field>
                </div>
              ) : null}

              {currentStep.key === "market" ? (
                <div className="grid gap-4 md:grid-cols-2">
                  {Object.keys(marketForm).map((key) => (
                    <Field key={key} label={key.toUpperCase()}>
                      <input className="input-base" type="password" value={(marketForm as any)[key]} onChange={(e) => setMarketForm((s) => ({ ...s, [key]: e.target.value }))} />
                    </Field>
                  ))}
                </div>
              ) : null}

              {currentStep.key === "notify" ? (
                <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
                  <div className="rounded-xl border border-slate-700/70 bg-slate-950/35 p-4 text-sm leading-6 text-slate-300">
                    <div className="font-semibold text-slate-100">MCP 工具安全等级</div>
                    <div className="mt-2 text-slate-400">L1/L2 为常规权限；L3 用于更高风险的本地工具调用，需要额外确认。</div>
                    <div className="mt-4 flex gap-2">
                      <Badge tone="warn">默认 L2</Badge>
                      <Badge>可跳过</Badge>
                    </div>
                  </div>
                  <div className="grid gap-4 md:grid-cols-2">
                    <Field label="MCP 最高等级">
                      <select className="input-base" value={notifyForm.openclaw_mcp_max_level} onChange={(e) => setNotifyForm((s) => ({ ...s, openclaw_mcp_max_level: e.target.value }))}>
                        <option value="L1">L1</option><option value="L2">L2</option><option value="L3">L3</option>
                      </select>
                    </Field>
                    <Field label="允许 L3">
                      <select className="input-base" value={notifyForm.openclaw_mcp_allow_l3} onChange={(e) => setNotifyForm((s) => ({ ...s, openclaw_mcp_allow_l3: e.target.value }))}>
                        <option value="false">false</option><option value="true">true</option>
                      </select>
                    </Field>
                    <Field label="L3 Confirmation Token">
                      <input className="input-base md:col-span-2" type="password" value={notifyForm.openclaw_mcp_l3_confirmation_token} onChange={(e) => setNotifyForm((s) => ({ ...s, openclaw_mcp_l3_confirmation_token: e.target.value }))} />
                    </Field>
                  </div>
                </div>
              ) : null}

              {currentStep.key === "research" ? (
                <div className="grid gap-4 md:grid-cols-2">
                  <Field label="OpenBB 启用">
                    <select className="input-base" value={researchForm.openbb_enabled} onChange={(e) => setResearchForm((s) => ({ ...s, openbb_enabled: e.target.value }))}>
                      <option value="false">false</option><option value="true">true</option>
                    </select>
                  </Field>
                  <Field label="OpenBB Base URL"><input className="input-base" value={researchForm.openbb_base_url} onChange={(e) => setResearchForm((s) => ({ ...s, openbb_base_url: e.target.value }))} /></Field>
                  <Field label="OpenBB Auto Start">
                    <select className="input-base" value={researchForm.openbb_auto_start} onChange={(e) => setResearchForm((s) => ({ ...s, openbb_auto_start: e.target.value }))}>
                      <option value="1">1</option><option value="0">0</option>
                    </select>
                  </Field>
                  <Field label="A股数据源优先级"><input className="input-base" value={researchForm.cn_market_data_provider_order} onChange={(e) => setResearchForm((s) => ({ ...s, cn_market_data_provider_order: e.target.value }))} /></Field>
                  <Field label="mootdx 启用"><select className="input-base" value={researchForm.cn_market_mootdx_enabled} onChange={(e) => setResearchForm((s) => ({ ...s, cn_market_mootdx_enabled: e.target.value }))}><option value="true">true</option><option value="false">false</option></select></Field>
                  <Field label="腾讯估值启用"><select className="input-base" value={researchForm.cn_market_tencent_enabled} onChange={(e) => setResearchForm((s) => ({ ...s, cn_market_tencent_enabled: e.target.value }))}><option value="true">true</option><option value="false">false</option></select></Field>
                  <Field label="AkShare 启用"><select className="input-base" value={researchForm.cn_market_akshare_enabled} onChange={(e) => setResearchForm((s) => ({ ...s, cn_market_akshare_enabled: e.target.value }))}><option value="true">true</option><option value="false">false</option></select></Field>
                  <Field label="Tushare 启用"><select className="input-base" value={researchForm.cn_market_tushare_enabled} onChange={(e) => setResearchForm((s) => ({ ...s, cn_market_tushare_enabled: e.target.value }))}><option value="true">true</option><option value="false">false</option></select></Field>
                  <Field label="BaoStock 启用"><select className="input-base" value={researchForm.cn_market_baostock_enabled} onChange={(e) => setResearchForm((s) => ({ ...s, cn_market_baostock_enabled: e.target.value }))}><option value="true">true</option><option value="false">false</option></select></Field>
                  <Field label="TUSHARE_TOKEN"><input className="input-base" type="password" value={researchForm.tushare_token} onChange={(e) => setResearchForm((s) => ({ ...s, tushare_token: e.target.value }))} /></Field>
                </div>
              ) : null}

              {currentStep.key === "apiKey" ? (
                <div className="space-y-4">
                  <div className="rounded-xl border border-slate-700/70 bg-slate-950/35 p-4 text-sm leading-6 text-slate-300">
                    个人 API Key 用于本机 Worker / 脚本调用 Local Agent。{apiKeyRequired ? "当前账号是 Pro / Premium / Admin，这一步需要完成后才能正式进入系统。" : "Free 用户可以先跳过。"}
                  </div>
                  {isOwnerBound ? <SetupApiKeysPanel /> : <div className="text-sm text-amber-200">请先完成用户名绑定。</div>}
                  {apiKeyRequired ? (
                    <label className="flex items-center gap-3 rounded-xl border border-amber-300/25 bg-amber-400/10 p-4 text-sm text-amber-100">
                      <input type="checkbox" checked={apiKeyConfirmed} onChange={(e) => setApiKeyConfirmed(e.target.checked)} />
                      我已创建个人 API Key，并按需应用到股票 / 期权自动交易 Worker。
                    </label>
                  ) : null}
                </div>
              ) : null}
                </>
              )}
            </div>
          </div>

          <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-slate-700/70 bg-slate-950/90 p-4">
            <div className="text-xs text-slate-500">
              {hasVisibleSteps
                ? currentStep.key === "owner"
                  ? "用户名绑定后才能继续。"
                  : "跳过不会丢失进度，可以稍后补充。"
                : "没有待补充步骤。"}
            </div>
            <div className="flex flex-wrap gap-2">
              {!hasVisibleSteps ? (
                <Link className="btn-secondary" href={setupHref}>进入系统</Link>
              ) : (
                <>
                  {stepIndex > 0 ? <button type="button" className="btn-secondary" onClick={() => setStepIndex((x) => Math.max(0, x - 1))}>上一步</button> : null}
                  {currentStep.skippable ? <button type="button" className="btn-secondary" onClick={skipStep} disabled={saving}>跳过</button> : null}
                  {currentStep.key !== "apiKey" ? (
                    <button type="button" className="btn-primary" onClick={() => void saveCurrentStep()} disabled={saving}>
                      {saving ? "保存中..." : currentStep.key === "owner" ? "保存并绑定" : "保存并继续"}
                    </button>
                  ) : (
                    <button type="button" className="btn-primary" onClick={() => void finish()} disabled={saving || (apiKeyRequired && !apiKeyConfirmed)}>
                      {saving ? "完成中..." : "完成并进入系统"}
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        </main>
      </section>
    </div>
  );
}
