"""基金业务逻辑 — CRUD + 状态管理"""

import json
import logging
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.fund import Fund
from backend.schemas.fund import FundCreate, FundUpdate

logger = logging.getLogger(__name__)


class FundService:
    """基金池服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_funds(self, status: Optional[str] = None) -> list[Fund]:
        """获取基金列表

        Args:
            status: 筛选状态，None 表示全部

        Returns:
            基金列表
        """
        stmt = select(Fund).order_by(Fund.id)
        if status:
            stmt = stmt.where(Fund.status == status)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_fund(self, fund_id: int) -> Optional[Fund]:
        """获取单个基金

        Args:
            fund_id: 基金 ID

        Returns:
            Fund 对象或 None
        """
        result = await self.db.execute(select(Fund).where(Fund.id == fund_id))
        return result.scalars().first()

    async def get_fund_by_code(self, code: str) -> Optional[Fund]:
        """根据代码获取基金

        Args:
            code: 基金代码

        Returns:
            Fund 对象或 None
        """
        result = await self.db.execute(select(Fund).where(Fund.code == code))
        return result.scalars().first()

    async def create_fund(self, data: FundCreate) -> Fund:
        """新增基金

        Args:
            data: 基金创建数据

        Returns:
            创建的 Fund 对象

        Raises:
            ValueError: 基金代码已存在
        """
        existing = await self.get_fund_by_code(data.code)
        if existing:
            raise ValueError(f"基金代码 {data.code} 已存在")

        fund = Fund(
            code=data.code,
            name=data.name,
            fund_type=data.fund_type,
            tags=data.tags,
            status="active",
        )
        self.db.add(fund)
        await self.db.commit()
        await self.db.refresh(fund)
        return fund

    async def update_fund(self, fund_id: int, data: FundUpdate) -> Optional[Fund]:
        """更新基金信息

        Args:
            fund_id: 基金 ID
            data: 更新数据

        Returns:
            更新后的 Fund 对象或 None
        """
        fund = await self.get_fund(fund_id)
        if fund is None:
            return None

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(fund, key, value)

        await self.db.commit()
        await self.db.refresh(fund)
        return fund

    async def delete_fund(self, fund_id: int) -> bool:
        """删除基金

        Args:
            fund_id: 基金 ID

        Returns:
            是否删除成功
        """
        fund = await self.get_fund(fund_id)
        if fund is None:
            return False

        await self.db.delete(fund)
        await self.db.commit()
        return True

    async def batch_update_status(self, ids: list[int], action: str) -> None:
        """批量更新基金状态

        Args:
            ids: 基金 ID 列表
            action: 操作类型 "active" / "disabled"
        """
        if action not in ("active", "disabled"):
            raise ValueError(f"无效的操作类型: {action}")

        stmt = update(Fund).where(Fund.id.in_(ids)).values(status=action)
        await self.db.execute(stmt)
        await self.db.commit()
