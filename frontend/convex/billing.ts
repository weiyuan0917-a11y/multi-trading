import { internalMutationGeneric, internalQueryGeneric } from "convex/server";
import { v } from "convex/values";
import { normalizePlan } from "./lib/plans";
import { assertOwnerId, createSignedLocalLicense, stableJson, type LocalLicenseStatus } from "./lib/license";
import { createPaymentIntent, normalizePaymentProvider } from "./lib/paymentProviders";

function now() {
  return Date.now();
}

function normalizeEmail(value: unknown) {
  return String(value || "").trim().toLowerCase();
}

function normalizeProvider(value: unknown) {
  const provider = String(value || "manual").trim().toLowerCase();
  return provider || "manual";
}

function normalizeBillingCycle(value: unknown): "month" | "year" {
  const raw = String(value || "").trim().toLowerCase();
  return raw === "year" || raw === "annual" || raw === "annually" ? "year" : "month";
}

function normalizeCurrentPeriodEnd(value: unknown): number | undefined {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return undefined;
  return n < 100000000000 ? Math.floor(n * 1000) : Math.floor(n);
}

function normalizeLicenseStatus(value: unknown): LocalLicenseStatus {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "trialing" || raw === "trial") return "trialing";
  if (raw === "canceled" || raw === "cancelled") return "canceled";
  if (raw === "expired") return "expired";
  if (raw === "inactive" || raw === "failed" || raw === "past_due" || raw === "incomplete") return "inactive";
  if (raw === "paid" || raw === "completed" || raw === "complete" || raw === "success") return "active";
  return "active";
}

function requireWebhookSecret(supplied: string) {
  const expected = String(process.env.MT_BILLING_WEBHOOK_SECRET || process.env.BILLING_WEBHOOK_SECRET || "").trim();
  if (!expected) throw new Error("billing_webhook_secret_required");
  if (!supplied || supplied !== expected) throw new Error("unauthorized");
}

function licenseExpiresAtMs(value: unknown) {
  const ts = Date.parse(String(value || ""));
  return Number.isFinite(ts) ? ts : now();
}

function clampDays(value: unknown, fallback = 30) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(1, Math.min(370, Math.floor(n)));
}

const MANUAL_ORDER_PRICE_CNY: Record<"pro" | "premium", Record<"month" | "year", number>> = {
  pro: { month: 99, year: 999 },
  premium: { month: 199, year: 1999 },
};

function manualOrderAmount(plan: "pro" | "premium", billingCycle: "month" | "year") {
  return MANUAL_ORDER_PRICE_CNY[plan][billingCycle];
}

function manualOrderDisplayAmount(row: any) {
  if (row?.currency === "HKD") {
    const amountHkd = Number(row?.amountHkd);
    if (Number.isFinite(amountHkd) && amountHkd > 0) return amountHkd;
  }
  const amountHkd = Number(row?.amountHkd);
  if (!Number.isFinite(Number(row?.amountCny)) && Number.isFinite(amountHkd) && amountHkd > 0) return amountHkd;
  return Number(row?.amountCny || 0);
}

function manualOrderDays(billingCycle: "month" | "year") {
  return billingCycle === "year" ? 365 : 30;
}

function activeDeliveryEnd(row: any) {
  return normalizeCurrentPeriodEnd(row?.currentPeriodEnd) || 0;
}

function isActiveDelivery(row: any, timestamp: number) {
  const status = normalizeLicenseStatus(row?.status);
  const end = activeDeliveryEnd(row);
  return (status === "active" || status === "trialing") && end > timestamp;
}

function createOrderNo(timestamp = now()) {
  const date = new Date(timestamp);
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  const random = Math.random().toString(36).slice(2, 8).toUpperCase();
  return `MT${yyyy}${mm}${dd}${random}`;
}

function planRank(plan: string) {
  const normalized = normalizePlan(plan);
  if (normalized === "premium") return 2;
  if (normalized === "pro") return 1;
  return 0;
}

