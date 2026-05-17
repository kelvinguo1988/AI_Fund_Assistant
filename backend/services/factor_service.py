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
        """获取因子列表（按 sort_order 排序）"""
        stmt = select(Factor).order_by(Factor.sort_order, Factor.id)
        if status:
            stmt = stmt.where(Factor.status == status)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_factor(self, factor_id: int) -> Optional[Factor]:
        """获取单个因子"""
        result = await self.db.execute(select(Factor).where(Factor.id == factor_id))
        return result.scalars().first()

    async def get_factor_by_code(self, code: str) -> Optional[Factor]:
        """根据代码获取因子"""
        result = await self.db.execute(select(Factor).where(Factor.code == code))
        return result.scalars().first()

    async def create_factor(self, data: FactorCreate) -> Factor:
        """新增因子"""
        existing = await self.get_factor_by_code(data.code)
        if existing:
            raise ValueError(f"因子代码 {data.code} 已存在")

        factor = Factor(
            name=data.name,
            code=data.code,
            data_field=data.data_field,
            data_fields=json.dumps(data.data_fields, ensure_ascii=False) if data.data_fields else None,
            weight=data.weight,
            direction=data.direction,
            params=json.dumps(data.params) if data.params else None,
            formula=data.formula,
            window=data.window,
            window_unit=data.window_unit,
            signal_rules=json.dumps(data.signal_rules, ensure_ascii=False) if data.signal_rules else None,
            normalization=data.normalization or "none",
            normalization_config=json.dumps(data.normalization_config) if data.normalization_config else None,
            status="active",
            sort_order=data.sort_order,
        )
        self.db.add(factor)
        await self.db.commit()
        await self.db.refresh(factor)
        return factor

    async def update_factor(self, factor_id: int, data: FactorUpdate) -> Optional[Factor]:
        """更新因子信息"""
        factor = await self.get_factor(factor_id)
        if factor is None:
            return None

        update_data = data.model_dump(exclude_unset=True)

        # JSON 字段序列化
        json_fields = {
            "params": "params",
            "data_fields": "data_fields",
            "signal_rules": "signal_rules",
            "normalization_config": "normalization_config",
        }
        for field_name in json_fields:
            if field_name in update_data and update_data[field_name] is not None:
                update_data[field_name] = json.dumps(
                    update_data[field_name], ensure_ascii=False
                )

        for key, value in update_data.items():
            setattr(factor, key, value)

        await self.db.commit()
        await self.db.refresh(factor)
        return factor

    async def delete_factor(self, factor_id: int) -> bool:
        """删除因子"""
        factor = await self.get_factor(factor_id)
        if factor is None:
            return False
        await self.db.delete(factor)
        await self.db.commit()
        return True

    async def get_total_weight(self, status: Optional[str] = "active") -> float:
        """获取活跃因子的总权重"""
        stmt = select(func.sum(Factor.weight))
        if status:
            stmt = stmt.where(Factor.status == status)
        result = await self.db.execute(stmt)
        total = result.scalar()
        return float(total) if total else 0.0

    async def get_active_factors_as_dicts(self) -> list[dict]:
        """获取所有活跃因子的字典列表（供 FactorEngine 使用）"""
        factors = await self.list_factors(status="active")
        return [
            {
                "id": f.id,
                "code": f.code,
                "name": f.name or f.code,
                "weight": f.weight,
                "direction": f.direction,
                "params": f.params or "{}",
                "data_field": f.data_field,
                "data_fields": json.loads(f.data_fields) if f.data_fields else None,
                "formula": f.formula,
                "window": f.window,
                "window_unit": f.window_unit,
                "signal_rules": json.loads(f.signal_rules) if f.signal_rules else [],
                "normalization": f.normalization or "none",
                "normalization_config": json.loads(f.normalization_config) if f.normalization_config else None,
            }
            for f in factors
        ]
