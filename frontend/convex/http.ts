import { httpActionGeneric, httpRouter } from "convex/server";
import { internal } from "./_generated/api";
import type { Id } from "./_generated/dataModel";

type RecordLicenseResult = {
  ok: boolean;
  duplicate?: boolean;
  deliveryId: any;
  email: string;
  ownerId: string;
  plan: string;
  license: any;
  licenseJson: string;
  expiresAt: number;
  currentPeriodEnd?: number | null;
  emailStatus?: string;
};

type LicenseDeliveryListResult = {
  ok: boolean;
  rows: any[];
};

type ManualOrderResult = RecordLicenseResult & {
  order: any;
};

function jsonResponse(status: number, body: any) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
    },
  });
}

function authHeaderSecret(request: Request) {
  const direct = request.headers.get("x-mt-webhook-secret") || request.headers.get("x-webhook-secret") || "";
  if (direct.trim()) return direct.trim();
  const authorization = request.headers.get("authorization") || "";
  return authorization.toLowerCase().startsWith("bearer ") ? authorization.slice(7).trim() : "";
}

function normalizePlan(value: unknown): "free" | "pro" | "premium" {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "premium") return "premium";
  if (raw === "pro") return "pro";
  return "free";
}

function firstString(...values: unknown[]) {
  for (const value of values) {
    const raw = String(value || "").trim();
    if (raw) return raw;
  }
  return "";
}

function firstNumber(...values: unknown[]) {
  for (const value of values) {
    const n = Number(value);
    if (Number.isFinite(n) && n > 0) return n;
  }
  return undefined;
}

function firstPaymentProvider(...values: unknown[]) {
  const raw = firstString(...values).trim().toLowerCase();
  if (raw === "wechat_native") return "wechat_native";
  if (raw === "alipay_qr") return "alipay_qr";
  if (raw === "aggregate_qr") return "aggregate_qr";
  return "manual_qr";
}

function unwrapPaymentPayload(body: any) {
  const data = body?.data?.object || body?.data || body?.event?.data || body;
  const metadata = data?.metadata || data?.custom_data || body?.metadata || {};
  const customer = data?.customer || data?.customer_details || body?.customer || {};
  return {
    data,
    email: firstString(
      body?.email,
      body?.customer_email,
      data?.email,
      data?.customer_email,
      customer?.email,
      metadata?.email
    ),
    ownerId: firstString(
      body?.owner_id,
      body?.ownerId,
      data?.owner_id,
      data?.ownerId,
      metadata?.owner_id,
      metadata?.ownerId,
      metadata?.local_owner_id
    ),
    plan: normalizePlan(firstString(body?.plan, data?.plan, metadata?.plan, metadata?.tier, data?.price?.nickname)),
    status: firstString(body?.status, data?.status, body?.type, data?.payment_status),
    provider: firstString(body?.provider, data?.provider, metadata?.provider),
    providerEventId: firstString(body?.provider_event_id, body?.providerEventId, body?.id, data?.event_id),
    providerCustomerId: firstString(
      body?.provider_customer_id,
      body?.providerCustomerId,
      data?.customer,
      customer?.id,
      metadata?.provider_customer_id
    ),
    providerSubscriptionId: firstString(
      body?.provider_subscription_id,
      body?.providerSubscriptionId,
      data?.subscription,
      data?.subscription_id,
      metadata?.provider_subscription_id
    ),
    currentPeriodEnd: firstNumber(
      body?.current_period_end,
      body?.currentPeriodEnd,
      data?.current_period_end,
      data?.currentPeriodEnd,
      data?.billing_period?.ends_at ? Date.parse(data.billing_period.ends_at) : undefined,
      data?.items?.data?.[0]?.current_period_end
    ),
    metadata,
  };
}

function base64EncodeUtf8(value: string) {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (char) => {
    if (char === "&") return "&amp;";
    if (char === "<") return "&lt;";
    if (char === ">") return "&gt;";
    if (char === '"') return "&quot;";
    return "&#39;";
  });
}