function betterManualOrderBase(left: any | null, right: any) {
  if (!left) return right;
  const leftRank = planRank(left.plan);
  const rightRank = planRank(right.plan);
  if (leftRank !== rightRank) return leftRank > rightRank ? left : right;
  const leftEnd = activeDeliveryEnd(left);
  const rightEnd = activeDeliveryEnd(right);
  if (leftEnd !== rightEnd) return leftEnd > rightEnd ? left : right;
  const leftIssued = Number(left.issuedAt || left.createdAt || 0);
  const rightIssued = Number(right.issuedAt || right.createdAt || 0);
  return leftIssued >= rightIssued ? left : right;
}

async function getBestActiveDeliveryForOwner(ctx: any, ownerId: string, timestamp: number) {
  const rows = await ctx.db
    .query("licenseDeliveries")
    .withIndex("by_owner_id", (q: any) => q.eq("ownerId", ownerId))
    .collect();
  let best: any = null;
  for (const row of rows) {
    if (!isActiveDelivery(row, timestamp)) continue;
    best = betterManualOrderBase(best, row);
  }
  return best;
}

function rowToPublic(row: any) {
  return {
    id: row._id,
    email: row.email,
    ownerId: row.ownerId,
    plan: row.plan,
    status: row.status,
    provider: row.provider || "",
    providerEventId: row.providerEventId || "",
    emailStatus: row.emailStatus,
    emailProvider: row.emailProvider || "",
    emailMessageId: row.emailMessageId || "",
    emailError: row.emailError || "",
    currentPeriodEnd: row.currentPeriodEnd || null,
    issuedAt: row.issuedAt,
    expiresAt: row.expiresAt,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
    licenseJson: row.licenseJson,
  };
}

function matchesSearch(row: any, q: string) {
  const needle = normalizeEmail(q);
  if (!needle) return true;
  return [row.email, row.ownerId, row.plan, row.status, row.provider, row.providerEventId, row.emailStatus]
    .map((value) => String(value || "").toLowerCase())
    .some((value) => value.includes(needle));
}

function manualOrderToPublic(row: any, delivery?: any) {
  const paymentProvider = normalizePaymentProvider(row.paymentProvider);
  return {
    id: row._id,
    orderNo: row.orderNo,
    email: row.email,
    ownerId: row.ownerId,
    plan: row.plan,
    billingCycle: row.billingCycle,
    amountCny: row.amountCny,
    amountHkd: row.amountHkd || (row.currency === "HKD" ? row.amountCny : undefined),
    amount: manualOrderDisplayAmount(row),
    currency: row.currency,
    paymentMethod: row.paymentMethod,
    paymentProvider,
    providerOrderId: row.providerOrderId || "",
    providerTradeId: row.providerTradeId || "",
    providerStatus: row.providerStatus || "",
    payUrl: row.payUrl || "",
    qrCodeUrl: row.qrCodeUrl || "",
    expiresAt: row.expiresAt || null,
    status: row.status,
    customerNote: row.customerNote || "",
    adminNote: row.adminNote || "",
    paymentReference: row.paymentReference || "",
    paidAt: row.paidAt || null,
    confirmedBy: row.confirmedBy || "",
    licenseDeliveryId: row.licenseDeliveryId || null,
    licenseEmailStatus: row.licenseEmailStatus || "",
    licenseEmailProvider: delivery?.emailProvider || "",
    licenseEmailMessageId: delivery?.emailMessageId || "",
    licenseEmailError: delivery?.emailError || "",
    licenseJson: delivery?.licenseJson || "",
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
  };
}

function matchesManualOrderSearch(row: any, q: string) {
  const needle = normalizeEmail(q);
  if (!needle) return true;
  return [
    row.orderNo,
    row.email,
    row.ownerId,
    row.plan,
    row.billingCycle,
    row.paymentMethod,
    row.paymentProvider,
    row.providerOrderId,
    row.providerTradeId,
    row.providerStatus,
    row.status,
    row.paymentReference,
  ]
    .map((value) => String(value || "").toLowerCase())
    .some((value) => value.includes(needle));
}

