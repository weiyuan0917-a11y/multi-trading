import "./globals.css";
import type { Metadata } from "next";
import { Suspense } from "react";
import { ClerkProvider } from "@clerk/nextjs";
import { AppAuthShell } from "@/components/app-auth-shell";
import { ConvexClientProvider } from "@/components/convex-client-provider";
import { CLERK_ENABLED } from "@/lib/clerk-mode";
import { CONVEX_ENABLED } from "@/lib/convex-mode";

export const metadata: Metadata = {
  title: "MultiTrading",
  description: "Trading dashboard and controls",
  applicationName: "MultiTrading",
  icons: {
    icon: [{ url: "/brand/multitrading-mark.svg", type: "image/svg+xml" }],
    shortcut: "/brand/multitrading-mark.svg",
    apple: "/brand/multitrading-mark.svg",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const content = (
    <html lang="zh-CN">
      <body className="min-h-screen">
        <div className="relative min-h-screen bg-[#08101d]">
          <Suspense
            fallback={
              <div className="flex min-h-screen items-center justify-center text-sm text-slate-300">
                正在加载…
              </div>
            }
          >
            <AppAuthShell>{children}</AppAuthShell>
          </Suspense>
        </div>
      </body>
    </html>
  );
  if (!CLERK_ENABLED) return content;
  if (CONVEX_ENABLED && process.env.NEXT_PUBLIC_CONVEX_URL) {
    return (
      <ClerkProvider>
        <ConvexClientProvider url={process.env.NEXT_PUBLIC_CONVEX_URL}>{content}</ConvexClientProvider>
      </ClerkProvider>
    );
  }
  return <ClerkProvider>{content}</ClerkProvider>;
}
