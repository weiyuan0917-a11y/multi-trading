"use client";

import { useUser } from "@clerk/nextjs";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Nav } from "@/components/nav";
import { ClerkTopBar } from "@/components/clerk-top-bar";
import { BackendConnectionBanner } from "@/components/backend-connection-banner";
import { ConvexUserSync } from "@/components/convex-user-sync";
import { CONVEX_ENABLED } from "@/lib/convex-mode";
import { setLocalAgentCloudIdentity } from "@/lib/local-agent-api";
import { getLocalOwnerBinding } from "@/lib/local-owner-binding";
import { isLocalOnboardingComplete, loadLocalOnboardingSnapshot } from "@/lib/onboarding-state";
import { activeLocalOwnerId, effectiveCloudPlan, useCloudSession } from "@/lib/use-cloud-session";

export function ClerkAuthShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isLoaded, isSignedIn, user } = useUser();
  const cloudSession = useCloudSession();
  const isAuthRoute = pathname === "/auth";
  const forceLogin = isAuthRoute && String(searchParams?.get("forceLogin") || "").trim() === "1";
  const isOnboardingRoute = Boolean(pathname?.startsWith("/onboarding"));
  const isProfileRoute = Boolean(pathname?.startsWith("/profile"));
  const email = user?.primaryEmailAddress?.emailAddress || user?.emailAddresses?.[0]?.emailAddress || "";
  const cloudOwnerId = activeLocalOwnerId(cloudSession.data);
  const cloudUser = cloudSession.data?.user;
  const effectivePlan = effectiveCloudPlan(cloudSession.data);
  const identityKey = [
    email,
    cloudOwnerId,
    effectivePlan,
    cloudUser?.role || "",
    cloudUser?.isAdmin ? "1" : "0",
  ].join("|");
  const [readyIdentityKey, setReadyIdentityKey] = useState("");
  const [localOnboardingStatus, setLocalOnboardingStatus] = useState<"idle" | "loading" | "complete" | "incomplete">("idle");
  const [clerkLoadTimedOut, setClerkLoadTimedOut] = useState(false);
  const localBinding = getLocalOwnerBinding(email);
  const hasLocalOwnerBinding = Boolean(cloudOwnerId || localBinding.matched);
  const hasCompletedOnboarding = Boolean(cloudSession.data?.user?.onboardingCompletedAt || localBinding.matched);
  const plan = String(effectivePlan || localBinding.plan || "free").toLowerCase();
  const role = String(cloudUser?.role || localBinding.role || "user").toLowerCase();
  const apiKeyRequired = Boolean(
    cloudUser?.isAdmin || localBinding.isAdmin || role === "admin" || plan === "pro" || plan === "premium"
  );
  const shouldWaitForLocalIdentity = !forceLogin && isLoaded && Boolean(isSignedIn) && readyIdentityKey !== identityKey;
  const shouldWaitForCloud =
    !forceLogin && isLoaded && Boolean(isSignedIn) && !localBinding.matched && cloudSession.status === "loading";
  const shouldCheckLocalOnboarding =
    !forceLogin &&
    isLoaded &&
    Boolean(isSignedIn) &&
    !shouldWaitForLocalIdentity &&
    !shouldWaitForCloud &&
    hasLocalOwnerBinding &&
    !hasCompletedOnboarding;
  const shouldRedirectToOnboarding =
    !forceLogin &&
    isLoaded &&
    Boolean(isSignedIn) &&
    !shouldWaitForLocalIdentity &&
    !shouldWaitForCloud &&
    (!hasLocalOwnerBinding || localOnboardingStatus === "incomplete") &&
    !isOnboardingRoute &&
    !isProfileRoute;
  const onboardingNextPath = pathname && pathname !== "/" && pathname !== "/auth" ? pathname : "/setup";
  const onboardingHref = `/onboarding?next=${encodeURIComponent(onboardingNextPath)}`;

  useEffect(() => {
    if (isLoaded) {
      setClerkLoadTimedOut(false);
      return;
    }
    const timer = window.setTimeout(() => setClerkLoadTimedOut(true), 8000);
    return () => window.clearTimeout(timer);
  }, [isLoaded]);

  useEffect(() => {
    setLocalAgentCloudIdentity({
      email,
      ownerId: cloudOwnerId,
      plan: effectivePlan,
      role: cloudUser?.role,
      isAdmin: cloudUser?.isAdmin,
    });
    setReadyIdentityKey(identityKey);
  }, [cloudOwnerId, cloudUser?.isAdmin, cloudUser?.role, effectivePlan, email, identityKey]);

  useEffect(() => {
    if (isAuthRoute || !isLoaded || isSignedIn) return;
    router.replace("/auth?forceLogin=1");
  }, [isAuthRoute, isLoaded, isSignedIn, router]);

  useEffect(() => {
    if (!shouldRedirectToOnboarding) return;
    router.replace(onboardingHref);
    if (typeof window === "undefined") return;
    const timer = window.setTimeout(() => {
      if (!window.location.pathname.startsWith("/onboarding")) {
        window.location.replace(onboardingHref);
      }
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [onboardingHref, router, shouldRedirectToOnboarding]);

  useEffect(() => {
    if (!shouldCheckLocalOnboarding) {
      setLocalOnboardingStatus("idle");
      return;
    }
    let cancelled = false;
    setLocalOnboardingStatus("loading");
    void loadLocalOnboardingSnapshot(apiKeyRequired)
      .then((snapshot) => {
        if (cancelled) return;
        setLocalOnboardingStatus(
          isLocalOnboardingComplete({ ownerBound: hasLocalOwnerBinding, snapshot, apiKeyRequired })
            ? "complete"
            : "incomplete"
        );
      })
      .catch(() => {
        if (!cancelled) setLocalOnboardingStatus("incomplete");
      });
    return () => {
      cancelled = true;
    };
  }, [apiKeyRequired, hasLocalOwnerBinding, shouldCheckLocalOnboarding]);

  useEffect(() => {
    if (forceLogin || !isAuthRoute || !isLoaded || !isSignedIn) return;
    if (shouldRedirectToOnboarding) return;
    router.replace("/dashboard");
    if (typeof window === "undefined") return;
    const timer = window.setTimeout(() => {
      if (window.location.pathname === "/auth") {
        window.location.replace("/dashboard");
      }
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [forceLogin, isAuthRoute, isLoaded, isSignedIn, router, shouldRedirectToOnboarding]);

  const renderClerkRecovery = (message: string) => (
    <div className="flex min-h-screen items-center justify-center p-6 text-sm text-slate-300">
      <div className="panel max-w-xl space-y-4 border-amber-300/30 bg-slate-950/90 text-center">
        <div className="text-base font-semibold text-slate-100">Clerk 登录服务响应较慢</div>
        <div>{message}</div>
        <div className="text-xs leading-6 text-slate-500">
          这通常是 Clerk 开发域名或本机网络代理超时导致的，不是本地 Agent 崩溃。可以先回到本地登录入口，或刷新后重试。
        </div>
        <div className="flex flex-wrap justify-center gap-2">
          <a className="btn-primary" href="/auth?forceLogin=1">
            打开登录页
          </a>
          <button className="btn-secondary" type="button" onClick={() => window.location.reload()}>
            刷新页面
          </button>
        </div>
      </div>
    </div>
  );

  if (isAuthRoute) {
    if (forceLogin) {
      return <main className="mx-auto w-full max-w-xl py-10">{children}</main>;
    }
    if (!isLoaded) {
      if (clerkLoadTimedOut) return renderClerkRecovery("还没有拿到 Clerk 会话状态。");
      return (
        <div className="flex min-h-screen items-center justify-center text-sm text-slate-300">
          正在校验登录状态...
        </div>
      );
    }
    if (isSignedIn) {
      return (
        <div className="flex min-h-screen items-center justify-center text-sm text-slate-300">
          <div className="grid gap-4 text-center">
            <div>正在进入本地 Agent 配置向导...</div>
            <a className="text-cyan-200 underline underline-offset-4" href={onboardingHref}>
              手动打开配置向导
            </a>
          </div>
        </div>
      );
    }
    return <main className="mx-auto w-full max-w-xl py-10">{children}</main>;
  }

  if (!isLoaded) {
    if (clerkLoadTimedOut) return renderClerkRecovery("当前页面一直在等待 Clerk 会话校验返回。");
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-300">
        正在校验登录状态...
      </div>
    );
  }

  if (!isSignedIn) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-300">
        <div className="grid gap-4 text-center">
          <div>正在返回登录页...</div>
          <a className="text-cyan-200 underline underline-offset-4" href="/auth?forceLogin=1">
            手动打开登录页
          </a>
        </div>
      </div>
    );
  }

  if (shouldWaitForLocalIdentity || shouldWaitForCloud || localOnboardingStatus === "loading" || shouldRedirectToOnboarding) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-300">
        <div className="grid gap-4 text-center">
          <div>
            {shouldWaitForLocalIdentity
              ? "正在准备本地 Agent 身份..."
              : shouldWaitForCloud
                ? "正在同步云端权限..."
                : localOnboardingStatus === "loading"
                  ? "正在检查本地配置完整度..."
                  : "正在进入本地 Agent 配置向导..."}
          </div>
          {shouldRedirectToOnboarding ? (
            <a className="text-cyan-200 underline underline-offset-4" href={onboardingHref}>
              手动打开配置向导
            </a>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className="app-frame">
      {CONVEX_ENABLED ? <ConvexUserSync /> : null}
      <Nav />
      <main className="min-w-0 flex-1">
        <ClerkTopBar />
        <BackendConnectionBanner />
        {children}
      </main>
    </div>
  );
}