export const recordLicenseCheckout = internalMutationGeneric({
  args: {
    webhookSecret: v.string(),
    email: v.string(),
    ownerId: v.string(),
    plan: v.union(v.literal("free"), v.literal("pro"), v.literal("premium")),
    status: v.optional(v.string()),
    provider: v.optional(v.string()),
    providerEventId: v.optional(v.string()),
    providerCustomerId: v.optional(v.string()),
    providerSubscriptionId: v.optional(v.string()),
    currentPeriodEnd: v.optional(v.number()),
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    requireWebhookSecret(args.webhookSecret);
    const email = normalizeEmail(args.email);
    if (!email || !email.includes("@")) throw new Error("email_required");
    const ownerId = assertOwnerId(args.ownerId);
    const providerEventId = String(args.providerEventId || "").trim();

    if (providerEventId) {
      const existing = await ctx.db
        .query("licenseDeliveries")
        .withIndex("by_provider_event_id", (q: any) => q.eq("providerEventId", providerEventId))
        .unique();
      if (existing) {
        return {
          ok: true,
          duplicate: true,
          deliveryId: existing._id,
          email,
          ownerId,
          plan: existing.plan,
          license: existing.license,
          licenseJson: existing.licenseJson,
          currentPeriodEnd: existing.currentPeriodEnd || null,
          expiresAt: existing.expiresAt,
          emailStatus: existing.emailStatus,
        };
      }
    }

    const plan = normalizePlan(args.plan);
    const currentPeriodEnd = normalizeCurrentPeriodEnd(args.currentPeriodEnd);
    const status = normalizeLicenseStatus(args.status);
    const provider = normalizeProvider(args.provider);
    const issued = await createSignedLocalLicense({
      ownerId,
      plan,
      status,
      role: "user",
      isAdmin: false,
      currentPeriodEnd,
      source: `billing_${provider}`,
    });
    const licenseJson = JSON.stringify(issued.license, null, 2);
    const timestamp = now();
    const deliveryId = await ctx.db.insert("licenseDeliveries", {
      email,
      ownerId,
      plan,
      status,
      provider,
      providerEventId: providerEventId || undefined,
      providerCustomerId: String(args.providerCustomerId || "").trim() || undefined,
      providerSubscriptionId: String(args.providerSubscriptionId || "").trim() || undefined,
      currentPeriodEnd,
      license: issued.license,
      licenseJson,
      emailStatus: "pending",
      metadata: args.metadata ? JSON.parse(stableJson(args.metadata)) : undefined,
      issuedAt: Date.parse(issued.issuedAt) || timestamp,
      expiresAt: licenseExpiresAtMs(issued.expiresAt),
      createdAt: timestamp,
      updatedAt: timestamp,
    });
    return {
      ok: true,
      duplicate: false,
      deliveryId,
      email,
      ownerId,
      plan,
      license: issued.license,
      licenseJson,
      expiresAt: Date.parse(issued.expiresAt) || timestamp,
      currentPeriodEnd,
      emailStatus: "pending",
    };
  },
});

export const markLicenseDeliveryEmail = internalMutationGeneric({
  args: {
    deliveryId: v.id("licenseDeliveries"),
    emailStatus: v.union(v.literal("sent"), v.literal("skipped"), v.literal("failed")),
    emailProvider: v.optional(v.string()),
    emailMessageId: v.optional(v.string()),
    emailError: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    await ctx.db.patch(args.deliveryId, {
      emailStatus: args.emailStatus,
      emailProvider: args.emailProvider,
      emailMessageId: args.emailMessageId,
      emailError: args.emailError,
      updatedAt: now(),
    });
    return { ok: true };
  },
});

export const listLicenseDeliveries = internalQueryGeneric({
  args: {
    webhookSecret: v.string(),
    limit: v.optional(v.number()),
    q: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireWebhookSecret(args.webhookSecret);
    const limit = Math.max(1, Math.min(100, Math.floor(Number(args.limit || 25))));
    const q = String(args.q || "").trim().toLowerCase();
    const rows = await ctx.db.query("licenseDeliveries").order("desc").take(q ? 250 : limit);
    const filtered = (q ? rows.filter((row: any) => matchesSearch(row, q)) : rows).slice(0, limit);
    return {
      ok: true,
      rows: filtered.map(rowToPublic),
    };
  },
});

