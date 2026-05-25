export type SubscriptionPlan = "free" | "pro" | "premium";
export type UserRole = "user" | "admin" | "owner";

export function normalizePlan(value: unknown): SubscriptionPlan {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "premium") return "premium";
  if (raw === "pro") return "pro";
  return "free";
}

export function normalizeRole(value: unknown): UserRole {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "owner") return "owner";
  if (raw === "admin") return "admin";
  return "user";
}

export function entitlementsForPlan(plan: SubscriptionPlan, isAdmin = false) {
  const effectivePlan = isAdmin ? "premium" : plan;
  return {
    research: true,
    backtest: true,
    tradingagents: true,
    openbb: true,
    stockAutoTrading: effectivePlan === "pro" || effectivePlan === "premium",
    optionAutoTrading: effectivePlan === "premium",
    multiBroker: effectivePlan === "premium",
    multiAccount: effectivePlan === "premium",
    source: isAdmin ? ("admin" as const) : ("plan" as const),
  };
}

