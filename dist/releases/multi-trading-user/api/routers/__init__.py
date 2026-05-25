from api.routers.agent_strategy_lab import router as agent_strategy_lab_router
from api.routers.auto_trader import router as auto_trader_router
from api.routers.backtest import router as backtest_router
from api.routers.backtests import router as backtests_router
from api.routers.dashboard_market import router as dashboard_market_router
from api.routers.fees_risk import router as fees_risk_router
from api.routers.license import router as license_router
from api.routers.market_data import router as market_data_router
from api.routers.notifications import router as notifications_router
from api.routers.options_trade import router as options_trade_router
from api.routers.qqq_0dte_strategy import router as qqq_0dte_strategy_router
from api.routers.setup import router as setup_router
from api.routers.auth import router as auth_router
from api.routers.auto_trading import router as auto_trading_router

__all__ = [
    "auto_trader_router",
    "agent_strategy_lab_router",
    "backtest_router",
    "backtests_router",
    "dashboard_market_router",
    "fees_risk_router",
    "license_router",
    "market_data_router",
    "notifications_router",
    "options_trade_router",
    "qqq_0dte_strategy_router",
    "setup_router",
    "auth_router",
    "auto_trading_router",
]
