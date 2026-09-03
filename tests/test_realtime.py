"""实时估值服务回归测试 — fundgz 解析 / 持仓加权模型 / 降级链"""

import sys, os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.fund_realtime_service import (
    FundRealtimeService,
    parse_fundgz_jsonp,
    parse_tencent_quotes,
    tencent_code,
    compute_holdings_growth,
)


# ── fundgz JSONP 解析 ─────────────────────────────────────────────────

class TestParseFundgzJsonp:
    def test_valid_jsonp(self):
        text = 'jsonpgz({"fundcode":"000001","name":"华夏成长","jzrq":"2026-08-27","dwjz":"1.0","gsz":"1.005","gszzl":"0.49","gztime":"2026-08-28 15:00"});'
        d = parse_fundgz_jsonp(text)
        assert d is not None
        assert d["gszzl"] == "0.49"
        assert d["gsz"] == "1.005"

    def test_anti_crawl_html_returns_none(self):
        html = "<!doctype html><html><head></head></html>"
        assert parse_fundgz_jsonp(html) is None

    def test_empty_and_garbage(self):
        assert parse_fundgz_jsonp("") is None
        assert parse_fundgz_jsonp("jsonpgz(not json);") is None


# ── 持仓加权估值模型 ──────────────────────────────────────────────────

class TestComputeHoldingsGrowth:
    def test_high_coverage_normalized(self):
        # 覆盖率 80% ≥ 0.5 → 归一法
        holdings = [("600000", 50.0), ("600036", 30.0)]
        pct = {"600000": 2.0, "600036": -1.0}
        growth, cov, model = compute_holdings_growth(holdings, pct)
        # (50*2 + 30*-1)/80 = 70/80 = 0.875
        assert growth == pytest.approx(0.875)
        assert cov == pytest.approx(0.8)
        assert model == "normalized"

    def test_low_coverage_index_blend(self):
        # 覆盖率 30% < 0.5 → 指数混合
        holdings = [("600000", 30.0)]
        pct = {"600000": 2.0}
        growth, cov, model = compute_holdings_growth(holdings, pct, index_pct=1.0)
        # 30*2/100 + 0.7*0.6*1.0 = 0.6 + 0.42 = 1.02
        assert growth == pytest.approx(1.02)
        assert cov == pytest.approx(0.3)
        assert model == "index_blend"

    def test_low_coverage_without_index_falls_back_normalized(self):
        holdings = [("600000", 30.0)]
        pct = {"600000": 2.0}
        growth, cov, model = compute_holdings_growth(holdings, pct, index_pct=None)
        assert growth == pytest.approx(2.0)
        assert model == "normalized"

    def test_no_overlap_returns_none(self):
        growth, cov, model = compute_holdings_growth(
            [("600000", 50.0)], {"000001": 1.0}
        )
        assert growth is None
        assert cov == 0.0
        assert model == ""

    def test_ultra_low_coverage_returns_none_not_fake_number(self):
        """覆盖率过低（ETF联接只披露 0.1% 股票仓位）→ 不估值，拒绝假数字

        此类基金主要资产是母 ETF，算出来的实质是「0.6 × 沪深300涨跌」，
        与基金实际跟踪的指数无关（稀土/稀有金属联接都跟着沪深300走）。
        推送看似精确、实则无关的数字比留空更有害。
        """
        holdings = [("600000", 0.1)]  # 覆盖率 0.001
        pct = {"600000": 2.0}
        growth, cov, model = compute_holdings_growth(holdings, pct, index_pct=1.0)
        assert growth is None
        assert model == ""
        assert cov < 0.1

    def test_moderate_coverage_still_blends(self):
        """覆盖率 30%（≥10% 下限）仍走指数混合法，不被误伤"""
        holdings = [("600000", 30.0)]
        pct = {"600000": 2.0}
        growth, cov, model = compute_holdings_growth(holdings, pct, index_pct=1.0)
        assert growth is not None
        assert model == "index_blend"

    def test_null_ratio_skipped(self):
        growth, _, _ = compute_holdings_growth(
            [("600000", None), ("600036", 40.0)], {"600036": 1.0, "600000": 5.0}
        )
        assert growth == pytest.approx(1.0)


