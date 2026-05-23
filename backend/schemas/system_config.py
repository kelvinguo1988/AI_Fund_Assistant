"""系统配置 Pydantic Schema"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class AIConfigUpdate(BaseModel):
    """系统配置更新请求体"""
    ai_enabled: Optional[bool] = Field(None, description="AI 功能总开关")
    ai_model: Optional[str] = Field(None, description="AI 模型：deepseek / openai / tongyi")
    ai_api_key: Optional[str] = Field(None, description="AI 模型 API Key")
    ai_base_url: Optional[str] = Field(None, description="AI 模型 API Base URL")


class AIConfigOut(BaseModel):
    """系统配置输出 Schema（不返回 api_key）"""
    ai_enabled: bool
    ai_model: str
    ai_base_url: str


class ScoringTier(BaseModel):
    """评分档位配置"""
    min_score: float = Field(..., description="该档位最低分（含）")
    label: str = Field(..., description="档位中文标签")
    signal_direction: str = Field(..., description="信号方向: buy/hold/sell")
    signal_strength: str = Field(..., description="信号强度标识")
    operation_advice: str = Field(..., description="操作建议模板，支持 {score} 和 {equity_pct} 占位符")
    equity_ratio: float = Field(..., ge=0.0, le=1.0, description="建议权益仓位比例")


class ScoringConfigOut(BaseModel):
    """评分配置输出 Schema"""
    score_range_min: float = -6.0
    score_range_max: float = 6.0
    thresholds: list[ScoringTier]


class ScoringConfigUpdate(BaseModel):
    """评分配置更新请求体"""
    thresholds: list[ScoringTier] = Field(..., min_length=3, max_length=10, description="阈值配置列表")


class ConnectivityItem(BaseModel):
    """单个连通性测试结果"""
    name: str = Field(..., description="测试目标名称/域名")
    reachable: bool = Field(..., description="是否可达")
    latency_ms: Optional[float] = Field(None, description="延迟(毫秒)")
    error: Optional[str] = Field(None, description="错误信息")


class ConnectivityResult(BaseModel):
    """连通性测试汇总结果"""
    status: Literal["ok", "partial", "fail"] = Field(..., description="整体状态: ok / partial / fail")
    results: list[ConnectivityItem] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict, description="{total, reachable, unreachable}")
