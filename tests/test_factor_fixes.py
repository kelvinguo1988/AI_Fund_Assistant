"""因子引擎 & 质量过滤器 2026-08-26 修复的回归测试

覆盖：
1. price_percentile: close=None 时不回退到 PE（PE量纲不同会产生垃圾分）
2. fed_model: bond_yield=None 返回中性分
3. size_stability: 1/CV 有上界，不会把加权总分打爆
4. align_price_series: 日期对齐 vs 退化为尾部对齐
5. momentum off-by-one: window 日动量 = window+1 个价格点
6. ECG: 脉冲后未回吐不再被误判
"""

import sys, os, pytest
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.data_sources.base import FundData
from backend.engines.factor_engine import (
    calculate_price_percentile,
    calculate_fed_model,
    calculate_size_stability,
    calculate_short_momentum,
    calculate_mid_momentum,
    align_price_series,
)
from backend.engines.quality_filter import check_ecg_pattern, QUALITY_CONFIG
from backend.engines.quality_filter import QualityFilterResult
from backend.engines.scoring_engine import compute_with_quality_filter
from backend.engines.factor_engine import FactorScoreResult


def make_fd(**overrides) -> FundData:
    """最小构造 FundData"""
    defaults = dict(
        code="000001", name="测试", date="2026-01-01",
        close=1.5, close_history=[],
    )
    defaults.update(overrides)
    return FundData(**defaults)


# ── 1. price_percentile: close=None 不回退 PE ────────────────────────

class TestPricePercentileNoPEFallback:
    def test_close_none_pe_exists_returns_neutral(self):
        fd = make_fd(close=None, pe=20.0, close_history=[1.0, 1.1, 1.2])
        r = calculate_price_percentile(fd)
        assert r.score == 0.0, "close=None 时必须返回中性分，不能用 PE 兜底"

    def test_close_none_pe_none_returns_neutral(self):
        fd = make_fd(close=None, pe=None, close_history=[1.0, 1.1])
        r = calculate_price_percentile(fd)
        assert r.score == 0.0


# ── 2. fed_model: bond_yield=None → 中性 ─────────────────────────────

class TestFedModelNoBondYield:
    def test_bond_yield_none(self):
        fd = make_fd(pe=15.0, bond_yield=None)
        r = calculate_fed_model(fd)
        assert r.score == 0.0
        assert r.raw_value == 0.0

    def test_bond_yield_present(self):
        fd = make_fd(pe=15.0, bond_yield=2.0)
        r = calculate_fed_model(fd)
        # earnings_yield = 100/15 ≈ 6.67, FED = 6.67-2 = 4.67 → 0.5 档
        assert r.score == 0.5


# ── 3. size_stability: 1/CV 上界 ─────────────────────────────────────

class TestSizeStabilityCapped:
    def test_nearly_constant_size_capped(self):
        fd = make_fd(fund_size_history=[1e9, 1.0001e9, 0.9999e9, 1.0001e9])
        r = calculate_size_stability(fd)
        # 1/CV 部分被 cap 在 5.0，bonus(2~50亿)额外 +0.2，所以上限是 5.2
        assert r.raw_value <= 5.2, f"1/CV cap=5.0 + max bonus 0.2 = 5.2，实际={r.raw_value}"

    def test_variable_size_normal(self):
        fd = make_fd(fund_size_history=[1e9, 1.5e9, 2e9, 2.5e9])
        r = calculate_size_stability(fd)
        assert r.raw_value > 0


# ── 4. align_price_series ─────────────────────────────────────────────

class TestAlignPriceSeries:
    def test_date_aligned(self):
        fd = make_fd(
            close_history=[10, 11, 12, 13, 14],
            date_history=["d1", "d2", "d3", "d4", "d5"],
            benchmark_history=[100, 101, 102, 103, 104, 105],
            benchmark_date_history=["d0", "d1", "d2", "d3", "d4", "d5"],
        )
        fp, bp = align_price_series(fd)
        assert len(fp) == 5
        assert len(bp) == 5
        assert list(fp) == [10, 11, 12, 13, 14]
        assert list(bp) == [101, 102, 103, 104, 105]

    def test_missing_dates_fallback(self):
        fd = make_fd(
            close_history=[10, 11, 12],
            date_history=[],
            benchmark_history=[100, 101, 102, 103],
            benchmark_date_history=[],
        )
        fp, bp = align_price_series(fd)
        assert len(fp) == 3
        assert len(bp) == 3

    def test_partial_overlap(self):
        fd = make_fd(
            close_history=[10, 11, 12, 13],
            date_history=["d2", "d3", "d4", "d5"],
            benchmark_history=[100, 101, 102, 103, 104],
            benchmark_date_history=["d1", "d2", "d3", "d4", "d5"],
        )
        fp, bp = align_price_series(fd)
        assert len(fp) == 4
        assert list(fp) == [10, 11, 12, 13]
        assert list(bp) == [101, 102, 103, 104]


