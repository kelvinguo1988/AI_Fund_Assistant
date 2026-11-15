"""格雷厄姆温度计回归测试 — 序列构建/交叉四象限/降级路径"""

import sys, os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.market_temperature_service import (
    graham_monthly_series,
    _cross_status,
    bond_month_end_map,
)


# ── 序列构建 ──────────────────────────────────────────────────────────

class TestGrahamSeries:
    def test_basic_math(self):
        """格雷厄姆 = (100/PE) ÷ 国债收益率%"""
        dates = ["2020-01-31", "2020-02-28"]
        pe = [10.0, 20.0]
        bond_me = {"2020-01": 2.5, "2020-02": 2.5}
        g = graham_monthly_series(pe, bond_me, dates)
        # (100/10)/2.5 = 4.0；(100/20)/2.5 = 2.0
        assert g[0] == pytest.approx(4.0)
        assert g[1] == pytest.approx(2.0)

    def test_missing_bond_month_none(self):
        dates = ["2020-01-31", "2020-02-28"]
        pe = [10.0, 20.0]
        bond_me = {"2020-01": 2.5}  # 2 月缺国债
        g = graham_monthly_series(pe, bond_me, dates)
        assert g[0] == pytest.approx(4.0)
        assert g[1] is None

    def test_zero_pe_guard(self):
        g = graham_monthly_series([0.0], {"2020-01": 2.5}, ["2020-01-31"])
        assert g == [None]


class TestBondMonthEnd:
    def test_last_of_month_wins(self):
        import pandas as pd
        df = pd.DataFrame({
            "日期": ["2026-01-05", "2026-01-20", "2026-01-31", "2026-02-10"],
            "中国国债收益率10年": [1.7, 1.65, 1.61, 1.58],
        })
        out = bond_month_end_map(df)
        assert out["2026-01"] == 1.61   # 月末（升序末次覆盖）
        assert out["2026-02"] == 1.58


# ── 交叉四象限 ────────────────────────────────────────────────────────

class TestCrossStatus:
    def test_true_cheap(self):
        r = _cross_status(30.0, 20.0)
        assert r["state"] == "true_cheap"

    def test_rate_distorted_cheap(self):
        """当前实测状态：温度 44.6 + 格雷厄姆 95.8 分位"""
        r = _cross_status(44.6, 95.8)
        assert r["state"] == "rate_distorted_cheap"
        assert "利率扭曲" in r["note"]

    def test_true_expensive(self):
        r = _cross_status(75.0, 90.0)
        assert r["state"] == "true_expensive"

    def test_rare_review(self):
        r = _cross_status(75.0, 20.0)
        assert r["state"] == "rare_review"

    def test_none_input(self):
        r = _cross_status(None, 50.0)
        assert r["state"] is None
