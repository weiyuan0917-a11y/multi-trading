from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _AutoTraderBody(BaseModel):
    model_config = ConfigDict(extra="allow")


class AutoTraderConfirmBody(_AutoTraderBody):
    confirmation_token: str | None = None


class AutoTraderConfigBody(_AutoTraderBody):
    pass


class AutoTraderImportConfigBody(_AutoTraderBody):
    pass


class AutoTraderImportBody(_AutoTraderBody):
    config: dict[str, Any] | None = None


class AutoTraderRollbackBody(_AutoTraderBody):
    backup_id: str | None = None


class AutoTraderTemplateApplyBody(_AutoTraderBody):
    name: str | None = None
    template: str | None = None


class AutoTraderResearchRunBody(_AutoTraderBody):
    market: str | None = None


class AutoTraderStrategyMatrixRunBody(_AutoTraderBody):
    market: str | None = None


class AutoTraderMlMatrixRunBody(_AutoTraderBody):
    market: str | None = None


class AutoTraderMlMatrixApplyBody(_AutoTraderBody):
    market: str | None = None
    variant: str | None = None
