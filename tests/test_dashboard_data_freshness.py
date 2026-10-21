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
