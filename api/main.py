from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from api.community import ExecutionIntent, PAPER_BROKER, option_research, quote_snapshot, simple_backtest, utc_now

app = FastAPI(title="MultiTrading Community API", version="1.0.0-community")


class BacktestRequest(BaseModel):
    prices: list[float] = Field(min_length=3)
    starting_cash: float = Field(default=100_000.0, gt=0)


class IntentRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    asset_class: Literal["stock", "option"]
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    reference_price: float = Field(gt=0)
    strategy: str = Field(min_length=1, max_length=120)
    execution_mode: Literal["paper", "rehearsal"] = "paper"


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "edition": "community", "execution_modes": ["paper", "rehearsal"]}


@app.get("/market/quote/{symbol}")
def market_quote(symbol: str) -> dict[str, object]:
    try:
        return quote_snapshot(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/research/options/{symbol}")
def options_research(symbol: str) -> dict[str, object]:
    try:
        return option_research(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/research/backtest")
def research_backtest(request: BacktestRequest) -> dict[str, object]:
    try:
        return simple_backtest(request.prices, request.starting_cash)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/research/execution-intents")
def create_execution_intent(request: IntentRequest) -> dict[str, object]:
    return ExecutionIntent(created_at=utc_now(), **request.model_dump()).to_dict()


@app.post("/paper/orders")
def paper_order(request: IntentRequest) -> dict[str, object]:
    try:
        return PAPER_BROKER.submit(ExecutionIntent(created_at=utc_now(), **request.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/paper/account")
def paper_account() -> dict[str, object]:
    return PAPER_BROKER.account()
