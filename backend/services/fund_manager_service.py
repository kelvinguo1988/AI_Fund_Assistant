"""基金经理服务 — 从 AKShare fund_manager_em 获取并存入数据库

数据来源：AKShare 封装的东方财富全量基金经理数据。
全量查询较慢（~10s），使用服务级全局缓存避免重复查询。
"""

import asyncio
import logging
from typing import Optional

import akshare as ak
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.fund_manager_record import FundManagerRecord

logger = logging.getLogger(__name__)

# 全量经理数据缓存（服务启动后缓存一次，避免重复 10s 查询）
_manager_cache: Optional[list[dict]] = None
_cache_lock = asyncio.Lock()


async def _get_all_managers() -> list[dict]:
    """获取全量基金经理数据（带缓存）"""
    global _manager_cache
    if _manager_cache is not None:
        return _manager_cache

    async with _cache_lock:
        if _manager_cache is not None:
            return _manager_cache
        try:
            df = await asyncio.to_thread(ak.fund_manager_em)
            if df is not None and not df.empty:
                records = df.to_dict(orient="records")
                _manager_cache = records
                logger.info("基金经理缓存已加载: %d 条", len(records))
                return records
        except Exception as e:
            logger.warning("获取全量基金经理失败: %s", e)
        return []


async def refresh_managers(db: AsyncSession, fund_id: int, fund_code: str) -> list[FundManagerRecord]:
    """刷新指定基金的经理信息（从全量缓存中匹配）"""
    all_managers = await _get_all_managers()
    if not all_managers:
        return []

    matched = []
    for m in all_managers:
        codes = str(m.get("现任基金代码", "")).split(",")
        codes = [c.strip() for c in codes if c.strip()]
        if fund_code in codes:
            matched.append(m)

    for m in matched:
        name = str(m.get("姓名", "")).strip()
        if not name:
            continue

        stmt = select(FundManagerRecord).where(
            FundManagerRecord.fund_id == fund_id,
            FundManagerRecord.manager_name == name,
        )
        existing = (await db.execute(stmt)).scalars().first()
        if existing:
            continue

        record = FundManagerRecord(
            fund_id=fund_id,
            manager_name=name,
            company=str(m.get("所属公司", "")) or None,
            tenure_days=_to_int(m.get("累计从业时间")),
            asset_scale=_to_float(m.get("现任基金资产总规模")),
            best_return=_to_float(m.get("现任基金最佳回报")),
            managed_codes=str(m.get("现任基金代码", "")),
        )
        db.add(record)

    await db.commit()

    stmt = select(FundManagerRecord).where(FundManagerRecord.fund_id == fund_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_current_managers(db: AsyncSession, fund_id: int) -> list[FundManagerRecord]:
    """获取指定基金的当前经理记录"""
    stmt = select(FundManagerRecord).where(FundManagerRecord.fund_id == fund_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def compute_manager_changes(
    db: AsyncSession, fund_id: int
) -> Optional[dict]:
    """计算基金经理变更情况

    Returns:
        {
            "current": [{"manager_name": "...", ...}],
            "history": [{"manager_name": "...", ...}],
            "changed": True/False,
        }
    """
    stmt = select(FundManagerRecord).where(
        FundManagerRecord.fund_id == fund_id
    ).order_by(FundManagerRecord.id)
    result = await db.execute(stmt)
    all_records = list(result.scalars().all())

    if not all_records:
        return None

    unique_names = list(dict.fromkeys(r.manager_name for r in all_records))
    current_names = unique_names[-1:]
    prev_names = unique_names[:-1]

    current = [r for r in all_records if r.manager_name in current_names]
    history = [r for r in all_records if r.manager_name not in current_names]

    return {
        "current": [{
            "manager_name": r.manager_name,
            "company": r.company,
            "tenure_days": r.tenure_days,
            "asset_scale": r.asset_scale,
            "best_return": r.best_return,
        } for r in current],
        "history": [{
            "manager_name": r.manager_name,
            "company": r.company,
            "tenure_days": r.tenure_days,
            "asset_scale": r.asset_scale,
        } for r in history],
        "changed": len(prev_names) > 0,
    }


def _to_float(v) -> Optional[float]:
    if v is None or v == "" or v == "None" or v == "nan":
        return None
    try:
        return round(float(v), 2)
    except (ValueError, TypeError):
        return None


def _to_int(v) -> Optional[int]:
    if v is None or v == "" or v == "None" or v == "nan":
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None
