"use client";

import { useEffect, useMemo, useState } from "react";
import { PageShell } from "@/components/ui/page-shell";
import { authHeaders, getAuthToken } from "@/lib/auth";
import { paymentProviderLabel, type PaymentProviderId } from "@/lib/payment-providers";
import { useEntitlements } from "@/lib/use-entitlements";

type Plan = "pro" | "premium";
type BillingCycle = "month" | "year";
type PaymentMethod = "wechat" | "alipay" | "wise" | "other";
type OrderStatus = "pending" | "paid" | "license_sent" | "canceled";

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
  providerTradeId?: string;
  providerStatus?: string;
  payUrl?: string;
  qrCodeUrl?: string;
  expiresAt?: number | null;
  status: OrderStatus;
  customerNote: string;
  adminNote: string;
  paymentReference: string;
  paidAt: number | null;
  confirmedBy: string;
  licenseDeliveryId: string | null;
  licenseEmailStatus: string;
  licenseEmailProvider: string;
  licenseEmailMessageId: string;
  licenseEmailError: string;
  licenseJson: string;
  createdAt: number;
  updatedAt: number;
};

type OrderListResponse = {
  ok?: boolean;
  rows?: ManualOrder[];
  error?: string;
};

type OrderActionResponse = {
  ok?: boolean;
  action?: string;
  order?: ManualOrder;
  deliveryId?: string;
  currentPeriodEnd?: number | null;
  emailStatus?: string;
  emailError?: string;
  error?: string;
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
  other: "其他",
};

const STATUS_LABELS: Record<OrderStatus, string> = {
  pending: "待确认",
  paid: "已确认",
  license_sent: "已发证",
  canceled: "已取消",
};

function formatTime(value?: number | null) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN");
}

function statusTone(status: string) {
  if (status === "license_sent") return "border-emerald-400/30 bg-emerald-400/10 text-emerald-100";
  if (status === "paid") return "border-cyan-400/30 bg-cyan-400/10 text-cyan-100";
  if (status === "canceled") return "border-rose-400/35 bg-rose-400/10 text-rose-100";
  return "border-amber-400/35 bg-amber-400/10 text-amber-100";
}

function orderAmount(row: Pick<ManualOrder, "amount" | "amountHkd" | "amountCny">) {
  return Number(row.amount ?? row.amountCny ?? row.amountHkd ?? 0);
}

function moneyLabel(row: Pick<ManualOrder, "amount" | "amountHkd" | "amountCny" | "currency">) {
  return `${row.currency || "CNY"} ${orderAmount(row).toLocaleString("zh-CN")}`;
}

function friendlyError(message: string) {
  if (message === "active_higher_plan_exists") {
    return "该 owner 当前已有更高档位的有效订阅。不能用低档位订单覆盖，请使用续期或升级功能处理。";
  }
  return message;
}

