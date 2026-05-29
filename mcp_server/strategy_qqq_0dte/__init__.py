"""
QQQ 0DTE 日内策略模块化实现：关键位、反应区、成交量/形态确认、会话闸门、
合成期权定价、选约、进出场规则、回测回放与下单意图构造。

strategy_variant=morning_strangle 时：美东绝对时间窗内、相对前收窄幅震荡则同时买入 Call/Put（选约：strike_step+call/put_strikes_otm），
组合权利金盈亏率（不含手续费，已实现平仓金额 + 剩余腿 bid，相对原始两腿 ask）达标或到达强平时刻平掉剩余腿。
morning_directional 时：同时间窗内相对前收跌超阈买 Call、涨超阈买 Put（同上选约），单腿权利金盈亏率达标或到达强平时刻平仓。
gamma_scalping 时：开盘窗口内按「站上昨高买 Call / 跌破昨低买 Put + VIX 同步」与「VWAP 偏离后首次回归」触发单腿，
并叠加 NVDA/TSLA 领先确认；以硬止损、快止盈、最长持仓与强平时刻退出。
gamma_pro 时：在 gamma_scalping 基础上增加「假突破反向」与「午后回踩 VWAP 后续航」两类信号，
时间窗默认偏向 10:00 之后，并在午间暂停窗口内禁止新开仓。

实盘请将 StrategyController 的产出交给现有 /options/* 与自动交易调度；回测用 run_qqq_0dte_backtest。
"""

from .backtest import run_qqq_0dte_backtest
from .config import Qqq0dteConfig
from .controller import StrategyController
from .monitoring import CircuitBreakerState
from .oms_adapter import intent_to_legs_template
from .state import TradeIntent, TradeIntentKind

__all__ = [
    "CircuitBreakerState",
    "Qqq0dteConfig",
    "StrategyController",
    "TradeIntent",
    "TradeIntentKind",
    "intent_to_legs_template",
    "run_qqq_0dte_backtest",
]
