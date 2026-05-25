import { mutationGeneric, queryGeneric } from "convex/server";
import { v } from "convex/values";
import { entitlementsForPlan, normalizePlan, normalizeRole } from "./lib/plans";
import { assertOwnerId, createSignedLocalLicense, normalizeOwnerId } from "./lib/license";

type Identity = {
  subject: string;
  email?: string;
  name?: string;
  pictureUrl?: string;
};

function now() {
  return Date.now();
}

function normalizeEmail(value: unknown) {
  return String(value || "").trim().toLowerCase();
}

function subscriptionIsActive(subscription: any) {
  const status = String(subscription?.status || "").trim().toLowerCase();
  if (status !== "active" && status !== "trialing") return false;
  const currentPeriodEnd = Number(subscription?.currentPeriodEnd || 0);
  if (Number.isFinite(currentPeriodEnd) && currentPeriodEnd > 0 && currentPeriodEnd < now()) return false;
  return true;
}

function bootstrapConfig(email: string) {
  const adminEmail = normalizeEmail(process.env.CONVEX_BOOTSTRAP_ADMIN_EMAIL);
  const ownerId = String(process.env.CONVEX_BOOTSTRAP_LOCAL_OWNER_ID || "").trim().toLowerCase();
  const matched = Boolean(adminEmail && email && adminEmail === email);
  return { matched, ownerId };
}

async function requireIdentity(ctx: any): Promise<Identity> {
  const identity = (await ctx.auth.getUserIdentity()) as Identity | null;
  if (!identity?.subject) {
    throw new Error("unauthenticated");
  }
  return identity;
}

async function currentUser(ctx: any) {
  const identity = await requireIdentity(ctx);
  const user = await ctx.db
    .query("users")
    .withIndex("by_clerk_user_id", (q: any) => q.eq("clerkUserId", identity.subject))
    .unique();
  return { identity, user };
}

async function getByClerkUserId(ctx: any, table: string, clerkUserId: string) {
  return await ctx.db
    .query(table)
    .withIndex("by_clerk_user_id", (q: any) => q.eq("clerkUserId", clerkUserId))
    .unique();
}

async function requireAdmin(ctx: any) {
  const { user } = await currentUser(ctx);
  const role = normalizeRole(user?.role);
  if (role !== "admin" && role !== "owner") {
    throw new Error("admin_required");
  }
}

function publicSession(user: any, subscription: any, entitlements: any, binding: any) {
  const plan = normalizePlan(subscription?.plan);
  const role = normalizeRole(user?.role);
  const isAdmin = role === "admin" || role === "owner";
  const subscriptionActive = subscriptionIsActive(subscription);
  const effectivePlan = isAdmin || subscriptionActive ? plan : "free";
  const defaultEntitlements = entitlementsForPlan(effectivePlan, isAdmin);
  const activeBinding = binding?.status === "active" ? binding : null;
  return {
    user: user
      ? {
          clerkUserId: user.clerkUserId,
          email: user.email,
          name: user.name || "",
          imageUrl: user.imageUrl || "",
          role,
          isAdmin,
          onboardingCompletedAt: user.onboardingCompletedAt || null,
        }
      : null,
    subscription: {
      plan,
      status: subscription?.status || "free",
      provider: subscription?.provider || "manual",
      currentPeriodEnd: subscription?.currentPeriodEnd || null,
    },
    entitlements: {
      research: defaultEntitlements.research,
      backtest: defaultEntitlements.backtest,
      tradingagents: defaultEntitlements.tradingagents,
      openbb: defaultEntitlements.openbb,
      stockAutoTrading: defaultEntitlements.stockAutoTrading,
      optionAutoTrading: defaultEntitlements.optionAutoTrading,
      multiBroker: defaultEntitlements.multiBroker,
      multiAccount: defaultEntitlements.multiAccount,
      source: isAdmin ? "admin" : subscriptionActive ? entitlements?.source || defaultEntitlements.source : "plan",
    },
    localOwnerBinding: activeBinding
      ? {
          ownerId: activeBinding.ownerId,
          status: activeBinding.status,
          source: activeBinding.source,
        }
      : null,
  };
}

