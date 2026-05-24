"""基金季度持仓 ORM 模型"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class FundHolding(Base):
    """基金季度股票持仓"""

    __tablename__ = "fund_holdings"
    __table_args__ = (
        UniqueConstraint("fund_id", "stock_code", "quarter_label",
                         name="uq_fund_stock_quarter"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fund_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("funds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False, comment="股票代码")
    stock_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="股票名称")
    ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="占净值比例%")
    shares: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="持股数(万股)")
    market_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="持仓市值(万元)")
    quarter_label: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="季度标签 如 2025年3季度股票投资明细"
    )
    report_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, comment="报告日期")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
