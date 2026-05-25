from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from api.schemas_backtest import BacktestKline


class SubmitOrderBody(BaseModel):
    action: Literal["buy", "sell"]
    symbol: str
    quantity: int = Field(ge=1)
    price: Optional[float] = Field(default=None, gt=0)
    confirmation_token: Optional[str] = None
    account_id: Optional[str] = None


class OptionLegBody(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    contracts: int = Field(ge=1)
    price: Optional[float] = Field(default=None, ge=0)


class OptionOrderBody(BaseModel):
    symbol: Optional[str] = None
    side: Optional[Literal["buy", "sell"]] = None
    contracts: Optional[int] = Field(default=None, ge=1)
    price: Optional[float] = Field(default=None, ge=0)
    legs: Optional[list[OptionLegBody]] = None
    max_loss_threshold: Optional[float] = Field(default=None, gt=0)
    max_capital_usage: Optional[float] = Field(default=None, gt=0)
    confirmation_token: Optional[str] = None
    account_id: Optional[str] = None


class OptionBacktestBody(BaseModel):
    symbol: str
    template: Literal["bull_call_spread", "bear_put_spread", "straddle", "strangle"]
    # 与 GET/POST /backtest/compare 一致：日历窗口 + 周期数 + K 线类型
    days: int = Field(default=180, ge=1, le=3650)
    periods: int = Field(default=0, ge=0, le=500_000)
    kline: BacktestKline = "1d"
    use_server_kline_cache: bool = False
    # 持有「K 线根数」（与日 K / 1 分 K 一致）；旧版字段名仍为 holding_days
    holding_days: int = Field(default=20, ge=2, le=20_000)
    contracts: int = Field(default=1, ge=1, le=50)
    width_pct: float = Field(default=0.05, ge=0.01, le=0.3)


class SyntheticOptionPathBody(BaseModel):
    """标的 K 线 + 滚动 HV + BS 合成期权理论价路径（无期权历史 K 线时使用）。"""

    symbol: str
    structure: Literal["single", "vertical"] = "single"
    strike: float | None = Field(default=None, gt=0)
    long_strike: float | None = Field(default=None, gt=0)
    short_strike: float | None = Field(default=None, gt=0)
    expiry: str = Field(description="到期时刻 ISO8601，例如 2025-03-28T20:00:00")
    right: Literal["call", "put", "C", "P"] = "call"
    days: int = Field(default=180, ge=1, le=3650)
    periods: int = Field(default=0, ge=0, le=500_000)
    kline: BacktestKline = "1d"
    use_server_kline_cache: bool = False
    rate: float = Field(default=0.052, ge=0.0, le=0.2)
    div_yield: float = Field(default=0.0, ge=0.0, le=0.5)
    vol_window: int = Field(default=20, ge=2, le=500)
    min_sigma: float = Field(default=0.01, ge=1e-6, le=2.0)
    spot_source: Literal["close", "open"] = "close"
    max_rows: int = Field(default=2000, ge=10, le=50_000)

    @model_validator(mode="after")
    def _strikes_match_structure(self) -> SyntheticOptionPathBody:
        if self.structure == "single":
            if self.strike is None:
                raise ValueError("structure=single 时必须提供 strike")
        else:
            if self.long_strike is None or self.short_strike is None:
                raise ValueError("structure=vertical 时必须提供 long_strike 与 short_strike")
        return self