async function upsertEntitlements(ctx: any, clerkUserId: string, plan: string, isAdmin: boolean) {
  const entitlements = entitlementsForPlan(normalizePlan(plan), isAdmin);
  const existing = await getByClerkUserId(ctx, "entitlements", clerkUserId);
  const payload = {
    ...entitlements,
    updatedAt: now(),
  };
  if (existing?._id) {
    await ctx.db.patch(existing._id, payload);
    return { ...existing, ...payload };
  }
  const id = await ctx.db.insert("entitlements", {
    clerkUserId,
    ...payload,
  });
  return await ctx.db.get(id);
}

async function upsertSubscription(ctx: any, clerkUserId: string, plan: string, status = "free", provider = "manual") {
  const existing = await getByClerkUserId(ctx, "subscriptions", clerkUserId);
  const payload = {
    plan: normalizePlan(plan),
    status,
    provider,
    updatedAt: now(),
  };
  if (existing?._id) {
    await ctx.db.patch(existing._id, payload);
    return { ...existing, ...payload };
  }
  const id = await ctx.db.insert("subscriptions", {
    clerkUserId,
    ...payload,
  });
  return await ctx.db.get(id);
}

async function upsertLocalOwnerBinding(ctx: any, clerkUserId: string, email: string, ownerId: string, source = "admin") {
  const existing = await getByClerkUserId(ctx, "localOwnerBindings", clerkUserId);
  const payload = {
    email,
    ownerId: normalizeOwnerId(ownerId),
    status: "active",
    source,
    updatedAt: now(),
  };
  if (existing?._id) {
    await ctx.db.patch(existing._id, payload);
    return { ...existing, ...payload };
  }
  const id = await ctx.db.insert("localOwnerBindings", {
    clerkUserId,
    ...payload,
    createdAt: now(),
  });
  return await ctx.db.get(id);
}

export const me = queryGeneric({
  args: {},
  handler: async (ctx) => {
    const identity = (await ctx.auth.getUserIdentity()) as Identity | null;
    if (!identity?.subject) return null;
    const user = await getByClerkUserId(ctx, "users", identity.subject);
    if (!user) return null;
    const [subscription, entitlements, binding] = await Promise.all([
      getByClerkUserId(ctx, "subscriptions", identity.subject),
      getByClerkUserId(ctx, "entitlements", identity.subject),
      getByClerkUserId(ctx, "localOwnerBindings", identity.subject),
    ]);
    return publicSession(user, subscription, entitlements, binding);
  },
});

export const upsertCurrentUser = mutationGeneric({
  args: {
    email: v.optional(v.string()),
    name: v.optional(v.string()),
    imageUrl: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const identity = await requireIdentity(ctx);
    const email = normalizeEmail(args.email || identity.email);
    const bootstrap = bootstrapConfig(email);
    const existing = await getByClerkUserId(ctx, "users", identity.subject);
    const role = bootstrap.matched ? "admin" : normalizeRole(existing?.role);
    const userPayload = {
      clerkUserId: identity.subject,
      email,
      name: String(args.name || identity.name || "").trim(),
      imageUrl: String(args.imageUrl || identity.pictureUrl || "").trim(),
      role,
      onboardingCompletedAt: bootstrap.matched ? existing?.onboardingCompletedAt || now() : existing?.onboardingCompletedAt,
      updatedAt: now(),
      lastSeenAt: now(),
    };
    const user = existing?._id
      ? await ctx.db.patch(existing._id, userPayload).then(() => ({ ...existing, ...userPayload }))
      : await ctx.db
          .insert("users", {
            ...userPayload,
            createdAt: now(),
          })
          .then((id: any) => ctx.db.get(id));
    const existingSubscription = await getByClerkUserId(ctx, "subscriptions", identity.subject);
    const subscription = await upsertSubscription(
      ctx,
      identity.subject,
      bootstrap.matched ? "premium" : existingSubscription?.plan || "free",
      bootstrap.matched ? "active" : existingSubscription?.status || "free",
      bootstrap.matched ? "manual" : existingSubscription?.provider || "manual"
    );
    const entitlements = await upsertEntitlements(ctx, identity.subject, subscription?.plan || "free", role === "admin" || role === "owner");
    const binding =
      bootstrap.matched && bootstrap.ownerId
        ? await upsertLocalOwnerBinding(ctx, identity.subject, email, bootstrap.ownerId, "env")
        : await getByClerkUserId(ctx, "localOwnerBindings", identity.subject);
    return publicSession(user, subscription, entitlements, binding);
  },
});