async function sendLicenseEmail(record: RecordLicenseResult) {
  const apiKey = String(process.env.RESEND_API_KEY || "").trim();
  if (!apiKey) return { status: "skipped" as const, provider: "resend", error: "missing_resend_api_key" };
  const from = String(process.env.LICENSE_EMAIL_FROM || "").trim();
  if (!from) return { status: "skipped" as const, provider: "resend", error: "missing_license_email_from" };
  const replyTo = String(process.env.LICENSE_EMAIL_REPLY_TO || "").trim();
  const subjectPrefix = String(process.env.LICENSE_EMAIL_SUBJECT_PREFIX || "MultiTrading").trim();
  const subject = `${subjectPrefix} ${record.plan.toUpperCase()} License`;
  const licenseExpires = Number.isFinite(record.expiresAt) ? new Date(record.expiresAt).toLocaleString("zh-CN") : "-";
  const subscriptionExpires = Number.isFinite(record.currentPeriodEnd)
    ? new Date(Number(record.currentPeriodEnd)).toLocaleString("zh-CN")
    : "-";
  const text = [
    "你的 MultiTrading 本地授权 License 已生成。",
    "",
    `本地 owner_id: ${record.ownerId}`,
    `订阅档位: ${record.plan}`,
    `订阅到期时间: ${subscriptionExpires}`,
    `本地授权密钥到期时间: ${licenseExpires}`,
    "",
    "请在 MultiTrading 本地客户端打开：个人中心 -> 本地授权 License -> 导入 License。",
    "",
    record.licenseJson,
  ].join("\n");
  const html = `
    <p>你的 <strong>MultiTrading</strong> 本地授权 License 已生成。</p>
    <ul>
      <li>本地 owner_id：<strong>${escapeHtml(record.ownerId)}</strong></li>
      <li>订阅档位：<strong>${escapeHtml(record.plan)}</strong></li>
      <li>订阅到期时间：${escapeHtml(subscriptionExpires)}</li>
      <li>本地授权密钥到期时间：${escapeHtml(licenseExpires)}</li>
    </ul>
    <p>请在本地客户端打开：个人中心 -> 本地授权 License -> 导入 License。</p>
    <p>如果附件被邮箱拦截，也可以复制下面的 JSON 导入：</p>
    <pre style="white-space:pre-wrap;background:#f6f8fa;border:1px solid #d0d7de;border-radius:6px;padding:12px;">${escapeHtml(record.licenseJson)}</pre>
  `;
  const payload: any = {
    from,
    to: [record.email],
    subject,
    text,
    html,
    attachments: [
      {
        filename: "multitrading-license.json",
        content: base64EncodeUtf8(record.licenseJson),
      },
    ],
  };
  if (replyTo) payload.reply_to = replyTo;
  const response = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  const responseText = await response.text();
  let responseJson: any = null;
  try {
    responseJson = responseText ? JSON.parse(responseText) : null;
  } catch {
    responseJson = null;
  }
  if (!response.ok) {
    return {
      status: "failed" as const,
      provider: "resend",
      error: responseJson?.message || responseJson?.error?.message || responseText || `resend_${response.status}`,
    };
  }
  return {
    status: "sent" as const,
    provider: "resend",
    messageId: String(responseJson?.id || ""),
  };
}

const http = httpRouter();

