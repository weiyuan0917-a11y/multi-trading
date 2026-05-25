import crypto from "crypto";
import fs from "fs";
import path from "path";

type ManualOrderForFeishu = {
  id?: string;
  orderNo?: string;
  email?: string;
  ownerId?: string;
  plan?: string;
  billingCycle?: string;
  amount?: number;
  amountCny?: number;
  amountHkd?: number;
  currency?: string;
  paymentMethod?: string;
  paymentProvider?: string;
  customerNote?: string;
  status?: string;
  createdAt?: number;
};

type FeishuWebhookConfig = {
  webhookUrl: string;
  secret?: string;
};

type LocalNotificationConfig = {
  webhook?: FeishuWebhookConfig;
  app?: {
    appId: string;
    appSecret: string;
    chatId: string;
  };
};

function firstString(...values: unknown[]) {
  for (const value of values) {
    const raw = String(value || "").trim();
    if (raw) return raw;
  }
  return "";
}

function localNotificationConfigPath() {
  const candidates = [
    path.resolve(process.cwd(), "..", "mcp_server", "notification_config.json"),
    path.resolve(process.cwd(), "mcp_server", "notification_config.json"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || "";
}

function readLocalNotificationConfig(): LocalNotificationConfig {
  try {
    const configPath = localNotificationConfigPath();
    if (!configPath) return {};
    const parsed = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    const bots = Array.isArray(parsed?.feishu_bots) ? parsed.feishu_bots : [];
    const bot = bots.find((item: any) => String(item?.webhook_url || "").trim()) || null;
    const app = parsed?.feishu_app || {};
    const out: LocalNotificationConfig = {};
    if (bot) {
      out.webhook = {
        webhookUrl: String(bot.webhook_url || "").trim(),
        secret: String(bot.secret || "").trim() || undefined,
      };
    }
    if (String(app?.app_id || "").trim() && String(app?.app_secret || "").trim() && String(app?.scheduled_chat_id || "").trim()) {
      out.app = {
        appId: String(app.app_id || "").trim(),
        appSecret: String(app.app_secret || "").trim(),
        chatId: String(app.scheduled_chat_id || "").trim(),
      };
    }
    return out;
  } catch {
    return {};
  }
}

function envFeishuAppConfig() {
  const appId = firstString(process.env.FEISHU_BILLING_APP_ID, process.env.FEISHU_APP_ID);
  const appSecret = firstString(process.env.FEISHU_BILLING_APP_SECRET, process.env.FEISHU_APP_SECRET);
  const chatId = firstString(
    process.env.FEISHU_BILLING_CHAT_ID,
    process.env.MT_FEISHU_BILLING_CHAT_ID,
    process.env.FEISHU_SCHEDULED_CHAT_ID
  );
  if (!appId || !appSecret || !chatId) return null;
  return { appId, appSecret, chatId };
}

function feishuAppConfig() {
  return envFeishuAppConfig() || readLocalNotificationConfig().app || null;
}

function feishuWebhookConfig(): FeishuWebhookConfig | null {
  const webhookUrl = firstString(
    process.env.FEISHU_BILLING_WEBHOOK_URL,
    process.env.MT_FEISHU_BILLING_WEBHOOK_URL,
    process.env.FEISHU_WEBHOOK_URL,
    process.env.MT_FEISHU_WEBHOOK_URL
  );
  if (webhookUrl) {
    return {
      webhookUrl,
      secret: firstString(
        process.env.FEISHU_BILLING_WEBHOOK_SECRET,
        process.env.MT_FEISHU_BILLING_WEBHOOK_SECRET,
        process.env.FEISHU_WEBHOOK_SECRET,
        process.env.MT_FEISHU_WEBHOOK_SECRET
      ),
    };
  }
  return readLocalNotificationConfig().webhook || null;
}

function signFeishu(timestamp: number, secret: string) {
  const stringToSign = `${timestamp}\n${secret}`;
  return crypto.createHmac("sha256", secret).update(stringToSign).digest("base64");
}

async function sendViaFeishuApp(text: string) {
  const config = feishuAppConfig();
  if (!config) return { ok: false, skipped: true, channel: "app", reason: "missing_feishu_app_config" };
  const tokenResponse = await fetch("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ app_id: config.appId, app_secret: config.appSecret }),
    cache: "no-store",
  });
  const tokenBody = await tokenResponse.json().catch(() => null);
  const token = String(tokenBody?.tenant_access_token || "").trim();
  if (!tokenResponse.ok || !token) {
    return { ok: false, channel: "app", status: tokenResponse.status, response: tokenBody };
  }
  const response = await fetch("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id", {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      receive_id: config.chatId,
      msg_type: "text",
      content: JSON.stringify({ text }),
    }),
    cache: "no-store",
  });
  const body = await response.json().catch(() => null);
  const ok = response.ok && body?.code === 0;
  return { ok, channel: "app", status: response.status, response: body };
}

