"use client";

import Qqq1dteStrategyPage from "../../strategy/qqq-1dte/page";
import { AutoTradingTabs } from "../auto-trading-tabs";
import { FeatureLockedPanel } from "@/components/entitlement-guard";
import { PageShell } from "@/components/ui/page-shell";
import { useEntitlements } from "@/lib/use-entitlements";

export default function AutoTradingOptions1dtePage() {
  const entitlements = useEntitlements();
  return (
    <>
      <AutoTradingTabs />
      {!entitlements.canUse("option_auto_trading") ? (
        <PageShell>
          <FeatureLockedPanel feature="option_auto_trading" plan={entitlements.plan} title="期权自动交易需要 Premium" />
        </PageShell>
      ) : (
      <Qqq1dteStrategyPage />
      )}
    </>
  );
}
