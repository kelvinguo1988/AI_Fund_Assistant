"""基金数据缓存服务 — 实现"先展示缓存，后台刷新"模式"""

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.fund_data_cache import FundDataCache
from backend.services.fund_detail_service import fetch_period_returns

logger = logging.getLogger(__name__)

CACHE_KEY_PERIOD_RETURNS = "period_returns"
CACHE_KEY_REFRESH_TIME = "detail_last_refreshed"


async def get_cached_period_returns(
    db: AsyncSession,
) -> tuple[list[dict], Optional[str]]:
    """获取缓存的阶段涨幅数据

    Returns:
        (data_list, updated_at_iso) — 无缓存时返回 ([], None)
    """
    stmt = select(FundDataCache).where(
        FundDataCache.cache_key == CACHE_KEY_PERIOD_RETURNS
    )
    result = await db.execute(stmt)
    cached = result.scalars().first()
    if cached is None:
        return [], None

    try:
        data = json.loads(cached.data_json)
        updated_at = cached.updated_at.isoformat() if cached.updated_at else None
        return data, updated_at
    except (json.JSONDecodeError, TypeError):
        return [], None


async def get_last_refreshed_time(db: AsyncSession) -> Optional[str]:
    """获取上次刷新时间"""
    stmt = select(FundDataCache).where(
        FundDataCache.cache_key == CACHE_KEY_REFRESH_TIME
    )
    result = await db.execute(stmt)
    cached = result.scalars().first()
    if cached:
        return cached.updated_at.isoformat() if cached.updated_at else None
    return None


async def update_period_returns_cache(
    db: AsyncSession,
    codes: list[str],
    name_map: dict[str, str],
) -> list[dict]:
    """抓取阶段涨幅并更新缓存

    Returns:
        更新后的数据列表
    """
    returns = await fetch_period_returns(codes)
    data = [
        {
            "code": code,
            "name": name_map.get(code, ""),
            "return_1m": returns.get(code, {}).get("return_1m"),
            "return_3m": returns.get(code, {}).get("return_3m"),
            "return_6m": returns.get(code, {}).get("return_6m"),
            "return_1y": returns.get(code, {}).get("return_1y"),
        }
        for code in codes
    ]

    now = datetime.now()
    # Upsert
    stmt = select(FundDataCache).where(
        FundDataCache.cache_key == CACHE_KEY_PERIOD_RETURNS
    )
    result = await db.execute(stmt)
    cached = result.scalars().first()
    if cached:
        cached.data_json = json.dumps(data, ensure_ascii=False)
        cached.updated_at = now
    else:
        db.add(FundDataCache(
            cache_key=CACHE_KEY_PERIOD_RETURNS,
            data_json=json.dumps(data, ensure_ascii=False),
            updated_at=now,
        ))

    # Update refresh timestamp
    ts_stmt = select(FundDataCache).where(
        FundDataCache.cache_key == CACHE_KEY_REFRESH_TIME
    )
    ts_result = await db.execute(ts_stmt)
    ts_cache = ts_result.scalars().first()
    if ts_cache:
        ts_cache.updated_at = now
    else:
        db.add(FundDataCache(
            cache_key=CACHE_KEY_REFRESH_TIME,
            data_json='"ok"',
            updated_at=now,
        ))

    await db.commit()
    return data


async def get_cached_json(db: AsyncSession, cache_key: str) -> tuple[Any, Optional[str]]:
    """通用缓存读取 — 返回 (data, updated_at_iso) 或 (None, None)"""
    stmt = select(FundDataCache).where(FundDataCache.cache_key == cache_key)
    result = await db.execute(stmt)
    cached = result.scalars().first()
    if cached is None:
        return None, None
    try:
        data = json.loads(cached.data_json)
        updated_at = cached.updated_at.isoformat() if cached.updated_at else None
        return data, updated_at
    except (json.JSONDecodeError, TypeError):
        return None, None


async def set_cached_json(db: AsyncSession, cache_key: str, data: Any) -> str:
    """通用缓存写入 — 返回 updated_at ISO 字符串"""
    now = datetime.now()
    json_str = json.dumps(data, ensure_ascii=False, default=str)
    stmt = select(FundDataCache).where(FundDataCache.cache_key == cache_key)
    result = await db.execute(stmt)
    cached = result.scalars().first()
    if cached:
        cached.data_json = json_str
        cached.updated_at = now
    else:
        db.add(FundDataCache(cache_key=cache_key, data_json=json_str, updated_at=now))
    await db.commit()
    return now.isoformat()
