"""基金季度扩展数据 ORM 模型 — 用于标的质量过滤（第零层）

存储季报/半年报/年报披露的扩展数据：
- 基金规模（元）
- 股票仓位占比（%）
- 机构持有比例（%）
- 内部人持有份额
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class FundQuarterly(Base):
    """基金季度扩展数据"""

    __tablename__ = "fund_quarterly"
    __table_args__ = (
        UniqueConstraint("fund_id", "report_date", name="uq_fund_quarterly_report"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fund_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("funds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # ── 报告信息 ──
    report_date: Mapped[str] = mapped_column(
        String(10), nullable=False, comment="报告期日期 如 2025-03-31"
    )
    effective_date: Mapped[str] = mapped_column(
        String(10), nullable=False, comment="生效日期（报告期次月第一个交易日）"
    )
    # ── 规模数据 ──
    fund_size: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="基金规模（元）"
    )
    # ── 股票仓位 ──
    stock_position_ratio: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="股票仓位占净值比例（%），如 85.0 表示 85%"
    )
    # ── 持有人结构 ──
    institution_holding_ratio: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="机构持有比例（%），如 30.5 表示 30.5%"
    )
    insider_holding_shares: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="内部人（基金经理+公司员工）持有份额"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
