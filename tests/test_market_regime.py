"""市场环境因子 & 阈值调节测试（2026-08-28 新增模块）

覆盖：
1. 三个市场环境因子的打分规则（估值分位/情绪/资金面）
2. 快照缺失时中性 0 分兜底
3. compute_dynamic_thresholds 极端估值调节（高估上调/低估下调/无快照不动）
4. MarketRegimeService 分位计算（mock 数据源，无网络）
5. 融资融券 7 日变化率计算（mock）
"""

import asyncio
import sys, os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.data_sources.base import FundData
from backend.engines import factor_engine as fe
from backend.engines.factor_engine import (
    FactorScoreResult,
    calculate_market_valuation,
    calculate_market_sentiment,
    calculate_market_fund_flow,
    set_current_regime,
    get_current_regime,
    FACTOR_CALCULATORS,
)
from backend.engines.quality_filter import (
    compute_dynamic_thresholds,
    QUALITY_CONFIG,
)
from backend.services.market_regime_service import (
    MarketRegimeService,
    MarketRegimeSnapshot,
)


def make_fd(**overrides) -> FundData:
    defaults = dict(code="000001", name="测试", date="2026-01-01",
                    close=1.5, close_history=[])
    defaults.update(overrides)
    return FundData(**defaults)


def make_snap(**overrides) -> MarketRegimeSnapshot:
    return MarketRegimeSnapshot(**overrides)


@pytest.fixture(autouse=True)
def reset_regime():
    """每个测试后清空模块级 regime，避免测试间污染"""
    yield
    set_current_regime(None)


# ── 1. 大盘估值分位因子 ────────────────────────────────────────────

class TestMarketValuation:
    def test_low_percentile_high_score(self):
        set_current_regime(make_snap(valuation_percentile=0.15))
        r = calculate_market_valuation(make_fd())
        assert r.score == 1.0
        assert r.direction == "negative"
        assert r.raw_value == pytest.approx(0.15)

    def test_high_percentile_min_score(self):
        set_current_regime(make_snap(valuation_percentile=0.9))
        r = calculate_market_valuation(make_fd())
        assert r.score == -1.0

    def test_mid_percentile_neutral(self):
        set_current_regime(make_snap(valuation_percentile=0.5))
        r = calculate_market_valuation(make_fd())
        assert r.score == 0.0

    def test_no_snapshot_neutral(self):
        set_current_regime(None)
        r = calculate_market_valuation(make_fd())
        assert r.score == 0.0
        assert r.raw_value == 0.0

    def test_snapshot_field_none_neutral(self):
        set_current_regime(make_snap(valuation_percentile=None))
        r = calculate_market_valuation(make_fd())
        assert r.score == 0.0


# ── 2. 市场情绪因子 ────────────────────────────────────────────────

class TestMarketSentiment:
    def test_strong_up_day(self):
        set_current_regime(make_snap(adv_decline_ratio=0.7))
        r = calculate_market_sentiment(make_fd())
        assert r.score == 1.0

    def test_strong_down_day(self):
        set_current_regime(make_snap(adv_decline_ratio=-0.8))
        r = calculate_market_sentiment(make_fd())
        assert r.score == -1.0

    def test_balanced_day(self):
        set_current_regime(make_snap(adv_decline_ratio=0.0))
        r = calculate_market_sentiment(make_fd())
        assert r.score == 0.0

    def test_no_snapshot_neutral(self):
        set_current_regime(None)
        assert calculate_market_sentiment(make_fd()).score == 0.0


# ── 3. 资金面因子 ──────────────────────────────────────────────────

