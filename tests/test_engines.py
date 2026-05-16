"""因子引擎 + 评分引擎 单元测试"""

import sys
import os
import pytest
import numpy as np

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.data_sources.base import FundData
from backend.engines.factor_engine import (
    FactorEngine,
    FactorScoreResult,
    calculate_pe_percentile,
    calculate_fed_model,
    calculate_macd_signal,
    calculate_ma_trend,
    calculate_volume_change,
)
from backend.engines.scoring_engine import ScoringEngine


# ── 测试数据工厂 ─────────────────────────────────────────────────────

def make_fund_data(
    code: str = "510300",
    name: str = "沪深300ETF",
    pe: float = 12.0,
    bond_yield: float = 2.8,
    close_history_len: int = 120,
    volume_history_len: int = 120,
    trend: str = "up",  # up / down / flat
) -> FundData:
    """构造测试用 FundData（匹配实际 dataclass 定义）"""
    np.random.seed(42)

    base_price = 4.0
    if trend == "up":
        deltas = np.random.randn(close_history_len) * 0.02 + 0.001
    elif trend == "down":
        deltas = np.random.randn(close_history_len) * 0.02 - 0.001
    else:
        deltas = np.random.randn(close_history_len) * 0.01

    closes = base_price + np.cumsum(deltas)
    closes = np.maximum(closes, 0.5)

    volumes = np.random.randint(1000000, 5000000, size=volume_history_len).astype(float)

    return FundData(
        code=code,
        name=name,
        date="2025-05-16",
        pe=pe,
        pb=1.5,
        close=float(closes[-1]),
        close_history=closes.tolist(),
        volume=float(volumes[-1]),
        volume_history=volumes.tolist(),
        index_close=float(closes[-1]),
        bond_yield=bond_yield,
    )


# ── PE 百分位因子测试 ──────────────────────────────────────────────

class TestPEPercentile:
    def test_returns_score_in_range(self):
        fd = make_fund_data()
        result = calculate_pe_percentile(fd)
        assert 0 <= result.score <= 5, f"评分 {result.score} 超出范围"

    def test_data_missing_returns_default(self):
        fd = FundData(code="000001", pe=None, close_history=[])
        result = calculate_pe_percentile(fd)
        assert result.score == 2.5

    def test_direction_is_positive(self):
        fd = make_fund_data()
        result = calculate_pe_percentile(fd)
        assert result.direction == "positive"


# ── FED 模型因子测试 ──────────────────────────────────────────────

class TestFEDModel:
    def test_high_fed_gets_high_score(self):
        fd = make_fund_data(pe=10.0, bond_yield=2.0)
        result = calculate_fed_model(fd)
        assert result.score >= 4.0, f"高FED差值得分 {result.score} 应≥4"

    def test_low_fed_gets_low_score(self):
        fd = make_fund_data(pe=100.0, bond_yield=3.0)
        result = calculate_fed_model(fd)
        assert result.score <= 2.5, f"低FED差值得分 {result.score} 应≤2.5"

    def test_missing_data_returns_default(self):
        fd = FundData(code="000001", pe=None, bond_yield=None)
        result = calculate_fed_model(fd)
        assert result.score == 2.5

    def test_zero_pe_returns_default(self):
        fd = make_fund_data(pe=0.0)
        result = calculate_fed_model(fd)
        assert result.score == 2.5

    def test_raw_value_is_fed_difference(self):
        fd = make_fund_data(pe=12.0, bond_yield=2.8)
        result = calculate_fed_model(fd)
        expected_fed = (1.0 / 12.0 * 100) - 2.8
        assert abs(result.raw_value - round(expected_fed, 4)) < 0.01


# ── MACD 信号因子测试 ──────────────────────────────────────────────

class TestMACDSignal:
    def test_score_in_range(self):
        fd = make_fund_data(trend="up")
        result = calculate_macd_signal(fd)
        assert 0 <= result.score <= 5

    def test_data_insufficient_returns_default(self):
        fd = make_fund_data(close_history_len=10)
        result = calculate_macd_signal(fd)
        assert result.score == 2.5

    def test_uptrend_scores_higher(self):
        fd_up = make_fund_data(trend="up")
        fd_down = make_fund_data(trend="down")
        score_up = calculate_macd_signal(fd_up).score
        score_down = calculate_macd_signal(fd_down).score
        assert score_up >= score_down, f"上升趋势 {score_up} 应≥下降趋势 {score_down}"


# ── 均线趋势因子测试 ──────────────────────────────────────────────

class TestMATrend:
    def test_score_in_range(self):
        fd = make_fund_data()
        result = calculate_ma_trend(fd)
        assert 0 <= result.score <= 5

    def test_data_insufficient_returns_default(self):
        fd = make_fund_data(close_history_len=30)
        result = calculate_ma_trend(fd)
        assert result.score == 2.5

    def test_uptrend_scores_higher(self):
        fd_up = make_fund_data(trend="up")
        fd_down = make_fund_data(trend="down")
        score_up = calculate_ma_trend(fd_up).score
        score_down = calculate_ma_trend(fd_down).score
        assert score_up >= score_down, f"上升趋势 {score_up} 应≥下降趋势 {score_down}"


