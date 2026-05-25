export type AppEdition = "user" | "admin";

export function normalizeAppEdition(value: unknown): AppEdition {
  return String(value || "").trim().toLowerCase() === "admin" ? "admin" : "user";
}

export const APP_EDITION: AppEdition = normalizeAppEdition(
  process.env.NEXT_PUBLIC_MT_EDITION || process.env.MT_APP_EDITION || "user"
);

export const ADMIN_EDITION_ENABLED = APP_EDITION === "admin";

