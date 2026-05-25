"use client";

import Qqq0dteStrategyPage from "../../strategy/qqq-0dte/page";
import { AutoTradingTabs } from "../auto-trading-tabs";
import { FeatureLockedPanel } from "@/components/entitlement-guard";
import { PageShell } from "@/components/ui/page-shell";
import { useEntitlements } from "@/lib/use-entitlements";

export default function AutoTradingOptions0dtePage() {
  const entitlements = useEntitlements();
  return (
    <>
      <AutoTradingTabs />
      {!entitlements.canUse("option_auto_trading") ? (
        <PageShell>
          <FeatureLockedPanel feature="option_auto_trading" plan={entitlements.plan} title="期权自动交易需要 Premium" />
        </PageShell>
      ) : (
      <Qqq0dteStrategyPage />
      )}
    </>
  );
}
