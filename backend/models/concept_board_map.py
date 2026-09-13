"""概念板块映射 ORM 模型 — THS 概念板块成分（渐进获取）"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class ConceptBoardMap(Base):
    """同花顺概念板块成分映射（stock_code → concepts）

    渐进获取：每轮抓取一批最久未更新的板块（防封禁），落库后
    暴露标签计算优先使用真概念，未覆盖股票回落关键词规则。
    """
    __tablename__ = "concept_board_map"
    __table_args__ = (
        UniqueConstraint("concept", "stock_code", name="uq_concept_stock"),
        Index("ix_concept_map_stock", "stock_code"),
        Index("ix_concept_map_updated", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    concept: Mapped[str] = mapped_column(String(60), nullable=False, comment="概念板块名")
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False, comment="股票代码")
    stock_name: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, comment="该板块成分最近抓取时间"
    )
