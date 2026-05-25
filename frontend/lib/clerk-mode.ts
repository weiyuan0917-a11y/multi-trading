export type AuthMode = "hybrid" | "local" | "clerk";

export const CLERK_CONFIGURED = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?.trim());

function normalizeAuthMode(value: unknown): AuthMode {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "clerk" || raw === "cloud") return "clerk";
  if (raw === "local" || raw === "offline") return "local";
  return "hybrid";
}

// hybrid/local are local-first and do not let Clerk block startup.
export const AUTH_MODE = normalizeAuthMode(process.env.NEXT_PUBLIC_AUTH_MODE || process.env.NEXT_PUBLIC_MT_AUTH_MODE);
export const LOCAL_FIRST_AUTH = AUTH_MODE === "hybrid" || AUTH_MODE === "local";
export const CLERK_OPTIONAL = CLERK_CONFIGURED && AUTH_MODE === "hybrid";
export const CLERK_ENABLED = CLERK_CONFIGURED && AUTH_MODE === "clerk";
