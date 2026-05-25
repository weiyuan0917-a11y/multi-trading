"use client";

import { normalizePlan, type SubscriptionPlan } from "@/lib/entitlements";

export type LocalOwnerBinding = {
  matched: boolean;
  ownerId: string;
  email: string;
  plan: SubscriptionPlan;
  role: string;
  isAdmin: boolean;
};

export const LOCAL_OWNER_ID = (process.env.NEXT_PUBLIC_LOCAL_AGENT_OWNER_ID || "").trim();
export const LOCAL_OWNER_EMAIL = (process.env.NEXT_PUBLIC_LOCAL_AGENT_OWNER_EMAIL || "").trim().toLowerCase();
export const LOCAL_OWNER_PLAN = normalizePlan(process.env.NEXT_PUBLIC_LOCAL_AGENT_OWNER_PLAN || "premium");
export const LOCAL_OWNER_ROLE = (process.env.NEXT_PUBLIC_LOCAL_AGENT_OWNER_ROLE || "admin").trim().toLowerCase() || "admin";
export const LOCAL_OWNER_IS_ADMIN = (process.env.NEXT_PUBLIC_LOCAL_AGENT_OWNER_IS_ADMIN || "true").trim().toLowerCase() !== "false";

let currentCloudEmail = "";
let currentCloudOwnerId = "";
let currentCloudPlan: SubscriptionPlan = "free";
let currentCloudRole = "user";
let currentCloudIsAdmin = false;

export function normalizeEmail(value: unknown): string {
  return String(value || "").trim().toLowerCase();
}

export function setLocalOwnerCloudEmail(email: unknown): void {
  currentCloudEmail = normalizeEmail(email);
}

export function setLocalOwnerCloudIdentity(identity: {
  email?: unknown;
  ownerId?: unknown;
  plan?: unknown;
  role?: unknown;
  isAdmin?: unknown;
}): void {
  currentCloudEmail = normalizeEmail(identity.email);
  currentCloudOwnerId = String(identity.ownerId || "").trim().toLowerCase();
  currentCloudPlan = normalizePlan(identity.plan);
  currentCloudRole = String(identity.role || "user").trim().toLowerCase() || "user";
  currentCloudIsAdmin = Boolean(identity.isAdmin) || currentCloudRole === "admin" || currentCloudRole === "owner";
}

export function getLocalOwnerCloudEmail(): string {
  return currentCloudEmail;
}

export function getLocalOwnerBinding(email: unknown): LocalOwnerBinding {
  const normalizedEmail = normalizeEmail(email);
  const runtimeMatched = Boolean(
    currentCloudOwnerId &&
      currentCloudEmail &&
      (!normalizedEmail || normalizedEmail === currentCloudEmail)
  );
  if (runtimeMatched) {
    return {
      matched: true,
      ownerId: currentCloudOwnerId,
      email: normalizedEmail || currentCloudEmail,
      plan: currentCloudIsAdmin ? "premium" : currentCloudPlan,
      role: currentCloudRole,
      isAdmin: currentCloudIsAdmin,
    };
  }

  const matched = Boolean(LOCAL_OWNER_ID && LOCAL_OWNER_EMAIL && normalizedEmail && normalizedEmail === LOCAL_OWNER_EMAIL);
  return {
    matched,
    ownerId: matched ? LOCAL_OWNER_ID : "",
    email: normalizedEmail,
    plan: matched ? LOCAL_OWNER_PLAN : "free",
    role: matched ? LOCAL_OWNER_ROLE : "user",
    isAdmin: matched ? LOCAL_OWNER_IS_ADMIN : false,
  };
}

export function getCurrentLocalOwnerBinding(): LocalOwnerBinding {
  if (currentCloudOwnerId) {
    return getLocalOwnerBinding(currentCloudEmail);
  }
  return getLocalOwnerBinding(currentCloudEmail);
}
