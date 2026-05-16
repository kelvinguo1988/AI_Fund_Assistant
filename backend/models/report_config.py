"""报告内容配置 ORM 模型"""

from datetime import datetime

from sqlalchemy import String, Boolean, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class ReportConfig(Base):
    """报告内容配置表"""

    __tablename__ = "report_config"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="显示名称")
    item_key: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, comment="标识键 如 factor_detail")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="排序")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