# ── 服务降级链 ────────────────────────────────────────────────────────

class _FakeFund:
    def __init__(self, id, code, name=""):
        self.id = id
        self.code = code
        self.name = name


@pytest.mark.asyncio
async def test_fundgz_failure_falls_back_to_holdings(monkeypatch):
    """fundgz 冷却中 → OTC 走持仓自算"""
    # 强制 fundgz 冷却
    FundRealtimeService._fundgz_fail_until = float("inf")
    FundRealtimeService._estimate_cache.clear()
    try:
        from backend.models.fund_holding import FundHolding

        class _FakeDB:
            async def execute(self, stmt):
                class R:
                    def all(self):
                        return [(1, "2026年2季度股票投资明细")]

                    def scalars(self):
                        class S:
                            def all(self_inner):
                                h = FundHolding(
                                    fund_id=1, stock_code="600000",
                                    stock_name="浦发银行", ratio=60.0,
                                    quarter_label="2026年2季度股票投资明细",
                                )
                                return [h]
                        return S()
                return R()

        svc = FundRealtimeService(db=_FakeDB())

        async def _fake_stock_spot(self, codes=None):
            return {"600000": 2.0}

        async def _fake_index(self):
            return 1.0

        monkeypatch.setattr(FundRealtimeService, "_get_stock_spot", _fake_stock_spot)
        monkeypatch.setattr(FundRealtimeService, "_get_index_pct", _fake_index)

        results = await svc.get_realtime([_FakeFund(1, "000001", "测试基金")])
        r = results.get("000001")
        assert r is not None
        assert r["source"] == "holdings_est"
        assert r["growth_pct"] == pytest.approx(2.0)
        assert r["coverage"] == pytest.approx(0.6)
        assert r["est_model"] == "normalized"
    finally:
        FundRealtimeService._fundgz_fail_until = 0.0
        FundRealtimeService._estimate_cache.clear()


@pytest.mark.asyncio
async def test_estimate_cache_hit(monkeypatch):
    """60s 内重复请求命中缓存，不重新计算"""
    FundRealtimeService._estimate_cache.clear()
    FundRealtimeService._estimate_cache["000001"] = (
        __import__("time").time(),
        {"code": "000001", "source": "fundgz", "growth_pct": 0.49},
    )
    svc = FundRealtimeService(db=None)
    results = await svc.get_realtime([_FakeFund(1, "000001")], force=False)
    assert results["000001"]["growth_pct"] == 0.49
    FundRealtimeService._estimate_cache.clear()


@pytest.mark.asyncio
async def test_etf_uses_spot(monkeypatch):
    """ETF 走场内行情分支"""
    FundRealtimeService._estimate_cache.clear()
    svc = FundRealtimeService(db=None)

    async def _fake_etf(self, codes=None):
        return {"510300": {"name": "沪深300ETF", "price": 4.679, "pct": -0.26,
                            "time": "15:00:00", "date": "2026-08-28"}}

    monkeypatch.setattr(FundRealtimeService, "_get_etf_spot", _fake_etf)
    results = await svc.get_realtime([_FakeFund(2, "510300")])
    r = results["510300"]
    assert r["source"] == "etf_spot"
    assert r["estimated_nav"] == 4.679
    assert r["growth_pct"] == -0.26
    FundRealtimeService._estimate_cache.clear()


# ── 报告 top10_change 渲染 ────────────────────────────────────────────

