"""Research-only MCP service. There are deliberately no broker tools here."""
from __future__ import annotations

from api.community import ExecutionIntent, PAPER_BROKER, option_research, quote_snapshot, simple_backtest, utc_now

COMMUNITY_TOOL_NAMES = (
    "market_quote",
    "option_research",
    "research_backtest",
    "create_execution_intent",
    "paper_submit_order",
    "paper_account",
)


def list_community_tools() -> tuple[str, ...]:
    return COMMUNITY_TOOL_NAMES


def market_quote(symbol: str) -> dict[str, object]:
    return quote_snapshot(symbol)


def option_research_tool(symbol: str) -> dict[str, object]:
    return option_research(symbol)


def research_backtest(prices: list[float]) -> dict[str, object]:
    return simple_backtest(prices)


def create_execution_intent(
    symbol: str, asset_class: str, side: str, quantity: float, reference_price: float, strategy: str
) -> dict[str, object]:
    return ExecutionIntent(
        symbol=symbol.upper(), asset_class=asset_class, side=side, quantity=quantity,
        reference_price=reference_price, strategy=strategy, created_at=utc_now(),
    ).to_dict()


def paper_submit_order(**intent: object) -> dict[str, object]:
    return PAPER_BROKER.submit(ExecutionIntent(created_at=utc_now(), **intent))


def paper_account() -> dict[str, object]:
    return PAPER_BROKER.account()


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("multitrading-community")
    server.tool()(market_quote)
    server.tool(name="option_research")(option_research_tool)
    server.tool()(research_backtest)
    server.tool()(create_execution_intent)
    server.tool()(paper_submit_order)
    server.tool()(paper_account)
    server.run()


if __name__ == "__main__":
    main()
