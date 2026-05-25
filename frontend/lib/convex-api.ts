import { makeFunctionReference, type FunctionReference } from "convex/server";

export type CloudEntitlements = {
  research: boolean;
  backtest: boolean;
  tradingagents: boolean;
  openbb: boolean;
  stockAutoTrading: boolean;
  optionAutoTrading: boolean;
  multiBroker: boolean;
  multiAccount: boolean;
  source: "plan" | "manual" | "admin";
};

export type CloudSession = {
  user: {
    clerkUserId: string;
    email: string;
    name: string;
    imageUrl: string;
    role: "user" | "admin" | "owner";
    isAdmin: boolean;
    onboardingCompletedAt: number | null;
  } | null;
  subscription: {
    plan: "free" | "pro" | "premium";
    status: "free" | "trialing" | "active" | "past_due" | "canceled" | "incomplete";
    provider: "stripe" | "clerk" | "manual";
    currentPeriodEnd: number | null;
  };
  entitlements: CloudEntitlements;
  localOwnerBinding: {
    ownerId: string;
    status: "active" | "revoked";
    source: "env" | "pairing_code" | "admin";
  } | null;
};

export type CloudLocalLicense = {
  owner_id: string;
  plan: "free" | "pro" | "premium";
  status: "active" | "trialing" | "inactive";
  role: "user" | "admin" | "owner";
  is_admin: boolean;
  features: string[];
  expires_at: string;
  issued_at: string;
  source: string;
  signature: string;
};

export const convexFunctions = {
  users: {
    me: makeFunctionReference<"query", Record<string, never>, CloudSession | null>("users:me"),
    upsertCurrentUser: makeFunctionReference<
      "mutation",
      { email?: string; name?: string; imageUrl?: string },
      CloudSession
    >("users:upsertCurrentUser"),
    adminSetSubscription: makeFunctionReference<
      "mutation",
      {
        clerkUserId: string;
        plan: "free" | "pro" | "premium";
        status?: "free" | "trialing" | "active" | "past_due" | "canceled" | "incomplete";
      },
      unknown
    >("users:adminSetSubscription"),
    adminBindLocalOwner: makeFunctionReference<
      "mutation",
      { clerkUserId: string; email: string; ownerId: string },
      unknown
    >("users:adminBindLocalOwner"),
    selfBindLocalOwner: makeFunctionReference<"mutation", { ownerId: string }, unknown>("users:selfBindLocalOwner"),
    completeOnboarding: makeFunctionReference<"mutation", Record<string, never>, number>("users:completeOnboarding"),
    issueLocalLicense: makeFunctionReference<
      "mutation",
      { ownerId?: string },
      { ok: boolean; license: CloudLocalLicense; issuedAt: string; expiresAt: string }
    >("users:issueLocalLicense"),
  },
} satisfies Record<string, Record<string, FunctionReference<any, any, any, any>>>;
