"""温度计取数重试与失败退避（2026-11-21 排查）

## 线上故障
仪表盘温度计停在 2026-11-13，其他面板（分析摘要 11-20）正常。
容器 11-15 启动后连续 6 天，每天 14:42 定时任务都失败，**一次都没成功过**。

## 根因（实测确认）
`ak.stock_index_pe_lg` 内部先用 `get_cookie_csrf` 抓乐咕首页拿 CSRF：
- 首页被限流返回 429/504 → `soup.find(...)` 得到 None → `.attrs` 崩
  （`AttributeError: 'NoneType' object has no attribute 'attrs'`）
- 或 API 返回错误体 → `KeyError: 'data'`
akshare **自身没有重试**，一次抖动即整体失败。

而失败冷却固定 120s，乐咕限流窗口比这更长 → 「失败 → 2 分钟后又打 → 又失败」
无限循环，缓存永远不更新。重启进程后立刻恢复（新进程首次请求未命中限流窗口），
这正是"重启就好"的假象。

## 修复
1. 取数内部退避重试 3 次（20s / 60s），覆盖短时限流
2. 失败冷却按连续次数指数退避（2min→4min→…→1h 上限）
3. payload 增加 data_lag_days / stale，前端可提示数据陈旧而非无声停摆
"""

import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.market_temperature_service import (  # noqa: E402
    MarketTemperatureService, _staleness_fields,
)


@pytest.fixture(autouse=True)
def _reset():
    MarketTemperatureService.reset_cache()
    yield
    MarketTemperatureService.reset_cache()


class TestExponentialBackoff:
    def test_cooldown_grows_with_failures(self):
        """连续失败时冷却时间必须递增，否则会无限重打限流窗口"""
        now = 1000.0
        seen = []
        for _ in range(5):
            MarketTemperatureService._mark_failure(now)
            seen.append(MarketTemperatureService._fail_until - now)
        assert seen == sorted(seen), f"冷却应递增，实际: {seen}"
        assert seen[0] == MarketTemperatureService._FAIL_COOLDOWN
        assert seen[1] == 2 * MarketTemperatureService._FAIL_COOLDOWN

    def test_cooldown_capped(self):
        """退避要有上限，不能无限增长"""
        now = 1000.0
        for _ in range(20):
            MarketTemperatureService._mark_failure(now)
        delta = MarketTemperatureService._fail_until - now
        assert delta <= MarketTemperatureService._FAIL_COOLDOWN_MAX

    def test_fail_count_increments(self):
        now = 1000.0
        assert MarketTemperatureService._fail_count == 0
        MarketTemperatureService._mark_failure(now)
        MarketTemperatureService._mark_failure(now)
        assert MarketTemperatureService._fail_count == 2

    def test_reset_clears_fail_count(self):
        MarketTemperatureService._mark_failure(1000.0)
        MarketTemperatureService.reset_cache()
        assert MarketTemperatureService._fail_count == 0
        assert MarketTemperatureService._fail_until == 0.0


class TestStaleness:
    def test_fresh_data_not_stale(self):
        got = _staleness_fields("2026-11-20", datetime(2026, 11, 21, 20, 0).timestamp())
        assert got["data_lag_days"] == 1
        assert got["stale"] is False

    def test_week_old_data_is_stale(self):
        """线上故障形态：数据停在 11-13，当天 11-21 → 8 天 → 必须标记陈旧"""
        got = _staleness_fields("2026-11-13", datetime(2026, 11, 21, 20, 0).timestamp())
        assert got["data_lag_days"] == 8
        assert got["stale"] is True

    def test_seven_days_not_yet_stale(self):
        """7 天内可能只是长假，不误报"""
        got = _staleness_fields("2026-11-14", datetime(2026, 11, 21, 20, 0).timestamp())
        assert got["data_lag_days"] == 7
        assert got["stale"] is False

    def test_bad_input_degrades_silently(self):
        for bad in (None, "", "not-a-date", 12345):
            got = _staleness_fields(bad, time.time())
            assert got["stale"] is False, f"{bad!r} 不应崩，也不应误报陈旧"


def _service_src() -> str:
    """读源文件（重试逻辑是 get_temperature 内的局部函数，getsource 取不到）"""
    from backend.services import market_temperature_service as m

    return Path(m.__file__).read_text(encoding="utf-8")


class TestRetryWiring:
    def test_fetch_has_retry_loop(self):
        """取数必须带重试（akshare 自身不重试，一次抖动即整体失败）"""
        src = _service_src()
        assert "_fetch_with_retry" in src
        assert "range(3)" in src, "应重试 3 次"

    def test_backoff_delays_present(self):
        """退避要覆盖乐咕的短时限流窗口（实测 90s 恢复）"""
        src = _service_src()
        assert "20.0" in src and "60.0" in src

    def test_timeout_allows_retries(self):
        """超时要容得下 3 次重试（退避 20+60=80s），否则重试没意义"""
        src = _service_src()
        assert "timeout=300" in src, "原 90s 会让第 2 次重试就被砍断"

    def test_payload_includes_staleness(self):
        src = _service_src()
        assert "_staleness_fields(dates[i], fetched_at)" in src

    def test_failure_uses_exponential_backoff(self):
        """失败不能再用固定 120s 冷却（那正是 6 天停摆的原因）"""
        src = _service_src()
        assert "_mark_failure(now)" in src
        assert src.count("_mark_failure(now)") >= 2, "两条失败路径都要退避"