class TestTop10ReportRendering:
    def test_markdown_top10_section(self):
        from backend.engines.report_engine import ReportEngine
        from backend.engines.scoring_engine import SignalResult

        signal = SignalResult(
            weighted_score=3.0, raw_score=3.0, signal_direction="buy",
            signal_strength="moderate_buy", operation_advice="x", equity_ratio=0.7,
        )
        changes = [
            {"stock_name": "中际旭创", "stock_code": "300308", "ratio": 8.7, "pct": -0.9},
            {"stock_name": "中芯国际", "stock_code": "00981", "ratio": 5.0, "pct": None},
        ]
        md = ReportEngine().generate_markdown(
            fund_code="018994", fund_name="测试", analysis_date="2026-08-29",
            signal=signal, factor_scores=[],
            enabled_items=["top10_change"], top10_changes=changes,
        )
        assert "## 前十大持仓涨跌" in md
        assert "-0.90%" in md
        assert "持仓加权涨跌" in md  # 只有 1 只有行情，加权=其自身
        assert "1/2 只已取到行情" in md

    def test_markdown_no_data_section_skipped(self):
        from backend.engines.report_engine import ReportEngine
        from backend.engines.scoring_engine import SignalResult

        signal = SignalResult(
            weighted_score=3.0, raw_score=3.0, signal_direction="buy",
            signal_strength="moderate_buy", operation_advice="x", equity_ratio=0.7,
        )
        md = ReportEngine().generate_markdown(
            fund_code="x", fund_name="t", analysis_date="d",
            signal=signal, factor_scores=[],
            enabled_items=["top10_change"], top10_changes=None,
        )
        assert "前十大持仓涨跌" not in md

    def test_get_top10_changes_hk_lookup(self, monkeypatch):
        """5 位纯数字代码走港股快照查找"""
        import asyncio
        from backend.models.fund_holding import FundHolding

        fake_holdings = [
            FundHolding(fund_id=1, stock_code="300308",
                        stock_name="中际旭创", ratio=8.7, quarter_label="q"),
            FundHolding(fund_id=1, stock_code="00981",
                        stock_name="中芯国际", ratio=5.0, quarter_label="q"),
        ]

        async def _fake_get_latest(db, fund_id, limit=10):
            return fake_holdings

        import backend.services.fund_holding_service as _fhs
        monkeypatch.setattr(_fhs, "get_latest_holdings", _fake_get_latest)

        svc = FundRealtimeService(db=None)

        async def _fake_stock(self, codes=None):
            return {"300308": -0.9}

        async def _fake_hk(self, codes=None):
            return {"00981": 1.23}

        monkeypatch.setattr(FundRealtimeService, "_get_stock_spot", _fake_stock)
        monkeypatch.setattr(FundRealtimeService, "_get_hk_spot", _fake_hk)
        result = asyncio.run(svc.get_top10_changes(1))
        by_code = {r["stock_code"]: r["pct"] for r in result}
        assert by_code["300308"] == -0.9
        assert by_code["00981"] == 1.23


# ── 持仓自算：港股持仓覆盖（2026-09-01 修复）────────────────────────────

def _fake_holdings_db(holdings):
    """构造返回指定持仓的假 DB（_fill_from_holdings 只查 fund_holdings）"""
    from backend.models.fund_holding import FundHolding

    class _FakeDB:
        async def execute(self, stmt):
            class R:
                def all(self):
                    return [(1, "2026年2季度股票投资明细")]

                def scalars(self):
                    class S:
                        def all(self_inner):
                            return [
                                FundHolding(
                                    fund_id=1, stock_code=c, stock_name=n,
                                    ratio=r, quarter_label="2026年2季度股票投资明细",
                                )
                                for c, n, r in holdings
                            ]
                    return S()
            return R()
    return _FakeDB()


@pytest.mark.asyncio
async def test_fill_from_holdings_merges_hk_quotes(monkeypatch):
    """持仓含港股(5 位代码)时，应同时取 A 股与港股行情并合并加权"""
    FundRealtimeService._fundgz_fail_until = float("inf")
    FundRealtimeService._estimate_cache.clear()
    try:
        svc = FundRealtimeService(db=_fake_holdings_db([
            ("600000", "浦发银行", 30.0),
            ("00981", "中芯国际", 30.0),
        ]))

        hk_calls = []

        async def _fake_stock(self, codes=None):
            return {"600000": 2.0}

        async def _fake_hk(self, codes=None):
            hk_calls.append(list(codes or []))
            return {"00981": 4.0}

        async def _fake_index(self):
            return 1.0

        monkeypatch.setattr(FundRealtimeService, "_get_stock_spot", _fake_stock)
        monkeypatch.setattr(FundRealtimeService, "_get_hk_spot", _fake_hk)
        monkeypatch.setattr(FundRealtimeService, "_get_index_pct", _fake_index)

        results = await svc.get_realtime([_FakeFund(1, "000001", "测试基金")])
        r = results.get("000001")
        assert r is not None
        assert hk_calls == [["00981"]], "5 位港股代码必须走港股行情源"
        # (30*2 + 30*4) / 60 = 3.0，覆盖率 60%
        assert r["growth_pct"] == pytest.approx(3.0)
        assert r["coverage"] == pytest.approx(0.6)
        assert r["est_model"] == "normalized"
    finally:
        FundRealtimeService._fundgz_fail_until = 0.0
        FundRealtimeService._estimate_cache.clear()