# ── 成交量变化因子测试 ──────────────────────────────────────────────

class TestVolumeChange:
    def test_score_in_range(self):
        fd = make_fund_data()
        result = calculate_volume_change(fd)
        assert 0 <= result.score <= 5

    def test_data_insufficient_returns_default(self):
        fd = make_fund_data(volume_history_len=10, close_history_len=10)
        result = calculate_volume_change(fd)
        assert result.score == 2.5


# ── 因子引擎统一入口测试 ──────────────────────────────────────────────

class TestFactorEngine:
    def test_calculate_all_returns_5_scores(self):
        fd = make_fund_data()
        engine = FactorEngine()
        factors = [
            {"code": "pe_percentile", "name": "PE百分位", "params": "{}", "direction": "positive"},
            {"code": "fed_model", "name": "股债性价比FED", "params": "{}", "direction": "positive"},
            {"code": "macd_signal", "name": "MACD信号", "params": "{}", "direction": "positive"},
            {"code": "ma_trend", "name": "均线趋势", "params": "{}", "direction": "positive"},
            {"code": "volume_change", "name": "成交量变化", "params": "{}", "direction": "positive"},
        ]
        results = engine.calculate_all(fd, factors)
        assert len(results) == 5
        for r in results:
            assert 0 <= r.score <= 5, f"因子 {r.factor_code} 评分 {r.score} 超出范围"

    def test_unknown_factor_returns_default(self):
        fd = make_fund_data()
        engine = FactorEngine()
        factors = [{"code": "unknown_factor", "name": "未知因子", "params": "{}", "direction": "positive"}]
        results = engine.calculate_all(fd, factors)
        assert results[0].score == 2.5

    def test_negative_direction_flips_score(self):
        fd = make_fund_data()
        engine = FactorEngine()
        factors_pos = [{"code": "fed_model", "name": "FED", "params": "{}", "direction": "positive"}]
        result_pos = engine.calculate_all(fd, factors_pos)[0]
        factors_neg = [{"code": "fed_model", "name": "FED", "params": "{}", "direction": "negative"}]
        result_neg = engine.calculate_all(fd, factors_neg)[0]
        assert abs(result_pos.score + result_neg.score - 5.0) < 0.01, \
            f"正反向评分之和应为5: {result_pos.score} + {result_neg.score}"


# ── 评分引擎测试 ──────────────────────────────────────────────────────

class TestScoringEngine:
    def setup_method(self):
        self.engine = ScoringEngine()

    def _make_factor_scores(self, scores: list, codes: list = None) -> list:
        if codes is None:
            codes = [f"factor_{i}" for i in range(len(scores))]
        return [
            FactorScoreResult(
                factor_code=c, factor_name=f"因子{c}", raw_value=s, score=s, direction="positive"
            )
            for c, s in zip(codes, scores)
        ]

    def test_score_3_5_is_buy(self):
        scores = self._make_factor_scores([3.5])
        result = self.engine.compute(scores, [1.0])
        assert result.signal_direction == "buy"
        assert result.signal_strength == "light_buy"

    def test_score_4_0_is_moderate_buy(self):
        scores = self._make_factor_scores([4.0])
        result = self.engine.compute(scores, [1.0])
        assert result.signal_direction == "buy"
        assert result.signal_strength == "moderate_buy"

    def test_score_4_5_is_heavy_buy(self):
        scores = self._make_factor_scores([4.5])
        result = self.engine.compute(scores, [1.0])
        assert result.signal_direction == "buy"
        assert result.signal_strength == "heavy_buy"

    def test_score_2_0_is_sell(self):
        scores = self._make_factor_scores([2.0])
        result = self.engine.compute(scores, [1.0])
        assert result.signal_direction == "sell"
        assert result.signal_strength == "light_sell"

    def test_score_1_5_is_moderate_sell(self):
        scores = self._make_factor_scores([1.5])
        result = self.engine.compute(scores, [1.0])
        assert result.signal_direction == "sell"
        assert result.signal_strength == "moderate_sell"

    def test_score_0_8_is_heavy_sell(self):
        scores = self._make_factor_scores([0.8])
        result = self.engine.compute(scores, [1.0])
        assert result.signal_direction == "sell"
        assert result.signal_strength == "heavy_sell"

    def test_score_3_0_is_hold(self):
        scores = self._make_factor_scores([3.0])
        result = self.engine.compute(scores, [1.0])
        assert result.signal_direction == "hold"

    def test_weighted_calculation(self):
        scores = self._make_factor_scores([4.0, 2.0], ["a", "b"])
        result = self.engine.compute(scores, [1.5, 1.0])
        assert abs(result.weighted_score - 3.2) < 0.01

    def test_empty_scores_returns_hold(self):
        result = self.engine.compute([], [])
        assert result.signal_direction == "hold"
        assert result.weighted_score == 2.5

    def test_advice_not_empty(self):
        scores = self._make_factor_scores([3.8])
        result = self.engine.compute(scores, [1.0])
        assert len(result.operation_advice) > 0

    def test_custom_thresholds(self):
        scores = self._make_factor_scores([3.0])
        result = self.engine.compute(scores, [1.0], buy_threshold=3.0)
        assert result.signal_direction == "buy"