http.route({
  path: "/billing/license-webhook",
  method: "POST",
  handler: httpActionGeneric(async (ctx, request) => {
    const webhookSecret = authHeaderSecret(request);
    if (!webhookSecret) return jsonResponse(401, { ok: false, error: "missing_webhook_secret" });
    let body: any;
    try {
      body = await request.json();
    } catch {
      return jsonResponse(400, { ok: false, error: "invalid_json" });
    }

    const payload = unwrapPaymentPayload(body);
    if (!payload.email) return jsonResponse(400, { ok: false, error: "email_required" });
    if (!payload.ownerId) return jsonResponse(400, { ok: false, error: "owner_id_required" });

    try {
      const record = (await ctx.runMutation(internal.billing.recordLicenseCheckout, {
        webhookSecret,
        email: payload.email,
        ownerId: payload.ownerId,
        plan: payload.plan,
        status: payload.status,
        provider: payload.provider || "manual",
        providerEventId: payload.providerEventId,
        providerCustomerId: payload.providerCustomerId,
        providerSubscriptionId: payload.providerSubscriptionId,
        currentPeriodEnd: payload.currentPeriodEnd,
        metadata: payload.metadata,
      })) as RecordLicenseResult;

      if (record.duplicate && record.emailStatus === "sent") {
        return jsonResponse(200, {
          ok: true,
          duplicate: true,
          deliveryId: record.deliveryId,
          emailStatus: record.emailStatus,
        });
      }

      const emailResult = await sendLicenseEmail(record);
      await ctx.runMutation(internal.billing.markLicenseDeliveryEmail, {
        deliveryId: record.deliveryId,
        emailStatus: emailResult.status,
        emailProvider: emailResult.provider,
        emailMessageId: "messageId" in emailResult ? emailResult.messageId : undefined,
        emailError: "error" in emailResult ? emailResult.error : undefined,
      });
      return jsonResponse(emailResult.status === "failed" ? 502 : 200, {
        ok: emailResult.status !== "failed",
        deliveryId: record.deliveryId,
        ownerId: record.ownerId,
        plan: record.plan,
        emailStatus: emailResult.status,
        emailError: "error" in emailResult ? emailResult.error : undefined,
      });
    } catch (err: any) {
      const message = String(err?.message || err);
      return jsonResponse(message === "unauthorized" ? 401 : 400, { ok: false, error: message });
    }
  }),
});

http.route({
  path: "/billing/license-deliveries",
  method: "GET",
  handler: httpActionGeneric(async (ctx, request) => {
    const webhookSecret = authHeaderSecret(request);
    if (!webhookSecret) return jsonResponse(401, { ok: false, error: "missing_webhook_secret" });
    const url = new URL(request.url);
    const limit = Number(url.searchParams.get("limit") || 25);
    const q = String(url.searchParams.get("q") || "").trim();
    try {
      const result = (await ctx.runQuery(internal.billing.listLicenseDeliveries, {
        webhookSecret,
        limit,
        q,
      })) as LicenseDeliveryListResult;
      return jsonResponse(200, result);
    } catch (err: any) {
      const message = String(err?.message || err);
      return jsonResponse(message === "unauthorized" ? 401 : 400, { ok: false, error: message });
    }
  }),
});

