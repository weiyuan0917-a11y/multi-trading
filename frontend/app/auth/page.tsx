"use client";

import { SignIn, SignUp } from "@clerk/nextjs";
import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { cloudPost } from "@/lib/cloud-api";
import { setAuthToken } from "@/lib/auth";
import { CLERK_ENABLED } from "@/lib/clerk-mode";
import { PageShell } from "@/components/ui/page-shell";

type Mode = "login" | "register";

function friendlyAuthError(raw: unknown, mode: Mode): string {
  const text = String(raw || "").trim();
  if (mode === "login" && (text.includes("invalid_username_or_password") || text.includes("用户名或密码不正确"))) {
    return "用户名或密码不正确。若这是升级、重装或迁移后的本地客户端，请切换到注册，用同一个用户名重新创建本地账号。";
  }
  if (text.includes("username_already_exists")) {
    return "该用户名已存在，请切换到登录。";
  }
  if (text.includes("password_too_short")) {
    return "密码至少需要 6 位。";
  }
  if (text.includes("username_required")) {
    return "请输入用户名。";
  }
  return text || "请求失败，请稍后重试。";
}

function AuthBrandHero() {
  return (
    <section
      className="mx-auto flex max-w-3xl flex-col items-center px-4 pb-8 pt-8 text-center sm:pt-10"
      style={{
        fontFamily:
          'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      }}
    >
      <div className="flex h-32 w-32 items-center justify-center rounded-[34px] border border-cyan-300/20 bg-slate-950/40 shadow-[0_24px_68px_rgba(34,211,238,0.22)] sm:h-36 sm:w-36">
        <img src="/brand/multitrading-mark.svg" alt="MultiTrading" className="h-28 w-28 sm:h-32 sm:w-32" />
      </div>
      <h1 className="mt-6 text-5xl font-medium tracking-normal text-slate-50 sm:text-6xl">MultiTrading</h1>
      <p className="mt-3 max-w-2xl text-base font-normal leading-7 text-slate-300 sm:text-lg">
        面向多账户、多策略的智能交易工作台，把行情、回测、风控和自动执行集中到一个清晰流程里。
      </p>
    </section>
  );
}

export default function AuthPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const forceLogin = String(searchParams?.get("forceLogin") || "").trim() === "1";

  useEffect(() => {
    if (String(searchParams?.get("mode") || "") === "register") setMode("register");
  }, [searchParams]);

  if (CLERK_ENABLED) {
    return (
      <PageShell>
        <AuthBrandHero />

        <div className="hidden">
          <div className="page-header">
            <div>
              <h1 className="page-title">MultiTrading 账号</h1>
              <div className="mt-1 text-sm text-slate-300">使用云端账号登录后，再连接本地 MultiTrading Agent。</div>
            </div>
          </div>
        </div>

        {forceLogin ? (
          <div className="panel border-amber-300/30 bg-amber-300/10 text-sm text-amber-100">
            已回到本地登录入口。如果刚才退出时跳到了 Clerk 外部页面，通常是 Clerk 开发域名响应超时；重新登录即可继续。
          </div>
        ) : null}

        <div className="flex justify-center">
          {mode === "login" ? (
            <SignIn
              routing="hash"
              signUpUrl="/auth?mode=register"
              fallbackRedirectUrl="/dashboard"
              signUpFallbackRedirectUrl="/dashboard"
            />
          ) : (
            <SignUp
              routing="hash"
              signInUrl="/auth"
              fallbackRedirectUrl="/dashboard"
              signInFallbackRedirectUrl="/dashboard"
            />
          )}
        </div>

        <div className="flex justify-center gap-2">
          <button
            className={mode === "login" ? "btn-primary" : "btn-secondary"}
            onClick={() => setMode("login")}
            type="button"
          >
            登录
          </button>
          <button
            className={mode === "register" ? "btn-primary" : "btn-secondary"}
            onClick={() => setMode("register")}
            type="button"
          >
            注册
          </button>
        </div>
      </PageShell>
    );
  }

  const submit = async () => {
    setSubmitting(true);
    try {
      const path = mode === "login" ? "/auth/login" : "/auth/register";
      const data = await cloudPost<any>(
        path,
        { username: username.trim(), password },
        { cacheTtlMs: 0, retries: 0, timeoutMs: 10000 }
      );
      const token = String(data?.token || "").trim();
      if (!token) throw new Error("登录成功但未返回 token");
      setAuthToken(token);
      setMessage(mode === "login" ? "登录成功，正在进入系统..." : "注册成功，正在进入系统...");
      setError("");
      router.replace("/setup");
    } catch (e: any) {
      setError(friendlyAuthError(e?.message || e, mode));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageShell>
      <AuthBrandHero />

      <div className="hidden">
        <div className="page-header">
          <div>
            <h1 className="page-title">账户登录 / 注册</h1>
            <div className="mt-1 text-sm text-slate-300">先登录系统账户，再配置券商 API。</div>
          </div>
        </div>
      </div>

      {message ? <div className="panel mx-auto w-full max-w-md border-emerald-200 bg-emerald-50 text-emerald-700">{message}</div> : null}
      {error ? (
        <div className="panel mx-auto w-full max-w-md border-rose-200 bg-rose-50 text-rose-700">
          <div>{error}</div>
          {mode === "login" && error.includes("重新创建本地账号") ? (
            <button
              className="btn-secondary mt-3"
              type="button"
              onClick={() => {
                setMode("register");
                setError("");
              }}
            >
              切换到注册
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="panel mx-auto w-full max-w-md space-y-3">
        <div className="flex gap-2">
          <button
            className={mode === "login" ? "btn-primary" : "btn-secondary"}
            onClick={() => setMode("login")}
            type="button"
          >
            登录
          </button>
          <button
            className={mode === "register" ? "btn-primary" : "btn-secondary"}
            onClick={() => setMode("register")}
            type="button"
          >
            注册
          </button>
        </div>

        <input
          className="input-base"
          placeholder="用户名（3-64位）"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <input
          className="input-base"
          type="password"
          placeholder="密码（至少6位）"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button className="btn-primary" onClick={submit} disabled={submitting}>
          {submitting ? "提交中..." : mode === "login" ? "登录并进入系统" : "注册并进入系统"}
        </button>
      </div>
    </PageShell>
  );
}

