"""AI Skill ORM 模型 — 可导入的分析技能包"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class AISkill(Base):
    """AI 分析 Skill

    Skill 是一段可启停的系统提示词扩展包，对话时按 id 序注入系统提示词，
    支持 {{fund_pool}} / {{market_regime}} / {{fund:<id>}} 占位符渲染数据上下文。
    """
    __tablename__ = "ai_skills"
    __table_args__ = (
        UniqueConstraint("name", name="uq_ai_skill_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="Skill 名称（唯一）")
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="功能描述")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, comment="注入的系统提示词（支持数据占位符）")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