@pytest.mark.asyncio
async def test_hk_quotes_survive_a_share_source_failure(monkeypatch):
    """A 股行情源熔断返回空时，纯港股持仓基金仍应算出估值

    原逻辑 `if not stock_pct: return` 会因 A 股源失败整批放弃，
    含港股的基金估值随之全部消失。
    """
    FundRealtimeService._fundgz_fail_until = float("inf")
    FundRealtimeService._estimate_cache.clear()
    try:
        svc = FundRealtimeService(db=_fake_holdings_db([
            ("00981", "中芯国际", 30.0),
        ]))

        async def _fake_stock(self, codes=None):
            return None  # 模拟 A 股源熔断

        async def _fake_hk(self, codes=None):
            return {"00981": 3.0}

        async def _fake_index(self):
            return 1.0

        monkeypatch.setattr(FundRealtimeService, "_get_stock_spot", _fake_stock)
        monkeypatch.setattr(FundRealtimeService, "_get_hk_spot", _fake_hk)
        monkeypatch.setattr(FundRealtimeService, "_get_index_pct", _fake_index)

        results = await svc.get_realtime([_FakeFund(1, "000001", "港股基金")])
        r = results.get("000001")
        assert r is not None, "A 股源失败不应连港股数据一起丢弃"
        # 覆盖率 30% < 0.5 → 指数混合: 30*3/100 + 0.7*0.6*1.0 = 0.9 + 0.42
        assert r["growth_pct"] == pytest.approx(1.32)
        assert r["est_model"] == "index_blend"
    finally:
        FundRealtimeService._fundgz_fail_until = 0.0
        FundRealtimeService._estimate_cache.clear()


# ── 推送取数：缓存优先，避免与调度预热重复强刷（2026-09-01 修复）────────

def _fake_push_db(n: int):
    """构造返回 n 只 active 基金的假 DB"""

    class _FakeDB:
        async def execute(self, stmt):
            class R:
                def scalars(self):
                    class S:
                        def all(self_inner):
                            return [_FakeFund(i, f"{i:06d}") for i in range(1, n + 1)]
                    return S()
            return R()
    return _FakeDB()


@pytest.mark.asyncio
async def test_push_prefers_cache_and_skips_force(monkeypatch):
    """缓存覆盖率足够时，推送只读缓存，不再强刷行情源"""
    from backend.services.push_service import PushService

    calls = []

    async def _fake_get_realtime(self, funds, force=False):
        calls.append(force)
        # 缓存轮返回全部 4 只，覆盖率 100%
        return {f"{i:06d}": {"code": f"{i:06d}", "growth_pct": 1.0}
                for i in range(1, 5)}

    monkeypatch.setattr(FundRealtimeService, "get_realtime", _fake_get_realtime)
    svc = PushService(db=_fake_push_db(4))
    m = await svc._fetch_realtime_map()
    assert calls == [False], "覆盖率足够时不应再发起 force=True 强刷"
    assert len(m) == 4


@pytest.mark.asyncio
async def test_push_forces_refresh_when_cache_empty(monkeypatch):
    """缓存为空（源熔断/预热全败）时才强刷一次"""
    from backend.services.push_service import PushService

    def _empty():
        return {}

    calls = []

    async def _fake_get_realtime(self, funds, force=False):
        calls.append(force)
        if force:
            return {f"{i:06d}": {"code": f"{i:06d}", "growth_pct": 2.0}
                    for i in range(1, 5)}
        return {}

    monkeypatch.setattr(FundRealtimeService, "get_realtime", _fake_get_realtime)
    svc = PushService(db=_fake_push_db(4))
    m = await svc._fetch_realtime_map()
    assert calls == [False, True], "缓存为空应补一次强刷"
    assert len(m) == 4


