"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";
import { PageShell } from "@/components/ui/page-shell";
import { SetupApiKeysPanel } from "@/components/setup-api-keys-panel";
import { EntitlementNotice } from "@/components/entitlement-guard";
import { useEntitlements } from "@/lib/use-entitlements";

const IS_CUSTOMER_BUILD = process.env.NEXT_PUBLIC_MT_BUILD_TARGET === "customer";

type SetupStatus = {
  configured: {
    longport: boolean;
    feishu: boolean;
    market_apis: boolean;
    openbb?: boolean;
    tradingagents?: boolean;
  };
  values: Record<string, string>;
};

type CnProviderStatus = {
  providers?: {
    id: string;
    name: string;
    configured?: boolean;
    installed?: boolean;
    enabled?: boolean;
    status_text?: string;
    setup_hint?: string;
  }[];
};

type SetupAccountItem = {
  account_id: string;
  broker_provider: string;
  is_default: boolean;
  status: string;
  last_error?: string | null;
  last_init_at?: string | null;
  quote_ready?: boolean;
  trade_ready?: boolean;
  manual_disconnected?: boolean;
};

type SetupAccountsResponse = {
  ok: boolean;
  default_account_id?: string | null;
  accounts: SetupAccountItem[];
};

type RiskCfg = {
  max_order_amount: number;
  max_daily_loss_pct: number;
  stop_loss_pct: number;
  max_position_pct: number;
  min_cash_ratio?: number;
  max_total_risk_pct?: number;
  max_stock_order_notional_pct?: number;
  max_stock_position_pct?: number;
  max_option_order_loss_pct?: number;
  max_0dte_order_loss_pct?: number;
  max_option_daily_new_risk_pct?: number;
  max_total_option_risk_pct?: number;
  block_naked_short_options?: boolean;
  fail_closed_for_live?: boolean;
  enabled: boolean;
};

type LongPortDiag = {
  connection_limit: number;
  active_connections_api_process: number;
  usage_pct_api_process: number;
  estimated_connections_total?: number;
  estimated_usage_pct_total?: number;
  estimated_breakdown?: {
    api_active: number;
    mcp_estimated: number;
    feishu_estimated: number;
  };
  processes?: {
    api?: { pid?: number; running?: boolean };
    mcp?: { pid?: number | null; running?: boolean };
    feishu_bot?: { pid?: number | null; running?: boolean };
  };
  quote_ctx_ready: boolean;
  trade_ctx_ready: boolean;
  last_init_at?: string | null;
  last_error?: string | null;
  probe?: { requested?: boolean; ok?: boolean | null; error?: string | null };
  alert_level?: "ok" | "notice" | "warning" | "critical";
  recommendations?: string[];
  note?: string;
};

type FeeScheduleResponse = {
  version: string;
  schedule: Record<string, any>;
  broker_id?: string;
  active_broker_id?: string;
  effective_broker_id?: string;
  manual_fee_broker_id?: string;
  fee_source?: string;
};

type FeeBrokersResponse = {
  active_broker_id: string;
  brokers: { broker_id: string; display_name: string }[];
  manual_fee_broker_id?: string;
  effective_broker_id?: string;
  fee_source?: string;
  fee_resolution?: Record<string, any>;
};

type FeeFormState = {
  hk_commission_enabled: boolean;
  hk_commission_rate_pct: number;
  hk_commission_min: number;
  hk_platform_fee: number;
  hk_stamp_duty_pct: number;
  hk_trading_fee_pct: number;
  hk_sfc_levy_pct: number;
  hk_afrc_levy_pct: number;
  hk_ccass_fee_pct: number;
  us_platform_per_share: number;
  us_platform_min: number;
  us_platform_max_pct_notional: number;
  us_settlement_per_share: number;
  us_settlement_max_pct_notional: number;
  us_taf_per_share: number;
  us_taf_min: number;
  us_taf_max: number;
  us_option_commission_per_contract: number;
  us_option_commission_min: number;
  us_option_platform_per_contract: number;
  us_option_platform_min: number;
  us_option_settlement_per_contract: number;
  us_option_regulatory_per_contract: number;
  us_option_clearing_per_contract: number;
  us_option_taf_per_contract: number;
  us_option_taf_min: number;
};

type SetupSectionKey = "accounts" | "secrets" | "research" | "agents" | "fees" | "risk" | "advanced";
type SetupSectionTone = "ready" | "attention" | "neutral";

type SetupSectionState = {
  tone: SetupSectionTone;
  label: string;
  detail: string;
  nextStep: string;
  metrics: { label: string; value: string; tone?: SetupSectionTone }[];
};

type SetupSectionAction = {
  label: string;
  variant?: "primary" | "secondary";
  disabled?: boolean;
  onClick: () => void | Promise<void>;
};

const SETUP_SECTIONS: Array<{
  key: SetupSectionKey;
  title: string;
  description: string;
}> = [
  { key: "accounts", title: "账户与券商", description: "账户注册、连接诊断与券商凭证" },
  { key: "secrets", title: "密钥配置", description: "Broker、Feishu、LLM 与行情 Key" },
  { key: "research", title: "Research 数据源", description: "OpenBB 与 A 股数据源" },
  { key: "agents", title: "TradingAgents", description: "研究增强模型与数据源" },
  { key: "fees", title: "费用模型", description: "券商费率、模板与试算" },
  { key: "risk", title: "风控与服务", description: "风控参数、服务启停" },
  { key: "advanced", title: "高级设置", description: "MCP 授权与个人 API Key" },
];

const externalCredentialLinks = {
  longbridgeOpenApi: "https://open.longportapp.com/",
  tigerOpenApi: "https://quant.itigerup.com/openapi/",
  fosunOpenApi: "https://openapi-docs.fosunxcz.com/?spec=guidelines",
  usmartOpenApi: "https://www.usmartsecurities.com/",
  feishuOpenPlatform: "https://open.feishu.cn/app",
  finnhub: "https://finnhub.io/register",
  tiingo: "https://www.tiingo.com/account/api/token",
  polygon: "https://polygon.io/dashboard/signup",
  twelveData: "https://twelvedata.com/register",
  fred: "https://fred.stlouisfed.org/docs/api/api_key.html",
  fmp: "https://site.financialmodelingprep.com/developer/docs",
  coingecko: "https://www.coingecko.com/en/developers/dashboard",
} as const;

const SUPPORTED_ACCOUNT_BROKERS = [
  { broker_id: "longbridge", display_name: "长桥（默认）" },
  { broker_id: "tiger", display_name: "老虎" },
  { broker_id: "fosun", display_name: "复兴证券" },
];

const mergeBrokerOptions = (brokers: { broker_id: string; display_name: string }[]) => {
  const merged = new Map<string, { broker_id: string; display_name: string }>();
  for (const b of SUPPORTED_ACCOUNT_BROKERS) {
    merged.set(b.broker_id, b);
  }
  for (const b of brokers) {
    const id = String(b.broker_id || "").trim();
    if (!id) continue;
    merged.set(id, { broker_id: id, display_name: String(b.display_name || id) });
  }
  return Array.from(merged.values()).sort((a, b) => a.broker_id.localeCompare(b.broker_id));
};

const pemFromArrayBuffer = (label: string, buffer: ArrayBuffer) => {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  const body = btoa(binary).match(/.{1,64}/g)?.join("\n") || "";
  return `-----BEGIN ${label}-----\n${body}\n-----END ${label}-----`;
};

function CredentialLink({ href, children }: { href: string; children: string }) {
  return (
    <a className="text-xs text-cyan-300 hover:text-cyan-200" href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

function SecretInputWithLink({
  label,
  href,
  input,
}: {
  label: string;
  href: string;
  input: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <CredentialLink href={href}>{label}</CredentialLink>
      {input}
    </div>
  );
}

function openbbReasonText(reason: unknown): string {
  const value = String(reason || "").trim();
  const messages: Record<string, string> = {
    empty_or_fmp_key_missing: "未配置 FMP Key",
    fmp_payment_required: "FMP 套餐不支持该端点",
    fmp_endpoint_not_found: "FMP/OpenBB 端点不可用",
    fmp_key_unauthorized: "FMP Key 无效",
    fmp_forbidden: "FMP Key 无权访问",
    empty_fmp_response: "FMP 返回空数据",
    empty_openbb_fmp_response: "OpenBB FMP 返回空数据",
    openbb_disabled_or_unconfigured: "OpenBB 未启用或未配置",
    openbb_auto_start_disabled: "OpenBB 自动启动关闭",
    openbb_port_occupied: "OpenBB 端口已被占用",
    openbb_command_not_found: "找不到 OpenBB API 命令",
    openbb_unreachable: "OpenBB 无法连接",
  };
  return messages[value] || value || "-";
}

function openbbOkText(ok: unknown): string {
  return ok ? "OK" : "不可用";
}

const setupToneDotClass = (tone: SetupSectionTone) =>
  tone === "ready"
    ? "bg-emerald-400 shadow-[0_0_0_3px_rgba(52,211,153,0.12)]"
    : tone === "attention"
      ? "bg-amber-300 shadow-[0_0_0_3px_rgba(252,211,77,0.12)]"
      : "bg-slate-500";

const setupTonePillClass = (tone: SetupSectionTone) =>
  tone === "ready"
    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
    : tone === "attention"
      ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
      : "border-slate-600/70 bg-slate-800/70 text-slate-300";

const setupMetricValueClass = (tone: SetupSectionTone = "neutral") =>
  tone === "ready" ? "text-emerald-200" : tone === "attention" ? "text-amber-200" : "text-slate-100";

/** 与后端 `fee_broker_profiles._BROKER_ID_RE` 一致 */
const FEE_BROKER_ID_PATTERN = /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/;

const feeNum = (v: any, fallback = 0): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
};

const fmtNum = (v: any, digits = 4): string => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits) : "-";
};

const emptyFeeForm: FeeFormState = {
  hk_commission_enabled: false,
  hk_commission_rate_pct: 0,
  hk_commission_min: 0,
  hk_platform_fee: 0,
  hk_stamp_duty_pct: 0,
  hk_trading_fee_pct: 0,
  hk_sfc_levy_pct: 0,
  hk_afrc_levy_pct: 0,
  hk_ccass_fee_pct: 0,
  us_platform_per_share: 0,
  us_platform_min: 0,
  us_platform_max_pct_notional: 0,
  us_settlement_per_share: 0,
  us_settlement_max_pct_notional: 0,
  us_taf_per_share: 0,
  us_taf_min: 0,
  us_taf_max: 0,
  us_option_commission_per_contract: 0,
  us_option_commission_min: 0,
  us_option_platform_per_contract: 0,
  us_option_platform_min: 0,
  us_option_settlement_per_contract: 0,
  us_option_regulatory_per_contract: 0,
  us_option_clearing_per_contract: 0,
  us_option_taf_per_contract: 0,
  us_option_taf_min: 0,
};

const scheduleToFeeForm = (s: Record<string, any>): FeeFormState => {
  const hk = s?.hk_stock || {};
  const us = s?.us_stock || {};
  const opt = s?.us_option_regular || {};
  return {
    hk_commission_enabled: Boolean(hk?.commission?.enabled),
    hk_commission_rate_pct: feeNum(hk?.commission?.rate_pct),
    hk_commission_min: feeNum(hk?.commission?.min_per_order),
    hk_platform_fee: feeNum(hk?.platform_fee?.amount),
    hk_stamp_duty_pct: feeNum(hk?.stamp_duty?.rate_pct),
    hk_trading_fee_pct: feeNum(hk?.trading_fee?.rate_pct),
    hk_sfc_levy_pct: feeNum(hk?.sfc_levy?.rate_pct),
    hk_afrc_levy_pct: feeNum(hk?.afrc_levy?.rate_pct),
    hk_ccass_fee_pct: feeNum(hk?.ccass_fee?.rate_pct),
    us_platform_per_share: feeNum(us?.platform_fee?.amount_per_share),
    us_platform_min: feeNum(us?.platform_fee?.min_per_order),
    us_platform_max_pct_notional: feeNum(us?.platform_fee?.max_pct_of_notional),
    us_settlement_per_share: feeNum(us?.settlement_fee?.amount_per_share),
    us_settlement_max_pct_notional: feeNum(us?.settlement_fee?.max_pct_of_notional),
    us_taf_per_share: feeNum(us?.taf?.amount_per_share),
    us_taf_min: feeNum(us?.taf?.min_per_order),
    us_taf_max: feeNum(us?.taf?.max_per_order),
    us_option_commission_per_contract: feeNum(opt?.commission?.amount_per_contract),
    us_option_commission_min: feeNum(opt?.commission?.min_per_order),
    us_option_platform_per_contract: feeNum(opt?.platform_fee?.amount_per_contract),
    us_option_platform_min: feeNum(opt?.platform_fee?.min_per_order),
    us_option_settlement_per_contract: feeNum(opt?.option_settlement_fee?.amount_per_contract),
    us_option_regulatory_per_contract: feeNum(opt?.option_regulatory_fee?.amount_per_contract),
    us_option_clearing_per_contract: feeNum(opt?.option_clearing_fee?.amount_per_contract),
    us_option_taf_per_contract: feeNum(opt?.option_taf?.amount_per_contract),
    us_option_taf_min: feeNum(opt?.option_taf?.min_per_order),
  };
};

const feeFormToSchedulePatch = (f: FeeFormState): Record<string, any> => ({
  hk_stock: {
    commission: { enabled: Boolean(f.hk_commission_enabled), rate_pct: feeNum(f.hk_commission_rate_pct), min_per_order: feeNum(f.hk_commission_min) },
    platform_fee: { amount: feeNum(f.hk_platform_fee) },
    stamp_duty: { rate_pct: feeNum(f.hk_stamp_duty_pct) },
    trading_fee: { rate_pct: feeNum(f.hk_trading_fee_pct) },
    sfc_levy: { rate_pct: feeNum(f.hk_sfc_levy_pct) },
    afrc_levy: { rate_pct: feeNum(f.hk_afrc_levy_pct) },
    ccass_fee: { rate_pct: feeNum(f.hk_ccass_fee_pct) },
  },
  us_stock: {
    platform_fee: {
      amount_per_share: feeNum(f.us_platform_per_share),
      min_per_order: feeNum(f.us_platform_min),
      max_pct_of_notional: feeNum(f.us_platform_max_pct_notional),
    },
    settlement_fee: {
      amount_per_share: feeNum(f.us_settlement_per_share),
      max_pct_of_notional: feeNum(f.us_settlement_max_pct_notional),
    },
    taf: {
      amount_per_share: feeNum(f.us_taf_per_share),
      min_per_order: feeNum(f.us_taf_min),
      max_per_order: feeNum(f.us_taf_max),
    },
  },
  us_option_regular: {
    commission: {
      amount_per_contract: feeNum(f.us_option_commission_per_contract),
      min_per_order: feeNum(f.us_option_commission_min),
    },
    platform_fee: {
      amount_per_contract: feeNum(f.us_option_platform_per_contract),
      min_per_order: feeNum(f.us_option_platform_min),
    },
    option_settlement_fee: { amount_per_contract: feeNum(f.us_option_settlement_per_contract) },
    option_regulatory_fee: { amount_per_contract: feeNum(f.us_option_regulatory_per_contract) },
    option_clearing_fee: { amount_per_contract: feeNum(f.us_option_clearing_per_contract) },
    option_taf: { amount_per_contract: feeNum(f.us_option_taf_per_contract), min_per_order: feeNum(f.us_option_taf_min) },
  },
});

