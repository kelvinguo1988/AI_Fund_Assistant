from __future__ import annotations
from typing import Optional
"""系统配置 ORM 模型（KV 结构）"""

from datetime import datetime

from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class SystemConfig(Base):
    """系统配置表（KV 结构）"""

    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    config_key: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, comment="配置键"
    )
    config_value: Mapped[str] = mapped_column(Text, nullable=False, comment="配置值")
    description: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="配置说明")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