@pytest.mark.asyncio
async def test_push_warns_when_realtime_empty(monkeypatch, caplog):
    """两轮都取不到数据时必须告警，不能静默丢失实时估值"""
    from backend.services.push_service import PushService

    async def _fake_get_realtime(self, funds, force=False):
        return {}

    monkeypatch.setattr(FundRealtimeService, "get_realtime", _fake_get_realtime)
    svc = PushService(db=_fake_push_db(3))
    with caplog.at_level("WARNING", logger="backend.services.push_service"):
        m = await svc._fetch_realtime_map()
    assert m == {}
    assert any("实时涨跌项将整块缺失" in r.message for r in caplog.records)


# ── 数据源熔断（防封禁）────────────────────────────────────────────────

class TestSourceCircuitBreaker:
    def setup_method(self):
        FundRealtimeService._source_fail_until.clear()
        FundRealtimeService._stock_spot_cache = None
        FundRealtimeService._etf_spot_cache = None
        FundRealtimeService._hk_spot_cache = None
        FundRealtimeService._index_pct_cache = None
        FundRealtimeService._spot_ts = 0.0

    def teardown_method(self):
        FundRealtimeService._source_fail_until.clear()

    def test_mark_fail_blocks_source(self):
        import time as _t
        svc = FundRealtimeService(db=None)
        svc._mark_source_fail("eastmoney", "test")
        assert not FundRealtimeService._source_available("eastmoney")
        # 其他源不受影响
        assert FundRealtimeService._source_available("sina")

    def test_em_breaker_skips_request_and_falls_to_sina(self, monkeypatch):
        """东财熔断期间不发请求，直接走新浪"""
        import asyncio
        import pandas as pd

        FundRealtimeService._source_fail_until["eastmoney"] = float("inf")
        svc = FundRealtimeService(db=None)

        em_called = []

        def _fake_em(*a, **k):
            em_called.append(1)
            raise RuntimeError("should not be called")

        def _fake_sina(*a, **k):
            return pd.DataFrame({
                "代码": ["sh600000", "sz000001"],
                "涨跌幅": [1.5, -0.5],
            })

        import akshare as ak
        monkeypatch.setattr(ak, "stock_zh_a_spot_em", _fake_em)
        monkeypatch.setattr(ak, "stock_zh_a_spot", _fake_sina)

        class _FakeAdapter:
            async def _call(self, func, *a, **k):
                return func(*a, **k)

        import backend.data_sources.akshare_adapter as _ada
        monkeypatch.setattr(_ada, "AKShareAdapter", _FakeAdapter)

        result = asyncio.run(svc._get_stock_spot())
        assert em_called == [], "东财熔断期间不应发出请求"
        assert result == {"600000": 1.5, "000001": -0.5}

    def test_all_sources_cooldown_returns_fast(self, monkeypatch):
        """双源都熔断 → 秒回 None，零网络请求"""
        import asyncio
        import time as _t

        FundRealtimeService._source_fail_until = {
            "eastmoney": _t.time() + 600,
            "sina": _t.time() + 600,
        }
        svc = FundRealtimeService(db=None)

        def _boom(*a, **k):
            raise RuntimeError("no network call expected")

        import akshare as ak
        monkeypatch.setattr(ak, "stock_zh_a_spot_em", _boom)
        monkeypatch.setattr(ak, "stock_zh_a_spot", _boom)

        result = asyncio.run(svc._get_stock_spot())
        assert result is None

    def test_success_clears_breaker(self):
        svc = FundRealtimeService(db=None)
        svc._mark_source_fail("eastmoney")
        svc._mark_source_ok("eastmoney")
        assert FundRealtimeService._source_available("eastmoney")


# ── 腾讯稳定源（2026-08-29 接入）─────────────────────────────────────

