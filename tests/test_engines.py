"""因子引擎 + 评分引擎 单元测试（-1~+1 分值体系）"""

import sys
import os
import pytest
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.data_sources.base import FundData
from backend.engines.factor_engine import (
    FactorEngine,
    FactorScoreResult,
    calculate_pe_percentile,
    calculate_fed_model,
    calculate_macd_signal,
    calculate_momentum_6m,
    calculate_inv_volatility,
    calculate_info_ratio,
    calculate_max_drawdown,
    calculate_size_stability,
    apply_cross_sectional_zscore,
    evaluate_signal_rules,
)
from backend.engines.scoring_engine import ScoringEngine


# ── 测试数据工厂 ─────────────────────────────────────────────────────

def make_fund_data(
    code: str = "510300",
    name: str = "沪深300ETF",
    pe: float = 12.0,
    bond_yield: float = 2.8,
    close_history_len: int = 250,
    volume_history_len: int = 250,
    trend: str = "up",
) -> FundData:
    """构造测试用 FundData"""
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
        assert -1 <= result.score <= 1, f"评分 {result.score} 超出 -1~+1 范围"

    def test_data_missing_returns_neutral(self):
        fd = FundData(code="000001", pe=None, close_history=[])
        result = calculate_pe_percentile(fd)
        assert result.score == 0.0

    def test_direction_is_negative(self):
        fd = make_fund_data()
        result = calculate_pe_percentile(fd)
        assert result.direction == "negative"


# ── FED 模型因子测试 ──────────────────────────────────────────────

class TestFEDModel:
    def test_high_fed_gets_high_score(self):
        fd = make_fund_data(pe=8.0, bond_yield=1.5, close_history_len=250)
        result = calculate_fed_model(fd)
        assert result.score >= 0.5, f"高FED得分 {result.score} 应≥0.5"

    def test_low_fed_gets_low_score(self):
        fd = make_fund_data(pe=80.0, bond_yield=4.0, close_history_len=250)
        result = calculate_fed_model(fd)
        assert result.score <= 0.0, f"低FED得分 {result.score} 应≤0"

    def test_missing_data_returns_neutral(self):
        fd = FundData(code="000001", pe=None, bond_yield=None)
        result = calculate_fed_model(fd)
        assert result.score == 0.0

    def test_zero_pe_returns_neutral(self):
        fd = make_fund_data(pe=0.0)
        result = calculate_fed_model(fd)
        assert result.score == 0.0

    def test_raw_value_is_fed_difference(self):
        fd = make_fund_data(pe=12.0, bond_yield=2.8)
        result = calculate_fed_model(fd)
        expected_fed = (1.0 / 12.0 * 100) - 2.8
        assert abs(result.raw_value - round(expected_fed, 4)) < 0.01


# ── 动量因子测试 ──────────────────────────────────────────────────

class TestMomentum6m:
    def test_score_in_range(self):
        fd = make_fund_data(trend="up", close_history_len=200)
        result = calculate_momentum_6m(fd)
        assert -1 <= result.score <= 1

    def test_uptrend_scores_positive(self):
        fd_up = make_fund_data(trend="up", close_history_len=200)
        fd_down = make_fund_data(trend="down", close_history_len=200)
        score_up = calculate_momentum_6m(fd_up).score
        score_down = calculate_momentum_6m(fd_down).score
        assert score_up >= score_down, f"上升 {score_up} 应≥下降 {score_down}"

    def test_data_insufficient_returns_neutral(self):
        fd = make_fund_data(close_history_len=50)
        result = calculate_momentum_6m(fd)
        assert result.score == 0.0


# ── MACD 信号因子测试 ──────────────────────────────────────────────

class TestMACDSignal:
    def test_score_in_range(self):
        fd = make_fund_data(trend="up")
        result = calculate_macd_signal(fd)
        assert -1 <= result.score <= 1

    def test_data_insufficient_returns_neutral(self):
        fd = make_fund_data(close_history_len=30)
        result = calculate_macd_signal(fd)
        assert result.score == 0.0

    def test_uptrend_scores_higher(self):
        fd_up = make_fund_data(trend="up")
        fd_down = make_fund_data(trend="down")
        score_up = calculate_macd_signal(fd_up).score
        score_down = calculate_macd_signal(fd_down).score
        assert score_up >= score_down, f"上升 {score_up} 应≥下降 {score_down}"


# ── 波动率倒数因子测试 ──────────────────────────────────────────────

class TestInvVolatility:
    def test_score_positive(self):
        fd = make_fund_data()
        result = calculate_inv_volatility(fd)
        assert result.score >= 0  # 低波动得正分

    def test_data_insufficient_returns_neutral(self):
        fd = make_fund_data(close_history_len=30)
        result = calculate_inv_volatility(fd)
        assert result.score == 0.0


