"use client";

import { ClerkAuthShell } from "@/components/clerk-auth-shell";
import { LocalAuthShell } from "@/components/local-auth-shell";
import { CLERK_ENABLED, LOCAL_FIRST_AUTH } from "@/lib/clerk-mode";

export function AppAuthShell({ children }: { children: React.ReactNode }) {
  if (LOCAL_FIRST_AUTH) return <LocalAuthShell>{children}</LocalAuthShell>;
  if (CLERK_ENABLED) return <ClerkAuthShell>{children}</ClerkAuthShell>;
  return <LocalAuthShell>{children}</LocalAuthShell>;
}
