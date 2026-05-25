"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Nav } from "@/components/nav";
import { TopBar } from "@/components/top-bar";
import { BackendConnectionBanner } from "@/components/backend-connection-banner";
import { cloudGet } from "@/lib/cloud-api";
import { authHeaders, getAuthToken, setAuthToken } from "@/lib/auth";
import { setLocalAgentCloudIdentity } from "@/lib/local-agent-api";

type AuthMeResponse = {
  ok?: boolean;
  user?: {
    username?: string;
    email?: string;
    plan?: string;
    role?: string;
    is_admin?: boolean;
  };
};

export function LocalAuthShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [ready, setReady] = useState(false);

  const isAuthRoute = useMemo(() => pathname === "/auth", [pathname]);
  const forceLogin = useMemo(
    () => isAuthRoute && String(searchParams?.get("forceLogin") || "").trim() === "1",
    [isAuthRoute, searchParams]
  );

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      if (forceLogin) {
        setAuthToken("");
        setLocalAgentCloudIdentity({});
        if (!cancelled) setReady(true);
        return;
      }
      const token = getAuthToken();
      if (!token) {
        setLocalAgentCloudIdentity({});
        if (!isAuthRoute) router.replace("/auth");
        if (!cancelled) setReady(true);
        return;
      }
      try {
        const me = await cloudGet<AuthMeResponse>("/auth/me", { headers: authHeaders(token), cacheTtlMs: 0, retries: 0, timeoutMs: 5000 });
        const user = me?.user || {};
        const username = String(user.username || "").trim().toLowerCase();
        setLocalAgentCloudIdentity({
          email: user.email || username,
          ownerId: username,
          plan: user.plan,
          role: user.role,
          isAdmin: user.is_admin,
        });
        if (isAuthRoute) {
          router.replace("/setup");
          return;
        }
      } catch {
        setAuthToken("");
        setLocalAgentCloudIdentity({});
        if (!isAuthRoute) router.replace("/auth");
      } finally {
        if (!cancelled) setReady(true);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [forceLogin, isAuthRoute, pathname, router]);

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-300">
        正在校验登录状态...
      </div>
    );
  }

  if (isAuthRoute) {
    return <main className="mx-auto w-full max-w-xl py-10">{children}</main>;
  }

  return (
    <div className="relative mx-auto flex min-h-screen max-w-[1440px] gap-6 p-6">
      <Nav />
      <main className="min-w-0 flex-1">
        <TopBar />
        <BackendConnectionBanner />
        {children}
      </main>
    </div>
  );
}
