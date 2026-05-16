"""基金 Pydantic Schema"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class FundCreate(BaseModel):
    """新增基金请求体"""
    code: str = Field(..., pattern=r'^\d{6}$', description="基金代码，6位数字")
    name: str = Field(..., description="基金名称")
    fund_type: Literal['etf', 'otc'] = 'etf'
    tags: Optional[str] = Field(None, description="标签，逗号分隔")


class FundUpdate(BaseModel):
    """更新基金请求体"""
    name: Optional[str] = None
    fund_type: Optional[Literal['etf', 'otc']] = None
    tags: Optional[str] = None
    status: Optional[Literal['active', 'disabled']] = None


class FundOut(BaseModel):
    """基金输出 Schema"""
    id: int
    code: str
    name: str
    fund_type: str
    tags: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
