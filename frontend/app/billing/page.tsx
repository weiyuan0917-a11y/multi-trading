"use client";

import { useEffect, useMemo, useState } from "react";
import { PageShell } from "@/components/ui/page-shell";
import { cloudGet } from "@/lib/cloud-api";
import {
  PAYMENT_PROVIDER_IDS,
  PAYMENT_PROVIDERS,
  normalizePaymentMethod,
  normalizePaymentProvider,
  type PaymentProviderId,
  type PaymentMethod,
} from "@/lib/payment-providers";

type Plan = "pro" | "premium";
type BillingCycle = "month" | "year";

type AuthMeResponse = {
  ok?: boolean;
  user?: {
    username?: string;
    email?: string;
  };
};

type ManualOrder = {
  id: string;
  orderNo: string;
  email: string;
  ownerId: string;
  plan: Plan;
  billingCycle: BillingCycle;
  amountCny: number;
  amountHkd?: number;
  amount?: number;
  currency: "CNY" | "HKD";
  paymentMethod: PaymentMethod;
  paymentProvider?: PaymentProviderId;
  providerOrderId?: string;
  providerStatus?: string;
  payUrl?: string;
  qrCodeUrl?: string;
  expiresAt?: number | null;
  status: string;
  createdAt: number;
};

type ManualOrderResponse = {
  ok?: boolean;
  order?: ManualOrder;
  error?: string;
};

const PRICES: Record<Plan, Record<BillingCycle, number>> = {
  pro: { month: 99, year: 999 },
  premium: { month: 199, year: 1999 },
};

const PLAN_LABELS: Record<Plan, string> = {
  pro: "Pro",
  premium: "Premium",
};

const CYCLE_LABELS: Record<BillingCycle, string> = {
  month: "月付",
  year: "年付",
};

const PAYMENT_LABELS: Record<PaymentMethod, string> = {
  wechat: "微信",
  alipay: "支付宝",
  wise: "Wise",
  other: "其他方式",
};

const WECHAT_QR_URLS = [
  process.env.NEXT_PUBLIC_PAYMENT_QR_WECHAT_URL,
  "/payments/wechat-qr.jpg",
  "/payments/wechat-qr.png",
  "/payments/20260517-151434.png",
].filter(Boolean) as string[];
const ALIPAY_QR_URLS = [
  process.env.NEXT_PUBLIC_PAYMENT_QR_ALIPAY_URL,
  "/payments/alipay-qr.jpg",
  "/payments/alipay-qr.png",
  "/payments/20260517-151444.jpg",
].filter(Boolean) as string[];
const WISE_QR_URLS = [
  process.env.NEXT_PUBLIC_PAYMENT_QR_WISE_URL,
  "/payments/wise-qr.jpg",
  "/payments/wise-qr.png",
].filter(Boolean) as string[];

function priceLabel(plan: Plan, cycle: BillingCycle) {
  return `CNY ${PRICES[plan][cycle].toLocaleString("zh-CN")}`;
}

function moneyLabel(amount: number, currency: string = "CNY") {
  return `${currency || "CNY"} ${Number(amount || 0).toLocaleString("zh-CN")}`;
}

function formatTime(value?: number | null) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN");
}

function QrBox({ title, srcs, active }: { title: string; srcs: string[]; active: boolean }) {
  const [srcIndex, setSrcIndex] = useState(0);
  const src = srcs[srcIndex] || "";
  const failed = !src;
  useEffect(() => setSrcIndex(0), [srcs]);
  return (
    <div className={`rounded-2xl border p-4 ${active ? "border-cyan-400/45 bg-cyan-400/10" : "border-slate-700 bg-slate-950/35"}`}>
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="text-sm font-semibold text-slate-100">{title}</div>
        {active ? <span className="rounded-full border border-cyan-300/35 bg-cyan-400/10 px-2 py-0.5 text-xs text-cyan-100">当前选择</span> : null}
      </div>
      {!failed ? (
        <img
          src={src}
          alt={`${title}收款码`}
          className="aspect-square w-full rounded-xl border border-slate-700 bg-white object-contain p-2"
          onError={() => setSrcIndex((current) => current + 1)}
        />
      ) : (
        <div className="flex aspect-square w-full items-center justify-center rounded-xl border border-dashed border-slate-600 bg-slate-950/60 p-5 text-center text-sm leading-6 text-slate-400">
          收款码未配置。请把图片放到 frontend/public/payments，或设置对应的 NEXT_PUBLIC_PAYMENT_QR_*_URL。
        </div>
      )}
    </div>
  );
}

