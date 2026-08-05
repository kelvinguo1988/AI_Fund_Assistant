from __future__ import annotations
"""调休/节假日日历同步服务

数据源(可配置, 默认 NateScarlet/holiday-cn):
  https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json
该数据集由社区基于国务院放假安排(gov.cn)整理, papers 字段可溯源到官方原文。

同步策略:
  - 自动同步: 每日在 holiday_auto_sync_time 检查一次, 若 holiday_auto_sync_enabled=true
    则同步当年+次年, 成功后自动置 enabled=false(只同步一次)。
  - 手动同步: 后台触发 POST /api/holiday/sync, 不受 enabled 限制。
"""

import asyncio
import logging
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 默认数据源地址（{year} 占位符在抓取时被替换为年份）
DEFAULT_HOLIDAY_SYNC_URL = "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"

# system_config 配置键
CFG_URL = "holiday_sync_url"
CFG_TIME = "holiday_auto_sync_time"
CFG_ENABLED = "holiday_auto_sync_enabled"
CFG_LAST_SYNC = "holiday_last_sync_at"


# ── 配置读写辅助（懒导入避免循环依赖）────────────────────────────────
async def _get_cfg(session, key: str, default: str) -> str:
    from sqlalchemy import select

    from backend.models.system_config import SystemConfig

    row = (await session.execute(
        select(SystemConfig).where(SystemConfig.config_key == key)
    )).scalars().first()
    return row.config_value if row else default


async def _set_cfg(session, key: str, value: str) -> None:
    from sqlalchemy import select

    from backend.models.system_config import SystemConfig

    row = (await session.execute(
        select(SystemConfig).where(SystemConfig.config_key == key)
    )).scalars().first()
    if row:
        row.config_value = value
        row.updated_at = datetime.now()
    else:
        session.add(SystemConfig(
            config_key=key, config_value=value, updated_at=datetime.now()
        ))


def parse_holiday_source(data: dict) -> list[tuple[str, bool, str]]:
    """解析数据源 JSON 为 (date_str, is_off_day, name) 列表。

    兼容两种格式:
      - NateScarlet: {"days":[{"date":"2026-05-04","name":"劳动节","isOffDay":true}]}
      - timor.tech:  {"holiday":{"05-04":{"holiday":true,"name":"劳动节","date":"2026-05-04"}}}
    is_off_day=True 表示休息日(休市); False 表示调休补班工作日(开市)。
    """
    out: list[tuple[str, bool, str]] = []
    if isinstance(data, dict) and "days" in data:
        for d in data.get("days", []):
            ds = d.get("date")
            if not ds:
                continue
            out.append((str(ds), bool(d.get("isOffDay", False)), d.get("name") or ""))
    elif isinstance(data, dict) and "holiday" in data:
        for _mmdd, d in data["holiday"].items():
            ds = d.get("date")
            if not ds:
                continue
            out.append((str(ds), bool(d.get("holiday", False)), d.get("name") or ""))
    return out


async def fetch_holiday_json(year: int, url_template: str) -> dict:
    """抓取并解析某年节假日 JSON。url_template 中的 {year} 会被替换。"""
    import requests

    url = url_template.replace("{year}", str(year))
    # 用 asyncio.to_thread 包裹同步 requests, 复用东财补丁注入的默认超时
    resp = await asyncio.to_thread(requests.get, url, timeout=20)
    resp.raise_for_status()
    return resp.json()


async def sync_holiday_calendar(
    session, year: Optional[int] = None, url_template: Optional[str] = None
) -> dict:
    """同步指定年份(默认当年+次年)的调休日历到 holiday_calendar 表。

    返回: {"synced_years":[...], "upserted":N, "errors":[...]}
    """
    from sqlalchemy import select

    from backend.models.holiday_calendar import HolidayCalendar

    if url_template is None:
        url_template = await _get_cfg(session, CFG_URL, DEFAULT_HOLIDAY_SYNC_URL)

    years = [year] if year else [date.today().year, date.today().year + 1]
    upserted = 0
    synced_years: list[int] = []
    errors: list[str] = []

    for y in years:
        try:
            data = await fetch_holiday_json(y, url_template)
            records = parse_holiday_source(data)
            if not records:
                errors.append(f"{y}: 解析为空")
                continue
            for ds, is_off, name in records:
                if not ds.startswith(str(y)):
                    continue
                existing = (await session.execute(
                    select(HolidayCalendar).where(HolidayCalendar.holiday_date == ds)
                )).scalars().first()
                if existing:
                    existing.is_off_day = is_off
                    existing.holiday_name = name
                    existing.source = url_template
                    existing.synced_at = datetime.now()
                else:
                    session.add(HolidayCalendar(
                        holiday_date=ds, is_off_day=is_off, holiday_name=name,
                        source=url_template, synced_at=datetime.now(),
                    ))
                upserted += 1
            synced_years.append(y)
            logger.info(f"调休日历同步完成 {y}: {len(records)} 条")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{y}: {e}")
            logger.warning(f"调休日历同步失败 {y}: {e}")

    if upserted:
        await session.commit()
    return {"synced_years": synced_years, "upserted": upserted, "errors": errors}


async def auto_sync_if_enabled(session) -> dict:
    """自动同步入口: 仅当 holiday_auto_sync_enabled=true 时执行, 成功后置 false。"""
    enabled = (await _get_cfg(session, CFG_ENABLED, "true")).lower() == "true"
    if not enabled:
        return {"skipped": True, "reason": "auto_sync_disabled"}

    summary = await sync_holiday_calendar(session)
    if summary.get("synced_years"):
        # 同步成功 → 关闭自动同步（只同步一次），并记录时间
        await _set_cfg(session, CFG_ENABLED, "false")
        await _set_cfg(session, CFG_LAST_SYNC, datetime.now().isoformat(timespec="seconds"))
        await session.commit()
        summary["auto_disabled"] = True
    else:
        # 同步失败 → 保留 enabled=true，次日重试
        summary["auto_disabled"] = False
    return summary