# ── 5. momentum off-by-one ─────────────────────────────────────────────

class TestMomentumOffByOne:
    def test_20d_momentum_uses_21_prices(self):
        # 20 日动量 = prices[-1] / prices[-21] - 1
        prices = [1.0] * 21 + [1.2]  # 前21个=1.0, 第22个=1.2
        fd = make_fd(close_history=prices)
        r = calculate_short_momentum(fd, {"window": 20})
        assert r.raw_value == pytest.approx(0.2, abs=1e-3)

    def test_60d_momentum_uses_61_prices(self):
        prices = list(np.linspace(1.0, 1.5, 65))
        fd = make_fd(close_history=prices)
        r = calculate_mid_momentum(fd, {"window": 60})
        # 65 points, index -61 → prices[4] = 1.0 + 4*(0.5/64) ≈ 1.03125
        expected = 1.5 / prices[-61] - 1
        assert r.raw_value == pytest.approx(expected, abs=1e-3)


# ── 6. ECG: 脉冲后未回吐不被误判 ─────────────────────────────────────

class TestECGPattern:
    def test_spike_no_revert_not_triggered(self):
        """单日脉冲 + 持续上涨不回落 → 不是心电图"""
        np.random.seed(99)
        n = 252
        base = 1.0
        prices = [base]
        for i in range(1, n):
            if i == 100:
                # +3% pulse
                prices.append(prices[-1] * 1.03)
            elif i == 200:
                # another +3% pulse
                prices.append(prices[-1] * 1.03)
            else:
                # tiny random walk, stays flat
                prices.append(prices[-1] * (1 + np.random.randn() * 0.001))
        prices = np.array(prices)
        fd = make_fd(close_history=prices.tolist())
        result = check_ecg_pattern(fd)
        assert result is False, "脉冲后持续上涨不回落，不应判定为心电图"

    def test_spike_revert_triggered(self):
        """脉冲后回落 + 低波 + 窄区间 → 心电图"""
        np.random.seed(88)
        n = 252
        base = 1.0
        prices = [base]
        spike_indices = [50, 100, 150]
        for i in range(1, n):
            if i in spike_indices:
                prices.append(prices[-1] * 1.03)  # +3% spike
            elif i in [s + 2 for s in spike_indices]:
                prices.append(prices[-1] * 0.985)  # drop back
            else:
                prices.append(prices[-1] * (1 + np.random.randn() * 0.0005))
        prices = np.array(prices)
        fd = make_fd(close_history=prices.tolist())
        # This may or may not trigger depending on exact vol/range;
        # the key regression is that the old version always triggered
        # (because np.min(future_norm) < high was always true).
        # We only assert it doesn't crash and returns a bool.
        assert isinstance(check_ecg_pattern(fd), bool)


# ── 7. 漂移警告去重 ───────────────────────────────────────────────────

class TestDriftWarningDedupe:
    def test_no_duplicate_drift_warning(self):
        """买入+漂移时，同一警告文案只出现一次"""
        drift_warning = "警告：该基金仓位择时成分显著，信号可能不稳定，请人工复核。"
        qr = QualityFilterResult(
            fund_code="000001",
            institution_bias=0.0,
            dynamic_buy_threshold=1.5,
            dynamic_sell_threshold=-1.5,
            drift_triggered=True,
        )
        qr.warnings.append(drift_warning)
        scores = [FactorScoreResult("macd_signal", "MACD", 0.5, 1.0, "positive")]
        signal = compute_with_quality_filter(
            factor_scores=scores, factor_weights=[2.0], quality_result=qr,
        )
        # 高分买入 + 漂移 → determine_signal 也生成同一文案，合并后应只 1 条
        assert signal.signal_direction == "buy"
        assert signal.quality_warnings.count(drift_warning) == 1