export default function BillingPage() {
  const [plan, setPlan] = useState<Plan>("pro");
  const [cycle, setCycle] = useState<BillingCycle>("month");
  const [paymentProvider, setPaymentProvider] = useState<PaymentProviderId>("manual_qr");
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>("wechat");
  const [email, setEmail] = useState("");
  const [ownerId, setOwnerId] = useState("");
  const [intent, setIntent] = useState("开通/续期");
  const [note, setNote] = useState("");
  const [order, setOrder] = useState<ManualOrder | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const amount = PRICES[plan][cycle];
  const paymentRemark = order ? `${order.orderNo} ${order.ownerId}` : `订单生成后显示`;
  const selectedProvider = PAYMENT_PROVIDERS[paymentProvider];
  const canSubmit = useMemo(() => {
    return Boolean(email.trim().includes("@") && /^[a-z0-9][a-z0-9_-]{2,39}$/.test(ownerId.trim().toLowerCase()));
  }, [email, ownerId]);

  useEffect(() => {
    setPaymentMethod((current) => normalizePaymentMethod(current, paymentProvider));
  }, [paymentProvider]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await cloudGet<AuthMeResponse>("/auth/me", { cacheTtlMs: 0, retries: 0, timeoutMs: 5000 });
        if (cancelled) return;
        if (!ownerId && data?.user?.username) setOwnerId(data.user.username);
        if (!email && data?.user?.email) setEmail(data.user.email);
      } catch {
        /* Prefill is best-effort. */
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [email, ownerId]);

  const createOrder = async () => {
    if (!canSubmit) {
      setError("请填写有效邮箱和本地 owner_id。");
      return;
    }
    setLoading(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch("/api/billing/manual-orders", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          email: email.trim(),
          ownerId: ownerId.trim().toLowerCase(),
          plan,
          billingCycle: cycle,
          paymentProvider,
          paymentMethod,
          customerNote: [intent, note.trim()].filter(Boolean).join("；"),
        }),
      });
      const data = (await response.json()) as ManualOrderResponse;
      if (!response.ok || data.error || !data.order) throw new Error(data.error || `request_${response.status}`);
      setOrder(data.order);
      setMessage("订单已生成。扫码付款时请尽量填写订单号备注，管理员确认到账后会自动发送 License 邮件。");
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setLoading(false);
    }
  };

  const copyRemark = async () => {
    if (!order) return;
    try {
      await navigator.clipboard.writeText(paymentRemark);
      setMessage("付款备注已复制。");
    } catch {
      setMessage("付款备注已显示，可手动复制。");
    }
  };

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-cyan-950/20">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-sm font-semibold text-cyan-200/80">MultiTrading Billing</div>
            <h1 className="mt-2 text-3xl font-bold text-slate-50">订购 / 升级</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              先生成订单，再扫码付款。管理员确认到账后，系统会自动签发本地 License 并发送到你的邮箱。
            </p>
          </div>
          <div className="rounded-2xl border border-slate-700 bg-slate-950/40 px-4 py-3 text-right">
            <div className="text-xs text-slate-500">当前应付</div>
            <div className="mt-1 text-3xl font-bold text-cyan-100">{moneyLabel(amount)}</div>
          </div>
        </div>
      </div>

      {message ? <div className="panel border-emerald-400/30 bg-emerald-400/10 text-emerald-100">{message}</div> : null}
      {error ? <div className="panel border-rose-400/35 bg-rose-400/10 text-rose-100">{error}</div> : null}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.62fr)]">
        <section className="panel space-y-5">
          <div>
            <div className="section-title">选择套餐</div>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              {(["pro", "premium"] as Plan[]).map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setPlan(item)}
                  className={`rounded-2xl border p-4 text-left transition ${
                    plan === item ? "border-cyan-400/55 bg-cyan-400/10" : "border-slate-700 bg-slate-950/35 hover:border-slate-500"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-lg font-bold text-slate-50">{PLAN_LABELS[item]}</div>
                      <div className="mt-1 text-sm leading-6 text-slate-400">
                        {item === "pro" ? "股票自动交易，适合股票策略用户。" : "期权自动交易、多券商和多账户。"}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-sm text-slate-500">月付</div>
                      <div className="font-semibold text-slate-100">{priceLabel(item, "month")}</div>
                    </div>
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-2 text-sm">
                    <div className="rounded-xl border border-slate-700 bg-slate-950/45 px-3 py-2">
                      月付 {priceLabel(item, "month")}
                    </div>
                    <div className="rounded-xl border border-slate-700 bg-slate-950/45 px-3 py-2">
                      年付 {priceLabel(item, "year")}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <label className="grid gap-2">
              <span className="field-label">周期</span>
              <select className="input-base" value={cycle} onChange={(event) => setCycle(event.target.value as BillingCycle)}>
                <option value="month">月付</option>
                <option value="year">年付</option>
              </select>
            </label>
            <label className="grid gap-2">
              <span className="field-label">用途</span>
              <select className="input-base" value={intent} onChange={(event) => setIntent(event.target.value)}>
                <option value="开通/续期">开通 / 续期</option>
                <option value="升级套餐">升级套餐</option>
              </select>
            </label>
            <label className="grid gap-2">
              <span className="field-label">支付通道</span>
              <select
                className="input-base"
                value={paymentProvider}
                onChange={(event) => {
                  const nextProvider = normalizePaymentProvider(event.target.value);
                  setPaymentProvider(nextProvider);
                  setPaymentMethod(PAYMENT_PROVIDERS[nextProvider].defaultPaymentMethod);
                }}
              >
                {PAYMENT_PROVIDER_IDS.map((providerId) => (
                  <option key={providerId} value={providerId} disabled={PAYMENT_PROVIDERS[providerId].status !== "available"}>
                    {PAYMENT_PROVIDERS[providerId].label}
                    {PAYMENT_PROVIDERS[providerId].status === "available" ? "" : "（预留）"}
                  </option>
                ))}
              </select>
              <span className="text-xs text-slate-500">{selectedProvider.description}</span>
            </label>
            <label className="grid gap-2">
              <span className="field-label">付款方式</span>
              <select className="input-base" value={paymentMethod} onChange={(event) => setPaymentMethod(event.target.value as PaymentMethod)}>
                <option value="wechat">微信</option>
                <option value="alipay">支付宝</option>
                <option value="wise">Wise</option>
                <option value="other">其他方式</option>
              </select>
            </label>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="grid gap-2">
              <span className="field-label">接收 License 的邮箱</span>
              <input className="input-base" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" />
            </label>
            <label className="grid gap-2">
              <span className="field-label">本地 owner_id</span>
              <input className="input-base" value={ownerId} onChange={(event) => setOwnerId(event.target.value)} placeholder="例如 davies" />
            </label>
          </div>

          <label className="grid gap-2">
            <span className="field-label">备注</span>
            <textarea
              className="input-base min-h-24"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="升级套餐时可写当前套餐、付款差价或和客服确认的信息。"
            />
          </label>

          <button type="button" className="btn-primary disabled:cursor-not-allowed disabled:opacity-45" disabled={loading || !canSubmit} onClick={() => void createOrder()}>
            {loading ? "正在生成订单..." : "生成付款订单"}
          </button>
        </section>

        <aside className="panel space-y-4">
          <div>
            <div className="section-title">付款信息</div>
            <div className="mt-3 rounded-2xl border border-slate-700 bg-slate-950/35 p-4">
              <div className="flex items-center justify-between gap-4 border-b border-slate-700/70 pb-3">
                <span className="text-slate-500">套餐</span>
                <span className="font-semibold text-slate-100">{PLAN_LABELS[plan]} / {CYCLE_LABELS[cycle]}</span>
              </div>
              <div className="flex items-center justify-between gap-4 border-b border-slate-700/70 py-3">
                <span className="text-slate-500">金额</span>
                <span className="text-xl font-bold text-cyan-100">{moneyLabel(order?.amount ?? order?.amountCny ?? amount, order?.currency || "CNY")}</span>
              </div>
              <div className="flex items-center justify-between gap-4 border-b border-slate-700/70 py-3">
                <span className="text-slate-500">通道</span>
                <span className="font-semibold text-slate-100">{PAYMENT_PROVIDERS[order?.paymentProvider || paymentProvider].label}</span>
              </div>
              <div className="flex items-center justify-between gap-4 border-b border-slate-700/70 py-3">
                <span className="text-slate-500">方式</span>
                <span className="font-semibold text-slate-100">{PAYMENT_LABELS[paymentMethod]}</span>
              </div>
              <div className="flex items-center justify-between gap-4 pt-3">
                <span className="text-slate-500">订单号</span>
                <span className="font-mono text-sm text-slate-100">{order?.orderNo || "生成后显示"}</span>
              </div>
              {order?.providerOrderId ? (
                <div className="flex items-center justify-between gap-4 pt-3">
                  <span className="text-slate-500">通道单号</span>
                  <span className="font-mono text-sm text-slate-100">{order.providerOrderId}</span>
                </div>
              ) : null}
            </div>
          </div>

          {order ? (
            <div className="rounded-2xl border border-amber-300/35 bg-amber-300/10 p-4 text-sm leading-6 text-amber-100">
              <div className="font-semibold">付款备注</div>
              <div className="mt-2 flex items-center justify-between gap-3 rounded-xl border border-amber-200/30 bg-slate-950/35 px-3 py-2 font-mono text-xs">
                <span>{paymentRemark}</span>
                <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => void copyRemark()}>
                  复制
                </button>
              </div>
              <div className="mt-2 text-xs text-amber-100/80">请尽量把订单号填入付款备注，方便管理员快速确认。</div>
            </div>
          ) : null}

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
            <QrBox title="微信收款码" srcs={WECHAT_QR_URLS} active={paymentMethod === "wechat"} />
            <QrBox title="支付宝收款码" srcs={ALIPAY_QR_URLS} active={paymentMethod === "alipay"} />
            <QrBox title="Wise 收款码" srcs={WISE_QR_URLS} active={paymentMethod === "wise"} />
          </div>

          {paymentMethod === "wise" ? (
            <div className="rounded-2xl border border-cyan-300/35 bg-cyan-300/10 p-4 text-sm leading-6 text-cyan-50">
              <div className="font-semibold">Wise 付款</div>
              <div className="mt-2 text-cyan-50/85">
                请扫描 Wise 收款码付款人民币，并在备注里填写订单号，方便管理员确认到账后签发 License。
              </div>
              <div className="mt-2 rounded-xl border border-cyan-200/30 bg-slate-950/35 px-3 py-2 font-mono text-xs">
                {paymentRemark}
              </div>
            </div>
          ) : null}

          {order ? (
            <div className="rounded-2xl border border-slate-700 bg-slate-950/35 p-4 text-sm leading-6 text-slate-400">
              <div className="font-semibold text-slate-100">订单已提交</div>
              <div className="mt-2">创建时间：{formatTime(order.createdAt)}</div>
              <div>当前状态：待管理员确认收款</div>
              <div className="mt-2 text-cyan-100">确认到账后，License 会自动发送到 {order.email}。</div>
            </div>
          ) : null}
        </aside>
      </div>
    </PageShell>
  );
}
