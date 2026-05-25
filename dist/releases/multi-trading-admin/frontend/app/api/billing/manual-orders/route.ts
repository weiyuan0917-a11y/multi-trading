import { NextResponse, type NextRequest } from "next/server";

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

export async function POST(request: NextRequest) {
  let body: any;
  try {
    body = await request.json();
  } catch {
    return json(400, { ok: false, error: "invalid_json" });
  }
  try {
    const result = await callConvex("/billing/manual-orders", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return json(200, result);
  } catch (err: any) {
    return json(400, { ok: false, error: String(err?.message || err) });
  }
}