export const getLicenseDelivery = internalQueryGeneric({
  args: {
    webhookSecret: v.string(),
    deliveryId: v.id("licenseDeliveries"),
  },
  handler: async (ctx, args) => {
    requireWebhookSecret(args.webhookSecret);
    const row = await ctx.db.get(args.deliveryId);
    if (!row) throw new Error("license_delivery_not_found");
    return { ok: true, row: rowToPublic(row) };
  },
});

export const renewLicenseDelivery = internalMutationGeneric({
  args: {
    webhookSecret: v.string(),
    deliveryId: v.id("licenseDeliveries"),
    periodDays: v.optional(v.number()),
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    requireWebhookSecret(args.webhookSecret);
    const existing = await ctx.db.get(args.deliveryId);
    if (!existing) throw new Error("license_delivery_not_found");
    const timestamp = now();
    const days = clampDays(args.periodDays, 30);
    const plan = normalizePlan(existing.plan);
    const basePeriodEnd = Math.max(Number(existing.currentPeriodEnd || 0), timestamp);
    const currentPeriodEnd = basePeriodEnd + days * 24 * 60 * 60 * 1000;
    const issued = await createSignedLocalLicense({
      ownerId: existing.ownerId,
      plan,
      status: "active",
      role: "user",
      isAdmin: false,
      currentPeriodEnd,
      source: "billing_admin_renew",
    });
    const licenseJson = JSON.stringify(issued.license, null, 2);
    const deliveryId = await ctx.db.insert("licenseDeliveries", {
      email: existing.email,
      ownerId: existing.ownerId,
      plan,
      status: "active",
      provider: "admin_renew",
      providerEventId: `admin_renew_${timestamp}_${String(existing._id)}`,
      providerCustomerId: existing.providerCustomerId,
      providerSubscriptionId: existing.providerSubscriptionId,
      currentPeriodEnd,
      license: issued.license,
      licenseJson,
      emailStatus: "pending",
      metadata: {
        previousDeliveryId: String(existing._id),
        periodDays: days,
        ...(args.metadata ? JSON.parse(stableJson(args.metadata)) : {}),
      },
      issuedAt: Date.parse(issued.issuedAt) || timestamp,
      expiresAt: licenseExpiresAtMs(issued.expiresAt),
      createdAt: timestamp,
      updatedAt: timestamp,
    });
    return {
      ok: true,
      deliveryId,
      email: existing.email,
      ownerId: existing.ownerId,
      plan,
      license: issued.license,
      licenseJson,
      currentPeriodEnd,
      expiresAt: Date.parse(issued.expiresAt) || timestamp,
      emailStatus: "pending",
    };
  },
});

export const upgradeLicenseDelivery = internalMutationGeneric({
  args: {
    webhookSecret: v.string(),
    deliveryId: v.id("licenseDeliveries"),
    plan: v.union(v.literal("pro"), v.literal("premium")),
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    requireWebhookSecret(args.webhookSecret);
    const existing = await ctx.db.get(args.deliveryId);
    if (!existing) throw new Error("license_delivery_not_found");
    const timestamp = now();
    const previousPlan = normalizePlan(existing.plan);
    const targetPlan = normalizePlan(args.plan);
    if (planRank(targetPlan) <= planRank(previousPlan)) throw new Error("upgrade_requires_higher_plan");
    const currentPeriodEnd = normalizeCurrentPeriodEnd(existing.currentPeriodEnd);
    if (!currentPeriodEnd || currentPeriodEnd <= timestamp) throw new Error("active_subscription_required_for_upgrade");

    const issued = await createSignedLocalLicense({
      ownerId: existing.ownerId,
      plan: targetPlan,
      status: "active",
      role: "user",
      isAdmin: false,
      currentPeriodEnd,
      source: "billing_admin_upgrade",
    });
    const licenseJson = JSON.stringify(issued.license, null, 2);
    const deliveryId = await ctx.db.insert("licenseDeliveries", {
      email: existing.email,
      ownerId: existing.ownerId,
      plan: targetPlan,
      status: "active",
      provider: "admin_upgrade",
      providerEventId: `admin_upgrade_${timestamp}_${String(existing._id)}`,
      providerCustomerId: existing.providerCustomerId,
      providerSubscriptionId: existing.providerSubscriptionId,
      currentPeriodEnd,
      license: issued.license,
      licenseJson,
      emailStatus: "pending",
      metadata: {
        previousDeliveryId: String(existing._id),
        previousPlan,
        upgradedTo: targetPlan,
        periodPolicy: "keep_existing_current_period_end",
        ...(args.metadata ? JSON.parse(stableJson(args.metadata)) : {}),
      },
      issuedAt: Date.parse(issued.issuedAt) || timestamp,
      expiresAt: licenseExpiresAtMs(issued.expiresAt),
      createdAt: timestamp,
      updatedAt: timestamp,
    });
    return {
      ok: true,
      deliveryId,
      email: existing.email,
      ownerId: existing.ownerId,
      plan: targetPlan,
      license: issued.license,
      licenseJson,
      currentPeriodEnd,
      expiresAt: Date.parse(issued.expiresAt) || timestamp,
      emailStatus: "pending",
    };
  },
});

