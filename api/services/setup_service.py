from __future__ import annotations

import os
from typing import Any, Callable


def build_setup_config_response(
    *,
    env_data: dict[str, Any],
    feishu_cfg: dict[str, Any],
    mask_secret: Callable[[str], str],
) -> dict[str, Any]:
    tiingo_val = env_data.get("TIINGO_API_KEY") or env_data.get("NEWS_API_KEY", "")
    broker_provider = str(env_data.get("BROKER_PROVIDER", "longbridge")).strip().lower() or "longbridge"
    default_account_id = str(env_data.get("DEFAULT_ACCOUNT_ID", "default")).strip() or "default"
    feishu_app_id = str(env_data.get("FEISHU_APP_ID", "") or "").strip()
    feishu_app_secret = str(env_data.get("FEISHU_APP_SECRET", "") or "").strip()
    feishu_scheduled_chat_id = str(env_data.get("FEISHU_SCHEDULED_CHAT_ID", "") or "").strip()
    if broker_provider == "longport":
        broker_provider = "longbridge"
    longbridge_ready = all(env_data.get(k) for k in ("LONGPORT_APP_KEY", "LONGPORT_APP_SECRET", "LONGPORT_ACCESS_TOKEN"))
    twelve_data_val = env_data.get("TWELVE_DATA_API_KEY") or env_data.get("TWELVEDATA_API_KEY", "")
    return {
        "configured": {
            "broker": longbridge_ready if broker_provider == "longbridge" else False,
            "longport": longbridge_ready,
            "longbridge": longbridge_ready,
            "feishu": bool(feishu_app_id and feishu_app_secret),
            "market_apis": any(
                env_data.get(k)
                for k in (
                    "FINNHUB_API_KEY",
                    "TIINGO_API_KEY",
                    "NEWS_API_KEY",
                    "POLYGON_API_KEY",
                    "TWELVE_DATA_API_KEY",
                    "TWELVEDATA_API_KEY",
                    "FRED_API_KEY",
                    "FMP_API_KEY",
                    "COINGECKO_API_KEY",
                )
            ),
            "openbb": str(env_data.get("OPENBB_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"},
            "cn_market_data": any(
                str(env_data.get(k, "")).strip()
                for k in (
                    "CN_MARKET_DATA_PROVIDER_ORDER",
                    "CN_MARKET_MOOTDX_ENABLED",
                    "CN_MARKET_TENCENT_ENABLED",
                    "CN_MARKET_AKSHARE_ENABLED",
                    "CN_MARKET_TUSHARE_ENABLED",
                    "CN_MARKET_BAOSTOCK_ENABLED",
                    "TUSHARE_TOKEN",
                )
            ),
            "tradingagents": str(env_data.get("TRADINGAGENTS_ENABLED", "false")).strip().lower()
            in {"1", "true", "yes", "on"},
        },
        "values": {
            "broker_provider": broker_provider,
            "default_account_id": default_account_id,
            "longport_app_key": mask_secret(env_data.get("LONGPORT_APP_KEY", "")),
            "longport_app_secret": mask_secret(env_data.get("LONGPORT_APP_SECRET", "")),
            "longport_access_token": mask_secret(env_data.get("LONGPORT_ACCESS_TOKEN", "")),
            "feishu_app_id": mask_secret(feishu_app_id),
            "feishu_app_secret": mask_secret(feishu_app_secret),
            "feishu_scheduled_chat_id": feishu_scheduled_chat_id,
            "finnhub_api_key": mask_secret(env_data.get("FINNHUB_API_KEY", "")),
            "tiingo_api_key": mask_secret(tiingo_val),
            "polygon_api_key": mask_secret(env_data.get("POLYGON_API_KEY", "")),
            "twelve_data_api_key": mask_secret(twelve_data_val),
            "fred_api_key": mask_secret(env_data.get("FRED_API_KEY", "")),
            "fmp_api_key": mask_secret(env_data.get("FMP_API_KEY", "")),
            "coingecko_api_key": mask_secret(env_data.get("COINGECKO_API_KEY", "")),
            "openclaw_mcp_max_level": env_data.get("OPENCLAW_MCP_MAX_LEVEL", "L2"),
            "openclaw_mcp_allow_l3": env_data.get("OPENCLAW_MCP_ALLOW_L3", "false"),
            "openclaw_mcp_l3_confirmation_token": mask_secret(env_data.get("OPENCLAW_MCP_L3_CONFIRMATION_TOKEN", "")),
            "openbb_enabled": env_data.get("OPENBB_ENABLED", "false"),
            "openbb_base_url": env_data.get("OPENBB_BASE_URL", "http://127.0.0.1:6900"),
            "openbb_timeout_seconds": env_data.get("OPENBB_TIMEOUT_SECONDS", "8"),
            "openbb_auto_start": env_data.get("OPENBB_AUTO_START", "1"),
            "cn_market_data_provider_order": env_data.get(
                "CN_MARKET_DATA_PROVIDER_ORDER", "mootdx,local_cache,akshare,tushare,baostock"
            ),
            "cn_market_mootdx_enabled": env_data.get("CN_MARKET_MOOTDX_ENABLED", "true"),
            "cn_market_tencent_enabled": env_data.get("CN_MARKET_TENCENT_ENABLED", "true"),
            "cn_market_akshare_enabled": env_data.get("CN_MARKET_AKSHARE_ENABLED", "true"),
            "cn_market_tushare_enabled": env_data.get("CN_MARKET_TUSHARE_ENABLED", "true"),
            "cn_market_baostock_enabled": env_data.get("CN_MARKET_BAOSTOCK_ENABLED", "true"),
            "tushare_token": mask_secret(env_data.get("TUSHARE_TOKEN", "")),
            "tradingagents_enabled": env_data.get("TRADINGAGENTS_ENABLED", "false"),
            "tradingagents_timeout_seconds": env_data.get("TRADINGAGENTS_TIMEOUT_SECONDS", "25"),
            "tradingagents_max_symbols": env_data.get("TRADINGAGENTS_MAX_SYMBOLS", "3"),
            "tradingagents_llm_provider": env_data.get("TRADINGAGENTS_LLM_PROVIDER", "openai"),
            "tradingagents_deep_model": env_data.get("TRADINGAGENTS_DEEP_MODEL", "gpt-5.4"),
            "tradingagents_quick_model": env_data.get("TRADINGAGENTS_QUICK_MODEL", "gpt-5.4-mini"),
            "tradingagents_output_language": env_data.get("TRADINGAGENTS_OUTPUT_LANGUAGE", "Chinese"),
            "tradingagents_max_debate_rounds": env_data.get("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "1"),
            "tradingagents_max_risk_discuss_rounds": env_data.get("TRADINGAGENTS_MAX_RISK_DISCUSS_ROUNDS", "1"),
            "tradingagents_checkpoint_enabled": env_data.get("TRADINGAGENTS_CHECKPOINT_ENABLED", "false"),
            "tradingagents_data_source": env_data.get("TRADINGAGENTS_DATA_SOURCE", "auto"),
            "tradingagents_public_market_source": env_data.get("TRADINGAGENTS_PUBLIC_MARKET_SOURCE", "auto"),
            "tradingagents_score_weight": env_data.get("TRADINGAGENTS_SCORE_WEIGHT", "0.25"),
            "openai_api_key": mask_secret(env_data.get("OPENAI_API_KEY", "")),
            "anthropic_api_key": mask_secret(env_data.get("ANTHROPIC_API_KEY", "")),
            "google_api_key": mask_secret(env_data.get("GOOGLE_API_KEY", "")),
            "xai_api_key": mask_secret(env_data.get("XAI_API_KEY", "")),
            "deepseek_api_key": mask_secret(env_data.get("DEEPSEEK_API_KEY", "")),
            "openrouter_api_key": mask_secret(env_data.get("OPENROUTER_API_KEY", "")),
            "dashscope_api_key": mask_secret(env_data.get("DASHSCOPE_API_KEY", "")),
            "zhipuai_api_key": mask_secret(env_data.get("ZHIPUAI_API_KEY", "")),
            "azure_openai_api_key": mask_secret(env_data.get("AZURE_OPENAI_API_KEY", "")),
            "azure_openai_endpoint": env_data.get("AZURE_OPENAI_ENDPOINT", ""),
        },
    }


def apply_setup_env_updates(
    *,
    payload: dict[str, Any],
    env_data: dict[str, str],
    env_var_map: dict[str, str],
) -> list[str]:
    changed: list[str] = []
    for field, env_key in env_var_map.items():
        val = payload.get(field)
        if val is None:
            continue
        clean = str(val).strip()
        if clean == "":
            # Ignore empty updates to avoid accidental secret wipe.
            continue
        env_data[env_key] = clean
        os.environ[env_key] = clean
        changed.append(env_key)
    return changed
