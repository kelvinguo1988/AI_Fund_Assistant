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