export const adminSetSubscription = mutationGeneric({
  args: {
    clerkUserId: v.string(),
    plan: v.union(v.literal("free"), v.literal("pro"), v.literal("premium")),
    status: v.optional(v.union(v.literal("free"), v.literal("trialing"), v.literal("active"), v.literal("past_due"), v.literal("canceled"), v.literal("incomplete"))),
  },
  handler: async (ctx, args) => {
    await requireAdmin(ctx);
    const status = args.status || (args.plan === "free" ? "free" : "active");
    const subscription = await upsertSubscription(ctx, args.clerkUserId, args.plan, status, "manual");
    const target = await getByClerkUserId(ctx, "users", args.clerkUserId);
    await upsertEntitlements(
      ctx,
      args.clerkUserId,
      subscriptionIsActive(subscription) ? args.plan : "free",
      normalizeRole(target?.role) !== "user"
    );
    return subscription;
  },
});

export const adminBindLocalOwner = mutationGeneric({
  args: {
    clerkUserId: v.string(),
    email: v.string(),
    ownerId: v.string(),
  },
  handler: async (ctx, args) => {
    await requireAdmin(ctx);
    return await upsertLocalOwnerBinding(ctx, args.clerkUserId, normalizeEmail(args.email), args.ownerId, "admin");
  },
});

export const selfBindLocalOwner = mutationGeneric({
  args: {
    ownerId: v.string(),
  },
  handler: async (ctx, args) => {
    const { identity, user } = await currentUser(ctx);
    if (!user?._id) throw new Error("user_not_found");
    const ownerId = assertOwnerId(args.ownerId);
    const existingForOwner = await ctx.db
      .query("localOwnerBindings")
      .withIndex("by_owner_id", (q: any) => q.eq("ownerId", ownerId))
      .collect();
    const taken = existingForOwner.find(
      (row: any) => row?.status === "active" && row?.clerkUserId !== identity.subject
    );
    if (taken) throw new Error("owner_already_bound");
    return await upsertLocalOwnerBinding(ctx, identity.subject, normalizeEmail(user.email || identity.email), ownerId, "pairing_code");
  },
});

export const completeOnboarding = mutationGeneric({
  args: {},
  handler: async (ctx) => {
    const { user } = await currentUser(ctx);
    if (!user?._id) throw new Error("user_not_found");
    const binding = await getByClerkUserId(ctx, "localOwnerBindings", user.clerkUserId);
    if (binding?.status !== "active") throw new Error("local_owner_required");
    const completedAt = now();
    await ctx.db.patch(user._id, { onboardingCompletedAt: completedAt, updatedAt: completedAt });
    return completedAt;
  },
});

export const issueLocalLicense = mutationGeneric({
  args: {
    ownerId: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const { identity, user } = await currentUser(ctx);
    if (!user?._id) throw new Error("user_not_found");

    const [subscription, binding] = await Promise.all([
      getByClerkUserId(ctx, "subscriptions", identity.subject),
      getByClerkUserId(ctx, "localOwnerBindings", identity.subject),
    ]);
    const activeBinding = binding?.status === "active" ? binding : null;
    const ownerId = assertOwnerId(args.ownerId || activeBinding?.ownerId || "");
    if (activeBinding?.ownerId && ownerId !== activeBinding.ownerId && normalizeRole(user.role) === "user") {
      throw new Error("owner_mismatch");
    }
    if (!activeBinding?.ownerId && normalizeRole(user.role) === "user") {
      throw new Error("local_owner_required");
    }

    const role = normalizeRole(user.role);
    const isAdmin = role === "admin" || role === "owner";
    const subscriptionActive = subscriptionIsActive(subscription);
    const plan = isAdmin ? "premium" : subscriptionActive ? normalizePlan(subscription?.plan) : "free";
    const licenseActive = isAdmin || subscriptionActive || plan === "free";

    const issued = await createSignedLocalLicense({
      ownerId,
      plan,
      status: licenseActive ? "active" : "inactive",
      role,
      isAdmin,
      currentPeriodEnd: subscription?.currentPeriodEnd,
      source: "convex_subscription",
    });
    return {
      ok: true,
      license: issued.license,
      issuedAt: issued.issuedAt,
      expiresAt: issued.expiresAt,
    };
  },
});