class TestTencentQuotes:
    @staticmethod
    def _tencent_line(key: str, name: str, price: str, prev: str, pct: str) -> str:
        """构造对齐真实字段位（30=时间 31=涨跌额 32=涨跌幅）的腾讯响应行"""
        fields = [""] * 30
        fields[0:9] = ["1", name, key.lstrip("shszhk"), price, prev, "", "", "", ""]
        fields += ["20260828161437", "0.05", pct]
        return f'v_{key}="{"~".join(fields)}";'

    def test_parse_quotes(self):
        text = self._tencent_line("sh600308", "华泰股份", "3.34", "3.29", "1.52")
        d = parse_tencent_quotes(text)
        assert "600308" in d
        q = d["600308"]
        assert q["name"] == "华泰股份"
        assert q["price"] == 3.34
        assert q["pct"] == 1.52

    def test_parse_hk(self):
        text = self._tencent_line("hk00981", "中芯国际", "70.150", "71.300", "-1.61")
        d = parse_tencent_quotes(text)
        assert "00981" in d
        assert d["00981"]["pct"] == -1.61

    def test_parse_garbage(self):
        assert parse_tencent_quotes("") == {}
        assert parse_tencent_quotes("<html>blocked</html>") == {}

    def test_code_prefix(self):
        from backend.services.fund_realtime_service import tencent_code
        assert tencent_code("600308") == "sh600308"
        assert tencent_code("000001") == "sz000001"
        assert tencent_code("300308") == "sz300308"
        assert tencent_code("510300") == "sh510300"
        assert tencent_code("159915") == "sz159915"
        assert tencent_code("00981") == "hk00981"

    def test_explicit_market_overrides_prefix_rule(self):
        """指数等首位数字无法区分归属的场景必须显式传 market

        实测（2026-09-02）：sz000300 → v_pv_none_match（查无此码），
        sh000300 → 沪深300 实时行情。但默认规则不能为指数特化 ——
        "000001" 既是上证指数(sh) 也是平安银行(sz)，默认必须服务个股。
        """
        from backend.services.fund_realtime_service import tencent_code
        assert tencent_code("000300", market="sh") == "sh000300"
        assert tencent_code("000300") == "sz000300", "默认规则服务个股"
        assert tencent_code("000001", market="sh") == "sh000001", "上证指数"
        assert tencent_code("000001") == "sz000001", "平安银行"
        assert tencent_code("00981", market="hk") == "hk00981"

    def test_em_breaker_falls_to_tencent(self, monkeypatch):
        """东财+新浪都熔断 → 腾讯按需接管"""
        import asyncio
        import time as _t
        import pandas as pd
        from backend.services import fund_realtime_service as m

        FundRealtimeService._source_fail_until = {
            "eastmoney": _t.time() + 600,
            "sina": _t.time() + 600,
        }
        FundRealtimeService._stock_spot_cache = None
        FundRealtimeService._spot_ts = 0.0
        try:
            svc = FundRealtimeService(db=None)

            async def _fake_tencent(self, codes):
                return {"300308": -0.9, "600487": -3.65}

            monkeypatch.setattr(FundRealtimeService, "_get_tencent_pct", _fake_tencent)
            result = asyncio.run(svc._get_stock_spot(codes=["300308", "600487"]))
            assert result == {"300308": -0.9, "600487": -3.65}
            # 腾讯按需结果应并入 60s 缓存
            assert FundRealtimeService._stock_spot_cache == result
        finally:
            FundRealtimeService._source_fail_until.clear()
            FundRealtimeService._stock_spot_cache = None


