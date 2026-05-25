import { createJsonApiClient } from "@/lib/http-client";

const LOCAL_AUTH_FALLBACK =
  process.env.NEXT_PUBLIC_LOCAL_AGENT_API_BASE ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://127.0.0.1:8010";

export const CLOUD_API_BASE =
  process.env.NEXT_PUBLIC_CLOUD_API_BASE ||
  process.env.NEXT_PUBLIC_CONSOLE_API_BASE ||
  LOCAL_AUTH_FALLBACK;
export const CLOUD_API_LOCAL_FALLBACK_ENABLED = process.env.NEXT_PUBLIC_CLOUD_API_ALLOW_LOCAL_FALLBACK === "true";

export const cloudApi = createJsonApiClient({
  baseUrl: CLOUD_API_BASE,
  fallbackBaseUrls: CLOUD_API_LOCAL_FALLBACK_ENABLED && CLOUD_API_BASE !== LOCAL_AUTH_FALLBACK ? [LOCAL_AUTH_FALLBACK] : [],
  includeAuthToken: true,
});

export const cloudGet = cloudApi.get;
export const cloudPost = cloudApi.post;
export const cloudPut = cloudApi.put;
export const cloudPatch = cloudApi.patch;
export const cloudDelete = cloudApi.delete;