# ── 信息比率因子测试 ──────────────────────────────────────────────

class TestInfoRatio:
    def test_score_returns_value(self):
        fd = make_fund_data(close_history_len=300)
        result = calculate_info_ratio(fd)
        assert isinstance(result.score, float)

    def test_data_insufficient_returns_neutral(self):
        fd = make_fund_data(close_history_len=50)
        result = calculate_info_ratio(fd)
        assert result.score == 0.0

    def test_benchmark_missing_returns_neutral(self):
        fd = make_fund_data(close_history_len=300)
        fd.benchmark_history = []
        result = calculate_info_ratio(fd)
        assert result.score == 0.0


# ── 最大回撤因子测试 ──────────────────────────────────────────────

class TestMaxDrawdown:
    def test_score_positive(self):
        fd = make_fund_data()
        result = calculate_max_drawdown(fd)
        # 负的 MDD → 回撤越小值越高
        assert isinstance(result.score, float)

    def test_data_insufficient_returns_neutral(self):
        fd = make_fund_data(close_history_len=50)
        result = calculate_max_drawdown(fd)
        assert result.score == 0.0

    def test_raw_value_is_mdd(self):
        fd = make_fund_data()
        result = calculate_max_drawdown(fd)
        assert result.raw_value >= 0.0  # MDD is always >= 0


# ── 规模稳定性因子测试 ──────────────────────────────────────────────

class TestSizeStability:
    def test_score_returns_value(self):
        fd = make_fund_data()
        fd.fund_size_history = [1e9, 1.1e9, 0.9e9, 1.05e9]
        result = calculate_size_stability(fd)
        assert isinstance(result.score, float)

    def test_data_insufficient_returns_neutral(self):
        fd = make_fund_data()
        fd.fund_size_history = []
        result = calculate_size_stability(fd)
        assert result.score == 0.0

    def test_single_quarter_returns_neutral(self):
        fd = make_fund_data()
        fd.fund_size_history = [1e9]
        result = calculate_size_stability(fd)
        assert result.score == 0.0


# ── 信号规则评估测试 ──────────────────────────────────────────────

class TestSignalRules:
    def test_less_equal(self):
        assert evaluate_signal_rules(0.15, [{"condition": "<= 0.2", "score": 1.0}]) == 1.0

    def test_greater_than(self):
        assert evaluate_signal_rules(0.9, [{"condition": "> 0.8", "score": -1.0}]) == -1.0

    def test_range_condition(self):
        rules = [{"condition": ">= -0.5 and <= 0.5", "score": 0.5}]
        assert evaluate_signal_rules(0.0, rules) == 0.5
        # 0.6 不在 [0.5, 0.5] 范围内 → 无匹配 → 返回 0.0
        assert evaluate_signal_rules(0.6, rules) == 0.0

    def test_else_fallback(self):
        rules = [{"condition": "> 10", "score": 1.0}, {"condition": "else", "score": 0.0}]
        assert evaluate_signal_rules(5.0, rules) == 0.0

    def test_empty_rules_returns_value(self):
        assert evaluate_signal_rules(0.8, []) == 0.8

    def test_no_match_returns_zero(self):
        rules = [{"condition": "> 10", "score": 1.0}]
        assert evaluate_signal_rules(5.0, rules) == 0.0


# ── 截面标准化测试 ──────────────────────────────────────────────

class TestCrossSectionalZScore:
    def test_normalization(self):
        scores = {"A": 10.0, "B": 20.0, "C": 30.0}
        result = apply_cross_sectional_zscore(scores)
        assert result["C"] >= result["A"]
        for v in result.values():
            assert -1 <= v <= 1

    def test_single_value_returns_zero(self):
        result = apply_cross_sectional_zscore({"A": 1.0})
        assert result["A"] == 0.0

    def test_custom_thresholds(self):
        scores = {"A": 5.0, "B": 5.1, "C": 100.0}
        result = apply_cross_sectional_zscore(scores, [0.5, 0, -0.5])
        for v in result.values():
            assert -1 <= v <= 1

    def test_old_3_threshold_compatibility(self):
        # 旧版 3 阈值 [1.0, 0, -1.0] 应自动扩展为新版 4 阈值
        scores = {"A": 0.2, "B": 0, "C": -0.3}
        result = apply_cross_sectional_zscore(scores, [1.0, 0, -1.0])
        for v in result.values():
            assert -1 <= v <= 1


# ── 因子引擎统一入口测试 ──────────────────────────────────────────────

