from __future__ import annotations
"""交易日历 — chinese_calendar 封装"""

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)


def is_trading_day(target_date: date | None = None) -> bool:
    """判断是否为交易日

    优先使用 chinese_calendar 库判断，如库不可用则回退到简单规则：
    周一至周五为工作日（不考虑法定节假日调休）。

    Args:
        target_date: 目标日期，默认为今天

    Returns:
        True 表示交易日
    """
    if target_date is None:
        target_date = date.today()

    try:
        import chinese_calendar  # type: ignore
        return chinese_calendar.is_workday(target_date)
    except ImportError:
        logger.warning(
            "chinese_calendar 库不可用，回退到简单工作日判断（周一至周五）"
        )
        return target_date.weekday() < 5
    except Exception as e:
        logger.warning(f"chinese_calendar 判断异常: {e}，回退到简单工作日判断")
        return target_date.weekday() < 5


async def is_a_share_trading_day_async(session, target_date: date | None = None) -> bool:
    """严格 A 股交易日判定（用于定时推送闸门）。

    规则：
      - 周末（周六/周日）一律休市 → 非交易日；
      - 若 holiday_calendar 表有该日记录且 is_off_day=True
        （法定节假日 或 调休休息日，如 2026-05-04 周一）→ 非交易日；
      - 表中无记录且为工作日 → 视为正常交易日；
      - 若表为空（尚未同步）→ 回退 chinese_calendar.is_workday 判断。

    说明：holiday_calendar 中 is_off_day=False 仅表示「调休补班工作日」
    （多为周末），周末已在第一步排除，故不会误判为开市。
    """
    if target_date is None:
        target_date = date.today()
    # 周末一律休市（含调休补班周六/周日，股市实际不开市）
    if target_date.weekday() >= 5:
        return False
    try:
        from sqlalchemy import select

        from backend.models.holiday_calendar import HolidayCalendar

        row = (
            await session.execute(
                select(HolidayCalendar).where(
                    HolidayCalendar.holiday_date == target_date.isoformat()
                )
            )
        ).scalars().first()
        if row is not None:
            return not row.is_off_day
        # 表无记录 → 回退 chinese_calendar（仅工作日且无特殊记录时视为交易日）
        return is_trading_day(target_date)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"holiday_calendar 查询失败，回退 chinese_calendar: {e}")
        return is_trading_day(target_date)


def get_latest_trading_day(target_date: date | None = None) -> date:
    """获取最近的交易日（向前回溯，最多 10 天）

    Args:
        target_date: 起始日期，默认为今天

    Returns:
        最近的交易日
    """
    if target_date is None:
        target_date = date.today()

    current = target_date
    for _ in range(10):
        if is_trading_day(current):
            return current
        current -= timedelta(days=1)

    # 回退 10 天仍未找到，返回当天
    logger.warning(f"回溯 10 天未找到交易日，返回 {target_date}")
    return target_date


def get_trading_days_between(start_date: date, end_date: date) -> list[date]:
    """获取两个日期之间的所有交易日

    Args:
        start_date: 开始日期
        end_date: 结束日期

    Returns:
        交易日列表
    """
    trading_days: list[date] = []
    current = start_date
    while current <= end_date:
        if is_trading_day(current):
            trading_days.append(current)
        current += timedelta(days=1)
    return trading_days