class TestMarketFundFlow:
    def test_margin_inflow(self):
        set_current_regime(make_snap(margin_change_pct_7d=0.05))
        r = calculate_market_fund_flow(make_fd())
        assert r.score == 1.0

    def test_margin_outflow(self):
        set_current_regime(make_snap(margin_change_pct_7d=-0.04))
        r = calculate_market_fund_flow(make_fd())
        assert r.score == -1.0

    def test_flat(self):
        set_current_regime(make_snap(margin_change_pct_7d=0.0))
        assert calculate_market_fund_flow(make_fd()).score == 0.0

    def test_no_snapshot_neutral(self):
        set_current_regime(None)
        assert calculate_market_fund_flow(make_fd()).score == 0.0


# ── 4. 因子注册表 ──────────────────────────────────────────────────

class TestRegistration:
    def test_registered_in_registry(self):
        for code in ("market_valuation", "market_sentiment", "market_fund_flow"):
            assert code in FACTOR_CALCULATORS


# ── 5. 极端估值阈值调节 ────────────────────────────────────────────

class TestDynamicThresholdRegime:
    def test_baseline_no_regime(self):
        set_current_regime(None)
        buy, sell = compute_dynamic_thresholds(False, False)
        assert buy == QUALITY_CONFIG["base_buy_threshold"]
        assert sell == QUALITY_CONFIG["base_sell_threshold"]

    def test_baseline_mid_percentile_no_adjust(self):
        set_current_regime(make_snap(valuation_percentile=0.5))
        buy, _ = compute_dynamic_thresholds(False, False)
        assert buy == QUALITY_CONFIG["base_buy_threshold"]

    def test_extreme_high_valuation_raises_buy(self):
        set_current_regime(make_snap(valuation_percentile=0.9))
        buy, _ = compute_dynamic_thresholds(False, False)
        assert buy == pytest.approx(
            QUALITY_CONFIG["base_buy_threshold"]
            + QUALITY_CONFIG["extreme_high_valuation_buy_increment"]
        )

    def test_extreme_low_valuation_lowers_buy(self):
        set_current_regime(make_snap(valuation_percentile=0.1))
        buy, _ = compute_dynamic_thresholds(False, False)
        assert buy == pytest.approx(
            QUALITY_CONFIG["base_buy_threshold"]
            - QUALITY_CONFIG["extreme_low_valuation_buy_decrement"]
        )

    def test_low_valuation_floor_not_below_half(self):
        # 配置超低下调把买入阈值打到 0.5 地板（base 1.5 - 0.5*3 = 0 → 钳到 0.5）
        cfg = dict(QUALITY_CONFIG)
        cfg["extreme_low_valuation_buy_decrement"] = 3.0
        set_current_regime(make_snap(valuation_percentile=0.05))
        buy, _ = compute_dynamic_thresholds(False, False, cfg)
        assert buy == 0.5

    def test_sell_threshold_untouched_by_regime(self):
        set_current_regime(make_snap(valuation_percentile=0.95))
        _, sell = compute_dynamic_thresholds(False, False)
        assert sell == QUALITY_CONFIG["base_sell_threshold"]


# ── 6. MarketRegimeService（mock 数据源，无网络） ────────────────────

