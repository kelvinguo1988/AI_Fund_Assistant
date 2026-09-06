from __future__ import annotations
from typing import Optional
"""基金池 ORM 模型"""

from datetime import datetime

from sqlalchemy import String, DateTime, Boolean
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
    tags: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="主标签（官方类型+基准定位+名称解析，逗号分隔）")
    fund_type_official: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="官方基金类型（F10 基本概况，如 混合型-偏股）"
    )
    benchmark_text: Mapped[Optional[str]] = mapped_column(
        String(300), nullable=True, comment="业绩比较基准原文（F10）"
    )
    exposure_tags: Mapped[Optional[str]] = mapped_column(
        String(300), nullable=True, comment="副标签：当前持仓赛道暴露（随季报变动，逗号分隔 含占比）"
    )
    starred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否星标收藏（基金池置顶 + 橙黄色展示）"
    )
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="active", comment="active / disabled"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