http.route({
  path: "/billing/license-admin",
  method: "POST",
  handler: httpActionGeneric(async (ctx, request) => {
    const webhookSecret = authHeaderSecret(request);
    if (!webhookSecret) return jsonResponse(401, { ok: false, error: "missing_webhook_secret" });
    let body: any;
    try {
      body = await request.json();
    } catch {
      return jsonResponse(400, { ok: false, error: "invalid_json" });
    }

    const action = String(body?.action || "").trim().toLowerCase();
    const rawDeliveryId = String(body?.deliveryId || body?.delivery_id || "").trim();
    if (!rawDeliveryId) return jsonResponse(400, { ok: false, error: "delivery_id_required" });
    const deliveryId = rawDeliveryId as Id<"licenseDeliveries">;

    try {
      if (action === "resend") {
        const result = (await ctx.runQuery(internal.billing.getLicenseDelivery, {
          webhookSecret,
          deliveryId,
        })) as { ok: boolean; row: any };
        const row = result.row;
        const emailResult = await sendLicenseEmail({
          ok: true,
          deliveryId: row.id,
          email: row.email,
          ownerId: row.ownerId,
          plan: row.plan,
          license: {},
          licenseJson: row.licenseJson,
          currentPeriodEnd: row.currentPeriodEnd,
          expiresAt: row.expiresAt,
        });
        await ctx.runMutation(internal.billing.markLicenseDeliveryEmail, {
          deliveryId,
          emailStatus: emailResult.status,
          emailProvider: emailResult.provider,
          emailMessageId: "messageId" in emailResult ? emailResult.messageId : undefined,
          emailError: "error" in emailResult ? emailResult.error : undefined,
        });
        return jsonResponse(emailResult.status === "failed" ? 502 : 200, {
          ok: emailResult.status !== "failed",
          action,
          deliveryId: row.id,
          emailStatus: emailResult.status,
          emailError: "error" in emailResult ? emailResult.error : undefined,
        });
      }

      if (action === "renew") {
        const record = (await ctx.runMutation(internal.billing.renewLicenseDelivery, {
          webhookSecret,
          deliveryId,
          periodDays: Number(body?.periodDays || body?.period_days || 30),
          metadata: body?.metadata,
        })) as RecordLicenseResult;
        const emailResult = await sendLicenseEmail(record);
        await ctx.runMutation(internal.billing.markLicenseDeliveryEmail, {
          deliveryId: record.deliveryId,
          emailStatus: emailResult.status,
          emailProvider: emailResult.provider,
          emailMessageId: "messageId" in emailResult ? emailResult.messageId : undefined,
          emailError: "error" in emailResult ? emailResult.error : undefined,
        });
        return jsonResponse(emailResult.status === "failed" ? 502 : 200, {
          ok: emailResult.status !== "failed",
          action,
          deliveryId: record.deliveryId,
          ownerId: record.ownerId,
          plan: record.plan,
          emailStatus: emailResult.status,
          emailError: "error" in emailResult ? emailResult.error : undefined,
        });
      }

      if (action === "upgrade") {
        const targetPlan = normalizePlan(body?.plan);
        if (targetPlan === "free") return jsonResponse(400, { ok: false, error: "upgrade_plan_required" });
        const record = (await ctx.runMutation(internal.billing.upgradeLicenseDelivery, {
          webhookSecret,
          deliveryId,
          plan: targetPlan,
          metadata: body?.metadata,
        })) as RecordLicenseResult;
        const emailResult = await sendLicenseEmail(record);
        await ctx.runMutation(internal.billing.markLicenseDeliveryEmail, {
          deliveryId: record.deliveryId,
          emailStatus: emailResult.status,
          emailProvider: emailResult.provider,
          emailMessageId: "messageId" in emailResult ? emailResult.messageId : undefined,
          emailError: "error" in emailResult ? emailResult.error : undefined,
        });
        return jsonResponse(emailResult.status === "failed" ? 502 : 200, {
          ok: emailResult.status !== "failed",
          action,
          deliveryId: record.deliveryId,
          ownerId: record.ownerId,
          plan: record.plan,
          emailStatus: emailResult.status,
          emailError: "error" in emailResult ? emailResult.error : undefined,
        });
      }

      if (action === "revoke") {
        const result = await ctx.runMutation(internal.billing.revokeLicenseDelivery, {
          webhookSecret,
          deliveryId,
          reason: String(body?.reason || "").trim() || undefined,
        });
        return jsonResponse(200, { ...result, action });
      }

      return jsonResponse(400, { ok: false, error: "unsupported_action" });
    } catch (err: any) {
      const message = String(err?.message || err);
      return jsonResponse(message === "unauthorized" ? 401 : 400, { ok: false, error: message });
    }
  }),
});

http.route({
  path: "/billing/manual-orders",
  method: "GET",
  handler: httpActionGeneric(async (ctx, request) => {
    const webhookSecret = authHeaderSecret(request);
    if (!webhookSecret) return jsonResponse(401, { ok: false, error: "missing_webhook_secret" });
    const url = new URL(request.url);
    const limit = Number(url.searchParams.get("limit") || 50);
    const q = String(url.searchParams.get("q") || "").trim();
    const status = String(url.searchParams.get("status") || "").trim();
    try {
      const result = await ctx.runQuery(internal.billing.listManualOrders, {
        webhookSecret,
        limit,
        q,
        status,
      });
      return jsonResponse(200, result);
    } catch (err: any) {
      const message = String(err?.message || err);
      return jsonResponse(message === "unauthorized" ? 401 : 400, { ok: false, error: message });
    }
  }),
});

http.route({
  path: "/billing/manual-orders",
  method: "POST",
  handler: httpActionGeneric(async (ctx, request) => {
    const webhookSecret = authHeaderSecret(request);
    if (!webhookSecret) return jsonResponse(401, { ok: false, error: "missing_webhook_secret" });
    let body: any;
    try {
      body = await request.json();
    } catch {
      return jsonResponse(400, { ok: false, error: "invalid_json" });
    }
    const plan = normalizePlan(body?.plan);
    if (plan === "free") return jsonResponse(400, { ok: false, error: "paid_plan_required" });
    const billingCycle = String(body?.billingCycle || body?.billing_cycle || "month").trim().toLowerCase() === "year" ? "year" : "month";
    try {
    const result = await ctx.runMutation(internal.billing.createManualOrder, {
      webhookSecret,
      email: firstString(body?.email, body?.customerEmail, body?.customer_email),
      ownerId: firstString(body?.ownerId, body?.owner_id),
      plan,
      billingCycle,
      paymentMethod: firstString(body?.paymentMethod, body?.payment_method),
      paymentProvider: firstPaymentProvider(body?.paymentProvider, body?.payment_provider, body?.provider),
      customerNote: firstString(body?.customerNote, body?.customer_note, body?.note),
    });
      return jsonResponse(200, result);
    } catch (err: any) {
      const message = String(err?.message || err);
      return jsonResponse(message === "unauthorized" ? 401 : 400, { ok: false, error: message });
    }
  }),
});

