from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Boolean, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class HolidayCalendar(Base):
    """调休/节假日日历表（从互联网数据源同步，溯源国务院放假安排）

    - is_off_day=True  表示休息日（股市休市）
    - is_off_day=False 表示调休补班工作日（周末上班，股市开市）
    """

    __tablename__ = "holiday_calendar"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    holiday_date: Mapped[str] = mapped_column(
        String(10), unique=True, nullable=False, comment="日期 YYYY-MM-DD"
    )
    is_off_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        comment="True=休息日(休市), False=调休补班工作日(开市)",
    )
    holiday_name: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="节假日名称"
    )
    source: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="数据来源地址"
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
