from __future__ import annotations
"""调休/节假日日历同步路由

- GET    /api/holiday        查看已同步的调休日历（?year=2026）
- GET    /api/holiday/config 获取同步配置（地址/时间/开关/最近同步时间）
- PUT    /api/holiday/config 更新同步配置（地址/时间/开关）
- POST   /api/holiday/sync   后台手动同步（不受自动开关限制）
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.holiday_calendar import HolidayCalendar
from backend.models.system_config import SystemConfig
from backend.schemas.common import ApiResponse
from backend.services.holiday_sync_service import (
    DEFAULT_HOLIDAY_SYNC_URL,
    auto_sync_if_enabled,
    CFG_ENABLED,
    CFG_LAST_SYNC,
    CFG_TIME,
    CFG_URL,
    sync_holiday_calendar,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 请求体 Schema ────────────────────────────────────────────────────
class HolidayConfigUpdate(BaseModel):
    sync_url: Optional[str] = None
    auto_sync_time: Optional[str] = None
    auto_sync_enabled: Optional[bool] = None


class HolidaySyncRequest(BaseModel):
    year: Optional[int] = None
    url: Optional[str] = None


async def _get_cfg(session: AsyncSession, key: str, default: str) -> str:
    row = (await session.execute(
        select(SystemConfig).where(SystemConfig.config_key == key)
    )).scalars().first()
    return row.config_value if row else default


@router.get("", response_model=ApiResponse)
async def list_holidays(
    year: int = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """查看已同步的调休日历"""
    stmt = select(HolidayCalendar)
    if year:
        stmt = stmt.where(HolidayCalendar.holiday_date.like(f"{year}-%"))
    stmt = stmt.order_by(HolidayCalendar.holiday_date)
    rows = (await db.execute(stmt)).scalars().all()
    items = [
        {
            "date": r.holiday_date,
            "is_off_day": r.is_off_day,
            "name": r.holiday_name,
            "source": r.source,
            "synced_at": r.synced_at.isoformat() if r.synced_at else None,
        }
        for r in rows
    ]
    return ApiResponse(data={"year": year, "count": len(items), "items": items})


@router.get("/config", response_model=ApiResponse)
async def get_holiday_config(db: AsyncSession = Depends(get_db)):
    """获取调休同步配置"""
    cfg = {
        "sync_url": await _get_cfg(db, CFG_URL, DEFAULT_HOLIDAY_SYNC_URL),
        "auto_sync_time": await _get_cfg(db, CFG_TIME, "03:00"),
        "auto_sync_enabled": (await _get_cfg(db, CFG_ENABLED, "true")).lower() == "true",
        "last_sync_at": await _get_cfg(db, CFG_LAST_SYNC, ""),
    }
    return ApiResponse(data=cfg)


@router.put("/config", response_model=ApiResponse)
async def update_holiday_config(
    body: HolidayConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新调休同步配置（地址/时间/开关）"""
    updates: dict[str, str] = {}
    if body.sync_url is not None:
        updates[CFG_URL] = body.sync_url
    if body.auto_sync_time is not None:
        updates[CFG_TIME] = body.auto_sync_time
    if body.auto_sync_enabled is not None:
        updates[CFG_ENABLED] = str(body.auto_sync_enabled).lower()

    for key, val in updates.items():
        row = (await db.execute(
            select(SystemConfig).where(SystemConfig.config_key == key)
        )).scalars().first()
        if row:
            row.config_value = val
            row.updated_at = datetime.now()
        else:
            db.add(SystemConfig(config_key=key, config_value=val, updated_at=datetime.now()))
    await db.commit()
    return await get_holiday_config(db)


@router.post("/sync", response_model=ApiResponse)
async def manual_sync(
    body: Optional[HolidaySyncRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """后台手动同步调休日历（不受 auto_sync_enabled 限制）"""
    year = body.year if body else None
    url = body.url if body and body.url else None
    summary = await sync_holiday_calendar(db, year=year, url_template=url)
    if summary.get("synced_years"):
        now_iso = datetime.now().isoformat(timespec="seconds")
        row = (await db.execute(
            select(SystemConfig).where(SystemConfig.config_key == CFG_LAST_SYNC)
        )).scalars().first()
        if row:
            row.config_value = now_iso
        else:
            db.add(SystemConfig(config_key=CFG_LAST_SYNC, config_value=now_iso))
        await db.commit()
    return ApiResponse(data=summary)