export const revokeLicenseDelivery = internalMutationGeneric({
  args: {
    webhookSecret: v.string(),
    deliveryId: v.id("licenseDeliveries"),
    reason: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireWebhookSecret(args.webhookSecret);
    const existing = await ctx.db.get(args.deliveryId);
    if (!existing) throw new Error("license_delivery_not_found");
    const timestamp = now();
    const issued = await createSignedLocalLicense({
      ownerId: existing.ownerId,
      plan: existing.plan,
      status: "canceled",
      role: "user",
      isAdmin: false,
      currentPeriodEnd: existing.currentPeriodEnd,
      source: "billing_admin_revoke",
    });
    const licenseJson = JSON.stringify(issued.license, null, 2);
    const metadata = {
      ...(existing.metadata && typeof existing.metadata === "object" ? existing.metadata : {}),
      revokedAt: timestamp,
      revokeReason: String(args.reason || "").trim() || "admin_revoke",
    };
    await ctx.db.patch(args.deliveryId, {
      status: "canceled",
      license: issued.license,
      licenseJson,
      metadata,
      issuedAt: Date.parse(issued.issuedAt) || timestamp,
      expiresAt: licenseExpiresAtMs(issued.expiresAt),
      updatedAt: timestamp,
    });
    const updated = await ctx.db.get(args.deliveryId);
    return { ok: true, row: rowToPublic(updated) };
  },
});

export const createManualOrder = internalMutationGeneric({
  args: {
    webhookSecret: v.string(),
    email: v.string(),
    ownerId: v.string(),
    plan: v.union(v.literal("pro"), v.literal("premium")),
    billingCycle: v.union(v.literal("month"), v.literal("year")),
    paymentMethod: v.optional(v.string()),
    paymentProvider: v.optional(v.string()),
    customerNote: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireWebhookSecret(args.webhookSecret);
    const email = normalizeEmail(args.email);
    if (!email || !email.includes("@")) throw new Error("email_required");
    const ownerId = assertOwnerId(args.ownerId);
    const plan = normalizePlan(args.plan);
    if (plan === "free") throw new Error("paid_plan_required");
    const billingCycle = normalizeBillingCycle(args.billingCycle);
    const timestamp = now();
    const orderNo = createOrderNo(timestamp);
    const intent = createPaymentIntent({
      paymentProvider: args.paymentProvider,
      paymentMethod: args.paymentMethod,
      orderNo,
    });
    const amountCny = manualOrderAmount(plan, billingCycle);
    const row = {
      orderNo,
      email,
      ownerId,
      plan,
      billingCycle,
      amountCny,
      currency: "CNY" as const,
      paymentMethod: intent.paymentMethod,
      paymentProvider: intent.paymentProvider,
      providerOrderId: intent.providerOrderId,
      providerStatus: intent.providerStatus,
      payUrl: intent.payUrl,
      qrCodeUrl: intent.qrCodeUrl,
      expiresAt: intent.expiresAt,
      providerPayload: intent.providerPayload,
      status: "pending" as const,
      customerNote: String(args.customerNote || "").trim() || undefined,
      createdAt: timestamp,
      updatedAt: timestamp,
    };
    const id = await ctx.db.insert("manualOrders", row);
    const saved = await ctx.db.get(id);
    return { ok: true, order: manualOrderToPublic(saved) };
  },
});

