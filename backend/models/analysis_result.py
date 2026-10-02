from __future__ import annotations
from typing import Optional
"""分析结果 ORM 模型"""

from datetime import date, datetime

from sqlalchemy import Float, String, Integer, Date, Text, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.timezone import now_beijing


class AnalysisResult(Base):
    """分析结果表"""

    __tablename__ = "analysis_results"
    __table_args__ = (
        UniqueConstraint("fund_id", "analysis_date", name="uq_fund_date"),
        Index("ix_analysis_results_fund_id", "fund_id"),
        Index("ix_analysis_results_analysis_date", "analysis_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fund_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("funds.id", ondelete="CASCADE"), nullable=False, comment="基金 ID"
    )
    analysis_date: Mapped[date] = mapped_column(Date, nullable=False, comment="分析日期")
    weighted_score: Mapped[float] = mapped_column(Float, nullable=False, comment="归一化总分 -6.0 ~ +6.0")
    signal_direction: Mapped[str] = mapped_column(
        String(10), nullable=False, comment="buy / sell / hold"
    )
    signal_strength: Mapped[str] = mapped_column(
        String(20), nullable=True, comment="heavy_buy / moderate_buy / hold / moderate_sell / heavy_sell"
    )
    operation_advice: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="操作建议文本")
    equity_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.5, comment="建议权益仓位比例 0.0-1.0")
    factor_scores: Mapped[str] = mapped_column(
        Text, nullable=False, comment='JSON: {"price_percentile": 4.2, "fed": 3.8, ...}'
    )
    # ── AI Agent 诊断地基（2026-09-23）：原仅 trigger 当次返回的三+一字段落库，
    #    供因子/质量过滤历史有效性回算（factor_audit）与前端历史详情展示 ──
    original_score: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="因子/质量修正前原始加权分（旧行为 NULL）"
    )
    dynamic_buy_threshold: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="当次动态买入阈值"
    )
    dynamic_sell_threshold: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="当次动态卖出阈值"
    )
    quality_warnings: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment='JSON: ["警告1","警告2"]，无警告/旧行为 NULL'
    )
    # Q9（2026-10-02）：本次评分所用的净值 as-of 日期，与 analysis_date 的差即披露缺口
    nav_as_of_date: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True, comment="评分所依据的最新净值日期 YYYY-MM-DD，旧行为 NULL"
    )
    # ── §3 影子评分层（2026-10-02 第二批）：生产列保持旧口径，新口径只写这四列 ──
    #    NULL = 该行没有影子对照（开关关闭 / 变体未注册 / 变体不适用），
    #    与"影子口径给出 hold"是两回事，分歧统计只数非 NULL 行
    shadow_score: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="影子（新口径）加权分，无影子对照为 NULL"
    )
    shadow_direction: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True, comment="影子（新口径）信号方向 buy/sell/hold"
    )
    shadow_variant: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="影子口径变体名（如 caliber_2c），用于分变体统计"
    )
    shadow_detail: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment='JSON: 影子口径的分项贡献/差异原因，供分歧归因'
    )
    # 影子对比的解释前提（§3 配套元数据）：同一个分数，池大小与因子覆盖率不同就不是一回事
    pool_size: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="本轮截面标准化的样本基金数（Q5），旧行为 NULL"
    )
    factor_coverage: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="有效权重和/总权重 0.0-1.0（Q4），NULL=总权重为 0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now_beijing
    )
