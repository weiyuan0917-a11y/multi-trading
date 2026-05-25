"use client";

import { normalizePlan, type SubscriptionPlan } from "@/lib/entitlements";
import { localAgentDelete, localAgentGet, localAgentPost, localAgentPut } from "@/lib/local-agent-api";

const LOCAL_LICENSE_CACHE_KEY = "mt_local_license_cache_v1";

export type LocalLicense = {
  owner_id?: string;
  ownerId?: string;
  plan?: string;
  status?: string;
  role?: string;
  is_admin?: boolean;
  isAdmin?: boolean;
  features?: string[];
  expires_at?: string | null;
  expiresAt?: string | null;
  subscription_expires_at?: string | null;
  subscriptionExpiresAt?: string | null;
  subscription_current_period_end?: number | null;
  subscriptionCurrentPeriodEnd?: number | null;
  issued_at?: string | null;
  source?: string;
  signature?: string;
  signature_status?: string;
  validation_reason?: string;
  valid?: boolean;
};

export type LocalLicenseStatus = {
  ok?: boolean;
  owner_id?: string;
  license?: LocalLicense | null;
  valid?: boolean;
  reason?: string;
};

export type LocalLicenseImportPreview = {
  ok?: boolean;
  owner_id?: string;
  current?: LocalLicense | null;
  incoming?: LocalLicense | null;
  can_import?: boolean;
  action?: string;
  reason?: string;
};

function normalizeStatus(value: unknown): string {
  const raw = String(value || "active").trim().toLowerCase();
  return raw || "active";
}

function parseExpiresAt(license: LocalLicense): number | null {
  const raw = String(license.expires_at || license.expiresAt || "").trim();
  if (!raw) return null;
  const ts = Date.parse(raw);
  return Number.isFinite(ts) ? ts : null;
}

function licenseOwner(license: LocalLicense | null | undefined): string {
  return String(license?.owner_id || license?.ownerId || "").trim().toLowerCase();
}

export function isLocalLicenseUsable(
  license: LocalLicense | null | undefined,
  expectedOwnerId = ""
): license is LocalLicense {
  if (!license || license.valid === false) return false;
  if (String(license.source || "").trim().toLowerCase() === "convex_session_cache") return false;
  const expectedOwner = String(expectedOwnerId || "").trim().toLowerCase();
  if (expectedOwner && licenseOwner(license) && licenseOwner(license) !== expectedOwner) return false;
  const status = normalizeStatus(license.status);
  if (status !== "active" && status !== "trialing") return false;
  const expiresAt = parseExpiresAt(license);
  if (expiresAt !== null && expiresAt < Date.now()) return false;
  return true;
}

export function localLicensePlan(license: LocalLicense | null | undefined): SubscriptionPlan {
  if (!license) return "free";
  return Boolean(license.is_admin || license.isAdmin) ? "premium" : normalizePlan(license.plan);
}

export function localLicenseRole(license: LocalLicense | null | undefined): string {
  if (!license) return "user";
  const role = String(license.role || "user").trim().toLowerCase();
  if (role === "admin" || role === "owner") return role;
  return "user";
}

export function readLocalLicenseCache(): LocalLicense | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(LOCAL_LICENSE_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as LocalLicense;
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

export function writeLocalLicenseCache(license: LocalLicense | null | undefined): void {
  if (typeof window === "undefined") return;
  if (!license) {
    window.localStorage.removeItem(LOCAL_LICENSE_CACHE_KEY);
    return;
  }
  window.localStorage.setItem(LOCAL_LICENSE_CACHE_KEY, JSON.stringify(license));
}

export async function loadLocalLicense(expectedOwnerId = ""): Promise<LocalLicense | null> {
  const cached = readLocalLicenseCache();
  try {
    const response = await localAgentGet<LocalLicenseStatus>("/license/local", {
      cacheTtlMs: 0,
      retries: 0,
      timeoutMs: 4000,
    });
    const license = response.license || null;
    if (response.valid && isLocalLicenseUsable(license, expectedOwnerId)) {
      writeLocalLicenseCache(license);
      return license;
    }
    if (response.ok && !response.valid) writeLocalLicenseCache(null);
  } catch {
    // Local-first mode must keep booting even when the agent is offline.
  }
  return isLocalLicenseUsable(cached, expectedOwnerId) ? cached : null;
}

export async function getLocalLicenseStatus(): Promise<LocalLicenseStatus> {
  return localAgentGet<LocalLicenseStatus>("/license/local", {
    cacheTtlMs: 0,
    retries: 0,
    timeoutMs: 5000,
  });
}

export async function previewLocalLicenseImport(license: LocalLicense): Promise<LocalLicenseImportPreview> {
  return localAgentPost<LocalLicenseImportPreview>("/license/local/preview", { license }, {
    cacheTtlMs: 0,
    retries: 0,
    timeoutMs: 8000,
  });
}

export async function importLocalLicense(license: LocalLicense): Promise<LocalLicense> {
  const response = await localAgentPut<{ ok?: boolean; license?: LocalLicense }>("/license/local", { license }, {
    cacheTtlMs: 0,
    retries: 0,
    timeoutMs: 8000,
  });
  const saved = response.license;
  if (!saved) throw new Error("license_import_failed");
  writeLocalLicenseCache(saved);
  return saved;
}

export async function clearLocalLicense(): Promise<void> {
  await localAgentDelete("/license/local", { cacheTtlMs: 0, retries: 0, timeoutMs: 5000 });
  writeLocalLicenseCache(null);
}
