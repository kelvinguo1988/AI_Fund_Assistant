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
    # 因子数据不足标记：原先只活在内存里的 FactorScoreResult，落库 JSON 时被丢掉
    # → 从 DB 重建的报告/AI 上下文永远看不到"该因子缺数据"（2026-10-01 审查 P1）
    data_valid: bool = True


class AnalysisResultOut(BaseModel):
    """分析结果输出 Schema"""
    id: int
    fund_id: int
    fund_code: str
    fund_name: str
    analysis_date: date
    weighted_score: float         # -8.5 ~ +8.5（池内相对分，见 pool_size）
    signal_direction: str         # buy / sell / hold
    signal_strength: str
    operation_advice: str
    equity_ratio: float = 0.5     # 建议权益仓位比例
    factor_scores: List[FactorScore]
    created_at: datetime
    # ── 第零层扩展字段（可选，向后兼容）──
    original_score: Optional[float] = None         # 因子修正前原始评分
    dynamic_buy_threshold: Optional[float] = None  # 动态买入阈值
    dynamic_sell_threshold: Optional[float] = None # 动态卖出阈值
    quality_warnings: Optional[List[str]] = None   # 质量过滤警告
    # Q9：本次评分依据的最新净值日期（与 analysis_date 的差 = 披露缺口；旧行为 NULL）
    nav_as_of_date: Optional[str] = None
    # Q5：本轮参与截面标准化的基金数 —— weighted_score 是**池内相对分**，
    # 同一个 2.5 分在 50 只池和 8 只池不是一回事；旧行与未跑过分析的为 NULL
    pool_size: Optional[int] = None

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
    dynamic_sell_threshold: Optional[float] = None
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
    benchmark_growth_pct: Optional[float] = None   # 基准同区间 %（沪深300 价格指数 + 股息，见 caliber）
    excess_pct: Optional[float] = None             # 超额 %
    best: Optional[FundReviewItem] = None
    worst: Optional[FundReviewItem] = None
    items: list[FundReviewItem] = []
    # 信号复盘：区间首日前最近信号与区间实际涨跌的同向率（绝对 + 超额两个口径）
    signal_stats: dict = {}
    # 生效口径（Q11）：{nav_adjusted, bench_div_yield_pct, lines=[三行口径头]}
    caliber: dict = {}
    summary_md: str = ""                            # Markdown 复盘报告（可直接喂 AI 解读）


# ── 基金 PK（多基金业绩/风险/归因对比，2026-08-31）─────────────────────

class FundCompareMetrics(BaseModel):
    """单窗口业绩风险指标"""
    window_label: str                       # 近2年 / 成立以来
    days: int                               # 实际样本天数
    annual_return_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    sharpe: Optional[float] = None
    beta: Optional[float] = None            # 对基准（默认沪深300）
    alpha_annual_pct: Optional[float] = None
    info_ratio: Optional[float] = None


class FundCompareItem(BaseModel):
    fund_code: str
    fund_name: str
    scale_growth: Optional[float] = None    # 份额/规模变化倍数（首末季报比）
    institution_pct: Optional[float] = None # 最新机构持有占比 %
    windows: list[FundCompareMetrics] = []
    error: Optional[str] = None


class CompareReport(BaseModel):
    baseline: str = "沪深300"
    items: list[FundCompareItem] = []
    caliber: dict = {}   # 生效口径（Q11）：同 ReviewReport.caliber
    summary_md: str = ""
