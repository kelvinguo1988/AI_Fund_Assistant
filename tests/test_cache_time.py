"""缓存时间戳时区回归测试

2026-08-28 修复：python:3.9-slim 无 tzdata，TZ=Asia/Shanghai 不生效，
datetime.now() 返回 UTC 被前端贴"北京时间"标签（11:58 刷新显示 03:59）。
"""

import sys, os
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.fund_cache_service import _now_beijing


class TestNowBeijing:
    def test_returns_naive(self):
        now = _now_beijing()
        assert now.tzinfo is None, "必须返回 naive datetime（与 SQLite 存储一致）"

    def test_wall_clock_is_beijing(self):
        """北京时间墙钟应比 UTC 墙钟快 8 小时（±1 分钟容差）"""
        bj = _now_beijing()
        utc_wall = datetime.now(timezone.utc).replace(tzinfo=None)
        delta = bj - utc_wall
        expected = timedelta(hours=8)
        assert abs(delta - expected) < timedelta(minutes=1), (
            f"北京时间墙钟与 UTC 差 {delta}，期望 8:00±1min"
        )

    def test_isoformat_parseable_by_frontend_regex(self):
        """isoformat 输出应能被前端 naive 分支的正则匹配"""
        import re
        iso = _now_beijing().isoformat()
        assert re.search(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})", iso), iso
