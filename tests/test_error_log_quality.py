"""错误日志质量（2026-10-27 线上日志分析）

线上 300 条日志分析发现四类"噪声淹没真故障"的问题，逐一修掉：

1. **同一次故障被记成 2~3 条**：akshare 适配器记一条、行情服务再记一条，
   消息相同但 module 不同，原指纹 (module, category, message) 去不掉。
   实测 14 组同秒成对记录。
2. **非交易时段的预热请求**：每次部署重启都无条件打 5 个行情源，21:00~23:00
   那批「熔断 600s / ConnectionError」几乎全来自这里；行情源收盘后本就限流，
   打了拿不到数据，只会把冷却带进次日开盘。
3. **熔断被标成 error**：熔断是防护机制按设计生效，不是故障。线上 41 条 error
   里绝大多数是这类，真故障被淹。
4. **永久性无数据每天刷一条**：968049 是香港互认基金，在内地基金列表
   24269 行里 0 命中，每天必然失败一次。
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def store(tmp_path, monkeypatch):
    """隔离的错误日志库，避免污染真实数据"""
    import backend.services.error_log_service as els

    monkeypatch.setattr(els, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(els.ErrorLogStore, "_instance", None)
    return els.ErrorLogStore()


class TestCrossModuleDedup:
    def test_same_message_different_module_logged_once(self, store):
        """同一故障被两层各记一条 → 只留先到的（线上 14 组重复）"""
        msg = "[ConnectionError] ('Connection aborted.', RemoteDisconnected)"
        assert store.log("akshare.fund_etf_spot_em", msg, category="rate_limit") is True
        assert store.log("realtime.eastmoney", msg, category="rate_limit") is False, (
            "同消息不同模块必须去重，否则 1 次故障报成 2 次"
        )
        assert len(store.query(limit=10)) == 1

    def test_different_messages_both_kept(self, store):
        """不同消息仍要各自留痕，不能过度去重"""
        assert store.log("a.b", "问题一", category="rate_limit") is True
        assert store.log("c.d", "问题二", category="rate_limit") is True
        assert len(store.query(limit=10)) == 2

    def test_same_module_still_throttled(self, store):
        """原有 (module, category, message) 指纹节流不能被破坏"""
        assert store.log("a.b", "同一模块同一消息", category="data") is True
        assert store.log("a.b", "同一模块同一消息", category="data") is False


class TestPermanentMissingFund:
    def test_hk_fund_logged_as_info(self):
        """互认基金（96/97/98 开头）永久无数据 → info，不占 warning 告警"""
        import inspect

        from backend.services import analysis_service as a

        src = inspect.getsource(a._log_missing_nav)
        assert "96" in src and "info" in src
        assert "severity" in src

    def test_normal_fund_still_warning(self):
        """普通基金缺数据仍是 warning，不能一起降级"""
        import inspect

        from backend.services import analysis_service as a

        src = inspect.getsource(a._log_missing_nav)
        assert '"warning"' in src, "普通基金缺数据必须保持 warning"


class TestPrewarmTimeAware:
    def test_prewarm_checks_trading_hours(self):
        """非交易时段不能无条件打全部行情源"""
        import inspect

        from backend import main

        src = inspect.getsource(main._prewarm_market_cache)
        assert "_in_trading_hours" in src, "预热必须按时段分流"
        assert "trading" in src

    def test_closed_session_skips_realtime_sources(self):
        """收盘后不碰板块排行/涨跌家数（需要实时源，收盘后必限流）"""
        import inspect

        from backend import main

        src = inspect.getsource(main._prewarm_market_cache)
        # 收盘分支只 gather 三项日频口径
        closed_branch = src.split("else:")[-1] if "else:" in src else ""
        assert "sector_flow" not in closed_branch, "收盘后不该打板块排行"
        assert "adv_decline" not in closed_branch, "收盘后不该打涨跌家数"


class TestCacheTtlTimeAware:
    def test_ttl_adapts_to_session(self):
        """收盘后数据当天不变，TTL 必须放宽，否则仪表盘反复空白"""
        from backend.services.market_service import MarketService

        ttl = MarketService._ttl()
        assert ttl > 0
        # 盘中 300s，收盘后 12h；至少不能是"盘中比收盘更长"的反向关系
        assert MarketService._CACHE_TTL == 300

    def test_ttl_never_shorter_than_intraday(self):
        from backend.services.market_service import MarketService

        assert MarketService._ttl() >= MarketService._CACHE_TTL


class TestBreakerSeverity:
    def test_breaker_is_warning_not_error(self):
        """熔断是防护生效，不该按 error 告警（否则真故障被淹）"""
        import inspect

        from backend.services import fund_realtime_service as f

        src = inspect.getsource(f.FundRealtimeService._mark_source_fail)
        assert 'severity="warning"' in src, "熔断埋点应为 warning"
