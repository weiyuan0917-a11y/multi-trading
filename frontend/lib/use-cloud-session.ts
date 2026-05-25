"use client";

import { useConvexAuth, useQuery_experimental as useQuery } from "convex/react";
import { CLERK_ENABLED } from "@/lib/clerk-mode";
import { convexFunctions, type CloudSession } from "@/lib/convex-api";
import { CONVEX_ENABLED } from "@/lib/convex-mode";
import { normalizePlan, type SubscriptionPlan } from "@/lib/entitlements";

export type CloudSessionStatus = "disabled" | "loading" | "unauthenticated" | "success" | "error";

export type CloudSessionState = {
  status: CloudSessionStatus;
  data: CloudSession | null;
};

export function useCloudSession(): CloudSessionState {
  if (!CLERK_ENABLED || !CONVEX_ENABLED) {
    return { status: "disabled", data: null };
  }

  const convexAuth = useConvexAuth();
  const session = useQuery({
    query: convexFunctions.users.me,
    args: {},
    throwOnError: false,
  });

  if (convexAuth.isLoading) return { status: "loading", data: null };
  if (!convexAuth.isAuthenticated) return { status: "unauthenticated", data: null };
  if (session.status === "success") return { status: "success", data: session.data };
  if (session.status === "error") return { status: "error", data: null };
  return { status: "loading", data: null };
}

export function activeLocalOwnerId(session: CloudSession | null): string {
  const binding = session?.localOwnerBinding;
  if (binding?.status !== "active") return "";
  return String(binding.ownerId || "").trim().toLowerCase();
}

export function isActiveCloudSubscription(subscription: CloudSession["subscription"] | null | undefined): boolean {
  const status = String(subscription?.status || "").trim().toLowerCase();
  if (status !== "active" && status !== "trialing") return false;
  const periodEnd = Number(subscription?.currentPeriodEnd || 0);
  if (Number.isFinite(periodEnd) && periodEnd > 0 && periodEnd < Date.now()) return false;
  return true;
}

export function effectiveCloudPlan(session: CloudSession | null): SubscriptionPlan {
  if (session?.user?.isAdmin) return "premium";
  return isActiveCloudSubscription(session?.subscription) ? normalizePlan(session?.subscription?.plan) : "free";
}