class TestTencentBatchResilience:
    """腾讯批量降级链韧性——单批抖动不得熔断整个源

    背景：全池持仓约 2756 只 → 56 批。原实现用单个 try 包住全部批次，
    任一批网络抖动即把腾讯源整体熔断 600s，表现为"偶发抖动 → 全池基金
    无估值"。修复后：单批独立重试，仅当全败或过半失败才熔断。
    """

    @staticmethod
    def _fake_text(codes):
        """构造腾讯行情响应文本（字段 [30] 时间 / [32] 涨跌幅）"""
        lines = []
        for c in codes:
            fields = (
                ["1", f"股票{c}", c, "10.00", "9.90"]
                + [""] * 25
                + ["20260828161437", "0.05", "1.01"]
            )
            lines.append(f'v_{tencent_code(c)}="' + "~".join(fields) + '"')
        return ";".join(lines) + ";"

    def _patch_requests(self, monkeypatch, fail_first_batch_of=None):
        """替换 requests.get；fail_first_batch_of 的首批代码所在批次抛异常"""
        from backend.services import fund_realtime_service as m

        class _Resp:
            def __init__(self, text):
                self.text = text

        def _fake_get(url, headers=None, timeout=None):
            batch = [x[2:] for x in url.split("q=")[1].split(",")]
            if fail_first_batch_of is not None and batch[0] == fail_first_batch_of:
                raise OSError("模拟网络抖动")
            return _Resp(self._fake_text(batch))

        monkeypatch.setattr(m.requests, "get", _fake_get)

    def test_partial_batch_failure_keeps_source_alive(self, monkeypatch):
        """部分批次失败：其余批次数据照常返回，腾讯源不熔断"""
        import asyncio

        FundRealtimeService._source_fail_until.clear()
        try:
            svc = FundRealtimeService(db=None)
            codes = [f"{600000 + i}" for i in range(150)]  # 3 批
            self._patch_requests(monkeypatch, fail_first_batch_of=codes[0])

            out = asyncio.run(svc._get_tencent_quotes(codes))
            # 第 2、3 批共 100 只应正常返回
            assert len(out) == 100
            # 关键：多数批次成功 → 腾讯源保持可用（原策略会在此熔断 600s）
            assert not FundRealtimeService._source_fail_until.get("tencent")
        finally:
            FundRealtimeService._source_fail_until.clear()

    def test_all_batches_fail_trips_breaker(self, monkeypatch):
        """全部批次失败 → 熔断腾讯源，避免持续撞死链"""
        import asyncio

        FundRealtimeService._source_fail_until.clear()
        try:
            svc = FundRealtimeService(db=None)
            codes = [f"{600000 + i}" for i in range(100)]  # 2 批

            from backend.services import fund_realtime_service as m

            def _boom(url, headers=None, timeout=None):
                raise OSError("模拟全链路失败")

            monkeypatch.setattr(m.requests, "get", _boom)
            out = asyncio.run(svc._get_tencent_quotes(codes))
            assert out == {}
            # 全败 → 熔断生效
            assert FundRealtimeService._source_fail_until.get("tencent", 0) > 0
        finally:
            FundRealtimeService._source_fail_until.clear()

    def test_batch_size_produces_multiple_batches(self, monkeypatch):
        """超过单批上限时确实拆成多批，全部成功则数据完整"""
        import asyncio

        FundRealtimeService._source_fail_until.clear()
        try:
            svc = FundRealtimeService(db=None)
            codes = [f"{600000 + i}" for i in range(150)]
            self._patch_requests(monkeypatch)
            out = asyncio.run(svc._get_tencent_quotes(codes))
            assert len(out) == 150
        finally:
            FundRealtimeService._source_fail_until.clear()