class TestFactorEngine:
    def test_calculate_all_returns_8_scores(self):
        fd = make_fund_data(close_history_len=300)
        fd.benchmark_history = fd.close_history[:]
        fd.fund_size_history = [1e9, 1.1e9, 0.9e9, 1.05e9]
        engine = FactorEngine()
        factors = [
            {"code": "pe_percentile", "name": "PE百分位", "params": "{}", "direction": "negative"},
            {"code": "fed_model", "name": "股债性价比FED", "params": "{}", "direction": "positive"},
            {"code": "momentum_6m", "name": "动量因子", "params": "{}", "direction": "positive"},
            {"code": "inv_volatility", "name": "波动率倒数", "params": "{}", "direction": "positive"},
            {"code": "info_ratio", "name": "信息比率", "params": "{}", "direction": "positive"},
            {"code": "macd_signal", "name": "MACD信号", "params": "{}", "direction": "positive"},
            {"code": "max_drawdown", "name": "最大回撤", "params": "{}", "direction": "positive"},
            {"code": "size_stability", "name": "规模稳定性", "params": "{}", "direction": "positive"},
        ]
        results = engine.calculate_all(fd, factors)
        assert len(results) == 8
        # 非标准化因子应在 -1~+1 范围；截面标准化因子的 pre-norm 值为原始值
        for r in results:
            if r.factor_code in ("inv_volatility", "info_ratio", "max_drawdown", "size_stability"):
                assert isinstance(r.score, float), f"{r.factor_code} 应为 float"
            else:
                assert -1 <= r.score <= 1, f"因子 {r.factor_code} 评分 {r.score} 超出 -1~+1"

    def test_unknown_factor_returns_neutral(self):
        fd = make_fund_data()
        engine = FactorEngine()
        factors = [{"code": "unknown_factor", "name": "未知因子", "params": "{}", "direction": "positive"}]
        results = engine.calculate_all(fd, factors)
        assert results[0].score == 0.0

    def test_cross_sectional_normalization(self):
        engine = FactorEngine()
        factors = [
            {"code": "inv_volatility", "name": "波动率倒数", "params": "{}", "direction": "positive",
             "normalization": "cross_sectional_zscore", "normalization_config": {"zscore_thresholds": [1.0, 0, -1.0]}},
            {"code": "pe_percentile", "name": "PE百分位", "params": "{}", "direction": "negative",
             "normalization": "none"},
        ]
        fd_a = make_fund_data(code="A", close_history_len=200)
        fd_b = make_fund_data(code="B", close_history_len=200)
        fd_c = make_fund_data(code="C", close_history_len=200)

        results = {
            "A": engine.calculate_all(fd_a, factors),
            "B": engine.calculate_all(fd_b, factors),
            "C": engine.calculate_all(fd_c, factors),
        }

        # 对因子索引 0 (inv_volatility) 做截面标准化
        # 由于测试数据随机，只验证接口正常执行
        normalized = engine.normalize_cross_sectional(results, factors)
        assert len(normalized) == 3
        for code, scores in normalized.items():
            assert -1 <= scores[0].score <= 1


# ── 评分引擎测试（-1~+1 因子分值 → -6~+6 总分）────────────────────────

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

    def test_high_score_is_buy(self):
        # 单因子权重 6.0，score=1 → weighted_sum=6.0 → signal=buy
        scores = self._make_factor_scores([1.0])
        result = self.engine.compute(scores, [6.0])
        assert result.signal_direction == "buy"
        assert result.weighted_score >= 3.0

    def test_low_score_is_sell(self):
        scores = self._make_factor_scores([-1.0])
        result = self.engine.compute(scores, [6.0])
        assert result.signal_direction == "sell"
        assert result.weighted_score <= -3.0

    def test_neutral_score_is_hold(self):
        scores = self._make_factor_scores([0.0])
        result = self.engine.compute(scores, [6.0])
        assert result.signal_direction == "hold"

    def test_weighted_calculation(self):
        # score=-0.5 w=1.2 → -0.6, score=1.0 w=1.0 → 1.0, total=0.4
        scores = self._make_factor_scores([-0.5, 1.0], ["a", "b"])
        result = self.engine.compute(scores, [1.2, 1.0])
        assert abs(result.weighted_score - 0.4) < 0.01

    def test_empty_scores_returns_hold(self):
        result = self.engine.compute([], [])
        assert result.signal_direction == "hold"
        assert result.weighted_score == 0.0

    def test_advice_not_empty(self):
        scores = self._make_factor_scores([0.8])
        result = self.engine.compute(scores, [6.0])
        assert len(result.operation_advice) > 0

    def test_equity_ratio_matches_tier(self):
        scores = self._make_factor_scores([1.0])
        result = self.engine.compute(scores, [6.0])
        # weighted_score=6.0 → heavy_buy → equity_ratio=0.9
        assert result.equity_ratio == 0.9

    def test_clamp_to_range(self):
        scores = self._make_factor_scores([2.0])  # impossible but test clamping
        result = self.engine.compute(scores, [6.4])
        assert result.weighted_score <= 6.4
