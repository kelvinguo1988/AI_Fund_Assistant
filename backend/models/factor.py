from __future__ import annotations
from typing import Optional
"""量化因子 ORM 模型"""

from datetime import datetime

from sqlalchemy import String, Float, Integer, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class Factor(Base):
    """量化因子表"""

    __tablename__ = "factors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="因子名称 如 PE百分位")
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, comment="因子代码 如 pe_percentile")
    data_field: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="数据源字段标识")
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, comment="权重")
    direction: Mapped[str] = mapped_column(
        String(10), nullable=False, default="positive", comment="positive(正向) / negative(反向)"
    )
    params: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="JSON 格式参数")
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="active", comment="active / disabled"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="排序")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
