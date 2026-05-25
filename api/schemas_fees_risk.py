from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FeeScheduleBody(BaseModel):
    schedule: dict[str, Any]
    broker_id: str | None = Field(default=None, description="要写入的券商配置；缺省为当前「费用试算默认」券商")


class FeeBrokerCreateBody(BaseModel):
    broker_id: str
    display_name: str = ""
    copy_from: str | None = Field(default=None, description="从已有券商复制费用表；缺省为系统默认模板")


class FeeBrokerActiveBody(BaseModel):
    broker_id: str = Field(description="未连接默认账户时使用的费用模板（持久化到 manual_fee_broker_id）")


class FeeBrokerDisplayNameBody(BaseModel):
    display_name: str = Field(
        default="",
        description="券商展示名称（下拉框等 UI 使用）；空字符串则回退为 broker_id",
    )

