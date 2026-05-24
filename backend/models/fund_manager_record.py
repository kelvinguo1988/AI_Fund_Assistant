"""基金经理记录 ORM 模型"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class FundManagerRecord(Base):
    """基金经理记录"""

    __tablename__ = "fund_manager_records"
    __table_args__ = (
        UniqueConstraint("fund_id", "manager_name", name="uq_fund_manager"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fund_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("funds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    manager_name: Mapped[str] = mapped_column(String(50), nullable=False, comment="经理姓名")
    company: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="所属公司")
    tenure_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="累计从业天数")
    asset_scale: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="现任基金资产总规模(亿元)")
    best_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="现任基金最佳回报%")
    managed_codes: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="现任基金代码列表")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
