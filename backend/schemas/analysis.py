"""分析结果 Pydantic Schema"""

from datetime import date, datetime
from typing import List, Optional

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
    # ── 第零层扩展字段（可选，向后兼容）──
    original_score: Optional[float] = None         # 因子修正前原始评分
    dynamic_buy_threshold: Optional[float] = None  # 动态买入阈值
    quality_warnings: Optional[List[str]] = None   # 质量过滤警告

    model_config = {"from_attributes": True}


# ── 历史报告导出/导入 ─────────────────────────────────────────────


class AnalysisExportItem(BaseModel):
    """单个历史报告导出条目"""
    fund_code: str
    fund_name: str
    analysis_date: str          # YYYY-MM-DD
    weighted_score: float
    signal_direction: str
    signal_strength: str
    operation_advice: str
    equity_ratio: float = 0.5
    factor_scores: dict  # JSON dict
    original_score: Optional[float] = None
    dynamic_buy_threshold: Optional[float] = None
    quality_warnings: Optional[list[str]] = None


class AnalysisExportPayload(BaseModel):
    """分析结果导出载体"""
    version: str = "1.0"
    exported_at: str = ""
    items: list[AnalysisExportItem]


class AnalysisImportResult(BaseModel):
    """导入结果统计"""
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = []


# ── 投资复盘（组合区间收益复盘，2026-08-30 新增）──────────────────────

class FundReviewItem(BaseModel):
    """单只基金区间复盘明细"""
    fund_code: str
    fund_name: str
    nav_start: Optional[float] = None       # 区间起点净值（起点日或之前最近交易日）
    nav_end: Optional[float] = None
    growth_pct: Optional[float] = None      # 区间涨跌 %
    score_start: Optional[float] = None     # 区间首日前最近一次评分
    score_end: Optional[float] = None
    signal_start: Optional[str] = None
    signal_end: Optional[str] = None
    contribution_pct: Optional[float] = None  # 等权贡献 = growth/N
    error: Optional[str] = None             # 净值获取失败原因


class ReviewReport(BaseModel):
    """组合区间复盘报告"""
    start_date: str
    end_date: str
    fund_count: int
    portfolio_growth_pct: Optional[float] = None   # 等权组合区间收益 %
    benchmark_growth_pct: Optional[float] = None   # 沪深300 同区间 %
    excess_pct: Optional[float] = None             # 超额 %
    best: Optional[FundReviewItem] = None
    worst: Optional[FundReviewItem] = None
    items: list[FundReviewItem] = []
    # 信号复盘：区间首日前最近信号与区间实际涨跌的同向率
    signal_stats: dict = {}
    summary_md: str = ""                            # Markdown 复盘报告（可直接喂 AI 解读）
