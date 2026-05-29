import { NextResponse, type NextRequest } from "next/server";
import { customerDisabledResponse, isCustomerBuild } from "@/lib/build-target";

const LOCAL_AGENT_API_BASE =
  process.env.NEXT_PUBLIC_LOCAL_AGENT_API_BASE ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://127.0.0.1:8010";

const CONVEX_SITE_URL =
  process.env.NEXT_PUBLIC_CONVEX_SITE_URL ||
  process.env.NEXT_PUBLIC_CONVEX_HTTP_ACTIONS_URL ||
  process.env.CONVEX_SITE_URL ||
  "";

function json(status: number, body: any) {
  return NextResponse.json(body, { status });
}

function envSecret() {
  return String(process.env.MT_BILLING_WEBHOOK_SECRET || process.env.BILLING_WEBHOOK_SECRET || "").trim();
}

function normalizeBaseUrl(value: string) {
  return String(value || "").replace(/\/+$/, "");
}

async function requireAdmin(request: NextRequest) {
  const authorization = request.headers.get("authorization") || "";
  if (!authorization.trim()) {
    return { ok: false as const, response: json(401, { ok: false, error: "unauthorized" }) };
  }
  try {
    const response = await fetch(`${normalizeBaseUrl(LOCAL_AGENT_API_BASE)}/auth/me`, {
      headers: { authorization },
      cache: "no-store",
    });
    if (!response.ok) {
      return { ok: false as const, response: json(401, { ok: false, error: "unauthorized" }) };
    }
    const data = await response.json();
    const user = data?.user || {};
    const role = String(user.role || "").trim().toLowerCase();
    const isAdmin = Boolean(user.is_admin) || role === "admin" || role === "owner";
    if (!isAdmin) {
      return { ok: false as const, response: json(403, { ok: false, error: "admin_required" }) };
    }
    return { ok: true as const, user };
  } catch (err: any) {
    return { ok: false as const, response: json(502, { ok: false, error: String(err?.message || err) }) };
  }
}

async function callConvex(path: string, init?: RequestInit) {
  const secret = envSecret();
  if (!secret) throw new Error("missing_mt_billing_webhook_secret");
  if (!CONVEX_SITE_URL) throw new Error("missing_convex_site_url");
  const response = await fetch(`${normalizeBaseUrl(CONVEX_SITE_URL)}${path}`, {
    ...init,
    headers: {
      "X-MT-Webhook-Secret": secret,
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  const text = await response.text();
  let body: any = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { ok: false, error: text || `convex_${response.status}` };
  }
  if (!response.ok) {
    throw new Error(String(body?.error || body?.message || `convex_${response.status}`));
  }
  return body;
}

export async function GET(request: NextRequest) {
  if (isCustomerBuild()) return customerDisabledResponse();
  const admin = await requireAdmin(request);
  if (!admin.ok) return admin.response;
  try {
    const limit = Math.max(1, Math.min(100, Number(request.nextUrl.searchParams.get("limit") || 25)));
    const q = String(request.nextUrl.searchParams.get("q") || "").trim();
    const params = new URLSearchParams({ limit: String(limit) });
    if (q) params.set("q", q);
    const result = await callConvex(`/billing/license-deliveries?${params.toString()}`);
    return json(200, result);
  } catch (err: any) {
    return json(400, { ok: false, error: String(err?.message || err) });
  }
}

export async function POST(request: NextRequest) {
  if (isCustomerBuild()) return customerDisabledResponse();
  const admin = await requireAdmin(request);
  if (!admin.ok) return admin.response;
  let body: any;
  try {
    body = await request.json();
  } catch {
    return json(400, { ok: false, error: "invalid_json" });
  }
  try {
    if (body?.action) {
      const result = await callConvex("/billing/license-admin", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      return json(200, result);
    }

    const result = await callConvex("/billing/license-webhook", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        ...body,
        provider: body?.provider || "admin",
        provider_event_id: body?.provider_event_id || `admin_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      }),
    });
    return json(200, result);
  } catch (err: any) {
    return json(400, { ok: false, error: String(err?.message || err) });
  }
}
