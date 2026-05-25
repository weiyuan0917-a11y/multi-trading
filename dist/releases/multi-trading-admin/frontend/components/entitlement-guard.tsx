"use client";

import { PLAN_LABELS, requiredPlanFor, type EntitlementKey, type SubscriptionPlan } from "@/lib/entitlements";

type EntitlementNoticeProps = {
  feature: EntitlementKey;
  plan: SubscriptionPlan;
  title?: string;
  className?: string;
};

export function EntitlementNotice({ feature, plan, title = "当前订阅暂不可用", className = "" }: EntitlementNoticeProps) {
  const required = requiredPlanFor(feature);
  return (
    <div className={`rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-100 ${className}`}>
      <div className="font-semibold">{title}</div>
      <div className="mt-1 text-amber-50/80">
        当前为 {PLAN_LABELS[plan]}；此功能需要 {PLAN_LABELS[required]}。
      </div>
    </div>
  );
}

export function FeatureLockedPanel({ feature, plan, title = "当前套餐暂不可用", className = "" }: EntitlementNoticeProps) {
  const required = requiredPlanFor(feature);
  return (
    <div className={`panel border-amber-500/35 bg-amber-500/10 ${className}`}>
      <div className="text-base font-semibold text-amber-100">{title}</div>
      <div className="mt-2 text-sm leading-6 text-amber-50/80">
        当前为 {PLAN_LABELS[plan]}；此功能需要 {PLAN_LABELS[required]}。Research、回测、OpenBB 与 TradingAgents 仍可继续使用。
      </div>
    </div>
  );
}