export const listManualOrders = internalQueryGeneric({
  args: {
    webhookSecret: v.string(),
    limit: v.optional(v.number()),
    q: v.optional(v.string()),
    status: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireWebhookSecret(args.webhookSecret);
    const limit = Math.max(1, Math.min(100, Math.floor(Number(args.limit || 50))));
    const q = String(args.q || "").trim().toLowerCase();
    const status = String(args.status || "").trim().toLowerCase();
    const rows = await ctx.db.query("manualOrders").order("desc").take(q || status ? 300 : limit);
    const filtered = rows
      .filter((row: any) => (status ? String(row.status || "").toLowerCase() === status : true))
      .filter((row: any) => matchesManualOrderSearch(row, q))
      .slice(0, limit);
    const publicRows = [];
    for (const row of filtered) {
      const delivery = row.licenseDeliveryId ? await ctx.db.get(row.licenseDeliveryId) : null;
      publicRows.push(manualOrderToPublic(row, delivery));
    }
    return { ok: true, rows: publicRows };
  },
});

export const cancelManualOrder = internalMutationGeneric({
  args: {
    webhookSecret: v.string(),
    orderId: v.id("manualOrders"),
    adminNote: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireWebhookSecret(args.webhookSecret);
    const row = await ctx.db.get(args.orderId);
    if (!row) throw new Error("manual_order_not_found");
    if (row.licenseDeliveryId) throw new Error("manual_order_already_issued");
    await ctx.db.patch(args.orderId, {
      status: "canceled",
      adminNote: String(args.adminNote || "").trim() || row.adminNote,
      providerStatus: "canceled",
      updatedAt: now(),
    });
    const updated = await ctx.db.get(args.orderId);
    return { ok: true, order: manualOrderToPublic(updated) };
  },
});