class TestMarketRegimeService:
    def _patch_sources(self, monkeypatch, svc, pe_series=None, adv=None, margin=None):
        async def _pe():
            return pe_series
        async def _adv(snap):
            if adv is not None:
                snap.up_count = adv[0]
                snap.down_count = adv[1]
                snap.adv_decline_ratio = round((adv[0] - adv[1]) / (adv[0] + adv[1]), 4)
        async def _margin(snap):
            if margin is not None:
                snap.margin_balance = margin[-1]
                snap.margin_change_pct_7d = round(margin[-1] / margin[-8] - 1, 6)
        monkeypatch.setattr(svc, "_get_index_pe_series", _pe)
        monkeypatch.setattr(svc, "_fill_adv_decline", _adv)
        monkeypatch.setattr(svc, "_fill_margin_flow", _margin)
        monkeypatch.setattr(MarketRegimeService, "_snapshot", None)
        monkeypatch.setattr(MarketRegimeService, "_snapshot_ts", 0.0)

    def test_valuation_percentile_computed(self, monkeypatch):
        svc = MarketRegimeService()
        # 1300 天 PE 序列：前 1200 天 10~20 线性，最近 PE=12（低分位）
        pes = [10 + 10 * i / 1200 for i in range(1200)] + [12.0] * 100
        dates = [f"2022-01-{i%28+1:02d}" for i in range(1300)]
        self._patch_sources(monkeypatch, svc, pe_series=(dates, pes),
                            adv=(3000, 2000), margin=[100.0] * 7 + [103.0])
        snap = asyncio.run(svc.get_snapshot())
        # PE=12 在 10~20 区间约 0.2 分位（前 1200 天中 24% ≤12）
        assert snap.valuation_percentile is not None
        assert 0.0 <= snap.valuation_percentile <= 1.0
        assert snap.valuation_percentile < 0.35
        assert snap.up_count == 3000
        assert snap.adv_decline_ratio == pytest.approx(0.2, abs=1e-6)
        assert snap.margin_change_pct_7d == pytest.approx(0.03, abs=1e-6)

    def test_snapshot_cached(self, monkeypatch):
        svc = MarketRegimeService()
        self._patch_sources(monkeypatch, svc, pe_series=None, adv=None, margin=None)
        s1 = asyncio.run(svc.get_snapshot())
        s2 = asyncio.run(svc.get_snapshot())
        assert s1 is s2  # TTL 内命中缓存返回同一对象

    def test_all_sources_fail_returns_none_fields(self, monkeypatch):
        svc = MarketRegimeService()
        async def _raise_pe():
            raise RuntimeError("network down")
        async def _raise_fill(snap):
            raise RuntimeError("network down")
        monkeypatch.setattr(svc, "_get_index_pe_series", _raise_pe)
        monkeypatch.setattr(svc, "_fill_adv_decline", _raise_fill)
        monkeypatch.setattr(svc, "_fill_margin_flow", _raise_fill)
        monkeypatch.setattr(MarketRegimeService, "_snapshot", None)
        monkeypatch.setattr(MarketRegimeService, "_snapshot_ts", 0.0)
        snap = asyncio.run(svc.get_snapshot())  # 不抛异常
        assert snap.valuation_percentile is None
        assert snap.adv_decline_ratio is None
        assert snap.margin_change_pct_7d is None

    def test_short_pe_history_skips_percentile(self, monkeypatch):
        svc = MarketRegimeService()
        self._patch_sources(monkeypatch, svc, pe_series=(["2026-01-01"] * 100, [12.0] * 100),
                            adv=None, margin=None)
        snap = asyncio.run(svc.get_snapshot())
        assert snap.valuation_percentile is None  # 数据不足 250 行


# ── 7. 端到端：因子引擎计算市场因子（注入快照） ─────────────────────

class TestFactorEngineIntegration:
    def test_calculate_all_with_regime(self):
        set_current_regime(make_snap(
            valuation_percentile=0.1,
            adv_decline_ratio=0.6,
            margin_change_pct_7d=0.04,
        ))
        factors = [
            {"code": "market_valuation", "name": "大盘估值分位",
             "params": "{}", "weight": 0.8, "direction": "negative",
             "normalization": "none"},
            {"code": "market_sentiment", "name": "市场情绪",
             "params": "{}", "weight": 0.5, "direction": "positive",
             "normalization": "none"},
            {"code": "market_fund_flow", "name": "资金面",
             "params": "{}", "weight": 0.5, "direction": "positive",
             "normalization": "none"},
        ]
        results = fe.factor_engine.calculate_all(make_fd(), factors)
        scores = {r.factor_code: r.score for r in results}
        assert scores["market_valuation"] == 1.0
        assert scores["market_sentiment"] == 1.0
        assert scores["market_fund_flow"] == 1.0
        # 全市场因子对任何基金同分（与 fund_data 无关）
        results2 = fe.factor_engine.calculate_all(make_fd(code="510300"), factors)
        assert {r.score for r in results2} == {1.0}
