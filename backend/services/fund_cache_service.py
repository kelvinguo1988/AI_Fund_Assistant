"""基金数据缓存服务 — 实现"先展示缓存，后台刷新"模式"""

import json
import logging
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.fund_data_cache import FundDataCache
from backend.services.fund_detail_service import fetch_all_js_texts, _parse_period_returns, _parse_extended_data

logger = logging.getLogger(__name__)


def _now_beijing() -> datetime:
    """北京时间墙钟（naive）

    2026-08-28 修复：python:3.9-slim 镜像无 tzdata，TZ=Asia/Shanghai 环境变量
    不生效，datetime.now() 实际返回 UTC——11:58 的刷新被记成 03:58 并被前端
    贴上"北京时间"标签。改用 ZoneInfo 显式取北京时间；PyPI tzdata 包兜底。
    返回 naive（SQLite DateTime 存储丢弃 tzinfo，读写保持一致）。
    """
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    except Exception:
        logger.warning("ZoneInfo 不可用，回退系统本地时间")
        return datetime.now()

CACHE_KEY_PERIOD_RETURNS = "period_returns"
CACHE_KEY_REFRESH_TIME = "detail_last_refreshed"
CACHE_KEY_EXTENDED_DETAIL = "extended_detail"


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
) -> tuple[list[dict], dict[str, str]]:
    """抓取阶段涨幅并更新缓存，同时返回原始 JS 文本

    Returns:
        (data_list, js_texts) — js_texts 可供扩展数据解析复用
    """
    js_texts = await fetch_all_js_texts(codes)
    returns: dict[str, dict] = {}
    for code, text in js_texts.items():
        returns[code] = _parse_period_returns(text)
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

    now = _now_beijing()
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
    return data, js_texts


async def update_extended_detail_cache(
    db: AsyncSession,
    js_texts: dict[str, str],
    name_map: dict[str, str],
) -> dict:
    """解析批量 JS 文本中的扩展数据并缓存

    Args:
        js_texts: {code: js_text} 来自 fetch_all_js_texts()
        name_map: {code: name}

    Returns:
        {code: {grand_total: ..., fluctuation_scale: ..., ...}}
    """
    all_data: dict[str, dict] = {}
    for code, js_text in js_texts.items():
        ext = _parse_extended_data(js_text)
        ext["name"] = name_map.get(code, "")
        all_data[code] = ext

    await set_cached_json(db, CACHE_KEY_EXTENDED_DETAIL, all_data)
    return all_data


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
    now = _now_beijing()
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
