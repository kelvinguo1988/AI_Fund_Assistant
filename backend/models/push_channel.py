from __future__ import annotations
from typing import Optional
"""推送渠道 ORM 模型"""

from datetime import datetime

from sqlalchemy import String, Boolean, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class PushChannel(Base):
    """推送渠道表"""

    __tablename__ = "push_channels"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="渠道名称")
    channel_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="feishu / qq")
    webhook_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="Webhook 地址")
    token: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="Secret / Token")
    config: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="JSON 额外配置")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
