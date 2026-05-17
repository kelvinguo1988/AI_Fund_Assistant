"""分析结果 Pydantic Schema"""

from datetime import date, datetime
from typing import List

from pydantic import BaseModel, Field


class FactorScore(BaseModel):
    """单个因子评分"""
    factor_code: str
    factor_name: str
    raw_value: float
    score: float            # -1.0 ~ +1.0 标准化评分
    direction: str


class AnalysisResultOut(BaseModel):
    """分析结果输出 Schema"""
    id: int
    fund_id: int
    fund_code: str
    fund_name: str
    analysis_date: date
    weighted_score: float         # -6.0 ~ +6.0 归一化评分
    signal_direction: str         # buy / sell / hold
    signal_strength: str
    operation_advice: str
    equity_ratio: float = 0.5     # 建议权益仓位比例
    factor_scores: List[FactorScore]
    created_at: datetime

    model_config = {"from_attributes": True}
