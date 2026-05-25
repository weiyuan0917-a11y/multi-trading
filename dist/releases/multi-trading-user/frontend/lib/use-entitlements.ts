"use client";

import { useEffect, useState } from "react";
import {
  canUse,
  getMockPlan,
  normalizePlan,
  PLAN_LABELS,
  planMeets,
  requiredPlanFor,
  type EntitlementKey,
  type SubscriptionPlan,
} from "@/lib/entitlements";
import { cloudGet } from "@/lib/cloud-api";
import { authHeaders, getAuthToken } from "@/lib/auth";
import { getCurrentLocalOwnerBinding, getLocalOwnerBinding } from "@/lib/local-owner-binding";
import { loadLocalLicense, localLicensePlan, localLicenseRole, type LocalLicense } from "@/lib/local-license";
import { effectiveCloudPlan, useCloudSession } from "@/lib/use-cloud-session";

type AuthMeResponse = {
  user?: {
    username?: string;
    plan?: string;
    role?: string;
    is_admin?: boolean;
  };
};

type EntitlementIdentity = {
  email?: string | null;
};

function normalizeRole(value: unknown): string {
  const role = String(value || "user").trim().toLowerCase();
  return role || "user";
}

function isAdminRole(value: unknown): boolean {
  const role = normalizeRole(value);
  return role === "admin" || role === "owner";
}

function strongerPlan(left: SubscriptionPlan, right: SubscriptionPlan): SubscriptionPlan {
  return planMeets(left, right) ? left : right;
}

function mergeLicenseEntitlement(
  basePlan: SubscriptionPlan,
  baseRole: string,
  baseIsAdmin: boolean,
  license?: LocalLicense | null
): { plan: SubscriptionPlan; role: string; isAdmin: boolean } {
  const cleanBaseRole = normalizeRole(baseRole);
  const cleanBaseIsAdmin = Boolean(baseIsAdmin) || isAdminRole(cleanBaseRole);
  if (!license) {
    return {
      plan: cleanBaseIsAdmin ? "premium" : basePlan,
      role: cleanBaseIsAdmin && cleanBaseRole === "user" ? "admin" : cleanBaseRole,
      isAdmin: cleanBaseIsAdmin,
    };
  }

  const licenseRole = localLicenseRole(license);
  const licenseIsAdmin = Boolean(license.is_admin || license.isAdmin) || isAdminRole(licenseRole);
  const nextIsAdmin = cleanBaseIsAdmin || licenseIsAdmin;
  const nextRole = nextIsAdmin
    ? isAdminRole(cleanBaseRole)
      ? cleanBaseRole
      : isAdminRole(licenseRole)
        ? licenseRole
        : "admin"
    : licenseRole || cleanBaseRole;
  return {
    plan: nextIsAdmin ? "premium" : strongerPlan(basePlan, localLicensePlan(license)),
    role: nextRole,
    isAdmin: nextIsAdmin,
  };
}

export function useEntitlements(identity?: EntitlementIdentity) {
  const [plan, setPlan] = useState<SubscriptionPlan>("free");
  const [role, setRole] = useState("user");
  const [isAdmin, setIsAdmin] = useState(false);
  const [username, setUsername] = useState("");
  const [source, setSource] = useState<"convex" | "local_owner" | "local_license" | "local_session" | "mock">("mock");
  const identityEmail = String(identity?.email || "");
  const cloudSession = useCloudSession();

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const cloud = cloudSession.data;
      if (cloud?.user) {
        const nextPlan = effectiveCloudPlan(cloud);
        const nextRole = normalizeRole(cloud.user.role);
        const nextIsAdmin = Boolean(cloud.user.isAdmin) || isAdminRole(nextRole);
        const nextUsername = String(cloud.localOwnerBinding?.ownerId || cloud.user.email || "");
        const localLicense = nextUsername ? await loadLocalLicense(nextUsername) : null;
        if (cancelled) return;
        const merged = mergeLicenseEntitlement(nextIsAdmin ? "premium" : nextPlan, nextRole, nextIsAdmin, localLicense);
        setUsername(nextUsername);
        setRole(merged.role);
        setIsAdmin(merged.isAdmin);
        setPlan(merged.plan);
        setSource(localLicense ? "local_license" : "convex");
        return;
      }

      const localBinding = identityEmail ? getLocalOwnerBinding(identityEmail) : getCurrentLocalOwnerBinding();
      if (localBinding.matched) {
        const bindingRole = normalizeRole(localBinding.role);
        const bindingIsAdmin = Boolean(localBinding.isAdmin) || isAdminRole(bindingRole);
        const bindingPlan = bindingIsAdmin ? "premium" : normalizePlan(localBinding.plan);
        const localLicense = localBinding.ownerId ? await loadLocalLicense(localBinding.ownerId) : null;
        if (cancelled) return;
        const merged = mergeLicenseEntitlement(bindingPlan, bindingRole, bindingIsAdmin, localLicense);
        setUsername(localBinding.ownerId);
        setRole(merged.role);
        setIsAdmin(merged.isAdmin);
        setPlan(merged.plan);
        setSource(localLicense ? "local_license" : "local_owner");
        return;
      }

      const fallbackPlan = getMockPlan();
      const token = getAuthToken();
      if (token) {
        try {
          const me = await cloudGet<AuthMeResponse>("/auth/me", {
            headers: authHeaders(token),
            cacheTtlMs: 0,
            retries: 0,
            timeoutMs: 8000,
          });
          if (cancelled) return;
          const user = me?.user || {};
          const sessionRole = normalizeRole(user.role);
          const sessionIsAdmin = Boolean(user.is_admin) || isAdminRole(sessionRole);
          const sessionPlan = sessionIsAdmin ? "premium" : normalizePlan(user.plan || fallbackPlan);

          const localLicense = await loadLocalLicense(String(user.username || ""));
          if (!cancelled && localLicense) {
            const merged = mergeLicenseEntitlement(sessionPlan, sessionRole, sessionIsAdmin, localLicense);
            setUsername(String(localLicense.owner_id || localLicense.ownerId || user.username || ""));
            setRole(merged.role);
            setIsAdmin(merged.isAdmin);
            setPlan(merged.plan);
            setSource("local_license");
            return;
          }

          setUsername(String(user.username || ""));
          setRole(sessionRole);
          setIsAdmin(sessionIsAdmin);
          setPlan(sessionPlan);
          setSource("local_session");
          return;
        } catch {
          if (!cancelled) setPlan(fallbackPlan);
        }
      }

      setPlan(fallbackPlan);
      setRole("user");
      setIsAdmin(false);
      setSource("mock");
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [cloudSession.data, identityEmail]);

  return {
    plan,
    planLabel: PLAN_LABELS[plan],
    role,
    isAdmin,
    username,
    source,
    cloudStatus: cloudSession.status,
    canUse: (feature: EntitlementKey) => canUse(plan, feature),
    requiredPlanFor,
  };
}
