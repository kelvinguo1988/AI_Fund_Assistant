"""基金数据缓存模型 — 存储阶段涨幅等外部数据，实现先展示缓存再后台刷新"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class FundDataCache(Base):
    """基金数据缓存"""

    __tablename__ = "fund_data_caches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True, comment="缓存键"
    )
    data_json: Mapped[str] = mapped_column(Text, nullable=False, comment="JSON 数据")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
