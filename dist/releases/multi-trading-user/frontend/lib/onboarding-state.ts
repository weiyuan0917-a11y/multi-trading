import { localAgentGet } from "@/lib/local-agent-api";

export type OnboardingStepKey = "owner" | "broker" | "llm" | "feishu" | "market" | "notify" | "research" | "apiKey";

export type OnboardingStep = {
  key: OnboardingStepKey;
  title: string;
  skippable: boolean;
};

export type OnboardingSetupStatus = {
  configured?: {
    broker?: boolean;
    longport?: boolean;
    longbridge?: boolean;
    feishu?: boolean;
    market_apis?: boolean;
    openbb?: boolean;
    cn_market_data?: boolean;
    tradingagents?: boolean;
  };
  values?: Record<string, string>;
};

export type OnboardingAccountsResponse = {
  accounts?: { account_id?: string; broker_provider?: string; status?: string }[];
};

export type OnboardingApiKeysResponse = {
  items?: { id: string; revoked_at?: string | null }[];
};

export type OnboardingSnapshot = {
  setup?: OnboardingSetupStatus | null;
  accounts?: OnboardingAccountsResponse | null;
  apiKeys?: OnboardingApiKeysResponse | null;
};

export const ONBOARDING_STEPS: OnboardingStep[] = [
  { key: "owner", title: "用户名", skippable: false },
  { key: "broker", title: "券商 API", skippable: true },
  { key: "llm", title: "LLM", skippable: true },
  { key: "feishu", title: "飞书应用", skippable: true },
  { key: "market", title: "行情 API", skippable: true },
  { key: "notify", title: "安全权限", skippable: true },
  { key: "research", title: "Research 数据", skippable: true },
  { key: "apiKey", title: "个人 API Key", skippable: true },
];

export const ONBOARDING_STEP_DESCRIPTIONS: Record<OnboardingStepKey, string> = {
  owner: "绑定本机 local owner，用来隔离本地配置、券商 Key 和历史记录。",
  broker: "填写券商账户和 API 凭证；也可以先跳过，稍后在 Setup 页面补全。",
  llm: "设置 TradingAgents 使用的模型与 API Key。",
  feishu: "配置飞书应用和定时消息会话。",
  market: "填写外部行情与宏观数据 API Key。",
  notify: "设置 MCP 工具调用安全等级，控制 L3 高权限工具是否可用。",
  research: "配置 OpenBB 与 A 股数据源。",
  apiKey: "创建并确认本机个人 API Key，供自动交易 Worker 或脚本调用。",
};

function hasValue(value: unknown): boolean {
  const raw = String(value || "").trim();
  if (!raw) return false;
  return !["-", "未配置", "none", "null", "undefined"].includes(raw.toLowerCase());
}

function anyValue(values: Record<string, string>, keys: string[]): boolean {
  return keys.some((key) => hasValue(values[key]));
}

export function getMissingOnboardingStepKeys(params: {
  ownerBound: boolean;
  snapshot?: OnboardingSnapshot | null;
  apiKeyRequired: boolean;
  done?: Partial<Record<OnboardingStepKey, boolean>>;
}): OnboardingStepKey[] {
  const done = params.done || {};
  if (!params.ownerBound) return done.owner ? [] : ["owner"];

  const setup = params.snapshot?.setup || null;
  const values = setup?.values || {};
  const configured = setup?.configured || {};
  const accounts = params.snapshot?.accounts?.accounts || [];
  const apiKeys = params.snapshot?.apiKeys?.items || [];
  const activeApiKeyCount = apiKeys.filter((item) => !item.revoked_at).length;

  const brokerReady =
    Boolean(configured.broker || configured.longport || configured.longbridge) ||
    accounts.some((row) => hasValue(row.account_id));
  const llmReady =
    String(values.tradingagents_llm_provider || "").trim().toLowerCase() === "ollama" ||
    anyValue(values, [
      "openai_api_key",
      "anthropic_api_key",
      "google_api_key",
      "xai_api_key",
      "deepseek_api_key",
      "openrouter_api_key",
      "dashscope_api_key",
      "zhipuai_api_key",
      "azure_openai_api_key",
    ]);
  const securityReady = hasValue(values.openclaw_mcp_max_level);
  const researchReady = Boolean(configured.openbb || configured.cn_market_data);

  const missing: OnboardingStepKey[] = [];
  if (!brokerReady) missing.push("broker");
  if (!llmReady) missing.push("llm");
  if (!configured.feishu) missing.push("feishu");
  if (!configured.market_apis) missing.push("market");
  if (!securityReady) missing.push("notify");
  if (!researchReady) missing.push("research");
  if (params.apiKeyRequired && activeApiKeyCount <= 0) missing.push("apiKey");
  return missing.filter((key) => !done[key]);
}

export function isLocalOnboardingComplete(params: {
  ownerBound: boolean;
  snapshot?: OnboardingSnapshot | null;
  apiKeyRequired: boolean;
}): boolean {
  return getMissingOnboardingStepKeys(params).length === 0;
}

export async function loadLocalOnboardingSnapshot(apiKeyRequired: boolean): Promise<OnboardingSnapshot> {
  const [setupRes, accountsRes, apiKeysRes] = await Promise.allSettled([
    localAgentGet<OnboardingSetupStatus>("/setup/config", { cacheTtlMs: 0, retries: 0 }),
    localAgentGet<OnboardingAccountsResponse>("/setup/accounts", { cacheTtlMs: 0, retries: 0 }),
    apiKeyRequired
      ? localAgentGet<OnboardingApiKeysResponse>("/auth/api-keys", { cacheTtlMs: 0, retries: 0 })
      : Promise.resolve({ items: [] }),
  ]);
  return {
    setup: setupRes.status === "fulfilled" ? setupRes.value : null,
    accounts: accountsRes.status === "fulfilled" ? accountsRes.value : null,
    apiKeys: apiKeysRes.status === "fulfilled" ? apiKeysRes.value : null,
  };
}
