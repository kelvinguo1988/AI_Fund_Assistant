"""实时估值服务回归测试 — fundgz 解析 / 持仓加权模型 / 降级链"""

import sys, os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.fund_realtime_service import (
    FundRealtimeService,
    parse_fundgz_jsonp,
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

        async def _fake_stock_spot(self):
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

    async def _fake_etf(self):
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

        async def _fake_stock(self):
            return {"300308": -0.9}

        async def _fake_hk(self):
            return {"00981": 1.23}

        monkeypatch.setattr(FundRealtimeService, "_get_stock_spot", _fake_stock)
        monkeypatch.setattr(FundRealtimeService, "_get_hk_spot", _fake_hk)
        result = asyncio.run(svc.get_top10_changes(1))
        by_code = {r["stock_code"]: r["pct"] for r in result}
        assert by_code["300308"] == -0.9
        assert by_code["00981"] == 1.23
