"""量化因子 Pydantic Schema"""

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class FactorCreate(BaseModel):
    """新增因子请求体"""
    name: str = Field(..., description="因子名称")
    code: str = Field(..., description="因子代码")
    data_field: Optional[str] = Field(None, description="(旧)数据源字段标识")
    data_fields: Optional[list[str]] = Field(None, description="所需数据字段列表")
    weight: float = Field(1.0, description="权重")
    direction: Literal['positive', 'negative'] = 'positive'
    params: Optional[dict] = Field(None, description="(旧)JSON 格式参数")
    formula: Optional[str] = Field(None, description="计算公式表达式")
    window: Optional[int] = Field(None, description="计算窗口")
    window_unit: Optional[Literal['day', 'quarter']] = Field(None, description="窗口单位")
    signal_rules: Optional[list[dict]] = Field(None, description="信号规则数组")
    normalization: str = Field("none", description="标准化方式")
    normalization_config: Optional[dict] = Field(None, description="标准化配置")
    sort_order: int = Field(0, description="排序")


class FactorUpdate(BaseModel):
    """更新因子请求体"""
    name: Optional[str] = None
    data_field: Optional[str] = None
    data_fields: Optional[list[str]] = None
    weight: Optional[float] = None
    direction: Optional[Literal['positive', 'negative']] = None
    params: Optional[dict] = None
    formula: Optional[str] = None
    window: Optional[int] = None
    window_unit: Optional[Literal['day', 'quarter']] = None
    signal_rules: Optional[list[dict]] = None
    normalization: Optional[str] = None
    normalization_config: Optional[dict] = None
    status: Optional[Literal['active', 'disabled']] = None
    sort_order: Optional[int] = None


def _try_parse_json(v: Any) -> Any:
    """将 JSON 字符串解析为 Python 对象"""
    if isinstance(v, str):
        return json.loads(v) if v.strip() else None
    return v


class FactorOut(BaseModel):
    """因子输出 Schema"""
    id: int
    name: str
    code: str
    data_field: Optional[str] = None
    data_fields: Optional[list[str]] = None
    weight: float
    direction: str
    params: Optional[dict] = None
    formula: Optional[str] = None
    window: Optional[int] = None
    window_unit: Optional[str] = None
    signal_rules: Optional[list[dict]] = None
    normalization: str = "none"
    normalization_config: Optional[dict] = None
    status: str
    sort_order: int
    weight_percentage: float = 0.0  # 计算字段

    _parse_params = field_validator("params", mode="before")(_try_parse_json)
    _parse_data_fields = field_validator("data_fields", mode="before")(_try_parse_json)
    _parse_signal_rules = field_validator("signal_rules", mode="before")(_try_parse_json)
    _parse_norm_config = field_validator("normalization_config", mode="before")(_try_parse_json)

    model_config = {"from_attributes": True}
