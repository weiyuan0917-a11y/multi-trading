export type PaymentProviderId = "manual_qr" | "wechat_native" | "alipay_qr" | "aggregate_qr";
export type PaymentMethod = "wechat" | "alipay" | "wise" | "other";
export type PaymentProviderMode = "manual" | "native" | "aggregate";

export type PaymentProviderConfig = {
  id: PaymentProviderId;
  label: string;
  mode: PaymentProviderMode;
  status: "available" | "planned";
  defaultPaymentMethod: PaymentMethod;
  description: string;
};

export const PAYMENT_PROVIDERS: Record<PaymentProviderId, PaymentProviderConfig> = {
  manual_qr: {
    id: "manual_qr",
    label: "静态码半自动",
    mode: "manual",
    status: "available",
    defaultPaymentMethod: "wechat",
    description: "用户扫码付款，管理员确认到账后签发 License。",
  },
  wechat_native: {
    id: "wechat_native",
    label: "微信 Native",
    mode: "native",
    status: "planned",
    defaultPaymentMethod: "wechat",
    description: "预留微信 Native 下单和支付回调。",
  },
  alipay_qr: {
    id: "alipay_qr",
    label: "支付宝二维码",
    mode: "native",
    status: "planned",
    defaultPaymentMethod: "alipay",
    description: "预留支付宝当面付/二维码支付回调。",
  },
  aggregate_qr: {
    id: "aggregate_qr",
    label: "聚合支付",
    mode: "aggregate",
    status: "planned",
    defaultPaymentMethod: "other",
    description: "预留聚合支付统一二维码和回调。",
  },
};

export const PAYMENT_PROVIDER_IDS = Object.keys(PAYMENT_PROVIDERS) as PaymentProviderId[];

export const AVAILABLE_PAYMENT_PROVIDER_IDS = PAYMENT_PROVIDER_IDS.filter(
  (providerId) => PAYMENT_PROVIDERS[providerId].status === "available"
);

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
  if (raw === "wise" || raw === "transferwise") return "wise";
  if (raw === "other") return "other";
  return PAYMENT_PROVIDERS[provider || "manual_qr"].defaultPaymentMethod;
}

export function paymentProviderLabel(value: unknown): string {
  return PAYMENT_PROVIDERS[normalizePaymentProvider(value)].label;
}
