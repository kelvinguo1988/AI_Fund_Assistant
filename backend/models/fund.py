from __future__ import annotations
from typing import Optional
"""基金池 ORM 模型"""

from datetime import datetime

from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class Fund(Base):
    """基金池表"""

    __tablename__ = "funds"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False, comment="基金代码 如 510300")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="基金名称 如 沪深300ETF")
    fund_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="etf", comment="etf / otc(场外)"
    )
    tags: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="标签 逗号分隔 如 宽基,大盘")
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="active", comment="active / disabled"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
