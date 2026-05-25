import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  users: defineTable({
    clerkUserId: v.string(),
    email: v.string(),
    name: v.optional(v.string()),
    imageUrl: v.optional(v.string()),
    role: v.union(v.literal("user"), v.literal("admin"), v.literal("owner")),
    onboardingCompletedAt: v.optional(v.number()),
    createdAt: v.number(),
    updatedAt: v.number(),
    lastSeenAt: v.number(),
  })
    .index("by_clerk_user_id", ["clerkUserId"])
    .index("by_email", ["email"]),

  subscriptions: defineTable({
    clerkUserId: v.string(),
    plan: v.union(v.literal("free"), v.literal("pro"), v.literal("premium")),
    status: v.union(
      v.literal("free"),
      v.literal("trialing"),
      v.literal("active"),
      v.literal("past_due"),
      v.literal("canceled"),
      v.literal("incomplete")
    ),
    provider: v.optional(v.union(v.literal("stripe"), v.literal("clerk"), v.literal("manual"))),
    providerCustomerId: v.optional(v.string()),
    providerSubscriptionId: v.optional(v.string()),
    currentPeriodEnd: v.optional(v.number()),
    updatedAt: v.number(),
  }).index("by_clerk_user_id", ["clerkUserId"]),

  entitlements: defineTable({
    clerkUserId: v.string(),
    research: v.boolean(),
    backtest: v.boolean(),
    tradingagents: v.boolean(),
    openbb: v.boolean(),
    stockAutoTrading: v.boolean(),
    optionAutoTrading: v.boolean(),
    multiBroker: v.boolean(),
    multiAccount: v.boolean(),
    source: v.union(v.literal("plan"), v.literal("manual"), v.literal("admin")),
    updatedAt: v.number(),
  }).index("by_clerk_user_id", ["clerkUserId"]),

  localOwnerBindings: defineTable({
    clerkUserId: v.string(),
    email: v.string(),
    ownerId: v.string(),
    status: v.union(v.literal("active"), v.literal("revoked")),
    source: v.union(v.literal("env"), v.literal("pairing_code"), v.literal("admin")),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_clerk_user_id", ["clerkUserId"])
    .index("by_owner_id", ["ownerId"])
    .index("by_email", ["email"]),

  licenseDeliveries: defineTable({
    email: v.string(),
    ownerId: v.string(),
    plan: v.union(v.literal("free"), v.literal("pro"), v.literal("premium")),
    status: v.union(v.literal("active"), v.literal("trialing"), v.literal("inactive"), v.literal("canceled"), v.literal("expired")),
    provider: v.optional(v.string()),
    providerEventId: v.optional(v.string()),
    providerCustomerId: v.optional(v.string()),
    providerSubscriptionId: v.optional(v.string()),
    currentPeriodEnd: v.optional(v.number()),
    license: v.any(),
    licenseJson: v.string(),
    emailStatus: v.union(v.literal("pending"), v.literal("sent"), v.literal("skipped"), v.literal("failed")),
    emailProvider: v.optional(v.string()),
    emailMessageId: v.optional(v.string()),
    emailError: v.optional(v.string()),
    metadata: v.optional(v.any()),
    issuedAt: v.number(),
    expiresAt: v.number(),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_email", ["email"])
    .index("by_owner_id", ["ownerId"])
    .index("by_provider_event_id", ["providerEventId"]),

  manualOrders: defineTable({
    orderNo: v.string(),
    email: v.string(),
    ownerId: v.string(),
    plan: v.union(v.literal("pro"), v.literal("premium")),
    billingCycle: v.union(v.literal("month"), v.literal("year")),
    amountCny: v.number(),
    amountHkd: v.optional(v.number()),
    currency: v.union(v.literal("CNY"), v.literal("HKD")),
    paymentMethod: v.union(v.literal("wechat"), v.literal("alipay"), v.literal("wise"), v.literal("other")),
    paymentProvider: v.optional(
      v.union(
        v.literal("manual_qr"),
        v.literal("wechat_native"),
        v.literal("alipay_qr"),
        v.literal("aggregate_qr")
      )
    ),
    providerOrderId: v.optional(v.string()),
    providerTradeId: v.optional(v.string()),
    providerStatus: v.optional(v.string()),
    providerPayload: v.optional(v.any()),
    payUrl: v.optional(v.string()),
    qrCodeUrl: v.optional(v.string()),
    expiresAt: v.optional(v.number()),
    status: v.union(v.literal("pending"), v.literal("paid"), v.literal("license_sent"), v.literal("canceled")),
    customerNote: v.optional(v.string()),
    adminNote: v.optional(v.string()),
    paymentReference: v.optional(v.string()),
    paidAt: v.optional(v.number()),
    confirmedBy: v.optional(v.string()),
    licenseDeliveryId: v.optional(v.id("licenseDeliveries")),
    licenseEmailStatus: v.optional(v.union(v.literal("pending"), v.literal("sent"), v.literal("skipped"), v.literal("failed"))),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_order_no", ["orderNo"])
    .index("by_email", ["email"])
    .index("by_owner_id", ["ownerId"])
    .index("by_status", ["status"])
    .index("by_payment_provider", ["paymentProvider"]),
});