http.route({
  path: "/billing/manual-order-admin",
  method: "POST",
  handler: httpActionGeneric(async (ctx, request) => {
    const webhookSecret = authHeaderSecret(request);
    if (!webhookSecret) return jsonResponse(401, { ok: false, error: "missing_webhook_secret" });
    let body: any;
    try {
      body = await request.json();
    } catch {
      return jsonResponse(400, { ok: false, error: "invalid_json" });
    }
    const action = String(body?.action || "").trim().toLowerCase();
    const rawOrderId = String(body?.orderId || body?.order_id || "").trim();
    if (!rawOrderId) return jsonResponse(400, { ok: false, error: "order_id_required" });
    const orderId = rawOrderId as Id<"manualOrders">;

    try {
      if (action === "cancel") {
        const result = await ctx.runMutation(internal.billing.cancelManualOrder, {
          webhookSecret,
          orderId,
          adminNote: String(body?.adminNote || body?.admin_note || "").trim() || undefined,
        });
        return jsonResponse(200, { ...result, action });
      }

      if (action === "confirm") {
        const record = (await ctx.runMutation(internal.billing.confirmManualOrderAndIssueLicense, {
          webhookSecret,
          orderId,
          paymentReference: String(body?.paymentReference || body?.payment_reference || "").trim() || undefined,
          adminNote: String(body?.adminNote || body?.admin_note || "").trim() || undefined,
          confirmedBy: String(body?.confirmedBy || body?.confirmed_by || "").trim() || undefined,
          providerTradeId: String(body?.providerTradeId || body?.provider_trade_id || body?.tradeNo || body?.trade_no || "").trim() || undefined,
          providerStatus: String(body?.providerStatus || body?.provider_status || "").trim() || undefined,
          providerPayload: body?.providerPayload || body?.provider_payload,
        })) as ManualOrderResult;

        if (record.duplicate && record.emailStatus === "sent") {
          return jsonResponse(200, {
            ok: true,
            duplicate: true,
            action,
            order: record.order,
            deliveryId: record.deliveryId,
            emailStatus: record.emailStatus,
            currentPeriodEnd: record.currentPeriodEnd || null,
          });
        }

        const emailResult = await sendLicenseEmail(record);
        await ctx.runMutation(internal.billing.markLicenseDeliveryEmail, {
          deliveryId: record.deliveryId,
          emailStatus: emailResult.status,
          emailProvider: emailResult.provider,
          emailMessageId: "messageId" in emailResult ? emailResult.messageId : undefined,
          emailError: "error" in emailResult ? emailResult.error : undefined,
        });
        await ctx.runMutation(internal.billing.markManualOrderLicenseEmail, {
          webhookSecret,
          orderId,
          emailStatus: emailResult.status,
        });
        return jsonResponse(emailResult.status === "failed" ? 502 : 200, {
          ok: emailResult.status !== "failed",
          action,
          order: record.order,
          deliveryId: record.deliveryId,
          ownerId: record.ownerId,
          plan: record.plan,
          currentPeriodEnd: record.currentPeriodEnd || null,
          emailStatus: emailResult.status,
          emailError: "error" in emailResult ? emailResult.error : undefined,
        });
      }

      return jsonResponse(400, { ok: false, error: "unsupported_action" });
    } catch (err: any) {
      const message = String(err?.message || err);
      const status = message === "unauthorized" ? 401 : message === "active_higher_plan_exists" ? 409 : 400;
      return jsonResponse(status, { ok: false, error: message });
    }
  }),
});

export default http;
