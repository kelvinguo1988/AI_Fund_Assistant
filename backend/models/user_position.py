"""用户真实持仓 ORM 模型（手动录入 / CSV 导入，不接第三方账户同步）"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Float, String, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class UserPosition(Base):
    """我的持仓表：一只基金一行，覆盖式更新"""

    __tablename__ = "user_positions"
    __table_args__ = (
        UniqueConstraint("fund_id", name="uq_position_fund"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fund_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("funds.id", ondelete="CASCADE"), nullable=False, comment="基金 ID"
    )
    shares: Mapped[float] = mapped_column(Float, nullable=False, comment="持有份额")
    cost_nav: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="持仓成本单价（可空，缺省时组合权重按等权近似）"
    )
    source: Mapped[str] = mapped_column(
        String(10), nullable=False, default="manual", comment="manual / import"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