class TestTencentOptionalPath:
    """可选路径（指数指标）不得熔断共享源

    沪深300 只是低覆盖混合法的精度补偿项，取不到仅影响少量基金的估值精度。
    但它与 ETF/个股行情**共用腾讯源**——若它失败就触发熔断，
    一次指数查询抖动会连累整个行情链（2026-09-02 实测的级联故障）。
    """

    def setup_method(self):
        FundRealtimeService._source_fail_until.clear()
        FundRealtimeService._index_pct_cache = None
        FundRealtimeService._spot_ts = 0.0

    def teardown_method(self):
        FundRealtimeService._source_fail_until.clear()
        FundRealtimeService._index_pct_cache = None
        FundRealtimeService._spot_ts = 0.0

    def test_market_prefix_reaches_request_url(self, monkeypatch):
        """显式 market 必须体现在实际请求代码上"""
        import asyncio
        from backend.services import fund_realtime_service as m

        urls = []

        class _Resp:
            text = ""

        def _fake_get(url, headers=None, timeout=None):
            urls.append(url)
            return _Resp()

        monkeypatch.setattr(m.requests, "get", _fake_get)
        svc = FundRealtimeService(db=None)
        asyncio.run(
            svc._get_tencent_quotes(["000300"], market="sh", trip_breaker=False)
        )
        assert urls, "应发起请求"
        assert "sh000300" in urls[0], f"显式 market 未生效: {urls[0]}"

    def test_optional_failure_does_not_trip_breaker(self, monkeypatch):
        """可选路径全败也不得熔断共享源"""
        import asyncio
        from backend.services import fund_realtime_service as m

        def _boom(url, headers=None, timeout=None):
            raise OSError("模拟指数查询失败")

        monkeypatch.setattr(m.requests, "get", _boom)
        svc = FundRealtimeService(db=None)
        out = asyncio.run(
            svc._get_tencent_quotes(["000300"], market="sh", trip_breaker=False)
        )
        assert out == {}
        assert not FundRealtimeService._source_fail_until.get("tencent", 0), \
            "可选路径失败熔断共享源会连累 ETF/个股行情"

    def test_critical_path_still_trips_breaker(self, monkeypatch):
        """同一接口在关键路径（默认 trip_breaker=True）下仍要熔断"""
        import asyncio
        from backend.services import fund_realtime_service as m

        def _boom(url, headers=None, timeout=None):
            raise OSError("模拟行情全链路失败")

        monkeypatch.setattr(m.requests, "get", _boom)
        svc = FundRealtimeService(db=None)
        out = asyncio.run(svc._get_tencent_quotes(["600000", "600001"]))
        assert out == {}
        assert FundRealtimeService._source_fail_until.get("tencent", 0) > 0

    def test_index_pct_uses_sh_market_and_no_breaker(self, monkeypatch):
        """_get_index_pct 必须带 market='sh' 且 trip_breaker=False"""
        import asyncio, time as _t

        FundRealtimeService._source_fail_until = {"eastmoney": _t.time() + 600}
        captured = {}

        async def _fake_quotes(self, codes, market=None, trip_breaker=True):
            captured.update(codes=codes, market=market, trip_breaker=trip_breaker)
            return {"000300": {"name": "沪深300", "price": 4552.58, "pct": 0.10,
                               "time": "20260903161413"}}

        monkeypatch.setattr(FundRealtimeService, "_get_tencent_quotes", _fake_quotes)
        svc = FundRealtimeService(db=None)
        pct = asyncio.run(svc._get_index_pct())
        assert pct == pytest.approx(0.10)
        assert captured["codes"] == ["000300"]
        assert captured["market"] == "sh", "指数须显式沪市，否则腾讯查无此码"
        assert captured["trip_breaker"] is False, "指数为可选路径，失败不得熔断共享源"


class TestQuoteTime:
    def setup_method(self):
        FundRealtimeService._spot_quote_time = ""
        FundRealtimeService._spot_quote_time_ts = None

    def teardown_method(self):
        FundRealtimeService._spot_quote_time = ""
        FundRealtimeService._spot_quote_time_ts = None

    def test_tencent_14digit_trusted(self):
        FundRealtimeService._update_quote_time("20260828161437", trusted=True)
        assert FundRealtimeService._spot_quote_time == "2026-08-28 16:14"

    def test_trusted_takes_newer(self):
        FundRealtimeService._update_quote_time("20260828161437", trusted=True)
        FundRealtimeService._update_quote_time("20260827090000", trusted=True)
        assert FundRealtimeService._spot_quote_time == "2026-08-28 16:14", "旧时间不应回退"

    def test_untrusted_only_fills_empty(self):
        FundRealtimeService._update_quote_time("20260828161437", trusted=True)
        FundRealtimeService._update_quote_time("", trusted=False)
        assert FundRealtimeService._spot_quote_time == "2026-08-28 16:14", \
            "服务器时间不得覆盖行情源时间"

    def test_untrusted_fills_when_empty(self):
        FundRealtimeService._update_quote_time("", trusted=False)
        assert FundRealtimeService._spot_quote_time != ""
