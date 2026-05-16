from __future__ import annotations
from typing import Optional
"""AI 对话历史 ORM 模型"""

from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class AIConversation(Base):
    """AI 对话历史表"""

    __tablename__ = "ai_conversations"
    __table_args__ = (
        Index("idx_ai_conv_id", "conversation_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), nullable=False, comment="会话 UUID"
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False, comment="user / assistant")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息内容")
    context_type: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="single_fund / pool / market"
    )
    fund_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("funds.id", ondelete="SET NULL"), nullable=True, comment="关联基金 ID"
    )
    model_name: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, comment="模型名称")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
