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
    # 最近一次在"现任经理名单"中被看到的时刻。记录按 (fund_id, name) 唯一且只增不改，
    # 单靠 created_at 无法区分"共同在任的多位经理"与"历任"，故用本列做在任快照：
    # 同一次刷新写入/续见的记录 = 现任；last_seen_at 停在旧批次的 = 已离任。
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, default=datetime.now, comment="最近确认在任时间"
    )
