"""基金 PK 指标回归测试 — 年化/回撤/夏普/Beta/Alpha/IR/对齐"""

import sys, os
import math
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.fund_compare_service import (
    annualized_return_pct,
    max_drawdown_pct,
    sharpe_ratio,
    beta_alpha_ir,
    align_by_dates,
)


class TestAnnualized:
    def test_one_year_exact(self):
        """一年翻倍 → 年化 100%"""
        vals = [1.0, 2.0]
        days = 365
        assert annualized_return_pct(vals, days) == pytest.approx(100.0, abs=0.1)

    def test_two_years_21(self):
        """两年 +21%（年化 10%）"""
        vals = [1.0, 1.1, 1.21]
        # 日期差按 len-1 近似 2 年？实际用日期差——这里手工传 days=730
        assert annualized_return_pct(vals, 730) == pytest.approx(10.0, abs=0.2)

    def test_invalid(self):
        assert annualized_return_pct([1.0], 365) is None
        assert annualized_return_pct([0.0, 1.0], 365) is None


class TestMaxDrawdown:
    def test_simple(self):
        vals = [1.0, 1.2, 0.9, 1.1]  # 峰 1.2 谷 0.9 → -25%
        assert max_drawdown_pct(vals) == pytest.approx(-25.0)

    def test_no_drawdown(self):
        assert max_drawdown_pct([1.0, 1.1, 1.2]) == 0.0


class TestSharpe:
    def test_positive_sharpe_for_steady_growth(self):
        # 稳定上涨序列 → 夏普为正
        vals = [1.0 * (1.001 ** i) for i in range(300)]
        s = sharpe_ratio(vals)
        assert s is not None and s > 0


class TestBetaAlphaIR:
    def _series(self, n=300, daily=0.0005):
        # 带确定波动的序列（恒定日收益方差为 0，Beta 数学上未定义）
        v = [1.0]
        for i in range(1, n):
            wiggle = 0.002 if i % 2 == 0 else -0.0015
            v.append(v[-1] * (1 + daily + wiggle))
        return v

    def test_perfect_correlation_beta_one(self):
        """基金=基准 → Beta≈1, Alpha≈0, IR 无效（超额恒 0 → std 0）"""
        v = self._series()
        beta, alpha, ir = beta_alpha_ir(v, v)
        assert beta == pytest.approx(1.0, abs=0.05)
        assert alpha is not None and abs(alpha) < 1.0
        # 超额恒 0 → var_e=0 → IR None
        assert ir is None

    def test_leveraged_beta_two(self):
        """基金日收益 = 基准×2 → Beta≈2"""
        bench = self._series(daily=0.0004)
        fund = [1.0]
        for i in range(1, len(bench)):
            fund.append(fund[-1] * (1 + (bench[i] / bench[i - 1] - 1) * 2))
        beta, alpha, ir = beta_alpha_ir(fund, bench)
        assert beta == pytest.approx(2.0, abs=0.1)

    def test_insufficient_data(self):
        assert beta_alpha_ir([1.0, 1.1], [1.0, 1.1]) == (None, None, None)


class TestAlign:
    def test_intersection(self):
        fund = [("d1", 1.0), ("d2", 2.0), ("d4", 4.0)]
        bench = [("d1", 100.0), ("d3", 103.0), ("d4", 104.0)]
        fv, bv = align_by_dates(fund, bench)
        # 基金序遍历，d1/d4 在基准中存在
        assert fv == [1.0, 4.0]
        assert bv == [100.0, 104.0]
