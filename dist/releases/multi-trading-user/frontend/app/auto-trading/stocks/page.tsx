"use client";

import AutoTraderPage from "../../auto-trader/page";
import { FeatureLockedPanel } from "@/components/entitlement-guard";
import { PageShell } from "@/components/ui/page-shell";
import { useEntitlements } from "@/lib/use-entitlements";
import { AutoTradingTabs } from "../auto-trading-tabs";

export default function AutoTradingStocksPage() {
  const entitlements = useEntitlements();
  return (
    <>
      <AutoTradingTabs />
      {!entitlements.canUse("stock_auto_trading") ? (
        <PageShell>
          <FeatureLockedPanel feature="stock_auto_trading" plan={entitlements.plan} title="股票自动交易需要 Pro 或 Premium" />
        </PageShell>
      ) : (
        <AutoTraderPage />
      )}
    </>
  );
}
