import { createJsonApiClient } from "@/lib/http-client";
import { getCurrentLocalOwnerBinding, LOCAL_OWNER_ID, setLocalOwnerCloudIdentity } from "@/lib/local-owner-binding";

export type LocalAgentStatus = {
  ok?: boolean;
  agent?: string;
  version?: string;
  apiVersion?: string;
};

export const LOCAL_AGENT_API_VERSION = "v1";
export const LOCAL_AGENT_API_BASE =
  process.env.NEXT_PUBLIC_LOCAL_AGENT_API_BASE ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://127.0.0.1:8010";
const LOCAL_AGENT_API_FALLBACK_BASES = [
  "http://127.0.0.1:8010",
  "http://localhost:8010",
].filter((base) => base !== LOCAL_AGENT_API_BASE);
export const LOCAL_AGENT_OWNER_ID = LOCAL_OWNER_ID;

export function setLocalAgentCloudIdentity(identity: {
  email?: string | null;
  ownerId?: string | null;
  plan?: string | null;
  role?: string | null;
  isAdmin?: boolean | null;
}) {
  setLocalOwnerCloudIdentity(identity);
}

function localOwnerHeaders(): Record<string, string> {
  const binding = getCurrentLocalOwnerBinding();
  return binding.matched
    ? {
        "X-MT-Local-Owner": binding.ownerId,
      }
    : {};
}

export const localAgentApi = createJsonApiClient({
  baseUrl: LOCAL_AGENT_API_BASE,
  fallbackBaseUrls: LOCAL_AGENT_API_FALLBACK_BASES,
  includeAuthToken: true,
  getRuntimeHeaders: localOwnerHeaders,
});

export const localAgentGet = localAgentApi.get;
export const localAgentPost = localAgentApi.post;
export const localAgentPut = localAgentApi.put;
export const localAgentPatch = localAgentApi.patch;
export const localAgentDelete = localAgentApi.delete;

export async function localAgentHealth(): Promise<LocalAgentStatus> {
  return localAgentGet<LocalAgentStatus>("/health", { cacheTtlMs: 0, retries: 0, timeoutMs: 5000 });
}
