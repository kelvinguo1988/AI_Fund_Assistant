"""仪表盘数据新鲜度（2026-10-21 线上核查发现的三处缺陷）

1. fundgz 在收盘后对全部基金返回「页面未找到」页，原代码判为反爬并冷却 24h，
   导致每天收盘到次日开盘 42 只基金估值全部 unavailable；
2. push2his 资金流 klines 是「老→新」，原实现在 lmt>1 时取 [0]（最旧一天）；
3. push2 实时兜底只有资金流、指数留 None，前端会与上一个源的指数混搭。
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestFundgzOffHours:
    """收盘后不应把「无估值页」当反爬"""

    def test_cooldown_is_not_24h(self):
        """24h 冷却会把次日盘中整段估值挡掉（线上实测）"""
        from backend.services.fund_realtime_service import FUNDGZ_FAIL_COOLDOWN

        assert FUNDGZ_FAIL_COOLDOWN <= 3600, (
            f"冷却 {FUNDGZ_FAIL_COOLDOWN}s 过长：收盘后触发的冷却会挡住次日盘中估值"
        )

    @pytest.mark.asyncio
    async def test_no_cooldown_when_market_closed(self, monkeypatch):
        """非盘中拿到无估值页 → 直接跳过，不触发冷却"""
        import backend.services.fund_realtime_service as frs

        monkeypatch.setattr(frs, "_in_trading_hours", lambda: False)
        frs.FundRealtimeService._fundgz_fail_until = 0.0

        class FakeResp:
            text = "<html><title>页面未找到 - 东方财富网</title></html>"

        monkeypatch.setattr(frs.requests, "get", lambda *a, **k: FakeResp())
        svc = frs.FundRealtimeService.__new__(frs.FundRealtimeService)
        r = await svc._fetch_fundgz("004011")
        assert r is None
        assert frs.FundRealtimeService._fundgz_fail_until == 0.0, (
            "收盘后不该进入冷却"
        )

    @pytest.mark.asyncio
    async def test_cooldown_still_applies_in_trading_hours(self, monkeypatch):
        """盘中同样的无估值页仍要判反爬冷却（别把真反爬放过去）"""
        import backend.services.fund_realtime_service as frs

        monkeypatch.setattr(frs, "_in_trading_hours", lambda: True)
        frs.FundRealtimeService._fundgz_fail_until = 0.0

        class FakeResp:
            text = "<html>blocked</html>"

        monkeypatch.setattr(frs.requests, "get", lambda *a, **k: FakeResp())
        svc = frs.FundRealtimeService.__new__(frs.FundRealtimeService)
        await svc._fetch_fundgz("004011")
        assert frs.FundRealtimeService._fundgz_fail_until > 0, "盘中应进入冷却"


class TestHoldingsStale:
    def test_stale_days_naive_and_aware(self):
        from backend.services.fund_realtime_service import _holdings_stale_days

        assert _holdings_stale_days("q", None) is None
        assert _holdings_stale_days("q", datetime.now()) == 0
        assert _holdings_stale_days("q", datetime.now() - timedelta(days=5)) == 5
        # 带时区也不能抛 naive/aware 相减错误
        from datetime import timezone
        aware = datetime.now(timezone.utc) - timedelta(days=2)
        assert isinstance(_holdings_stale_days("q", aware), int)

    def test_threshold_is_reasonable(self):
        from backend.services.fund_realtime_service import HOLDINGS_STALE_DAYS

        assert 1 <= HOLDINGS_STALE_DAYS <= 7


class TestKlinesOrdering:
    def test_push2his_takes_latest_not_oldest(self):
        """klines 老→新，必须取最后一条"""
        import inspect
        from backend.services import market_service

        src = inspect.getsource(
            market_service.MarketService._fetch_capital_flow_push2his
        )
        assert "klines[-1]" in src, "应取最新一条"
        assert "klines[0]" not in src, "取 [0] 会拿到区间里最旧的一天"


class TestPush2RealtimeHasIndex:
    def test_request_asks_for_index_fields(self):
        """缺 f2/f3/f12 时兜底返回的指数是 None，前端会混搭上一个源的指数"""
        import inspect
        from backend.services import market_service

        src = inspect.getsource(
            market_service.MarketService._fetch_capital_flow_push2_realtime
        )
        for f in ("f2", "f3", "f12"):
            assert f in src, f"请求字段缺 {f}"
        assert "fltt" in src, "缺 fltt=2 时价格是 ×100 的整数"


class TestMarketCacheFreshness:
    """数据库缓存行情必须校验日期（2026-10-21 线上：今日仍显示昨日指数）

    历史缺陷：读取路径只判「缓存帧里有没有数据」，不看数据日期。上游连续
    失败时仪表盘会一直展示上一交易日行情，updated_at 却是「刚刚」。

    第一版修复用「N 天容差」，实测被 1 天容差放过（10-21 与 10-20 恰好差
    一天）。改为按**交易日历**判定：缓存日期须是最近一个交易日。
    """

    def test_previous_trading_day_rejected(self):
        """交易日当天，上一交易日的数据必须判陈旧"""
        from backend.services.fund_cache_service import market_cache_is_fresh
        from backend.utils.timezone import beijing_today
        from datetime import timedelta
        import pytest as _pytest
        from backend.data_sources.trading_calendar import is_trading_day

        today = beijing_today()
        if not is_trading_day(today):
            _pytest.skip("今天不是交易日")
        yesterday = (today - timedelta(days=1)).isoformat()
        assert market_cache_is_fresh({"market_flow": {"date": yesterday}}) is False

    def test_today_cache_accepted(self):
        from backend.services.fund_cache_service import market_cache_is_fresh
        from backend.utils.timezone import beijing_today

        today = beijing_today().isoformat()
        assert market_cache_is_fresh({"market_flow": {"date": today}}) is True

    def test_far_past_rejected(self):
        from backend.services.fund_cache_service import market_cache_is_fresh

        assert market_cache_is_fresh({"market_flow": {"date": "2026-10-19"}}) is False

    def test_missing_date_is_backward_compatible(self):
        """老缓存没有 date 字段 → 维持原行为，不能一刀切当过期"""
        from backend.services.fund_cache_service import market_cache_is_fresh

        assert market_cache_is_fresh({"market_flow": {"sh_index": 3713.0}}) is True

    def test_empty_cache_not_fresh(self):
        from backend.services.fund_cache_service import market_cache_is_fresh

        assert market_cache_is_fresh(None) is False
        assert market_cache_is_fresh({}) is False

    def test_read_path_uses_freshness(self):
        """读取路径必须调用新鲜度判定，不能只看有无数据"""
        import inspect
        from backend.routers import analysis

        src = inspect.getsource(analysis)
        assert "market_cache_is_fresh(" in src, (
            "summary 读取缓存时必须校验日期，否则旧行情会被当成今日数据"
        )


class TestIndexLevelSupplement:
    """datacenter-web 资金流报表没有指数点位，需用腾讯补齐（2026-10-21）

    线上现象：修复日期后仪表盘显示「上证 None (-0.60%)」——涨跌幅正确但点位
    缺失。datacenter 报表本身不含点位（代码注释已写明），改用腾讯行情补。
    """

    @pytest.mark.asyncio
    async def test_parses_tencent_index_levels(self, monkeypatch):
        import backend.services.market_service as ms

        body = (
            'v_sh000001="1~上证指数~000001~3713.03~3715.00~"'
            ';v_sz399001="1~深证成指~399001~11704.35~11780.00~";'
        )

        class FakeResp:
            text = body
            encoding = ""

        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp())
        sh, sz = await ms._fetch_index_levels_tencent()
        assert sh == 3713.03
        assert sz == 11704.35

    @pytest.mark.asyncio
    async def test_failure_returns_none_pair(self, monkeypatch):
        """取不到点位时不能抛异常，资金流本身要继续可用"""
        import backend.services.market_service as ms
        import requests

        def boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr(requests, "get", boom)
        sh, sz = await ms._fetch_index_levels_tencent()
        assert sh is None and sz is None

    def test_only_supplement_for_today(self):
        """只在报表日期是今天时补点位：拿实时点位配昨日涨跌幅会自相矛盾"""
        import inspect
        from backend.services import market_service

        src = inspect.getsource(market_service.MarketService.get_market_capital_flow)
        assert "beijing_today().isoformat()" in src, (
            "补点位前必须校验报表日期是否为今天"
        )
