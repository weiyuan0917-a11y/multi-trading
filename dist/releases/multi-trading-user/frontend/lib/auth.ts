"use client";

export const AUTH_TOKEN_KEY = "mt_auth_token";

export function getAuthToken(): string {
  if (typeof window === "undefined") return "";
  return String(window.localStorage.getItem(AUTH_TOKEN_KEY) || "").trim();
}

export function setAuthToken(token: string): void {
  if (typeof window === "undefined") return;
  const tk = String(token || "").trim();
  if (!tk) {
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
    return;
  }
  window.localStorage.setItem(AUTH_TOKEN_KEY, tk);
}

export function authHeaders(token?: string): HeadersInit {
  const tk = String(token || getAuthToken() || "").trim();
  return tk ? { Authorization: `Bearer ${tk}` } : {};
}

