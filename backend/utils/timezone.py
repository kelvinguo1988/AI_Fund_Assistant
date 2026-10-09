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
    return _now_beijing_dt()


def _now_beijing_dt() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    except Exception:
        return datetime.now(_BEIJING).replace(tzinfo=None)


def format_beijing(ts: float) -> str:
    """epoch 秒 → 北京时间串

    缓存里存的是 time.time()，展示要落回北京时：容器 TZ=UTC 时
    `time.strftime(..., time.localtime(ts))` 给出的是 UTC，比界面其他时间戳早 8 小时。
    """
    return datetime.fromtimestamp(ts, _BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def beijing_today():
    """北京日期（date 对象）

    凡"今天"参与交易日闸门 / analysis_date 落库 / 回测 cutoff 都必须用它：
    容器 TZ=UTC 时 `date.today()` 在北京 18:00 之后仍返回昨天，与
    now_beijing() 写的时间戳、uq_fund_date 唯一键、review/backtest 的北京日
    基准错位（2026-09-29 审查 P1）。
    """
    return _now_beijing_dt().date()
