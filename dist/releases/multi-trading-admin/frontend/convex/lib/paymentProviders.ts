export type PaymentProviderId = "manual_qr" | "wechat_native" | "alipay_qr" | "aggregate_qr";
export type PaymentMethod = "wechat" | "alipay" | "other";

export type PaymentProviderConfig = {
  id: PaymentProviderId;
  label: string;
  mode: "manual" | "native" | "aggregate";
  status: "available" | "planned";
  defaultPaymentMethod: PaymentMethod;
};

export type PaymentIntentInput = {
  paymentProvider?: unknown;
  paymentMethod?: unknown;
  orderNo: string;
};

export type PaymentIntent = {
  paymentProvider: PaymentProviderId;
  paymentMethod: PaymentMethod;
  providerOrderId: string;
  providerStatus: string;
  payUrl?: string;
  qrCodeUrl?: string;
  expiresAt?: number;
  providerPayload?: any;
};

export const PAYMENT_PROVIDERS: Record<PaymentProviderId, PaymentProviderConfig> = {
  manual_qr: {
    id: "manual_qr",
    label: "静态码半自动",
    mode: "manual",
    status: "available",
    defaultPaymentMethod: "wechat",
  },
  wechat_native: {
    id: "wechat_native",
    label: "微信 Native",
    mode: "native",
    status: "planned",
    defaultPaymentMethod: "wechat",
  },
  alipay_qr: {
    id: "alipay_qr",
    label: "支付宝二维码",
    mode: "native",
    status: "planned",
    defaultPaymentMethod: "alipay",
  },
  aggregate_qr: {
    id: "aggregate_qr",
    label: "聚合支付",
    mode: "aggregate",
    status: "planned",
    defaultPaymentMethod: "other",
  },
};

export function normalizePaymentProvider(value: unknown): PaymentProviderId {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "wechat_native") return "wechat_native";
  if (raw === "alipay_qr") return "alipay_qr";
  if (raw === "aggregate_qr") return "aggregate_qr";
  return "manual_qr";
}

export function normalizePaymentMethod(value: unknown, provider?: PaymentProviderId): PaymentMethod {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "wechat" || raw === "weixin" || raw === "wx") return "wechat";
  if (raw === "alipay" || raw === "ali") return "alipay";
  if (raw === "other") return "other";
  return PAYMENT_PROVIDERS[provider || "manual_qr"].defaultPaymentMethod;
}

export function providerOrderPrefix(provider: PaymentProviderId): string {
  if (provider === "wechat_native") return "wx";
  if (provider === "alipay_qr") return "ali";
  if (provider === "aggregate_qr") return "agg";
  return "manual";
}

export function createPaymentIntent(input: PaymentIntentInput): PaymentIntent {
  const paymentProvider = normalizePaymentProvider(input.paymentProvider);
  const config = PAYMENT_PROVIDERS[paymentProvider];
  if (config.status !== "available") {
    throw new Error(`payment_provider_not_enabled:${paymentProvider}`);
  }
  const paymentMethod = normalizePaymentMethod(input.paymentMethod, paymentProvider);
  const providerOrderId = `${providerOrderPrefix(paymentProvider)}_${String(input.orderNo || "").trim()}`;

  if (paymentProvider === "manual_qr") {
    return {
      paymentProvider,
      paymentMethod,
      providerOrderId,
      providerStatus: "created",
    };
  }

  throw new Error(`payment_provider_not_implemented:${paymentProvider}`);
}
