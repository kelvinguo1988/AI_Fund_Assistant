"""时区工具

2026-08-28 修复背景：python:3.9-slim 镜像无 tzdata，TZ=Asia/Shanghai 环境变量
不生效，datetime.now() 实际返回 UTC——北京时间 11:58 的写入被记成 03:58。
全项目时间戳写入统一走本模块，勿再直接使用 datetime.now()。
"""

from datetime import datetime, timedelta, timezone

_BEIJING = timezone(timedelta(hours=8))


def now_beijing() -> datetime:
    """北京时间墙钟（naive）

    返回 naive 与 SQLite DateTime 列存储行为保持一致（读写对齐）。
    """
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    except Exception:
        return datetime.now(_BEIJING).replace(tzinfo=None)