async function sendViaWebhook(text: string) {
  const config = feishuWebhookConfig();
  if (!config?.webhookUrl) return { ok: false, skipped: true, channel: "webhook", reason: "missing_feishu_webhook" };

  const timestamp = Math.floor(Date.now() / 1000);
  const payload: any = {
    msg_type: "text",
    content: { text },
  };
  if (config.secret) {
    payload.timestamp = String(timestamp);
    payload.sign = signFeishu(timestamp, config.secret);
  }

  const response = await fetch(config.webhookUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  const raw = await response.text();
  let body: any = null;
  try {
    body = raw ? JSON.parse(raw) : null;
  } catch {
    body = null;
  }
  const ok = response.ok && (!body || body.code === 0 || body.StatusCode === 0);
  return { ok, channel: "webhook", status: response.status, response: body || raw };
}

function paymentMethodLabel(value: unknown) {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "wechat") return "微信";
  if (raw === "alipay") return "支付宝";
  if (raw === "wise") return "Wise";
  return raw || "其他";
}

function paymentProviderLabel(value: unknown) {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "manual_qr") return "静态码半自动";
  if (raw === "wechat_native") return "微信 Native";
  if (raw === "alipay_qr") return "支付宝二维码";
  if (raw === "aggregate_qr") return "聚合支付";
  return raw || "-";
}

function billingCycleLabel(value: unknown) {
  return String(value || "").trim().toLowerCase() === "year" ? "年付" : "月付";
}

function moneyLabel(order: ManualOrderForFeishu) {
  const amount = Number(order.amount ?? order.amountCny ?? order.amountHkd ?? 0);
  const currency = String(order.currency || "CNY").trim() || "CNY";
  return `${currency} ${amount.toLocaleString("zh-CN")}`;
}

function formatTime(value: unknown) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return "-";
  return new Date(n).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
}

function billingOrderText(order: ManualOrderForFeishu) {
  const orderNo = String(order.orderNo || order.id || "-");
  return [
    "MultiTrading 新付款订单",
    "",
    `订单号：${orderNo}`,
    `订单ID：${order.id || "-"}`,
    `客户邮箱：${order.email || "-"}`,
    `owner_id：${order.ownerId || "-"}`,
    `套餐：${String(order.plan || "-").toUpperCase()} / ${billingCycleLabel(order.billingCycle)}`,
    `金额：${moneyLabel(order)}`,
    `支付方式：${paymentMethodLabel(order.paymentMethod)} · ${paymentProviderLabel(order.paymentProvider)}`,
    `状态：${order.status || "pending"}`,
    `创建时间：${formatTime(order.createdAt)}`,
    order.customerNote ? `客户备注：${order.customerNote}` : "",
    "",
    `飞书确认命令：确认收款 ${orderNo} <流水号或备注>`,
    `也可使用：确认收款并发证 ${orderNo} <流水号或备注>`,
    "确认后会复用后台发证流程：标记已收款、签发 License，并按现有邮件配置发送给客户。",
  ]
    .filter(Boolean)
    .join("\n");
}

export async function notifyBillingOrderCreated(order: ManualOrderForFeishu | null | undefined) {
  if (!order) return { ok: false, skipped: true, reason: "missing_order" };
  const text = billingOrderText(order);
  try {
    const webhook = await sendViaWebhook(text);
    if (webhook.ok) return webhook;
    const app = await sendViaFeishuApp(text);
    return { ...app, fallbackFrom: webhook };
  } catch (err: any) {
    return { ok: false, error: String(err?.message || err) };
  }
}
