"""基金数据缓存服务 — 实现"先展示缓存，后台刷新"模式"""

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.fund_data_cache import FundDataCache
from backend.utils.timezone import now_beijing as _now_beijing
from backend.services.fund_detail_service import fetch_all_js_texts, _parse_period_returns, _parse_extended_data

logger = logging.getLogger(__name__)

CACHE_KEY_PERIOD_RETURNS = "period_returns"
CACHE_KEY_REFRESH_TIME = "detail_last_refreshed"
CACHE_KEY_EXTENDED_DETAIL = "extended_detail"
CACHE_KEY_MARKET_SUMMARY = "market_summary"

# 行情快照的五个板块：任一有值才算有效帧
_MARKET_SECTIONS = ("market_flow", "sector_flow", "hsgt_flow", "adv_decline", "turnover")


def build_market_summary_payload(
    market_flow, sector_flow_list, hsgt_flow, adv_decline, turnover
) -> dict:
    """五板块 → 落库 dict（三处写入点共用，防口径漂移）"""
    return {
        "market_flow": market_flow.model_dump() if market_flow else None,
        "sector_flow": [s.model_dump() for s in sector_flow_list],
        "hsgt_flow": hsgt_flow.model_dump() if hsgt_flow else None,
        "adv_decline": adv_decline.model_dump() if adv_decline else None,
        "turnover": turnover.model_dump() if turnover else None,
    }


def market_cache_has_data(cache) -> bool:
    """缓存帧是否含任一板块

    全 None 的帧不是"合法的空行情"，而是五路取数同时失败的痕迹，不能当有效
    缓存展示。
    """
    return bool(cache) and any(cache.get(k) for k in _MARKET_SECTIONS)


async def store_market_summary(db: AsyncSession, cache_data: dict) -> Optional[str]:
    """行情板块落库；整帧全空时保留旧缓存与旧时间戳

    旧实现无条件 set_cached_json：五个源同时被限的那一次会留下"全 None + 刚刚
    更新"的行，而读取路径只判断行是否存在 —— 仪表盘此后永远空白、时间戳照旧
    推进，真实故障被伪装成"数据已最新"（2026-10-01 审查 P2）。
    """
    if not market_cache_has_data(cache_data):
        _old, old_at = await get_cached_json(db, CACHE_KEY_MARKET_SUMMARY)
        logger.warning("行情五板块全部为空，保留旧缓存、不推进更新时间")
        return old_at
    return await set_cached_json(db, CACHE_KEY_MARKET_SUMMARY, cache_data)


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


async def _upsert_cache_row(
    db: AsyncSession, cache_key: str, data_json: str, now: datetime
) -> None:
    """原子 upsert —— 替代 select-then-insert。

    并发请求下两个会话同时判"无行"再各自 INSERT 会撞 cache_key 唯一约束
    （IntegrityError → 500），交给 SQLite ON CONFLICT 做原子写入。
    调用方负责 commit。
    """
    stmt = (
        sqlite_insert(FundDataCache)
        .values(cache_key=cache_key, data_json=data_json, updated_at=now)
        .on_conflict_do_update(
            index_elements=["cache_key"],
            set_={"data_json": data_json, "updated_at": now},
        )
    )
    await db.execute(stmt)


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

    # 本轮没拉到 JS 文本的代码沿用上一轮的数值，而不是写一串 None：
    # 全量 60 只里挂 3 只时，旧实现会把那 3 只在缓存里清空，而接口又是
    # "先展示缓存"，前端表现成阶段涨幅随机消失（2026-10-01 审查 P2）
    prev_rows, _prev_at = await get_cached_period_returns(db)
    prev_map = {str(r.get("code")): r for r in prev_rows if isinstance(r, dict)}

    def _cell(code: str, key: str):
        if code in returns:
            return returns[code].get(key)
        return prev_map.get(code, {}).get(key)

    data = [
        {
            "code": code,
            "name": name_map.get(code, "") or prev_map.get(code, {}).get("name", ""),
            "return_1m": _cell(code, "return_1m"),
            "return_3m": _cell(code, "return_3m"),
            "return_6m": _cell(code, "return_6m"),
            "return_1y": _cell(code, "return_1y"),
        }
        for code in codes
    ]

    # 全部代码都没取到 JS 文本时不能把刷新时间推进：否则"刷新失败"在页面上
    # 显示成"刚刚刷新过"，脏数据会被当成新鲜缓存一直展示（2026-09-29 审查 P1）
    hit_codes = [c for c in codes if js_texts.get(c)]
    if hit_codes:
        now = _now_beijing()
        await _upsert_cache_row(
            db, CACHE_KEY_PERIOD_RETURNS, json.dumps(data, ensure_ascii=False), now
        )
        await _upsert_cache_row(db, CACHE_KEY_REFRESH_TIME, '"ok"', now)
        await db.commit()
    else:
        logger.warning(
            "阶段涨幅全部拉取失败（%d 只），保留旧缓存且不更新刷新时间", len(codes)
        )
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

    # 与上一轮按代码合并后落库：本函数只在"部分代码"刷新时被调用，整表覆盖
    # 会让未参与本轮刷新的基金详情字段（累计净值/波动率）凭空消失
    prev, _at = await get_cached_json(db, CACHE_KEY_EXTENDED_DETAIL)
    if isinstance(prev, dict):
        await set_cached_json(db, CACHE_KEY_EXTENDED_DETAIL, {**prev, **all_data})
    else:
        await set_cached_json(db, CACHE_KEY_EXTENDED_DETAIL, all_data)
    # 返回值仍是本轮解析结果：下游拿它落 fund_quarterly，不该被历史条目放大
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
    await _upsert_cache_row(db, cache_key, json_str, now)
    await db.commit()
    return now.isoformat()
