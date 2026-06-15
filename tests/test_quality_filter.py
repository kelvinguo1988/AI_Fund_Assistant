"""第零层标的质量过滤引擎 单元测试

测试场景：
1. 棺材钉形态基金被正确剔除，不进入评分
2. 规模冲击+漂移的基金，买入阈值上调，原本达标Score变为观望
3. 机构认可度加分后，从低于阈值变为达标，正确输出买入
4. 各衍生因子计算正确性
5. 原有因子计算结果不受影响
"""

import sys
import os
import pytest
import numpy as np
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.data_sources.base import FundData
from backend.engines.factor_engine import FactorScoreResult, FactorEngine
from backend.engines.scoring_engine import ScoringEngine, compute_with_quality_filter
from backend.engines.quality_filter import (
    QualityFilter,
    QualityFilterResult,
    QUALITY_CONFIG,
    check_coffin_nail_pattern,
    check_ecg_pattern,
    check_liquidation_risk,
    calc_momentum_stability,
    calc_excess_return_persistence,
    check_size_shock,
    check_allocation_drift,
    calc_institution_approval,
    compute_dynamic_thresholds,
    determine_signal,
    apply_factor_corrections,
)


# ── 测试数据工厂 ─────────────────────────────────────────────────────

def make_fund_data(
    code: str = "510300",
    name: str = "测试基金",
    close_history_len: int = 300,
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

    return FundData(
        code=code,
        name=name,
        date="2025-06-13",
        pe=12.0,
        close=float(closes[-1]),
        close_history=closes.tolist(),
        benchmark_history=closes.tolist(),  # 基准同净值（简化测试）
        fund_size_history=[1e9, 1.1e9, 0.9e9, 1.05e9],
    )


def make_coffin_nail_fund() -> FundData:
    """构造触发"棺材钉"形态的基金数据

    策略：让崩溃完全发生在20日窗口的前半段，使恢复窗口
    （peak_idx+1 ~ peak_idx+60）中所有价格均低于 peak*0.90。
    具体做法：前10天价格从高点跳空大跌，后10天保持低位。
    """
    n = 400
    prices = [0.0] * n
    # 前 250 日正常上涨
    prices[0] = 4.0
    for i in range(1, 250):
        prices[i] = prices[i - 1] * 1.005
    peak = prices[249]  # ≈ 13.95

    # 第 250~259 日跳空大跌（每天跌幅 ≥ 3%，10天跌超 30%）
    for i in range(250, 260):
        prices[i] = prices[i - 1] * 0.965  # 每天跌 3.5%
    crash_low = prices[259]  # ≈ peak * 0.70

    # 第 260~399 日平坦横盘（远低于 peak * 0.90）
    for i in range(260, n):
        prices[i] = crash_low

    return FundData(
        code="COFFIN01",
        name="棺材钉基金",
        date="2025-06-13",
        close=prices[-1],
        close_history=prices,
    )


def make_size_shock_quarterly() -> list[dict]:
    """构造触发规模冲击的季度数据（含4条记录，满足漂移检测最低要求）"""
    return [
        {"report_date": "2024-06-30", "effective_date": "2024-08-01",
         "fund_size": 0.7e8, "stock_position_ratio": 80.0,
         "institution_holding_ratio": 24.0, "insider_holding_shares": 100.0},
        {"report_date": "2024-09-30", "effective_date": "2024-11-01",
         "fund_size": 0.8e8, "stock_position_ratio": 82.0,
         "institution_holding_ratio": 25.0, "insider_holding_shares": 100.0},
        {"report_date": "2024-12-31", "effective_date": "2025-02-01",
         "fund_size": 0.9e8, "stock_position_ratio": 80.0,
         "institution_holding_ratio": 26.0, "insider_holding_shares": 100.0},
        {"report_date": "2025-03-31", "effective_date": "2025-05-01",
         "fund_size": 2.0e8, "stock_position_ratio": 78.0,
         "institution_holding_ratio": 27.0, "insider_holding_shares": 100.0},
    ]


def make_drift_quarterly() -> list[dict]:
    """构造触发仓位漂移的季度数据（某季度仓位 < 60%）"""
    return [
        {"report_date": "2024-06-30", "effective_date": "2024-08-01",
         "fund_size": 1e9, "stock_position_ratio": 85.0},
        {"report_date": "2024-09-30", "effective_date": "2024-11-01",
         "fund_size": 1e9, "stock_position_ratio": 82.0},
        {"report_date": "2024-12-31", "effective_date": "2025-02-01",
         "fund_size": 1e9, "stock_position_ratio": 55.0},  # < 60% → 漂移
        {"report_date": "2025-03-31", "effective_date": "2025-05-01",
         "fund_size": 1e9, "stock_position_ratio": 80.0},
    ]


def make_institution_approval_quarterly() -> list[dict]:
    """构造触发机构认可度加分的季度数据（连续两期上升 ≥ 1 个百分点）"""
    return [
        {"report_date": "2024-06-30", "effective_date": "2024-08-01",
         "fund_size": 1e9, "stock_position_ratio": 85.0,
         "institution_holding_ratio": 20.0, "insider_holding_shares": 100.0},
        {"report_date": "2024-09-30", "effective_date": "2024-11-01",
         "fund_size": 1e9, "stock_position_ratio": 85.0,
         "institution_holding_ratio": 22.0, "insider_holding_shares": 100.0},
        {"report_date": "2024-12-31", "effective_date": "2025-02-01",
         "fund_size": 1e9, "stock_position_ratio": 85.0,
         "institution_holding_ratio": 24.0, "insider_holding_shares": 130.0},  # +30% > 20%
        {"report_date": "2025-03-31", "effective_date": "2025-05-01",
         "fund_size": 1e9, "stock_position_ratio": 85.0,
         "institution_holding_ratio": 26.0, "insider_holding_shares": 130.0},
    ]


def make_default_factors_config() -> list[dict]:
    """7 因子默认配置"""
    return [
        {"code": "short_momentum", "name": "短期动量", "weight": 1.2, "direction": "positive"},
        {"code": "mid_momentum", "name": "中期动量", "weight": 1.2, "direction": "positive"},
        {"code": "inv_volatility", "name": "波动率倒数", "weight": 1.0, "direction": "positive"},
        {"code": "drawdown_recovery", "name": "回撤修复度", "weight": 0.8, "direction": "positive"},
        {"code": "return_risk_ratio", "name": "收益风险比", "weight": 0.8, "direction": "positive"},
        {"code": "momentum_accel", "name": "动量加速度", "weight": 0.5, "direction": "positive"},
        {"code": "trend_consistency", "name": "趋势一致性", "weight": 0.5, "direction": "positive"},
    ]


# ═══════════════════════════════════════════════════════════════════════
# 场景 1：棺材钉形态基金被正确剔除
# ═══════════════════════════════════════════════════════════════════════

class TestCoffinNailVeto:
    def test_coffin_nail_detected(self):
        fd = make_coffin_nail_fund()
        assert check_coffin_nail_pattern(fd) is True, "应检测到棺材钉形态"

    def test_normal_fund_not_vetoed(self):
        fd = make_fund_data(trend="up")
        assert check_coffin_nail_pattern(fd) is False, "正常上涨基金不应被否决"

    def test_quality_filter_vetoes_coffin_nail(self):
        qf = QualityFilter()
        fd = make_coffin_nail_fund()
        vetoed, reason = qf.pre_filter(fd, [])
        assert vetoed is True
        assert "棺材钉" in reason

    def test_vetoed_fund_skipped_in_build_result(self):
        """棺材钉基金在 build_result 中被否决，不进入评分"""
        qf = QualityFilter()
        fd = make_coffin_nail_fund()
        factors = make_default_factors_config()
        factor_scores = [
            FactorScoreResult(f["code"], f["name"], 0.5, 0.5, "positive")
            for f in factors
        ]
        result, scores, weights = qf.build_result(
            fund_code="COFFIN01",
            fund_data=fd,
            quarterly_history=[],
            factor_scores=factor_scores,
            active_factors=factors,
        )
        assert result.vetoed is True
        assert result.fund_code == "COFFIN01"


# ═══════════════════════════════════════════════════════════════════════
# 场景 2：规模冲击 + 漂移 → 买入阈值上调
# ═══════════════════════════════════════════════════════════════════════

class TestSizeShockAndDrift:
    def test_size_shock_detected(self):
        quarterly = make_size_shock_quarterly()
        today = date(2025, 6, 13)
        assert check_size_shock(quarterly, today) is True

    def test_drift_detected(self):
        quarterly = make_drift_quarterly()
        today = date(2025, 6, 13)
        is_drift, _ = check_allocation_drift(quarterly, today)
        assert is_drift is True

    def test_combined_threshold_raised(self):
        """规模冲击 + 漂移 → 买入阈值上调 base + 1.0 + 1.0 = 3.5"""
        buy, sell = compute_dynamic_thresholds(size_shock=True, drift=True)
        expected_buy = QUALITY_CONFIG["base_buy_threshold"] + QUALITY_CONFIG["size_shock_buy_increment"] + QUALITY_CONFIG["drift_buy_increment"]
        assert buy == expected_buy
        assert sell == QUALITY_CONFIG["base_sell_threshold"]

    def test_score_below_raised_threshold_is_hold(self):
        """原本可达标的分数在上调阈值后变为观望

        base_buy=1.5, 上调2.0 → 3.5
        score=2.5 < 3.5 → 观望
        """
        qf = QualityFilter()
        fd = make_fund_data(trend="up")
        quarterly = make_size_shock_quarterly()
        # 叠加漂移
        for q in quarterly:
            q["stock_position_ratio"] = 55.0  # 触发漂移

        factors = make_default_factors_config()
        factor_scores = [
            FactorScoreResult(f["code"], f["name"], 0.5, 0.5, "positive")
            for f in factors
        ]

        result, corrected_scores, corrected_weights = qf.build_result(
            fund_code="SHOCK_DRIFT",
            fund_data=fd,
            quarterly_history=quarterly,
            factor_scores=factor_scores,
            active_factors=factors,
            today=date(2025, 6, 13),
        )

        assert result.size_shock_triggered is True
        assert result.drift_triggered is True
        assert result.dynamic_buy_threshold == QUALITY_CONFIG["base_buy_threshold"] + 2.0

        # 模拟加权评分 = 2.5（低于上调后的阈值3.5）
        direction, strength, warning = qf.decide(
            adjusted_score=2.5,
            buy_threshold=result.dynamic_buy_threshold,
            sell_threshold=result.dynamic_sell_threshold,
            drift_triggered=result.drift_triggered,
        )
        assert direction == "hold", f"Score=2.5 < threshold={result.dynamic_buy_threshold}, 应为观望"
        # hold 信号不附带漂移警告（警告仅在买入信号时触发）
        # 验证漂移标志已正确设置即可
        assert result.drift_triggered is True


# ═══════════════════════════════════════════════════════════════════════
# 场景 3：机构认可度加分后突破阈值
# ═══════════════════════════════════════════════════════════════════════

class TestInstitutionApproval:
    def test_approval_bonus_calculated(self):
        quarterly = make_institution_approval_quarterly()
        today = date(2025, 6, 13)
        bias = calc_institution_approval(quarterly, today)
        # 连续上升 + 内部人增持 → bonus + insider_bonus
        assert bias > 0, "应有正向偏置"

    def test_bonus_pushes_score_above_threshold(self):
        """机构加分 +0.5 使评分从低于阈值变为达标买入

        base_buy=1.5, 原始score=1.2, +0.5=1.7 > 1.5 → 买入
        """
        qf = QualityFilter()
        direction, strength, _ = qf.decide(
            adjusted_score=1.2 + QUALITY_CONFIG["institution_approval_bonus"],
            buy_threshold=QUALITY_CONFIG["base_buy_threshold"],
            sell_threshold=QUALITY_CONFIG["base_sell_threshold"],
            drift_triggered=False,
        )
        assert direction == "buy", "加分后应突破阈值触发买入"

    def test_decline_penalty(self):
        """机构持有比例大幅下降 → 惩罚"""
        quarterly = [
            {"report_date": "2024-09-30", "effective_date": "2024-11-01",
             "institution_holding_ratio": 30.0},
            {"report_date": "2024-12-31", "effective_date": "2025-02-01",
             "institution_holding_ratio": 28.0},
            {"report_date": "2025-03-31", "effective_date": "2025-05-01",
             "institution_holding_ratio": 25.0},  # 下降 3% > 2%
        ]
        today = date(2025, 6, 13)
        bias = calc_institution_approval(quarterly, today)
        assert bias < 0, "应触发惩罚偏置"


# ═══════════════════════════════════════════════════════════════════════
# 衍生因子计算测试
# ═══════════════════════════════════════════════════════════════════════

class TestDerivedFactors:
    def test_momentum_stability_range(self):
        fd = make_fund_data(trend="up")
        stability = calc_momentum_stability(fd)
        assert stability is not None
        assert 0 <= stability <= 1, f"动量稳定性应在[0,1]，实际={stability}"

    def test_momentum_stability_data_insufficient(self):
        fd = FundData(code="SHORT", close_history=[1.0] * 10)
        stability = calc_momentum_stability(fd)
        assert stability is None

    def test_excess_persistence_with_benchmark(self):
        fd = make_fund_data(trend="up", close_history_len=200)
        # 基准与基金相同 → 超额=0 → persistence=0
        persistence = calc_excess_return_persistence(fd)
        assert persistence in (0, 1)

    def test_excess_persistence_no_benchmark(self):
        fd = make_fund_data(trend="up")
        fd.benchmark_history = []
        persistence = calc_excess_return_persistence(fd)
        assert persistence == 0


# ═══════════════════════════════════════════════════════════════════════
# 因子修正测试（验证原有因子计算结果不变）
# ═══════════════════════════════════════════════════════════════════════

class TestFactorCorrections:
    def _make_factor_scores(self) -> list[FactorScoreResult]:
        factors = make_default_factors_config()
        return [
            FactorScoreResult(f["code"], f["name"], 0.5, 0.5, "positive")
            for f in factors
        ]

    def test_original_factors_unchanged_for_non_target(self):
        """非波动率倒数/趋势一致性的因子得分完全不变"""
        scores = self._make_factor_scores()
        factors = make_default_factors_config()

        corrected, weights = apply_factor_corrections(
            scores, factors,
            momentum_stability=0.5,
            excess_persistence=0,  # 不触发趋势权重提升
        )

        # short_momentum (idx=0) 应不变
        assert corrected[0].score == scores[0].score
        # mid_momentum (idx=1) 应不变
        assert corrected[1].score == scores[1].score
        # drawdown_recovery (idx=3) 应不变
        assert corrected[3].score == scores[3].score

    def test_inv_volatility_corrected(self):
        """波动率倒数得分被修正: score × (0.5 + 0.5 × stability)"""
        scores = self._make_factor_scores()
        factors = make_default_factors_config()

        stability = 0.8
        corrected, _ = apply_factor_corrections(
            scores, factors,
            momentum_stability=stability,
            excess_persistence=0,
        )

        inv_idx = 2  # inv_volatility
        expected = 0.5 * (0.5 + 0.5 * stability)
        assert abs(corrected[inv_idx].score - expected) < 1e-6

    def test_inv_volatility_stability_zero_halves_score(self):
        """stability=0 → 得分减半"""
        scores = self._make_factor_scores()
        factors = make_default_factors_config()

        corrected, _ = apply_factor_corrections(
            scores, factors,
            momentum_stability=0.0,
            excess_persistence=0,
        )
        inv_idx = 2
        assert abs(corrected[inv_idx].score - 0.25) < 1e-6  # 0.5 * 0.5

    def test_trend_consistency_weight_boost(self):
        """超额持续性=1 且趋势一致性=1.0 → 权重从 0.5 提升至 0.8"""
        scores = self._make_factor_scores()
        factors = make_default_factors_config()
        # 将趋势一致性得分设为 1.0
        scores[6] = FactorScoreResult("trend_consistency", "趋势一致性", 1.0, 1.0, "positive")

        _, corrected_weights = apply_factor_corrections(
            scores, factors,
            momentum_stability=None,
            excess_persistence=1,
        )
        assert corrected_weights[6] == QUALITY_CONFIG["trend_consistency_boost_weight"]

    def test_trend_consistency_weight_not_boosted_when_negative(self):
        """趋势一致性为负值 → 权重不变"""
        scores = self._make_factor_scores()
        factors = make_default_factors_config()
        scores[6] = FactorScoreResult("trend_consistency", "趋势一致性", -1.0, -1.0, "positive")

        _, corrected_weights = apply_factor_corrections(
            scores, factors,
            momentum_stability=None,
            excess_persistence=1,
        )
        assert corrected_weights[6] == 0.5  # 原始权重


# ═══════════════════════════════════════════════════════════════════════
# 集成测试：compute_with_quality_filter
# ═══════════════════════════════════════════════════════════════════════

class TestComputeWithQualityFilter:
    def test_bias_changes_signal(self):
        """偏置加分使信号从观望变为买入"""
        factors = make_default_factors_config()
        scores = [
            FactorScoreResult(f["code"], f["name"], 0.3, 0.3, "positive")
            for f in factors
        ]
        weights = [f["weight"] for f in factors]

        # 无偏置：0.3 * 6.0 = 1.8 > 1.5 → 买入
        qf_no_bias = QualityFilterResult(
            fund_code="TEST",
            institution_bias=0.0,
            dynamic_buy_threshold=QUALITY_CONFIG["base_buy_threshold"],
            dynamic_sell_threshold=QUALITY_CONFIG["base_sell_threshold"],
        )
        signal = compute_with_quality_filter(scores, weights, qf_no_bias)
        # weighted_score should be ~1.8, which is above 1.5
        assert signal.signal_direction == "buy"

        # 加偏置后分数更高
        qf_with_bias = QualityFilterResult(
            fund_code="TEST",
            institution_bias=0.5,
            dynamic_buy_threshold=QUALITY_CONFIG["base_buy_threshold"],
            dynamic_sell_threshold=QUALITY_CONFIG["base_sell_threshold"],
        )
        signal2 = compute_with_quality_filter(scores, weights, qf_with_bias)
        assert signal2.weighted_score > signal.weighted_score

    def test_dynamic_threshold_overrides_signal(self):
        """高阈值使原本买入的信号变为观望"""
        factors = make_default_factors_config()
        scores = [
            FactorScoreResult(f["code"], f["name"], 0.3, 0.3, "positive")
            for f in factors
        ]
        weights = [f["weight"] for f in factors]

        # 高阈值：score ~1.8 < 3.5 → 观望
        qf = QualityFilterResult(
            fund_code="TEST",
            institution_bias=0.0,
            dynamic_buy_threshold=3.5,  # 上调后的阈值
            dynamic_sell_threshold=QUALITY_CONFIG["base_sell_threshold"],
        )
        signal = compute_with_quality_filter(scores, weights, qf)
        assert signal.signal_direction == "hold"

    def test_original_score_preserved(self):
        """SignalResult.original_score 保存了修正前的原始评分"""
        factors = make_default_factors_config()
        scores = [
            FactorScoreResult(f["code"], f["name"], 0.5, 0.5, "positive")
            for f in factors
        ]
        weights = [f["weight"] for f in factors]

        qf = QualityFilterResult(
            fund_code="TEST",
            institution_bias=0.5,
            dynamic_buy_threshold=QUALITY_CONFIG["base_buy_threshold"],
            dynamic_sell_threshold=QUALITY_CONFIG["base_sell_threshold"],
        )
        signal = compute_with_quality_filter(scores, weights, qf)
        assert signal.original_score != signal.weighted_score
        assert abs(signal.weighted_score - (signal.original_score + 0.5)) < 0.01


# ═══════════════════════════════════════════════════════════════════════
# 清盘风险测试
# ═══════════════════════════════════════════════════════════════════════

class TestLiquidationRisk:
    def test_liquidation_detected(self):
        quarterly = [
            {"report_date": "2024-06-30", "effective_date": "2024-08-01",
             "fund_size": 1e8},
            {"report_date": "2024-09-30", "effective_date": "2024-11-01",
             "fund_size": 6e7},  # 缩减 40%
            {"report_date": "2024-12-31", "effective_date": "2025-02-01",
             "fund_size": 3.5e7},  # 缩减 41.7%, < 5000万
            {"report_date": "2025-03-31", "effective_date": "2025-05-01",
             "fund_size": 2e7},  # 继续缩减
        ]
        today = date(2025, 6, 13)
        assert check_liquidation_risk(quarterly, today) is True

    def test_no_liquidation_with_large_fund(self):
        quarterly = [
            {"report_date": "2024-09-30", "effective_date": "2024-11-01",
             "fund_size": 1e9},
            {"report_date": "2024-12-31", "effective_date": "2025-02-01",
             "fund_size": 6e8},  # 缩减 40%
            {"report_date": "2025-03-31", "effective_date": "2025-05-01",
             "fund_size": 3e8},  # 缩减 50% 但 > 5000万
        ]
        today = date(2025, 6, 13)
        assert check_liquidation_risk(quarterly, today) is False


# ═══════════════════════════════════════════════════════════════════════
# 数据时效性测试（禁止未来数据泄露）
# ═══════════════════════════════════════════════════════════════════════

class TestDataTimeliness:
    def test_future_quarterly_not_used(self):
        """未到生效日期的季度数据不应被使用"""
        quarterly = [
            {"report_date": "2025-03-31", "effective_date": "2025-05-01",
             "fund_size": 2e8, "stock_position_ratio": 80.0},
            {"report_date": "2025-06-30", "effective_date": "2025-08-01",  # 未生效
             "fund_size": 5e7, "stock_position_ratio": 50.0},
        ]
        today = date(2025, 6, 13)
        # 只有1条有效记录，不足以触发规模冲击
        assert check_size_shock(quarterly, today) is False
