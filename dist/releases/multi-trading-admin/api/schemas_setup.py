from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SetupConfigBody(BaseModel):
    broker_provider: Optional[str] = None
    default_account_id: Optional[str] = None
    longport_app_key: Optional[str] = None
    longport_app_secret: Optional[str] = None
    longport_access_token: Optional[str] = None
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_scheduled_chat_id: Optional[str] = None
    finnhub_api_key: Optional[str] = None
    tiingo_api_key: Optional[str] = None
    polygon_api_key: Optional[str] = None
    twelve_data_api_key: Optional[str] = None
    fred_api_key: Optional[str] = None
    coingecko_api_key: Optional[str] = None
    openclaw_mcp_max_level: Optional[str] = None
    openclaw_mcp_allow_l3: Optional[str] = None
    openclaw_mcp_l3_confirmation_token: Optional[str] = None
    openbb_enabled: Optional[str] = None
    openbb_base_url: Optional[str] = None
    openbb_timeout_seconds: Optional[str] = None
    openbb_auto_start: Optional[str] = None
    cn_market_data_provider_order: Optional[str] = None
    cn_market_mootdx_enabled: Optional[str] = None
    cn_market_tencent_enabled: Optional[str] = None
    cn_market_akshare_enabled: Optional[str] = None
    cn_market_tushare_enabled: Optional[str] = None
    cn_market_baostock_enabled: Optional[str] = None
    tushare_token: Optional[str] = None
    tradingagents_enabled: Optional[str] = None
    tradingagents_timeout_seconds: Optional[str] = None
    tradingagents_max_symbols: Optional[str] = None
    tradingagents_llm_provider: Optional[str] = None
    tradingagents_deep_model: Optional[str] = None
    tradingagents_quick_model: Optional[str] = None
    tradingagents_output_language: Optional[str] = None
    tradingagents_max_debate_rounds: Optional[str] = None
    tradingagents_max_risk_discuss_rounds: Optional[str] = None
    tradingagents_checkpoint_enabled: Optional[str] = None
    tradingagents_data_source: Optional[str] = None
    tradingagents_public_market_source: Optional[str] = None
    tradingagents_score_weight: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    xai_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    dashscope_api_key: Optional[str] = None
    zhipuai_api_key: Optional[str] = None
    azure_openai_api_key: Optional[str] = None
    azure_openai_endpoint: Optional[str] = None
    llm_api_key: Optional[str] = None


class SetupCnMarketDataInstallBody(BaseModel):
    provider: Optional[str] = None
    packages: Optional[list[str]] = None


class SetupAccountRegisterBody(BaseModel):
    account_id: str
    broker_provider: str = "longbridge"
    credentials: Optional[dict[str, Any]] = None
    tiger_id: Optional[str] = None
    tiger_account: Optional[str] = None
    tiger_license: Optional[str] = None
    tiger_env: Optional[str] = None
    tiger_private_key: Optional[str] = None
    tiger_private_key_path: Optional[str] = None
    tiger_props_path: Optional[str] = None
    tiger_secret_key: Optional[str] = None
    tiger_token_path: Optional[str] = None
    longport_app_key: Optional[str] = None
    longport_app_secret: Optional[str] = None
    longport_access_token: Optional[str] = None
    is_default: bool = False
    overwrite: bool = False


class SetupStartBody(BaseModel):
    start_feishu_bot: bool = True
    enable_auto_trader: bool = False
    enable_qqq_0dte_live: bool = False
    enable_qqq_1dte_live: bool = False


class SetupRiskConfigBody(BaseModel):
    max_order_amount: Optional[float] = Field(default=None, gt=0)
    max_daily_loss_pct: Optional[float] = Field(default=None, ge=0, le=1)
    stop_loss_pct: Optional[float] = Field(default=None, ge=0, le=1)
    max_position_pct: Optional[float] = Field(default=None, ge=0, le=1)
    enabled: Optional[bool] = None


class SetupStopBody(BaseModel):
    stop_feishu_bot: bool = False
    stop_auto_trader: bool = False
    stop_qqq_0dte_live: bool = False
    stop_qqq_1dte_live: bool = False


class SetupStopAllBody(BaseModel):
    stop_backend: bool = True
    stop_frontend: bool = True
    stop_feishu_bot: bool = True
    stop_auto_trader: bool = True
    stop_qqq_0dte_live: bool = True
    stop_qqq_1dte_live: bool = True
