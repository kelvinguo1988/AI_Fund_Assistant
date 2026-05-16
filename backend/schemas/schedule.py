"""调度计划 Pydantic Schema"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ScheduleCreate(BaseModel):
    """新增调度计划请求体"""
    name: str = Field(..., description="调度名称")
    cron_expr: Optional[str] = Field(None, description="Cron 表达式")
    time_point: Optional[str] = Field(None, description="固定时间 HH:MM")
    task_type: Literal['analysis_push'] = 'analysis_push'
    channel_id: Optional[int] = Field(None, description="推送渠道 ID")
    enabled: bool = Field(True, description="是否启用")


class ScheduleUpdate(BaseModel):
    """更新调度计划请求体"""
    name: Optional[str] = None
    cron_expr: Optional[str] = None
    time_point: Optional[str] = None
    task_type: Optional[Literal['analysis_push']] = None
    channel_id: Optional[int] = None
    enabled: Optional[bool] = None


class ScheduleOut(BaseModel):
    """调度计划输出 Schema"""
    id: int
    name: str
    cron_expr: Optional[str]
    time_point: Optional[str]
    task_type: str
    channel_id: Optional[int]
    enabled: bool
    last_run_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
