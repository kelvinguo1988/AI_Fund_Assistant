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


class FundHoldingOut(BaseModel):
    """基金持仓输出"""
    stock_code: str
    stock_name: str
    ratio: Optional[float] = None
    shares: Optional[float] = None
    market_value: Optional[float] = None
    quarter_label: str

    model_config = {"from_attributes": True}


class FundManagerOut(BaseModel):
    """基金经理输出"""
    manager_name: str
    company: Optional[str] = None
    tenure_days: Optional[int] = None
    asset_scale: Optional[float] = None
    best_return: Optional[float] = None

    model_config = {"from_attributes": True}


class FundPeriodReturn(BaseModel):
    """基金阶段涨幅"""
    code: str
    name: str
    return_1m: Optional[str] = None
    return_3m: Optional[str] = None
    return_6m: Optional[str] = None
    return_1y: Optional[str] = None


class FundDetailResponse(BaseModel):
    """基金详情响应（含缓存时间）"""
    funds: list[FundPeriodReturn] = []
    updated_at: Optional[str] = None


class FundDetailStatus(BaseModel):
    """基金详情缓存状态"""
    has_cache: bool = False
    updated_at: Optional[str] = None
    refreshing: bool = False


class HoldingChangeItem(BaseModel):
    """调仓明细项"""
    stock_code: str
    stock_name: str
    ratio: Optional[float] = None


class HoldingChanges(BaseModel):
    """持仓变更"""
    latest_quarter: str
    previous_quarter: str
    added: list[HoldingChangeItem] = []
    removed: list[HoldingChangeItem] = []


class ManagerChangeInfo(BaseModel):
    """经理变更信息"""
    manager_name: str
    company: Optional[str] = None
    tenure_days: Optional[int] = None
    asset_scale: Optional[float] = None
    best_return: Optional[float] = None


class ManagerChanges(BaseModel):
    """经理变更"""
    current: list[ManagerChangeInfo] = []
    history: list[ManagerChangeInfo] = []
    changed: bool = False


class FundChangeSummary(BaseModel):
    """基金变更摘要"""
    fund_id: int
    fund_code: str
    fund_name: str
    holding_changes: Optional[HoldingChanges] = None
    manager_changes: Optional[ManagerChanges] = None
    tags: list[str] = []
