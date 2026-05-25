import {
  localAgentDelete,
  localAgentGet,
  localAgentPatch,
  localAgentPost,
  localAgentPut,
} from "@/lib/local-agent-api";
import type { RequestOptions } from "@/lib/http-client";

export type { RequestOptions } from "@/lib/http-client";

/**
 * Legacy compatibility layer.
 *
 * New code should import from "@/lib/cloud-api" for identity/subscription calls
 * or "@/lib/local-agent-api" for setup/research/trade/broker calls.
 */
export async function apiGet<T>(path: string, options?: RequestOptions): Promise<T> {
  return localAgentGet<T>(path, options);
}

export async function apiPost<T>(path: string, body: unknown, options?: RequestOptions): Promise<T> {
  return localAgentPost<T>(path, body, options);
}

export async function apiPut<T>(path: string, body: unknown, options?: RequestOptions): Promise<T> {
  return localAgentPut<T>(path, body, options);
}

export async function apiPatch<T>(path: string, body: unknown, options?: RequestOptions): Promise<T> {
  return localAgentPatch<T>(path, body, options);
}

export async function apiDelete<T>(path: string, options?: RequestOptions): Promise<T> {
  return localAgentDelete<T>(path, options);
}