export default function SetupPage() {
  const entitlements = useEntitlements();
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [cnProviderStatus, setCnProviderStatus] = useState<CnProviderStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [risk, setRisk] = useState<RiskCfg | null>(null);
  const [services, setServices] = useState<any>(null);
  const [diag, setDiag] = useState<LongPortDiag | null>(null);
  const [showBrokerDiagnostics, setShowBrokerDiagnostics] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testingOpenbb, setTestingOpenbb] = useState(false);
  const [restartingOpenbb, setRestartingOpenbb] = useState(false);
  const [openbbDiagnostics, setOpenbbDiagnostics] = useState<any>(null);
  const [installingCnProvider, setInstallingCnProvider] = useState("");
  const [cnInstallRestartHint, setCnInstallRestartHint] = useState<Record<string, boolean>>({});
  const [taAdvancedMode, setTaAdvancedMode] = useState(false);
  const [activeSection, setActiveSection] = useState<SetupSectionKey>("accounts");
  const [stoppingAll, setStoppingAll] = useState(false);
  const [savingFees, setSavingFees] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [feeScheduleText, setFeeScheduleText] = useState("");
  const [feeAdvancedMode, setFeeAdvancedMode] = useState(false);
  const [feeForm, setFeeForm] = useState<FeeFormState>(emptyFeeForm);
  const [feeBrokers, setFeeBrokers] = useState<{ broker_id: string; display_name: string }[]>([]);
  /** 当前 /fees/estimate 与回测实际采用的模板 */
  const [feeEffectiveBrokerId, setFeeEffectiveBrokerId] = useState("");
  /** 未连接默认账户时使用的模板（持久化 manual_fee_broker_id） */
  const [feeManualTemplateId, setFeeManualTemplateId] = useState("");
  const [feeSource, setFeeSource] = useState("");
  const [feeEditingBrokerId, setFeeEditingBrokerId] = useState("");
  const [newFeeBrokerId, setNewFeeBrokerId] = useState("");
  const [newFeeBrokerName, setNewFeeBrokerName] = useState("");
  const [newFeeBrokerCopyFrom, setNewFeeBrokerCopyFrom] = useState("");
  const [feeDisplayNameDraft, setFeeDisplayNameDraft] = useState("");
  const prevNewFeeBrokerIdRef = useRef<string>("");
  const [feeEstimate, setFeeEstimate] = useState<any>(null);
  const [accountsResp, setAccountsResp] = useState<SetupAccountsResponse | null>(null);
  const [accountsLoading, setAccountsLoading] = useState(false);
  const [showAccountRegistrationForm, setShowAccountRegistrationForm] = useState(false);
  const [registeringAccount, setRegisteringAccount] = useState(false);
  const [accountActionLoading, setAccountActionLoading] = useState<Record<string, "connect" | "disconnect" | "delete" | undefined>>({});
  const [generatingFosunKeyPair, setGeneratingFosunKeyPair] = useState(false);
  const [fosunClientPublicKey, setFosunClientPublicKey] = useState("");
  const [publicIpLoading, setPublicIpLoading] = useState(false);
  const [publicIpInfo, setPublicIpInfo] = useState<{ ip?: string; source?: string; error?: string } | null>(null);
  const [accountForm, setAccountForm] = useState({
    account_id: "default",
    broker_provider: "longbridge",
    longport_app_key: "",
    longport_app_secret: "",
    longport_access_token: "",
    tiger_id: "",
    tiger_account: "",
    tiger_license: "",
    tiger_env: "PAPER",
    tiger_private_key_path: "",
    tiger_props_path: "",
    tiger_secret_key: "",
    tiger_token_path: "",
    fosun_api_key: "",
    fosun_base_url: "",
    fosun_sub_account_id: "",
    fosun_client_id: "",
    fosun_server_public_key: "",
    fosun_client_private_key: "",
    fosun_sdk_type: "",
    fosun_apply_account_id: "",
    fosun_option_apply_account_id: "",
    usmart_trade_host: "https://open-jy.yxzq.com",
    usmart_quote_host: "https://open-hz.yxzq.com:8443",
    usmart_x_lang: "1",
    usmart_x_channel: "",
    usmart_area_code: "86",
    usmart_phone_number: "",
    usmart_login_password: "",
    usmart_trade_password: "",
    usmart_server_public_key: "",
    usmart_client_private_key: "",
    usmart_timeout_seconds: "8",
    is_default: true,
    overwrite: true,
  });
  const [feeEstimateForm, setFeeEstimateForm] = useState({
    asset_class: "stock" as "stock" | "us_option",
    market: "US" as "HK" | "US" | "CN" | "OTHER",
    side: "buy" as "buy" | "sell",
    quantity: 100,
    price: 10,
  });

  const [form, setForm] = useState({
    longport_app_key: "",
    longport_app_secret: "",
    longport_access_token: "",
    feishu_app_id: "",
    feishu_app_secret: "",
    feishu_scheduled_chat_id: "",
    finnhub_api_key: "",
    tiingo_api_key: "",
    polygon_api_key: "",
    twelve_data_api_key: "",
    fred_api_key: "",
    fmp_api_key: "",
    coingecko_api_key: "",
    openclaw_mcp_max_level: "",
    openclaw_mcp_allow_l3: "",
    openclaw_mcp_l3_confirmation_token: "",
    openbb_enabled: "",
    openbb_base_url: "",
    openbb_timeout_seconds: "",
    openbb_auto_start: "",
    cn_market_data_provider_order: "",
    cn_market_mootdx_enabled: "",
    cn_market_tencent_enabled: "",
    cn_market_akshare_enabled: "",
    cn_market_tushare_enabled: "",
    cn_market_baostock_enabled: "",
    tushare_token: "",
    tradingagents_enabled: "",
    tradingagents_timeout_seconds: "",
    tradingagents_max_symbols: "",
    tradingagents_llm_provider: "",
    tradingagents_deep_model: "",
    tradingagents_quick_model: "",
    tradingagents_output_language: "",
    tradingagents_max_debate_rounds: "",
    tradingagents_max_risk_discuss_rounds: "",
    tradingagents_checkpoint_enabled: "",
    tradingagents_data_source: "",
    tradingagents_public_market_source: "",
    tradingagents_score_weight: "",
    llm_api_key: "",
    azure_openai_endpoint: "",
  });
  const taDraft = {
    enabled: form.tradingagents_enabled || status?.values?.tradingagents_enabled || "false",
    timeoutSeconds: form.tradingagents_timeout_seconds || status?.values?.tradingagents_timeout_seconds || "180",
    maxSymbols: form.tradingagents_max_symbols || status?.values?.tradingagents_max_symbols || "3",
    provider: form.tradingagents_llm_provider || status?.values?.tradingagents_llm_provider || "openai",
    deepModel: form.tradingagents_deep_model || status?.values?.tradingagents_deep_model || "gpt-5.4",
    quickModel: form.tradingagents_quick_model || status?.values?.tradingagents_quick_model || "gpt-5.4-mini",
    outputLanguage: form.tradingagents_output_language || status?.values?.tradingagents_output_language || "Chinese",
    maxDebateRounds: form.tradingagents_max_debate_rounds || status?.values?.tradingagents_max_debate_rounds || "1",
    maxRiskDiscussRounds:
      form.tradingagents_max_risk_discuss_rounds || status?.values?.tradingagents_max_risk_discuss_rounds || "1",
    checkpointEnabled:
      form.tradingagents_checkpoint_enabled || status?.values?.tradingagents_checkpoint_enabled || "false",
    dataSource: form.tradingagents_data_source || status?.values?.tradingagents_data_source || "auto",
    publicMarketSource:
      form.tradingagents_public_market_source || status?.values?.tradingagents_public_market_source || "auto",
    scoreWeight: form.tradingagents_score_weight || status?.values?.tradingagents_score_weight || "0.25",
  };

  useEffect(() => {
    if (taAdvancedMode) return;
    const p = String(form.tradingagents_llm_provider || status?.values?.tradingagents_llm_provider || "").toLowerCase();
    if (p !== "deepseek") return;
    const allow = new Set(["deepseek-v4-flash", "deepseek-v4-pro"]);
    const d = String(form.tradingagents_deep_model || status?.values?.tradingagents_deep_model || "").trim();
    const q = String(form.tradingagents_quick_model || status?.values?.tradingagents_quick_model || "").trim();
    if (allow.has(d) && allow.has(q)) return;
    setForm((s) => ({
      ...s,
      tradingagents_deep_model: allow.has(d) ? d : "deepseek-v4-pro",
      tradingagents_quick_model: allow.has(q) ? q : "deepseek-v4-flash",
    }));
  }, [
    taAdvancedMode,
    form.tradingagents_llm_provider,
    form.tradingagents_deep_model,
    form.tradingagents_quick_model,
    status?.values?.tradingagents_llm_provider,
    status?.values?.tradingagents_deep_model,
    status?.values?.tradingagents_quick_model,
  ]);

  const llmProviderEnvKey = (
    {
      openai: "OPENAI_API_KEY",
      anthropic: "ANTHROPIC_API_KEY",
      google: "GOOGLE_API_KEY",
      xai: "XAI_API_KEY",
      deepseek: "DEEPSEEK_API_KEY",
      openrouter: "OPENROUTER_API_KEY",
      qwen: "DASHSCOPE_API_KEY",
      glm: "ZHIPUAI_API_KEY",
      azure: "AZURE_OPENAI_API_KEY",
      ollama: "无需 API Key（本地模型）",
    } as Record<string, string>
  )[String(taDraft.provider || "").toLowerCase()] || "OPENAI_API_KEY";
  const llmProviderMaskedCurrent = (
    {
      openai: status?.values?.openai_api_key || "",
      anthropic: status?.values?.anthropic_api_key || "",
      google: status?.values?.google_api_key || "",
      xai: status?.values?.xai_api_key || "",
      deepseek: status?.values?.deepseek_api_key || "",
      openrouter: status?.values?.openrouter_api_key || "",
      qwen: status?.values?.dashscope_api_key || "",
      glm: status?.values?.zhipuai_api_key || "",
      azure: status?.values?.azure_openai_api_key || "",
      ollama: "不需要",
    } as Record<string, string>
  )[String(taDraft.provider || "").toLowerCase()] || "";

  const applyFeeBrokersMeta = (br: FeeBrokersResponse) => {
    const brokers = mergeBrokerOptions(br.brokers || []);
    setFeeBrokers(brokers);
    const ids = brokers.map((b) => b.broker_id);
    const rawEff = String(br.effective_broker_id || br.active_broker_id || "");
    const rawMan = String(br.manual_fee_broker_id || rawEff || "");
    const safeEff = rawEff && ids.includes(rawEff) ? rawEff : ids[0] || "";
    const safeMan = rawMan && ids.includes(rawMan) ? rawMan : safeEff;
    setFeeEffectiveBrokerId(safeEff);
    setFeeManualTemplateId(safeMan);
    setFeeSource(String(br.fee_source || ""));
    /** 避免 value 与 option 不一致时浏览器显示空白下拉框 */
    setFeeEditingBrokerId((cur) => (cur && ids.includes(cur) ? cur : safeEff));
  };

  const refreshFeeBrokersMeta = async () => {
    const r = await apiGet<FeeBrokersResponse>("/fees/brokers", { cacheTtlMs: 0 });
    applyFeeBrokersMeta(r);
  };

  const applyFeeScheduleResponse = (fs: FeeScheduleResponse, opts?: { setEditor?: boolean }) => {
    setFeeScheduleText(JSON.stringify(fs?.schedule || {}, null, 2));
    setFeeForm(scheduleToFeeForm(fs?.schedule || {}));
    if (opts?.setEditor !== false && fs.broker_id) {
      setFeeEditingBrokerId(String(fs.broker_id));
    }
    const eff = String(fs.effective_broker_id || fs.active_broker_id || "");
    if (eff) setFeeEffectiveBrokerId(eff);
    if (fs.manual_fee_broker_id) setFeeManualTemplateId(String(fs.manual_fee_broker_id));
  };

  const loadFeeScheduleForEditor = async (bid: string) => {
    const fs = await apiGet<FeeScheduleResponse>(`/fees/schedule?broker_id=${encodeURIComponent(bid)}`, {
      cacheTtlMs: 0,
    });
    applyFeeScheduleResponse(fs);
  };

  const load = async () => {
    let feeBrokersLoadError: string | null = null;
    let feeBrokersRecoveredFromSchedule = false;
    try {
      setStatusLoading(true);
      setAccountsLoading(true);
      // 配置状态先返回，避免首屏长时间显示“未配置”误导用户。
      const s = await apiGet<SetupStatus>("/setup/config");
      setStatus(s);
      setStatusLoading(false);

      const br = await apiGet<FeeBrokersResponse>("/fees/brokers", { cacheTtlMs: 0 }).catch((e: any) => {
        feeBrokersLoadError = String(e?.message || e);
        return null;
      });
      if (br) {
        applyFeeBrokersMeta(br);
      }

      const effId = br?.effective_broker_id || br?.active_broker_id;
      const feeQ = effId ? `?broker_id=${encodeURIComponent(String(effId))}` : "";

      const [rRes, svcRes, dgRes, feesRes, acRes, cnProviderRes] = await Promise.allSettled([
        apiGet<RiskCfg>("/risk/config"),
        apiGet<any>("/setup/services/status"),
        apiGet<LongPortDiag>("/setup/longport/diagnostics"),
        apiGet<FeeScheduleResponse>(`/fees/schedule${feeQ}`, { cacheTtlMs: 0 }),
        apiGet<SetupAccountsResponse>("/setup/accounts"),
        apiGet<CnProviderStatus>("/market-data/providers/status", { cacheTtlMs: 0 }),
      ]);

      if (rRes.status === "fulfilled") setRisk(rRes.value);
      if (svcRes.status === "fulfilled") setServices(svcRes.value);
      if (dgRes.status === "fulfilled") setDiag(dgRes.value);
      if (feesRes.status === "fulfilled") {
        const fs = feesRes.value;
        applyFeeScheduleResponse(fs);
        if (!br && fs.broker_id) {
          const syn: FeeBrokersResponse = {
            active_broker_id: fs.active_broker_id || fs.broker_id!,
            effective_broker_id: fs.effective_broker_id || fs.active_broker_id || fs.broker_id!,
            manual_fee_broker_id: fs.manual_fee_broker_id || fs.active_broker_id || fs.broker_id!,
            brokers: [{ broker_id: fs.broker_id!, display_name: fs.broker_id! }],
          };
          applyFeeBrokersMeta(syn);
          feeBrokersRecoveredFromSchedule = true;
        }
      }
      if (acRes.status === "fulfilled") setAccountsResp(acRes.value);
      if (cnProviderRes.status === "fulfilled") setCnProviderStatus(cnProviderRes.value);
      if (feeBrokersLoadError && !feeBrokersRecoveredFromSchedule) {
        setErr(
          `无法加载 /fees/brokers（${feeBrokersLoadError}）。请确认后端已重启到最新版本；若接口 404 说明 API 过旧。`
        );
      } else {
        setErr("");
      }
    } catch (e: any) {
      setErr(String(e.message || e));
      setStatusLoading(false);
    } finally {
      setAccountsLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    const b = feeBrokers.find((x) => x.broker_id === feeEditingBrokerId);
    setFeeDisplayNameDraft(b?.display_name ?? "");
  }, [feeEditingBrokerId, feeBrokers]);

  /** 新券商 ID 与「显示名称」联动：未单独改过显示名时随 ID 变化；清空 ID 时清空显示名 */
  useEffect(() => {
    const id = newFeeBrokerId;
    const idTrim = id.trim();
    const prev = prevNewFeeBrokerIdRef.current;
    prevNewFeeBrokerIdRef.current = id;
    setNewFeeBrokerName((nm) => {
      if (idTrim === "") return "";
      const prevTrim = (prev ?? "").trim();
      const nmTrim = nm.trim();
      if (nmTrim === "" || nmTrim === prevTrim) return idTrim;
      return nm;
    });
  }, [newFeeBrokerId]);

  const saveSecrets = async () => {
    setSaving(true);
    try {
      const payload: Record<string, string> = {};
      Object.entries(form).forEach(([k, v]) => {
        if (v.trim()) payload[k] = v.trim();
      });
      if (!Object.keys(payload).length) {
        setMsg("未填写新值，未执行保存。");
        setSaving(false);
        return;
      }
      const resp = await apiPost<{ restart_recommended?: boolean }>("/setup/config", payload);
      setMsg(resp?.restart_recommended ? "配置已保存到 .env，建议重启后端使所有进程一致生效。" : "配置已保存到 .env。");
      setForm({
        longport_app_key: "",
        longport_app_secret: "",
        longport_access_token: "",
        feishu_app_id: "",
        feishu_app_secret: "",
        feishu_scheduled_chat_id: "",
        finnhub_api_key: "",
        tiingo_api_key: "",
        polygon_api_key: "",
        twelve_data_api_key: "",
        fred_api_key: "",
        fmp_api_key: "",
        coingecko_api_key: "",
        openclaw_mcp_max_level: "",
        openclaw_mcp_allow_l3: "",
        openclaw_mcp_l3_confirmation_token: "",
        openbb_enabled: "",
        openbb_base_url: "",
        openbb_timeout_seconds: "",
        openbb_auto_start: "",
        cn_market_data_provider_order: "",
        cn_market_mootdx_enabled: "",
        cn_market_tencent_enabled: "",
        cn_market_akshare_enabled: "",
        cn_market_tushare_enabled: "",
        cn_market_baostock_enabled: "",
        tushare_token: "",
        tradingagents_enabled: "",
        tradingagents_timeout_seconds: "",
        tradingagents_max_symbols: "",
        tradingagents_llm_provider: "",
        tradingagents_deep_model: "",
        tradingagents_quick_model: "",
        tradingagents_output_language: "",
        tradingagents_max_debate_rounds: "",
        tradingagents_max_risk_discuss_rounds: "",
        tradingagents_checkpoint_enabled: "",
        tradingagents_data_source: "",
        tradingagents_public_market_source: "",
        tradingagents_score_weight: "",
        llm_api_key: "",
        azure_openai_endpoint: "",
      });
      await load();
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  const testOpenbb = async () => {
    setTestingOpenbb(true);
    try {
      const r = await apiGet<any>("/research/external/openbb/health", { cacheTtlMs: 0, retries: 0 });
      const enabled = Boolean(r?.health?.enabled);
      const ok = Boolean(r?.health?.ok);
      const base = r?.health?.base_url || status?.values?.openbb_base_url || "未设置";
      if (enabled && ok) {
        setMsg(`OpenBB 连接正常（${base}）`);
      } else if (!enabled) {
        setMsg("OpenBB 当前未启用，请先保存 OPENBB_ENABLED=true。");
      } else {
        const reason = String(r?.health?.autostart?.reason || r?.health?.reason || "service_unreachable");
        const hint =
          reason === "openbb_command_not_found"
            ? "客户版未预置 OpenBB API 服务；请单独启动 OpenBB API，或由管理员重新打包含 OpenBB 运行环境。"
            : "请确认 OpenBB API 服务是否启动，并检查 OPENBB_BASE_URL。";
        setMsg(`OpenBB 已启用但连接失败（${base}，原因：${reason}）。${hint}`);
      }
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setTestingOpenbb(false);
    }
  };

  const refreshOpenbbDiagnostics = async () => {
    const r = await apiGet<any>("/research/external/openbb/diagnostics", { cacheTtlMs: 0, retries: 0, timeoutMs: 90000 });
    const diag = r?.diagnostics || null;
    setOpenbbDiagnostics(diag);
    return diag;
  };

  const testOpenbbDiagnostics = async () => {
    setTestingOpenbb(true);
    try {
      const diag = await refreshOpenbbDiagnostics();
      const health = diag?.health || {};
      const enabled = Boolean(diag?.enabled ?? health?.enabled);
      const ok = Boolean(health?.ok);
      const base = diag?.base_url || health?.base_url || status?.values?.openbb_base_url || "未配置";
      if (enabled && ok) {
        setMsg(`OpenBB 连接正常：${base}`);
      } else if (!enabled) {
        setMsg("OpenBB 当前未启用，请先保存 OPENBB_ENABLED=true。");
      } else {
        const reason = String(health?.autostart?.reason || health?.reason || "service_unreachable");
        setMsg(`OpenBB 已启用但连接失败：${base}，原因：${openbbReasonText(reason)}。`);
      }
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setTestingOpenbb(false);
    }
  };

  const restartOpenbb = async () => {
    setRestartingOpenbb(true);
    try {
      const r = await apiPost<any>("/research/external/openbb/restart", { clear_cache: true }, { cacheTtlMs: 0, retries: 0, timeoutMs: 120000 });
      await refreshOpenbbDiagnostics();
      if (r?.restart?.ok) {
        setMsg("OpenBB 已重启，并已清理 OpenBB research cache。");
      } else {
        setMsg(`OpenBB 重启未完成：${openbbReasonText(r?.restart?.reason || "openbb_restart_failed")}`);
      }
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setRestartingOpenbb(false);
    }
  };

  const installCnProvider = async (provider: "mootdx" | "akshare" | "tushare" | "baostock" | "all") => {
    if (IS_CUSTOMER_BUILD) {
      setErr("客户版不支持运行时安装 Python 数据源包；请使用安装包内置数据源，或由管理员重新打包含所需数据源。");
      return;
    }
    setInstallingCnProvider(provider);
    setErr("");
    try {
      const resp = await apiPost<any>("/setup/cn-market-data/install", { provider }, { timeoutMs: 600000, retries: 0 });
      if (resp?.ok) {
        setMsg(`A 股数据源安装完成：${(resp.packages || []).join(", ")}。建议重启后端后再刷新状态。`);
        const nextHint: Record<string, boolean> = { ...cnInstallRestartHint };
        (resp.packages || []).forEach((pkg: string) => {
          nextHint[String(pkg)] = true;
        });
        setCnInstallRestartHint(nextHint);
        if (resp?.provider_status) setCnProviderStatus(resp.provider_status);
        await load();
      } else {
        const tail = String(resp?.stderr_tail || resp?.stdout_tail || resp?.error || "安装失败");
        setErr(`安装失败：${tail.slice(-800)}`);
      }
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setInstallingCnProvider("");
    }
  };

  const cnProviderById = (id: string) => cnProviderStatus?.providers?.find((p) => p.id === id);
  const cnProviderStatusLabel = (p?: { installed?: boolean; configured?: boolean; enabled?: boolean; status_text?: string }) => {
    if (!p) return "检测中";
    if (!p.installed && !p.configured) return "未预置";
    if (!p.enabled) return "已停用";
    return p.status_text || "可用";
  };
  const cnProviderStatusHelp = (id: string, p?: { installed?: boolean; configured?: boolean; enabled?: boolean; setup_hint?: string }) => {
    if (IS_CUSTOMER_BUILD && !p?.installed && !p?.configured) {
      return `${id} 未随客户安装包预置，客户版不能在线安装 Python 包；请使用 local_cache/Tencent/EastMoney，或让管理员重新打包。`;
    }
    const tokenReady = Boolean(status?.values?.tushare_token);
    if (id === "tushare" && p?.installed && !tokenReady) {
      return "Tushare Pro 已预置，但需要填写 TUSHARE_TOKEN 后保存。";
    }
    if (p?.installed && p?.enabled === false) {
      return "包已预置，但当前 owner 的启用开关为 false；改为 true 并保存后刷新状态。";
    }
    return p?.setup_hint || "读取后端检测结果。";
  };
  const cnInstallButtonLabel = (id: "mootdx" | "akshare" | "tushare" | "baostock", title: string) => {
    if (IS_CUSTOMER_BUILD) return "客户版不支持在线安装";
    if (installingCnProvider === id) return "安装中...";
    if (cnInstallRestartHint[id]) return "已安装，建议重启";
    const provider = cnProviderById(id);
    if (provider?.installed) return "已安装";
    return `安装 ${title}`;
  };

  const saveRisk = async () => {
    if (!risk) return;
    try {
      await apiPost("/setup/risk-config", risk);
      setMsg("风控参数已保存。");
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  };

  const stopAllServices = async () => {
    const ok = confirm("确认关闭 MultiTrading 系统吗？这会停止前端和后端服务，当前页面连接会断开。");
    if (!ok) return;
    setStoppingAll(true);
    try {
      await apiPost("/setup/services/stop-all", {
        stop_backend: true,
        stop_frontend: true,
        stop_feishu_bot: true,
        stop_auto_trader: true,
      });
      setMsg("关闭系统命令已发送，页面可能即将断开。");
      setErr("");
      // 不调用 load()，因为后端会自停，避免无意义报错闪烁。
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setStoppingAll(false);
    }
  };

  const saveFeeSchedule = async () => {
    setSavingFees(true);
    try {
      const parsed = feeAdvancedMode
        ? JSON.parse(feeScheduleText || "{}")
        : feeFormToSchedulePatch(feeForm);
      const resp = await apiPost<FeeScheduleResponse>("/fees/schedule", {
        schedule: parsed,
        broker_id: feeEditingBrokerId || undefined,
      });
      setMsg("费用模型已保存（当前所选券商）。");
      setErr("");
      setFeeScheduleText(JSON.stringify(resp?.schedule || {}, null, 2));
      setFeeForm(scheduleToFeeForm(resp?.schedule || {}));
      await refreshFeeBrokersMeta().catch(() => {});
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setSavingFees(false);
    }
  };

  const resetFeeScheduleToDefault = async () => {
    setSavingFees(true);
    try {
      const def = await apiGet<FeeScheduleResponse>("/fees/schedule/default");
      const resp = await apiPost<FeeScheduleResponse>("/fees/schedule", {
        schedule: def?.schedule || {},
        broker_id: feeEditingBrokerId || undefined,
      });
      setFeeScheduleText(JSON.stringify(resp?.schedule || {}, null, 2));
      setFeeForm(scheduleToFeeForm(resp?.schedule || {}));
      await refreshFeeBrokersMeta().catch(() => {});
      setMsg("已恢复当前券商的默认费用模板并保存。");
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setSavingFees(false);
    }
  };

  const saveManualFeeTemplate = async () => {
    if (!feeManualTemplateId) return;
    setSavingFees(true);
    try {
      const r = await apiPost<FeeBrokersResponse>("/fees/brokers/active", { broker_id: feeManualTemplateId });
      applyFeeBrokersMeta(r);
      setMsg("已保存「未连接默认账户」时使用的费用模板（连接账户后仍会自动跟随账户券商）。");
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setSavingFees(false);
    }
  };

  const addFeeBrokerProfile = async () => {
    const bid = newFeeBrokerId.trim();
    if (!bid) {
      setErr("请填写券商 ID（英文标识，如 tiger）。");
      return;
    }
    if (!FEE_BROKER_ID_PATTERN.test(bid)) {
      setErr(
        "券商 ID 格式不正确：须以英文字母开头，只能包含字母、数字、下划线与连字符（与费用模块存储键一致）。请勿使用中文或空格。"
      );
      return;
    }
    setSavingFees(true);
    try {
      await apiPost<FeeBrokersResponse>("/fees/brokers", {
        broker_id: bid,
        display_name: newFeeBrokerName.trim() || bid,
        copy_from: newFeeBrokerCopyFrom.trim() || null,
      });
      const r = await apiGet<FeeBrokersResponse>("/fees/brokers", { cacheTtlMs: 0 });
      applyFeeBrokersMeta(r);
      setFeeEditingBrokerId(bid);
      await loadFeeScheduleForEditor(bid);
      setNewFeeBrokerId("");
      setNewFeeBrokerName("");
      setNewFeeBrokerCopyFrom("");
      setMsg("已新增券商费用配置，可继续编辑后保存。");
      setErr("");
    } catch (e: any) {
      const t = String(e.message || e);
      if (/已存在|already exists|duplicate/i.test(t)) {
        setErr(`券商「${bid}」的费用模板已存在（系统默认即为 longbridge，无需重复添加）。已刷新列表。`);
        try {
          await refreshFeeBrokersMeta();
        } catch {
          /* ignore */
        }
      } else {
        setErr(t);
      }
    } finally {
      setSavingFees(false);
    }
  };

  const updateFeeBrokerDisplayName = async () => {
    if (!feeEditingBrokerId) return;
    setSavingFees(true);
    try {
      const r = await apiPatch<FeeBrokersResponse>(`/fees/brokers/${encodeURIComponent(feeEditingBrokerId)}`, {
        display_name: feeDisplayNameDraft.trim() || feeEditingBrokerId,
      });
      applyFeeBrokersMeta(r);
      setMsg("当前券商显示名称已更新。");
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setSavingFees(false);
    }
  };

  const generateFosunClientKeyPair = async () => {
    if (!window.crypto?.subtle) {
      setErr("当前浏览器不支持 WebCrypto，无法在页面内生成 RSA 密钥。请用 openssl 生成后再粘贴。");
      return;
    }
    if (
      accountForm.fosun_client_private_key.trim() &&
      !window.confirm("这会覆盖当前填写的 FSOPENAPI_CLIENT_PRIVATE_KEY_PEM，确认继续？")
    ) {
      return;
    }
    try {
      setGeneratingFosunKeyPair(true);
      const keyPair = await window.crypto.subtle.generateKey(
        {
          name: "RSASSA-PKCS1-v1_5",
          modulusLength: 2048,
          publicExponent: new Uint8Array([1, 0, 1]),
          hash: "SHA-256",
        },
        true,
        ["sign", "verify"],
      );
      const [privateKey, publicKey] = await Promise.all([
        window.crypto.subtle.exportKey("pkcs8", keyPair.privateKey),
        window.crypto.subtle.exportKey("spki", keyPair.publicKey),
      ]);
      const privatePem = pemFromArrayBuffer("PRIVATE KEY", privateKey);
      const publicPem = pemFromArrayBuffer("PUBLIC KEY", publicKey);
      setAccountForm((s) => ({ ...s, fosun_client_private_key: privatePem }));
      setFosunClientPublicKey(publicPem);
      setMsg("已在本地浏览器生成复兴客户端密钥对。私钥已填入表单，公钥请提交给复兴绑定。");
      setErr("");
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setGeneratingFosunKeyPair(false);
    }
  };

  const copyText = async (text: string, successMessage: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setMsg(successMessage);
      setErr("");
    } catch (e: any) {
      setErr(`复制失败：${String(e?.message || e)}`);
    }
  };

  const refreshPublicIp = async () => {
    try {
      setPublicIpLoading(true);
      const r = await apiGet<{ ok?: boolean; ip?: string; source?: string; error?: string }>("/setup/public-ip", {
        cacheTtlMs: 0,
      });
      setPublicIpInfo({ ip: r.ip || "", source: r.source || "", error: r.error || "" });
      if (!r.ip) {
        setErr(r.error || "公网 IP 查询失败，请稍后重试。");
      } else {
        setErr("");
      }
    } catch (e: any) {
      const error = String(e?.message || e);
      setPublicIpInfo({ ip: "", source: "", error });
      setErr(error);
    } finally {
      setPublicIpLoading(false);
    }
  };

  const deleteFeeBrokerProfile = async () => {
    if (!feeEditingBrokerId || feeBrokers.length <= 1) return;
    const label = feeBrokers.find((b) => b.broker_id === feeEditingBrokerId)?.display_name || feeEditingBrokerId;
    if (!confirm(`确定删除券商「${label}」（${feeEditingBrokerId}）及其费用配置吗？不可恢复。`)) return;
    setSavingFees(true);
    try {
      const r = await apiDelete<FeeBrokersResponse>(`/fees/brokers/${encodeURIComponent(feeEditingBrokerId)}`, {
        cacheTtlMs: 0,
      });
      applyFeeBrokersMeta(r);
      const nextActive = r.effective_broker_id || r.active_broker_id || r.brokers[0]?.broker_id || "";
      setFeeEditingBrokerId(nextActive);
      if (nextActive) await loadFeeScheduleForEditor(nextActive);
      setMsg("已删除该券商费用配置。");
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setSavingFees(false);
    }
  };

  const runFeeEstimate = async () => {
    try {
      const params = new URLSearchParams({
        asset_class: feeEstimateForm.asset_class,
        market: feeEstimateForm.market,
        side: feeEstimateForm.side,
        quantity: String(Math.max(1, Number(feeEstimateForm.quantity) || 1)),
        price: String(Math.max(0, Number(feeEstimateForm.price) || 0)),
      });
      const est = await apiGet<any>(`/fees/estimate?${params.toString()}`);
      setFeeEstimate(est);
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  };

  const probeLongPort = async () => {
    try {
      const dg = await apiGet<LongPortDiag>("/setup/longport/diagnostics?probe=true", {
        timeoutMs: 45000,
        retries: 0,
        cacheTtlMs: 0,
      });
      setDiag(dg);
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  };

  const registerAccount = async () => {
    setRegisteringAccount(true);
    try {
      const payload: Record<string, any> = {
        account_id: accountForm.account_id.trim(),
        broker_provider: accountForm.broker_provider.trim() || "longbridge",
        is_default: Boolean(accountForm.is_default),
        overwrite: Boolean(accountForm.overwrite),
      };
      if (!payload.account_id) {
        setErr("请输入 account_id。");
        return;
      }
      const brokerProvider = String(payload.broker_provider || "").trim().toLowerCase();
      const existingAccountIds = new Set((accountsResp?.accounts || []).map((x) => String(x.account_id || "").trim()));
      const createsAdditionalAccount = !existingAccountIds.has(String(payload.account_id || "").trim()) && existingAccountIds.size >= 1;
      const usesNonDefaultBroker = brokerProvider !== "longbridge";
      if ((createsAdditionalAccount || usesNonDefaultBroker) && !entitlements.canUse("multi_broker")) {
        setErr("多账户/多券商需要 Premium。Free/Pro 仅允许一个 Longbridge 默认账户。");
        return;
      }
      if (accountForm.longport_app_key.trim()) payload.longport_app_key = accountForm.longport_app_key.trim();
      if (accountForm.longport_app_secret.trim()) payload.longport_app_secret = accountForm.longport_app_secret.trim();
      if (accountForm.longport_access_token.trim()) payload.longport_access_token = accountForm.longport_access_token.trim();
      if (brokerProvider === "tiger" || brokerProvider === "itiger") {
        payload.credentials = {
          tiger_id: accountForm.tiger_id.trim(),
          account: accountForm.tiger_account.trim(),
          license: accountForm.tiger_license.trim(),
          env: accountForm.tiger_env.trim() || "PAPER",
          private_key_path: accountForm.tiger_private_key_path.trim(),
          props_path: accountForm.tiger_props_path.trim(),
          secret_key: accountForm.tiger_secret_key.trim(),
          token_path: accountForm.tiger_token_path.trim(),
        };
      }
      if (brokerProvider === "fosun" || brokerProvider === "fosunwealth") {
        payload.credentials = {
          api_key: accountForm.fosun_api_key.trim(),
          base_url: accountForm.fosun_base_url.trim(),
          sub_account_id: accountForm.fosun_sub_account_id.trim(),
          client_id: accountForm.fosun_client_id.trim(),
          server_public_key: accountForm.fosun_server_public_key.trim(),
          client_private_key: accountForm.fosun_client_private_key.trim(),
          sdk_type: accountForm.fosun_sdk_type.trim(),
          apply_account_id: accountForm.fosun_apply_account_id.trim(),
          option_apply_account_id: accountForm.fosun_option_apply_account_id.trim(),
        };
      }
      if (brokerProvider === "usmart") {
        payload.credentials = {
          trade_host: accountForm.usmart_trade_host.trim(),
          quote_host: accountForm.usmart_quote_host.trim(),
          x_lang: accountForm.usmart_x_lang.trim() || "1",
          x_channel: accountForm.usmart_x_channel.trim(),
          area_code: accountForm.usmart_area_code.trim() || "86",
          phone_number: accountForm.usmart_phone_number.trim(),
          login_password: accountForm.usmart_login_password,
          trade_password: accountForm.usmart_trade_password,
          server_public_key: accountForm.usmart_server_public_key.trim(),
          client_private_key: accountForm.usmart_client_private_key.trim(),
          timeout_seconds: accountForm.usmart_timeout_seconds.trim(),
        };
      }
      await apiPost("/setup/accounts/register", payload);
      setMsg(`账户 ${payload.account_id} 已注册。`);
      setErr("");
      setAccountForm((s) => ({
        ...s,
        longport_app_key: "",
        longport_app_secret: "",
        longport_access_token: "",
        tiger_id: "",
        tiger_account: "",
        tiger_license: "",
        tiger_private_key_path: "",
        tiger_props_path: "",
        tiger_secret_key: "",
        tiger_token_path: "",
        fosun_api_key: "",
        fosun_base_url: "",
        fosun_sub_account_id: "",
        fosun_client_id: "",
        fosun_server_public_key: "",
        fosun_client_private_key: "",
        fosun_sdk_type: "",
        fosun_apply_account_id: "",
        fosun_option_apply_account_id: "",
        usmart_x_channel: "",
        usmart_phone_number: "",
        usmart_login_password: "",
        usmart_trade_password: "",
        usmart_server_public_key: "",
        usmart_client_private_key: "",
      }));
      const latest = await apiGet<SetupAccountsResponse>("/setup/accounts");
      setAccountsResp(latest);
      setShowAccountRegistrationForm(false);
      await refreshFeeBrokersMeta().catch(() => {});
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setRegisteringAccount(false);
    }
  };

  const connectAccount = async (accountId: string) => {
    const aid = String(accountId || "").trim();
    if (!aid) return;
    setAccountActionLoading((s) => ({ ...s, [aid]: "connect" }));
    try {
      const resp = await apiPost<any>(
        `/setup/accounts/${encodeURIComponent(aid)}/connect`,
        {},
        { timeoutMs: 45000, retries: 0 }
      );
      const processes = Array.isArray(resp?.auto_stopped_processes)
        ? resp.auto_stopped_processes
        : Array.isArray(resp?.auto_stopped_workers)
          ? resp.auto_stopped_workers
          : [];
      const stoppedNames = processes
        .filter((x: any) => String(x?.stop_status || "").startsWith("stopped"))
        .map((x: any) => String(x?.process || x?.worker || ""))
        .filter(Boolean);
      if (resp?.account_switched && stoppedNames.length) {
        setMsg(`账户 ${aid} 已连接并切为默认账户，已停止旧账户 worker：${stoppedNames.join(", ")}`);
      } else if (resp?.account_switched) {
        setMsg(`账户 ${aid} 已连接并切为默认账户。`);
      } else {
        setMsg(`账户 ${aid} 已连接。`);
      }
      setErr("");
      const latest = await apiGet<SetupAccountsResponse>("/setup/accounts");
      setAccountsResp(latest);
      await refreshFeeBrokersMeta().catch(() => {});
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setAccountActionLoading((s) => ({ ...s, [aid]: undefined }));
    }
  };

  const disconnectAccount = async (accountId: string) => {
    const aid = String(accountId || "").trim();
    if (!aid) return;
    setAccountActionLoading((s) => ({ ...s, [aid]: "disconnect" }));
    try {
      const resp = await apiPost<any>(`/setup/accounts/${encodeURIComponent(aid)}/disconnect`, {});
      const processes = Array.isArray(resp?.auto_stopped_processes)
        ? resp.auto_stopped_processes
        : Array.isArray(resp?.auto_stopped_workers)
          ? resp.auto_stopped_workers
          : [];
      const stoppedNames = processes
        .filter((x: any) => String(x?.stop_status || "").startsWith("stopped"))
        .map((x: any) => String(x?.process || x?.worker || ""))
        .filter(Boolean);
      if (stoppedNames.length) {
        setMsg(`账户 ${aid} 已断开，并自动停止自动交易进程：${stoppedNames.join(", ")}`);
      } else if (resp?.all_accounts_disconnected === false) {
        setMsg(`账户 ${aid} 已断开。仍有其他账户保持连接，自动交易继续运行。`);
      } else {
        setMsg(`账户 ${aid} 已断开。`);
      }
      setErr("");
      const latest = await apiGet<SetupAccountsResponse>("/setup/accounts");
      setAccountsResp(latest);
      await refreshFeeBrokersMeta().catch(() => {});
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setAccountActionLoading((s) => ({ ...s, [aid]: undefined }));
    }
  };

  const deleteAccount = async (accountId: string, brokerProvider?: string) => {
    const aid = String(accountId || "").trim();
    if (!aid) return;
    const broker = String(brokerProvider || "").trim();
    const label = broker ? `${aid} / ${broker}` : aid;
    if (!window.confirm(`确定删除券商账户 ${label}？本地保存的该账户 API 密钥也会从账户列表中移除。`)) return;
    setAccountActionLoading((s) => ({ ...s, [aid]: "delete" }));
    try {
      const resp = await apiDelete<any>(`/setup/accounts/${encodeURIComponent(aid)}`, {
        timeoutMs: 30000,
        retries: 0,
      });
      const processes = Array.isArray(resp?.auto_stopped_processes)
        ? resp.auto_stopped_processes
        : Array.isArray(resp?.auto_stopped_workers)
          ? resp.auto_stopped_workers
          : [];
      const stoppedNames = processes
        .filter((x: any) => String(x?.stop_status || "").startsWith("stopped"))
        .map((x: any) => String(x?.process || x?.worker || ""))
        .filter(Boolean);
      setMsg(
        stoppedNames.length
          ? `账户 ${aid} 已删除，并已停止相关自动交易进程：${stoppedNames.join(", ")}`
          : `账户 ${aid} 已删除。`
      );
      setErr("");
      const latest = await apiGet<SetupAccountsResponse>("/setup/accounts", { cacheTtlMs: 0 });
      setAccountsResp(latest);
      await refreshFeeBrokersMeta().catch(() => {});
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setAccountActionLoading((s) => ({ ...s, [aid]: undefined }));
    }
  };

  const activeSectionMeta = SETUP_SECTIONS.find((x) => x.key === activeSection) || SETUP_SECTIONS[0];
  const defaultAccount = accountsResp?.accounts?.find((x) => x.is_default) || accountsResp?.accounts?.[0] || null;
  const accountReady = Boolean(defaultAccount && !defaultAccount.manual_disconnected && defaultAccount.quote_ready && defaultAccount.trade_ready);
  const accountStatusText = defaultAccount
    ? accountReady
      ? "已连接"
      : defaultAccount.manual_disconnected
        ? "手动断开"
        : defaultAccount.status || "未就绪"
    : "未注册";
  const serviceRunning = Boolean(services?.feishu_bot_running || services?.auto_trader_scheduler_running);
  const accountCount = accountsResp?.accounts?.length || 0;
  const normalizedAccountBroker = accountForm.broker_provider.trim().toLowerCase() || "longbridge";
  const accountFormExistingIds = new Set((accountsResp?.accounts || []).map((x) => String(x.account_id || "").trim()));
  const accountFormCreatesAdditional =
    !accountFormExistingIds.has(accountForm.account_id.trim()) && accountFormExistingIds.size >= 1;
  const accountFormUsesNonDefaultBroker = normalizedAccountBroker !== "longbridge";
  const accountRegistrationNeedsPremium =
    (accountFormCreatesAdditional || accountFormUsesNonDefaultBroker) && !entitlements.canUse("multi_broker");
  const cnProviders = cnProviderStatus?.providers || [];
  const cnReadyCount = cnProviders.filter((p) => p.configured || p.installed || cnInstallRestartHint[p.id]).length;
  const cnProviderSummary = cnProviders.length ? `${cnReadyCount}/${cnProviders.length}` : "未检测";
  const enabledCnProviderOrder =
    form.cn_market_data_provider_order || status?.values?.cn_market_data_provider_order || "mootdx,local_cache,akshare,tushare,baostock";
  const hasAnyMarketApi = Boolean(status?.configured.market_apis);
  const hasTushareToken = Boolean(status?.values?.tushare_token);
  const riskEnabled = Boolean(risk?.enabled);
  const openbbEnabled = String(form.openbb_enabled || status?.values?.openbb_enabled || "").toLowerCase() === "true";
  const tradingAgentsEnabled =
    String(form.tradingagents_enabled || status?.values?.tradingagents_enabled || "").toLowerCase() === "true" ||
    Boolean(status?.configured.tradingagents);
  const hasLlmKey = Boolean(llmProviderMaskedCurrent) || String(taDraft.provider || "").toLowerCase() === "ollama";
  const mcpLevel = form.openclaw_mcp_max_level || status?.values?.openclaw_mcp_max_level || "L2";
  const sectionStates: Record<SetupSectionKey, SetupSectionState> = {
    accounts: {
      tone: accountReady ? "ready" : accountCount ? "attention" : "neutral",
      label: accountReady ? "可交易" : accountCount ? "需连接" : "待注册",
      detail: defaultAccount
        ? `默认账户 ${defaultAccount.account_id} · ${defaultAccount.broker_provider}`
        : "先注册一个默认券商账户，再做连接诊断。",
      nextStep: accountReady ? "可继续检查费用模型和风控。" : accountCount ? "连接默认账户，并确认行情/交易都就绪。" : "填写券商凭证并注册默认账户。",
      metrics: [
        { label: "账户数", value: String(accountCount), tone: accountCount ? "ready" : "neutral" },
        { label: "默认账户", value: defaultAccount?.account_id || "-", tone: defaultAccount ? "ready" : "neutral" },
        { label: "连接", value: accountStatusText, tone: accountReady ? "ready" : defaultAccount ? "attention" : "neutral" },
      ],
    },
    secrets: {
      tone: status?.configured.longport && status?.configured.feishu ? "ready" : status?.configured.longport ? "attention" : "neutral",
      label: status?.configured.longport && status?.configured.feishu ? "核心已配" : status?.configured.longport ? "缺 Feishu" : "待配置",
      detail: "集中管理券商、飞书、外部行情 API Key。",
      nextStep: status?.configured.longport ? "只填写需要更新的密钥，然后保存。" : "先补齐 LONGPORT_APP_KEY / SECRET / ACCESS_TOKEN。",
      metrics: [
        { label: "Broker API", value: status?.configured.longport ? "已配置" : "未配置", tone: status?.configured.longport ? "ready" : "attention" },
        { label: "Feishu", value: status?.configured.feishu ? "已配置" : "未配置", tone: status?.configured.feishu ? "ready" : "neutral" },
        { label: "行情 Key", value: hasAnyMarketApi ? "已配置" : "可选", tone: hasAnyMarketApi ? "ready" : "neutral" },
      ],
    },
    research: {
      tone: openbbEnabled || cnReadyCount > 0 ? "ready" : "neutral",
      label: openbbEnabled ? "OpenBB 已启用" : cnReadyCount > 0 ? "A股可用" : "可选增强",
      detail: "Research 外部数据源、OpenBB 服务与 A 股免费数据源。",
      nextStep: openbbEnabled ? "测试 OpenBB 连接；A 股数据源按需要安装。" : "需要美股外部因子时启用 OpenBB；A 股可优先使用 local_cache/AkShare。",
      metrics: [
        { label: "OpenBB", value: openbbEnabled ? "已启用" : "未启用", tone: openbbEnabled ? "ready" : "neutral" },
        { label: "A股源", value: cnProviderSummary, tone: cnReadyCount > 0 ? "ready" : "neutral" },
        { label: "优先级", value: enabledCnProviderOrder, tone: hasTushareToken ? "ready" : "neutral" },
      ],
    },
    agents: {
      tone: tradingAgentsEnabled && hasLlmKey ? "ready" : tradingAgentsEnabled ? "attention" : "neutral",
      label: tradingAgentsEnabled && hasLlmKey ? "可运行" : tradingAgentsEnabled ? "缺 Key" : "未启用",
      detail: `Provider ${taDraft.provider} · Deep ${taDraft.deepModel} · Quick ${taDraft.quickModel}`,
      nextStep: hasLlmKey ? "保存模型参数后即可给 Research 调用。" : `为 ${llmProviderEnvKey} 填写 API Key，或切换到本地模型。`,
      metrics: [
        { label: "启用", value: tradingAgentsEnabled ? "true" : "false", tone: tradingAgentsEnabled ? "ready" : "neutral" },
        { label: "Provider", value: taDraft.provider, tone: "neutral" },
        { label: "LLM Key", value: hasLlmKey ? "已配置" : "未配置", tone: hasLlmKey ? "ready" : "attention" },
      ],
    },
    fees: {
      tone: feeEffectiveBrokerId ? "ready" : "attention",
      label: feeEffectiveBrokerId ? "模板可用" : "待检查",
      detail: "费用试算和回测会按默认账户券商自动选择模板。",
      nextStep: feeEffectiveBrokerId ? "检查当前模板费率；需要时保存修改或做一次试算。" : "先加载或新增一个券商费用模板。",
      metrics: [
        { label: "当前模板", value: feeEffectiveBrokerId || "-", tone: feeEffectiveBrokerId ? "ready" : "attention" },
        { label: "模板数", value: String(feeBrokers.length), tone: feeBrokers.length ? "ready" : "neutral" },
        { label: "来源", value: feeSource || "auto", tone: "neutral" },
      ],
    },
    risk: {
      tone: riskEnabled && serviceRunning ? "ready" : riskEnabled ? "attention" : "neutral",
      label: riskEnabled ? (serviceRunning ? "运行中" : "风控已开") : "风控关闭",
      detail: "风控参数与系统服务状态；飞书在通知中心管理，自动交易在自动交易页面管理。",
      nextStep: riskEnabled ? "确认最大订单金额和单日损失阈值；如需飞书通知请去通知中心管理。" : "建议先启用风控，再开启自动交易相关模块。",
      metrics: [
        { label: "风控", value: riskEnabled ? "已启用" : "未启用", tone: riskEnabled ? "ready" : "attention" },
        { label: "Feishu Bot", value: services?.feishu_bot_running ? "运行中" : "未运行", tone: services?.feishu_bot_running ? "ready" : "neutral" },
        { label: "Auto Trader", value: services?.auto_trader_scheduler_running ? "运行中" : "未运行", tone: services?.auto_trader_scheduler_running ? "ready" : "neutral" },
      ],
    },
    advanced: {
      tone: mcpLevel === "L3" ? "attention" : "neutral",
      label: `MCP ${mcpLevel}`,
      detail: "OpenClaw MCP 授权级别与本机 Worker 个人 API Key。",
      nextStep: mcpLevel === "L3" ? "L3 会放开高风险工具，请确认 token 和调用侧权限。" : "默认 L2 更适合日常自动化；个人 API Key 可按需创建。",
      metrics: [
        { label: "MCP 等级", value: mcpLevel, tone: mcpLevel === "L3" ? "attention" : "neutral" },
        { label: "L3 开关", value: form.openclaw_mcp_allow_l3 || status?.values?.openclaw_mcp_allow_l3 || "false", tone: "neutral" },
        { label: "API Key", value: "独立管理", tone: "neutral" },
      ],
    },
  };
  const activeSectionState = sectionStates[activeSection];
  const sectionActions: SetupSectionAction[] =
    activeSection === "accounts"
      ? [
          { label: "刷新状态", onClick: load, disabled: statusLoading || accountsLoading },
          { label: "测试券商连接", onClick: probeLongPort, variant: "primary" },
        ]
      : activeSection === "secrets"
        ? [
            { label: "刷新状态", onClick: load, disabled: statusLoading },
            { label: saving ? "保存中..." : "保存密钥", onClick: saveSecrets, disabled: saving, variant: "primary" },
          ]
        : activeSection === "research"
          ? [
              { label: saving ? "保存中..." : "保存数据源配置", onClick: saveSecrets, disabled: saving, variant: "primary" },
              { label: testingOpenbb ? "测试中..." : "测试 OpenBB", onClick: testOpenbbDiagnostics, disabled: testingOpenbb },
            ]
          : activeSection === "agents"
            ? [
                { label: saving ? "保存中..." : "保存 TradingAgents", onClick: saveSecrets, disabled: saving, variant: "primary" },
              ]
            : activeSection === "fees"
              ? [
                  { label: savingFees ? "保存中..." : "保存费用模型", onClick: saveFeeSchedule, disabled: savingFees, variant: "primary" },
                  { label: "费用试算", onClick: runFeeEstimate, disabled: savingFees },
                ]
              : activeSection === "risk"
                ? [
                    { label: "保存风控参数", onClick: saveRisk, disabled: !risk, variant: "primary" },
                    { label: stoppingAll ? "关闭中..." : "关闭系统", onClick: stopAllServices, disabled: stoppingAll },
                  ]
                : [
                    { label: saving ? "保存中..." : "保存高级设置", onClick: saveSecrets, disabled: saving, variant: "primary" },
                  ];

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">配置中心</h1>
            <div className="mt-1 text-sm text-slate-300">
              {activeSectionMeta.title} · {activeSectionMeta.description}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="tag-muted">Broker API {statusLoading ? "检测中..." : status?.configured.longport ? "已配置" : "未配置"}</span>
            <span className="tag-muted">Feishu {statusLoading ? "检测中..." : status?.configured.feishu ? "已配置" : "未配置"}</span>
            <span className="tag-muted">OpenBB {statusLoading ? "检测中..." : status?.configured.openbb ? "已启用" : "未启用"}</span>
            <span className="tag-muted">TradingAgents {statusLoading ? "检测中..." : status?.configured.tradingagents ? "已启用" : "未启用"}</span>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2 text-sm md:grid-cols-4 xl:grid-cols-7">
          {[
            { label: "默认账户", value: accountStatusText, tone: accountReady ? "ready" : defaultAccount ? "attention" : "neutral" },
            { label: "Broker API", value: statusLoading ? "检测中" : status?.configured.longport ? "已配置" : "未配置", tone: status?.configured.longport ? "ready" : "attention" },
            { label: "Feishu", value: statusLoading ? "检测中" : status?.configured.feishu ? "已配置" : "未配置", tone: status?.configured.feishu ? "ready" : "neutral" },
            { label: "行情 API", value: statusLoading ? "检测中" : status?.configured.market_apis ? "已配置" : "可选", tone: status?.configured.market_apis ? "ready" : "neutral" },
            { label: "OpenBB", value: statusLoading ? "检测中" : status?.configured.openbb ? "已启用" : "未启用", tone: status?.configured.openbb ? "ready" : "neutral" },
            { label: "Agents", value: statusLoading ? "检测中" : status?.configured.tradingagents ? "已启用" : "未启用", tone: status?.configured.tradingagents ? "ready" : "neutral" },
            { label: "服务", value: serviceRunning ? "运行中" : "未运行", tone: serviceRunning ? "ready" : "neutral" },
          ].map((item) => (
            <div key={item.label} className="rounded-lg border border-slate-700/70 bg-slate-900/70 px-3 py-2">
              <div className="text-[11px] text-slate-500">{item.label}</div>
              <div className={`mt-0.5 truncate text-sm font-semibold ${setupMetricValueClass(item.tone as SetupSectionTone)}`}>
                {item.value}
              </div>
            </div>
          ))}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {SETUP_SECTIONS.map((section) => {
            const active = activeSection === section.key;
            const sectionState = sectionStates[section.key];
            return (
              <button
                key={section.key}
                type="button"
                className={`min-w-[10.5rem] rounded-lg border px-3 py-2 text-left text-xs transition ${
                  active
                    ? "border-cyan-400/50 bg-cyan-500/15 text-cyan-100 shadow-[0_0_0_1px_rgba(34,211,238,0.18)]"
                    : "border-slate-700/70 bg-slate-950/35 text-slate-300 hover:border-slate-500/80 hover:bg-slate-800/70"
                }`}
                onClick={() => setActiveSection(section.key)}
              >
                <span className="flex items-center justify-between gap-3">
                  <span className="font-medium">{section.title}</span>
                  <span className="flex items-center gap-1.5 text-[11px] text-slate-400">
                    <span className={`h-1.5 w-1.5 rounded-full ${setupToneDotClass(sectionState.tone)}`} />
                    {sectionState.label}
                  </span>
                </span>
                <span className="mt-0.5 block text-[11px] text-slate-500">{section.description}</span>
              </button>
            );
          })}
        </div>
      </div>

      {msg ? <div className="panel border-emerald-200 bg-emerald-50 text-emerald-700">{msg}</div> : null}
      {err ? <div className="panel border-rose-200 bg-rose-50 text-rose-700">{err}</div> : null}

      <div className="sticky top-3 z-20 rounded-xl border border-slate-700/70 bg-slate-950/90 p-2 shadow-xl shadow-black/20 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <span>当前分组：<span className="text-slate-100">{activeSectionMeta.title}</span></span>
            <span className={`rounded-full border px-2 py-0.5 ${setupTonePillClass(activeSectionState.tone)}`}>
              {activeSectionState.label}
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {sectionActions.map((action) => (
              <button
                key={action.label}
                className={`${action.variant === "primary" ? "btn-primary" : "btn-secondary"} px-3 py-1.5 text-xs`}
                onClick={action.onClick}
                disabled={action.disabled}
              >
                {action.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="panel border-slate-700/70 bg-slate-950/35">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-3xl">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold text-slate-100">{activeSectionMeta.title}</h2>
              <span className={`rounded-full border px-2 py-0.5 text-xs ${setupTonePillClass(activeSectionState.tone)}`}>
                {activeSectionState.label}
              </span>
            </div>
            <p className="mt-1 text-sm text-slate-400">{activeSectionState.detail}</p>
            <p className="mt-2 text-xs text-cyan-200">下一步：{activeSectionState.nextStep}</p>
          </div>
          <div className="grid w-full grid-cols-1 gap-2 sm:grid-cols-3 lg:max-w-xl">
            {activeSectionState.metrics.map((metric) => (
              <div key={metric.label} className="rounded-lg border border-slate-800/80 bg-slate-900/60 px-3 py-2">
                <div className="text-[11px] text-slate-500">{metric.label}</div>
                <div className={`mt-1 truncate text-sm font-semibold ${setupMetricValueClass(metric.tone)}`}>{metric.value}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {activeSection === "accounts" ? (
        <>
      <div className="panel space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="field-label">账户管理（本地持久化）</div>
          <button type="button" className="btn-primary px-3 py-2 text-xs" onClick={() => setShowAccountRegistrationForm(true)}>
            添加账户
          </button>
        </div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
            <span>
              默认账户：<span className="text-slate-200">{accountsResp?.default_account_id || "-"}</span>
            </span>
            <button className="btn-secondary" onClick={load} disabled={accountsLoading}>
              {accountsLoading ? "刷新中..." : "刷新账户列表"}
            </button>
          </div>
          <div className="mt-3 table-shell">
            <table className="min-w-full text-xs">
              <thead className="table-head">
                <tr className="text-left">
                  <th className="px-3 py-2">account_id</th>
                  <th className="px-3 py-2">broker</th>
                  <th className="px-3 py-2">默认</th>
                  <th className="px-3 py-2">状态</th>
                  <th className="px-3 py-2">上下文</th>
                  <th className="px-3 py-2">最近错误</th>
                  <th className="px-3 py-2">操作</th>
                </tr>
              </thead>
              <tbody>
                {(accountsResp?.accounts || []).map((ac) => (
                  <tr key={ac.account_id} className="border-t border-slate-800/90">
                    <td className="px-3 py-2 text-slate-200">{ac.account_id}</td>
                    <td className="px-3 py-2 text-slate-300">{ac.broker_provider}</td>
                    <td className="px-3 py-2">{ac.is_default ? <span className="text-emerald-300">是</span> : <span className="text-slate-400">否</span>}</td>
                    <td className="px-3 py-2 text-slate-300">
                      {ac.manual_disconnected ? "disconnected(manual)" : ac.status || "-"}
                    </td>
                    <td className="px-3 py-2 text-slate-400">
                      Q:{ac.quote_ready ? "Y" : "N"} / T:{ac.trade_ready ? "Y" : "N"}
                    </td>
                    <td className="px-3 py-2 text-rose-300">{ac.last_error || "-"}</td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-2">
                        <button
                          className="btn-secondary px-2 py-1 text-xs"
                          onClick={() => connectAccount(ac.account_id)}
                          disabled={!!accountActionLoading[ac.account_id] || (!ac.manual_disconnected && ac.quote_ready && ac.trade_ready)}
                        >
                          {accountActionLoading[ac.account_id] === "connect" ? "连接中..." : "连接"}
                        </button>
                        <button
                          className="btn-secondary px-2 py-1 text-xs"
                          onClick={() => disconnectAccount(ac.account_id)}
                          disabled={!!accountActionLoading[ac.account_id] || !!ac.manual_disconnected}
                        >
                          {accountActionLoading[ac.account_id] === "disconnect" ? "断开中..." : "断开"}
                        </button>
                        <button
                          className="btn-secondary border-rose-500/40 px-2 py-1 text-xs text-rose-200"
                          onClick={() => deleteAccount(ac.account_id, ac.broker_provider)}
                          disabled={!!accountActionLoading[ac.account_id]}
                        >
                          {accountActionLoading[ac.account_id] === "delete" ? "删除中..." : "删除"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!(accountsResp?.accounts || []).length ? (
                  <tr className="border-t border-slate-800/90">
                    <td className="px-3 py-2 text-slate-400" colSpan={7}>
                      暂无账户
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>

        {showAccountRegistrationForm ? (
          <>
        {accountRegistrationNeedsPremium ? (
          <EntitlementNotice
            feature="multi_broker"
            plan={entitlements.plan}
            title={accountFormUsesNonDefaultBroker ? "多券商需要 Premium" : "多账户需要 Premium"}
          />
        ) : null}

        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="setup-account-id" className="text-xs font-medium text-slate-400">
              账户名
            </label>
            <input
              id="setup-account-id"
              className="input-base"
              placeholder="如 default、sim-01（对应后端 account_id）"
              value={accountForm.account_id}
              onChange={(e) => setAccountForm((s) => ({ ...s, account_id: e.target.value }))}
              autoComplete="off"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="setup-broker-provider" className="text-xs font-medium text-slate-400">
              券商名（broker_provider）
            </label>
            {feeBrokers.length > 0 ? (
              <>
                <select
                  id="setup-broker-provider"
                  className="input-base"
                  value={
                    feeBrokers.some((b) => b.broker_id === accountForm.broker_provider)
                      ? accountForm.broker_provider
                      : "__manual__"
                  }
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === "__manual__") {
                      setAccountForm((s) => ({ ...s, broker_provider: "" }));
                    } else {
                      setAccountForm((s) => ({ ...s, broker_provider: v }));
                    }
                  }}
                >
                  {feeBrokers.map((b) => (
                    <option key={b.broker_id} value={b.broker_id}>
                      {b.display_name} ({b.broker_id})
                    </option>
                  ))}
                  <option value="__manual__">其他（手动输入）</option>
                </select>
                {!feeBrokers.some((b) => b.broker_id === accountForm.broker_provider) ? (
                  <input
                    className="input-base mt-1"
                    placeholder="手动输入 broker_provider"
                    value={accountForm.broker_provider}
                    onChange={(e) => setAccountForm((s) => ({ ...s, broker_provider: e.target.value }))}
                    autoComplete="off"
                  />
                ) : null}
                <div className="text-[11px] text-slate-500">选项来自下方「交易费用模型」中的券商列表；新增券商后此处会自动出现。</div>
              </>
            ) : (
              <>
                <input
                  id="setup-broker-provider"
                  className="input-base"
                  placeholder="如 longbridge；或在下方费用模型中添加券商后出现下拉选项"
                  value={accountForm.broker_provider}
                  onChange={(e) => setAccountForm((s) => ({ ...s, broker_provider: e.target.value }))}
                  autoComplete="off"
                />
                <div className="text-[11px] text-slate-500">暂无费用券商档案时请手动填写；与费用模块中的券商 ID 保持一致便于对账。</div>
              </>
            )}
          </div>
          <div className="flex items-center gap-3 rounded-lg border border-slate-700/70 bg-slate-900/50 px-3 py-2 text-xs">
            <label className="flex items-center gap-1 text-slate-300">
              <input
                type="checkbox"
                checked={accountForm.is_default}
                onChange={(e) => setAccountForm((s) => ({ ...s, is_default: e.target.checked }))}
              />
              设为默认
            </label>
            <label className="flex items-center gap-1 text-slate-300">
              <input
                type="checkbox"
                checked={accountForm.overwrite}
                onChange={(e) => setAccountForm((s) => ({ ...s, overwrite: e.target.checked }))}
              />
              覆盖同名
            </label>
          </div>
          {["longbridge", "longport"].includes(accountForm.broker_provider.trim().toLowerCase()) ? (
            <>
              <SecretInputWithLink
                label="申请 Longbridge OpenAPI"
                href={externalCredentialLinks.longbridgeOpenApi}
                input={
                  <input
                    className="input-base"
                    type="password"
                    placeholder="LONGPORT_APP_KEY（可留空，回退环境配置）"
                    value={accountForm.longport_app_key}
                    onChange={(e) => setAccountForm((s) => ({ ...s, longport_app_key: e.target.value }))}
                  />
                }
              />
              <SecretInputWithLink
                label="申请 Longbridge OpenAPI"
                href={externalCredentialLinks.longbridgeOpenApi}
                input={
                  <input
                    className="input-base"
                    type="password"
                    placeholder="LONGPORT_APP_SECRET（可留空，回退环境配置）"
                    value={accountForm.longport_app_secret}
                    onChange={(e) => setAccountForm((s) => ({ ...s, longport_app_secret: e.target.value }))}
                  />
                }
              />
              <SecretInputWithLink
                label="申请 Longbridge OpenAPI"
                href={externalCredentialLinks.longbridgeOpenApi}
                input={
                  <input
                    className="input-base"
                    type="password"
                    placeholder="LONGPORT_ACCESS_TOKEN（可留空，回退环境配置）"
                    value={accountForm.longport_access_token}
                    onChange={(e) => setAccountForm((s) => ({ ...s, longport_access_token: e.target.value }))}
                  />
                }
              />
            </>
          ) : null}
          {["tiger", "itiger"].includes(accountForm.broker_provider.trim().toLowerCase()) ? (
            <div className="grid gap-2 rounded border border-slate-700/70 bg-slate-950/40 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="text-xs font-medium text-slate-300">Tiger OpenAPI</div>
                <CredentialLink href={externalCredentialLinks.tigerOpenApi}>申请 Tiger OpenAPI</CredentialLink>
              </div>
              <input
                className="input-base"
                type="password"
                placeholder="tiger_id"
                value={accountForm.tiger_id}
                onChange={(e) => setAccountForm((s) => ({ ...s, tiger_id: e.target.value }))}
              />
              <input
                className="input-base"
                type="password"
                placeholder="account"
                value={accountForm.tiger_account}
                onChange={(e) => setAccountForm((s) => ({ ...s, tiger_account: e.target.value }))}
              />
              <input
                className="input-base"
                type="password"
                placeholder="license"
                value={accountForm.tiger_license}
                onChange={(e) => setAccountForm((s) => ({ ...s, tiger_license: e.target.value }))}
              />
              <select
                className="input-base"
                value={accountForm.tiger_env}
                onChange={(e) => setAccountForm((s) => ({ ...s, tiger_env: e.target.value }))}
              >
                <option value="PAPER">PAPER</option>
                <option value="PROD">PROD</option>
                <option value="SANDBOX">SANDBOX</option>
              </select>
              <input
                className="input-base"
                type="password"
                placeholder="private_key_path"
                value={accountForm.tiger_private_key_path}
                onChange={(e) => setAccountForm((s) => ({ ...s, tiger_private_key_path: e.target.value }))}
              />
              <input
                className="input-base"
                type="password"
                placeholder="props_path"
                value={accountForm.tiger_props_path}
                onChange={(e) => setAccountForm((s) => ({ ...s, tiger_props_path: e.target.value }))}
              />
              <input
                className="input-base"
                type="password"
                placeholder="secret_key"
                value={accountForm.tiger_secret_key}
                onChange={(e) => setAccountForm((s) => ({ ...s, tiger_secret_key: e.target.value }))}
              />
              <input
                className="input-base"
                type="password"
                placeholder="token_path"
                value={accountForm.tiger_token_path}
                onChange={(e) => setAccountForm((s) => ({ ...s, tiger_token_path: e.target.value }))}
              />
            </div>
          ) : null}
          {["fosun", "fosunwealth"].includes(accountForm.broker_provider.trim().toLowerCase()) ? (
            <div className="grid gap-2 rounded border border-slate-700/70 bg-slate-950/40 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="text-xs font-medium text-slate-300">复兴证券 OpenAPI</div>
                <CredentialLink href={externalCredentialLinks.fosunOpenApi}>查看复兴 API 文档</CredentialLink>
              </div>
              <div className="flex flex-wrap items-center gap-2 rounded border border-cyan-500/20 bg-cyan-500/5 p-3">
                <button
                  type="button"
                  className="btn-secondary px-3 py-2 text-xs"
                  onClick={() => void refreshPublicIp()}
                  disabled={publicIpLoading}
                >
                  {publicIpLoading ? "查询 IP 中..." : "查询公网 IP"}
                </button>
                <button
                  type="button"
                  className="btn-secondary px-3 py-2 text-xs"
                  onClick={() => void generateFosunClientKeyPair()}
                  disabled={generatingFosunKeyPair}
                >
                  {generatingFosunKeyPair ? "生成中..." : "生成客户端密钥对"}
                </button>
                <div className="min-w-0 flex-1">
                  <div className="text-[11px] text-slate-400">后端出口公网 IP（提交给复兴 IP 白名单）</div>
                  <div className="mt-1 font-mono text-sm text-cyan-100">{publicIpInfo?.ip || "未查询"}</div>
                  <div className="mt-1 text-[11px] text-slate-500">
                    {publicIpInfo?.source ? `来源：${publicIpInfo.source}` : "云服务器部署时以服务器出口 IP 为准。"}
                  </div>
                  {publicIpInfo?.error ? <div className="mt-1 text-[11px] text-rose-300">{publicIpInfo.error}</div> : null}
                </div>
                <button
                  type="button"
                  className="btn-secondary px-3 py-2 text-xs"
                  onClick={() => void copyText(publicIpInfo?.ip || "", "已复制公网 IP。")}
                  disabled={!publicIpInfo?.ip}
                >
                  复制 IP
                </button>
              </div>
              <input
                className="input-base"
                type="password"
                placeholder="FSOPENAPI API Key"
                value={accountForm.fosun_api_key}
                onChange={(e) => setAccountForm((s) => ({ ...s, fosun_api_key: e.target.value }))}
              />
              <input
                className="input-base"
                placeholder="Base URL，例如 https://openapi-sit.fosunxcz.com"
                value={accountForm.fosun_base_url}
                onChange={(e) => setAccountForm((s) => ({ ...s, fosun_base_url: e.target.value }))}
              />
              <input
                className="input-base"
                placeholder="sub_account_id / 证券子账户号"
                value={accountForm.fosun_sub_account_id}
                onChange={(e) => setAccountForm((s) => ({ ...s, fosun_sub_account_id: e.target.value }))}
              />
              <input
                className="input-base"
                placeholder="client_id（可选）"
                value={accountForm.fosun_client_id}
                onChange={(e) => setAccountForm((s) => ({ ...s, fosun_client_id: e.target.value }))}
              />
              <textarea
                className="input-base min-h-24"
                placeholder="FSOPENAPI_SERVER_PUBLIC_KEY PEM"
                value={accountForm.fosun_server_public_key}
                onChange={(e) => setAccountForm((s) => ({ ...s, fosun_server_public_key: e.target.value }))}
              />
              <textarea
                className="input-base min-h-24"
                placeholder="FSOPENAPI_CLIENT_PRIVATE_KEY PEM"
                value={accountForm.fosun_client_private_key}
                onChange={(e) => setAccountForm((s) => ({ ...s, fosun_client_private_key: e.target.value }))}
              />
              {fosunClientPublicKey ? (
                <div className="grid gap-2 rounded border border-emerald-500/20 bg-emerald-500/5 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div className="text-xs font-medium text-emerald-100">客户端公钥</div>
                      <p className="mt-1 text-[11px] text-slate-500">把这段 PUBLIC KEY 提交给复兴绑定；私钥只保存在上方表单里。</p>
                    </div>
                    <button
                      type="button"
                      className="btn-secondary px-3 py-2 text-xs"
                      onClick={() => void copyText(fosunClientPublicKey, "已复制复兴客户端公钥。")}
                    >
                      复制客户端公钥
                    </button>
                  </div>
                  <textarea className="input-base min-h-24 font-mono text-xs" readOnly value={fosunClientPublicKey} />
                </div>
              ) : null}
              <select
                className="input-base"
                value={accountForm.fosun_sdk_type}
                onChange={(e) => setAccountForm((s) => ({ ...s, fosun_sdk_type: e.target.value }))}
              >
                <option value="">默认 /api</option>
                <option value="ops">ops /api/ops</option>
              </select>
              <input
                className="input-base"
                placeholder="apply_account_id（可选，子账号/柜台账号）"
                value={accountForm.fosun_apply_account_id}
                onChange={(e) => setAccountForm((s) => ({ ...s, fosun_apply_account_id: e.target.value }))}
              />
              <input
                className="input-base"
                placeholder="option_apply_account_id（可选，期权子账户）"
                value={accountForm.fosun_option_apply_account_id}
                onChange={(e) => setAccountForm((s) => ({ ...s, fosun_option_apply_account_id: e.target.value }))}
              />
              <p className="text-xs leading-5 text-slate-500">
                系统会在连接该账户时临时注入 SDK 所需 PEM 环境变量；不需要写入 Windows 全局环境变量。第一版已支持账户、行情、资金、持仓、订单、下单和撤单；期权链选择仍需后续单独适配。
              </p>
            </div>
          ) : null}
          {accountForm.broker_provider.trim().toLowerCase() === "usmart" ? (
            <div className="grid gap-2 rounded border border-slate-700/70 bg-slate-950/40 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="text-xs font-medium text-slate-300">uSMART OpenAPI</div>
                <CredentialLink href={externalCredentialLinks.usmartOpenApi}>查看 uSMART 官网</CredentialLink>
              </div>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                <input
                  className="input-base"
                  placeholder="trade_host，例如 https://open-jy.yxzq.com"
                  value={accountForm.usmart_trade_host}
                  onChange={(e) => setAccountForm((s) => ({ ...s, usmart_trade_host: e.target.value }))}
                />
                <input
                  className="input-base"
                  placeholder="quote_host，例如 https://open-hz.yxzq.com:8443"
                  value={accountForm.usmart_quote_host}
                  onChange={(e) => setAccountForm((s) => ({ ...s, usmart_quote_host: e.target.value }))}
                />
                <input
                  className="input-base"
                  placeholder="X-Channel 渠道号"
                  value={accountForm.usmart_x_channel}
                  onChange={(e) => setAccountForm((s) => ({ ...s, usmart_x_channel: e.target.value }))}
                  autoComplete="off"
                />
                <input
                  className="input-base"
                  placeholder="X-Lang，默认 1"
                  value={accountForm.usmart_x_lang}
                  onChange={(e) => setAccountForm((s) => ({ ...s, usmart_x_lang: e.target.value }))}
                />
                <input
                  className="input-base"
                  placeholder="areaCode，默认 86"
                  value={accountForm.usmart_area_code}
                  onChange={(e) => setAccountForm((s) => ({ ...s, usmart_area_code: e.target.value }))}
                />
                <input
                  className="input-base"
                  placeholder="phoneNumber"
                  value={accountForm.usmart_phone_number}
                  onChange={(e) => setAccountForm((s) => ({ ...s, usmart_phone_number: e.target.value }))}
                  autoComplete="off"
                />
                <input
                  className="input-base"
                  type="password"
                  placeholder="login_password"
                  value={accountForm.usmart_login_password}
                  onChange={(e) => setAccountForm((s) => ({ ...s, usmart_login_password: e.target.value }))}
                  autoComplete="new-password"
                />
                <input
                  className="input-base"
                  type="password"
                  placeholder="trade_password"
                  value={accountForm.usmart_trade_password}
                  onChange={(e) => setAccountForm((s) => ({ ...s, usmart_trade_password: e.target.value }))}
                  autoComplete="new-password"
                />
                <input
                  className="input-base"
                  placeholder="timeout_seconds，默认 8"
                  value={accountForm.usmart_timeout_seconds}
                  onChange={(e) => setAccountForm((s) => ({ ...s, usmart_timeout_seconds: e.target.value }))}
                />
              </div>
              <textarea
                className="input-base min-h-24 font-mono text-xs"
                placeholder="uSMART 服务端 PUBLIC KEY PEM 或去掉头尾后的内容"
                value={accountForm.usmart_server_public_key}
                onChange={(e) => setAccountForm((s) => ({ ...s, usmart_server_public_key: e.target.value }))}
              />
              <textarea
                className="input-base min-h-24 font-mono text-xs"
                placeholder="uSMART 客户端 PRIVATE KEY PEM 或去掉头尾后的内容"
                value={accountForm.usmart_client_private_key}
                onChange={(e) => setAccountForm((s) => ({ ...s, usmart_client_private_key: e.target.value }))}
              />
              <p className="text-xs leading-5 text-slate-500">
                根据本地 demo 接入：登录、交易解锁、实时行情、持仓、今日委托、股票下单和撤单已映射；期权链、期权报价和历史 K 线暂未映射，自动期权 worker 不应选择该券商。
              </p>
            </div>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-primary" onClick={() => void registerAccount()} disabled={registeringAccount || accountRegistrationNeedsPremium}>
            {registeringAccount ? "注册中..." : "注册账户"}
          </button>
          <button type="button" className="btn-secondary" onClick={() => setShowAccountRegistrationForm(false)} disabled={registeringAccount}>
            取消
          </button>
        </div>
          </>
        ) : null}
      </div>

      <div className="panel space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="field-label">Broker API 连接诊断（可视化）</div>
            <p className="mt-1 text-xs text-slate-500">
              连接占用与上下文状态诊断，默认折叠。
            </p>
          </div>
          <button type="button" className="btn-secondary px-3 py-2 text-xs" onClick={() => setShowBrokerDiagnostics((v) => !v)}>
            {showBrokerDiagnostics ? "收起" : "展开"}
          </button>
        </div>
        {showBrokerDiagnostics ? (
          <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
            <div className="flex items-center justify-between text-sm">
              <span className="text-slate-300">估算总连接占用（API + MCP + Feishu）</span>
              <span className={(diag?.estimated_connections_total || 0) >= 8 ? "text-rose-300" : (diag?.estimated_connections_total || 0) >= 5 ? "text-amber-300" : "text-emerald-300"}>
                {diag?.estimated_connections_total ?? 0}/{diag?.connection_limit ?? 10}
              </span>
            </div>
            <div className="mt-2 h-2 w-full rounded bg-slate-800">
              <div
                className={`h-2 rounded ${((diag?.estimated_usage_pct_total || 0) >= 80 ? "bg-rose-500" : (diag?.estimated_usage_pct_total || 0) >= 50 ? "bg-amber-500" : "bg-emerald-500")}`}
                style={{ width: `${Math.max(0, Math.min(100, diag?.estimated_usage_pct_total || 0))}%` }}
              />
            </div>
            <div className="mt-2 text-xs text-slate-400">
              API: {diag?.estimated_breakdown?.api_active ?? 0} | MCP: {diag?.estimated_breakdown?.mcp_estimated ?? 0} | Feishu: {diag?.estimated_breakdown?.feishu_estimated ?? 0}
            </div>
            {(diag?.alert_level && diag.alert_level !== "ok") ? (
              <div className={`mt-2 rounded border px-2 py-1 text-xs ${
                diag.alert_level === "critical"
                  ? "border-rose-500/50 bg-rose-950/30 text-rose-300"
                  : diag.alert_level === "warning"
                    ? "border-amber-500/50 bg-amber-950/20 text-amber-300"
                    : "border-cyan-500/40 bg-cyan-950/20 text-cyan-300"
              }`}>
                连接占用告警：{diag.alert_level === "critical" ? "严重" : diag.alert_level === "warning" ? "偏高" : "注意"}
              </div>
            ) : null}
            <div className="mt-3 border-t border-slate-800 pt-3" />
            <div className="flex items-center justify-between text-sm">
              <span className="text-slate-300">当前 API 进程连接占用</span>
              <span className={diag?.active_connections_api_process ? "text-amber-300" : "text-emerald-300"}>
                {diag?.active_connections_api_process ?? 0}/{diag?.connection_limit ?? 10}
              </span>
            </div>
            <div className="mt-2 h-2 w-full rounded bg-slate-800">
              <div
                className={`h-2 rounded ${((diag?.usage_pct_api_process || 0) >= 80 ? "bg-rose-500" : (diag?.usage_pct_api_process || 0) >= 50 ? "bg-amber-500" : "bg-emerald-500")}`}
                style={{ width: `${Math.max(0, Math.min(100, diag?.usage_pct_api_process || 0))}%` }}
              />
            </div>
            <div className="mt-2 text-xs text-slate-400">
              QuoteCtx: {diag?.quote_ctx_ready ? "已建立" : "未建立"} | TradeCtx: {diag?.trade_ctx_ready ? "已建立" : "未建立"}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              进程状态：API {diag?.processes?.api?.running ? "运行" : "未运行"} / MCP {diag?.processes?.mcp?.running ? "运行" : "未运行"} / Feishu {diag?.processes?.feishu_bot?.running ? "运行" : "未运行"}
            </div>
            {(diag?.recommendations || []).length ? (
              <div className="mt-2 rounded border border-slate-700/70 bg-slate-950/50 p-2 text-xs text-slate-300">
                <div className="mb-1 text-slate-200">建议操作</div>
                {(diag?.recommendations || []).map((x, i) => (
                  <div key={`${i}-${x}`}>- {x}</div>
                ))}
              </div>
            ) : null}
            {diag?.last_error ? <div className="mt-1 text-xs text-rose-300">最近错误：{diag.last_error}</div> : null}
            <div className="mt-1 text-xs text-slate-500">{diag?.note || "暂无诊断信息"}</div>
            <button className="btn-secondary mt-3" onClick={probeLongPort}>立即探测连接</button>
          </div>
        ) : null}
      </div>

        </>
      ) : null}

      {activeSection === "secrets" ? (
        <>
      <div className="panel space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="field-label">密钥配置</div>
            <p className="mt-1 text-xs text-slate-500">Broker、Feishu、LLM 与扩展行情 Key。仅填写要更新的项。</p>
          </div>
          <button className="btn-primary" onClick={saveSecrets} disabled={saving}>
            {saving ? "保存中..." : "保存秘钥到 .env"}
          </button>
        </div>
        <p className="text-xs text-slate-400">
          仅填写要更新的项；保存请使用页面下方「保存秘钥到 .env」。
        </p>

        <div className="space-y-2">
          <div className="field-label">Broker API（LONGPORT_*）</div>
          <p className="text-xs text-slate-500">券商 OpenAPI 凭证，写入环境变量 LONGPORT_APP_KEY / SECRET / ACCESS_TOKEN。</p>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            <SecretInputWithLink
              label="申请 Longbridge OpenAPI"
              href={externalCredentialLinks.longbridgeOpenApi}
              input={
                <input
                  className="input-base"
                  type="password"
                  placeholder={`LONGPORT_APP_KEY (当前: ${status?.values.longport_app_key || "未配置"})`}
                  value={form.longport_app_key}
                  onChange={(e) => setForm((s) => ({ ...s, longport_app_key: e.target.value }))}
                />
              }
            />
            <SecretInputWithLink
              label="申请 Longbridge OpenAPI"
              href={externalCredentialLinks.longbridgeOpenApi}
              input={
                <input
                  className="input-base"
                  type="password"
                  placeholder={`LONGPORT_APP_SECRET (当前: ${status?.values.longport_app_secret || "未配置"})`}
                  value={form.longport_app_secret}
                  onChange={(e) => setForm((s) => ({ ...s, longport_app_secret: e.target.value }))}
                />
              }
            />
            <SecretInputWithLink
              label="申请 Longbridge OpenAPI"
              href={externalCredentialLinks.longbridgeOpenApi}
              input={
                <input
                  className="input-base md:col-span-2"
                  type="password"
                  placeholder={`LONGPORT_ACCESS_TOKEN (当前: ${status?.values.longport_access_token || "未配置"})`}
                  value={form.longport_access_token}
                  onChange={(e) => setForm((s) => ({ ...s, longport_access_token: e.target.value }))}
                />
              }
            />
          </div>
        </div>

        <div className="space-y-2 border-t border-slate-800/80 pt-4">
          <div className="field-label">飞书（FEISHU_*）</div>
          <p className="text-xs text-slate-500">飞书应用与定时消息会话，写入 FEISHU_APP_ID / APP_SECRET / SCHEDULED_CHAT_ID。</p>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            <SecretInputWithLink
              label="打开飞书开放平台"
              href={externalCredentialLinks.feishuOpenPlatform}
              input={
                <input
                  className="input-base"
                  type="password"
                  placeholder={`FEISHU_APP_ID (当前: ${status?.values.feishu_app_id || "未配置"})`}
                  value={form.feishu_app_id}
                  onChange={(e) => setForm((s) => ({ ...s, feishu_app_id: e.target.value }))}
                />
              }
            />
            <SecretInputWithLink
              label="打开飞书开放平台"
              href={externalCredentialLinks.feishuOpenPlatform}
              input={
                <input
                  className="input-base"
                  type="password"
                  placeholder={`FEISHU_APP_SECRET (当前: ${status?.values.feishu_app_secret || "未配置"})`}
                  value={form.feishu_app_secret}
                  onChange={(e) => setForm((s) => ({ ...s, feishu_app_secret: e.target.value }))}
                />
              }
            />
            <SecretInputWithLink
              label="打开飞书开放平台"
              href={externalCredentialLinks.feishuOpenPlatform}
              input={
                <input
                  className="input-base md:col-span-2"
                  placeholder={`FEISHU_SCHEDULED_CHAT_ID (当前: ${status?.values.feishu_scheduled_chat_id || "未配置"})`}
                  value={form.feishu_scheduled_chat_id}
                  onChange={(e) => setForm((s) => ({ ...s, feishu_scheduled_chat_id: e.target.value }))}
                />
              }
            />
          </div>
        </div>
      </div>

      <div className="panel space-y-3">
        <div className="field-label">扩展行情 API Key（可选）</div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <SecretInputWithLink
            label="申请 Finnhub API Key"
            href={externalCredentialLinks.finnhub}
            input={<input className="input-base" type="password" placeholder={`FINNHUB_API_KEY (当前: ${status?.values.finnhub_api_key || "未配置"})`} value={form.finnhub_api_key} onChange={(e) => setForm((s) => ({ ...s, finnhub_api_key: e.target.value }))} />}
          />
          <SecretInputWithLink
            label="查看 Tiingo API Token"
            href={externalCredentialLinks.tiingo}
            input={<input className="input-base" type="password" placeholder={`TIINGO_API_KEY (当前: ${status?.values.tiingo_api_key || "未配置"})`} value={form.tiingo_api_key} onChange={(e) => setForm((s) => ({ ...s, tiingo_api_key: e.target.value }))} />}
          />
          <SecretInputWithLink
            label="申请 Polygon API Key"
            href={externalCredentialLinks.polygon}
            input={<input className="input-base" type="password" placeholder={`POLYGON_API_KEY (当前: ${status?.values.polygon_api_key || "未配置"})`} value={form.polygon_api_key} onChange={(e) => setForm((s) => ({ ...s, polygon_api_key: e.target.value }))} />}
          />
          <SecretInputWithLink
            label="申请 Twelve Data API Key"
            href={externalCredentialLinks.twelveData}
            input={<input className="input-base" type="password" placeholder={`TWELVE_DATA_API_KEY (当前: ${status?.values.twelve_data_api_key || "未配置"})`} value={form.twelve_data_api_key} onChange={(e) => setForm((s) => ({ ...s, twelve_data_api_key: e.target.value }))} />}
          />
          <SecretInputWithLink
            label="申请 FRED API Key"
            href={externalCredentialLinks.fred}
            input={<input className="input-base" type="password" placeholder={`FRED_API_KEY (当前: ${status?.values.fred_api_key || "未配置"})`} value={form.fred_api_key} onChange={(e) => setForm((s) => ({ ...s, fred_api_key: e.target.value }))} />}
          />
          <SecretInputWithLink
            label="申请 FMP API Key"
            href={externalCredentialLinks.fmp}
            input={<input className="input-base" type="password" placeholder={`FMP_API_KEY (当前: ${status?.values.fmp_api_key || "未配置"})`} value={form.fmp_api_key} onChange={(e) => setForm((s) => ({ ...s, fmp_api_key: e.target.value }))} />}
          />
          <SecretInputWithLink
            label="申请 CoinGecko API Key"
            href={externalCredentialLinks.coingecko}
            input={<input className="input-base" type="password" placeholder={`COINGECKO_API_KEY (当前: ${status?.values.coingecko_api_key || "未配置"})`} value={form.coingecko_api_key} onChange={(e) => setForm((s) => ({ ...s, coingecko_api_key: e.target.value }))} />}
          />
        </div>
        <button className="btn-primary" onClick={saveSecrets} disabled={saving}>
          {saving ? "保存中..." : "保存秘钥到 .env"}
        </button>
      </div>

        </>
      ) : null}

      {activeSection === "advanced" ? (
        <>
      <div className="panel space-y-3">
        <div className="field-label">OpenClaw MCP 工具分级授权（L1/L2/L3）</div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          <select
            className="input-base"
            value={form.openclaw_mcp_max_level}
            onChange={(e) => setForm((s) => ({ ...s, openclaw_mcp_max_level: e.target.value }))}
          >
            <option value="">OPENCLAW_MCP_MAX_LEVEL (当前: {status?.values.openclaw_mcp_max_level || "L2"})</option>
            <option value="L1">L1</option>
            <option value="L2">L2</option>
            <option value="L3">L3</option>
          </select>
          <select
            className="input-base"
            value={form.openclaw_mcp_allow_l3}
            onChange={(e) => setForm((s) => ({ ...s, openclaw_mcp_allow_l3: e.target.value }))}
          >
            <option value="">OPENCLAW_MCP_ALLOW_L3 (当前: {status?.values.openclaw_mcp_allow_l3 || "false"})</option>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <input
            className="input-base"
            type="password"
            placeholder={`OPENCLAW_MCP_L3_CONFIRMATION_TOKEN (当前: ${status?.values.openclaw_mcp_l3_confirmation_token || "未配置"})`}
            value={form.openclaw_mcp_l3_confirmation_token}
            onChange={(e) => setForm((s) => ({ ...s, openclaw_mcp_l3_confirmation_token: e.target.value }))}
          />
        </div>
        <div className="text-xs text-slate-400">
          提示：L3 需要同时满足 MAX_LEVEL=L3、ALLOW_L3=true，并在调用时提供 confirmation_token。
        </div>
      </div>

      <div className="panel space-y-3">
        <div>
          <div className="field-label">个人 API Key（自动化 / QQQ 实盘 Worker）</div>
          <p className="mt-1 text-xs text-slate-500">用于本地自动化调用与 Worker 鉴权，和券商凭证分开管理。</p>
        </div>
        <SetupApiKeysPanel />
      </div>

        </>
      ) : null}

      {activeSection === "research" ? (
        <>
      <div className="panel space-y-3">
        <div className="field-label">OpenBB 外部研究源（可选）</div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          <select
            className="input-base"
            value={form.openbb_enabled}
            onChange={(e) => setForm((s) => ({ ...s, openbb_enabled: e.target.value }))}
          >
            <option value="">OPENBB_ENABLED (当前: {status?.values.openbb_enabled || "false"})</option>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <input
            className="input-base"
            placeholder={`OPENBB_BASE_URL (当前: ${status?.values.openbb_base_url || "http://127.0.0.1:6900"})`}
            value={form.openbb_base_url}
            onChange={(e) => setForm((s) => ({ ...s, openbb_base_url: e.target.value }))}
          />
          <input
            className="input-base"
            placeholder={`OPENBB_TIMEOUT_SECONDS (当前: ${status?.values.openbb_timeout_seconds || "8"})`}
            value={form.openbb_timeout_seconds}
            onChange={(e) => setForm((s) => ({ ...s, openbb_timeout_seconds: e.target.value }))}
          />
          <select
            className="input-base"
            value={form.openbb_auto_start}
            onChange={(e) => setForm((s) => ({ ...s, openbb_auto_start: e.target.value }))}
          >
            <option value="">OPENBB_AUTO_START (当前: {status?.values.openbb_auto_start || "1"})</option>
            <option value="1">1</option>
            <option value="0">0</option>
          </select>
        </div>
        <div className="text-xs text-slate-400">
          建议先启动 OpenBB API（默认 http://127.0.0.1:6900），再点击“测试 OpenBB 连接”。
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary" onClick={testOpenbbDiagnostics} disabled={testingOpenbb || restartingOpenbb}>
            {testingOpenbb ? "诊断中..." : "诊断 OpenBB"}
          </button>
          <button className="btn-secondary" onClick={restartOpenbb} disabled={testingOpenbb || restartingOpenbb}>
            {restartingOpenbb ? "重启中..." : "重启 OpenBB 并清缓存"}
          </button>
        </div>
        {openbbDiagnostics ? (
          <div className="rounded-lg border border-slate-700/70 bg-slate-950/40 p-3 text-xs text-slate-300">
            <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
              <div>
                <div className="text-slate-500">API</div>
                <div className={openbbDiagnostics?.health?.ok ? "text-emerald-300" : "text-rose-300"}>{openbbOkText(openbbDiagnostics?.health?.ok)}</div>
                <div className="truncate text-slate-400">{openbbDiagnostics?.base_url || "-"}</div>
              </div>
              <div>
                <div className="text-slate-500">Process</div>
                <div>{openbbDiagnostics?.process?.port_open ? "port open" : "port closed"}</div>
                <div className="text-slate-400">PID: {(openbbDiagnostics?.process?.listener_pids || []).join(", ") || "-"}</div>
              </div>
              <div>
                <div className="text-slate-500">Cache</div>
                <div>{openbbDiagnostics?.cache?.enabled ? "enabled" : "disabled"}</div>
                <div className="text-slate-400">entries: {openbbDiagnostics?.cache?.entries ?? 0}</div>
              </div>
              <div>
                <div className="text-slate-500">Keys</div>
                <div>FMP: {openbbDiagnostics?.capabilities?.credentials?.fmp?.configured ? "configured" : "-"}</div>
                <div>FRED: {openbbDiagnostics?.capabilities?.credentials?.fred?.configured ? "configured" : "-"}</div>
              </div>
            </div>
            <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
              {[
                ["FMP profile", openbbDiagnostics?.capabilities?.fmp_profile],
                ["FMP ETF holdings", openbbDiagnostics?.capabilities?.fmp_etf_holdings],
                ["FMP ETF sectors", openbbDiagnostics?.capabilities?.fmp_etf_sectors],
                ["SEC", openbbDiagnostics?.capabilities?.sec],
                ["FRED", openbbDiagnostics?.capabilities?.fred],
                ["CFTC", openbbDiagnostics?.capabilities?.cftc],
              ].map(([label, item]: any) => (
                <div key={label} className="rounded border border-slate-800/80 px-2 py-1">
                  <div className="flex items-center justify-between gap-2">
                    <span>{label}</span>
                    <span className={item?.ok ? "text-emerald-300" : "text-amber-300"}>{openbbOkText(item?.ok)}</span>
                  </div>
                  <div className="truncate text-slate-500">
                    {openbbReasonText(item?.reason)}
                    {typeof item?.count === "number" ? ` / count ${item.count}` : ""}
                    {typeof item?.filings_count === "number" ? ` / filings ${item.filings_count}` : ""}
                    {typeof item?.available_count === "number" ? ` / available ${item.available_count}` : ""}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <div className="panel space-y-3">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div className="field-label">A 股数据源（可选）</div>
          <button
            type="button"
            className="btn-secondary"
            disabled={
              IS_CUSTOMER_BUILD ||
              Boolean(installingCnProvider) ||
              (Boolean(cnProviderById("mootdx")?.installed) &&
                Boolean(cnProviderById("akshare")?.installed) &&
                Boolean(cnProviderById("tushare")?.installed) &&
                Boolean(cnProviderById("baostock")?.installed))
            }
            onClick={() => void installCnProvider("all")}
          >
            {installingCnProvider === "all" ? "安装中..." : "一键安装全部"}
          </button>
        </div>
        <div className="text-xs leading-relaxed text-slate-400">
          这些配置控制 <code className="text-slate-300">/market-data/cn/*</code> 统一接口。未安装第三方库时系统会自动回退到本地 K 线缓存；免费数据源仅用于投研、回测、模拟交易和交易计划，不作为实盘自动下单依据。
        </div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
          {(["mootdx", "akshare", "tushare", "baostock"] as const).map((id) => {
            const p = cnProviderById(id);
            return (
              <div key={id} className="rounded-lg border border-slate-700/70 bg-slate-950/30 px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-slate-100">{p?.name || id}</span>
                  <span
                    className={
                      "rounded-full px-2 py-0.5 text-[11px] " +
                      (cnInstallRestartHint[id] || p?.configured || p?.installed
                        ? "bg-emerald-500/10 text-emerald-200"
                        : "bg-amber-500/10 text-amber-200")
                    }
                  >
                    {cnInstallRestartHint[id] ? "已安装，建议重启" : cnProviderStatusLabel(p)}
                  </span>
                </div>
                <div className="mt-1 text-[11px] leading-relaxed text-slate-500">
                  {cnInstallRestartHint[id] ? "安装已完成；重启后端后状态会从运行环境重新检测。" : cnProviderStatusHelp(id, p)}
                </div>
              </div>
            );
          })}
        </div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
          <select
            className="input-base"
            value={form.cn_market_mootdx_enabled}
            onChange={(e) => setForm((s) => ({ ...s, cn_market_mootdx_enabled: e.target.value }))}
          >
            <option value="">mootdx enabled (current: {status?.values.cn_market_mootdx_enabled || "true"})</option>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <button
            type="button"
            className="btn-secondary"
            disabled={IS_CUSTOMER_BUILD || Boolean(installingCnProvider) || Boolean(cnProviderById("mootdx")?.installed)}
            onClick={() => void installCnProvider("mootdx")}
          >
            {cnInstallButtonLabel("mootdx", "mootdx")}
          </button>
          <select
            className="input-base"
            value={form.cn_market_tencent_enabled}
            onChange={(e) => setForm((s) => ({ ...s, cn_market_tencent_enabled: e.target.value }))}
          >
            <option value="">Tencent valuation enabled (current: {status?.values.cn_market_tencent_enabled || "true"})</option>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <div className="rounded-lg border border-slate-700/70 bg-slate-950/30 px-3 py-2 text-xs text-slate-400">
            Tencent valuation uses public HTTP, no package install needed.
          </div>
          <select
            className="input-base"
            value={form.cn_market_akshare_enabled}
            onChange={(e) => setForm((s) => ({ ...s, cn_market_akshare_enabled: e.target.value }))}
          >
            <option value="">AkShare 启用 (当前: {status?.values.cn_market_akshare_enabled || "true"})</option>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <button
            type="button"
            className="btn-secondary"
            disabled={IS_CUSTOMER_BUILD || Boolean(installingCnProvider) || Boolean(cnProviderById("akshare")?.installed)}
            onClick={() => void installCnProvider("akshare")}
          >
            {cnInstallButtonLabel("akshare", "AkShare")}
          </button>
          <select
            className="input-base"
            value={form.cn_market_tushare_enabled}
            onChange={(e) => setForm((s) => ({ ...s, cn_market_tushare_enabled: e.target.value }))}
          >
            <option value="">Tushare 启用 (当前: {status?.values.cn_market_tushare_enabled || "true"})</option>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <button
            type="button"
            className="btn-secondary"
            disabled={IS_CUSTOMER_BUILD || Boolean(installingCnProvider) || Boolean(cnProviderById("tushare")?.installed)}
            onClick={() => void installCnProvider("tushare")}
          >
            {cnInstallButtonLabel("tushare", "Tushare")}
          </button>
          <select
            className="input-base"
            value={form.cn_market_baostock_enabled}
            onChange={(e) => setForm((s) => ({ ...s, cn_market_baostock_enabled: e.target.value }))}
          >
            <option value="">BaoStock 启用 (当前: {status?.values.cn_market_baostock_enabled || "true"})</option>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <button
            type="button"
            className="btn-secondary"
            disabled={IS_CUSTOMER_BUILD || Boolean(installingCnProvider) || Boolean(cnProviderById("baostock")?.installed)}
            onClick={() => void installCnProvider("baostock")}
          >
            {cnInstallButtonLabel("baostock", "BaoStock")}
          </button>
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <label className="space-y-1 text-xs text-slate-300">
            <span className="font-semibold text-slate-200">数据源优先级</span>
            <input
              className="input-base"
              name="cn-market-provider-order"
              autoComplete="off"
              data-lpignore="true"
              data-1p-ignore="true"
              data-form-type="other"
              spellCheck={false}
              placeholder={`当前: ${status?.values.cn_market_data_provider_order || "mootdx,local_cache,akshare,tushare,baostock"}`}
              value={form.cn_market_data_provider_order}
              onChange={(e) => setForm((s) => ({ ...s, cn_market_data_provider_order: e.target.value }))}
            />
          </label>
          <label className="space-y-1 text-xs text-slate-300">
            <span className="font-semibold text-slate-200">Tushare Pro Token</span>
            <input
              className="input-base"
              name="setup-tushare-pro-token"
              type="password"
              autoComplete="new-password"
              data-lpignore="true"
              data-1p-ignore="true"
              data-form-type="other"
              spellCheck={false}
              placeholder={`当前: ${status?.values.tushare_token || "未配置"}`}
              value={form.tushare_token}
              onChange={(e) => setForm((s) => ({ ...s, tushare_token: e.target.value }))}
            />
          </label>
        </div>

        <div className="rounded-lg border border-slate-800/80 bg-slate-950/30 px-3 py-2 text-xs leading-relaxed text-slate-500">
          优先级示例：<code className="text-slate-300">mootdx,local_cache,akshare,tushare,baostock</code> 表示先尝试 mootdx，再读本地缓存；如果想完全离线，可设为 <code className="text-slate-300">local_cache</code>。Tushare 只需要 Token，不需要填写用户名或密码。
        </div>
      </div>

        </>
      ) : null}

      {activeSection === "agents" ? (
        <>
      <div className="panel space-y-3">
        <div className="field-label">TradingAgents 研究增强（可选）</div>
        <div className="flex items-center justify-between">
          <div className="text-xs text-slate-400">
            每个配置项已显示字段名；标准模式用推荐下拉，高级模式支持任意模型名与超时秒数手动输入。
          </div>
          <button className="btn-secondary" onClick={() => setTaAdvancedMode((x) => !x)}>
            {taAdvancedMode ? "切换到标准模式" : "切换到高级模式"}
          </button>
        </div>
        {String(taDraft.provider || "").toLowerCase() === "deepseek" ? (
          <div className="text-xs text-amber-200/90">
            标准模式：Deep 模型下拉为「思考」、Quick 为「非思考」；后端会按 DeepSeek 文档为请求附加{" "}
            <code className="text-amber-100">extra_body.thinking</code>（Deep 开、Quick 关）。Deep 仍走工具链，若出现{" "}
            <code className="text-amber-100">reasoning_content</code> 相关 400，可在高级模式换模型，或设环境变量{" "}
            <code className="text-amber-100">TRADINGAGENTS_DEEPSEEK_THINKING_EXTRA_BODY=false</code> 关闭该注入以排查。
          </div>
        ) : null}
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          <label className="text-xs text-slate-300">
            TRADINGAGENTS_ENABLED
            <select className="input-base mt-1" value={taDraft.enabled} onChange={(e) => setForm((s) => ({ ...s, tradingagents_enabled: e.target.value }))}>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          </label>
          <label className="text-xs text-slate-300">
            TRADINGAGENTS_TIMEOUT_SECONDS
            {taAdvancedMode ? (
              <input
                className="input-base mt-1"
                type="number"
                min={1}
                step={1}
                placeholder={`当前: ${status?.values.tradingagents_timeout_seconds || "180"}`}
                value={taDraft.timeoutSeconds}
                onChange={(e) => setForm((s) => ({ ...s, tradingagents_timeout_seconds: e.target.value }))}
              />
            ) : (
              <select className="input-base mt-1" value={taDraft.timeoutSeconds} onChange={(e) => setForm((s) => ({ ...s, tradingagents_timeout_seconds: e.target.value }))}>
                <option value="60">60</option><option value="90">90</option><option value="120">120</option><option value="180">180</option><option value="240">240</option><option value="300">300</option>
              </select>
            )}
          </label>
          <label className="text-xs text-slate-300">
            TRADINGAGENTS_MAX_SYMBOLS
            <select className="input-base mt-1" value={taDraft.maxSymbols} onChange={(e) => setForm((s) => ({ ...s, tradingagents_max_symbols: e.target.value }))}>
              <option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="5">5</option><option value="8">8</option><option value="10">10</option>
            </select>
          </label>
          <label className="text-xs text-slate-300">
            TRADINGAGENTS_LLM_PROVIDER
            <select
              className="input-base mt-1"
              value={taDraft.provider}
              onChange={(e) => {
                const v = e.target.value;
                setForm((s) => {
                  const next = { ...s, tradingagents_llm_provider: v };
                  if (String(v).toLowerCase() === "deepseek" && !taAdvancedMode) {
                    next.tradingagents_deep_model = "deepseek-v4-pro";
                    next.tradingagents_quick_model = "deepseek-v4-flash";
                  }
                  return next;
                });
              }}
            >
              <option value="openai">openai</option><option value="anthropic">anthropic</option><option value="google">google</option><option value="xai">xai</option><option value="deepseek">deepseek</option><option value="qwen">qwen</option><option value="glm">glm</option><option value="openrouter">openrouter</option><option value="ollama">ollama</option><option value="azure">azure</option>
            </select>
          </label>
          <label className="text-xs text-slate-300">
            TRADINGAGENTS_DEEP_MODEL
            {taAdvancedMode ? (
              <input className="input-base mt-1" placeholder={`当前: ${status?.values.tradingagents_deep_model || "gpt-5.4"}`} value={taDraft.deepModel} onChange={(e) => setForm((s) => ({ ...s, tradingagents_deep_model: e.target.value }))} />
            ) : String(taDraft.provider || "").toLowerCase() === "deepseek" ? (
              <select className="input-base mt-1" value={taDraft.deepModel} onChange={(e) => setForm((s) => ({ ...s, tradingagents_deep_model: e.target.value }))}>
                <option value="deepseek-v4-flash">deepseek-v4-flash（思考）</option>
                <option value="deepseek-v4-pro">deepseek-v4-pro（思考）</option>
              </select>
            ) : (
              <select className="input-base mt-1" value={taDraft.deepModel} onChange={(e) => setForm((s) => ({ ...s, tradingagents_deep_model: e.target.value }))}>
                <option value="gpt-5.4">gpt-5.4</option><option value="gpt-5.4-mini">gpt-5.4-mini</option><option value="gpt-4.1">gpt-4.1</option><option value="o4-mini">o4-mini</option><option value="claude-3-7-sonnet-latest">claude-3-7-sonnet-latest</option><option value="claude-3-5-haiku-latest">claude-3-5-haiku-latest</option><option value="gemini-2.5-pro">gemini-2.5-pro</option><option value="gemini-2.5-flash">gemini-2.5-flash</option><option value="deepseek-chat">deepseek-chat（推荐）</option><option value="deepseek-reasoner">deepseek-reasoner（多轮易 400）</option><option value="qwen-max">qwen-max</option><option value="glm-4.5">glm-4.5</option><option value="grok-3-mini">grok-3-mini</option>
              </select>
            )}
          </label>
          <label className="text-xs text-slate-300">
            TRADINGAGENTS_QUICK_MODEL
            {taAdvancedMode ? (
              <input className="input-base mt-1" placeholder={`当前: ${status?.values.tradingagents_quick_model || "gpt-5.4-mini"}`} value={taDraft.quickModel} onChange={(e) => setForm((s) => ({ ...s, tradingagents_quick_model: e.target.value }))} />
            ) : String(taDraft.provider || "").toLowerCase() === "deepseek" ? (
              <select className="input-base mt-1" value={taDraft.quickModel} onChange={(e) => setForm((s) => ({ ...s, tradingagents_quick_model: e.target.value }))}>
                <option value="deepseek-v4-flash">deepseek-v4-flash（非思考）</option>
                <option value="deepseek-v4-pro">deepseek-v4-pro（非思考）</option>
              </select>
            ) : (
              <select className="input-base mt-1" value={taDraft.quickModel} onChange={(e) => setForm((s) => ({ ...s, tradingagents_quick_model: e.target.value }))}>
                <option value="gpt-5.4-mini">gpt-5.4-mini</option><option value="gpt-4.1-mini">gpt-4.1-mini</option><option value="o4-mini">o4-mini</option><option value="claude-3-5-haiku-latest">claude-3-5-haiku-latest</option><option value="gemini-2.5-flash">gemini-2.5-flash</option><option value="deepseek-chat">deepseek-chat</option><option value="qwen-plus">qwen-plus</option><option value="glm-4-air">glm-4-air</option><option value="grok-3-mini">grok-3-mini</option>
              </select>
            )}
          </label>
          <label className="text-xs text-slate-300">
            TRADINGAGENTS_OUTPUT_LANGUAGE
            <select className="input-base mt-1" value={taDraft.outputLanguage} onChange={(e) => setForm((s) => ({ ...s, tradingagents_output_language: e.target.value }))}>
              <option value="Chinese">Chinese</option><option value="English">English</option><option value="Japanese">Japanese</option>
            </select>
          </label>
          <label className="text-xs text-slate-300">
            TRADINGAGENTS_MAX_DEBATE_ROUNDS
            <select className="input-base mt-1" value={taDraft.maxDebateRounds} onChange={(e) => setForm((s) => ({ ...s, tradingagents_max_debate_rounds: e.target.value }))}>
              <option value="0">0</option><option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4</option><option value="5">5</option>
            </select>
          </label>
          <label className="text-xs text-slate-300">
            TRADINGAGENTS_MAX_RISK_DISCUSS_ROUNDS
            <select className="input-base mt-1" value={taDraft.maxRiskDiscussRounds} onChange={(e) => setForm((s) => ({ ...s, tradingagents_max_risk_discuss_rounds: e.target.value }))}>
              <option value="0">0</option><option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4</option><option value="5">5</option>
            </select>
          </label>
          <label className="text-xs text-slate-300">
            TRADINGAGENTS_CHECKPOINT_ENABLED
            <select className="input-base mt-1" value={taDraft.checkpointEnabled} onChange={(e) => setForm((s) => ({ ...s, tradingagents_checkpoint_enabled: e.target.value }))}>
              <option value="true">true</option><option value="false">false</option>
            </select>
          </label>
          <label className="text-xs text-slate-300">
            TRADINGAGENTS_DATA_SOURCE
            <select className="input-base mt-1" value={taDraft.dataSource} onChange={(e) => setForm((s) => ({ ...s, tradingagents_data_source: e.target.value }))}>
              <option value="auto">auto</option><option value="public">public</option><option value="yfinance">yfinance</option><option value="longbridge">longbridge</option>
            </select>
          </label>
          <label className="text-xs text-slate-300">
            TRADINGAGENTS_PUBLIC_MARKET_SOURCE
            <select className="input-base mt-1" value={taDraft.publicMarketSource} onChange={(e) => setForm((s) => ({ ...s, tradingagents_public_market_source: e.target.value }))}>
              <option value="auto">auto</option><option value="mootdx">mootdx</option><option value="eastmoney">eastmoney</option><option value="akshare">akshare</option><option value="cn_local_cache">cn_local_cache</option><option value="yahoo">yahoo</option><option value="stooq">stooq</option>
            </select>
          </label>
          <label className="text-xs text-slate-300">
            TRADINGAGENTS_SCORE_WEIGHT
            <select className="input-base mt-1" value={taDraft.scoreWeight} onChange={(e) => setForm((s) => ({ ...s, tradingagents_score_weight: e.target.value }))}>
              <option value="0">0</option><option value="0.1">0.1</option><option value="0.15">0.15</option><option value="0.2">0.2</option><option value="0.25">0.25</option><option value="0.3">0.3</option><option value="0.4">0.4</option><option value="0.5">0.5</option><option value="0.6">0.6</option>
            </select>
          </label>
        </div>
        <div className="field-label mt-2">LLM API Key（单输入，自动按 Provider 写入）</div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <label className="text-xs text-slate-300">
            LLM_API_KEY（将写入: {llmProviderEnvKey}）
            <input
              className="input-base mt-1"
              type="password"
              placeholder={`当前: ${llmProviderMaskedCurrent || "未配置"}`}
              value={form.llm_api_key}
              onChange={(e) => setForm((s) => ({ ...s, llm_api_key: e.target.value }))}
            />
          </label>
          {String(taDraft.provider || "").toLowerCase() === "azure" ? (
            <label className="text-xs text-slate-300">
              AZURE_OPENAI_ENDPOINT
              <input
                className="input-base mt-1"
                placeholder={`当前: ${status?.values.azure_openai_endpoint || "未配置"}`}
                value={form.azure_openai_endpoint}
                onChange={(e) => setForm((s) => ({ ...s, azure_openai_endpoint: e.target.value }))}
              />
            </label>
          ) : (
            <div className="text-xs text-slate-400 self-end">已按 Provider 自动匹配 Key 变量。</div>
          )}
        </div>
        <div className="text-xs text-slate-400">提示：仅填写这一个 Key 输入框即可；保存时后端会根据当前 Provider 自动写入对应的 API Key 环境变量。</div>
      </div>

        </>
      ) : null}

      {activeSection === "fees" ? (
        <>
      <div className="panel space-y-3">
        <div className="field-label">交易费用模型（港股/美股/美股期权）</div>
        <div className="text-xs text-slate-400">
          每个券商一份费用快照，写入 <code className="text-slate-300">config/fee_schedule.json</code>。<strong>默认账户已连接</strong>
          时，试算/回测自动使用该账户的 <code className="text-slate-300">broker_provider</code> 对应模板；<strong>未连接</strong>
          时使用下方「未连接时」保存的模板。
        </div>
        <div className="rounded border border-cyan-900/40 bg-slate-950/40 px-3 py-2 text-xs text-slate-300">
          <div>
            当前试算/回测模板：
            <span className="text-cyan-200 font-medium">
              {feeBrokers.find((b) => b.broker_id === feeEffectiveBrokerId)?.display_name || feeEffectiveBrokerId || "—"}
            </span>
            <span className="text-slate-500 ml-1">({feeEffectiveBrokerId || "—"})</span>
          </div>
          <div className="mt-1 text-slate-400">
            {feeSource === "account"
              ? "规则：默认交易账户已连接（行情+交易就绪），费用表与账户券商一致。"
              : feeSource === "manual_fallback_no_template"
                ? "规则：账户已连接，但费用库中没有与账户券商同名的模板，暂用「未连接时」所选模板；请在模板列表中新增同名券商或调整账户 broker_provider。"
                : feeSource === "manual"
                  ? "规则：默认账户未连接，使用「未连接时」所选费用模板。"
                  : "规则：根据账户连接状态自动切换（详见后端 fee_resolution）。"}
          </div>
        </div>
        <div className="rounded border border-slate-700/70 p-3 space-y-3">
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
            <label className="text-xs text-slate-300">
              正在编辑的费用模板
              <select
                className="input-base mt-1"
                value={feeBrokers.some((b) => b.broker_id === feeEditingBrokerId) ? feeEditingBrokerId : ""}
                disabled={savingFees || !feeBrokers.length}
                onChange={async (e) => {
                  const bid = e.target.value;
                  setFeeEditingBrokerId(bid);
                  try {
                    await loadFeeScheduleForEditor(bid);
                    setErr("");
                  } catch (err: any) {
                    setErr(String(err?.message || err));
                  }
                }}
              >
                {!feeBrokers.length ? (
                  <option value="">暂无模板，请先添加券商</option>
                ) : null}
                {feeBrokers.map((b) => (
                  <option key={b.broker_id} value={b.broker_id}>
                    {b.display_name} ({b.broker_id})
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs text-slate-300 md:col-span-2">
              未连接默认账户时使用
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <select
                  className="input-base flex-1 min-w-[10rem]"
                  value={feeBrokers.some((b) => b.broker_id === feeManualTemplateId) ? feeManualTemplateId : ""}
                  disabled={savingFees || !feeBrokers.length}
                  onChange={(e) => setFeeManualTemplateId(e.target.value)}
                >
                {!feeBrokers.length ? (
                  <option value="">暂无模板</option>
                ) : null}
                  {feeBrokers.map((b) => (
                    <option key={b.broker_id} value={b.broker_id}>
                      {b.display_name} ({b.broker_id})
                    </option>
                  ))}
                </select>
                <button type="button" className="btn-secondary shrink-0" disabled={savingFees || !feeManualTemplateId} onClick={() => void saveManualFeeTemplate()}>
                  保存兜底模板
                </button>
              </div>
              <div className="mt-0.5 text-[11px] text-slate-500">连接账户后仍以账户券商为准；断开后再用此处选择。</div>
            </label>
          </div>
          <div className="flex flex-wrap items-end gap-2 md:gap-3">
            <label className="text-xs text-slate-300 flex-1 min-w-[12rem]">
              当前券商显示名称
              <input
                className="input-base mt-1"
                value={feeDisplayNameDraft}
                onChange={(e) => setFeeDisplayNameDraft(e.target.value)}
                placeholder="下拉框等处展示用"
                disabled={savingFees || !feeEditingBrokerId}
              />
            </label>
            <button
              type="button"
              className="btn-secondary"
              disabled={savingFees || !feeEditingBrokerId}
              onClick={() => void updateFeeBrokerDisplayName()}
            >
              更新显示名
            </button>
            <button
              type="button"
              className="btn-secondary border-red-900/50 text-red-200 hover:bg-red-950/40"
              disabled={savingFees || !feeEditingBrokerId || feeBrokers.length <= 1}
              onClick={() => void deleteFeeBrokerProfile()}
            >
              删除当前券商
            </button>
          </div>
          <div className="border-t border-slate-700/60 pt-3 grid grid-cols-1 gap-2 md:grid-cols-4">
            <label className="text-xs text-slate-300">
              新券商 ID
              <input
                className="input-base mt-1"
                placeholder="如 tiger、futu（英文开头）"
                value={newFeeBrokerId}
                onChange={(e) => setNewFeeBrokerId(e.target.value)}
                autoComplete="off"
              />
              <div className="mt-0.5 text-[11px] text-slate-500">
                填写后「显示名称」会自动同步；你可再改成中文名。系统默认已有 <code className="text-slate-400">longbridge</code>{" "}
                模板，若提示「已存在」请直接看上方面下拉框，无需重复添加。
              </div>
            </label>
            <label className="text-xs text-slate-300">
              显示名称
              <input
                className="input-base mt-1"
                placeholder="默认同 ID，可改为中文名"
                value={newFeeBrokerName}
                onChange={(e) => setNewFeeBrokerName(e.target.value)}
                autoComplete="off"
              />
            </label>
            <label className="text-xs text-slate-300">
              复制自
              <select
                className="input-base mt-1"
                value={newFeeBrokerCopyFrom}
                onChange={(e) => setNewFeeBrokerCopyFrom(e.target.value)}
              >
                <option value="">系统默认模板</option>
                {feeBrokers.map((b) => (
                  <option key={b.broker_id} value={b.broker_id}>
                    {b.display_name}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex items-end">
              <button
                type="button"
                className="btn-secondary w-full"
                disabled={savingFees}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  void addFeeBrokerProfile();
                }}
              >
                {savingFees ? "处理中..." : "添加券商"}
              </button>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-secondary" onClick={() => setFeeAdvancedMode((x) => !x)}>
            {feeAdvancedMode ? "切换到表单模式" : "切换到高级JSON模式"}
          </button>
          <button className="btn-secondary" onClick={resetFeeScheduleToDefault} disabled={savingFees}>
            恢复默认费率
          </button>
        </div>
        {!feeAdvancedMode ? (
          <div className="space-y-4">
            <div className="rounded border border-slate-700/70 p-3">
              <div className="mb-2 text-sm text-slate-200">港股（股票）</div>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
                <label className="text-xs text-slate-300">佣金启用
                  <select className="input-base mt-1" value={feeForm.hk_commission_enabled ? "1" : "0"} onChange={(e) => setFeeForm((s) => ({ ...s, hk_commission_enabled: e.target.value === "1" }))}>
                    <option value="0">否（免佣）</option><option value="1">是</option>
                  </select>
                </label>
                <label className="text-xs text-slate-300">佣金费率(%)
                  <input className="input-base mt-1" type="number" step="0.0001" value={feeForm.hk_commission_rate_pct} onChange={(e) => setFeeForm((s) => ({ ...s, hk_commission_rate_pct: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">佣金最低(HKD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.hk_commission_min} onChange={(e) => setFeeForm((s) => ({ ...s, hk_commission_min: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">平台费(HKD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.hk_platform_fee} onChange={(e) => setFeeForm((s) => ({ ...s, hk_platform_fee: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">印花税(%)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.hk_stamp_duty_pct} onChange={(e) => setFeeForm((s) => ({ ...s, hk_stamp_duty_pct: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">交易费(%)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.hk_trading_fee_pct} onChange={(e) => setFeeForm((s) => ({ ...s, hk_trading_fee_pct: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">交易征费(%)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.hk_sfc_levy_pct} onChange={(e) => setFeeForm((s) => ({ ...s, hk_sfc_levy_pct: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">会财局征费(%)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.hk_afrc_levy_pct} onChange={(e) => setFeeForm((s) => ({ ...s, hk_afrc_levy_pct: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">交收费(%)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.hk_ccass_fee_pct} onChange={(e) => setFeeForm((s) => ({ ...s, hk_ccass_fee_pct: feeNum(e.target.value) }))} />
                </label>
              </div>
            </div>
            <div className="rounded border border-slate-700/70 p-3">
              <div className="mb-2 text-sm text-slate-200">美股（股票）</div>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
                <label className="text-xs text-slate-300">平台费(USD/股)
                  <input className="input-base mt-1" type="number" step="0.000001" value={feeForm.us_platform_per_share} onChange={(e) => setFeeForm((s) => ({ ...s, us_platform_per_share: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">平台费最低(USD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_platform_min} onChange={(e) => setFeeForm((s) => ({ ...s, us_platform_min: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">平台费最高(%成交额)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_platform_max_pct_notional} onChange={(e) => setFeeForm((s) => ({ ...s, us_platform_max_pct_notional: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">交收费(USD/股)
                  <input className="input-base mt-1" type="number" step="0.000001" value={feeForm.us_settlement_per_share} onChange={(e) => setFeeForm((s) => ({ ...s, us_settlement_per_share: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">交收费最高(%成交额)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_settlement_max_pct_notional} onChange={(e) => setFeeForm((s) => ({ ...s, us_settlement_max_pct_notional: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">TAF(USD/股,卖出)
                  <input className="input-base mt-1" type="number" step="0.000001" value={feeForm.us_taf_per_share} onChange={(e) => setFeeForm((s) => ({ ...s, us_taf_per_share: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">TAF最低(USD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_taf_min} onChange={(e) => setFeeForm((s) => ({ ...s, us_taf_min: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">TAF最高(USD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_taf_max} onChange={(e) => setFeeForm((s) => ({ ...s, us_taf_max: feeNum(e.target.value) }))} />
                </label>
              </div>
            </div>
            <div className="rounded border border-slate-700/70 p-3">
              <div className="mb-2 text-sm text-slate-200">美股期权（普通订单）</div>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
                <label className="text-xs text-slate-300">佣金(USD/张)
                  <input className="input-base mt-1" type="number" step="0.0001" value={feeForm.us_option_commission_per_contract} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_commission_per_contract: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">佣金最低(USD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_option_commission_min} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_commission_min: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">平台费(USD/张)
                  <input className="input-base mt-1" type="number" step="0.0001" value={feeForm.us_option_platform_per_contract} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_platform_per_contract: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">平台费最低(USD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_option_platform_min} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_platform_min: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">期权交收费(USD/张)
                  <input className="input-base mt-1" type="number" step="0.0001" value={feeForm.us_option_settlement_per_contract} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_settlement_per_contract: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">期权监管费(USD/张)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.us_option_regulatory_per_contract} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_regulatory_per_contract: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">期权清算费(USD/张)
                  <input className="input-base mt-1" type="number" step="0.0001" value={feeForm.us_option_clearing_per_contract} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_clearing_per_contract: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">期权TAF(USD/张,卖出)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.us_option_taf_per_contract} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_taf_per_contract: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">期权TAF最低(USD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_option_taf_min} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_taf_min: feeNum(e.target.value) }))} />
                </label>
              </div>
            </div>
          </div>
        ) : (
          <textarea
            className="input-base min-h-[280px] w-full font-mono text-xs"
            value={feeScheduleText}
            onChange={(e) => setFeeScheduleText(e.target.value)}
          />
        )}
        <div className="flex gap-2">
          <button className="btn-secondary" onClick={load}>从后端重新加载</button>
          <button className="btn-primary" onClick={saveFeeSchedule} disabled={savingFees}>
            {savingFees ? "保存中..." : "保存费用模型"}
          </button>
        </div>
        <div className="rounded border border-slate-700/70 p-3">
          <div className="mb-2 text-sm text-slate-200">费用试算</div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-5">
            <select className="input-base" value={feeEstimateForm.asset_class} onChange={(e) => setFeeEstimateForm((s) => ({ ...s, asset_class: e.target.value as "stock" | "us_option" }))}>
              <option value="stock">股票</option>
              <option value="us_option">美股期权</option>
            </select>
            <select className="input-base" value={feeEstimateForm.market} onChange={(e) => setFeeEstimateForm((s) => ({ ...s, market: e.target.value as "HK" | "US" | "CN" | "OTHER" }))} disabled={feeEstimateForm.asset_class !== "stock"}>
              <option value="US">US</option><option value="HK">HK</option><option value="CN">CN</option><option value="OTHER">OTHER</option>
            </select>
            <select className="input-base" value={feeEstimateForm.side} onChange={(e) => setFeeEstimateForm((s) => ({ ...s, side: e.target.value as "buy" | "sell" }))}>
              <option value="buy">买入</option><option value="sell">卖出</option>
            </select>
            <input className="input-base" type="number" value={feeEstimateForm.quantity} onChange={(e) => setFeeEstimateForm((s) => ({ ...s, quantity: Number(e.target.value) }))} placeholder="数量/张数" />
            <input className="input-base" type="number" step="0.0001" value={feeEstimateForm.price} onChange={(e) => setFeeEstimateForm((s) => ({ ...s, price: Number(e.target.value) }))} placeholder="价格" disabled={feeEstimateForm.asset_class !== "stock"} />
          </div>
          <div className="mt-2 flex gap-2">
            <button className="btn-secondary" onClick={runFeeEstimate}>立即试算</button>
          </div>
          {feeEstimate ? (
            <div className="mt-2 space-y-2">
              <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
                <div className="rounded border border-slate-700/70 bg-slate-950/50 p-2 text-xs text-slate-300">
                  <div className="text-slate-400">总费用</div>
                  <div className="mt-1 text-sm text-emerald-300">{fmtNum(feeEstimate?.estimate?.total_fee, 6)}</div>
                </div>
                <div className="rounded border border-slate-700/70 bg-slate-950/50 p-2 text-xs text-slate-300">
                  <div className="text-slate-400">成交额</div>
                  <div className="mt-1 text-sm">{feeEstimate?.estimate?.notional !== undefined ? fmtNum(feeEstimate?.estimate?.notional, 4) : "-"}</div>
                </div>
                <div className="rounded border border-slate-700/70 bg-slate-950/50 p-2 text-xs text-slate-300">
                  <div className="text-slate-400">费用占成交额</div>
                  <div className="mt-1 text-sm">
                    {Number(feeEstimate?.estimate?.notional || 0) > 0
                      ? `${((Number(feeEstimate?.estimate?.total_fee || 0) / Number(feeEstimate?.estimate?.notional || 1)) * 100).toFixed(4)}%`
                      : "-"}
                  </div>
                </div>
                <div className="rounded border border-slate-700/70 bg-slate-950/50 p-2 text-xs text-slate-300">
                  <div className="text-slate-400">资产类型</div>
                  <div className="mt-1 text-sm">{feeEstimate?.asset_class || "-"}</div>
                </div>
              </div>
              <div className="table-shell">
                <table className="min-w-full text-xs">
                  <thead className="table-head">
                    <tr className="text-left">
                      <th className="px-3 py-2">费用项</th>
                      <th className="px-3 py-2">金额</th>
                      <th className="px-3 py-2">占总费用%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(feeEstimate?.estimate?.components || {}).map(([k, v]: [string, any]) => {
                      const total = Number(feeEstimate?.estimate?.total_fee || 0);
                      const amt = Number(v || 0);
                      return (
                        <tr key={k} className="border-t border-slate-800/90">
                          <td className="px-3 py-2 text-slate-300">{k}</td>
                          <td className="px-3 py-2 text-slate-200">{fmtNum(amt, 6)}</td>
                          <td className="px-3 py-2 text-slate-400">{total > 0 ? `${(amt / total * 100).toFixed(2)}%` : "-"}</td>
                        </tr>
                      );
                    })}
                    {feeEstimate?.estimate?.stamp_duty ? (
                      <tr className="border-t border-slate-800/90">
                        <td className="px-3 py-2 text-slate-300">stamp_duty</td>
                        <td className="px-3 py-2 text-slate-200">{fmtNum(feeEstimate?.estimate?.stamp_duty, 6)}</td>
                        <td className="px-3 py-2 text-slate-400">
                          {Number(feeEstimate?.estimate?.total_fee || 0) > 0
                            ? `${(Number(feeEstimate?.estimate?.stamp_duty || 0) / Number(feeEstimate?.estimate?.total_fee || 1) * 100).toFixed(2)}%`
                            : "-"}
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
        </div>
      </div>

        </>
      ) : null}

      {activeSection === "risk" ? (
        <>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(340px,0.9fr)]">
        <div className="panel space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="field-label">风控规则</div>
              <p className="mt-1 text-sm text-slate-400">保存后作为自动交易的安全边界；保存参数不会启动飞书或自动交易。</p>
            </div>
            <span className={`rounded-full border px-3 py-1 text-sm font-semibold ${
              risk?.enabled
                ? "border-emerald-400/40 bg-emerald-500/10 text-emerald-200"
                : "border-amber-400/40 bg-amber-500/10 text-amber-200"
            }`}>
              {risk?.enabled ? "风控已启用" : "风控关闭"}
            </span>
          </div>
          {risk ? (
            <>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">单笔最大金额</span>
                  <input className="input-base" type="number" value={risk.max_order_amount} onChange={(e) => setRisk({ ...risk, max_order_amount: Number(e.target.value) })} />
                  <span className="block text-[11px] text-slate-500">超过该金额的订单会被拦截。</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">单日最大亏损比例</span>
                  <input className="input-base" type="number" step="0.01" value={risk.max_daily_loss_pct} onChange={(e) => setRisk({ ...risk, max_daily_loss_pct: Number(e.target.value) })} />
                  <span className="block text-[11px] text-slate-500">例如 0.2 表示 20%。</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">单笔止损比例</span>
                  <input className="input-base" type="number" step="0.01" value={risk.stop_loss_pct} onChange={(e) => setRisk({ ...risk, stop_loss_pct: Number(e.target.value) })} />
                  <span className="block text-[11px] text-slate-500">用于自动交易的基础止损边界。</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">单仓最大占比</span>
                  <input className="input-base" type="number" step="0.01" value={risk.max_position_pct} onChange={(e) => setRisk({ ...risk, max_position_pct: Number(e.target.value) })} />
                  <span className="block text-[11px] text-slate-500">限制单个持仓占账户的比例。</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">账户现金底线</span>
                  <input className="input-base" type="number" step="0.01" value={risk.min_cash_ratio ?? 0.2} onChange={(e) => setRisk({ ...risk, min_cash_ratio: Number(e.target.value) })} />
                  <span className="block text-[11px] text-slate-500">例如 0.2 表示下单后保留 20% 现金/购买力。</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">账户总风险上限</span>
                  <input className="input-base" type="number" step="0.01" value={risk.max_total_risk_pct ?? 0.1} onChange={(e) => setRisk({ ...risk, max_total_risk_pct: Number(e.target.value) })} />
                  <span className="block text-[11px] text-slate-500">所有自动交易合计风险暴露上限。</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">股票单笔账户占比</span>
                  <input className="input-base" type="number" step="0.01" value={risk.max_stock_order_notional_pct ?? 0.03} onChange={(e) => setRisk({ ...risk, max_stock_order_notional_pct: Number(e.target.value) })} />
                  <span className="block text-[11px] text-slate-500">股票单笔名义金额占账户净值上限。</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">股票单标的账户占比</span>
                  <input className="input-base" type="number" step="0.01" value={risk.max_stock_position_pct ?? 0.1} onChange={(e) => setRisk({ ...risk, max_stock_position_pct: Number(e.target.value) })} />
                  <span className="block text-[11px] text-slate-500">股票买入后单标的市值占账户净值上限。</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">期权单笔最大亏损</span>
                  <input className="input-base" type="number" step="0.001" value={risk.max_option_order_loss_pct ?? 0.005} onChange={(e) => setRisk({ ...risk, max_option_order_loss_pct: Number(e.target.value) })} />
                  <span className="block text-[11px] text-slate-500">普通期权单笔最大可亏损占账户净值比例。</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">0DTE 单笔最大亏损</span>
                  <input className="input-base" type="number" step="0.001" value={risk.max_0dte_order_loss_pct ?? 0.002} onChange={(e) => setRisk({ ...risk, max_0dte_order_loss_pct: Number(e.target.value) })} />
                  <span className="block text-[11px] text-slate-500">0DTE 单笔最大可亏损占账户净值比例。</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">期权当日新增风险</span>
                  <input className="input-base" type="number" step="0.001" value={risk.max_option_daily_new_risk_pct ?? 0.015} onChange={(e) => setRisk({ ...risk, max_option_daily_new_risk_pct: Number(e.target.value) })} />
                  <span className="block text-[11px] text-slate-500">当天新开期权最大风险占账户净值比例。</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">期权总风险暴露</span>
                  <input className="input-base" type="number" step="0.001" value={risk.max_total_option_risk_pct ?? 0.05} onChange={(e) => setRisk({ ...risk, max_total_option_risk_pct: Number(e.target.value) })} />
                  <span className="block text-[11px] text-slate-500">全部未平期权风险占账户净值比例。</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">禁止裸卖期权</span>
                  <select className="input-base" value={(risk.block_naked_short_options ?? true) ? "1" : "0"} onChange={(e) => setRisk({ ...risk, block_naked_short_options: e.target.value === "1" })}>
                    <option value="1">禁止自动裸卖</option>
                    <option value="0">允许策略自行控制</option>
                  </select>
                  <span className="block text-[11px] text-slate-500">建议保持禁止，裸卖期权应单独人工审批。</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-slate-400">实盘缺数据时</span>
                  <select className="input-base" value={(risk.fail_closed_for_live ?? true) ? "1" : "0"} onChange={(e) => setRisk({ ...risk, fail_closed_for_live: e.target.value === "1" })}>
                    <option value="1">拦截下单</option>
                    <option value="0">仅记录告警</option>
                  </select>
                  <span className="block text-[11px] text-slate-500">账户净值或持仓不可用时的总风控行为。</span>
                </label>
                <label className="space-y-1 md:col-span-2 xl:col-span-1">
                  <span className="text-xs font-medium text-slate-400">风控总开关</span>
                  <select className="input-base" value={risk.enabled ? "1" : "0"} onChange={(e) => setRisk({ ...risk, enabled: e.target.value === "1" })}>
                    <option value="1">启用风控</option>
                    <option value="0">关闭风控</option>
                  </select>
                  <span className="block text-[11px] text-slate-500">建议先启用风控，再到自动交易页面启动具体 worker。</span>
                </label>
              </div>
              <div className={`rounded-xl border px-4 py-3 text-sm ${
                risk.enabled
                  ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-100"
                  : "border-amber-500/25 bg-amber-500/10 text-amber-100"
              }`}>
                {risk.enabled
                  ? "当前风控会参与自动交易下单前检查。"
                  : "当前风控未启用：自动交易仍可单独运行，但缺少总闸保护。"}
              </div>
              <button className="btn-secondary" onClick={saveRisk}>保存风控参数</button>
            </>
          ) : (
            <div className="text-sm text-slate-400">加载中...</div>
          )}
        </div>

        <div className="panel space-y-4">
          <div>
            <div className="field-label">服务状态与系统关闭</div>
            <p className="mt-1 text-sm text-slate-400">这里只显示服务状态和关闭系统入口；飞书与自动交易分别在各自页面管理。</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
            <div className="rounded-xl border border-slate-700/70 bg-slate-950/40 p-4">
              <div className="text-sm text-slate-400">Feishu Bot</div>
              <div className={`mt-1 text-xl font-semibold ${services?.feishu_bot_running ? "text-emerald-300" : "text-slate-200"}`}>
                {services?.feishu_bot_running ? "运行中" : "未运行"}
              </div>
              <div className="mt-1 text-xs text-slate-500">用于飞书通知、指令和收款确认。</div>
              <Link className="mt-3 inline-flex text-sm font-semibold text-cyan-200 hover:text-cyan-100" href="/notifications">
                去通知中心管理飞书
              </Link>
            </div>
            <div className="rounded-xl border border-slate-700/70 bg-slate-950/40 p-4">
              <div className="text-sm text-slate-400">Auto Trader</div>
              <div className={`mt-1 text-xl font-semibold ${services?.auto_trader_scheduler_running ? "text-emerald-300" : "text-slate-200"}`}>
                {services?.auto_trader_scheduler_running ? "运行中" : "未运行"}
              </div>
              <div className="mt-1 text-xs text-slate-500">用于自动扫描、生成信号和执行交易流程。</div>
              <Link className="mt-3 inline-flex text-sm font-semibold text-cyan-200 hover:text-cyan-100" href="/auto-trader">
                去自动交易页面管理
              </Link>
            </div>
          </div>
          <div className="rounded-xl border border-cyan-500/20 bg-cyan-500/10 px-4 py-3 text-sm text-cyan-100">
            推荐顺序：先保存并启用风控，再到通知中心启动飞书，到自动交易页面启动具体交易模块。
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-secondary border-rose-500/40 bg-rose-500/10 text-rose-100 hover:bg-rose-500/20" onClick={stopAllServices} disabled={stoppingAll}>
              {stoppingAll ? "关闭中..." : "关闭系统"}
            </button>
          </div>
          <p className="text-xs text-rose-200/80">关闭系统会停止前端和后端服务，当前页面会断开；不会删除任何已保存配置。</p>
        </div>
      </div>
        </>
      ) : null}
    </PageShell>
  );
}
