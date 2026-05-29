import { NextResponse, type NextRequest } from "next/server";
import { notifyBillingOrderCreated } from "@/lib/server/feishu-billing";

const CONVEX_SITE_URL =
  process.env.NEXT_PUBLIC_CONVEX_SITE_URL ||
  process.env.NEXT_PUBLIC_CONVEX_HTTP_ACTIONS_URL ||
  process.env.CONVEX_SITE_URL ||
  "";
const BILLING_PUBLIC_ORDER_API_URL =
  process.env.BILLING_PUBLIC_ORDER_API_URL ||
  process.env.NEXT_PUBLIC_BILLING_PUBLIC_ORDER_API_URL ||
  process.env.BILLING_ORDER_API_URL ||
  process.env.NEXT_PUBLIC_BILLING_ORDER_API_URL ||
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

async function callConvexPublic(path: string, init?: RequestInit) {
  if (!CONVEX_SITE_URL) return null;
  const response = await fetch(`${normalizeBaseUrl(CONVEX_SITE_URL)}${path}`, {
    ...init,
    headers: {
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

async function callBillingOrderApi(body: any) {
  if (!BILLING_PUBLIC_ORDER_API_URL) return null;
  const response = await fetch(BILLING_PUBLIC_ORDER_API_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const text = await response.text();
  let parsed: any = {};
  try {
    parsed = text ? JSON.parse(text) : {};
  } catch {
    parsed = { ok: false, error: text || `billing_order_api_${response.status}` };
  }
  if (!response.ok) {
    throw new Error(String(parsed?.error || parsed?.message || `billing_order_api_${response.status}`));
  }
  return parsed;
}

export async function POST(request: NextRequest) {
  let body: any;
  try {
    body = await request.json();
  } catch {
    return json(400, { ok: false, error: "invalid_json" });
  }
  try {
    const result =
      (await callBillingOrderApi(body)) ||
      (await callConvexPublic("/billing/public/manual-orders", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      }).catch((err) => {
        if (envSecret()) return null;
        throw err;
      })) ||
      (await callConvex("/billing/manual-orders", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      }));
    const feishu = await notifyBillingOrderCreated(result?.order);
    return json(200, { ...result, feishuNotification: feishu });
  } catch (err: any) {
    return json(400, { ok: false, error: String(err?.message || err) });
  }
}
