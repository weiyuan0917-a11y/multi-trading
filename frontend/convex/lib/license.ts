import { normalizePlan, normalizeRole } from "./plans";

export type LocalLicensePlan = "free" | "pro" | "premium";
export type LocalLicenseStatus = "active" | "trialing" | "inactive" | "canceled" | "expired";

export type SignedLocalLicense = {
  owner_id: string;
  plan: LocalLicensePlan;
  status: LocalLicenseStatus;
  role: string;
  is_admin: boolean;
  features: string[];
  expires_at: string;
  subscription_expires_at?: string;
  subscription_current_period_end?: number;
  issued_at: string;
  source: string;
  signature_alg?: string;
  signature_kid?: string;
  signature: string;
};

export function normalizeLicenseTtlDays(value: unknown) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 7;
  return Math.max(1, Math.min(45, Math.floor(n)));
}

export function normalizeOwnerId(value: unknown) {
  return String(value || "").trim().toLowerCase();
}

export function assertOwnerId(value: unknown) {
  const ownerId = normalizeOwnerId(value);
  if (!/^[a-z0-9][a-z0-9_-]{2,39}$/.test(ownerId)) {
    throw new Error("invalid_owner_id");
  }
  if (["admin", "root", "system", "__system__", "null", "undefined"].includes(ownerId)) {
    throw new Error("reserved_owner_id");
  }
  return ownerId;
}

export function licenseFeatures(plan: string, isAdmin: boolean) {
  const effectivePlan = isAdmin ? "premium" : normalizePlan(plan);
  const base = ["research", "backtest", "tradingagents", "openbb"];
  if (effectivePlan === "pro") return [...base, "stock_auto_trading"];
  if (effectivePlan === "premium") {
    return [...base, "stock_auto_trading", "option_auto_trading", "multi_broker", "multi_account"];
  }
  return base;
}

export function stableJson(value: any): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map((item) => stableJson(item)).join(",")}]`;
  return `{${Object.keys(value)
    .filter((key) => value[key] !== undefined)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
    .join(",")}}`;
}

export async function hmacSha256Hex(secret: string, payload: string) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(payload));
  return Array.from(new Uint8Array(signature))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function envPem(value: unknown) {
  return String(value || "").trim().replace(/\\n/g, "\n");
}

function base64ToBytes(input: string) {
  const clean = input.replace(/\s/g, "");
  if (typeof atob === "function") {
    const bin = atob(clean);
    return Uint8Array.from(bin, (ch) => ch.charCodeAt(0));
  }
  return Uint8Array.from(Buffer.from(clean, "base64"));
}

function arrayBufferToBase64(input: ArrayBuffer) {
  const bytes = new Uint8Array(input);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  if (typeof btoa === "function") return btoa(bin);
  return Buffer.from(bytes).toString("base64");
}

function pemToPkcs8Der(privateKeyPem: string) {
  const body = privateKeyPem
    .replace(/-----BEGIN PRIVATE KEY-----/g, "")
    .replace(/-----END PRIVATE KEY-----/g, "")
    .replace(/\s/g, "");
  if (!body) throw new Error("invalid_private_key_pem");
  return base64ToBytes(body);
}

async function rsaPssSha256Base64(privateKeyPem: string, payload: string) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "pkcs8",
    pemToPkcs8Der(privateKeyPem),
    { name: "RSA-PSS", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign({ name: "RSA-PSS", saltLength: 32 }, key, encoder.encode(payload));
  return arrayBufferToBase64(signature);
}

export function licenseExpiresAt(currentPeriodEnd?: unknown, nowMs = Date.now()) {
  const ttlMs = normalizeLicenseTtlDays(process.env.CONVEX_LOCAL_LICENSE_TTL_DAYS) * 24 * 60 * 60 * 1000;
  const ttlEnd = nowMs + ttlMs;
  const rawPeriodEnd = Number(currentPeriodEnd || 0);
  const periodEnd = rawPeriodEnd > 0 && rawPeriodEnd < 100000000000 ? rawPeriodEnd * 1000 : rawPeriodEnd;
  const expires = periodEnd > nowMs ? Math.min(ttlEnd, periodEnd) : ttlEnd;
  return new Date(expires).toISOString();
}

export function normalizePeriodEndMs(value: unknown) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return undefined;
  return n < 100000000000 ? Math.floor(n * 1000) : Math.floor(n);
}

export async function createSignedLocalLicense({
  ownerId,
  plan,
  status,
  role,
  isAdmin,
  currentPeriodEnd,
  source,
  nowMs = Date.now(),
}: {
  ownerId: string;
  plan: string;
  status: LocalLicenseStatus;
  role?: string;
  isAdmin?: boolean;
  currentPeriodEnd?: unknown;
  source: string;
  nowMs?: number;
}): Promise<{ license: SignedLocalLicense; issuedAt: string; expiresAt: string }> {
  const privateKeyPem = envPem(
    process.env.CONVEX_LOCAL_LICENSE_PRIVATE_KEY_PEM ||
      process.env.LOCAL_LICENSE_PRIVATE_KEY_PEM ||
      process.env.CONVEX_LOCAL_LICENSE_RSA_PRIVATE_KEY_PEM ||
      process.env.LOCAL_LICENSE_RSA_PRIVATE_KEY_PEM ||
      ""
  );
  const signatureKid = String(
    process.env.CONVEX_LOCAL_LICENSE_KEY_ID ||
      process.env.LOCAL_LICENSE_KEY_ID ||
      process.env.CONVEX_LOCAL_LICENSE_SIGNATURE_KID ||
      process.env.LOCAL_LICENSE_SIGNATURE_KID ||
      ""
  ).trim();
  const secret = String(
    process.env.CONVEX_LOCAL_LICENSE_SIGNING_SECRET ||
      process.env.LOCAL_LICENSE_SIGNING_SECRET ||
      ""
  ).trim();
  if (!privateKeyPem && !secret) throw new Error("license_signing_secret_required");

  const cleanPlan = normalizePlan(plan);
  const cleanRole = normalizeRole(role);
  const admin = Boolean(isAdmin) || cleanRole === "admin" || cleanRole === "owner";
  const issuedAt = new Date(nowMs).toISOString();
  const subscriptionPeriodEnd = normalizePeriodEndMs(currentPeriodEnd);
  const signatureAlg = privateKeyPem ? "rsa-pss-sha256" : "hmac-sha256";
  const unsignedLicense = {
    owner_id: assertOwnerId(ownerId),
    plan: admin ? "premium" : cleanPlan,
    status,
    role: admin && cleanRole === "user" ? "admin" : cleanRole,
    is_admin: admin,
    features: licenseFeatures(cleanPlan, admin),
    expires_at: licenseExpiresAt(currentPeriodEnd, nowMs),
    subscription_expires_at: subscriptionPeriodEnd ? new Date(subscriptionPeriodEnd).toISOString() : undefined,
    subscription_current_period_end: subscriptionPeriodEnd,
    issued_at: issuedAt,
    source,
    signature_alg: signatureAlg,
    signature_kid: signatureKid || undefined,
  };
  const signature = privateKeyPem
    ? `rsa-pss-sha256=${await rsaPssSha256Base64(privateKeyPem, stableJson(unsignedLicense))}`
    : `sha256=${await hmacSha256Hex(secret, stableJson(unsignedLicense))}`;
  const license = { ...unsignedLicense, signature } as SignedLocalLicense;
  return { license, issuedAt, expiresAt: license.expires_at };
}
