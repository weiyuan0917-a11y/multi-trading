from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from api.schemas_backtest import BacktestKline


class Qqq0dteBacktestBody(BaseModel):
    """QQQ 0DTE 策略回测：拉标的 K 线 + 模块内合成期权价。"""

    symbol: str = Field(default="QQQ.US", min_length=1)
    days: int = Field(default=5, ge=1, le=3650)
    periods: int = Field(default=0, ge=0, le=500_000)
    kline: BacktestKline = "1m"
    use_server_kline_cache: bool = False
    rth_only: bool = Field(
        False,
        description="仅使用美股常规交易时段(RTH 09:30-16:00 ET)的K线进行回测；开启后会先过滤再回测",
    )
    strategy_config: dict = Field(default_factory=dict, description="覆盖 Qqq0dteConfig 字段的 JSON 对象")
    save_snapshot: bool = Field(
        True,
        description="是否将本次请求与回测结果写入 data/qqq_0dte/backtest_snapshots.json",
    )


class Qqq0dteResolveContractBody(BaseModel):
    """按标的 + 到期日 + 行权价 + 方向解析 OPRA，与回测里 select_strike 结果对齐后用于实盘下单。"""

    symbol: str = Field(default="QQQ.US", min_length=1, description="标的，如 QQQ.US")
    strike: float = Field(..., gt=0)
    right: str = Field(..., description="call / put / C / P")
    expiry_date: str | None = Field(
        None,
        description="YYYY-MM-DD；0DTE 填当日到期日。省略则使用 LongPort 返回的最近到期（未必是 0DTE）",
    )
    strike_window: float = Field(5.0, ge=0.5, le=80.0, description="链查询行权价半宽")
    standard_only: bool = Field(False, description="仅标准合约")
    max_strike_diff: float = Field(1.5, ge=0.01, le=20.0, description="允许与请求行权价的最大偏差")
    use_bid_for_sell_limit: bool = Field(
        False,
        description="为 true 时，suggested_limit_price_per_share 优先用 LongPort 实时买一（depth bid，失败则 bid 字段/last 回退）；用于实盘平仓限价",
    )
    use_ask_for_buy_limit: bool = Field(
        False,
        description="为 true 时，suggested_limit_price_per_share 优先用 LongPort 实时卖一（depth ask，失败则 ask 字段/last 回退）；用于实盘开仓（买）限价",
    )


class Qqq0dteMatrixBody(BaseModel):
    """同一批 K 线下对 grid 做笛卡尔积回测；grid 键可用后端字段名，或用 reaction_zone_width_pct（界面同款百分数）。"""

    symbol: str = Field(default="QQQ.US", min_length=1)
    days: int = Field(default=5, ge=1, le=3650)
    periods: int = Field(default=0, ge=0, le=500_000)
    kline: BacktestKline = "1m"
    use_server_kline_cache: bool = False
    rth_only: bool = Field(
        False,
        description="仅使用美股常规交易时段(RTH 09:30-16:00 ET)的K线；开启后先过滤再做参数矩阵",
    )
    strategy_config: dict[str, Any] = Field(default_factory=dict, description="非网格基线配置，与单次回测一致")
    grid: dict[str, list[Any]] = Field(
        ...,
        min_length=1,
        description='例如 {"reaction_zone_width_pct": [0.08, 0.1]}；早盘可增 {"strategy_variant": ["morning_strangle","morning_directional"], "strangle_range_pct_ui": [0.3, 0.5]}（与前端矩阵表一致）',
    )
    top_n: int = Field(20, ge=1, le=100)
    sort_by: Literal["realized_pnl", "return_pct"] = Field(
        "realized_pnl",
        description="return_pct 按各行 realized_pnl ÷ 累计开仓权利金 ×100；无开仓则为 null 并排后",
    )
    max_combinations: int = Field(2000, ge=1, le=10000, description="组合数上限，超限则 400")
    suppress_logs: bool = Field(True, description="网格内关闭逐 bar 决策日志以加速")
