"use client";

import { useClerk, useUser } from "@clerk/nextjs";
import { TopBar } from "@/components/top-bar";
import { activeLocalOwnerId, effectiveCloudPlan, useCloudSession } from "@/lib/use-cloud-session";

export function ClerkTopBar() {
  const { signOut } = useClerk();
  const { user } = useUser();
  const cloudSession = useCloudSession();
  const email = user?.primaryEmailAddress?.emailAddress || user?.emailAddresses?.[0]?.emailAddress || "";
  const username = user?.username || user?.fullName || email || user?.id || "";
  const cloudUser = cloudSession.data?.user;

  return (
    <TopBar
      authMode="clerk"
      cloudUsername={username}
      cloudEmail={email}
      cloudOwnerId={activeLocalOwnerId(cloudSession.data)}
      cloudPlan={effectiveCloudPlan(cloudSession.data)}
      cloudRole={cloudUser?.role}
      cloudIsAdmin={cloudUser?.isAdmin}
      onCloudSignOut={() => signOut()}
    />
  );
}
