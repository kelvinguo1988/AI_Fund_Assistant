"""量化因子 Pydantic Schema"""

import json
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class FactorCreate(BaseModel):
    """新增因子请求体"""
    name: str = Field(..., description="因子名称")
    code: str = Field(..., description="因子代码")
    data_field: Optional[str] = Field(None, description="数据源字段标识")
    weight: float = Field(1.0, description="权重")
    direction: Literal['positive', 'negative'] = 'positive'
    params: Optional[dict] = Field(None, description="JSON 格式参数")
    sort_order: int = Field(0, description="排序")


class FactorUpdate(BaseModel):
    """更新因子请求体"""
    name: Optional[str] = None
    weight: Optional[float] = None
    direction: Optional[Literal['positive', 'negative']] = None
    params: Optional[dict] = None
    status: Optional[Literal['active', 'disabled']] = None
    sort_order: Optional[int] = None


class FactorOut(BaseModel):
    """因子输出 Schema（含 weight_percentage 计算字段）"""
    id: int
    name: str
    code: str
    data_field: Optional[str]
    weight: float
    direction: str
    params: Optional[dict] = None
    status: str
    sort_order: int
    weight_percentage: float = 0.0  # 计算字段：当前权重/总权重*100

    @field_validator("params", mode="before")
    @classmethod
    def parse_params(cls, v):
        if isinstance(v, str):
            return json.loads(v) if v else None
        return v

    model_config = {"from_attributes": True}
