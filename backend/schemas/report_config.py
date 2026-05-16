"""报告内容配置 Pydantic Schema"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ReportConfigOut(BaseModel):
    """报告配置输出 Schema"""
    id: int
    name: str
    item_key: str
    enabled: bool
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ReportConfigUpdate(BaseModel):
    """报告配置更新请求体"""
    id: int = Field(..., description="配置项 ID")
    enabled: Optional[bool] = Field(None, description="是否启用")
    sort_order: Optional[int] = Field(None, description="排序")
