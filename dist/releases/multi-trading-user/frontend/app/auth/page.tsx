"use client";

import { SignIn, SignUp } from "@clerk/nextjs";
import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { cloudPost } from "@/lib/cloud-api";
import { setAuthToken } from "@/lib/auth";
import { CLERK_ENABLED } from "@/lib/clerk-mode";
import { PageShell } from "@/components/ui/page-shell";

type Mode = "login" | "register";

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
        <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
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
      setError(String(e?.message || e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">账户登录 / 注册</h1>
            <div className="mt-1 text-sm text-slate-300">先登录系统账户，再配置券商 API。</div>
          </div>
        </div>
      </div>

      {message ? <div className="panel border-emerald-200 bg-emerald-50 text-emerald-700">{message}</div> : null}
      {error ? <div className="panel border-rose-200 bg-rose-50 text-rose-700">{error}</div> : null}

      <div className="panel space-y-3">
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