export default function AdminOrdersPage() {
  const entitlements = useEntitlements();
  const [rows, setRows] = useState<ManualOrder[]>([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [paymentReference, setPaymentReference] = useState("");
  const [adminNote, setAdminNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [actionLoadingId, setActionLoadingId] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [selectedLicense, setSelectedLicense] = useState("");

  const counts = useMemo(() => {
    return rows.reduce(
      (acc, row) => {
        acc[row.status] += 1;
        return acc;
      },
      { pending: 0, paid: 0, license_sent: 0, canceled: 0 } as Record<OrderStatus, number>
    );
  }, [rows]);

  const loadOrders = async (nextQuery = query, nextStatus = status) => {
    setLoading(true);
    setError("");
    try {
      const token = getAuthToken();
      const params = new URLSearchParams({ limit: "100" });
      if (nextQuery.trim()) params.set("q", nextQuery.trim());
      if (nextStatus.trim()) params.set("status", nextStatus.trim());
      const response = await fetch(`/api/admin/manual-orders?${params.toString()}`, {
        headers: authHeaders(token),
        cache: "no-store",
      });
      const data = (await response.json()) as OrderListResponse;
      if (!response.ok || data.error) throw new Error(data.error || `request_${response.status}`);
      setRows(data.rows || []);
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadOrders("", "");
  }, []);

  const runAction = async (row: ManualOrder, action: "confirm" | "cancel") => {
    if (action === "confirm") {
      const ok = window.confirm(`确认 ${row.orderNo} 已收到 ${moneyLabel(row)}，并给 ${row.ownerId} 签发 ${PLAN_LABELS[row.plan]} License？`);
      if (!ok) return;
    }
    if (action === "cancel") {
      const ok = window.confirm(`确认取消订单 ${row.orderNo}？`);
      if (!ok) return;
    }
    setActionLoadingId(`${row.id}:${action}`);
    setMessage("");
    setError("");
    try {
      const token = getAuthToken();
      const response = await fetch("/api/admin/manual-orders", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          ...authHeaders(token),
        },
        body: JSON.stringify({
          action,
          orderId: row.id,
          paymentReference: paymentReference.trim() || row.paymentReference || row.orderNo,
          adminNote: adminNote.trim() || undefined,
        }),
      });
      const data = (await response.json()) as OrderActionResponse;
      if (!response.ok || data.error) throw new Error(data.error || `request_${response.status}`);
      if (action === "confirm") {
        const periodText = data.currentPeriodEnd ? `订阅到期：${formatTime(data.currentPeriodEnd)}。` : "";
        setMessage(
          data.emailStatus === "sent"
            ? `${row.orderNo} 已确认收款，并已发送 License 邮件。${periodText}`
            : `${row.orderNo} 已确认收款并生成 License，邮件状态：${data.emailStatus || "unknown"}。${periodText}`
        );
      } else {
        setMessage(`${row.orderNo} 已取消。`);
      }
      await loadOrders();
    } catch (err: any) {
      setError(friendlyError(String(err?.message || err)));
    } finally {
      setActionLoadingId("");
    }
  };

  const copyLicense = async (row: ManualOrder) => {
    if (!row.licenseJson) {
      setError("这笔订单还没有可复制的 License。");
      return;
    }
    setSelectedLicense(row.licenseJson);
    try {
      await navigator.clipboard.writeText(row.licenseJson);
      setMessage(`${row.orderNo} 的 License JSON 已复制。`);
    } catch {
      setMessage(`${row.orderNo} 的 License JSON 已展开，可手动复制。`);
    }
  };

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-cyan-200/80">MultiTrading Admin</div>
            <h1 className="mt-2 text-3xl font-bold text-slate-50">收款订单中心</h1>
            <p className="mt-2 text-sm leading-6 text-slate-400">
              用于半自动扫码收款：用户提交订单，管理员核对到账后点击确认，系统自动签发并邮件发送 License。
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
          当前账号不是管理员。页面可打开，但确认收款和取消订单接口会拒绝非管理员请求。
        </div>
      ) : null}

      {message ? <div className="panel border-emerald-400/30 bg-emerald-400/10 text-emerald-100">{message}</div> : null}
      {error ? <div className="panel border-rose-400/35 bg-rose-400/10 text-rose-100">{error}</div> : null}

      <section className="panel space-y-4">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_12rem_12rem_auto]">
          <input
            className="input-base"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索订单号 / 邮箱 / owner_id / 套餐 / 付款备注"
          />
          <select className="input-base" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">全部状态</option>
            <option value="pending">待确认</option>
            <option value="paid">已确认</option>
            <option value="license_sent">已发证</option>
            <option value="canceled">已取消</option>
          </select>
          <button type="button" className="btn-secondary" onClick={() => void loadOrders(query, status)} disabled={loading}>
            {loading ? "刷新中..." : "刷新 / 搜索"}
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={loading}
            onClick={() => {
              setQuery("");
              setStatus("");
              void loadOrders("", "");
            }}
          >
            清空
          </button>
        </div>

        <div className="grid gap-3 md:grid-cols-4">
          {(Object.keys(STATUS_LABELS) as OrderStatus[]).map((key) => (
            <div key={key} className="rounded-2xl border border-slate-700 bg-slate-950/35 p-4">
              <div className="text-sm text-slate-500">{STATUS_LABELS[key]}</div>
              <div className="mt-1 text-2xl font-bold text-slate-100">{counts[key]}</div>
            </div>
          ))}
        </div>

        <div className="grid gap-3 lg:grid-cols-2">
          <label className="grid gap-2">
            <span className="field-label">收款流水 / 备注</span>
            <input
              className="input-base"
              value={paymentReference}
              onChange={(event) => setPaymentReference(event.target.value)}
              placeholder="可填微信/支付宝流水号；为空则使用订单号"
            />
          </label>
          <label className="grid gap-2">
            <span className="field-label">管理员备注</span>
            <input className="input-base" value={adminNote} onChange={(event) => setAdminNote(event.target.value)} placeholder="可选" />
          </label>
        </div>

        <div className="table-shell">
          <table className="min-w-full text-left text-sm">
            <thead className="table-head">
              <tr>
                <th className="px-4 py-3">订单</th>
                <th className="px-4 py-3">客户</th>
                <th className="px-4 py-3">套餐</th>
                <th className="px-4 py-3">金额 / 方式</th>
                <th className="px-4 py-3">状态</th>
                <th className="px-4 py-3">备注</th>
                <th className="px-4 py-3">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const canConfirm = row.status === "pending" || row.status === "paid";
                const canCancel = row.status === "pending";
                return (
                  <tr key={row.id} className="border-t border-slate-700/70">
                    <td className="px-4 py-3">
                      <div className="font-mono text-sm font-semibold text-cyan-100">{row.orderNo}</div>
                      <div className="mt-1 text-xs text-slate-500">{formatTime(row.createdAt)}</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-semibold text-slate-100">{row.email}</div>
                      <div className="mt-1 text-xs text-slate-500">owner: {row.ownerId}</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-semibold text-slate-100">{PLAN_LABELS[row.plan]}</div>
                      <div className="mt-1 text-xs text-slate-500">{CYCLE_LABELS[row.billingCycle]}</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-semibold text-slate-100">{moneyLabel(row)}</div>
                      <div className="mt-1 text-xs text-slate-500">{paymentProviderLabel(row.paymentProvider)} / {PAYMENT_LABELS[row.paymentMethod]}</div>
                      {row.providerOrderId ? (
                        <div className="mt-1 max-w-[11rem] truncate font-mono text-xs text-slate-500" title={row.providerOrderId}>
                          {row.providerOrderId}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full border px-2 py-1 text-xs font-semibold ${statusTone(row.status)}`}>
                        {STATUS_LABELS[row.status] || row.status}
                      </span>
                      {row.licenseEmailStatus ? <div className="mt-2 text-xs text-slate-500">邮件：{row.licenseEmailStatus}</div> : null}
                      {row.licenseEmailMessageId ? (
                        <div className="mt-1 max-w-[10rem] truncate text-xs text-slate-500" title={row.licenseEmailMessageId}>
                          ID：{row.licenseEmailMessageId}
                        </div>
                      ) : null}
                      {row.licenseEmailError ? (
                        <div className="mt-1 max-w-[10rem] truncate text-xs text-rose-200" title={row.licenseEmailError}>
                          {row.licenseEmailError}
                        </div>
                      ) : null}
                    </td>
                    <td className="max-w-[18rem] px-4 py-3 text-xs leading-5 text-slate-400">
                      <div className="line-clamp-3">{row.customerNote || "-"}</div>
                      {row.paymentReference ? <div className="mt-2 text-cyan-100">流水：{row.paymentReference}</div> : null}
                      {row.providerTradeId ? <div className="mt-1 text-cyan-100">通道流水：{row.providerTradeId}</div> : null}
                      {row.providerStatus ? <div className="mt-1 text-slate-500">通道状态：{row.providerStatus}</div> : null}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          className="btn-secondary px-3 py-1.5 text-xs"
                          disabled={!canConfirm || actionLoadingId === `${row.id}:confirm`}
                          onClick={() => void runAction(row, "confirm")}
                        >
                          {actionLoadingId === `${row.id}:confirm` ? "处理中" : "确认收款并发证"}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary px-3 py-1.5 text-xs"
                          disabled={!row.licenseJson}
                          onClick={() => void copyLicense(row)}
                        >
                          复制 License
                        </button>
                        <button
                          type="button"
                          className="btn-secondary px-3 py-1.5 text-xs text-rose-200"
                          disabled={!canCancel || actionLoadingId === `${row.id}:cancel`}
                          onClick={() => void runAction(row, "cancel")}
                        >
                          {actionLoadingId === `${row.id}:cancel` ? "取消中" : "取消"}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {!rows.length ? (
                <tr>
                  <td className="px-4 py-8 text-center text-slate-500" colSpan={7}>
                    暂无订单
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

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
