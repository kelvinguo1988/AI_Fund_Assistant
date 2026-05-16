"""因子业务逻辑 — CRUD + 权重管理"""

import json
import logging
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.factor import Factor
from backend.schemas.factor import FactorCreate, FactorUpdate

logger = logging.getLogger(__name__)


class FactorService:
    """因子管理服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_factors(self, status: Optional[str] = None) -> list[Factor]:
        """获取因子列表（按 sort_order 排序）

        Args:
            status: 筛选状态

        Returns:
            因子列表
        """
        stmt = select(Factor).order_by(Factor.sort_order, Factor.id)
        if status:
            stmt = stmt.where(Factor.status == status)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_factor(self, factor_id: int) -> Optional[Factor]:
        """获取单个因子

        Args:
            factor_id: 因子 ID

        Returns:
            Factor 对象或 None
        """
        result = await self.db.execute(select(Factor).where(Factor.id == factor_id))
        return result.scalars().first()

    async def get_factor_by_code(self, code: str) -> Optional[Factor]:
        """根据代码获取因子

        Args:
            code: 因子代码

        Returns:
            Factor 对象或 None
        """
        result = await self.db.execute(select(Factor).where(Factor.code == code))
        return result.scalars().first()

    async def create_factor(self, data: FactorCreate) -> Factor:
        """新增因子

        Args:
            data: 因子创建数据

        Returns:
            创建的 Factor 对象

        Raises:
            ValueError: 因子代码已存在
        """
        existing = await self.get_factor_by_code(data.code)
        if existing:
            raise ValueError(f"因子代码 {data.code} 已存在")

        params_str = json.dumps(data.params) if data.params else None

        factor = Factor(
            name=data.name,
            code=data.code,
            data_field=data.data_field,
            weight=data.weight,
            direction=data.direction,
            params=params_str,
            status="active",
            sort_order=data.sort_order,
        )
        self.db.add(factor)
        await self.db.commit()
        await self.db.refresh(factor)
        return factor

    async def update_factor(self, factor_id: int, data: FactorUpdate) -> Optional[Factor]:
        """更新因子信息

        Args:
            factor_id: 因子 ID
            data: 更新数据

        Returns:
            更新后的 Factor 对象或 None
        """
        factor = await self.get_factor(factor_id)
        if factor is None:
            return None

        update_data = data.model_dump(exclude_unset=True)

        # 特殊处理 params：dict → JSON 字符串
        if "params" in update_data and update_data["params"] is not None:
            update_data["params"] = json.dumps(update_data["params"])

        for key, value in update_data.items():
            setattr(factor, key, value)

        await self.db.commit()
        await self.db.refresh(factor)
        return factor

    async def delete_factor(self, factor_id: int) -> bool:
        """删除因子

        Args:
            factor_id: 因子 ID

        Returns:
            是否删除成功
        """
        factor = await self.get_factor(factor_id)
        if factor is None:
            return False

        await self.db.delete(factor)
        await self.db.commit()
        return True

    async def get_total_weight(self, status: Optional[str] = "active") -> float:
        """获取活跃因子的总权重

        Args:
            status: 状态筛选，默认 "active"

        Returns:
            总权重
        """
        stmt = select(func.sum(Factor.weight))
        if status:
            stmt = stmt.where(Factor.status == status)
        result = await self.db.execute(stmt)
        total = result.scalar()
        return float(total) if total else 0.0

    async def get_active_factors_as_dicts(self) -> list[dict]:
        """获取所有活跃因子的字典列表（供 FactorEngine 使用）

        Returns:
            因子字典列表
        """
        factors = await self.list_factors(status="active")
        return [
            {
                "id": f.id,
                "code": f.code,
                "name": f.name,
                "weight": f.weight,
                "direction": f.direction,
                "params": f.params or "{}",
                "data_field": f.data_field,
            }
            for f in factors
        ]
