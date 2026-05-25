export type SubscriptionPlan = "free" | "pro" | "premium";

export type EntitlementKey =
  | "research"
  | "backtest"
  | "tradingagents"
  | "openbb"
  | "stock_auto_trading"
  | "option_auto_trading"
  | "multi_broker"
  | "multi_account";

export type EntitlementMap = Record<EntitlementKey, boolean>;

export const PLAN_LABELS: Record<SubscriptionPlan, string> = {
  free: "Free",
  pro: "Pro",
  premium: "Premium",
};

const PLAN_RANK: Record<SubscriptionPlan, number> = {
  free: 0,
  pro: 1,
  premium: 2,
};

export const PLAN_ENTITLEMENTS: Record<SubscriptionPlan, EntitlementMap> = {
  free: {
    research: true,
    backtest: true,
    tradingagents: true,
    openbb: true,
    stock_auto_trading: false,
    option_auto_trading: false,
    multi_broker: false,
    multi_account: false,
  },
  pro: {
    research: true,
    backtest: true,
    tradingagents: true,
    openbb: true,
    stock_auto_trading: true,
    option_auto_trading: false,
    multi_broker: false,
    multi_account: false,
  },
  premium: {
    research: true,
    backtest: true,
    tradingagents: true,
    openbb: true,
    stock_auto_trading: true,
    option_auto_trading: true,
    multi_broker: true,
    multi_account: true,
  },
};

const FEATURE_REQUIRED_PLAN: Record<EntitlementKey, SubscriptionPlan> = {
  research: "free",
  backtest: "free",
  tradingagents: "free",
  openbb: "free",
  stock_auto_trading: "pro",
  option_auto_trading: "premium",
  multi_broker: "premium",
  multi_account: "premium",
};

export function normalizePlan(value: unknown): SubscriptionPlan {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "premium") return "premium";
  if (raw === "pro") return "pro";
  return "free";
}

export function getMockPlan(): SubscriptionPlan {
  const envPlan = normalizePlan(process.env.NEXT_PUBLIC_MT_PLAN);
  if (typeof window === "undefined") return envPlan;
  const localPlan = window.localStorage.getItem("mt_mock_plan");
  return localPlan ? normalizePlan(localPlan) : envPlan;
}

export function getEntitlements(plan: SubscriptionPlan): EntitlementMap {
  return PLAN_ENTITLEMENTS[plan] || PLAN_ENTITLEMENTS.free;
}

export function canUse(plan: SubscriptionPlan, feature: EntitlementKey): boolean {
  return Boolean(getEntitlements(plan)[feature]);
}

export function requiredPlanFor(feature: EntitlementKey): SubscriptionPlan {
  return FEATURE_REQUIRED_PLAN[feature];
}

export function planMeets(plan: SubscriptionPlan, minimum: SubscriptionPlan): boolean {
  return PLAN_RANK[plan] >= PLAN_RANK[minimum];
}