export const confirmManualOrderAndIssueLicense = internalMutationGeneric({
  args: {
    webhookSecret: v.string(),
    orderId: v.id("manualOrders"),
    paymentReference: v.optional(v.string()),
    adminNote: v.optional(v.string()),
    confirmedBy: v.optional(v.string()),
    providerTradeId: v.optional(v.string()),
    providerStatus: v.optional(v.string()),
    providerPayload: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    requireWebhookSecret(args.webhookSecret);
    const order = await ctx.db.get(args.orderId);
    if (!order) throw new Error("manual_order_not_found");
    if (order.status === "canceled") throw new Error("manual_order_canceled");
    if (order.licenseDeliveryId) {
      const existing = await ctx.db.get(order.licenseDeliveryId);
      if (!existing) throw new Error("license_delivery_not_found");
      return {
        ok: true,
        duplicate: true,
        order: manualOrderToPublic(order),
        deliveryId: existing._id,
        email: existing.email,
        ownerId: existing.ownerId,
        plan: existing.plan,
        license: existing.license,
        licenseJson: existing.licenseJson,
        currentPeriodEnd: existing.currentPeriodEnd || null,
        expiresAt: existing.expiresAt,
        emailStatus: existing.emailStatus,
      };
    }

    const timestamp = now();
    const addedDays = manualOrderDays(order.billingCycle);
    const activeBase = await getBestActiveDeliveryForOwner(ctx, order.ownerId, timestamp);
    const activeBasePlan = activeBase ? normalizePlan(activeBase.plan) : "free";
    if (activeBase && planRank(activeBasePlan) > planRank(order.plan)) {
      throw new Error("active_higher_plan_exists");
    }
    const baseCurrentPeriodEnd = activeBase ? activeDeliveryEnd(activeBase) : 0;
    const basePeriodEnd = Math.max(baseCurrentPeriodEnd, timestamp);
    const currentPeriodEnd = basePeriodEnd + addedDays * 24 * 60 * 60 * 1000;
    const paymentProvider = normalizePaymentProvider(order.paymentProvider);
    const issued = await createSignedLocalLicense({
      ownerId: order.ownerId,
      plan: order.plan,
      status: "active",
      role: "user",
      isAdmin: false,
      currentPeriodEnd,
      source: `billing_${paymentProvider}`,
    });
    const licenseJson = JSON.stringify(issued.license, null, 2);
    const deliveryId = await ctx.db.insert("licenseDeliveries", {
      email: order.email,
      ownerId: order.ownerId,
      plan: order.plan,
      status: "active",
      provider: paymentProvider,
      providerEventId: `${paymentProvider}_${order.orderNo}`,
      currentPeriodEnd,
      license: issued.license,
      licenseJson,
      emailStatus: "pending",
      metadata: {
        orderId: String(order._id),
        orderNo: order.orderNo,
        amountCny: order.amountCny,
        amountHkd: order.amountHkd || (order.currency === "HKD" ? order.amountCny : undefined),
        amount: manualOrderDisplayAmount(order),
        currency: order.currency,
        billingCycle: order.billingCycle,
        paymentMethod: order.paymentMethod,
        paymentProvider,
        providerOrderId: order.providerOrderId,
        providerTradeId: String(args.providerTradeId || "").trim() || undefined,
        providerStatus: String(args.providerStatus || "").trim() || undefined,
        paymentReference: String(args.paymentReference || "").trim() || undefined,
        adminNote: String(args.adminNote || "").trim() || undefined,
        periodPolicy: activeBase ? "extend_from_active_subscription_end" : "start_from_payment_time",
        baseDeliveryId: activeBase ? String(activeBase._id) : undefined,
        basePlan: activeBase ? activeBase.plan : undefined,
        baseCurrentPeriodEnd: baseCurrentPeriodEnd || undefined,
        addedDays,
      },
      issuedAt: Date.parse(issued.issuedAt) || timestamp,
      expiresAt: licenseExpiresAtMs(issued.expiresAt),
      createdAt: timestamp,
      updatedAt: timestamp,
    });
    await ctx.db.patch(args.orderId, {
      status: "paid",
      paymentReference: String(args.paymentReference || "").trim() || undefined,
      adminNote: String(args.adminNote || "").trim() || undefined,
      paidAt: timestamp,
      confirmedBy: String(args.confirmedBy || "").trim() || undefined,
      providerTradeId: String(args.providerTradeId || "").trim() || order.providerTradeId || undefined,
      providerStatus: String(args.providerStatus || "").trim() || "paid",
      providerPayload: args.providerPayload ? JSON.parse(stableJson(args.providerPayload)) : order.providerPayload,
      licenseDeliveryId: deliveryId,
      licenseEmailStatus: "pending",
      updatedAt: timestamp,
    });
    const updated = await ctx.db.get(args.orderId);
    return {
      ok: true,
      duplicate: false,
      order: manualOrderToPublic(updated),
      deliveryId,
      email: order.email,
      ownerId: order.ownerId,
      plan: order.plan,
      license: issued.license,
      licenseJson,
      currentPeriodEnd,
      expiresAt: Date.parse(issued.expiresAt) || timestamp,
      emailStatus: "pending",
    };
  },
});

export const markManualOrderLicenseEmail = internalMutationGeneric({
  args: {
    webhookSecret: v.string(),
    orderId: v.id("manualOrders"),
    emailStatus: v.union(v.literal("sent"), v.literal("skipped"), v.literal("failed")),
  },
  handler: async (ctx, args) => {
    requireWebhookSecret(args.webhookSecret);
    const order = await ctx.db.get(args.orderId);
    if (!order) throw new Error("manual_order_not_found");
    await ctx.db.patch(args.orderId, {
      status: args.emailStatus === "sent" ? "license_sent" : "paid",
      licenseEmailStatus: args.emailStatus,
      updatedAt: now(),
    });
    const updated = await ctx.db.get(args.orderId);
    return { ok: true, order: manualOrderToPublic(updated) };
  },
});
