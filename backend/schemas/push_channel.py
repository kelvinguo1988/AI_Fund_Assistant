"""推送渠道 Pydantic Schema"""

import json
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class PushChannelCreate(BaseModel):
    """新增推送渠道请求体"""
    name: str = Field(..., description="渠道名称")
    channel_type: Literal['feishu', 'qq'] = Field(..., description="渠道类型")
    webhook_url: Optional[str] = Field(None, description="Webhook 地址")
    token: Optional[str] = Field(None, description="Secret / Token")
    config: Optional[dict] = Field(None, description="额外 JSON 配置")
    enabled: bool = Field(True, description="是否启用")


class PushChannelUpdate(BaseModel):
    """更新推送渠道请求体"""
    name: Optional[str] = None
    channel_type: Optional[Literal['feishu', 'qq']] = None
    webhook_url: Optional[str] = None
    token: Optional[str] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None


class PushChannelOut(BaseModel):
    """推送渠道输出 Schema"""
    id: int
    name: str
    channel_type: str
    webhook_url: Optional[str]
    token: Optional[str]
    config: Optional[dict] = None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("config", mode="before")
    @classmethod
    def parse_config(cls, v):
        if isinstance(v, str):
            return json.loads(v) if v else None
        return v

    model_config = {"from_attributes": True}
