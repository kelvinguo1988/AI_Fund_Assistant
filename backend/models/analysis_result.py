from __future__ import annotations
from typing import Optional
"""分析结果 ORM 模型"""

from datetime import date, datetime

from sqlalchemy import Float, String, Integer, Date, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class AnalysisResult(Base):
    """分析结果表"""

    __tablename__ = "analysis_results"
    __table_args__ = (
        UniqueConstraint("fund_id", "analysis_date", name="uq_fund_date"),
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
        Text, nullable=False, comment='JSON: {"pe_percentile": 4.2, "fed": 3.8, ...}'
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
