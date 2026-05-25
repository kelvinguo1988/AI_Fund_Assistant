"""基金业务逻辑 — CRUD + 状态管理"""

import asyncio
import json
import logging
import re
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.data_sources.base import guess_fund_type as _guess_fund_type
from backend.models.fund import Fund
from backend.schemas.fund import FundCreate, FundUpdate
from backend.services.fund_theme_service import fetch_related_themes

logger = logging.getLogger(__name__)


def _merge_tags(existing_tags: Optional[str], new_themes: list[str]) -> Optional[str]:
    """将自动抓取的主题合并到已有标签中，去重"""
    if not new_themes:
        return existing_tags
    existing = set()
    if existing_tags:
        existing = {t.strip() for t in existing_tags.split(",") if t.strip()}
    for theme in new_themes:
        existing.add(theme.strip())
    return ",".join(sorted(existing)) if existing else None


# guess_fund_type 从 backend.data_sources.base 导入，与数据源层共享同一份路由规则


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

        创建后自动从天天基金抓取相关主题并合并到标签中。

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

        # 仅当未手动填写标签时，自动抓取天天基金相关主题
        if not fund.tags:
            themes = await asyncio.to_thread(fetch_related_themes, fund.code)
            merged = _merge_tags(fund.tags, themes)
            if merged != fund.tags:
                fund.tags = merged
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

    async def batch_import(self, items: list[dict]) -> dict:
        """批量导入基金

        已有代码跳过不重复创建，其余自动识别类型并创建。
        导入完成后会逐个抓取天天基金相关主题并合并到标签中。

        Args:
            items: [{"code": "000001", "name": "示例基金", "tags": "宽基"}]

        Returns:
            {"total": 3, "created": 2, "skipped": ["000001"], "errors": []}
        """
        created = 0
        skipped: list[str] = []
        errors: list[str] = []
        created_codes: list[str] = []

        logger.info("批量导入 %d 个基金", len(items))
        for item in items:
            code = str(item.get("code", "")).strip()
            name = str(item.get("name", "")).strip()
            tags = str(item.get("tags", "")).strip() or None

            if not code or not name:
                errors.append(f"代码或名称为空: {item}")
                continue
            if not re.match(r"^\d{6}$", code):
                errors.append(f"代码格式无效: {code}")
                continue

            try:
                existing = await self.get_fund_by_code(code)
                if existing:
                    skipped.append(code)
                    continue

                fund = Fund(
                    code=code,
                    name=name,
                    fund_type=_guess_fund_type(code),
                    tags=tags,
                    status="active",
                )
                self.db.add(fund)
                await self.db.flush()
                created_codes.append(code)
                created += 1
            except Exception as e:
                errors.append(f"{code}: {e}")
                continue

        await self.db.commit()

        # 对新创建的基金抓取相关主题（仅当未手动填写标签时）
        if created_codes:
            logger.info("开始抓取 %d 个新基金的相关主题", len(created_codes))
            for code in created_codes:
                try:
                    fund_obj = await self.get_fund_by_code(code)
                    if fund_obj and not fund_obj.tags:
                        themes = await asyncio.to_thread(fetch_related_themes, code)
                        if themes:
                            merged = _merge_tags(None, themes)
                            if merged != fund_obj.tags:
                                fund_obj.tags = merged
                except Exception as e:
                    logger.warning("抓取基金 %s 主题时异常: %s", code, e)
            await self.db.commit()

        logger.info("批量导入完成: total=%d created=%d skipped=%d errors=%d",
                     len(items), created, len(skipped), len(errors))
        if errors:
            logger.warning("导入失败项: %s", errors)
        return {
            "total": len(items),
            "created": created,
            "skipped": skipped,
            "errors": errors,
        }

    async def refresh_themes(self, fund_id: int) -> Optional[Fund]:
        """刷新指定基金的相关主题

        Args:
            fund_id: 基金 ID

        Returns:
            更新后的 Fund 对象或 None
        """
        fund = await self.get_fund(fund_id)
        if fund is None:
            return None

        themes = await asyncio.to_thread(fetch_related_themes, fund.code)
        merged = _merge_tags(fund.tags, themes)
        if merged != fund.tags:
            fund.tags = merged
            await self.db.commit()
            await self.db.refresh(fund)
        return fund

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
