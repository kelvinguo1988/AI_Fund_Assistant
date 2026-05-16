from __future__ import annotations
from typing import Optional
"""调度计划 ORM 模型"""

from datetime import datetime

from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class Schedule(Base):
    """调度计划表"""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="调度名称")
    cron_expr: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="Cron 表达式")
    time_point: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, comment="固定时间 HH:MM")
    task_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="analysis_push", comment="任务类型"
    )
    channel_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("push_channels.id", ondelete="SET NULL"), nullable=True, comment="推送渠道 ID"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="上次运行时间")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
