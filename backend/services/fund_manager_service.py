"""基金经理服务 — 从 AKShare fund_manager_em 获取并存入数据库

数据来源：AKShare 封装的东方财富全量基金经理数据。
全量查询较慢（~30s，9页数据），使用服务级全局缓存避免重复查询。
"""

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.fund_manager_record import FundManagerRecord
from backend.utils.concurrency import run_with_timeout
from backend.utils.timezone import now_beijing

logger = logging.getLogger(__name__)

# 全量经理数据缓存（服务启动后缓存一次，避免重复 ~30s 查询）
_manager_cache: Optional[list[dict]] = None
_cache_ts: float = 0.0
_cache_lock = asyncio.Lock()

# 缓存 TTL：进程常驻（NAS 上常连续数周不重启），永久缓存会让新任命/离任
# 在本进程内永远不可见（2026-09-29 审查 P2）。任免是慢变量，6 小时足够。
_MANAGER_CACHE_TTL: float = 6 * 3600.0

# 全量经理查询超时。fund_manager_em 返回全市场经理数据（~35000 条，9 页），
# 实测 ~28s（含反爬 sleep）。Docker 网络可能更慢，120s 留足缓冲。
_MANAGER_TIMEOUT: float = 120.0

# 失败冷却时间（秒）。首次查询失败后，在冷却期内不再重试，
# 避免 40-60 只基金 × 5 并发 = 200+ 次全量重试风暴。
_FAIL_COOLDOWN: float = 60.0
_last_fail_time: float = 0.0

# 判定"同一轮刷新写入"的时间窗口：一基金的多位共同在任经理在同一次 commit
# 内落库（行间毫秒级），取 1 分钟既容得下批内漂移，也不会把两轮刷新并成一批。
_SEEN_BATCH_WINDOW: timedelta = timedelta(minutes=1)


def _cache_fresh() -> bool:
    return _manager_cache is not None and (time.time() - _cache_ts) < _MANAGER_CACHE_TTL


async def _get_all_managers() -> list[dict]:
    """获取全量基金经理数据（带 TTL 缓存 + 失败冷却）"""
    global _manager_cache, _cache_ts, _last_fail_time

    if _cache_fresh():
        return _manager_cache

    # 失败冷却期内直接返回（有过期缓存就交过期缓存，别让整轮经理数据空掉）
    if _last_fail_time and time.time() - _last_fail_time < _FAIL_COOLDOWN:
        return _manager_cache or []

    async with _cache_lock:
        if _cache_fresh():
            return _manager_cache
        try:
            df = await run_with_timeout(
                ak.fund_manager_em,
                timeout=_MANAGER_TIMEOUT,
            )
            if df is not None and not df.empty:
                records = df.to_dict(orient="records")
                _manager_cache = records
                _cache_ts = time.time()
                logger.info("基金经理缓存已加载: %d 条", len(records))
                return records
        except asyncio.TimeoutError:
            logger.warning("获取全量基金经理超时（%ss），%ss 内不重试", _MANAGER_TIMEOUT, _FAIL_COOLDOWN)
        except Exception as e:
            logger.warning("获取全量基金经理失败: %s，%ss 内不重试", e, _FAIL_COOLDOWN)

        # 记录失败时间，冷却期内不重试
        _last_fail_time = time.time()
        if _manager_cache is not None:
            # 刷新失败但仍有旧快照：经理任免是慢变量，陈旧数据远好于空数据
            logger.warning(
                "经理全量数据刷新失败，继续使用 %.1f 小时前的缓存（%d 条）",
                (time.time() - _cache_ts) / 3600, len(_manager_cache),
            )
            return _manager_cache
        return []


async def refresh_managers(db: AsyncSession, fund_id: int, fund_code: str) -> list[FundManagerRecord]:
    """刷新指定基金的经理信息（从全量缓存中匹配）

    命中即视为"在任"：新名字插行，已存在的名字刷新统计值并把 last_seen_at
    推到本次（否则共同在任的经理会被 later 批次挤成"前任"）。
    """
    all_managers = await _get_all_managers()
    if not all_managers:
        return []

    matched = []
    for m in all_managers:
        codes = str(m.get("现任基金代码", "")).split(",")
        codes = [c.strip() for c in codes if c.strip()]
        if fund_code in codes:
            matched.append(m)

    now = now_beijing()
    for m in matched:
        name = str(m.get("姓名") or "").strip()
        if not name:
            continue
        # 数据源缺失时 str(None) 会得到字面量 "None"，先归一成空串
        company = str(m.get("所属公司") or "").strip()
        managed_codes = str(m.get("现任基金代码") or "").strip()

        stmt = select(FundManagerRecord).where(
            FundManagerRecord.fund_id == fund_id,
            FundManagerRecord.manager_name == name,
        )
        existing = (await db.execute(stmt)).scalars().first()
        if existing:
            # 在任经理的从业天数/规模/最佳回报随季度变化，逐次刷新
            # 2026-09-29 修复：原 `新值 or 旧值` 把合法的 0（新任经理累计从业 0 天、
            # 最佳回报 0.0%）当成缺失而一直留着旧值。_to_int/_to_float 在数据源缺字段
            # 时返回 None，故"缺字段"的判据是 None 而不是 falsy。
            existing.company = company or existing.company
            existing.tenure_days = _or_keep(_to_int(m.get("累计从业时间")), existing.tenure_days)
            existing.asset_scale = _or_keep(_to_float(m.get("现任基金资产总规模")), existing.asset_scale)
            existing.best_return = _or_keep(_to_float(m.get("现任基金最佳回报")), existing.best_return)
            existing.managed_codes = managed_codes or existing.managed_codes
            existing.last_seen_at = now
            continue

        db.add(FundManagerRecord(
            fund_id=fund_id,
            manager_name=name,
            company=company or None,
            tenure_days=_to_int(m.get("累计从业时间")),
            asset_scale=_to_float(m.get("现任基金资产总规模")),
            best_return=_to_float(m.get("现任基金最佳回报")),
            managed_codes=managed_codes or None,
            last_seen_at=now,
        ))

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

    现任 = 最近一轮刷新确认在任的全部经理（共同管理时有多位）；
    前任 = last_seen_at 停在更早轮次的记录。
    旧口径按插入顺序只把最后一个名字当现任，于是同批写入的共同在任经理
    全被误报成"经理变更"。

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

    def _seen(r: FundManagerRecord) -> datetime:
        return r.last_seen_at or r.created_at

    # 同一轮刷新内逐行时间相差毫秒级；两轮刷新至少间隔一个任务周期
    latest = max(_seen(r) for r in all_records)
    cutoff = latest - _SEEN_BATCH_WINDOW
    current = [r for r in all_records if _seen(r) >= cutoff]
    history = [r for r in all_records if _seen(r) < cutoff]

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
        "changed": len(history) > 0,
    }


def _to_float(v) -> Optional[float]:
    if v is None or v == "" or v == "None" or v == "nan":
        return None
    try:
        out = round(float(v), 2)
    except (ValueError, TypeError):
        return None
    # NaN 是 truthy，会穿过 `新值 or 旧值` 与 `is None` 两种判法把脏值写进库
    return None if math.isnan(out) or math.isinf(out) else out


def _to_int(v) -> Optional[int]:
    if v is None or v == "" or v == "None" or v == "nan":
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    return None if math.isnan(f) or math.isinf(f) else int(f)


def _or_keep(new, old):
    """数据源缺字段（_to_* 返回 None）时保留旧值；0 是合法观测值，不判缺失"""
    return old if new is None else new
