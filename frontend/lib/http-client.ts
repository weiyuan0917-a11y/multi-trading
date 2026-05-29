export type RequestOptions = {
  timeoutMs?: number;
  retries?: number;
  cacheTtlMs?: number;
  headers?: HeadersInit;
};

type JsonApiClientConfig = {
  baseUrl: string;
  fallbackBaseUrls?: string[];
  includeAuthToken?: boolean;
  getRuntimeHeaders?: () => Record<string, string>;
};

const DEFAULT_TIMEOUT_MS = 30000;
const DEFAULT_RETRIES = 3;
const DEFAULT_GET_CACHE_TTL_MS = 15000;
const memoryGetCache = new Map<string, { expiresAt: number; data: unknown }>();

export class JsonApiError extends Error {
  status: number;
  url: string;

  constructor(message: string, status: number, url: string) {
    super(message);
    this.name = "JsonApiError";
    this.status = status;
    this.url = url;
  }
}

export function getJsonApiErrorStatus(error: unknown): number {
  return error instanceof JsonApiError ? error.status : 0;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function normalizeBaseUrl(value: string): string {
  return String(value || "").replace(/\/+$/, "");
}

function unique(values: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const value of values) {
    const normalized = normalizeBaseUrl(value);
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
}

function normalizeErrorMessage(rawText: string): string {
  const text = String(rawText || "").trim();
  if (!text) return "\u8bf7\u6c42\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5";
  const friendlyReason = (value: string) => {
    const reason = String(value || "").trim();
    const messages: Record<string, string> = {
      older_license_rejected: "当前本地已有更新的 License，已拒绝导入旧密钥。",
      lower_plan_rejected: "当前本地套餐更高且仍有效，已拒绝导入低套餐 License。",
      shorter_subscription_rejected: "当前本地订阅到期时间更晚，已拒绝导入较短周期 License。",
      missing_issued_at_rejected: "新 License 缺少签发时间，无法覆盖当前 License。",
      invalid_signature: "License 签名无效，请确认使用的是最新邮件里的完整 License JSON。",
      missing_signature: "License 缺少签名，无法导入。",
      expired: "License 密钥已过期，请重新签发或导入新的 License。",
    };
    return messages[reason] || reason;
  };
  try {
    const parsed = JSON.parse(text) as {
      error?: unknown;
      message?: unknown;
      detail?: unknown;
    };
    if (typeof parsed.message === "string" && parsed.message.trim()) return parsed.message.trim();
    if (typeof parsed.detail === "string" && parsed.detail.trim()) return parsed.detail.trim();
    if (parsed.detail && typeof parsed.detail === "object") {
      const detail = parsed.detail as {
        error?: unknown;
        message?: unknown;
        detail?: unknown;
        reason?: unknown;
        signature_status?: unknown;
        feature?: unknown;
        required_plan?: unknown;
        current_plan?: unknown;
        session_owner?: unknown;
        requested_owner?: unknown;
      };
      const message = typeof detail.message === "string" ? detail.message.trim() : "";
      const code = typeof detail.error === "string" ? detail.error.trim() : "";
      const nested = typeof detail.detail === "string" ? detail.detail.trim() : "";
      const reason = typeof detail.reason === "string" ? detail.reason.trim() : "";
      const signatureStatus = typeof detail.signature_status === "string" ? detail.signature_status.trim() : "";
      if (code === "license_import_rejected" && reason) return friendlyReason(reason);
      if (code === "plan_required") {
        const feature = typeof detail.feature === "string" ? detail.feature.trim() : "";
        const requiredPlan = typeof detail.required_plan === "string" ? detail.required_plan.trim() : "";
        const currentPlan = typeof detail.current_plan === "string" ? detail.current_plan.trim() : "";
        const suffix = [
          feature ? `\u529f\u80fd ${feature}` : "",
          requiredPlan ? `\u9700\u8981 ${requiredPlan}` : "",
          currentPlan ? `\u5f53\u524d ${currentPlan}` : "",
        ]
          .filter(Boolean)
          .join("\uff0c");
        return suffix ? `\u5957\u9910\u6743\u9650\u4e0d\u8db3\uff1a${suffix}` : "\u5957\u9910\u6743\u9650\u4e0d\u8db3";
      }
      if (code === "local_owner_session_mismatch") {
        const sessionOwner = typeof detail.session_owner === "string" ? detail.session_owner.trim() : "";
        const requestedOwner = typeof detail.requested_owner === "string" ? detail.requested_owner.trim() : "";
        return sessionOwner && requestedOwner
          ? `\u5f53\u524d\u767b\u5f55\u7528\u6237 ${sessionOwner} \u4e0e\u672c\u5730 owner ${requestedOwner} \u4e0d\u4e00\u81f4\uff0c\u8bf7\u5237\u65b0\u6216\u91cd\u65b0\u767b\u5f55`
          : "\u767b\u5f55\u7528\u6237\u4e0e\u672c\u5730 owner \u4e0d\u4e00\u81f4\uff0c\u8bf7\u5237\u65b0\u6216\u91cd\u65b0\u767b\u5f55";
      }
      if (message) return code ? `${message} [${code}]` : message;
      if (code && nested) return `${code}: ${nested}`;
      if (code && reason) {
        const friendly = friendlyReason(reason);
        return signatureStatus && signatureStatus !== reason ? `${code}: ${friendly} (${friendlyReason(signatureStatus)})` : `${code}: ${friendly}`;
      }
      if (code) return code;
    }
    if (typeof parsed.error === "string" && parsed.error.trim()) return parsed.error.trim();
  } catch {}
  return text;
}

function buildUrl(base: string, path: string): string {
  const rawPath = String(path || "");
  if (/^https?:\/\//i.test(rawPath)) return rawPath;
  return `${normalizeBaseUrl(base)}${rawPath.startsWith("/") ? rawPath : `/${rawPath}`}`;
}

function authHeaders(includeAuthToken: boolean): Record<string, string> {
  if (!includeAuthToken || typeof window === "undefined") return {};
  const token = String(window.localStorage.getItem("mt_auth_token") || "").trim();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function cacheKeyFor(url: string, method: string, headers: Record<string, string>): string {
  const owner = String(headers["X-MT-Local-Owner"] || headers["x-mt-local-owner"] || "");
  const auth = String(headers.Authorization || headers.authorization || "");
  return `${method}:${url}:owner=${owner}:auth=${auth.slice(0, 32)}`;
}

export function createJsonApiClient(config: JsonApiClientConfig) {
  const bases = unique([config.baseUrl, ...(config.fallbackBaseUrls || [])]);
  const includeAuthToken = config.includeAuthToken ?? true;
  const getRuntimeHeaders = config.getRuntimeHeaders;

  async function requestJson<T>(path: string, init?: RequestInit, options?: RequestOptions): Promise<T> {
    const timeoutMs = options?.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const retries = options?.retries ?? DEFAULT_RETRIES;
    const cacheTtlMs = Math.max(0, options?.cacheTtlMs ?? DEFAULT_GET_CACHE_TTL_MS);
    const method = String(init?.method || "GET").toUpperCase();
    const candidateBases = /^https?:\/\//i.test(String(path || "")) ? [""] : bases;
    let lastErr: unknown;

    for (const base of candidateBases) {
      const url = buildUrl(base, path);
      const baseHeaders = {
        ...authHeaders(includeAuthToken),
        ...(getRuntimeHeaders?.() || {}),
        ...((init?.headers || {}) as Record<string, string>),
        ...((options?.headers || {}) as Record<string, string>),
      };
      const cacheKey = cacheKeyFor(url, method, baseHeaders);
      if (method === "GET" && cacheTtlMs > 0) {
        const cached = memoryGetCache.get(cacheKey);
        if (cached && cached.expiresAt > Date.now()) {
          return cached.data as T;
        }
      }

      for (let attempt = 0; attempt <= retries; attempt += 1) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        try {
          const res = await fetch(url, {
            ...init,
            headers: baseHeaders,
            cache: "no-store",
            signal: controller.signal,
          });
          clearTimeout(timer);
          if (!res.ok) {
            throw new JsonApiError(normalizeErrorMessage(await res.text()), res.status, url);
          }
          const raw = await res.text();
          const data = (raw ? JSON.parse(raw) : {}) as T;
          if (method === "GET" && cacheTtlMs > 0) {
            memoryGetCache.set(cacheKey, {
              expiresAt: Date.now() + cacheTtlMs,
              data,
            });
          }
          return data;
        } catch (err) {
          clearTimeout(timer);
          lastErr = err;
          if (attempt < retries) {
            const backoffMs = 500 * 2 ** attempt;
            const jitterMs = Math.floor(Math.random() * 300);
            await sleep(backoffMs + jitterMs);
          }
        }
      }
    }

    if (lastErr instanceof Error && lastErr.name === "AbortError") {
      throw new Error("\u8bf7\u6c42\u8d85\u65f6\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5");
    }
    throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
  }

  return {
    get<T>(path: string, options?: RequestOptions): Promise<T> {
      return requestJson<T>(path, undefined, options);
    },
    post<T>(path: string, body: unknown, options?: RequestOptions): Promise<T> {
      return requestJson<T>(
        path,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
        options
      );
    },
    put<T>(path: string, body: unknown, options?: RequestOptions): Promise<T> {
      return requestJson<T>(
        path,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
        options
      );
    },
    patch<T>(path: string, body: unknown, options?: RequestOptions): Promise<T> {
      return requestJson<T>(
        path,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
        options
      );
    },
    delete<T>(path: string, options?: RequestOptions): Promise<T> {
      return requestJson<T>(path, { method: "DELETE" }, options);
    },
  };
}
